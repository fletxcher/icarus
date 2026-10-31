#!/usr/bin/env python3
"""Builds, and reads back, one 3D OpenFOAM case around an uploaded solid.

Called by run_cfd3d.sh. Usage:
  cfd3d_case.py mesh <job_dir>                prepare the case, mesh it (blockMesh + snappyHexMesh)
  cfd3d_case.py results <job_dir>             read the solution into status.json and view.json
  cfd3d_case.py status <job_dir> <state> [message]
  cfd3d_case.py info <job_dir>                static facts about the job (for the monitor)
  cfd3d_case.py progress <job_dir> <log_offset> <coeff_offset> [final]
  cfd3d_case.py summary <jobs_dir> <job_id>...

Three kinds of domain (the `domain` of case.json):
  external   a box around the solid in a uniform stream (far sides: freestream conditions)
  passage    one blade passage of a cascade: periodic in the pitch direction, slip walls at the
             span ends, flow in at one end and out of the other
  rotating   external, with a cylinder of fluid around the solid rotating with it (a frozen-rotor
             MRF zone), for propellers, fans and rotors

Incompressible steady RANS (simpleFoam, k-omega SST, wall functions), meshed by snappyHexMesh
from the solid's STL. Lengths inside the case are metres.
`mesh` and `results` need numpy, pyvista and scipy (setup.sh puts them in ~/icarus/venv); the
other commands need nothing.
"""

import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

UNIT_TO_M = {"mm": 1e-3, "m": 1.0, "in": 0.0254}
AXES = {"x": 0, "y": 1, "z": 2}
STAGES = ["starting", "mesh", "solve", "results"]

# base: background cells across the solid's largest dimension; surface: refinement levels of the
# cells at the surface (each level halves the cell); cells: the most the solver should be given;
# iterations: the SIMPLE iterations to run if the residuals haven't converged before.
LEVELS = {
    "coarse": {"base": 3, "surface": (3, 4), "cells": 450_000, "iterations": 500},
    "medium": {"base": 4, "surface": (4, 5), "cells": 1_000_000, "iterations": 800},
    "fine": {"base": 5, "surface": (4, 6), "cells": 1_600_000, "iterations": 1200},
}
LEVEL_ORDER = ["fine", "medium", "coarse"]
HEADER = "FoamFile\n{\n    version 2.0;\n    format ascii;\n    class %s;\n    object %s;\n}\n\n"
BODY = "body"


def read_case(job_dir: Path) -> dict:
    return json.loads((job_dir / "case.json").read_text())


def write_status(job_dir: Path, state: str, message: str | None, result: dict | None = None) -> None:
    payload = {"status": state}
    if message:
        payload["error"] = message
    if result is not None:
        payload["result"] = result
    tmp = job_dir / "status.json.tmp"
    tmp.write_text(json.dumps(payload))
    tmp.replace(job_dir / "status.json")


def foam(path: Path, cls: str, name: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((HEADER % (cls, name)) + body.strip() + "\n")


def vec(v) -> str:
    return "(" + " ".join(f"{x:.8g}" for x in v) + ")"


# --- geometry -------------------------------------------------------------------------------

def load_surface(job_dir: Path, case: dict):
    """The solid's triangles in metres, cleaned, and notes on what was done to them."""
    import numpy as np

    import fea_case

    triangles = fea_case.read_stl(job_dir / "geometry.stl") * UNIT_TO_M[case["units"]]
    if len(triangles) == 0:
        raise SystemExit("geometry.stl has no triangles")
    triangles, notes = fea_case.clean_triangles(triangles)
    if case["fill_cavities"]:
        triangles, envelope_notes = envelope(triangles)
        notes += envelope_notes
    else:
        problems = fea_case.surface_problems(triangles)
        if problems:
            notes.append("the surface isn't a clean closed solid (" + "; ".join(problems) + ")")
    return np.asarray(triangles), notes


def envelope(triangles):
    """The solid's outside: small holes, pits and cavities behind them are filled in.

    A part with internal cooling passages and film-cooling holes would let the flow into the
    passages, and meshing those needs cells far finer than the flow around the part does. The
    solid is voxelised, closed (which plugs holes smaller than a few voxels), filled and turned back
    into a smooth surface.
    """
    import numpy as np
    import pyvista as pv
    from scipy import ndimage

    points = triangles.reshape(-1, 3)
    low, high = points.min(axis=0), points.max(axis=0)
    size = float((high - low).max())
    voxel = size / 220
    pad = 4 * voxel
    dims = np.ceil((high - low + 2 * pad) / voxel).astype(int) + 1
    grid = pv.ImageData(dimensions=tuple(dims), spacing=(voxel,) * 3, origin=tuple(low - pad))
    faces = np.hstack([np.full((len(triangles), 1), 3), np.arange(len(points)).reshape(-1, 3)])
    surface = pv.PolyData(points, faces.ravel())
    inside = grid.select_enclosed_points(surface, tolerance=0.0, check_surface=False)
    occupied = np.asarray(inside.point_data["SelectedPoints"]).reshape(dims, order="F").astype(bool)

    radius = 3  # voxels: plugs holes up to about this size
    axis = np.arange(-radius, radius + 1)
    ball = (axis[:, None, None] ** 2 + axis[None, :, None] ** 2 + axis[None, None, :] ** 2) <= radius**2
    closed = ndimage.binary_closing(occupied, structure=ball, border_value=0)
    filled = ndimage.binary_fill_holes(closed)
    smooth = ndimage.gaussian_filter(filled.astype(np.float32), sigma=1.0)

    grid.point_data["occupancy"] = smooth.ravel(order="F")
    contour = grid.contour([0.5], scalars="occupancy").extract_surface().triangulate().clean()
    contour = contour.connectivity("largest").extract_surface().triangulate()
    if contour.n_cells < 100:
        raise SystemExit("couldn't build an outer surface for the solid (is it a closed solid?)")
    tri = np.asarray(contour.points)[contour.faces.reshape(-1, 4)[:, 1:]]
    removed = float(filled.sum() - occupied.sum()) * voxel**3
    note = (
        f"filled the part's cavities and holes into one outer surface ({removed * 1e9:.0f} mm3 added)"
        if removed > 0
        else "built the part's outer surface"
    )
    return tri, [note]


def write_stl(path: Path, triangles) -> None:
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    norm = np.linalg.norm(cross, axis=1, keepdims=True)
    normals = cross / np.where(norm == 0, 1, norm)
    with path.open("w") as f:
        f.write("solid body\n")
        for n, tri in zip(normals, triangles, strict=True):
            f.write(f"facet normal {n[0]:.6e} {n[1]:.6e} {n[2]:.6e}\n outer loop\n")
            for v in tri:
                f.write(f"  vertex {v[0]:.7e} {v[1]:.7e} {v[2]:.7e}\n")
            f.write(" endloop\nendfacet\n")
        f.write("endsolid body\n")


def projected_area(triangles, direction) -> float:
    """The area the solid shows to a flow along `direction` (half the sum of |n . d| dA)."""
    import numpy as np

    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d)
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    return float(0.5 * np.abs(cross @ d).sum() / 2)


