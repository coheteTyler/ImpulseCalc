"""OpenFOAM mesh / solver / sample via subprocess.

Discovers tools from:
  1) PATH (native)
  2) OPENFOAM_DIR / BLUECFD_DIR environment variables
  3) Common blueCFD-Core / ESI install locations on Windows
  4) WSL Ubuntu OpenFOAM (multiple bashrc paths)
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass
class RunResult:
    success: bool
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    message: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "command": list(self.command),
            "returncode": self.returncode,
            "stdout_tail": (self.stdout or "")[-1500:],
            "stderr_tail": (self.stderr or "")[-1500:],
            "message": self.message,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

_TOOLS = (
    "blockMesh",
    "checkMesh",
    "topoSet",
    "subsetMesh",
    "createPatch",
    "snappyHexMesh",
    "rhoCentralFoam",
    "foamRun",
    "postProcess",
)

_BLUECFD_GLOBS = (
    r"C:\blueCFD*",
    r"C:\Program Files\blueCFD*",
    r"C:\Program Files (x86)\blueCFD*",
    r"D:\blueCFD*",
    r"C:\Users\tyler\blueCFD*",
    r"C:\Users\tyler\AppData\Local\Programs\blueCFD*",
)

_WSL_BASHRC_CANDIDATES = (
    "/opt/openfoam12/etc/bashrc",
    "/opt/openfoam11/etc/bashrc",
    "/opt/openfoam10/etc/bashrc",
    "/opt/openfoam*/etc/bashrc",
    "/usr/lib/openfoam/openfoam*/etc/bashrc",
    "$HOME/OpenFOAM/OpenFOAM-*/etc/bashrc",
    "/usr/share/openfoam/etc/bashrc",
)


def _wsl_path(case_dir: Path) -> str:
    win = str(case_dir.resolve()).replace("\\", "/")
    if len(win) >= 2 and win[1] == ":":
        return f"/mnt/{win[0].lower()}{win[2:]}"
    return win


def _find_bluecfd_roots() -> list[Path]:
    roots: list[Path] = []
    for env_key in ("BLUECFD_DIR", "OPENFOAM_DIR", "WM_PROJECT_DIR"):
        v = os.environ.get(env_key, "").strip()
        if v and Path(v).is_dir():
            roots.append(Path(v))
    for pattern in _BLUECFD_GLOBS:
        for p in glob.glob(pattern):
            roots.append(Path(p))
    # Dedup
    out: list[Path] = []
    seen: set[str] = set()
    for r in roots:
        key = str(r.resolve()).lower()
        if key not in seen and r.is_dir():
            seen.add(key)
            out.append(r)
    return out


def _find_native_tool(tool: str) -> str | None:
    """Return absolute path to tool if on PATH or in known install trees."""
    w = shutil.which(tool)
    if w:
        return w
    # blueCFD / MinGW style: platforms/*/bin/blockMesh.exe
    names = [f"{tool}.exe", tool]
    for root in _find_bluecfd_roots():
        for name in names:
            hits = list(root.rglob(name))
            # Prefer platforms bin
            hits.sort(key=lambda p: (0 if "platforms" in str(p).lower() else 1, len(str(p))))
            for h in hits:
                if h.is_file():
                    return str(h)
    return None


def _wsl_source_prefix() -> str:
    """Bash snippet that activates OpenFOAM inside WSL.

    Prefer openfoam.com install under /opt/openfoam12 (full env).
    Fall back to distro /usr/bin tools (Ubuntu package works in-case).
    """
    return (
        "set +e; "
        "if [ -f /opt/openfoam12/etc/bashrc ]; then "
        "  . /opt/openfoam12/etc/bashrc; "
        "elif [ -f /opt/openfoam11/etc/bashrc ]; then "
        "  . /opt/openfoam11/etc/bashrc; "
        "else "
        "  for f in /opt/openfoam*/etc/bashrc; do "
        "    [ -f \"$f\" ] && . \"$f\" && break; "
        "  done; "
        "fi; "
        # Known absolute bins if bashrc did not put them on PATH
        "export PATH=\"/opt/openfoam12/platforms/linux64GccDPInt32Opt/bin:"
        "/opt/openfoam12/bin:/usr/bin:$PATH\"; "
        "if [ -n \"${FOAM_APPBIN:-}\" ]; then export PATH=\"$FOAM_APPBIN:$PATH\"; fi; "
    )


# Cache only successful probes; failures are re-tried (WSL can blip with rc 0xC0000142).
_WSL_OF_CACHE: dict[str, Any] | None = None


def clear_openfoam_probe_cache() -> None:
    """Drop cached WSL OpenFOAM probe (call before mesh if last probe failed)."""
    global _WSL_OF_CACHE
    _WSL_OF_CACHE = None


def _wsl_run_probe_once(timeout_s: float = 45.0) -> dict[str, Any]:
    """Single attempt: prefer direct binary paths (no interactive bashrc required)."""
    if not shutil.which("wsl"):
        return {"ok": False, "reason": "wsl_not_found", "returncode": None, "stdout": ""}

    # Attempt A: bare existence checks (fast, resilient when bashrc is slow)
    simple = (
        "set +e; "
        "BM=''; "
        "for c in "
        "/opt/openfoam12/platforms/linux64GccDPInt32Opt/bin/blockMesh "
        "/usr/bin/blockMesh "
        "/bin/blockMesh; do "
        "  if [ -x \"$c\" ]; then BM=$c; break; fi; "
        "done; "
        "if [ -n \"$BM\" ]; then echo OF_OK; echo \"$BM\"; exit 0; fi; "
        "if command -v blockMesh >/dev/null 2>&1; then "
        "  echo OF_OK; command -v blockMesh; exit 0; fi; "
        "echo OF_MISSING; exit 1"
    )
    attempts: list[list[str]] = [
        ["wsl", "-d", "Ubuntu", "--", "bash", "-lc", simple],
        # Attempt B: source bashrc then which
        [
            "wsl",
            "-d",
            "Ubuntu",
            "--",
            "bash",
            "-lc",
            _wsl_source_prefix()
            + "if command -v blockMesh >/dev/null 2>&1; then "
            "echo OF_OK; command -v blockMesh; else echo OF_MISSING; fi",
        ],
        # Attempt C: default distro if not named Ubuntu
        ["wsl", "--", "bash", "-lc", simple],
    ]

    last: dict[str, Any] = {"ok": False, "reason": "openfoam_not_in_wsl", "returncode": None, "stdout": ""}
    for cmd in attempts:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            last = {"ok": False, "reason": f"timeout:{exc}", "returncode": None, "stdout": ""}
            continue
        except OSError as exc:
            last = {"ok": False, "reason": str(exc), "returncode": None, "stdout": ""}
            continue

        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        # Windows sometimes returns STATUS_DLL_INIT_FAILED (3221225794) on flaky WSL starts
        if proc.returncode in (3221225794, 4294967295) or proc.returncode == -1073741515:
            last = {
                "ok": False,
                "reason": f"wsl_spawn_failed_rc={proc.returncode}",
                "returncode": proc.returncode,
                "stdout": out[-400:],
            }
            continue

        if "OF_OK" in out:
            block = None
            for line in (proc.stdout or "").splitlines():
                s = line.strip()
                if s.endswith("blockMesh") or "/blockMesh" in s:
                    block = s
                    break
            return {
                "ok": True,
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[-800:],
                "blockMesh": block or "/usr/bin/blockMesh",
                "WM_PROJECT_DIR": "/opt/openfoam12" if "openfoam12" in out else None,
                "reason": "ok",
            }
        last = {
            "ok": False,
            "reason": "openfoam_not_in_wsl",
            "returncode": proc.returncode,
            "stdout": out[-800:],
            "blockMesh": None,
            "WM_PROJECT_DIR": None,
        }
    return last


def _wsl_openfoam_probe(*, force: bool = False) -> dict[str, Any]:
    """Probe WSL for blockMesh. Successful results are cached; failures are not."""
    global _WSL_OF_CACHE
    if not force and _WSL_OF_CACHE is not None and _WSL_OF_CACHE.get("ok"):
        return _WSL_OF_CACHE

    # Up to 3 tries — first WSL call after idle often fails with 0xC0000142
    result: dict[str, Any] = {"ok": False, "reason": "openfoam_not_in_wsl"}
    for attempt in range(3):
        result = _wsl_run_probe_once(timeout_s=50.0 + 15.0 * attempt)
        if result.get("ok"):
            _WSL_OF_CACHE = result
            return result
        # Brief backoff before retry (helps WSL after concurrent spawn)
        try:
            import time

            time.sleep(0.4 * (attempt + 1))
        except Exception:  # noqa: BLE001
            pass

    # Do not cache failure — next mesh click re-probes
    _WSL_OF_CACHE = None
    return result


def openfoam_available(*, force_probe: bool = False) -> dict[str, Any]:
    tools = {t: _find_native_tool(t) for t in _TOOLS}
    native = any(tools.values())
    wsl = bool(shutil.which("wsl"))
    wsl_of = _wsl_openfoam_probe(force=force_probe) if wsl else {"ok": False, "reason": "no_wsl"}
    blue = [str(p) for p in _find_bluecfd_roots()]
    available = native or bool(wsl_of.get("ok"))
    if native:
        msg = "OpenFOAM tools found on Windows PATH / install tree"
    elif wsl_of.get("ok"):
        msg = f"OpenFOAM available via WSL ({wsl_of.get('WM_PROJECT_DIR') or 'Ubuntu'})"
        if wsl_of.get("blockMesh"):
            msg += f" · {wsl_of.get('blockMesh')}"
    elif wsl:
        reason = wsl_of.get("reason") or "unknown"
        if "wsl_spawn" in str(reason) or "timeout" in str(reason):
            msg = (
                f"WSL OpenFOAM probe failed ({reason}). "
                "Retry mesh; if it persists run: wsl -d Ubuntu -- blockMesh -help"
            )
        else:
            msg = (
                "WSL present but OpenFOAM not found in Ubuntu — "
                "run scripts/install_openfoam_wsl.ps1"
            )
    else:
        msg = "No OpenFOAM found — install blueCFD-Core or OpenFOAM in WSL"
    return {
        "available": available,
        "tools": tools,
        "wsl": wsl,
        "wsl_openfoam": wsl_of,
        "bluecfd_roots": blue,
        "message": msg,
    }


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _friendly_solver_failure(stdout: str, stderr: str, returncode: int | None) -> str:
    """Map OpenFOAM crash signatures to actionable ImpulseCalc messages."""
    blob = f"{stdout or ''}\n{stderr or ''}"
    low = blob.lower()
    # Only apply shockFluid FPE help when the log actually looks like a solver crash
    if (
        ("floating point exception" in low or "sigfpe" in low or "fluxpredictor" in low)
        and ("shockfluid" in low or "foamrun" in low or "rhocentral" in low or returncode in (136, -8))
    ):
        return (
            "shockFluid floating-point crash (rc=136, usually T≤0 or ρ≤0 in fluxPredictor). "
            "Case uses Tadmor+Minmod, maxCo≈0.03–0.10 (Mach-aware), pure-impulse p_out≈0.95 p1. "
            "Rebuild mesh (§3) and re-run solve (§4). If it still fails: lower Mw1, reduce t/c "
            "to paper ~0.18, or shorten endTime (see docs/PAPER_VALIDATION.md)."
        )
    if "FOAM FATAL" in blob or "FOAM FATAL ERROR" in blob:
        # last fatal line
        for line in reversed(blob.splitlines()):
            if "FOAM FATAL" in line or "--> FOAM FATAL" in line:
                return line.strip()[:400]
    return ""


def _run(
    cmd: list[str],
    cwd: Path,
    timeout_s: float | None = 600,
    env: dict[str, str] | None = None,
) -> RunResult:
    """Run a subprocess. ``timeout_s=None`` means *no* wall-clock limit (high-accuracy)."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,  # None → unlimited (multi-hour mesh/solve OK)
            check=False,
            env=env or os.environ.copy(),
        )
        ok = proc.returncode == 0
        msg = "ok"
        if not ok:
            friendly = _friendly_solver_failure(
                proc.stdout or "", proc.stderr or "", proc.returncode
            )
            msg = friendly or f"rc={proc.returncode}"
            if not friendly:
                tail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()[-300:]
                if tail:
                    msg = f"rc={proc.returncode}: {tail}"
        notes: list[str] = []
        if timeout_s is None:
            notes.append("timeout=unlimited")
        return RunResult(
            ok,
            cmd,
            proc.returncode,
            proc.stdout or "",
            proc.stderr or "",
            msg,
            notes,
        )
    except FileNotFoundError as exc:
        return RunResult(
            False, cmd, None, "", str(exc), f"not found: {cmd[0]}", ["tool_missing"]
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            False,
            cmd,
            None,
            "",
            "timeout",
            f"timeout {timeout_s}s",
            ["timeout"],
        )


