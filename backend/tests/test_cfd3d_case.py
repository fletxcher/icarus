"""The work station's 3D CFD case builder (workstation/cfd3d_case.py), checked without OpenFOAM."""

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

_dir = Path(__file__).resolve().parents[2] / "workstation"
sys.path.insert(0, str(_dir))  # cfd3d_case imports fea_case, which sits next to it
_spec = importlib.util.spec_from_file_location("cfd3d_case", _dir / "cfd3d_case.py")
cfd3d = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfd3d)

CASE = {
    "kind": "cfd3d", "geometry_file_id": "f", "geometry_name": "bar.stl", "units": "m",
    "domain": "external", "speed_ms": 30.0, "flow_direction": [0.0, -1.0, 0.0],
    "density_kg_m3": 1.225, "kinematic_viscosity_m2_s": 1.5e-5, "speed_of_sound_ms": 340.0,
    "turbulence_intensity": 0.05, "mesh": "medium", "iterations": None, "fill_cavities": False,
    "pitch": None, "pitch_axis": None, "spin_rpm": 0.0,
    "spin_axis_origin": [0.0, 0.0, 0.0], "spin_axis_direction": [0.0, 0.0, 1.0],
}  # fmt: skip


def box(size=(0.2, 0.02, 0.01)) -> np.ndarray:
    """Triangles of a closed box centred on the origin."""
    sx, sy, sz = (s / 2 for s in size)
    v = np.array([[x * sx, y * sy, z * sz] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)])
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return np.array([v[list(f)][list(t)] for f in faces for t in ((0, 1, 2), (0, 2, 3))])


def case(**changes) -> dict:
    return {**CASE, **changes}


def test_the_frontal_area_is_what_the_flow_sees():
    triangles = box((0.2, 0.02, 0.01))

    assert cfd3d.projected_area(triangles, [1, 0, 0]) == pytest.approx(0.02 * 0.01)
    assert cfd3d.projected_area(triangles, [0, 0, 1]) == pytest.approx(0.2 * 0.02)
    assert cfd3d.projected_area(triangles, [0, -1, 0]) == pytest.approx(0.2 * 0.01)


def test_an_external_box_has_room_upstream_downstream_and_to_the_sides():
    geo = cfd3d.layout(case(), box())  # flow along -y; the body is 0.2 long, 0.02 wide

    assert geo["flow_axis"] == 1 and geo["flow_sign"] == -1 and geo["length"] == pytest.approx(0.2)
    low, high = geo["box_low"], geo["box_high"]
    assert low[1] == pytest.approx(-0.01 - 7 * 0.2)  # downstream is the -y side
    assert high[1] == pytest.approx(0.01 + 3 * 0.2)
    assert high[0] - low[0] == pytest.approx(5 * 0.2) and high[2] - low[2] == pytest.approx(5 * 0.2)


def test_the_block_mesh_has_an_inlet_an_outlet_and_far_sides():
    geo = cfd3d.layout(case(), box())
    text = cfd3d.block_mesh_dict(case(), geo, cfd3d.LEVELS["medium"])

    for name in ("inlet", "outlet", "far"):
        assert f"    {name}\n" in text
    assert "cyclic" not in text
    # Flow runs towards -y, so the inlet is the y-max face and the outlet the y-min face.
    inlet = text.split("    inlet")[1].split("}")[0]
    assert "(3 7 6 2)" in inlet
    counts = [int(n) for n in text.split("hex (0 1 2 3 4 5 6 7) (")[1].split(")")[0].split()]
    assert all(n % 2 == 0 and n >= 4 for n in counts)


def test_a_passage_is_periodic_across_the_pitch_and_closed_at_the_ends_of_the_span():
    c = case(domain="passage", pitch=0.05, pitch_axis="x")
    geo = cfd3d.layout(c, box((0.02, 0.2, 0.01)))

    text = cfd3d.block_mesh_dict(c, geo, cfd3d.LEVELS["coarse"])

    assert "neighbourPatch periodicMax" in text and "neighbourPatch periodicMin" in text
    assert "spanWalls" in text and "    far\n" not in text
    assert geo["box_high"][0] - geo["box_low"][0] == pytest.approx(0.05)  # exactly one pitch
    assert geo["span_axis"] == 2 and geo["pitch_axis"] == 0


def test_blades_that_would_overlap_are_refused():
    with pytest.raises(SystemExit, match="would overlap"):
        cfd3d.layout(case(domain="passage", pitch=0.2, pitch_axis="x"), box((0.2, 0.1, 0.01)))
    with pytest.raises(SystemExit, match="can't be the flow direction"):
        cfd3d.layout(case(domain="passage", pitch=0.5, pitch_axis="y"), box())


