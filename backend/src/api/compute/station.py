"""The compute host: the one machine, reached over SSH, where XFOIL and part builds run."""

import logging
from dataclasses import dataclass
from typing import NoReturn

from pymongo.asynchronous.database import AsyncDatabase

from ..config import Settings
from ..design.service import DesignService
from ..files.repository import FileRepository
from .runner import (
    CommandResult,
    CommandRunner,
    HostNotConfigured,
    LocalRunner,
    SSHRunner,
    WorkstationTarget,
    via_exec,
)
from .xfoil import XfoilService

logger = logging.getLogger(__name__)


class UnconfiguredRunner:
    """Stands in for a compute host that isn't set up, failing with a clear message."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def _fail(self) -> NoReturn:
        raise RuntimeError(f"no compute host: {self._reason}")

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
    available: bool
    # Why there is no compute host, when there isn't.
    reason: str = ""


class Compute:
    def __init__(self, db: AsyncDatabase, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._station = self._build_station(settings)

    @staticmethod
    def _build_station(settings: Settings) -> Workstation:
        workdir = settings.compute_workdir
        try:
            target = WorkstationTarget.from_settings(settings)
        except HostNotConfigured as problem:
            return Workstation(UnconfiguredRunner(str(problem)), workdir, False, str(problem))
        return Workstation(SSHRunner(target), workdir, True)

    def set_station(self, station: Workstation) -> None:
        """Replaces the compute host (tests plug a fake one in)."""
        self._station = station

    @property
    def station(self) -> Workstation:
        return self._station

    def designs(self, user_id: str) -> DesignService:
        return DesignService(
            self._station.runner,
            workdir=self._station.workdir,
            files=FileRepository(self._db),
            user_id=user_id,
        )

    def xfoil(self) -> XfoilService:
        s = self._settings
        local = s.xfoil_runner == "local"
        return XfoilService(
            LocalRunner() if local else self._station.runner,
            command_prefix="" if local else via_exec(self._station.workdir),
            xfoil_path=s.xfoil_path,
            display=s.xfoil_display,
            timeout_s=s.xfoil_timeout_s,
            max_concurrency=s.xfoil_max_concurrency,
        )

    async def close(self) -> None:
        if isinstance(self._station.runner, SSHRunner):
            await self._station.runner.close()
