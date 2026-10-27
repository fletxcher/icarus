"""A small SSH server standing in for a user's work station in tests.

It accepts one key, answers the few commands the backend runs (making directories, the
home directory, the tool check) and serves SFTP from a temporary directory.
"""

import asyncio
import os
import shlex
import threading
from dataclasses import dataclass, field
from pathlib import Path

import asyncssh

HOME = "/home/tester"
# What the tool check finds on this fake machine: everything but gmsh.
TOOLS_FOUND = "xfoil=1\nxvfb-run=1\ngmsh=0\npvbatch=1\nffmpeg=1\npython3=1\nopenfoam=1\n"


@dataclass
class FakeStation:
    host: str
    port: int
    host_key: asyncssh.SSHKey
    client_key: asyncssh.SSHKey
    root: Path
    commands: list[str] = field(default_factory=list)
    _loop: asyncio.AbstractEventLoop | None = None
    _server: asyncssh.SSHAcceptor | None = None

    def stop(self) -> None:
        assert self._loop is not None and self._server is not None
        self._loop.call_soon_threadsafe(self._server.close)
        self._loop.call_soon_threadsafe(self._loop.stop)


def start(root: Path, *, authorized: asyncssh.SSHKey | None = None) -> FakeStation:
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    station = FakeStation("127.0.0.1", 0, host_key, client_key, root)
    allowed = authorized or client_key
    authorized_keys = asyncssh.import_authorized_keys(allowed.export_public_key("openssh").decode())

    async def handle(process: asyncssh.SSHServerProcess) -> None:
        command = process.command or ""
        station.commands.append(command)
        if command.startswith("printf %s"):
            process.stdout.write(HOME)
        elif command.startswith("mkdir -p"):
            for path in shlex.split(command)[2:]:
                os.makedirs(root / path.lstrip("/"), exist_ok=True)
        elif command.startswith("bash -c"):
            process.stdout.write(TOOLS_FOUND)
        process.exit(0)

    def sftp(chan: asyncssh.SSHServerChannel) -> asyncssh.SFTPServer:
        return asyncssh.SFTPServer(chan, chroot=str(root).encode())

    loop = asyncio.new_event_loop()
    station._loop = loop

    async def serve() -> None:
        station._server = await asyncssh.create_server(
            asyncssh.SSHServer,
            station.host,
            0,
            server_host_keys=[host_key],
            authorized_client_keys=authorized_keys,
            process_factory=handle,
            sftp_factory=sftp,
        )
        station.port = station._server.sockets[0].getsockname()[1]

    ready = threading.Event()

    def run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(serve())
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(10)
    return station


def private_pem(key: asyncssh.SSHKey) -> str:
    return key.export_private_key("openssh").decode()
