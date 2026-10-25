"""The work station's case builder (ws/cfd_case.py), checked without gmsh or OpenFOAM."""

import importlib.util
import json
import math
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from api.compute.schemas import CfdCase

_path = Path(__file__).resolve().parents[2] / "ws" / "cfd_case.py"
_spec = importlib.util.spec_from_file_location("cfd_case", _path)
cfd_case = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfd_case)

CASES = {
    "airfoil": {"shape": "airfoil", "naca": "2412", "alpha": 4, "reynolds": 1e6},
    "cylinder": {"shape": "cylinder", "reynolds": 1e5},
    "ellipse": {"shape": "ellipse", "aspect_ratio": 3, "reynolds": 1e5},
    "wedge": {"shape": "wedge", "half_angle": 20, "reynolds": 1e5},
    "rectangle": {"shape": "rectangle", "aspect_ratio": 2, "reynolds": 1e5},
}


def make_job(tmp_path: Path, **case) -> Path:
    # The same shape the backend writes: every CfdCase field, unset ones as null.
    full = CfdCase(**case).model_dump()
    (tmp_path / "case.json").write_text(json.dumps(full))
    return tmp_path


@pytest.mark.parametrize("code", ["0012", "2412", "4415", "23012", "25018"])
def test_naca_surfaces_are_closed_and_ordered(code):
    upper, lower = cfd_case.naca_surfaces(code, 60)

    assert upper[0] == lower[-1] == (1.0, 0.0)
    assert upper[-1] == lower[0] == (0.0, 0.0)
    assert all(up[1] >= lo[1] for up, lo in zip(upper[::-1], lower, strict=True))
    thickness = max(up[1] - lo[1] for up, lo in zip(upper[::-1], lower, strict=True))
    assert thickness == pytest.approx(int(code[-2:]) / 100, rel=0.05)


def test_naca_5_digit_camber_matches_the_design_lift():
    # 230xx: design Cl 0.3, maximum camber about 1.8% of the chord.
    upper, lower = cfd_case.naca_surfaces("23012", 200)
    camber = max((up[1] + lo[1]) / 2 for up, lo in zip(upper[::-1], lower, strict=True))
    assert camber == pytest.approx(0.0183, abs=0.001)


def test_reflex_5_digit_airfoil_is_rejected_by_the_schema_and_the_builder():
    with pytest.raises(ValidationError, match="non-reflex"):
        CfdCase(naca="23112", reynolds=1e6)
    with pytest.raises(ValueError, match="isn't supported"):
        cfd_case.naca_surfaces("23112", 20)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"shape": "airfoil"}, "needs naca"),
        ({"shape": "ellipse"}, "needs aspect_ratio"),
        ({"shape": "wedge"}, "needs half_angle"),
        ({"shape": "cylinder", "naca": "0012"}, "only applies to an airfoil"),
        (
            {"shape": "wedge", "half_angle": 20, "wall_type": "rotating"},
            "only supported for a cylinder",
        ),
    ],
)
def test_case_schema_asks_for_what_each_shape_needs(fields, message):
    with pytest.raises(ValidationError, match=message):
        CfdCase(reynolds=1e5, **fields)


def test_old_airfoil_jobs_still_validate():
    case = CfdCase(**{"naca": "2412", "alpha": 4, "reynolds": 3e6, "mach": 0.15, "mesh": "medium"})
    assert case.shape == "airfoil"
    assert case.wall_type == "no_slip"


@pytest.mark.parametrize("name", CASES)
def test_geo_names_the_three_patches_and_closes_the_loops(tmp_path, name):
    job = make_job(tmp_path, **CASES[name])
    cfd_case.write_geo(job)
    geo = (job / "mesh" / "body.geo").read_text()

    for patch in ("farfield", "body", "frontAndBack"):
        assert f'Physical Surface("{patch}")' in geo
    # Farfield arcs come right after the body's curves, and the loops use those ids.
    body_curves = len(re.findall(r"^(?:Spline|Line|Circle|Ellipse)\(\d+\)", geo, re.M)) - 4
    assert f"Curve Loop(2) = {{{', '.join(map(str, range(1, body_curves + 1)))}}};" in geo
    body_faces = re.search(r'Physical Surface\("body"\) = \{(.*)\};', geo).group(1)
    assert body_faces.count("out[") == body_curves
    assert ("Field[7] = Cylinder;" in geo) == (name != "airfoil")  # wake refinement


