"""Perfect-gas shock relations (Hill & Peterson, Mechanics and Thermodynamics
of Propulsion, 2nd ed., §3.7 “Shocks”, pp. 85–87 style tables).

Provides the textbook ratio set for normal shocks and a minimal oblique-shock
β–θ–M path so cascade post-process can attach p₂/p₁, ρ₂/ρ₁, T₂/T₁, p₀₂/p₀₁,
and M₂ to every detected recompression — not only a qualitative flag.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class NormalShockResult:
    """Normal-shock jump at upstream Mach M1, ratio of specific heats γ."""

    M1: float
    gamma: float
    M2: float
    p2_p1: float
    rho2_rho1: float
    T2_T1: float
    p02_p01: float
    # extras useful for charts
    p1_p01: float  # static/total upstream (isentropic)
    p2_p02: float  # static/total downstream

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObliqueShockResult:
    """Oblique shock: freestream M1, wave angle β (deg), deflection θ (deg)."""

    M1: float
    gamma: float
    beta_deg: float
    theta_deg: float
    Mn1: float
    Mn2: float
    M2: float
    p2_p1: float
    rho2_rho1: float
    T2_T1: float
    p02_p01: float
    branch: str  # "weak" | "strong"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _check_gamma(gamma: float) -> float:
    g = float(gamma)
    if g <= 1.0:
        raise ValueError(f"gamma must be > 1, got {g}")
    return g


def isentropic_p_p0(M: float, gamma: float = 1.4) -> float:
    """p/p0 for isentropic perfect gas."""
    g = _check_gamma(gamma)
    M = max(float(M), 0.0)
    return (1.0 + 0.5 * (g - 1.0) * M * M) ** (-g / (g - 1.0))


def isentropic_T_T0(M: float, gamma: float = 1.4) -> float:
    g = _check_gamma(gamma)
    M = max(float(M), 0.0)
    return 1.0 / (1.0 + 0.5 * (g - 1.0) * M * M)


def isentropic_rho_rho0(M: float, gamma: float = 1.4) -> float:
    g = _check_gamma(gamma)
    M = max(float(M), 0.0)
    return (1.0 + 0.5 * (g - 1.0) * M * M) ** (-1.0 / (g - 1.0))


def normal_shock(M1: float, gamma: float = 1.4) -> NormalShockResult:
    """Normal-shock relations (Hill–Peterson §3.7 style).

    For M1 ≤ 1 returns a trivial identity jump (no shock).
    """
    g = _check_gamma(gamma)
    M1 = float(M1)
    if M1 < 1.0 + 1e-12:
        # subsonic: no shock
        p_p0 = isentropic_p_p0(max(M1, 0.0), g)
        return NormalShockResult(
            M1=max(M1, 0.0),
            gamma=g,
            M2=max(M1, 0.0),
            p2_p1=1.0,
            rho2_rho1=1.0,
            T2_T1=1.0,
            p02_p01=1.0,
            p1_p01=p_p0,
            p2_p02=p_p0,
        )

    # Standard perfect-gas normal shock
    # M2^2 = (1 + ½(γ-1)M1^2) / (γ M1^2 - ½(γ-1))
    num = 1.0 + 0.5 * (g - 1.0) * M1 * M1
    den = g * M1 * M1 - 0.5 * (g - 1.0)
    M2 = math.sqrt(max(num / max(den, 1e-15), 0.0))

    p2_p1 = 1.0 + (2.0 * g / (g + 1.0)) * (M1 * M1 - 1.0)
    rho2_rho1 = ((g + 1.0) * M1 * M1) / ((g - 1.0) * M1 * M1 + 2.0)
    T2_T1 = p2_p1 / rho2_rho1

    # Stagnation pressure ratio across shock (total pressure loss)
    # p02/p01 = (p02/p2)*(p2/p1)*(p1/p01)
    p1_p01 = isentropic_p_p0(M1, g)
    p2_p02 = isentropic_p_p0(M2, g)
    p02_p01 = (p2_p1 * p1_p01) / max(p2_p02, 1e-15)

    return NormalShockResult(
        M1=M1,
        gamma=g,
        M2=M2,
        p2_p1=p2_p1,
        rho2_rho1=rho2_rho1,
        T2_T1=T2_T1,
        p02_p01=p02_p01,
        p1_p01=p1_p01,
        p2_p02=p2_p02,
    )


def normal_shock_table(
    M1_list: list[float] | None = None, gamma: float = 1.4
) -> list[dict[str, Any]]:
    """Tabulate normal-shock ratios vs M1 (textbook chart replacement)."""
    if M1_list is None:
        M1_list = [1.0, 1.2, 1.4, 1.5, 1.6, 1.8, 2.0, 2.5, 3.0, 4.0, 5.0]
    return [normal_shock(M, gamma).to_dict() for M in M1_list]


def _theta_from_beta(M1: float, beta_rad: float, gamma: float) -> float:
    """θ(β) from the θ–β–M relation (radians in/out)."""
    g = gamma
    s = math.sin(beta_rad)
    if abs(s) < 1e-12:
        return 0.0
    num = M1 * M1 * s * s - 1.0
    den = M1 * M1 * ((g + 1.0) / 2.0 - s * s) + 1.0
    if abs(den) < 1e-15:
        return 0.0
    return math.atan(2.0 / math.tan(beta_rad) * num / den)


def oblique_shock_from_beta(
    M1: float, beta_deg: float, gamma: float = 1.4
) -> ObliqueShockResult:
    """Oblique shock given wave angle β (deg) and freestream M1."""
    g = _check_gamma(gamma)
    M1 = float(M1)
    beta = math.radians(float(beta_deg))
    if beta <= 0.0 or beta >= math.pi / 2:
        raise ValueError("beta must be in (0, 90) degrees")

    Mn1 = M1 * math.sin(beta)
    ns = normal_shock(Mn1, g)
    Mn2 = ns.M2
    # M2 from normal component and flow deflection
    theta = _theta_from_beta(M1, beta, g)
    # Downstream Mach
    # Mn2 = M2 sin(β - θ)
    denom = math.sin(beta - theta)
    M2 = Mn2 / max(abs(denom), 1e-12) if abs(denom) > 1e-12 else Mn2

    return ObliqueShockResult(
        M1=M1,
        gamma=g,
        beta_deg=float(beta_deg),
        theta_deg=math.degrees(theta),
        Mn1=Mn1,
        Mn2=Mn2,
        M2=M2,
        p2_p1=ns.p2_p1,
        rho2_rho1=ns.rho2_rho1,
        T2_T1=ns.T2_T1,
        p02_p01=ns.p02_p01,
        branch="from_beta",
    )


def oblique_shock_from_deflection(
    M1: float,
    theta_deg: float,
    gamma: float = 1.4,
    *,
    branch: str = "weak",
) -> ObliqueShockResult | None:
    """Solve θ–β–M for wave angle β given deflection θ (deg). Weak or strong branch."""
    g = _check_gamma(gamma)
    M1 = float(M1)
    theta = math.radians(float(theta_deg))
    if M1 <= 1.0 or theta <= 0.0:
        return None

    # Max deflection ~ exists; search β from μ to 90°
    mu = math.asin(min(1.0, 1.0 / M1))
    betas = [mu + (math.pi / 2 - mu) * i / 200 for i in range(1, 200)]
    thetas = [_theta_from_beta(M1, b, g) for b in betas]
    # Find max θ
    imax = max(range(len(thetas)), key=lambda i: thetas[i])
    if theta > thetas[imax] + 1e-9:
        return None  # detached

    # Weak: β between μ and β(θmax); strong: β between β(θmax) and 90°
    if branch == "strong":
        lo, hi = imax, len(betas) - 1
    else:
        lo, hi = 0, imax

    # Find β where θ(β) ≈ theta
    best_b = betas[lo]
    best_err = 1e9
    for i in range(lo, hi + 1):
        err = abs(thetas[i] - theta)
        if err < best_err:
            best_err = err
            best_b = betas[i]

    res = oblique_shock_from_beta(M1, math.degrees(best_b), g)
    return ObliqueShockResult(
        M1=res.M1,
        gamma=res.gamma,
        beta_deg=res.beta_deg,
        theta_deg=res.theta_deg,
        Mn1=res.Mn1,
        Mn2=res.Mn2,
        M2=res.M2,
        p2_p1=res.p2_p1,
        rho2_rho1=res.rho2_rho1,
        T2_T1=res.T2_T1,
        p02_p01=res.p02_p01,
        branch=branch,
    )


def shock_jump_from_upstream_mach(
    M1: float,
    gamma: float = 1.4,
    *,
    kind: str = "normal",
    beta_deg: float | None = None,
    theta_deg: float | None = None,
) -> dict[str, Any]:
    """Unified dict for design-report tables (normal default; optional oblique)."""
    if kind == "oblique" and beta_deg is not None:
        r = oblique_shock_from_beta(M1, beta_deg, gamma)
        d = r.to_dict()
        d["kind"] = "oblique"
        return d
    if kind == "oblique" and theta_deg is not None:
        r = oblique_shock_from_deflection(M1, theta_deg, gamma, branch="weak")
        if r is None:
            return {
                "kind": "oblique",
                "M1": M1,
                "gamma": gamma,
                "theta_deg": theta_deg,
                "detached": True,
            }
        d = r.to_dict()
        d["kind"] = "oblique"
        d["detached"] = False
        return d
    r = normal_shock(M1, gamma)
    d = r.to_dict()
    d["kind"] = "normal"
    return d
