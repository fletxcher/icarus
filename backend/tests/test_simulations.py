import asyncio
import shlex
import stat
import time

from api.compute.runner import CommandResult, LocalRunner
from api.compute.schemas import CfdCase
from api.compute.simulations import SimulationService


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
