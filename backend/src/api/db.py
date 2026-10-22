from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from api.config import Settings

THREADS = "threads"
MESSAGES = "messages"
SIMULATIONS = "simulations"


def create_client(settings: Settings) -> AsyncMongoClient:
    # tz_aware so datetimes come back as UTC-aware, matching what we write.
    return AsyncMongoClient(settings.mongodb_uri, tz_aware=True)


async def ensure_indexes(db: AsyncDatabase) -> None:
    await db[THREADS].create_index([("updated_at", DESCENDING)])
    await db[MESSAGES].create_index([("thread_id", ASCENDING), ("seq", ASCENDING)], unique=True)
    await db[SIMULATIONS].create_index([("thread_id", ASCENDING), ("created_at", DESCENDING)])
