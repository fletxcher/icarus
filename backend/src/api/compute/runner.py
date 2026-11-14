"""Where shell commands run: this server, or the compute host over SSH.

Tools depend on the `CommandRunner` protocol, so XFOIL can move between machines by
changing `XFOIL_RUNNER` without touching tool code.
"""

import asyncio
import logging
import shlex
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import asyncssh

from ..config import Settings
from ..log_config import truncate

logger = logging.getLogger(__name__)

STDERR_LOG_CHARS = 2000


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def _log_result(where: str, command: str, result: "CommandResult", started: float) -> None:
    elapsed_ms = (time.perf_counter() - started) * 1000
    if result.ok:
        logger.info("%s command ok in %.0fms: %s", where, elapsed_ms, command)
    else:
        logger.warning(
            "%s command exited %d in %.0fms: %s\nstderr: %s",
            where,
            result.exit_code,
            elapsed_ms,
            command,
            truncate(result.stderr.strip(), STDERR_LOG_CHARS) or "(empty)",
        )


def via_exec(workdir: str) -> str:
    """The prefix that runs a command with the compute host's Python environment on the PATH: its
    `bin/icarus-exec`. Empty on a machine that doesn't have it, so the command runs as it is."""
    path = shlex.quote(f"{workdir}/bin/icarus-exec")
    return f"$(test -x {path} && echo {path}) "


class CommandRunner(Protocol):
    async def run(
        self, command: str, *, cwd: str | None = None, timeout_s: float | None = None
    ) -> CommandResult: ...

    async def write_file(self, path: str, content: str) -> None: ...

    async def write_bytes(self, path: str, content: bytes) -> None: ...

    async def read_file(self, path: str) -> str:
        """Raises FileNotFoundError if the file doesn't exist."""
        ...

    async def read_bytes(self, path: str) -> bytes:
        """Raises FileNotFoundError if the file doesn't exist."""
        ...

    async def make_temp_dir(self) -> str: ...

    async def remove_dir(self, path: str) -> None: ...


class LocalRunner:
    """Runs commands on the machine hosting the API."""

    async def run(
        self, command: str, *, cwd: str | None = None, timeout_s: float | None = None
    ) -> CommandResult:
        logger.debug("local run (cwd=%s): %s", cwd, command)
        started = time.perf_counter()
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_s)
        except TimeoutError:
            logger.error("local command timed out after %ss: %s", timeout_s, command)
            process.kill()
            await process.wait()
            raise
        result = CommandResult(process.returncode or 0, stdout.decode(), stderr.decode())
        _log_result("local", command, result, started)
        return result

    async def write_file(self, path: str, content: str) -> None:
        await asyncio.to_thread(Path(path).write_text, content)

    async def write_bytes(self, path: str, content: bytes) -> None:
        await asyncio.to_thread(Path(path).write_bytes, content)

    async def read_file(self, path: str) -> str:
        return await asyncio.to_thread(Path(path).read_text)

    async def read_bytes(self, path: str) -> bytes:
        return await asyncio.to_thread(Path(path).read_bytes)

    async def make_temp_dir(self) -> str:
        return await asyncio.to_thread(tempfile.mkdtemp, prefix="icarus-")

    async def remove_dir(self, path: str) -> None:
        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)


CONNECT_TIMEOUT_S = 15.0


class HostNotConfigured(ValueError):
    """The compute host's settings are missing or unusable; the message names what to fix."""


def _host_key_parts(text: str) -> tuple[str, str]:
    """The key type and key from what `ssh-keyscan` prints (`host type key`, with `#` comment
    lines) or from just `type key`."""
    for line in text.splitlines():
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        if len(words) >= 3 and not words[0].startswith(("ssh-", "ecdsa-", "sk-")):
            words = words[1:]  # the host name or address in front
        if len(words) >= 2:
            return words[0], words[1]
    raise HostNotConfigured("WS_HOST_KEY isn't a host key: use the output of ssh-keyscan")


