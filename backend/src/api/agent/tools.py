"""Tools exposed to the agent. Each wraps a service and returns compact text for the LLM."""

import json
from typing import Annotated, Any, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, BeforeValidator, Field

from ..compute.plots import describe_figure, validate_figure
from ..compute.python_exec import PythonError, PythonService, format_result
from ..compute.xfoil import Polar, PolarRequest, XfoilService
from ..design.service import DesignError, DesignService

TOP_CANDIDATES = 5


class NacaSearchRequest(BaseModel):
    reynolds: float = Field(gt=0, description="Chord Reynolds number, e.g. 1e6")
    mach: float = Field(default=0.0, ge=0, lt=1)
    alpha_start: float = Field(default=0.0, ge=-10, le=20)
    alpha_end: float = Field(default=10.0, ge=-10, le=20)
    min_thickness: int = Field(default=10, ge=6, le=24, description="Percent chord")
    max_thickness: int = Field(default=16, ge=6, le=24, description="Percent chord")


class GeometryRequest(BaseModel):
    name: str = Field(description="A short name for the part, e.g. 'mounting bracket'")
    code: str = Field(
        description=(
            "build123d Python code that leaves the finished solid in a variable called `result`"
        )
    )


class GeometryLookup(BaseModel):
    geometry_file_id: str = Field(description="The id of a part you designed in this chat")


class ExportRequest(BaseModel):
    geometry_file_id: str = Field(description="The id create_geometry returned")
    format: Literal["step", "stl", "3mf", "brep", "obj", "glb"] = Field(
        description="The format the user said they want"
    )


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


GEOMETRY_DESCRIPTION = """\
Design a part by writing build123d code, which runs on the compute host.
The code runs as a script: `from build123d import *`, build the solid (builder mode, \
`with BuildPart() as part: ...`, or algebra mode, `Box(...) - Cylinder(...)`), and finish with \
`result = part.part` (or the Part itself). Rules: millimetres; put the dimensions in named \
variables at the top; make one fused solid, not several; no files, network or other imports \
than build123d, math and numpy. A failure returns the error and the line, so fix the code \
and call again. Each call makes a new part with a new id. It appears in the chat as a card the \
user can download in any format, so don't ask which format they want."""

EXPORT_DESCRIPTION = """\
Export a part from create_geometry in a format the user named in their message. step (editable \
CAD, for other CAD programs), stl or 3mf (3D printing), obj or glb (rendering and viewers), brep \
(OpenCascade). Only call it when the user asks for a format; the part's card in the chat already \
offers every format to download, so never ask them which one they want."""


def design_tools(designs: DesignService) -> list[BaseTool]:
    async def create_geometry(config: RunnableConfig, **fields) -> str:
        request = GeometryRequest(**fields)
        thread_id = config.get("configurable", {}).get("thread_id")
        try:
            built = await designs.build(request.name, request.code, thread_id=thread_id)
        except DesignError as problem:
            return f"The part wasn't built: {problem}"
        report = built.report
        size = " x ".join(f"{v:g}" for v in report["size_mm"])
        lines = [
            f"Built {built.file.name}. File id: {built.file.id}",
            f"Size {size} mm, volume {report['volume_mm3']:,.0f} mm3, "
            f"{report['faces']} faces, centre of mass {report['center_of_mass_mm']} mm.",
            f"Bounding box (min, max): {report['bounding_box_mm']} mm.",
            *[f"Warning: {note}" for note in report["notes"]],
            "",
            "The part is shown to the user as a card with download buttons. Describe what you "
            "built and the assumptions you made. The card already offers every export format to "
            "download, so don't ask which format they want or export it unless they name one.",
        ]
        return "\n".join(lines)

    async def get_geometry(**fields) -> str:
        found = await designs.code(GeometryLookup(**fields).geometry_file_id)
        if found is None:
            return "No designed part has that id."
        name, code = found
        return f"{name} was built with this build123d code:\n\n{code}"

    async def export_geometry(**fields) -> str:
        request = ExportRequest(**fields)
        try:
            exported = await designs.export(request.geometry_file_id, request.format)
        except DesignError as problem:
            return f"Not exported: {problem}"
        return (
            f"Exported {exported.name} ({len(exported.data) / 1000:.0f} kB). The user can download "
            "it from the part's card in the chat."
        )

    return [
        StructuredTool.from_function(
            coroutine=create_geometry,
            name="create_geometry",
            description=GEOMETRY_DESCRIPTION,
            args_schema=GeometryRequest,
        ),
        StructuredTool.from_function(
            coroutine=get_geometry,
            name="get_geometry",
            description=(
                "Read the build123d code of a part you designed, to change it: edit the code and "
                "pass it to create_geometry, which makes a new part."
            ),
            args_schema=GeometryLookup,
        ),
        StructuredTool.from_function(
            coroutine=export_geometry,
            name="export_geometry",
            description=EXPORT_DESCRIPTION,
            args_schema=ExportRequest,
        ),
    ]


PYTHON_DESCRIPTION = """\
Write and run Python code, and get what it prints. Use it for calculations, fitting a model to \
data, parameter sweeps and anything else you would do with a script. Available: the standard \
library, numpy, scipy, pandas, scikit-learn and Cantera (chemical kinetics, equilibrium and \
flame temperatures: `import cantera as ct`; mechanisms such as "gri30.yaml" are built in). \
Rules: it runs in a sandbox with no internet and no access to the machine, only to a scratch \
directory that is its working directory; to read a file the user attached, pass its id in \
file_ids and open it by its name. Print the results you want to see: only what the script prints \
comes back (long output is cut). A script is stopped after about a minute, and the memory is \
small, so keep problems modest. Nothing is kept between calls: each call starts fresh, so \
include everything the code needs. The user sees the code you wrote and its output."""


class PythonRequest(BaseModel):
    code: str = Field(description="A complete Python script. Print the results.")
    file_ids: list[str] = Field(
        default_factory=list,
        description="Ids of attached text or CSV files to make readable by name in the script",
    )


def python_tools(python: PythonService) -> list[BaseTool]:
    async def exec_python(**fields) -> str:
        request = PythonRequest(**fields)
        try:
            result, files = await python.run(request.code, request.file_ids)
        except PythonError as problem:
            return f"Not run: {problem}"
        return format_result(result, files)

    return [
        StructuredTool.from_function(
            coroutine=exec_python,
            name="exec_python",
            description=PYTHON_DESCRIPTION,
            args_schema=PythonRequest,
        )
    ]


def build_tools(
    xfoil: XfoilService | None,
    designs: DesignService | None = None,
    python: PythonService | None = None,
) -> list[BaseTool]:
    """The tools that can actually run with the current setup."""
    tools = plot_tools()
    if xfoil is not None:
        tools += xfoil_tools(xfoil)
    if designs is not None:
        tools += design_tools(designs)
    if python is not None:
        tools += python_tools(python)
    return tools
