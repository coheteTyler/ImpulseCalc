"""Comparable design package assembly (machine-diffable JSON + flat CSVs).

Schema ``impulsecalc_design_package_v3`` is the offline contract for comparing
two cascade design runs without the live UI.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKAGE_FORMAT = "impulsecalc_design_package_v3"
SCHEMA_VERSION = 3

# Key scalar columns for spreadsheet / git-diff comparison of two designs
COMPARISON_METRIC_KEYS = (
    "eta_design_proxy",
    "eta_meanline_proxy",
    "surface_loss_penalty",
    "mach_w1",
    "w1_m_s",
    "euler_work_j_kg",
    "stage_loading_psi",
    "flow_coeff_phi",
    "solidity",
    "lieblein_df_ss",
    "diffusion_ss",
    "peak_ss_cp",
    "peak_ss_x_c",
    "peak_ss_m_isen",
    "n_shocks",
    "max_shock_dcp",
    "strongest_shock_x_c",
    "loading_int_dcp",
    "loading_front_frac",
    "loading_mid_frac",
    "loading_aft_frac",
    "tip_mach_proxy",
    "mass_flow_kg_s",
    "power_w",
    "opening_o_s",
    "throat_o_m",
    "stagger_deg",
    "incidence_deg",
    "deviation_deg",
    "metal_beta1_deg",
    "metal_beta2_deg",
    "beta1_deg",
    "beta2_deg",
    "mean_radius_m",
    "rpm",
    "span_m",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _rel_or_abs(path: str | Path | None, root: Path | None) -> str | None:
    if path is None:
        return None
    p = Path(path)
    if root is not None:
        try:
            return str(p.resolve().relative_to(Path(root).resolve()))
        except ValueError:
            pass
    return str(p.resolve()) if p.exists() or p.is_absolute() else str(p)


def assemble_comparable_package(
    *,
    operating: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    meanline_inputs: dict[str, Any] | None = None,
    meanline_result: dict[str, Any] | None = None,
    blade_shape: dict[str, Any] | None = None,
    domain: dict[str, Any] | None = None,
    stations: list[dict[str, Any]] | None = None,
    surface_table: list[dict[str, Any]] | None = None,
    shocks: list[dict[str, Any]] | None = None,
    shock_relations_table: list[dict[str, Any]] | None = None,
    loss_report: dict[str, Any] | None = None,
    industry_advice: dict[str, Any] | None = None,
    ranked_fixes: list[str] | None = None,
    summary: str | None = None,
    case_dir: str | None = None,
    export_paths: dict[str, str] | None = None,
    notes: list[str] | None = None,
    blade_name: str | None = None,
) -> dict[str, Any]:
    """Build a versioned, comparable package dict (no disk I/O)."""
    m = dict(metrics or {})
    op = dict(operating or {})
    name = (
        blade_name
        or (meanline_inputs or {}).get("blade_name")
        or op.get("blade_name")
        or m.get("top_loss_id")
        and None
        or "impulse_design"
    )
    if not isinstance(name, str) or not name or name == "impulse_design":
        name = str(
            (meanline_inputs or {}).get("blade_name")
            or (meanline_result or {}).get("inputs", {}).get("blade_name")
            or "impulse_design"
        )

    ranked = list(ranked_fixes or [])
    if not ranked and loss_report:
        ranked = list(loss_report.get("ranked_fixes") or [])

    pkg: dict[str, Any] = {
        "format": PACKAGE_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "description": (
            "Comparable ImpulseCalc cascade design package for offline / git diff. "
            "Contains operating point, metal knobs, dense metrics, stations, shocks, "
            "loss report, and paths to companion CSV/JSON tables."
        ),
        "created_utc": _utc_now(),
        "blade_name": name,
        "case_dir": case_dir,
        "operating": op,
        "meanline_inputs": dict(meanline_inputs or {}),
        "meanline_result": dict(meanline_result or {}),
        "blade_shape": dict(blade_shape or {}),
        "domain": dict(domain or {}),
        "metrics": m,
        "stations": list(stations or []),
        "surface_table": list(surface_table or []),
        "shocks": list(shocks or []),
        "shock_relations_table": list(shock_relations_table or shocks or []),
        "loss_report": dict(loss_report or {}),
        "industry_advice": dict(industry_advice or {}),
        "ranked_fixes": ranked,
        "summary": summary or "",
        "exports": dict(export_paths or {}),
        "notes": list(notes or []),
        "comparison_keys": list(COMPARISON_METRIC_KEYS),
    }
    return pkg


def write_metrics_comparison_csv(
    path: str | Path,
    metrics: dict[str, Any] | None,
    *,
    operating: dict[str, Any] | None = None,
    blade_name: str = "",
    extra: dict[str, Any] | None = None,
) -> Path:
    """Single-row (or key,value) CSV of comparable scalars for spreadsheet diff."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    m = dict(metrics or {})
    op = dict(operating or {})
    row: dict[str, Any] = {
        "blade_name": blade_name or m.get("blade_name") or "",
        "source": m.get("source") or op.get("source") or "",
    }
    for k in COMPARISON_METRIC_KEYS:
        if k in m and m[k] is not None:
            row[k] = m[k]
        elif k in op and op[k] is not None:
            row[k] = op[k]
    for k, v in (extra or {}).items():
        if v is not None:
            row[k] = v
    # Also fold a few operating keys
    for k in ("mach_w1", "solidity", "gamma", "p1_pa", "q_ref_pa", "u_m_s", "rpm"):
        if k not in row and k in op:
            row[k] = op[k]

    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        w.writeheader()
        w.writerow(row)
    return path


