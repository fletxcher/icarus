import json
from datetime import UTC, datetime
from functools import partial
from typing import Any

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from openrouter.errors import OpenRouterError
from pymongo import MongoClient
from pymongo.database import Database

from api.agent.graph import build_agent
from api.agent.tools import plot_tools
from api.auth.schemas import Identity, Preferences
from api.catalog import COLLECTIONS, SESSIONS, SIMULATIONS, THREADS, USERS
from api.compute.runner import CommandResult
from api.compute.schemas import CfdCase
from api.compute.simulations import SimulationService
from api.schema_export import DATABASE_DIR, collection_document, stale_files
from tests.conftest import MONGODB_URI, ScriptedModel
from tests.test_auth_schema import make_session, make_user
from tests.test_chats_api import create_thread, read_events
from tests.test_files import AIRFOIL, upload
from tests.test_plots import POLAR

CHECK_SUFFIX = "__check"


def test_database_files_are_up_to_date_with_the_catalog():
    # Fix with `uv run export-schemas` after changing a definition.
    assert stale_files() == []


def test_each_file_documents_its_collection():
    for spec in COLLECTIONS:
        document = json.loads((DATABASE_DIR / f"{spec.name}.json").read_text())
        assert document == json.loads(json.dumps(collection_document(spec)))
        assert document["validator"]["$jsonSchema"]["bsonType"] == "object"
        assert all({"name", "key"} <= index.keys() for index in document["indexes"])


def _run_app_flows(client: TestClient) -> None:
    """Exercises the app so every collection it writes to holds real documents."""
    # A chat with a pinned thread, an attached file, reasoning, a plot and a failing tool.
    uploaded = upload(client, "naca2412.dat", AIRFOIL.encode()).json()
    thread_id = create_thread(client, "Wing study")
    client.patch(f"/api/threads/{thread_id}", json={"pinned": True})
    plot = {"id": "p1", "name": "create_plot", "args": {"title": "Lift", "figure": POLAR}}
    failing = {"id": "p2", "name": "missing_tool", "args": {"x": 1}}
    thinking = [{"type": "thinking", "thinking": [{"type": "text", "text": "Plan the plot."}]}]
    model = ScriptedModel(
        replies=[
            AIMessage([*thinking], tool_calls=[plot, failing]),
            AIMessage("Here is the plot."),
        ],
        seen=[],
    )
    client.app.state.services.agent = build_agent(
        {"mistral-medium-3-5": model}, plot_tools(), default_model="mistral-medium-3-5"
    )
    read_events(client, thread_id, {"content": "Plot it", "attachments": [uploaded["id"]]})

    # A reply that fails, so `error` is stored on a message.
    class FailingModel(ScriptedModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            request = httpx.Request("POST", "https://example.test")
            raise OpenRouterError("Rate limit exceeded", httpx.Response(429, request=request))

    client.app.state.services.agent = build_agent(
        {"mistral-medium-3-5": FailingModel(replies=[], seen=[])},
        [],
        default_model="mistral-medium-3-5",
    )
    read_events(client, create_thread(client), {"content": "This one fails"})

    # An unsent file, and CFD jobs: one launched, one that failed to launch.
    upload(client, "draft.txt", b"draft")
    db = client.app.state.db

    class Runner:
        def __init__(self, fail: bool) -> None:
            self.fail = fail

        async def run(self, command, *, cwd=None, timeout_s=None):
            if self.fail:
                raise RuntimeError("ssh: connection refused")
            return CommandResult(0, "", "")

        async def write_file(self, path, content):
            return None

    case = CfdCase(naca="2412", alpha=4, reynolds=3e6)
    for fail in (False, True):
        service = SimulationService(db, Runner(fail), workdir="/w", script="/w/run.sh")
        client.portal.call(partial(service.submit, case, thread_id=thread_id))
    service = SimulationService(db, Runner(False), workdir="/w", script="/w/run.sh")
    client.portal.call(service.submit, case)  # not tied to a chat


def _documents(database: Database, name: str) -> list[dict[str, Any]]:
    return list(database[name].find())


def _model_documents() -> dict[str, list[dict[str, Any]]]:
    """users and sessions come from the models: nothing writes them yet."""
    now = datetime(2026, 10, 22, tzinfo=UTC)
    identity = Identity(provider="github", subject="42", linked_at=now)
    users = [
        make_user(id="u1", email="a@example.com", password_hash="$argon2id$x"),
        make_user(
            id="u2",
            email="b@example.com",
            identities=[identity],
            role="admin",
            preferences=Preferences(default_model="mistral-medium-3-5", theme="dark"),
            last_login_at=now,
        ),
    ]
    sessions = [make_session(id="s1", user_id="u1"), make_session(id="s2", user_id="u2")]
    return {
        USERS: [user.to_mongo() for user in users],
        SESSIONS: [session.to_mongo() for session in sessions],
    }


def test_documents_the_app_really_writes_pass_every_written_schema(client: TestClient):
    _run_app_flows(client)
    source = MongoClient(MONGODB_URI)
    app_db = source[client.app.state.db.name]
    produced = _model_documents()

    checks: list[str] = []
    try:
        for spec in COLLECTIONS:
            documents = produced.get(spec.name) or _documents(app_db, spec.name)
            # Every collection must hold something, or the check proves nothing.
            assert documents, f"no {spec.name} documents were produced"

            # A scratch collection with the schema enforced, whatever `spec.enforced` says.
            scratch = app_db[spec.name + CHECK_SUFFIX]
            app_db.drop_collection(scratch.name)
            app_db.create_collection(
                scratch.name,
                validator=spec.validator,
                validationLevel="strict",
                validationAction="error",
            )
            checks.append(scratch.name)
            scratch.insert_many(documents)  # raises WriteError on any mismatch
    finally:
        for name in checks:
            app_db.drop_collection(name)
        source.close()


def test_the_flows_cover_optional_fields_too(client: TestClient):
    """A pinned thread, a thread-less job and a failed job exist, so those branches ran."""
    _run_app_flows(client)
    source = MongoClient(MONGODB_URI)
    app_db = source[client.app.state.db.name]
    try:
        assert app_db[THREADS].count_documents({"pinned_at": {"$type": "date"}}) == 1
        assert app_db[SIMULATIONS].count_documents({"thread_id": None}) == 1
        assert app_db[SIMULATIONS].count_documents({"status": "failed"}) == 1
        assert app_db["messages"].count_documents({"status": "error", "error": {"$ne": None}}) == 1
        assert app_db["messages"].count_documents({"parts.type": "reasoning"}) == 1
    finally:
        source.close()