def _bluecfd_env(tool_path: str) -> dict[str, str]:
    """Best-effort PATH enrichment when invoking a blueCFD binary directly."""
    env = os.environ.copy()
    p = Path(tool_path)
    bin_dir = str(p.parent)
    # Walk up to find msys64/usr/bin and platforms lib
    path_parts = [bin_dir]
    for parent in p.parents:
        msys = parent / "msys64" / "usr" / "bin"
        if msys.is_dir():
            path_parts.append(str(msys))
        mingw = parent / "msys64" / "mingw64" / "bin"
        if mingw.is_dir():
            path_parts.append(str(mingw))
    env["PATH"] = os.pathsep.join(path_parts + [env.get("PATH", "")])
    return env


def _of_cmd(
    tool: str,
    case_dir: Path,
    extra: list[str] | None = None,
    *,
    timeout_s: float | None = 600,
) -> RunResult:
    extra = extra or []
    native = _find_native_tool(tool)
    if native:
        env = _bluecfd_env(native) if "bluecfd" in native.lower() or "openfoam" in native.lower() else None
        return _run([native, *extra], case_dir, timeout_s=timeout_s, env=env)

    # WSL OpenFOAM
    wsl_of = _wsl_openfoam_probe()
    if wsl_of.get("ok") and shutil.which("wsl"):
        wp = _wsl_path(case_dir)
        args = " ".join([tool, *extra])
        bash = _wsl_source_prefix() + f'cd "{wp}" && {args}'
        return _run(
            ["wsl", "-d", "Ubuntu", "--", "bash", "-lc", bash],
            case_dir,
            timeout_s=timeout_s,
        )

    return RunResult(
        False,
        [tool],
        None,
        "",
        "",
        f"{tool} not found (install blueCFD-Core or OpenFOAM in WSL)",
        ["tool_missing", "hint: run ImpulseCalc/scripts/install_openfoam_wsl.ps1"],
    )


