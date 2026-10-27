import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.graph.state import CompiledStateGraph
from pydantic import SecretStr
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from api.agent.factory import UserAgent
from api.agent.graph import build_agent
from api.agent.models import available_models
from api.agent.titles import TitleGenerator
from api.config import Settings, get_settings


class ScriptedModel(BaseChatModel):
    """Chat model that returns pre-written replies in order, for driving the agent in tests."""

    replies: list[AIMessage]
    seen: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


@tool
async def add(a: int, b: int) -> str:
    """Add two integers."""
    return str(a + b)


MONGODB_URI = os.environ.get("MONGODB_LOCAL_URI", "mongodb://localhost:27017")


@pytest.fixture(scope="session")
def _test_database() -> Iterator[str]:
    """One throwaway database for the whole run; skips when MongoDB isn't reachable.

    It's shared (and emptied between tests) rather than created per test: every database
    adds files for its collections and indexes, and enough of them exhaust MongoDB's
    open-file limit and crash it.
    """
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=1000)
    try:
        client.admin.command("ping")
    except PyMongoError:
        pytest.skip(f"MongoDB not reachable at {MONGODB_URI}")
    name = f"icarus_test_{uuid4().hex[:8]}"
    yield name
    client.drop_database(name)
    client.close()


@pytest.fixture
def mongo_db_name(_test_database: str) -> str:
    """The shared test database, with every collection emptied (schemas and indexes stay)."""
    client = MongoClient(MONGODB_URI)
    database = client[_test_database]
    for name in database.list_collection_names():
        database[name].delete_many({})
    client.close()
    return _test_database


@pytest.fixture
def anon_client(mongo_db_name: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The app with nobody signed in."""
    # Ignore the developer's .env so local keys and settings can't leak into tests.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in ("OPENROUTER_API_KEY", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    # Always the local server, whatever the developer's environment says.
    monkeypatch.setenv("MONGODB_TARGET", "local")
    monkeypatch.setenv("MONGODB_LOCAL_URI", MONGODB_URI)
    monkeypatch.setenv("MONGODB_LOCAL_NAME", mongo_db_name)
    monkeypatch.setenv("MISTRAL_API_TOKEN", "test-token")
    monkeypatch.setenv("DEFAULT_MODEL", "mistral-medium-3-5")
    # Off unless a test plugs in a scripted title model (see use_scripted_titles).
    monkeypatch.setenv("AUTO_TITLE", "false")
    # A key of its own, so tests never use (or write with) the developer's.
    monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("WS_HOST", raising=False)
    get_settings.cache_clear()

    from api.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


TEST_USER = {"name": "Test User", "email": "tester@example.com", "password": "correct horse"}


def sign_up_and_in(client: TestClient, **changes: str) -> dict:
    """Creates an account and signs in as it (the session cookie stays on `client`)."""
    account = TEST_USER | changes
    assert client.post("/api/auth/signup", json=account).status_code == 201
    login = client.post("/api/auth/login", json=account)
    assert login.status_code == 200
    return login.json()


@pytest.fixture
def client(anon_client: TestClient) -> TestClient:
    """The app with a user signed in; `anon_client` is the same app with nobody."""
    sign_up_and_in(anon_client)
    return anon_client


def use_agent(
    client: TestClient, agent: CompiledStateGraph | None, titles: TitleGenerator | None = None
) -> None:
    """Every user gets this agent (None: no model is available) instead of one built from keys."""
    models = available_models({"mistral": SecretStr("test-token")}) if agent else []
    client.app.state.services.agents.override = UserAgent(
        models, "mistral-medium-3-5" if agent else None, agent, titles
    )


def use_scripted_agent(client: TestClient, replies: list[AIMessage]) -> ScriptedModel:
    model = ScriptedModel(replies=replies, seen=[])
    use_agent(
        client,
        build_agent({"mistral-medium-3-5": model}, [add], default_model="mistral-medium-3-5"),
    )
    return model


def use_scripted_titles(client: TestClient, replies: list[AIMessage]) -> ScriptedModel:
    services = client.app.state.services
    model = ScriptedModel(replies=replies, seen=[])
    services.agents.override.titles = TitleGenerator(services.chats, {"mistral-medium-3-5": model})
    return model
