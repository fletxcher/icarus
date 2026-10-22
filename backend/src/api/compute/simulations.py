"""CFD jobs on the simulation VM.

CFD runs take minutes to hours, so jobs are fire-and-poll rather than awaited:

1. The backend creates `{VM_WORKDIR}/jobs/{job_id}/case.json` on the VM.
2. It launches `{VM_CFD_SCRIPT} {job_dir}` detached (see `vm/run_case.sh`).
3. That script keeps `{job_dir}/status.json` up to date:
   `{"status": "running" | "completed" | "failed", "result": {...}, "error": "..."}`.
4. Status checks read `status.json` and mirror it into the `simulations` collection.
"""

import json
import logging
import shlex
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from api.chats.schemas import MongoModel
from api.compute.runner import CommandRunner
from api.compute.xfoil import NacaCode
from api.db import SIMULATIONS

logger = logging.getLogger(__name__)

SimulationStatus = Literal["queued", "running", "completed", "failed"]
TERMINAL_STATUSES: set[SimulationStatus] = {"completed", "failed"}


class CfdCase(BaseModel):
    """Inputs for an external-flow airfoil case. Extend as the VM solver grows."""

    naca: NacaCode
    alpha: float = Field(ge=-30, le=30, description="Angle of attack in degrees")
    reynolds: float = Field(gt=0, description="Chord Reynolds number")
    mach: float = Field(default=0.15, ge=0, lt=5)
    mesh: Literal["coarse", "medium", "fine"] = "medium"


class Simulation(MongoModel):
    thread_id: str | None = None
    case: CfdCase
    status: SimulationStatus
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


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
            launch = await self._runner.run(
                f"nohup {shlex.quote(self._script)} {shlex.quote(job_dir)} "
                "> run.log 2>&1 < /dev/null &",
                cwd=job_dir,
            )
            if not launch.ok:
                raise RuntimeError(launch.stderr.strip() or "launch failed")
        except Exception as error:
            logger.exception("cfd job %s failed to launch", doc["_id"])
            return await self._update(doc["_id"], status="failed", error=f"submit failed: {error}")

        logger.info("cfd job %s launched on VM in %s", doc["_id"], job_dir)
        return await self._update(doc["_id"], status="running")

    async def get(self, job_id: str) -> Simulation | None:
        """Returns the job, refreshing it from the VM while it's still in flight."""
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
