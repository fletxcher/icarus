import logging
import time

import asyncssh
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pymongo import MongoClient

from api.config import get_settings
from api.credentials import providers, workstation
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
    assert anon_client.delete("/api/credentials/workstations/any").status_code == 401


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
    assert {path.name for path in setup.iterdir()} >= {
        "run_cfd2d.sh",
        "cfd2d_case.py",
        "run_fea.sh",
        "fea_case.py",
        "setup.sh",
    }


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
    assert "Your remote work station" in after["detail"]
    assert "true" in station.commands


def test_a_work_station_can_be_rechecked_and_its_card_filled_in(client: TestClient, station):
    assert client.post("/api/credentials/workstation/check").status_code == 404
    client.put("/api/credentials/workstation", json=station_body(station, name="Lab box"))

    recheck = client.post("/api/credentials/workstation/check")
    assert recheck.status_code == 200
    assert recheck.json()["workstation"]["host"] == station.host

    [saved] = client.get("/api/credentials").json()["workstations"]
    assert saved["name"] == "Lab box" and saved["active"] and saved["status"] == "ready"
    assert saved["hardware"] == {"cpus": 8, "cpu_model": None, "gpu": None}


def test_machines_default_to_their_host_as_a_name(client: TestClient, station):
    client.put("/api/credentials/workstation", json=station_body(station))

    [saved] = client.get("/api/credentials").json()["workstations"]
    assert saved["name"] == station.host


def test_only_one_machine_is_connected_at_a_time(client: TestClient, station, tmp_path):
    other = ssh_station.start(tmp_path / "other")
    try:
        first = client.put("/api/credentials/workstation", json=station_body(station, name="A"))
        second = client.put("/api/credentials/workstation", json=station_body(other, name="B"))
        first_id, second_id = first.json()["workstation"]["id"], second.json()["workstation"]["id"]
        assert first_id != second_id

        def active() -> list[str]:
            machines = client.get("/api/credentials").json()["workstations"]
            return [m["name"] for m in machines if m["active"]]

        assert active() == ["B"]  # adding connects the new one

        connected = client.post(f"/api/credentials/workstations/{first_id}/connect")
        assert connected.status_code == 200 and connected.json()["active"] is True
        assert active() == ["A"]
        status = {c["id"]: c for c in client.get("/api/status").json()}["work_station"]
        assert status["ok"] is True

        assert (
            client.post(f"/api/credentials/workstations/{first_id}/disconnect").status_code == 204
        )
        assert active() == []
        assert len(client.get("/api/credentials").json()["workstations"]) == 2
        status = {c["id"]: c for c in client.get("/api/status").json()}["work_station"]
        assert status["ok"] is False  # nothing connected, and tests have no shared one

        assert client.post("/api/credentials/workstations/nope/connect").status_code == 404
    finally:
        other.stop()


def _wait_until(client: TestClient, done) -> list[dict]:
    machines: list[dict] = []
    for _ in range(100):
        machines = client.get("/api/credentials").json()["workstations"]
        if done(machines):
            return machines
        time.sleep(0.05)
    raise AssertionError(f"still waiting: {machines}")


def test_removing_a_machine_cleans_it_up_then_forgets_it(client: TestClient, station):
    saved = client.put("/api/credentials/workstation", json=station_body(station)).json()
    machine_id, public_key = saved["workstation"]["id"], saved["workstation"]["public_key"]
    station.commands.clear()

    response = client.delete(f"/api/credentials/workstations/{machine_id}")

    assert response.status_code == 202 and response.json()["status"] == "removing"
    _wait_until(client, lambda machines: machines == [])
    uninstall = next(c for c in station.commands if "--uninstall" in c)
    assert "--purge --yes --home /home/tester/icarus" in uninstall
    keys = next(c for c in station.commands if "authorized_keys" in c)
    assert public_key.split()[1] in keys
    # The cleanup comes before the key goes, and nothing of the machine is left in the database.
    assert station.commands.index(uninstall) < station.commands.index(keys)
    assert stored_credentials(client) == []
    status = {c["id"]: c for c in client.get("/api/status").json()}["work_station"]
    assert status["ok"] is False


