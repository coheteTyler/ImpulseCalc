"""Tests: CFD fidelity mode (fast vs high-accuracy) — shipped mapping + case write."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from impulsecalc.fidelity import (
    FIDELITY_ACCURATE,
    FIDELITY_FAST,
    compare_fidelity,
    fidelity_from_request,
    recommended_end_time,
    resolve_fidelity,
)
from impulsecalc.meanline import MeanlineInputs
from impulsecalc.openfoam_case import generate_openfoam_case
from impulsecalc.runners import timeouts_from_case_meta


def test_accurate_has_strictly_higher_resolution_than_fast():
    fast = resolve_fidelity(FIDELITY_FAST)
    acc = resolve_fidelity(FIDELITY_ACCURATE)
    cmp_ = compare_fidelity(fast, acc)
    assert cmp_["nx_higher"], (fast.nx, acc.nx)
    assert cmp_["ny_higher"], (fast.ny, acc.ny)
    assert cmp_["end_mult_higher"], (fast.end_time_transit_mult, acc.end_time_transit_mult)
    assert cmp_["max_co_tighter_or_eq"], (fast.max_co, acc.max_co)
    assert acc.nx >= 2 * fast.nx
    assert acc.ny >= 2 * fast.ny


def test_accurate_has_unlimited_or_much_longer_solve_budget():
    fast = resolve_fidelity("fast")
    acc = resolve_fidelity("accurate")
    assert fast.solve_timeout_s is not None
    assert fast.solve_timeout_s <= 7200.0
    # High accuracy: no short wall-clock abort (None = unlimited)
    assert acc.solve_timeout_s is None
    assert acc.mesh_timeout_s is None
    assert acc.end_time_cap_s is None


def test_fast_defaults_match_legacy_board_numbers():
    """Regression: fast mode must keep design-board nx/ny (timeouts sized for WSL)."""
    f = resolve_fidelity("fast")
    assert f.nx == 80
    assert f.ny == 40
    assert f.mesh_timeout_s == 1200.0
    assert f.solve_timeout_s == 5400.0
    assert f.end_time_cap_s == pytest.approx(3.0e-3)
    assert f.max_co == pytest.approx(0.10)
    assert f.mesh_path == "stair_step"
    assert f.turbulence_model == "laminar"
    assert f.wall_bc == "slip"


def test_slider_interpolates_between_presets():
    lo = resolve_fidelity(level=0)
    mid = resolve_fidelity(level=50)
    hi = resolve_fidelity(level=100)
    assert lo.nx < mid.nx < hi.nx
    assert lo.ny < mid.ny < hi.ny
    assert mid.solve_timeout_s is not None
    assert hi.solve_timeout_s is None


def test_fidelity_from_request_nested_and_flat():
    a = fidelity_from_request({"fidelity": {"mode": "accurate", "level": 100}})
    assert a.mode == FIDELITY_ACCURATE
    assert a.solve_timeout_s is None
    b = fidelity_from_request({"fidelity_mode": "fast", "fidelity_level": 0})
    assert b.nx == 80
    c = fidelity_from_request({"fidelity_level": 90})
    assert c.level == 90
    assert c.nx > 160


def test_recommended_end_time_accurate_longer_than_fast():
    chord, w1 = 0.01, 300.0
    t_fast = recommended_end_time(chord, w1, resolve_fidelity("fast"))
    t_acc = recommended_end_time(chord, w1, resolve_fidelity("accurate"))
    assert t_acc["end_time"] > t_fast["end_time"]
    assert t_acc["end_time"] >= resolve_fidelity("accurate").end_time_floor_s


def test_generate_case_writes_accurate_params(tmp_path: Path):
    """Real generate_openfoam_case path: controlDict + meta honor high-accuracy."""
    inp = MeanlineInputs(
        beta1_deg=-36.0,
        pure_impulse_lock=True,
        w1_m_s=400.0,
        blade_speed_u_m_s=120.0,
        p1_pa=2e5,
        t1_k=320.0,
        chord_m=0.01,
        solidity=1.5,
        blade_name="fid_acc_test",
    )
    fid = resolve_fidelity("accurate")
    res = generate_openfoam_case(
        inp,
        tmp_path,
        case_name="fid_acc_test",
        n_blades=3,
        # omit nx/ny → fidelity supplies them
        nx=None,
        ny=None,
        end_time=None,
        startup=False,
        fidelity=fid,
    )
    assert res.success, res.message
    cdir = Path(res.case_dir)
    cd = (cdir / "system" / "controlDict").read_text(encoding="utf-8")
    assert re.search(r"maxCo\s+0\.0[0-3]", cd), cd
    et_m = re.search(r"endTime\s+([0-9.eE+-]+)", cd)
    assert et_m, cd
    assert float(et_m.group(1)) >= fid.end_time_floor_s

    meta = json.loads((cdir / "impulsecalc_case_meta.json").read_text(encoding="utf-8"))
    assert meta["geometry"]["nx"] == fid.nx
    assert meta["geometry"]["ny"] == fid.ny
    assert meta["fidelity"]["mode"] == FIDELITY_ACCURATE
    assert meta["runner_timeouts"]["solve_timeout_s"] is None
    assert meta["runner_timeouts"]["mesh_timeout_s"] is None

    bm = (cdir / "system" / "blockMeshDict").read_text(encoding="utf-8")
    # hex block has nx ny in simpleGrading line region — check integers present
    assert str(fid.nx) in bm or f"({fid.nx}" in bm.replace(" ", "")
    # sample denser
    sample = (cdir / "system" / "sample").read_text(encoding="utf-8")
    assert f"nPoints {fid.sample_n_points}" in sample

    # Runner helper reads unlimited budgets
    to = timeouts_from_case_meta(cdir)
    assert to["solve_timeout_s"] is None
    assert to["mesh_timeout_s"] is None


def test_generate_case_accepts_end_time_auto_string(tmp_path: Path):
    """UI sends end_time: 'auto' — must not raise float('auto')."""
    inp = MeanlineInputs(
        beta1_deg=50.0,
        pure_impulse_lock=True,
        w1_m_s=300.0,
        blade_speed_u_m_s=200.0,
        p1_pa=5.5e5,
        t1_k=550.0,
        chord_m=0.01,
        solidity=1.7,
        blade_name="auto_et_test",
    )
    res = generate_openfoam_case(
        inp,
        tmp_path,
        case_name="auto_et_test",
        fidelity="fast",
        end_time="auto",  # string from JSON body
        nx=40,
        ny=20,
    )
    assert res.success, res.message
    cd = (Path(res.case_dir) / "system" / "controlDict").read_text(encoding="utf-8")
    assert "endTime" in cd
    # Parsed a real number, not the word auto
    import re

    m = re.search(r"endTime\s+([0-9.eE+-]+)", cd)
    assert m is not None
    assert float(m.group(1)) > 0


def test_generate_case_fast_matches_legacy_defaults(tmp_path: Path):
    inp = MeanlineInputs(
        beta1_deg=50.0,
        pure_impulse_lock=True,
        w1_m_s=300.0,
        blade_speed_u_m_s=200.0,
        p1_pa=5.5e5,
        t1_k=550.0,
        chord_m=0.01,
        solidity=1.7,
        blade_name="fid_fast_test",
    )
    res = generate_openfoam_case(
        inp,
        tmp_path,
        case_name="fid_fast_test",
        fidelity="fast",
        nx=None,
        ny=None,
    )
    assert res.success
    meta = json.loads(
        (Path(res.case_dir) / "impulsecalc_case_meta.json").read_text(encoding="utf-8")
    )
    assert meta["geometry"]["nx"] == 80
    assert meta["geometry"]["ny"] == 40
    assert meta["runner_timeouts"]["solve_timeout_s"] == 5400.0
    assert meta["fidelity"]["mode"] == FIDELITY_FAST


def test_ui_fidelity_control_present_in_html_and_js():
    body = (ROOT / "static" / "calcbody.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "calc.js").read_text(encoding="utf-8")
    assert 'id="fidelity_bar"' in body
    assert 'name="fidelity_mode"' in body
    assert 'id="fidelity_level"' in body
    assert "High accuracy" in body or "high accuracy" in body.lower()
    assert "Fast (design board)" in body
    assert "onFidelityModeChange" in js
    assert "onFidelitySlider" in js
    assert "fidelity_mode" in js
    assert "fidelity_level" in js
    assert "getFidelitySettings" in js
    # Payload wiring into case build
    assert "fidelity_mode" in js and "generateCase" in js
    # generateCase body includes fidelity
    assert re.search(r"fidelity_mode:\s*fid\.mode|fidelity:\s*\{\s*mode:", js)


def test_server_generate_case_accepts_fidelity(tmp_path: Path, monkeypatch):
    """Exercise Flask /api/generate_case with accurate fidelity (real handler)."""
    # Import app without binding port
    sys.path.insert(0, str(ROOT))
    import server as srv

    client = srv.app.test_client()
    payload = {
        "meanline": {
            "beta1_deg": -36,
            "pure_impulse_lock": True,
            "w1_m_s": 350,
            "blade_speed_u_m_s": 100,
            "p1_pa": 2e5,
            "t1_k": 320,
            "chord_m": 0.0095,
            "solidity": 1.6,
            "blade_name": "api_fid_acc",
        },
        "output_dir": str(tmp_path),
        "n_blades": 3,
        "fidelity_mode": "accurate",
        "fidelity_level": 100,
        "end_time": "auto",
        "startup": False,
    }
    r = client.post("/api/generate_case", json=payload)
    assert r.status_code == 200, r.get_data(as_text=True)
    data = r.get_json()
    assert data.get("success") is True
    assert data.get("fidelity", {}).get("mode") == "accurate"
    assert data["fidelity"]["solve_timeout_s"] is None
    meta_path = Path(data["case_dir"]) / "impulsecalc_case_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["geometry"]["nx"] >= 300
    assert meta["runner_timeouts"]["solve_timeout_s"] is None

    # presets endpoint
    pr = client.get("/api/fidelity_presets")
    assert pr.status_code == 200
    presets = pr.get_json()["presets"]
    assert presets["accurate"]["nx"] > presets["fast"]["nx"]
