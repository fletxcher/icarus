import logging

import asyncssh
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pymongo import MongoClient

from api.config import get_settings
from api.credentials import providers
from api.credentials.vault import SealedValueRejected, Vault, VaultUnavailable
from tests import ssh_station
from tests.conftest import MONGODB_URI, sign_up_and_in

API_KEY = "sk-secret-value-1234"


# The vault


def test_a_sealed_value_opens_only_for_the_credential_it_was_sealed_for():
    vault = Vault(SecretStr(Fernet.generate_key().decode()))

    sealed = vault.seal("u1", "model_key", "mistral", API_KEY)

    assert API_KEY not in sealed
    assert vault.open("u1", "model_key", "mistral", sealed) == API_KEY
    for other in [("u2", "model_key", "mistral"), ("u1", "model_key", "openrouter")]:
        with pytest.raises(SealedValueRejected):
            vault.open(*other, sealed)


def test_a_value_sealed_with_another_key_is_refused_and_keys_can_rotate():
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    sealed = Vault(SecretStr(old)).seal("u1", "model_key", "mistral", API_KEY)

    with pytest.raises(SealedValueRejected):
        Vault(SecretStr(new)).open("u1", "model_key", "mistral", sealed)
    rotated = Vault(SecretStr(f"{new},{old}"))
    assert rotated.open("u1", "model_key", "mistral", sealed) == API_KEY
    # New values use the first key, so the old one can be dropped later.
    resealed = rotated.seal("u1", "model_key", "mistral", API_KEY)
    assert Vault(SecretStr(new)).open("u1", "model_key", "mistral", resealed) == API_KEY


def test_the_vault_without_a_key_refuses_to_store_anything():
    assert not Vault(None).available
    with pytest.raises(VaultUnavailable):
        Vault(None).seal("u1", "model_key", "mistral", API_KEY)
    with pytest.raises(VaultUnavailable):
        Vault(SecretStr("not-a-fernet-key"))


# Model keys


