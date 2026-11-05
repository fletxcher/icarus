import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import SecretStr
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from ..agent.models import Provider
from ..catalog import CREDENTIALS
from .schemas import Hardware, ModelKeyInfo, WorkstationDetails, WorkstationInfo
from .vault import SealedValueRejected, Vault, VaultUnavailable

logger = logging.getLogger(__name__)

MODEL_KEY = "model_key"
WORKSTATION = "workstation"


@dataclass(frozen=True)
class StoredWorkstation:
    id: str
    details: WorkstationDetails
    private_key: SecretStr
    updated_at: datetime


class CredentialRepository:
    """A user's own model API keys and work station, with the secrets encrypted at rest."""

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
        cursor = self._credentials.find({"user_id": user_id, "kind": MODEL_KEY}).sort("name", 1)
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
        async for doc in self._credentials.find({"user_id": user_id, "kind": MODEL_KEY}):
            try:
                value = self.vault.open(user_id, MODEL_KEY, doc["name"], doc["sealed"])
            except (SealedValueRejected, VaultUnavailable) as error:
                logger.warning("skipping %s key of user %s: %s", doc["name"], user_id, error)
                continue
            keys[doc["name"]] = SecretStr(value)
        return keys

    async def delete_model_key(self, user_id: str, provider: Provider) -> bool:
        return await self._delete(user_id, MODEL_KEY, provider)

    # Work stations: a user can save several machines; at most one is connected (active)

    @staticmethod
    def _info(doc: dict[str, Any]) -> WorkstationInfo:
        details = doc["details"]
        return WorkstationInfo(
            **WorkstationDetails(**details).model_dump(),
            id=doc["name"],
            # Saved before there could be several: the one machine was the connected one.
            active=details.get("active", True),
            status=details.get("status", "ready"),
            status_message=details.get("status_message", ""),
            updated_at=doc["updated_at"],
        )

    async def add_workstation(
        self,
        user_id: str,
        details: WorkstationDetails,
        private_key: str,
        *,
        machine_id: str | None = None,
    ) -> WorkstationInfo:
        """Saves a machine (or, with `machine_id`, replaces that one) and connects it, so
        any other machine is disconnected."""
        machine_id = machine_id or str(uuid4())
        await self._credentials.update_many(
            {"user_id": user_id, "kind": WORKSTATION}, {"$set": {"details.active": False}}
        )
        await self._put(
            user_id,
            WORKSTATION,
            machine_id,
            private_key,
            {**details.model_dump(), "active": True, "status": "ready", "status_message": ""},
        )
        info = await self.workstation_info(user_id, machine_id)
        assert info is not None
        return info

    async def list_workstations(self, user_id: str) -> list[WorkstationInfo]:
        cursor = self._credentials.find({"user_id": user_id, "kind": WORKSTATION}).sort(
            "created_at", 1
        )
        return [self._info(doc) async for doc in cursor]

    async def workstation_info(self, user_id: str, machine_id: str) -> WorkstationInfo | None:
        doc = await self._credentials.find_one(
            {"user_id": user_id, "kind": WORKSTATION, "name": machine_id}
        )
        return None if doc is None else self._info(doc)

    async def _open(self, user_id: str, doc: dict[str, Any]) -> StoredWorkstation | None:
        try:
            key = self.vault.open(user_id, WORKSTATION, doc["name"], doc["sealed"])
        except (SealedValueRejected, VaultUnavailable) as error:
            logger.warning(
                "ignoring the work station %s of user %s: %s", doc["name"], user_id, error
            )
            return None
        return StoredWorkstation(
            doc["name"], WorkstationDetails(**doc["details"]), SecretStr(key), doc["updated_at"]
        )

    async def workstation(self, user_id: str) -> StoredWorkstation | None:
        """The connected machine with its decrypted key; None if none is connected (or its key
        can't be opened, in which case the shared one is used)."""
        doc = await self._credentials.find_one(
            {
                "user_id": user_id,
                "kind": WORKSTATION,
                "details.active": {"$ne": False},
                "details.status": {"$ne": "removing"},
            }
        )
        return None if doc is None else await self._open(user_id, doc)

    async def stored_workstation(self, user_id: str, machine_id: str) -> StoredWorkstation | None:
        """A saved machine by id with its decrypted key, connected or not."""
        doc = await self._credentials.find_one(
            {"user_id": user_id, "kind": WORKSTATION, "name": machine_id}
        )
        return None if doc is None else await self._open(user_id, doc)

    async def _set_details(self, user_id: str, machine_id: str, **fields: Any) -> bool:
        """Changes some details of a machine without touching its key or `updated_at`, which
        connections are cached by."""
        result = await self._credentials.update_one(
            {"user_id": user_id, "kind": WORKSTATION, "name": machine_id},
            {"$set": {f"details.{name}": value for name, value in fields.items()}},
        )
        return result.matched_count > 0

    async def connect_workstation(self, user_id: str, machine_id: str) -> bool:
        """Makes this machine the connected one. False if it doesn't exist or is being removed."""
        doc = await self._credentials.find_one(
            {"user_id": user_id, "kind": WORKSTATION, "name": machine_id}
        )
        if doc is None or doc["details"].get("status") == "removing":
            return False
        await self._credentials.update_many(
            {"user_id": user_id, "kind": WORKSTATION}, {"$set": {"details.active": False}}
        )
        return await self._set_details(user_id, machine_id, active=True)

    async def disconnect_workstations(self, user_id: str) -> None:
        await self._credentials.update_many(
            {"user_id": user_id, "kind": WORKSTATION}, {"$set": {"details.active": False}}
        )

    async def set_hardware(self, user_id: str, machine_id: str, hardware: Hardware) -> None:
        await self._set_details(user_id, machine_id, hardware=hardware.model_dump())

    async def set_workstation_status(
        self, user_id: str, machine_id: str, state: str, message: str = ""
    ) -> bool:
        """Records how a machine's removal is going; a machine being removed isn't connected."""
        fields: dict[str, Any] = {"status": state, "status_message": message}
        if state == "removing":
            fields["active"] = False
        return await self._set_details(user_id, machine_id, **fields)

    async def delete_workstation(self, user_id: str, machine_id: str) -> bool:
        return await self._delete(user_id, WORKSTATION, machine_id)

    async def delete_all(self, user_id: str) -> None:
        await self._credentials.delete_many({"user_id": user_id})