def timeouts_from_case_meta(case_dir: str | Path) -> dict[str, float | None]:
    """Read runner budgets written by generate_openfoam_case (fidelity mode)."""
    import json

    meta_path = Path(case_dir) / "impulsecalc_case_meta.json"
    defaults: dict[str, float | None] = {
        "mesh_timeout_s": 600.0,
        "solve_timeout_s": 1800.0,
    }
    if not meta_path.is_file():
        return defaults
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return defaults
    rt = meta.get("runner_timeouts") or {}
    fid = meta.get("fidelity") or {}
    mesh_t = rt.get("mesh_timeout_s", fid.get("mesh_timeout_s", defaults["mesh_timeout_s"]))
    solve_t = rt.get("solve_timeout_s", fid.get("solve_timeout_s", defaults["solve_timeout_s"]))
    # JSON null → None (unlimited)
    return {
        "mesh_timeout_s": None if mesh_t is None else float(mesh_t),
        "solve_timeout_s": None if solve_t is None else float(solve_t),
    }


def run_blockmesh(case_dir: str | Path, timeout_s: float | None = 600) -> RunResult:
    return _of_cmd("blockMesh", Path(case_dir), timeout_s=timeout_s)


def run_checkmesh(case_dir: str | Path, timeout_s: float | None = 600) -> RunResult:
    return _of_cmd("checkMesh", Path(case_dir), timeout_s=timeout_s)


