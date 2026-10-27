"""The work station's case builder (workstation/cfd_case.py), checked without gmsh or OpenFOAM."""

import importlib.util
import json
import math
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from api.compute.schemas import CfdCase

_path = Path(__file__).resolve().parents[2] / "workstation" / "cfd_case.py"
_spec = importlib.util.spec_from_file_location("cfd_case", _path)
cfd_case = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfd_case)

_render_path = _path.with_name("render_video.py")
_render_spec = importlib.util.spec_from_file_location("render_video", _render_path)
render_video = importlib.util.module_from_spec(_render_spec)
_render_spec.loader.exec_module(render_video)  # paraview itself is only imported by main()

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
    job = make_job(tmp_path, **CASES["airfoil"], video=False)
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


def test_video_runs_keep_every_snapshot_and_other_runs_only_the_last(tmp_path):
    (tmp_path / "video").mkdir()
    (tmp_path / "plain").mkdir()
    with_video = make_job(tmp_path / "video", **CASES["cylinder"])
    without = make_job(tmp_path / "plain", **CASES["cylinder"], video=False)
    steady = make_job(tmp_path, **CASES["airfoil"])
    for job in (with_video, without, steady):
        cfd_case.write_foam(job, 1)

    control = (with_video / "system" / "controlDict").read_text()
    assert "adjustableRunTime" in control
    assert f"writeInterval   {cfd_case.VIDEO_INTERVAL};" in control
    assert "purgeWrite      0;" in control
    assert "writeFormat     binary;" in control
    assert "writeInterval   80.0;" in (without / "system" / "controlDict").read_text()
    assert "purgeWrite      1;" in (steady / "system" / "controlDict").read_text()


def test_results_include_the_video_that_was_rendered(tmp_path):
    job = make_job(tmp_path, **CASES["cylinder"])
    write_coefficients(job, [(t / 10, 1.2, 0.5 * math.sin(t), 0.0) for t in range(1, 800)])
    (job / "log.solver").write_text("")
    media = [
        {"file": "velocity.png", "title": "Velocity magnitude", "kind": "image"},
        {"file": "flow.mp4", "title": "Vorticity (z)", "kind": "video", "frames": 320, "fps": 25},
    ]
    (job / "video.json").write_text(json.dumps({"media": media}))

    result = cfd_case.build_results(job)

    assert result["media"] == media
    assert not any("video" in warning for warning in result["warnings"])


def test_failed_video_is_a_warning_not_a_failed_job(tmp_path):
    job = make_job(tmp_path, **CASES["cylinder"])
    write_coefficients(job, [(t / 10, 1.2, 0.5 * math.sin(t), 0.0) for t in range(1, 800)])
    (job / "log.solver").write_text("")
    (job / "video.failed").write_text("ParaView (pvbatch), ffmpeg or xvfb-run isn't installed")

    result = cfd_case.build_results(job)

    assert "media" not in result
    assert any("pvbatch" in warning and "unaffected" in warning for warning in result["warnings"])


def test_video_can_be_turned_off(tmp_path):
    job = make_job(tmp_path, **CASES["cylinder"], video=False)
    write_coefficients(job, [(t / 10, 1.2, 0.5 * math.sin(t), 0.0) for t in range(1, 800)])
    (job / "log.solver").write_text("")

    assert not any("video" in warning for warning in cfd_case.build_results(job)["warnings"])


def test_snapshots_are_ordered_by_time_index_not_by_name(tmp_path):
    for index in (0, 2, 10, 1):
        folder = tmp_path / "VTK" / f"case_{index}"
        folder.mkdir(parents=True)
        (folder / "internal.vtu").write_text("")

    names = [path.parent.name for path in render_video.snapshot_files(tmp_path)]

    assert names == ["case_0", "case_1", "case_2", "case_10"]


