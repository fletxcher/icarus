import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from api.agent.graph import build_agent
from api.config import get_settings


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


@pytest.fixture
def mongo_db_name() -> Iterator[str]:
    """A throwaway database; skips the test when MongoDB isn't reachable."""
    uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    client = MongoClient(uri, serverSelectionTimeoutMS=1000)
    try:
        client.admin.command("ping")
    except PyMongoError:
        pytest.skip(f"MongoDB not reachable at {uri}")
    name = f"icarus_test_{uuid4().hex[:8]}"
    yield name
    client.drop_database(name)
    client.close()


@pytest.fixture
def client(mongo_db_name: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("MONGODB_DB", mongo_db_name)
    monkeypatch.setenv("MISTRAL_API_TOKEN", "test-token")
    # Pin values the tests rely on, whatever the local .env says.
    monkeypatch.setenv("DEFAULT_MODEL", "mistral-medium-3-5")
    get_settings.cache_clear()

    from api.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def use_scripted_agent(client: TestClient, replies: list[AIMessage]) -> ScriptedModel:
    model = ScriptedModel(replies=replies, seen=[])
    client.app.state.services.agent = build_agent(
        {"mistral-medium-3-5": model}, [add], default_model="mistral-medium-3-5"
    )
    return model
