import logging

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from .catalog import COLLECTIONS, FILES, MESSAGES, SESSIONS, SIMULATIONS, THREADS, USERS
from .config import Settings

__all__ = [
    "FILES",
    "MESSAGES",
    "SESSIONS",
    "SIMULATIONS",
    "THREADS",
    "USERS",
    "create_client",
    "ensure_schema",
]

logger = logging.getLogger(__name__)


def create_client(settings: Settings) -> AsyncMongoClient:
    # tz_aware so datetimes come back as UTC-aware, matching what we write.
    return AsyncMongoClient(settings.mongodb_uri, tz_aware=True)


async def ensure_schema(db: AsyncDatabase) -> None:
    """Applies the catalog: validators for enforced collections, then every index.

    Safe to run on every startup: validators are re-applied (`collMod`), indexes that
    already exist are left alone.
    """
    existing = set(await db.list_collection_names())
    for spec in COLLECTIONS:
        if spec.enforced:
            options = {
                "validator": spec.validator,
                "validationLevel": "strict",
                "validationAction": "error",
            }
            if spec.name in existing:
                await db.command("collMod", spec.name, **options)
            else:
                await db.create_collection(spec.name, **options)
                logger.info("created collection %s with a schema validator", spec.name)
        await db[spec.name].create_indexes(spec.indexes)
