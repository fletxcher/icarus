"""3D CFD jobs through the API's service and the agent's tool (the work station is a fake)."""

import json
from functools import partial

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.agent.tools import simulation_tools
from api.compute.pool import Workstation
from api.compute.schemas import Cfd3dCase
from api.compute.simulations import GeometryUnavailable
from tests.test_fea_jobs import RecordingStation, service_for_current_user
from tests.test_file_kinds import binary_stl, cube_triangles
from tests.test_files import upload


@pytest.fixture
def station(client: TestClient) -> RecordingStation:
    fake = RecordingStation()
    client.app.state.services.compute.set_shared(
        Workstation(fake, "/w", "/w/bin/run_cfd2d.sh", "shared")
    )
    return fake


def cfd3d_case(file_id: str, **changes) -> Cfd3dCase:
    fields = {"domain": "external", "speed_ms": 20, "flow_direction": [0, -1, 0], **changes}
    return Cfd3dCase(geometry_file_id=file_id, **fields)


def test_a_3d_case_is_checked_for_its_domain():
    with pytest.raises(ValidationError):
        cfd3d_case("x", flow_direction=[0, 0, 0])
    with pytest.raises(ValidationError):
        cfd3d_case("x", domain="passage")  # no pitch
    with pytest.raises(ValidationError):
        cfd3d_case("x", domain="passage", pitch=0.05, pitch_axis="y")  # the flow runs along y
    with pytest.raises(ValidationError):
        cfd3d_case("x", domain="rotating")  # no spin
    with pytest.raises(ValidationError):
        cfd3d_case("x", speed_ms=0)
    assert cfd3d_case("x").kind == "cfd3d" and cfd3d_case("x").mesh == "medium"
    assert cfd3d_case("x", domain="passage", pitch=0.05, pitch_axis="x").pitch == 0.05
    assert cfd3d_case("x", domain="rotating", spin_rpm=3000).spin_rpm == 3000


def test_submitting_sends_the_solid_and_launches_the_3d_script(client, station):
    stl = binary_stl(cube_triangles())
    file = upload(client, "cube.stl", stl).json()

    job = client.portal.call(
        service_for_current_user(client).submit, cfd3d_case(file["id"], mesh="coarse")
    )

    assert job.status == "running" and job.case.kind == "cfd3d"
    job_dir = f"/w/jobs/{job.id}"
    assert station.files[f"{job_dir}/geometry.stl"] == stl
    sent = json.loads(station.files[f"{job_dir}/case.json"])
    assert sent["kind"] == "cfd3d" and sent["mesh"] == "coarse" and sent["speed_ms"] == 20
    launch = station.commands[-1]
    assert "/w/bin/run_cfd3d.sh" in launch and job_dir in launch and "setsid nohup" in launch


def test_the_solid_has_to_be_the_users_own_stl(client, station):
    service = service_for_current_user(client)
    obj = upload(client, "cube.obj", b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n").json()

    with pytest.raises(GeometryUnavailable, match="STL of a closed solid"):
        client.portal.call(partial(service.submit, cfd3d_case(obj["id"])))
    with pytest.raises(GeometryUnavailable, match="no uploaded geometry"):
        client.portal.call(partial(service.submit, cfd3d_case("missing")))
    assert not any("run_cfd3d.sh" in command for command in station.commands)


def test_the_agents_tool_starts_the_job_and_explains_a_bad_file(client, station):
    file = upload(client, "cube.stl", binary_stl(cube_triangles())).json()
    tools = {t.name: t for t in simulation_tools(service_for_current_user(client))}
    args = {
        "geometry_file_id": file["id"],
        "domain": "external",
        "speed_ms": 20,
        "flow_direction": [0, -1, 0],
    }

    started = client.portal.call(tools["submit_cfd3d_simulation"].ainvoke, args)
    refused = client.portal.call(
        tools["submit_cfd3d_simulation"].ainvoke, {**args, "geometry_file_id": "nope"}
    )

    assert "running" in started and "never call get_simulation_status to wait" in started
    assert refused.startswith("Not started: no uploaded geometry has the id 'nope'")


def test_3d_jobs_are_listed_monitored_and_counted_with_their_own_script(client, station):
    file = upload(client, "cube.stl", binary_stl(cube_triangles())).json()
    job = client.portal.call(service_for_current_user(client).submit, cfd3d_case(file["id"]))
    station.summary = {
        job.id: {"status": "running", "stage": "mesh", "fraction": 0.1, "eta_s": None}
    }

    listed = client.get("/api/simulations").json()
    client.get(f"/api/simulations/{job.id}/progress")
    client.get(f"/api/simulations/{job.id}/info")
    usage = client.get("/api/simulations/usage").json()

    assert [(j["id"], j["case"]["kind"], j["stage"]) for j in listed] == [(job.id, "cfd3d", "mesh")]
    assert any("/w/bin/cfd3d_case.py summary" in c for c in station.commands)
    assert any(f"/w/bin/cfd3d_case.py progress /w/jobs/{job.id}" in c for c in station.commands)
    assert any(f"/w/bin/cfd3d_case.py info /w/jobs/{job.id}" in c for c in station.commands)
    assert usage["all_time"]["by_shape"] == {"cfd3d": 1}
