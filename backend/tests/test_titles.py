from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from api.agent.titles import clean_title
from tests.conftest import use_scripted_agent, use_scripted_titles
from tests.test_chats_api import create_thread, read_events


def test_clean_title_keeps_just_the_title():
    assert clean_title('"NACA 2412 Lift Analysis."') == "NACA 2412 Lift Analysis"
    assert clean_title("Title: **Wing Drag Study**\nextra line") == "Wing Drag Study"
    assert clean_title("   \n  ") is None
    long = clean_title("word " * 30)
    assert long is not None and len(long) <= 60 and not long.endswith(" ")


def test_first_message_names_the_chat(client: TestClient):
    use_scripted_agent(client, [AIMessage("Here you go."), AIMessage("Again.")])
    titles = use_scripted_titles(client, [AIMessage("NACA 2412 Lift Analysis.")])
    thread_id = create_thread(client, "What lift does a NACA 2412 make")

    events = read_events(client, thread_id, {"content": "What lift does a NACA 2412 make?"})

    assert ("title", {"title": "NACA 2412 Lift Analysis"}) in events
    assert [name for name, _ in events][-2:] == ["title", "done"]
    thread = client.get(f"/api/threads/{thread_id}").json()
    assert thread["title"] == "NACA 2412 Lift Analysis"
    assert thread["auto_title"] is False

    # Only the first message names the chat.
    events = read_events(client, thread_id, {"content": "And at Re 2e6?"})
    assert "title" not in [name for name, _ in events]
    assert len(titles.seen) == 1


def test_renamed_chat_keeps_the_users_title(client: TestClient):
    use_scripted_agent(client, [AIMessage("Here you go.")])
    titles = use_scripted_titles(client, [AIMessage("Generated Title")])
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
