"""
ImpulseCalc HTTP server — serves Devenport-style HTML + JSON APIs.

  cd C:\\Users\\tyler\\ImpulseCalc
  pip install -r requirements.txt
  python server.py

Then open: http://127.0.0.1:8765/calc.html
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import threading
import time
import traceback
import uuid
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable

from flask import Flask, jsonify, request, send_from_directory


def _roots() -> tuple[Path, Path, Path]:
    """(data root, static folder, writable output folder)."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        meipass = Path(getattr(sys, "_MEIPASS", exe_dir))
        static = meipass / "static"
        if not static.is_dir():
            static = exe_dir / "static"
        data = meipass if (meipass / "configs").is_dir() else exe_dir
        return data, static, exe_dir / "output"
    root = Path(__file__).resolve().parent
    return root, root / "static", root / "output"

from impulsecalc.meanline import MeanlineInputs, compute_meanline
from impulsecalc.geometry import BladeGeometry, BladeShapeParams, blade_preview_payload
from impulsecalc.openfoam_case import generate_openfoam_case
from impulsecalc.postprocess import load_surface_pressure, synthetic_surface_pressure
from impulsecalc.design_report import build_design_report
from impulsecalc.cascade_job import run_cascade_job
from impulsecalc.runners import mesh_pipeline, openfoam_available, run_sample, run_solver
from impulsecalc.technical_video import VideoOptions, generate_technical_video, workflow_status
from impulsecalc.workflow import WorkflowState, save_design

ROOT, STATIC, OUTPUT_DIR = _roots()

app = Flask(__name__, static_folder=str(STATIC), static_url_path="")
_LAST_DESIGN: dict | None = None

# Background mesh/solve jobs so the browser is not held open for hours
# (long sync POSTs cause "Failed to fetch" when the connection drops).
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def _job_set(job_id: str, **fields: Any) -> None:
    with _JOBS_LOCK:
        cur = _JOBS.get(job_id) or {}
        cur.update(fields)
        cur["updated_at"] = time.time()
        _JOBS[job_id] = cur


def _start_job(
    kind: str,
    case_dir: str,
    fn: Callable[[], dict[str, Any]],
    *,
    estimate_s: float | None = None,
) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    _job_set(
        job_id,
        kind=kind,
        case_dir=case_dir,
        status="running",
        success=None,
        message=f"{kind} started…",
        result=None,
        error=None,
        started_at=now,
        id=job_id,
        estimate_s=estimate_s,
    )

    def _worker() -> None:
        try:
            result = fn()
            ok = bool(result.get("success"))
            _job_set(
                job_id,
                status="done",
                success=ok,
                message=result.get("message") or ("ok" if ok else f"{kind} failed"),
                result=result,
            )
        except Exception as exc:  # noqa: BLE001
            _job_set(
                job_id,
                status="done",
                success=False,
                message=str(exc),
                error=str(exc),
                traceback=traceback.format_exc()[-1500:],
                result={"success": False, "message": str(exc)},
            )

    t = threading.Thread(target=_worker, name=f"impulsecalc-{kind}-{job_id}", daemon=True)
    t.start()
    return {
        "async": True,
        "job_id": job_id,
        "status": "running",
        "success": None,
        "message": f"{kind} running in background (poll /api/job/{job_id})",
        "case_dir": case_dir,
        "estimate_s": estimate_s,
        "elapsed_s": 0.0,
        "started_at": now,
    }


@app.get("/")
def index():
    return send_from_directory(STATIC, "calc.html")


@app.get("/api/health")
def health():
    """Liveness + version stamp so the UI can detect a stale server process."""
    api_routes = sorted(
        {
            r.rule
            for r in app.url_map.iter_rules()
            if str(r.rule).startswith("/api/")
        }
    )
    return jsonify({
        "ok": True,
        "app": "ImpulseCalc",
        "ui": "html",
        "version": "1.5.0",
        "api_routes": api_routes,
        "has_analyze_loss": any(str(r).startswith("/api/analyze_loss") for r in api_routes),
        "has_design_report": any(str(r).startswith("/api/design_report") for r in api_routes),
        "has_gasdynamics": True,
        "default_stage": "user_stage_r040",
        "share_url": "https://coheteTyler.github.io/app-share/impulsecalc/",
        "standalone": True,
    })


def _probe_url(url: str, timeout: float = 0.6) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 400
    except Exception:
        return False


@app.get("/api/share")
def api_share():
    """HTTPS share link for Discord/WhatsApp — never localhost."""
    from impulsecalc.share_catalog import SHARE_BASE, by_id, share_url

    app_id = (request.args.get("id") or "impulsecalc").strip()
    row = by_id(app_id)
    if not row:
        return jsonify({"ok": False, "error": "unknown app", "id": app_id}), 404
    url = share_url(app_id)
    return jsonify(
        {
            "ok": True,
            "id": row["id"],
            "name": row["name"],
            "url": url,
            "protocol": row["protocol"] + "://open",
            "download": row.get("download") or "",
            "github": row.get("github") or "",
            "base": SHARE_BASE,
        }
    )


