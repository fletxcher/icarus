import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, model_validator

from ..mongo import MongoModel
from .xfoil import NacaCode

SimulationStatus = Literal["queued", "running", "completed", "failed"]
TERMINAL_STATUSES: set[SimulationStatus] = {"completed", "failed"}


CfdShape = Literal["airfoil", "cylinder", "ellipse", "wedge", "rectangle"]
WallType = Literal["no_slip", "slip", "moving", "rotating"]
CfdRegime = Literal["auto", "steady", "transient"]

# NACA 5-digit cambers the work station can build: non-reflex, second digit 1-5.
SUPPORTED_FIVE_DIGIT = re.compile(r"^\d[1-5]0\d\d$")


FeaMaterial = Literal["inconel718", "ti6al4v", "aluminium7075", "steel4340"]


class CfdCase(BaseModel):
    """Inputs for a 2D incompressible flow past one body, in a unit freestream.

    The reference length is the chord for an airfoil and the frontal height otherwise (the
    diameter of a cylinder, the base of a wedge, the thickness of an ellipse or rectangle);
    the Reynolds number and the force coefficients are based on it. Extend as the work
    station grows.
    """

    kind: Literal["cfd"] = "cfd"
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


class FeaCase(BaseModel):
    """Inputs for a CalculiX analysis of an uploaded solid: its stress, deflection and, if asked,
    natural frequencies, under spin, gas pressure and heating.

    The solid is meshed with tetrahedra on the work station. It is held fixed (clamped) on its
    lowest or highest face along one axis; stress at that clamp is an artefact, so results are
    also given away from it. Dimensions are in the file's units (`units`).
    """

    kind: Literal["fea"] = "fea"
    geometry_file_id: str = Field(
        description=(
            "Id of an uploaded STL file, as shown in the attachment (id='...'). The file must be "
            "a closed solid."
        )
    )
    geometry_name: str | None = Field(default=None, description="Set automatically")
    units: Literal["mm", "m", "in"] = Field(
        default="mm", description="The units the STL's coordinates are in (an STL doesn't say)"
    )
    material: FeaMaterial = Field(
        default="inconel718",
        description=(
            "'inconel718' (nickel superalloy, for hot parts), 'ti6al4v' (titanium), "
            "'aluminium7075' (7075-T6) or 'steel4340'. Properties vary with temperature."
        ),
    )
    mesh: Literal["coarse", "medium", "fine"] = "medium"
    support_axis: Literal["x", "y", "z"] = Field(
        description="Which axis the held-fixed end lies along (the part is clamped on one face)"
    )
    support_side: Literal["min", "max"] = Field(
        description="Which end along support_axis is held fixed: lowest ('min') or highest ('max')"
    )
    spin_rpm: float = Field(
        default=0.0,
        ge=0,
        le=200_000,
        description="Rotation speed, which loads the part centrifugally",
    )
    spin_axis_origin: list[float] = Field(
        default_factory=lambda: [0.0, 0.0, 0.0],
        min_length=3,
        max_length=3,
        description="A point on the rotation axis, in the file's units",
    )
    spin_axis_direction: list[float] = Field(
        default_factory=lambda: [0.0, 0.0, 1.0],
        min_length=3,
        max_length=3,
        description="Direction of the rotation axis",
    )
    pressure_mpa: float = Field(
        default=0.0,
        ge=-100,
        le=100,
        description="Uniform pressure on the whole outer surface, in MPa (positive pushes in)",
    )
    gas_temperature_c: float | None = Field(
        default=None,
        ge=-50,
        le=1500,
        description=(
            "Gas temperature in degC that heats the whole outer surface; leave out for no "
            "thermal analysis. Turns the run into a coupled temperature and stress analysis."
        ),
    )
    htc_w_m2k: float = Field(
        default=1000.0,
        gt=0,
        le=50_000,
        description="Gas-side heat transfer coefficient in W/m2K (with gas_temperature_c)",
    )
    base_temperature_c: float = Field(
        default=20.0,
        ge=-50,
        le=1500,
        description="Temperature of the held-fixed face (a cooled root, say), and the start",
    )
    modal: bool = Field(default=False, description="Also compute the natural frequencies")

    @model_validator(mode="after")
    def _check(self) -> "FeaCase":
        if self.spin_rpm and not any(self.spin_axis_direction):
            raise ValueError("spin_axis_direction can't be all zeros")
        return self


def _default_kind(value: Any) -> Any:
    """Jobs stored before FEA existed have no `kind`: they are all CFD."""
    if isinstance(value, dict) and "kind" not in value:
        return {**value, "kind": "cfd"}
    return value


# A job's inputs: CFD or FEA, told apart by `kind`.
JobCase = Annotated[CfdCase | FeaCase, Field(discriminator="kind"), BeforeValidator(_default_kind)]


class SimulationSummary(BaseModel):
    """One row of the simulations list: the job, and how far along it is."""

    id: str
    thread_id: str | None
    case: JobCase
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
    case: JobCase
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
