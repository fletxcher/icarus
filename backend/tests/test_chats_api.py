import json

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from openrouter.errors import OpenRouterError

from api.agent.graph import build_agent
from tests.conftest import ScriptedModel, use_agent, use_scripted_agent


def read_events(client: TestClient, thread_id: str, body: dict) -> list[tuple[str, dict]]:
    events = []
    with client.stream("POST", f"/api/threads/{thread_id}/messages", json=body) as response:
        assert response.status_code == 200, response.read()
        event = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                events.append((event, json.loads(line.removeprefix("data: "))))
    return events


def create_thread(client: TestClient, title: str = "Wing study") -> str:
    response = client.post("/api/threads", json={"title": title})
    assert response.status_code == 201
    return response.json()["id"]


def test_thread_crud(client: TestClient):
    thread_id = create_thread(client)

    response = client.patch(f"/api/threads/{thread_id}", json={"title": "Renamed", "pinned": True})
    assert response.json()["title"] == "Renamed"
    assert response.json()["pinned_at"] is not None

    assert [t["id"] for t in client.get("/api/threads").json()] == [thread_id]
    assert client.get(f"/api/threads/{thread_id}").json()["messages"] == []

    assert client.delete(f"/api/threads/{thread_id}").status_code == 204
    assert client.get(f"/api/threads/{thread_id}").status_code == 404


def test_send_message_streams_tool_calls_and_persists_reply(client: TestClient):
    use_scripted_agent(
        client,
        [
            AIMessage("", tool_calls=[{"id": "call-1", "name": "add", "args": {"a": 2, "b": 3}}]),
            AIMessage("The sum is 5."),
        ],
    )
    thread_id = create_thread(client)

    events = read_events(client, thread_id, {"content": "What is 2 + 3?"})

    names = [name for name, _ in events]
    assert names == ["start", "tool_call", "tool_result", "token", "done"]
    assert events[2][1] == {
        "id": "call-1",
        "name": "add",
        "status": "success",
        "content": "5",
        "artifact": None,
    }

    messages = client.get(f"/api/threads/{thread_id}").json()["messages"]
    assert [(m["role"], m["content"], m["status"]) for m in messages] == [
        ("user", "What is 2 + 3?", "done"),
        ("assistant", "The sum is 5.", "done"),
    ]
    assert [part["type"] for part in messages[1]["parts"]] == ["tool", "text"]
    assert messages[1]["parts"][0]["status"] == "success"
    assert messages[1]["parts"][0]["result"] == "5"


def test_regenerate_replays_history_after_truncation(client: TestClient):
    model = use_scripted_agent(client, [AIMessage("First answer."), AIMessage("Second answer.")])
    thread_id = create_thread(client)
    read_events(client, thread_id, {"content": "Hello"})
    reply_id = client.get(f"/api/threads/{thread_id}").json()["messages"][1]["id"]

    assert client.delete(f"/api/threads/{thread_id}/messages/{reply_id}").status_code == 204
    read_events(client, thread_id, {})

    messages = client.get(f"/api/threads/{thread_id}").json()["messages"]
    assert [m["content"] for m in messages] == ["Hello", "Second answer."]
    # The regenerated turn saw only the user message, not the discarded reply.
    assert [type(m) for m in model.seen[1][1:]] == [HumanMessage]


def test_regenerate_requires_a_trailing_user_message(client: TestClient):
    use_scripted_agent(client, [])
    thread_id = create_thread(client)
    response = client.post(f"/api/threads/{thread_id}/messages", json={})
    assert response.status_code == 409


def test_send_message_to_missing_thread_is_404(client: TestClient):
    use_scripted_agent(client, [])
    response = client.post("/api/threads/missing/messages", json={"content": "hi"})
    assert response.status_code == 404


def test_models_endpoint_lists_only_configured_providers(client: TestClient):
    ids = [model["id"] for model in client.get("/api/models").json()]
    assert ids == ["mistral-medium-3-5", "mistral-small-latest"]


