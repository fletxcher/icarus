"""XFOIL batch runs: build the keystroke script, run it, parse the saved polar."""

import asyncio
import itertools
import logging
import re
import shlex
import time
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, Field

from api.compute.runner import CommandResult, CommandRunner

logger = logging.getLogger(__name__)

XfoilDisplay = Literal["xvfb", "none"]

# Virtual display numbers cycle through this range, clear of real displays like :0.
FIRST_DISPLAY = 100
DISPLAY_COUNT = 900
XVFB_FAILED = "Xvfb failed to start"

POLAR_FILE = "polar.txt"
INPUT_FILE = "input.txt"
NACA_PATTERN = re.compile(r"^\d{4,5}$")


def _naca_from_number(value: object) -> object:
    """Models sometimes send the designation as a number; 12 means NACA 0012."""
    return str(value).zfill(4) if isinstance(value, int) else value


def _normalize_naca(value: str) -> str:
    value = value.strip().upper().removeprefix("NACA").strip()
    if not NACA_PATTERN.match(value):
        raise ValueError("expected a 4- or 5-digit NACA designation")
    return value


NacaCode = Annotated[
    str,
    BeforeValidator(_naca_from_number),
    AfterValidator(_normalize_naca),
    Field(description="NACA 4- or 5-digit designation, e.g. '2412' or '23012'"),
]


class PolarRequest(BaseModel):
    naca: NacaCode
    reynolds: float = Field(gt=0, description="Chord Reynolds number, e.g. 1e6")
    mach: float = Field(default=0.0, ge=0, lt=1)
    alpha_start: float = Field(default=-4.0, ge=-30, le=30, description="Degrees")
    alpha_end: float = Field(default=12.0, ge=-30, le=30, description="Degrees")
    alpha_step: float = Field(default=1.0, gt=0, le=5, description="Degrees")
    ncrit: float = Field(default=9.0, gt=0, description="e^N transition criterion")
    max_iter: int = Field(default=100, ge=10, le=500)


@dataclass(frozen=True)
class PolarPoint:
    alpha: float
    cl: float
    cd: float
    cdp: float
    cm: float
    xtr_top: float
    xtr_bottom: float

    @property
    def lift_to_drag(self) -> float:
        return self.cl / self.cd if self.cd > 0 else 0.0


@dataclass(frozen=True)
class Polar:
    request: PolarRequest
    points: list[PolarPoint]

    @property
    def best_lift_to_drag(self) -> PolarPoint | None:
        return max(self.points, key=lambda point: point.lift_to_drag, default=None)

    @property
    def max_lift(self) -> PolarPoint | None:
        return max(self.points, key=lambda point: point.cl, default=None)


def _best_lift_to_drag(polar: Polar) -> float:
    best = polar.best_lift_to_drag
    return best.lift_to_drag if best else 0.0


def build_script(request: PolarRequest, *, disable_graphics: bool) -> str:
    """Keystrokes for one viscous alpha sweep, saving the polar to POLAR_FILE."""
    # Without a display XFOIL must not open its plot window. Debian/Ubuntu's build
    # crashes (SIGFPE) when graphics are turned off, so it runs under xvfb instead.
    graphics_off = ["PLOP", "G F", ""] if disable_graphics else []
    lines = [
        *graphics_off,
        f"NACA {request.naca}",
        "PANE",
        "OPER",
        f"VISC {request.reynolds:g}",
        f"MACH {request.mach:g}",
        "VPAR",
        f"N {request.ncrit:g}",
        "",
        f"ITER {request.max_iter}",
        "PACC",
        POLAR_FILE,
        "",  # no dump file
        f"ASEQ {request.alpha_start:g} {request.alpha_end:g} {request.alpha_step:g}",
        "PACC",
        "",
        "QUIT",
    ]
    return "\n".join(lines) + "\n"


