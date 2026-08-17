"""Dense CFD design report for impulse-turbine cascade iteration.

Single entry ``build_design_report`` fuses surface pressure, mean-line
operating point, and loss/shock analysis into a package comparable in *role*
to an internal turbine design review board: where loss is, how bad, what knob
to turn next.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .cascade_loss import (
    CascadeLossMetrics,
    cascade_loss_from_meanline_proxy,
    cascade_loss_from_sample_rows,
    cascade_loss_from_station_means,
    isentropic_p0_from_p,
)
from .design_advisor import DesignAdvice, analyze_against_standards
from .gasdynamics import normal_shock, normal_shock_table, shock_jump_from_upstream_mach
from .loss_analysis import LossReport, ShockCandidate, analyze_losses
from .meanline import MeanlineResult
from .postprocess import (
    SurfacePressureResult,
    surface_pressure_to_csv_rows,
    write_surface_csv,
)


def _trapz(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    s = 0.0
    for i in range(len(xs) - 1):
        s += 0.5 * (ys[i] + ys[i + 1]) * (xs[i + 1] - xs[i])
    return s


def _interp(xs: list[float], ys: list[float], x: float) -> float:
    if not xs or not ys:
        return 0.0
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1] or xs[i] >= x >= xs[i + 1]:
            dx = xs[i + 1] - xs[i]
            t = 0.0 if abs(dx) < 1e-15 else (x - xs[i]) / dx
            return ys[i] + t * (ys[i + 1] - ys[i])
    return ys[-1]


def _peak_min(xs: list[float], ys: list[float]) -> tuple[float, float]:
    if not ys:
        return 0.0, 0.5
    i = min(range(len(ys)), key=lambda k: ys[k])
    return float(ys[i]), float(xs[i]) if xs else 0.5


def _peak_max(xs: list[float], ys: list[float]) -> tuple[float, float]:
    if not ys:
        return 0.0, 0.5
    i = max(range(len(ys)), key=lambda k: ys[k])
    return float(ys[i]), float(xs[i]) if xs else 0.5


def _isen_mach_from_cp(cp: float, gamma: float, m_ref: float) -> float:
    """Isentropic surface Mach estimate from Cp relative to freestream at M_ref.

    Clamps Cp to the isentropic vacuum limit so strong synthetic expansions
    do not produce nonsense M ≫ freestream.
    """
    g = max(gamma, 1.01)
    m_ref = max(float(m_ref), 1e-6)
    # Vacuum Cp for q_∞ = ½ γ p M²: Cp_vac = -2/(γ M²)
    cp_vac = -2.0 / (g * m_ref * m_ref)
    cp_use = max(float(cp), cp_vac * 0.995)

    if m_ref < 0.35:
        vr = math.sqrt(max(1.0 - cp_use, 0.0))
        return float(m_ref * vr)

    # p/p∞ = 1 + (γ/2) M² Cp
    pr = 1.0 + 0.5 * g * m_ref * m_ref * cp_use
    pr = max(pr, 1e-4)
    try:
        pt_p = (1.0 + 0.5 * (g - 1.0) * m_ref * m_ref) ** (g / (g - 1.0))
        pt_pl = pt_p / pr
        if pt_pl <= 1.0:
            return 0.0
        m2 = (2.0 / (g - 1.0)) * (pt_pl ** ((g - 1.0) / g) - 1.0)
        mloc = math.sqrt(max(m2, 0.0))
        # Cap at a generous multiple of freestream (design board, not CFD truth)
        return float(min(mloc, 3.5 * m_ref))
    except (ValueError, OverflowError):
        return float(m_ref)


@dataclass
class StationRow:
    x_c: float
    cp_ps: float
    cp_ss: float
    p_ps_pa: float
    p_ss_pa: float
    delta_cp: float
    m_isen_ss: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DesignMetrics:
    """Scalar board for design comparison across runs."""

    # operating
    beta1_deg: float
    beta2_deg: float
    camber_deg: float
    mach_w1: float
    w1_m_s: float
    p1_pa: float
    q_ref_pa: float
    euler_work_j_kg: float
    stage_loading_psi: float
    flow_coeff_phi: float
    solidity: float
    # loading / forces
    loading_int_dcp: float
    loading_front_frac: float  # 0–0.33 chord share of |loading|
    loading_mid_frac: float
    loading_aft_frac: float
    cx_force_coeff: float  # axial force ~ ∫ Cp d(y) proxy using ΔCp
    cy_force_coeff: float
    # suction / pressure peaks
    peak_ss_cp: float
    peak_ss_x_c: float
    peak_ps_cp: float
    peak_ps_x_c: float
    peak_ss_m_isen: float
    # LE / TE
    le_cp_ps: float
    le_cp_ss: float
    le_delta_cp: float
    te_cp_ps: float
    te_cp_ss: float
    te_delta_cp: float
    # diffusion
    diffusion_ss: float
    diffusion_ps: float
    lieblein_df_ss: float  # (Wmax-W2)/W1 proxy from isentropic M
    # shocks / health
    n_shocks: int
    max_shock_dcp: float
    strongest_shock_x_c: float | None
    # efficiency proxies
    eta_meanline_proxy: float
    surface_loss_penalty: float  # 0–0.4 additive from shocks/diffusion
    eta_design_proxy: float  # meanline * (1 - penalty)
    loss_severity_sum: float
    top_loss_id: str
    # data quality
    n_ps: int
    n_ss: int
    source: str
    # flight / stage derived (from meanline + geometry)
    mean_radius_m: float = 0.0
    rpm: float = 0.0
    span_m: float = 0.0
    mass_flow_kg_s: float = 0.0
    power_w: float = 0.0
    tip_mach_proxy: float = 0.0
    incidence_deg: float = 0.0
    deviation_deg: float = 0.0
    metal_beta1_deg: float = 0.0
    metal_beta2_deg: float = 0.0
    opening_o_s: float = 0.0
    throat_o_m: float = 0.0
    stagger_deg: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DesignReport:
    success: bool
    format: str
    operating: dict[str, Any]
    metrics: DesignMetrics
    loss_report: LossReport
    stations: list[StationRow]
    surface: dict[str, Any]
    surface_table: list[dict[str, Any]]
    shocks: list[dict[str, Any]]
    shock_relations_table: list[dict[str, Any]]  # Hill–Peterson §3.7 style
    normal_shock_chart: list[dict[str, Any]]  # ratios vs M1 reference table
    industry_advice: dict[str, Any]
    ranked_fixes: list[str]
    iteration_checklist: list[str]
    exports: dict[str, str]
    plots: dict[str, str]  # name → base64 png (optional)
    summary: str
    notes: list[str] = field(default_factory=list)
    # Industry-standard cascade total-pressure / KE loss (mass-averaged when possible)
    cascade_loss: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "format": self.format,
            "operating": dict(self.operating),
            "metrics": self.metrics.to_dict(),
            "loss_report": self.loss_report.to_dict(),
            "cascade_loss": dict(self.cascade_loss),
            "stations": [s.to_dict() for s in self.stations],
            "surface": dict(self.surface),
            "surface_table": list(self.surface_table),
            "shocks": list(self.shocks),
            "shock_relations_table": list(self.shock_relations_table),
            "normal_shock_chart": list(self.normal_shock_chart),
            "industry_advice": dict(self.industry_advice),
            "ranked_fixes": list(self.ranked_fixes),
            "iteration_checklist": list(self.iteration_checklist),
            "exports": dict(self.exports),
            "plots": dict(self.plots),
            "summary": self.summary,
            "notes": list(self.notes),
        }


def compute_cascade_loss_metrics(
    surf: SurfacePressureResult,
    stations: list[StationRow],
    *,
    ml: MeanlineResult | None = None,
    gamma: float = 1.3,
    mach_w1: float = 1.0,
    surface_loss_penalty: float = 0.0,
    inlet_outlet_samples: dict[str, Any] | None = None,
) -> CascadeLossMetrics:
    """Industry cascade loss ω, ζ from proper inlet/outlet stations or physics proxy.

    Blade surface Cp (LE/TE) is **not** a cascade rake — using it as p0 samples can
    invent p0_recovery > 1. Prefer:

    1. Explicit inlet/outlet mass-averaged samples (CFD rake / sample dict)
    2. Meanline + surface_loss_penalty proxy (always ω≥0, p0_recovery≤1)

    ``stations`` are surface stations for design loading only — not used for ω.
    """
    g = float(gamma)
    mw = max(float(mach_w1 if ml is None else ml.mach_w1), 0.05)
    p1 = float(ml.inputs.p1_pa) if ml is not None else float(surf.p_ref_pa)
    m2 = None
    if ml is not None:
        m2 = float(getattr(ml, "mach_w2", 0.0) or 0.0) or None

    samples = inlet_outlet_samples or {}
    # Real CFD rake path: mass-averaged inlet/outlet p and Mach lists
    if (
        samples.get("inlet_p")
        and samples.get("inlet_mach")
        and samples.get("outlet_p")
        and samples.get("outlet_mach")
    ):
        cl = cascade_loss_from_sample_rows(
            list(samples["inlet_p"]),
            list(samples["inlet_mach"]),
            list(samples["outlet_p"]),
            list(samples["outlet_mach"]),
            gamma=g,
            inlet_mass=samples.get("inlet_mass"),
            outlet_mass=samples.get("outlet_mass"),
        )
        # Physical clamp: total-pressure recovery cannot exceed 1 without energy input
        if cl.p0_recovery > 1.0 or cl.omega_pt < 0.0:
            cl = cascade_loss_from_station_means(
                p_in=cl.p_in_pa,
                p0_in=cl.p0_in_pa,
                p_out=max(cl.p_in_pa * 0.99, 1.0),  # placeholder static
                p0_out=min(cl.p0_out_pa, cl.p0_in_pa),
                gamma=g,
                source="mass_averaged_samples_clamped",
                notes=list(cl.mass_avg_notes)
                + ["clamped p0_out<=p0_in (no unphysical recovery)"],
            )
        return cl

    # Physics-valid proxy: freestream p0 with recovery drop from shocks/diffusion penalty
    pen = min(max(float(surface_loss_penalty), 0.0), 0.8)
    cl = cascade_loss_from_meanline_proxy(
        p1_pa=p1,
        mach_w1=mw,
        mach_w2=m2,
        gamma=g,
        loss_penalty=pen,
    )
    # Guarantee industry report invariants
    if cl.omega_pt < 0.0 or cl.p0_recovery > 1.0:
        p01 = isentropic_p0_from_p(p1, mw, g)
        rec = min(max(cl.p0_recovery, 0.5), 1.0)
        p02 = p01 * rec
        cl = cascade_loss_from_station_means(
            p_in=p1,
            p0_in=p01,
            p_out=p1,
            p0_out=min(p02, p01),
            gamma=g,
            source="meanline_proxy_clamped",
            notes=list(cl.mass_avg_notes) + ["enforced omega_pt>=0 p0_recovery<=1"],
        )
    cl.mass_avg_notes.append(
        "no CFD inlet/outlet rake — meanline/proxy loss (surface Cp is not a rake)"
    )
    # stations unused for ω by design (kept in signature for API stability)
    _ = stations
    return cl


def enrich_shock_with_jumps(
    shock: ShockCandidate,
    *,
    M_up: float,
    gamma: float,
    cp_upstream: float | None = None,
    cp_downstream: float | None = None,
) -> ShockCandidate:
    """Attach Hill–Peterson normal-shock ratios using estimated upstream Mach.

    M_up is surface isentropic Mach just upstream of the recompression (estimate).
    """
    M1 = max(float(M_up), 1.001)  # relations need M≥1 for a shock
    jump = normal_shock(M1, gamma)
    # Optional consistency check: surface ΔCp vs p2/p1 (order-of-magnitude only)
    note = (
        f"Normal-shock estimate (Hill–Peterson §3.7): "
        f"M1≈{jump.M1:.3f} → M2≈{jump.M2:.3f}, "
        f"p2/p1={jump.p2_p1:.3f}, T2/T1={jump.T2_T1:.3f}, "
        f"ρ2/ρ1={jump.rho2_rho1:.3f}, p02/p01={jump.p02_p01:.4f} (total-pressure loss)"
    )
    if cp_upstream is not None and cp_downstream is not None:
        note += f"; surface ΔCp≈{cp_downstream - cp_upstream:.3f} (qualitative)"

    return ShockCandidate(
        side=shock.side,
        x_c=shock.x_c,
        delta_cp=shock.delta_cp,
        severity=shock.severity,
        note=shock.note,
        M1=jump.M1,
        M2=jump.M2,
        p2_p1=jump.p2_p1,
        rho2_rho1=jump.rho2_rho1,
        T2_T1=jump.T2_T1,
        p02_p01=jump.p02_p01,
        kind="normal",
        estimate_note=(
            "M1 from surface isentropic Mach reconstruction; jump from perfect-gas "
            "normal-shock relations (γ from case). Not a full-field CFD probe."
        ),
    )


def compute_metrics(
    surf: SurfacePressureResult,
    *,
    ml: MeanlineResult | None = None,
    beta1_deg: float = 72.0,
    beta2_deg: float = -72.0,
    mach_w1: float = 1.4,
    solidity: float = 1.13688,
    gamma: float = 1.3,
    loss: LossReport | None = None,
) -> DesignMetrics:
    xs_ps, cp_ps = list(surf.x_c_ps), list(surf.cp_ps)
    xs_ss, cp_ss = list(surf.x_c_ss), list(surf.cp_ss)

    def dcp_at(x: float) -> float:
        return _interp(xs_ps, cp_ps, x) - _interp(xs_ss, cp_ss, x)

    # loading integral
    if xs_ps and len(xs_ps) == len(xs_ss) and xs_ps == xs_ss:
        dcp = [a - b for a, b in zip(cp_ps, cp_ss)]
        loading = _trapz(xs_ps, dcp)
        # force proxies
        cx = _trapz(xs_ps, dcp)  # axial ~ loading in cascade chord frame
        cy = 0.0
        for i in range(len(xs_ps) - 1):
            # rough normal contribution
            cy += 0.5 * (cp_ps[i] + cp_ps[i + 1] + cp_ss[i] + cp_ss[i + 1]) * (
                xs_ps[i + 1] - xs_ps[i]
            ) * 0.25
    else:
        xs = xs_ps or xs_ss
        dcp = [dcp_at(x) for x in xs]
        loading = _trapz(xs, dcp) if xs else 0.0
        cx, cy = loading, 0.0

    # spanwise loading thirds
    def load_band(a: float, b: float) -> float:
        xs = [i * 0.02 for i in range(int(a / 0.02), int(b / 0.02) + 1)]
        if len(xs) < 2:
            return 0.0
        return abs(_trapz(xs, [dcp_at(x) for x in xs]))

    f = load_band(0.0, 0.33)
    m = load_band(0.33, 0.66)
    a = load_band(0.66, 1.0)
    tot = max(f + m + a, 1e-12)

    peak_ss, peak_ss_x = _peak_min(xs_ss, cp_ss)
    peak_ps, peak_ps_x = _peak_max(xs_ps, cp_ps)
    te_ss = cp_ss[-1] if cp_ss else 0.0
    te_ps = cp_ps[-1] if cp_ps else 0.0
    le_ss = cp_ss[0] if cp_ss else 0.0
    le_ps = cp_ps[0] if cp_ps else 0.0
    diff_ss = te_ss - peak_ss
    diff_ps = te_ps - (min(cp_ps) if cp_ps else 0.0)

    m_ss_peak = _isen_mach_from_cp(peak_ss, gamma, mach_w1)
    m_ss_te = _isen_mach_from_cp(te_ss, gamma, mach_w1)
    # Lieblein DF ≈ (Wmax - W2)/W1 ≈ (Mmax - M2)/M1 for similar a
    if mach_w1 > 1e-6:
        lieblein = max(m_ss_peak - m_ss_te, 0.0) / mach_w1
    else:
        lieblein = 0.0

    n_shocks = 0
    max_dcp = 0.0
    shock_x = None
    if loss and loss.shock_candidates:
        n_shocks = len(loss.shock_candidates)
        best = max(loss.shock_candidates, key=lambda s: s.delta_cp)
        max_dcp = best.delta_cp
        shock_x = best.x_c

    eta_ml = float(ml.efficiency_proxy) if ml else 0.75
    penalty = 0.0
    if n_shocks:
        penalty += min(0.12, 0.04 * n_shocks + 0.01 * max_dcp)
    if diff_ss > 0.9:
        penalty += min(0.1, 0.05 * (diff_ss - 0.9))
    if peak_ss < -0.8 and mach_w1 > 1.0:
        penalty += min(0.08, 0.04 * abs(peak_ss + 0.5))
    if abs(le_ss - le_ps) > 0.5:
        penalty += 0.03
    penalty = min(0.35, penalty)
    eta_des = max(0.0, eta_ml * (1.0 - penalty))

    sev_sum = sum(L.severity for L in (loss.losses if loss else []))
    top_id = loss.losses[0].id if loss and loss.losses else ""

    return DesignMetrics(
        beta1_deg=float(beta1_deg),
        beta2_deg=float(beta2_deg),
        camber_deg=float(abs(beta1_deg - beta2_deg)),
        mach_w1=float(mach_w1),
        w1_m_s=float(ml.w1_m_s) if ml else 0.0,
        p1_pa=float(surf.p_ref_pa),
        q_ref_pa=float(surf.q_ref_pa),
        euler_work_j_kg=float(ml.euler_work_j_kg) if ml else 0.0,
        stage_loading_psi=float(ml.stage_loading) if ml else 0.0,
        flow_coeff_phi=float(ml.flow_coefficient) if ml else 0.0,
        solidity=float(solidity),
        loading_int_dcp=float(loading),
        loading_front_frac=float(f / tot),
        loading_mid_frac=float(m / tot),
        loading_aft_frac=float(a / tot),
        cx_force_coeff=float(cx),
        cy_force_coeff=float(cy),
        peak_ss_cp=float(peak_ss),
        peak_ss_x_c=float(peak_ss_x),
        peak_ps_cp=float(peak_ps),
        peak_ps_x_c=float(peak_ps_x),
        peak_ss_m_isen=float(m_ss_peak),
        le_cp_ps=float(le_ps),
        le_cp_ss=float(le_ss),
        le_delta_cp=float(le_ss - le_ps),
        te_cp_ps=float(te_ps),
        te_cp_ss=float(te_ss),
        te_delta_cp=float(abs(te_ss - te_ps)),
        diffusion_ss=float(diff_ss),
        diffusion_ps=float(diff_ps),
        lieblein_df_ss=float(lieblein),
        n_shocks=int(n_shocks),
        max_shock_dcp=float(max_dcp),
        strongest_shock_x_c=shock_x,
        eta_meanline_proxy=float(eta_ml),
        surface_loss_penalty=float(penalty),
        eta_design_proxy=float(eta_des),
        loss_severity_sum=float(sev_sum),
        top_loss_id=top_id,
        n_ps=len(cp_ps),
        n_ss=len(cp_ss),
        source=surf.source,
        mean_radius_m=float(ml.mean_radius_m) if ml else 0.0,
        rpm=float(ml.rpm) if ml else 0.0,
        span_m=float(ml.span_m) if ml else 0.0,
        mass_flow_kg_s=float(ml.mass_flow_kg_s) if ml else 0.0,
        power_w=float(ml.power_w) if ml else 0.0,
        tip_mach_proxy=float(ml.tip_mach_proxy) if ml else 0.0,
        incidence_deg=float(ml.incidence_deg) if ml else 0.0,
        deviation_deg=float(ml.deviation_deg) if ml else 0.0,
        metal_beta1_deg=float(ml.metal_beta1_deg) if ml else float(beta1_deg),
        metal_beta2_deg=float(ml.metal_beta2_deg) if ml else float(beta2_deg),
    )


def build_stations(
    surf: SurfacePressureResult, *, gamma: float = 1.3, mach_w1: float = 1.4, n: int = 21
) -> list[StationRow]:
    rows: list[StationRow] = []
    for i in range(n):
        x = i / max(n - 1, 1)
        cps = _interp(list(surf.x_c_ps), list(surf.cp_ps), x)
        css = _interp(list(surf.x_c_ss), list(surf.cp_ss), x)
        pps = _interp(list(surf.x_c_ps), list(surf.p_ps), x) if surf.p_ps else surf.p_ref_pa
        pss = _interp(list(surf.x_c_ss), list(surf.p_ss), x) if surf.p_ss else surf.p_ref_pa
        rows.append(
            StationRow(
                x_c=x,
                cp_ps=cps,
                cp_ss=css,
                p_ps_pa=pps,
                p_ss_pa=pss,
                delta_cp=cps - css,
                m_isen_ss=_isen_mach_from_cp(css, gamma, mach_w1),
            )
        )
    return rows


def _iteration_checklist(metrics: DesignMetrics, loss: LossReport) -> list[str]:
    steps = [
        "1. Read metrics board: η_design_proxy, loading thirds, SS peak x/c, n_shocks.",
        "2. Open ranked fixes — change only the top 1–2 knobs in §1–2 per iteration.",
        "3. Rebuild §3 case → §4 mesh/solve/sample (or synthetic reload) → re-run this report.",
        "4. Diff design_package.json metrics between runs (eta_design_proxy, peak_ss_cp, n_shocks).",
        "5. Stop when n_shocks=0 (or mild), SS peak not too forward, TE dump small, η_design rising.",
    ]
    if metrics.n_shocks:
        steps.insert(
            1,
            f"   → Active: {metrics.n_shocks} shock(s); strongest near x/c="
            f"{metrics.strongest_shock_x_c if metrics.strongest_shock_x_c is not None else '?'}.",
        )
    if loss.ranked_fixes:
        steps.insert(2, f"   → Top fix: {loss.ranked_fixes[0][:140]}")
    return steps


def build_design_report(
    surf: SurfacePressureResult,
    *,
    ml: MeanlineResult | None = None,
    beta1_deg: float | None = None,
    beta2_deg: float | None = None,
    mach_w1: float | None = None,
    solidity: float = 1.13688,
    gamma: float = 1.3,
    thickness_ratio: float = 0.50,
    le_fillet_r_c: float = 0.002,
    thickness_peak_x: float = 0.50,
    arc_bulge: float = 1.2,
    case_dir: str | Path | None = None,
    write_exports: bool = True,
    include_plots: bool = True,
    blade_shape: dict[str, Any] | None = None,
) -> DesignReport:
    """Primary entry: surface + operating point → dense design report + optional disk exports."""
    b1 = float(beta1_deg if beta1_deg is not None else (ml.beta1_deg if ml else 72.0))
    b2 = float(beta2_deg if beta2_deg is not None else (ml.beta2_deg if ml else -72.0))
    mw = float(mach_w1 if mach_w1 is not None else (ml.mach_w1 if ml else 1.4))
    sol = float(solidity if solidity else (ml.inputs.solidity if ml else 1.4))
    g = float(gamma if gamma else (ml.inputs.gamma if ml else 1.3))

    loss = analyze_losses(
        surf,
        beta1_deg=b1,
        beta2_deg=b2,
        mach_w1=mw,
        thickness_ratio=thickness_ratio,
        solidity=sol,
        le_fillet_r_c=le_fillet_r_c,
    )
    stations = build_stations(surf, gamma=g, mach_w1=mw, n=21)

    # Enrich each surface-detected shock with Hill–Peterson jump table
    enriched: list[ShockCandidate] = []
    for sh in loss.shock_candidates:
        # M_up: isentropic M on that side just upstream of x_c (station table)
        xs = list(surf.x_c_ss) if sh.side == "SS" else list(surf.x_c_ps)
        cps = list(surf.cp_ss) if sh.side == "SS" else list(surf.cp_ps)
        x_up = max(sh.x_c - 0.04, 0.0)
        cp_up = _interp(xs, cps, x_up)
        cp_dn = _interp(xs, cps, min(sh.x_c + 0.04, 1.0))
        M_up = _isen_mach_from_cp(cp_up, g, mw)
        # If surface says subsonic, still report freestream-based weak shock estimate
        if M_up < 1.05:
            M_up = max(mw, 1.05)
        enriched.append(
            enrich_shock_with_jumps(
                sh, M_up=M_up, gamma=g, cp_upstream=cp_up, cp_downstream=cp_dn
            )
        )
    loss.shock_candidates = enriched
    # Refresh loss notes with total-pressure loss summary
    if enriched:
        worst = min(enriched, key=lambda s: s.p02_p01 if s.p02_p01 is not None else 1.0)
        loss.notes.append(
            f"strongest_p0_loss p02/p01={worst.p02_p01:.4f} at {worst.side} x/c≈{worst.x_c:.2f} "
            f"(Hill–Peterson normal-shock estimate)"
        )

    metrics = compute_metrics(
        surf,
        ml=ml,
        beta1_deg=b1,
        beta2_deg=b2,
        mach_w1=mw,
        solidity=sol,
        gamma=g,
        loss=loss,
    )
    cascade_loss_m = compute_cascade_loss_metrics(
        surf,
        stations,
        ml=ml,
        gamma=g,
        mach_w1=mw,
        surface_loss_penalty=float(metrics.surface_loss_penalty),
    )
    cascade_loss_dict = cascade_loss_m.to_dict()
    # Throat / stagger from blade shape when available
    shape_d = dict(blade_shape or {})
    if shape_d or ml:
        try:
            from .geometry import (
                BladeGeometry,
                BladeShapeParams,
                resolved_stagger_deg,
                throat_metrics,
            )

            sh = BladeShapeParams.from_dict(shape_d)
            metal_b1 = float(ml.metal_beta1_deg) if ml else b1
            metal_b2 = float(ml.metal_beta2_deg) if ml else b2
            chord = float(ml.inputs.chord_m) if ml else 0.01
            geom = BladeGeometry(
                chord_m=chord,
                beta1_deg=metal_b1,
                beta2_deg=metal_b2,
                solidity=sol,
                shape=sh,
            )
            th = throat_metrics(geom)
            metrics.throat_o_m = float(th.get("throat_o_m") or 0.0)
            metrics.opening_o_s = float(th.get("opening_o_s") or 0.0)
            metrics.stagger_deg = float(resolved_stagger_deg(geom))
        except Exception:  # noqa: BLE001
            pass

    table = surface_pressure_to_csv_rows(surf)
    shocks = [s.to_dict() for s in loss.shock_candidates]
    shock_relations_table = list(shocks)
    # Textbook reference chart data (ratios vs M1) for UI / export
    chart_M = sorted(
        {round(s.M1, 3) for s in enriched if s.M1 is not None}
        | {1.0, 1.2, 1.4, 1.5, 1.6, 1.8, 2.0, 2.5, 3.0, round(mw, 2)}
    )
    normal_shock_chart = normal_shock_table(chart_M, g)

    operating = {
        "beta1_deg": b1,
        "beta2_deg": b2,
        "mach_w1": mw,
        "solidity": sol,
        "gamma": g,
        "p1_pa": surf.p_ref_pa,
        "q_ref_pa": surf.q_ref_pa,
        "source": surf.source,
    }
    if ml:
        operating.update(
            {
                "metal_beta1_deg": ml.metal_beta1_deg,
                "metal_beta2_deg": ml.metal_beta2_deg,
                "incidence_deg": ml.incidence_deg,
                "deviation_deg": ml.deviation_deg,
                "mean_radius_m": ml.mean_radius_m,
                "rpm": ml.rpm,
                "span_m": ml.span_m,
                "mass_flow_kg_s": ml.mass_flow_kg_s,
                "power_w": ml.power_w,
                "tip_mach_proxy": ml.tip_mach_proxy,
                "u_m_s": ml.u_m_s,
            }
        )
    operating["opening_o_s"] = metrics.opening_o_s
    operating["throat_o_m"] = metrics.throat_o_m
    operating["stagger_deg"] = metrics.stagger_deg
    if ml:
        operating.update(
            {
                "w1_m_s": ml.w1_m_s,
                "w2_m_s": ml.w2_m_s,
                "u_m_s": ml.u_m_s,
                "euler_work_j_kg": ml.euler_work_j_kg,
                "stage_loading": ml.stage_loading,
                "flow_coefficient": ml.flow_coefficient,
                "rho1_kg_m3": ml.rho1_kg_m3,
                "t1_k": ml.inputs.t1_k,
                "r_specific": ml.inputs.r_specific_j_kg_k,
                "mu_pa_s": ml.inputs.mu_pa_s,
                "chord_m": ml.inputs.chord_m,
                "blade_name": ml.inputs.blade_name,
            }
        )

    exports: dict[str, str] = {}
    plots: dict[str, str] = {}
    notes = list(surf.notes or []) + list(loss.notes or [])

    if write_exports and case_dir:
        cdir = Path(case_dir)
        if cdir.is_dir():
            pp = cdir / "postProcessing"
            pp.mkdir(parents=True, exist_ok=True)
            csv_path = write_surface_csv(pp / "surface_pressure.csv", surf)
            exports["surface_csv"] = str(csv_path)
            # station CSV
            st_path = pp / "stations.csv"
            with st_path.open("w", encoding="utf-8", newline="") as f:
                f.write("x_c,cp_ps,cp_ss,p_ps_pa,p_ss_pa,delta_cp,m_isen_ss\n")
                for s in stations:
                    f.write(
                        f"{s.x_c},{s.cp_ps},{s.cp_ss},{s.p_ps_pa},{s.p_ss_pa},"
                        f"{s.delta_cp},{s.m_isen_ss}\n"
                    )
            exports["stations_csv"] = str(st_path)
            loss_path = pp / "loss_report.json"
            loss_path.write_text(json.dumps(loss.to_dict(), indent=2), encoding="utf-8")
            exports["loss_json"] = str(loss_path)
            cl_path = pp / "cascade_loss.json"
            cl_path.write_text(json.dumps(cascade_loss_dict, indent=2), encoding="utf-8")
            exports["cascade_loss_json"] = str(cl_path)
            metrics_path = pp / "design_metrics.json"
            metrics_path.write_text(json.dumps(metrics.to_dict(), indent=2), encoding="utf-8")
            exports["metrics_json"] = str(metrics_path)
            sh_path = pp / "shock_relations.csv"
            with sh_path.open("w", encoding="utf-8", newline="") as f:
                f.write(
                    "side,x_c,severity,M1,M2,p2_p1,rho2_rho1,T2_T1,p02_p01,kind,delta_cp\n"
                )
                for s in enriched:
                    f.write(
                        f"{s.side},{s.x_c},{s.severity},{s.M1},{s.M2},{s.p2_p1},"
                        f"{s.rho2_rho1},{s.T2_T1},{s.p02_p01},{s.kind},{s.delta_cp}\n"
                    )
            exports["shock_relations_csv"] = str(sh_path)
            chart_path = pp / "normal_shock_chart.json"
            chart_path.write_text(json.dumps(normal_shock_chart, indent=2), encoding="utf-8")
            exports["normal_shock_chart_json"] = str(chart_path)

    if include_plots:
        try:
            plots.update(
                _build_plot_pngs(
                    surf, loss, metrics, stations, normal_shock_chart=normal_shock_chart
                )
            )
        except Exception as exc:  # noqa: BLE001
            notes.append(f"plot_error:{exc}")

    checklist = _iteration_checklist(metrics, loss)
    p0_bits = ""
    if enriched:
        p0min = min(s.p02_p01 for s in enriched if s.p02_p01 is not None)
        p0_bits = f" · min p02/p01≈{p0min:.4f}"
    flight_bits = ""
    if metrics.tip_mach_proxy:
        flight_bits += f" · tipM≈{metrics.tip_mach_proxy:.2f}"
    if metrics.opening_o_s:
        flight_bits += f" · o/s={metrics.opening_o_s:.3f}"
    if metrics.mass_flow_kg_s:
        flight_bits += f" · ṁ={metrics.mass_flow_kg_s:.3g}kg/s"
    summary = (
        f"η_design≈{metrics.eta_design_proxy:.3f} (η_ml≈{metrics.eta_meanline_proxy:.3f}, "
        f"penalty={metrics.surface_loss_penalty:.3f}) · "
        f"ω_pt={cascade_loss_m.omega_pt:.4f} ζ_ke={cascade_loss_m.zeta_ke:.4f} "
        f"(p02/p01={cascade_loss_m.p0_recovery:.4f}) · "
        f"load∫ΔCp={metrics.loading_int_dcp:.3f} "
        f"(F/M/A={metrics.loading_front_frac:.0%}/{metrics.loading_mid_frac:.0%}/{metrics.loading_aft_frac:.0%}) · "
        f"SS peak Cp={metrics.peak_ss_cp:.2f}@x/c={metrics.peak_ss_x_c:.2f} "
        f"Misen≈{metrics.peak_ss_m_isen:.2f} · DF_SS≈{metrics.lieblein_df_ss:.2f} · "
        f"shocks={metrics.n_shocks}{p0_bits}{flight_bits} · {loss.summary}"
    )
    notes.append(
        "gasdynamics: Hill & Peterson §3.7 normal-shock ratios attached to each "
        "detected recompression (perfect-gas estimates)."
    )
    notes.append(
        f"cascade_loss: ω=(p01−p02)/(p01−p1)={cascade_loss_m.omega_pt:.4f} "
        f"source={cascade_loss_m.source}"
    )

    shape_for_advice = dict(blade_shape or {})
    shape_for_advice.setdefault("thickness_ratio", thickness_ratio)
    shape_for_advice.setdefault("thickness_peak_x", thickness_peak_x)
    shape_for_advice.setdefault("arc_bulge", arc_bulge)
    shape_for_advice.setdefault("le_fillet_r_c", le_fillet_r_c)
    advice = analyze_against_standards(
        metrics.to_dict(),
        shocks=shocks,
        shape=shape_for_advice,
    )
    industry = advice.to_dict()
    # Prefer industry ranked patches in checklist
    if advice.patches_merged:
        checklist.insert(
            0,
            "0. Review §5a industry pass/fail table, then Auto-apply patches & re-run "
            f"({len(advice.patches_merged)} knob changes).",
        )

    # Comparable package v3 + comparison CSVs (after industry advice is ready)
    if write_exports and case_dir and Path(case_dir).is_dir():
        from .design_package import assemble_comparable_package, write_comparable_package

        ml_in = ml.inputs.to_dict() if ml else {}
        ml_res = ml.to_dict() if ml else {}
        domain: dict[str, Any] = {}
        if isinstance(operating, dict):
            for k in (
                "opening_o_s", "throat_o_m", "stagger_deg",
                "x_up_c", "x_dn_c", "x_min", "x_max", "y_span_m",
            ):
                if k in operating:
                    domain[k] = operating[k]
        meta_path = Path(case_dir) / "impulsecalc_case_meta.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                gmeta = meta.get("geometry") or {}
                if gmeta.get("domain"):
                    domain = dict(gmeta["domain"])
                else:
                    if gmeta.get("x_up_c") is not None:
                        domain.setdefault("x_up_c", gmeta.get("x_up_c"))
                    if gmeta.get("x_dn_c") is not None:
                        domain.setdefault("x_dn_c", gmeta.get("x_dn_c"))
            except Exception:  # noqa: BLE001
                pass

        full_summary = summary + " · " + advice.summary
        pkg = assemble_comparable_package(
            operating=operating,
            metrics=metrics.to_dict(),
            meanline_inputs=ml_in,
            meanline_result=ml_res,
            blade_shape=shape_for_advice,
            domain=domain,
            stations=[s.to_dict() for s in stations],
            surface_table=table,
            shocks=shocks,
            shock_relations_table=shock_relations_table,
            loss_report=loss.to_dict(),
            industry_advice=industry,
            ranked_fixes=list(loss.ranked_fixes),
            summary=full_summary,
            case_dir=str(Path(case_dir).resolve()),
            export_paths=dict(exports),
            notes=list(notes),
            blade_name=(ml.inputs.blade_name if ml else None),
        )
        pkg["cascade_loss"] = cascade_loss_dict
        pkg["normal_shock_chart"] = normal_shock_chart
        pkg["iteration_checklist"] = list(checklist)
        pkg["surface_summary"] = {
            "source": surf.source,
            "n_ps": len(surf.x_c_ps or []),
            "n_ss": len(surf.x_c_ss or []),
            "p_ref_pa": surf.p_ref_pa,
            "q_ref_pa": surf.q_ref_pa,
        }
        pp = Path(case_dir) / "postProcessing"
        written = write_comparable_package(pp, pkg, filename="design_package.json")
        exports.update(written)

    return DesignReport(
        success=True,
        format="impulsecalc_design_package_v3",
        operating=operating,
        metrics=metrics,
        loss_report=loss,
        stations=stations,
        surface=surf.to_dict(),
        surface_table=table,
        shocks=shocks,
        shock_relations_table=shock_relations_table,
        normal_shock_chart=normal_shock_chart,
        industry_advice=industry,
        ranked_fixes=list(loss.ranked_fixes),
        iteration_checklist=checklist,
        exports=exports,
        plots=plots,
        summary=summary + " · " + advice.summary,
        notes=notes,
        cascade_loss=cascade_loss_dict,
    )


def _build_plot_pngs(
    surf: SurfacePressureResult,
    loss: LossReport,
    metrics: DesignMetrics,
    stations: list[StationRow],
    *,
    normal_shock_chart: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    import base64
    import io

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .figures import figure_cp_vs_x

    out: dict[str, str] = {}

    def fig_b64(fig) -> str:
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    shocks = loss.shock_candidates
    fig = figure_cp_vs_x(
        surf.x_c_ps,
        surf.cp_ps,
        surf.x_c_ss,
        surf.cp_ss,
        title=f"Cp vs x/c · Mw1={metrics.mach_w1:.2f} · η≈{metrics.eta_design_proxy:.3f}",
        shock_x=[s.x_c for s in shocks],
        shock_labels=[f"{s.side} {s.severity}" for s in shocks[:2]] if shocks else None,
    )
    out["cp"] = fig_b64(fig)

    # Loading ΔCp and isentropic M along chord
    fig2, axes = plt.subplots(1, 2, figsize=(9.0, 3.6), dpi=110, facecolor="#f5f2e8")
    ax = axes[0]
    ax.set_facecolor("#faf8f0")
    xs = [s.x_c for s in stations]
    ax.plot(xs, [s.delta_cp for s in stations], "k-", lw=1.6, label="ΔCp = Cp_PS−Cp_SS")
    ax.axhline(0, color="#666", lw=0.5)
    ax.set_xlabel("x/c")
    ax.set_ylabel("ΔCp (loading)")
    ax.set_title("Blade loading distribution")
    ax.grid(True, alpha=0.35)
    ax.legend(fontsize=8)

    ax = axes[1]
    ax.set_facecolor("#faf8f0")
    ax.plot(xs, [s.m_isen_ss for s in stations], "r-", lw=1.6, label="M_isen SS")
    ax.axhline(1.0, color="#aa5500", ls="--", lw=1, label="M=1")
    ax.set_xlabel("x/c")
    ax.set_ylabel("Isentropic Mach (SS)")
    ax.set_title("SS isentropic Mach estimate")
    ax.grid(True, alpha=0.35)
    ax.legend(fontsize=8)
    fig2.tight_layout()
    out["loading_mach"] = fig_b64(fig2)

    # Loss severity bar
    fig3, ax = plt.subplots(figsize=(7.2, max(2.5, 0.35 * max(len(loss.losses), 1) + 1.2)), dpi=110, facecolor="#f5f2e8")
    ax.set_facecolor("#faf8f0")
    labels = [L.location[:28] for L in loss.losses[:8]]
    vals = [L.severity for L in loss.losses[:8]]
    if not labels:
        labels, vals = ["none"], [0.0]
    y = list(range(len(labels)))
    ax.barh(y, vals, color="#a04040")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("Relative severity")
    ax.set_title("Loss map (higher = fix first)")
    ax.set_xlim(0, 1.05)
    ax.invert_yaxis()
    ax.grid(True, axis="x", alpha=0.35)
    fig3.tight_layout()
    out["loss_bars"] = fig_b64(fig3)

    # Hill–Peterson §3.7 style: ratios vs M1 + detected shocks marked
    chart = normal_shock_chart or normal_shock_table(None, 1.3)
    fig4, axes = plt.subplots(1, 2, figsize=(9.2, 3.8), dpi=110, facecolor="#f5f2e8")
    Ms = [row["M1"] for row in chart]
    ax = axes[0]
    ax.set_facecolor("#faf8f0")
    ax.plot(Ms, [row["p2_p1"] for row in chart], "b-o", ms=3, label="p₂/p₁")
    ax.plot(Ms, [row["T2_T1"] for row in chart], "r-s", ms=3, label="T₂/T₁")
    ax.plot(Ms, [row["rho2_rho1"] for row in chart], "g-^", ms=3, label="ρ₂/ρ₁")
    for s in shocks:
        if s.M1 and s.p2_p1:
            ax.plot(s.M1, s.p2_p1, "k*", ms=10)
    ax.set_xlabel("M₁ (upstream)")
    ax.set_ylabel("Static ratios")
    ax.set_title("Normal shock · static jumps (Hill–Peterson §3.7)")
    ax.grid(True, alpha=0.35)
    ax.legend(fontsize=8)

    ax = axes[1]
    ax.set_facecolor("#faf8f0")
    ax.plot(Ms, [row["M2"] for row in chart], "b-o", ms=3, label="M₂")
    ax.plot(Ms, [row["p02_p01"] for row in chart], "m-s", ms=3, label="p₀₂/p₀₁")
    for s in shocks:
        if s.M1 and s.p02_p01 is not None:
            ax.plot(s.M1, s.p02_p01, "k*", ms=10)
    ax.set_xlabel("M₁ (upstream)")
    ax.set_ylabel("M₂ · p₀ ratio")
    ax.set_title("Normal shock · M₂ & total-pressure loss")
    ax.grid(True, alpha=0.35)
    ax.legend(fontsize=8)
    ax.set_ylim(0, max(1.05, max(row["M2"] for row in chart) * 1.05))
    fig4.tight_layout()
    out["hill_shock_chart"] = fig_b64(fig4)

    return out


def compare_design_metrics(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Diff two metrics dicts for offline iteration (A = baseline, B = candidate)."""
    keys = sorted(set(a) | set(b))
    delta: dict[str, Any] = {}
    for k in keys:
        va, vb = a.get(k), b.get(k)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            delta[k] = {"a": va, "b": vb, "delta_b_minus_a": vb - va}
        elif va != vb:
            delta[k] = {"a": va, "b": vb}
    better = []
    if isinstance(a.get("eta_design_proxy"), (int, float)) and isinstance(
        b.get("eta_design_proxy"), (int, float)
    ):
        if b["eta_design_proxy"] > a["eta_design_proxy"]:
            better.append("eta_design_proxy improved")
        elif b["eta_design_proxy"] < a["eta_design_proxy"]:
            better.append("eta_design_proxy worsened")
    if isinstance(a.get("n_shocks"), (int, float)) and isinstance(b.get("n_shocks"), (int, float)):
        if b["n_shocks"] < a["n_shocks"]:
            better.append("fewer shocks")
        elif b["n_shocks"] > a["n_shocks"]:
            better.append("more shocks")
    return {"deltas": delta, "notes": better}