def test_slip_walls_get_no_boundary_layer(tmp_path):
    job = make_job(tmp_path, **CASES["cylinder"], wall_type="slip")
    cfd_case.write_geo(job)
    assert "BoundaryLayer" not in (job / "mesh" / "body.geo").read_text()


def test_rough_walls_use_wall_functions_and_a_taller_first_cell(tmp_path):
    (tmp_path / "smooth").mkdir()
    (tmp_path / "rough").mkdir()
    smooth = make_job(tmp_path / "smooth", **CASES["airfoil"])
    rough = make_job(tmp_path / "rough", **CASES["airfoil"], roughness_height=0.001)
    cfd_case.write_geo(smooth)
    cfd_case.write_geo(rough)

    def first_cell(job):
        geo = (job / "mesh" / "body.geo").read_text()
        return float(re.search(r"hwall_n = (\S+);", geo).group(1))

    assert first_cell(rough) == pytest.approx(30 * first_cell(smooth), rel=1e-3)
    cfd_case.write_foam(rough, 1)
    assert "nutkRoughWallFunction" in (rough / "0" / "nut").read_text()


@pytest.mark.parametrize("name", CASES)
def test_foam_case_is_complete(tmp_path, name):
    job = make_job(tmp_path, **CASES[name])
    cfd_case.write_foam(job, 4)

    for path in (
        "0/U",
        "0/p",
        "0/k",
        "0/omega",
        "0/nut",
        "constant/turbulenceProperties",
        "constant/transportProperties",
        "system/controlDict",
        "system/fvSchemes",
        "system/fvSolution",
        "system/decomposeParDict",
    ):
        assert (job / path).is_file(), path
    transient = name != "airfoil"
    control = (job / "system" / "controlDict").read_text()
    assert ("pimpleFoam" if transient else "simpleFoam") in control
    assert ("PIMPLE" if transient else "SIMPLE") in (job / "system" / "fvSolution").read_text()
    assert "numberOfSubdomains 4;" in (job / "system" / "decomposeParDict").read_text()
    # Every boundary field names exactly the three patches.
    for field in ("U", "p", "k", "omega", "nut"):
        text = (job / "0" / field).read_text()
        assert all(patch in text for patch in ("farfield", "body", "frontAndBack"))


def test_freestream_follows_the_angle_of_attack(tmp_path):
    job = make_job(tmp_path, **CASES["airfoil"])
    cfd_case.write_foam(job, 1)
    alpha = math.radians(4)

    assert f"({math.cos(alpha):.8g} {math.sin(alpha):.8g} 0)" in (job / "0" / "U").read_text()
    control = (job / "system" / "controlDict").read_text()
    assert f"liftDir         ({-math.sin(alpha):.8g} {math.cos(alpha):.8g} 0)" in control


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"wall_type": "slip"}, "type slip;"),
        ({"wall_type": "moving", "wall_velocity_x": 0.5}, "value uniform (0.5 0 0)"),
        ({"wall_type": "rotating", "spin_ratio": 2.0}, "omega 4;"),
    ],
)
def test_wall_conditions_reach_the_velocity_field(tmp_path, fields, expected):
    job = make_job(tmp_path, **CASES["cylinder"], **fields)
    cfd_case.write_foam(job, 1)
    assert expected in (job / "0" / "U").read_text()


def test_low_reynolds_runs_laminar(tmp_path):
    job = make_job(tmp_path, shape="cylinder", reynolds=100)
    cfd_case.write_foam(job, 1)

    assert "simulationType laminar;" in (job / "constant" / "turbulenceProperties").read_text()
    assert not (job / "0" / "k").exists()


