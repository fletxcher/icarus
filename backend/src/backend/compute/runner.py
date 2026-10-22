"""Where shell commands run: this server, or the simulation VM over SSH.

Tools depend on the `CommandRunner` protocol, so XFOIL can move between machines by
changing `XFOIL_RUNNER` without touching tool code.
"""

import asyncio
import shlex
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import asyncssh

from backend.config import Settings


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


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
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_s)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        return CommandResult(process.returncode or 0, stdout.decode(), stderr.decode())

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
                self._connection = await asyncssh.connect(
                    s.vm_host,
                    port=s.vm_port,
                    username=s.vm_user,
                    # For both options () means the ~/.ssh defaults, while None would
                    # disable key auth / host key checking, so never pass None.
                    client_keys=[_expand(s.vm_ssh_key_path)] if s.vm_ssh_key_path else (),
                    known_hosts=_expand(s.vm_known_hosts_path) if s.vm_known_hosts_path else (),
                )
            return self._connection

    async def run(
        self, command: str, *, cwd: str | None = None, timeout_s: float | None = None
    ) -> CommandResult:
        connection = await self._connect()
        if cwd:
            command = f"cd {shlex.quote(cwd)} && {command}"
        result = await connection.run(command, check=False, timeout=timeout_s)
        return CommandResult(
            result.exit_status if result.exit_status is not None else -1,
            str(result.stdout or ""),
            str(result.stderr or ""),
        )

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