@dataclass(frozen=True)
class WorkstationTarget:
    """Where and how to reach the compute host, and where Icarus's files live on it."""

    host: str
    port: int
    username: str
    workdir: str
    key: asyncssh.SSHKey
    host_keys: asyncssh.SSHKnownHosts

    @classmethod
    def from_settings(cls, settings: Settings) -> "WorkstationTarget":
        """Raises HostNotConfigured, saying what is missing, if the settings can't reach a host."""
        missing = [
            name
            for name, value in (
                ("WS_HOST", settings.ws_host),
                ("WS_SSH_KEY", settings.ws_ssh_key),
                ("WS_HOST_KEY", settings.ws_host_key),
            )
            if not value
        ]
        if missing:
            raise HostNotConfigured(f"not set up on the server: {', '.join(missing)}")
        assert settings.ws_host and settings.ws_ssh_key and settings.ws_host_key
        pem = settings.ws_ssh_key.get_secret_value().strip()
        if "\n" not in pem:
            pem = pem.replace("\\n", "\n")  # a key written on one line, with \n for line breaks
        try:
            key = asyncssh.import_private_key(pem + "\n")
        except (asyncssh.KeyImportError, asyncssh.KeyEncryptionError, ValueError):
            raise HostNotConfigured(
                "WS_SSH_KEY isn't a usable private key (it must not have a passphrase)"
            ) from None
        key_type, host_key = _host_key_parts(settings.ws_host_key)
        entry = (
            settings.ws_host
            if settings.ws_port == 22
            else f"[{settings.ws_host}]:{settings.ws_port}"
        )
        try:
            host_keys = asyncssh.import_known_hosts(f"{entry} {key_type} {host_key}\n")
        except (asyncssh.KeyImportError, ValueError):
            raise HostNotConfigured("WS_HOST_KEY isn't a usable host key") from None
        return cls(
            host=settings.ws_host,
            port=settings.ws_port,
            username=settings.ws_user,
            workdir=settings.compute_workdir,
            key=key,
            host_keys=host_keys,
        )

    def connect_options(self) -> dict[str, Any]:
        """The key given and the host key approved, and nothing from ~/.ssh on this server."""
        return {
            "client_keys": [self.key],
            "known_hosts": self.host_keys,
            "preferred_auth": "publickey",
            "config": None,
            "agent_path": None,
            "agent_forwarding": False,
        }


async def connect(target: WorkstationTarget) -> asyncssh.SSHClientConnection:
    return await asyncssh.connect(
        target.host,
        port=target.port,
        username=target.username,
        connect_timeout=CONNECT_TIMEOUT_S,
        login_timeout=CONNECT_TIMEOUT_S,
        **target.connect_options(),
    )


class SSHRunner:
    """Runs commands on the compute host, reusing one SSH connection."""

    def __init__(self, target: WorkstationTarget) -> None:
        self.target = target
        self._connection: asyncssh.SSHClientConnection | None = None
        self._lock = asyncio.Lock()

    async def _connect(self) -> asyncssh.SSHClientConnection:
        async with self._lock:
            if self._connection is None or self._connection.is_closed():
                t = self.target
                logger.info("connecting to compute host %s@%s:%d", t.username, t.host, t.port)
                self._connection = await connect(t)
                logger.info("connected to compute host %s", t.host)
            return self._connection

    async def run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout_s: float | None = None,
    ) -> CommandResult:
        connection = await self._connect()
        if cwd:
            command = f"cd {shlex.quote(cwd)} && {command}"
        logger.debug("ws run: %s", command)
        started = time.perf_counter()
        try:
            completed = await connection.run(command, check=False, timeout=timeout_s)
        except (asyncssh.Error, TimeoutError):
            logger.exception("ws command failed (connection error or timeout): %s", command)
            raise
        result = CommandResult(
            completed.exit_status if completed.exit_status is not None else -1,
            str(completed.stdout or ""),
            str(completed.stderr or ""),
        )
        _log_result("ws", command, result, started)
        return result

    async def write_file(self, path: str, content: str) -> None:
        connection = await self._connect()
        async with connection.start_sftp_client() as sftp, sftp.open(path, "w") as file:
            await file.write(content)

    async def write_bytes(self, path: str, content: bytes) -> None:
        connection = await self._connect()
        async with connection.start_sftp_client() as sftp, sftp.open(path, "wb") as file:
            await file.write(content)

    async def read_file(self, path: str) -> str:
        connection = await self._connect()
        try:
            async with connection.start_sftp_client() as sftp, sftp.open(path, "r") as file:
                return await file.read()
        except asyncssh.SFTPNoSuchFile as error:
            raise FileNotFoundError(path) from error

    async def read_bytes(self, path: str) -> bytes:
        connection = await self._connect()
        try:
            async with connection.start_sftp_client() as sftp, sftp.open(path, "rb") as file:
                return await file.read()
        except asyncssh.SFTPNoSuchFile as error:
            raise FileNotFoundError(path) from error

    async def make_temp_dir(self) -> str:
        result = await self.run("mktemp -d -t icarus-XXXXXX")
        if not result.ok:
            raise RuntimeError(
                f"could not create temp dir on compute host: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    async def remove_dir(self, path: str) -> None:
        await self.run(f"rm -rf {shlex.quote(path)}")

    async def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            await self._connection.wait_closed()