_SOLVE_TIMEOUT_UNSET = object()


def run_solver(
    case_dir: str | Path,
    timeout_s: float | None | object = _SOLVE_TIMEOUT_UNSET,
) -> RunResult:
    """Run compressible density-based solver (OF-12: foamRun -solver shockFluid).

    ``timeout_s``: seconds, or ``None`` for unlimited (high-accuracy multi-hour runs).
    If omitted, reads fidelity budgets from case meta (default 1800 s for fast mode).
    """
    cdir = Path(case_dir)
    if timeout_s is _SOLVE_TIMEOUT_UNSET:
        timeout_s = timeouts_from_case_meta(cdir).get("solve_timeout_s", 1800.0)
    # Always start from clean 0/ — leftover times from a prior FPE mid-run are toxic.
    try:
        from .openfoam_case import clean_case_time_dirs

        cleaned = clean_case_time_dirs(cdir, keep_zero=True)
    except Exception:  # noqa: BLE001
        cleaned = []

    t_note = "timeout=unlimited" if timeout_s is None else f"timeout_s={timeout_s}"

    # Prefer modern modular solver; rhoCentralFoam is only a redirect on OF-12.
    if _find_native_tool("foamRun"):
        r = _of_cmd(
            "foamRun", cdir, ["-solver", "shockFluid"], timeout_s=timeout_s  # type: ignore[arg-type]
        )
        r.notes = list(r.notes) + ["solver=foamRun/shockFluid", t_note]
        if cleaned:
            r.notes.append(f"cleaned_times={len(cleaned)}")
        return r
    if _find_native_tool("rhoCentralFoam"):
        r = _of_cmd("rhoCentralFoam", cdir, timeout_s=timeout_s)  # type: ignore[arg-type]
        r.notes = list(r.notes) + ["solver=rhoCentralFoam", t_note]
        if cleaned:
            r.notes.append(f"cleaned_times={len(cleaned)}")
        return r

    wsl_of = _wsl_openfoam_probe()
    if wsl_of.get("ok") and shutil.which("wsl"):
        wp = _wsl_path(cdir)
        bash = (
            _wsl_source_prefix()
            + f'cd "{wp}" && '
            # Prefer foamRun; fall back to rhoCentralFoam shim
            "(foamRun -solver shockFluid || rhoCentralFoam)"
        )
        r = _run(
            ["wsl", "-d", "Ubuntu", "--", "bash", "-lc", bash],
            cdir,
            timeout_s=timeout_s,  # type: ignore[arg-type]
        )
        r.notes = list(r.notes) + ["solver=wsl/shockFluid", t_note]
        if cleaned:
            r.notes.append(f"cleaned_times={len(cleaned)}")
        return r

    return RunResult(
        False,
        ["foamRun", "-solver", "shockFluid"],
        None,
        "",
        "",
        "Solver not found — install OpenFOAM (WSL or blueCFD) then retry",
        ["manual_run_required", "tool_missing"],
    )


