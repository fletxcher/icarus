import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from api.agent.graph import build_agent
from api.agent.prompts import build_system_prompt
from api.agent.tools import plot_tools
from api.config import Settings
from api.services import CFD_CAPABILITY, XFOIL_CAPABILITY, unavailable_capabilities
from tests.conftest import ScriptedModel
from tests.test_chats_api import create_thread, read_events


def settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_capabilities_follow_the_setup(monkeypatch: pytest.MonkeyPatch):
    installed = {"xfoil", "xvfb-run"}
    monkeypatch.setattr("shutil.which", lambda name: name if name in installed else None)

    # Local XFOIL with xvfb, no work station: only CFD is missing.
    assert unavailable_capabilities(settings(xfoil_runner="local")) == {
        CFD_CAPABILITY: "the simulation work station isn't configured"
    }
    # A work station makes both available.
    assert unavailable_capabilities(settings(xfoil_runner="ws", ws_host="10.0.0.5")) == {}
    # XFOIL on the work station without a work station: neither.
    assert set(unavailable_capabilities(settings(xfoil_runner="ws"))) == {
        XFOIL_CAPABILITY,
        CFD_CAPABILITY,
    }

    installed.discard("xvfb-run")
    reason = unavailable_capabilities(settings(xfoil_runner="local"))[XFOIL_CAPABILITY]
    assert "xvfb" in reason
    installed.discard("xfoil")
    reason = unavailable_capabilities(settings(xfoil_runner="local"))[XFOIL_CAPABILITY]
    assert reason == "XFOIL isn't installed on the server"


def test_prompt_lists_available_tools_and_missing_capabilities():
    prompt = build_system_prompt(
        plot_tools(), {CFD_CAPABILITY: "the work station isn't configured"}
    )

    assert "- create_plot: Draw a plot in the chat" in prompt
    assert "Not available right now" in prompt
    assert f"- {CFD_CAPABILITY}: the work station isn't configured" in prompt
    assert "Never invent tool names" in prompt


def test_prompt_without_missing_capabilities_has_no_unavailable_section():
    assert "Not available" not in build_system_prompt(plot_tools(), {})


def test_unconfigured_tools_are_not_given_to_the_model(client: TestClient):
    # The test client has no work station and no xfoil binary configured for the app.
    services = client.app.state.services
    graph = services.agent.get_graph()
    tool_names = set(graph.nodes["tools"].data.tools_by_name)
    assert "create_plot" in tool_names
    assert "submit_cfd_simulation" not in tool_names


def test_calling_a_tool_that_does_not_exist_reports_an_error_to_the_model(client: TestClient):
    call = {"id": "w1", "name": "web_search", "args": {"query": "NACA 2412"}}
    model = ScriptedModel(
        replies=[
            AIMessage("", tool_calls=[call]),
            AIMessage("I can't search the web, but I can explain NACA 2412."),
        ],
        seen=[],
    )
    client.app.state.services.agent = build_agent(
        {"mistral-medium-3-5": model}, plot_tools(), default_model="mistral-medium-3-5"
    )
    thread_id = create_thread(client)

    events = read_events(client, thread_id, {"content": "Search the web for NACA 2412"})

    result = next(data for name, data in events if name == "tool_result")
    assert result["status"] == "error"
    assert "not a valid tool" in result["content"]
    assert events[-1][0] == "done"