def parse_polar(text: str) -> list[PolarPoint]:
    """Reads the data rows that follow the dashed separator in an XFOIL polar file."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip().startswith("---")) + 1
    except StopIteration:
        return []

    points = []
    for line in lines[start:]:
        fields = line.split()
        if len(fields) < 7:
            continue
        try:
            points.append(PolarPoint(*(float(field) for field in fields[:7])))
        except ValueError:
            continue
    return points


class XfoilService:
    def __init__(
        self,
        runner: CommandRunner,
        *,
        xfoil_path: str,
        display: XfoilDisplay,
        timeout_s: float,
        max_concurrency: int,
    ) -> None:
        self._runner = runner
        self._xfoil_path = xfoil_path
        self._display = display
        self._timeout_s = timeout_s
        self._semaphore = asyncio.Semaphore(max_concurrency)
        # Each run gets the next display number. `xvfb-run -a` races when several start
        # at once, and reusing a number right away fails while the last Xvfb shuts down.
        self._displays = itertools.cycle(range(FIRST_DISPLAY, FIRST_DISPLAY + DISPLAY_COUNT))

    async def _run_xfoil(self, workdir: str) -> CommandResult:
        xfoil = f"{shlex.quote(self._xfoil_path)} < {INPUT_FILE}"
        if self._display == "none":
            return await self._runner.run(xfoil, cwd=workdir, timeout_s=self._timeout_s)

        # Retry once on a fresh number in case another process holds this display.
        for attempt in range(2):
            command = f"xvfb-run --server-num={next(self._displays)} {xfoil}"
            result = await self._runner.run(command, cwd=workdir, timeout_s=self._timeout_s)
            if result.ok or XVFB_FAILED not in result.stderr:
                break
            logger.warning("xvfb failed to start (attempt %d); retrying", attempt + 1)
        return result

    async def run_polar(self, request: PolarRequest) -> Polar:
        started = time.perf_counter()
        script = build_script(request, disable_graphics=self._display == "none")
        async with self._semaphore:
            workdir = await self._runner.make_temp_dir()
            try:
                await self._runner.write_file(f"{workdir}/{INPUT_FILE}", script)
                result = await self._run_xfoil(workdir)
                if not result.ok:
                    raise RuntimeError(f"xfoil exited with {result.exit_code}: {result.stderr}")
                text = await self._runner.read_file(f"{workdir}/{POLAR_FILE}")
            finally:
                await self._runner.remove_dir(workdir)
        polar = Polar(request=request, points=parse_polar(text))
        expected = int((request.alpha_end - request.alpha_start) / request.alpha_step) + 1
        log = logger.info if polar.points else logger.warning
        log(
            "xfoil NACA %s Re=%.3g M=%g: %d/%d angles converged in %.0fms",
            request.naca,
            request.reynolds,
            request.mach,
            len(polar.points),
            expected,
            (time.perf_counter() - started) * 1000,
        )
        return polar

    async def search_naca4(
        self,
        base: PolarRequest,
        *,
        cambers: list[int],
        camber_positions: list[int],
        thicknesses: list[int],
    ) -> list[Polar]:
        """Runs every NACA 4-digit combination and ranks them by best L/D.

        A coarse grid search: a starting point for real shape optimization
        (e.g. CST parameters with a gradient-free optimizer).
        """
        designations = {
            f"{m}{p if m else 0}{t:02d}"
            for m, p, t in itertools.product(cambers, camber_positions, thicknesses)
        }
        requests = [base.model_copy(update={"naca": code}) for code in sorted(designations)]
        logger.info("naca4 search: %d candidates at Re=%.3g", len(requests), base.reynolds)
        results = await asyncio.gather(
            *(self.run_polar(request) for request in requests), return_exceptions=True
        )
        for request, result in zip(requests, results, strict=True):
            if isinstance(result, BaseException):
                logger.error(
                    "naca4 search: NACA %s failed",
                    request.naca,
                    exc_info=(type(result), result, result.__traceback__),
                )
        polars = [result for result in results if isinstance(result, Polar) and result.points]
        logger.info("naca4 search: %d/%d candidates converged", len(polars), len(requests))
        return sorted(polars, key=_best_lift_to_drag, reverse=True)
