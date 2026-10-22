from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from tests.conftest import use_scripted_agent
from tests.test_chats_api import create_thread, read_events

AIRFOIL = "NACA 2412\n1.0000 0.0013\n0.5000 0.0600\n0.0000 0.0000\n"


def upload(client: TestClient, name: str, data: bytes, content_type: str = "text/plain"):
    return client.post("/api/files", files={"file": (name, data, content_type)})


def test_text_file_upload(client: TestClient):
    response = upload(client, "naca2412.dat", AIRFOIL.encode())

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "naca2412.dat"
    assert body["size"] == len(AIRFOIL)


def test_binary_and_oversized_files_are_rejected(client: TestClient):
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    assert upload(client, "plot.png", png, "image/png").status_code == 415
    assert upload(client, "big.txt", b"a" * 1_000_001).status_code == 413


def test_attached_file_reaches_the_model_and_is_shown_on_the_message(client: TestClient):
    model = use_scripted_agent(client, [AIMessage("Looks like a NACA 2412.")])
    file = upload(client, "naca2412.dat", AIRFOIL.encode()).json()
    thread_id = create_thread(client)

    events = read_events(
        client, thread_id, {"content": "What airfoil is this?", "attachments": [file["id"]]}
    )

    assert events[-1][0] == "done"
    prompt = model.seen[0][-1].content
    assert prompt.startswith("What airfoil is this?")
    assert "<file name='naca2412.dat'>" in prompt and "0.5000 0.0600" in prompt
    user = client.get(f"/api/threads/{thread_id}").json()["messages"][0]
    assert user["attachments"] == [file]


def test_unknown_attachment_is_rejected(client: TestClient):
    use_scripted_agent(client, [])
    thread_id = create_thread(client)
    body = {"content": "hi", "attachments": ["missing"]}
    response = client.post(f"/api/threads/{thread_id}/messages", json=body)
    assert response.status_code == 422


def test_unsent_files_can_be_removed_but_sent_ones_belong_to_the_chat(client: TestClient):
    use_scripted_agent(client, [AIMessage("Got it.")])
    draft = upload(client, "draft.txt", b"draft").json()
    assert client.delete(f"/api/files/{draft['id']}").status_code == 204

    sent = upload(client, "sent.txt", b"sent").json()
    thread_id = create_thread(client)
    read_events(client, thread_id, {"content": "see file", "attachments": [sent["id"]]})
    assert client.delete(f"/api/files/{sent['id']}").status_code == 404

    # Deleting the chat deletes its files.
    client.delete(f"/api/threads/{thread_id}")
    other = create_thread(client)
    response = client.post(
        f"/api/threads/{other}/messages", json={"content": "hi", "attachments": [sent["id"]]}
    )
    assert response.status_code == 422
