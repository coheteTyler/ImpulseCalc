"""Paper / NASA validation gates for ImpulseCalc (shipped paper_validation module)."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from impulsecalc.meanline import compute_meanline
from impulsecalc.openfoam_case import generate_openfoam_case
from impulsecalc.paper_validation import (
    ETC2019_165,
    etc2019_rotor_meanline_inputs,
    run_all_paper_validations,
    seume2017_geometry_inputs,
    u_over_w1_from_angles,
    validate_etc2019_meanline,
    validate_nasa_normal_shock_tables,
    validate_seume2017_kinematics,
)


def test_u_over_w1_etc2019_value():
    r = u_over_w1_from_angles(36.0, 20.0)
    assert r == pytest.approx(0.29332714732, rel=1e-6)


def test_etc2019_meanline_matches_paper_angles():
    rep = validate_etc2019_meanline()
    assert rep.ok, rep.to_dict()
    ml = compute_meanline(etc2019_rotor_meanline_inputs())
    assert abs(ml.alpha1_deg) == pytest.approx(20.0, abs=0.15)
    assert abs(ml.beta1_deg) == pytest.approx(36.0)
    assert ml.beta2_deg == pytest.approx(-ml.beta1_deg)
    assert abs(ml.degree_of_reaction) <= 0.05
    # Sign of euler follows β convention (neg β1 → euler < 0); power uses |Δh|
    ideal = 2.0 * ml.u_m_s * ml.w1_m_s * math.sin(math.radians(abs(ml.beta1_deg)))
    assert abs(ml.euler_work_j_kg) == pytest.approx(ideal, rel=1e-6)
    assert ml.power_w > 0


def test_nasa_shock_tables():
    rep = validate_nasa_normal_shock_tables()
    assert rep.ok, rep.to_dict()


def test_seume2017_scale_kinematics():
    rep = validate_seume2017_kinematics()
    assert rep.ok, rep.to_dict()


def test_etc2019_case_builds_with_blade_wall_wiring(tmp_path: Path):
    inp = etc2019_rotor_meanline_inputs()
    res = generate_openfoam_case(
        inp,
        tmp_path,
        case_name="etc2019_val",
        n_blades=3,
        nx=40,
        ny=30,
        startup=False,
    )
    assert res.success
    cdir = Path(res.case_dir)
    assert (cdir / "system" / "topoSetDict").is_file()
    # Pre-mesh: no blades BC (added after createPatch). STL + topoSet wired for walls.
    u0 = (cdir / "0" / "U").read_text(encoding="utf-8")
    assert "blades" not in u0
    assert "inlet" in u0
    assert (cdir / "constant" / "triSurface" / "blades.stl").is_file()
    assert "blades" in (cdir / "system" / "createPatchDict").read_text(encoding="utf-8")
    # Pitch matches Z,r_m
    pitch = 2 * math.pi * ETC2019_165["r_m"] / ETC2019_165["Z_rotor"]
    assert inp.chord_m / inp.solidity == pytest.approx(pitch, rel=1e-6)


def test_run_all_paper_validations_offline():
    out = run_all_paper_validations()
    assert out["ok"] is True
    assert len(out["reports"]) >= 4


def test_etc2019_euler_magnitude_matches_impulse_formula():
    ml = compute_meanline(etc2019_rotor_meanline_inputs())
    ideal = 2.0 * ml.u_m_s * ml.w1_m_s * math.sin(math.radians(abs(ml.beta1_deg)))
    assert abs(ml.euler_work_j_kg) == pytest.approx(ideal, rel=1e-6)
    assert abs(ml.alpha1_deg) == pytest.approx(ETC2019_165["alpha1_deg_paper"], abs=0.15)


def test_seume2017_geometry_scales():
    from impulsecalc.paper_validation import SEUME_2017

    inp = seume2017_geometry_inputs()
    assert inp.n_blades_machine == SEUME_2017["Z_rotor"]
    assert inp.span_m == pytest.approx(SEUME_2017["span_m"])
    assert inp.tip_radius_m == pytest.approx(SEUME_2017["D_shroud_m"] / 2.0)


def test_live_etc2019_case_if_present():
    """If a prior validation CFD case exists, re-check walls + field gates."""
    from impulsecalc.paper_validation import (
        extract_cfd_field_stats,
        validate_cfd_solution,
        validate_cfd_wall_mesh,
    )

    cdir = ROOT / "output" / "openfoam_cases" / "etc2019_165_rotor"
    if not (cdir / "constant" / "polyMesh" / "boundary").is_file():
        pytest.skip("ETC CFD case not built yet")
    walls = validate_cfd_wall_mesh(cdir)
    assert walls.ok, walls.to_dict()
    stats = extract_cfd_field_stats(cdir)
    if not stats.get("ok"):
        pytest.skip("ETC case meshed but not solved")
    rep = validate_cfd_solution(cdir)
    assert rep.ok, rep.to_dict()
