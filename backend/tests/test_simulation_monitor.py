import json
from datetime import UTC, datetime
from functools import partial

from api.compute.runner import CommandResult
from api.compute.schemas import CfdCase
from api.compute.simulations import SimulationService


class FakeWorkStation:
    """Answers the monitor's calls like the work station would."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.files = {"/w/jobs": b""}
        self.fail = False
        self.progress_payload = {"stage": "solver", "log_offset": 10, "fraction": 0.25}
        self.statuses: dict[str, str] = {}  # job id -> what its status.json says

    def summary_payload(self, command: str) -> dict:
        ids = [part for part in command.split() if part.count("-") == 4]
        return {
            job_id: {
                "status": self.statuses.get(job_id),
                "stage": "solver",
                "fraction": 0.4,
                "eta_s": 120.0,
            }
            for job_id in ids
        }

    async def read_status(self, job_id: str) -> str:
        return json.dumps({"status": self.statuses[job_id], "result": {"cl": 1.0}})

    async def run(self, command, *, cwd=None, timeout_s=None):
        self.commands.append(command)
        if self.fail:
            return CommandResult(1, "", "Traceback\nFileNotFoundError: case.json")
        if " summary " in command:
            return CommandResult(0, json.dumps(self.summary_payload(command)), "")
        if " info " in command:
            return CommandResult(0, json.dumps({"solver": "pimpleFoam"}), "")
        if " progress " in command:
            return CommandResult(0, json.dumps(self.progress_payload), "")
        return CommandResult(0, "", "")  # the launch

    async def write_file(self, path, content):
        return None

    async def read_file(self, path):
        for job_id, status in self.statuses.items():
            if path == f"/w/jobs/{job_id}/status.json":
                return json.dumps({"status": status, "result": {"cl": 1.0}})
        raise FileNotFoundError(path)

    async def read_bytes(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


def submit_job(client, station) -> str:
    service = SimulationService(
        client.app.state.db, station, workdir="/w", script="/w/bin/run_case.sh"
    )
    client.app.state.services.simulations = service
    case = CfdCase(shape="cylinder", reynolds=100)
    return client.portal.call(partial(service.submit, case)).id


def test_info_runs_the_case_tool_next_to_the_run_script(client):
    station = FakeWorkStation()
    job_id = submit_job(client, station)

    response = client.get(f"/api/simulations/{job_id}/info")

    assert response.status_code == 200
    assert response.json() == {"solver": "pimpleFoam"}
    assert station.commands[-1] == f"python3 /w/bin/cfd_case.py info /w/jobs/{job_id}"


def test_progress_passes_the_offsets_and_adds_the_job_status(client):
    station = FakeWorkStation()
    job_id = submit_job(client, station)

    response = client.get(
        f"/api/simulations/{job_id}/progress", params={"log_offset": 120, "coeff_offset": 7}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "running", **station.progress_payload}
    assert station.commands[-1].endswith(f"progress /w/jobs/{job_id} 120 7")


def test_progress_asks_for_the_whole_remainder_once_the_job_has_ended(client):
    station = FakeWorkStation()
    job_id = submit_job(client, station)
    client.portal.call(
        partial(
            client.app.state.services.simulations._update,  # noqa: SLF001
            job_id,
            status="completed",
        )
    )

    client.get(f"/api/simulations/{job_id}/progress")

    assert station.commands[-1].endswith("0 0 final")


def test_an_unreachable_work_station_is_a_bad_gateway_not_a_crash(client):
    station = FakeWorkStation()
    job_id = submit_job(client, station)
    station.fail = True

    response = client.get(f"/api/simulations/{job_id}/progress")

    assert response.status_code == 502
    assert "case.json" in response.json()["detail"]


def test_unknown_jobs_are_404(client):
    assert client.get("/api/simulations/nope/info").status_code == 404
    assert client.get("/api/simulations/nope/progress").status_code == 404


def test_only_the_rendered_files_are_served(client):
    station = FakeWorkStation()
    job_id = submit_job(client, station)
    station.files[f"/w/jobs/{job_id}/flow.mp4"] = b"video-bytes"
    station.files[f"/w/jobs/{job_id}/velocity.png"] = b"png-bytes"

    video = client.get(f"/api/simulations/{job_id}/files/flow.mp4")

    assert video.status_code == 200
    assert video.content == b"video-bytes"
    assert video.headers["content-type"] == "video/mp4"
    image = client.get(f"/api/simulations/{job_id}/files/velocity.png")
    assert (image.status_code, image.headers["content-type"]) == (200, "image/png")
    assert (
        client.get(f"/api/simulations/{job_id}/files/pressure.png").status_code == 404
    )  # not made
    assert client.get(f"/api/simulations/{job_id}/files/case.json").status_code == 404
    assert client.get(f"/api/simulations/{job_id}/files/..%2Fstatus.json").status_code == 404


def test_the_list_shows_running_jobs_with_progress_first(client):
    station = FakeWorkStation()
    done = submit_job(client, station)
    client.portal.call(
        partial(client.app.state.services.simulations._update, done, status="completed")  # noqa: SLF001
    )
    running = submit_job(client, station)

    jobs = client.get("/api/simulations").json()

    assert [job["id"] for job in jobs][:2] == [running, done]
    assert jobs[0]["status"] == "running"
    assert jobs[0]["fraction"] == 0.4
    assert jobs[0]["eta_s"] == 120.0
    assert jobs[0]["stage"] == "solver"
    assert jobs[1]["status"] == "completed"
    assert jobs[1]["fraction"] is None  # finished jobs have no progress bar
    assert jobs[0]["case"]["shape"] == "cylinder"
    # One call to the work station covers every running job, and not the finished ones.
    summaries = [c for c in station.commands if " summary " in c]
    assert len(summaries) == 1
    assert running in summaries[0]
    assert done not in summaries[0]


def test_a_job_that_ended_is_moved_to_finished_when_listed(client):
    station = FakeWorkStation()
    job_id = submit_job(client, station)
    station.statuses[job_id] = "completed"

    jobs = client.get("/api/simulations").json()

    assert jobs[0]["id"] == job_id
    assert jobs[0]["status"] == "completed"


def test_the_list_still_works_when_the_work_station_is_unreachable(client):
    station = FakeWorkStation()
    job_id = submit_job(client, station)
    station.fail = True

    jobs = client.get("/api/simulations").json()

    assert [job["id"] for job in jobs] == [job_id]
    assert jobs[0]["status"] == "running"
    assert jobs[0]["fraction"] is None


def test_an_empty_list_is_an_empty_array(client):
    assert client.get("/api/simulations").json() == []


def test_deleting_a_job_removes_its_record_and_its_files(client):
    station = FakeWorkStation()
    job_id = submit_job(client, station)

    response = client.delete(f"/api/simulations/{job_id}")

    assert response.status_code == 200
    assert response.json() == {"files_removed": True}
    assert client.get(f"/api/simulations/{job_id}").status_code == 404
    command = station.commands[-1]
    assert f"rm -rf -- /w/jobs/{job_id}" in command
    assert "kill -TERM" in command  # a running job is stopped first
    assert f"[r]un_case.sh .*{job_id}" in command


def test_deleting_a_job_still_works_when_the_work_station_is_unreachable(client):
    station = FakeWorkStation()
    job_id = submit_job(client, station)
    station.fail = True

    response = client.delete(f"/api/simulations/{job_id}")

    assert response.json() == {"files_removed": False}
    assert client.get(f"/api/simulations/{job_id}").status_code == 404


def test_deleting_an_unknown_job_is_404(client):
    assert client.delete("/api/simulations/nope").status_code == 404


def test_odd_job_ids_never_reach_a_recursive_delete(client):
    station = FakeWorkStation()
    submit_job(client, station)
    service = client.app.state.services.simulations
    now = datetime.now(UTC)
    client.portal.call(
        partial(
            service._collection.insert_one,  # noqa: SLF001
            {
                "_id": "../..",
                "thread_id": None,
                "case": CfdCase(shape="cylinder", reynolds=100).model_dump(),
                "status": "failed",
                "created_at": now,
                "updated_at": now,
            },
        )
    )
    before = len(station.commands)

    removed = client.portal.call(service.delete, "../..")

    assert removed is False
    assert len(station.commands) == before  # nothing was run on the work station
