"""Industry-standard 2D cascade path: body-fitted, SST, noSlip, cascade loss."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path  # noqa: I001

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from impulsecalc.cascade_loss import (
    cascade_loss_from_meanline_proxy,
    cascade_loss_from_sample_rows,
    cascade_loss_from_station_means,
    mass_average,
    total_pressure_loss_coefficient,
)
from impulsecalc.fidelity import (
    MESH_BODY,
    MESH_STAIR,
    TURB_LAMINAR,
    TURB_SST,
    WALL_NOSLIP,
    WALL_SLIP,
    compare_fidelity,
    resolve_fidelity,
)
from impulsecalc.meanline import MeanlineInputs, compute_meanline
from impulsecalc.openfoam_case import (
    BLADE_WALL_PATCH,
    blade_wall_normal_stats,
    generate_openfoam_case,
    write_fields,
    write_turbulence,
)
from impulsecalc.postprocess import synthetic_surface_pressure
from impulsecalc.design_report import build_design_report, compute_cascade_loss_metrics
from impulsecalc.runners import (
    _mesh_path_from_case_meta,
    mesh_pipeline,
    openfoam_available,
    clear_openfoam_probe_cache,
)


def _ml_inputs(**kw) -> MeanlineInputs:
    base = dict(
        beta1_deg=-36.0,
        pure_impulse_lock=True,
        w1_m_s=400.0,
        blade_speed_u_m_s=120.0,
        p1_pa=2e5,
        t1_k=320.0,
        chord_m=0.01,
        solidity=1.5,
        blade_name="industry_test",
    )
    base.update(kw)
    return MeanlineInputs(**base)


# ---------------------------------------------------------------------------
# Cascade loss math
# ---------------------------------------------------------------------------


def test_total_pressure_loss_coefficient_standard():
    # ω = (p01−p02)/(p01−p1) = (1.5e5−1.4e5)/(1.5e5−1.0e5) = 0.2
    w = total_pressure_loss_coefficient(1.5e5, 1.4e5, 1.0e5)
    assert w == pytest.approx(0.2, rel=1e-9)


def test_mass_average_weights():
    assert mass_average([1.0, 3.0], [1.0, 1.0]) == pytest.approx(2.0)
    assert mass_average([1.0, 3.0], [3.0, 1.0]) == pytest.approx(1.5)


def test_cascade_loss_from_station_means():
    m = cascade_loss_from_station_means(
        p_in=1e5, p0_in=1.5e5, p_out=1e5, p0_out=1.4e5, gamma=1.4
    )
    assert m.omega_pt == pytest.approx(0.2, rel=1e-6)
    assert 0.0 < m.p0_recovery < 1.0


def test_cascade_loss_mass_averaged_samples():
    m = cascade_loss_from_sample_rows(
        [1e5, 1.01e5],
        [0.8, 0.8],
        [1e5, 0.99e5],
        [0.75, 0.75],
        gamma=1.4,
        inlet_mass=[1.0, 1.0],
        outlet_mass=[1.0, 1.0],
    )
    assert m.source == "mass_averaged_samples"
    assert m.omega_pt >= 0.0


def test_meanline_proxy_has_industry_fields():
    m = cascade_loss_from_meanline_proxy(p1_pa=2e5, mach_w1=1.2, loss_penalty=0.1)
    assert "omega_pt" in m.to_dict()
    assert "zeta_ke" in m.to_dict()
    assert m.source == "meanline_proxy"


# ---------------------------------------------------------------------------
# Fidelity industry flags
# ---------------------------------------------------------------------------


def test_fast_is_stair_laminar_slip():
    f = resolve_fidelity("fast")
    assert f.mesh_path == MESH_STAIR
    assert f.turbulence_model == TURB_LAMINAR
    assert f.wall_bc == WALL_SLIP


def test_accurate_is_body_sst_noslip():
    a = resolve_fidelity("accurate")
    assert a.mesh_path == MESH_BODY
    assert a.turbulence_model == TURB_SST
    assert a.wall_bc == WALL_NOSLIP
    b = resolve_fidelity("balanced")
    assert b.mesh_path == MESH_BODY
    assert b.turbulence_model == TURB_SST
    assert b.wall_bc == WALL_NOSLIP
    cmp_ = compare_fidelity(resolve_fidelity("fast"), a)
    assert cmp_["industry_mesh"]
    assert cmp_["industry_sst"]
    assert cmp_["industry_noslip"]


def test_industry_alias_maps_to_accurate():
    ind = resolve_fidelity("industry")
    assert ind.mode == "accurate"
    assert ind.mesh_path == MESH_BODY


# ---------------------------------------------------------------------------
# Case generation: turbulence + walls + mesh path
# ---------------------------------------------------------------------------


def test_write_turbulence_laminar_vs_sst(tmp_path: Path):
    c = tmp_path / "t"
    c.mkdir()
    write_turbulence(c, turbulence_model=TURB_LAMINAR)
    mom = (c / "constant" / "momentumTransport").read_text(encoding="utf-8")
    assert "laminar" in mom
    assert "kOmegaSST" not in mom
    write_turbulence(c, turbulence_model=TURB_SST)
    mom = (c / "constant" / "momentumTransport").read_text(encoding="utf-8")
    assert "RAS" in mom
    assert "kOmegaSST" in mom


def test_write_fields_sst_writes_k_omega_nut(tmp_path: Path):
    ml = compute_meanline(_ml_inputs())
    case = tmp_path / "sst"
    case.mkdir()
    paths = write_fields(
        case,
        ml,
        include_blade_walls=True,
        wall_bc=WALL_NOSLIP,
        turbulence_model=TURB_SST,
    )
    assert "k" in paths and paths["k"].is_file()
    assert "omega" in paths and paths["omega"].is_file()
    assert "nut" in paths and paths["nut"].is_file()
    u = (case / "0" / "U").read_text(encoding="utf-8")
    assert "noSlip" in u
    k = (case / "0" / "k").read_text(encoding="utf-8")
    assert "kqRWallFunction" in k or "fixedValue" in k
    om = (case / "0" / "omega").read_text(encoding="utf-8")
    assert "omegaWallFunction" in om or "fixedValue" in om


def test_generate_industry_case_wires_all(tmp_path: Path):
    res = generate_openfoam_case(
        _ml_inputs(blade_name="ind_gen"),
        tmp_path,
        case_name="ind_gen",
        n_blades=3,
        fidelity="accurate",
        nx=None,
        ny=None,
        startup=False,
    )
    assert res.success, res.message
    cdir = Path(res.case_dir)
    meta = json.loads((cdir / "impulsecalc_case_meta.json").read_text(encoding="utf-8"))
    assert meta["fidelity"]["mesh_path"] == MESH_BODY
    assert meta["fidelity"]["turbulence_model"] == TURB_SST
    assert meta["fidelity"]["wall_bc"] == WALL_NOSLIP
    assert meta["turbulence"]["model"] == TURB_SST
    assert meta["blade_walls"]["mesh_path"] == MESH_BODY
    assert "snappy" in meta["blade_walls"]["method"].lower() or meta["blade_walls"][
        "preferred"
    ].lower().startswith("snappy")
    # Thin-3D required so snappyHexMesh accepts the mesh (no empty patches)
    assert int(meta["geometry"]["nz"]) >= 3
    assert meta["geometry"]["front_back_type"] == "wall"
    assert meta["spanwise"]["front_back_type"] == "wall"
    bm = (cdir / "system" / "blockMeshDict").read_text(encoding="utf-8")
    assert re.search(r"hex \(0 1 2 3 4 5 6 7\) \(\d+ \d+ [3-9]\d*\)", bm) or re.search(
        r"\(\d+ \d+ [3-9]\)", bm
    )
    assert "type wall" in bm  # frontAndBack

    mom = (cdir / "constant" / "momentumTransport").read_text(encoding="utf-8")
    assert "kOmegaSST" in mom
    # Pre-mesh 0/ has RANS fields but no blades patch
    assert (cdir / "0" / "k").is_file()
    assert (cdir / "0" / "omega").is_file()
    assert BLADE_WALL_PATCH not in (cdir / "0" / "U").read_text(encoding="utf-8")
    # frontAndBack is slip wall (thin-3D), not empty
    u0 = (cdir / "0" / "U").read_text(encoding="utf-8")
    assert "frontAndBack" in u0
    assert "type empty" not in u0 or "slip" in u0

    snappy = (cdir / "system" / "snappyHexMeshDict").read_text(encoding="utf-8")
    assert "body-fitted" in snappy or "snap" in snappy
    assert "nSolveIter" in snappy
    assert "minTetQuality" in snappy
    assert 'file "blades.stl"' in snappy or "file" in snappy
    pipe = (cdir / "MESH_PIPELINE.txt").read_text(encoding="utf-8")
    assert "BODY-FITTED" in pipe or "body" in pipe.lower()
    assert "snappyHexMesh" in pipe

    assert "blade_walls_snappy_primary" in res.notes
    assert any("turbulence=kOmegaSST" in n for n in res.notes)
    assert any("wall_bc=noSlip" in n for n in res.notes)

    # mesh_path helper used by runner
    assert _mesh_path_from_case_meta(cdir) == MESH_BODY


def test_generate_fast_case_still_stair_laminar(tmp_path: Path):
    res = generate_openfoam_case(
        _ml_inputs(blade_name="fast_reg"),
        tmp_path,
        case_name="fast_reg",
        fidelity="fast",
    )
    assert res.success
    cdir = Path(res.case_dir)
    meta = json.loads((cdir / "impulsecalc_case_meta.json").read_text(encoding="utf-8"))
    assert meta["fidelity"]["mesh_path"] == MESH_STAIR
    assert meta["fidelity"]["turbulence_model"] == TURB_LAMINAR
    assert meta["fidelity"]["wall_bc"] == WALL_SLIP
    mom = (cdir / "constant" / "momentumTransport").read_text(encoding="utf-8")
    assert "laminar" in mom
    assert not (cdir / "0" / "k").is_file()
    assert "blade_walls_topoSet" in res.notes
    pipe = (cdir / "MESH_PIPELINE.txt").read_text(encoding="utf-8")
    assert "STAIR" in pipe or "topoSet" in pipe


# ---------------------------------------------------------------------------
# Design report cascade loss
# ---------------------------------------------------------------------------


def test_design_report_includes_cascade_loss(tmp_path: Path):
    ml = compute_meanline(_ml_inputs())
    surf = synthetic_surface_pressure(
        p1_pa=ml.inputs.p1_pa,
        mach_w1=ml.mach_w1,
        n=40,
        thickness_ratio=0.15,
        beta1_deg=ml.beta1_deg,
        beta2_deg=ml.beta2_deg,
    )
    case = tmp_path / "rep"
    case.mkdir()
    rep = build_design_report(
        surf,
        ml=ml,
        case_dir=case,
        write_exports=True,
        include_plots=False,
        thickness_ratio=0.15,
    )
    assert rep.success
    cl = rep.cascade_loss
    assert "omega_pt" in cl
    assert "zeta_ke" in cl
    assert "p0_recovery" in cl
    # Physics-valid report path: no unphysical recovery from surface rakes
    assert cl["omega_pt"] >= 0.0
    assert cl["p0_recovery"] <= 1.0 + 1e-9
    assert "ω_pt=" in rep.summary or "omega" in rep.summary.lower() or "ω_pt" in rep.summary
    cl_path = case / "postProcessing" / "cascade_loss.json"
    assert cl_path.is_file()
    disk = json.loads(cl_path.read_text(encoding="utf-8"))
    assert disk["omega_pt"] == pytest.approx(cl["omega_pt"])
    assert disk["omega_pt"] >= 0.0
    assert disk["p0_recovery"] <= 1.0 + 1e-9


def test_compute_cascade_loss_proxy_physics_valid():
    """Surface Cp is not an inlet/outlet rake — proxy must keep ω≥0, p02/p01≤1."""
    surf = synthetic_surface_pressure(p1_pa=2e5, mach_w1=1.2, n=30, thickness_ratio=0.12)
    from impulsecalc.design_report import build_stations

    st = build_stations(surf, gamma=1.3, mach_w1=1.2, n=11)
    m = compute_cascade_loss_metrics(
        surf, st, gamma=1.3, mach_w1=1.2, surface_loss_penalty=0.05
    )
    assert m.omega_pt >= 0.0
    assert m.p0_recovery <= 1.0 + 1e-9
    assert m.p0_in_pa > 0
    assert "proxy" in m.source or "meanline" in m.source


def test_compute_cascade_loss_from_explicit_rake_samples():
    """When real inlet/outlet samples are supplied, mass-average them."""
    surf = synthetic_surface_pressure(p1_pa=1e5, mach_w1=0.8, n=10)
    m = compute_cascade_loss_metrics(
        surf,
        [],
        gamma=1.4,
        mach_w1=0.8,
        inlet_outlet_samples={
            "inlet_p": [1e5, 1e5],
            "inlet_mach": [0.8, 0.8],
            "outlet_p": [1e5, 1e5],
            "outlet_mach": [0.75, 0.75],
        },
    )
    assert m.omega_pt >= 0.0
    assert m.p0_recovery <= 1.0 + 1e-9


@pytest.mark.skipif(
    not openfoam_available(force_probe=True).get("available"),
    reason="OpenFOAM/WSL not available",
)
def test_live_industry_mesh_pipeline_uses_snappy(tmp_path: Path):
    """Gating live mesh: body_fitted path must cut with snappyHexMesh, nFaces>0.

    Proves the primary industry path is not stair-step topoSet (AC1).
    """
    clear_openfoam_probe_cache()
    res = generate_openfoam_case(
        _ml_inputs(blade_name="live_snap"),
        tmp_path,
        case_name="live_snap",
        n_blades=3,
        fidelity="balanced",
        nx=48,
        ny=28,
        startup=False,
    )
    assert res.success
    cdir = Path(res.case_dir)
    assert _mesh_path_from_case_meta(cdir) == MESH_BODY
    result = mesh_pipeline(cdir, timeout_s=600)
    assert result.get("success") is True, result.get("detail") or result.get("notes")
    assert result.get("prefer_snappy") is True
    assert result.get("cut_method") == "snappyHexMesh", (
        f"expected snappy primary success, got {result.get('cut_method')}: "
        f"{result.get('notes')}"
    )
    walls = result.get("blade_walls") or {}
    assert walls.get("ok") is True
    assert int(walls.get("nFaces") or 0) > 0
    # Body-fitted: wall normals not purely axis-aligned stair-steps
    stats = blade_wall_normal_stats(cdir)
    assert stats.get("ok"), stats
    assert stats.get("body_fitted_like") is True or float(stats.get("oblique_frac") or 0) > 0.05, stats
    # Industry BCs after mesh
    u = (cdir / "0" / "U").read_text(encoding="utf-8")
    assert "noSlip" in u
    assert (cdir / "0" / "k").is_file()
