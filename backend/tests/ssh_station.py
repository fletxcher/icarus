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
MACHINE_PROBED = (
    "hostname=bench\nkernel=Linux\narch=x86_64\ncpus=8\nmem_kb=16777216\n"
    "disk_free_kb=104857600\nos_name=Ubuntu 24.04 LTS\ndocker=missing\nprivilege=password_sudo\n"
)
TOOLS_FOUND = "xfoil=1\nxvfb-run=1\ngmsh=0\npvbatch=1\nffmpeg=1\npython3=1\nopenfoam=1\n"


@dataclass
class FakeStation:
    host: str
    port: int
    host_key: asyncssh.SSHKey
    client_key: asyncssh.SSHKey
    root: Path
    commands: list[str] = field(default_factory=list)
    # What was sent on the standard input of commands that read it (the sudo password).
    stdins: list[str] = field(default_factory=list)
    sudo_password: str = "right"
    # The password the machine accepts for logging in (none: key logins only), and the public
    # keys (`ssh-ed25519 AAAA...`) added to its authorized_keys, by the person or by Icarus.
    password: str | None = None
    installed_keys: list[str] = field(default_factory=list)
    # What `setup.status` and `setup.log` hold.
    setup_status: str = ""
    setup_log: str = ""
    # What the log holds once setup has been launched.
    log_on_launch: str = ""
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
    station.installed_keys.append(
        " ".join(allowed.export_public_key("openssh").decode().split()[:2])
    )

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username: str) -> bool:
            return True

        def password_auth_supported(self) -> bool:
            return station.password is not None

        def validate_password(self, username: str, password: str) -> bool:
            return password == station.password

        def public_key_auth_supported(self) -> bool:
            return True

        def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
            line = " ".join(key.export_public_key("openssh").decode().split()[:2])
            return line in station.installed_keys

    async def handle(process: asyncssh.SSHServerProcess) -> None:
        command = process.command or ""
        station.commands.append(command)
        if 'echo >> "$HOME/.ssh/authorized_keys"' in command:
            line = next(part for part in shlex.split(command) if part.startswith("ssh-"))
            station.installed_keys.append(" ".join(line.split()[:2]))
        elif command.startswith("printf %s"):
            process.stdout.write(HOME)
        elif command.startswith("mkdir -p"):
            for path in shlex.split(command)[2:]:
                os.makedirs(root / path.lstrip("/"), exist_ok=True)
        elif command == "sudo -S -p '' -v":
            given = str(await process.stdin.read())
            station.stdins.append(given)
            process.exit(0 if given == f"{station.sudo_password}\n" else 1)
            return
        elif "setsid nohup bash -c" in command:
            if "read -r secret" in command:
                station.stdins.append(str(await process.stdin.read()))
            station.setup_log = station.log_on_launch
            process.stdout.write("started\n")
        elif "setup.status" in command and "echo ---" in command:
            process.stdout.write(f"{station.setup_status}\n---\n{station.setup_log}")
        elif command.endswith("icarus-exec' --status"):
            process.stdout.write("runtime=native\nimage=\nimage_present=0\n")
        elif "bash -c" in command and "hostname=" in command:
            process.stdout.write(MACHINE_PROBED)
        elif "bash -c" in command:
            process.stdout.write(TOOLS_FOUND)
        process.exit(0)

    def sftp(chan: asyncssh.SSHServerChannel) -> asyncssh.SFTPServer:
        return asyncssh.SFTPServer(chan, chroot=str(root).encode())

    loop = asyncio.new_event_loop()
    station._loop = loop

    async def serve() -> None:
        station._server = await asyncssh.create_server(
            Server,
            station.host,
            0,
            server_host_keys=[host_key],
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
