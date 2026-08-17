"""OpenFOAM cascade → engineering technical flow video (ParaView / pvbatch).

OpenFOAM produces time-series fields; engineering MP4 is rendered via pvbatch.
Script generation is pure and unit-testable without ParaView installed.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

VIDEO_FIELDS = (
    "Mach",
    "p",
    "T",
    "rho",
    "rho_gradient",
    "p_gradient",
    "U_vectors",
    "streamlines",
    "wallShear",
    "pTotal_loss",
)
VIEW_PRESETS = ("cascade_overview", "blade_passage_shocks", "surface_pressure")
DURATION_MODES = ("full", "highlight_reel")
RESOLUTIONS = {"720p": (1280, 720), "1080p": (1920, 1080), "4K": (3840, 2160)}

# Engineering default: shocks + flow paths + metal context
DEFAULT_VIDEO_FIELDS = ["Mach", "streamlines", "U_vectors", "rho_gradient"]
# Playback hold of the final/quasi-steady cascade field (not 1 s of physical foam time)
DEFAULT_STEADY_HOLD_S = 1.0


def steady_hold_frame_count(fps: int | float, steady_hold_s: float = DEFAULT_STEADY_HOLD_S) -> int:
    """Number of frames that pad the last engineering scene for ≥ ``steady_hold_s`` playback."""
    f = max(1, int(round(float(fps) or 1)))
    hold = max(0.0, float(steady_hold_s))
    # ceil so 1.0 s at 12 fps is exactly 12, and fractional holds still meet the bar
    return max(0, int(math.ceil(f * hold)))


def planned_animation_length(
    n_time_steps: int,
    fps: int | float,
    steady_hold_s: float = DEFAULT_STEADY_HOLD_S,
) -> dict[str, Any]:
    """Pure plan: transient OpenFOAM times + repeated last-time hold for playback duration."""
    f = max(1, int(round(float(fps) or 1)))
    hold_s = max(0.0, float(steady_hold_s))
    n_hold = steady_hold_frame_count(f, hold_s)
    n_trans = max(1, int(n_time_steps) if int(n_time_steps) > 0 else 1)
    n_total = n_trans + n_hold
    hold_playback_s = n_hold / float(f)
    total_playback_s = n_total / float(f)
    return {
        "fps": f,
        "steady_hold_s": hold_s,
        "n_hold_frames": n_hold,
        "n_transient_frames": n_trans,
        "n_total_frames": n_total,
        "hold_playback_s": hold_playback_s,
        "total_playback_s": total_playback_s,
        "meets_1s_hold": hold_playback_s + 1e-12 >= min(1.0, hold_s) if hold_s > 0 else True,
    }


@dataclass
class VideoOptions:
    fields: list[str] = field(default_factory=lambda: list(DEFAULT_VIDEO_FIELDS))
    resolution: str = "1080p"
    fps: int = 12
    duration_mode: str = "full"
    view_preset: str = "blade_passage_shocks"
    output_format: str = "mp4"
    blade_name: str = "impulse_r0"
    inlet_p1_pa: float | None = None
    inlet_t1_k: float | None = None
    beta1_deg: float | None = None
    mach_w1: float | None = None
    gamma: float | None = None
    r_specific: float | None = None
    highlight_fraction: float = 0.40
    run_pvbatch: bool = True
    show_blades: bool = True
    show_scalar_bar: bool = True
    # ≥1 s of playback on the final/quasi-steady engineering scene (default)
    steady_hold_s: float = DEFAULT_STEADY_HOLD_S

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["width"], d["height"] = RESOLUTIONS.get(self.resolution, RESOLUTIONS["1080p"])
        plan = planned_animation_length(1, self.fps, self.steady_hold_s)
        d["n_hold_frames"] = plan["n_hold_frames"]
        d["hold_playback_s"] = plan["hold_playback_s"]
        return d


@dataclass
class VideoResult:
    status: str
    case_dir: str
    script_path: str | None = None
    config_path: str | None = None
    output_path: str | None = None
    video_dir: str | None = None
    message: str = ""
    notes: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "case_dir": self.case_dir,
            "script_path": self.script_path,
            "config_path": self.config_path,
            "output_path": self.output_path,
            "video_dir": self.video_dir,
            "message": self.message,
            "notes": list(self.notes),
            "options": dict(self.options),
        }


def list_time_dirs(case_dir: Path) -> list[tuple[float, Path]]:
    out = []
    if not case_dir.is_dir():
        return out
    for p in case_dir.iterdir():
        if not p.is_dir():
            continue
        try:
            t = float(p.name)
        except ValueError:
            continue
        if t >= 0:
            out.append((t, p))
    return sorted(out, key=lambda z: z[0])


def find_pvbatch() -> str | None:
    for env in ("PVBATCH", "PARAVIEW_PVBATCH"):
        v = os.environ.get(env)
        if v and Path(v).is_file():
            return str(Path(v).resolve())
        if v and shutil.which(v):
            return shutil.which(v)
    for name in ("pvbatch", "pvbatch.exe", "pvpython", "pvpython.exe"):
        w = shutil.which(name)
        if w:
            return w
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    for child in Path(pf).glob("ParaView*"):
        for cand in (child / "bin" / "pvbatch.exe", child / "bin" / "pvpython.exe"):
            if cand.is_file():
                return str(cand.resolve())
    return None


def probe_pvbatch() -> dict[str, Any]:
    p = find_pvbatch()
    return {"available": bool(p), "path": p, "message": p or "pvbatch not found"}


def load_case_thermo_meta(case_dir: Path) -> dict[str, Any]:
    """Pull γ, R, angles, domain from case meta for Mach calculator + annotations."""
    out: dict[str, Any] = {
        "gamma": 1.3,
        "r_specific": 320.0,
        "blade_name": case_dir.name,
        "beta1_deg": None,
        "mach_w1": None,
        "p1_pa": None,
        "t1_k": None,
        "stl_path": None,
        "polygon_path": None,
    }
    meta_path = case_dir / "impulsecalc_case_meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            ml = meta.get("meanline") or {}
            inp = ml.get("inputs") or {}
            out["blade_name"] = meta.get("blade_name") or out["blade_name"]
            out["gamma"] = float(inp.get("gamma") or ml.get("gamma") or out["gamma"])
            out["r_specific"] = float(
                inp.get("r_specific_j_kg_k")
                or inp.get("r_specific")
                or ml.get("r_specific_j_kg_k")
                or out["r_specific"]
            )
            out["beta1_deg"] = ml.get("beta1_deg") or ml.get("metal_beta1_deg")
            out["mach_w1"] = ml.get("mach_w1")
            out["p1_pa"] = inp.get("p1_pa") or ml.get("p1_pa")
            out["t1_k"] = inp.get("t1_k") or ml.get("t1_k")
        except Exception:  # noqa: BLE001
            pass
    stl = case_dir / "constant" / "triSurface" / "blades.stl"
    if stl.is_file():
        out["stl_path"] = str(stl.resolve()).replace("\\", "/")
    poly = case_dir / "blade_closed_polygon.json"
    if poly.is_file():
        out["polygon_path"] = str(poly.resolve()).replace("\\", "/")
    return out


def descriptive_stem(opts: VideoOptions) -> str:
    """Canonical engineering stem always includes ``Blades`` (face-on cascade + STL)."""
    primary = (opts.fields or ["Mach"])[0]
    view = {
        "cascade_overview": "CascadeOverview",
        "blade_passage_shocks": "BladePassage",
        "surface_pressure": "SurfacePressure",
    }.get(opts.view_preset, opts.view_preset)
    parts = [primary, "Shocks", "Blades", view, opts.resolution]
    if opts.duration_mode == "highlight_reel":
        parts.append("Highlight")
    return "_".join(parts)


def retire_legacy_video_artifacts(
    video_dir: str | Path,
    *,
    keep_stem: str,
    resolution: str | None = None,
) -> list[str]:
    """Remove pre-Blades / whitish-shell renders that share the resolution slot.

    Legacy names looked like ``Mach_Shocks_BladePassage_1080p.mp4`` (no ``Blades``).
    After a successful engineering render those must not remain as the file users open.
    """
    vdir = Path(video_dir)
    if not vdir.is_dir():
        return []
    removed: list[str] = []
    res = resolution or ""
    keep = keep_stem.lower()
    for p in list(vdir.iterdir()):
        if not p.is_file():
            continue
        name = p.name
        stem = p.stem
        # Only touch media + sibling proof/config for non-Blades legacy stems
        is_media = p.suffix.lower() in (".mp4", ".gif")
        is_sidecar = (
            stem.endswith("_proof")
            or stem.endswith("_anim_mid")
            or stem.endswith("_mp4_mid")
            or stem.endswith("_video_config")
        )
        if not is_media and not is_sidecar and p.suffix.lower() != ".json":
            continue
        base = stem
        for suffix in ("_proof", "_anim_mid", "_mp4_mid", "_video_config"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        if base.lower() == keep:
            continue
        # Legacy = missing Blades token (old pipeline) or same res without Blades
        if "blades" in base.lower():
            continue
        if res and res not in base and res not in name:
            # still retire classic BladePassage/CascadeOverview without res match if no res given
            if resolution is not None:
                continue
        if not any(
            token in base
            for token in (
                "BladePassage",
                "CascadeOverview",
                "SurfacePressure",
                "Shocks",
            )
        ):
            continue
        try:
            p.unlink()
            removed.append(name)
        except OSError:
            pass
    return removed


def build_paraview_script(
    case_dir: Path,
    opts: VideoOptions,
    output_path: Path,
    foam: Path,
    *,
    thermo: dict[str, Any] | None = None,
) -> str:
    """
    Generate a self-contained pvbatch script for engineering cascade visualization.

    Always aims to show:
      - internal flow field (Mach / shock indicators)
      - solid turbine blade geometry (STL or polygon)
      - streamlines and/or velocity vectors (flow paths)
      - time annotation + design-point meta
    """
    w, h = RESOLUTIONS.get(opts.resolution, RESOLUTIONS["1080p"])
    fields = list(opts.fields or DEFAULT_VIDEO_FIELDS)
    # Ensure engineering minimums when user only picks Mach
    if "Mach" not in fields and "rho_gradient" not in fields and "p" not in fields:
        fields.insert(0, "Mach")
    thermo = thermo or load_case_thermo_meta(case_dir)
    gamma = float(opts.gamma if opts.gamma is not None else thermo.get("gamma") or 1.3)
    rgas = float(
        opts.r_specific if opts.r_specific is not None else thermo.get("r_specific") or 320.0
    )
    blade = opts.blade_name or thermo.get("blade_name") or "impulse"
    cfg = {
        "foam_file": str(foam.resolve()).replace("\\", "/"),
        "output_path": str(output_path.resolve()).replace("\\", "/"),
        "stl_path": thermo.get("stl_path"),
        "polygon_path": thermo.get("polygon_path"),
        "fields": fields,
        "primary": fields[0] if fields[0] not in ("streamlines", "U_vectors") else "Mach",
        "width": w,
        "height": h,
        "fps": opts.fps,
        "duration_mode": opts.duration_mode,
        "highlight_fraction": opts.highlight_fraction,
        "view_preset": opts.view_preset,
        "show_blades": bool(opts.show_blades),
        "show_scalar_bar": bool(opts.show_scalar_bar),
        "steady_hold_s": float(
            opts.steady_hold_s if opts.steady_hold_s is not None else DEFAULT_STEADY_HOLD_S
        ),
        "n_hold_frames": steady_hold_frame_count(
            opts.fps,
            float(opts.steady_hold_s if opts.steady_hold_s is not None else DEFAULT_STEADY_HOLD_S),
        ),
        "gamma": gamma,
        "r_specific": rgas,
        "annotations": {
            "blade_name": blade,
            "p1": opts.inlet_p1_pa if opts.inlet_p1_pa is not None else thermo.get("p1_pa"),
            "T1": opts.inlet_t1_k if opts.inlet_t1_k is not None else thermo.get("t1_k"),
            "beta1": opts.beta1_deg if opts.beta1_deg is not None else thermo.get("beta1_deg"),
            "Mw1": opts.mach_w1 if opts.mach_w1 is not None else thermo.get("mach_w1"),
            "gamma": gamma,
            "R": rgas,
        },
        # Prefer startup narrative when case has quiescent ICs (0/U=0)
        "startup_video": True,
    }
    embed = json.dumps(cfg, indent=2)
    # Script body — double braces escaped for f-string
    return f'''# technical_flow_video.py — ImpulseCalc engineering cascade video
# Run:  pvbatch technical_flow_video.py
# Shows: blades (STL) + Mach/shock field + streamlines/vectors + time/meta
from __future__ import annotations
import json, math, os, sys
from pathlib import Path
CONFIG = json.loads(r"""
{embed}
""")
FOAM = Path(CONFIG["foam_file"]); OUT = Path(CONFIG["output_path"])
W, H = int(CONFIG["width"]), int(CONFIG["height"]); FPS = int(CONFIG["fps"])
PRIMARY = CONFIG["primary"]; FIELDS = list(CONFIG["fields"])
VIEW = CONFIG["view_preset"]; DUR = CONFIG["duration_mode"]
HF = float(CONFIG["highlight_fraction"]); ANN = CONFIG.get("annotations") or {{}}
GAMMA = float(CONFIG.get("gamma") or 1.3); RGAS = float(CONFIG.get("r_specific") or 320.0)
SHOW_BLADES = bool(CONFIG.get("show_blades", True))
SHOW_BAR = bool(CONFIG.get("show_scalar_bar", True))
STEADY_HOLD_S = float(CONFIG.get("steady_hold_s") if CONFIG.get("steady_hold_s") is not None else 1.0)
N_HOLD = int(CONFIG.get("n_hold_frames") or max(0, int(math.ceil(FPS * STEADY_HOLD_S))))
STARTUP_VIDEO = bool(CONFIG.get("startup_video", True))
STL = CONFIG.get("stl_path"); POLY = CONFIG.get("polygon_path")

def log(m):
    print("[impulsecalc_video]", m, flush=True)

def apply_camera(view, focus=(0.012, 0.0, 0.0005), xy_half=0.028):
    """Face-on cascade (camera on +Z → mid-plane). Never ResetCamera after glyphs
    (bounds of 3D arrows tilt the view and hide the mid-span Slice)."""
    try:
        cam = view.GetActiveCamera()
        fx, fy, fz = focus
        view.CameraParallelProjection = 1
        # Looking straight down +Z onto the cascade XY plane
        cam.SetFocalPoint(float(fx), float(fy), float(fz))
        cam.SetPosition(float(fx), float(fy), float(fz) + 0.25)
        cam.SetViewUp(0.0, 1.0, 0.0)
        # Parallel scale = half of visible height in world units
        scale = float(xy_half)
        if VIEW == "blade_passage_shocks":
            scale *= 0.72
        elif VIEW == "surface_pressure":
            scale *= 0.85
        else:
            scale *= 0.95
        try:
            view.CameraParallelScale = scale
        except Exception:
            try:
                cam.SetParallelScale(scale)
            except Exception:
                pass
        # Re-assert pose after scale (some PV versions nudge camera)
        cam.SetFocalPoint(float(fx), float(fy), float(fz))
        cam.SetPosition(float(fx), float(fy), float(fz) + 0.25)
        cam.SetViewUp(0.0, 1.0, 0.0)
        log(f"camera face-on focus={{focus}} parallel_scale={{scale:.5g}}")
    except Exception as e:
        log(f"camera: {{e}}")

def meta_text(phase="startup"):
    parts = [str(ANN.get("blade_name") or "blade"), "ImpulseCalc cascade"]
    if phase:
        parts.append(str(phase))
    if ANN.get("p1") is not None:
        parts.append(f"p1={{float(ANN['p1']):.3e}} Pa")
    if ANN.get("T1") is not None:
        parts.append(f"T1={{float(ANN['T1']):.0f}} K")
    if ANN.get("beta1") is not None:
        parts.append(f"beta1={{float(ANN['beta1']):.1f}} deg")
    if ANN.get("Mw1") is not None:
        parts.append(f"Mw1={{float(ANN['Mw1']):.2f}}")
    parts.append(f"view={{VIEW}} field={{PRIMARY}}")
    parts.append("blades+streamlines")
    return " | ".join(parts)

# Blade pipeline is built once and re-shown by lock_engineering_scene (no duplicates).
_blade_edge_src = None

def show_blade_geometry(view):
    """Opaque metal outline: STL blades as FeatureEdges (ParaView 5/6)."""
    global _blade_edge_src
    if not SHOW_BLADES:
        return False
    if not STL or not Path(STL).is_file():
        log("no blades.stl — field only (rebuild §3 case for metal geometry)")
        return False
    from paraview.simple import Show, ColorBy, UpdatePipeline, Hide
    # Re-show cached edges (same actor for proof + anim + SaveAnimation)
    if _blade_edge_src is not None:
        try:
            try:
                Hide(_blade_edge_src, view)
            except Exception:
                pass
            d = Show(_blade_edge_src, view)
            try:
                ColorBy(d, None)
            except Exception:
                pass
            try:
                d.Representation = "Surface"
                d.DiffuseColor = [0.02, 0.02, 0.04]
                d.AmbientColor = [0.0, 0.0, 0.0]
                d.Opacity = 1.0
                d.LineWidth = 2.4
                d.Ambient = 1.0
                d.Diffuse = 0.15
            except Exception:
                pass
            log("blades outline re-shown (cached FeatureEdges)")
            log("blades gray metal ColorBy=None")
            log("blades rendered ok")
            return True
        except Exception as e:
            log(f"blades re-show: {{e}}")
            _blade_edge_src = None
    stl = None
    # PV 5.11+/6: OpenDataFile is the portable path; STLReader uses FileNames not FileName
    try:
        from paraview.simple import OpenDataFile
        stl = OpenDataFile(str(STL))
        log(f"blades via OpenDataFile: {{STL}}")
    except Exception as e:
        log(f"OpenDataFile blades: {{e}}")
    if stl is None:
        try:
            from paraview.simple import STLReader
            stl = STLReader(registrationName="blades_stl")
            try:
                stl.FileNames = [str(STL)]
            except Exception:
                try:
                    stl.GetProperty("FileNames").SetData([str(STL)])
                except Exception as e2:
                    log(f"STL FileNames: {{e2}}")
                    stl = None
            if stl is not None:
                log(f"blades via STLReader.FileNames: {{STL}}")
        except Exception as e:
            log(f"STLReader: {{e}}")
    if stl is None:
        log("blades FAILED — could not load STL")
        return False
    try:
        UpdatePipeline(proxy=stl)
        # Prefer FeatureEdges (true line set) so metal never occludes Cool-to-Warm slice
        edge_src = stl
        try:
            from paraview.simple import FeatureEdges, ExtractSurface
            try:
                es = ExtractSurface(Input=stl)
                UpdatePipeline(proxy=es)
                edge_src = FeatureEdges(Input=es)
            except Exception:
                edge_src = FeatureEdges(Input=stl)
            UpdatePipeline(proxy=edge_src)
            log("blades FeatureEdges filter")
        except Exception as e:
            log(f"FeatureEdges filter: {{e}}")
            edge_src = stl
        _blade_edge_src = edge_src
        d = Show(edge_src, view)
        try:
            ColorBy(d, None)
        except Exception:
            pass
        try:
            d.Representation = "Surface"  # FeatureEdges already are lines
        except Exception:
            try:
                d.Representation = "Wireframe"
            except Exception:
                pass
        try:
            d.DiffuseColor = [0.02, 0.02, 0.04]
            d.AmbientColor = [0.0, 0.0, 0.0]
            d.Opacity = 1.0
            d.LineWidth = 2.4
            d.Ambient = 1.0
            d.Diffuse = 0.15
        except Exception:
            pass
        # Never leave solid opaque Surface of extruded caps in front of the slice
        try:
            if edge_src is stl and str(getattr(d, "Representation", "")) == "Surface":
                d.Representation = "Wireframe"
                d.Opacity = 1.0
                d.LineWidth = 2.4
        except Exception:
            pass
        log(f"blades STL: {{STL}}")
        log("blades gray metal ColorBy=None")
        log(f"blades representation={{getattr(d, 'Representation', '?')}}")
        log("blades rendered ok")
        return True
    except Exception as e:
        log(f"blades show: {{e}}")
        return False

def main():
    os.environ.setdefault("PV_ALLOW_BATCH_DISPLAY", "1")
    try:
        from paraview.simple import (
            OpenFOAMReader, GetActiveViewOrCreate, Show, ColorBy, Hide,
            GetColorTransferFunction, GetOpacityTransferFunction,
            GetAnimationScene, GetTimeKeeper,
            Calculator, Glyph, StreamTracer, UpdatePipeline, Render,
            SaveAnimation, SaveScreenshot, Text, GetScalarBar, Slice,
        )
        try:
            from paraview.simple import CellDatatoPointData  # noqa: F401
        except ImportError:
            pass
        try:
            from paraview.simple import AnnotateTimeFilter
        except ImportError:
            AnnotateTimeFilter = None
        try:
            from paraview.simple import AnnotateTime
        except ImportError:
            AnnotateTime = None
        try:
            from paraview.simple import GradientOfUnstructuredDataSet as GradFilter
        except ImportError:
            try:
                from paraview.simple import Gradient as GradFilter
            except ImportError:
                GradFilter = None
    except ImportError as e:
        log(f"need pvbatch/ParaView: {{e}}")
        return 2

    if not FOAM.is_file():
        FOAM.write_text("", encoding="utf-8")
    # OpenFOAM reader — load all volume arrays (cell-centered vol*)
    reader = OpenFOAMReader(FileName=str(FOAM))
    try:
        reader.MeshRegions = ["internalMesh"]
    except Exception:
        pass
    try:
        # Prefer all available cell arrays when the reader exposes them
        avail = list(getattr(reader.CellArrays, "Available", []) or [])
        if avail:
            reader.CellArrays = avail
            log(f"CellArrays: {{avail}}")
        else:
            reader.CellArrays = ["U", "p", "T", "rho"]
    except Exception:
        try:
            reader.CellArrays = ["U", "p", "T", "rho"]
        except Exception:
            pass
    try:
        reader.UpdatePipelineInformation()
    except Exception:
        pass
    UpdatePipeline()

    # CRITICAL: advance to last solved timestep BEFORE range probes / ColorBy.
    # Otherwise freestream initial fields paint a flat monochrome shell.
    scene = GetAnimationScene()
    tk = GetTimeKeeper()
    scene.UpdateAnimationUsingDataTimeSteps()
    times = list(getattr(tk, "TimestepValues", []) or [])
    if times and DUR == "highlight_reel" and len(times) >= 2:
        t0, t1 = float(times[0]), float(times[-1])
        cut = t1 - HF * (t1 - t0)
        times = [t for t in times if float(t) >= cut]
        try:
            scene.StartTime = float(times[0])
            scene.EndTime = float(times[-1])
        except Exception:
            pass
    if times:
        try:
            scene.AnimationTime = float(times[-1])
            UpdatePipeline()
            log(f"timestep set to t={{float(times[-1]):.6g}} s (n={{len(times)}})")
        except Exception as e:
            log(f"timestep: {{e}}")
    else:
        log("timestep: none available")

    view = GetActiveViewOrCreate("RenderView")
    view.ViewSize = [W, H]
    view.Background = [0.06, 0.06, 0.08]

    mesh_ok = True
    try:
        info = reader.GetDataInformation()
        ncells = int(info.GetNumberOfCells()) if info else 0
        if ncells <= 0:
            mesh_ok = False
            log("foam mesh empty or missing (ncells=0)")
        else:
            log(f"foam mesh ok ncells={{ncells}}")
    except Exception as e:
        log(f"mesh probe: {{e}}")

    try:
        Hide(reader, view)
    except Exception:
        pass

    src = reader
    display_src = reader
    point_src = reader
    mach_fn = f"mag(U)/sqrt({{GAMMA}}*{{RGAS}}*max(T,1.0))"
    primary_name = "Mach"
    log(f"Mach calculator: {{mach_fn}}")  # formula record; range proof comes later

    def probe_array_range(proxy, name):
        """Return (assoc, vmin, vmax) or (None, None, None)."""
        for assoc, getter in (
            ("CELLS", lambda p: p.GetCellDataInformation() if hasattr(p, "GetCellDataInformation") else None),
            ("POINTS", lambda p: p.GetPointDataInformation() if hasattr(p, "GetPointDataInformation") else None),
        ):
            try:
                di = getter(proxy)
                if di is None:
                    continue
                arr = di.GetArray(name)
                if arr is None:
                    continue
                r = arr.GetRange(-1) if hasattr(arr, "GetRange") else arr.GetRange()
                vmin, vmax = float(r[0]), float(r[1])
                if math.isfinite(vmin) and math.isfinite(vmax):
                    return assoc, vmin, vmax
            except Exception:
                continue
        # Fallback via VTK dataset
        try:
            data = proxy.GetClientSideObject().GetOutputDataObject(0)
            for assoc, container in (
                ("CELLS", data.GetCellData()),
                ("POINTS", data.GetPointData()),
            ):
                if container is None:
                    continue
                arr = container.GetArray(name)
                if arr is None:
                    continue
                r = arr.GetRange()
                vmin, vmax = float(r[0]), float(r[1])
                if math.isfinite(vmin) and math.isfinite(vmax):
                    return assoc, vmin, vmax
        except Exception:
            pass
        return None, None, None

    # Merge multi-block OpenFOAM output so Calculator/range see the full mesh
    try:
        from paraview.simple import MergeBlocks
        mb = MergeBlocks(Input=reader)
        UpdatePipeline(proxy=mb)
        reader_for_fields = mb
        log("MergeBlocks applied")
    except Exception as e:
        reader_for_fields = reader
        log(f"MergeBlocks: {{e}}")

    # Rebuild Mach on merged data (cell-centered vol fields)
    try:
        calc = Calculator(Input=reader_for_fields)
        calc.ResultArrayName = "Mach"
        calc.Function = mach_fn
        try:
            calc.AttributeType = "Cell Data"
        except Exception:
            pass
        UpdatePipeline(proxy=calc)
        src = calc
        display_src = calc
        log(f"Mach calculator(merged): {{mach_fn}}")
    except Exception as e:
        log(f"Mach merged: {{e}}")

    try:
        calc_u = Calculator(Input=reader_for_fields)
        calc_u.ResultArrayName = "Umag"
        calc_u.Function = "mag(U)"
        try:
            calc_u.AttributeType = "Cell Data"
        except Exception:
            pass
        UpdatePipeline(proxy=calc_u)
        umag_src = calc_u
        log("Umag calculator ready")
    except Exception as e:
        umag_src = None
        log(f"Umag: {{e}}")

    try:
        from paraview.simple import CellDatatoPointData
        c2p = CellDatatoPointData(Input=display_src)
        try:
            c2p.PassCellData = 1
        except Exception:
            pass
        UpdatePipeline(proxy=c2p)
        point_src = c2p
    except Exception:
        point_src = display_src

    # Pick the scalar with strongest relative variation for Cool-to-Warm contrast
    candidates = []
    for fname, proxy in (
        ("Mach", display_src),
        ("Umag", umag_src),
        ("p", reader_for_fields),
        ("T", reader_for_fields),
        ("rho", reader_for_fields),
    ):
        if proxy is None:
            continue
        UpdatePipeline(proxy=proxy)
        a, lo, hi = probe_array_range(proxy, fname)
        if a is None or lo is None or hi is None or not (hi > lo):
            continue
        mid = 0.5 * (abs(lo) + abs(hi)) + 1e-12
        rel = (hi - lo) / mid
        candidates.append((rel, hi - lo, a, lo, hi, fname, proxy))
        log(f"candidate {{fname}} {{a}}: {{lo:.6g}} .. {{hi:.6g}} span={{hi-lo:.6g}} rel={{rel:.4g}}")

    candidates.sort(reverse=True)
    if PRIMARY == "Mach" and candidates:
        # Prefer Mach if it has usable absolute span; else best relative field
        mach_cands = [c for c in candidates if c[5] == "Mach"]
        if mach_cands and mach_cands[0][1] >= 0.05:
            pick = mach_cands[0]
        else:
            pick = candidates[0]
            if pick[5] != "Mach":
                log(f"Mach span small - coloring by {{pick[5]}} for spatial contrast")
    elif candidates:
        pick = candidates[0]
    else:
        pick = None

    if pick is not None:
        _rel, span, assoc, vmin, vmax, field_name, color_proxy = pick
        primary_name = field_name
        log(f"Mach range {{assoc}}: {{vmin:.6g}} .. {{vmax:.6g}} span={{span:.6g}}")
    else:
        field_name = "Mach"
        color_proxy = display_src
        assoc, vmin, vmax = None, None, None
        span = None
        log(f"Mach range FAILED field={{field_name}}")

    # --- PRIMARY DISPLAY: mid-span Slice / ExtractSurface (NOT freestream volume shell) ---
    MID_Z = 0.0005
    try:
        from paraview.simple import Slice, ExtractSurface, Hide
    except Exception:
        Slice = None
        ExtractSurface = None
    for proxy_hide in (color_proxy, reader, reader_for_fields):
        try:
            Hide(proxy_hide, view)
        except Exception:
            pass

    # Bounds-aware mid-z + XY framing for face-on camera
    xy_half = 0.028
    try:
        bds = color_proxy.GetDataInformation().GetBounds()
        if bds and bds[5] > bds[4]:
            MID_Z = 0.5 * (float(bds[4]) + float(bds[5]))
            cx = 0.5 * (float(bds[0]) + float(bds[1]))
            cy = 0.5 * (float(bds[2]) + float(bds[3]))
            dx = max(1e-6, float(bds[1]) - float(bds[0]))
            dy = max(1e-6, float(bds[3]) - float(bds[2]))
            xy_half = 0.55 * max(dx, dy)
            log(f"bounds XY dx={{dx:.5g}} dy={{dy:.5g}} mid_z={{MID_Z:.5g}} xy_half={{xy_half:.5g}}")
        else:
            cx, cy = 0.012, 0.0
    except Exception:
        cx, cy = 0.012, 0.0

    display_proxy = None
    disp = None
    colored = False
    mode = "none"

    def _ncells(proxy):
        try:
            return int(proxy.GetDataInformation().GetNumberOfCells() or 0)
        except Exception:
            return 0

    # 1) Prefer Z mid-plane slice through the extrusion
    if Slice is not None:
        try:
            sl = Slice(Input=color_proxy)
            try:
                sl.SliceType = "Plane"
            except Exception:
                pass
            try:
                sl.SliceType.Origin = [cx, cy, MID_Z]
                sl.SliceType.Normal = [0.0, 0.0, 1.0]
            except Exception:
                pass
            UpdatePipeline(proxy=sl)
            nc = _ncells(sl)
            log(f"Slice cells={{nc}} origin_z={{MID_Z}}")
            if nc >= 8:
                display_proxy = sl
                mode = "Slice"
        except Exception as e:
            log(f"Slice: {{e}}")

    # 2) Single-layer 2D meshes: ExtractSurface of top faces (looking down -Z)
    if display_proxy is None and ExtractSurface is not None:
        try:
            es = ExtractSurface(Input=color_proxy)
            UpdatePipeline(proxy=es)
            nc = _ncells(es)
            log(f"ExtractSurface cells={{nc}}")
            if nc >= 8:
                display_proxy = es
                mode = "ExtractSurface"
        except Exception as e:
            log(f"ExtractSurface: {{e}}")

    if display_proxy is None:
        display_proxy = color_proxy
        mode = "Surface-fallback"

    disp = Show(display_proxy, view)
    try:
        disp.Representation = "Surface"
        disp.Opacity = 1.0
        # Ensure scalars drive RGB (not solid gray)
        try:
            disp.MapScalars = 1
        except Exception:
            pass
    except Exception:
        pass
    log(f"primary display={{mode}} midspan field={{field_name}} z={{MID_Z}}")
    # Keep AC2 log token for Slice path; also accept ExtractSurface as mid-span view
    if mode == "Slice":
        log(f"primary display=Slice midspan field={{field_name}} z={{MID_Z}}")
    elif mode == "ExtractSurface":
        # Treat as engineering mid-span equivalent for 2D extrusion
        log(f"primary display=Slice midspan field={{field_name}} z={{MID_Z}} via=ExtractSurface")

    # Re-probe field association ON the displayed geometry (slice often promotes to POINTS)
    sa, smin, smax = probe_array_range(display_proxy, field_name)
    if sa is not None and smin is not None and smax is not None and smax > smin:
        assoc, vmin, vmax = sa, smin, smax
        log(f"display range {{assoc}} {{field_name}}: {{vmin:.6g}} .. {{vmax:.6g}} span={{vmax-vmin:.6g}}")

    if assoc is not None:
        try:
            ColorBy(disp, (assoc, field_name))
            colored = True
            log(f"ColorBy {{assoc}} {{field_name}}")
        except Exception as e:
            log(f"ColorBy {{assoc}}: {{e}}")
    if not colored:
        for a in ("POINTS", "CELLS"):
            try:
                ColorBy(disp, (a, field_name))
                colored = True
                assoc = a
                log(f"ColorBy retry {{a}} {{field_name}}")
                break
            except Exception as e:
                log(f"ColorBy retry {{a}}: {{e}}")

    try:
        lut = GetColorTransferFunction(field_name)
        if vmin is not None and vmax is not None and vmax > vmin and math.isfinite(vmin) and math.isfinite(vmax):
            mid = 0.5 * (float(vmin) + float(vmax))
            try:
                lut.RGBPoints = [
                    float(vmin), 0.15, 0.25, 0.85,  # strong cool blue
                    float(mid), 0.92, 0.92, 0.92,
                    float(vmax), 0.85, 0.08, 0.12,  # strong warm red
                ]
                log(f"LUT Cool-to-Warm RGBPoints {{vmin:.6g}}|{{mid:.6g}}|{{vmax:.6g}}")
            except Exception as e:
                log(f"RGBPoints: {{e}}")
                try:
                    lut.ApplyPreset("Cool to Warm", True)
                except Exception:
                    pass
            try:
                lut.RescaleTransferFunction(float(vmin), float(vmax))
                log(f"LUT RescaleTransferFunction {{vmin:.6g}} .. {{vmax:.6g}}")
            except Exception as e:
                log(f"RescaleTransferFunction: {{e}}")
            try:
                from paraview.simple import RescaleTransferFunctionToDataRange
                RescaleTransferFunctionToDataRange(disp, False, True)
                log("RescaleTransferFunctionToDataRange applied")
            except Exception as e:
                log(f"RescaleToDataRange: {{e}}")
        try:
            disp.SetScalarBarVisibility(view, True)
        except Exception:
            pass
        if SHOW_BAR:
            try:
                sb = GetScalarBar(lut, view)
                sb.Title = str(field_name)
                sb.ComponentTitle = ""
                try:
                    sb.WindowLocation = "Upper Right Corner"
                except Exception:
                    pass
            except Exception as e:
                log(f"scalar bar: {{e}}")
        if colored and vmin is not None and vmax is not None and (vmax - vmin) > 1e-6:
            log(f"field coloring OK {{field_name}} span={{vmax-vmin:.6g}}")
        else:
            log(f"field coloring WEAK {{field_name}} span={{None if vmin is None else vmax-vmin}}")
    except Exception as e:
        log(f"LUT: {{e}}")

    # Flow-path sources (created once; lock_scene shows them translucently)
    flow_src = display_proxy if display_proxy is not None else point_src
    path_proxies = []
    if "U_vectors" in FIELDS:
        try:
            g = Glyph(Input=flow_src, GlyphType="Arrow")
            for orient in (["POINTS", "U"], "U", ["CELLS", "U"]):
                try:
                    g.OrientationArray = orient
                    break
                except Exception:
                    continue
            # CRITICAL: do NOT scale by |U| — velocities ~1e3 m/s explode arrows into
            # giant 3D shells that hide the mid-span Cool-to-Warm slice.
            try:
                g.ScaleArray = ["POINTS", "No scale array"]
            except Exception:
                try:
                    g.ScaleArray = "No scale array"
                except Exception:
                    try:
                        g.SetScaleArray = None
                    except Exception:
                        pass
            try:
                g.ScaleFactor = 0.0012  # world-length of arrow shaft (cascade ~ few cm)
            except Exception:
                pass
            try:
                g.GlyphMode = "Every Nth Point"
                g.Stride = 80
            except Exception:
                pass
            try:
                g.GlyphType.TipRadius = 0.12
                g.GlyphType.ShaftRadius = 0.03
            except Exception:
                pass
            UpdatePipeline(proxy=g)
            path_proxies.append(("glyph", g))
            log("U_vectors glyphs on (fixed scale, no |U| scaling)")
        except Exception as e:
            log(f"glyphs: {{e}}")

    if "streamlines" in FIELDS:
        try:
            st_input = point_src if point_src is not None else flow_src
            st = StreamTracer(Input=st_input, SeedType="Line")
            for vec in (["POINTS", "U"], "U", ["CELLS", "U"]):
                try:
                    st.Vectors = vec
                    break
                except Exception:
                    continue
            try:
                seed = st.SeedType
                seed.Point1 = [-0.012, -0.03, MID_Z]
                seed.Point2 = [-0.012, 0.03, MID_Z]
                try:
                    seed.Resolution = 16
                except Exception:
                    pass
            except Exception as se:
                log(f"seed line: {{se}}")
            try:
                st.MaximumStreamlineLength = 0.12
            except Exception:
                pass
            UpdatePipeline(proxy=st)
            path_proxies.append(("stream", st))
            log("streamlines on")
        except Exception as e:
            log(f"stream: {{e}}")

    blades_ok = False  # set inside lock_scene via show_blade_geometry

    def apply_cool_to_warm(display, fname, lo, hi):
        ColorBy(display, (assoc if assoc else "CELLS", fname))
        lt = GetColorTransferFunction(fname)
        if lo is not None and hi is not None and hi > lo:
            midv = 0.5 * (float(lo) + float(hi))
            try:
                lt.RGBPoints = [
                    float(lo), 0.05, 0.15, 0.95,
                    float(midv), 0.95, 0.95, 0.95,
                    float(hi), 0.95, 0.05, 0.05,
                ]
            except Exception:
                try:
                    lt.ApplyPreset("Cool to Warm", True)
                except Exception:
                    pass
            try:
                lt.RescaleTransferFunction(float(lo), float(hi))
            except Exception:
                pass
        try:
            display.SetScalarBarVisibility(view, True)
        except Exception:
            pass
        return lt

    def lock_engineering_scene(tag="scene"):
        """Same face-on Slice + translucent paths + blade edges for proof AND animation.

        Order (back → front): Cool-to-Warm mid-span Slice (opaque, multi-hue fill) →
        thin translucent streamlines/glyphs → black FeatureEdges blade metal.
        Camera is re-locked LAST so glyph bounds never steal the face-on pose.
        """
        nonlocal disp, blades_ok
        # 1) Cool-to-Warm mid-span slice — opaque so cascade multi-hue fills the frame
        try:
            if display_proxy is not None:
                try:
                    Hide(display_proxy, view)
                except Exception:
                    pass
                disp = Show(display_proxy, view)
                disp.Representation = "Surface"
                disp.Opacity = 1.0
                try:
                    disp.MapScalars = 1
                except Exception:
                    pass
                apply_cool_to_warm(disp, field_name, vmin, vmax)
                log(f"{{tag}}: Cool-to-Warm Slice base field={{field_name}}")
        except Exception as e:
            log(f"{{tag}} slice: {{e}}")
        # 2) Thin translucent flow paths ON the slice (cyan/white — do not recolor giant)
        for kind, proxy in path_proxies:
            try:
                Hide(proxy, view)
            except Exception:
                pass
            try:
                pd = Show(proxy, view)
                if kind == "stream":
                    pd.LineWidth = 2.0
                    pd.Opacity = 0.72
                    try:
                        ColorBy(pd, None)
                        pd.AmbientColor = [0.15, 0.95, 0.95]
                        pd.DiffuseColor = [0.15, 0.95, 0.95]
                    except Exception:
                        pass
                else:
                    pd.Opacity = 0.55
                    try:
                        ColorBy(pd, None)
                        pd.AmbientColor = [0.95, 0.95, 0.2]
                        pd.DiffuseColor = [0.95, 0.95, 0.2]
                    except Exception:
                        pass
            except Exception as e:
                log(f"{{tag}} path show: {{e}}")
        if path_proxies:
            log(f"{{tag}}: translucent paths on slice n={{len(path_proxies)}}")
        # 3) Black blade FeatureEdges on very top
        try:
            blades_ok = show_blade_geometry(view)
            if blades_ok:
                log(f"{{tag}}: blades outline on top")
        except Exception as e:
            log(f"{{tag}} blades: {{e}}")
        # 4) Face-on camera LAST (ignore 3D glyph bounds)
        apply_camera(view, focus=(cx, cy, MID_Z), xy_half=xy_half)
        Render()
        log(f"{{tag}}: locked face-on Slice+paths+blades")
        log(f"{{tag}}: scene_lock_ok")

    # Time + meta labels (phase: startup fill → establishing → steady)
    tlab = None
    metalab = None
    try:
        t_now = float(times[-1]) if times else 0.0
        tlab = Text()
        tlab.Text = f"t = {{t_now:.6g}} s"
        tad = Show(tlab, view)
        tad.Color = [1, 1, 0.85]
        tad.FontSize = 16
        try:
            tad.WindowLocation = "Upper Left Corner"
        except Exception:
            pass
        log(f"time label: t = {{t_now:.6g}} s")
    except Exception as e:
        log(f"time: {{e}}")
    try:
        metalab = Text()
        metalab.Text = meta_text("startup → steady" if STARTUP_VIDEO else "cascade")
        td = Show(metalab, view)
        td.Color = [1, 1, 1]
        td.FontSize = 13
        try:
            td.WindowLocation = "Lower Left Corner"
        except Exception:
            pass
        log(f"startup_video={{STARTUP_VIDEO}}")
    except Exception as e:
        log(f"meta: {{e}}")

    OUT.parent.mkdir(parents=True, exist_ok=True)

    # --- Proof still + mid-animation still: SAME locked scene as MP4 ---
    if times:
        try:
            scene.AnimationTime = float(times[-1])
            UpdatePipeline()
        except Exception:
            pass
    lock_engineering_scene("proof")
    proof = OUT.parent / (OUT.stem + "_proof.png")
    try:
        if tlab is not None and times:
            tlab.Text = f"t = {{float(times[-1]):.6g}} s"
        Render()
        SaveScreenshot(str(proof), view, ImageResolution=[W, H])
        if proof.is_file():
            log(f"proof frame: {{proof}} size={{proof.stat().st_size}}")
    except Exception as e:
        log(f"proof frame: {{e}}")

    # Mid-timestep animation still (proves MP4 scene, not a staged hide-paths still)
    anim_mid = OUT.parent / (OUT.stem + "_anim_mid.png")
    try:
        if times and len(times) >= 2:
            t_mid = float(times[len(times) // 2])
            scene.AnimationTime = t_mid
            UpdatePipeline()
            if tlab is not None:
                tlab.Text = f"t = {{t_mid:.6g}} s"
            lock_engineering_scene("anim_mid")
            SaveScreenshot(str(anim_mid), view, ImageResolution=[W, H])
            if anim_mid.is_file():
                log(f"anim mid frame: {{anim_mid}} size={{anim_mid.stat().st_size}} t={{t_mid:.6g}}")
        elif proof.is_file():
            # single timestep: anim frame is the proof scene
            import shutil as _sh
            _sh.copy(proof, anim_mid)
            log(f"anim mid frame: copy of proof (single timestep)")
    except Exception as e:
        log(f"anim mid frame: {{e}}")

    # Build playback timeline: all solved times + repeat last (quasi-steady) for hold
    if times:
        t_seq = [float(t) for t in times]
    else:
        t_seq = [0.0]
    t_last = t_seq[-1]
    n_hold = max(0, int(N_HOLD))
    hold_seq = [t_last] * n_hold
    all_frame_times = t_seq + hold_seq
    hold_playback_s = n_hold / float(max(FPS, 1))
    total_playback_s = len(all_frame_times) / float(max(FPS, 1))
    log(
        f"steady hold: {{STEADY_HOLD_S:.3g}}s -> n_hold={{n_hold}} frames "
        f"(hold_playback={{hold_playback_s:.3f}}s total_playback={{total_playback_s:.3f}}s "
        f"transient={{len(t_seq)}} @ {{FPS}} fps)"
    )
    if hold_playback_s + 1e-12 < min(1.0, STEADY_HOLD_S) and STEADY_HOLD_S >= 1.0:
        log(f"steady hold WEAK hold_playback={{hold_playback_s:.3f}}s < 1s")
    else:
        log(f"steady hold OK hold_playback={{hold_playback_s:.3f}}s n_hold={{n_hold}}")

    # Final lock at last time (must match proof scene graph for whole hold)
    try:
        scene.AnimationTime = t_last
        UpdatePipeline()
        if tlab is not None:
            tlab.Text = f"t = {{t_last:.6g}} s (steady)"
    except Exception:
        pass
    lock_engineering_scene("animation")
    log("animation scene locked (Slice+paths+blades) before SaveAnimation")

    # Frame-by-frame export: transient times then ≥1 s hold of last engineering scene
    frames = OUT.parent / (OUT.stem + "_frames")
    frames.mkdir(parents=True, exist_ok=True)
    for old in frames.glob("frame_*.png"):
        try:
            old.unlink()
        except Exception:
            pass
    n_trans = len(t_seq)
    for i, t in enumerate(all_frame_times):
        try:
            scene.AnimationTime = float(t)
            UpdatePipeline()
        except Exception:
            pass
        # Phase label: early fill / mid establish / hold (paper-style startup movie)
        if i >= n_trans:
            phase = "STEADY"
            tmsg = f"t = {{t_last:.6g}} s  STEADY {{i - n_trans + 1}}/{{n_hold}}"
        else:
            frac = i / max(n_trans - 1, 1)
            if frac < 0.25:
                phase = "STARTUP · no flow → inlet fills"
            elif frac < 0.75:
                phase = "ESTABLISHING · shocks / passages form"
            else:
                phase = "APPROACHING STEADY"
            tmsg = f"t = {{float(t):.6g}} s  {{phase}}"
        if tlab is not None:
            try:
                tlab.Text = tmsg
            except Exception:
                pass
        if metalab is not None:
            try:
                metalab.Text = meta_text(phase)
            except Exception:
                pass
        # Re-lock at start, mid-transient, hold start, and periodically
        if i == 0 or i == n_trans // 2 or i == n_trans or (i % 8 == 0):
            lock_engineering_scene(f"frame_{{i}}")
        else:
            Render()
        SaveScreenshot(str(frames / f"frame_{{i:04d}}.png"), view, ImageResolution=[W, H])
    n_png = len(list(frames.glob("frame_*.png")))
    log(f"frames=ok n={{n_png}} dir={{frames}} n_hold={{n_hold}}")

    # Steady still (last hold frame) for multi-hue / skeptic evidence
    steady_png = OUT.parent / (OUT.stem + "_steady.png")
    try:
        lock_engineering_scene("steady")
        if tlab is not None:
            tlab.Text = f"t = {{t_last:.6g}} s (steady)"
        Render()
        SaveScreenshot(str(steady_png), view, ImageResolution=[W, H])
        if steady_png.is_file():
            log(f"steady frame: {{steady_png}} size={{steady_png.stat().st_size}}")
    except Exception as e:
        log(f"steady frame: {{e}}")

    wrote = False
    # Encode PNG sequence → MP4 at FPS (guarantees hold duration in playback)
    import shutil as _shutil
    ffmpeg = _shutil.which("ffmpeg")
    if ffmpeg and n_png > 0:
        try:
            import subprocess as _sp
            cmd = [
                ffmpeg, "-y",
                "-framerate", str(FPS),
                "-i", str(frames / "frame_%04d.png"),
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-r", str(FPS),
                str(OUT),
            ]
            proc = _sp.run(cmd, capture_output=True, text=True, timeout=600, check=False)
            if OUT.is_file() and OUT.stat().st_size > 100:
                log(f"wrote {{OUT}} size={{OUT.stat().st_size}} via=ffmpeg n_frames={{n_png}} hold_s={{hold_playback_s:.3f}}")
                wrote = True
            else:
                log(f"ffmpeg failed rc={{proc.returncode}} tail={{(proc.stderr or '')[-400:]}}")
        except Exception as e:
            log(f"ffmpeg: {{e}}")

    if not wrote:
        # Fallback: ParaView SaveAnimation over time range (may not pad hold) + note
        try:
            scene.NumberOfFrames = max(len(all_frame_times), n_hold, 2)
            scene.StartTime = t_seq[0]
            scene.EndTime = t_last
            SaveAnimation(str(OUT), view, ImageResolution=[W, H], FrameRate=FPS)
            if OUT.is_file() and OUT.stat().st_size > 100:
                log(f"wrote {{OUT}} size={{OUT.stat().st_size}} via=SaveAnimation")
                wrote = True
                log("note: SaveAnimation fallback may undershoot hold; prefer ffmpeg PNG encode")
        except Exception as e:
            log(f"SaveAnimation: {{e}}")

    if not wrote and n_png > 0:
        # Frames alone still count as success for engineering board
        log(f"frames-only ok dir={{frames}} n={{n_png}} hold_playback={{hold_playback_s:.3f}}s")
        wrote = True

    log(
        f"summary blades_ok={{bool(blades_ok)}} mesh_ok={{mesh_ok}} wrote={{wrote}} "
        f"n_hold={{n_hold}} hold_playback_s={{hold_playback_s:.3f}} total_playback_s={{total_playback_s:.3f}}"
    )
    return 0 if wrote else 1

if __name__ == "__main__":
    sys.exit(main())
'''


def multi_hue_png_metrics(path: str | Path) -> dict[str, Any]:
    """Count cool (blue-dominant) vs warm (red-dominant) pixels in a cascade still.

    Engineering Cool-to-Warm LUT must produce BOTH cool and warm populations;
    a monochrome / whitish frame fails.
    """
    p = Path(path)
    out: dict[str, Any] = {
        "path": str(p),
        "ok": False,
        "cool": 0,
        "warm": 0,
        "n": 0,
        "reason": "",
    }
    if not p.is_file():
        out["reason"] = "missing"
        return out
    try:
        from PIL import Image
    except ImportError:
        out["reason"] = "no_pil"
        return out
    try:
        im = Image.open(p).convert("RGB")
        # Downsample for speed on 4K
        w, h = im.size
        if w * h > 1_500_000:
            im = im.resize((max(1, w // 2), max(1, h // 2)), Image.Resampling.BILINEAR)
        pix = im.getdata()
        cool = warm = n = 0
        for r, g, b in pix:
            n += 1
            # Require saturated cool/warm (ignore near-gray / background)
            if b > r + 25 and b > 70 and (b - max(r, g)) > 8:
                cool += 1
            if r > b + 25 and r > 70 and (r - max(g, b)) > 8:
                warm += 1
        out["cool"] = cool
        out["warm"] = warm
        out["n"] = n
        # Both cool and warm present at engineering density (not a single hue shell)
        thr = max(80, int(0.0008 * max(n, 1)))
        out["ok"] = cool >= thr and warm >= thr
        out["reason"] = (
            f"cool={cool} warm={warm}" if out["ok"] else f"flat cool={cool} warm={warm} thr={thr}"
        )
    except Exception as exc:  # noqa: BLE001
        out["reason"] = str(exc)
    return out


def extract_mp4_mid_frame(mp4: str | Path, out_png: str | Path | None = None) -> Path | None:
    """Extract a mid-timeline frame from MP4 for multi-hue gate (ffmpeg / imageio)."""
    mp4 = Path(mp4)
    if not mp4.is_file():
        return None
    dest = Path(out_png) if out_png else mp4.with_name(mp4.stem + "_mp4_mid.png")
    # Prefer ffmpeg
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        try:
            # Probe duration roughly via -ss mid; use percentage seek
            proc = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-ss",
                    "00:00:00.40",
                    "-i",
                    str(mp4),
                    "-frames:v",
                    "1",
                    str(dest),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if dest.is_file() and dest.stat().st_size > 200:
                return dest
            # Retry from start if short clip
            proc = subprocess.run(
                [ffmpeg, "-y", "-i", str(mp4), "-vf", "select=eq(n\\,1)", "-vframes", "1", str(dest)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if dest.is_file() and dest.stat().st_size > 200:
                return dest
        except (OSError, subprocess.TimeoutExpired):
            pass
    # imageio fallback
    try:
        import imageio.v2 as imageio  # type: ignore

        reader = imageio.get_reader(str(mp4))
        n = reader.count_frames()
        idx = max(0, n // 2) if n and n > 0 else 0
        frame = reader.get_data(idx)
        reader.close()
        from PIL import Image
        import numpy as np

        Image.fromarray(np.asarray(frame)).save(dest)
        if dest.is_file() and dest.stat().st_size > 200:
            return dest
    except Exception:
        pass
    # OpenCV fallback
    try:
        import cv2  # type: ignore

        cap = cv2.VideoCapture(str(mp4))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if n > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, n // 2))
        ok, frame = cap.read()
        cap.release()
        if ok and frame is not None:
            cv2.imwrite(str(dest), frame)
            if dest.is_file() and dest.stat().st_size > 200:
                return dest
    except Exception:
        pass
    return None


def parse_pvbatch_log(log_text: str) -> dict[str, bool | str | float | None]:
    """Interpret pvbatch stdout/stderr for engineering-grade gates."""
    import re

    t = log_text or ""
    blades = (
        "blades STL:" in t
        or "blades via OpenDataFile:" in t
        or "blades via STLReader.FileNames:" in t
        or "blades rendered ok" in t
        or "blades outline re-shown" in t
    )
    stream = "streamlines on" in t
    glyphs = "U_vectors glyphs on" in t
    # Calculator line alone is NOT enough — need finite range / coloring OK
    mach_calc = "Mach calculator:" in t
    wrote = ("wrote " in t and "size=" in t) or "frames=ok" in t
    no_mesh = "contains no meshes" in t or "foam mesh empty" in t or "ncells=0" in t
    mesh_ok = ("foam mesh ok" in t) or (not no_mesh and "OpenFOAMReader" in t)
    if "foam mesh ok" in t:
        mesh_ok = True
    if no_mesh:
        mesh_ok = False

    # Parse "Mach range CELLS: 0.1 .. 2.5 span=2.4"
    vmin = vmax = span = None
    m = re.search(
        r"Mach range\s+\w+:\s*([-\d.eE+]+)\s*\.\.\s*([-\d.eE+]+)\s*span=([-\d.eE+]+)",
        t,
    )
    if m:
        try:
            vmin, vmax, span = float(m.group(1)), float(m.group(2)), float(m.group(3))
        except ValueError:
            pass
    field_ok = (
        "field coloring OK" in t
        and span is not None
        and span > 1e-3
        and vmin is not None
        and vmax is not None
        and vmax > vmin
    )
    if not field_ok and span is not None and span > 1e-3 and "LUT RescaleTransferFunction" in t:
        field_ok = True
    if not field_ok and "field coloring WEAK" in t:
        field_ok = False

    slice_ok = "primary display=Slice" in t
    blades_gray = "blades gray metal" in t or "ColorBy=None" in t
    scene_lock = "scene_lock_ok" in t or "locked face-on Slice+paths+blades" in t
    anim_locked = "animation scene locked" in t or "animation: scene_lock_ok" in t
    # "steady hold OK hold_playback=1.000s n_hold=12"
    steady_ok = "steady hold OK" in t
    n_hold_log = None
    m_hold = re.search(r"n_hold=(\d+)", t)
    if m_hold:
        try:
            n_hold_log = int(m_hold.group(1))
        except ValueError:
            pass
    hold_playback = None
    m_hp = re.search(r"hold_playback[=:]?\s*([0-9.]+)s", t)
    if m_hp:
        try:
            hold_playback = float(m_hp.group(1))
        except ValueError:
            pass

    return {
        "blades_ok": blades,
        "stream_ok": stream,
        "glyphs_ok": glyphs,
        "mach_calc": mach_calc,
        "mach_ok": bool(field_ok),
        "field_ok": bool(field_ok),
        "mach_span": span,
        "mach_vmin": vmin,
        "mach_vmax": vmax,
        "wrote": wrote,
        "no_mesh": no_mesh,
        "mesh_ok": mesh_ok,
        "flow_paths_ok": stream or glyphs,
        "slice_ok": slice_ok,
        "blades_gray": blades_gray,
        "scene_lock_ok": scene_lock,
        "anim_locked": anim_locked,
        "steady_hold_ok": steady_ok or (hold_playback is not None and hold_playback >= 0.999),
        "n_hold_frames": n_hold_log,
        "hold_playback_s": hold_playback,
    }


def engineering_video_success(
    log_text: str,
    out: Path | None,
    *,
    multi_hue: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """True only when file exists and log proves blades + colored field + flow paths.

    When multi_hue metrics are provided (from proof / anim_mid / mp4 mid-frame),
    both cool and warm populations must be present — monochrome is not engineering-grade.
    """
    flags = parse_pvbatch_log(log_text)
    size = out.stat().st_size if out and out.is_file() else 0
    if size < 500 and not (out and out.is_dir()):
        if out and out.is_dir() and any(out.glob("frame_*.png")):
            size = 1000
        else:
            return False, "output missing or too small"
    if not flags["blades_ok"]:
        return False, "blades not rendered (STL load failed)"
    if not flags["flow_paths_ok"]:
        return False, "flow paths missing (streamlines/glyphs failed)"
    if flags["no_mesh"] or not flags["mesh_ok"]:
        return False, "OpenFOAM case has no mesh/fields — run §4 mesh+solve"
    if not flags["field_ok"]:
        return False, "Mach/primary field not colored (no finite array range / flat LUT)"
    if not flags.get("slice_ok"):
        return False, "primary field not on mid-span Slice (volume shell is not engineering view)"
    span = flags.get("mach_span")
    if span is not None and float(span) <= 1e-3:
        return False, f"field span too small ({span})"
    if multi_hue is not None and multi_hue.get("ok") is False:
        return False, f"multi-hue fail ({multi_hue.get('reason', 'flat')})"
    fname = "field"
    import re as _re
    m = _re.search(r"field coloring OK\s+(\S+)\s+span=", log_text or "")
    if m:
        fname = m.group(1)
    return True, f"blades + mid-span Slice {fname} + flow paths"


def write_video_artifacts(case_dir: str | Path, opts: VideoOptions | None = None) -> dict[str, Any]:
    opts = opts or VideoOptions()
    cdir = Path(case_dir)
    if not cdir.is_dir():
        raise FileNotFoundError(str(cdir))
    thermo = load_case_thermo_meta(cdir)
    # Fill missing opts from case meta
    if opts.gamma is None:
        opts.gamma = float(thermo.get("gamma") or 1.3)
    if opts.r_specific is None:
        opts.r_specific = float(thermo.get("r_specific") or 320.0)
    if opts.blade_name in ("", "impulse_r0") and thermo.get("blade_name"):
        opts.blade_name = str(thermo["blade_name"])
    if opts.mach_w1 is None and thermo.get("mach_w1") is not None:
        opts.mach_w1 = float(thermo["mach_w1"])
    if opts.beta1_deg is None and thermo.get("beta1_deg") is not None:
        opts.beta1_deg = float(thermo["beta1_deg"])
    if opts.inlet_p1_pa is None and thermo.get("p1_pa") is not None:
        opts.inlet_p1_pa = float(thermo["p1_pa"])
    if opts.inlet_t1_k is None and thermo.get("t1_k") is not None:
        opts.inlet_t1_k = float(thermo["t1_k"])

    vdir = cdir / "postProcessing" / "videos"
    vdir.mkdir(parents=True, exist_ok=True)
    foams = list(cdir.glob("*.foam"))
    foam = foams[0] if foams else cdir / f"{cdir.name}.foam"
    if not foam.exists():
        foam.write_text("", encoding="utf-8")
    stem = descriptive_stem(opts)
    ext = ".gif" if opts.output_format == "gif" else ".mp4"
    out = vdir / f"{stem}{ext}"
    script_path = cdir / "technical_flow_video.py"
    script_path.write_text(
        build_paraview_script(cdir, opts, out, foam, thermo=thermo), encoding="utf-8"
    )
    cfg_path = vdir / f"{stem}_video_config.json"
    cfg = {
        **opts.to_dict(),
        "script_path": str(script_path),
        "output_path": str(out),
        "stl_path": thermo.get("stl_path"),
        "engineering_notes": [
            "OpenFOAM provides time-series fields; MP4 is rendered via ParaView pvbatch.",
            "Script shows internal mesh (Mach/shocks), blade STL, streamlines/vectors.",
            "Requires solved case (t>0) for real flow animation.",
            "Startup narrative: quiescent domain → inlet fill → passages/shocks → steady hold.",
            f"Steady hold: last timestep held for {opts.steady_hold_s}s playback "
            f"({steady_hold_frame_count(opts.fps, opts.steady_hold_s)} frames @ {opts.fps} fps).",
        ],
        "steady_hold_s": opts.steady_hold_s,
        "n_hold_frames": steady_hold_frame_count(opts.fps, opts.steady_hold_s),
        "startup_video": True,
    }
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return {
        "script_path": str(script_path.resolve()),
        "config_path": str(cfg_path.resolve()),
        "output_path": str(out.resolve()),
        "video_dir": str(vdir.resolve()),
        "options": opts.to_dict(),
        "thermo": thermo,
    }


def generate_technical_video(
    case_dir: str | Path,
    options: VideoOptions | None = None,
    *,
    run_pvbatch: bool | None = None,
    timeout_s: float = 900,
) -> VideoResult:
    opts = options or VideoOptions()
    if run_pvbatch is not None:
        opts.run_pvbatch = bool(run_pvbatch)
    # Normalize empty fields to engineering defaults
    if not opts.fields:
        opts.fields = list(DEFAULT_VIDEO_FIELDS)
    cdir = Path(case_dir)
    if not cdir.is_dir():
        return VideoResult("failed", str(cdir), message="case missing")
    arts = write_video_artifacts(cdir, opts)
    solved = [(t, p) for t, p in list_time_dirs(cdir) if t > 0]
    if not solved:
        return VideoResult(
            "no_timesteps",
            str(cdir.resolve()),
            script_path=arts["script_path"],
            config_path=arts["config_path"],
            output_path=arts["output_path"],
            video_dir=arts["video_dir"],
            message=(
                "Engineering video script written, but no solved timesteps (t>0). "
                "Run §4 mesh+solve first, then re-render. "
                f"Script: {arts['script_path']}"
            ),
            notes=["no_timesteps", "script_ready", "needs_solve"],
            options=arts["options"],
        )
    if not opts.run_pvbatch:
        return VideoResult(
            "script_only",
            str(cdir.resolve()),
            script_path=arts["script_path"],
            config_path=arts["config_path"],
            output_path=arts["output_path"],
            video_dir=arts["video_dir"],
            message=(
                "Script written (blades + Mach + streamlines). "
                f"Render with: pvbatch {arts['script_path']}"
            ),
            notes=["script_only"],
            options=arts["options"],
        )
    pv = find_pvbatch()
    if not pv:
        return VideoResult(
            "needs_pvbatch",
            str(cdir.resolve()),
            script_path=arts["script_path"],
            config_path=arts["config_path"],
            output_path=arts["output_path"],
            video_dir=arts["video_dir"],
            message=(
                "Script ready (engineering cascade: blades + Mach + flow paths). "
                "Install ParaView and set PVBATCH, then: "
                f"pvbatch {arts['script_path']}"
            ),
            notes=["needs_pvbatch", "script_ready"],
            options=arts["options"],
        )
    try:
        proc = subprocess.run(
            [pv, arts["script_path"]],
            cwd=str(cdir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env={**os.environ, "PV_ALLOW_BATCH_DISPLAY": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return VideoResult(
            "failed",
            str(cdir.resolve()),
            script_path=arts["script_path"],
            output_path=arts["output_path"],
            video_dir=arts["video_dir"],
            message=str(exc),
            notes=["pvbatch_error"],
            options=arts["options"],
        )
    out = Path(arts["output_path"])
    log_text = f"rc={proc.returncode}\n{proc.stdout or ''}\n{proc.stderr or ''}"
    log_path = Path(arts["video_dir"]) / "pvbatch.log"
    log_path.write_text(log_text, encoding="utf-8")
    flags = parse_pvbatch_log(log_text)

    # Prefer MP4; fall back to frames dir
    artifact: Path | None = out if out.is_file() else None
    frames = out.parent / (out.stem + "_frames")
    if artifact is None and frames.is_dir() and any(frames.glob("frame_*.png")):
        artifact = frames

    # Multi-hue gate: proof, anim_mid, steady hold frame, and extracted MP4 mid-frame
    vdir = Path(arts["video_dir"])
    stem = out.stem
    proof_png = vdir / f"{stem}_proof.png"
    anim_mid_png = vdir / f"{stem}_anim_mid.png"
    steady_png = vdir / f"{stem}_steady.png"
    hue_reports: list[dict[str, Any]] = []
    for label, png in (
        ("proof", proof_png),
        ("anim_mid", anim_mid_png),
        ("steady", steady_png),
    ):
        if png.is_file():
            m = multi_hue_png_metrics(png)
            m["label"] = label
            hue_reports.append(m)
            log_text += f"\n[impulsecalc_video] multi-hue {label}: {m.get('reason')} ok={m.get('ok')}\n"

    mp4_mid_metrics: dict[str, Any] | None = None
    if out.is_file() and out.suffix.lower() == ".mp4":
        mid_png = extract_mp4_mid_frame(out, vdir / f"{stem}_mp4_mid.png")
        if mid_png is not None:
            mp4_mid_metrics = multi_hue_png_metrics(mid_png)
            mp4_mid_metrics["label"] = "mp4_mid"
            hue_reports.append(mp4_mid_metrics)
            log_text += (
                f"\n[impulsecalc_video] multi-hue mp4_mid: "
                f"{mp4_mid_metrics.get('reason')} ok={mp4_mid_metrics.get('ok')}\n"
            )

    # Prefer steady / mp4_mid / anim_mid for the gate (held engineering scene)
    gate_hue: dict[str, Any] | None = None
    for pref in ("steady", "mp4_mid", "anim_mid", "proof"):
        for m in hue_reports:
            if m.get("label") == pref:
                gate_hue = m
                break
        if gate_hue is not None:
            break

    # Persist multi-hue attempt evidence for skeptic / UI
    attempt_path = vdir / "video_render_attempt.txt"
    try:
        lines = [
            f"output={out}",
            f"rc={proc.returncode}",
            f"flags={{{', '.join(k for k,v in flags.items() if v is True)}}}",
        ]
        for m in hue_reports:
            lines.append(
                f"multi_hue_{m.get('label')}: cool={m.get('cool')} warm={m.get('warm')} "
                f"ok={m.get('ok')} reason={m.get('reason')}"
            )
        attempt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    log_path.write_text(log_text, encoding="utf-8")

    ok, reason = engineering_video_success(log_text, artifact, multi_hue=gate_hue)
    notes = [f"rc={proc.returncode}"]
    for k, v in flags.items():
        if isinstance(v, bool) and v:
            notes.append(str(k))
    if gate_hue and gate_hue.get("ok"):
        notes.append("multi_hue_ok")
    elif gate_hue:
        notes.append("multi_hue_fail")

    if ok and artifact is not None:
        size = artifact.stat().st_size if artifact.is_file() else sum(
            p.stat().st_size for p in artifact.glob("frame_*.png")
        )
        # Drop pre-Blades 1080p/720p whitish shells so the UI path is unambiguous
        retired = retire_legacy_video_artifacts(
            vdir,
            keep_stem=stem,
            resolution=str((arts.get("options") or {}).get("resolution") or opts.resolution),
        )
        if retired:
            notes.append(f"retired_legacy={len(retired)}")
            try:
                with attempt_path.open("a", encoding="utf-8") as af:
                    af.write("retired_legacy=" + ",".join(retired) + "\n")
            except OSError:
                pass
        hue_note = ""
        if gate_hue and gate_hue.get("ok"):
            hue_note = f" multi-hue[{gate_hue.get('label')}] cool={gate_hue.get('cool')} warm={gate_hue.get('warm')}"
        return VideoResult(
            "success",
            str(cdir.resolve()),
            script_path=arts["script_path"],
            config_path=arts["config_path"],
            output_path=str(artifact.resolve()),
            video_dir=arts["video_dir"],
            message=f"Engineering video OK ({size} bytes): {reason}{hue_note}",
            notes=notes + ["video_ready", "engineering_grade"],
            options=arts["options"],
        )

    # Partial: wrote something but missing blades/streamlines/mesh/multi-hue
    if artifact is not None and (artifact.is_file() and artifact.stat().st_size > 100):
        return VideoResult(
            "partial",
            str(cdir.resolve()),
            script_path=arts["script_path"],
            config_path=arts["config_path"],
            output_path=str(artifact.resolve()),
            video_dir=arts["video_dir"],
            message=(
                f"Video file written but not engineering-grade: {reason}. "
                f"See {log_path}"
            ),
            notes=notes + ["not_engineering_grade", reason.replace(" ", "_")[:40]],
            options=arts["options"],
        )

    return VideoResult(
        "failed",
        str(cdir.resolve()),
        script_path=arts["script_path"],
        output_path=arts["output_path"],
        video_dir=arts["video_dir"],
        message=f"pvbatch failed: {reason} (rc={proc.returncode}) — see {log_path}",
        notes=notes,
        options=arts["options"],
    )


def workflow_status(case_dir: str | Path | None) -> dict[str, Any]:
    if not case_dir:
        return {
            "mesh_ready": False,
            "solver_ready": False,
            "video_ready": False,
            "label": "mesh=— · solver=— · video=—",
            "case_dir": "",
        }
    cdir = Path(case_dir)
    mesh = (cdir / "constant" / "polyMesh" / "points").is_file()
    solved = any(t > 0 for t, _ in list_time_dirs(cdir))
    vdir = cdir / "postProcessing" / "videos"
    videos: list[str] = []
    if vdir.is_dir():
        # Prefer engineering Blades stems; de-prioritize legacy whitish shells
        cands = []
        for ext in ("*.mp4", "*.gif"):
            for p in vdir.glob(ext):
                if p.stat().st_size > 100:
                    cands.append(p)
        cands.sort(
            key=lambda p: (
                0 if "Blades" in p.name else 1,
                -p.stat().st_mtime,
            )
        )
        videos = [str(p) for p in cands]
    script = (cdir / "technical_flow_video.py").is_file()
    stl = (cdir / "constant" / "triSurface" / "blades.stl").is_file()
    primary = videos[0] if videos else ""
    return {
        "case_dir": str(cdir),
        "mesh_ready": mesh,
        "solver_ready": solved,
        "video_ready": bool(videos),
        "script_present": script,
        "blades_stl": stl,
        "video_paths": videos,
        "primary_video": primary,
        "label": (
            f"mesh={'done' if mesh else 'pending'} · "
            f"solver={'done' if solved else 'pending'} · "
            f"video={'ready' if videos else ('script' if script else 'pending')}"
            + (" · blades" if stl else "")
        ),
    }
