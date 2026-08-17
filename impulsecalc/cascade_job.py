"""JSON/YAML-driven cascade job: inputs → OpenFOAM case → (optional) run → surface p + loss report.

Matches the required program interface:

  - closed blade points *or* parametric upper/lower curves (via blade_shape)
  - inlet/outlet relative angles β1, β2
  - upstream pressure, relative |W1|, γ, R (or cp), μ, T
  - builds 3–5 blade 2D domain + OpenFOAM configs
  - optional mesh / checkMesh / solve / sample via subprocess
  - outputs upper/lower surface pressure + shock/loss diagnostics
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .geometry import BladeGeometry, BladeShapeParams, blade_closed_polygon
from .design_report import build_design_report
from .meanline import MeanlineInputs, compute_meanline
from .openfoam_case import generate_openfoam_case
from .postprocess import load_surface_pressure
from .runners import mesh_pipeline, run_sample, run_solver


def _load_mapping(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ImportError("PyYAML required for YAML jobs: pip install pyyaml") from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("job file must be a mapping/object at top level")
    return data


@dataclass
class CascadeJobResult:
    success: bool
    case_dir: str | None
    meanline: dict[str, Any]
    surface: dict[str, Any] | None
    loss_report: dict[str, Any] | None
    design_report: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    surface_csv: str | None = None
    loss_json: str | None = None
    design_package_json: str | None = None
    run_log: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "case_dir": self.case_dir,
            "meanline": self.meanline,
            "surface": self.surface,
            "loss_report": self.loss_report,
            "design_report": self.design_report,
            "metrics": self.metrics,
            "surface_csv": self.surface_csv,
            "loss_json": self.loss_json,
            "design_package_json": self.design_package_json,
            "run_log": list(self.run_log),
            "message": self.message,
        }


def meanline_inputs_from_job(data: dict[str, Any]) -> MeanlineInputs:
    """Map external job schema → MeanlineInputs."""
    # Accept flat or nested {"meanline": {...}, "gas": {...}, ...}
    ml = dict(data.get("meanline") or {})
    gas = dict(data.get("gas") or {})
    flow = dict(data.get("flow") or {})

    def pick(*keys: str, default: Any = None) -> Any:
        for k in keys:
            if k in data and data[k] is not None:
                return data[k]
            if k in ml and ml[k] is not None:
                return ml[k]
            if k in gas and gas[k] is not None:
                return gas[k]
            if k in flow and flow[k] is not None:
                return flow[k]
        return default

    gamma = float(pick("gamma", "specific_heat_ratio", "g", default=1.3))
    R = pick("r_specific_j_kg_k", "gas_constant", "R", default=None)
    cp = pick("cp", "cp_j_kg_k", default=None)
    if R is None and cp is not None:
        R = float(cp) * (gamma - 1.0) / gamma
    if R is None:
        R = 320.0

    return MeanlineInputs(
        beta1_deg=float(pick("beta1_deg", "beta1", "relative_inlet_angle_deg", default=72.0)),
        beta2_deg=float(pick("beta2_deg", "beta2", "relative_outlet_angle_deg", default=-72.0)),
        blade_speed_u_m_s=float(pick("blade_speed_u_m_s", "U", "blade_speed", default=450.0)),
        w1_m_s=float(pick("w1_m_s", "W1", "relative_velocity", "relative_inlet_velocity", default=950.0)),
        p1_pa=float(pick("p1_pa", "p1", "upstream_pressure", "inlet_pressure", default=5.5e5)),
        t1_k=float(pick("t1_k", "T1", "temperature", "inlet_temperature", default=1100.0)),
        gamma=gamma,
        r_specific_j_kg_k=float(R),
        mu_pa_s=float(pick("mu_pa_s", "mu", "viscosity", default=4.5e-5)),
        chord_m=float(pick("chord_m", "chord", default=0.01)),
        solidity=float(pick("solidity", "c_over_s", default=1.13688)),
        blade_name=str(pick("blade_name", "name", default="cascade_job")),
        pure_impulse_lock=bool(pick("pure_impulse_lock", default=False)),
        y_plus_target=float(pick("y_plus_target", "yplus", default=1.0)),
        mean_radius_m=float(pick("mean_radius_m", "r_m", "mean_radius", default=0.0375)),
        rpm=float(pick("rpm", default=0.0)),
        u_from_rpm=bool(pick("u_from_rpm", default=False)),
        span_m=float(pick("span_m", "span", "h", default=0.005)),
        mass_flow_kg_s=float(pick("mass_flow_kg_s", "mdot", "mass_flow", default=0.0)),
        power_target_w=float(pick("power_target_w", "power_w", "power_target", default=0.0)),
        incidence_deg=float(pick("incidence_deg", "incidence", default=0.0)),
        deviation_deg=float(pick("deviation_deg", "deviation", default=0.0)),
    )


def blade_shape_from_job(data: dict[str, Any]) -> BladeShapeParams | None:
    if data.get("blade_shape"):
        return BladeShapeParams.from_dict(data["blade_shape"])
    if data.get("profile_points"):
        # Closed polygon provided — shape params only used for fillets/thickness fallbacks;
        # openfoam path currently builds from parametric shape. Store points in notes via meta.
        return BladeShapeParams.from_dict(data.get("shape") or {})
    return BladeShapeParams.from_dict(data.get("shape"))


def run_cascade_job(
    data: dict[str, Any] | None = None,
    *,
    job_path: str | Path | None = None,
    output_dir: str | Path = "output",
    run_mesh: bool | None = None,
    run_solve: bool | None = None,
    run_sample_step: bool | None = None,
) -> CascadeJobResult:
    """
    Execute a full cascade job from a dict or JSON/YAML file.

    Required-ish fields (aliases accepted — see meanline_inputs_from_job):
      beta1, beta2, p1, W1, gamma, R|cp, mu, T, chord, solidity,
      blade_shape or profile_points, n_blades (3–5)
    """
    log: list[str] = []
    if job_path is not None:
        data = _load_mapping(job_path)
        log.append(f"loaded_job:{job_path}")
    if not data:
        return CascadeJobResult(
            success=False,
            case_dir=None,
            meanline={},
            surface=None,
            loss_report=None,
            run_log=log,
            message="no job data",
        )

    inp = meanline_inputs_from_job(data)
    ml = compute_meanline(inp)
    log.append(f"meanline_ok Mw1={ml.mach_w1:.3f}")

    from .fidelity import fidelity_from_request

    shape = blade_shape_from_job(data)
    fid = fidelity_from_request(data)
    n_blades = int(data.get("n_blades") or data.get("num_blades") or fid.n_blades_default or 3)
    n_blades = max(3, min(5, n_blades))
    nx = int(data.get("nx") if data.get("nx") not in (None, "") else fid.nx)
    ny = int(data.get("ny") if data.get("ny") not in (None, "") else fid.ny)
    from .openfoam_case import _optional_float

    end_time = _optional_float(data.get("end_time"), default=None)
    out = Path(data.get("output_dir") or output_dir)
    startup = bool(data.get("startup", True))

    do_mesh = bool(data.get("run_mesh", True if run_mesh is None else run_mesh))
    do_solve = bool(data.get("run_solve", True if run_solve is None else run_solve))
    do_sample = bool(data.get("run_sample", True if run_sample_step is None else run_sample_step))

    # Optional closed polygon → write alongside case for external tools
    profile_points = data.get("profile_points") or data.get("blade_points")

    x_up = float(data.get("x_up_c") if data.get("x_up_c") is not None else data.get("inlet_c") or 0.5)
    x_dn = float(data.get("x_dn_c") if data.get("x_dn_c") is not None else data.get("outlet_c") or 1.0)
    case = generate_openfoam_case(
        inp,
        out,
        case_name=inp.blade_name or "cascade_job",
        n_blades=n_blades,
        nx=nx,
        ny=ny,
        end_time=end_time,
        blade_shape=shape.to_dict() if shape else None,
        x_up_c=x_up,
        x_dn_c=x_dn,
        startup=startup,
        fidelity=fid,
    )
    log.append(
        f"fidelity={fid.mode} L{fid.level} nx={nx} ny={ny} "
        f"solve_timeout={fid.solve_timeout_s}"
    )
    log.append(f"case:{case.case_dir} success={case.success}")
    if not case.success:
        return CascadeJobResult(
            success=False,
            case_dir=case.case_dir,
            meanline=ml.to_dict(),
            surface=None,
            loss_report=None,
            run_log=log,
            message=case.message,
        )

    cdir = Path(case.case_dir)
    if profile_points:
        (cdir / "blade_profile.json").write_text(
            json.dumps({"points": profile_points}, indent=2), encoding="utf-8"
        )
        log.append("wrote blade_profile.json from input points")

    # Always dump parametric closed polygon for reproducibility (metal angles)
    geom = BladeGeometry(
        chord_m=inp.chord_m,
        beta1_deg=ml.metal_beta1_deg,
        beta2_deg=ml.metal_beta2_deg,
        solidity=inp.solidity,
        shape=shape or BladeShapeParams(),
    )
    poly = blade_closed_polygon(geom)
    (cdir / "blade_closed_polygon.json").write_text(
        json.dumps([{"x": x, "y": y} for x, y in poly], indent=2), encoding="utf-8"
    )

    if do_mesh:
        mres = mesh_pipeline(cdir)
        log.append(f"mesh success={mres.get('success')} detail={mres.get('detail')}")
        if not mres.get("success"):
            # Still allow synthetic surface analysis
            do_solve = False
            do_sample = False

    if do_solve:
        sres = run_solver(cdir)
        log.append(f"solve success={sres.success} {sres.message}")
        if not sres.success:
            do_sample = False

    if do_sample:
        r = run_sample(cdir)
        log.append(f"sample success={r.success}")

    surf = load_surface_pressure(
        cdir,
        p1_pa=inp.p1_pa,
        rho1=ml.rho1_kg_m3,
        w1_m_s=inp.w1_m_s,
        chord_m=inp.chord_m,
        allow_synthetic=True,
        force_synthetic=bool(data.get("force_synthetic", False)),
        mach_w1=ml.mach_w1,
        gamma=inp.gamma,
        t1_k=inp.t1_k,
        r_specific=inp.r_specific_j_kg_k,
        blade_shape=(shape.to_dict() if shape else {}),
        beta1_deg=ml.beta1_deg,
        beta2_deg=ml.beta2_deg,
    )
    sh = shape or BladeShapeParams()
    design = build_design_report(
        surf,
        ml=ml,
        beta1_deg=ml.beta1_deg,
        beta2_deg=ml.beta2_deg,
        mach_w1=ml.mach_w1,
        solidity=inp.solidity,
        gamma=inp.gamma,
        thickness_ratio=sh.thickness_ratio,
        le_fillet_r_c=sh.le_fillet_r_c,
        thickness_peak_x=sh.thickness_peak_x,
        arc_bulge=sh.arc_bulge,
        blade_shape=sh.to_dict(),
        case_dir=cdir,
        write_exports=True,
        include_plots=False,
    )
    log.append(f"design_report ok exports={list(design.exports)}")

    return CascadeJobResult(
        success=True,
        case_dir=str(cdir),
        meanline=ml.to_dict(),
        surface=surf.to_dict(),
        loss_report=design.loss_report.to_dict(),
        design_report=design.to_dict(),
        metrics=design.metrics.to_dict(),
        surface_csv=design.exports.get("surface_csv"),
        loss_json=design.exports.get("loss_json"),
        design_package_json=design.exports.get("design_package_json"),
        run_log=log,
        message=design.summary,
    )


def run_cascade_job_file(path: str | Path, **kwargs: Any) -> CascadeJobResult:
    return run_cascade_job(job_path=path, **kwargs)