@app.get("/api/siblings")
def api_siblings():
    from impulsecalc.share_catalog import APPS, SHARE_BASE

    rows = []
    for row in APPS:
        rec = dict(row)
        rec["up"] = _probe_url(str(row["health"]))
        rec["share_url"] = f"{SHARE_BASE}/{row['share']}/"
        rows.append(rec)
    return jsonify({"ok": True, "current": "impulsecalc", "apps": rows})


@app.get("/api/default_design")
def api_default_design():
    """Ship the user-table stage as the board default (configs/default_design.json)."""
    path = ROOT / "configs" / "default_design.json"
    if not path.is_file():
        # Fall back to MeanlineInputs dataclass defaults (already user table)
        inp = MeanlineInputs()
        return jsonify(
            {
                "ok": True,
                "source": "MeanlineInputs_defaults",
                "meanline_inputs": inp.to_dict(),
                "blade_shape": BladeShapeParams(
                    profile_family="impulse_bucket",
                    thickness_ratio=0.50,
                    wall_thickness_c=0.50,
                    thickness_peak_x=0.45,
                    arc_bulge=1.70,
                    bucket_suction_cutback=0.20,
                ).to_dict(),
                "geometry": {
                    "rotor_tip_radius_m": inp.tip_radius_m,
                    "hub_radius_m": inp.hub_radius_m,
                    "mean_radius_m": inp.mean_radius_m,
                    "span_m": inp.span_m,
                    "n_blades": inp.n_blades_machine,
                    "chord_m": inp.chord_m,
                    "blade_spacing_m": inp.chord_m / max(inp.solidity, 1e-9),
                    "solidity": inp.solidity,
                    "min_blade_thickness_m": 0.005,
                    "thickness_ratio": 0.50,
                    "profile_family": "impulse_bucket",
                },
            }
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500
    data["ok"] = True
    data["source"] = str(path)
    return jsonify(data)


def _api_error(status: int, error: str, message: str, **extra):
    body = {"success": False, "error": error, "message": message, "path": request.path}
    body.update(extra)
    return jsonify(body), status


@app.errorhandler(404)
def api_not_found(err):
    """Return JSON for /api/* errors so the UI never tries to parse HTML as JSON."""
    if request.path.startswith("/api/"):
        return _api_error(
            404,
            "not_found",
            f"No API route for {request.path}. Restart server.py with the latest ImpulseCalc code.",
        )
    return ("Not found", 404)


@app.errorhandler(405)
def api_method_not_allowed(err):
    if request.path.startswith("/api/"):
        return _api_error(
            405,
            "method_not_allowed",
            f"{request.method} not allowed for {request.path}. Use POST with JSON body.",
            method=request.method,
        )
    return ("Method not allowed", 405)


@app.errorhandler(500)
def api_server_error(err):
    if request.path.startswith("/api/"):
        return _api_error(
            500,
            "server_error",
            str(getattr(err, "original_exception", err) or err),
        )
    return ("Server error", 500)


@app.get("/api/openfoam_probe")
def of_probe():
    # force_probe: WSL can fail once with STATUS_DLL_INIT_FAILED then succeed
    return jsonify(openfoam_available(force_probe=True))


@app.post("/api/blade_preview")
def api_blade_preview():
    """Return meanline + closed profile points for live SVG preview."""
    data = request.get_json(force=True, silent=True) or {}
    ml_in = data.get("meanline") or {}
    try:
        inp = MeanlineInputs.from_dict(ml_in)
        ml = compute_meanline(inp)
        shape = BladeShapeParams.from_dict(data.get("blade_shape"))
        geom = BladeGeometry(
            chord_m=inp.chord_m,
            beta1_deg=ml.metal_beta1_deg,
            beta2_deg=ml.metal_beta2_deg,
            solidity=inp.solidity,
            thickness_ratio=shape.thickness_ratio,
            shape=shape,
            n_points=shape.n_points,
        )
        # Always use the same n_blades as §3 case (mesh parity); min 1 for single profile
        n_blades = int(data.get("n_blades") or 1)
        x_up = float(data.get("x_up_c") if data.get("x_up_c") is not None else data.get("inlet_c") or 0.5)
        x_dn = float(data.get("x_dn_c") if data.get("x_dn_c") is not None else data.get("outlet_c") or 1.0)
        payload = blade_preview_payload(
            geom,
            n_blades=n_blades,
            x_up_c=x_up,
            x_dn_c=x_dn,
            flow_beta1_deg=ml.beta1_deg,
            flow_beta2_deg=ml.beta2_deg,
            w1_m_s=ml.w1_m_s,
            p1_pa=inp.p1_pa,
            t1_k=inp.t1_k,
            mach_w1=ml.mach_w1,
            mean_radius_m=ml.mean_radius_m,
            span_m=ml.span_m,
            tip_radius_m=getattr(inp, "tip_radius_m", None),
            hub_radius_m=getattr(inp, "hub_radius_m", None),
            n_blades_machine=getattr(inp, "n_blades_machine", None),
            blade_name=inp.blade_name,
        )
        return jsonify({"success": True, **payload})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 400


@app.post("/api/generate_case")
def api_generate_case():
    data = request.get_json(force=True, silent=True) or {}
    ml_in = data.get("meanline") or {}
    try:
        from impulsecalc.fidelity import fidelity_from_request
        from impulsecalc.openfoam_case import _optional_float

        inp = MeanlineInputs.from_dict(ml_in)
        # Domain extents — tolerate missing / "auto"
        x_up = _optional_float(
            data.get("x_up_c") if data.get("x_up_c") is not None else data.get("inlet_c"),
            default=0.5,
        )
        x_dn = _optional_float(
            data.get("x_dn_c") if data.get("x_dn_c") is not None else data.get("outlet_c"),
            default=1.0,
        )
        if x_up is None:
            x_up = 0.5
        if x_dn is None:
            x_dn = 1.0
        # UI may send end_time: null or the string "auto" — never float("auto")
        end_time = _optional_float(data.get("end_time"), default=None)
        startup = bool(data.get("startup", True))
        fid = fidelity_from_request(data)
        # Prefer explicit nx/ny from client (fidelity UI writes them); else fidelity preset
        nx_raw = data.get("nx")
        ny_raw = data.get("ny")
        try:
            nx = int(nx_raw) if nx_raw not in (None, "") else fid.nx
            ny = int(ny_raw) if ny_raw not in (None, "") else fid.ny
        except (TypeError, ValueError):
            nx, ny = fid.nx, fid.ny
        res = generate_openfoam_case(
            inp,
            data.get("output_dir") or "output",
            case_name=inp.blade_name or "cascade",
            n_blades=int(data.get("n_blades") or fid.n_blades_default or 3),
            nx=nx,
            ny=ny,
            end_time=end_time,
            blade_shape=data.get("blade_shape"),
            x_up_c=float(x_up),
            x_dn_c=float(x_dn),
            startup=startup,
            fidelity=fid,
        )
        out = res.to_dict()
        out["fidelity"] = fid.to_dict()
        # Surface domain bounds for UI feedback
        try:
            from impulsecalc.geometry import BladeGeometry, BladeShapeParams, domain_bounds
            from impulsecalc.meanline import compute_meanline

            ml = compute_meanline(inp)
            sh = BladeShapeParams.from_dict(data.get("blade_shape"))
            geom = BladeGeometry(
                chord_m=inp.chord_m,
                beta1_deg=ml.metal_beta1_deg,
                beta2_deg=ml.metal_beta2_deg,
                solidity=inp.solidity,
                shape=sh,
            )
            out["domain"] = domain_bounds(
                geom, int(data.get("n_blades") or 3), x_up_c=x_up, x_dn_c=x_dn
            )
        except Exception:  # noqa: BLE001
            pass
        return jsonify(out)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc), "message": str(exc)}), 400


