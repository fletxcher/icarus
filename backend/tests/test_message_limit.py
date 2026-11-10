"""The daily message limit: five messages (a prompt and its reply) in any 24 hours."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from openrouter.errors import OpenRouterError
from pymongo import MongoClient

from api.agent.graph import build_agent
from api.catalog import MESSAGE_USAGE, USERS
from api.limits.repository import LimitReached
from tests.conftest import MONGODB_URI, ScriptedModel, use_agent, use_scripted_agent
from tests.test_chats_api import create_thread, read_events

LIMIT = 3


@pytest.fixture
def limited(client: TestClient) -> TestClient:
    """A signed-in user, with the limit on and a model that always answers."""
    client.app.state.services.limiter.limit = LIMIT
    use_scripted_agent(client, [AIMessage("ok")] * 20)
    return client


def user_id(client: TestClient) -> str:
    return client.get("/api/auth/me").json()["id"]


def send(client: TestClient, thread_id: str, body: dict | None = None) -> httpx.Response:
    with client.stream(
        "POST",
        f"/api/threads/{thread_id}/messages",
        json={"content": "hi"} if body is None else body,
    ) as response:
        response.read()
        return response


def used(client: TestClient) -> int:
    return client.get("/api/usage/messages").json()["used"]


def test_the_message_after_the_limit_is_refused_and_leaves_no_trace(limited: TestClient):
    thread_id = create_thread(limited)
    for _ in range(LIMIT):
        assert send(limited, thread_id).status_code == 200
    stored = len(limited.get(f"/api/threads/{thread_id}").json()["messages"])

    refused = send(limited, thread_id, {"content": "one more"})

    assert refused.status_code == 429
    assert "messages" in refused.json()["detail"]
    assert 0 < int(refused.headers["retry-after"]) <= 24 * 3600
    assert len(limited.get(f"/api/threads/{thread_id}").json()["messages"]) == stored


def test_the_usage_endpoint_reports_what_is_left(limited: TestClient):
    thread_id = create_thread(limited)
    send(limited, thread_id)

    body = limited.get("/api/usage/messages").json()

    assert (body["limit"], body["used"], body["remaining"]) == (LIMIT, 1, LIMIT - 1)
    assert body["unlimited"] is False
    assert body["own_key_providers"] == []
    resets = datetime.fromisoformat(body["resets_at"])
    assert timedelta(hours=23, minutes=59) < resets - datetime.now(UTC) <= timedelta(hours=24)


def age_claims(client: TestClient, by: timedelta) -> None:
    source = MongoClient(MONGODB_URI)
    document = source[client.app.state.db.name][MESSAGE_USAGE].find_one()
    claims = [{**claim, "at": claim["at"] - by} for claim in document["claims"]]
    source[client.app.state.db.name][MESSAGE_USAGE].update_one(
        {"_id": document["_id"]}, {"$set": {"claims": claims}}
    )
    source.close()


def test_a_message_stops_counting_after_24_hours(limited: TestClient):
    thread_id = create_thread(limited)
    for _ in range(LIMIT):
        send(limited, thread_id)
    assert send(limited, thread_id).status_code == 429

    age_claims(limited, timedelta(hours=24, minutes=1))

    assert limited.get("/api/usage/messages").json()["used"] == 0
    assert send(limited, thread_id).status_code == 200


def test_the_window_rolls_a_message_at_a_time(limited: TestClient):
    thread_id = create_thread(limited)
    send(limited, thread_id)
    age_claims(limited, timedelta(hours=23))
    send(limited, thread_id)
    send(limited, thread_id)
    assert send(limited, thread_id).status_code == 429

    age_claims(limited, timedelta(hours=2))  # only the first is now older than a day

    assert used(limited) == 2
    assert send(limited, thread_id).status_code == 200
    assert send(limited, thread_id).status_code == 429


def test_regenerating_and_editing_count(limited: TestClient):
    thread_id = create_thread(limited)
    send(limited, thread_id)
    messages = limited.get(f"/api/threads/{thread_id}").json()["messages"]

    limited.delete(f"/api/threads/{thread_id}/messages/{messages[1]['id']}")  # the reply
    assert send(limited, thread_id, {}).status_code == 200  # regenerate
    assert used(limited) == 2

    limited.delete(f"/api/threads/{thread_id}/messages/{messages[0]['id']}")  # the prompt
    assert send(limited, thread_id, {"content": "edited"}).status_code == 200  # edit
    assert used(limited) == 3


def test_deleting_chats_does_not_give_messages_back(limited: TestClient):
    thread_id = create_thread(limited)
    for _ in range(LIMIT):
        send(limited, thread_id)

    limited.delete(f"/api/threads/{thread_id}")

    assert used(limited) == LIMIT
    assert send(limited, create_thread(limited)).status_code == 429


def test_a_failed_reply_is_given_back(client: TestClient):
    client.app.state.services.limiter.limit = LIMIT

    class Failing(ScriptedModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            request = httpx.Request("POST", "https://example.test")
            raise OpenRouterError("Rate limit exceeded", httpx.Response(429, request=request))

    use_agent(
        client,
        build_agent(
            {"mistral-medium-3-5": Failing(replies=[], seen=[])},
            [],
            default_model="mistral-medium-3-5",
        ),
    )
    events = read_events(client, create_thread(client), {"content": "hi"})

    assert events[-1][0] == "error"
    assert used(client) == 0


def test_a_request_that_fails_to_start_is_given_back(limited: TestClient):
    thread_id = create_thread(limited)
    send(limited, thread_id)  # the thread now ends with a reply

    nothing_to_regenerate = send(limited, thread_id, {})

    assert nothing_to_regenerate.status_code == 409
    assert used(limited) == 1


def test_a_bad_request_costs_nothing(limited: TestClient):
    thread_id = create_thread(limited)

    assert send(limited, thread_id, {"content": "hi", "model": "no-such-model"}).status_code == 422
    assert send(limited, thread_id, {"content": "hi", "attachments": ["nope"]}).status_code == 422

    assert used(limited) == 0


def test_admins_are_not_counted(limited: TestClient):
    source = MongoClient(MONGODB_URI)
    source[limited.app.state.db.name][USERS].update_one({}, {"$set": {"role": "admin"}})
    source.close()
    thread_id = create_thread(limited)

    for _ in range(LIMIT + 2):
        assert send(limited, thread_id).status_code == 200

    body = limited.get("/api/usage/messages").json()
    assert body["unlimited"] is True and body["used"] == 0


def test_a_model_reached_with_the_users_own_key_is_not_counted(limited: TestClient):
    credentials = limited.app.state.services.credentials
    limited.portal.call(credentials.set_model_key, user_id(limited), "mistral", "sk-mine-1234")
    thread_id = create_thread(limited)

    for _ in range(LIMIT + 2):
        assert send(limited, thread_id).status_code == 200

    body = limited.get("/api/usage/messages").json()
    assert body["used"] == 0
    assert body["own_key_providers"] == ["mistral"]


def test_no_limit_when_it_is_set_to_zero(limited: TestClient):
    limited.app.state.services.limiter.limit = 0
    thread_id = create_thread(limited)

    for _ in range(LIMIT + 2):
        assert send(limited, thread_id).status_code == 200

    assert limited.get("/api/usage/messages").json()["unlimited"] is True


def test_simultaneous_requests_cannot_beat_the_limit(limited: TestClient):
    limiter = limited.app.state.services.limiter
    uid = user_id(limited)

    async def claim_ten() -> list[object]:
        return await asyncio.gather(
            *(limiter.claim(uid) for _ in range(10)), return_exceptions=True
        )

    results = limited.portal.call(claim_ten)

    assert sum(isinstance(r, str) for r in results) == LIMIT
    assert sum(isinstance(r, LimitReached) for r in results) == 10 - LIMIT


def test_other_users_have_their_own_allowance(limited: TestClient):
    thread_id = create_thread(limited)
    for _ in range(LIMIT):
        send(limited, thread_id)
    assert send(limited, thread_id).status_code == 429
    limited.post("/api/auth/logout")

    limited.post(
        "/api/auth/signup",
        json={"name": "Bob", "email": "bob@example.com", "password": "correct horse"},
    )
    limited.post("/api/auth/login", json={"email": "bob@example.com", "password": "correct horse"})
    use_scripted_agent(limited, [AIMessage("ok")])

    assert send(limited, create_thread(limited)).status_code == 200