def run_sample(case_dir: str | Path, timeout_s: float | None = 600) -> RunResult:
    return _of_cmd(
        "postProcess", Path(case_dir), ["-func", "sample"], timeout_s=timeout_s
    )


def run_topo_set(case_dir: str | Path, timeout_s: float | None = 600) -> RunResult:
    return _of_cmd("topoSet", Path(case_dir), timeout_s=timeout_s)


def run_subset_mesh_blades(
    case_dir: str | Path, timeout_s: float | None = 600
) -> RunResult:
    """Keep fluidCells; exposed metal faces become ``oldInternalFaces``."""
    return _of_cmd(
        "subsetMesh",
        Path(case_dir),
        ["-overwrite", "fluidCells"],
        timeout_s=timeout_s,
    )


def run_create_patch_blades(
    case_dir: str | Path, timeout_s: float | None = 600
) -> RunResult:
    """Rename ``oldInternalFaces`` → wall ``blades`` via createPatchDict."""
    return _of_cmd(
        "createPatch", Path(case_dir), ["-overwrite"], timeout_s=timeout_s
    )


def run_snappy_hex_mesh(
    case_dir: str | Path, timeout_s: float | None = 600
) -> RunResult:
    return _of_cmd(
        "snappyHexMesh", Path(case_dir), ["-overwrite"], timeout_s=timeout_s
    )


