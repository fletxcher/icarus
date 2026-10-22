import asyncio

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from api.agent.titles import TitleGenerator, clean_title
from api.chats import router as chats_router
from tests.conftest import ScriptedModel, use_scripted_agent, use_scripted_titles
from tests.test_chats_api import create_thread, read_events


def title_call(title: str) -> AIMessage:
    """A model reply calling the ChatTitle tool, as structured output expects."""
    return AIMessage("", tool_calls=[{"id": "t1", "name": "ChatTitle", "args": {"title": title}}])


def test_clean_title_keeps_just_the_title():
    assert clean_title('"NACA 2412 Lift Analysis."') == "NACA 2412 Lift Analysis"
    assert clean_title("Title: **Wing Drag Study**\nextra line") == "Wing Drag Study"
    assert clean_title("   \n  ") is None
    long = clean_title("word " * 30)
    assert long is not None and len(long) <= 60 and not long.endswith(" ")


def test_first_message_names_the_chat(client: TestClient):
    use_scripted_agent(client, [AIMessage("Here you go."), AIMessage("Again.")])
    titles = use_scripted_titles(client, [title_call("NACA 2412 Lift Analysis.")])
    thread_id = create_thread(client, "What lift does a NACA 2412 make")

    events = read_events(client, thread_id, {"content": "What lift does a NACA 2412 make?"})

    # The title comes first, before the reply starts.
    assert events[0] == ("title", {"title": "NACA 2412 Lift Analysis"})
    assert [name for name, _ in events][1:] == ["start", "token", "done"]
    thread = client.get(f"/api/threads/{thread_id}").json()
    assert thread["title"] == "NACA 2412 Lift Analysis"
    assert thread["auto_title"] is False

    # Only the first message names the chat.
    events = read_events(client, thread_id, {"content": "And at Re 2e6?"})
    assert "title" not in [name for name, _ in events]
    assert len(titles.seen) == 1


def test_renamed_chat_keeps_the_users_title(client: TestClient):
    use_scripted_agent(client, [AIMessage("Here you go.")])
    titles = use_scripted_titles(client, [title_call("Generated Title")])
    thread_id = create_thread(client)
    client.patch(f"/api/threads/{thread_id}", json={"title": "My wing study"})

    events = read_events(client, thread_id, {"content": "Hello"})

    assert "title" not in [name for name, _ in events]
    assert client.get(f"/api/threads/{thread_id}").json()["title"] == "My wing study"
    assert titles.seen == []


def test_failed_title_generation_does_not_affect_the_reply(client: TestClient):
    use_scripted_agent(client, [AIMessage("Here you go.")])
    use_scripted_titles(client, [])  # No reply scripted: the title call raises.
    thread_id = create_thread(client, "Placeholder")

    events = read_events(client, thread_id, {"content": "Hello"})

    assert [name for name, _ in events] == ["start", "token", "done"]
    thread = client.get(f"/api/threads/{thread_id}").json()
    assert thread["title"] == "Placeholder"
    assert thread["auto_title"] is True


def test_reply_is_not_held_back_by_a_slow_title(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    class SlowTitleModel(ScriptedModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            await asyncio.sleep(0.5)
            return await super()._agenerate(messages, stop, run_manager, **kwargs)

    monkeypatch.setattr(chats_router, "TITLE_WAIT_S", 0.05)
    use_scripted_agent(client, [AIMessage("Here you go.")])
    services = client.app.state.services
    model = SlowTitleModel(replies=[title_call("Slow Title")], seen=[])
    services.titles = TitleGenerator(services.chats, {"mistral-medium-3-5": model})
    thread_id = create_thread(client)

    events = read_events(client, thread_id, {"content": "Hello"})

    # The reply went ahead; the late title still arrives before the reply finishes.
    assert [name for name, _ in events] == ["start", "token", "title", "done"]


def test_title_from_a_model_that_ignores_the_tool_is_skipped(client: TestClient):
    use_scripted_agent(client, [AIMessage("Here you go.")])
    use_scripted_titles(client, [AIMessage("User Safety: safe")])
    thread_id = create_thread(client, "can you tell me about CFD ?")

    events = read_events(client, thread_id, {"content": "can you tell me about CFD ?"})

    assert "title" not in [name for name, _ in events]
    assert client.get(f"/api/threads/{thread_id}").json()["title"] == "can you tell me about CFD ?"


def test_model_without_tool_calling_leaves_the_reply_intact(client: TestClient):
    class NoToolsModel(ScriptedModel):
        def bind_tools(self, tools, **kwargs):
            raise NotImplementedError

    use_scripted_agent(client, [AIMessage("Here you go.")])
    services = client.app.state.services
    model = NoToolsModel(replies=[], seen=[])
    services.titles = TitleGenerator(services.chats, {"mistral-medium-3-5": model})
    thread_id = create_thread(client, "Placeholder")

    events = read_events(client, thread_id, {"content": "Hello"})

    assert [name for name, _ in events] == ["start", "token", "done"]
    assert client.get(f"/api/threads/{thread_id}").json()["title"] == "Placeholder"
