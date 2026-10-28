from datetime import UTC, datetime
from typing import NamedTuple
from uuid import uuid4

from bson import Binary
from pymongo.asynchronous.database import AsyncDatabase

from ..catalog import FILES
from .schemas import StoredFile


class FileText(NamedTuple):
    """What the model reads of a file, and how the file was read."""

    text: str
    kind: str


# Only metadata by default; the text can be large.
METADATA = {"text": False, "data": False}


class FileRepository:
    """Uploaded text files. Content lives with the metadata (files are small)."""

    def __init__(self, db: AsyncDatabase) -> None:
        self._files = db[FILES]

    async def create(
        self,
        user_id: str,
        name: str,
        content_type: str,
        text: str,
        size: int,
        *,
        kind: str = "text",
        data: bytes | None = None,
    ) -> StoredFile:
        """`text` is what the model reads; `data` is the original, kept for files that are
        summarized rather than pasted (meshes, CAD, PDFs)."""
        doc = {
            "_id": str(uuid4()),
            "user_id": user_id,
            "name": name,
            "size": size,
            "content_type": content_type,
            "text": text,
            "kind": kind,
            "thread_id": None,
            "created_at": datetime.now(UTC),
        }
        await self._files.insert_one(
            {**doc, **({"data": Binary(data)} if data is not None else {})}
        )
        return StoredFile.model_validate(doc)

    async def get_many(self, file_ids: list[str], user_id: str) -> list[StoredFile]:
        """The user's files that exist, in the order given."""
        cursor = self._files.find({"_id": {"$in": file_ids}, "user_id": user_id}, METADATA)
        found = {doc["_id"]: StoredFile.model_validate(doc) async for doc in cursor}
        return [found[file_id] for file_id in file_ids if file_id in found]

    async def texts(self, file_ids: list[str]) -> dict[str, FileText]:
        cursor = self._files.find({"_id": {"$in": file_ids}}, {"text": True, "kind": True})
        return {doc["_id"]: FileText(doc["text"], doc.get("kind", "text")) async for doc in cursor}

    async def original(self, file_id: str, user_id: str) -> tuple[StoredFile, bytes] | None:
        """The user's file with its original bytes (kept for meshes, CAD files and PDFs)."""
        doc = await self._files.find_one({"_id": file_id, "user_id": user_id})
        if doc is None or "data" not in doc:
            return None
        return StoredFile.model_validate(doc), bytes(doc["data"])

    async def attach(self, file_ids: list[str], thread_id: str, user_id: str) -> None:
        await self._files.update_many(
            {"_id": {"$in": file_ids}, "user_id": user_id}, {"$set": {"thread_id": thread_id}}
        )

    async def delete_unattached(self, file_id: str, user_id: str) -> bool:
        """Removes a file that hasn't been sent yet (e.g. taken out of the composer)."""
        result = await self._files.delete_one(
            {"_id": file_id, "user_id": user_id, "thread_id": None}
        )
        return result.deleted_count == 1

    async def delete_for_thread(self, thread_id: str) -> None:
        await self._files.delete_many({"thread_id": thread_id})
