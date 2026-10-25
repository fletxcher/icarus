from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..mongo import MongoModel
from .xfoil import NacaCode

SimulationStatus = Literal["queued", "running", "completed", "failed"]
TERMINAL_STATUSES: set[SimulationStatus] = {"completed", "failed"}


class CfdCase(BaseModel):
    """Inputs for an external-flow airfoil case. Extend as the work station solver grows."""

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
