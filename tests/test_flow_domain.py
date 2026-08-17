"""Cascade flow domain: pure bounds + case/blockMesh wiring."""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from impulsecalc.geometry import (
    BladeGeometry,
    BladeShapeParams,
    DEFAULT_X_DN_C,
    DEFAULT_X_UP_C,
    compute_domain_bounds,
    domain_bounds,
)
from impulsecalc.meanline import MeanlineInputs
from impulsecalc.openfoam_case import generate_openfoam_case, write_blockmesh


def test_default_domain_bounds_finite_and_ordered():
    c, pitch, n = 0.024, 0.024 / 1.4, 3
    b = compute_domain_bounds(c, pitch, n_blades=n)
    assert math.isfinite(b["x_min"]) and math.isfinite(b["x_max"])
    assert b["x_min"] < 0 <= c <= b["x_max"]
    assert b["x_min"] == pytest.approx(-DEFAULT_X_UP_C * c)
    assert b["x_max"] == pytest.approx(c + DEFAULT_X_DN_C * c)
    y_span = b["y_max"] - b["y_min"]
    assert y_span == pytest.approx(n * pitch, rel=1e-12)
    assert b["y_span_m"] == pytest.approx(y_span)
    assert b["inlet_length_m"] > 0 and b["outlet_length_m"] > 0


def test_larger_extents_grow_domain():
    c, pitch = 0.03, 0.02
    small = compute_domain_bounds(c, pitch, 3, x_up_c=0.3, x_dn_c=0.5)
    large = compute_domain_bounds(c, pitch, 3, x_up_c=1.5, x_dn_c=2.5)
    assert abs(large["x_min"]) > abs(small["x_min"])
    assert (large["x_max"] - c) > (small["x_max"] - c)
    assert large["axial_length_m"] > small["axial_length_m"]
    # y unchanged by axial extents
    assert large["y_span_m"] == pytest.approx(small["y_span_m"])


def test_domain_bounds_from_geom_matches_compute():
    geom = BladeGeometry(chord_m=0.024, solidity=1.4, beta1_deg=72, beta2_deg=-72)
    b1 = domain_bounds(geom, n_blades=4, x_up_c=0.8, x_dn_c=1.2)
    b2 = compute_domain_bounds(
        geom.chord_m, geom.resolved_pitch(), 4, x_up_c=0.8, x_dn_c=1.2
    )
    assert b1["x_min"] == pytest.approx(b2["x_min"])
    assert b1["x_max"] == pytest.approx(b2["x_max"])
    assert b1["y_span_m"] == pytest.approx(4 * geom.resolved_pitch())


def test_preview_domain_matches_blockmesh_and_has_inlet_spacing(tmp_path: Path):
    """Preview purple box must equal blockMesh vertices; include inlet flow + pitch."""
    from impulsecalc.geometry import blade_preview_payload

    geom = BladeGeometry(
        chord_m=0.01,
        solidity=1.13688,
        beta1_deg=72,
        beta2_deg=-72,
        thickness_ratio=0.5,
        shape=BladeShapeParams(thickness_ratio=0.5),
    )
    n = 3
    x_up, x_dn = 0.5, 1.0
    prev = blade_preview_payload(
        geom,
        n_blades=n,
        x_up_c=x_up,
        x_dn_c=x_dn,
        flow_beta1_deg=72.0,
        w1_m_s=950.0,
        p1_pa=5.5e5,
        t1_k=1100.0,
        mach_w1=1.4,
        n_blades_machine=25,
    )
    path = write_blockmesh(tmp_path, geom, n_blades=n, nx=40, ny=20, x_up_c=x_up, x_dn_c=x_dn)
    text = path.read_text(encoding="utf-8")
    b = prev["domain"]
    assert f"{b['x_min']}" in text
    assert f"{b['x_max']}" in text
    assert f"{b['y_min']}" in text
    assert f"{b['y_max']}" in text
    assert prev["mesh_parity"]["same_as_blockMesh"] is True
    assert prev["pitch_m"] == pytest.approx(geom.resolved_pitch())
    assert prev["solidity"] == pytest.approx(0.01 / 0.008796, rel=1e-4)
    assert prev["inlet_flow"]["w1_m_s"] == pytest.approx(950.0)
    assert prev["inlet_flow"]["Ux"] is not None
    assert abs(prev["inlet_flow"]["x_m"] - b["x_min"]) < 1e-12
    assert len(prev["spacing_pairs"]) == n - 1
    assert prev["spacing_pairs"][0]["pitch_m"] == pytest.approx(prev["pitch_m"])
    assert "working fluid" in (prev["inlet_flow"].get("note") or "").lower()


def test_blockmesh_vertices_reflect_extents(tmp_path: Path):
    geom = BladeGeometry(
        chord_m=0.024,
        solidity=1.4,
        beta1_deg=70,
        beta2_deg=-68,
        shape=BladeShapeParams(),
    )
    path = write_blockmesh(
        tmp_path, geom, n_blades=3, nx=40, ny=20, x_up_c=1.0, x_dn_c=2.0
    )
    text = path.read_text(encoding="utf-8")
    b = domain_bounds(geom, 3, x_up_c=1.0, x_dn_c=2.0)
    # first vertex is (x_min, y_min, z0)
    assert f"({b['x_min']}" in text or f"({b['x_min']:.}" in text
    assert str(b["x_min"]) in text or f"{b['x_min']}" in text
    assert f"{b['x_max']}" in text
    assert "inlet" in text and "outlet" in text and "cyclic" in text


def test_generate_case_writes_domain_meta(tmp_path: Path):
    res = generate_openfoam_case(
        MeanlineInputs(blade_name="dom_test", chord_m=0.024, solidity=1.4),
        tmp_path,
        case_name="dom_test",
        n_blades=3,
        nx=40,
        ny=20,
        x_up_c=0.75,
        x_dn_c=1.5,
    )
    assert res.success
    meta_path = Path(res.case_dir) / "impulsecalc_case_meta.json"
    assert meta_path.is_file()
    import json

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    dom = meta["geometry"]["domain"]
    assert dom["x_up_c"] == pytest.approx(0.75)
    assert dom["x_dn_c"] == pytest.approx(1.5)
    assert dom["x_min"] < 0
    assert dom["x_max"] > 0.024
    bm = Path(res.case_dir) / "system" / "blockMeshDict"
    txt = bm.read_text(encoding="utf-8")
    assert str(dom["x_min"]) in txt or f"{dom['x_min']}" in txt


def test_ui_exposes_domain_controls():
    body = (ROOT / "static" / "calcbody.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "calc.js").read_text(encoding="utf-8")
    assert 'name="x_up_c"' in body
    assert 'name="x_dn_c"' in body
    assert (
        "Flow domain" in body
        or "cascade tunnel" in body.lower()
        or "Cascade CFD domain" in body
        or "cascade cfd domain" in body.lower()
    )
    assert "getDomainExtents" in js
    assert "x_up_c" in js and "x_dn_c" in js
    assert "data.domain" in js or "domain" in js
