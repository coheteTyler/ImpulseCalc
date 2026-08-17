"""Write a complete 2D multi-blade OpenFOAM cascade case from mean-line inputs."""

from __future__ import annotations

import json
import math
import shutil
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fidelity import (
    MESH_BODY,
    MESH_STAIR,
    TURB_LAMINAR,
    TURB_SST,
    WALL_NOSLIP,
    WALL_SLIP,
    FidelitySettings,
    fidelity_from_request,
    recommended_end_time,
    resolve_fidelity,
)
from .geometry import (
    BladeGeometry,
    BladeShapeParams,
    cascade_blade_outlines,
    domain_bounds,
)
from .meanline import MeanlineInputs, MeanlineResult, compute_meanline


def _optional_float(value: Any, default: float | None = None) -> float | None:
    """Parse optional numeric; treat None / '' / 'auto' (any case) as default."""
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).strip()
    if s == "" or s.lower() in ("auto", "none", "null", "default"):
        return default
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


@dataclass
class CaseBuildResult:
    case_dir: str
    success: bool
    files: dict[str, str]
    message: str
    meanline: dict[str, Any]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_dir": self.case_dir,
            "success": self.success,
            "files": dict(self.files),
            "message": self.message,
            "meanline": self.meanline,
            "notes": list(self.notes),
        }


def _hdr(cls: str, obj: str) -> str:
    return textwrap.dedent(
        f"""\
        /*--------------------------------*- C++ -*----------------------------------*\\
        | ImpulseCalc generated OpenFOAM case                                        |
        \\*---------------------------------------------------------------------------*/
        FoamFile
        {{
            version     2.0;
            format      ascii;
            class       {cls};
            object      {obj};
        }}
        // * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
        """
    )


def write_stl(case_dir: Path, geom: BladeGeometry, n_blades: int) -> Path:
    """Extruded 2.5D blade solids (top, bottom, and vertical side walls) for CFD video."""
    tri = case_dir / "constant" / "triSurface"
    tri.mkdir(parents=True, exist_ok=True)
    stl = tri / "blades.stl"
    z0, z1 = 0.0, 0.001
    lines = ["solid blades"]

    def facet(n, a, b, c):
        lines.extend(
            [
                f"  facet normal {n[0]} {n[1]} {n[2]}",
                "    outer loop",
                f"      vertex {a[0]} {a[1]} {a[2]}",
                f"      vertex {b[0]} {b[1]} {b[2]}",
                f"      vertex {c[0]} {c[1]} {c[2]}",
                "    endloop",
                "  endfacet",
            ]
        )

    for poly in cascade_blade_outlines(geom, n_blades):
        pts = poly[:-1] if poly and poly[0] == poly[-1] else poly
        if len(pts) < 3:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        # Cap fans (top z1, bottom z0)
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            facet((0, 0, 1), (cx, cy, z1), (x1, y1, z1), (x2, y2, z1))
            facet((0, 0, -1), (cx, cy, z0), (x2, y2, z0), (x1, y1, z0))
            # Vertical side wall (extrusion) — two triangles
            dx, dy = x2 - x1, y2 - y1
            # outward-ish normal in xy (approximate)
            nx, ny = dy, -dx
            ln = math.hypot(nx, ny) or 1.0
            nx, ny = nx / ln, ny / ln
            facet((nx, ny, 0), (x1, y1, z0), (x2, y2, z0), (x2, y2, z1))
            facet((nx, ny, 0), (x1, y1, z0), (x2, y2, z1), (x1, y1, z1))
    lines.append("endsolid blades")
    stl.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return stl


# Boundary name for metal after solid cells are removed (subsetMesh -patch)
BLADE_WALL_PATCH = "blades"

# Spanwise thickness of cascade slab (m). Industry path uses multi-cell z so
# snappyHexMesh can snap (it rejects empty 1-cell "2D" meshes).
CASCADE_Z_THICK_M = 0.001


def fluid_seed_point(
    geom: BladeGeometry,
    n_blades: int,
    *,
    x_up_c: float = 0.5,
    x_dn_c: float = 1.0,
) -> tuple[float, float, float]:
    """A guaranteed fluid location (upstream of LE, mid-pitch) for surfaceToCell / snappy."""
    b = domain_bounds(geom, n_blades, x_up_c=x_up_c, x_dn_c=x_dn_c)
    # Slightly inside the inlet plane, on the cyclic mid-line (between blades)
    x = float(b["x_min"]) + 0.15 * max(float(geom.chord_m), 1e-6)
    y = 0.0
    z = 0.5 * float(CASCADE_Z_THICK_M)
    return (x, y, z)