def _mesh_path_from_case_meta(case_dir: Path) -> str:
    """Read preferred mesh path: body_fitted (industry) or stair_step (fast)."""
    meta_path = Path(case_dir) / "impulsecalc_case_meta.json"
    if not meta_path.is_file():
        return "stair_step"
    try:
        import json

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "stair_step"
    fid = meta.get("fidelity") or {}
    walls = meta.get("blade_walls") or {}
    mp = walls.get("mesh_path") or fid.get("mesh_path") or "stair_step"
    return str(mp)


def _run_topo_set_cut_path(
    cdir: Path,
    *,
    t_mesh: float | None,
    steps: dict[str, Any],
    notes: list[str],
) -> tuple[bool, str]:
    """topoSet → subsetMesh → createPatch. Returns (walls_ok, cut_method)."""
    from .openfoam_case import mesh_has_blade_walls

    ts = run_topo_set(cdir, timeout_s=t_mesh)
    steps["topoSet"] = ts.to_dict()
    notes.append(f"topoSet={ts.success}")
    walls_ok = False
    cut_method = "none"
    if not ts.success:
        steps["subsetMesh"] = {"success": False, "message": "skipped_topoSet_failed"}
        return False, cut_method
    sm = run_subset_mesh_blades(cdir, timeout_s=t_mesh)
    steps["subsetMesh"] = sm.to_dict()
    notes.append(f"subsetMesh={sm.success}")
    if not sm.success:
        notes.append("subsetMesh_failed")
        return False, cut_method
    cp = run_create_patch_blades(cdir, timeout_s=t_mesh)
    steps["createPatch"] = cp.to_dict()
    notes.append(f"createPatch={cp.success}")
    walls = mesh_has_blade_walls(cdir)
    walls_ok = bool(walls.get("ok"))
    if cp.success and walls_ok:
        cut_method = "topoSet_subsetMesh_createPatch"
        notes.append(f"blade_wall_faces={walls.get('nFaces')}")
    else:
        notes.append("createPatch_no_blade_wall")
        oi = mesh_has_blade_walls(cdir, patch="oldInternalFaces")
        if oi.get("ok"):
            walls_ok = True
            cut_method = "topoSet_subsetMesh_oldInternalFaces"
            notes.append(f"oldInternalFaces_nFaces={oi.get('nFaces')}")
    return walls_ok, cut_method