def test_a_machine_that_cant_be_reached_stays_until_removed_anyway(client: TestClient, station):
    machine_id = client.put("/api/credentials/workstation", json=station_body(station)).json()[
        "workstation"
    ]["id"]
    station.stop()

    assert client.delete(f"/api/credentials/workstations/{machine_id}").status_code == 202
    [kept] = _wait_until(client, lambda machines: machines[0]["status"] == "removal_failed")
    assert kept["status_message"] and kept["active"] is False
    assert len(stored_credentials(client)) == 1  # the key is still saved

    forced = client.delete(f"/api/credentials/workstations/{machine_id}", params={"force": "true"})
    assert forced.status_code == 204
    assert client.get("/api/credentials").json()["workstations"] == []
    assert stored_credentials(client) == []


def test_the_cleanup_commands_only_touch_this_key_and_icarus_files():
    from api.credentials import cleanup

    command = cleanup.authorized_keys_command("ssh-ed25519 AAAAC3Nza-key-blob")
    assert "grep -vF -- AAAAC3Nza-key-blob" in command and "AAAAC3Nza-key-blob" in command
    assert "ssh-ed25519" not in command  # matched by the key itself, not its type or comment
    assert "if [ -f " in cleanup.uninstall_command("/home/u/icarus")
    assert "--uninstall --purge --yes --home /home/u/icarus" in cleanup.uninstall_command(
        "/home/u/icarus"
    )


def test_another_user_cant_use_a_work_station_they_didnt_add(anon_client: TestClient, station):
    sign_up_and_in(anon_client)
    anon_client.put("/api/credentials/workstation", json=station_body(station))
    anon_client.post("/api/auth/logout")
    sign_up_and_in(anon_client, email="other@example.com")

    assert anon_client.get("/api/credentials").json()["workstations"] == []
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


PROBE_OUTPUT = """hostname=bench
kernel=Linux
arch=x86_64
cpus=16
cpu_model=AMD EPYC 7763
mem_kb=33554432
disk_free_kb=209715200
os_id=ubuntu
os_name=Ubuntu 24.04.1 LTS
gpu=NVIDIA A10
docker=installed
privilege=password_sudo
"""


def test_a_supported_machine_is_described_from_the_probe():
    machine = workstation.parse_machine(PROBE_OUTPUT)

    assert machine.supported and machine.problems == []
    assert (machine.hostname, machine.os, machine.arch) == ("bench", "Ubuntu 24.04.1 LTS", "x86_64")
    assert (machine.cpus, machine.memory_gb, machine.disk_free_gb) == (16, 32.0, 200.0)
    assert machine.gpu == "NVIDIA A10" and machine.cpu_model == "AMD EPYC 7763"
    assert (machine.docker, machine.privilege) == ("installed", "password_sudo")
    assert "16 cores" in workstation.describe_machine(machine)


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"arch": "riscv64"}, "riscv64"),
        ({"kernel": "Darwin"}, "Darwin"),
        ({"mem_kb": "1048576"}, "1 GB of memory"),
        ({"disk_free_kb": "1048576"}, "1 GB of free disk"),
    ],
)
def test_a_machine_that_cant_run_icarus_says_why(changes: dict[str, str], problem: str):
    lines = dict(line.split("=", 1) for line in PROBE_OUTPUT.splitlines()) | changes
    machine = workstation.parse_machine("\n".join(f"{k}={v}" for k, v in lines.items()))

    assert not machine.supported
    assert any(problem in text for text in machine.problems)


def test_a_probe_that_printed_nothing_still_gives_a_machine():
    machine = workstation.parse_machine("")

    assert machine.supported and machine.docker == "missing" and machine.privilege == "none"
    assert machine.cpus is None and machine.os == "unknown"


def test_the_runtime_check_compares_the_machines_image_with_the_shipped_dockerfile():
    expected = workstation.image_tag()
    assert expected.startswith("icarus-solvers:") and len(expected.split(":")[1]) == 12

    direct = workstation.runtime_check({"runtime": "native"})
    current = workstation.runtime_check(
        {"runtime": "docker", "image": expected, "image_present": "1"}
    )
    stale = workstation.runtime_check(
        {"runtime": "docker", "image": "icarus-solvers:old", "image_present": "1"}
    )
    missing = workstation.runtime_check(
        {"runtime": "docker", "image": expected, "image_present": "0"}
    )

    assert direct.ok and current.ok
    assert not stale.ok and "out of date" in stale.detail
    assert not missing.ok and "isn't on the machine" in missing.detail


def test_commands_go_through_the_wrapper_only_when_the_machine_has_it():
    from api.compute.runner import via_exec

    assert via_exec("/w") == "$(test -x /w/bin/icarus-exec && echo /w/bin/icarus-exec) "


