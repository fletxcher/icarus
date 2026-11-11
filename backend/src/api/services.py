"""Builds the long-lived services shared by every request."""

import logging
from dataclasses import dataclass

from pymongo.asynchronous.database import AsyncDatabase

from .agent.factory import AgentFactory
from .agent.models import Provider, server_keys
from .auth.repository import SessionRepository, UserRepository
from .chats.repository import ChatRepository
from .compute.station import Compute
from .config import Settings
from .credentials.repository import CredentialRepository
from .credentials.vault import Vault
from .files.repository import FileRepository
from .limits.repository import MessageLimiter

logger = logging.getLogger(__name__)


@dataclass
class Services:
    chats: ChatRepository
    files: FileRepository
    users: UserRepository
    sessions: SessionRepository
    # The daily limit on messages.
    limiter: MessageLimiter
    credentials: CredentialRepository
    # The compute host where XFOIL and part builds run.
    compute: Compute
    # Each user's agent: their model keys (or the server's) and the compute host.
    agents: AgentFactory
    # The models the server itself supplies keys for, offered until a user adds their own.
    server_providers: list[Provider]

    async def close(self) -> None:
        await self.compute.close()


def build_services(settings: Settings, db: AsyncDatabase) -> Services:
    vault = Vault(settings.credentials_key)
    if not vault.available:
        logger.warning("CREDENTIALS_KEY is not set; users can't save their own keys")
    credentials = CredentialRepository(db, vault)
    compute = Compute(db, settings)
    chats = ChatRepository(db)
    providers = sorted(server_keys(settings))
    logger.info("server model providers: %s", ", ".join(providers) or "none")
    station = compute.station
    logger.info(
        "compute host: %s",
        settings.ws_host if station.available else station.reason or "not configured",
    )
    return Services(
        chats=chats,
        files=FileRepository(db),
        users=UserRepository(db),
        sessions=SessionRepository(db),
        limiter=MessageLimiter(db, limit=settings.daily_message_limit),
        credentials=credentials,
        compute=compute,
        agents=AgentFactory(settings, chats, credentials, compute),
        server_providers=providers,
    )
