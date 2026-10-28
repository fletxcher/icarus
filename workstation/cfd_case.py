#!/usr/bin/env python3
"""Builds, and reads back, one OpenFOAM 2D case. Called by run_openfoam.sh.

Standard library only, so it runs on a fresh work station.

  cfd_case.py geo      <job_dir>            write mesh/body.geo (gmsh) from case.json
  cfd_case.py foam     <job_dir> [procs]    write the OpenFOAM dictionaries and 0/ fields
  cfd_case.py boundary <job_dir>            mark the patches gmshToFoam made as wall/empty
  cfd_case.py results  <job_dir>            write the finished status.json with the results
  cfd_case.py status   <job_dir> <state> [message]   write status.json atomically
  cfd_case.py info     <job_dir>            print the static facts about a job (JSON)
  cfd_case.py summary  <jobs_dir> <job_id>...   print each job's status and progress (JSON)
  cfd_case.py progress <job_dir> <log_offset> <coeff_offset> [final]
                                            print what the run has done since those offsets
                                            (JSON), for the live monitor

Incompressible flow (simpleFoam for steady cases, pimpleFoam for transient ones) around
a body of reference length 1 in a unit freestream, so the Reynolds number sets the
viscosity and the coefficients need no scaling. The reference length is the chord for
airfoils and the frontal height of the body otherwise (the diameter of a cylinder, the
base of a wedge). Mach isn't used; build_results flags it when it matters.

The patches are `body`, `farfield` and `frontAndBack` (empty: the mesh is one cell thick).
"""

import json
import math
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

SPAN = 0.1  # extrusion depth of the one-cell-thick 2D mesh
FARFIELD_RADIUS = 25.0  # in reference lengths (more for long bodies)
TURBULENCE_INTENSITY = 0.001
VISCOSITY_RATIO = 10.0  # freestream nut / nu
LAMINAR_BELOW_REYNOLDS = 1000.0
MAX_HISTORY_POINTS = 200
# Convective times (reference length / freestream speed) a transient run covers. The
# environment variable shortens it, e.g. for a quick check of a new case.
TRANSIENT_END_TIME = float(os.environ.get("ICARUS_TRANSIENT_END_TIME", "80"))
MAX_COURANT = 1.0
VIDEO_INTERVAL = 0.25  # convective times between the snapshots a video is rendered from

# Surface spacing, points per airfoil side, farfield spacing, boundary layer growth
# and the iteration limit of steady runs.
MESHES = {
    "coarse": {"ds": 0.012, "points": 70, "far": 3.0, "growth": 1.25, "iterations": 2500},
    "medium": {"ds": 0.009, "points": 90, "far": 2.0, "growth": 1.18, "iterations": 3500},
    "fine": {"ds": 0.005, "points": 140, "far": 1.5, "growth": 1.12, "iterations": 5000},
}

# NACA 5-digit camber lines without reflex, by the second digit: position of maximum
# camber and k1 (for a design lift coefficient of 0.3).
FIVE_DIGIT = {
    1: (0.0580, 361.4),
    2: (0.1260, 51.64),
    3: (0.2025, 15.957),
    4: (0.2900, 6.643),
    5: (0.3910, 3.230),
}

BLUFF = ("cylinder", "ellipse", "wedge", "rectangle")


def read_case(job_dir: Path) -> dict:
    case = json.loads((job_dir / "case.json").read_text())
    for key, default in (
        ("shape", "airfoil"),
        ("alpha", 0.0),
        ("mach", 0.0),
        ("wall_type", "no_slip"),
        ("wall_velocity_x", 0.0),
        ("wall_velocity_y", 0.0),
        ("spin_ratio", 0.0),
        ("roughness_height", 0.0),
        ("regime", "auto"),
        ("video", True),
    ):
        if case.get(key) is None:
            case[key] = default
    return case


def is_transient(case: dict) -> bool:
    if case["regime"] != "auto":
        return case["regime"] == "transient"
    return case["shape"] in BLUFF


def is_laminar(case: dict) -> bool:
    return case["reynolds"] < LAMINAR_BELOW_REYNOLDS


# --- geometry ---------------------------------------------------------------


