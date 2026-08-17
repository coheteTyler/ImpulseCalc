"""Comparable design package v3 + engineering video script content."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from impulsecalc.design_package import (
    PACKAGE_FORMAT,
    SCHEMA_VERSION,
    assemble_comparable_package,
    package_required_keys,
    write_comparable_package,
    write_metrics_comparison_csv,
)
from impulsecalc.meanline import MeanlineInputs, compute_meanline
from impulsecalc.openfoam_case import generate_openfoam_case
from impulsecalc.postprocess import synthetic_surface_pressure
from impulsecalc.design_report import build_design_report
from impulsecalc.technical_video import (
    DEFAULT_STEADY_HOLD_S,
    DEFAULT_VIDEO_FIELDS,
    VideoOptions,
    build_paraview_script,
    descriptive_stem,
    generate_technical_video,
    planned_animation_length,
    retire_legacy_video_artifacts,
    steady_hold_frame_count,
    write_video_artifacts,
)


def test_assemble_package_has_required_keys():
    pkg = assemble_comparable_package(
        operating={"mach_w1": 1.4, "beta1_deg": 72},
        metrics={"eta_design_proxy": 0.8, "n_shocks": 1, "mach_w1": 1.4},
        meanline_inputs={"blade_name": "t", "beta1_deg": 72},
        blade_shape={"thickness_ratio": 0.2},
        domain={"x_up_c": 0.5, "x_dn_c": 1.0},
        stations=[{"x_c": 0.0, "cp_ss": -1.0}],
        shocks=[{"x_c": 0.4, "M1": 1.5}],
        loss_report={"summary": "ok", "ranked_fixes": ["fix1"]},
        ranked_fixes=["fix1"],
        summary="test summary",
        case_dir="/tmp/case",
        blade_name="t",
    )
    assert pkg["format"] == PACKAGE_FORMAT
    assert pkg["schema_version"] == SCHEMA_VERSION
    for k in package_required_keys():
        assert k in pkg, k
    assert pkg["domain"]["x_up_c"] == 0.5
    assert pkg["metrics"]["eta_design_proxy"] == 0.8


def test_write_comparable_package_json_and_csv(tmp_path: Path):
    pkg = assemble_comparable_package(
        operating={"mach_w1": 1.5, "solidity": 1.4},
        metrics={
            "eta_design_proxy": 0.77,
            "lieblein_df_ss": 0.5,
            "n_shocks": 2,
            "mach_w1": 1.5,
            "peak_ss_x_c": 0.45,
        },
        meanline_inputs={"blade_name": "pack_test", "w1_m_s": 900},
        blade_shape={"arc_bulge": 1.1},
        domain={"x_up_c": 0.75, "x_dn_c": 1.5, "x_min": -0.02},
        stations=[{"x_c": 0.1, "cp_ss": -0.8, "cp_ps": 0.2}],
        shocks=[{"side": "SS", "x_c": 0.5, "M1": 1.6, "p02_p01": 0.9}],
        loss_report={"summary": "shocks", "ranked_fixes": ["drop W1"]},
        ranked_fixes=["drop W1"],
        summary="η≈0.77",
        blade_name="pack_test",
    )
    written = write_comparable_package(tmp_path, pkg)
    assert Path(written["design_package_json"]).is_file()
    assert Path(written["comparison_csv"]).is_file()
    assert Path(written["metrics_kv_csv"]).is_file()
    loaded = json.loads(Path(written["design_package_json"]).read_text(encoding="utf-8"))
    assert loaded["format"] == PACKAGE_FORMAT
    assert loaded["schema_version"] == 3
    assert "comparison_csv" in loaded["exports"]
    csv_text = Path(written["comparison_csv"]).read_text(encoding="utf-8")
    assert "eta_design_proxy" in csv_text
    assert "0.77" in csv_text or "0.770" in csv_text
    assert "n_shocks" in csv_text


def test_design_report_writes_v3_package(tmp_path: Path):
    ml = compute_meanline(MeanlineInputs(blade_name="dr_v3"))
    cdir = tmp_path / "case"
    (cdir / "system").mkdir(parents=True)
    # minimal meta with domain
    (cdir / "impulsecalc_case_meta.json").write_text(
        json.dumps(
            {
                "blade_name": "dr_v3",
                "geometry": {"domain": {"x_up_c": 0.6, "x_dn_c": 1.2, "x_min": -0.014}},
            }
        ),
        encoding="utf-8",
    )
    surf = synthetic_surface_pressure(p1_pa=ml.inputs.p1_pa, mach_w1=ml.mach_w1)
    rep = build_design_report(
        surf, ml=ml, case_dir=cdir, write_exports=True, include_plots=False
    )
    assert rep.exports.get("design_package_json")
    pkg_path = Path(rep.exports["design_package_json"])
    assert pkg_path.is_file()
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    assert pkg["format"] == PACKAGE_FORMAT
    assert pkg["schema_version"] == 3
    assert "metrics" in pkg and "meanline_inputs" in pkg
    assert "blade_shape" in pkg
    assert pkg.get("domain", {}).get("x_up_c") == pytest.approx(0.6)
    assert Path(rep.exports["comparison_csv"]).is_file()
    assert Path(rep.exports["stations_csv"]).is_file()


def test_video_script_engineering_content(tmp_path: Path):
    """Script must reference foam reader, blades STL, Mach, streamlines/vectors."""
    cdir = tmp_path / "vc"
    cdir.mkdir()
    (cdir / "constant" / "triSurface").mkdir(parents=True)
    stl = cdir / "constant" / "triSurface" / "blades.stl"
    stl.write_text("solid blades\nendsolid blades\n", encoding="utf-8")
    (cdir / "0.0001").mkdir()
    (cdir / "0.0001" / "U").write_text("U\n", encoding="utf-8")
    (cdir / "impulsecalc_case_meta.json").write_text(
        json.dumps(
            {
                "blade_name": "vid_blade",
                "meanline": {
                    "mach_w1": 1.55,
                    "beta1_deg": 72.0,
                    "inputs": {"gamma": 1.32, "r_specific_j_kg_k": 310.0, "t1_k": 1100},
                },
            }
        ),
        encoding="utf-8",
    )
    foam = cdir / "vid_blade.foam"
    foam.write_text("", encoding="utf-8")
    opts = VideoOptions(
        fields=["Mach", "streamlines", "U_vectors", "rho_gradient"],
        view_preset="blade_passage_shocks",
        blade_name="vid_blade",
        run_pvbatch=False,
    )
    arts = write_video_artifacts(cdir, opts)
    text = Path(arts["script_path"]).read_text(encoding="utf-8")
    assert "OpenFOAMReader" in text
    assert "OpenDataFile" in text or "FileNames" in text
    assert "blades.stl" in text or "STL" in text
    assert "Mach" in text
    # Mid-span slice is the primary colored actor (not freestream volume shell)
    assert "Slice(" in text or "Slice(Input=" in text
    assert "primary display=Slice" in text
    assert "StreamTracer" in text
    assert 'SeedType="Line"' in text or "SeedType='Line'" in text
    assert "High Resolution Line Source" not in text
    assert "Glyph" in text
    assert "blades gray metal" in text or "ColorBy=None" in text or "ColorBy(d, None)" in text
    assert "1.32" in text or "GAMMA" in text
    # Same engineering scene for proof PNG and SaveAnimation (no hide-paths dance)
    assert "lock_engineering_scene" in text
    assert 'lock_engineering_scene("proof")' in text
    assert 'lock_engineering_scene("animation")' in text
    assert "scene_lock_ok" in text
    assert "anim_mid" in text
    script2 = build_paraview_script(cdir, opts, Path(arts["output_path"]), foam)
    assert "Slice(" in script2 and "primary display=Slice" in script2
    assert "lock_engineering_scene" in script2


def test_generate_technical_video_script_only(tmp_path: Path):
    cdir = tmp_path / "g"
    cdir.mkdir()
    (cdir / "1e-4").mkdir()
    (cdir / "1e-4" / "U").write_text("x\n", encoding="utf-8")
    (cdir / "constant" / "triSurface").mkdir(parents=True)
    (cdir / "constant" / "triSurface" / "blades.stl").write_text(
        "solid b\nendsolid b\n", encoding="utf-8"
    )
    res = generate_technical_video(
        cdir,
        VideoOptions(fields=list(DEFAULT_VIDEO_FIELDS), run_pvbatch=False),
        run_pvbatch=False,
    )
    assert res.status == "script_only"
    assert res.script_path and Path(res.script_path).is_file()
    body = Path(res.script_path).read_text(encoding="utf-8")
    assert "StreamTracer" in body
    assert "OpenFOAMReader" in body


def test_case_stl_has_side_walls(tmp_path: Path):
    res = generate_openfoam_case(
        MeanlineInputs(blade_name="stl_side"),
        tmp_path,
        case_name="stl_side",
        n_blades=3,
        nx=40,
        ny=20,
    )
    stl = Path(res.case_dir) / "constant" / "triSurface" / "blades.stl"
    assert stl.is_file()
    text = stl.read_text(encoding="utf-8")
    assert "facet normal" in text
    # side walls have non-zero xy normals (not only 0 0 ±1)
    assert "facet normal 0 0 1" in text  # caps still present
    # some facet with horizontal component
    assert any(
        line.startswith("  facet normal") and not line.strip().endswith("0 0 1")
        and not line.strip().endswith("0 0 -1")
        for line in text.splitlines()
    )


def test_ui_mentions_package_v3_and_video_fields():
    body = (ROOT / "static" / "calcbody.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "calc.js").read_text(encoding="utf-8")
    assert "comparison_scalars" in body or "design package" in body.lower()
    assert "streamlines" in body
    assert "U vectors" in body or "U_vectors" in body
    assert "impulsecalc_design_package_v3" in js
    assert "streamlines" in js


def test_parse_pvbatch_log_and_success_gate():
    from impulsecalc.technical_video import engineering_video_success, parse_pvbatch_log

    # Calculator line alone must NOT set mach_ok / field_ok
    calc_only = parse_pvbatch_log(
        "Mach calculator: mag(U)/sqrt(1.3*320*T)\n"
        "foam mesh ok ncells=100\n"
        "streamlines on\n"
        "blades STL: /x.stl\n"
        "wrote /x.mp4 size=12000\n"
    )
    assert calc_only["mach_calc"] is True
    assert calc_only["mach_ok"] is False
    assert calc_only["field_ok"] is False

    bad = parse_pvbatch_log(
        "Mach calculator: x\nstream: obsolete\nSTL blades: Attribute FileName\nwrote /x.mp4 size=11748\n"
    )
    assert bad["blades_ok"] is False
    assert bad["stream_ok"] is False

    good_log = (
        "Mach calculator: mag(U)/sqrt(1.3*320*T)\n"
        "foam mesh ok ncells=12000\n"
        "primary display=Slice midspan field=p z=0.0005\n"
        "Mach range CELLS: 0.2 .. 2.8 span=2.6\n"
        "LUT RescaleTransferFunction 0.2 .. 2.8\n"
        "field coloring OK Mach span=2.6\n"
        "streamlines on\n"
        "U_vectors glyphs on\n"
        "blades via OpenDataFile: /case/blades.stl\n"
        "blades STL: /case/blades.stl\n"
        "blades gray metal ColorBy=None\n"
        "blades rendered ok\n"
        "wrote /out.mp4 size=250000\n"
    )
    good = parse_pvbatch_log(good_log)
    assert good["blades_ok"] and good["stream_ok"] and good["mesh_ok"]
    assert good["field_ok"] is True and good["mach_ok"] is True
    assert good["slice_ok"] is True
    assert good["mach_span"] == pytest.approx(2.6)


def test_empty_mesh_not_engineering_success(tmp_path: Path):
    """File size alone must not yield success without blades + flow paths + field range."""
    from impulsecalc.technical_video import engineering_video_success

    mp4 = tmp_path / "tiny.mp4"
    mp4.write_bytes(b"0" * 12000)
    ok, reason = engineering_video_success(
        "Mach calculator: x\nwrote tiny.mp4 size=12000\ncontains no meshes\n",
        mp4,
    )
    assert ok is False
    assert "blade" in reason.lower() or "mesh" in reason.lower() or "flow" in reason.lower() or "mach" in reason.lower() or "field" in reason.lower()


def test_mach_calc_alone_not_engineering_success(tmp_path: Path):
    from impulsecalc.technical_video import engineering_video_success

    mp4 = tmp_path / "mono.mp4"
    mp4.write_bytes(b"0" * 20000)
    log = (
        "Mach calculator: mag(U)/sqrt(1.3*320*T)\n"
        "foam mesh ok ncells=3200\n"
        "streamlines on\n"
        "blades STL: /b.stl\n"
        "blades rendered ok\n"
        "wrote mono.mp4 size=20000\n"
        "Could not determine array range.\n"
    )
    ok, reason = engineering_video_success(log, mp4)
    assert ok is False
    assert "field" in reason.lower() or "mach" in reason.lower() or "color" in reason.lower() or "slice" in reason.lower()


def test_volume_surface_without_slice_not_engineering_success(tmp_path: Path):
    from impulsecalc.technical_video import engineering_video_success

    mp4 = tmp_path / "shell.mp4"
    mp4.write_bytes(b"0" * 20000)
    log = (
        "Mach calculator: x\n"
        "foam mesh ok ncells=3200\n"
        "Mach range CELLS: 1.4 .. 1.43 span=0.03\n"
        "field coloring OK Mach span=0.03\n"
        "streamlines on\n"
        "blades STL: /b.stl\n"
        "blades rendered ok\n"
        "wrote shell.mp4 size=20000\n"
        # missing primary display=Slice
    )
    ok, reason = engineering_video_success(log, mp4)
    assert ok is False
    assert "slice" in reason.lower()


def test_steady_hold_frame_count_meets_one_second():
    """Default 1 s hold must produce ≥ fps frames (playback ≥ 1 s at that fps)."""
    for fps in (10, 12, 24, 30):
        n = steady_hold_frame_count(fps, 1.0)
        assert n >= fps
        assert n / float(fps) >= 1.0 - 1e-12
    assert steady_hold_frame_count(12, DEFAULT_STEADY_HOLD_S) == 12
    assert steady_hold_frame_count(12, 1.5) == 18
    assert steady_hold_frame_count(12, 0.0) == 0


def test_planned_animation_length_includes_hold():
    plan = planned_animation_length(n_time_steps=11, fps=12, steady_hold_s=1.0)
    assert plan["n_hold_frames"] == 12
    assert plan["n_transient_frames"] == 11
    assert plan["n_total_frames"] == 23
    assert plan["hold_playback_s"] == pytest.approx(1.0)
    assert plan["total_playback_s"] == pytest.approx(23 / 12)
    assert plan["meets_1s_hold"] is True
    d = VideoOptions(fps=10, steady_hold_s=1.0).to_dict()
    assert d["n_hold_frames"] >= 10
    assert d["hold_playback_s"] >= 1.0 - 1e-12


def test_video_script_startup_phase_labels(tmp_path: Path):
    cdir = tmp_path / "start_v"
    cdir.mkdir()
    (cdir / "constant" / "triSurface").mkdir(parents=True)
    (cdir / "constant" / "triSurface" / "blades.stl").write_text(
        "solid b\nendsolid b\n", encoding="utf-8"
    )
    (cdir / "0.0001").mkdir()
    (cdir / "0.0001" / "U").write_text("U\n", encoding="utf-8")
    foam = cdir / "s.foam"
    foam.write_text("", encoding="utf-8")
    opts = VideoOptions(fields=list(DEFAULT_VIDEO_FIELDS), fps=12, steady_hold_s=1.0, run_pvbatch=False)
    arts = write_video_artifacts(cdir, opts)
    text = Path(arts["script_path"]).read_text(encoding="utf-8")
    cfg = json.loads(Path(arts["config_path"]).read_text(encoding="utf-8"))
    assert cfg.get("startup_video") is True
    assert "STARTUP" in text or "startup" in text
    assert "STEADY" in text or "steady" in text
    assert "ESTABLISHING" in text or "establishing" in text.lower() or "no flow" in text


def test_video_script_encodes_steady_hold(tmp_path: Path):
    cdir = tmp_path / "hold_v"
    cdir.mkdir()
    (cdir / "constant" / "triSurface").mkdir(parents=True)
    (cdir / "constant" / "triSurface" / "blades.stl").write_text(
        "solid b\nendsolid b\n", encoding="utf-8"
    )
    (cdir / "0.0001").mkdir()
    (cdir / "0.0001" / "U").write_text("U\n", encoding="utf-8")
    foam = cdir / "hold.foam"
    foam.write_text("", encoding="utf-8")
    opts = VideoOptions(
        fields=list(DEFAULT_VIDEO_FIELDS),
        fps=12,
        steady_hold_s=1.0,
        run_pvbatch=False,
    )
    arts = write_video_artifacts(cdir, opts)
    text = Path(arts["script_path"]).read_text(encoding="utf-8")
    cfg = json.loads(Path(arts["config_path"]).read_text(encoding="utf-8"))
    assert cfg.get("steady_hold_s") == pytest.approx(1.0)
    assert int(cfg.get("n_hold_frames") or 0) >= 12
    assert "STEADY_HOLD" in text or "steady_hold" in text or "n_hold" in text
    assert "steady hold" in text.lower() or "N_HOLD" in text
    assert "hold_seq" in text or "all_frame_times" in text
    assert "lock_engineering_scene" in text


def test_descriptive_stem_always_includes_blades():
    opts = VideoOptions(resolution="1080p", view_preset="blade_passage_shocks")
    stem = descriptive_stem(opts)
    assert "Blades" in stem
    assert "1080p" in stem
    assert "BladePassage" in stem
    # Must not match the legacy whitish-shell name users opened by mistake
    assert stem != "Mach_Shocks_BladePassage_1080p"


def test_retire_legacy_video_artifacts_removes_pre_blades(tmp_path: Path):
    vdir = tmp_path / "videos"
    vdir.mkdir()
    legacy = vdir / "Mach_Shocks_BladePassage_1080p.mp4"
    legacy.write_bytes(b"0" * 5000)
    (vdir / "Mach_Shocks_BladePassage_1080p_video_config.json").write_text("{}", encoding="utf-8")
    good = vdir / "Mach_Shocks_Blades_BladePassage_1080p.mp4"
    good.write_bytes(b"1" * 8000)
    removed = retire_legacy_video_artifacts(
        vdir, keep_stem="Mach_Shocks_Blades_BladePassage_1080p", resolution="1080p"
    )
    assert any("Mach_Shocks_BladePassage_1080p" in r for r in removed)
    assert not legacy.is_file()
    assert good.is_file()


def test_multi_hue_metrics_detects_cool_and_warm(tmp_path: Path):
    from PIL import Image
    from impulsecalc.technical_video import multi_hue_png_metrics

    im = Image.new("RGB", (64, 64), (40, 40, 40))
    # left cool blue, right warm red
    for x in range(32):
        for y in range(64):
            im.putpixel((x, y), (20, 40, 200))
            im.putpixel((x + 32, y), (220, 30, 20))
    p = tmp_path / "hue.png"
    im.save(p)
    m = multi_hue_png_metrics(p)
    assert m["ok"] is True
    assert m["cool"] > 50 and m["warm"] > 50

    mono = tmp_path / "mono.png"
    Image.new("RGB", (64, 64), (180, 180, 180)).save(mono)
    m2 = multi_hue_png_metrics(mono)
    assert m2["ok"] is False


def test_engineering_success_fails_flat_multi_hue(tmp_path: Path):
    from impulsecalc.technical_video import engineering_video_success

    mp4 = tmp_path / "flat.mp4"
    mp4.write_bytes(b"0" * 20000)
    log = (
        "Mach calculator: x\n"
        "foam mesh ok ncells=3200\n"
        "primary display=Slice midspan field=p z=0.0005\n"
        "Mach range CELLS: 1.0 .. 2.0 span=1.0\n"
        "field coloring OK p span=1.0\n"
        "streamlines on\n"
        "blades STL: /b.stl\n"
        "blades rendered ok\n"
        "wrote flat.mp4 size=20000\n"
    )
    ok, reason = engineering_video_success(
        log, mp4, multi_hue={"ok": False, "reason": "flat cool=0 warm=0"}
    )
    assert ok is False
    assert "multi-hue" in reason.lower()
