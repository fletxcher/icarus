import json

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from tests.conftest import use_scripted_agent


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
    assert events[2][1] == {"id": "call-1", "name": "add", "content": "5"}

    messages = client.get(f"/api/threads/{thread_id}").json()["messages"]
    assert [(m["role"], m["content"], m["status"]) for m in messages] == [
        ("user", "What is 2 + 3?", "done"),
        ("assistant", "The sum is 5.", "done"),
    ]
    assert messages[1]["tool_calls"][0]["result"] == "5"


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


def test_send_message_without_mistral_token_is_503(client: TestClient):
    client.app.state.services.agent = None
    thread_id = create_thread(client)
    response = client.post(f"/api/threads/{thread_id}/messages", json={"content": "hi"})
    assert response.status_code == 503