@pytest.fixture
def accepted_keys(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Providers accept any key, and the checks are recorded."""
    seen: list[tuple[str, str]] = []

    async def accept(provider, key):
        seen.append((provider, key.get_secret_value()))

    monkeypatch.setattr(providers, "check_key", accept)
    return seen


def stored_credentials(client: TestClient) -> list[dict]:
    source = MongoClient(MONGODB_URI)
    try:
        return list(source[client.app.state.db.name]["credentials"].find())
    finally:
        source.close()


def test_credentials_need_a_signed_in_user(anon_client: TestClient):
    assert anon_client.get("/api/credentials").status_code == 401
    assert anon_client.put("/api/credentials/model-keys/mistral", json={}).status_code == 401
    assert anon_client.delete("/api/credentials/workstation").status_code == 401


def test_a_saved_key_is_encrypted_and_never_sent_back(client: TestClient, accepted_keys):
    response = client.put("/api/credentials/model-keys/mistral", json={"api_key": f" {API_KEY} "})

    assert response.status_code == 200
    assert response.json()["provider"] == "mistral"
    assert response.json()["last4"] == "1234"
    assert accepted_keys == [("mistral", API_KEY)]  # trimmed
    [stored] = stored_credentials(client)
    assert API_KEY not in str(stored)
    assert stored["details"] == {"last4": "1234"}
    overview = client.get("/api/credentials").json()
    assert API_KEY not in str(overview)
    assert [key["provider"] for key in overview["model_keys"]] == ["mistral"]
    assert overview["default_providers"] == ["mistral"]  # from the server's environment
    assert overview["storage_available"] is True


def test_a_key_the_provider_rejects_is_not_saved(client: TestClient, monkeypatch):
    async def reject(provider, key):
        raise providers.KeyRejected(provider)

    monkeypatch.setattr(providers, "check_key", reject)

    response = client.put("/api/credentials/model-keys/mistral", json={"api_key": API_KEY})

    assert response.status_code == 422
    assert "rejected" in response.json()["detail"]
    assert stored_credentials(client) == []


def test_bad_key_input_is_rejected(client: TestClient, accepted_keys):
    assert (
        client.put("/api/credentials/model-keys/mistral", json={"api_key": "x"}).status_code == 422
    )
    assert (
        client.put("/api/credentials/model-keys/cohere", json={"api_key": API_KEY}).status_code
        == 422
    )


def test_a_key_can_be_replaced_and_removed(client: TestClient, accepted_keys):
    client.put("/api/credentials/model-keys/mistral", json={"api_key": API_KEY})
    client.put("/api/credentials/model-keys/mistral", json={"api_key": "sk-another-key-9999"})

    [stored] = stored_credentials(client)
    assert stored["details"]["last4"] == "9999"
    assert client.delete("/api/credentials/model-keys/mistral").status_code == 204
    assert client.delete("/api/credentials/model-keys/mistral").status_code == 404
    assert stored_credentials(client) == []


def test_without_a_credentials_key_nothing_can_be_saved(anon_client: TestClient, monkeypatch):
    monkeypatch.setattr(anon_client.app.state.services.credentials, "vault", Vault(None))
    sign_up_and_in(anon_client)

    assert anon_client.get("/api/credentials").json()["storage_available"] is False
    response = anon_client.put("/api/credentials/model-keys/mistral", json={"api_key": API_KEY})
    assert response.status_code == 503
    assert "CREDENTIALS_KEY" in response.json()["detail"]


def test_a_users_own_key_replaces_the_servers_for_that_provider_only(
    client: TestClient, accepted_keys
):
    services = client.app.state.services
    user_id = client.get("/api/auth/me").json()["id"]
    client.put("/api/credentials/model-keys/openrouter", json={"api_key": API_KEY})

    keys = client.portal.call(services.agents.keys_for, user_id)

    assert keys["mistral"].get_secret_value() == "test-token"  # the server's
    assert keys["openrouter"].get_secret_value() == API_KEY  # theirs
    ids = [model["id"] for model in client.get("/api/models").json()]
    assert "openrouter/free" in ids and "mistral-medium-3-5" in ids


def test_another_user_never_gets_a_users_key(anon_client: TestClient, accepted_keys):
    sign_up_and_in(anon_client)
    anon_client.put("/api/credentials/model-keys/openrouter", json={"api_key": API_KEY})
    anon_client.post("/api/auth/logout")
    sign_up_and_in(anon_client, email="other@example.com")

    assert anon_client.get("/api/credentials").json()["model_keys"] == []
    assert "openrouter/free" not in [m["id"] for m in anon_client.get("/api/models").json()]


def test_secrets_are_not_written_to_the_logs(client: TestClient, accepted_keys, caplog):
    with caplog.at_level(logging.DEBUG):
        client.put("/api/credentials/model-keys/mistral", json={"api_key": API_KEY})
        client.post(
            "/api/auth/login", json={"email": "tester@example.com", "password": "correct horse"}
        )

    assert API_KEY not in caplog.text
    assert "correct horse" not in caplog.text


# Work station


@pytest.fixture
def station(tmp_path):
    fake = ssh_station.start(tmp_path)
    yield fake
    fake.stop()


def host_key_line(fake: ssh_station.FakeStation) -> str:
    return " ".join(fake.host_key.export_public_key("openssh").decode().split()[:2])


def station_body(fake: ssh_station.FakeStation, **changes) -> dict:
    return {
        "host": fake.host,
        "port": fake.port,
        "username": "tester",
        "private_key": ssh_station.private_pem(fake.client_key),
        "host_key": host_key_line(fake),
    } | changes


def test_the_hosts_key_is_shown_for_approval(client: TestClient, station):
    response = client.post(
        "/api/credentials/workstation/host-key", json={"host": station.host, "port": station.port}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["host_key"] == host_key_line(station)
    assert body["key_type"] == "ssh-ed25519"
    assert body["fingerprint"] == station.host_key.get_fingerprint("sha256")


def test_an_unreachable_host_is_explained(client: TestClient):
    response = client.post(
        "/api/credentials/workstation/host-key", json={"host": "127.0.0.1", "port": 1}
    )

    assert response.status_code == 422
    assert "127.0.0.1:1" in response.json()["detail"]


def test_a_work_station_is_connected_checked_and_saved(client: TestClient, station):
    response = client.put("/api/credentials/workstation", json=station_body(station))

    assert response.status_code == 200, response.text
    body = response.json()
    info = body["workstation"]
    assert (info["host"], info["port"], info["username"]) == (station.host, station.port, "tester")
    assert info["workdir"] == f"{ssh_station.HOME}/icarus"
    assert info["fingerprint"] == station.host_key.get_fingerprint("sha256")
    assert info["public_key"].startswith("ssh-ed25519 ")
    checks = {check["id"]: check for check in body["checks"]}
    assert checks["connection"]["ok"] and checks["runner"]["ok"] and checks["xfoil"]["ok"]
    assert not checks["cfd"]["ok"] and "gmsh" in checks["cfd"]["detail"]
    assert "setup.sh --install" in checks["install"]["detail"]
    # The runner scripts and the setup script were put on the machine.
    setup = station.root / ssh_station.HOME.lstrip("/") / "icarus" / "setup"
    assert {path.name for path in setup.iterdir()} >= {"run_openfoam.sh", "cfd_case.py", "setup.sh"}


def test_the_private_key_is_stored_encrypted_and_never_returned(client: TestClient, station):
    pem = ssh_station.private_pem(station.client_key)
    response = client.put("/api/credentials/workstation", json=station_body(station))

    assert response.status_code == 200
    stored = stored_credentials(client)
    assert [doc["kind"] for doc in stored] == ["workstation"]
    body_of_key = pem.split("\n")[1]
    assert body_of_key not in str(stored)
    assert body_of_key not in client.get("/api/credentials").text
    assert body_of_key not in response.text


def test_a_work_station_needs_its_host_key_to_match(client: TestClient, station):
    other = asyncssh.generate_private_key("ssh-ed25519")
    wrong = " ".join(other.export_public_key("openssh").decode().split()[:2])

    response = client.put(
        "/api/credentials/workstation", json=station_body(station, host_key=wrong)
    )

    assert response.status_code == 422
    assert "host key" in response.json()["detail"]
    assert stored_credentials(client) == []


def test_a_rejected_key_says_which_public_key_to_authorize(client: TestClient, tmp_path):
    fake = ssh_station.start(tmp_path, authorized=asyncssh.generate_private_key("ssh-ed25519"))
    try:
        response = client.put("/api/credentials/workstation", json=station_body(fake))
    finally:
        fake.stop()

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "authorized_keys" in detail
    public = " ".join(fake.client_key.export_public_key("openssh").decode().split()[:2])
    assert public in detail
    assert stored_credentials(client) == []


def test_unreadable_keys_and_bad_fields_are_rejected(client: TestClient, station):
    junk = "-----BEGIN OPENSSH PRIVATE KEY-----\nnot a key at all, just text here\n-----END-----"
    assert (
        client.put(
            "/api/credentials/workstation", json=station_body(station, private_key=junk)
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/credentials/workstation", json=station_body(station, host="a b")
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/credentials/workstation", json=station_body(station, username="a;b")
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/credentials/workstation", json=station_body(station, workdir="~/x")
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/credentials/workstation", json=station_body(station, port=70000)
        ).status_code
        == 422
    )


def test_private_addresses_can_be_turned_off_for_a_public_server(
    client: TestClient, station, monkeypatch
):
    monkeypatch.setattr(get_settings(), "allow_private_workstations", False)

    probe = client.post(
        "/api/credentials/workstation/host-key", json={"host": station.host, "port": station.port}
    )
    save = client.put("/api/credentials/workstation", json=station_body(station))

    for response in (probe, save):
        assert response.status_code == 422
        assert "server's own network" in response.json()["detail"]


def test_the_users_work_station_is_used_for_their_work(client: TestClient, station):
    before = {c["id"]: c for c in client.get("/api/status").json()}["work_station"]
    assert before["ok"] is False  # none in tests until they add one
    client.put("/api/credentials/workstation", json=station_body(station))
    station.commands.clear()

    after = {c["id"]: c for c in client.get("/api/status").json()}["work_station"]

    assert after["ok"] is True
    assert "Your machine" in after["detail"]
    assert "true" in station.commands


def test_a_work_station_can_be_rechecked_and_removed(client: TestClient, station):
    assert client.post("/api/credentials/workstation/check").status_code == 404
    client.put("/api/credentials/workstation", json=station_body(station))

    recheck = client.post("/api/credentials/workstation/check")
    assert recheck.status_code == 200
    assert recheck.json()["workstation"]["host"] == station.host

    assert client.delete("/api/credentials/workstation").status_code == 204
    assert client.delete("/api/credentials/workstation").status_code == 404
    assert client.get("/api/credentials").json()["workstation"] is None
    after = {c["id"]: c for c in client.get("/api/status").json()}["work_station"]
    assert after["ok"] is False  # back to the shared one, which tests don't have


def test_another_user_cant_use_a_work_station_they_didnt_add(anon_client: TestClient, station):
    sign_up_and_in(anon_client)
    anon_client.put("/api/credentials/workstation", json=station_body(station))
    anon_client.post("/api/auth/logout")
    sign_up_and_in(anon_client, email="other@example.com")

    assert anon_client.get("/api/credentials").json()["workstation"] is None
    status = {c["id"]: c for c in anon_client.get("/api/status").json()}["work_station"]
    assert status["ok"] is False


# Ollama


@pytest.fixture
def ollama_server(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """An Ollama server that has these models installed."""
    from api.agent import ollama

    installed = ["llama3.1:8b", "qwen3:4b"]

    async def tags(base_url: str) -> list[str]:
        return installed

    monkeypatch.setattr(ollama, "installed_models", tags)
    ollama._cache.clear()  # noqa: SLF001
    return installed


def test_an_ollama_server_adds_its_installed_models(client: TestClient, ollama_server):
    response = client.put("/api/credentials/ollama", json={"base_url": "http://localhost:11434/"})

    assert response.status_code == 200
    assert response.json()["url"] == "http://localhost:11434"
    models = {m["id"]: m for m in client.get("/api/models").json()}
    assert models["ollama/llama3.1:8b"]["provider"] == "ollama"
    assert "ollama/qwen3:4b" in models and "mistral-medium-3-5" in models
    overview = client.get("/api/credentials").json()
    assert overview["model_keys"][0]["url"] == "http://localhost:11434"
    assert client.delete("/api/credentials/model-keys/ollama").status_code == 204
    assert "ollama/qwen3:4b" not in [m["id"] for m in client.get("/api/models").json()]


def test_bad_ollama_addresses_are_rejected(client: TestClient, ollama_server, monkeypatch):
    def put(url: str):
        return client.put("/api/credentials/ollama", json={"base_url": url})

    assert put("localhost:11434").status_code == 422
    assert put("http://user:pass@localhost:11434").status_code == 422
    assert put("ftp://localhost:11434").status_code == 422
    assert (
        client.put(
            "/api/credentials/model-keys/ollama", json={"api_key": "not-a-key-really"}
        ).status_code
        == 422
    )
    monkeypatch.setattr(get_settings(), "allow_private_workstations", False)
    assert put("http://127.0.0.1:11434").status_code == 422
    assert stored_credentials(client) == []


def test_something_that_isnt_ollama_is_not_saved(client: TestClient, monkeypatch):
    from api.agent import ollama

    async def not_ollama(base_url: str) -> list[str]:
        raise ollama.OllamaUnavailable("HTTPStatusError")

    monkeypatch.setattr(ollama, "installed_models", not_ollama)

    response = client.put("/api/credentials/ollama", json={"base_url": "http://localhost:11434"})

    assert response.status_code == 422
    assert "Ollama" in response.json()["detail"]
    assert stored_credentials(client) == []