def write_blockmesh(
    case_dir: Path,
    geom: BladeGeometry,
    n_blades: int,
    nx: int,
    ny: int,
    *,
    x_up_c: float = 0.5,
    x_dn_c: float = 1.0,
    nz: int = 1,
    front_back_type: str = "empty",
) -> Path:
    """Write a single-block cascade domain: inlet / outlet / pitch-periodic cyclics.

    Background hex mesh only — blade metal is cut out later (snappy or topoSet).

    ``nz`` / ``front_back_type``:
      - Fast 2D: nz=1, front_back_type=empty (topoSet stair-step).
      - Industry body-fitted: nz>=3, front_back_type=wall (thin 3D so snappyHexMesh
        can snap; front/back get slip in 0/ fields). snappy refuses empty patches.
    """
    b = domain_bounds(geom, n_blades, x_up_c=x_up_c, x_dn_c=x_dn_c)
    x0, x1, y0, y1 = b["x_min"], b["x_max"], b["y_min"], b["y_max"]
    z0, z1 = 0.0, float(CASCADE_Z_THICK_M)
    # Slightly denser default mesh so surfaceToCell can resolve blade walls
    nx = max(int(nx), 40)
    ny = max(int(ny), 30)
    nz_use = max(int(nz), 1)
    fb = (front_back_type or "empty").strip().lower()
    if fb not in ("empty", "wall", "patch", "symmetryplane", "symmetryPlane"):
        fb = "empty"
    # OpenFOAM type token
    fb_type = "symmetryPlane" if fb in ("symmetryplane", "symmetryPlane") else fb
    if nz_use > 1 and fb_type == "empty":
        # empty + multi-z is invalid; force wall for thin-3D industry path
        fb_type = "wall"
    text = _hdr("dictionary", "blockMeshDict") + textwrap.dedent(
        f"""\
        convertToMeters 1;
        // Cascade flow domain (chord frame): LE x=0, TE x=chord
        // inlet length = {b['x_up_c']:.3f} c · outlet length = {b['x_dn_c']:.3f} c
        // y-span = n_blades * pitch = {b['y_span_m']:.6g} m (cyclic top/bottom)
        // nz={nz_use} frontAndBack={fb_type}
        //   empty/nz=1 = fast 2D · wall/nz>=3 = thin-3D for snappy body-fitted
        vertices
        (
            ({x0} {y0} {z0}) ({x1} {y0} {z0}) ({x1} {y1} {z0}) ({x0} {y1} {z0})
            ({x0} {y0} {z1}) ({x1} {y0} {z1}) ({x1} {y1} {z1}) ({x0} {y1} {z1})
        );
        blocks ( hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz_use}) simpleGrading (1 1 1) );
        edges ();
        boundary
        (
            inlet  {{ type patch;  faces ( (0 4 7 3) ); }}
            outlet {{ type patch;  faces ( (1 2 6 5) ); }}
            bottom {{ type cyclic; neighbourPatch top;    faces ( (0 1 5 4) ); }}
            top    {{ type cyclic; neighbourPatch bottom; faces ( (3 7 6 2) ); }}
            frontAndBack {{ type {fb_type}; faces ( (0 3 2 1) (4 5 6 7) ); }}
        );
        mergePatchPairs ();
        // ************************************************************************* //
        """
    )
    path = case_dir / "system" / "blockMeshDict"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_topo_set_dict(
    case_dir: Path,
    geom: BladeGeometry,
    n_blades: int,
    *,
    x_up_c: float = 0.5,
    x_dn_c: float = 1.0,
) -> Path:
    """Select solid cells inside blades.stl and invert → fluidCells for subsetMesh."""
    ox, oy, oz = fluid_seed_point(geom, n_blades, x_up_c=x_up_c, x_dn_c=x_dn_c)
    # surfaceToCell: cells inside closed STL = metal (removed from fluid)
    text = _hdr("dictionary", "topoSetDict") + textwrap.dedent(
        f"""\
        actions
        (
            // Cells whose centres lie inside the closed blade STL solids
            {{
                name    solidCells;
                type    cellSet;
                action  new;
                source  surfaceToCell;
                sourceInfo
                {{
                    file            "constant/triSurface/blades.stl";
                    outsidePoints   (({ox:.8g} {oy:.8g} {oz:.8g}));
                    includeCut      true;
                    includeInside   true;
                    includeOutside  false;
                    nearDistance    -1;
                    curvature       -100;
                }}
            }}
            // Start from all cells then drop solid → fluid only
            {{
                name    fluidCells;
                type    cellSet;
                action  new;
                source  cellToCell;
                sourceInfo
                {{
                    set solidCells;
                }}
            }}
            {{
                name    fluidCells;
                type    cellSet;
                action  invert;
            }}
        );
        // ************************************************************************* //
        """
    )
    path = case_dir / "system" / "topoSetDict"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_snappy_hex_mesh_dict(
    case_dir: Path,
    geom: BladeGeometry,
    n_blades: int,
    *,
    x_up_c: float = 0.5,
    x_dn_c: float = 1.0,
    body_fitted: bool = False,
    refine_level: int = 1,
) -> Path:
    """snappyHexMesh dict for body-fitted blade walls (industry primary) or fallback.

    ``body_fitted=True`` enables stronger snap + optional prism layers near blades.
    Layers stay conservative for 2D empty-patch cascades (may be skipped by snappy).
    """
    ox, oy, oz = fluid_seed_point(geom, n_blades, x_up_c=x_up_c, x_dn_c=x_dn_c)
    # Industry path: higher surface refinement + snap iterations.
    # Layers stay OFF by default: prism layers on thin cascade blades often abort;
    # castellation+snap alone already yields body-fitted (non-stair-step) wall faces.
    lv0 = max(1, int(refine_level))
    lv1 = lv0 + (1 if body_fitted else 0)
    snap_on = "true"
    layers_on = "false"
    layer_block = """\
            relativeSizes true;
            layers {}
            expansionRatio 1.0;
            finalLayerThickness 0.3;
            minThickness 0.1;
            nGrow 0;
            featureAngle 60;
            nRelaxIter 3;
            nSmoothSurfaceNormals 1;
            nSmoothNormals 3;
            nSmoothThickness 10;
            maxFaceThicknessRatio 0.5;
            maxThicknessToMedialRatio 0.3;
            minMedianAxisAngle 90;
            nBufferCellsNoExtrude 0;
            nLayerIter 50;
"""
    text = _hdr("dictionary", "snappyHexMeshDict") + textwrap.dedent(
        f"""\
        // ImpulseCalc: {"body-fitted primary" if body_fitted else "fallback"} blade walls
        castellatedMesh true;
        snap            {snap_on};
        addLayers       {layers_on};
        geometry
        {{
            // OF-12: explicit file= required (name key alone is not enough)
            {BLADE_WALL_PATCH}
            {{
                type triSurfaceMesh;
                file "blades.stl";
                name {BLADE_WALL_PATCH};
            }}
        }}
        castellatedMeshControls
        {{
            maxLocalCells 2000000;
            maxGlobalCells 4000000;
            minRefinementCells 0;
            maxLoadUnbalance 0.10;
            nCellsBetweenLevels 2;
            features ();
            refinementSurfaces
            {{
                {BLADE_WALL_PATCH}
                {{
                    level ({lv0} {lv1});
                    patchInfo {{ type wall; }}
                }}
            }}
            resolveFeatureAngle 30;
            refinementRegions {{}}
            locationInMesh ({ox:.8g} {oy:.8g} {oz:.8g});
            allowFreeStandingZoneFaces true;
        }}
        snapControls
        {{
            nSmoothPatch {"5" if body_fitted else "3"};
            tolerance {"1.5" if body_fitted else "2.0"};
            nSolveIter {"50" if body_fitted else "30"};
            nRelaxIter {"8" if body_fitted else "5"};
            nFeatureSnapIter {"15" if body_fitted else "10"};
            implicitFeatureSnap false;
            explicitFeatureSnap false;
            multiRegionFeatureSnap false;
        }}
        addLayersControls
        {{
{layer_block}        }}
        meshQualityControls
        {{
            // OpenFOAM-12 requires minTetQuality (was optional in older OF)
            maxNonOrtho 65;
            maxBoundarySkewness 20;
            maxInternalSkewness 4;
            maxConcave 80;
            minFlatness 0.5;
            minVol 1e-13;
            minTetQuality 1e-30;
            minArea -1;
            minTwist 0.02;
            minDeterminant 0.001;
            minFaceWeight 0.02;
            minVolRatio 0.01;
            minTriangleTwist -1;
            nSmoothScale 4;
            errorReduction 0.75;
            relaxed
            {{
                maxNonOrtho 75;
            }}
        }}
        mergeTolerance 1e-6;
        // ************************************************************************* //
        """
    )
    path = case_dir / "system" / "snappyHexMeshDict"
    path.write_text(text, encoding="utf-8")
    return path


def write_create_patch_dict(case_dir: Path) -> Path:
    """Rename subsetMesh ``oldInternalFaces`` → wall patch ``blades``."""
    text = _hdr("dictionary", "createPatchDict") + textwrap.dedent(
        f"""\
        pointSync false;
        patches
        (
            {{
                name {BLADE_WALL_PATCH};
                patchInfo
                {{
                    type wall;
                }}
                constructFrom patches;
                patches (oldInternalFaces);
            }}
        );
        // ************************************************************************* //
        """
    )
    path = case_dir / "system" / "createPatchDict"
    path.write_text(text, encoding="utf-8")
    return path


def write_mesh_pipeline_readme(
    case_dir: Path,
    *,
    mesh_path: str = MESH_STAIR,
) -> Path:
    """Document how blade walls are introduced into the volume mesh."""
    if mesh_path == MESH_BODY:
        text = textwrap.dedent(
            f"""\
            ImpulseCalc cascade mesh pipeline — BODY-FITTED (industry primary)
            ==================================================================
            1. blockMesh          — rectangular background hex (inlet/outlet/cyclic)
            2. snappyHexMesh -overwrite
                                    — castellation + snap to constant/triSurface/blades.stl
                                    — wall patch "{BLADE_WALL_PATCH}" (optional prism layers)
            3. checkMesh

            Fallback if snappy fails or yields no blade wall faces:
            4. topoSet → subsetMesh fluidCells → createPatch (stair-step walls)
               (same as design-board / fast path)

            Without a wall patch the volume is an empty duct (blades only video overlay).
            """
        )
    else:
        text = textwrap.dedent(
            f"""\
            ImpulseCalc cascade mesh pipeline — STAIR-STEP (fast design board)
            =================================================================
            1. blockMesh          — rectangular background hex (inlet/outlet/cyclic)
            2. topoSet            — solidCells = inside constant/triSurface/blades.stl
                                    fluidCells = invert(solidCells)
            3. subsetMesh fluidCells -overwrite
                                    — delete metal cells; exposed faces → oldInternalFaces
            4. createPatch -overwrite
                                    — rename oldInternalFaces → wall patch "{BLADE_WALL_PATCH}"
            5. checkMesh

            Without steps 2–4 the volume is an empty duct and blades are only a video overlay.
            Fallback: snappyHexMesh -overwrite (system/snappyHexMeshDict) if cut fails.
            """
        )
    path = case_dir / "MESH_PIPELINE.txt"
    path.write_text(text, encoding="utf-8")
    return path