@app.get("/api/fidelity_presets")
def api_fidelity_presets():
    """Return fast / balanced / accurate settings for the top-of-UI control."""
    from impulsecalc.fidelity import (
        FIDELITY_ACCURATE,
        FIDELITY_BALANCED,
        FIDELITY_FAST,
        resolve_fidelity,
    )

    out = {}
    for m in (FIDELITY_FAST, FIDELITY_BALANCED, FIDELITY_ACCURATE):
        out[m] = resolve_fidelity(m).to_dict()
    return jsonify({"presets": out, "default": FIDELITY_FAST})


def _do_mesh(case_dir: str) -> dict[str, Any]:
    res = mesh_pipeline(case_dir)
    if res.get("success"):
        res["message"] = "mesh ok"
    else:
        detail = res.get("detail") or ""
        of = res.get("openfoam") or {}
        res["message"] = (
            f"mesh failed: {detail}" if detail else "mesh failed or OpenFOAM tools missing"
        )
        res["hint"] = of.get("message") or (
            "Install OpenFOAM: run scripts/install_openfoam_wsl.ps1 "
            "or install blueCFD-Core and set BLUECFD_DIR"
        )
    return res


def _do_solve(case_dir: str) -> dict[str, Any]:
    # Re-write OF-12-compatible system/constant files if missing PIMPLE / physicalProperties
    patch_note = "case_ok"
    try:
        from impulsecalc.openfoam_case import (
            write_control,
            write_schemes,
            write_solution,
            write_thermo,
            write_turbulence,
        )

        cdir = Path(case_dir)
        fv = (cdir / "system" / "fvSolution").read_text(encoding="utf-8", errors="ignore")
        if "PIMPLE" not in fv or not (cdir / "constant" / "physicalProperties").is_file():
            ml = compute_meanline(MeanlineInputs())
            end_time, delta_t, write_interval = 5e-4, 1e-8, 5e-5
            cd = cdir / "system" / "controlDict"
            if cd.is_file():
                text = cd.read_text(encoding="utf-8", errors="ignore")
                import re

                def _f(key: str, default: float) -> float:
                    m = re.search(rf"{key}\s+([0-9.eE+-]+)", text)
                    return float(m.group(1)) if m else default

                end_time = _f("endTime", end_time)
                delta_t = _f("deltaT", delta_t)
                write_interval = _f("writeInterval", write_interval)
            write_control(cdir, end_time, delta_t, write_interval)
            write_schemes(cdir)
            write_solution(cdir)
            write_thermo(
                cdir,
                ml.inputs.gamma,
                ml.inputs.r_specific_j_kg_k,
                ml.inputs.mu_pa_s,
            )
            write_turbulence(cdir)
            patch_note = "case_patched_for_openfoam12"
    except Exception as exc:  # noqa: BLE001
        patch_note = f"case_patch_skipped: {exc}"

    res = run_solver(case_dir).to_dict()
    res.setdefault("notes", []).append(patch_note)
    if res.get("success"):
        res["message"] = res.get("message") or "solve ok"
    else:
        tail = (res.get("stderr_tail") or res.get("stdout_tail") or "")[-400:]
        res["message"] = f"solve failed: {res.get('message', '')} {tail}".strip()
    return res


