"""Validates Plotly figure specs written by the model.

Plots are declarative data (Plotly's JSON figure format), never code, so the same spec
always renders the same plot and nothing runs on the server. Plotly's own validator
rejects unknown trace types and properties with messages the model can act on.

Weaker models make two predictable mistakes, which `repair_spec` fixes instead of failing
(each retry costs a request and free models often repeat the mistake):
- wrapping an array as {"item": [...]} (an XML-style habit), and
- writing numbers as strings: ["0", "1", "2"].
"""

import json
import logging
import math
from collections.abc import Iterable
from typing import Any

import plotly.graph_objects as go

logger = logging.getLogger(__name__)

MAX_TRACES = 20
MAX_POINTS = 50_000
MAX_BYTES = 2_000_000
# Styling the app applies itself (palette, fonts, light/dark theme); dropped from specs.
THEMED_LAYOUT_KEYS = ("template", "paper_bgcolor", "plot_bgcolor", "font")


# Trace properties that hold numbers, where numeric strings are converted to numbers.
NUMERIC_TRACE_KEYS = frozenset(
    "x y z r theta a b c u v w lat lon values open high low close base width".split()
)
# Keys models use to wrap a plain list, e.g. {"item": [1, 2]}.
WRAPPER_KEYS = frozenset({"item", "items"})

ARRAY_HINT = (
    'Data arrays must be plain JSON arrays of numbers, e.g. "x": [0, 1, 2] and '
    '"y": [0.1, 0.4, 0.9]: not objects like {"item": [...]}, and not strings like "0".'
)


class PlotError(ValueError):
    """The figure spec is invalid; the message tells the model how to fix it."""


def _unwrap(value: Any) -> Any:
    """Replaces {"item": [...]} wrappers with the list, everywhere in the spec."""
    if isinstance(value, dict):
        if len(value) == 1:
            ((key, inner),) = value.items()
            if key in WRAPPER_KEYS and isinstance(inner, list):
                return _unwrap(inner)
        return {key: _unwrap(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_unwrap(item) for item in value]
    return value


def _to_number(value: Any) -> float | int | None:
    if not isinstance(value, str):
        return None
    try:
        number = float(value.strip())
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return (
        int(number)
        if number.is_integer() and "." not in value and "e" not in value.lower()
        else number
    )


def _numeric_array(values: list[Any]) -> list[Any]:
    """Converts an array of numeric strings to numbers; leaves anything else alone.

    Only whole arrays convert, so mixed arrays and category labels (["A", "B"]) stay as
    written. Nested arrays (2D fields such as contour `z`) convert row by row.
    """
    if values and all(isinstance(row, list) for row in values):
        return [_numeric_array(row) for row in values]
    numbers = [_to_number(value) for value in values]
    return numbers if values and all(number is not None for number in numbers) else values


def repair_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """The spec with the common model mistakes (see module docstring) fixed."""
    repaired = _unwrap(spec)
    for trace in repaired.get("data") or []:
        if not isinstance(trace, dict):
            continue
        for key in NUMERIC_TRACE_KEYS & trace.keys():
            if isinstance(trace[key], list):
                trace[key] = _numeric_array(trace[key])
    if repaired != spec:
        logger.info("repaired plot spec (unwrapped item lists and/or converted numeric strings)")
    return repaired


def _count_points(values: Iterable[Any]) -> int:
    total = 0
    for value in values:
        if isinstance(value, list | tuple):
            total += (
                _count_points(value) if value and isinstance(value[0], list | tuple) else len(value)
            )
    return total


def validate_figure(spec: dict[str, Any]) -> dict[str, Any]:
    """Returns the figure as normalized Plotly JSON, or raises PlotError."""
    spec = repair_spec(spec)
    data = spec.get("data")
    if not isinstance(data, list) or not data:
        raise PlotError("The figure needs a non-empty 'data' list of traces.")
    if len(data) > MAX_TRACES:
        raise PlotError(f"Too many traces ({len(data)}); use at most {MAX_TRACES}.")
    points = sum(
        _count_points(trace.get(axis, []) for axis in ("x", "y", "z"))
        for trace in data
        if isinstance(trace, dict)
    )
    if points > MAX_POINTS:
        raise PlotError(f"Too many data points ({points}); use at most {MAX_POINTS}.")

    try:
        figure = go.Figure(spec)
    except ValueError as error:
        raise PlotError(f"Invalid Plotly figure: {error}\n{ARRAY_HINT}") from error

    normalized = json.loads(figure.to_json())
    layout = normalized.setdefault("layout", {})
    for key in THEMED_LAYOUT_KEYS:
        layout.pop(key, None)
    if len(json.dumps(normalized)) > MAX_BYTES:
        raise PlotError("The figure is too large; reduce the number of points.")
    return normalized


def describe_figure(figure: dict[str, Any]) -> str:
    """A short summary for the model, instead of echoing the data back."""
    traces = figure["data"]
    kinds = sorted({trace.get("type", "scatter") for trace in traces})
    names = [trace["name"] for trace in traces if trace.get("name")]
    summary = f"{len(traces)} trace(s) of type {', '.join(kinds)}"
    if names:
        summary += f": {', '.join(names)}"
    return summary