# --- domain ---------------------------------------------------------------------------------

def layout(case: dict, triangles) -> dict:
    """The box around the solid, and the pieces of the domain that depend on the kind of case."""
    import numpy as np

    points = triangles.reshape(-1, 3)
    low, high = points.min(axis=0), points.max(axis=0)
    centre = (low + high) / 2
    direction = np.asarray(case["flow_direction"], float)
    direction = direction / np.linalg.norm(direction)
    flow_axis = int(np.argmax(np.abs(direction)))
    sign = 1 if direction[flow_axis] > 0 else -1
    length = float((high - low).max())
    info: dict = {
        "bbox_min": low.tolist(), "bbox_max": high.tolist(), "centre": centre.tolist(),
        "length": length, "flow_direction": direction.tolist(), "flow_axis": flow_axis,
        "flow_sign": sign,
    }

    zone = None
    if case["domain"] == "rotating":
        origin = np.asarray(case["spin_axis_origin"], float) * UNIT_TO_M[case["units"]]
        axis = np.asarray(case["spin_axis_direction"], float)
        axis = axis / np.linalg.norm(axis)
        along = (points - origin) @ axis
        radial = np.linalg.norm((points - origin) - np.outer(along, axis), axis=1)
        radius = 1.25 * float(radial.max())
        pad = 0.25 * radius
        zone = {
            "origin": origin.tolist(), "axis": axis.tolist(), "radius": radius,
            "p1": (origin + axis * (along.min() - pad)).tolist(),
            "p2": (origin + axis * (along.max() + pad)).tolist(),
        }
        info["zone"] = zone
        length = max(length, 2 * radius)
        info["length"] = length

    # A passage is a short box (the cascade's own inflow and outflow); the rest sit in open flow.
    upstream, downstream = (1.5, 3.0) if case["domain"] == "passage" else (3.0, 7.0)
    upstream, downstream, side = upstream * length, downstream * length, 2.5 * length
    box_low, box_high = np.empty(3), np.empty(3)
    for axis_index in range(3):
        if axis_index == flow_axis:
            near, far = (upstream, downstream) if sign > 0 else (downstream, upstream)
            box_low[axis_index] = low[axis_index] - near
            box_high[axis_index] = high[axis_index] + far
        else:
            box_low[axis_index] = centre[axis_index] - side
            box_high[axis_index] = centre[axis_index] + side
    if zone is not None:  # the whole rotating cylinder must lie inside the box
        for axis_index in range(3):
            reach = zone["radius"] + 0.1 * length
            box_low[axis_index] = min(box_low[axis_index], min(zone["p1"][axis_index], zone["p2"][axis_index]) - reach)
            box_high[axis_index] = max(box_high[axis_index], max(zone["p1"][axis_index], zone["p2"][axis_index]) + reach)
    if case["domain"] == "passage":
        pitch_axis = AXES[case["pitch_axis"]]
        if pitch_axis == flow_axis:
            raise SystemExit("the pitch direction can't be the flow direction")
        pitch = case["pitch"] * UNIT_TO_M[case["units"]]
        thickness = float(high[pitch_axis] - low[pitch_axis])
        if pitch <= thickness * 1.05:
            raise SystemExit(
                f"the pitch ({case['pitch']} {case['units']}) is no larger than the solid's own width "
                "in that direction: neighbouring blades would overlap"
            )
        box_low[pitch_axis] = centre[pitch_axis] - pitch / 2
        box_high[pitch_axis] = centre[pitch_axis] + pitch / 2
        info["pitch_axis"] = pitch_axis
        info["pitch"] = pitch
        info["free_distance"] = (pitch - thickness) / 2  # from the solid to a periodic face
        span_axis = [a for a in range(3) if a not in (flow_axis, pitch_axis)][0]
        info["span_axis"] = span_axis
        # Slip walls a little beyond the solid's ends along its span.
        box_low[span_axis] = low[span_axis] - 0.15 * length
        box_high[span_axis] = high[span_axis] + 0.15 * length
    info["box_low"], info["box_high"] = box_low.tolist(), box_high.tolist()
    info["frontal_area"] = projected_area(triangles, direction)
    return info