def write_control(
    case_dir: Path,
    end_time: float,
    delta_t: float,
    write_interval: float,
    *,
    max_co: float | None = None,
    max_delta_t: float | None = None,
    mach_w1: float | None = None,
) -> Path:
    # OpenFOAM 12: rhoCentralFoam is a shim → foamRun -solver shockFluid
    # maxCo ≤ 0.2 and small maxDeltaT: vanLeer overshoots at Co~0.3 → T<0 → FPE in fluxPredictor.
    # Supersonic relative inlet (paper Mw1≈1.2–1.6) needs even tighter CFL with stair-step walls.
    mw = float(mach_w1) if mach_w1 is not None else 0.0
    if max_co is None:
        # Stair-step blade walls + M_w>1: keep Co very low (Tadmor still needs help)
        max_co = 0.03 if mw >= 1.2 else (0.05 if mw >= 1.0 else 0.10)
    if max_delta_t is None:
        max_delta_t = 2e-8 if mw >= 1.2 else (5e-8 if mw >= 1.0 else 1e-7)
    # Initial deltaT: keep well under maxDeltaT for high-M starts
    dt0 = min(float(delta_t), float(max_delta_t) * 0.5, 5e-9 if mw >= 1.0 else float(delta_t))
    text = _hdr("dictionary", "controlDict") + textwrap.dedent(
        f"""\
        application     foamRun;
        solver          shockFluid;
        startFrom       startTime;
        startTime       0;
        stopAt          endTime;
        endTime         {end_time};
        deltaT          {dt0};
        writeControl    adjustableRunTime;
        writeInterval   {write_interval};
        purgeWrite      0;
        writeFormat     ascii;
        writePrecision  8;
        writeCompression off;
        timeFormat      general;
        timePrecision   6;
        runTimeModifiable true;
        adjustTimeStep  yes;
        maxCo           {float(max_co):.4g};
        maxDeltaT       {float(max_delta_t):.4g};
        // ************************************************************************* //
        """
    )
    path = case_dir / "system" / "controlDict"
    path.write_text(text, encoding="utf-8")
    return path


def write_schemes(
    case_dir: Path,
    *,
    flux_scheme: str = "Tadmor",
    reconstruct: str = "Minmod",
) -> Path:
    # Minmod is more dissipative than vanLeer — avoids negative T/rho near shocks
    # that make shockFluid::fluxPredictor call Foam::sqrt on a negative field (SIGFPE).
    flux = flux_scheme if flux_scheme in ("Tadmor", "Kurganov") else "Tadmor"
    rec = reconstruct if reconstruct in ("Minmod", "vanLeer") else "Minmod"
    rec_v = "MinmodV" if rec == "Minmod" else "vanLeerV"
    text = _hdr("dictionary", "fvSchemes") + textwrap.dedent(
        f"""\
        // Tadmor + limited reconstruction: stable with stair-step blade walls
        // from topoSet/subsetMesh. High-accuracy mode uses finer mesh + lower Co.
        fluxScheme      {flux};
        ddtSchemes {{ default Euler; }}
        gradSchemes {{ default cellLimited Gauss linear 1; }}
        divSchemes {{ default none; }}
        laplacianSchemes {{ default Gauss linear corrected; }}
        interpolationSchemes {{
            default linear;
            reconstruct(rho) {rec};
            reconstruct(U) {rec_v};
            reconstruct(T) {rec};
        }}
        snGradSchemes {{ default corrected; }}
        // ************************************************************************* //
        """
    )
    path = case_dir / "system" / "fvSchemes"
    path.write_text(text, encoding="utf-8")
    return path


def write_solution(case_dir: Path) -> Path:
    # PIMPLE block is mandatory for OpenFOAM-12 shockFluid (empty is OK; matches tutorials)
    text = _hdr("dictionary", "fvSolution") + textwrap.dedent(
        """\
        solvers {
            "(rho|rhoU|rhoE).*" { solver diagonal; }
            "U.*" {
                solver smoothSolver;
                smoother GaussSeidel;
                nSweeps 2;
                tolerance 1e-09;
                relTol 0.01;
            }
            "e.*" {
                solver smoothSolver;
                smoother GaussSeidel;
                nSweeps 2;
                tolerance 1e-10;
                relTol 0;
            }
        }
        PIMPLE
        {
        }
        // ************************************************************************* //
        """
    )
    path = case_dir / "system" / "fvSolution"
    path.write_text(text, encoding="utf-8")
    return path


def write_thermo(case_dir: Path, gamma: float, R: float, mu: float) -> Path:
    """Write thermo for OF12 (physicalProperties) and legacy (thermophysicalProperties)."""
    Cp = gamma * R / max(gamma - 1.0, 1e-9)
    mw = 8314.462618 / R if R else 28.9
    body = textwrap.dedent(
        f"""\
        thermoType {{
            type hePsiThermo; mixture pureMixture; transport const;
            thermo hConst; equationOfState perfectGas; specie specie;
            energy sensibleInternalEnergy;
        }}
        mixture {{
            specie {{ molWeight {mw:.4f}; }}
            thermodynamics {{ Cp {Cp:.4f}; hf 0; }}
            transport {{ mu {mu}; Pr 0.7; }}
        }}
        // ************************************************************************* //
        """
    )
    const = case_dir / "constant"
    const.mkdir(parents=True, exist_ok=True)
    # OF-12 name
    phys = const / "physicalProperties"
    phys.write_text(_hdr("dictionary", "physicalProperties") + body, encoding="utf-8")
    # Legacy alias (older OF / some tools)
    legacy = const / "thermophysicalProperties"
    legacy.write_text(_hdr("dictionary", "thermophysicalProperties") + body, encoding="utf-8")
    return phys


def write_turbulence(
    case_dir: Path,
    *,
    turbulence_model: str = TURB_LAMINAR,
) -> Path:
    """Write momentumTransport (OF12) + turbulenceProperties (legacy).

    Industry path: RAS kOmegaSST. Fast path: laminar.
    Note: shockFluid (density-based) may only partially couple RAS; files are
    written for industry-standard case structure and future solver paths.
    """
    const = case_dir / "constant"
    const.mkdir(parents=True, exist_ok=True)
    model = (turbulence_model or TURB_LAMINAR).strip()
    if model == TURB_SST or model.lower() in ("komegasst", "k-omega-sst", "sst", "ras"):
        body = textwrap.dedent(
            """\
            simulationType  RAS;
            RAS
            {
                model           kOmegaSST;
                turbulence      on;
                printCoeffs     on;
            }
            // ************************************************************************* //
            """
        )
    else:
        body = "simulationType laminar;\n// ************************************************************************* //\n"
    mom = const / "momentumTransport"
    mom.write_text(_hdr("dictionary", "momentumTransport") + body, encoding="utf-8")
    turb = const / "turbulenceProperties"
    turb.write_text(_hdr("dictionary", "turbulenceProperties") + body, encoding="utf-8")
    return mom


def _turbulence_inlet_values(ml: MeanlineResult) -> dict[str, float]:
    """Estimate freestream k, omega, nut for cascade RANS (I≈5%, L≈0.1c)."""
    w = max(abs(float(ml.w1_m_s)), 1.0)
    c = max(float(ml.inputs.chord_m), 1e-6)
    mu = max(float(ml.inputs.mu_pa_s), 1e-8)
    rho = max(float(getattr(ml, "rho1_kg_m3", 0.0) or 0.0), 0.1)
    nu = mu / rho
    I = 0.05  # 5% turbulence intensity (typical cascade freestream)
    k = 1.5 * (I * w) ** 2
    # Specific dissipation: ω = Cμ^{-1/4} √k / ℓ  with ℓ ~ 0.07–0.1 chord
    ell = 0.1 * c
    cmu = 0.09
    omega = max((cmu ** -0.25) * math.sqrt(max(k, 1e-12)) / max(ell, 1e-9), 1.0)
    nut = max(cmu * k / max(omega, 1e-12), nu * 0.1)
    return {"k": float(k), "omega": float(omega), "nut": float(nut), "nu": float(nu)}


