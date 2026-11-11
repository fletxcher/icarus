import logging
from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pymongo import MongoClient

from api.config import get_settings
from api.credentials import providers
from api.credentials.vault import SealedValueRejected, Vault, VaultUnavailable
from tests.conftest import MONGODB_URI, sign_up_and_in

API_KEY = "sk-secret-value-1234"


# The vault


def test_a_sealed_value_opens_only_for_the_credential_it_was_sealed_for():
    vault = Vault(SecretStr(Fernet.generate_key().decode()))

    sealed = vault.seal("u1", "model_key", "anthropic", API_KEY)

    assert API_KEY not in sealed
    assert vault.open("u1", "model_key", "anthropic", sealed) == API_KEY
    for other in [("u2", "model_key", "anthropic"), ("u1", "model_key", "openrouter")]:
        with pytest.raises(SealedValueRejected):
            vault.open(*other, sealed)


def test_a_value_sealed_with_another_key_is_refused_and_keys_can_rotate():
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    sealed = Vault(SecretStr(old)).seal("u1", "model_key", "anthropic", API_KEY)

    with pytest.raises(SealedValueRejected):
        Vault(SecretStr(new)).open("u1", "model_key", "anthropic", sealed)
    rotated = Vault(SecretStr(f"{new},{old}"))
    assert rotated.open("u1", "model_key", "anthropic", sealed) == API_KEY
    # New values use the first key, so the old one can be dropped later.
    resealed = rotated.seal("u1", "model_key", "anthropic", API_KEY)
    assert Vault(SecretStr(new)).open("u1", "model_key", "anthropic", resealed) == API_KEY


def test_the_vault_without_a_key_refuses_to_store_anything():
    assert not Vault(None).available
    with pytest.raises(VaultUnavailable):
        Vault(None).seal("u1", "model_key", "anthropic", API_KEY)
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
    assert anon_client.put("/api/credentials/model-keys/anthropic", json={}).status_code == 401


def test_a_saved_key_is_encrypted_and_never_sent_back(client: TestClient, accepted_keys):
    response = client.put("/api/credentials/model-keys/anthropic", json={"api_key": f" {API_KEY} "})

    assert response.status_code == 200
    assert response.json()["provider"] == "anthropic"
    assert response.json()["last4"] == "1234"
    assert accepted_keys == [("anthropic", API_KEY)]  # trimmed
    [stored] = stored_credentials(client)
    assert API_KEY not in str(stored)
    assert stored["details"] == {"last4": "1234"}
    overview = client.get("/api/credentials").json()
    assert API_KEY not in str(overview)
    assert [key["provider"] for key in overview["model_keys"]] == ["anthropic"]
    assert overview["default_providers"] == ["openrouter"]  # from the server's environment
    assert overview["storage_available"] is True


def test_a_key_the_provider_rejects_is_not_saved(client: TestClient, monkeypatch):
    async def reject(provider, key):
        raise providers.KeyRejected(provider)

    monkeypatch.setattr(providers, "check_key", reject)

    response = client.put("/api/credentials/model-keys/anthropic", json={"api_key": API_KEY})

    assert response.status_code == 422
    assert "rejected" in response.json()["detail"]
    assert stored_credentials(client) == []


def test_bad_key_input_is_rejected(client: TestClient, accepted_keys):
    assert (
        client.put("/api/credentials/model-keys/anthropic", json={"api_key": "x"}).status_code
        == 422
    )
    assert (
        client.put("/api/credentials/model-keys/cohere", json={"api_key": API_KEY}).status_code
        == 422
    )


def test_a_key_can_be_replaced_and_removed(client: TestClient, accepted_keys):
    client.put("/api/credentials/model-keys/anthropic", json={"api_key": API_KEY})
    client.put("/api/credentials/model-keys/anthropic", json={"api_key": "sk-another-key-9999"})

    [stored] = stored_credentials(client)
    assert stored["details"]["last4"] == "9999"
    assert client.delete("/api/credentials/model-keys/anthropic").status_code == 204
    assert client.delete("/api/credentials/model-keys/anthropic").status_code == 404
    assert stored_credentials(client) == []


def test_without_a_credentials_key_nothing_can_be_saved(anon_client: TestClient, monkeypatch):
    monkeypatch.setattr(anon_client.app.state.services.credentials, "vault", Vault(None))
    sign_up_and_in(anon_client)

    assert anon_client.get("/api/credentials").json()["storage_available"] is False
    response = anon_client.put("/api/credentials/model-keys/anthropic", json={"api_key": API_KEY})
    assert response.status_code == 503
    assert "CREDENTIALS_KEY" in response.json()["detail"]


def test_a_users_own_key_replaces_the_servers_for_that_provider_only(
    client: TestClient, accepted_keys
):
    services = client.app.state.services
    user_id = client.get("/api/auth/me").json()["id"]
    client.put("/api/credentials/model-keys/openrouter", json={"api_key": API_KEY})
    client.put("/api/credentials/model-keys/anthropic", json={"api_key": API_KEY})

    keys = client.portal.call(services.agents.keys_for, user_id)

    assert keys["openrouter"].get_secret_value() == API_KEY  # theirs replaces the server's
    assert keys["anthropic"].get_secret_value() == API_KEY  # theirs
    ids = [model["id"] for model in client.get("/api/models").json()]
    assert "openrouter/free" in ids and "claude-opus-5-5" in ids


def test_another_user_never_gets_a_users_key(anon_client: TestClient, accepted_keys):
    sign_up_and_in(anon_client)
    anon_client.put("/api/credentials/model-keys/anthropic", json={"api_key": API_KEY})
    anon_client.post("/api/auth/logout")
    sign_up_and_in(anon_client, email="other@example.com")

    assert anon_client.get("/api/credentials").json()["model_keys"] == []
    assert "claude-opus-5-5" not in [m["id"] for m in anon_client.get("/api/models").json()]


def test_secrets_are_not_written_to_the_logs(client: TestClient, accepted_keys, caplog):
    with caplog.at_level(logging.DEBUG):
        client.put("/api/credentials/model-keys/anthropic", json={"api_key": API_KEY})
        client.post(
            "/api/auth/login", json={"email": "tester@example.com", "password": "correct horse"}
        )

    assert API_KEY not in caplog.text
    assert "correct horse" not in caplog.text


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
    assert "ollama/qwen3:4b" in models and "openrouter/free" in models
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


def test_a_key_saved_for_a_provider_the_app_dropped_is_ignored(client: TestClient):
    """Mistral was once offered; keys saved for it stay in the database but are never used."""
    user_id = client.get("/api/auth/me").json()["id"]
    source = MongoClient(MONGODB_URI)
    source[client.app.state.db.name]["credentials"].insert_one(
        {
            "_id": "legacy",
            "user_id": user_id,
            "kind": "model_key",
            "name": "mistral",
            "sealed": "x",
            "details": {"last4": "1234"},
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )
    source.close()

    overview = client.get("/api/credentials")

    assert overview.status_code == 200
    assert overview.json()["model_keys"] == []
    assert client.get("/api/models").status_code == 200
