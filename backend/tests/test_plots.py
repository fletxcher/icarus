import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from api.agent.graph import build_agent
from api.agent.tools import PlotRequest, plot_tools
from api.compute.plots import PlotError, repair_spec, validate_figure
from tests.conftest import ScriptedModel
from tests.test_chats_api import create_thread, read_events

POLAR = {
    "data": [
        {
            "type": "scatter",
            "mode": "lines+markers",
            "name": "NACA 2412",
            "x": [-2, 0, 4],
            "y": [0.04, 0.25, 0.69],
        }
    ],
    "layout": {
        "xaxis": {"title": {"text": "α (deg)"}},
        "template": "plotly_dark",
        "paper_bgcolor": "black",
    },
}


def test_valid_figure_is_normalized_and_left_for_the_app_to_style():
    figure = validate_figure(POLAR)

    assert figure["data"][0]["y"] == [0.04, 0.25, 0.69]
    assert figure["layout"]["xaxis"]["title"]["text"] == "α (deg)"
    # Theme-related settings are dropped so the app's light/dark palette applies.
    assert "template" not in figure["layout"]
    assert "paper_bgcolor" not in figure["layout"]


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"data": []}, "non-empty 'data'"),
        ({"data": [{"type": "scater", "x": [1]}]}, "Invalid Plotly figure"),
        ({"data": [{"type": "scatter", "x": [1], "linecolor": "red"}]}, "linecolor"),
        ({"data": [{"type": "scatter", "x": list(range(60_000))}]}, "Too many data points"),
    ],
)
def test_invalid_figures_explain_what_to_fix(spec, message):
    with pytest.raises(PlotError, match=message):
        validate_figure(spec)


def test_create_plot_sends_the_figure_to_the_ui_but_only_a_summary_to_the_model(
    client: TestClient,
):
    call = {"id": "p1", "name": "create_plot", "args": {"title": "Lift curve", "figure": POLAR}}
    model = ScriptedModel(replies=[AIMessage("", tool_calls=[call]), AIMessage("Done.")], seen=[])
    client.app.state.services.agent = build_agent(
        {"mistral-medium-3-5": model}, plot_tools(), default_model="mistral-medium-3-5"
    )
    thread_id = create_thread(client)

    events = read_events(client, thread_id, {"content": "Plot it"})

    result = next(data for name, data in events if name == "tool_result")
    assert result["status"] == "success"
    assert result["content"] == "Plotted 'Lift curve': 1 trace(s) of type scatter: NACA 2412."
    assert result["artifact"]["layout"]["title"]["text"] == "Lift curve"
    assert result["artifact"]["data"][0]["x"] == [-2, 0, 4]
    # The model sees the summary, not the figure's numbers.
    assert model.seen[1][-1].content == result["content"]
    part = client.get(f"/api/threads/{thread_id}").json()["messages"][1]["parts"][0]
    assert part["artifact"] == result["artifact"]


def test_figure_sent_as_a_json_string_is_accepted():
    request = PlotRequest(title="Lift", figure=json.dumps(POLAR))
    assert request.figure.data[0]["name"] == "NACA 2412"


# The figure a free model really sent: arrays wrapped as {"item": [...]} with string numbers.
WRAPPED = {
    "title": "Efficiency: L/D Ratio vs Angle of Attack",
    "figure": {
        "data": [
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "NACA 2412 (max 104.4 @ 5°)",
                "x": {"item": ["0", "1", "2", "3"]},
                "y": {"item": ["42", "62.3", "77.8", "93.3"]},
            }
        ],
        "layout": {"xaxis": {"title": {"text": "α (deg)"}}},
    },
}


def test_item_wrapped_string_arrays_from_a_real_model_are_repaired():
    request = PlotRequest(**WRAPPED)
    figure = validate_figure(request.figure.model_dump())

    assert figure["data"][0]["x"] == [0, 1, 2, 3]
    assert figure["data"][0]["y"] == [42, 62.3, 77.8, 93.3]
    assert figure["layout"]["xaxis"]["title"]["text"] == "α (deg)"


