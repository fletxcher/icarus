"""Each user's work station: the one they added, or the shared one the server provides.

Users who add a machine get their own SSH connection to it; everyone else shares the
server's (`WS_*`) one. Connections are kept open for reuse, up to `MAX_OWN_CONNECTIONS`;
the least recently used is closed to make room, and reopens on its next use.
"""

import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal, NoReturn

import asyncssh
from pymongo.asynchronous.database import AsyncDatabase

from ..config import Settings
from ..credentials.repository import CredentialRepository, StoredWorkstation
from ..files.repository import FileRepository
from .runner import CommandResult, CommandRunner, LocalRunner, SSHRunner, WorkstationTarget
from .simulations import SimulationService
from .xfoil import XfoilService

logger = logging.getLogger(__name__)

MAX_OWN_CONNECTIONS = 32

NOT_CONFIGURED = (
    "no simulation work station is available: the server has none (WS_HOST), "
    "and you haven't added your own in Settings"
)


class UnconfiguredRunner:
    """Stands in for a work station that doesn't exist, failing with a clear message."""

    def _fail(self) -> NoReturn:
        raise RuntimeError(NOT_CONFIGURED)

    async def run(
        self, command: str, *, cwd: str | None = None, timeout_s: float | None = None
    ) -> CommandResult:
        self._fail()

    async def write_file(self, path: str, content: str) -> None:
        self._fail()

    async def write_bytes(self, path: str, content: bytes) -> None:
        self._fail()

    async def read_file(self, path: str) -> str:
        self._fail()

    async def read_bytes(self, path: str) -> bytes:
        self._fail()

    async def make_temp_dir(self) -> str:
        self._fail()

    async def remove_dir(self, path: str) -> None:
        self._fail()


@dataclass(frozen=True)
class Workstation:
    runner: CommandRunner
    workdir: str
    script: str
    # "own": added by the user; "shared": the server's; "none": there isn't one.
    source: Literal["own", "shared", "none"]

    @property
    def available(self) -> bool:
        return self.source != "none"


def target_for(stored: StoredWorkstation) -> WorkstationTarget:
    """How to reach a user's saved work station: their key, and only the host key they approved."""
    details = stored.details
    host_entry = details.host if details.port == 22 else f"[{details.host}]:{details.port}"
    return WorkstationTarget(
        host=details.host,
        port=details.port,
        username=details.username,
        workdir=details.workdir,
        cfd_script=f"{details.workdir}/bin/run_openfoam.sh",
        own_key=asyncssh.import_private_key(stored.private_key.get_secret_value()),
        host_keys=asyncssh.import_known_hosts(f"{host_entry} {details.host_key}\n"),
    )


class ComputePool:
    def __init__(
        self, db: AsyncDatabase, settings: Settings, credentials: CredentialRepository
    ) -> None:
        self._db = db
        self._settings = settings
        self._credentials = credentials
        self._shared = (
            Workstation(
                SSHRunner(WorkstationTarget.from_settings(settings)),
                settings.ws_workdir,
                settings.ws_cfd_script,
                "shared",
            )
            if settings.ws_host
            else Workstation(
                UnconfiguredRunner(), settings.ws_workdir, settings.ws_cfd_script, "none"
            )
        )
        # user id -> (when the saved work station last changed, its runner)
        self._own: OrderedDict[str, tuple[str, SSHRunner]] = OrderedDict()

    def set_shared(self, station: Workstation) -> None:
        """Replaces the shared work station (tests plug a fake one in)."""
        self._shared = station

    @property
    def shared_available(self) -> bool:
        return self._shared.available

    async def workstation(self, user_id: str) -> Workstation:
        stored = await self._credentials.workstation(user_id)
        if stored is None:
            await self.forget(user_id)
            return self._shared
        version = stored.updated_at.isoformat()
        cached = self._own.get(user_id)
        if cached is not None and cached[0] == version:
            self._own.move_to_end(user_id)
            runner = cached[1]
        else:
            await self.forget(user_id)
            runner = SSHRunner(target_for(stored))
            self._own[user_id] = (version, runner)
            while len(self._own) > MAX_OWN_CONNECTIONS:
                _, (_, oldest) = self._own.popitem(last=False)
                await oldest.close()
        return Workstation(runner, runner.target.workdir, runner.target.cfd_script, "own")

    async def forget(self, user_id: str) -> None:
        """Closes the user's connection (their work station changed or was removed)."""
        cached = self._own.pop(user_id, None)
        if cached is not None:
            await cached[1].close()

    async def simulations_for(self, user_id: str) -> SimulationService:
        station = await self.workstation(user_id)
        return SimulationService(
            self._db,
            station.runner,
            user_id=user_id,
            workdir=station.workdir,
            script=station.script,
            files=FileRepository(self._db),
        )

    async def xfoil_for(self, user_id: str, station: Workstation | None = None) -> XfoilService:
        s = self._settings
        station = station or await self.workstation(user_id)
        return XfoilService(
            LocalRunner() if s.xfoil_runner == "local" else station.runner,
            xfoil_path=s.xfoil_path,
            display=s.xfoil_display,
            timeout_s=s.xfoil_timeout_s,
            max_concurrency=s.xfoil_max_concurrency,
        )

    async def close(self) -> None:
        for _, runner in self._own.values():
            await runner.close()
        self._own.clear()
        if isinstance(self._shared.runner, SSHRunner):
            await self._shared.runner.close()
