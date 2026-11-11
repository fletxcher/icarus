"""Chats and files belong to the user who made them."""

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from tests.conftest import sign_up_and_in, use_scripted_agent
from tests.test_chats_api import create_thread, read_events
from tests.test_files import upload

SIGNED_IN_ONLY = [
    ("get", "/api/threads"),
    ("post", "/api/threads"),
    ("get", "/api/threads/x"),
    ("patch", "/api/threads/x"),
    ("delete", "/api/threads/x"),
    ("post", "/api/threads/x/messages"),
    ("delete", "/api/threads/x/messages/y"),
    ("post", "/api/files"),
    ("delete", "/api/files/x"),
    ("get", "/api/models"),
    ("get", "/api/status"),
    ("get", "/api/credentials"),
]


@pytest.mark.parametrize(("method", "path"), SIGNED_IN_ONLY)
def test_nothing_is_available_without_signing_in(anon_client: TestClient, method, path):
    response = getattr(anon_client, method)(path)

    assert response.status_code == 401


@pytest.fixture
def two_users(anon_client: TestClient):
    """The same app twice over: ada's session and grace's, one cookie jar each."""
    ada = anon_client
    sign_up_and_in(ada, email="ada@example.com")
    cookies = dict(ada.cookies)
    ada.cookies.clear()
    sign_up_and_in(ada, email="grace@example.com")
    grace_cookies = dict(ada.cookies)

    def as_user(name: str) -> TestClient:
        ada.cookies.clear()
        for key, value in (cookies if name == "ada" else grace_cookies).items():
            ada.cookies.set(key, value)
        return ada

    return as_user


def test_a_user_only_sees_their_own_chats(two_users):
    ada = two_users("ada")
    thread_id = create_thread(ada, "Ada's wing")

    grace = two_users("grace")

    assert grace.get("/api/threads").json() == []
    assert grace.get(f"/api/threads/{thread_id}").status_code == 404
    assert two_users("ada").get("/api/threads").json()[0]["title"] == "Ada's wing"


def test_a_user_cant_change_or_delete_or_continue_anothers_chat(two_users):
    ada = two_users("ada")
    use_scripted_agent(ada, [AIMessage("Hello")])
    thread_id = create_thread(ada, "Private")
    read_events(ada, thread_id, {"content": "Hi"})
    message_id = ada.get(f"/api/threads/{thread_id}").json()["messages"][0]["id"]

    grace = two_users("grace")

    assert grace.patch(f"/api/threads/{thread_id}", json={"title": "Mine now"}).status_code == 404
    assert grace.delete(f"/api/threads/{thread_id}/messages/{message_id}").status_code == 404
    assert (
        grace.post(f"/api/threads/{thread_id}/messages", json={"content": "x"}).status_code == 404
    )
    assert grace.delete(f"/api/threads/{thread_id}").status_code == 404
    thread = two_users("ada").get(f"/api/threads/{thread_id}").json()
    assert thread["title"] == "Private" and len(thread["messages"]) == 2


def test_a_user_cant_attach_or_remove_anothers_file(two_users):
    ada = two_users("ada")
    use_scripted_agent(ada, [AIMessage("Got it.")])
    uploaded = upload(ada, "mine.txt", b"secret notes").json()

    grace = two_users("grace")
    use_scripted_agent(grace, [AIMessage("x")])
    thread_id = create_thread(grace)

    assert grace.delete(f"/api/files/{uploaded['id']}").status_code == 404
    send = grace.post(
        f"/api/threads/{thread_id}/messages",
        json={"content": "read it", "attachments": [uploaded["id"]]},
    )
    assert send.status_code == 422
    assert two_users("ada").delete(f"/api/files/{uploaded['id']}").status_code == 204