FACES = {  # a block's faces, wound so each normal points out of the box
    (0, "min"): (0, 4, 7, 3), (0, "max"): (1, 2, 6, 5),
    (1, "min"): (0, 1, 5, 4), (1, "max"): (3, 7, 6, 2),
    (2, "min"): (0, 3, 2, 1), (2, "max"): (4, 5, 6, 7),
}


def block_mesh_dict(case: dict, geo: dict, level: dict) -> str:
    length = geo["length"]
    cell = length / level["base"]
    low, high = geo["box_low"], geo["box_high"]
    counts = []
    for a in range(3):
        n = max(4, math.ceil((high[a] - low[a]) / cell))
        counts.append(n + (n % 2))
    flow_axis, sign = geo["flow_axis"], geo["flow_sign"]
    inlet_side = "min" if sign > 0 else "max"
    outlet_side = "max" if sign > 0 else "min"
    corners = [
        (low[0], low[1], low[2]), (high[0], low[1], low[2]), (high[0], high[1], low[2]),
        (low[0], high[1], low[2]), (low[0], low[1], high[2]), (high[0], low[1], high[2]),
        (high[0], high[1], high[2]), (low[0], high[1], high[2]),
    ]  # fmt: skip

    def face(axis, side):
        return "(" + " ".join(map(str, FACES[(axis, side)])) + ")"

    patches = [
        ("inlet", "patch", [face(flow_axis, inlet_side)], None),
        ("outlet", "patch", [face(flow_axis, outlet_side)], None),
    ]
    others = [a for a in range(3) if a != flow_axis]
    if case["domain"] == "passage":
        pitch_axis, span_axis = geo["pitch_axis"], geo["span_axis"]
        patches += [
            ("periodicMin", "cyclic", [face(pitch_axis, "min")], "periodicMax"),
            ("periodicMax", "cyclic", [face(pitch_axis, "max")], "periodicMin"),
            ("spanWalls", "wall", [face(span_axis, "min"), face(span_axis, "max")], None),
        ]
    else:
        faces = [face(a, side) for a in others for side in ("min", "max")]
        patches.append(("far", "patch", faces, None))
    boundary = ""
    for name, kind, faces, neighbour in patches:
        boundary += f"    {name}\n    {{\n        type {kind};\n"
        if neighbour:
            boundary += f"        neighbourPatch {neighbour};\n"
        boundary += "        faces\n        (\n" + "".join(f"            {f}\n" for f in faces) + "        );\n    }\n"
    vertices = "\n".join(f"    {vec(c)}" for c in corners)
    return f"""
convertToMeters 1;
vertices
(
{vertices}
);
blocks ( hex (0 1 2 3 4 5 6 7) ({counts[0]} {counts[1]} {counts[2]}) simpleGrading (1 1 1) );
edges ();
boundary
(
{boundary});
mergePatchPairs ();
"""


def snappy_dict(case: dict, geo: dict, level: dict, cells: int, mesh_cell: float) -> str:
    length = geo["length"]
    low, high = geo["box_low"], geo["box_high"]
    s_min, s_max = level["surface"]
    # A point in the fluid, near the upstream corner and off every cell face.
    inside = [low[a] + (0.337, 0.411, 0.523)[a] * mesh_cell for a in range(3)]
    geometry = f"    {BODY}.stl {{ type triSurfaceMesh; name {BODY}; }}\n"
    regions = [
        (0.02 * length, max(s_max - 1, 1)),
        (0.08 * length, max(s_max - 2, 1)),
        (0.3 * length, max(s_max - 3, 1)),
    ]
    if case["domain"] == "passage":
        # Refinement must not reach the periodic faces: it would differ from one to the other.
        limit = 0.8 * geo["free_distance"]
        regions = [(d, lv) for d, lv in regions if d < limit] or [(min(regions[0][0], limit), regions[0][1])]
    region_text = "".join(f"({d:.6g} {lv}) " for d, lv in regions)
    refinement_regions = f"        {BODY} {{ mode distance; levels ({region_text}); }}\n"
    surfaces = f"        {BODY} {{ level ({s_min} {s_max}); patchInfo {{ type wall; }} }}\n"
    if case["domain"] == "external":
        # A wake box behind the solid, along the flow.
        a, sign = geo["flow_axis"], geo["flow_sign"]
        w_low, w_high = list(geo["bbox_min"]), list(geo["bbox_max"])
        for b in range(3):
            if b != a:
                mid = geo["centre"][b]
                w_low[b], w_high[b] = mid - 0.7 * length, mid + 0.7 * length
        if sign > 0:
            w_low[a], w_high[a] = geo["bbox_min"][a], geo["bbox_max"][a] + 3.5 * length
        else:
            w_low[a], w_high[a] = geo["bbox_min"][a] - 3.5 * length, geo["bbox_max"][a]
        geometry += f"    wake {{ type searchableBox; min {vec(w_low)}; max {vec(w_high)}; }}\n"
        refinement_regions += f"        wake {{ mode inside; levels ((1e15 {max(s_max - 3, 1)})); }}\n"
    if case["domain"] == "rotating":
        z = geo["zone"]
        geometry += (
            f"    rotor {{ type searchableCylinder; point1 {vec(z['p1'])}; point2 {vec(z['p2'])}; "
            f"radius {z['radius']:.8g}; }}\n"
        )
        surfaces += (
            "        rotor { level (2 2); faceZone rotatingZone; cellZone rotatingZone; "
            "cellZoneInside inside; }\n"
        )
        refinement_regions += f"        rotor {{ mode inside; levels ((1e15 {max(s_max - 2, 1)})); }}\n"
    return f"""
castellatedMesh true;
snap true;
addLayers false;
geometry
{{
{geometry}}};
castellatedMeshControls
{{
    maxLocalCells {cells};
    maxGlobalCells {cells};
    minRefinementCells 10;
    maxLoadUnbalance 0.2;
    nCellsBetweenLevels 3;
    features ();
    refinementSurfaces
    {{
{surfaces}    }}
    resolveFeatureAngle 30;
    refinementRegions
    {{
{refinement_regions}    }}
    locationInMesh {vec(inside)};
    allowFreeStandingZoneFaces true;
}}
snapControls
{{
    nSmoothPatch 3;
    tolerance 2.0;
    nSolveIter 60;
    nRelaxIter 5;
    nFeatureSnapIter 10;
    implicitFeatureSnap false;
    explicitFeatureSnap false;
    multiRegionFeatureSnap false;
}}
addLayersControls {{ layers {{}} relativeSizes true; expansionRatio 1.2; finalLayerThickness 0.5; minThickness 0.1; nGrow 0; featureAngle 60; nRelaxIter 3; nSmoothSurfaceNormals 1; nSmoothNormals 3; nSmoothThickness 10; maxFaceThicknessRatio 0.5; maxThicknessToMedialRatio 0.3; minMedialAxisAngle 90; nBufferCellsNoExtrude 0; nLayerIter 50; }}
meshQualityControls
{{
    maxNonOrtho 65;
    maxBoundarySkewness 20;
    maxInternalSkewness 4;
    maxConcave 80;
    minVol 1e-13;
    minTetQuality -1e30;
    minArea -1;
    minTwist 0.02;
    minDeterminant 0.001;
    minFaceWeight 0.05;
    minVolRatio 0.01;
    minTriangleTwist -1;
    nSmoothScale 4;
    errorReduction 0.75;
    relaxed {{ maxNonOrtho 75; }}
}}
mergeTolerance 1e-6;
"""


