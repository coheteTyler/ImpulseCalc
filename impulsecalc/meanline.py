"""Mean-line velocity-triangle calculator for pure / near-pure axial impulse stages.

Flight / stage knobs (in addition to classic cascade inputs):
  - mean radius r_m + rpm → U = Ω·r (optional lock)
  - span (annulus height) → annulus area, ṁ, power
  - mass-flow or power target (counterpart computed from Euler work)
  - incidence i and deviation δ so **metal** β* can differ from **flow** β

Angle convention (documented in UI):
  - beta1_deg / beta2_deg are **design relative FLOW** angles (velocity triangles).
  - metal_beta1 = flow_beta1 − incidence   (i>0 ⇒ metal more open than flow at LE)
  - metal_beta2 = flow_beta2 + deviation   (δ>0 ⇒ metal more turned than exit flow)
  Geometry / cascade mesh use metal angles; triangles use flow angles.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MeanlineInputs:
    # Design relative FLOW angles (velocity triangles)
    beta1_deg: float = 72.0
    beta2_deg: float = -72.0
    blade_speed_u_m_s: float = 450.0
    w1_m_s: float = 950.0
    p1_pa: float = 5.5e5
    t1_k: float = 1100.0
    gamma: float = 1.30
    r_specific_j_kg_k: float = 320.0
    mu_pa_s: float = 4.5e-5
    # Geometry DEFAULT = user stage table: tip 0.04 / hub 0.035 / c=0.01 / s=0.008796 / Z=25
    chord_m: float = 0.01
    solidity: float = 1.13688  # c/s = 0.01 / 0.008796
    blade_name: str = "user_stage_r040"
    y_plus_target: float = 1.0
    pure_impulse_lock: bool = True
    # --- Flight / stage knobs (same user table) ---
    mean_radius_m: float = 0.0375  # midspan (tip 0.04 + hub 0.035) / 2
    rpm: float = 0.0  # shaft speed; used when u_from_rpm
    u_from_rpm: bool = False  # if True and r_m,rpm>0: U = Ω·r_m
    span_m: float = 0.005  # tip − hub = 0.04 − 0.035
    tip_radius_m: float = 0.04
    hub_radius_m: float = 0.035
    n_blades_machine: int = 25  # full wheel count (cascade CFD uses pitch from σ)
    # Optional design targets (0 = unused / free)
    mass_flow_kg_s: float = 0.0
    power_target_w: float = 0.0
    # Metal vs flow
    incidence_deg: float = 0.0  # i: metal_β1 = flow_β1 − i
    deviation_deg: float = 0.0  # δ: metal_β2 = flow_β2 + δ

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MeanlineInputs":
        if not d:
            return cls()
        # Accept short UI aliases
        aliases = {
            "U": "blade_speed_u_m_s",
            "W1": "w1_m_s",
            "w1": "w1_m_s",
            "p1": "p1_pa",
            "T1": "t1_k",
            "R": "r_specific_j_kg_k",
            "mu": "mu_pa_s",
            "chord": "chord_m",
            "r_m": "mean_radius_m",
            "mean_radius": "mean_radius_m",
            "span": "span_m",
            "h": "span_m",
            "mdot": "mass_flow_kg_s",
            "mass_flow": "mass_flow_kg_s",
            "power_w": "power_target_w",
            "power_target": "power_target_w",
            "incidence": "incidence_deg",
            "deviation": "deviation_deg",
            "beta1": "beta1_deg",
            "beta2": "beta2_deg",
            "yplus": "y_plus_target",
        }
        cleaned: dict[str, Any] = {}
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        for k, v in d.items():
            key = aliases.get(k, k)
            if key in known:
                cleaned[key] = v
        return cls(**cleaned)


@dataclass
class MeanlineResult:
    inputs: MeanlineInputs
    # Flow angles (triangles)
    beta1_deg: float
    beta2_deg: float
    alpha1_deg: float
    alpha2_deg: float
    # Metal angles (geometry)
    metal_beta1_deg: float
    metal_beta2_deg: float
    incidence_deg: float
    deviation_deg: float
    u_m_s: float
    w1_m_s: float
    w2_m_s: float
    c1_m_s: float
    c2_m_s: float
    c_theta1_m_s: float
    c_theta2_m_s: float
    c_axial1_m_s: float
    c_axial2_m_s: float
    a1_m_s: float
    rho1_kg_m3: float
    mach_w1: float
    mach_c1: float
    mach_w2: float
    euler_work_j_kg: float
    degree_of_reaction: float
    flow_coefficient: float
    stage_loading: float
    efficiency_proxy: float
    yplus_first_layer_m: float
    # Flight / stage derived
    mean_radius_m: float
    rpm: float
    omega_rad_s: float
    span_m: float
    annulus_area_m2: float
    mass_flow_kg_s: float
    power_w: float
    tip_radius_m: float
    tip_speed_m_s: float
    tip_mach_proxy: float
    u_from_rpm: bool
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["inputs"] = self.inputs.to_dict()
        return d


def compute_meanline(inp: MeanlineInputs) -> MeanlineResult:
    notes: list[str] = []
    # --- Flow angles ---
    b1 = float(inp.beta1_deg)
    if inp.pure_impulse_lock:
        b2 = -abs(b1) if b1 >= 0.0 else abs(b1)
        notes.append("pure_impulse_lock")
    else:
        b2 = float(inp.beta2_deg)

    i_deg = float(inp.incidence_deg)
    d_deg = float(inp.deviation_deg)
    metal_b1 = b1 - i_deg
    metal_b2 = b2 + d_deg
    if abs(i_deg) > 1e-12 or abs(d_deg) > 1e-12:
        notes.append(f"metal_vs_flow i={i_deg:.2f}° δ={d_deg:.2f}°")

    # --- U from rpm·r or free U ---
    r_m = max(float(inp.mean_radius_m), 0.0)
    rpm_in = float(inp.rpm)
    u_from_rpm = bool(inp.u_from_rpm)
    omega = 0.0
    if u_from_rpm and r_m > 1e-9 and rpm_in > 1e-9:
        omega = rpm_in * 2.0 * math.pi / 60.0
        U = omega * r_m
        notes.append("U_from_rpm")
    else:
        # Free U: tip speed must follow U/r_m, not a leftover rpm field
        U = float(inp.blade_speed_u_m_s)
        if r_m > 1e-9 and abs(U) > 1e-9:
            omega = U / r_m
            # backfill rpm for display only when user left rpm unset
            if rpm_in <= 1e-9:
                rpm_in = omega * 60.0 / (2.0 * math.pi)
                notes.append("rpm_from_U")
            # else: keep user rpm as informational; omega stays consistent with free U
        u_from_rpm = False

    W1 = max(float(inp.w1_m_s), 1.0)
    gamma = float(inp.gamma)
    R = float(inp.r_specific_j_kg_k)
    T1 = max(float(inp.t1_k), 1.0)
    p1 = max(float(inp.p1_pa), 1.0)
    span = max(float(inp.span_m), 0.0)

    def rad(d: float) -> float:
        return math.radians(d)

    # Velocity triangles from FLOW angles
    Wa1 = W1 * math.cos(rad(b1))
    Wt1 = W1 * math.sin(rad(b1))
    Ca1, Ct1 = Wa1, Wt1 + U
    C1 = math.hypot(Ca1, Ct1)
    alpha1 = math.degrees(math.atan2(Ct1, Ca1))

    W2 = W1
    Wa2 = W2 * math.cos(rad(b2))
    Wt2 = W2 * math.sin(rad(b2))
    Ca2, Ct2 = Wa2, Wt2 + U
    C2 = math.hypot(Ca2, Ct2)
    alpha2 = math.degrees(math.atan2(Ct2, Ca2))

    a_sound = math.sqrt(gamma * R * T1)
    rho1 = p1 / (R * T1)
    Mw1 = W1 / max(a_sound, 1e-9)
    Mc1 = C1 / max(a_sound, 1e-9)
    Mw2 = W2 / max(a_sound, 1e-9)

    euler = U * (Ct1 - Ct2)
    r_rxn = 0.0 if inp.pure_impulse_lock else (
        (W2**2 - W1**2) / (2.0 * euler) if abs(euler) > 1e-6 else 0.0
    )
    phi = Ca1 / max(U, 1e-9)
    psi = euler / max(U * U, 1e-9)
    eta_proxy = max(0.0, min(1.0, 1.0 - 0.35 * (C2 / max(C1, 1.0)) ** 2))

    Re_c = rho1 * W1 * max(inp.chord_m, 1e-6) / max(inp.mu_pa_s, 1e-12)
    cf = 0.027 / max(Re_c**0.1429, 1e-12)
    tau_w = 0.5 * rho1 * W1**2 * cf
    u_tau = math.sqrt(max(tau_w / max(rho1, 1e-12), 0.0))
    y_m = float(inp.y_plus_target) * inp.mu_pa_s / max(rho1 * u_tau, 1e-12)

    # Annulus: A ≈ 2π r_m h (thin annulus)
    annulus = 2.0 * math.pi * r_m * span if r_m > 0 and span > 0 else 0.0
    mdot_from_area = rho1 * abs(Ca1) * annulus if annulus > 0 else 0.0

    mdot_target = float(inp.mass_flow_kg_s)
    power_target = float(inp.power_target_w)

    if mdot_target > 1e-12:
        mdot = mdot_target
        notes.append("mdot_from_target")
    elif power_target > 1e-12 and abs(euler) > 1e-6:
        mdot = power_target / abs(euler)
        notes.append("mdot_from_power_target")
    else:
        mdot = mdot_from_area
        if mdot > 0:
            notes.append("mdot_from_annulus")

    if power_target > 1e-12:
        power = power_target
        notes.append("power_from_target")
    else:
        power = mdot * abs(euler)

    tip_r = r_m + 0.5 * span if r_m > 0 else 0.0
    tip_u = omega * tip_r if omega > 0 and tip_r > 0 else (U * tip_r / r_m if r_m > 1e-9 and tip_r > 0 else U)
    tip_mach = tip_u / max(a_sound, 1e-9)

    return MeanlineResult(
        inputs=inp,
        beta1_deg=b1,
        beta2_deg=b2,
        alpha1_deg=alpha1,
        alpha2_deg=alpha2,
        metal_beta1_deg=metal_b1,
        metal_beta2_deg=metal_b2,
        incidence_deg=i_deg,
        deviation_deg=d_deg,
        u_m_s=U,
        w1_m_s=W1,
        w2_m_s=W2,
        c1_m_s=C1,
        c2_m_s=C2,
        c_theta1_m_s=Ct1,
        c_theta2_m_s=Ct2,
        c_axial1_m_s=Ca1,
        c_axial2_m_s=Ca2,
        a1_m_s=a_sound,
        rho1_kg_m3=rho1,
        mach_w1=Mw1,
        mach_c1=Mc1,
        mach_w2=Mw2,
        euler_work_j_kg=euler,
        degree_of_reaction=r_rxn,
        flow_coefficient=phi,
        stage_loading=psi,
        efficiency_proxy=eta_proxy,
        yplus_first_layer_m=y_m,
        mean_radius_m=r_m,
        rpm=rpm_in,
        omega_rad_s=omega,
        span_m=span,
        annulus_area_m2=annulus,
        mass_flow_kg_s=mdot,
        power_w=power,
        tip_radius_m=tip_r,
        tip_speed_m_s=tip_u,
        tip_mach_proxy=tip_mach,
        u_from_rpm=u_from_rpm,
        notes=notes,
    )