def naca_surfaces(code: str, n: int) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Upper surface from the trailing edge to the leading edge, and the lower one back.

    Cosine spacing, sharp trailing edge, both including the shared end points.
    """
    if len(code) == 4:
        m, p, t = int(code[0]) / 100, int(code[1]) / 10, int(code[2:]) / 100
        k1 = None
    elif len(code) == 5:
        lift, pos, reflex, t = int(code[0]), int(code[1]), int(code[2]), int(code[3:]) / 100
        if reflex or pos not in FIVE_DIGIT:
            raise ValueError(f"NACA {code} isn't supported: only non-reflex 5-digit cambers")
        p, k1 = FIVE_DIGIT[pos]
        k1 = k1 * (lift * 3 / 20) / 0.3
        m = 0.0
    else:
        raise ValueError(f"expected a 4- or 5-digit NACA designation, got {code!r}")

    def camber(x: float) -> tuple[float, float]:
        if k1 is None:
            if m == 0 or p == 0:
                return 0.0, 0.0
            if x < p:
                return m / p**2 * (2 * p * x - x * x), 2 * m / p**2 * (p - x)
            return (
                m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x * x),
                2 * m / (1 - p) ** 2 * (p - x),
            )
        if x < p:
            return (
                k1 / 6 * (x**3 - 3 * p * x**2 + p * p * (3 - p) * x),
                k1 / 6 * (3 * x**2 - 6 * p * x + p * p * (3 - p)),
            )
        return k1 * p**3 / 6 * (1 - x), -k1 * p**3 / 6

    def thickness(x: float) -> float:
        return (
            5 * t * (0.2969 * math.sqrt(x) - 0.1260 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1036 * x**4)
        )

    upper, lower = [], []
    for i in range(n + 1):
        x = 0.5 * (1 - math.cos(math.pi * i / n))  # 0 at the leading edge, 1 at the trailing edge
        yc, dyc = camber(x)
        yt, theta = thickness(x), math.atan(dyc)
        upper.append((x - yt * math.sin(theta), yc + yt * math.cos(theta)))
        lower.append((x + yt * math.sin(theta), yc - yt * math.cos(theta)))
    # Pin the end points exactly so the two surfaces share them.
    upper[0] = lower[0] = (0.0, 0.0)
    upper[-1] = lower[-1] = (1.0, 0.0)
    return upper[::-1], lower


def first_layer_height(reynolds: float, y_plus: float) -> float:
    """Height of the first cell for the given y+, from a flat-plate skin friction estimate."""
    skin_friction = 0.026 * reynolds ** (-1 / 7)
    u_tau = math.sqrt(skin_friction / 2)
    return 2 * y_plus / (reynolds * u_tau)  # nu = 1/Re; the cell centre sits at half the height


def boundary_layer_thickness(reynolds: float) -> float:
    return min(0.1, 3 * 0.37 * reynolds ** (-0.2))


@dataclass
class Geo:
    """Accumulates a gmsh .geo file, numbering points and curves."""

    lines: list[str] = field(default_factory=list)
    points: int = 0
    curves: int = 0

    def point(self, x: float, y: float, size: float) -> int:
        self.points += 1
        self.lines.append(f"Point({self.points}) = {{{x:.8f}, {y:.8f}, 0, {size:.6f}}};")
        return self.points

    def curve(self, kind: str, *refs: int) -> int:
        self.curves += 1
        self.lines.append(f"{kind}({self.curves}) = {{{', '.join(map(str, refs))}}};")
        return self.curves


@dataclass
class Body:
    curves: list[int]
    sharp_points: list[int]  # corners and edges where the mesh is refined
    centre: tuple[float, float]  # the farfield is centred here, and moments are taken about it
    length: float  # extent along x, to size the domain


def build_body(geo: Geo, case: dict, level: dict) -> Body:
    shape, ds = case["shape"], level["ds"]

    if shape == "airfoil":
        upper, lower = naca_surfaces(case["naca"], level["points"])
        ids_upper = [geo.point(x, y, ds) for x, y in upper]
        ids_lower = [ids_upper[-1]] + [geo.point(x, y, ds) for x, y in lower[1:-1]] + [ids_upper[0]]
        curves = [geo.curve("Spline", *ids_upper), geo.curve("Spline", *ids_lower)]
        return Body(curves, [ids_upper[-1], ids_upper[0]], (0.5, 0.0), 1.0)

    if shape in ("cylinder", "ellipse"):
        # Frontal height 1. An ellipse's streamwise axis is aspect_ratio times longer.
        b = 0.5
        a = b * (case.get("aspect_ratio") or 1.0) if shape == "ellipse" else b
        centre = geo.point(0, 0, ds)
        ring = [geo.point(a, 0, ds), geo.point(0, b, ds), geo.point(-a, 0, ds), geo.point(0, -b, ds)]
        if shape == "cylinder":
            curves = [geo.curve("Circle", ring[i], centre, ring[(i + 1) % 4]) for i in range(4)]
        else:
            curves = [geo.curve("Ellipse", ring[i], centre, ring[0], ring[(i + 1) % 4]) for i in range(4)]
        sharp = [ring[0], ring[2]] if shape == "ellipse" and a >= 3 * b else []
        return Body(curves, sharp, (0.0, 0.0), 2 * a)

    if shape == "wedge":
        # Apex facing the flow, flat base of height 1 at the back.
        length = 0.5 / math.tan(math.radians(case["half_angle"]))
        apex, top, bottom = geo.point(0, 0, ds), geo.point(length, 0.5, ds), geo.point(length, -0.5, ds)
        curves = [geo.curve("Line", apex, top), geo.curve("Line", top, bottom), geo.curve("Line", bottom, apex)]
        return Body(curves, [apex, top, bottom], (length / 2, 0.0), length)

    if shape == "rectangle":
        length = case["aspect_ratio"]
        corners = [geo.point(x, y, ds) for x, y in ((0, -0.5), (length, -0.5), (length, 0.5), (0, 0.5))]
        curves = [geo.curve("Line", corners[i], corners[(i + 1) % 4]) for i in range(4)]
        return Body(curves, corners, (length / 2, 0.0), length)

    raise ValueError(f"unknown shape {shape!r}")


def write_geo(job_dir: Path) -> None:
    case = read_case(job_dir)
    level = MESHES[case["mesh"]]
    ds = level["ds"]
    geo = Geo()
    geo.lines += [
        "// Generated by cfd_case.py: 2D mesh around the body, extruded one cell for OpenFOAM.",
        'SetFactory("Built-in");',
        "Mesh.MshFileVersion = 2.2;",
        "Mesh.MeshSizeFromPoints = 0;",
        "Mesh.MeshSizeExtendFromBoundary = 0;",
        "Mesh.MeshSizeFromCurvature = 0;",
        "Mesh.Algorithm = 6;",
        "",
    ]
    body = build_body(geo, case, level)
    radius = FARFIELD_RADIUS * max(1.0, body.length)
    cx, cy = body.centre

    # Farfield circle as four arcs.
    centre = geo.point(cx, cy, level["far"])
    ring = [
        geo.point(cx + radius, cy, level["far"]),
        geo.point(cx, cy + radius, level["far"]),
        geo.point(cx - radius, cy, level["far"]),
        geo.point(cx, cy - radius, level["far"]),
    ]
    far_curves = [geo.curve("Circle", ring[i], centre, ring[(i + 1) % 4]) for i in range(4)]
    body_ids = ", ".join(map(str, body.curves))
    far_ids = ", ".join(map(str, far_curves))
    geo.lines += [
        f"Curve Loop(1) = {{{far_ids}}};",
        f"Curve Loop(2) = {{{body_ids}}};",
        "Plane Surface(1) = {1, 2};",
        "",
    ]

    rough = case["roughness_height"] > 0
    if case["wall_type"] != "slip":
        # Slip walls have no boundary layer to resolve.
        y_plus = 30.0 if rough else 1.0  # rough walls use wall functions
        if is_laminar(case):
            # No turbulence model: resolve a laminar layer, thickness ~ 5 / sqrt(Re).
            thickness = min(0.5, 5 / math.sqrt(case["reynolds"]))
            first_layer = thickness / 40
        else:
            thickness = boundary_layer_thickness(case["reynolds"])
            first_layer = first_layer_height(case["reynolds"], y_plus)
        geo.lines += [
            "Field[1] = BoundaryLayer;",
            f"Field[1].EdgesList = {{{body_ids}}};",
            f"Field[1].hwall_n = {first_layer:.4e};",
            f"Field[1].ratio = {level['growth']};",
            f"Field[1].thickness = {thickness:.4f};",
            f"Field[1].hfar = {ds * 2:.5f};",
            "Field[1].Quads = 1;",
            "BoundaryLayer Field = 1;",
            "",
        ]
    geo.lines += [
        "Field[2] = Distance;",
        f"Field[2].CurvesList = {{{body_ids}}};",
        "Field[2].Sampling = 400;",
        "Field[3] = Threshold;",
        "Field[3].InField = 2;",
        f"Field[3].SizeMin = {ds:.5f};",
        f"Field[3].SizeMax = {level['far']};",
        "Field[3].DistMin = 0.05;",
        f"Field[3].DistMax = {radius * 0.8};",
    ]
    fields = [3]
    if body.sharp_points:
        # Finer near corners and edges, where the flow turns hardest. This grows linearly with
        # distance, so it only matters close by (a Threshold field would force the fine size
        # everywhere beyond its range).
        geo.lines += [
            "Field[4] = Distance;",
            f"Field[4].PointsList = {{{', '.join(map(str, body.sharp_points))}}};",
            "Field[5] = MathEval;",
            f'Field[5].F = "{ds * 0.12:.6f} + {ds * 0.88 / 0.2:.6f} * F4";',
        ]
        fields.append(5)
    if is_transient(case):
        # The wake sheds vortices that must reach the farfield without being smeared out.
        alpha = math.radians(case["alpha"])
        reach = 4 + 0.5 * body.length  # half the length of the refined wake region
        geo.lines += [
            "Field[7] = Cylinder;",
            f"Field[7].XCenter = {cx + reach * math.cos(alpha):.4f};",
            f"Field[7].YCenter = {cy + reach * math.sin(alpha):.4f};",
            "Field[7].ZCenter = 0;",
            f"Field[7].XAxis = {reach * math.cos(alpha):.4f};",
            f"Field[7].YAxis = {reach * math.sin(alpha):.4f};",
            "Field[7].ZAxis = 0;",
            f"Field[7].Radius = {1.2 + 0.5 * math.sqrt(body.length):.3f};",
            f"Field[7].VIn = {ds * 6:.5f};",
            f"Field[7].VOut = {level['far']};",
        ]
        fields.append(7)
    geo.lines += [
        "Field[6] = Min;",
        f"Field[6].FieldsList = {{{', '.join(map(str, fields))}}};",
        "Background Field = 6;",
        "",
        f"out[] = Extrude {{0, 0, {SPAN}}} {{ Surface{{1}}; Layers{{1}}; Recombine; }};",
        # out[0] is the top face and out[1] the volume, then come the side faces in the order
        # of the surface's boundary curves: the four farfield arcs, then the body's curves.
        'Physical Surface("farfield") = {out[2], out[3], out[4], out[5]};',
        f'Physical Surface("body") = {{{", ".join(f"out[{6 + i}]" for i in range(len(body.curves)))}}};',
        'Physical Surface("frontAndBack") = {1, out[0]};',
        'Physical Volume("fluid") = {out[1]};',
    ]
    mesh_dir = job_dir / "mesh"
    mesh_dir.mkdir(exist_ok=True)
    (mesh_dir / "body.geo").write_text("\n".join(geo.lines) + "\n")


# --- OpenFOAM case ----------------------------------------------------------

HEADER = """FoamFile
{
    version     2.0;
    format      ascii;
    class       %s;
    object      %s;
}
"""


def foam_file(path: Path, cls: str, name: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER % (cls, name) + "\n" + body.strip() + "\n")


def vector(x: float, y: float, z: float = 0.0) -> str:
    return f"({x:.8g} {y:.8g} {z:.8g})"


def wall_conditions(case: dict, k: float, omega: float, centre: tuple[float, float]) -> dict[str, str]:
    """Boundary condition bodies for the `body` patch, per field."""
    wall = case["wall_type"]
    if wall == "slip":
        return {
            "U": "        type slip;",
            "p": "        type zeroGradient;",
            "k": "        type zeroGradient;",
            "omega": "        type zeroGradient;",
            "nut": "        type zeroGradient;",
        }
    if wall == "moving":
        velocity = vector(case["wall_velocity_x"], case["wall_velocity_y"])
        speed = f"        type fixedValue;\n        value uniform {velocity};"
    elif wall == "rotating":
        # spin_ratio = surface speed / freestream speed, on a body of radius 0.5.
        speed = (
            "        type rotatingWallVelocity;\n"
            f"        origin {vector(*centre)};\n"
            "        axis (0 0 1);\n"
            f"        omega {2 * case['spin_ratio']:.8g};"
        )
    else:
        speed = "        type noSlip;"

    if case["roughness_height"] > 0:
        nut = (
            f"        type nutkRoughWallFunction;\n        Ks uniform {case['roughness_height']:.6g};\n"
            "        Cs uniform 0.5;\n        value uniform 0;"
        )
    else:
        nut = "        type nutUSpaldingWallFunction;\n        value uniform 0;"
    return {
        "U": speed,
        "p": "        type zeroGradient;",
        "k": f"        type kqRWallFunction;\n        value uniform {k:.6e};",
        "omega": f"        type omegaWallFunction;\n        value uniform {omega:.6e};",
        "nut": nut,
    }


def write_foam(job_dir: Path, processors: int) -> None:
    case = read_case(job_dir)
    level = MESHES[case["mesh"]]
    alpha = math.radians(case["alpha"])
    nu = 1 / case["reynolds"]
    ux, uy = math.cos(alpha), math.sin(alpha)
    k = 1.5 * TURBULENCE_INTENSITY**2
    omega = k / (VISCOSITY_RATIO * nu)
    freestream = vector(ux, uy)
    laminar, transient = is_laminar(case), is_transient(case)
    centre = build_body(Geo(), case, level).centre
    conditions = wall_conditions(case, k, omega, centre)

    def field(name: str, cls: str, dims: str, internal: str, farfield: str) -> None:
        patches = {"farfield": farfield, "body": conditions[name], "frontAndBack": "        type empty;"}
        text = "\n".join(f"    {patch}\n    {{\n{body}\n    }}" for patch, body in patches.items())
        foam_file(
            job_dir / "0" / name,
            cls,
            name,
            f"dimensions {dims};\ninternalField uniform {internal};\nboundaryField\n{{\n{text}\n}}",
        )

    field(
        "U",
        "volVectorField",
        "[0 1 -1 0 0 0 0]",
        freestream,
        f"        type freestreamVelocity;\n        freestreamValue uniform {freestream};",
    )
    field(
        "p",
        "volScalarField",
        "[0 2 -2 0 0 0 0]",
        "0",
        "        type freestreamPressure;\n        freestreamValue uniform 0;",
    )
    if not laminar:
        field(
            "k",
            "volScalarField",
            "[0 2 -2 0 0 0 0]",
            f"{k:.6e}",
            f"        type freestream;\n        freestreamValue uniform {k:.6e};",
        )
        field(
            "omega",
            "volScalarField",
            "[0 0 -1 0 0 0 0]",
            f"{omega:.6e}",
            f"        type freestream;\n        freestreamValue uniform {omega:.6e};",
        )
        field(
            "nut",
            "volScalarField",
            "[0 2 -1 0 0 0 0]",
            f"{k / omega:.6e}",
            f"        type calculated;\n        value uniform {k / omega:.6e};",
        )

    foam_file(
        job_dir / "constant" / "transportProperties",
        "dictionary",
        "transportProperties",
        f"transportModel Newtonian;\nnu [0 2 -1 0 0 0 0] {nu:.8e};",
    )
    model = (
        "simulationType laminar;"
        if laminar
        else "simulationType RAS;\nRAS\n{\n    model kOmegaSST;\n    turbulence on;\n    printCoeffs on;\n}"
    )
    foam_file(job_dir / "constant" / "turbulenceProperties", "dictionary", "turbulenceProperties", model)

    video = transient and case["video"]
    if video:
        # Keep every snapshot for the video; binary files keep them small.
        write_control = f"writeControl    adjustableRunTime;\nwriteInterval   {VIDEO_INTERVAL};"
    else:
        write_control = f"writeControl    runTime;\nwriteInterval   {TRANSIENT_END_TIME};"
    if transient:
        time_control = f"""