def write_fields(
    case_dir: Path,
    ml: MeanlineResult,
    p_exit: float | None = None,
    *,
    startup: bool = True,
    include_blade_walls: bool = False,
    wall_bc: str = WALL_SLIP,
    turbulence_model: str = TURB_LAMINAR,
    front_back_type: str = "empty",
) -> dict[str, Path]:
    """Write 0/ U,p,T (and k,ω,nut when RANS SST).

    ``startup=True`` (default): domain starts *quiescent* (U=0, p≈p_exit) while the
    inlet patch is already at design W₁/p₁/T₁. The density-based solver then develops
    the cascade from no working fluid through the passage to quasi-steady — the
    sequence paper-style cascade CFD videos show (startup → shocks/passages → hold).

    ``startup=False``: freestream IC already at W₁ (legacy / restart-like).

    ``include_blade_walls``: only True *after* createPatch/snappy adds the ``blades`` wall.
    Pre-mesh fields must match blockMesh patches only — otherwise OpenFOAM-12
    ``subsetMesh`` aborts in GeometricBoundaryField::readField (extra patch in 0/).

    ``wall_bc``: slip (fast) or noSlip (industry viscous walls).
    ``turbulence_model``: laminar or kOmegaSST (writes 0/k, 0/omega, 0/nut).
    ``front_back_type``: empty (strict 2D) or wall (thin-3D industry / snappy).
    """
    p1, T1 = ml.inputs.p1_pa, ml.inputs.t1_k
    b1 = math.radians(ml.beta1_deg)
    Wx = ml.w1_m_s * math.cos(b1)
    Wy = ml.w1_m_s * math.sin(b1)
    wall_type = WALL_NOSLIP if (wall_bc or "").strip() == WALL_NOSLIP else WALL_SLIP
    use_sst = (turbulence_model or TURB_LAMINAR).strip() == TURB_SST or (
        turbulence_model or ""
    ).lower() in ("komegasst", "k-omega-sst", "sst", "ras")
    fb = (front_back_type or "empty").strip().lower()
    if fb in ("wall", "patch"):
        # Thin-3D industry slab: slip on spanwise faces (no spanwise viscous drag)
        fb_u = "frontAndBack { type slip; }"
        fb_s = "frontAndBack { type zeroGradient; }"
        fb_nut = "frontAndBack { type calculated; value uniform 0; }"
    elif fb in ("symmetryplane", "symmetry"):
        fb_u = "frontAndBack { type symmetryPlane; }"
        fb_s = "frontAndBack { type symmetryPlane; }"
        fb_nut = "frontAndBack { type symmetryPlane; }"
    else:
        fb_u = "frontAndBack { type empty; }"
        fb_s = "frontAndBack { type empty; }"
        fb_nut = "frontAndBack { type empty; }"
    # Back-pressure policy:
    # - Pure impulse (reaction≈0): static pressure nearly constant through rotor
    #   (Seume/ETC impulse stages expand in the *nozzle*, not the bucket). Use a
    #   mild 0.95·p1 dump so the density-based solver still has a slight drive
    #   without over-expanding into T≤0 near LE shocks.
    # - Subsonic / reacting: 0.72·p1 is fine for short design-board solves.
    if p_exit is not None:
        p_out = float(p_exit)
    elif bool(getattr(ml.inputs, "pure_impulse_lock", False)) or abs(ml.degree_of_reaction) < 0.05:
        p_out = max(p1 * 0.95, 1e4)
    else:
        p_out = max(p1 * 0.72, 1e4)
    # Supersonic relative inlet: even milder expansion (LE bow shock already raises p)
    if ml.mach_w1 >= 1.0:
        p_out = max(p_out, p1 * 0.92)
    zero = case_dir / "0"
    zero.mkdir(parents=True, exist_ok=True)

    if startup:
        # Quiescent interior — working fluid has not yet filled the cascade
        u_int = "uniform (0 0 0)"
        p_int = f"uniform {p_out:.6g}"
        t_int = f"uniform {T1:.6g}"
        u_out_val = "uniform (0 0 0)"
    else:
        u_int = f"uniform ({Wx:.6g} {Wy:.6g} 0)"
        p_int = f"uniform {p1:.6g}"
        t_int = f"uniform {T1:.6g}"
        u_out_val = f"uniform ({Wx:.6g} {Wy:.6g} 0)"

    # ``blades`` wall only after mesh_pipeline createPatch/snappy — do not put it in 0/
    # before subsetMesh or OF-12 readField dies.
    wall_u = (
        f"            {BLADE_WALL_PATCH} {{ type {wall_type}; }}\n"
        if include_blade_walls
        else ""
    )
    wall_p = (
        f"            {BLADE_WALL_PATCH} {{ type zeroGradient; }}\n"
        if include_blade_walls
        else ""
    )
    wall_t = (
        f"            {BLADE_WALL_PATCH} {{ type zeroGradient; }}\n"
        if include_blade_walls
        else ""
    )
    u = _hdr("volVectorField", "U") + textwrap.dedent(
        f"""\
        dimensions [0 1 -1 0 0 0 0];
        internalField {u_int};
        boundaryField {{
            // Design relative inlet — drives startup from the left patch
            inlet  {{ type fixedValue; value uniform ({Wx:.6g} {Wy:.6g} 0); }}
            outlet {{ type inletOutlet; inletValue uniform (0 0 0); value {u_out_val}; }}
            bottom {{ type cyclic; }} top {{ type cyclic; }}
            {fb_u}
{wall_u.rstrip()}
        }}
        // ************************************************************************* //
        """
    )
    # fixedValue outlet pressure is more robust than waveTransmissive for short
    # cascade design runs (waveTransmissive + vanLeer often FPE mid-run).
    p = _hdr("volScalarField", "p") + textwrap.dedent(
        f"""\
        dimensions [1 -1 -2 0 0 0 0];
        internalField {p_int};
        boundaryField {{
            inlet  {{ type fixedValue; value uniform {p1:.6g}; }}
            outlet {{ type fixedValue; value uniform {p_out:.6g}; }}
            bottom {{ type cyclic; }} top {{ type cyclic; }}
            {fb_s}
{wall_p.rstrip()}
        }}
        // ************************************************************************* //
        """
    )
    t = _hdr("volScalarField", "T") + textwrap.dedent(
        f"""\
        dimensions [0 0 0 1 0 0 0];
        internalField {t_int};
        boundaryField {{
            inlet  {{ type fixedValue; value uniform {T1:.6g}; }}
            outlet {{ type inletOutlet; inletValue uniform {T1:.6g}; value uniform {T1:.6g}; }}
            bottom {{ type cyclic; }} top {{ type cyclic; }}
            {fb_s}
{wall_t.rstrip()}
        }}
        // ************************************************************************* //
        """
    )
    paths = {"U": zero / "U", "p": zero / "p", "T": zero / "T"}
    paths["U"].write_text(u, encoding="utf-8")
    paths["p"].write_text(p, encoding="utf-8")
    paths["T"].write_text(t, encoding="utf-8")

    # Remove stale RANS fields when switching back to laminar
    for stale in ("k", "omega", "nut", "epsilon", "nuTilda"):
        sp = zero / stale
        if sp.is_file() and not use_sst:
            try:
                sp.unlink()
            except OSError:
                pass

    if use_sst:
        tv = _turbulence_inlet_values(ml)
        k0, om0, nut0 = tv["k"], tv["omega"], tv["nut"]
        # Wall treatments: noSlip uses wall functions; slip uses zeroGradient-ish freestream
        if include_blade_walls:
            if wall_type == WALL_NOSLIP:
                wall_k = f"            {BLADE_WALL_PATCH} {{ type kqRWallFunction; value uniform {k0:.6g}; }}\n"
                wall_om = f"            {BLADE_WALL_PATCH} {{ type omegaWallFunction; value uniform {om0:.6g}; }}\n"
                wall_nut = f"            {BLADE_WALL_PATCH} {{ type nutkWallFunction; value uniform {nut0:.6g}; }}\n"
            else:
                wall_k = f"            {BLADE_WALL_PATCH} {{ type zeroGradient; }}\n"
                wall_om = f"            {BLADE_WALL_PATCH} {{ type zeroGradient; }}\n"
                wall_nut = f"            {BLADE_WALL_PATCH} {{ type calculated; value uniform 0; }}\n"
        else:
            wall_k = wall_om = wall_nut = ""
        k_txt = _hdr("volScalarField", "k") + textwrap.dedent(
            f"""\
            dimensions [0 2 -2 0 0 0 0];
            internalField uniform {k0:.6g};
            boundaryField {{
                inlet  {{ type fixedValue; value uniform {k0:.6g}; }}
                outlet {{ type inletOutlet; inletValue uniform {k0:.6g}; value uniform {k0:.6g}; }}
                bottom {{ type cyclic; }} top {{ type cyclic; }}
                {fb_s}
{wall_k.rstrip()}
            }}
            // ************************************************************************* //
            """
        )
        om_txt = _hdr("volScalarField", "omega") + textwrap.dedent(
            f"""\
            dimensions [0 0 -1 0 0 0 0];
            internalField uniform {om0:.6g};
            boundaryField {{
                inlet  {{ type fixedValue; value uniform {om0:.6g}; }}
                outlet {{ type inletOutlet; inletValue uniform {om0:.6g}; value uniform {om0:.6g}; }}
                bottom {{ type cyclic; }} top {{ type cyclic; }}
                {fb_s}
{wall_om.rstrip()}
            }}
            // ************************************************************************* //
            """
        )
        nut_txt = _hdr("volScalarField", "nut") + textwrap.dedent(
            f"""\
            dimensions [0 2 -1 0 0 0 0];
            internalField uniform {nut0:.6g};
            boundaryField {{
                inlet  {{ type calculated; value uniform {nut0:.6g}; }}
                outlet {{ type calculated; value uniform {nut0:.6g}; }}
                bottom {{ type cyclic; }} top {{ type cyclic; }}
                {fb_nut}
{wall_nut.rstrip()}
            }}
            // ************************************************************************* //
            """
        )
        paths["k"] = zero / "k"
        paths["omega"] = zero / "omega"
        paths["nut"] = zero / "nut"
        paths["k"].write_text(k_txt, encoding="utf-8")
        paths["omega"].write_text(om_txt, encoding="utf-8")
        paths["nut"].write_text(nut_txt, encoding="utf-8")

    # Marker for video/UI: startup IC mode
    (zero / "impulsecalc_startup").write_text(
        "startup\n" if startup else "freestream\n", encoding="utf-8"
    )
    return paths


