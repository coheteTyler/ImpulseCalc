"""Shock / loss diagnostics from blade surface pressure for design optimization.

Given pressure-side (PS) and suction-side (SS) distributions, locate likely
loss mechanisms (passage shocks, over-expansion, LE incidence, TE dump,
loading unbalance) and emit concrete geometry / operating-point fixes.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from .postprocess import SurfacePressureResult


@dataclass
class ShockCandidate:
    side: str  # "SS" | "PS"
    x_c: float
    delta_cp: float
    severity: str  # mild | moderate | strong
    note: str
    # Hill–Peterson §3.7 style jump estimates (filled by design_report when γ, M known)
    M1: float | None = None
    M2: float | None = None
    p2_p1: float | None = None
    rho2_rho1: float | None = None
    T2_T1: float | None = None
    p02_p01: float | None = None
    kind: str = "normal"  # normal | oblique_estimate
    estimate_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LossItem:
    id: str
    location: str  # e.g. "SS x/c≈0.55" or "passage midspan"
    mechanism: str
    evidence: str
    severity: float  # 0..1 relative weight
    fix: str
    design_knobs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LossReport:
    source: str
    beta1_deg: float
    beta2_deg: float
    mach_w1: float
    # integrated metrics
    delta_cp_loading: float  # rough ∫(Cp_ps - Cp_ss) d(x/c)
    peak_ss_cp: float
    peak_ss_x_c: float
    peak_ps_cp: float
    diffusion_ss: float  # (Cp_te - Cp_min) on SS
    diffusion_ps: float
    shock_candidates: list[ShockCandidate]
    losses: list[LossItem]
    ranked_fixes: list[str]
    summary: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "beta1_deg": self.beta1_deg,
            "beta2_deg": self.beta2_deg,
            "mach_w1": self.mach_w1,
            "delta_cp_loading": self.delta_cp_loading,
            "peak_ss_cp": self.peak_ss_cp,
            "peak_ss_x_c": self.peak_ss_x_c,
            "peak_ps_cp": self.peak_ps_cp,
            "diffusion_ss": self.diffusion_ss,
            "diffusion_ps": self.diffusion_ps,
            "shock_candidates": [s.to_dict() for s in self.shock_candidates],
            "losses": [L.to_dict() for L in self.losses],
            "ranked_fixes": list(self.ranked_fixes),
            "summary": self.summary,
            "notes": list(self.notes),
        }


def _trapz(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    s = 0.0
    for i in range(len(xs) - 1):
        s += 0.5 * (ys[i] + ys[i + 1]) * (xs[i + 1] - xs[i])
    return s


def _smooth_dcp(xs: list[float], cps: list[float]) -> list[tuple[float, float]]:
    """Return (x_mid, dCp/d(x/c)) with light 3-pt smoothing on Cp first."""
    n = len(xs)
    if n < 3:
        return []
    sm = list(cps)
    for i in range(1, n - 1):
        sm[i] = 0.25 * cps[i - 1] + 0.5 * cps[i] + 0.25 * cps[i + 1]
    out: list[tuple[float, float]] = []
    for i in range(n - 1):
        dx = xs[i + 1] - xs[i]
        if abs(dx) < 1e-12:
            continue
        out.append((0.5 * (xs[i] + xs[i + 1]), (sm[i + 1] - sm[i]) / dx))
    return out


def _find_shocks(
    side: str, xs: list[float], cps: list[float], *, mach_w1: float
) -> list[ShockCandidate]:
    """Detect abrupt pressure recovery (shock-like) as large positive dCp/dx."""
    grads = _smooth_dcp(xs, cps)
    if not grads:
        return []
    # Threshold scales with Mach: stronger shocks more likely when Mw1 > 1
    thr = 1.8 + 1.2 * max(mach_w1 - 1.0, 0.0)
    cands: list[ShockCandidate] = []
    for x, dcp in grads:
        if dcp < thr:
            continue
        if dcp > thr * 2.5:
            sev = "strong"
        elif dcp > thr * 1.5:
            sev = "moderate"
        else:
            sev = "mild"
        cands.append(
            ShockCandidate(
                side=side,
                x_c=float(x),
                delta_cp=float(dcp),
                severity=sev,
                note=f"Abrupt Cp recovery on {side} near x/c={x:.2f} (dCp/d(x/c)≈{dcp:.1f})",
            )
        )
    # Keep strongest few, non-clustered
    cands.sort(key=lambda s: -s.delta_cp)
    kept: list[ShockCandidate] = []
    for s in cands:
        if any(abs(s.x_c - k.x_c) < 0.08 for k in kept):
            continue
        kept.append(s)
        if len(kept) >= 3:
            break
    return kept


def analyze_losses(
    surf: SurfacePressureResult,
    *,
    beta1_deg: float = 72.0,
    beta2_deg: float = -72.0,
    mach_w1: float = 1.5,
    thickness_ratio: float = 0.22,
    solidity: float = 1.4,
    le_fillet_r_c: float = 0.002,
) -> LossReport:
    """Build a design-oriented loss report from surface Cp."""
    notes: list[str] = list(surf.notes or [])
    notes.append(f"source={surf.source}")

    xs_ps, cp_ps = list(surf.x_c_ps), list(surf.cp_ps)
    xs_ss, cp_ss = list(surf.x_c_ss), list(surf.cp_ss)

    # Loading proxy: positive when PS higher pressure than SS
    loading = 0.0
    if xs_ps and xs_ss and len(xs_ps) == len(xs_ss):
        loading = _trapz(xs_ps, [a - b for a, b in zip(cp_ps, cp_ss)])
    elif xs_ps and xs_ss:
        # resample SS onto PS x
        def interp(x: float) -> float:
            for i in range(len(xs_ss) - 1):
                if xs_ss[i] <= x <= xs_ss[i + 1] or xs_ss[i] >= x >= xs_ss[i + 1]:
                    t = (x - xs_ss[i]) / max(xs_ss[i + 1] - xs_ss[i], 1e-12)
                    return cp_ss[i] + t * (cp_ss[i + 1] - cp_ss[i])
            return cp_ss[-1]
        loading = _trapz(xs_ps, [cp_ps[i] - interp(xs_ps[i]) for i in range(len(xs_ps))])

    def peak_min(xs: list[float], cps: list[float]) -> tuple[float, float]:
        if not cps:
            return 0.0, 0.5
        i = min(range(len(cps)), key=lambda k: cps[k])
        return cps[i], xs[i] if xs else 0.5

    def peak_max(xs: list[float], cps: list[float]) -> tuple[float, float]:
        if not cps:
            return 0.0, 0.5
        i = max(range(len(cps)), key=lambda k: cps[k])
        return cps[i], xs[i] if xs else 0.5

    # SS peak suction = minimum Cp; PS peak pressure = maximum Cp (matches design_report)
    peak_ss, peak_ss_x = peak_min(xs_ss, cp_ss)
    peak_ps, _ = peak_max(xs_ps, cp_ps)
    te_ss = cp_ss[-1] if cp_ss else 0.0
    te_ps = cp_ps[-1] if cp_ps else 0.0
    le_ss = cp_ss[0] if cp_ss else 0.0
    le_ps = cp_ps[0] if cp_ps else 0.0
    diff_ss = te_ss - peak_ss
    # PS diffusion: recompression from local min Cp toward TE (not from peak max)
    ps_min = min(cp_ps) if cp_ps else 0.0
    diff_ps = te_ps - ps_min

    shocks = _find_shocks("SS", xs_ss, cp_ss, mach_w1=mach_w1)
    shocks += _find_shocks("PS", xs_ps, cp_ps, mach_w1=mach_w1)

    losses: list[LossItem] = []

    # 1) Passage / surface shocks
    for sh in shocks:
        sev = {"mild": 0.35, "moderate": 0.55, "strong": 0.8}.get(sh.severity, 0.5)
        if sh.side == "SS":
            fix = (
                "Weaken the SS shock: reduce front suction by lowering camber arc bulge, "
                "move thickness peak slightly aft (x/c 0.45→0.55), or drop |W₁|/inlet Mach. "
                "If incidence is high, reduce β₁ a few degrees toward axial."
            )
            knobs = ["arc_bulge", "thickness_peak_x", "w1_m_s", "beta1_deg", "solidity"]
        else:
            fix = (
                "PS shock is unusual for pure impulse — check LE geometry / incidence. "
                "Slightly increase LE fillet or reduce positive incidence (lower β₁)."
            )
            knobs = ["le_fillet_r_c", "beta1_deg", "inlet_line_frac"]
        losses.append(
            LossItem(
                id=f"shock_{sh.side}_{sh.x_c:.2f}",
                location=f"{sh.side} x/c≈{sh.x_c:.2f}",
                mechanism="Compressible shock / abrupt recompression",
                evidence=sh.note,
                severity=sev * min(1.0, 0.5 + 0.3 * max(mach_w1 - 1.0, 0.0)),
                fix=fix,
                design_knobs=knobs,
            )
        )

    # 2) Over-expansion / peak suction too strong or too forward
    if peak_ss < -0.6 and mach_w1 > 1.0:
        forward = peak_ss_x < 0.35
        losses.append(
            LossItem(
                id="ss_overexpansion",
                location=f"SS peak suction x/c≈{peak_ss_x:.2f}",
                mechanism="Strong suction-side expansion (pre-shock / wave drag risk)",
                evidence=f"min Cp_SS={peak_ss:.2f} at x/c={peak_ss_x:.2f}",
                severity=min(1.0, 0.4 + 0.25 * abs(peak_ss) + (0.15 if forward else 0.0)),
                fix=(
                    "Soften LE/front camber: set inlet straight line frac ~0.05–0.15, "
                    "reduce arc bulge toward 1.0, or thicken the nose slightly. "
                    "Forward peak suction (x/c<0.35) especially wants less LE camber."
                    if forward
                    else "Reduce mid-chord camber rate (lower arc bulge) or raise solidity "
                    "so the passage constrains expansion."
                ),
                design_knobs=["inlet_line_frac", "arc_bulge", "le_fillet_r_c", "solidity", "thickness_ratio"],
            )
        )

    # 3) Excessive SS diffusion (separation risk after shock or in subsonic patch)
    if diff_ss > 0.9:
        losses.append(
            LossItem(
                id="ss_diffusion",
                location=f"SS aft of x/c≈{peak_ss_x:.2f}",
                mechanism="Strong suction-side recompression / diffusion (separation risk)",
                evidence=f"Cp_TE − Cp_min on SS = {diff_ss:.2f}",
                severity=min(1.0, 0.3 + 0.4 * (diff_ss - 0.9)),
                fix=(
                    "Limit aft diffusion: move thickness peak forward slightly, "
                    "add a short exit straight (outlet_line_frac 0.05–0.12) at β₂, "
                    "or reduce total camber (|β₁−β₂|) if work allows."
                ),
                design_knobs=["thickness_peak_x", "outlet_line_frac", "beta1_deg", "beta2_deg", "arc_bulge"],
            )
        )

    # 4) LE incidence (PS/SS LE Cp asymmetry)
    le_jump = abs(le_ss - le_ps)
    if le_jump > 0.45:
        # high SS LE suction → positive incidence; high PS → negative
        pos_inc = le_ss < le_ps
        losses.append(
            LossItem(
                id="le_incidence",
                location="Leading edge",
                mechanism="LE incidence mismatch (metal angle vs relative flow)",
                evidence=f"Cp_LE SS={le_ss:.2f}, PS={le_ps:.2f} (Δ={le_jump:.2f})",
                severity=min(1.0, 0.25 + 0.5 * (le_jump - 0.45)),
                fix=(
                    "Positive incidence: reduce β₁ a few degrees or increase LE fillet; "
                    "check pure-impulse lock still matches design U/W₁."
                    if pos_inc
                    else "Negative incidence: increase |β₁| slightly or thin the LE fillet; "
                    "avoid a blunt LE if the relative flow is already aligned."
                ),
                design_knobs=["beta1_deg", "le_fillet_r_c", "blade_speed_u_m_s", "w1_m_s"],
            )
        )

    # 5) TE pressure mismatch / base dump
    te_jump = abs(te_ss - te_ps)
    if te_jump > 0.25:
        losses.append(
            LossItem(
                id="te_dump",
                location="Trailing edge",
                mechanism="TE pressure mismatch / base dump",
                evidence=f"Cp_TE SS={te_ss:.2f}, PS={te_ps:.2f}",
                severity=min(0.7, 0.2 + te_jump),
                fix=(
                    "Tighten TE: lower te_fillet_r_c carefully, keep exit metal angle β₂ "
                    "aligned with design relative exit, add short exit straight for cleaner dump."
                ),
                design_knobs=["te_fillet_r_c", "beta2_deg", "outlet_line_frac"],
            )
        )

    # 6) Loading / solidity mismatch
    if abs(loading) < 0.15 and mach_w1 > 0.8:
        losses.append(
            LossItem(
                id="underloaded",
                location="Full chord",
                mechanism="Low blade loading (little ΔCp between PS and SS)",
                evidence=f"∫(Cp_PS−Cp_SS)d(x/c)≈{loading:.3f}",
                severity=0.35,
                fix=(
                    "Increase turning (|β₁−β₂|) or raise solidity (c/s) so the cascade "
                    "loads more; confirm pure-impulse β₂=−β₁ is what you want."
                ),
                design_knobs=["beta1_deg", "beta2_deg", "solidity", "arc_bulge"],
            )
        )
    elif abs(loading) > 1.8:
        losses.append(
            LossItem(
                id="overloaded",
                location="Full chord",
                mechanism="Very high blade loading (secondary loss / shock risk)",
                evidence=f"∫(Cp_PS−Cp_SS)d(x/c)≈{loading:.3f}",
                severity=0.55,
                fix=(
                    "Unload slightly: lower solidity, reduce camber, or drop |W₁|. "
                    "Thick blades (t/c large) with high loading also need gentler LE."
                ),
                design_knobs=["solidity", "arc_bulge", "w1_m_s", "thickness_ratio"],
            )
        )

    # 7) Geometry heuristics from shape params
    if thickness_ratio > 0.24 and mach_w1 > 1.2:
        losses.append(
            LossItem(
                id="thick_supersonic",
                location="Passage blockage",
                mechanism="High thickness at high relative Mach (blockage / stronger waves)",
                evidence=f"t/c={thickness_ratio:.3f}, Mw1={mach_w1:.2f}",
                severity=0.4,
                fix="Thin the section (t/c toward 0.12–0.18) for supersonic relative inlet.",
                design_knobs=["thickness_ratio"],
            )
        )
    if le_fillet_r_c < 0.0015 and mach_w1 > 1.0:
        losses.append(
            LossItem(
                id="sharp_le",
                location="Leading edge",
                mechanism="Very sharp LE under compressible relative flow",
                evidence=f"LE r/c={le_fillet_r_c:.4f}",
                severity=0.25,
                fix="A slightly larger LE fillet (r/c ~0.005–0.015) can cut LE spike losses "
                "if incidence is imperfect — trade against peak suction.",
                design_knobs=["le_fillet_r_c"],
            )
        )

    # If almost nothing found, still give a healthy baseline note
    if not losses:
        losses.append(
            LossItem(
                id="baseline_ok",
                location="Overall",
                mechanism="No strong shock/loss signatures from surface Cp",
                evidence="Smooth Cp, moderate diffusion, LE/TE well matched",
                severity=0.1,
                fix=(
                    "Keep refining with CFD mesh/y⁺ checks. For more work, raise |β| or U; "
                    "for efficiency, watch SS peak suction and TE dump as you change camber."
                ),
                design_knobs=["mesh_nx", "y_plus_target", "arc_bulge"],
            )
        )

    losses.sort(key=lambda L: -L.severity)

    # Ranked unique fixes
    ranked: list[str] = []
    for L in losses:
        if L.severity < 0.2 and L.id != "baseline_ok":
            continue
        line = f"[{L.location}] {L.fix}"
        if line not in ranked:
            ranked.append(line)
        if len(ranked) >= 6:
            break

    camber = abs(beta1_deg - beta2_deg)
    summary = (
        f"Mw1≈{mach_w1:.2f}, camber≈{camber:.0f}°, loading∫ΔCp≈{loading:.2f}, "
        f"SS peak Cp={peak_ss:.2f} @ x/c={peak_ss_x:.2f}, SS diffusion={diff_ss:.2f}, "
        f"{len(shocks)} shock candidate(s). "
        + (
            f"Top loss: {losses[0].mechanism} @ {losses[0].location}."
            if losses
            else "No major flags."
        )
    )

    return LossReport(
        source=surf.source,
        beta1_deg=float(beta1_deg),
        beta2_deg=float(beta2_deg),
        mach_w1=float(mach_w1),
        delta_cp_loading=float(loading),
        peak_ss_cp=float(peak_ss),
        peak_ss_x_c=float(peak_ss_x),
        peak_ps_cp=float(peak_ps),
        diffusion_ss=float(diff_ss),
        diffusion_ps=float(diff_ps),
        shock_candidates=shocks,
        losses=losses,
        ranked_fixes=ranked,
        summary=summary,
        notes=notes,
    )