def machine(**changes) -> workstation.MachineInfo:
    lines = dict(line.split("=", 1) for line in PROBE_OUTPUT.splitlines())
    lines |= {"docker": "missing", "privilege": "password_sudo"} | changes
    return workstation.parse_machine("\n".join(f"{k}={v}" for k, v in lines.items()))


def test_the_setup_plan_says_what_is_installed_and_what_it_needs():
    from api.credentials import provision

    needs_password = provision.plan_for(machine())
    assert needs_password.can_run and needs_password.needs_root and needs_password.needs_password
    assert any("docker.io" in line for line in needs_password.installs)
    assert any("OpenFOAM" in line and "icarus-solvers:" in line for line in needs_password.installs)

    free = provision.plan_for(machine(privilege="passwordless_sudo"))
    assert free.can_run and free.needs_root and not free.needs_password

    ready = provision.plan_for(machine(docker="usable", privilege="none"))
    assert ready.can_run and not ready.needs_root and len(ready.installs) == 1

    stuck = provision.plan_for(machine(privilege="none"))
    assert not stuck.can_run and "administrator" in stuck.reason

    unsupported = provision.plan_for(machine(arch="riscv64"))
    assert not unsupported.can_run and "riscv64" in unsupported.reason
    assert provision.plan_for(machine(arch="aarch64")).can_run


def test_the_launch_command_holds_no_password_and_detaches_setup():
    from api.credentials import provision

    asked = provision.launch_command("/home/u/icarus", "u", machine(), password=True)
    assert "read -r secret" in asked and "sudo -S -p" in asked and "sg docker -c" in asked
    assert "--install-system --home /home/u/icarus --user u" in asked
    assert "setsid nohup" in asked and "setup.log" in asked and asked.endswith("echo started")

    free = provision.launch_command(
        "/w", "u", machine(privilege="passwordless_sudo"), password=False
    )
    assert "sudo -n" in free and "read -r" not in free and "< /dev/null" in free

    root = provision.launch_command(
        "/root/icarus", "root", machine(privilege="root"), password=False
    )
    assert "sudo" not in root and "sg docker" not in root and "--install-user" in root

    usable = provision.launch_command("/w", "u", machine(docker="usable"), password=False)
    assert "--install-system" not in usable and "--install-user" in usable


def test_setup_checks_the_password_then_runs_in_the_background(client: TestClient, station):
    client.put("/api/credentials/workstation", json=station_body(station))

    plan = client.get("/api/credentials/workstation/setup/plan").json()
    assert plan["needs_password"] and plan["can_run"]

    missing = client.post("/api/credentials/workstation/setup", json={})
    assert missing.status_code == 422 and "sudo password" in missing.json()["detail"]
    wrong = client.post("/api/credentials/workstation/setup", json={"password": "nope"})
    assert wrong.status_code == 422 and "rejected" in wrong.json()["detail"]
    assert not any("setsid nohup" in command for command in station.commands)

    # Once launched, the machine has written its log but not yet its first status.
    station.log_on_launch = "step 3/8\n"
    started = client.post("/api/credentials/workstation/setup", json={"password": "right"})

    assert started.status_code == 202, started.text
    assert started.json()["state"] == "running" and started.json()["log_tail"] == "step 3/8"
    launch = next(command for command in station.commands if "setsid nohup" in command)
    assert "right" not in launch and all("right" not in c for c in station.commands)
    assert station.stdins[-1] == "right\n"


def test_setup_cant_start_twice_and_reports_its_state(client: TestClient, station):
    client.put("/api/credentials/workstation", json=station_body(station))
    station.setup_status = '{"state": "running", "stage": "image", "message": "Building"}'

    again = client.post("/api/credentials/workstation/setup", json={"password": "right"})
    assert again.status_code == 422 and "already running" in again.json()["detail"]

    station.setup_status = '{"state": "failed", "stage": "image", "message": "failed during image"}'
    done = client.get("/api/credentials/workstation/setup").json()
    assert done["state"] == "failed" and done["message"] == "failed during image"

    station.setup_status = ""
    station.setup_log = ""
    assert client.get("/api/credentials/workstation/setup").json()["state"] == "idle"


def test_setup_needs_a_saved_machine(client: TestClient):
    assert client.get("/api/credentials/workstation/setup").status_code == 404
    assert client.post("/api/credentials/workstation/setup", json={}).status_code == 404


