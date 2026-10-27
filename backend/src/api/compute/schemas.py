import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..mongo import MongoModel
from .xfoil import NacaCode

SimulationStatus = Literal["queued", "running", "completed", "failed"]
TERMINAL_STATUSES: set[SimulationStatus] = {"completed", "failed"}


CfdShape = Literal["airfoil", "cylinder", "ellipse", "wedge", "rectangle"]
WallType = Literal["no_slip", "slip", "moving", "rotating"]
CfdRegime = Literal["auto", "steady", "transient"]

# NACA 5-digit cambers the work station can build: non-reflex, second digit 1-5.
SUPPORTED_FIVE_DIGIT = re.compile(r"^\d[1-5]0\d\d$")


class CfdCase(BaseModel):
    """Inputs for a 2D incompressible flow past one body, in a unit freestream.

    The reference length is the chord for an airfoil and the frontal height otherwise (the
    diameter of a cylinder, the base of a wedge, the thickness of an ellipse or rectangle);
    the Reynolds number and the force coefficients are based on it. Extend as the work
    station grows.
    """

    shape: CfdShape = Field(
        default="airfoil",
        description=(
            "Body shape: 'airfoil' (needs naca), 'cylinder', 'ellipse' (needs aspect_ratio), "
            "'wedge' (needs half_angle; the apex faces the flow, flat base behind) or "
            "'rectangle' (needs aspect_ratio)."
        ),
    )
    naca: NacaCode | None = Field(default=None, description="Airfoil only: NACA 4- or 5-digit code")
    aspect_ratio: float | None = Field(
        default=None,
        ge=1,
        le=20,
        description="Ellipse or rectangle: streamwise length divided by frontal height",
    )
    half_angle: float | None = Field(
        default=None, ge=5, le=60, description="Wedge only: half the apex angle, degrees"
    )
    alpha: float = Field(
        default=0.0, ge=-30, le=30, description="Angle of attack / incidence in degrees"
    )
    reynolds: float = Field(gt=0, description="Reynolds number on the reference length")
    mach: float = Field(
        default=0.15, ge=0, lt=5, description="Ignored: the solver is incompressible (low Mach)"
    )
    mesh: Literal["coarse", "medium", "fine"] = "medium"
    wall_type: WallType = Field(
        default="no_slip",
        description=(
            "Condition on the body surface: 'no_slip' (default), 'slip' (frictionless), "
            "'moving' (surface velocity wall_velocity_x/y) or 'rotating' (cylinder spinning "
            "at spin_ratio)."
        ),
    )
    wall_velocity_x: float = Field(
        default=0.0, description="'moving' wall: surface velocity along x, in freestream speeds"
    )
    wall_velocity_y: float = Field(
        default=0.0, description="'moving' wall: surface velocity along y, in freestream speeds"
    )
    spin_ratio: float = Field(
        default=0.0,
        ge=-10,
        le=10,
        description="'rotating' wall: surface over freestream speed (positive: counter-clockwise)",
    )
    roughness_height: float = Field(
        default=0.0,
        ge=0,
        le=0.05,
        description="Sand-grain roughness height as a fraction of reference length; 0 is smooth",
    )
    regime: CfdRegime = Field(
        default="auto",
        description=(
            "'steady' (RANS iterations to a converged state), 'transient' (time-accurate; "
            "finds vortex shedding and the Strouhal number) or 'auto': steady for airfoils, "
            "transient for the other shapes."
        ),
    )

    video: bool = Field(
        default=True,
        description=(
            "Render velocity and pressure images after the solve, and for transient runs a "
            "flow video. Costs a few minutes; the numbers don't depend on it."
        ),
    )

    @model_validator(mode="after")
    def _needs_for_shape(self) -> "CfdCase":
        if self.shape == "airfoil":
            if self.naca is None:
                raise ValueError("an airfoil needs naca")
            if len(self.naca) == 5 and not SUPPORTED_FIVE_DIGIT.match(self.naca):
                raise ValueError(
                    f"NACA {self.naca} isn't supported: 5-digit airfoils must be non-reflex "
                    "(third digit 0, second digit 1-5)"
                )
        elif self.naca is not None:
            raise ValueError(f"naca only applies to an airfoil, not a {self.shape}")
        if self.shape in ("ellipse", "rectangle") and self.aspect_ratio is None:
            raise ValueError(f"a {self.shape} needs aspect_ratio")
        if self.shape == "wedge" and self.half_angle is None:
            raise ValueError("a wedge needs half_angle")
        if self.wall_type == "rotating" and self.shape != "cylinder":
            raise ValueError("a rotating wall is only supported for a cylinder")
        return self


class SimulationSummary(BaseModel):
    """One row of the simulations list: the job, and how far along it is."""

    id: str
    thread_id: str | None
    case: CfdCase
    status: SimulationStatus
    error: str | None
    created_at: datetime
    # Progress of a running job, from the work station; None if it can't be reached.
    stage: str | None = None
    fraction: float | None = None
    eta_s: float | None = None


class Simulation(MongoModel):
    thread_id: str | None = None
    # Taken off the simulations list; the job and its results are kept and still reachable.
    hidden: bool = False
    case: CfdCase
    status: SimulationStatus
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class UsagePeriod(BaseModel):
    """CFD simulations started in a period."""

    runs: int = 0
    completed: int = 0
    failed: int = 0
    running: int = 0
    # Time from starting to being seen finished, summed over finished runs. Approximate: the
    # end is when the app noticed the run was done, which is soon after it is looked at.
    compute_s: float = 0.0
    by_shape: dict[str, int] = Field(default_factory=dict)


class Usage(BaseModel):
    week: UsagePeriod
    month: UsagePeriod
    all_time: UsagePeriod
