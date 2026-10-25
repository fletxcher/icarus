"""Tools exposed to the agent. Each wraps a service and returns compact text for the LLM."""

import json
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, BeforeValidator, Field

from ..compute.plots import describe_figure, validate_figure
from ..compute.schemas import CfdCase, Simulation
from ..compute.simulations import SimulationService
from ..compute.xfoil import Polar, PolarRequest, XfoilService

TOP_CANDIDATES = 5


class NacaSearchRequest(BaseModel):
    reynolds: float = Field(gt=0, description="Chord Reynolds number, e.g. 1e6")
    mach: float = Field(default=0.0, ge=0, lt=1)
    alpha_start: float = Field(default=0.0, ge=-10, le=20)
    alpha_end: float = Field(default=10.0, ge=-10, le=20)
    min_thickness: int = Field(default=10, ge=6, le=24, description="Percent chord")
    max_thickness: int = Field(default=16, ge=6, le=24, description="Percent chord")


class SimulationLookup(BaseModel):
    job_id: str = Field(description="Job id returned by submit_cfd_simulation")


def _format_polar(polar: Polar) -> str:
    request = polar.request
    header = (
        f"NACA {request.naca}, Re={request.reynolds:.3g}, M={request.mach:g}, "
        f"Ncrit={request.ncrit:g}"
    )
    if not polar.points:
        return f"{header}: XFOIL did not converge at any angle of attack."

    rows = ["| alpha | CL | CD | CM | L/D |", "|---|---|---|---|---|"]
    rows += [
        f"| {p.alpha:.2f} | {p.cl:.4f} | {p.cd:.5f} | {p.cm:.4f} | {p.lift_to_drag:.1f} |"
        for p in polar.points
    ]
    best, max_lift = polar.best_lift_to_drag, polar.max_lift
    assert best is not None and max_lift is not None
    summary = (
        f"Best L/D {best.lift_to_drag:.1f} at alpha={best.alpha:.2f}; "
        f"max CL {max_lift.cl:.3f} at alpha={max_lift.alpha:.2f}."
    )
    return "\n".join([header, summary, "", *rows])


def _format_simulation(simulation: Simulation) -> str:
    lines = [f"Job {simulation.id}: {simulation.status}", f"Case: {simulation.case.model_dump()}"]
    if simulation.result:
        lines.append(f"Result: {simulation.result}")
    if simulation.error:
        lines.append(f"Error: {simulation.error}")
    return "\n".join(lines)


def xfoil_tools(xfoil: XfoilService) -> list[BaseTool]:
    async def run_xfoil_polar(**fields) -> str:
        polar = await xfoil.run_polar(PolarRequest(**fields))
        return _format_polar(polar)

    async def search_naca4_airfoils(**fields) -> str:
        request = NacaSearchRequest(**fields)
        base = PolarRequest(
            naca="0012",
            reynolds=request.reynolds,
            mach=request.mach,
            alpha_start=request.alpha_start,
            alpha_end=request.alpha_end,
        )
        ranked = await xfoil.search_naca4(
            base,
            cambers=[0, 2, 4, 6],
            camber_positions=[2, 4, 6],
            thicknesses=list(range(request.min_thickness, request.max_thickness + 1, 2)),
        )
        if not ranked:
            return "No candidate converged; try a narrower alpha range or higher Reynolds number."
        lines = [f"Evaluated NACA 4-digit candidates; top {TOP_CANDIDATES} by best L/D:"]
        for polar in ranked[:TOP_CANDIDATES]:
            best = polar.best_lift_to_drag
            assert best is not None
            lines.append(
                f"- NACA {polar.request.naca}: L/D {best.lift_to_drag:.1f} at "
                f"alpha={best.alpha:.2f} (CL {best.cl:.3f}, CD {best.cd:.5f})"
            )
        return "\n".join(lines)

    return [
        StructuredTool.from_function(
            coroutine=run_xfoil_polar,
            name="run_xfoil_polar",
            description=(
                "Run an XFOIL viscous analysis of a NACA airfoil over a range of angles of "
                "attack. Returns CL, CD, CM and L/D per angle. Takes seconds."
            ),
            args_schema=PolarRequest,
        ),
        StructuredTool.from_function(
            coroutine=search_naca4_airfoils,
            name="search_naca4_airfoils",
            description=(
                "Search NACA 4-digit airfoils with XFOIL for the highest lift-to-drag ratio "
                "at the given flow conditions. Runs dozens of analyses; takes up to a minute."
            ),
            args_schema=NacaSearchRequest,
        ),
    ]


