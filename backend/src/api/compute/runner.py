"""Where shell commands run: this server, or the simulation VM over SSH.

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
from typing import Protocol

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


def _expand(path: str) -> str:
    return str(Path(path).expanduser())


class CommandRunner(Protocol):
    async def run(
        self, command: str, *, cwd: str | None = None, timeout_s: float | None = None
    ) -> CommandResult: ...

    async def write_file(self, path: str, content: str) -> None: ...

    async def read_file(self, path: str) -> str:
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

    async def read_file(self, path: str) -> str:
        return await asyncio.to_thread(Path(path).read_text)

    async def make_temp_dir(self) -> str:
        return await asyncio.to_thread(tempfile.mkdtemp, prefix="icarus-")

    async def remove_dir(self, path: str) -> None:
        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)


class SSHRunner:
    """Runs commands on the simulation VM, reusing one SSH connection."""

    def __init__(self, settings: Settings) -> None:
        if not settings.vm_host:
            raise ValueError("VM_HOST must be set to run commands on the simulation VM")
        self._settings = settings
        self._connection: asyncssh.SSHClientConnection | None = None
        self._lock = asyncio.Lock()

    async def _connect(self) -> asyncssh.SSHClientConnection:
        async with self._lock:
            if self._connection is None or self._connection.is_closed():
                s = self._settings
                logger.info("connecting to VM %s@%s:%d", s.vm_user, s.vm_host, s.vm_port)
                self._connection = await asyncssh.connect(
                    s.vm_host,
                    port=s.vm_port,
                    username=s.vm_user,
                    # For both options () means the ~/.ssh defaults, while None would
                    # disable key auth / host key checking, so never pass None.
                    client_keys=[_expand(s.vm_ssh_key_path)] if s.vm_ssh_key_path else (),
                    known_hosts=_expand(s.vm_known_hosts_path) if s.vm_known_hosts_path else (),
                )
                logger.info("connected to VM %s", s.vm_host)
            return self._connection

    async def run(
        self, command: str, *, cwd: str | None = None, timeout_s: float | None = None
    ) -> CommandResult:
        connection = await self._connect()
        if cwd:
            command = f"cd {shlex.quote(cwd)} && {command}"
        logger.debug("vm run: %s", command)
        started = time.perf_counter()
        try:
            completed = await connection.run(command, check=False, timeout=timeout_s)
        except (asyncssh.Error, TimeoutError):
            logger.exception("vm command failed (connection error or timeout): %s", command)
            raise
        result = CommandResult(
            completed.exit_status if completed.exit_status is not None else -1,
            str(completed.stdout or ""),
            str(completed.stderr or ""),
        )
        _log_result("vm", command, result, started)
        return result

    async def write_file(self, path: str, content: str) -> None:
        connection = await self._connect()
        async with connection.start_sftp_client() as sftp, sftp.open(path, "w") as file:
            await file.write(content)

    async def read_file(self, path: str) -> str:
        connection = await self._connect()
        try:
            async with connection.start_sftp_client() as sftp, sftp.open(path, "r") as file:
                return await file.read()
        except asyncssh.SFTPNoSuchFile as error:
            raise FileNotFoundError(path) from error

    async def make_temp_dir(self) -> str:
        result = await self.run("mktemp -d -t icarus-XXXXXX")
        if not result.ok:
            raise RuntimeError(f"could not create temp dir on VM: {result.stderr.strip()}")
        return result.stdout.strip()

    async def remove_dir(self, path: str) -> None:
        await self.run(f"rm -rf {shlex.quote(path)}")

    async def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            await self._connection.wait_closed()
