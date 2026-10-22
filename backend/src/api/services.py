"""Builds the long-lived services shared by every request."""

import logging
from dataclasses import dataclass
from typing import NoReturn

from langgraph.graph.state import CompiledStateGraph
from pymongo.asynchronous.database import AsyncDatabase

from api.agent.graph import build_agent
from api.agent.models import ModelOption, available_models, create_chat_model
from api.agent.tools import build_tools
from api.chats.repository import ChatRepository
from api.compute.runner import CommandResult, CommandRunner, LocalRunner, SSHRunner
from api.compute.simulations import SimulationService
from api.compute.xfoil import XfoilService
from api.config import Settings

logger = logging.getLogger(__name__)


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
    # Models whose provider has an API key; the only ones offered.
    models: list[ModelOption]
    default_model: str
    # None until a provider key is set; chat storage still works without it.
    agent: CompiledStateGraph | None

    async def close(self) -> None:
        if isinstance(self.vm, SSHRunner):
            await self.vm.close()


def build_services(settings: Settings, db: AsyncDatabase) -> Services:
    vm: CommandRunner = SSHRunner(settings) if settings.vm_host else UnconfiguredRunner()
    xfoil = XfoilService(
        LocalRunner() if settings.xfoil_runner == "local" else vm,
        xfoil_path=settings.xfoil_path,
        display=settings.xfoil_display,
        timeout_s=settings.xfoil_timeout_s,
        max_concurrency=settings.xfoil_max_concurrency,
    )
    simulations = SimulationService(
        db, vm, workdir=settings.vm_workdir, script=settings.vm_cfd_script
    )

    models = available_models(settings)
    agent = None
    default_model = settings.default_model
    if not models:
        logger.warning(
            "no model provider is configured (set MISTRAL_API_TOKEN or OPENROUTER_API_KEY)"
        )
    else:
        if default_model not in {option.id for option in models}:
            logger.warning(
                "DEFAULT_MODEL=%s isn't available (available: %s); using %s",
                default_model,
                ", ".join(option.id for option in models),
                models[0].id,
            )
            default_model = models[0].id
        logger.info("models available: %s", ", ".join(option.id for option in models))
        agent = build_agent(
            {option.id: create_chat_model(option, settings) for option in models},
            build_tools(xfoil, simulations),
            default_model=default_model,
        )

    return Services(
        chats=ChatRepository(db),
        simulations=simulations,
        vm=vm,
        models=models,
        default_model=default_model,
        agent=agent,
    )