def test_repair_converts_whole_numeric_arrays_only():
    spec = {
        "data": [
            {"type": "bar", "x": ["A", "B"], "y": ["1", "2.5"]},
            {"type": "scatter", "x": ["1", "two"], "y": [1, "2"]},
            {"type": "contour", "z": [["1", "2"], ["3", "4"]], "x": ["0", "1"], "y": ["0", "1"]},
        ]
    }

    bar, mixed, contour = repair_spec(spec)["data"]

    assert bar["x"] == ["A", "B"]  # category labels stay as written
    assert bar["y"] == [1, 2.5]
    assert mixed["x"] == ["1", "two"]  # mixed arrays are left for Plotly to judge
    assert mixed["y"] == [1, "2"]  # ...including ones that are already partly numbers
    assert contour["z"] == [[1, 2], [3, 4]]


def test_repair_leaves_strings_outside_numeric_data_alone():
    spec = {
        "data": [{"type": "scatter", "x": [1, 2], "y": [1, 2], "name": "12", "text": ["1", "2"]}],
        "layout": {"xaxis": {"tickvals": [1, 2], "ticktext": ["1", "2"], "title": {"text": "7"}}},
    }

    repaired = repair_spec(spec)

    assert repaired["data"][0]["name"] == "12"
    assert repaired["data"][0]["text"] == ["1", "2"]
    assert repaired["layout"]["xaxis"]["ticktext"] == ["1", "2"]


def test_wrapper_lookalikes_are_not_unwrapped():
    spec = {"data": [{"type": "scatter", "x": [1], "y": [1], "meta": {"item": "not a list"}}]}
    assert repair_spec(spec)["data"][0]["meta"] == {"item": "not a list"}


def test_unrepairable_arrays_get_an_error_that_shows_the_right_shape():
    spec = {"data": [{"type": "scatter", "x": {"values": [1, 2]}, "y": [1, 2]}]}

    with pytest.raises(PlotError, match=r"plain JSON arrays of numbers"):
        validate_figure(spec)


@pytest.mark.parametrize(
    "figure",
    [
        # What models send instead of {"data": [...]}:
        [POLAR["data"][0]],
        POLAR["data"][0],
        json.dumps([POLAR["data"][0]]),
    ],
    ids=["bare list of traces", "single trace", "bare list as a string"],
)
def test_other_figure_shapes_models_send_are_accepted(figure):
    request = PlotRequest(title="Lift", figure=figure)
    assert request.figure.data[0]["name"] == "NACA 2412"


@pytest.mark.parametrize(
    "figure",
    [
        WRAPPED["figure"],  # {"item": [...]} arrays with string numbers
        WRAPPED["figure"]["data"],  # a bare list of traces
        json.dumps(WRAPPED["figure"]),  # the figure as a JSON string
    ],
    ids=["item-wrapped strings", "bare list of traces", "json string"],
)
def test_malformed_figures_models_really_sent_still_plot_through_the_agent(
    client: TestClient, figure
):
    call = {"id": "p1", "name": "create_plot", "args": {"title": "L/D", "figure": figure}}
    model = ScriptedModel(replies=[AIMessage("", tool_calls=[call]), AIMessage("Done.")], seen=[])
    client.app.state.services.agent = build_agent(
        {"mistral-medium-3-5": model}, plot_tools(), default_model="mistral-medium-3-5"
    )
    thread_id = create_thread(client)

    events = read_events(client, thread_id, {"content": "Plot it"})

    result = next(data for name, data in events if name == "tool_result")
    assert result["status"] == "success", result["content"]
    trace = result["artifact"]["data"][0]
    assert trace["x"] == [0, 1, 2, 3]
    assert trace["y"] == [42, 62.3, 77.8, 93.3]