def test_the_refinement_in_a_passage_stops_short_of_the_periodic_faces():
    """A refinement band that reached one periodic face but not the other would not match."""
    c = case(domain="passage", pitch=0.05, pitch_axis="x")
    triangles = box((0.02, 0.2, 0.01))  # 0.02 wide across the pitch, so 0.015 to each periodic face
    geo = cfd3d.layout(c, triangles)

    text = cfd3d.snappy_dict(c, geo, cfd3d.LEVELS["medium"], 100_000, 0.05)

    distances = [
        float(d)
        for d in text.split("mode distance; levels (")[1]
        .split(")")[0]
        .replace("(", " ")
        .split()[::2]
    ]
    assert distances and max(distances) < 0.8 * geo["free_distance"] + 1e-9
    assert "wake" not in text and "rotor" not in text


def test_an_external_case_refines_a_wake_behind_the_solid():
    geo = cfd3d.layout(case(), box())

    text = cfd3d.snappy_dict(case(), geo, cfd3d.LEVELS["medium"], 100_000, 0.05)

    assert "wake { type searchableBox" in text and "level (4 5)" in text
    assert "body.stl { type triSurfaceMesh; name body; }" in text
    assert "patchInfo { type wall; }" in text


def test_a_rotating_case_has_a_zone_around_the_solid_that_turns_with_it():
    c = case(domain="rotating", spin_rpm=3000.0, flow_direction=[0, 0, 1.0], speed_ms=5.0)
    geo = cfd3d.layout(c, box((0.2, 0.024, 0.008)))  # a paddle turning about z through its centre

    assert geo["zone"]["radius"] == pytest.approx(1.25 * 0.1, rel=0.02)  # 25% beyond the tip
    text = cfd3d.snappy_dict(c, geo, cfd3d.LEVELS["coarse"], 100_000, 0.05)
    assert "searchableCylinder" in text and "cellZone rotatingZone" in text
    assert "cellZoneInside inside" in text


def test_the_solver_case_sets_inflow_turbulence_and_the_right_boundaries(tmp_path):
    triangles = box()
    geo = cfd3d.layout(case(), triangles)

    cfd3d.write_solver_case(tmp_path, case(), geo, {"cells": 1000}, 800)

    u = (tmp_path / "0/U").read_text()
    assert "value uniform (0 -30 0)" in u and "type freestreamVelocity" in u and "type noSlip" in u
    k = float((tmp_path / "0/k").read_text().split("internalField uniform ")[1].split(";")[0])
    assert k == pytest.approx(1.5 * (0.05 * 30) ** 2, rel=1e-3)
    assert "endTime 800;" in (tmp_path / "system/controlDict").read_text()
    assert "rhoInf 1.225" in (tmp_path / "system/controlDict").read_text()
    assert (
        "nu [0 2 -1 0 0 0 0] 1.500000e-05"
        in (tmp_path / "constant/transportProperties").read_text()
    )
    assert not (tmp_path / "constant/MRFProperties").exists()


def test_a_passage_case_uses_cyclic_and_slip_boundaries(tmp_path):
    c = case(domain="passage", pitch=0.05, pitch_axis="x")
    geo = cfd3d.layout(c, box((0.02, 0.2, 0.01)))

    cfd3d.write_solver_case(tmp_path, c, geo, {"cells": 1000}, 400)

    u = (tmp_path / "0/U").read_text()
    assert u.count("type cyclic;") == 2 and "type slip;" in u and "freestream" not in u


def test_a_rotating_case_gets_an_mrf_zone_at_the_spin_rate(tmp_path):
    c = case(domain="rotating", spin_rpm=3000.0, flow_direction=[0, 0, 1.0], speed_ms=5.0,
             spin_axis_origin=[0, 0, 0], spin_axis_direction=[0, 0, 2.0])  # fmt: skip
    geo = cfd3d.layout(c, box((0.2, 0.024, 0.008)))

    cfd3d.write_solver_case(tmp_path, c, geo, {"cells": 1000}, 300)

    mrf = (tmp_path / "constant/MRFProperties").read_text()
    assert "cellZone rotatingZone;" in mrf and "axis (0 0 1);" in mrf  # the axis is normalised
    assert f"omega {3000 * 2 * math.pi / 60:.8g};" in mrf


FORCES = """# Force
# CofR : (0 0 0)
# Time total_x total_y total_z pressure_x pressure_y pressure_z viscous_x viscous_y viscous_z
"""


def finished_job(tmp_path: Path, domain="external", **changes) -> Path:
    c = case(domain=domain, **changes)
    triangles = box()
    geo = cfd3d.layout(c, triangles)
    (tmp_path / "case.json").write_text(json.dumps(c))
    (tmp_path / "mesh.json").write_text(json.dumps({
        "cells": 120000, "level": "medium", "iterations": 800, "notes": ["a note"], "geometry": geo,
    }))  # fmt: skip
    rows = "".join(
        f"{i} (0.01 -0.2 0.002) (0 0 0) (0 0 0)\n".replace("(", "").replace(")", "")
        for i in range(5, 105, 5)
    )
    for name in ("force", "moment"):
        folder = tmp_path / "postProcessing/forces/0"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.dat").write_text(FORCES + rows)
    (tmp_path / "log.solver").write_text(
        "Time = 99\n"
        "smoothSolver:  Solving for Ux, Initial residual = 2e-05, Final residual = 1e-07\n"
        "GAMG:  Solving for p, Initial residual = 3e-05, Final residual = 1e-07\n"
        "SIMPLE solution converged in 100 iterations\n"
    )
    (tmp_path / "log.yPlus").write_text("patch body y+ : min = 10, max = 120, average = 45\n")
    return tmp_path


