"""Chats, files and simulations belong to the user who made them."""

from functools import partial

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from api.compute.schemas import CfdCase
from tests.conftest import sign_up_and_in, use_scripted_agent
from tests.test_chats_api import create_thread, read_events
from tests.test_files import upload
from tests.test_simulation_monitor import FakeWorkStation, submit_job

SIGNED_IN_ONLY = [
    ("get", "/api/threads"),
    ("post", "/api/threads"),
    ("get", "/api/threads/x"),
    ("patch", "/api/threads/x"),
    ("delete", "/api/threads/x"),
    ("post", "/api/threads/x/messages"),
    ("delete", "/api/threads/x/messages/y"),
    ("get", "/api/simulations"),
    ("get", "/api/simulations/usage"),
    ("get", "/api/simulations/x"),
    ("patch", "/api/simulations/x"),
    ("get", "/api/simulations/x/info"),
    ("get", "/api/simulations/x/progress"),
    ("get", "/api/simulations/x/files/flow.mp4"),
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


def test_a_user_only_sees_their_own_simulations(two_users):
    ada = two_users("ada")
    job_id = submit_job(ada, FakeWorkStation())

    grace = two_users("grace")

    assert grace.get("/api/simulations").json() == []
    assert grace.get("/api/simulations/usage").json()["all_time"]["runs"] == 0
    for path in ("", "/info", "/progress", "/files/flow.mp4"):
        assert grace.get(f"/api/simulations/{job_id}{path}").status_code == 404, path
    assert grace.patch(f"/api/simulations/{job_id}", json={"hidden": True}).status_code == 404
    assert two_users("ada").get(f"/api/simulations/{job_id}").status_code == 200


def test_deleting_a_chat_never_reaches_another_users_simulations(two_users):
    ada = two_users("ada")
    station = FakeWorkStation()
    thread_id = create_thread(ada, "Ada's CFD")
    service_job = submit_job(ada, station)  # also pins the fake station for everyone
    case = CfdCase(shape="cylinder", reynolds=100)
    ada.portal.call(partial(ada.simulations.submit, case, thread_id=thread_id))

    grace = two_users("grace")

    assert grace.delete(f"/api/threads/{thread_id}").status_code == 404
    assert two_users("ada").get(f"/api/simulations/{service_job}").status_code == 200