def recommended_startup_timing(
    chord_m: float,
    w1_m_s: float,
    *,
    x_up_c: float = 0.5,
    x_dn_c: float = 1.0,
) -> dict[str, float]:
    """Flow-through based endTime / writeInterval for a visible startup movie.

    Transit time τ ≈ L_axial / max(|W₁|, a_proxy). Capture ~8–12 τ with ≥20 write frames
    so the video shows quiescent → filling → quasi-steady (paper-style cascade startup).
    """
    c = max(float(chord_m), 1e-6)
    L = c * (float(x_up_c) + 1.0 + float(x_dn_c))
    w = max(abs(float(w1_m_s)), 50.0)
    tau = L / w
    # Enough physical time for shocks/passages to establish, but design-board practical.
    # Floor 6e-4 s so short-chord cases still dump enough frames for a startup movie.
    end_time = float(min(max(16.0 * tau, 6e-4), 3.0e-3))
    # ≥24 dumps across the run (plus t=0)
    write_interval = float(max(end_time / 28.0, 1e-5))
    delta_t = float(min(1e-8, write_interval / 50.0))
    return {
        "end_time": end_time,
        "write_interval": write_interval,
        "delta_t": delta_t,
        "transit_s": tau,
        "n_writes_est": end_time / write_interval,
    }


def clean_case_time_dirs(case_dir: Path, *, keep_zero: bool = True) -> list[str]:
    """Remove previous time directories (and processor*) so solves start clean from 0/."""
    removed: list[str] = []
    if not case_dir.is_dir():
        return removed
    for p in list(case_dir.iterdir()):
        if not p.is_dir():
            continue
        name = p.name
        if name.startswith("processor"):
            shutil.rmtree(p, ignore_errors=True)
            removed.append(name)
            continue
        try:
            t = float(name)
        except ValueError:
            continue
        if keep_zero and abs(t) < 1e-30:
            continue
        if t >= 0:
            shutil.rmtree(p, ignore_errors=True)
            removed.append(name)
    return removed


def write_sample(
    case_dir: Path,
    geom: BladeGeometry,
    *,
    n_points: int = 40,
) -> Path:
    c = geom.chord_m
    np = max(20, int(n_points))
    text = _hdr("dictionary", "sample") + textwrap.dedent(
        f"""\
        type sets; libs ("libsampling.so"); writeControl writeTime;
        setFormat csv; fields (p T U); interpolationScheme cellPoint;
        sets {{
            pressureSide {{ type uniform; axis x; start (0 0.0002 0.0005); end ({c} 0.0002 0.0005); nPoints {np}; }}
            suctionSide  {{ type uniform; axis x; start (0 -0.0002 0.0005); end ({c} -0.0002 0.0005); nPoints {np}; }}
        }}
        // ************************************************************************* //
        """
    )
    path = case_dir / "system" / "sample"
    path.write_text(text, encoding="utf-8")
    return path