def test_send_message_with_unconfigured_provider_model_is_422(client: TestClient):
    use_scripted_agent(client, [])
    thread_id = create_thread(client)
    body = {"content": "hi", "model": "openrouter/free"}
    response = client.post(f"/api/threads/{thread_id}/messages", json=body)
    assert response.status_code == 422


def test_send_message_without_any_provider_is_503(client: TestClient):
    use_agent(client, None)
    thread_id = create_thread(client)
    response = client.post(f"/api/threads/{thread_id}/messages", json={"content": "hi"})
    assert response.status_code == 503


def test_provider_errors_are_explained_to_the_user(client: TestClient):
    class RateLimitedModel(ScriptedModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            request = httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions")
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    model = RateLimitedModel(replies=[], seen=[])
    use_agent(
        client, build_agent({"mistral-medium-3-5": model}, [], default_model="mistral-medium-3-5")
    )
    thread_id = create_thread(client)

    events = read_events(client, thread_id, {"content": "hi"})

    assert events[-1][0] == "error"
    assert "rate limit" in events[-1][1]["message"]
    reply = client.get(f"/api/threads/{thread_id}").json()["messages"][1]
    assert reply["status"] == "error"
    assert reply["error"] == events[-1][1]["message"]


def test_failing_tool_is_reported_to_the_model_and_the_turn_completes(client: TestClient):
    @tool
    async def explode(x: int) -> str:
        """Always fails."""
        raise RuntimeError("solver diverged")

    model = ScriptedModel(
        replies=[
            AIMessage("", tool_calls=[{"id": "call-1", "name": "explode", "args": {"x": 1}}]),
            AIMessage("The solver failed."),
        ],
        seen=[],
    )
    use_agent(
        client,
        build_agent({"mistral-medium-3-5": model}, [explode], default_model="mistral-medium-3-5"),
    )
    thread_id = create_thread(client)

    events = read_events(client, thread_id, {"content": "Run it"})

    assert [name for name, _ in events][-1] == "done"
    result = next(data for name, data in events if name == "tool_result")
    assert result["status"] == "error"
    assert "solver diverged" in result["content"]
    part = client.get(f"/api/threads/{thread_id}").json()["messages"][1]["parts"][0]
    assert part["status"] == "error"


def test_reasoning_blocks_are_streamed_and_stored(client: TestClient):
    thinking = [{"type": "thinking", "thinking": [{"type": "text", "text": "Consider Re."}]}]
    use_scripted_agent(client, [AIMessage([*thinking, {"type": "text", "text": "Use 2412."}])])
    thread_id = create_thread(client)

    events = read_events(client, thread_id, {"content": "Pick an airfoil"})

    assert ("reasoning", {"text": "Consider Re."}) in events
    assert ("token", {"text": "Use 2412."}) in events
    message = client.get(f"/api/threads/{thread_id}").json()["messages"][1]
    assert message["content"] == "Use 2412."
    assert message["parts"] == [
        {"type": "reasoning", "text": "Consider Re."},
        {"type": "text", "text": "Use 2412."},
    ]


def test_openrouter_errors_are_explained_with_the_providers_reason(client: TestClient):
    class DailyQuotaModel(ScriptedModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
            raise OpenRouterError(
                "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock more",
                httpx.Response(429, request=request, text="{}"),
            )

    model = DailyQuotaModel(replies=[], seen=[])
    use_agent(
        client, build_agent({"mistral-medium-3-5": model}, [], default_model="mistral-medium-3-5")
    )
    thread_id = create_thread(client)

    events = read_events(client, thread_id, {"content": "hi"})

    message = events[-1][1]["message"]
    assert events[-1][0] == "error"
    assert message.startswith("OpenRouter's rate limit was exceeded.")
    assert "free-models-per-day" in message
