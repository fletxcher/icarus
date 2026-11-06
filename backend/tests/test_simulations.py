import asyncio
import shlex
import stat
import time
from functools import partial

import pytest

from api.compute.runner import CommandResult, LocalRunner
from api.compute.schemas import CfdCase
from api.compute.simulations import JobStation, MonitorError, SimulationService


class RemoteLikeRunner(LocalRunner):
    """Runs commands the way SSHRunner does: `bash -c "cd <cwd> && <command>"`, returning
    only once the command's output streams close (as an SSH channel does)."""

    async def run(self, command, *, cwd=None, timeout_s=None):
        if cwd:
            command = f"cd {shlex.quote(cwd)} && {command}"
        process = await asyncio.create_subprocess_exec(
            "bash", "-c", command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_s)
        return CommandResult(process.returncode or 0, stdout.decode(), stderr.decode())


def test_submitting_a_job_does_not_wait_for_it_to_finish(client, tmp_path):
    """The agent's tool call must return as soon as the job is launched, not when it ends."""
    script = tmp_path / "slow_job.sh"
    script.write_text(
        '#!/usr/bin/env bash\nsleep 6\necho \'{"status": "completed"}\' > "$1/status.json"\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    service = SimulationService(
        client.app.state.db,
        RemoteLikeRunner(),
        user_id="someone",
        workdir=str(tmp_path),
        script=str(script),
    )

    started = time.perf_counter()
    simulation = client.portal.call(service.submit, CfdCase(shape="cylinder", reynolds=100))
    elapsed = time.perf_counter() - started

    assert simulation.status == "running"
    assert elapsed < 3, f"submit took {elapsed:.1f}s: it waited for the job"
    # The job really is running on its own, and finishes later.
    deadline = time.time() + 15
    status = tmp_path / "jobs" / simulation.id / "status.json"
    while not status.exists() and time.time() < deadline:
        time.sleep(0.2)
    assert status.exists()


class RecordingRunner:
    """A machine that remembers what it was asked and answers with empty JSON."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.reads: list[str] = []

    async def run(self, command, *, cwd=None, timeout_s=None):
        self.commands.append(command)
        return CommandResult(0, "{}", "")

    async def write_file(self, path, content):
        return None

    async def write_bytes(self, path, content):
        return None

    async def read_file(self, path):
        self.reads.append(path)
        raise FileNotFoundError(path)

    async def read_bytes(self, path):
        self.reads.append(path)
        return b"bytes"


def two_machines(client):
    """A service for a connected machine B that can also find machine A, which a job started on."""
    db = client.app.state.db
    a, b = RecordingRunner(), RecordingRunner()
    station_a = JobStation(a, "/a", "/a/bin/run_cfd2d.sh")

    async def lookup(machine_id):
        return {"mach-a": station_a}.get(machine_id)

    def service(runner, name, found=lookup):
        return SimulationService(
            db,
            runner,
            user_id="u",
            workdir=f"/{name}",
            script=f"/{name}/bin/run_cfd2d.sh",
            workstation_id=f"mach-{name}",
            lookup=found,
        )

    return a, b, service(a, "a"), service(b, "b"), service


def test_a_job_is_remembered_with_the_machine_it_started_on(client):
    a, b, on_a, on_b, _ = two_machines(client)

    job = client.portal.call(on_a.submit, CfdCase(shape="cylinder", reynolds=100))
    other = client.portal.call(on_b.submit, CfdCase(shape="cylinder", reynolds=100))

    assert job.workstation_id == "mach-a" and other.workstation_id == "mach-b"
    assert any(f"/a/jobs/{job.id}" in command for command in a.commands)
    assert not any(job.id in command for command in b.commands)
    assert any(f"/b/jobs/{other.id}" in command for command in b.commands)


def test_a_job_keeps_using_its_machine_after_another_one_is_connected(client):
    a, b, on_a, on_b, _ = two_machines(client)
    job = client.portal.call(partial(on_a.submit, CfdCase(shape="cylinder", reynolds=100)))
    a.commands.clear()

    # B is the connected machine now; the job is still read, monitored and fetched on A.
    client.portal.call(on_b.get, job.id)
    client.portal.call(on_b.info, job.id)
    client.portal.call(partial(on_b.progress, job.id, log_offset=0, coeff_offset=0, final=False))
    client.portal.call(on_b.job_file, job.id, "velocity.png")
    client.portal.call(on_b.list_summaries)

    assert f"/a/jobs/{job.id}/status.json" in a.reads
    assert f"/a/jobs/{job.id}/velocity.png" in a.reads
    assert any("cfd2d_case.py info /a/jobs/" in command for command in a.commands)
    assert any("progress /a/jobs/" in command for command in a.commands)
    assert any("summary /a/jobs " in command for command in a.commands)
    assert b.commands == [] and b.reads == []


def test_deleting_a_chat_removes_its_jobs_from_the_machine_they_ran_on(client):
    a, b, on_a, on_b, _ = two_machines(client)
    job = client.portal.call(
        partial(on_a.submit, CfdCase(shape="cylinder", reynolds=100), thread_id="t1")
    )
    a.commands.clear()

    assert client.portal.call(on_b.delete_for_thread, "t1") == 1

    assert any(f"rm -rf -- /a/jobs/{job.id}" in command for command in a.commands)
    assert b.commands == []
    assert client.portal.call(on_b.get, job.id) is None


def test_a_job_whose_machine_was_removed_can_still_be_listed_but_not_read(client):
    a, b, on_a, _, service = two_machines(client)
    job = client.portal.call(partial(on_a.submit, CfdCase(shape="cylinder", reynolds=100)))

    async def nothing(machine_id):
        return None

    orphaned = service(b, "b", nothing)

    assert client.portal.call(orphaned.get, job.id).status == "running"
    with pytest.raises(MonitorError, match="was removed"):
        client.portal.call(orphaned.info, job.id)
    assert [s.id for s in client.portal.call(orphaned.list_summaries)] == [job.id]


def test_removing_a_machine_ends_the_jobs_still_running_on_it(client):
    a, b, on_a, on_b, _ = two_machines(client)
    gone = client.portal.call(on_a.submit, CfdCase(shape="cylinder", reynolds=100))
    kept = client.portal.call(on_b.submit, CfdCase(shape="cylinder", reynolds=100))

    ended = client.portal.call(client.app.state.services.compute.machine_removed, "u", "mach-a")

    assert ended == 1
    after = client.portal.call(on_b.get, gone.id)
    assert after.status == "failed" and "removed" in after.error
    assert client.portal.call(on_b.get, kept.id).status == "running"
