"""A chat is told when its simulation ends, so the agent never has to poll for it."""

import json
from functools import partial

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from pymongo import MongoClient

from api.agent.stream import to_langchain
from api.catalog import SIMULATIONS
from api.chats.schemas import Message
from api.compute.describe import finished_notice, running_note
from api.compute.pool import Workstation
from api.compute.schemas import CfdCase
from api.compute.watcher import watch_once
from tests.conftest import MONGODB_URI, use_scripted_agent
from tests.test_chats_api import create_thread, read_events


class Station:
    """A work station whose jobs end when the test says so."""

    def __init__(self) -> None:
        self.status: dict[str, dict] = {}
        self.broken = False

    async def run(self, command, *, cwd=None, timeout_s=None):
        from api.compute.runner import CommandResult

        return CommandResult(0, "", "")

    async def write_file(self, path, content):
        return None

    async def write_bytes(self, path, content):
        return None

    async def read_file(self, path):
        if self.broken:
            raise OSError("ssh: connection refused")
        for job_id, status in self.status.items():
            if path == f"/w/jobs/{job_id}/status.json":
                return json.dumps(status)
        raise FileNotFoundError(path)


@pytest.fixture
def station(client: TestClient) -> Station:
    fake = Station()
    client.app.state.services.compute.set_shared(
        Workstation(fake, "/w", "/w/bin/run_openfoam.sh", "shared")
    )
    return fake


def submit(client: TestClient, thread_id: str | None):
    user_id = client.get("/api/auth/me").json()["id"]
    service = client.portal.call(client.app.state.services.compute.simulations_for, user_id)
    case = CfdCase(shape="cylinder", reynolds=100)
    return client.portal.call(partial(service.submit, case, thread_id=thread_id))


def watch(client: TestClient) -> int:
    services = client.app.state.services
    return client.portal.call(watch_once, services.compute, services.chats)


def messages(client: TestClient, thread_id: str) -> list[dict]:
    return client.get(f"/api/threads/{thread_id}").json()["messages"]


def test_a_finished_job_leaves_a_notice_in_its_chat_once(client, station):
    thread_id = create_thread(client, "CFD")
    job = submit(client, thread_id)
    assert watch(client) == 0  # still running: nothing to say

    station.status[job.id] = {"status": "completed", "result": {"cl": 0.1, "cd": 1.2}}
    assert watch(client) == 1
    assert watch(client) == 0  # told once, not every pass

    [notice] = messages(client, thread_id)
    assert notice["role"] == "notice" and notice["status"] == "done"
    assert f"Simulation {job.id} finished" in notice["content"]
    assert "completed" in notice["content"] and "'cd': 1.2" in notice["content"]
    assert "No need to check on it" in notice["content"]


def test_a_failed_job_is_reported_with_its_error(client, station):
    thread_id = create_thread(client)
    job = submit(client, thread_id)
    station.status[job.id] = {"status": "failed", "error": "failed during solve (exit 1)"}

    watch(client)

    [notice] = messages(client, thread_id)
    assert "it failed" in notice["content"] and "failed during solve" in notice["content"]


def test_a_job_seen_finishing_elsewhere_is_still_announced(client, station):
    """Opening the job's page records that it ended; the chat is still told."""
    thread_id = create_thread(client)
    job = submit(client, thread_id)
    station.status[job.id] = {"status": "completed", "result": {"cl": 0.1, "cd": 0.2}}
    assert client.get(f"/api/simulations/{job.id}").json()["status"] == "completed"

    assert watch(client) == 1
    assert len(messages(client, thread_id)) == 1


def test_a_job_outside_any_chat_has_nobody_to_tell(client, station):
    job = submit(client, None)
    station.status[job.id] = {"status": "completed", "result": {"cl": 0.1, "cd": 0.2}}

    assert watch(client) == 0
    assert client.get(f"/api/simulations/{job.id}").json()["status"] == "completed"


def test_an_unreachable_work_station_is_retried_not_fatal(client, station):
    thread_id = create_thread(client)
    job = submit(client, thread_id)
    station.broken = True
    assert watch(client) == 0  # no exception, nothing recorded

    station.broken = False
    station.status[job.id] = {"status": "completed", "result": {"cl": 0.1, "cd": 0.2}}
    assert watch(client) == 1


def test_a_deleted_chat_is_skipped_quietly(client, station):
    thread_id = create_thread(client)
    job = submit(client, thread_id)
    client.portal.call(
        client.app.state.services.chats.delete_thread,
        thread_id,
        client.get("/api/auth/me").json()["id"],
    )
    station.status[job.id] = {"status": "completed", "result": {"cl": 0.1, "cd": 0.2}}

    assert watch(client) == 0