# --- the solver case ------------------------------------------------------------------------

def write_numerics(job_dir: Path) -> None:
    """The discretisation and solver settings (snappyHexMesh reads them too)."""
    foam(job_dir / "system/fvSchemes", "dictionary", "fvSchemes", """
ddtSchemes { default steadyState; }
gradSchemes { default Gauss linear; }
divSchemes
{
    default none;
    div(phi,U) bounded Gauss linearUpwind grad(U);
    div(phi,k) bounded Gauss upwind;
    div(phi,omega) bounded Gauss upwind;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
wallDist { method meshWave; }
""")
    foam(job_dir / "system/fvSolution", "dictionary", "fvSolution", """
solvers
{
    p { solver GAMG; smoother GaussSeidel; tolerance 1e-8; relTol 0.05; }
    "(U|k|omega)" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-9; relTol 0.1; }
}
SIMPLE
{
    nNonOrthogonalCorrectors 1;
    consistent yes;
    residualControl { p 1e-4; U 1e-5; "(k|omega)" 1e-5; }
}
relaxationFactors
{
    equations { U 0.9; k 0.8; omega 0.8; ".*" 0.9; }
}
""")


def write_control(job_dir: Path, case: dict, geo: dict, iterations: int) -> None:
    rho = case["density_kg_m3"]
    centre = geo["centre"]
    foam(job_dir / "system/controlDict", "dictionary", "controlDict", f"""
application simpleFoam;
startFrom startTime;
startTime 0;
stopAt endTime;
endTime {iterations};
deltaT 1;
writeControl timeStep;
writeInterval {iterations};
purgeWrite 1;
writeFormat binary;
runTimeModifiable true;
functions
{{
    forces
    {{
        type forces;
        libs (forces);
        writeControl timeStep;
        writeInterval 5;
        patches ({BODY});
        rho rhoInf;
        rhoInf {rho};
        CofR {vec(centre)};
    }}
    inletFlow
    {{
        type surfaceFieldValue;
        libs (fieldFunctionObjects);
        operation sum;
        regionType patch;
        name inlet;
        fields (phi);
        writeFields false;
        writeControl timeStep;
        writeInterval 10;
    }}
    outletFlow
    {{
        type surfaceFieldValue;
        libs (fieldFunctionObjects);
        operation sum;
        regionType patch;
        name outlet;
        fields (phi);
        writeFields false;
        writeControl timeStep;
        writeInterval 10;
    }}
}}
""")