@app.post("/api/mesh")
def api_mesh():
    data = request.get_json(force=True, silent=True) or {}
    case_dir = data.get("case_dir") or ""
    if not case_dir or not Path(case_dir).is_dir():
        return jsonify({"success": False, "message": "invalid case_dir"}), 400
    # Default async — mesh can take minutes; sync still available with async=false
    async_mode = data.get("async", True)
    est = data.get("estimate_s")
    try:
        est_f = float(est) if est is not None else None
    except (TypeError, ValueError):
        est_f = None
    if async_mode in (False, "false", 0, "0"):
        return jsonify(_do_mesh(case_dir))
    return jsonify(
        _start_job("mesh", case_dir, lambda: _do_mesh(case_dir), estimate_s=est_f)
    )


@app.post("/api/solve")
def api_solve():
    data = request.get_json(force=True, silent=True) or {}
    case_dir = data.get("case_dir") or ""
    if not case_dir or not Path(case_dir).is_dir():
        return jsonify({"success": False, "message": "invalid case_dir"}), 400
    # Default async — shockFluid can run minutes–hours; browser must not block
    async_mode = data.get("async", True)
    est = data.get("estimate_s")
    try:
        est_f = float(est) if est is not None else None
    except (TypeError, ValueError):
        est_f = None
    if async_mode in (False, "false", 0, "0"):
        return jsonify(_do_solve(case_dir))
    return jsonify(
        _start_job("solve", case_dir, lambda: _do_solve(case_dir), estimate_s=est_f)
    )


def _case_solve_progress(case_dir: str | Path) -> dict[str, Any]:
    """Best-effort CFD progress from written time directories vs controlDict endTime."""
    cdir = Path(case_dir)
    end_time = None
    cd = cdir / "system" / "controlDict"
    if cd.is_file():
        import re

        m = re.search(r"endTime\s+([0-9.eE+-]+)", cd.read_text(encoding="utf-8", errors="ignore"))
        if m:
            try:
                end_time = float(m.group(1))
            except ValueError:
                end_time = None
    latest_t = 0.0
    n_times = 0
    if cdir.is_dir():
        for p in cdir.iterdir():
            if not p.is_dir():
                continue
            try:
                t = float(p.name)
            except ValueError:
                continue
            if t > 0 and (p / "U").is_file():
                n_times += 1
                if t > latest_t:
                    latest_t = t
    frac = None
    if end_time and end_time > 0 and latest_t > 0:
        frac = min(0.99, max(0.0, latest_t / end_time))
    return {
        "sim_time_s": latest_t if latest_t > 0 else None,
        "end_time_s": end_time,
        "n_time_dirs": n_times,
        "sim_progress_frac": frac,
    }


