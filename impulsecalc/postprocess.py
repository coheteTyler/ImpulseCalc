"""Surface pressure / Cp from sample CSVs or educational synthetic curves."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SurfacePressureResult:
    x_c_ps: list[float]
    p_ps: list[float]
    cp_ps: list[float]
    x_c_ss: list[float]
    p_ss: list[float]
    cp_ss: list[float]
    p_ref_pa: float
    q_ref_pa: float
    source: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "x_c_ps": self.x_c_ps, "p_ps": self.p_ps, "cp_ps": self.cp_ps,
            "x_c_ss": self.x_c_ss, "p_ss": self.p_ss, "cp_ss": self.cp_ss,
            "p_ref_pa": self.p_ref_pa, "q_ref_pa": self.q_ref_pa,
            "source": self.source, "notes": list(self.notes),
        }


def _cp(p: float, p_ref: float, q_ref: float) -> float:
    return (p - p_ref) / max(q_ref, 1e-9)


def synthetic_surface_pressure(
    *,
    p1_pa: float,
    mach_w1: float,
    n: int = 40,
    thickness_ratio: float = 0.12,
    thickness_peak_x: float = 0.40,
    arc_bulge: float = 1.0,
    inlet_line_frac: float = 0.0,
    outlet_line_frac: float = 0.0,
    beta1_deg: float = 72.0,
    beta2_deg: float = -72.0,
) -> SurfacePressureResult:
    """Educational Cp modulated by operating point **and** blade shape knobs.

    Shape sensitivity lets re-analyze / industry auto-apply change metrics even
    without a fresh OpenFOAM sample (source remains synthetic_*).
    """
    p_ref = p1_pa
    q_ref = 0.5 * p1_pa * 1.3 * max(mach_w1, 0.3) ** 2
    xs = [i / max(n - 1, 1) for i in range(n)]
    m = max(mach_w1, 0.3)
    # Geometry / camber modulators (clamped)
    thk = min(max(float(thickness_ratio), 0.04), 0.35)
    peak = min(max(float(thickness_peak_x), 0.15), 0.85)
    bulge = min(max(float(arc_bulge), 0.3), 2.0)
    f_in = min(max(float(inlet_line_frac), 0.0), 0.45)
    f_out = min(max(float(outlet_line_frac), 0.0), 0.45)
    camber = abs(float(beta1_deg) - float(beta2_deg)) / 144.0  # ~1 at ±72
    camber = min(max(camber, 0.3), 1.4)

    # Peak suction location moves aft with peak_x and inlet straight
    x_peak = min(0.75, max(0.18, 0.22 + 0.55 * peak + 0.35 * f_in))
    # Suction amplitude grows with M, thickness, bulge, camber
    ss_amp = (0.35 + 0.45 * m) * (0.75 + 1.4 * thk) * (0.7 + 0.45 * bulge) * (0.75 + 0.4 * camber)
    # Shock strength: high M + thick + high bulge; reduced by aft peak / exit line
    shock_x0 = min(0.85, x_peak + 0.12 + 0.15 * (1.0 - f_out))
    shock_str = max(0.0, (m - 0.95)) * (0.35 + 0.9 * thk) * (0.6 + 0.5 * bulge) * (1.15 - 0.4 * f_out)

    p_ps: list[float] = []
    p_ss: list[float] = []
    for x in xs:
        # Pressure side: mild compression, stronger if thick
        cp_ps = 0.12 + (0.18 + 0.35 * thk) * math.sin(math.pi * x * 0.95) ** 1.15
        # Suction: expansion peaking near x_peak
        # smooth bump: exp(-((x-x_peak)/w)^2)
        w = 0.22 + 0.08 * bulge
        bump = math.exp(-((x - x_peak) / max(w, 0.08)) ** 2)
        # early LE expansion reduced by inlet straight
        le_soft = 1.0 - 0.55 * f_in * math.exp(-((x / max(0.12 + f_in, 0.05)) ** 2))
        cp_ss = -ss_amp * bump * le_soft - 0.08 * m * (1.0 - x)
        if m > 0.95 and x >= shock_x0 - 0.02:
            shock = min(1.0, max(0.0, (x - (shock_x0 - 0.02)) / 0.10))
            cp_ss += shock * shock_str * (0.9 + 0.3 * m)
        if x > 1.0 - max(f_out, 0.12):
            # TE dump toward common base (stronger with thick TE region)
            t0 = 1.0 - max(f_out, 0.12)
            blend = (x - t0) / max(1.0 - t0, 1e-6)
            base = 0.04 + 0.06 * thk
            cp_ps = cp_ps * (1 - blend) + base * blend
            cp_ss = cp_ss * (1 - blend) + (base - 0.03) * blend
        p_ps.append(p_ref + q_ref * cp_ps)
        p_ss.append(p_ref + q_ref * cp_ss)
    return SurfacePressureResult(
        xs, p_ps, [_cp(p, p_ref, q_ref) for p in p_ps],
        xs, p_ss, [_cp(p, p_ref, q_ref) for p in p_ss],
        p_ref, q_ref, "synthetic_educational",
        notes=[
            "synthetic — replace with OpenFOAM sample when available",
            "Cp shape responds to Mw1 + blade_shape (t/c, peak, bulge, lines, β)",
            f"shape thk={thk:.3f} peak={peak:.2f} bulge={bulge:.2f} fin={f_in:.2f} fout={f_out:.2f}",
        ],
    )


def surface_pressure_to_csv_rows(surf: SurfacePressureResult) -> list[dict[str, float | str]]:
    """Flatten PS/SS into row dicts for CSV/JSON export."""
    rows: list[dict[str, float | str]] = []
    for x, p, cp in zip(surf.x_c_ps, surf.p_ps, surf.cp_ps):
        rows.append({"side": "PS", "x_c": x, "p_pa": p, "Cp": cp})
    for x, p, cp in zip(surf.x_c_ss, surf.p_ss, surf.cp_ss):
        rows.append({"side": "SS", "x_c": x, "p_pa": p, "Cp": cp})
    return rows


def write_surface_csv(path: str | Path, surf: SurfacePressureResult) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["side", "x_c", "p_pa", "Cp"])
        for row in surface_pressure_to_csv_rows(surf):
            w.writerow([row["side"], row["x_c"], row["p_pa"], row["Cp"]])
    return path


def _read_csv(path: Path) -> tuple[list[float], list[float]]:
    xs, ps = [], []
    rows = [ln for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if ln.strip() and not ln.startswith("#")]
    if not rows:
        return [], []
    reader = csv.reader(rows)
    for row in reader:
        if len(row) < 2:
            continue
        try:
            xs.append(float(row[0]))
            ps.append(float(row[1]))
        except ValueError:
            continue
    return xs, ps


def load_surface_pressure(
    case_dir: str | Path,
    *,
    p1_pa: float,
    rho1: float,
    w1_m_s: float,
    chord_m: float = 0.024,
    allow_synthetic: bool = True,
    force_synthetic: bool = False,
    mach_w1: float | None = None,
    gamma: float = 1.3,
    t1_k: float | None = None,
    r_specific: float | None = None,
    blade_shape: dict | None = None,
    beta1_deg: float = 72.0,
    beta2_deg: float = -72.0,
) -> SurfacePressureResult:
    """Load PS/SS surface pressure from an OpenFOAM case, or synthetic fallback.

    ``mach_w1`` is the relative inlet Mach used for synthetic Cp shapes. When
    omitted, it is derived from W1 and sound speed (√(γ R T)) if T and R are
    given, else from W1 / a_ref with a_ref from γ·R·T defaults — **not** W1/500.
    """
    cdir = Path(case_dir)
    q_ref = 0.5 * rho1 * w1_m_s**2
    found: dict[str, Path] = {}
    pp = cdir / "postProcessing"
    if (not force_synthetic) and pp.is_dir():
        for path in pp.rglob("*"):
            if not path.is_file():
                continue
            parts = "/".join(path.parts).lower()
            if "pressureside" in parts and path.suffix in (".csv", ".xy"):
                found["ps"] = path
            if "suctionside" in parts and path.suffix in (".csv", ".xy"):
                found["ss"] = path
    if (not force_synthetic) and "ps" in found and "ss" in found:
        xps, pps = _read_csv(found["ps"])
        xss, pss = _read_csv(found["ss"])
        if xps and xss:
            def norm(xs: list[float]) -> list[float]:
                if max(xs) > 1.5:
                    return [(x - min(xs)) / max(chord_m, 1e-9) for x in xs]
                mn, mx = min(xs), max(xs)
                return [(x - mn) / max(mx - mn, 1e-12) for x in xs]
            return SurfacePressureResult(
                norm(xps), pps, [_cp(p, p1_pa, q_ref) for p in pps],
                norm(xss), pss, [_cp(p, p1_pa, q_ref) for p in pss],
                p1_pa, q_ref, "openfoam_sample", notes=["loaded_sample_csv"],
            )
    if allow_synthetic:
        mw = mach_w1
        if mw is None or mw <= 0:
            g = max(float(gamma), 1.01)
            R = float(r_specific) if r_specific is not None else 320.0
            T = float(t1_k) if t1_k is not None else 1100.0
            a = math.sqrt(g * R * max(T, 1.0))
            mw = float(w1_m_s) / max(a, 1e-9)
        sh = blade_shape or {}
        return synthetic_surface_pressure(
            p1_pa=p1_pa,
            mach_w1=float(mw),
            thickness_ratio=float(sh.get("thickness_ratio") or 0.12),
            thickness_peak_x=float(sh.get("thickness_peak_x") or 0.40),
            arc_bulge=float(sh.get("arc_bulge") or 1.0),
            inlet_line_frac=float(sh.get("inlet_line_frac") or 0.0),
            outlet_line_frac=float(sh.get("outlet_line_frac") or 0.0),
            beta1_deg=float(beta1_deg),
            beta2_deg=float(beta2_deg),
        )
    return SurfacePressureResult([], [], [], [], [], [], p1_pa, q_ref, "missing", notes=["no_data"])