def simulation_tools(simulations: SimulationService) -> list[BaseTool]:
    async def submit_cfd_simulation(config: RunnableConfig, **fields) -> str:
        thread_id = config.get("configurable", {}).get("thread_id")
        simulation = await simulations.submit(CfdCase(**fields), thread_id=thread_id)
        return _format_simulation(simulation)

    async def get_simulation_status(job_id: str) -> str:
        simulation = await simulations.get(job_id)
        return _format_simulation(simulation) if simulation else f"No job with id {job_id}."

    return [
        StructuredTool.from_function(
            coroutine=submit_cfd_simulation,
            name="submit_cfd_simulation",
            description=(
                "Start a CFD simulation of a NACA airfoil on the simulation work station. Runs for "
                "minutes to hours; returns a job id to check with get_simulation_status."
            ),
            args_schema=CfdCase,
        ),
        StructuredTool.from_function(
            coroutine=get_simulation_status,
            name="get_simulation_status",
            description="Check the status and results of a CFD simulation job.",
            args_schema=SimulationLookup,
        ),
    ]


class PlotFigure(BaseModel):
    """A Plotly figure; typed as a list of traces so models produce the right shape."""

    data: list[dict[str, Any]] = Field(
        min_length=1,
        description='Plotly traces with their numbers inline, e.g. {"type": "scatter", ...}',
    )
    layout: dict[str, Any] = Field(default_factory=dict, description="Plotly layout")


def _figure_from_model(value: object) -> object:
    """Accepts the shapes models send for a figure and returns {"data": [...], ...}.

    Besides the right shape, models send the figure as a JSON string, as a bare list of
    traces, or as a single trace.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    if isinstance(value, list):
        return {"data": value}
    if isinstance(value, dict) and "data" not in value and "type" in value:
        return {"data": [value]}
    return value


class PlotRequest(BaseModel):
    title: str = Field(description="Short title shown above the plot")
    figure: Annotated[PlotFigure, BeforeValidator(_figure_from_model)]


PLOT_DESCRIPTION = "\n".join(
    [
        "Draw a plot in the chat from a Plotly figure spec (JSON, not code). The same spec "
        "always renders the same plot. Use it to show data visually, e.g. lift/drag polars, "
        "Cp distributions, convergence histories, contour or surface fields.",
        "",
        "How to write the figure:",
        '- data: traces with their numbers inline, e.g. {"type": "scatter", "mode": '
        '"lines+markers", "name": "NACA 2412", "x": [...], "y": [...]}. Other useful types: '
        "bar, contour, heatmap, surface, histogram.",
        '- layout: axis titles with units, e.g. {"xaxis": {"title": {"text": "α (deg)"}}, '
        '"yaxis": {"title": {"text": "C<sub>L</sub>"}}}. Use "yaxis": {"autorange": '
        '"reversed"} for Cp plots.',
        "- Plot text doesn't render LaTeX: write symbols in Unicode (α, ρ, Δ) and "
        "sub/superscripts as HTML, e.g. C<sub>L</sub>, Re = 10<sup>6</sup>.",
        '- Arrays are plain JSON arrays of numbers: "x": [0, 1, 2], "y": [0.1, 0.4, 0.9]. '
        'Not strings ("0"), and not wrapped in objects like {"item": [...]}.',
        "- Give every series a name; keep to at most 8 series per plot.",
        "- Use one y-axis. For two quantities with different units, make two plots.",
        "- Don't set colors, fonts, backgrounds or a template: the app applies its own theme.",
        "- Only plot numbers you have (from tools, files or the conversation); never invent data.",
        "At most 20 traces and 50,000 points. Invalid specs return an error explaining what "
        "to fix.",
    ]
)


def plot_tools() -> list[BaseTool]:
    async def create_plot(
        title: str, figure: PlotFigure | dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        spec = figure.model_dump() if isinstance(figure, PlotFigure) else figure
        validated = validate_figure(spec)
        validated["layout"]["title"] = {"text": title}
        return f"Plotted '{title}': {describe_figure(validated)}.", validated

    return [
        StructuredTool.from_function(
            coroutine=create_plot,
            name="create_plot",
            description=PLOT_DESCRIPTION,
            args_schema=PlotRequest,
            # The model gets the short summary; the figure goes to the UI as the artifact.
            response_format="content_and_artifact",
        )
    ]


def build_tools(
    xfoil: XfoilService | None, simulations: SimulationService | None
) -> list[BaseTool]:
    """The tools that can actually run with the current setup."""
    tools = plot_tools()
    if xfoil is not None:
        tools += xfoil_tools(xfoil)
    if simulations is not None:
        tools += simulation_tools(simulations)
    return tools