def write_metrics_kv_csv(path: str | Path, metrics: dict[str, Any] | None) -> Path:
    """Two-column key,value metrics dump (easy line-diff)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    m = dict(metrics or {})
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", "value"])
        for k in sorted(m.keys()):
            w.writerow([k, m[k]])
    return path


def write_comparable_package(
    out_dir: str | Path,
    package: dict[str, Any],
    *,
    filename: str = "design_package.json",
    write_comparison_csv: bool = True,
) -> dict[str, str]:
    """
    Write package JSON + companion comparison CSVs under ``out_dir``.
    Returns map of export role → absolute path.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    pkg = dict(package)
    pkg.setdefault("format", PACKAGE_FORMAT)
    pkg.setdefault("schema_version", SCHEMA_VERSION)
    pkg.setdefault("created_utc", _utc_now())

    # Companion comparison tables
    if write_comparison_csv:
        comp = write_metrics_comparison_csv(
            out / "comparison_scalars.csv",
            pkg.get("metrics") if isinstance(pkg.get("metrics"), dict) else {},
            operating=pkg.get("operating") if isinstance(pkg.get("operating"), dict) else {},
            blade_name=str(pkg.get("blade_name") or ""),
        )
        paths["comparison_csv"] = str(comp.resolve())
        kv = write_metrics_kv_csv(
            out / "metrics_kv.csv",
            pkg.get("metrics") if isinstance(pkg.get("metrics"), dict) else {},
        )
        paths["metrics_kv_csv"] = str(kv.resolve())

    # Merge paths into package.exports (absolute + relative to out_dir)
    exports = dict(pkg.get("exports") or {})
    exports.update(paths)
    # Prefer relative paths inside package for portability when under case
    rel_exports = {}
    for k, v in exports.items():
        rel_exports[k] = _rel_or_abs(v, out) or v
    pkg["exports"] = rel_exports
    pkg["exports_absolute"] = {k: str(Path(v).resolve()) if v else v for k, v in exports.items()}

    pkg_path = out / filename
    pkg_path.write_text(json.dumps(pkg, indent=2, default=str), encoding="utf-8")
    paths["design_package_json"] = str(pkg_path.resolve())
    return paths


def package_required_keys() -> list[str]:
    """Keys that tests / consumers expect on a complete package."""
    return [
        "format",
        "schema_version",
        "operating",
        "metrics",
        "meanline_inputs",
        "blade_shape",
        "stations",
        "shocks",
        "loss_report",
        "ranked_fixes",
        "summary",
        "exports",
    ]