def write_solver_case(job_dir: Path, case: dict, geo: dict, mesh_info: dict, iterations: int) -> None:
    speed = case["speed_ms"]
    d = geo["flow_direction"]
    u = [speed * c for c in d]
    length = geo["length"]
    intensity = case["turbulence_intensity"]
    k = 1.5 * (intensity * speed) ** 2
    mixing = 0.07 * length  # a turbulence length scale of 7% of the solid's size
    omega = math.sqrt(k) / (0.09**0.25 * mixing)
    nu = case["kinematic_viscosity_m2_s"]
    rho = case["density_kg_m3"]
    centre = geo["centre"]

    write_control(job_dir, case, geo, iterations)
    write_numerics(job_dir)
    foam(job_dir / "system/decomposeParDict", "dictionary", "decomposeParDict",
         f"numberOfSubdomains {int(os.environ.get('ICARUS_NPROC', '4'))};\nmethod scotch;")
    foam(job_dir / "constant/transportProperties", "dictionary", "transportProperties",
         f"transportModel Newtonian;\nnu [0 2 -1 0 0 0 0] {nu:.6e};")
    for name in ("momentumTransport", "turbulenceProperties"):
        foam(job_dir / "constant" / name, "dictionary", name,
             "simulationType RAS;\nRAS { model kOmegaSST; turbulence on; printCoeffs on; }")
    if case["domain"] == "rotating":
        z = geo["zone"]
        omega_rot = case["spin_rpm"] * 2 * math.pi / 60
        foam(job_dir / "constant/MRFProperties", "dictionary", "MRFProperties", f"""
MRF1
{{
    cellZone rotatingZone;
    active yes;
    nonRotatingPatches ();
    origin {vec(z['origin'])};
    axis {vec(z['axis'])};
    omega {omega_rot:.8g};
}}
""")

    def field(name: str, cls: str, dim: str, internal: str, patches: dict[str, str]) -> None:
        body = f"dimensions {dim};\ninternalField uniform {internal};\nboundaryField\n{{\n"
        for patch, spec in patches.items():
            body += f"    {patch}\n    {{\n        {spec}\n    }}\n"
        foam(job_dir / "0" / name, cls, name, body + "}\n")

    passage = case["domain"] == "passage"
    sides = (
        {"periodicMin": "type cyclic;", "periodicMax": "type cyclic;"}
        if passage else {}
    )
    far = (lambda v, free: {"spanWalls": v}) if passage else (lambda v, free: {"far": free})
    field("U", "volVectorField", "[0 1 -1 0 0 0 0]", vec(u), {
        "inlet": f"type fixedValue;\n        value uniform {vec(u)};",
        "outlet": "type zeroGradient;",
        BODY: "type noSlip;", **sides,
        **far("type slip;", f"type freestreamVelocity;\n        freestreamValue uniform {vec(u)};"),
    })
    field("p", "volScalarField", "[0 2 -2 0 0 0 0]", "0", {
        "inlet": "type zeroGradient;",
        "outlet": "type fixedValue;\n        value uniform 0;",
        BODY: "type zeroGradient;", **sides,
        **far("type zeroGradient;", "type freestreamPressure;\n        freestreamValue uniform 0;\n        supersonic false;"),
    })
    for name, value, dim in (("k", k, "[0 2 -2 0 0 0 0]"), ("omega", omega, "[0 0 -1 0 0 0 0]")):
        wall = "kqRWallFunction" if name == "k" else "omegaWallFunction"
        field(name, "volScalarField", dim, f"{value:.6g}", {
            "inlet": f"type fixedValue;\n        value uniform {value:.6g};",
            "outlet": f"type inletOutlet;\n        inletValue uniform {value:.6g};\n        value uniform {value:.6g};",
            BODY: f"type {wall};\n        value uniform {value:.6g};", **sides,
            **far("type zeroGradient;", f"type inletOutlet;\n        inletValue uniform {value:.6g};\n        value uniform {value:.6g};"),
        })
    field("nut", "volScalarField", "[0 2 -1 0 0 0 0]", "1e-5", {
        "inlet": "type calculated;\n        value uniform 0;", "outlet": "type calculated;\n        value uniform 0;",
        BODY: "type nutUSpaldingWallFunction;\n        value uniform 0;", **sides,
        **far("type zeroGradient;", "type calculated;\n        value uniform 0;"),
    })


# --- meshing --------------------------------------------------------------------------------

