"""Blade walls in the cascade volume mesh (not STL-only overlays)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from impulsecalc.geometry import BladeGeometry, BladeShapeParams
from impulsecalc.meanline import MeanlineInputs
from impulsecalc.openfoam_case import (
    BLADE_WALL_PATCH,
    fluid_seed_point,
    generate_openfoam_case,
    mesh_has_blade_walls,
    parse_boundary_file,
    write_blockmesh,
    write_topo_set_dict,
)


def test_case_builder_wires_blade_wall_mesh_path(tmp_path: Path):
    """Generated case must reference blades.stl cut-out + wall BC (not empty duct only)."""
    res = generate_openfoam_case(
        MeanlineInputs(
            blade_name="wall_case",
            chord_m=0.01,
            solidity=1.13688,
            w1_m_s=500.0,
            pure_impulse_lock=True,
        ),
        tmp_path,
        case_name="wall_case",
        n_blades=3,
        nx=40,
        ny=30,
        startup=True,
    )
    cdir = Path(res.case_dir)
    assert res.success
    assert (cdir / "constant" / "triSurface" / "blades.stl").is_file()
    topo = (cdir / "system" / "topoSetDict").read_text(encoding="utf-8")
    assert "surfaceToCell" in topo
    assert "blades.stl" in topo
    assert "solidCells" in topo and "fluidCells" in topo
    assert "invert" in topo
    cpd = (cdir / "system" / "createPatchDict").read_text(encoding="utf-8")
    assert BLADE_WALL_PATCH in cpd
    assert "oldInternalFaces" in cpd
    assert "type wall" in cpd
    snappy = (cdir / "system" / "snappyHexMeshDict").read_text(encoding="utf-8")
    assert "blades.stl" in snappy
    assert BLADE_WALL_PATCH in snappy
    # Pre-mesh 0/ must NOT list blades — OF-12 subsetMesh readField aborts otherwise
    u0 = (cdir / "0" / "U").read_text(encoding="utf-8")
    p0 = (cdir / "0" / "p").read_text(encoding="utf-8")
    assert BLADE_WALL_PATCH not in u0
    assert "inlet" in u0 and "outlet" in u0
    assert BLADE_WALL_PATCH not in p0
    assert "blade_walls_topoSet" in res.notes
    assert (cdir / "MESH_PIPELINE.txt").is_file()
    meta = (cdir / "impulsecalc_case_meta.json").read_text(encoding="utf-8")
    assert "blade_walls" in meta
    assert BLADE_WALL_PATCH in meta


def test_write_fields_blade_walls_toggle(tmp_path: Path):
    from impulsecalc.meanline import compute_meanline
    from impulsecalc.openfoam_case import write_fields

    ml = compute_meanline(
        MeanlineInputs(w1_m_s=400.0, pure_impulse_lock=True, chord_m=0.01)
    )
    case = tmp_path / "fld"
    case.mkdir()
    write_fields(case, ml, include_blade_walls=False)
    assert BLADE_WALL_PATCH not in (case / "0" / "U").read_text(encoding="utf-8")
    write_fields(case, ml, include_blade_walls=True)
    assert BLADE_WALL_PATCH in (case / "0" / "U").read_text(encoding="utf-8")
    assert "slip" in (case / "0" / "U").read_text(encoding="utf-8")
    # Industry no-slip walls
    write_fields(case, ml, include_blade_walls=True, wall_bc="noSlip")
    assert "noSlip" in (case / "0" / "U").read_text(encoding="utf-8")


def test_prepare_zero_strips_blades_for_subset(tmp_path: Path):
    from impulsecalc.openfoam_case import prepare_zero_for_subset_mesh

    res = generate_openfoam_case(
        MeanlineInputs(blade_name="prep0", w1_m_s=400.0, pure_impulse_lock=True),
        tmp_path,
        case_name="prep0",
        n_blades=3,
        nx=30,
        ny=20,
    )
    cdir = Path(res.case_dir)
    # Simulate stale post-mesh 0/ with blades (re-mesh scenario)
    u = cdir / "0" / "U"
    text = u.read_text(encoding="utf-8")
    u.write_text(
        text.replace(
            "frontAndBack { type empty; }",
            "frontAndBack { type empty; }\n    blades { type slip; }",
        ),
        encoding="utf-8",
    )
    assert BLADE_WALL_PATCH in u.read_text(encoding="utf-8")
    prep = prepare_zero_for_subset_mesh(cdir)
    assert prep.get("ok")
    assert BLADE_WALL_PATCH not in u.read_text(encoding="utf-8")


def test_fluid_seed_is_upstream_of_leading_edge():
    geom = BladeGeometry(chord_m=0.01, solidity=1.4, beta1_deg=72, beta2_deg=-72)
    x, y, z = fluid_seed_point(geom, 3, x_up_c=0.5, x_dn_c=1.0)
    assert x < 0  # upstream of LE at x=0
    assert abs(y) < 1e-9
    assert 0 < z < 0.001


def test_parse_boundary_and_mesh_has_blade_walls(tmp_path: Path):
    """mesh_has_blade_walls drives real polyMesh/boundary content."""
    bpath = tmp_path / "boundary"
    bpath.write_text(
        """
5
(
    inlet { type patch; nFaces 10; startFace 0; }
    outlet { type patch; nFaces 10; startFace 10; }
    bottom { type cyclic; nFaces 20; startFace 20; }
    top { type cyclic; nFaces 20; startFace 40; }
    blades { type wall; nFaces 128; startFace 60; }
)
""",
        encoding="utf-8",
    )
    # Wrap as case
    case = tmp_path / "case"
    (case / "constant" / "polyMesh").mkdir(parents=True)
    (case / "constant" / "polyMesh" / "boundary").write_text(
        bpath.read_text(encoding="utf-8"), encoding="utf-8"
    )
    patches = parse_boundary_file(case / "constant" / "polyMesh" / "boundary")
    assert "blades" in patches
    assert patches["blades"]["type"] == "wall"
    assert patches["blades"]["nFaces"] == 128
    rep = mesh_has_blade_walls(case)
    assert rep["ok"] is True
    assert rep["nFaces"] == 128

    # Empty duct (legacy) fails gate
    (case / "constant" / "polyMesh" / "boundary").write_text(
        """
4
(
    inlet { type patch; nFaces 10; startFace 0; }
    outlet { type patch; nFaces 10; startFace 10; }
    bottom { type cyclic; nFaces 20; startFace 20; }
    top { type cyclic; nFaces 20; startFace 40; }
)
""",
        encoding="utf-8",
    )
    bad = mesh_has_blade_walls(case)
    assert bad["ok"] is False
    assert "blades" not in bad["patches"]


def test_topo_set_dict_outside_point_in_domain(tmp_path: Path):
    geom = BladeGeometry(
        chord_m=0.01,
        solidity=1.13688,
        beta1_deg=50,
        beta2_deg=-50,
        shape=BladeShapeParams(thickness_ratio=0.5),
    )
    path = write_topo_set_dict(tmp_path, geom, 3, x_up_c=0.5, x_dn_c=1.0)
    text = path.read_text(encoding="utf-8")
    ox, oy, oz = fluid_seed_point(geom, 3)
    assert f"{ox:.8g}" in text or str(ox)[:6] in text
    assert "outsidePoints" in text
