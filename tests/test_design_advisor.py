"""Industry-standard advice + auto-apply patches (shipped path)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from impulsecalc.design_advisor import analyze_against_standards
from impulsecalc.design_report import build_design_report
from impulsecalc.meanline import MeanlineInputs, compute_meanline
from impulsecalc.postprocess import synthetic_surface_pressure


def test_high_mach_triggers_fail_and_patches():
    ml = compute_meanline(MeanlineInputs(w1_m_s=950, pure_impulse_lock=True, gamma=1.3))
    surf = synthetic_surface_pressure(p1_pa=5.5e5, mach_w1=ml.mach_w1, n=60)
    rep = build_design_report(
        surf,
        ml=ml,
        gamma=1.3,
        blade_shape={"thickness_ratio": 0.22, "arc_bulge": 1.2, "thickness_peak_x": 0.4},
        include_plots=False,
        write_exports=False,
    )
    adv = rep.industry_advice
    assert adv
    assert "items" in adv
    assert any(i["status"] in ("fail", "warn") for i in adv["items"])
    # Must cite sources
    assert adv["sources"]
    assert any("Lieblein" in s or "Hill" in s or "Dixon" in s or "Zweifel" in s for s in adv["sources"])
    # Patches must target meanline or bladeform fields
    if adv["auto_apply_safe"]:
        assert adv["patches_merged"]
        for p in adv["patches_merged"]:
            assert p["section"] in ("meanline", "bladeform")
            assert p["field"]
            assert p["action"] in ("set", "delta", "scale")


def test_analyze_against_standards_direct():
    metrics = {
        "lieblein_df_ss": 0.85,
        "peak_ss_x_c": 0.25,
        "diffusion_ss": 1.2,
        "n_shocks": 2,
        "min_p02_p01": 0.7,
        "solidity": 1.4,
        "stage_loading_psi": 1.5,
        "mach_w1": 1.5,
        "w1_m_s": 950,
        "beta1_deg": 72,
        "peak_ss_m_isen": 2.0,
    }
    adv = analyze_against_standards(metrics, shocks=[{"p02_p01": 0.7}], shape={"arc_bulge": 1.2})
    assert adv.patches_merged
    fails = [i for i in adv.items if i.status == "fail"]
    assert fails
    assert all(i.cite for i in fails)


def test_mild_metrics_can_pass():
    metrics = {
        "lieblein_df_ss": 0.4,
        "peak_ss_x_c": 0.45,
        "diffusion_ss": 0.5,
        "n_shocks": 0,
        "min_p02_p01": 0.98,
        "solidity": 1.4,
        "stage_loading_psi": 1.2,
        "mach_w1": 0.9,
        "w1_m_s": 500,
        "beta1_deg": 55,
        "peak_ss_m_isen": 1.1,
    }
    adv = analyze_against_standards(metrics, shocks=[], shape={"arc_bulge": 1.0})
    assert all(i.status == "pass" for i in adv.items)
    assert not adv.patches_merged