def password_body(fake: ssh_station.FakeStation, **changes) -> dict:
    body = station_body(fake, password="hunter2", **changes)
    del body["private_key"]
    return body


def test_a_password_adds_icarus_s_own_key_and_connects_with_it(client: TestClient, station):
    station.password = "hunter2"
    station.sudo_password = "hunter2"

    response = client.put("/api/credentials/workstation", json=password_body(station))

    assert response.status_code == 200, response.text
    saved = response.json()["workstation"]
    assert saved["public_key"].split()[1] in " ".join(station.installed_keys)
    assert any('authorized_keys"' in command for command in station.commands)
    # The password was used for the login (and sudo), and is nowhere: not in a command, the
    # response, or what's stored.
    everything = " ".join(station.commands) + response.text + str(stored_credentials(client))
    assert "hunter2" not in everything
    assert [doc["kind"] for doc in stored_credentials(client)] == ["workstation"]
    # The saved key is the one Icarus made: it can connect again without the password.
    station.password = None
    assert client.post("/api/credentials/workstation/check").status_code == 200


def test_a_wrong_password_saves_nothing(client: TestClient, station):
    station.password = "right-one"

    response = client.put("/api/credentials/workstation", json=password_body(station))

    assert response.status_code == 422 and "rejected that password" in response.json()["detail"]
    assert "hunter2" not in response.text
    assert stored_credentials(client) == []


def test_a_password_or_a_key_is_needed_but_not_both(client: TestClient, station):
    neither = station_body(station)
    del neither["private_key"]
    both = station_body(station, password="hunter2")

    for body in (neither, both):
        response = client.put("/api/credentials/workstation", json=body)
        assert response.status_code == 422 and "either" in response.json()["detail"]
    assert stored_credentials(client) == []


def test_connecting_with_a_password_starts_setup_with_the_same_password(
    client: TestClient, station
):
    station.password = "hunter2"
    station.sudo_password = "hunter2"
    station.log_on_launch = "installing docker\n"

    report = client.put("/api/credentials/workstation", json=password_body(station)).json()

    assert report["setup"]["state"] == "running" and report["setup_note"] is None
    launch = next(command for command in station.commands if "setsid nohup" in command)
    assert "hunter2" not in launch and "--install-system" in launch
    assert station.stdins[-1] == "hunter2\n"  # handed to the one sudo that installs Docker


def test_a_password_that_sudo_rejects_leaves_the_machine_saved_with_a_note(
    client: TestClient, station
):
    station.password = "hunter2"
    station.sudo_password = "something-else"

    report = client.put("/api/credentials/workstation", json=password_body(station)).json()

    assert report["setup"] is None and "rejected" in report["setup_note"]
    assert "Set up this machine" in report["setup_note"]
    assert len(client.get("/api/credentials").json()["workstations"]) == 1


def test_a_machine_that_cant_run_icarus_is_connected_without_starting_setup(
    client: TestClient, station, monkeypatch
):
    station.password = "hunter2"
    monkeypatch.setattr(
        ssh_station, "MACHINE_PROBED", ssh_station.MACHINE_PROBED.replace("x86_64", "riscv64")
    )

    report = client.put("/api/credentials/workstation", json=password_body(station)).json()

    assert report["setup"] is None and "riscv64" in report["setup_note"]


def test_the_install_key_command_adds_the_key_once_and_keeps_the_others():
    command = workstation.install_key_command("ssh-ed25519 AAAAblob icarus")

    assert "chmod 700" in command and "chmod 600" in command
    assert "grep -qF -- AAAAblob" in command  # not added twice
    assert ">> " in command and "authorized_keys" in command


def test_setup_can_be_cancelled(client: TestClient, station):
    client.put("/api/credentials/workstation", json=station_body(station))
    station.setup_status = '{"state": "running", "stage": "image", "message": "Building"}'

    cancelled = client.delete("/api/credentials/workstation/setup")

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled" and station.cancelled
    cancel_command = next(c for c in station.commands if "--cancel" in c)
    assert "--cancel --home /home/tester/icarus" in cancel_command
    # After a cancel it can be started again.
    assert client.get("/api/credentials/workstation/setup").json()["state"] == "cancelled"


def test_cancelling_needs_a_saved_machine(client: TestClient):
    assert client.delete("/api/credentials/workstation/setup").status_code == 404