def generate_openfoam_case(
    meanline_inputs: MeanlineInputs,
    output_dir: str | Path,
    *,
    case_name: str = "cascade",
    n_blades: int = 3,
    nx: int | None = None,
    ny: int | None = None,
    end_time: float | str | None = None,
    delta_t: float | str | None = None,
    write_interval: float | str | None = None,
    blade_shape: dict | BladeShapeParams | None = None,
    x_up_c: float = 0.5,
    x_dn_c: float = 1.0,
    startup: bool = True,
    fidelity: str | FidelitySettings | dict | None = None,
    fidelity_level: int | float | None = None,
) -> CaseBuildResult:
    # Resolve fidelity preset (fast default keeps legacy board numbers)
    if isinstance(fidelity, FidelitySettings):
        fid = fidelity
    elif isinstance(fidelity, dict):
        fid = fidelity_from_request(fidelity)
    else:
        fid = resolve_fidelity(fidelity, level=fidelity_level)

    # Explicit nx/ny from caller override fidelity (UI fields when user typed them)
    nx_use = int(nx if nx is not None else fid.nx)
    ny_use = int(ny if ny is not None else fid.ny)
    nx_use = max(20, nx_use)
    ny_use = max(12, ny_use)

    ml = compute_meanline(meanline_inputs)
    out = Path(output_dir)
    case_dir = out / "openfoam_cases" / case_name
    case_dir.mkdir(parents=True, exist_ok=True)
    # Drop stale time folders from prior solves (corrupted mid-FPE fields must not linger)
    cleaned = clean_case_time_dirs(case_dir, keep_zero=True)

    shape = (
        blade_shape
        if isinstance(blade_shape, BladeShapeParams)
        else BladeShapeParams.from_dict(blade_shape)
    )
    # High-accuracy: denser profile sampling; prefer paper-like t/c if still at thick default
    shape_dict = shape.to_dict()
    shape_dict["n_points"] = max(int(shape.n_points), int(fid.blade_n_points))
    if fid.prefer_paper_thickness and float(shape.thickness_ratio) >= 0.40:
        shape_dict["thickness_ratio"] = float(fid.paper_thickness_ratio)
        shape_dict["le_fillet_r_c"] = max(float(shape.le_fillet_r_c), 0.02)
    shape = BladeShapeParams.from_dict(shape_dict)

    # Geometry uses metal angles (flow ± incidence/deviation)
    geom = BladeGeometry(
        chord_m=meanline_inputs.chord_m,
        beta1_deg=ml.metal_beta1_deg,
        beta2_deg=ml.metal_beta2_deg,
        solidity=meanline_inputs.solidity,
        thickness_ratio=shape.thickness_ratio,
        shape=shape,
        n_points=shape.n_points,
    )
    domain = domain_bounds(geom, n_blades, x_up_c=x_up_c, x_dn_c=x_dn_c)
    # Timing: fidelity-scaled (accurate = many transit times, no short cap)
    timing = recommended_end_time(
        meanline_inputs.chord_m,
        ml.w1_m_s,
        fid,
        x_up_c=domain["x_up_c"],
        x_dn_c=domain["x_dn_c"],
    )
    # end_time may arrive as the string "auto" from the UI — never float() that.
    et_opt = _optional_float(end_time, default=None)
    wi_opt = _optional_float(write_interval, default=None)
    dt_opt = _optional_float(delta_t, default=None)
    et = float(et_opt if et_opt is not None else timing["end_time"])
    wi = float(wi_opt if wi_opt is not None else timing["write_interval"])
    dt = float(dt_opt if dt_opt is not None else timing["delta_t"])
    files: dict[str, str] = {}
    body_fitted = fid.mesh_path == MESH_BODY
    # snappyHexMesh requires a fully 3D mesh (no empty patches). Industry path uses
    # a thin multi-z slab with wall front/back; fast path keeps classic empty 2D.
    nz_use = 4 if body_fitted else 1
    front_back = "wall" if body_fitted else "empty"
    files["blockMeshDict"] = str(
        write_blockmesh(
            case_dir,
            geom,
            n_blades,
            nx_use,
            ny_use,
            x_up_c=domain["x_up_c"],
            x_dn_c=domain["x_dn_c"],
            nz=nz_use,
            front_back_type=front_back,
        )
    )
    files["topoSetDict"] = str(
        write_topo_set_dict(
            case_dir, geom, n_blades, x_up_c=domain["x_up_c"], x_dn_c=domain["x_dn_c"]
        )
    )
    files["createPatchDict"] = str(write_create_patch_dict(case_dir))
    refine_lv = 1 if fid.level < 75 else 2
    files["snappyHexMeshDict"] = str(
        write_snappy_hex_mesh_dict(
            case_dir,
            geom,
            n_blades,
            x_up_c=domain["x_up_c"],
            x_dn_c=domain["x_dn_c"],
            body_fitted=body_fitted,
            refine_level=refine_lv,
        )
    )
    files["MESH_PIPELINE"] = str(
        write_mesh_pipeline_readme(case_dir, mesh_path=fid.mesh_path)
    )
    # CFL from fidelity; still Mach-aware floor for supersonic relative inlet
    max_co = float(fid.max_co)
    if ml.mach_w1 >= 1.2:
        max_co = min(max_co, 0.03 if not fid.is_high_accuracy else 0.02)
    elif ml.mach_w1 >= 1.0:
        max_co = min(max_co, 0.05)
    files["controlDict"] = str(
        write_control(
            case_dir,
            et,
            dt,
            wi,
            max_co=max_co,
            max_delta_t=fid.max_delta_t,
            mach_w1=ml.mach_w1,
        )
    )
    files["fvSchemes"] = str(
        write_schemes(
            case_dir,
            flux_scheme=fid.flux_scheme,
            reconstruct=fid.reconstruct,
        )
    )
    files["fvSolution"] = str(write_solution(case_dir))
    files["thermophysicalProperties"] = str(
        write_thermo(case_dir, ml.inputs.gamma, ml.inputs.r_specific_j_kg_k, ml.inputs.mu_pa_s)
    )
    files["turbulenceProperties"] = str(
        write_turbulence(case_dir, turbulence_model=fid.turbulence_model)
    )
    # Pre-mesh: blockMesh patches only (no blades) so subsetMesh can map 0/
    for k, p in write_fields(
        case_dir,
        ml,
        startup=startup,
        include_blade_walls=False,
        wall_bc=fid.wall_bc,
        turbulence_model=fid.turbulence_model,
        front_back_type=front_back,
    ).items():
        files[f"0/{k}"] = str(p)
    files["blades.stl"] = str(write_stl(case_dir, geom, n_blades))
    files["sample"] = str(
        write_sample(case_dir, geom, n_points=fid.sample_n_points)
    )

    foam = case_dir / f"{case_name}.foam"
    foam.write_text("", encoding="utf-8")
    files["foam"] = str(foam)

    to_mesh = fid.mesh_timeout_s
    to_solve = fid.solve_timeout_s
    to_mesh_s = "unlimited" if to_mesh is None else f"{to_mesh:g}s"
    to_solve_s = "unlimited" if to_solve is None else f"{to_solve:g}s"
    mesh_desc = (
        "blockMesh && snappyHexMesh (body-fitted primary; topoSet fallback)"
        if body_fitted
        else "blockMesh && topoSet && subsetMesh fluidCells (stair-step; snappy fallback)"
    )
    turb_desc = fid.turbulence_model
    wall_desc = fid.wall_bc
    (case_dir / "README.txt").write_text(
        textwrap.dedent(
            f"""\
            ImpulseCalc OpenFOAM case: {case_name}
            Fidelity: {fid.label} (level={fid.level}) · nx={nx_use} ny={ny_use} · maxCo={max_co:g}
            Industry path: mesh={fid.mesh_path} · turb={turb_desc} · wall={wall_desc}
            flow β1={ml.beta1_deg:.2f}° β2={ml.beta2_deg:.2f}° · metal β1*={ml.metal_beta1_deg:.2f}° β2*={ml.metal_beta2_deg:.2f}°
            W1={ml.w1_m_s:.1f} m/s Mw1={ml.mach_w1:.3f} · r_m={ml.mean_radius_m:.4f} m rpm={ml.rpm:.0f} ṁ={ml.mass_flow_kg_s:.4g} kg/s
            Domain: x=[{domain['x_min']:.5g}, {domain['x_max']:.5g}] m  (inlet {domain['x_up_c']:.2f}c · outlet {domain['x_dn_c']:.2f}c)
                    y-span={domain['y_span_m']:.5g} m ({n_blades} blades × pitch) · cyclic top/bottom
            ICs: {"startup (U=0 interior → inlet W1 drives fill)" if startup else "freestream (U=W1 throughout)"}
            Timing: endTime={et:g} writeInterval={wi:g} (transit≈{timing['transit_s']:.3g} s)
            Runner budgets: mesh={to_mesh_s} · solve={to_solve_s}
            Mesh:  {mesh_desc}
                   (see MESH_PIPELINE.txt)
            Solve: foamRun -solver shockFluid   (OF-12; rhoCentralFoam is a shim)
            Sample: postProcess -func sample
            Video:  pvbatch technical_flow_video.py  (startup → steady hold)
            Hint: {fid.hint}
            """
        ),
        encoding="utf-8",
    )
    seed = fluid_seed_point(
        geom, n_blades, x_up_c=domain["x_up_c"], x_dn_c=domain["x_dn_c"]
    )
    preferred_cut = "snappyHexMesh" if body_fitted else "topoSet_surfaceToCell_subsetMesh"
    meta = {
        "blade_name": meanline_inputs.blade_name,
        "meanline": ml.to_dict(),
        "geometry": {
            "n_blades": n_blades,
            "nx": nx_use,
            "ny": ny_use,
            "nz": nz_use,
            "front_back_type": front_back,
            "blade_shape": shape.to_dict(),
            "domain": domain,
            "x_up_c": domain["x_up_c"],
            "x_dn_c": domain["x_dn_c"],
        },
        "fidelity": fid.to_dict(),
        "solver": "shockFluid",
        "startup": bool(startup),
        "blade_walls": {
            "patch": BLADE_WALL_PATCH,
            "method": preferred_cut,
            "mesh_path": fid.mesh_path,
            "preferred": preferred_cut,
            "stl": "constant/triSurface/blades.stl",
            "fluid_seed": list(seed),
            "wall_bc": fid.wall_bc,
        },
        "turbulence": {
            "model": fid.turbulence_model,
            "wall_bc": fid.wall_bc,
        },
        "spanwise": {
            "nz": nz_use,
            "front_back_type": front_back,
            "z_thick_m": CASCADE_Z_THICK_M,
            "note": (
                "thin-3D wall front/back for snappy body-fitted"
                if body_fitted
                else "empty 2D cascade slab"
            ),
        },
        "timing": {
            "end_time": et,
            "write_interval": wi,
            "delta_t": dt,
            "transit_s": timing["transit_s"],
            "max_co": max_co,
        },
        "runner_timeouts": {
            "mesh_timeout_s": to_mesh,
            "solve_timeout_s": to_solve,
        },
    }
    meta_path = case_dir / "impulsecalc_case_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    files["meta"] = str(meta_path)

    mesh_note = "blade_walls_snappy_primary" if body_fitted else "blade_walls_topoSet"
    turb_note = f"turbulence={fid.turbulence_model}"
    wall_note = f"wall_bc={fid.wall_bc}"
    return CaseBuildResult(
        case_dir=str(case_dir.resolve()),
        success=True,
        files=files,
        message=(
            f"OpenFOAM case written: {case_dir} · fidelity={fid.mode} L{fid.level} · "
            f"nx={nx_use}×ny={ny_use} · mesh={fid.mesh_path} · {turb_desc}/{wall_desc} · "
            f"{'startup ICs' if startup else 'freestream ICs'} · "
            f"domain inlet={domain['x_up_c']:.2f}c outlet={domain['x_dn_c']:.2f}c · "
            f"endTime={et:g} · maxCo={max_co:g}"
        ),
        meanline=ml.to_dict(),
        notes=[
            "relative_inlet_from_W1_beta1",
            "shockFluid",
            "openfoam12",
            "stable_minmod_cfl",
            mesh_note,
            f"mesh_path={fid.mesh_path}",
            turb_note,
            wall_note,
            f"blade_wall_patch={BLADE_WALL_PATCH}",
            "startup_ics" if startup else "freestream_ics",
            f"domain_x_up_c={domain['x_up_c']}",
            f"domain_x_dn_c={domain['x_dn_c']}",
            f"end_time={et:g}",
            f"fidelity={fid.mode}",
            f"fidelity_level={fid.level}",
            f"nx={nx_use}",
            f"ny={ny_use}",
            f"max_co={max_co:g}",
            f"mesh_timeout_s={to_mesh}",
            f"solve_timeout_s={to_solve}",
            *([f"cleaned_times={len(cleaned)}"] if cleaned else []),
        ],
    )