@app.get("/api/job/<job_id>")
def api_job_status(job_id: str):
    """Poll background mesh/solve job (includes elapsed + ETA hints)."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if not job:
        return jsonify({"success": False, "status": "missing", "message": "unknown job_id"}), 404
    now = time.time()
    started = float(job.get("started_at") or now)
    elapsed = max(0.0, now - started)
    out = dict(job)
    out["elapsed_s"] = elapsed
    out["server_time"] = now

    # Optional estimate from client (seconds) for wall-clock ETA
    est = job.get("estimate_s")
    try:
        est_f = float(est) if est is not None else None
    except (TypeError, ValueError):
        est_f = None
    if est_f is None or est_f <= 0:
        # Defaults by kind (design-board vs accurate are refined by client)
        kind = str(job.get("kind") or "")
        est_f = 90.0 if kind == "mesh" else 600.0
    out["estimate_s"] = est_f
    remain = max(0.0, est_f - elapsed)
    # Cap progress at 95% until done so bar never looks "finished" while running
    wall_frac = min(0.95, elapsed / est_f) if est_f > 0 else 0.0
    out["eta_s"] = remain if job.get("status") == "running" else 0.0
    out["progress_frac"] = 1.0 if job.get("status") == "done" else wall_frac

    # CFD sim-time progress when solving (more honest than wall clock alone)
    if job.get("kind") == "solve" and job.get("case_dir"):
        sp = _case_solve_progress(job["case_dir"])
        out["cfd"] = sp
        if sp.get("sim_progress_frac") is not None and job.get("status") == "running":
            # Blend wall and sim progress — sim is more meaningful once dumps appear
            sf = float(sp["sim_progress_frac"])
            out["progress_frac"] = min(0.99, max(wall_frac * 0.35, sf * 0.85 + wall_frac * 0.15))
            if sp.get("end_time_s") and sp.get("sim_time_s") is not None:
                # ETA from sim rate once we have enough data
                st = float(sp["sim_time_s"])
                if st > 1e-12 and elapsed > 5.0:
                    rate = st / elapsed  # sim-seconds per wall-second
                    left_sim = max(0.0, float(sp["end_time_s"]) - st)
                    if rate > 1e-15:
                        out["eta_s"] = left_sim / rate
                        out["estimate_s"] = elapsed + out["eta_s"]

    # Human heartbeat line
    if job.get("status") == "running":
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        eta_m = int(float(out.get("eta_s") or 0) // 60)
        eta_s = int(float(out.get("eta_s") or 0) % 60)
        pct = int(100 * float(out.get("progress_frac") or 0))
        cfd = out.get("cfd") or {}
        extra = ""
        if cfd.get("sim_time_s") is not None and cfd.get("end_time_s"):
            extra = (
                f" · CFD t={cfd['sim_time_s']:.4g}/{cfd['end_time_s']:.4g} s"
                f" ({cfd.get('n_time_dirs') or 0} dumps)"
            )
        out["heartbeat"] = (
            f"STILL RUNNING · elapsed {mins}m {secs:02d}s · ~{pct}% · "
            f"ETA ~{eta_m}m {eta_s:02d}s{extra} · not stuck"
        )
        out["message"] = out["heartbeat"]
    elif job.get("status") == "done" and isinstance(job.get("result"), dict):
        res = job["result"]
        out["success"] = res.get("success", job.get("success"))
        out["message"] = res.get("message") or job.get("message")
        out["progress_frac"] = 1.0
        out["eta_s"] = 0.0
        for k, v in res.items():
            if k not in out:
                out[k] = v
        out["heartbeat"] = (
            f"DONE in {int(elapsed // 60)}m {int(elapsed % 60):02d}s — "
            + ("success" if out.get("success") else "failed")
        )
    return jsonify(out)


@app.post("/api/sample")
def api_sample():
    data = request.get_json(force=True, silent=True) or {}
    case_dir = data.get("case_dir") or ""
    if not case_dir or not Path(case_dir).is_dir():
        return jsonify({"success": False, "message": "invalid case_dir"}), 400
    return jsonify(run_sample(case_dir).to_dict())


def _surface_from_request(data: dict) -> tuple:
    """Return (surf, meanline_result_or_None, shape_dict)."""
    case_dir = data.get("case_dir") or ""
    ml_in = data.get("meanline") or {}
    try:
        inp = MeanlineInputs.from_dict(ml_in) if ml_in else MeanlineInputs(
            p1_pa=float(data.get("p1_pa") or 5.5e5),
            w1_m_s=float(data.get("w1_m_s") or 950.0),
            chord_m=float(data.get("chord_m") or 0.01),
        )
        ml = compute_meanline(inp)
    except Exception:  # noqa: BLE001
        ml = compute_meanline(MeanlineInputs())
    p1 = float(data.get("p1_pa") or ml.inputs.p1_pa)
    rho1 = float(data.get("rho1") or ml.rho1_kg_m3)
    w1 = float(data.get("w1_m_s") or ml.inputs.w1_m_s)
    chord = float(data.get("chord_m") or ml.inputs.chord_m)
    shape = data.get("blade_shape") or {}
    force_synthetic = bool(data.get("force_synthetic") or data.get("prefer_synthetic"))
    # Always pass shape so synthetic (and re-analyze) reflects current §2 knobs
    synth_kw = dict(
        p1_pa=p1,
        mach_w1=ml.mach_w1,
        thickness_ratio=float(shape.get("thickness_ratio") or 0.12),
        thickness_peak_x=float(shape.get("thickness_peak_x") or 0.40),
        arc_bulge=float(shape.get("arc_bulge") or 1.0),
        inlet_line_frac=float(shape.get("inlet_line_frac") or 0.0),
        outlet_line_frac=float(shape.get("outlet_line_frac") or 0.0),
        beta1_deg=ml.metal_beta1_deg,
        beta2_deg=ml.metal_beta2_deg,
    )
    if case_dir and Path(case_dir).is_dir() and not force_synthetic:
        surf = load_surface_pressure(
            case_dir,
            p1_pa=p1,
            rho1=rho1,
            w1_m_s=w1,
            chord_m=chord,
            allow_synthetic=True,
            force_synthetic=False,
            mach_w1=ml.mach_w1,
            gamma=ml.inputs.gamma,
            t1_k=ml.inputs.t1_k,
            r_specific=ml.inputs.r_specific_j_kg_k,
            blade_shape=shape,
            beta1_deg=ml.metal_beta1_deg,
            beta2_deg=ml.metal_beta2_deg,
        )
        # If load fell back to synthetic without shape (legacy), rebuild with shape
        if surf.source.startswith("synthetic"):
            surf = synthetic_surface_pressure(**synth_kw)
    else:
        surf = synthetic_surface_pressure(**synth_kw)
    return surf, ml, shape


def _design_report_from_request(data: dict):
    """Shared builder for surface_pressure / design_report / analyze_loss."""
    surf, ml, shape = _surface_from_request(data)
    case_dir = data.get("case_dir") or ""
    write_exports = bool(case_dir and Path(case_dir).is_dir())
    rep = build_design_report(
        surf,
        ml=ml,
        beta1_deg=float(data.get("beta1_deg") or ml.beta1_deg),
        beta2_deg=float(data.get("beta2_deg") or ml.beta2_deg),
        mach_w1=float(data.get("mach_w1") or ml.mach_w1),
        solidity=float(data.get("solidity") or ml.inputs.solidity),
        gamma=float(data.get("gamma") or ml.inputs.gamma),
        thickness_ratio=float((shape or {}).get("thickness_ratio") or 0.50),
        le_fillet_r_c=float((shape or {}).get("le_fillet_r_c") or 0.002),
        thickness_peak_x=float((shape or {}).get("thickness_peak_x") or 0.50),
        arc_bulge=float((shape or {}).get("arc_bulge") or 1.2),
        blade_shape=shape or {},
        case_dir=case_dir if write_exports else None,
        write_exports=write_exports,
        include_plots=bool(data.get("include_plots", True)),
    )
    return rep, ml, surf


@app.route("/api/surface_pressure", methods=["GET", "POST"], strict_slashes=False)
@app.route("/api/design_report", methods=["GET", "POST"], strict_slashes=False)
def api_surface_and_design_report():
    """Primary post-§4 endpoint: dense surface + metrics + loss + plots + exports."""
    data = request.get_json(force=True, silent=True) or {}
    if request.method == "GET" and request.args:
        for key in (
            "case_dir", "p1_pa", "w1_m_s", "chord_m", "rho1",
            "beta1_deg", "beta2_deg", "mach_w1", "solidity", "gamma",
        ):
            if key in request.args and key not in data:
                data[key] = request.args.get(key)
    try:
        rep, ml, surf = _design_report_from_request(data)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc), "message": str(exc)}), 400

    # Backward-compatible surface envelope + full design report
    out = surf.to_dict()
    out["success"] = True
    out["loss_report"] = rep.loss_report.to_dict()
    out["metrics"] = rep.metrics.to_dict()
    out["stations"] = [s.to_dict() for s in rep.stations]
    out["surface_table"] = rep.surface_table
    out["shocks"] = rep.shocks
    out["shock_relations_table"] = rep.shock_relations_table
    out["normal_shock_chart"] = rep.normal_shock_chart
    out["industry_advice"] = rep.industry_advice
    out["ranked_fixes"] = rep.ranked_fixes
    out["iteration_checklist"] = rep.iteration_checklist
    out["summary"] = rep.summary
    out["design_report"] = rep.to_dict()
    out["gasdynamics_note"] = (
        "Shock jump ratios from perfect-gas normal-shock relations "
        "(Hill & Peterson, Mechanics and Thermodynamics of Propulsion, 2nd ed., §3.7). "
        "M1 estimated from surface isentropic Mach; source=" + str(surf.source)
    )
    if "hill_shock_chart" in rep.plots:
        out["plot_hill_shock_chart"] = rep.plots["hill_shock_chart"]
    out["meanline"] = {
        "beta1_deg": ml.beta1_deg,
        "beta2_deg": ml.beta2_deg,
        "metal_beta1_deg": ml.metal_beta1_deg,
        "metal_beta2_deg": ml.metal_beta2_deg,
        "incidence_deg": ml.incidence_deg,
        "deviation_deg": ml.deviation_deg,
        "mach_w1": ml.mach_w1,
        "rho1": ml.rho1_kg_m3,
        "euler_work_j_kg": ml.euler_work_j_kg,
        "stage_loading": ml.stage_loading,
        "flow_coefficient": ml.flow_coefficient,
        "efficiency_proxy": ml.efficiency_proxy,
        "mean_radius_m": ml.mean_radius_m,
        "rpm": ml.rpm,
        "span_m": ml.span_m,
        "mass_flow_kg_s": ml.mass_flow_kg_s,
        "power_w": ml.power_w,
        "tip_mach_proxy": ml.tip_mach_proxy,
        "u_m_s": ml.u_m_s,
    }
    out["exports"] = rep.exports
    if rep.exports.get("surface_csv"):
        out["surface_csv"] = rep.exports["surface_csv"]
    if rep.exports.get("loss_json"):
        out["loss_json"] = rep.exports["loss_json"]
    if rep.exports.get("design_package_json"):
        out["design_package_json"] = rep.exports["design_package_json"]
    # plots
    if "cp" in rep.plots:
        out["plot_png_base64"] = rep.plots["cp"]
    out["plots"] = rep.plots
    return jsonify(out)


@app.route("/api/analyze_loss", methods=["GET", "POST"], strict_slashes=False)
@app.route("/api/analyze_losses", methods=["GET", "POST"], strict_slashes=False)
def api_analyze_loss():
    """Shock / loss diagnostics + ranked design fixes from surface pressure.

    Accepts GET or POST so form navigation / redirects never yield opaque 405 HTML.
    """
    data = request.get_json(force=True, silent=True) or {}
    # Also accept query-string case_dir for simple GET checks
    if request.method == "GET" and request.args:
        for key in ("case_dir", "p1_pa", "w1_m_s", "chord_m", "beta1_deg", "beta2_deg", "mach_w1", "solidity"):
            if key in request.args and key not in data:
                data[key] = request.args.get(key)
    try:
        rep, _ml, surf = _design_report_from_request(data)
        return jsonify({
            "success": True,
            "surface": surf.to_dict(),
            "report": rep.loss_report.to_dict(),
            "metrics": rep.metrics.to_dict(),
            "design_report": rep.to_dict(),
            "summary": rep.summary,
            "ranked_fixes": rep.ranked_fixes,
            "exports": rep.exports,
            "plots": rep.plots,
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc), "message": str(exc)}), 400


@app.post("/api/run_job")
def api_run_job():
    """Full JSON job: geometry + gas/flow → case (+ optional OpenFOAM) → p(s) + loss report."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        res = run_cascade_job(
            data,
            output_dir=data.get("output_dir") or "output",
            run_mesh=data.get("run_mesh"),
            run_solve=data.get("run_solve"),
            run_sample_step=data.get("run_sample"),
        )
        return jsonify(res.to_dict())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "message": str(exc), "error": str(exc)}), 400


