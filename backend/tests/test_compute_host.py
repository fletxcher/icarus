"""Reaching the compute host: the three settings, and what the connection checks."""

import asyncio
import threading
from collections.abc import Iterator
from dataclasses import dataclass

import asyncssh
import pytest

from api.compute.runner import HostNotConfigured, SSHRunner, WorkstationTarget
from api.compute.station import Compute
from api.config import Settings


@dataclass
class Host:
    address: str
    port: int
    host_key: asyncssh.SSHKey
    client_key: asyncssh.SSHKey
    stop: object


def public_line(key: asyncssh.SSHKey) -> str:
    return " ".join(key.export_public_key("openssh").decode().split()[:2])


def private_pem(key: asyncssh.SSHKey) -> str:
    return key.export_private_key("openssh").decode()


@pytest.fixture
def host() -> Iterator[Host]:
    """An SSH server that lets one key in and answers every command with `ran: <command>`."""
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    allowed = public_line(client_key)
    loop = asyncio.new_event_loop()
    state: dict = {}

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username: str) -> bool:
            return True

        def public_key_auth_supported(self) -> bool:
            return True

        def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
            return public_line(key) == allowed

    async def handle(process: asyncssh.SSHServerProcess) -> None:
        process.stdout.write(f"ran: {process.command}")
        process.exit(0)

    async def serve() -> None:
        state["server"] = await asyncssh.create_server(
            Server, "127.0.0.1", 0, server_host_keys=[host_key], process_factory=handle
        )
        state["port"] = state["server"].sockets[0].getsockname()[1]

    ready = threading.Event()

    def run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(serve())
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(10)

    def stop() -> None:
        loop.call_soon_threadsafe(state["server"].close)
        loop.call_soon_threadsafe(loop.stop)

    yield Host("127.0.0.1", state["port"], host_key, client_key, stop)
    stop()


def settings(host: Host, **changes) -> Settings:
    values = {
        "ws_host": host.address,
        "ws_port": host.port,
        "ws_user": "tester",
        "ws_ssh_key": private_pem(host.client_key),
        "ws_host_key": public_line(host.host_key),
    } | changes
    return Settings(_env_file=None, **values)


def run_command(config: Settings, command: str = "true") -> str:
    async def go() -> str:
        runner = SSHRunner(WorkstationTarget.from_settings(config))
        try:
            return (await runner.run(command, timeout_s=10)).stdout
        finally:
            await runner.close()

    return asyncio.run(go())


def test_the_server_logs_in_with_the_key_and_host_key_it_was_given(host: Host):
    assert run_command(settings(host), "hostname") == "ran: hostname"


def test_the_key_can_be_written_on_one_line_with_backslash_n(host: Host):
    one_line = private_pem(host.client_key).strip().replace("\n", "\\n")

    assert run_command(settings(host, ws_ssh_key=one_line)) == "ran: true"


def test_the_host_key_can_be_the_output_of_ssh_keyscan(host: Host):
    scanned = f"# {host.address}:22 SSH-2.0-test\n{host.address} {public_line(host.host_key)}\n"

    assert run_command(settings(host, ws_host_key=scanned)) == "ran: true"


def test_a_host_presenting_another_key_is_refused(host: Host):
    other = public_line(asyncssh.generate_private_key("ssh-ed25519"))

    with pytest.raises(asyncssh.Error):
        run_command(settings(host, ws_host_key=other))


def test_a_key_the_host_does_not_know_is_refused(host: Host):
    stranger = private_pem(asyncssh.generate_private_key("ssh-ed25519"))

    with pytest.raises(asyncssh.PermissionDenied):
        run_command(settings(host, ws_ssh_key=stranger))


def test_the_working_directory_defaults_to_icarus_in_the_users_home(host: Host):
    assert settings(host).compute_workdir == "/home/tester/icarus"
    assert settings(host, ws_workdir="/srv/icarus").compute_workdir == "/srv/icarus"
    assert WorkstationTarget.from_settings(settings(host)).workdir == "/home/tester/icarus"


def test_the_error_names_each_setting_that_is_missing(host: Host):
    config = settings(host, ws_ssh_key="", ws_host_key=None)

    with pytest.raises(HostNotConfigured, match="WS_SSH_KEY, WS_HOST_KEY"):
        WorkstationTarget.from_settings(config)
    with pytest.raises(HostNotConfigured, match="WS_HOST"):
        WorkstationTarget.from_settings(Settings(_env_file=None))


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"ws_ssh_key": "not a key"}, "WS_SSH_KEY"),
        ({"ws_host_key": "nonsense"}, "WS_HOST_KEY"),
    ],
)
def test_unusable_values_are_explained(host: Host, changes: dict, named: str):
    with pytest.raises(HostNotConfigured, match=named):
        WorkstationTarget.from_settings(settings(host, **changes))


def test_without_all_three_settings_there_is_no_compute_host(host: Host):
    missing = Compute._build_station(settings(host, ws_host_key=None))  # noqa: SLF001
    complete = Compute._build_station(settings(host))  # noqa: SLF001

    assert not missing.available and "WS_HOST_KEY" in missing.reason
    assert complete.available and complete.reason == ""
