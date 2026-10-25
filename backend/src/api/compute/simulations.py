"""CFD jobs on the simulation work station.

CFD runs take minutes to hours, so jobs are fire-and-poll rather than awaited:

1. The backend creates `{WS_WORKDIR}/jobs/{job_id}/case.json` on the work station.
2. It launches `{WS_CFD_SCRIPT} {job_dir}` detached (see `ws/run_case.sh`).
3. That script keeps `{job_dir}/status.json` up to date:
   `{"status": "running" | "completed" | "failed", "result": {...}, "error": "..."}`.
4. Status checks read `status.json` and mirror it into the `simulations` collection.
"""

import json
import logging
import posixpath
import re
import shlex
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from ..catalog import SIMULATIONS
from .runner import CommandRunner
from .schemas import TERMINAL_STATUSES, CfdCase, Simulation, SimulationSummary

logger = logging.getLogger(__name__)

LAUNCH_TIMEOUT_S = 30.0
MONITOR_TIMEOUT_S = 20.0
MAX_LISTED = 50  # running jobs in the list
RECENT_FINISHED = 10  # finished jobs kept in the list
# Files a finished job can serve (made by ws/render_video.py), with their media types.
JOB_FILES = {
    "flow.mp4": "video/mp4",
    "velocity.png": "image/png",
    "pressure.png": "image/png",
}


# Job ids become part of a path that is removed recursively, so they must be plain tokens.
SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class MonitorError(Exception):
    """The work station couldn't report on a job (unreachable, or the job's files are gone)."""


