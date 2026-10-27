"""Builds the long-lived services shared by every request."""

import logging
from dataclasses import dataclass

from pymongo.asynchronous.database import AsyncDatabase

from .agent.factory import AgentFactory
from .agent.models import Provider, server_keys
from .auth.repository import SessionRepository, UserRepository
from .chats.repository import ChatRepository
from .compute.pool import ComputePool
from .config import Settings
from .credentials.repository import CredentialRepository
from .credentials.vault import Vault
from .files.repository import FileRepository

logger = logging.getLogger(__name__)


@dataclass
class Services:
    chats: ChatRepository
    files: FileRepository
    users: UserRepository
    sessions: SessionRepository
    credentials: CredentialRepository
    # Each user's work station: their own, or the shared one.
    compute: ComputePool
    # Each user's agent: their model keys (or the server's) and their work station.
    agents: AgentFactory
    # The models the server itself supplies keys for, offered until a user adds their own.
    server_providers: list[Provider]

    async def close(self) -> None:
        await self.compute.close()


def build_services(settings: Settings, db: AsyncDatabase) -> Services:
    vault = Vault(settings.credentials_key)
    if not vault.available:
        logger.warning("CREDENTIALS_KEY is not set; users can't save their own keys or machines")
    credentials = CredentialRepository(db, vault)
    compute = ComputePool(db, settings, credentials)
    chats = ChatRepository(db)
    providers = sorted(server_keys(settings))
    logger.info("server model providers: %s", ", ".join(providers) or "none")
    logger.info("shared work station: %s", settings.ws_host or "not configured")
    return Services(
        chats=chats,
        files=FileRepository(db),
        users=UserRepository(db),
        sessions=SessionRepository(db),
        credentials=credentials,
        compute=compute,
        agents=AgentFactory(settings, chats, credentials, compute),
        server_providers=providers,
    )