def test_the_notice_is_stored_as_a_job_told_exactly_once(client, station):
    thread_id = create_thread(client)
    job = submit(client, thread_id)
    source = MongoClient(MONGODB_URI)
    try:
        doc = source[client.app.state.db.name][SIMULATIONS]
        assert doc.find_one({"_id": job.id})["notified_at"] is None
        station.status[job.id] = {"status": "completed", "result": {"cl": 0.1, "cd": 0.2}}
        watch(client)
        assert doc.find_one({"_id": job.id})["notified_at"] is not None
    finally:
        source.close()


def test_the_agent_reads_a_notice_as_part_of_the_users_next_message():
    def message(role, content, seq):
        return Message.model_validate(
            {
                "_id": str(seq), "thread_id": "t", "role": role, "content": content,
                "status": "done", "created_at": "2026-01-01T00:00:00Z",
            }
        )  # fmt: skip

    history = to_langchain(
        [
            message("user", "Run it", 1),
            message("assistant", "Started job 7.", 2),
            message("notice", "Simulation 7 finished: completed", 3),
            message("user", "How did it go?", 4),
        ],
        {},
        file_max_chars=1000,
    )

    assert [type(m) for m in history] == [HumanMessage, AIMessage, HumanMessage]
    assert history[2].content.startswith("[Automatic notice, not written by the user]")
    assert "Simulation 7 finished" in history[2].content
    assert history[2].content.endswith("How did it go?")


def test_the_next_turn_includes_the_notice_and_a_notice_can_start_a_turn(client, station):
    model = use_scripted_agent(client, [AIMessage("It converged."), AIMessage("Anything else?")])
    thread_id = create_thread(client)
    job = submit(client, thread_id)
    station.status[job.id] = {"status": "completed", "result": {"cl": 0.1, "cd": 0.2}}
    watch(client)

    # The user asks nothing: "discuss results" regenerates from the notice.
    events = read_events(client, thread_id, {})
    assert events[-1][0] == "done"
    prompt = model.seen[0][-1].content
    assert f"Simulation {job.id} finished" in prompt

    # A later user message still sees it in the history.
    read_events(client, thread_id, {"content": "thanks"})
    assert any(f"Simulation {job.id} finished" in str(m.content) for m in model.seen[1])


def test_a_chat_with_nothing_to_answer_still_cant_be_regenerated(client):
    use_scripted_agent(client, [])
    thread_id = create_thread(client)
    assert client.post(f"/api/threads/{thread_id}/messages", json={}).status_code == 409


def test_running_jobs_are_listed_for_the_agent_each_turn(client, station):
    model = use_scripted_agent(client, [AIMessage("Still going.")])
    thread_id = create_thread(client)
    job = submit(client, thread_id)

    read_events(client, thread_id, {"content": "done yet?"})

    situation = [m.content for m in model.seen[0] if "still running" in str(m.content)]
    assert situation and job.id in situation[0]
    assert "Don't call get_simulation_status to wait" in situation[0]


def test_a_chat_without_running_jobs_gets_no_such_note(client):
    model = use_scripted_agent(client, [AIMessage("Hi.")])
    thread_id = create_thread(client)

    read_events(client, thread_id, {"content": "hello"})

    assert not any("still running" in str(m.content) for m in model.seen[0])


def test_the_texts_name_the_job_and_its_duration():
    from datetime import UTC, datetime, timedelta

    from api.compute.schemas import Simulation

    start = datetime(2026, 1, 1, tzinfo=UTC)
    job = Simulation.model_validate(
        {
            "_id": "abc", "thread_id": "t", "case": {"shape": "cylinder", "reynolds": 100},
            "status": "completed", "result": {"cl": 1.0}, "created_at": start,
            "updated_at": start + timedelta(minutes=2, seconds=13),
        }
    )  # fmt: skip

    assert "after about 2 min 13 s" in finished_notice(job)
    running = running_note(
        [job.model_copy(update={"status": "running"})], start + timedelta(hours=1, minutes=5)
    )
    assert "abc (cfd), running for 1 h 5 min" in running
    assert running_note([]) is None


def test_the_watcher_runs_on_its_own_and_stops_cleanly(client, station):
    import asyncio

    from api.compute.watcher import watching

    thread_id = create_thread(client)
    job = submit(client, thread_id)
    station.status[job.id] = {"status": "completed", "result": {"cl": 0.1, "cd": 0.2}}
    services = client.app.state.services

    async def wait_for_the_notice():
        async with watching(services.compute, services.chats, 0.05):
            for _ in range(100):
                await asyncio.sleep(0.05)
                if await services.chats.list_messages(thread_id):
                    return True
        return False

    assert client.portal.call(wait_for_the_notice)
    assert len(messages(client, thread_id)) == 1