class SimulationService:
    def __init__(self, db: AsyncDatabase, runner: CommandRunner, *, workdir: str, script: str):
        self._collection = db[SIMULATIONS]
        self._runner = runner
        self._workdir = workdir
        self._script = script

    def _job_dir(self, job_id: str) -> str:
        return f"{self._workdir}/jobs/{job_id}"

    async def submit(self, case: CfdCase, *, thread_id: str | None = None) -> Simulation:
        now = datetime.now(UTC)
        doc = {
            "_id": str(uuid4()),
            "thread_id": thread_id,
            "case": case.model_dump(),
            "status": "queued",
            "created_at": now,
            "updated_at": now,
        }
        await self._collection.insert_one(doc)
        logger.info("cfd job %s queued: %s", doc["_id"], case.model_dump())

        job_dir = self._job_dir(doc["_id"])
        try:
            await self._runner.run(f"mkdir -p {shlex.quote(job_dir)}")
            await self._runner.write_file(f"{job_dir}/case.json", case.model_dump_json(indent=2))
            # The job must be detached from this command: the command returns when its output
            # streams close, so anything still holding them (a backgrounded `cd && ...` list,
            # say) would keep the submission waiting until the whole simulation is done.
            launch = await self._runner.run(
                f"cd {shlex.quote(job_dir)} && {{ setsid nohup {shlex.quote(self._script)} "
                f"{shlex.quote(job_dir)} > run.log 2>&1 < /dev/null & }}",
                timeout_s=LAUNCH_TIMEOUT_S,
            )
            if not launch.ok:
                raise RuntimeError(launch.stderr.strip() or "launch failed")
        except Exception as error:
            logger.exception("cfd job %s failed to launch", doc["_id"])
            return await self._update(doc["_id"], status="failed", error=f"submit failed: {error}")

        logger.info("cfd job %s launched on work station in %s", doc["_id"], job_dir)
        return await self._update(doc["_id"], status="running")

    async def _monitor(self, *args: str) -> dict[str, Any]:
        """Runs ws/cfd_case.py (installed next to the run script) and returns its JSON."""
        tool = posixpath.join(posixpath.dirname(self._script), "cfd_case.py")
        command = " ".join(["python3", *map(shlex.quote, (tool, *args))])
        try:
            result = await self._runner.run(command, timeout_s=MONITOR_TIMEOUT_S)
        except Exception as error:
            raise MonitorError(f"could not reach the work station: {error}") from error
        if not result.ok:
            raise MonitorError(
                result.stderr.strip().splitlines()[-1] if result.stderr else "failed"
            )
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise MonitorError(f"unreadable monitor output: {error}") from error

    async def info(self, job_id: str) -> dict[str, Any]:
        """Static facts about the job: case, solver, mesh quality, dictionaries."""
        return await self._monitor("info", self._job_dir(job_id))

    async def progress(
        self, job_id: str, *, log_offset: int, coeff_offset: int, final: bool
    ) -> dict[str, Any]:
        """What the run did since the given offsets (residuals, coefficients, stage, ETA)."""
        args = ["progress", self._job_dir(job_id), str(log_offset), str(coeff_offset)]
        return await self._monitor(*args, *(["final"] if final else []))

    async def list_summaries(self, finished: int = RECENT_FINISHED) -> list[SimulationSummary]:
        """Running jobs with their progress, then the most recently finished ones."""
        active_docs = (
            await self._collection.find(
                {"status": {"$in": ["queued", "running"]}, "hidden": {"$ne": True}}
            )
            .sort("created_at", -1)
            .to_list(MAX_LISTED)
        )
        progress: dict[str, dict[str, Any]] = {}
        if active_docs:
            try:
                progress = await self._monitor(
                    "summary", f"{self._workdir}/jobs", *(doc["_id"] for doc in active_docs)
                )
            except MonitorError as error:
                logger.warning("job list: no progress from the work station (%s)", error)
        for doc in active_docs:
            # A job that ended since the last look: record its result before listing it.
            if progress.get(doc["_id"], {}).get("status") in TERMINAL_STATUSES:
                await self.get(doc["_id"])
                doc.update(await self._collection.find_one({"_id": doc["_id"]}) or {})
        finished_docs = (
            await self._collection.find(
                {"status": {"$in": sorted(TERMINAL_STATUSES)}, "hidden": {"$ne": True}}
            )
            .sort("created_at", -1)
            .to_list(finished)
        )

        summaries = []
        for doc in [*active_docs, *finished_docs]:
            job = progress.get(doc["_id"], {})
            summaries.append(
                SimulationSummary(
                    id=doc["_id"],
                    thread_id=doc.get("thread_id"),
                    case=doc["case"],
                    status=doc["status"],
                    error=doc.get("error"),
                    created_at=doc["created_at"],
                    stage=job.get("stage"),
                    fraction=job.get("fraction")
                    if doc["status"] not in TERMINAL_STATUSES
                    else None,
                    eta_s=job.get("eta_s"),
                )
            )
        return sorted(
            summaries, key=lambda s: (s.status in TERMINAL_STATUSES, -s.created_at.timestamp())
        )

    async def delete_for_thread(self, thread_id: str) -> int:
        """Deletes a chat's simulations for real: each running job is stopped, its files on the
        work station are removed and its record is deleted. This is the only way a simulation
        is ever deleted; the work station being unreachable doesn't stop the records going.

        Returns how many simulations were deleted.
        """
        ids = [doc["_id"] async for doc in self._collection.find({"thread_id": thread_id})]
        for job_id in ids:
            await self._remove_files(job_id)
        if ids:
            await self._collection.delete_many({"_id": {"$in": ids}})
            logger.info("deleted %d cfd job(s) of thread %s", len(ids), thread_id)
        return len(ids)

    async def _remove_files(self, job_id: str) -> None:
        """Stops the job if it is running and removes its directory on the work station."""
        if not SAFE_JOB_ID.match(job_id):
            logger.warning("not removing the files of cfd job %r: unusual id", job_id)
            return
        # run_case.sh leads its own process group (setsid at launch), so one signal to the
        # group stops the solver and everything else the job started. The [r] keeps pgrep
        # from matching this very command.
        command = (
            f"pid=$(pgrep -f '[r]un_case.sh .*{job_id}' | head -n 1); "
            'if [ -n "$pid" ]; then kill -TERM -- "-$pid"; sleep 2; fi; '
            f"rm -rf -- {shlex.quote(self._job_dir(job_id))}"
        )
        try:
            await self._runner.run(command, timeout_s=MONITOR_TIMEOUT_S)
        except Exception:
            logger.exception("could not remove the files of cfd job %s", job_id)

    async def set_hidden(self, job_id: str, hidden: bool) -> Simulation | None:
        """Takes a job off the simulations list (or puts it back). Nothing is deleted: the job
        keeps running or keeps its results, and its page and files stay available."""
        doc = await self._collection.find_one_and_update(
            {"_id": job_id},
            {"$set": {"hidden": hidden}},
            return_document=ReturnDocument.AFTER,
        )
        return Simulation.model_validate(doc) if doc else None

    async def job_file(self, job_id: str, name: str) -> bytes:
        """A file the job produced (see JOB_FILES); FileNotFoundError if it isn't there."""
        if name not in JOB_FILES:
            raise FileNotFoundError(name)
        return await self._runner.read_bytes(f"{self._job_dir(job_id)}/{name}")

    async def get(self, job_id: str) -> Simulation | None:
        """Returns the job, refreshing it from the work station while it's still in flight."""
        doc = await self._collection.find_one({"_id": job_id})
        if doc is None:
            return None
        simulation = Simulation.model_validate(doc)
        if simulation.status in TERMINAL_STATUSES:
            return simulation

        try:
            raw = await self._runner.read_file(f"{self._job_dir(job_id)}/status.json")
            status = json.loads(raw)
        except (OSError, ValueError) as error:
            # Not written yet, or caught mid-write.
            logger.debug("cfd job %s: status.json not readable yet (%s)", job_id, error)
            return simulation

        if status.get("status") != simulation.status:
            logger.info(
                "cfd job %s: %s -> %s%s",
                job_id,
                simulation.status,
                status.get("status"),
                f" (error: {status['error']})" if status.get("error") else "",
            )
        return await self._update(
            job_id,
            status=status.get("status", simulation.status),
            result=status.get("result"),
            error=status.get("error"),
        )

    async def _update(self, job_id: str, **changes: Any) -> Simulation:
        changes["updated_at"] = datetime.now(UTC)
        doc = await self._collection.find_one_and_update(
            {"_id": job_id}, {"$set": changes}, return_document=ReturnDocument.AFTER
        )
        return Simulation.model_validate(doc)
