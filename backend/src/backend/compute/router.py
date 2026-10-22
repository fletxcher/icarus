from fastapi import APIRouter, HTTPException, status

from backend.compute.simulations import Simulation
from backend.dependencies import ServicesDep

router = APIRouter(prefix="/simulations", tags=["simulations"])


@router.get("/{job_id}")
async def get_simulation(job_id: str, services: ServicesDep) -> Simulation:
    simulation = await services.simulations.get(job_id)
    if simulation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Simulation not found")
    return simulation