def test_snapshot_times_come_from_the_series_file(tmp_path):
    files = []
    for index in (0, 10, 25):
        folder = tmp_path / "VTK" / f"case_{index}"
        folder.mkdir(parents=True)
        files.append(folder / "internal.vtu")
    series = {
        "file-series-version": "1.0",
        "files": [
            {"name": f"case_{i}.vtm", "time": t} for i, t in ((0, 0.0), (10, 0.25), (25, 0.5))
        ],
    }
    (tmp_path / "VTK" / "case.vtm.series").write_text(json.dumps(series))

    assert render_video.snapshot_times(tmp_path, files) == [0.0, 0.25, 0.5]


def test_snapshot_times_fall_back_to_the_time_directories(tmp_path):
    for name in ("0", "0.25", "0.5", "constant", "system", "VTK"):
        (tmp_path / name).mkdir()
    files = [tmp_path / "VTK" / f"case_{i}" / "internal.vtu" for i in range(3)]

    assert render_video.snapshot_times(tmp_path, files) == [0.0, 0.25, 0.5]
    assert render_video.snapshot_times(tmp_path, files[:2]) == [0.0, 1.0]  # unknown: indices


def test_view_follows_the_body_geometry():
    wedge = {"shape": "wedge", "half_angle": 30}
    assert render_video.body_length(wedge) == pytest.approx(0.5 / math.tan(math.radians(30)))
    assert render_video.body_centre(wedge) == (
        pytest.approx(render_video.body_length(wedge) / 2),
        0.0,
    )
    assert render_video.body_centre({"shape": "airfoil"}) == (0.5, 0.0)
    assert render_video.body_length({"shape": "cylinder"}) == 1.0


SOLVER_LOG = """\
Courant Number mean: 0.1 max: 0.5
deltaT = 0.002
Time = 0.002

smoothSolver:  Solving for Ux, Initial residual = 1, Final residual = 1e-6, No Iterations 2
smoothSolver:  Solving for Uy, Initial residual = 0.5, Final residual = 1e-6, No Iterations 2
GAMG:  Solving for p, Initial residual = 0.9, Final residual = 1e-3, No Iterations 4
GAMG:  Solving for p, Initial residual = 0.1, Final residual = 1e-8, No Iterations 6
ExecutionTime = 0.1 s

Courant Number mean: 0.1 max: 0.7
deltaT = 0.003
Time = 0.005

smoothSolver:  Solving for Ux, Initial residual = 0.1, Final residual = 1e-6, No Iterations 2
GAMG:  Solving for p, Initial residual = 0.2, Final residual = 1e-3, No Iterations 4
"""


def test_solver_steps_are_parsed_with_the_first_residual_of_each_field():
    steps = cfd_case.parse_steps(SOLVER_LOG)

    assert steps["time"] == [0.002, 0.005]
    assert steps["residuals"]["Ux"] == [1.0, 0.1]
    assert steps["residuals"]["p"] == [0.9, 0.2]  # the first of the step's pressure solves
    assert steps["residuals"]["Uy"] == [0.5, None]
    assert steps["courant_max"] == [0.5, 0.7]  # printed just before each step's "Time ="
    assert steps["delta_t"] == [0.002, 0.003]


def test_log_chunks_stop_before_the_step_being_written(tmp_path):
    log = tmp_path / "log.solver"
    log.write_text(SOLVER_LOG)

    first, offset = cfd_case.read_chunk(log, 0, final=False)
    assert "Time = 0.002" in first
    assert "Time = 0.005" not in first
    assert not first.rstrip().endswith("deltaT = 0.003")  # the pending step's preamble waits too

    # More output arrives; the pending step is now complete and nothing is read twice.
    log.write_text(SOLVER_LOG + "ExecutionTime = 0.2 s\n\nTime = 0.008\n")
    second, offset = cfd_case.read_chunk(log, offset, final=False)
    assert "Time = 0.005" in second
    assert "Time = 0.008" not in second
    assert cfd_case.parse_steps(first)["time"] + cfd_case.parse_steps(second)["time"] == [
        0.002,
        0.005,
    ]

    # Once the run has ended, the last step is included too.
    last, _ = cfd_case.read_chunk(log, offset, final=True)
    assert "Time = 0.008" in last


