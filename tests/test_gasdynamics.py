"""Hill–Peterson §3.7 normal/oblique shock relations — shipped functions only."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from impulsecalc.gasdynamics import (
    normal_shock,
    normal_shock_table,
    oblique_shock_from_beta,
    oblique_shock_from_deflection,
)
from impulsecalc.design_report import build_design_report
from impulsecalc.meanline import MeanlineInputs, compute_meanline
from impulsecalc.postprocess import synthetic_surface_pressure


def test_normal_shock_textbook_inequalities_m15_gamma13():
    r = normal_shock(1.5, gamma=1.3)
    assert r.M1 == pytest.approx(1.5)
    assert r.M2 < r.M1
    assert r.M2 < 1.0  # subsonic after normal shock
    assert r.p2_p1 > 1.0
    assert r.rho2_rho1 > 1.0
    assert r.T2_T1 > 1.0
    assert 0.0 < r.p02_p01 < 1.0  # total pressure loss
    # Stronger shock → more loss
    r2 = normal_shock(2.5, gamma=1.3)
    assert r2.p02_p01 < r.p02_p01
    assert r2.p2_p1 > r.p2_p1


def test_normal_shock_gamma14_m2():
    r = normal_shock(2.0, gamma=1.4)
    # Classic γ=1.4, M=2: p2/p1 = 4.5 exactly
    assert r.p2_p1 == pytest.approx(4.5, rel=1e-9)
    assert r.M2 == pytest.approx(0.57735026919, rel=1e-6)
    assert r.rho2_rho1 == pytest.approx(2.6666666667, rel=1e-6)


def test_subsonic_identity():
    r = normal_shock(0.8, gamma=1.4)
    assert r.p2_p1 == pytest.approx(1.0)
    assert r.p02_p01 == pytest.approx(1.0)
    assert r.M2 == pytest.approx(0.8)


def test_normal_shock_table_nonempty():
    rows = normal_shock_table(gamma=1.4)
    assert len(rows) >= 5
    assert all("M2" in row and "p02_p01" in row for row in rows)


def test_oblique_from_beta_weaker_than_normal():
    # Same Mn as a normal shock of M=1.5 when β=90°
    n = normal_shock(1.5, 1.4)
    o = oblique_shock_from_beta(2.0, 48.0, 1.4)  # Mn = 2 sin48 ≈ 1.49
    assert o.p2_p1 > 1.0
    assert o.p02_p01 < 1.0
    assert o.Mn1 > 1.0
    assert o.theta_deg > 0.0
    # Weak oblique total-pressure loss should be less severe than strong normal at M=2
    n2 = normal_shock(2.0, 1.4)
    assert o.p02_p01 > n2.p02_p01


def test_oblique_from_deflection_weak():
    r = oblique_shock_from_deflection(2.0, 10.0, gamma=1.4, branch="weak")
    assert r is not None
    assert r.branch == "weak"
    assert r.beta_deg > 0
    assert r.p2_p1 > 1.0


def test_design_report_shock_table_has_hill_peterson_fields():
    ml = compute_meanline(MeanlineInputs(w1_m_s=950, pure_impulse_lock=True, gamma=1.3))
    surf = synthetic_surface_pressure(p1_pa=5.5e5, mach_w1=ml.mach_w1, n=60)
    rep = build_design_report(
        surf, ml=ml, gamma=1.3, write_exports=False, include_plots=False
    )
    assert rep.shock_relations_table is not None
    assert rep.normal_shock_chart
    # High-Mw1 synthetic produces shocks with full ratio set
    assert rep.shocks
    s0 = rep.shocks[0]
    for key in ("M1", "M2", "p2_p1", "rho2_rho1", "T2_T1", "p02_p01"):
        assert key in s0
        assert s0[key] is not None
    assert s0["M2"] < s0["M1"]
    assert s0["p2_p1"] > 1.0
    assert 0 < s0["p02_p01"] < 1.0
    # Chart rows are textbook table entries
    assert all("p02_p01" in row for row in rep.normal_shock_chart)