def parse_boundary_file(boundary_path: str | Path) -> dict[str, dict[str, Any]]:
    """Parse constant/polyMesh/boundary into {patch: {type, nFaces, ...}}."""
    path = Path(boundary_path)
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    # Drop FoamFile header block
    if "(" in text:
        body = text.split("(", 1)[1]
    else:
        body = text
    patches: dict[str, dict[str, Any]] = {}
    # patchName { type ...; nFaces N; ... }
    import re

    for m in re.finditer(
        r"(\w+)\s*\{([^}]*)\}",
        body,
        flags=re.M,
    ):
        name = m.group(1)
        if name in ("FoamFile", "inGroups"):
            continue
        block = m.group(2)
        info: dict[str, Any] = {}
        tm = re.search(r"type\s+(\w+)", block)
        if tm:
            info["type"] = tm.group(1)
        nm = re.search(r"nFaces\s+(\d+)", block)
        if nm:
            info["nFaces"] = int(nm.group(1))
        patches[name] = info
    return patches


def mesh_has_blade_walls(
    case_dir: str | Path,
    *,
    min_faces: int = 1,
    patch: str = BLADE_WALL_PATCH,
) -> dict[str, Any]:
    """Report whether polyMesh exposes the metal blade wall patch with faces."""
    cdir = Path(case_dir)
    boundary = cdir / "constant" / "polyMesh" / "boundary"
    patches = parse_boundary_file(boundary)
    info = patches.get(patch) or {}
    n = int(info.get("nFaces") or 0)
    ptype = str(info.get("type") or "")
    ok = n >= min_faces and ptype.lower() in ("wall", "patch")
    return {
        "ok": ok,
        "patch": patch,
        "type": ptype,
        "nFaces": n,
        "patches": sorted(patches.keys()),
        "boundary_path": str(boundary) if boundary.is_file() else "",
    }