def mesh_pipeline(
    case_dir: str | Path,
    *,
    timeout_s: float | None | object = _SOLVE_TIMEOUT_UNSET,
) -> dict[str, Any]:
    """blockMesh → blade walls (body-fitted snappy primary or stair-step) → checkMesh.

    Industry fidelity (mesh_path=body_fitted): snappyHexMesh first, topoSet fallback.
    Fast path: topoSet+subsetMesh primary, snappy fallback.

    ``timeout_s``: per-step mesh tool budget; ``None`` = unlimited (high accuracy).
    If omitted, reads fidelity mesh_timeout_s from case meta.
    """
    from .openfoam_case import BLADE_WALL_PATCH, mesh_has_blade_walls

    # Always re-probe WSL (do not trust a stale failure from an earlier API click)
    clear_openfoam_probe_cache()
    probe = openfoam_available(force_probe=True)
    cdir = Path(case_dir)
    notes: list[str] = []
    steps: dict[str, Any] = {}
    if timeout_s is _SOLVE_TIMEOUT_UNSET:
        timeout_s = timeouts_from_case_meta(cdir).get("mesh_timeout_s", 600.0)
    t_mesh: float | None = timeout_s  # type: ignore[assignment]
    mesh_path = _mesh_path_from_case_meta(cdir)
    prefer_snappy = mesh_path == "body_fitted"
    notes.append(
        "mesh_timeout=unlimited" if t_mesh is None else f"mesh_timeout_s={t_mesh}"
    )
    notes.append(f"mesh_path={mesh_path}")
    notes.append(f"prefer_snappy={prefer_snappy}")
    notes.append(f"openfoam_available={probe.get('available')}")
    if not probe.get("available"):
        return {
            "blockMesh": {"success": False, "message": "skipped"},
            "checkMesh": {"success": False, "message": "skipped"},
            "success": False,
            "blade_walls": mesh_has_blade_walls(cdir),
            "openfoam": probe,
            "mesh_path": mesh_path,
            "notes": notes + ["openfoam_unavailable"],
            "detail": probe.get("message") or "OpenFOAM not available",
        }

    # Drop leftover time folders so subsetMesh only sees 0/ matching blockMesh
    try:
        from .openfoam_case import clean_case_time_dirs

        cleaned = clean_case_time_dirs(cdir, keep_zero=True)
        if cleaned:
            notes.append(f"cleaned_times={len(cleaned)}")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"clean_times_failed={exc}")

    bm = run_blockmesh(cdir, timeout_s=t_mesh)
    steps["blockMesh"] = bm.to_dict()
    notes.append(f"blockMesh={bm.success}")
    if not bm.success:
        tail = (bm.stderr or bm.stdout or "").strip()
        detail = f"{bm.message}: {tail[-400:]}" if tail else (probe.get("message") or bm.message)
        return {
            "blockMesh": bm.to_dict(),
            "checkMesh": {"success": False, "message": "skipped"},
            "success": False,
            "blade_walls": mesh_has_blade_walls(cdir),
            "openfoam": probe,
            "mesh_path": mesh_path,
            "notes": notes + ["mesh_failed_blockMesh"],
            "detail": detail,
        }

    # Critical: 0/ must NOT list blades before createPatch (OF-12 subsetMesh FPE/readField)
    try:
        from .openfoam_case import prepare_zero_for_subset_mesh

        prep = prepare_zero_for_subset_mesh(cdir)
        steps["prepareZero"] = prep
        notes.append(f"prepare_zero={prep.get('ok')}:{prep.get('method')}")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"prepare_zero_failed={exc}")

    walls_ok = False
    cut_method = "none"

    if prefer_snappy:
        # Industry primary: body-fitted snap to blades.stl
        sn = run_snappy_hex_mesh(cdir, timeout_s=t_mesh)
        steps["snappyHexMesh"] = sn.to_dict()
        notes.append(f"snappyHexMesh={sn.success}")
        walls = mesh_has_blade_walls(cdir)
        walls_ok = bool(walls.get("ok"))
        if sn.success and walls_ok:
            cut_method = "snappyHexMesh"
            notes.append(f"blade_wall_faces={walls.get('nFaces')}")
        else:
            notes.append("snappy_primary_no_walls_falling_back_topoSet")
            # Reset background mesh then stair-step cut (snappy may have corrupted polyMesh)
            bm2 = run_blockmesh(cdir, timeout_s=t_mesh)
            steps["blockMesh_retry"] = bm2.to_dict()
            notes.append(f"blockMesh_retry={bm2.success}")
            if bm2.success:
                try:
                    from .openfoam_case import prepare_zero_for_subset_mesh

                    prep2 = prepare_zero_for_subset_mesh(cdir)
                    notes.append(f"prepare_zero_retry={prep2.get('ok')}")
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"prepare_zero_retry_failed={exc}")
                walls_ok, cut_method = _run_topo_set_cut_path(
                    cdir, t_mesh=t_mesh, steps=steps, notes=notes
                )
    else:
        # Fast path primary: stair-step topoSet cut
        walls_ok, cut_method = _run_topo_set_cut_path(
            cdir, t_mesh=t_mesh, steps=steps, notes=notes
        )
        # Fallback: snappyHexMesh if wall faces still missing
        if not walls_ok:
            sn = run_snappy_hex_mesh(cdir, timeout_s=t_mesh)
            steps["snappyHexMesh"] = sn.to_dict()
            notes.append(f"snappyHexMesh={sn.success}")
            walls = mesh_has_blade_walls(cdir)
            walls_ok = bool(walls.get("ok"))
            if sn.success and walls_ok:
                cut_method = "snappyHexMesh"
                notes.append(f"blade_wall_faces={walls.get('nFaces')}")
            elif not walls_ok:
                notes.append("blade_walls_MISSING")

    if not walls_ok:
        notes.append("blade_walls_MISSING")

    cm = run_checkmesh(cdir, timeout_s=t_mesh)
    steps["checkMesh"] = cm.to_dict()
    notes.append(f"checkMesh={cm.success}")

    walls = mesh_has_blade_walls(cdir)
    success = bool(bm.success and walls.get("ok"))

    # After mesh: restore wall-aware fields (slip/noSlip + optional RANS k/ω)
    if success:
        try:
            from .openfoam_case import rewrite_zero_fields_after_mesh

            rw = rewrite_zero_fields_after_mesh(cdir)
            notes.append(f"zero_fields_synced={rw.get('ok')}")
            if rw.get("wall_bc"):
                notes.append(f"wall_bc={rw.get('wall_bc')}")
            if rw.get("turbulence_model"):
                notes.append(f"turbulence={rw.get('turbulence_model')}")
            steps["zeroFields"] = rw
        except Exception as exc:  # noqa: BLE001
            notes.append(f"zero_fields_sync_failed={exc}")

    detail = (
        f"mesh ok · {BLADE_WALL_PATCH} wall nFaces={walls.get('nFaces')} via {cut_method}"
        f" (mesh_path={mesh_path})"
        if success
        else (
            "mesh incomplete: blade wall patch missing — fluid will ignore metal "
            f"(patches={walls.get('patches')}). See MESH_PIPELINE.txt"
        )
    )
    if not success:
        for key in ("subsetMesh", "createPatch", "snappyHexMesh", "topoSet", "blockMesh"):
            st = steps.get(key) or {}
            if st and not st.get("success"):
                tail = (st.get("stderr_tail") or st.get("stdout_tail") or st.get("message") or "")[
                    -300:
                ]
                if tail:
                    detail = f"{detail} | {key}: {tail}"
                break

    return {
        "blockMesh": steps.get("blockMesh"),
        "topoSet": steps.get("topoSet"),
        "subsetMesh": steps.get("subsetMesh"),
        "createPatch": steps.get("createPatch"),
        "snappyHexMesh": steps.get("snappyHexMesh"),
        "checkMesh": steps.get("checkMesh"),
        "success": success,
        "blade_walls": walls,
        "cut_method": cut_method,
        "mesh_path": mesh_path,
        "prefer_snappy": prefer_snappy,
        "openfoam": probe,
        "notes": notes + [probe.get("message", "")],
        "detail": detail,
    }
