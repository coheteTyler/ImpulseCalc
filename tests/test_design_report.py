"""Tests for dense CFD design report (shipped paths, no theater)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from impulsecalc.cascade_job import run_cascade_job
from impulsecalc.design_report import (
    build_design_report,
    compare_design_metrics,
    compute_metrics,
)
from impulsecalc.meanline import MeanlineInputs, compute_meanline
from impulsecalc.postprocess import synthetic_surface_pressure


def test_synthetic_cp_changes_with_blade_shape():
    """Re-analyze must move numbers when §2 knobs change (not only Mach)."""
    from impulsecalc.postprocess import synthetic_surface_pressure

    base = synthetic_surface_pressure(
        p1_pa=5.5e5, mach_w1=1.4, n=50,
        thickness_ratio=0.22, arc_bulge=1.2, thickness_peak_x=0.4,
        inlet_line_frac=0.0, beta1_deg=72, beta2_deg=-72,
    )
    soft = synthetic_surface_pressure(
        p1_pa=5.5e5, mach_w1=1.4, n=50,
        thickness_ratio=0.12, arc_bulge=0.9, thickness_peak_x=0.55,
        inlet_line_frac=0.12, beta1_deg=72, beta2_deg=-72,
    )
    # Peak SS Cp or location must differ when shape softens / aft-loads
    assert abs(min(base.cp_ss) - min(soft.cp_ss)) > 0.02 or abs(
        base.cp_ss.index(min(base.cp_ss)) - soft.cp_ss.index(min(soft.cp_ss))
    ) >= 1
    rep_b = build_design_report(base, mach_w1=1.4, gamma=1.3, write_exports=False, include_plots=False,
                                blade_shape={"thickness_ratio": 0.22, "arc_bulge": 1.2})
    rep_s = build_design_report(soft, mach_w1=1.4, gamma=1.3, write_exports=False, include_plots=False,
                                blade_shape={"thickness_ratio": 0.12, "arc_bulge": 0.9})
    assert rep_b.metrics.peak_ss_cp != pytest.approx(rep_s.metrics.peak_ss_cp, abs=1e-4) or (
        rep_b.metrics.lieblein_df_ss != pytest.approx(rep_s.metrics.lieblein_df_ss, abs=1e-4)
    )


def test_force_synthetic_ignores_stale_of_samples(tmp_path: Path):
    from impulsecalc.postprocess import load_surface_pressure, write_surface_csv, synthetic_surface_pressure

    cdir = tmp_path / "case"
    pp = cdir / "postProcessing" / "sample" / "0"
    pp.mkdir(parents=True)
    # Fake OF-like samples that would dominate if not force_synthetic
    old = synthetic_surface_pressure(p1_pa=5.5e5, mach_w1=2.5, n=20, arc_bulge=1.8, thickness_ratio=0.28)
    # write as pressureSide/suctionSide csvs
    (pp / "pressureSide.csv").write_text(
        "\n".join(f"{x},{p}" for x, p in zip(old.x_c_ps, old.p_ps)), encoding="utf-8"
    )
    (pp / "suctionSide.csv").write_text(
        "\n".join(f"{x},{p}" for x, p in zip(old.x_c_ss, old.p_ss)), encoding="utf-8"
    )
    forced = load_surface_pressure(
        cdir, p1_pa=5.5e5, rho1=1.5, w1_m_s=950, force_synthetic=True,
        mach_w1=1.2, blade_shape={"arc_bulge": 0.9, "thickness_ratio": 0.12},
        beta1_deg=60, beta2_deg=-60,
    )
    assert forced.source.startswith("synthetic")
    # Should match soft synthetic, not the M=2.5 fake OF file
    ref = synthetic_surface_pressure(
        p1_pa=5.5e5, mach_w1=1.2, n=40, arc_bulge=0.9, thickness_ratio=0.12,
        beta1_deg=60, beta2_deg=-60,
    )
    assert abs(min(forced.cp_ss) - min(ref.cp_ss)) < 0.15


def test_empty_case_dir_synthetic_uses_meanline_mach(tmp_path: Path):
    """case_dir after §3 without OF samples must use ml.mach_w1, not W1/500."""
    from impulsecalc.postprocess import load_surface_pressure

    ml = compute_meanline(
        MeanlineInputs(w1_m_s=950.0, p1_pa=5.5e5, t1_k=1100.0, gamma=1.3, r_specific_j_kg_k=320.0)
    )
    # Empty case dir: no postProcessing PS/SS samples
    cdir = tmp_path / "empty_case"
    cdir.mkdir()
    (cdir / "system").mkdir()

    # Wrong path (the old bug): W1/500 ≈ 1.9
    wrong_mw = ml.inputs.w1_m_s / 500.0
    assert abs(wrong_mw - ml.mach_w1) > 0.2

    surf = load_surface_pressure(
        cdir,
        p1_pa=ml.inputs.p1_pa,
        rho1=ml.rho1_kg_m3,
        w1_m_s=ml.inputs.w1_m_s,
        chord_m=ml.inputs.chord_m,
        allow_synthetic=True,
        mach_w1=ml.mach_w1,
        gamma=ml.inputs.gamma,
        t1_k=ml.inputs.t1_k,
        r_specific=ml.inputs.r_specific_j_kg_k,
    )
    assert surf.source == "synthetic_educational"
    ref = synthetic_surface_pressure(p1_pa=ml.inputs.p1_pa, mach_w1=ml.mach_w1, n=len(surf.cp_ss))
    # Cp series must match correct-Mach synthetic, not W1/500 synthetic
    wrong = synthetic_surface_pressure(p1_pa=ml.inputs.p1_pa, mach_w1=wrong_mw, n=len(surf.cp_ss))
    assert surf.cp_ss[len(surf.cp_ss) // 3] == pytest.approx(
        ref.cp_ss[len(ref.cp_ss) // 3], rel=1e-9
    )
    assert abs(surf.cp_ss[len(surf.cp_ss) // 3] - wrong.cp_ss[len(wrong.cp_ss) // 3]) > 1e-4

    # Also when mach_w1 omitted: derive from γ,R,T (not W1/500)
    surf2 = load_surface_pressure(
        cdir,
        p1_pa=ml.inputs.p1_pa,
        rho1=ml.rho1_kg_m3,
        w1_m_s=ml.inputs.w1_m_s,
        allow_synthetic=True,
        gamma=ml.inputs.gamma,
        t1_k=ml.inputs.t1_k,
        r_specific=ml.inputs.r_specific_j_kg_k,
    )
    assert surf2.cp_ss[10] == pytest.approx(ref.cp_ss[10], rel=1e-6, abs=1e-5)


def test_loss_and_metrics_peak_ps_cp_agree():
    """PS peak in LossReport is max Cp (same as design_report metrics)."""
    from impulsecalc.loss_analysis import analyze_losses

    surf = synthetic_surface_pressure(p1_pa=5.5e5, mach_w1=1.4, n=50)
    loss = analyze_losses(surf, beta1_deg=72, beta2_deg=-72, mach_w1=1.4)
    m = compute_metrics(surf, mach_w1=1.4, beta1_deg=72, beta2_deg=-72, loss=loss)
    assert loss.peak_ps_cp == pytest.approx(m.peak_ps_cp, abs=1e-9)
    assert loss.peak_ps_cp == pytest.approx(max(surf.cp_ps), abs=1e-9)
    assert loss.peak_ss_cp == pytest.approx(m.peak_ss_cp, abs=1e-9)
    assert loss.peak_ss_cp == pytest.approx(min(surf.cp_ss), abs=1e-9)


def test_synthetic_high_mach_yields_dense_metrics_and_fixes():
    ml = compute_meanline(MeanlineInputs(w1_m_s=950.0, p1_pa=5.5e5, pure_impulse_lock=True))
    surf = synthetic_surface_pressure(p1_pa=ml.inputs.p1_pa, mach_w1=ml.mach_w1, n=60)
    assert len(surf.x_c_ps) >= 20 and len(surf.x_c_ss) >= 20

    rep = build_design_report(
        surf,
        ml=ml,
        solidity=ml.inputs.solidity,
        gamma=ml.inputs.gamma,
        write_exports=False,
        include_plots=False,
    )
    assert rep.success
    m = rep.metrics
    assert m.n_ps == len(surf.cp_ps)
    assert m.n_ss == len(surf.cp_ss)
    assert isinstance(m.peak_ss_cp, float)
    assert 0.0 <= m.peak_ss_x_c <= 1.0
    assert isinstance(m.loading_int_dcp, float)
    assert isinstance(m.diffusion_ss, float)
    assert m.mach_w1 == pytest.approx(ml.mach_w1, rel=1e-3)
    # High-Mw1 synthetic includes shock-like jump → expect shock or over-expansion loss
    assert rep.loss_report.losses
    assert any(L.location and L.mechanism and L.fix for L in rep.loss_report.losses)
    assert rep.ranked_fixes
    assert "severity" in rep.loss_report.losses[0].to_dict()
    assert len(rep.stations) >= 10
    assert rep.surface_table
    assert rep.summary
    assert m.eta_design_proxy <= m.eta_meanline_proxy + 1e-9


def test_metrics_change_when_mach_changes():
    """Two designs through the same shipped path must produce comparable, different boards."""
    high = synthetic_surface_pressure(p1_pa=5.5e5, mach_w1=1.7, n=50)
    low = synthetic_surface_pressure(p1_pa=5.5e5, mach_w1=0.8, n=50)
    mh = compute_metrics(high, mach_w1=1.7, beta1_deg=72, beta2_deg=-72)
    ml_ = compute_metrics(low, mach_w1=0.8, beta1_deg=72, beta2_deg=-72)
    assert mh.mach_w1 != ml_.mach_w1
    # High-Mach synthetic is more aggressive on SS suction
    assert mh.peak_ss_cp <= ml_.peak_ss_cp + 0.05
    diff = compare_design_metrics(ml_.to_dict(), mh.to_dict())
    assert "deltas" in diff
    assert "mach_w1" in diff["deltas"]
    assert diff["deltas"]["mach_w1"]["delta_b_minus_a"] == pytest.approx(0.9, abs=1e-6)


def test_design_report_writes_package(tmp_path: Path):
    ml = compute_meanline(MeanlineInputs(blade_name="dr_write"))
    # minimal fake case dir structure
    cdir = tmp_path / "case"
    (cdir / "system").mkdir(parents=True)
    surf = synthetic_surface_pressure(p1_pa=ml.inputs.p1_pa, mach_w1=ml.mach_w1)
    rep = build_design_report(
        surf, ml=ml, case_dir=cdir, write_exports=True, include_plots=False
    )
    assert rep.exports.get("surface_csv")
    assert Path(rep.exports["surface_csv"]).is_file()
    assert Path(rep.exports["loss_json"]).is_file()
    assert Path(rep.exports["metrics_json"]).is_file()
    assert Path(rep.exports["design_package_json"]).is_file()
    pkg = json.loads(Path(rep.exports["design_package_json"]).read_text(encoding="utf-8"))
    assert pkg["format"] in (
        "impulsecalc_design_package_v3",
        "impulsecalc_design_report_v2",
    )
    assert "metrics" in pkg and "loss_report" in pkg and "stations" in pkg
    if pkg["format"] == "impulsecalc_design_package_v3":
        assert pkg.get("schema_version") == 3
        assert "meanline_inputs" in pkg
        assert rep.exports.get("comparison_csv")


def test_flask_design_report_json_not_html():
    pytest.importorskip("flask")
    from server import app

    client = app.test_client()
    h = client.get("/api/health")
    assert h.status_code == 200
    hj = h.get_json()
    assert hj["ok"] is True
    assert hj.get("version")
    assert hj.get("has_design_report") is True
    assert hj.get("has_gasdynamics") is True
    assert any("design_report" in r for r in hj.get("api_routes", []))

    r = client.post(
        "/api/design_report",
        json={
            "w1_m_s": 950.0,
            "p1_pa": 5.5e5,
            "beta1_deg": 72.0,
            "beta2_deg": -72.0,
            "include_plots": False,
            "meanline": {
                "beta1_deg": 72.0,
                "pure_impulse_lock": True,
                "w1_m_s": 950.0,
                "p1_pa": 5.5e5,
                "t1_k": 1100.0,
                "gamma": 1.3,
                "r_specific_j_kg_k": 320.0,
            },
            "blade_shape": {"thickness_ratio": 0.22, "arc_bulge": 1.2},
        },
    )
    assert r.status_code == 200
    assert "json" in (r.content_type or "")
    body = r.get_json()
    assert body is not None
    assert body.get("success") is True
    assert body.get("metrics")
    assert body["metrics"]["peak_ss_x_c"] is not None
    assert body.get("loss_report")
    assert body["loss_report"].get("losses")
    assert body.get("stations")
    assert body.get("surface_table")
    assert body.get("ranked_fixes") is not None
    assert "x_c_ps" in body and "cp_ss" in body
    # Hill–Peterson shock table on API payload
    assert body.get("shock_relations_table") is not None
    assert body.get("normal_shock_chart")
    if body["shock_relations_table"]:
        s0 = body["shock_relations_table"][0]
        assert s0.get("p02_p01") is not None
        assert s0.get("p2_p1") is not None
        assert s0.get("M2") is not None

    # 405/404 for unknown API must stay JSON
    bad = client.get("/api/does_not_exist_xyz")
    assert bad.status_code == 404
    assert bad.is_json
    assert bad.get_json().get("error") == "not_found"


def test_cascade_job_includes_design_package(tmp_path: Path):
    res = run_cascade_job(
        {
            "blade_name": "dense_job",
            "n_blades": 3,
            "nx": 40,
            "ny": 20,
            "output_dir": str(tmp_path),
            "run_mesh": False,
            "run_solve": False,
            "run_sample": False,
            "beta1_deg": 72.0,
            "pure_impulse_lock": True,
            "w1_m_s": 950.0,
            "p1_pa": 5.5e5,
            "t1_k": 1100.0,
            "gamma": 1.3,
            "r_specific_j_kg_k": 320.0,
            "blade_shape": {"thickness_ratio": 0.22, "arc_bulge": 1.2},
        }
    )
    assert res.success
    assert res.metrics is not None
    assert res.design_report is not None
    assert res.loss_report is not None
    assert res.design_package_json and Path(res.design_package_json).is_file()
    assert "η_design" in res.message or "eta" in res.message.lower() or "load" in res.message.lower()
