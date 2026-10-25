"""Builds the long-lived services shared by every request."""

import logging
import shutil
from dataclasses import dataclass
from typing import NoReturn

from langgraph.graph.state import CompiledStateGraph
from pymongo.asynchronous.database import AsyncDatabase

from .agent.graph import build_agent
from .agent.models import ModelOption, available_models, create_chat_model
from .agent.prompts import build_system_prompt
from .agent.titles import TitleGenerator
from .agent.tools import build_tools
from .chats.repository import ChatRepository
from .compute.runner import CommandResult, CommandRunner, LocalRunner, SSHRunner
from .compute.simulations import SimulationService
from .compute.xfoil import XfoilService
from .config import Settings
from .files.repository import FileRepository

logger = logging.getLogger(__name__)


def _ws_not_configured() -> NoReturn:
    raise RuntimeError("the simulation work station is not configured (set WS_HOST)")


class UnconfiguredRunner:
    """Stands in for the work station until WS_HOST is set, failing with a clear message."""

    async def run(
        self, command: str, *, cwd: str | None = None, timeout_s: float | None = None
    ) -> CommandResult:
        _ws_not_configured()

    async def write_file(self, path: str, content: str) -> None:
        _ws_not_configured()

    async def read_file(self, path: str) -> str:
        _ws_not_configured()

    async def make_temp_dir(self) -> str:
        _ws_not_configured()

    async def remove_dir(self, path: str) -> None:
        _ws_not_configured()


@dataclass
class Services:
    chats: ChatRepository
    files: FileRepository
    simulations: SimulationService
    ws: CommandRunner
    # Models whose provider has an API key; the only ones offered.
    models: list[ModelOption]
    default_model: str
    # None until a provider key is set; chat storage still works without it.
    agent: CompiledStateGraph | None
    # None when AUTO_TITLE is off or no provider is configured.
    titles: TitleGenerator | None

    async def close(self) -> None:
        if isinstance(self.ws, SSHRunner):
            await self.ws.close()


XFOIL_CAPABILITY = "XFOIL airfoil analysis and airfoil search"
CFD_CAPABILITY = "CFD simulations"


def xfoil_unavailable(settings: Settings) -> str | None:
    """Why XFOIL can't run with this setup, or None if it can."""
    if settings.xfoil_runner == "ws":
        return None if settings.ws_host else "the simulation work station isn't configured"
    if shutil.which(settings.xfoil_path) is None:
        return "XFOIL isn't installed on the server"
    if settings.xfoil_display == "xvfb" and shutil.which("xvfb-run") is None:
        return "xvfb isn't installed on the server, which XFOIL needs to run"
    return None


def unavailable_capabilities(settings: Settings) -> dict[str, str]:
    unavailable = {}
    if reason := xfoil_unavailable(settings):
        unavailable[XFOIL_CAPABILITY] = reason
    if not settings.ws_host:
        unavailable[CFD_CAPABILITY] = "the simulation work station isn't configured"
    return unavailable


def build_services(settings: Settings, db: AsyncDatabase) -> Services:
    ws: CommandRunner = SSHRunner(settings) if settings.ws_host else UnconfiguredRunner()
    xfoil = XfoilService(
        LocalRunner() if settings.xfoil_runner == "local" else ws,
        xfoil_path=settings.xfoil_path,
        display=settings.xfoil_display,
        timeout_s=settings.xfoil_timeout_s,
        max_concurrency=settings.xfoil_max_concurrency,
    )
    simulations = SimulationService(
        db, ws, workdir=settings.ws_workdir, script=settings.ws_cfd_script
    )

    # Only tools that can run are given to the model; the prompt names the rest as missing.
    unavailable = unavailable_capabilities(settings)
    for capability, reason in unavailable.items():
        logger.warning("%s unavailable: %s", capability, reason)
    tools = build_tools(
        xfoil if XFOIL_CAPABILITY not in unavailable else None,
        simulations if CFD_CAPABILITY not in unavailable else None,
    )
    logger.info("agent tools: %s", ", ".join(tool.name for tool in tools))

    models = available_models(settings)
    chats = ChatRepository(db)
    files = FileRepository(db)
    agent = None
    titles = None
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
        chat_models = {option.id: create_chat_model(option, settings) for option in models}
        agent = build_agent(
            chat_models,
            tools,
            default_model=default_model,
            system_prompt=build_system_prompt(tools, unavailable),
        )
        if settings.auto_title:
            titles = TitleGenerator(chats, chat_models)

    return Services(
        chats=chats,
        files=files,
        simulations=simulations,
        ws=ws,
        models=models,
        default_model=default_model,
        agent=agent,
        titles=titles,
    )