application     pimpleFoam;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {TRANSIENT_END_TIME};
deltaT          0.001;
adjustTimeStep  yes;
maxCo           {MAX_COURANT};
maxDeltaT       0.05;
{write_control}
"""
    else:
        time_control = f"""
application     simpleFoam;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {level['iterations']};
deltaT          1;
writeControl    timeStep;
writeInterval   {level['iterations']};
"""
    foam_file(
        job_dir / "system" / "controlDict",
        "dictionary",
        "controlDict",
        f"""
{time_control}
purgeWrite      {0 if video else 1};
writeFormat     {"binary" if video else "ascii"};
writePrecision  8;
timeFormat      general;
timePrecision   6;
runTimeModifiable false;

functions
{{
    forceCoeffs
    {{
        type            forceCoeffs;
        libs            (forces);
        writeControl    timeStep;
        writeInterval   1;
        patches         (body);
        rho             rhoInf;
        rhoInf          1;
        liftDir         {vector(-math.sin(alpha), math.cos(alpha))};
        dragDir         {vector(math.cos(alpha), math.sin(alpha))};
        CofR            {vector(*centre)};
        pitchAxis       (0 0 1);
        magUInf         1;
        lRef            1;
        Aref            {SPAN};
    }}
    yPlus
    {{
        type            yPlus;
        libs            (fieldFunctionObjects);
        writeControl    writeTime;
    }}
}}
""",
    )

    bounded = "" if transient else "bounded "
    turbulence_schemes = (
        ""
        if laminar
        else f"    turbulence          {bounded}Gauss limitedLinear 1;\n"
        "    div(phi,k)          $turbulence;\n    div(phi,omega)      $turbulence;\n"
    )
    foam_file(
        job_dir / "system" / "fvSchemes",
        "dictionary",
        "fvSchemes",
        f"""
