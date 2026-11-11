"""`GET /api/status`: whether each part the app depends on is working, for the help dialog."""

import asyncio
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .agent.capabilities import xfoil_unavailable
from .auth.router import CurrentUser
from .compute.station import Workstation
from .config import get_settings
from .dependencies import ServicesDep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["meta"])

WORK_STATION_TIMEOUT_S = 5.0


class Check(BaseModel):
    id: str
    label: str
    ok: bool
    detail: str


async def _database(request: Request) -> Check:
    try:
        await request.app.state.db.command("ping")
    except Exception as error:
        logger.warning("status: database check failed: %s", error)
        return Check(id="database", label="Database", ok=False, detail="Can't reach MongoDB")
    return Check(id="database", label="Database", ok=True, detail="Connected")


async def _work_station(station: Workstation) -> Check:
    label = "Compute host"
    if not station.available:
        return Check(
            id="work_station",
            label=label,
            ok=False,
            detail=station.reason or "Not set up on the server.",
        )
    try:
        result = await asyncio.wait_for(
            station.runner.run("true", timeout_s=WORK_STATION_TIMEOUT_S),
            timeout=WORK_STATION_TIMEOUT_S + 2,
        )
    except Exception as error:
        logger.warning("status: compute host check failed: %s", error)
        return Check(id="work_station", label=label, ok=False, detail="Can't reach it over SSH")
    if not result.ok:
        return Check(
            id="work_station", label=label, ok=False, detail="Reachable, but a command failed"
        )
    return Check(id="work_station", label=label, ok=True, detail="Reachable over SSH")


@router.get("/status")
async def status(request: Request, services: ServicesDep, user: CurrentUser) -> list[Check]:
    """Checks the database, the user's models, XFOIL and the compute host."""
    models, _ = await services.agents.models_for(user.id)
    station = services.compute.station
    xfoil_problem = xfoil_unavailable(get_settings(), workstation=station.available)
    return [
        await _database(request),
        Check(
            id="models",
            label="Language models",
            ok=bool(models),
            detail=", ".join(option.label for option in models)
            or "No API key. Add one in Settings, under API keys.",
        ),
        Check(
            id="xfoil",
            label="XFOIL",
            ok=xfoil_problem is None,
            detail=xfoil_problem or "Available",
        ),
        await _work_station(station),
    ]
