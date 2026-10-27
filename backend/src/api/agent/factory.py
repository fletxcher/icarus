"""Builds the agent for one user: their model keys (or the server's), their work station."""

import logging
from dataclasses import dataclass

from langgraph.graph.state import CompiledStateGraph
from pydantic import SecretStr

from ..chats.repository import ChatRepository
from ..compute.pool import ComputePool
from ..config import Settings
from ..credentials.repository import CredentialRepository
from .capabilities import CFD_CAPABILITY, XFOIL_CAPABILITY, unavailable_capabilities
from .graph import build_agent
from .models import ModelOption, Provider, available_models, create_chat_model, server_keys
from .ollama import model_options
from .prompts import build_system_prompt
from .titles import TitleGenerator
from .tools import build_tools

logger = logging.getLogger(__name__)


@dataclass
class UserAgent:
    models: list[ModelOption]
    # None when no model is available.
    default_model: str | None
    agent: CompiledStateGraph | None
    # None when AUTO_TITLE is off or no model is available.
    titles: TitleGenerator | None


class AgentFactory:
    def __init__(
        self,
        settings: Settings,
        chats: ChatRepository,
        credentials: CredentialRepository,
        compute: ComputePool,
    ) -> None:
        self._settings = settings
        self._chats = chats
        self._credentials = credentials
        self._compute = compute
        # Tests plug in a scripted agent here; it's used for every user.
        self.override: UserAgent | None = None

    async def keys_for(self, user_id: str) -> dict[Provider, SecretStr]:
        """The server's keys, replaced provider by provider by the user's own."""
        return {**server_keys(self._settings), **await self._credentials.model_keys(user_id)}

    def _default_model(self, models: list[ModelOption]) -> str | None:
        ids = [option.id for option in models]
        if not ids:
            return None
        if self._settings.default_model in ids:
            return self._settings.default_model
        return ids[0]

    async def _models(self, keys: dict[Provider, SecretStr]) -> list[ModelOption]:
        models = available_models(keys)
        if "ollama" in keys:
            models += await model_options(keys["ollama"].get_secret_value())
        return models

    async def models_for(self, user_id: str) -> tuple[list[ModelOption], str | None]:
        """The models this user can use, and the one used when a message names none."""
        if self.override is not None:
            return self.override.models, self.override.default_model
        models = await self._models(await self.keys_for(user_id))
        return models, self._default_model(models)

    async def for_user(self, user_id: str) -> UserAgent:
        if self.override is not None:
            return self.override
        settings = self._settings
        keys = await self.keys_for(user_id)
        models = await self._models(keys)
        default_model = self._default_model(models)
        if default_model is None:
            return UserAgent(models, None, None, None)

        station = await self._compute.workstation(user_id)
        unavailable = unavailable_capabilities(settings, workstation=station.available)
        xfoil = (
            await self._compute.xfoil_for(user_id, station)
            if XFOIL_CAPABILITY not in unavailable
            else None
        )
        simulations = (
            await self._compute.simulations_for(user_id)
            if CFD_CAPABILITY not in unavailable
            else None
        )
        # Only tools that can run are given to the model; the prompt names the rest as missing.
        tools = build_tools(xfoil, simulations)
        chat_models = {
            option.id: create_chat_model(option, keys, reasoning_effort=settings.reasoning_effort)
            for option in models
        }
        agent = build_agent(
            chat_models,
            tools,
            default_model=default_model,
            system_prompt=build_system_prompt(tools, unavailable),
        )
        titles = TitleGenerator(self._chats, chat_models) if settings.auto_title else None
        return UserAgent(models, default_model, agent, titles)