ddtSchemes {{ default {"backward" if transient else "steadyState"}; }}
gradSchemes {{ default Gauss linear; }}
divSchemes
{{
    default             none;
    div(phi,U)          {bounded}Gauss linearUpwind grad(U);
{turbulence_schemes}    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}}
laplacianSchemes {{ default Gauss linear corrected; }}
interpolationSchemes {{ default linear; }}
snGradSchemes {{ default corrected; }}
wallDist {{ method meshWave; }}
""",
    )

    if transient:
        solution = """
solvers
{
    p
    {
        solver          GAMG;
        smoother        GaussSeidel;
        tolerance       1e-8;
        relTol          0.01;
    }
    pFinal
    {
        $p;
        relTol          0;
    }
    "(U|k|omega)"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0.01;
    }
    "(U|k|omega)Final"
    {
        $U;
        relTol          0;
    }
}
PIMPLE
{
    nOuterCorrectors 1;
    nCorrectors      2;
    nNonOrthogonalCorrectors 1;
    momentumPredictor yes;
}
"""
    else:
        solution = """
solvers
{
    p
    {
        solver          GAMG;
        smoother        GaussSeidel;
        tolerance       1e-10;
        relTol          0.1;
    }
    "(U|k|omega)"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-10;
        relTol          0.1;
    }
}
SIMPLE
{
    nNonOrthogonalCorrectors 1;
    consistent      yes;
    residualControl
    {
        p               1e-5;
        U               1e-6;
        "(k|omega)"     1e-6;
    }
}
relaxationFactors
{
    equations
    {
        U               0.9;
        ".*"            0.9;
    }
}
"""
    foam_file(job_dir / "system" / "fvSolution", "dictionary", "fvSolution", solution)
    foam_file(
        job_dir / "system" / "decomposeParDict",
        "dictionary",
        "decomposeParDict",
        f"numberOfSubdomains {processors};\nmethod scotch;",
    )


def fix_boundary(job_dir: Path) -> None:
    """gmshToFoam makes every patch a plain `patch`; the body is a wall and the sides empty."""
    path = job_dir / "constant" / "polyMesh" / "boundary"
    text = path.read_text()
    for name, kind in (("body", "wall"), ("frontAndBack", "empty")):
        text, count = re.subn(
            rf"({re.escape(name)}\s*\{{[^}}]*?type\s+)patch;", rf"\1{kind};", text, flags=re.S
        )
        if count != 1:
            raise SystemExit(f"patch {name!r} not found in {path} (the gmsh surface order changed?)")
    if not re.search(r"\bfarfield\b", text):
        raise SystemExit(f"patch 'farfield' missing from {path}")
    path.write_text(text)


# --- results ----------------------------------------------------------------


def read_coefficients(job_dir: Path) -> dict[str, list[float]]:
    """The force-coefficient history, whatever this OpenFOAM release calls the file."""
    root = job_dir / "postProcessing" / "forceCoeffs"
    files = sorted(root.glob("*/coefficient.dat")) + sorted(root.glob("*/forceCoeffs.dat"))
    if not files:
        raise SystemExit("no force coefficient output found; the solver didn't get going")
    columns: list[str] = []
    rows: list[list[float]] = []
    for line in files[-1].read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            names = stripped.lstrip("#").split()
            if names and names[0] == "Time":
                columns = names
            continue
        if stripped:
            rows.append([float(value) for value in stripped.split()])
    if not rows or not columns:
        raise SystemExit("force coefficient file is empty or has no header")

    def column(*names: str) -> list[float]:
        for name in names:
            if name in columns:
                return [row[columns.index(name)] for row in rows]
        raise SystemExit(f"none of {names} in the force coefficient header {columns}")

    return {
        "time": column("Time"),
        "cl": column("Cl"),
        "cd": column("Cd"),
        "cm": column("CmPitch", "Cm"),
    }


def read_residuals(log: Path) -> tuple[dict[str, float], int | None]:
    """Last initial residual per field, and the iteration the solver reported converging at."""
    residuals: dict[str, float] = {}
    converged_at = None
    pattern = re.compile(r"Solving for (\w+), Initial residual = ([-+0-9.eE]+)")
    for line in log.read_text(errors="replace").splitlines():
        match = pattern.search(line)
        if match:
            residuals[match.group(1)] = float(match.group(2))
        converged = re.search(r"SIMPLE solution converged in (\d+) iterations", line)
        if converged:
            converged_at = int(converged.group(1))
    return residuals, converged_at


def read_y_plus(job_dir: Path) -> dict[str, float] | None:
    files = sorted((job_dir / "postProcessing" / "yPlus").glob("*/yPlus.dat"))
    if not files:
        return None
    rows = [line.split() for line in files[-1].read_text().splitlines() if line.strip() and not line.startswith("#")]
    rows = [row for row in rows if len(row) >= 5 and row[1] == "body"]
    if not rows:
        return None
    last = rows[-1]
    return {"min": float(last[2]), "max": float(last[3]), "average": float(last[4])}


def shedding_frequency(times: list[float], values: list[float]) -> float | None:
    """Frequency of the oscillation of `values`, from its upward mean crossings, or None."""
    mean = statistics.fmean(values)
    if statistics.pstdev(values) < 1e-3:
        return None
    crossings = [
        times[i - 1] + (times[i] - times[i - 1]) * (mean - values[i - 1]) / (values[i] - values[i - 1])
        for i in range(1, len(values))
        if values[i - 1] < mean <= values[i]
    ]
    if len(crossings) < 3:
        return None
    return (len(crossings) - 1) / (crossings[-1] - crossings[0])


def subsample(history: dict[str, list[float]]) -> dict[str, list[float]]:
    step = max(1, math.ceil(len(history["time"]) / MAX_HISTORY_POINTS))
    sampled = {name: values[::step] for name, values in history.items()}
    if sampled["time"][-1] != history["time"][-1]:
        for name in sampled:
            sampled[name].append(history[name][-1])
    return sampled


def read_media(job_dir: Path, case: dict, warnings: list[str]) -> list[dict] | None:
    """The images and video render_video.py made, or a warning if it was asked to and couldn't."""
    info_file = job_dir / "video.json"
    if info_file.exists():
        return json.loads(info_file.read_text())["media"]
    if case["video"]:
        failed = job_dir / "video.failed"
        reason = failed.read_text().strip() if failed.exists() else "it did not run"
        warnings.append(
            f"The flow images and video could not be made ({reason}); the numbers are unaffected."
        )
    return None