def blade_wall_normal_stats(
    case_dir: str | Path,
    *,
    patch: str = BLADE_WALL_PATCH,
    sample_limit: int = 400,
) -> dict[str, Any]:
    """Sample blade wall face normals to detect stair-step vs body-fitted.

    Stair-step (topoSet cut) walls are almost purely axis-aligned (±x/±y).
    Snappy body-fitted walls have a large fraction of oblique normals.
    """
    import re
    import math

    cdir = Path(case_dir)
    boundary = cdir / "constant" / "polyMesh" / "boundary"
    faces_p = cdir / "constant" / "polyMesh" / "faces"
    points_p = cdir / "constant" / "polyMesh" / "points"
    patches = parse_boundary_file(boundary)
    info = patches.get(patch) or {}
    n_faces = int(info.get("nFaces") or 0)
    if n_faces <= 0 or not faces_p.is_file() or not points_p.is_file():
        return {
            "ok": False,
            "reason": "no_blade_faces_or_mesh",
            "nFaces": n_faces,
            "oblique_frac": 0.0,
            "body_fitted_like": False,
        }
    # startFace is not always in our parser — re-parse
    btext = boundary.read_text(encoding="utf-8", errors="replace")
    m = re.search(
        rf"{re.escape(patch)}\s*\{{[^}}]*nFaces\s+(\d+);\s*startFace\s+(\d+);",
        btext,
        flags=re.S,
    )
    if not m:
        # try alternate order
        m = re.search(
            rf"{re.escape(patch)}\s*\{{[^}}]*startFace\s+(\d+);\s*nFaces\s+(\d+);",
            btext,
            flags=re.S,
        )
        if m:
            start_face, n_faces = int(m.group(1)), int(m.group(2))
        else:
            return {
                "ok": False,
                "reason": "no_startFace",
                "nFaces": n_faces,
                "oblique_frac": 0.0,
                "body_fitted_like": False,
            }
    else:
        n_faces, start_face = int(m.group(1)), int(m.group(2))

    # Parse points
    ptxt = points_p.read_text(encoding="utf-8", errors="replace")
    pts: list[tuple[float, float, float]] = []
    for pm in re.finditer(
        r"\(([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\)", ptxt
    ):
        pts.append((float(pm.group(1)), float(pm.group(2)), float(pm.group(3))))
    if not pts:
        return {
            "ok": False,
            "reason": "no_points",
            "nFaces": n_faces,
            "oblique_frac": 0.0,
            "body_fitted_like": False,
        }

    # Parse face index list (OF faces file: N( ... ) blocks)
    ftxt = faces_p.read_text(encoding="utf-8", errors="replace")
    # Drop header; faces after first bare count line
    face_defs: list[list[int]] = []
    for fm in re.finditer(r"(\d+)\(([^)]*)\)", ftxt):
        idxs = [int(x) for x in fm.group(2).split() if x.strip().lstrip("-").isdigit() or (x.strip().lstrip("-").replace(".", "", 1).isdigit() is False and x.strip().isdigit())]
        # simpler: split all ints
        idxs = [int(x) for x in re.findall(r"-?\d+", fm.group(2))]
        if idxs:
            face_defs.append(idxs)
    if start_face + n_faces > len(face_defs):
        return {
            "ok": False,
            "reason": f"face_range_oob start={start_face} n={n_faces} n_defs={len(face_defs)}",
            "nFaces": n_faces,
            "oblique_frac": 0.0,
            "body_fitted_like": False,
        }

    step = max(1, n_faces // max(sample_limit, 1))
    axis = 0
    oblique = 0
    sampled = 0
    for i in range(start_face, start_face + n_faces, step):
        ids = face_defs[i]
        if len(ids) < 3:
            continue
        try:
            p0 = pts[ids[0]]
            p1 = pts[ids[1]]
            p2 = pts[ids[2]]
        except IndexError:
            continue
        ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        ln = math.sqrt(nx * nx + ny * ny + nz * nz)
        if ln < 1e-30:
            continue
        nx, ny, nz = nx / ln, ny / ln, nz / ln
        # Axis-aligned if one component dominates strongly
        amax = max(abs(nx), abs(ny), abs(nz))
        if amax > 0.98:
            axis += 1
        else:
            oblique += 1
        sampled += 1
        if sampled >= sample_limit:
            break
    if sampled == 0:
        return {
            "ok": False,
            "reason": "no_sampled_normals",
            "nFaces": n_faces,
            "oblique_frac": 0.0,
            "body_fitted_like": False,
        }
    obl_frac = oblique / sampled
    # Stair-step cut: nearly all axis-aligned. Snappy snap: many oblique.
    body_like = obl_frac >= 0.15 or (n_faces >= 200 and obl_frac >= 0.08)
    return {
        "ok": True,
        "nFaces": n_faces,
        "sampled": sampled,
        "axis_aligned": axis,
        "oblique": oblique,
        "oblique_frac": obl_frac,
        "body_fitted_like": body_like,
        "patch": patch,
    }


def _inject_missing_patch_bcs(field_text: str, field_name: str, patches: dict[str, dict[str, Any]]) -> str:
    """Ensure every polyMesh patch has a boundaryField entry."""
    import re

    text = field_text
    extras: list[str] = []
    for pname, info in patches.items():
        # Match "blades {" or "blades\n    {" etc.
        if re.search(rf"\b{re.escape(pname)}\s*\{{", text):
            continue
        ptype = str(info.get("type") or "").lower()
        nfaces = int(info.get("nFaces") or 0)
        # Always add wall-ish patches even if nFaces parse failed
        if nfaces <= 0 and ptype not in ("wall", "patch") and pname not in (
            BLADE_WALL_PATCH,
            "oldInternalFaces",
        ):
            continue
        if ptype == "internal" or pname == "oldInternalFaces":
            # Empty leftover from subsetMesh/createPatch (nFaces often 0)
            extras.append(f"    {pname} {{ type internal; }}\n")
        elif field_name == "U":
            if ptype in ("wall", "patch") or pname == BLADE_WALL_PATCH:
                # Prefer noSlip if already present elsewhere; default slip for inject
                extras.append(f"    {pname} {{ type slip; }}\n")
            elif ptype == "empty":
                extras.append(f"    {pname} {{ type empty; }}\n")
            elif ptype == "cyclic":
                extras.append(f"    {pname} {{ type cyclic; }}\n")
            else:
                extras.append(f"    {pname} {{ type zeroGradient; }}\n")
        else:
            if ptype == "empty":
                extras.append(f"    {pname} {{ type empty; }}\n")
            elif ptype == "cyclic":
                extras.append(f"    {pname} {{ type cyclic; }}\n")
            else:
                extras.append(f"    {pname} {{ type zeroGradient; }}\n")
    if not extras:
        return text
    marker = "// ************************************************************************* //"
    if marker in text:
        idx = text.rfind("}", 0, text.find(marker))
        if idx > 0:
            return text[:idx] + "".join(extras) + text[idx:]
    idx = text.rfind("}")
    if idx > 0:
        return text[:idx] + "".join(extras) + text[idx:]
    return text + "\n" + "".join(extras)


def _case_wall_and_turb_from_meta(meta: dict[str, Any]) -> tuple[str, str, str]:
    """Read wall_bc, turbulence_model, front_back_type from case meta."""
    fid = meta.get("fidelity") or {}
    turb_block = meta.get("turbulence") or {}
    walls = meta.get("blade_walls") or {}
    geom = meta.get("geometry") or {}
    span = meta.get("spanwise") or {}
    wall = (
        walls.get("wall_bc")
        or turb_block.get("wall_bc")
        or fid.get("wall_bc")
        or WALL_SLIP
    )
    turb = turb_block.get("model") or fid.get("turbulence_model") or TURB_LAMINAR
    fb = (
        span.get("front_back_type")
        or geom.get("front_back_type")
        or ("wall" if fid.get("mesh_path") == MESH_BODY else "empty")
    )
    return str(wall), str(turb), str(fb)


def rewrite_zero_fields_after_mesh(case_dir: str | Path) -> dict[str, Any]:
    """Re-write 0/U,p,T (and k/ω/nut) after mesh so ``blades`` wall BCs match polyMesh.

    subsetMesh rewrites 0/ boundaryField from the mesh and often leaves
    ``oldInternalFaces`` without the engineering wall types we need.
    Honors fidelity wall_bc (slip/noSlip) and turbulence_model from meta.
    """
    cdir = Path(case_dir)
    meta_path = cdir / "impulsecalc_case_meta.json"
    if not meta_path.is_file():
        return {"ok": False, "reason": "no_meta"}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc)}
    ml_dict = meta.get("meanline") or {}
    inputs = ml_dict.get("inputs") or ml_dict
    try:
        from .meanline import MeanlineInputs, compute_meanline

        inp = MeanlineInputs.from_dict(inputs)
        ml = compute_meanline(inp)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"meanline:{exc}"}
    startup_marker = cdir / "0" / "impulsecalc_startup"
    startup = True
    if startup_marker.is_file():
        startup = "startup" in startup_marker.read_text(encoding="utf-8").lower()
    walls = mesh_has_blade_walls(cdir)
    wall_bc, turb_model, front_back = _case_wall_and_turb_from_meta(meta)
    # Prefer actual polyMesh frontAndBack type if present
    patches_pre = parse_boundary_file(cdir / "constant" / "polyMesh" / "boundary")
    fb_info = patches_pre.get("frontAndBack") or {}
    if fb_info.get("type"):
        front_back = str(fb_info["type"])
    # After createPatch/snappy the mesh has blades — include wall BCs. Pre-subsetMesh
    # must use include_blade_walls=False (see prepare_zero_for_subset_mesh).
    paths = write_fields(
        cdir,
        ml,
        startup=startup,
        include_blade_walls=bool(walls.get("ok")),
        wall_bc=wall_bc,
        turbulence_model=turb_model,
        front_back_type=front_back,
    )
    patches = parse_boundary_file(cdir / "constant" / "polyMesh" / "boundary")
    field_names = list(paths.keys())
    for field_name in field_names:
        fpath = cdir / "0" / field_name
        if not fpath.is_file():
            continue
        text = fpath.read_text(encoding="utf-8")
        text2 = _inject_missing_patch_bcs(text, field_name, patches)
        # If industry noSlip, ensure blades wall type on U
        if field_name == "U" and wall_bc == WALL_NOSLIP and BLADE_WALL_PATCH in text2:
            import re

            text2 = re.sub(
                rf"({BLADE_WALL_PATCH}\s*\{{\s*type\s+)slip(\s*;)",
                rf"\1{WALL_NOSLIP}\2",
                text2,
            )
        fpath.write_text(text2, encoding="utf-8")
    return {
        "ok": True,
        "fields": [str(p) for p in paths.values()],
        "blade_walls": walls,
        "wall_bc": wall_bc,
        "turbulence_model": turb_model,
        "front_back_type": front_back,
        "patches": sorted(patches.keys()),
    }


def prepare_zero_for_subset_mesh(case_dir: str | Path) -> dict[str, Any]:
    """Rewrite 0/ to match *blockMesh* patches only (no blades) before subsetMesh.

    OpenFOAM-12 subsetMesh maps volFields from 0/; if boundaryField lists a
    ``blades`` patch that is not yet on polyMesh, readField aborts with
    GeometricBoundaryField.C:158.
    """
    cdir = Path(case_dir)
    meta_path = cdir / "impulsecalc_case_meta.json"
    if not meta_path.is_file():
        # Best-effort: strip blades/oldInternalFaces lines from existing 0/ fields
        stripped = 0
        for name in ("U", "p", "T"):
            fpath = cdir / "0" / name
            if not fpath.is_file():
                continue
            text = fpath.read_text(encoding="utf-8")
            import re

            text2 = re.sub(
                rf"\s*{BLADE_WALL_PATCH}\s*\{{[^}}]*\}}",
                "",
                text,
                flags=re.S,
            )
            text2 = re.sub(r"\s*oldInternalFaces\s*\{[^}]*\}", "", text2, flags=re.S)
            if text2 != text:
                fpath.write_text(text2, encoding="utf-8")
                stripped += 1
        return {"ok": True, "method": "strip", "n_fields": stripped}

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        ml_dict = meta.get("meanline") or {}
        inputs = ml_dict.get("inputs") or ml_dict
        from .meanline import MeanlineInputs, compute_meanline

        ml = compute_meanline(MeanlineInputs.from_dict(inputs))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc)}

    startup_marker = cdir / "0" / "impulsecalc_startup"
    startup = True
    if startup_marker.is_file():
        startup = "startup" in startup_marker.read_text(encoding="utf-8").lower()
    wall_bc, turb_model, front_back = _case_wall_and_turb_from_meta(meta)
    paths = write_fields(
        cdir,
        ml,
        startup=startup,
        include_blade_walls=False,
        wall_bc=wall_bc,
        turbulence_model=turb_model,
        front_back_type=front_back,
    )
    return {
        "ok": True,
        "method": "rewrite_pre_mesh",
        "fields": [str(p) for p in paths.values()],
        "wall_bc": wall_bc,
        "turbulence_model": turb_model,
        "front_back_type": front_back,
    }