def run(job_dir: Path, name: str, command: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    """Runs an OpenFOAM tool in the job, logging to log.<name>."""
    with (job_dir / f"log.{name}").open("w") as log:
        return subprocess.run(command, cwd=job_dir, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)


def mesh_cells(job_dir: Path) -> int | None:
    text = (job_dir / "log.checkMesh").read_text() if (job_dir / "log.checkMesh").exists() else ""
    match = re.search(r"cells:\s+(\d+)", text)
    return int(match.group(1)) if match else None


def mesh_ok(job_dir: Path) -> bool:
    text = (job_dir / "log.checkMesh").read_text() if (job_dir / "log.checkMesh").exists() else ""
    return "Mesh OK" in text


def clear_mesh(job_dir: Path) -> None:
    import shutil

    for name in ("constant/polyMesh", "processor0"):
        shutil.rmtree(job_dir / name, ignore_errors=True)
    for path in job_dir.glob("[1-9]*"):
        shutil.rmtree(path, ignore_errors=True)


def do_mesh(job_dir: Path) -> None:
    case = read_case(job_dir)
    triangles, notes = load_surface(job_dir, case)
    write_stl(job_dir / "constant/triSurface" / f"{BODY}.stl", triangles)
    geo = layout(case, triangles)
    start = LEVEL_ORDER.index(case["mesh"])
    tried = []
    for name in LEVEL_ORDER[start:]:
        level = LEVELS[name]
        clear_mesh(job_dir)
        # The snappyHexMesh settings and the background mesh follow the level being tried.
        cell = geo["length"] / level["base"]
        foam(job_dir / "system/blockMeshDict", "dictionary", "blockMeshDict", block_mesh_dict(case, geo, level))
        foam(job_dir / "system/snappyHexMeshDict", "dictionary", "snappyHexMeshDict",
             snappy_dict(case, geo, level, level["cells"], cell))
        write_control(job_dir, case, geo, 1)
        write_numerics(job_dir)
        if run(job_dir, "blockMesh", ["blockMesh"]).returncode != 0:
            raise SystemExit("blockMesh failed: " + (job_dir / "log.blockMesh").read_text()[-400:])
        result = run(job_dir, "snappyHexMesh", ["snappyHexMesh", "-overwrite"])
        failed = result.returncode != 0
        if not failed:
            run(job_dir, "checkMesh", ["checkMesh"])
            cells = mesh_cells(job_dir)
            failed = cells is None or not mesh_ok(job_dir)
            if cells is not None and cells > level["cells"] * 1.1:
                failed = True
        tried.append(name)
        if not failed:
            geo["mesh_level"] = name
            break
        notes.append(f"the {name} mesh didn't work out (see log.snappyHexMesh); trying a coarser one")
    else:
        raise SystemExit(
            "couldn't mesh the solid at any level: " + (job_dir / "log.snappyHexMesh").read_text()[-500:]
        )
    cells = mesh_cells(job_dir) or 0
    if tried[0] != geo["mesh_level"]:
        notes.append(f"meshed at the {geo['mesh_level']} level instead of {tried[0]}")
    level = LEVELS[geo["mesh_level"]]
    iterations = case.get("iterations") or level["iterations"]
    write_solver_case(job_dir, case, geo, {"cells": cells}, iterations)
    if case["domain"] == "rotating":
        zones = (job_dir / "log.snappyHexMesh").read_text()
        if "rotatingZone" not in zones:
            notes.append("the rotating zone doesn't appear in the mesh log; check the spin axis")
    (job_dir / "mesh.json").write_text(json.dumps({
        "cells": cells, "level": geo["mesh_level"], "iterations": iterations, "notes": notes, "geometry": geo,
    }))
    print(f"{cells:,} cells at the {geo['mesh_level']} level; {iterations} iterations")
    for note in notes:
        print("note:", note)


# --- results ----------------------------------------------------------------------------------

def last_float_row(path: Path):
    """The last numeric row of a function object's .dat file (None if there isn't one)."""
    if not path.exists():
        return None
    rows = [line.split() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]
    return [float(v) for v in rows[-1]] if rows else None


def read_series(job_dir: Path, folder: str, filename: str):
    """Every row of a function object's output across restarts, as an array."""
    import numpy as np

    rows = []
    base = job_dir / "postProcessing" / folder
    for part in sorted(base.glob("*"), key=lambda p: float(p.name) if re.match(r"^[\d.]+$", p.name) else 0):
        path = part / filename
        if path.exists():
            rows += [
                [float(v) for v in line.replace("(", " ").replace(")", " ").split()]
                for line in path.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            ]
    return np.array(rows) if rows else None


def solver_summary(job_dir: Path) -> dict:
    text = (job_dir / "log.solver").read_text(errors="replace") if (job_dir / "log.solver").exists() else ""
    times = re.findall(r"^Time = (\d+)", text, re.M)
    residuals = {}
    for field in ("p", "Ux", "Uy", "Uz", "k", "omega"):
        found = re.findall(rf"Solving for {field}, Initial residual = ([0-9.eE+-]+)", text)
        if found:
            residuals[field] = float(found[-1])
    converged = re.search(r"SIMPLE solution converged in (\d+) iterations", text)
    return {
        "iterations": int(converged.group(1)) if converged else (int(times[-1]) if times else 0),
        "converged": bool(converged),
        "residuals": residuals,
    }


def y_plus(job_dir: Path) -> dict | None:
    text = (job_dir / "log.yPlus").read_text(errors="replace") if (job_dir / "log.yPlus").exists() else ""
    match = re.search(rf"patch {BODY} y\+ : min = ([0-9.eE+-]+), max = ([0-9.eE+-]+), average = ([0-9.eE+-]+)", text)
    if not match:
        return None
    low, high, mean = (float(v) for v in match.groups())
    return {"min": low, "max": high, "average": mean}


def vtk_patch(job_dir: Path, patch: str):
    try:
        import pyvista as pv
    except ImportError:  # no 3D view, and no inlet/outlet averages, without it
        return None

    found = sorted(job_dir.glob(f"VTK/*/boundary/{patch}.vtp"), key=lambda p: p.stat().st_mtime)
    return pv.read(found[-1]) if found else None


def flux_averages(patch):
    """Flux-weighted averages over an inlet or outlet patch: static and total pressure (kinematic)
    and the flow angle, and the volume flow."""
    import numpy as np

    patch = patch.compute_cell_sizes(length=False, volume=False, area=True)
    area = np.asarray(patch.cell_data["Area"])
    u = np.asarray(patch.cell_data["U"])
    p = np.asarray(patch.cell_data["p"])
    normals = np.asarray(patch.compute_normals(cell_normals=True, point_normals=False).cell_data["Normals"])
    flux = np.abs(np.einsum("ij,ij->i", u, normals)) * area
    weight = flux / flux.sum()
    speed2 = np.einsum("ij,ij->i", u, u)
    return {
        "volume_flow": float(flux.sum()),
        "p": float((p * weight).sum()),
        "p0": float(((p + 0.5 * speed2) * weight).sum()),
        "velocity": (u * weight[:, None]).sum(axis=0).tolist(),
    }


def build_view(job_dir: Path, case: dict, geo: dict) -> dict | None:
    """The solid's surface with the pressure (and wall shear) on it, for the browser."""
    import base64

    import numpy as np

    body = vtk_patch(job_dir, BODY)
    if body is None:
        return None
    rho, speed = case["density_kg_m3"], case["speed_ms"]
    body = body.cell_data_to_point_data().triangulate().clean()
    pressure = np.asarray(body.point_data["p"]) * rho
    fields = {"pressure": (pressure, "Pa")}
    if case["domain"] != "rotating":
        fields["pressure_coefficient"] = (pressure / (0.5 * rho * speed**2), "")
    if "wallShearStress" in body.point_data:
        shear = np.linalg.norm(np.asarray(body.point_data["wallShearStress"]), axis=1) * rho
        fields["wall_shear"] = (shear, "Pa")
    enc = lambda a, t: base64.b64encode(np.ascontiguousarray(a, dtype=t).tobytes()).decode()  # noqa: E731
    points = np.asarray(body.points)
    faces = body.faces.reshape(-1, 4)[:, 1:]
    scale = 1e3  # the viewer shows the part in millimetres, like the FEA view
    return {
        "vertices": int(len(points)),
        "triangles": int(len(faces)),
        "position": enc(points * scale, "<f4"),
        "displacement_vector": enc(np.zeros_like(points), "<f4"),
        "index": enc(faces, "<u4"),
        "fields": {
            name: {
                "values": enc(values, "<f4"),
                "min": float(values.min()),
                "max": float(values.max()),
                "p98": float(np.percentile(values, 98)),
                "unit": unit,
            }
            for name, (values, unit) in fields.items()
        },
        "bbox_min": (points.min(axis=0) * scale).tolist(),
        "bbox_max": (points.max(axis=0) * scale).tolist(),
    }


def build_results(job_dir: Path) -> tuple[dict, dict | None]:
    import numpy as np

    case = read_case(job_dir)
    meta = json.loads((job_dir / "mesh.json").read_text())
    geo = meta["geometry"]
    rho, speed = case["density_kg_m3"], case["speed_ms"]
    direction = np.asarray(geo["flow_direction"])
    dynamic = 0.5 * rho * speed**2
    area = geo["frontal_area"]
    series = read_series(job_dir, "forces", "force.dat")
    moments = read_series(job_dir, "forces", "moment.dat")
    if series is None:
        raise SystemExit("the solver wrote no forces; did it run?")
    window = series[int(len(series) * 0.8):]  # the last fifth of the run
    force = window[:, 1:4].mean(axis=0)
    spread = float(np.abs(window[:, 1:4]).max(axis=0).max())
    wobble = float(np.abs(window[:, 1:4] - force).max() / max(np.linalg.norm(force), 1e-12))
    drag = float(force @ direction)
    side = force - drag * direction
    solver = solver_summary(job_dir)
    result: dict = {
        "domain": case["domain"],
        "cells": meta["cells"],
        "mesh_level": meta["level"],
        "iterations": solver["iterations"],
        "converged": solver["converged"],
        "final_residuals": solver["residuals"],
        "reference_length_m": round(geo["length"], 6),
        "reference_area_m2": round(area, 8),
        "dynamic_pressure_pa": round(dynamic, 3),
        "force_n": [round(float(v), 6) for v in force],
        "drag_n": round(drag, 6),
        "cross_force_n": round(float(np.linalg.norm(side)), 6),
        "drag_coefficient": round(drag / (dynamic * area), 5) if area else None,
        "cross_force_coefficient": round(float(np.linalg.norm(side)) / (dynamic * area), 5) if area else None,
        "force_variation": round(wobble, 5),
    }
    if moments is not None:
        result["moment_nm"] = [round(float(v), 6) for v in moments[int(len(moments) * 0.8):, 1:4].mean(axis=0)]
    yp = y_plus(job_dir)
    if yp:
        result["y_plus"] = yp

    warnings = list(meta["notes"])
    if not solver["converged"]:
        warnings.append(
            f"the residuals hadn't converged after {solver['iterations']} iterations (final residuals "
            + ", ".join(f"{k} {v:.1e}" for k, v in solver["residuals"].items())
            + "); the forces are the average of the last fifth of the run"
        )
    if wobble > 0.05:
        warnings.append(f"the force was still varying by {wobble * 100:.0f}% at the end: treat it as approximate")
    if speed > 0.3 * case["speed_of_sound_ms"]:
        warnings.append(
            f"the speed is {speed / case['speed_of_sound_ms']:.2f} of the speed of sound; the solver is "
            "incompressible, which is good to a few percent below Mach 0.3 and increasingly wrong above it"
        )
    if yp and (yp["average"] > 300 or yp["average"] < 20):
        warnings.append(
            f"wall y+ averages {yp['average']:.0f}; wall functions want 30 to 300, so skin friction "
            "and flow separation are only roughly right"
        )

    inlet, outlet = vtk_patch(job_dir, "inlet"), vtk_patch(job_dir, "outlet")
    if case["domain"] == "passage" and inlet is not None and outlet is not None:
        a, b = flux_averages(inlet), flux_averages(outlet)
        axis = geo["flow_axis"]
        pitch_axis = geo["pitch_axis"]
        angle = lambda v: math.degrees(math.atan2(v[pitch_axis], abs(v[axis])))  # noqa: E731
        result["total_pressure_loss_coefficient"] = round((a["p0"] - b["p0"]) / max(b["p0"] - b["p"], 1e-12), 5)
        result["inlet_flow_angle_deg"] = round(angle(a["velocity"]), 2)
        result["exit_flow_angle_deg"] = round(angle(b["velocity"]), 2)
        result["flow_turning_deg"] = round(angle(b["velocity"]) - angle(a["velocity"]), 2)
        result["mass_balance_error"] = round(abs(a["volume_flow"] - b["volume_flow"]) / a["volume_flow"], 5)
        pitch_force = float(force[pitch_axis])
        result["blade_force_per_span_n_per_m"] = round(pitch_force / (geo["bbox_max"][geo["span_axis"]] - geo["bbox_min"][geo["span_axis"]]), 3)
        if result["mass_balance_error"] > 0.02:
            warnings.append(f"inlet and outlet flows differ by {result['mass_balance_error'] * 100:.1f}%: not converged")
    if case["domain"] == "rotating":
        z = geo["zone"]
        axis = np.asarray(z["axis"])
        omega = case["spin_rpm"] * 2 * math.pi / 60
        torque = float(np.asarray(result["moment_nm"]) @ axis)
        thrust = float(force @ axis)
        diameter = 2 * z["radius"] / 1.25
        result.update({
            "spin_rpm": case["spin_rpm"],
            "thrust_n": round(thrust, 6),
            "torque_nm": round(torque, 6),
            "shaft_power_w": round(abs(torque * omega), 4),
            "diameter_m": round(diameter, 5),
        })
        n = case["spin_rpm"] / 60
        if n:
            result["thrust_coefficient"] = round(thrust / (rho * n**2 * diameter**4), 5)
            result["power_coefficient"] = round(abs(torque * omega) / (rho * n**3 * diameter**5), 5)
            result["advance_ratio"] = round(speed / (n * diameter), 4)
        if abs(torque * omega) > 0 and speed > 0:
            result["propulsive_efficiency"] = round(thrust * speed / abs(torque * omega), 4)
        warnings.append("rotating cases use a frozen-rotor (MRF) model: the blades are held in one position, so unsteady effects are not captured")
    result["warnings"] = warnings
    view = build_view(job_dir, case, geo)
    return result, view


def describe_results(result: dict) -> str:
    text = f"drag {result['drag_n']:.4g} N (Cd {result['drag_coefficient']}), cross force {result['cross_force_n']:.4g} N"
    if "total_pressure_loss_coefficient" in result:
        text += f", loss coefficient {result['total_pressure_loss_coefficient']}"
    if "thrust_n" in result:
        text += f", thrust {result['thrust_n']:.4g} N, torque {result['torque_nm']:.4g} N m"
    return text


def do_results(job_dir: Path) -> None:
    result, view = build_results(job_dir)
    if view is not None:  # written first so the browser finds it as soon as the job shows completed
        (job_dir / "view.json").write_text(json.dumps(view))
    write_status(job_dir, "completed", None, result)
    print(describe_results(result))


# --- monitor (what the backend asks while a job runs) ----------------------------------------------

def read_status(job_dir: Path) -> dict:
    try:
        return json.loads((job_dir / "status.json").read_text())
    except (OSError, ValueError):
        return {"status": "running"}


def current_stage(job_dir: Path, status: str) -> str:
    if status == "completed":
        return "results"
    present = [s for s in STAGES if s != "starting" and (job_dir / f"log.{s}").exists()]
    return present[-1] if present else "starting"


def solve_fraction(job_dir: Path) -> float:
    try:
        total = json.loads((job_dir / "mesh.json").read_text())["iterations"]
    except (OSError, ValueError, KeyError):
        return 0.0
    return min(solver_summary(job_dir)["iterations"] / max(total, 1), 1.0)


def build_info(job_dir: Path) -> dict:
    info = {"case": read_case(job_dir), "stages_expected": [s for s in STAGES if s != "starting"]}
    mesh = job_dir / "mesh.json"
    if mesh.exists():
        meta = json.loads(mesh.read_text())
        info["mesh"] = {"cells": meta["cells"], "level": meta["level"], "notes": meta["notes"]}
    return info


def build_progress(job_dir: Path, log_offset: int, final: bool) -> dict:
    status = read_status(job_dir)["status"]
    stage = current_stage(job_dir, status)
    log = job_dir / ("log.solver" if stage == "solve" else f"log.{stage}")
    lines = log.read_text(errors="replace").splitlines()[-12:] if log.exists() else []
    expected = [s for s in STAGES if s != "starting"]
    index = expected.index(stage) if stage in expected else 0
    weights = {"mesh": (0.0, 0.25), "solve": (0.25, 0.9), "results": (0.9, 0.97)}
    start, end = weights.get(stage, (0.0, 0.0))
    fraction = 1.0 if status == "completed" else start + (end - start) * (solve_fraction(job_dir) if stage == "solve" else 0.5)
    del index
    return {"stage": stage, "fraction": round(fraction if status == "completed" else min(fraction, 0.97), 3), "eta_s": None,
            "log_offset": log_offset, "coeff_offset": 0, "lines": lines}


def build_summary(jobs_dir: Path, job_ids: list[str]) -> dict:
    out = {}
    for job_id in job_ids:
        job_dir = jobs_dir / job_id
        if not job_dir.is_dir():
            continue
        progress = build_progress(job_dir, 0, False)
        out[job_id] = {"status": read_status(job_dir)["status"], "stage": progress["stage"],
                       "fraction": progress["fraction"], "eta_s": None}
    return out


def main(argv: list[str]) -> None:
    if len(argv) < 3:
        raise SystemExit(__doc__)
    command, job_dir = argv[1], Path(argv[2])
    if command == "mesh":
        do_mesh(job_dir)
    elif command == "results":
        do_results(job_dir)
    elif command == "info":
        print(json.dumps(build_info(job_dir)))
    elif command == "summary":
        print(json.dumps(build_summary(job_dir, argv[3:])))
    elif command == "progress":
        print(json.dumps(build_progress(job_dir, int(argv[3]), len(argv) > 5 and argv[5] == "final")))
    elif command == "status":
        write_status(job_dir, argv[3], argv[4] if len(argv) > 4 else None)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