def test_boundary_patches_become_wall_and_empty(tmp_path):
    boundary = tmp_path / "constant" / "polyMesh" / "boundary"
    boundary.parent.mkdir(parents=True)
    block = "    {name}\n    {{\n        type patch;\n        nFaces 10;\n    }}\n"
    boundary.write_text(
        "3\n(\n"
        + "".join(block.format(name=n) for n in ("farfield", "body", "frontAndBack"))
        + ")\n"
    )

    cfd_case.fix_boundary(tmp_path)

    text = boundary.read_text()
    assert re.search(r"body\s*\{\s*type\s+wall;", text)
    assert re.search(r"frontAndBack\s*\{\s*type\s+empty;", text)
    assert re.search(r"farfield\s*\{\s*type\s+patch;", text)


def write_coefficients(job: Path, rows: list[tuple[float, float, float, float]]) -> None:
    out = job / "postProcessing" / "forceCoeffs" / "0"
    out.mkdir(parents=True)
    lines = ["# Force coefficients", "# Time Cm Cd Cl Cl(f) Cl(r)"]
    lines += [f"{t} {cm} {cd} {cl} 0 0" for t, cd, cl, cm in rows]
    (out / "coefficient.dat").write_text("\n".join(lines) + "\n")


def test_steady_results_average_the_tail_and_report_convergence(tmp_path):
    job = make_job(tmp_path, **CASES["airfoil"])
    write_coefficients(job, [(i, 0.02, 0.5 + 1 / i, -0.05) for i in range(1, 301)])
    (job / "log.solver").write_text(
        "Solving for Ux, Initial residual = 1e-3\nSolving for p, Initial residual = 2e-6\n"
        "SIMPLE solution converged in 300 iterations\n"
    )

    result = cfd_case.build_results(job)

    assert result["converged"] is True
    assert result["iterations"] == 300
    assert result["cl"] == pytest.approx(0.5, abs=0.01)
    assert result["cd"] == pytest.approx(0.02)
    assert result["final_residuals"] == {"Ux": 1e-3, "p": 2e-6}
    assert result["warnings"] == []
    assert len(result["history"]["time"]) <= cfd_case.MAX_HISTORY_POINTS + 1
    assert result["history"]["time"][-1] == 300


def test_unconverged_steady_run_says_so(tmp_path):
    job = make_job(tmp_path, **CASES["airfoil"])
    write_coefficients(job, [(i, 0.02, 0.5, -0.05) for i in range(1, 51)])
    (job / "log.solver").write_text("Solving for p, Initial residual = 0.01\n")

    result = cfd_case.build_results(job)

    assert result["converged"] is False
    assert "did not reach their targets" in result["warnings"][0]


def test_transient_results_find_the_shedding_frequency(tmp_path):
    job = make_job(tmp_path, **CASES["cylinder"])
    strouhal = 0.2
    rows = [
        (t / 20, 1.2, 0.6 * math.sin(2 * math.pi * strouhal * t / 20), 0.0) for t in range(1, 1601)
    ]
    write_coefficients(job, rows)
    (job / "log.solver").write_text("Solving for p, Initial residual = 1e-4\n")

    result = cfd_case.build_results(job)

    assert result["regime"] == "transient"
    assert result["strouhal"] == pytest.approx(strouhal, rel=0.02)
    assert result["cl_rms"] == pytest.approx(0.6 / math.sqrt(2), rel=0.05)
    assert "converged" not in result


def test_steady_flow_reports_no_shedding(tmp_path):
    job = make_job(tmp_path, **CASES["cylinder"])
    write_coefficients(job, [(t / 10, 1.2, 0.0, 0.0) for t in range(1, 400)])
    (job / "log.solver").write_text("")

    result = cfd_case.build_results(job)

    assert "strouhal" not in result
    assert any("No periodic vortex shedding" in warning for warning in result["warnings"])


def test_status_is_written_atomically(tmp_path):
    cfd_case.write_status(tmp_path, "failed", "boom")

    assert json.loads((tmp_path / "status.json").read_text()) == {
        "status": "failed",
        "error": "boom",
    }
    assert not (tmp_path / "status.json.tmp").exists()
