"""Builds the long-lived services shared by every request."""

from dataclasses import dataclass
from typing import NoReturn

from langchain_mistralai import ChatMistralAI
from langgraph.graph.state import CompiledStateGraph
from pymongo.asynchronous.database import AsyncDatabase

from backend.agent.graph import build_agent
from backend.agent.models import MODELS
from backend.agent.tools import build_tools
from backend.chats.repository import ChatRepository
from backend.compute.runner import CommandResult, CommandRunner, LocalRunner, SSHRunner
from backend.compute.simulations import SimulationService
from backend.compute.xfoil import XfoilService
from backend.config import Settings


def _vm_not_configured() -> NoReturn:
    raise RuntimeError("the simulation VM is not configured (set VM_HOST)")


class UnconfiguredRunner:
    """Stands in for the VM until VM_HOST is set, failing with a clear message."""

    async def run(
        self, command: str, *, cwd: str | None = None, timeout_s: float | None = None
    ) -> CommandResult:
        _vm_not_configured()

    async def write_file(self, path: str, content: str) -> None:
        _vm_not_configured()

    async def read_file(self, path: str) -> str:
        _vm_not_configured()

    async def make_temp_dir(self) -> str:
        _vm_not_configured()

    async def remove_dir(self, path: str) -> None:
        _vm_not_configured()


@dataclass
class Services:
    chats: ChatRepository
    simulations: SimulationService
    vm: CommandRunner
    # None until MISTRAL_API_TOKEN is set; chat storage still works without it.
    agent: CompiledStateGraph | None

    async def close(self) -> None:
        if isinstance(self.vm, SSHRunner):
            await self.vm.close()


def build_services(settings: Settings, db: AsyncDatabase) -> Services:
    vm: CommandRunner = SSHRunner(settings) if settings.vm_host else UnconfiguredRunner()
    xfoil = XfoilService(
        LocalRunner() if settings.xfoil_runner == "local" else vm,
        xfoil_path=settings.xfoil_path,
        timeout_s=settings.xfoil_timeout_s,
        max_concurrency=settings.xfoil_max_concurrency,
    )
    simulations = SimulationService(
        db, vm, workdir=settings.vm_workdir, script=settings.vm_cfd_script
    )

    agent = None
    if settings.mistral_api_token:
        models = {
            option.id: ChatMistralAI(
                model_name=option.id, api_key=settings.mistral_api_token, temperature=0.3
            )
            for option in MODELS
        }
        agent = build_agent(
            models, build_tools(xfoil, simulations), default_model=settings.default_model
        )

    return Services(chats=ChatRepository(db), simulations=simulations, vm=vm, agent=agent)
