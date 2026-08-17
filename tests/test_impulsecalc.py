"""Backend package tests (no Streamlit)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from impulsecalc.meanline import MeanlineInputs, compute_meanline
from impulsecalc.openfoam_case import generate_openfoam_case
from impulsecalc.technical_video import VideoOptions, write_video_artifacts
from impulsecalc.postprocess import synthetic_surface_pressure
from impulsecalc.loss_analysis import analyze_losses
from impulsecalc.cascade_job import run_cascade_job


def test_no_marlin_imports():
    ban = re.compile(r"^\s*(from|import)\s+marlin", re.M)
    for path in ROOT.rglob("*.py"):
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        assert not ban.search(path.read_text(encoding="utf-8")), path


def test_meanline_pure_impulse():
    r = compute_meanline(MeanlineInputs(beta1_deg=72.0, pure_impulse_lock=True, w1_m_s=950.0))
    assert r.beta2_deg == pytest.approx(-72.0)
    assert abs(r.degree_of_reaction) <= 0.05
    assert r.mach_w1 > 1.0


def test_loss_analysis_finds_ss_features():
    surf = synthetic_surface_pressure(p1_pa=5.5e5, mach_w1=1.6, n=50)
    rep = analyze_losses(surf, beta1_deg=72, beta2_deg=-72, mach_w1=1.6, thickness_ratio=0.22)
    assert rep.summary
    assert rep.losses
    assert rep.peak_ss_x_c >= 0.0
    d = rep.to_dict()
    assert "ranked_fixes" in d
    assert "shock_candidates" in d


def test_openfoam_case_stable_shockfluid_settings(tmp_path: Path):
    """shockFluid FPE (sqrt of T<0) is avoided by Minmod + low Co + fixed outlet p."""
    res = generate_openfoam_case(
        MeanlineInputs(blade_name="stable_of", w1_m_s=950.0, pure_impulse_lock=True),
        tmp_path,
        case_name="stable_of",
        n_blades=3,
        nx=40,
        ny=20,
    )
    cdir = Path(res.case_dir)
    schemes = (cdir / "system" / "fvSchemes").read_text(encoding="utf-8")
    control = (cdir / "system" / "controlDict").read_text(encoding="utf-8")
    p0 = (cdir / "0" / "p").read_text(encoding="utf-8")
    assert "Minmod" in schemes
    assert "vanLeer" not in schemes  # vanLeer overshoots → negative T mid-run
    # maxCo is Mach-aware (≤0.10 subsonic, tighter when Mw1>1)
    assert "maxCo" in control
    assert "fixedValue" in p0
    assert "waveTransmissive" not in p0
    # outlet is fixedValue (not waveTransmissive); pure-impulse uses mild ~0.95 p1 dump
    assert "outlet" in p0

    # time-dir cleaner removes t>0, keeps 0/
    tdir = cdir / "0.0001"
    tdir.mkdir()
    (tdir / "U").write_text("x\n", encoding="utf-8")
    from impulsecalc.openfoam_case import clean_case_time_dirs

    removed = clean_case_time_dirs(cdir, keep_zero=True)
    assert "0.0001" in removed
    assert (cdir / "0").is_dir()
    assert not tdir.exists()


def test_startup_ics_quiescent_interior_inlet_drive(tmp_path: Path):
    """Startup case: U=0 inside, design W at inlet — video can show no-flow → steady."""
    from impulsecalc.openfoam_case import recommended_startup_timing

    res = generate_openfoam_case(
        MeanlineInputs(
            blade_name="startup_of",
            w1_m_s=950.0,
            pure_impulse_lock=True,
            chord_m=0.01,
            beta1_deg=72.0,
        ),
        tmp_path,
        case_name="startup_of",
        n_blades=3,
        nx=40,
        ny=20,
        startup=True,
    )
    cdir = Path(res.case_dir)
    u0 = (cdir / "0" / "U").read_text(encoding="utf-8")
    p0 = (cdir / "0" / "p").read_text(encoding="utf-8")
    # Interior quiescent
    assert "internalField uniform (0 0 0)" in u0
    # Inlet still at design relative velocity (Wx = 950*cos72 ≈ 293.6)
    assert "inlet" in u0 and "fixedValue" in u0
    assert "293.566" in u0 or "903.504" in u0
    # Marker file
    assert (cdir / "0" / "impulsecalc_startup").read_text(encoding="utf-8").strip() == "startup"
    assert "startup_ics" in res.notes
    # Timing captures multiple dumps
    control = (cdir / "system" / "controlDict").read_text(encoding="utf-8")
    assert "endTime" in control
    timing = recommended_startup_timing(0.01, 950.0)
    assert timing["end_time"] >= 6e-4
    assert timing["n_writes_est"] >= 20
    # Free-stream mode still available
    res2 = generate_openfoam_case(
        MeanlineInputs(blade_name="fs_of", w1_m_s=900.0, pure_impulse_lock=True),
        tmp_path,
        case_name="fs_of",
        startup=False,
    )
    u_fs = Path(res2.case_dir, "0", "U").read_text(encoding="utf-8")
    assert "internalField uniform (0 0 0)" not in u_fs


def test_cascade_job_builds_case_and_report(tmp_path: Path):
    job = {
        "blade_name": "job_test",
        "n_blades": 3,
        "nx": 40,
        "ny": 20,
        "output_dir": str(tmp_path),
        "run_mesh": False,
        "run_solve": False,
        "run_sample": False,
        "beta1_deg": 72.0,
        "beta2_deg": -72.0,
        "pure_impulse_lock": True,
        "w1_m_s": 950.0,
        "p1_pa": 5.5e5,
        "t1_k": 1100.0,
        "gamma": 1.3,
        "r_specific_j_kg_k": 320.0,
        "mu_pa_s": 4.5e-5,
        "chord_m": 0.024,
        "solidity": 1.4,
        "blade_shape": {
            "thickness_ratio": 0.22,
            "thickness_peak_x": 0.5,
            "arc_bulge": 1.2,
            "inlet_line_frac": 0.0,
            "outlet_line_frac": 0.0,
        },
    }
    res = run_cascade_job(job)
    assert res.success
    assert res.case_dir and Path(res.case_dir).is_dir()
    assert res.surface is not None
    assert res.loss_report is not None
    assert res.surface_csv and Path(res.surface_csv).is_file()
    assert res.loss_json and Path(res.loss_json).is_file()
    assert "Mw1" in res.message or "loading" in res.message.lower() or res.message


def test_inlet_straight_lengthens_without_s_kink():
    """Inlet line frac must extend LE metal at β1 without overshooting into an S."""
    import math

    from impulsecalc.geometry import meanline_lines_arc

    c = 0.024
    for fin in (0.0, 0.1, 0.18, 0.3, 0.4):
        ml = meanline_lines_arc(c, 72.0, -72.0, fin, 0.18, 1.0, 80)
        assert math.hypot(ml[-1][0] - ml[0][0], ml[-1][1] - ml[0][1]) == pytest.approx(c, rel=1e-6)
        # LE and TE on chord after stagger=0: TE y ≈ 0
        assert abs(ml[-1][1] - ml[0][1]) < 1e-9
        if fin >= 0.05:
            # first segment at inlet metal angle
            a0 = math.degrees(math.atan2(ml[2][1] - ml[0][1], ml[2][0] - ml[0][0]))
            assert a0 == pytest.approx(72.0, abs=1.5)
        # single camber hump: y rises then falls (no mid drop = no S)
        ys = [p[1] for p in ml]
        imax = max(range(len(ys)), key=lambda i: ys[i])
        assert all(ys[i] <= ys[i + 1] + 1e-9 for i in range(imax))
        assert all(ys[i] >= ys[i + 1] - 1e-9 for i in range(imax, len(ys) - 1))


def test_openfoam_case(tmp_path: Path):
    res = generate_openfoam_case(MeanlineInputs(blade_name="t"), tmp_path, case_name="t", nx=40, ny=20)
    assert res.success
    assert (Path(res.case_dir) / "system" / "controlDict").is_file()


def test_video_artifacts(tmp_path: Path):
    cdir = tmp_path / "c"
    (cdir / "2e-06").mkdir(parents=True)
    (cdir / "2e-06" / "U").write_text("x\n", encoding="utf-8")
    (cdir / "constant" / "triSurface").mkdir(parents=True)
    (cdir / "constant" / "triSurface" / "blades.stl").write_text(
        "solid b\nendsolid b\n", encoding="utf-8"
    )
    arts = write_video_artifacts(
        cdir,
        VideoOptions(
            fields=["Mach", "streamlines", "U_vectors"],
            view_preset="blade_passage_shocks",
        ),
    )
    assert Path(arts["script_path"]).is_file()
    text = Path(arts["script_path"]).read_text(encoding="utf-8")
    assert "OpenFOAMReader" in text
    assert "AnnotateTime" in text or "AnnotateTimeFilter" in text
    assert "StreamTracer" in text
    assert "OpenDataFile" in text or "FileNames" in text
    assert "SeedType" in text and "Line" in text
    assert "High Resolution Line Source" not in text
    assert "Slice(" in text and "primary display=Slice" in text
