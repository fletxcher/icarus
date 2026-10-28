from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel

from ..auth.router import CurrentUser
from ..dependencies import ServicesDep
from .schemas import TERMINAL_STATUSES, Simulation, SimulationSummary, Usage
from .simulations import JOB_FILES, MonitorError, SimulationService

router = APIRouter(prefix="/simulations", tags=["simulations"])


async def user_simulations(services: ServicesDep, user: CurrentUser) -> SimulationService:
    """The signed-in user's simulations, on the work station they use."""
    return await services.compute.simulations_for(user.id)


Simulations = Annotated[SimulationService, Depends(user_simulations)]


@router.get("")
async def list_simulations(simulations: Simulations) -> list[SimulationSummary]:
    """Running jobs with their progress, then recently finished ones."""
    return await simulations.list_summaries()


@router.get("/usage")
async def simulation_usage(simulations: Simulations) -> Usage:
    """How many CFD runs were started this week, this month and in all, and for how long."""
    return await simulations.usage()


@router.get("/{job_id}")
async def get_simulation(job_id: str, simulations: Simulations) -> Simulation:
    simulation = await simulations.get(job_id)
    if simulation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Simulation not found")
    return simulation


class SimulationUpdate(BaseModel):
    hidden: bool


@router.patch("/{job_id}")
async def update_simulation(
    job_id: str, update: SimulationUpdate, simulations: Simulations
) -> Simulation:
    """Takes a job off the simulations list, or puts it back. Nothing is deleted."""
    simulation = await simulations.set_hidden(job_id, update.hidden)
    if simulation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Simulation not found")
    return simulation


@router.get("/{job_id}/info")
async def get_simulation_info(job_id: str, simulations: Simulations) -> dict[str, Any]:
    """Static facts for the monitor: case, solver, mesh quality and the OpenFOAM dictionaries."""
    simulation = await get_simulation(job_id, simulations)
    try:
        return await simulations.info(job_id, kind=simulation.case.kind)
    except MonitorError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error


@router.get("/{job_id}/progress")
async def get_simulation_progress(
    job_id: str,
    simulations: Simulations,
    log_offset: Annotated[int, Query(ge=0)] = 0,
    coeff_offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """What the run did since the offsets the previous call returned (0 and 0 for all of it)."""
    simulation = await get_simulation(job_id, simulations)
    try:
        progress = await simulations.progress(
            job_id,
            log_offset=log_offset,
            coeff_offset=coeff_offset,
            final=simulation.status in TERMINAL_STATUSES,
            kind=simulation.case.kind,
        )
    except MonitorError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error
    return {"status": simulation.status, **progress}


@router.get("/{job_id}/files/{name}")
async def get_simulation_file(job_id: str, name: str, simulations: Simulations) -> Response:
    """A velocity or pressure image, or the flow video, that a finished job rendered."""
    await get_simulation(job_id, simulations)
    if name not in JOB_FILES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")
    try:
        content = await simulations.job_file(job_id, name)
    except FileNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not available") from error
    except Exception as error:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"could not read {name}: {error}"
        ) from error
    return Response(
        content, media_type=JOB_FILES[name], headers={"Cache-Control": "private, max-age=3600"}
    )