def test_the_forces_are_averaged_over_the_end_of_the_run_and_turned_into_coefficients(tmp_path):
    finished_job(tmp_path)

    result, view = cfd3d.build_results(tmp_path)

    assert result["force_n"] == [0.01, -0.2, 0.002]
    assert result["drag_n"] == pytest.approx(0.2)  # the flow travels along -y
    assert result["cross_force_n"] == pytest.approx(math.hypot(0.01, 0.002), abs=1e-5)
    area = 0.2 * 0.01  # what the flow sees of the box
    assert result["drag_coefficient"] == pytest.approx(0.2 / (0.5 * 1.225 * 30**2 * area), rel=1e-3)
    assert result["converged"] is True and result["iterations"] == 100
    assert result["y_plus"]["average"] == 45 and result["force_variation"] == 0
    assert "a note" in result["warnings"]
    assert not any("converged after" in w for w in result["warnings"])


def test_warnings_say_when_the_result_should_be_doubted(tmp_path):
    finished_job(tmp_path, speed_ms=150.0)
    (tmp_path / "log.solver").write_text(
        "Time = 800\nGAMG:  Solving for p, Initial residual = 3e-02, Final residual = 1e-07\n"
    )
    (tmp_path / "log.yPlus").write_text("patch body y+ : min = 400, max = 900, average = 600\n")

    result, _ = cfd3d.build_results(tmp_path)

    text = " ".join(result["warnings"])
    assert result["converged"] is False and "hadn't converged after 800 iterations" in text
    assert "0.44 of the speed of sound" in text and "incompressible" in text
    assert "y+ averages 600" in text


def test_a_rotating_job_reports_thrust_torque_and_efficiency(tmp_path):
    finished_job(
        tmp_path,
        domain="rotating",
        spin_rpm=3000.0,
        flow_direction=[0, 0, 1.0],
        speed_ms=5.0,
        spin_axis_origin=[0, 0, 0],
        spin_axis_direction=[0, 0, 1.0],
    )
    rows = "".join(f"{i} 0 0 0.6 0 0 0 0 0 0\n" for i in range(5, 105, 5))
    (tmp_path / "postProcessing/forces/0/force.dat").write_text(FORCES + rows)
    moments = "".join(f"{i} 0 0 -0.02 0 0 0 0 0 0\n" for i in range(5, 105, 5))
    (tmp_path / "postProcessing/forces/0/moment.dat").write_text(FORCES + moments)

    result, _ = cfd3d.build_results(tmp_path)

    omega = 3000 * 2 * math.pi / 60
    assert result["thrust_n"] == pytest.approx(0.6) and result["torque_nm"] == pytest.approx(-0.02)
    assert result["shaft_power_w"] == pytest.approx(0.02 * omega, rel=1e-3)
    assert result["propulsive_efficiency"] == pytest.approx(0.6 * 5.0 / (0.02 * omega), rel=1e-3)
    assert any("frozen-rotor" in w for w in result["warnings"])


def test_the_monitor_knows_the_stage_from_the_logs(tmp_path):
    (tmp_path / "case.json").write_text(json.dumps(CASE))
    (tmp_path / "status.json").write_text('{"status": "running"}')
    assert cfd3d.build_progress(tmp_path, 0, False)["stage"] == "starting"

    (tmp_path / "log.mesh").write_text("meshing\n")
    assert cfd3d.build_progress(tmp_path, 0, False)["stage"] == "mesh"

    (tmp_path / "mesh.json").write_text(
        json.dumps({"iterations": 100, "cells": 1, "level": "coarse", "notes": []})
    )
    (tmp_path / "log.solve").write_text("")
    (tmp_path / "log.solver").write_text("Time = 50\nTime = 51\n")
    progress = cfd3d.build_progress(tmp_path, 0, False)
    assert progress["stage"] == "solve" and 0.25 < progress["fraction"] < 0.9
    assert progress["lines"] == ["Time = 50", "Time = 51"]

    (tmp_path / "status.json").write_text('{"status": "completed"}')
    done = cfd3d.build_progress(tmp_path, 0, False)
    assert done["stage"] == "results" and done["fraction"] == 1.0
    summary = cfd3d.build_summary(tmp_path.parent, [tmp_path.name, "missing"])
    assert list(summary) == [tmp_path.name] and summary[tmp_path.name]["status"] == "completed"