def test_progress_reports_stage_fraction_and_new_coefficients(tmp_path):
    job = make_job(tmp_path, **CASES["cylinder"])
    for stage in ("geo", "gmsh", "decomposePar"):
        (job / f"log.{stage}").write_text("")
    (job / "log.solver").write_text(SOLVER_LOG + "\nTime = 20\n")
    write_coefficients(job, [(0.002, 1.2, 0.01, 0.0), (0.005, 1.1, 0.02, 0.0)])

    first = cfd_case.build_progress(job, 0, 0, final=False)

    assert first["stage"] == "solver"
    assert first["latest_time"] == 20
    assert first["fraction"] == pytest.approx(20 / cfd_case.TRANSIENT_END_TIME)
    assert first["steps"]["time"] == [0.002, 0.005]
    assert first["coefficients"]["cd"] == [1.2, 1.1]
    assert first["horizon_unit"] == "convective times"
    assert first["eta_s"] is not None

    again = cfd_case.build_progress(job, first["log_offset"], first["coeff_offset"], final=False)
    assert again["steps"]["time"] == []
    assert again["coefficients"]["time"] == []


def test_info_collects_the_case_the_mesh_and_the_dictionaries(tmp_path):
    job = make_job(tmp_path, **CASES["airfoil"])
    cfd_case.write_foam(job, 4)
    (job / "log.checkMesh").write_text(
        "    cells:            1234\n    Max aspect ratio = 12.5 OK.\n"
        "    Mesh non-orthogonality Max: 40.5 average: 3\n    Max skewness = 0.8 OK.\nMesh OK.\n"
    )

    info = cfd_case.build_info(job)

    assert info["solver"] == "simpleFoam"
    assert info["regime"] == "steady"
    assert info["turbulence"] == "kOmegaSST"
    assert info["mesh"] == {
        "cells": 1234, "points": None, "max_non_orthogonality": 40.5,
        "max_skewness": 0.8, "max_aspect_ratio": 12.5, "ok": True,
    }  # fmt: skip
    assert "system/fvSchemes" in info["dictionaries"]
    assert not info["dictionaries"]["system/fvSchemes"].startswith("FoamFile")
    assert info["horizon"] == cfd_case.MESHES["medium"]["iterations"]


def test_summary_reports_each_jobs_stage_fraction_and_status(tmp_path):
    jobs = tmp_path / "jobs"
    running = make_job(_mkdir(jobs / "a"), **CASES["cylinder"])
    for stage in ("geo", "decomposePar", "solver"):
        (running / f"log.{stage}").write_text("")
    (running / "log.solver").write_text("Time = 40\n")
    done = make_job(_mkdir(jobs / "b"), **CASES["airfoil"])
    cfd_case.write_status(done, "completed", None, {"cl": 1})

    summary = cfd_case.build_summary(jobs, ["a", "b", "gone"])

    assert summary["a"]["stage"] == "solver"
    assert summary["a"]["fraction"] == pytest.approx(0.5)
    assert summary["a"]["status"] is None
    assert summary["a"]["eta_s"] is not None
    assert summary["b"]["status"] == "completed"
    assert summary["b"]["fraction"] == 1.0
    assert summary["gone"] == {"missing": True}


def _mkdir(path):
    path.mkdir(parents=True)
    return path


def test_results_command_logs_what_it_wrote(tmp_path, capsys):
    job = make_job(tmp_path, **CASES["cylinder"], video=False)
    write_coefficients(job, [(t / 10, 1.2, 0.5 * math.sin(t), 0.0) for t in range(1, 800)])
    (job / "log.solver").write_text("")

    cfd_case.main(["cfd_case.py", "results", str(job)])

    printed = capsys.readouterr().out
    assert "transient cylinder: Cl =" in printed
    assert "Cd = 1.2000" in printed
    assert "wrote status.json" in printed
    assert json.loads((job / "status.json").read_text())["status"] == "completed"