@app.post("/api/video")
def api_video():
    data = request.get_json(force=True, silent=True) or {}
    case_dir = data.get("case_dir") or ""
    if not case_dir or not Path(case_dir).is_dir():
        return jsonify({"status": "failed", "message": "invalid case_dir"}), 400
    # Engineering defaults: Mach + streamlines + vectors (+ optional gradients)
    fields = list(data.get("fields") or [])
    if not fields:
        from impulsecalc.technical_video import DEFAULT_VIDEO_FIELDS

        fields = list(DEFAULT_VIDEO_FIELDS)
    try:
        steady_hold_s = float(data.get("steady_hold_s") if data.get("steady_hold_s") is not None else 1.0)
    except (TypeError, ValueError):
        steady_hold_s = 1.0
    opts = VideoOptions(
        fields=fields,
        resolution=str(data.get("resolution") or "1080p"),
        fps=int(data.get("fps") or 12),
        duration_mode=str(data.get("duration_mode") or "full"),
        view_preset=str(data.get("view_preset") or "blade_passage_shocks"),
        output_format=str(data.get("output_format") or "mp4"),
        blade_name=str(data.get("blade_name") or "user_stage_r040"),
        inlet_p1_pa=data.get("inlet_p1_pa"),
        inlet_t1_k=data.get("inlet_t1_k"),
        beta1_deg=data.get("beta1_deg"),
        mach_w1=data.get("mach_w1"),
        gamma=data.get("gamma"),
        r_specific=data.get("r_specific") or data.get("r_specific_j_kg_k"),
        run_pvbatch=bool(data.get("run_pvbatch", True)),
        show_blades=bool(data.get("show_blades", True)),
        steady_hold_s=steady_hold_s,
    )
    res = generate_technical_video(case_dir, opts, run_pvbatch=opts.run_pvbatch)
    return jsonify(res.to_dict())


