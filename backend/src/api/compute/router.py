from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status

from ..dependencies import ServicesDep
from .schemas import TERMINAL_STATUSES, Simulation, SimulationSummary
from .simulations import JOB_FILES, MonitorError

router = APIRouter(prefix="/simulations", tags=["simulations"])


@router.get("")
async def list_simulations(services: ServicesDep) -> list[SimulationSummary]:
    """Running jobs with their progress, then recently finished ones."""
    return await services.simulations.list_summaries()


@router.get("/{job_id}")
async def get_simulation(job_id: str, services: ServicesDep) -> Simulation:
    simulation = await services.simulations.get(job_id)
    if simulation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Simulation not found")
    return simulation


@router.delete("/{job_id}")
async def delete_simulation(job_id: str, services: ServicesDep) -> dict[str, bool]:
    """Stops the job if it is running, then deletes its record and its files."""
    files_removed = await services.simulations.delete(job_id)
    if files_removed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Simulation not found")
    return {"files_removed": files_removed}


@router.get("/{job_id}/info")
async def get_simulation_info(job_id: str, services: ServicesDep) -> dict[str, Any]:
    """Static facts for the monitor: case, solver, mesh quality and the OpenFOAM dictionaries."""
    await get_simulation(job_id, services)
    try:
        return await services.simulations.info(job_id)
    except MonitorError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error


@router.get("/{job_id}/progress")
async def get_simulation_progress(
    job_id: str,
    services: ServicesDep,
    log_offset: Annotated[int, Query(ge=0)] = 0,
    coeff_offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """What the run did since the offsets the previous call returned (0 and 0 for all of it)."""
    simulation = await get_simulation(job_id, services)
    try:
        progress = await services.simulations.progress(
            job_id,
            log_offset=log_offset,
            coeff_offset=coeff_offset,
            final=simulation.status in TERMINAL_STATUSES,
        )
    except MonitorError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error
    return {"status": simulation.status, **progress}


@router.get("/{job_id}/files/{name}")
async def get_simulation_file(job_id: str, name: str, services: ServicesDep) -> Response:
    """A velocity or pressure image, or the flow video, that a finished job rendered."""
    await get_simulation(job_id, services)
    if name not in JOB_FILES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")
    try:
        content = await services.simulations.job_file(job_id, name)
    except FileNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not available") from error
    except Exception as error:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"could not read {name}: {error}"
        ) from error
    return Response(
        content, media_type=JOB_FILES[name], headers={"Cache-Control": "private, max-age=3600"}
    )
