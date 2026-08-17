"""Industry-standard cascade criteria → ranked upstream design patches.

Compares design-board metrics against published turbomachinery limits and
emits machine-applicable form patches (§1 mean-line / §2 blade shape) so the
UI can auto-apply and re-run the pipeline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Limits (with literature sources). Values are engineering guidelines used in
# axial cascade / turbine design practice — not hard certification rules.
# ---------------------------------------------------------------------------
STANDARDS: list[dict[str, Any]] = [
    {
        "id": "lieblein_df",
        "name": "Lieblein diffusion factor (SS)",
        "metric": "lieblein_df_ss",
        "limit": 0.60,
        "op": "<=",
        "unit": "—",
        "cite": (
            "S. Lieblein, “Loss and Stall Analysis of Compressor Cascades,” "
            "ASME J. Basic Eng., 1959; classical DF limit ≈ 0.6 also in "
            "Dixon & Hall, Fluid Mechanics and Thermodynamics of Turbomachinery, "
            "7th ed., Ch. 3–5 (cascade diffusion / loading limits)."
        ),
        "why": "DF ≳ 0.6 correlates with rapid boundary-layer growth and separation risk on the suction surface.",
    },
    {
        "id": "de_haller",
        "name": "de Haller number proxy (W₂/W₁ ≈ M_te/M_peak)",
        "metric": "de_haller_proxy",
        "limit": 0.72,
        "op": ">=",
        "unit": "—",
        "cite": (
            "P. de Haller, “Das Verhalten von Tragflügelgittern in Axialverdichtern "
            "und im Windkanal,” BWK, 1953; restated in Dixon & Hall and NASA cascade "
            "design notes as W₂/W₁ ≳ 0.72 to limit diffusion."
        ),
        "why": "Too much velocity diffusion across the blade row drives separation and profile loss.",
    },
    {
        "id": "shock_p0_loss",
        "name": "Shock total-pressure recovery p₀₂/p₀₁",
        "metric": "min_p02_p01",
        "limit": 0.90,
        "op": ">=",
        "unit": "—",
        "cite": (
            "Hill & Peterson, Mechanics and Thermodynamics of Propulsion, 2nd ed., "
            "§3.7 “Shocks” (normal-shock p₀₂/p₀₁ tables); stronger shocks (lower p₀₂/p₀₁) "
            "destroy available work in impulse turbines."
        ),
        "why": "Passage shocks with large total-pressure loss dominate stage efficiency at high relative Mach.",
    },
    {
        "id": "n_shocks",
        "name": "Number of detected surface shocks",
        "metric": "n_shocks",
        "limit": 0,
        "op": "<=",
        "unit": "count",
        "cite": (
            "Same Hill & Peterson §3.7; design practice for supersonic relative inlet "
            "impulse blades aims to weaken or eliminate strong passage shocks "
            "(see also NASA SP-36 compressor/turbine cascade chapters on shock losses)."
        ),
        "why": "Each strong recompression is a candidate entropy generator.",
    },
    {
        "id": "ss_peak_location",
        "name": "SS peak suction location x/c",
        "metric": "peak_ss_x_c",
        "limit": 0.35,
        "op": ">=",
        "unit": "x/c",
        "cite": (
            "Aft-loaded cascade practice for high-speed blades: move peak suction aft of "
            "~0.3–0.4 c to reduce LE spike / shock pairing (Dixon & Hall; common in "
            "controlled-diffusion and aft-loaded turbine airfoil literature)."
        ),
        "why": "Forward peak suction at high M couples to LE shocks and early BL transition.",
    },
    {
        "id": "ss_diffusion_cp",
        "name": "SS Cp recompression (TE − Cp_min)",
        "metric": "diffusion_ss",
        "limit": 0.90,
        "op": "<=",
        "unit": "ΔCp",
        "cite": (
            "Cascade profile-loss correlation practice: large suction-surface adverse "
            "pressure gradients raise profile loss (Lieblein loss correlations; "
            "Dixon & Hall Ch. 3)."
        ),
        "why": "Large SS recompression after peak suction risks separation.",
    },
    {
        "id": "solidity_range",
        "name": "Solidity σ = c/s",
        "metric": "solidity",
        "limit_lo": 1.0,
        "limit_hi": 1.8,
        "op": "between",
        "unit": "—",
        "cite": (
            "Zweifel (1945) loading / pitch rule of thumb and axial turbine design "
            "handbooks: mid-range solidity ~1–1.8 for impulse-like rows before "
            "secondary-loss growth (Zweifel, “The Spacing of Turbo-Machine Blading,” "
            "Brown Boveri Rev., 1945; Dixon & Hall)."
        ),
        "why": "Too sparse → overloading; too dense → secondary / friction loss.",
    },
    {
        "id": "stage_loading",
        "name": "Stage loading ψ = Δh₀/U²",
        "metric": "stage_loading_psi",
        "limit": 2.5,
        "op": "<=",
        "unit": "—",
        "cite": (
            "Smith chart / axial turbine loading practice: very high ψ increases "
            "turning and loss (Smith, “A Simple Correlation of Turbine Efficiency,” "
            "JRAeS, 1965; Dixon & Hall stage loading discussions)."
        ),
        "why": "Extreme loading amplifies secondary flow and shock strength in impulse stages.",
    },
]


@dataclass
class DesignPatch:
    """A single form edit the UI can apply."""

    section: str  # "meanline" | "bladeform"
    field: str  # form control name (e.g. beta1, bulge)
    action: str  # "set" | "delta" | "scale"
    value: float
    label: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AdviceItem:
    standard_id: str
    name: str
    status: str  # pass | warn | fail
    metric: str
    value: float | None
    limit_text: str
    cite: str
    why: str
    suggestion: str
    patches: list[DesignPatch] = field(default_factory=list)
    priority: float = 0.0  # higher = apply first

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["patches"] = [p.to_dict() for p in self.patches]
        return d


@dataclass
class DesignAdvice:
    items: list[AdviceItem]
    patches_merged: list[DesignPatch]
    summary: str
    sources: list[str]
    auto_apply_safe: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [i.to_dict() for i in self.items],
            "patches_merged": [p.to_dict() for p in self.patches_merged],
            "summary": self.summary,
            "sources": list(self.sources),
            "auto_apply_safe": self.auto_apply_safe,
        }


def _get(metrics: dict[str, Any], key: str) -> float | None:
    v = metrics.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _eval(op: str, value: float, std: dict[str, Any]) -> str:
    if op == "<=":
        lim = float(std["limit"])
        if value <= lim:
            return "pass"
        if value <= lim * 1.15:
            return "warn"
        return "fail"
    if op == ">=":
        lim = float(std["limit"])
        if value >= lim:
            return "pass"
        if value >= lim * 0.9:
            return "warn"
        return "fail"
    if op == "between":
        lo, hi = float(std["limit_lo"]), float(std["limit_hi"])
        if lo <= value <= hi:
            return "pass"
        if lo * 0.85 <= value <= hi * 1.15:
            return "warn"
        return "fail"
    return "warn"


def _patches_for(std_id: str, metrics: dict[str, Any], shape: dict[str, Any] | None) -> tuple[list[DesignPatch], str]:
    """Return (patches, human suggestion) for a failed/warned standard."""
    shape = shape or {}
    mw = _get(metrics, "mach_w1") or 1.0
    df = _get(metrics, "lieblein_df_ss") or 0.0
    sol = _get(metrics, "solidity") or 1.13688
    b1 = _get(metrics, "beta1_deg") or 72.0
    peak_x = float(shape.get("thickness_peak_x") or 0.5)
    bulge = float(shape.get("arc_bulge") or 1.2)
    thk = float(shape.get("thickness_ratio") or 0.50)
    w1 = _get(metrics, "w1_m_s") or 950.0

    if std_id == "lieblein_df":
        return (
            [
                DesignPatch("bladeform", "bulge", "set", max(0.85, bulge * 0.85), "reduce camber arc bulge"),
                DesignPatch("bladeform", "peak", "set", min(0.58, peak_x + 0.05), "move thickness peak aft"),
                DesignPatch("meanline", "solidity", "set", min(1.7, sol + 0.1), "raise solidity slightly"),
            ],
            f"DF={df:.2f} > 0.6: reduce camber rate (bulge↓), aft-load thickness peak, or raise σ.",
        )
    if std_id == "de_haller":
        return (
            [
                DesignPatch("bladeform", "bulge", "set", max(0.85, bulge * 0.9), "ease camber"),
                DesignPatch("meanline", "beta1", "delta", -2.0, "reduce |β₁| 2° (less turning)"),
            ],
            "de Haller proxy low: cut total turning or diffusion (β₁↓ a few deg, bulge↓).",
        )
    if std_id in ("shock_p0_loss", "n_shocks"):
        patches = [
            DesignPatch("meanline", "w1", "scale", 0.92, "drop |W₁| ~8% (lower Mw1)"),
            DesignPatch("bladeform", "bulge", "set", max(0.9, min(bulge, 1.05)), "flatten mid camber"),
            DesignPatch("bladeform", "line_in", "set", 0.08, "short inlet straight to ease LE expansion"),
            DesignPatch("meanline", "incidence", "set", 1.0, "small positive incidence (metal more open)"),
        ]
        if thk > 0.18:
            patches.append(DesignPatch("bladeform", "thk", "set", max(0.12, thk - 0.04), "thin section for high M"))
        return (
            patches,
            f"Shock / p₀ loss at Mw1≈{mw:.2f}: lower relative inlet speed, soften camber, thin if t/c high.",
        )
    if std_id == "ss_peak_location":
        return (
            [
                DesignPatch("bladeform", "line_in", "set", 0.10, "inlet straight ~0.10c"),
                DesignPatch("bladeform", "peak", "set", min(0.55, max(peak_x, 0.45)), "thickness peak mid-aft"),
                DesignPatch("bladeform", "bulge", "set", max(0.9, bulge * 0.92), "reduce front camber"),
                DesignPatch("bladeform", "camber_dist", "set", 0.65, "aft-load camber distribution"),
            ],
            "SS peak too far forward: aft-load the section (inlet line + peak x/c↑, camber_dist↑, bulge↓).",
        )
    if std_id == "ss_diffusion_cp":
        return (
            [
                DesignPatch("bladeform", "line_out", "set", 0.10, "exit straight ~0.10c"),
                DesignPatch("bladeform", "peak", "set", max(0.35, peak_x - 0.05), "move peak slightly forward"),
                DesignPatch("bladeform", "bulge", "set", max(0.9, bulge * 0.9), "reduce camber"),
            ],
            "Large SS recompression: add exit straight, reduce aft camber/diffusion.",
        )
    if std_id == "solidity_range":
        o_s = _get(metrics, "opening_o_s")
        if o_s is not None and o_s < 0.15 and sol > 1.2:
            return (
                [
                    DesignPatch("meanline", "solidity", "set", max(1.0, sol - 0.15), "lower σ to open throat"),
                    DesignPatch("bladeform", "te_thk", "set", 0.003, "thin TE slightly"),
                ],
                f"σ={sol:.2f} with low o/s≈{o_s:.2f}: open the passage (σ↓ / thinner TE).",
            )
        target = 1.35 if sol < 1.0 else 1.55
        return (
            [DesignPatch("meanline", "solidity", "set", target, f"set σ→{target}")],
            f"σ={sol:.2f} outside ~1.0–1.8: move toward mid-range Zweifel pitch.",
        )
    if std_id == "stage_loading":
        return (
            [
                DesignPatch("meanline", "U", "scale", 1.08, "raise blade speed U ~8%"),
                DesignPatch("meanline", "rpm", "scale", 1.08, "or raise rpm ~8% (if U from rpm)"),
                DesignPatch("meanline", "beta1", "delta", -3.0, "trim turning 3°"),
            ],
            "ψ very high: raise U/rpm or reduce |β| turning to unload the stage.",
        )
    return [], "Review metrics against cited limits."


def analyze_against_standards(
    metrics: dict[str, Any],
    *,
    shocks: list[dict[str, Any]] | None = None,
    shape: dict[str, Any] | None = None,
) -> DesignAdvice:
    """Compare metrics (+ optional shocks) to STANDARDS; build merged patches."""
    m = dict(metrics or {})
    shocks = shocks or []

    # Derived de Haller proxy: M_isen TE / M_isen peak ≈ W2/W1 under isothermal a
    peak_m = _get(m, "peak_ss_m_isen") or 1.0
    # approximate TE M from peak and diffusion if not present
    te_m = peak_m / max(1.0 + 0.5 * (_get(m, "diffusion_ss") or 0.0), 1.05)
    m["de_haller_proxy"] = te_m / max(peak_m, 1e-6)

    p0s = [s.get("p02_p01") for s in shocks if s.get("p02_p01") is not None]
    m["min_p02_p01"] = float(min(p0s)) if p0s else 1.0

    items: list[AdviceItem] = []
    sources: list[str] = []

    for std in STANDARDS:
        mid = std["id"]
        metric = std["metric"]
        val = _get(m, metric)
        if val is None:
            continue
        op = std["op"]
        status = _eval(op, val, std)
        if op == "between":
            lim_txt = f"in [{std['limit_lo']}, {std['limit_hi']}]"
        elif op == "<=":
            lim_txt = f"≤ {std['limit']}"
        else:
            lim_txt = f"≥ {std['limit']}"

        patches: list[DesignPatch] = []
        suggestion = "Within guideline."
        if status != "pass":
            patches, suggestion = _patches_for(mid, m, shape)

        pri = 0.0
        if status == "fail":
            pri = 2.0 + abs(val - float(std.get("limit", val) or val))
        elif status == "warn":
            pri = 1.0

        items.append(
            AdviceItem(
                standard_id=mid,
                name=std["name"],
                status=status,
                metric=metric,
                value=val,
                limit_text=lim_txt,
                cite=std["cite"],
                why=std["why"],
                suggestion=suggestion,
                patches=patches,
                priority=pri,
            )
        )
        if std["cite"] not in sources:
            sources.append(std["cite"])

    items.sort(key=lambda x: -x.priority)
    # Merge patches: later lower-priority cannot overwrite higher if same field
    merged: dict[tuple[str, str], DesignPatch] = {}
    for it in items:
        if it.status == "pass":
            continue
        for p in it.patches:
            key = (p.section, p.field)
            if key not in merged:
                merged[key] = p

    fails = sum(1 for i in items if i.status == "fail")
    warns = sum(1 for i in items if i.status == "warn")
    summary = (
        f"{fails} fail / {warns} warn vs industry cascade limits "
        f"({len(merged)} unique upstream patches ready to apply)."
    )
    return DesignAdvice(
        items=items,
        patches_merged=list(merged.values()),
        summary=summary,
        sources=sources,
        auto_apply_safe=len(merged) > 0,
    )
