import logging
from datetime import UTC, datetime
from typing import Any, get_args
from uuid import uuid4

from pydantic import SecretStr
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from ..agent.models import Provider
from ..catalog import CREDENTIALS
from .schemas import ModelKeyInfo
from .vault import SealedValueRejected, Vault, VaultUnavailable

logger = logging.getLogger(__name__)

MODEL_KEY = "model_key"


class CredentialRepository:
    """A user's own model API keys, with the secrets encrypted at rest."""

    def __init__(self, db: AsyncDatabase, vault: Vault) -> None:
        self._credentials = db[CREDENTIALS]
        self.vault = vault

    async def _put(
        self, user_id: str, kind: str, name: str, secret: Any, details: dict[str, Any]
    ) -> datetime:
        now = datetime.now(UTC)
        await self._credentials.find_one_and_update(
            {"user_id": user_id, "kind": kind, "name": name},
            {
                "$set": {
                    "sealed": self.vault.seal(user_id, kind, name, secret),
                    "details": details,
                    "updated_at": now,
                },
                "$setOnInsert": {"_id": str(uuid4()), "created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return now

    async def _delete(self, user_id: str, kind: str, name: str | None = None) -> bool:
        query: dict[str, Any] = {"user_id": user_id, "kind": kind}
        if name is not None:
            query["name"] = name
        return (await self._credentials.delete_many(query)).deleted_count > 0

    # Model keys

    async def set_model_key(self, user_id: str, provider: Provider, api_key: str) -> None:
        await self._put(user_id, MODEL_KEY, provider, api_key, {"last4": api_key[-4:]})

    async def set_ollama(self, user_id: str, base_url: str) -> None:
        await self._put(user_id, MODEL_KEY, "ollama", base_url, {"last4": "", "url": base_url})

    async def list_model_keys(self, user_id: str) -> list[ModelKeyInfo]:
        # Keys of a provider the app no longer offers (e.g. Mistral's) stay in the database, unused.
        cursor = self._credentials.find(
            {"user_id": user_id, "kind": MODEL_KEY, "name": {"$in": list(get_args(Provider))}}
        ).sort("name", 1)
        return [
            ModelKeyInfo(
                provider=doc["name"],
                last4=doc["details"]["last4"],
                url=doc["details"].get("url"),
                updated_at=doc["updated_at"],
            )
            async for doc in cursor
        ]

    async def model_keys(self, user_id: str) -> dict[Provider, SecretStr]:
        """The user's decrypted keys by provider. A key that can't be opened is skipped."""
        keys: dict[Provider, SecretStr] = {}
        cursor = self._credentials.find(
            {"user_id": user_id, "kind": MODEL_KEY, "name": {"$in": list(get_args(Provider))}}
        )
        async for doc in cursor:
            try:
                value = self.vault.open(user_id, MODEL_KEY, doc["name"], doc["sealed"])
            except (SealedValueRejected, VaultUnavailable) as error:
                logger.warning("skipping %s key of user %s: %s", doc["name"], user_id, error)
                continue
            keys[doc["name"]] = SecretStr(value)
        return keys

    async def delete_model_key(self, user_id: str, provider: Provider) -> bool:
        return await self._delete(user_id, MODEL_KEY, provider)

    async def delete_all(self, user_id: str) -> None:
        await self._credentials.delete_many({"user_id": user_id})
