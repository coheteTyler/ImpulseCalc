"""Flight-stage knobs: mean-line (r_m/rpm/U, i/δ, ṁ/power) and geometry (stagger/throat/TE/LE/camber).

Exercises shipped impulsecalc modules only — no reimplementation of the math under test.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from impulsecalc.meanline import MeanlineInputs, compute_meanline
from impulsecalc.geometry import (
    BladeGeometry,
    BladeShapeParams,
    blade_closed_polygon,
    meanline_lines_arc,
    resolved_stagger_deg,
    throat_metrics,
)
from impulsecalc.design_report import build_design_report
from impulsecalc.postprocess import synthetic_surface_pressure


def test_u_from_rpm_matches_omega_r():
    r_m = 0.10
    rpm = 30000.0
    omega = rpm * 2.0 * math.pi / 60.0
    u_expect = omega * r_m
    res = compute_meanline(
        MeanlineInputs(
            mean_radius_m=r_m,
            rpm=rpm,
            u_from_rpm=True,
            blade_speed_u_m_s=1.0,  # must be overridden
            span_m=0.02,
        )
    )
    assert res.u_from_rpm is True
    assert res.u_m_s == pytest.approx(u_expect, rel=1e-9)
    assert res.omega_rad_s == pytest.approx(omega, rel=1e-9)


def test_free_u_tip_speed_ignores_nonzero_rpm():
    """HTML default may leave rpm filled while free-U is on; tip U follows free U."""
    r_m = 0.08
    span = 0.012
    U = 100.0
    rpm_leftover = 54000.0  # must not drive tip speed in free-U mode
    res = compute_meanline(
        MeanlineInputs(
            mean_radius_m=r_m,
            rpm=rpm_leftover,
            u_from_rpm=False,
            blade_speed_u_m_s=U,
            span_m=span,
        )
    )
    tip_r = r_m + 0.5 * span
    tip_expect = U * tip_r / r_m
    assert res.u_from_rpm is False
    assert res.u_m_s == pytest.approx(U)
    assert res.omega_rad_s == pytest.approx(U / r_m, rel=1e-9)
    assert res.tip_speed_m_s == pytest.approx(tip_expect, rel=1e-9)
    assert res.tip_mach_proxy == pytest.approx(
        tip_expect / res.a1_m_s, rel=1e-9
    )
    # leftover rpm is not used for tip kinematics
    assert abs(res.tip_speed_m_s - (rpm_leftover * 2 * math.pi / 60) * tip_r) > 1.0


def test_incidence_deviation_split_metal_vs_flow():
    flow_b1, flow_b2 = 70.0, -70.0
    i, d = 3.0, 2.0
    res = compute_meanline(
        MeanlineInputs(
            beta1_deg=flow_b1,
            beta2_deg=flow_b2,
            pure_impulse_lock=False,
            incidence_deg=i,
            deviation_deg=d,
        )
    )
    assert res.beta1_deg == pytest.approx(flow_b1)
    assert res.beta2_deg == pytest.approx(flow_b2)
    assert res.metal_beta1_deg == pytest.approx(flow_b1 - i)
    assert res.metal_beta2_deg == pytest.approx(flow_b2 + d)
    assert res.metal_beta1_deg != res.beta1_deg
    assert res.metal_beta2_deg != res.beta2_deg


def test_span_and_mdot_power_finite():
    res = compute_meanline(
        MeanlineInputs(
            mean_radius_m=0.08,
            rpm=54000,
            u_from_rpm=True,
            span_m=0.015,
            mass_flow_kg_s=0.0,
            power_target_w=0.0,
        )
    )
    assert res.annulus_area_m2 > 0
    assert res.mass_flow_kg_s > 0
    assert res.power_w > 0
    assert math.isfinite(res.tip_mach_proxy)
    assert res.tip_radius_m > res.mean_radius_m

    # power target drives ṁ
    res2 = compute_meanline(
        MeanlineInputs(
            mean_radius_m=0.08,
            blade_speed_u_m_s=450,
            span_m=0.015,
            power_target_w=50_000.0,
        )
    )
    assert res2.power_w == pytest.approx(50_000.0)
    assert res2.mass_flow_kg_s == pytest.approx(
        50_000.0 / abs(res2.euler_work_j_kg), rel=1e-6
    )


def test_geometry_flight_variants_finite_profile_and_throat():
    shape = BladeShapeParams(
        thickness_ratio=0.20,
        thickness_peak_x=0.48,
        arc_bulge=1.1,
        inlet_line_frac=0.05,
        outlet_line_frac=0.08,
        stagger_deg=5.0,
        camber_dist=0.75,
        le_shape="elliptical",
        te_thickness_c=0.006,
        te_wedge_deg=12.0,
        le_fillet_r_c=0.01,
        te_fillet_r_c=0.004,
    ).clamp()
    geom = BladeGeometry(
        chord_m=0.024,
        beta1_deg=69.0,  # metal
        beta2_deg=-68.0,
        solidity=1.35,
        shape=shape,
        n_points=80,
    )
    poly = blade_closed_polygon(geom)
    assert len(poly) >= 20
    for x, y in poly:
        assert math.isfinite(x) and math.isfinite(y)
    th = throat_metrics(geom)
    assert th["throat_o_m"] > 0
    assert th["opening_o_s"] > 0
    assert math.isfinite(th["opening_o_s"])
    assert resolved_stagger_deg(geom) == pytest.approx(5.0)


def test_free_stagger_changes_meanline_vs_auto():
    auto = meanline_lines_arc(0.024, 72, -72, 0.0, 0.0, 1.0, 60, stagger_deg=None)
    free = meanline_lines_arc(0.024, 72, -72, 0.0, 0.0, 1.0, 60, stagger_deg=10.0)
    # LE is origin; TE chord length preserved; free stagger rotates path
    assert math.hypot(auto[-1][0] - auto[0][0], auto[-1][1] - auto[0][1]) == pytest.approx(
        0.024, rel=1e-5
    )
    assert math.hypot(free[-1][0] - free[0][0], free[-1][1] - free[0][1]) == pytest.approx(
        0.024, rel=1e-5
    )
    # midpoints should differ under free stagger
    mid_a = auto[len(auto) // 2]
    mid_f = free[len(free) // 2]
    assert math.hypot(mid_a[0] - mid_f[0], mid_a[1] - mid_f[1]) > 1e-4


def test_camber_dist_shifts_meanline_mid_height():
    front = meanline_lines_arc(
        0.024, 72, -72, 0.0, 0.0, 1.2, 80, camber_dist=0.0
    )
    aft = meanline_lines_arc(
        0.024, 72, -72, 0.0, 0.0, 1.2, 80, camber_dist=1.0
    )
    # max camber location (s) should move aft when camber_dist → 1
    def peak_s(pts):
        ys = [p[1] for p in pts]
        i = max(range(len(ys)), key=lambda k: ys[k])
        return i / (len(ys) - 1)

    assert peak_s(aft) >= peak_s(front) - 0.02


def test_le_shapes_produce_finite_closed_poly():
    for le in ("circular", "elliptical", "wedge"):
        sh = BladeShapeParams(le_shape=le, le_fillet_r_c=0.015).clamp()
        geom = BladeGeometry(shape=sh, beta1_deg=72, beta2_deg=-72)
        poly = blade_closed_polygon(geom)
        assert all(math.isfinite(x) and math.isfinite(y) for x, y in poly)
        assert poly[0] == poly[-1] or math.hypot(
            poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]
        ) < 1e-9


def test_design_report_includes_flight_metrics():
    ml = compute_meanline(
        MeanlineInputs(
            mean_radius_m=0.09,
            rpm=48000,
            u_from_rpm=True,
            span_m=0.014,
            incidence_deg=2.0,
            deviation_deg=1.5,
            pure_impulse_lock=True,
        )
    )
    shape = BladeShapeParams(
        stagger_deg=2.0,
        camber_dist=0.6,
        le_shape="wedge",
        te_thickness_c=0.005,
        te_wedge_deg=10.0,
    ).clamp()
    surf = synthetic_surface_pressure(
        p1_pa=ml.inputs.p1_pa,
        mach_w1=ml.mach_w1,
        thickness_ratio=shape.thickness_ratio,
        beta1_deg=ml.metal_beta1_deg,
        beta2_deg=ml.metal_beta2_deg,
    )
    rep = build_design_report(
        surf,
        ml=ml,
        solidity=ml.inputs.solidity,
        blade_shape=shape.to_dict(),
        write_exports=False,
        include_plots=False,
    )
    m = rep.metrics
    assert m.tip_mach_proxy > 0
    assert m.mass_flow_kg_s > 0
    assert m.metal_beta1_deg == pytest.approx(ml.metal_beta1_deg)
    assert m.incidence_deg == pytest.approx(2.0)
    assert m.opening_o_s > 0
    assert "tipM" in rep.summary or "o/s" in rep.summary or m.tip_mach_proxy > 0


def test_ui_exposes_flight_controls():
    body = (ROOT / "static" / "calcbody.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "calc.js").read_text(encoding="utf-8")
    for name in (
        "r_m", "rpm", "u_from_rpm", "span", "mdot", "power",
        "incidence", "deviation", "stagger", "camber_dist",
        "le_shape", "te_thk", "te_wedge",
    ):
        assert f'name="{name}"' in body or f"name='{name}'" in body, name
    for token in (
        "mean_radius_m", "incidence_deg", "deviation_deg",
        "stagger_deg", "camber_dist", "te_thickness_c", "le_shape",
        "opening_o_s", "tip_mach_proxy",
    ):
        assert token in js or token in body, token
    assert "bladeShapeFromForm" in js
    assert "te_thk" in js
    assert "camber_dist" in js
