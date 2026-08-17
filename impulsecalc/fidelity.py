"""CFD fidelity modes: fast design-board vs industry-oriented high-accuracy.

High-accuracy / industry path:
  - body-fitted blade walls (snappyHexMesh snap primary)
  - RANS k-ω SST + no-slip blades
  - finer mesh, longer endTime, optional unlimited wall-clock

Fast path keeps stair-step + laminar + slip for responsive design iteration.
Still a 2D relative cascade — not full-stage 3D URANS.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


FIDELITY_FAST = "fast"
FIDELITY_BALANCED = "balanced"
FIDELITY_ACCURATE = "accurate"

LABELS = {
    FIDELITY_FAST: "Fast (design board)",
    FIDELITY_BALANCED: "Balanced (industry mesh + SST)",
    FIDELITY_ACCURATE: "Industry cascade (body-fitted + SST)",
}

MESH_STAIR = "stair_step"
MESH_BODY = "body_fitted"
TURB_LAMINAR = "laminar"
TURB_SST = "kOmegaSST"
WALL_SLIP = "slip"
WALL_NOSLIP = "noSlip"


@dataclass(frozen=True)
class FidelitySettings:
    """Concrete mesh/solve parameters for one fidelity choice."""

    mode: str
    level: int  # 0–100
    label: str
    hint: str
    # Mesh
    nx: int
    ny: int
    n_blades_default: int
    blade_n_points: int
    sample_n_points: int
    # Solvers / CFL
    max_co: float
    max_delta_t: float
    # Physical time (auto endTime when user leaves "auto")
    end_time_transit_mult: float
    end_time_floor_s: float
    end_time_cap_s: float | None  # None = no upper cap
    # Schemes
    flux_scheme: str  # Tadmor | Kurganov
    reconstruct: str  # Minmod | vanLeer
    write_precision: int
    # Runner budgets (None = unlimited / no subprocess timeout)
    mesh_timeout_s: float | None
    solve_timeout_s: float | None
    # Prefer thinner paper-like t/c when shape is the thick educational default
    prefer_paper_thickness: bool
    paper_thickness_ratio: float = 0.18
    # --- Industry cascade path ---
    mesh_path: str = MESH_STAIR  # stair_step | body_fitted
    turbulence_model: str = TURB_LAMINAR  # laminar | kOmegaSST
    wall_bc: str = WALL_SLIP  # slip | noSlip

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_high_accuracy(self) -> bool:
        return self.mode == FIDELITY_ACCURATE or self.level >= 75

    @property
    def is_industry(self) -> bool:
        """True when mesh/turbulence follow industry cascade practice."""
        return (
            self.mesh_path == MESH_BODY
            or self.turbulence_model == TURB_SST
            or self.wall_bc == WALL_NOSLIP
            or self.level >= 50
        )

    @property
    def unlimited_timeouts(self) -> bool:
        return self.mesh_timeout_s is None or self.solve_timeout_s is None


def _lerp_int(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _preset_fast() -> FidelitySettings:
    """Design-board defaults (responsive; stair-step + laminar OK)."""
    return FidelitySettings(
        mode=FIDELITY_FAST,
        level=0,
        label=LABELS[FIDELITY_FAST],
        hint="Quick mesh/solve for design iteration. Stair-step walls, laminar, slip.",
        nx=80,
        ny=40,
        n_blades_default=3,
        blade_n_points=96,
        sample_n_points=40,
        max_co=0.10,
        max_delta_t=1e-7,
        end_time_transit_mult=16.0,
        end_time_floor_s=6e-4,
        end_time_cap_s=3.0e-3,
        flux_scheme="Tadmor",
        reconstruct="Minmod",
        write_precision=8,
        # Body-fitted / WSL starts often need >30 min wall-clock even on "fast"
        mesh_timeout_s=1200.0,
        solve_timeout_s=5400.0,
        prefer_paper_thickness=False,
        paper_thickness_ratio=0.18,
        mesh_path=MESH_STAIR,
        turbulence_model=TURB_LAMINAR,
        wall_bc=WALL_SLIP,
    )


def _preset_balanced() -> FidelitySettings:
    return FidelitySettings(
        mode=FIDELITY_BALANCED,
        level=50,
        label=LABELS[FIDELITY_BALANCED],
        hint=(
            "Industry-oriented 2D cascade: body-fitted snap walls preferred, "
            "k-ω SST, no-slip blades. Longer run than Fast."
        ),
        nx=160,
        ny=90,
        n_blades_default=3,
        blade_n_points=120,
        sample_n_points=80,
        max_co=0.05,
        max_delta_t=5e-8,
        end_time_transit_mult=32.0,
        end_time_floor_s=1.2e-3,
        end_time_cap_s=1.0e-2,
        flux_scheme="Tadmor",
        reconstruct="Minmod",
        write_precision=10,
        mesh_timeout_s=3600.0,
        solve_timeout_s=14400.0,
        prefer_paper_thickness=True,
        paper_thickness_ratio=0.18,
        mesh_path=MESH_BODY,
        turbulence_model=TURB_SST,
        wall_bc=WALL_NOSLIP,
    )


def _preset_accurate() -> FidelitySettings:
    """Industry cascade: body-fitted + SST + no-slip, fine mesh, long endTime."""
    return FidelitySettings(
        mode=FIDELITY_ACCURATE,
        level=100,
        label=LABELS[FIDELITY_ACCURATE],
        hint=(
            "Closest ImpulseCalc path to industry 2D cascade practice: snappy "
            "body-fitted blade walls, k-ω SST RANS, no-slip, fine mesh, long endTime. "
            "Not full-stage 3D URANS / CFX."
        ),
        nx=360,
        ny=220,
        n_blades_default=3,
        blade_n_points=180,
        sample_n_points=160,
        max_co=0.02,
        max_delta_t=1e-8,
        end_time_transit_mult=80.0,
        end_time_floor_s=3.0e-3,
        end_time_cap_s=None,
        flux_scheme="Tadmor",
        reconstruct="Minmod",
        write_precision=12,
        mesh_timeout_s=None,
        solve_timeout_s=None,
        prefer_paper_thickness=True,
        paper_thickness_ratio=0.18,
        mesh_path=MESH_BODY,
        turbulence_model=TURB_SST,
        wall_bc=WALL_NOSLIP,
    )


def normalize_fidelity_mode(mode: str | None, level: int | float | None = None) -> tuple[str, int]:
    """Return (mode_name, level_0_100)."""
    if level is not None:
        try:
            lv = int(round(float(level)))
        except (TypeError, ValueError):
            lv = 0
        lv = max(0, min(100, lv))
        if mode in (None, "", "auto"):
            if lv >= 75:
                return FIDELITY_ACCURATE, lv
            if lv >= 35:
                return FIDELITY_BALANCED, lv
            return FIDELITY_FAST, lv
    m = (mode or FIDELITY_FAST).strip().lower()
    aliases = {
        "fast": FIDELITY_FAST,
        "quick": FIDELITY_FAST,
        "default": FIDELITY_FAST,
        "design": FIDELITY_FAST,
        "board": FIDELITY_FAST,
        "balanced": FIDELITY_BALANCED,
        "medium": FIDELITY_BALANCED,
        "mid": FIDELITY_BALANCED,
        "accurate": FIDELITY_ACCURATE,
        "high": FIDELITY_ACCURATE,
        "high_accuracy": FIDELITY_ACCURATE,
        "high-accuracy": FIDELITY_ACCURATE,
        "paper": FIDELITY_ACCURATE,
        "precision": FIDELITY_ACCURATE,
        "industry": FIDELITY_ACCURATE,
        "rans": FIDELITY_ACCURATE,
        "sst": FIDELITY_BALANCED,
    }
    m = aliases.get(m, m if m in (FIDELITY_FAST, FIDELITY_BALANCED, FIDELITY_ACCURATE) else FIDELITY_FAST)
    if level is None:
        level_map = {FIDELITY_FAST: 0, FIDELITY_BALANCED: 50, FIDELITY_ACCURATE: 100}
        return m, level_map[m]
    return m, max(0, min(100, int(round(float(level)))))


def resolve_fidelity(
    mode: str | None = None,
    *,
    level: int | float | None = None,
) -> FidelitySettings:
    """Map mode name and/or 0–100 slider to concrete settings."""
    mode_name, lv = normalize_fidelity_mode(mode, level)
    fast = _preset_fast()
    bal = _preset_balanced()
    acc = _preset_accurate()

    if lv <= 0:
        return fast
    if lv >= 100:
        return acc
    if lv == 50:
        return bal

    if lv < 50:
        t = lv / 50.0
        a, b = fast, bal
        mid_mode = FIDELITY_BALANCED if lv >= 35 else FIDELITY_FAST
    else:
        t = (lv - 50) / 50.0
        a, b = bal, acc
        mid_mode = FIDELITY_ACCURATE if lv >= 75 else FIDELITY_BALANCED

    if b.end_time_cap_s is None:
        end_cap: float | None = None if t > 0.85 else (
            a.end_time_cap_s if a.end_time_cap_s is not None else None
        )
        if end_cap is not None and t > 0:
            end_cap = _lerp(float(a.end_time_cap_s or 3e-3), 5.0e-2, t)
            if t > 0.85:
                end_cap = None
    else:
        end_cap = _lerp(float(a.end_time_cap_s or 3e-3), float(b.end_time_cap_s), t)

    def mix_timeout(ta: float | None, tb: float | None) -> float | None:
        if ta is None and tb is None:
            return None
        if tb is None:
            if t >= 0.9:
                return None
            base = float(ta or 1800.0)
            return _lerp(base, 28800.0, t)
        if ta is None:
            return tb
        return _lerp(float(ta), float(tb), t)

    # Discrete industry flags: flip to body_fitted/SST/noSlip at mid of segment
    mesh_path = b.mesh_path if t >= 0.35 else a.mesh_path
    turb = b.turbulence_model if t >= 0.35 else a.turbulence_model
    wall = b.wall_bc if t >= 0.35 else a.wall_bc

    return FidelitySettings(
        mode=mid_mode if mode_name == FIDELITY_FAST and lv > 0 else mode_name,
        level=lv,
        label=LABELS.get(mid_mode, mid_mode),
        hint=b.hint if t > 0.5 else a.hint,
        nx=_lerp_int(a.nx, b.nx, t),
        ny=_lerp_int(a.ny, b.ny, t),
        n_blades_default=a.n_blades_default,
        blade_n_points=_lerp_int(a.blade_n_points, b.blade_n_points, t),
        sample_n_points=_lerp_int(a.sample_n_points, b.sample_n_points, t),
        max_co=_lerp(a.max_co, b.max_co, t),
        max_delta_t=_lerp(a.max_delta_t, b.max_delta_t, t),
        end_time_transit_mult=_lerp(a.end_time_transit_mult, b.end_time_transit_mult, t),
        end_time_floor_s=_lerp(a.end_time_floor_s, b.end_time_floor_s, t),
        end_time_cap_s=end_cap,
        flux_scheme=b.flux_scheme if t >= 0.5 else a.flux_scheme,
        reconstruct=b.reconstruct if t >= 0.5 else a.reconstruct,
        write_precision=_lerp_int(a.write_precision, b.write_precision, t),
        mesh_timeout_s=mix_timeout(a.mesh_timeout_s, b.mesh_timeout_s),
        solve_timeout_s=mix_timeout(a.solve_timeout_s, b.solve_timeout_s),
        prefer_paper_thickness=b.prefer_paper_thickness if t >= 0.35 else a.prefer_paper_thickness,
        paper_thickness_ratio=b.paper_thickness_ratio,
        mesh_path=mesh_path,
        turbulence_model=turb,
        wall_bc=wall,
    )


def fidelity_from_request(data: dict[str, Any] | None) -> FidelitySettings:
    """Extract fidelity from API / job JSON body."""
    data = data or {}
    nested = data.get("fidelity")
    if isinstance(nested, dict):
        return resolve_fidelity(nested.get("mode"), level=nested.get("level"))
    if isinstance(nested, str):
        return resolve_fidelity(nested, level=data.get("fidelity_level"))
    return resolve_fidelity(
        data.get("fidelity_mode") or data.get("mode"),
        level=data.get("fidelity_level") if data.get("fidelity_level") is not None else data.get("level"),
    )


def recommended_end_time(
    chord_m: float,
    w1_m_s: float,
    settings: FidelitySettings,
    *,
    x_up_c: float = 0.5,
    x_dn_c: float = 1.0,
) -> dict[str, float]:
    """Physical-time budget scaled by fidelity."""
    import math

    c = max(float(chord_m), 1e-6)
    L = c * (float(x_up_c) + 1.0 + float(x_dn_c))
    w = max(abs(float(w1_m_s)), 50.0)
    tau = L / w
    end = max(settings.end_time_transit_mult * tau, settings.end_time_floor_s)
    if settings.end_time_cap_s is not None:
        end = min(end, float(settings.end_time_cap_s))
    n_frames = 28.0 if settings.level < 50 else (40.0 if settings.level < 75 else 60.0)
    write_interval = max(end / n_frames, 1e-6)
    delta_t = min(1e-8, write_interval / 50.0, settings.max_delta_t * 0.5)
    return {
        "end_time": float(end),
        "write_interval": float(write_interval),
        "delta_t": float(delta_t),
        "transit_s": float(tau),
        "n_writes_est": float(end / write_interval),
    }


def compare_fidelity(a: FidelitySettings, b: FidelitySettings) -> dict[str, Any]:
    """Numeric comparison for tests."""
    return {
        "nx_higher": b.nx > a.nx,
        "ny_higher": b.ny > a.ny,
        "end_mult_higher": b.end_time_transit_mult > a.end_time_transit_mult,
        "max_co_tighter_or_eq": b.max_co <= a.max_co,
        "sample_finer": b.sample_n_points >= a.sample_n_points,
        "industry_mesh": b.mesh_path == MESH_BODY and a.mesh_path == MESH_STAIR,
        "industry_sst": b.turbulence_model == TURB_SST and a.turbulence_model == TURB_LAMINAR,
        "industry_noslip": b.wall_bc == WALL_NOSLIP and a.wall_bc == WALL_SLIP,
        "solve_budget_higher": (
            (b.solve_timeout_s is None and a.solve_timeout_s is not None)
            or (
                b.solve_timeout_s is not None
                and a.solve_timeout_s is not None
                and b.solve_timeout_s > a.solve_timeout_s
            )
            or (b.solve_timeout_s is None and a.solve_timeout_s is None)
        ),
        "a": a.to_dict(),
        "b": b.to_dict(),
    }
