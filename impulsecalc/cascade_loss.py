"""Industry-standard 2D cascade loss metrics (pure math — no OpenFOAM required).

References (standard cascade / turbomachinery notation):
  - Total-pressure loss coefficient:
        ω = (p0_in − p0_out) / (p0_in − p_in)
    for incompressible/subsonic sections; used as a primary 2D cascade figure of merit.
  - Kinetic-energy / profile loss proxy (Denton-style educational form):
        ζ = (c_is² − c²) / c_is²   when isentropic exit speed is known
    or from total-pressure recovery when only p0, M available.

Mass-averaging for discrete station samples:
    φ̄ = Σ(φ_i · ṁ_i) / Σṁ_i    with ṁ_i ∝ ρ |u_n| A_i  (or equal-weight if only p0 given)
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence


def isentropic_p0_from_p(p: float, mach: float, gamma: float = 1.4) -> float:
    """Stagnation pressure from static p and Mach (perfect gas, isentropic)."""
    g = max(float(gamma), 1.01)
    m = max(float(mach), 0.0)
    return float(p) * (1.0 + 0.5 * (g - 1.0) * m * m) ** (g / (g - 1.0))


def isentropic_mach_from_p_p0(p: float, p0: float, gamma: float = 1.4) -> float:
    """Mach from p/p0 (isentropic); 0 if p>=p0."""
    g = max(float(gamma), 1.01)
    ratio = min(max(float(p) / max(float(p0), 1e-30), 1e-12), 1.0)
    # p/p0 = (1+0.5(g-1)M^2)^(-g/(g-1))
    exp = (g - 1.0) / g
    term = ratio ** (-exp)  # 1+0.5(g-1)M^2
    m2 = max((term - 1.0) / (0.5 * (g - 1.0)), 0.0)
    return math.sqrt(m2)


def total_pressure_loss_coefficient(
    p0_in: float,
    p0_out: float,
    p_in: float,
) -> float:
    """Cascade total-pressure loss ω = (p01−p02)/(p01−p1).

    Clamped to a finite range for pathological inputs.
    """
    num = float(p0_in) - float(p0_out)
    den = float(p0_in) - float(p_in)
    if abs(den) < 1e-30:
        return 0.0
    w = num / den
    # Physical loss usually in [0, ~2]; allow mild negative (numerical)
    return float(min(max(w, -0.5), 5.0))


def kinetic_energy_loss_coefficient(
    c_actual: float,
    c_isentropic: float,
) -> float:
    """ζ = 1 − (c/c_is)²  (profile KE loss)."""
    cis = max(abs(float(c_isentropic)), 1e-30)
    c = abs(float(c_actual))
    z = 1.0 - (c / cis) ** 2
    return float(min(max(z, -0.5), 1.5))


def mass_average(values: Sequence[float], masses: Sequence[float] | None = None) -> float:
    """Mass-weighted mean; equal weights if masses omitted or invalid."""
    vals = [float(v) for v in values]
    if not vals:
        return 0.0
    if masses is None or len(masses) != len(vals):
        return sum(vals) / len(vals)
    ws = [max(float(m), 0.0) for m in masses]
    s = sum(ws)
    if s <= 1e-30:
        return sum(vals) / len(vals)
    return sum(v * w for v, w in zip(vals, ws)) / s


@dataclass
class CascadeLossMetrics:
    """Industry-facing 2D cascade loss summary."""

    omega_pt: float  # total-pressure loss coefficient
    zeta_ke: float  # kinetic-energy loss coefficient (0 if unavailable)
    p0_in_pa: float
    p0_out_pa: float
    p_in_pa: float
    p0_recovery: float  # p02/p01
    mass_avg_notes: list[str] = field(default_factory=list)
    source: str = "analytic"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def cascade_loss_from_station_means(
    *,
    p_in: float,
    p0_in: float,
    p_out: float,
    p0_out: float,
    c_out: float | None = None,
    c_out_is: float | None = None,
    gamma: float = 1.4,
    source: str = "station_means",
    notes: list[str] | None = None,
) -> CascadeLossMetrics:
    """Build metrics from mass-averaged station means (inlet/outlet)."""
    # Enforce p02 ≤ p01 for physical recovery (no energy input)
    p0_in_f = float(p0_in)
    p0_out_f = min(float(p0_out), p0_in_f)
    p_in_f = float(p_in)
    omega = total_pressure_loss_coefficient(p0_in_f, p0_out_f, p_in_f)
    omega = max(omega, 0.0)
    zeta = 0.0
    if c_out is not None and c_out_is is not None and c_out_is > 0:
        zeta = kinetic_energy_loss_coefficient(c_out, c_out_is)
    elif p0_out_f > 0 and p_out > 0 and p0_in_f > 0:
        # Actual exit M from p_out/p02; isentropic ideal from p_out/p01 (M_is ≥ M_act)
        m_act = isentropic_mach_from_p_p0(p_out, p0_out_f, gamma)
        m_is = isentropic_mach_from_p_p0(p_out, p0_in_f, gamma)
        if m_is > 1e-6:
            zeta = kinetic_energy_loss_coefficient(m_act, m_is)
    zeta = max(float(zeta), 0.0)
    rec = p0_out_f / max(p0_in_f, 1e-30)
    rec = min(rec, 1.0)
    return CascadeLossMetrics(
        omega_pt=omega,
        zeta_ke=zeta,
        p0_in_pa=p0_in_f,
        p0_out_pa=p0_out_f,
        p_in_pa=p_in_f,
        p0_recovery=rec,
        mass_avg_notes=list(notes or []),
        source=source,
    )


def cascade_loss_from_sample_rows(
    inlet_p: Sequence[float],
    inlet_mach: Sequence[float],
    outlet_p: Sequence[float],
    outlet_mach: Sequence[float],
    *,
    gamma: float = 1.4,
    inlet_mass: Sequence[float] | None = None,
    outlet_mass: Sequence[float] | None = None,
) -> CascadeLossMetrics:
    """Mass-average discrete inlet/outlet samples then compute ω, ζ."""
    p0_in_s = [isentropic_p0_from_p(p, m, gamma) for p, m in zip(inlet_p, inlet_mach)]
    p0_out_s = [isentropic_p0_from_p(p, m, gamma) for p, m in zip(outlet_p, outlet_mach)]
    p_in = mass_average(inlet_p, inlet_mass)
    p0_in = mass_average(p0_in_s, inlet_mass)
    p_out = mass_average(outlet_p, outlet_mass)
    p0_out = mass_average(p0_out_s, outlet_mass)
    return cascade_loss_from_station_means(
        p_in=p_in,
        p0_in=p0_in,
        p_out=p_out,
        p0_out=p0_out,
        gamma=gamma,
        source="mass_averaged_samples",
        notes=[
            f"n_in={len(inlet_p)} n_out={len(outlet_p)}",
            "p0 from isentropic p,M (perfect gas)",
        ],
    )


def cascade_loss_from_meanline_proxy(
    *,
    p1_pa: float,
    mach_w1: float,
    mach_w2: float | None = None,
    gamma: float = 1.4,
    loss_penalty: float = 0.0,
) -> CascadeLossMetrics:
    """Educational proxy when no CFD samples: shock/profile penalty on p0.

    Applies a mild recovery drop from optional loss_penalty ∈ [0,1] and M-based
    estimate so design board still reports industry-shaped fields.
    """
    g = gamma
    m1 = max(float(mach_w1), 0.05)
    m2 = max(float(mach_w2 if mach_w2 is not None else mach_w1 * 0.95), 0.05)
    p1 = float(p1_pa)
    p01 = isentropic_p0_from_p(p1, m1, g)
    # Exit static ≈ p1 for pure impulse; p02 reduced by penalty + weak M change
    pen = min(max(float(loss_penalty), 0.0), 0.8)
    # Base recovery for subsonic cascade ~0.95–0.99; worse if supersonic inlet
    base_rec = 0.97 if m1 < 0.9 else (0.90 if m1 < 1.2 else 0.82)
    rec = max(0.5, base_rec * (1.0 - 0.5 * pen))
    p02 = p01 * rec
    p2 = p1  # impulse-ish
    return cascade_loss_from_station_means(
        p_in=p1,
        p0_in=p01,
        p_out=p2,
        p0_out=p02,
        gamma=g,
        source="meanline_proxy",
        notes=["no CFD sample — proxy recovery from Mw1 and loss_penalty"],
    )
