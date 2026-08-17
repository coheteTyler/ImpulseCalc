"""Radial impulse bucket: dual arcs, sharp or filleted tips, meshable outline."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from impulsecalc.geometry import (
    BladeGeometry,
    BladeShapeParams,
    blade_closed_polygon,
    cascade_blade_outlines,
    blade_preview_payload,
    throat_metrics,
    _circular_arc_le_te,
    _impulse_bucket_polygon,
    _max_tip_fillet_radius,
)


def test_arc_exact_le_te_points():
    pts = _circular_arc_le_te(0.01, 0.005, 48)
    assert pts[0] == (0.0, 0.0)
    assert pts[-1] == (0.01, 0.0)
    assert max(y for _, y in pts) > 0.0045


def test_bucket_has_sharp_le_te_vertices():
    """Both arcs share LE/TE — polygon must include exact tip points (not a slab)."""
    c = 0.01
    geom = BladeGeometry(
        chord_m=c,
        beta1_deg=72,
        beta2_deg=-72,
        solidity=1.14,
        shape=BladeShapeParams(
            upper_sagitta_c=0.48,
            lower_sagitta_c=0.28,
            le_fillet_r_c=0.0,
            te_fillet_r_c=0.0,
            n_points=160,
        ),
    )
    poly = blade_closed_polygon(geom)
    le = any(abs(p[0]) < 1e-12 and abs(p[1]) < 1e-12 for p in poly)
    te = any(abs(p[0] - c) < 1e-12 and abs(p[1]) < 1e-12 for p in poly)
    assert le and te
    for i, (x, y) in enumerate(poly):
        if abs(x) < 1e-12 and abs(y) < 1e-12:
            prv = poly[i - 1]
            nxt = poly[(i + 1) % len(poly)]
            d_prv = (prv[0] ** 2 + prv[1] ** 2) ** 0.5
            d_nxt = (nxt[0] ** 2 + nxt[1] ** 2) ** 0.5
            assert d_prv < 0.12 * c
            assert d_nxt < 0.12 * c
            break


def test_mid_solid_from_dual_sagitta():
    """Mid solid thickness ≈ (upper − lower)·chord."""
    c = 0.01
    hu, hl = 0.48, 0.28
    geom = BladeGeometry(
        chord_m=c,
        beta1_deg=72,
        beta2_deg=-72,
        solidity=1.2,
        shape=BladeShapeParams(
            upper_sagitta_c=hu,
            lower_sagitta_c=hl,
            le_fillet_r_c=0.0,
            te_fillet_r_c=0.0,
            n_points=200,
        ),
    )
    poly = blade_closed_polygon(geom)
    # sample y values near mid-chord
    mid_ys = sorted(p[1] for p in poly if abs(p[0] - 0.5 * c) < 0.05 * c)
    assert len(mid_ys) >= 2
    t_mid = mid_ys[-1] - mid_ys[0]
    expected = (hu - hl) * c
    assert abs(t_mid - expected) < 0.15 * expected  # discrete sample tolerance
    assert max(p[1] for p in poly) == pytest.approx(hu * c, rel=0.02)


def test_legacy_arc_bulge_maps_to_sagitta():
    sh = BladeShapeParams.from_dict({"arc_bulge": 1.15, "thickness_ratio": 0.18})
    assert sh.upper_sagitta_c > sh.lower_sagitta_c
    assert abs(sh.thickness_ratio - (sh.upper_sagitta_c - sh.lower_sagitta_c)) < 1e-9


def test_fillet_removes_sharp_tip_and_respects_pitch_cap():
    c = 0.01
    pitch = c / 1.2
    hu, hl = 0.48 * c, 0.28 * c
    r_cap = _max_tip_fillet_radius(c, hu, hl, pitch)
    assert r_cap > 0
    assert 2 * r_cap <= pitch + 1e-12  # diameter ≤ inter-blade gap

    geom = BladeGeometry(
        chord_m=c,
        beta1_deg=72,
        beta2_deg=-72,
        solidity=1.2,
        shape=BladeShapeParams(
            upper_sagitta_c=0.48,
            lower_sagitta_c=0.28,
            le_fillet_r_c=0.05,  # large request — will clamp
            te_fillet_r_c=0.05,
            n_points=160,
        ),
    )
    poly = blade_closed_polygon(geom)
    # With fillets, exact (0,0) / (c,0) knife points should not appear
    sharp_le = any(abs(p[0]) < 1e-12 and abs(p[1]) < 1e-12 for p in poly)
    assert not sharp_le
    assert len(poly) > 40


def test_cascade_passage_upper_faces_lower():
    """Neighbor blade is pitch-shifted; throat stays positive."""
    c, s = 0.01, 0.008796
    geom = BladeGeometry(
        chord_m=c,
        beta1_deg=72,
        beta2_deg=-72,
        solidity=c / s,
        shape=BladeShapeParams(upper_sagitta_c=0.48, lower_sagitta_c=0.28),
    )
    outs = cascade_blade_outlines(geom, 3)
    assert len(outs) == 3
    # y-offset between center and upper neighbor ≈ pitch
    y0 = sum(p[1] for p in outs[1]) / len(outs[1])
    y1 = sum(p[1] for p in outs[2]) / len(outs[2])
    assert abs((y1 - y0) - s) < 1e-9
    th = throat_metrics(geom)
    assert th["throat_o_m"] > 0.1 * s
    assert th["opening_o_s"] > 0.1


def test_cascade_and_preview_api():
    c, s = 0.01, 0.008796
    geom = BladeGeometry(
        chord_m=c,
        beta1_deg=72,
        beta2_deg=-72,
        solidity=c / s,
        shape=BladeShapeParams(upper_sagitta_c=0.48, lower_sagitta_c=0.28),
    )
    outs = cascade_blade_outlines(geom, 3)
    assert len(outs) == 3
    p = blade_preview_payload(geom, n_blades=3)
    assert len(p["cascade"]) == 3
    assert abs(p["meanline"][0]["x"]) < 1e-9
    assert abs(p["meanline"][-1]["x"] - c) < 1e-9
    assert p["shape"]["upper_sagitta_c"] == pytest.approx(0.48, rel=1e-6)
    assert p["shape"]["lower_sagitta_c"] == pytest.approx(0.28, rel=1e-6)


def test_f1_scale_builds():
    c = 0.055
    geom = BladeGeometry(
        chord_m=c,
        beta1_deg=65,
        beta2_deg=-65,
        solidity=2.2,
        shape=BladeShapeParams(
            upper_sagitta_c=0.46,
            lower_sagitta_c=0.30,
            le_fillet_r_c=0.015,
            te_fillet_r_c=0.012,
            n_points=160,
        ),
    )
    poly = blade_closed_polygon(geom)
    assert len(poly) > 40
    # closed loop
    assert abs(poly[0][0] - poly[-1][0]) < 1e-12
    assert abs(poly[0][1] - poly[-1][1]) < 1e-12
