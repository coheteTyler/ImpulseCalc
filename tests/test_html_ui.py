"""Tests: Devenport-style HTML UI + Flask API."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATIC = ROOT / "static"


def test_devenport_html_structure():
    assert (STATIC / "calc.html").is_file()
    assert (STATIC / "calcbody.html").is_file()
    assert (STATIC / "scratch.html").is_file()
    assert (STATIC / "calc.js").is_file()
    assert (STATIC / "winback.gif").is_file()

    frames = (STATIC / "calc.html").read_text(encoding="utf-8")
    assert "frameset" in frames.lower()
    assert "calcbody.html" in frames
    assert "scratch.html" in frames

    body = (STATIC / "calcbody.html").read_text(encoding="utf-8")
    assert 'background' in body.lower() or "winback.gif" in body
    assert "Times New Roman" in body
    assert "TABLE" in body.upper() or "<table" in body.lower()
    assert 'border' in body.lower()
    assert "Update triangles" in body or "Calculate" in body
    assert "velocity triangles" in body.lower() or "Mean-Line" in body
    assert "Blade metal shape" in body or "fillet" in body.lower()
    assert "cascade CFD" in body.lower() or "OpenFOAM" in body
    assert "flow video" in body.lower() or "Technical Flow Video" in body
    assert "devenport.aoe.vt.edu" in body.lower()

    scratch = (STATIC / "scratch.html").read_text(encoding="utf-8")
    assert "Scratch pad" in scratch
    assert "Evaluate" in scratch
    assert "winback.gif" in scratch

    js = (STATIC / "calc.js").read_text(encoding="utf-8")
    assert "calcMeanline" in js
    assert "generateCase" in js
    assert "genVideo" in js
    assert "bladeShapeFromForm" in js
    assert "runFullTest" in js
    assert "propagateFromSection1" in js
    assert "propagateFromSection2" in js
    assert "propagateCaseDir" in js
    assert "pipeline_banner" in body or "From §1" in body
    assert "loadDesignReport" in js or "loadSurfaceAnalysis" in js
    assert "exportDesignPackage" in js
    assert "metrics_grid" in body
    assert "stations_table" in body
    assert "design board" in body.lower() or "CFD design" in body
    assert "ranked" in body.lower() or "Loss map" in body
    assert "shock_table" in body
    assert "Hill" in body or "§3.7" in body or "3.7" in body
    assert "fillShockRelationsTable" in js
    assert "suggestion_list" in body or "fix_box" in body
    assert "fix-panel" in body and "fix-card" in body
    # Cascade preview engineering: pitch/spacing control + inlet + mesh parity
    assert 'name="pitch"' in body
    assert "pitch_sc" in body or "Blade packing" in body
    assert "blade_eng_readout" in body
    assert "onPitchChange" in js
    assert "fillBladeEngReadout" in js
    assert "working fluid" in body.lower() or "INLET" in js
    assert "applySelectedFixesAndReanalyze" in js or "applyIndustryPatchesAndRerun" in js
    assert "Lieblein" in body or "industry" in body.lower() or "Design fixes" in body
    # Applied cards must collapse — no sticky min-height dead space
    assert "min-height: 0 !important" in body or "min-height:0 !important" in body.replace(" ", "")
    assert "_paintFixStatusBar" in js
    assert "fix-card-leaving" in body or "fix-empty" in body
    assert "unit_system" in body or "Metric" in body
    assert "setUnitSystem" in js
    # CFD fidelity mode (top bar): fast vs high-accuracy
    assert 'id="fidelity_bar"' in body or "fidelity_mode" in body
    assert "onFidelityModeChange" in js
    assert "High accuracy" in body or "fidelity_level" in body
    # Long mesh/solve must use background jobs (avoids Failed to fetch)
    assert "apiJob" in js
    assert "/api/job/" in js
    # Progress UI so multi-minute jobs do not look stuck
    assert "long_job_panel" in body
    assert "formatDuration" in js
    assert "updateLongJobPanel" in js
    assert "estimateJobSeconds" in js
    # User stage table is the board default
    assert "user_stage_r040" in body or "0.0375" in body
    assert "loadAndApplyDefaultDesign" in js or "default_design" in js
    assert 'value="0.0375"' in body
    assert "0.008796" in body
    assert "fromDisplay" in js and "toDisplay" in js


def test_no_streamlit_required_for_ui():
    """Primary UI must not be Streamlit."""
    body = (STATIC / "calcbody.html").read_text(encoding="utf-8")
    assert "streamlit" not in body.lower()
    # server entry is server.py
    assert (ROOT / "server.py").is_file()
    srv = (ROOT / "server.py").read_text(encoding="utf-8")
    assert "Flask" in srv or "flask" in srv
    assert "streamlit" not in srv.lower()


def test_flask_api_generate_case():
    pytest.importorskip("flask")
    from server import app

    client = app.test_client()
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    r = client.get("/calc.html")
    assert r.status_code == 200
    assert b"frameset" in r.data.lower() or b"Impulse" in r.data

    r = client.get("/calcbody.html")
    assert r.status_code == 200
    assert b"winback.gif" in r.data or b"Times New Roman" in r.data
    assert b"fillet" in r.data.lower() or b"Blade" in r.data

    payload = {
        "meanline": {
            "beta1_deg": 72.0,
            "blade_speed_u_m_s": 450.0,
            "w1_m_s": 950.0,
            "p1_pa": 5.5e5,
            "t1_k": 1100.0,
            "gamma": 1.3,
            "r_specific_j_kg_k": 320.0,
            "blade_name": "html_test",
            "pure_impulse_lock": True,
        },
        "blade_shape": {
            "thickness_ratio": 0.14,
            "le_fillet_r_c": 0.03,
            "te_fillet_r_c": 0.01,
            "inlet_line_frac": 0.2,
            "outlet_line_frac": 0.15,
            "arc_bulge": 1.0,
        },
        "output_dir": str(ROOT / "output" / "html_test"),
        "n_blades": 3,
        "nx": 40,
        "ny": 20,
    }
    r = client.post("/api/generate_case", json=payload)
    assert r.status_code == 200
    data = r.get_json()
    assert data["success"] is True
    assert Path(data["case_dir"]).is_dir()
    assert (Path(data["case_dir"]) / "0" / "U").is_file()

    r = client.post(
        "/api/blade_preview",
        json={
            "meanline": payload["meanline"],
            "blade_shape": payload["blade_shape"],
            "n_blades": 1,
        },
    )
    assert r.status_code == 200
    prev = r.get_json()
    assert prev["success"] is True
    assert len(prev["profile"]) > 10
    assert len(prev["meanline"]) > 5


def test_api_video_script_only(tmp_path: Path):
    pytest.importorskip("flask")
    from server import app

    cdir = tmp_path / "case"
    (cdir / "1e-06").mkdir(parents=True)
    (cdir / "1e-06" / "U").write_text("x\n", encoding="utf-8")
    client = app.test_client()
    r = client.post(
        "/api/video",
        json={
            "case_dir": str(cdir),
            "fields": ["Mach"],
            "resolution": "720p",
            "run_pvbatch": False,
        },
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] in ("script_only", "needs_pvbatch", "success", "no_timesteps")
    assert data.get("script_path")
    assert Path(data["script_path"]).is_file()
