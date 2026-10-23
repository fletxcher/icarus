from datetime import UTC, datetime
from uuid import uuid4

from pymongo.asynchronous.database import AsyncDatabase

from ..catalog import FILES
from .schemas import StoredFile

# Only metadata by default; the text can be large.
METADATA = {"text": False}


class FileRepository:
    """Uploaded text files. Content lives with the metadata (files are small)."""

    def __init__(self, db: AsyncDatabase) -> None:
        self._files = db[FILES]

    async def create(self, name: str, content_type: str, text: str, size: int) -> StoredFile:
        doc = {
            "_id": str(uuid4()),
            "name": name,
            "size": size,
            "content_type": content_type,
            "text": text,
            "thread_id": None,
            "created_at": datetime.now(UTC),
        }
        await self._files.insert_one(doc)
        return StoredFile.model_validate(doc)

    async def get_many(self, file_ids: list[str]) -> list[StoredFile]:
        """The files that exist, in the order given."""
        cursor = self._files.find({"_id": {"$in": file_ids}}, METADATA)
        found = {doc["_id"]: StoredFile.model_validate(doc) async for doc in cursor}
        return [found[file_id] for file_id in file_ids if file_id in found]

    async def texts(self, file_ids: list[str]) -> dict[str, str]:
        cursor = self._files.find({"_id": {"$in": file_ids}}, {"text": True})
        return {doc["_id"]: doc["text"] async for doc in cursor}

    async def attach(self, file_ids: list[str], thread_id: str) -> None:
        await self._files.update_many(
            {"_id": {"$in": file_ids}}, {"$set": {"thread_id": thread_id}}
        )

    async def delete_unattached(self, file_id: str) -> bool:
        """Removes a file that hasn't been sent yet (e.g. taken out of the composer)."""
        result = await self._files.delete_one({"_id": file_id, "thread_id": None})
        return result.deleted_count == 1

    async def delete_for_thread(self, thread_id: str) -> None:
        await self._files.delete_many({"thread_id": thread_id})