def build_results(job_dir: Path) -> dict:
    case = read_case(job_dir)
    history = read_coefficients(job_dir)
    count = len(history["time"])
    transient = is_transient(case)
    residuals, converged_at = read_residuals(job_dir / "log.solver")
    warnings: list[str] = []

    if transient:
        # Skip the start-up transient: average over the last half of the run.
        start = count // 2
        times = history["time"][start:]
        tail = {name: history[name][start:] for name in ("cl", "cd", "cm")}
        window = f"the last {times[-1] - times[0]:.1f} convective times"
    else:
        size = max(10, min(100, count // 10))
        tail = {name: history[name][-size:] for name in ("cl", "cd", "cm")}
        window = f"the last {size} iterations"
    final = {name: statistics.fmean(values) for name, values in tail.items()}
    cl_scatter = statistics.pstdev(tail["cl"]) / max(abs(final["cl"]), 0.05)

    airfoil = case["shape"] == "airfoil"
    result: dict = {
        "shape": case["shape"],
        "regime": "transient" if transient else "steady",
        "cl": final["cl"],
        "cd": final["cd"],
        "cm": final["cm"],
        "cl_over_cd": final["cl"] / final["cd"] if final["cd"] else None,
        "averaged_over": window,
        "final_residuals": residuals,
        "coefficient_basis": (
            "per unit span, normalised by "
            + ("the chord" if airfoil else "the frontal height (1 at zero incidence)")
            + "; Cm about "
            + ("the quarter chord" if airfoil else "the body centre")
        ),
    }
    if transient:
        result["end_time"] = history["time"][-1]
        result["cl_rms"] = statistics.pstdev(tail["cl"])
        frequency = shedding_frequency(times, tail["cl"])
        if frequency is not None:
            result["strouhal"] = frequency  # reference length and freestream speed are both 1
        elif not airfoil:
            warnings.append("No periodic vortex shedding was detected in the lift coefficient.")
    else:
        converged = converged_at is not None
        result["converged"] = converged
        result["iterations"] = converged_at or count
        if not converged:
            warnings.append(
                f"The residuals did not reach their targets in {count} iterations; the "
                f"coefficients are averaged over {window} (Cl varies by {cl_scatter:.1%} there)."
            )
        elif cl_scatter > 0.02:
            warnings.append(f"Cl still varies by {cl_scatter:.1%} over {window}.")

    if case["mach"] > 0.3:
        warnings.append(f"Mach {case['mach']} was ignored: the solver is incompressible.")
    if is_laminar(case):
        warnings.append(f"Re < {LAMINAR_BELOW_REYNOLDS:g}: solved laminar, without a turbulence model.")
    if case["wall_type"] == "rotating" and case["shape"] != "cylinder":
        warnings.append("Rotation is only meaningful for a cylinder.")
    media = read_media(job_dir, case, warnings)
    if media:
        result["media"] = media
    result["warnings"] = warnings
    result["history"] = subsample(history)

    y_plus = read_y_plus(job_dir)
    if y_plus:
        result["y_plus_body"] = y_plus
    mesh_log = job_dir / "log.checkMesh"
    if mesh_log.exists():
        cells = re.search(r"cells:\s+(\d+)", mesh_log.read_text(errors="replace"))
        if cells:
            result["cells"] = int(cells.group(1))
    return result


# --- live monitoring --------------------------------------------------------

# The steps run_openfoam.sh logs to log.<step>, in the order it runs them.
STAGES = [
    "geo", "gmsh", "foam", "gmshToFoam", "boundary", "checkMesh", "decomposePar",
    "solver", "reconstruct", "vtk", "render", "results",
]  # fmt: skip
MAX_CHUNK_BYTES = 4_000_000  # the most log read in one progress call
TAIL_BYTES = 6000
DICTIONARIES = (
    "system/controlDict",
    "system/fvSchemes",
    "system/fvSolution",
    "system/decomposeParDict",
    "constant/turbulenceProperties",
    "constant/transportProperties",
)
STEP_START = re.compile(r"^Time = (\S+)\s*$", re.M)
RESIDUAL = re.compile(r"Solving for (\w+), Initial residual = ([-+0-9.eE]+)")
COURANT = re.compile(r"Courant Number mean: \S+ max: ([-+0-9.eE]+)")
DELTA_T = re.compile(r"^deltaT = ([-+0-9.eE]+)", re.M)


def read_chunk(path: Path, offset: int, *, final: bool) -> tuple[str, int]:
    """New text of an append-only log after `offset`, ending where a solver step starts.

    The step being written is left for the next call, so that no step is split between two
    chunks (`final` takes everything, once the run has ended).
    """
    if not path.exists():
        return "", offset
    with path.open("rb") as file:
        file.seek(offset)
        data = file.read(MAX_CHUNK_BYTES)
    if not data:
        return "", offset
    if final and len(data) < MAX_CHUNK_BYTES:
        end = len(data)
    else:
        end = data.rfind(b"\nTime = ")
        if end < 0:
            return "", offset  # no complete step yet (or one step bigger than the chunk)
        # A step's Courant number and time step are printed just before its "Time =" line.
        preamble = data.rfind(b"\nCourant Number", 0, end)
        if preamble >= 0 and b"Solving for" not in data[preamble:end]:
            end = preamble
        end += 1
    return data[:end].decode(errors="replace"), offset + end


def parse_steps(text: str) -> dict[str, list]:
    """One entry per solver step: its time, the first residual of each field, Courant, dt."""
    starts = list(STEP_START.finditer(text))
    steps: dict[str, list] = {"time": [], "courant_max": [], "delta_t": [], "residuals": {}}
    fields: list[str] = []
    parsed = []
    for i, start in enumerate(starts):
        try:
            moment = float(start.group(1))
        except ValueError:
            continue
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        residuals: dict[str, float] = {}
        for name, value in RESIDUAL.findall(text[start.end() : end]):
            residuals.setdefault(name, float(value))  # the first solve of the step
        fields += [name for name in residuals if name not in fields]
        # The Courant number and time step are printed just before the "Time =" line.
        before = text[starts[i - 1].end() if i else 0 : start.start()]
        courant = COURANT.findall(before)
        delta_t = DELTA_T.findall(before)
        parsed.append((moment, residuals, float(courant[-1]) if courant else None,
                       float(delta_t[-1]) if delta_t else None))  # fmt: skip
    for moment, residuals, courant, delta_t in parsed:
        steps["time"].append(moment)
        steps["courant_max"].append(courant)
        steps["delta_t"].append(delta_t)
    steps["residuals"] = {name: [r.get(name) for _, r, _, _ in parsed] for name in fields}
    return steps


def coefficient_files(job_dir: Path) -> list[Path]:
    root = job_dir / "postProcessing" / "forceCoeffs"
    return sorted(root.glob("*/coefficient.dat")) + sorted(root.glob("*/forceCoeffs.dat"))


def read_new_coefficients(job_dir: Path, offset: int) -> tuple[dict[str, list[float]], int]:
    files = coefficient_files(job_dir)
    empty: dict[str, list[float]] = {"time": [], "cl": [], "cd": [], "cm": []}
    if not files:
        return empty, offset
    columns: list[str] = []
    with files[-1].open("rb") as file:
        for raw in file:
            line = raw.decode(errors="replace").strip()
            if not line.startswith("#"):
                break
            names = line.lstrip("#").split()
            if names and names[0] == "Time":
                columns = names
        file.seek(offset)
        data = file.read(MAX_CHUNK_BYTES)
    end = data.rfind(b"\n") + 1
    if not columns or end <= 0:
        return empty, offset
    wanted = {"time": ("Time",), "cl": ("Cl",), "cd": ("Cd",), "cm": ("CmPitch", "Cm")}
    index = {key: next((columns.index(n) for n in names if n in columns), None) for key, names in wanted.items()}
    if any(position is None for position in index.values()):
        return empty, offset
    for line in data[:end].decode(errors="replace").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        values = line.split()
        if len(values) < len(columns):
            continue
        for key, position in index.items():
            empty[key].append(float(values[position]))
    return empty, offset + end


def last_solver_time(log: Path) -> float | None:
    if not log.exists():
        return None
    with log.open("rb") as file:
        file.seek(0, 2)
        file.seek(max(0, file.tell() - 65536))
        tail = file.read().decode(errors="replace")
    found = STEP_START.findall(tail)
    return float(found[-1]) if found else None


def log_tail(job_dir: Path, stage: str | None) -> list[str]:
    if stage is None:
        return []
    path = job_dir / f"log.{stage}"
    if not path.exists():
        return []
    with path.open("rb") as file:
        file.seek(0, 2)
        file.seek(max(0, file.tell() - TAIL_BYTES))
        text = file.read().decode(errors="replace")
    return text.splitlines()[-30:]


def present_stages(job_dir: Path) -> list[str]:
    return [stage for stage in STAGES if (job_dir / f"log.{stage}").exists()]


def run_horizon(case: dict) -> float:
    """The simulated time (transient) or iteration count (steady) a run is set to reach."""
    return TRANSIENT_END_TIME if is_transient(case) else float(MESHES[case["mesh"]]["iterations"])


def build_progress(job_dir: Path, log_offset: int, coeff_offset: int, final: bool) -> dict:
    case = read_case(job_dir)
    now = time.time()
    chunk, new_log_offset = read_chunk(job_dir / "log.solver", log_offset, final=final)
    coefficients, new_coeff_offset = read_new_coefficients(job_dir, coeff_offset)
    stages = present_stages(job_dir)
    current = stages[-1] if stages else None
    latest = last_solver_time(job_dir / "log.solver")
    horizon = run_horizon(case)
    started = (job_dir / "case.json").stat().st_mtime
    decomposed = job_dir / "log.decomposePar"
    solver_started = decomposed.stat().st_mtime if decomposed.exists() else None

    fraction = min(1.0, latest / horizon) if latest is not None and horizon else None
    eta = None
    if fraction and solver_started and 0 < fraction < 1 and current == "solver":
        eta = (now - solver_started) * (1 - fraction) / fraction
    return {
        "log_offset": new_log_offset,
        "coeff_offset": new_coeff_offset,
        "steps": parse_steps(chunk),
        "coefficients": coefficients,
        "stage": current,
        "stages": stages,
        "latest_time": latest,
        "horizon": horizon,
        "horizon_unit": "convective times" if is_transient(case) else "iterations",
        "fraction": fraction,
        "elapsed_s": now - started,
        "solver_elapsed_s": now - solver_started if solver_started else None,
        "eta_s": eta,
        "tail": log_tail(job_dir, current),
    }


def build_summary(jobs_dir: Path, job_ids: list[str]) -> dict[str, dict]:
    """For the job list: where each job is, without reading its whole log."""
    summary: dict[str, dict] = {}
    for job_id in job_ids:
        job_dir = jobs_dir / job_id
        if not (job_dir / "case.json").exists():
            summary[job_id] = {"missing": True}
            continue
        case = read_case(job_dir)
        stages = present_stages(job_dir)
        latest = last_solver_time(job_dir / "log.solver")
        horizon = run_horizon(case)
        try:
            status = json.loads((job_dir / "status.json").read_text()).get("status")
        except (OSError, ValueError):
            status = None
        fraction = min(1.0, latest / horizon) if latest is not None and horizon else None
        decomposed = job_dir / "log.decomposePar"
        eta = None
        if fraction and 0 < fraction < 1 and decomposed.exists() and stages[-1:] == ["solver"]:
            eta = (time.time() - decomposed.stat().st_mtime) * (1 - fraction) / fraction
        summary[job_id] = {
            "status": status,
            "stage": stages[-1] if stages else None,
            "fraction": 1.0 if status == "completed" else fraction,
            "eta_s": eta,
        }
    return summary


def mesh_quality(job_dir: Path) -> dict:
    log = job_dir / "log.checkMesh"
    if not log.exists():
        return {}
    text = log.read_text(errors="replace")

    def number(pattern: str) -> float | None:
        match = re.search(pattern, text)
        return float(match.group(1)) if match else None

    return {
        "cells": number(r"cells:\s+(\d+)"),
        "points": number(r"points:\s+(\d+)"),
        "max_non_orthogonality": number(r"Mesh non-orthogonality Max: ([-+0-9.eE]+)"),
        "max_skewness": number(r"Max skewness = ([-+0-9.eE]+)"),
        "max_aspect_ratio": number(r"Max aspect ratio = ([-+0-9.eE]+)"),
        "ok": "Mesh OK" in text,
    }


def build_info(job_dir: Path) -> dict:
    case = read_case(job_dir)
    dictionaries = {}
    for name in DICTIONARIES:
        path = job_dir / name
        if path.exists():
            text = path.read_text(errors="replace")
            dictionaries[name] = re.sub(r"^FoamFile\s*\{.*?\}\s*", "", text, flags=re.S).strip()
    processors = len(list(job_dir.glob("processor*")))
    return {
        "case": case,
        "regime": "transient" if is_transient(case) else "steady",
        "solver": "pimpleFoam" if is_transient(case) else "simpleFoam",
        "turbulence": "laminar" if is_laminar(case) else "kOmegaSST",
        "processors": processors or None,
        "horizon": run_horizon(case),
        "horizon_unit": "convective times" if is_transient(case) else "iterations",
        "mesh": mesh_quality(job_dir),
        "dictionaries": dictionaries,
        "stages_expected": STAGES,
    }


def describe_results(result: dict) -> str:
    """A few lines for log.results: what was written to status.json."""
    lines = [
        f"{result['regime']} {result['shape']}: "
        f"Cl = {result['cl']:.4f}, Cd = {result['cd']:.4f}, Cm = {result['cm']:.4f}",
        f"averaged over {result['averaged_over']}",
    ]
    if "strouhal" in result:
        lines.append(f"Strouhal number = {result['strouhal']:.4f}")
    if "converged" in result:
        lines.append(f"converged = {result['converged']} ({result['iterations']} iterations)")
    lines += [f"media: {item['file']}" for item in result.get("media", [])]
    lines += [f"warning: {warning}" for warning in result["warnings"]]
    lines.append("wrote status.json")
    return "\n".join(lines)


def write_status(job_dir: Path, state: str, message: str | None, result: dict | None = None) -> None:
    status: dict = {"status": state}
    if message:
        status["error"] = message
    if result is not None:
        status["result"] = result
    tmp = job_dir / "status.json.tmp"
    # Write then rename so the backend never reads a half-written file.
    tmp.write_text(json.dumps(status) + "\n")
    tmp.replace(job_dir / "status.json")


def main(argv: list[str]) -> None:
    if len(argv) < 3:
        raise SystemExit(__doc__)
    command, job_dir = argv[1], Path(argv[2])
    if command == "geo":
        write_geo(job_dir)
    elif command == "foam":
        write_foam(job_dir, int(argv[3]) if len(argv) > 3 else 1)
    elif command == "boundary":
        fix_boundary(job_dir)
    elif command == "results":
        result = build_results(job_dir)
        write_status(job_dir, "completed", None, result)
        print(describe_results(result))  # ends up in log.results
    elif command == "info":
        print(json.dumps(build_info(job_dir)))
    elif command == "summary":
        print(json.dumps(build_summary(job_dir, argv[3:])))
    elif command == "progress":
        offsets = (int(argv[3]), int(argv[4]))
        print(json.dumps(build_progress(job_dir, *offsets, final=len(argv) > 5 and argv[5] == "final")))
    elif command == "status":
        write_status(job_dir, argv[3], argv[4] if len(argv) > 4 else None)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
