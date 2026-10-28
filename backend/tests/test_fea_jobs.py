"""FEA jobs through the API's service and the agent's tool (the work station is a fake)."""

import json
from functools import partial

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pymongo import MongoClient

from api.agent.tools import simulation_tools
from api.compute.pool import Workstation
from api.compute.runner import CommandResult
from api.compute.schemas import FeaCase
from api.compute.simulations import GeometryUnavailable
from tests.conftest import MONGODB_URI, sign_up_and_in
from tests.test_file_kinds import binary_stl, cube_triangles
from tests.test_files import upload


class RecordingStation:
    """A work station that records what it's given."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.files: dict[str, bytes | str] = {}
        self.summary: dict = {}

    async def run(self, command, *, cwd=None, timeout_s=None):
        self.commands.append(command)
        if " summary " in command:
            return CommandResult(0, json.dumps(self.summary), "")
        return CommandResult(0, "", "")

    async def write_file(self, path, content):
        self.files[path] = content

    async def write_bytes(self, path, content):
        self.files[path] = content

    async def read_file(self, path):
        raise FileNotFoundError(path)


@pytest.fixture
def station(client: TestClient) -> RecordingStation:
    fake = RecordingStation()
    client.app.state.services.compute.set_shared(
        Workstation(fake, "/w", "/w/bin/run_openfoam.sh", "shared")
    )
    return fake


def service_for_current_user(client: TestClient):
    user_id = client.get("/api/auth/me").json()["id"]
    return client.portal.call(client.app.state.services.compute.simulations_for, user_id)


def fea_case(file_id: str, **changes) -> FeaCase:
    return FeaCase(geometry_file_id=file_id, support_axis="z", support_side="min", **changes)


def test_an_fea_case_needs_a_support_and_a_sensible_axis():
    with pytest.raises(ValidationError):
        FeaCase(geometry_file_id="x")  # no support given
    with pytest.raises(ValidationError):
        fea_case("x", spin_rpm=1000, spin_axis_direction=[0, 0, 0])
    with pytest.raises(ValidationError):
        fea_case("x", material="unobtainium")
    assert fea_case("x").kind == "fea" and fea_case("x").units == "mm"


def test_submitting_sends_the_solid_and_launches_the_fea_script(client, station):
    stl = binary_stl(cube_triangles())
    file = upload(client, "cube.stl", stl).json()

    job = client.portal.call(
        service_for_current_user(client).submit, fea_case(file["id"], spin_rpm=30000, modal=True)
    )

    assert job.status == "running" and job.case.kind == "fea"
    assert job.case.geometry_name == "cube.stl"
    job_dir = f"/w/jobs/{job.id}"
    assert station.files[f"{job_dir}/geometry.stl"] == stl
    sent = json.loads(station.files[f"{job_dir}/case.json"])
    assert (
        sent["kind"] == "fea" and sent["spin_rpm"] == 30000 and sent["geometry_name"] == "cube.stl"
    )
    launch = station.commands[-1]
    assert "/w/bin/run_fea.sh" in launch and job_dir in launch and "setsid nohup" in launch


def test_the_solid_has_to_be_the_users_own_stl(client, station):
    service = service_for_current_user(client)
    submit = lambda file_id: client.portal.call(partial(service.submit, fea_case(file_id)))  # noqa: E731

    with pytest.raises(GeometryUnavailable, match="no uploaded geometry"):
        submit("missing")
    text = upload(client, "notes.txt", b"hello").json()
    with pytest.raises(GeometryUnavailable, match="no uploaded geometry"):
        submit(text["id"])  # plain text files keep no original bytes
    obj = upload(client, "cube.obj", b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n").json()
    with pytest.raises(GeometryUnavailable, match="OBJ file; FEA needs an STL"):
        submit(obj["id"])
    assert not any("run_fea.sh" in command for command in station.commands)

    mine = upload(client, "cube.stl", binary_stl(cube_triangles())).json()
    client.post("/api/auth/logout")
    sign_up_and_in(client, email="other@example.com")
    with pytest.raises(GeometryUnavailable):
        client.portal.call(partial(service_for_current_user(client).submit, fea_case(mine["id"])))


def test_the_agents_tool_starts_the_job_and_explains_a_bad_file(client, station):
    file = upload(client, "cube.stl", binary_stl(cube_triangles())).json()
    tools = {t.name: t for t in simulation_tools(service_for_current_user(client))}
    args = {"geometry_file_id": file["id"], "support_axis": "z", "support_side": "min"}

    started = client.portal.call(tools["submit_fea_simulation"].ainvoke, args)
    refused = client.portal.call(
        tools["submit_fea_simulation"].ainvoke, {**args, "geometry_file_id": "nope"}
    )

    assert "Job " in started and "running" in started and "do not wait for it" in started
    assert refused.startswith("Not started: no uploaded geometry has the id 'nope'")


def test_fea_jobs_are_listed_with_their_own_progress(client, station):
    file = upload(client, "cube.stl", binary_stl(cube_triangles())).json()
    job = client.portal.call(service_for_current_user(client).submit, fea_case(file["id"]))
    station.summary = {
        job.id: {"status": "running", "stage": "solve", "fraction": 0.5, "eta_s": None}
    }

    listed = client.get("/api/simulations").json()

    assert [(j["id"], j["case"]["kind"], j["stage"], j["fraction"]) for j in listed] == [
        (job.id, "fea", "solve", 0.5)
    ]
    summary_command = next(c for c in station.commands if " summary " in c)
    assert "/w/bin/fea_case.py summary" in summary_command  # not the CFD script


def test_the_monitor_asks_the_fea_script_about_fea_jobs(client, station):
    file = upload(client, "cube.stl", binary_stl(cube_triangles())).json()
    job = client.portal.call(service_for_current_user(client).submit, fea_case(file["id"]))

    client.get(f"/api/simulations/{job.id}/progress")
    client.get(f"/api/simulations/{job.id}/info")

    assert any(f"/w/bin/fea_case.py progress /w/jobs/{job.id}" in c for c in station.commands)
    assert any(f"/w/bin/fea_case.py info /w/jobs/{job.id}" in c for c in station.commands)


def test_fea_runs_are_counted_in_the_usage(client, station):
    file = upload(client, "cube.stl", binary_stl(cube_triangles())).json()
    client.portal.call(service_for_current_user(client).submit, fea_case(file["id"]))

    usage = client.get("/api/simulations/usage").json()

    assert usage["all_time"]["by_shape"] == {"fea": 1}


def test_a_stored_cfd_job_from_before_fea_still_loads(client, station):
    source = MongoClient(MONGODB_URI)
    try:
        user_id = client.get("/api/auth/me").json()["id"]
        old = {
            "_id": "old-job", "user_id": user_id, "thread_id": None, "status": "completed",
            "case": {"shape": "cylinder", "reynolds": 100.0, "mach": 0.1, "mesh": "medium"},
            "created_at": __import__("datetime").datetime.now(__import__("datetime").UTC),
            "updated_at": __import__("datetime").datetime.now(__import__("datetime").UTC),
        }  # fmt: skip
        source[client.app.state.db.name]["simulations"].insert_one(old)
    finally:
        source.close()

    body = client.get("/api/simulations/old-job").json()

    assert body["case"]["kind"] == "cfd" and body["case"]["shape"] == "cylinder"