@app.get("/api/workflow")
def api_workflow():
    case_dir = request.args.get("case_dir") or ""
    return jsonify(workflow_status(case_dir or None))


@app.post("/api/save_design")
def api_save_design():
    global _LAST_DESIGN
    data = request.get_json(force=True, silent=True) or {}
    ml = data.get("meanline") or {}
    state = WorkflowState(
        meanline_inputs=ml,
        case_dir=data.get("case_dir"),
        output_dir=str(data.get("output_dir") or "output"),
    )
    out_dir = Path(state.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "design.json"
    save_design(out, state)
    _LAST_DESIGN = state.to_dict()
    # Full comparable package (v3) if client sent it — write JSON + comparison CSV
    if data.get("package"):
        from impulsecalc.design_package import (
            PACKAGE_FORMAT,
            assemble_comparable_package,
            write_comparable_package,
        )

        raw = data["package"]
        # If client already sent a v3-ish package, still re-assemble for CSV companions
        pkg = assemble_comparable_package(
            operating=raw.get("operating") or {},
            metrics=raw.get("metrics") or {},
            meanline_inputs=raw.get("meanline_inputs") or raw.get("meanline") or ml,
            meanline_result=raw.get("meanline_result") or {},
            blade_shape=raw.get("blade_shape") or data.get("blade_shape") or {},
            domain=raw.get("domain") or {},
            stations=raw.get("stations") or [],
            surface_table=raw.get("surface_table") or [],
            shocks=raw.get("shocks") or [],
            shock_relations_table=raw.get("shock_relations_table") or raw.get("shocks") or [],
            loss_report=raw.get("loss_report") or {},
            industry_advice=raw.get("industry_advice") or {},
            ranked_fixes=raw.get("ranked_fixes") or [],
            summary=raw.get("summary") or "",
            case_dir=data.get("case_dir") or raw.get("case_dir"),
            export_paths=raw.get("exports") or {},
            notes=list(raw.get("notes") or []) + ["saved_via_api_save_design"],
            blade_name=raw.get("blade_name")
            or (ml.get("blade_name") if isinstance(ml, dict) else None),
        )
        # Preserve extra client keys
        for k in ("normal_shock_chart", "surface_summary", "iteration_checklist", "description"):
            if k in raw and k not in pkg:
                pkg[k] = raw[k]
        written = write_comparable_package(out_dir, pkg, filename="design_package.json")
        return jsonify(
            {
                "path": written.get("design_package_json"),
                "ok": True,
                "design": str(out.resolve()),
                "exports": written,
                "format": PACKAGE_FORMAT,
            }
        )
    return jsonify({"path": str(out.resolve()), "ok": True})


@app.get("/api/download_design")
def api_download_design():
    global _LAST_DESIGN
    path = OUTPUT_DIR / "design.json"
    if path.is_file():
        return send_from_directory(path.parent, path.name, as_attachment=True)
    if _LAST_DESIGN:
        return jsonify(_LAST_DESIGN)
    return jsonify({"error": "no design saved yet"}), 404


@app.after_request
def _cors_for_library(resp):
    """Allow LPRE Library (other localhost port) to health-check / fetch exports."""
    origin = request.headers.get("Origin") or ""
    if origin.startswith("http://127.0.0.1:") or origin.startswith("http://localhost:"):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


def _library_snapshot_from_disk() -> dict[str, Any]:
    """Build a Library-friendly snapshot from on-disk + last design."""
    global _LAST_DESIGN
    out: dict[str, Any] = {
        "source": "impulsecalc",
        "ok": True,
        "live_form": False,
    }
    handoff = OUTPUT_DIR / "library_handoff.json"
    pkg_path = OUTPUT_DIR / "design_package.json"
    design_path = OUTPUT_DIR / "design.json"
    if handoff.is_file():
        try:
            live = json.loads(handoff.read_text(encoding="utf-8"))
            live["ok"] = True
            live["from_handoff"] = True
            return live
        except Exception as exc:
            out["handoff_error"] = str(exc)
    if pkg_path.is_file():
        try:
            out["package"] = json.loads(pkg_path.read_text(encoding="utf-8"))
        except Exception as exc:
            out["package_error"] = str(exc)
    if design_path.is_file():
        try:
            out["design"] = json.loads(design_path.read_text(encoding="utf-8"))
        except Exception as exc:
            out["design_error"] = str(exc)
    if _LAST_DESIGN:
        out["last_design"] = _LAST_DESIGN
    interfaces: dict[str, Any] = {}
    ml: dict[str, Any] = {}
    if isinstance(out.get("design"), dict):
        ml = out["design"].get("meanline_inputs") or {}
    if isinstance(out.get("package"), dict):
        ml = out["package"].get("meanline_inputs") or ml
        metrics = out["package"].get("metrics") or {}
        for k in ("eta_design_proxy", "euler_work_j_kg", "mach_w1", "power_w", "opening_o_s"):
            if metrics.get(k) is not None:
                interfaces["turbine_power_w" if k == "power_w" else k] = metrics[k]
    if ml:
        if ml.get("rpm") is not None:
            interfaces["shaft_rpm"] = ml["rpm"]
        if ml.get("power_target_w") is not None:
            interfaces["turbine_power_w"] = ml["power_target_w"]
        for src in (
            "chord_m",
            "solidity",
            "mean_radius_m",
            "span_m",
            "beta1_deg",
            "beta2_deg",
            "w1_m_s",
        ):
            if ml.get(src) is not None:
                interfaces[src] = ml[src]
    out["interfaces"] = interfaces
    out["checklist_updates"] = {
        "turbine_blades": "done",
        "turbine_rotor": "done",
        "turbine_system": "done",
    }
    return out


@app.get("/api/library_export")
def api_library_export():
    """Snapshot for LPRE Library (handoff file preferred, else disk design package)."""
    return jsonify(_library_snapshot_from_disk())


@app.post("/api/library_handoff")
def api_library_handoff():
    """Browser posts live form export so Library can import after user returns to its tab."""
    data = request.get_json(force=True, silent=True) or {}
    out_dir = OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["source"] = payload.get("source") or "impulsecalc"
    payload["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path = out_dir / "library_handoff.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    # Also mirror lightweight design.json meanline if present
    if payload.get("meanline"):
        try:
            design_path = out_dir / "design.json"
            design_path.write_text(
                json.dumps(
                    {
                        "format": "impulsecalc_v1",
                        "meanline_inputs": payload["meanline"],
                        "notes": ["library_handoff"],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
    return jsonify({"ok": True, "path": str(path.resolve()), "saved_at": payload["saved_at"]})


def _already_up() -> bool:
    return _probe_url("http://127.0.0.1:8765/api/health", timeout=0.8)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="ImpulseCalc standalone server")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--protocol", default="", help="impulsecalc:// URL from the OS handler")
    p.add_argument("--register-protocol", action="store_true")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args(argv)

    from impulsecalc.protocol import register_protocol

    registered = register_protocol()
    if args.register_protocol and not args.protocol:
        print("impulsecalc:// registered" if registered else "protocol register skipped")
        return 0

    url = "http://127.0.0.1:8765/calc.html"
    if _already_up():
        print("ImpulseCalc already running on http://127.0.0.1:8765/calc.html")
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return 0

    print("ImpulseCalc — standalone")
    print(f"  Open:  {url}")
    print("  This process does not start LPRE-Library or other apps.")
    print(f"  Static: {STATIC}")
    if registered:
        print("  Protocol: impulsecalc://  (share links can reopen this app)")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=int(args.port), debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
