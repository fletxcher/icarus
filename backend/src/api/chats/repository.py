from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from ..catalog import MESSAGES, THREADS
from ..files.schemas import Attachment
from .schemas import Message, MessagePart, MessageStatus, Role, Thread


def _now() -> datetime:
    return datetime.now(UTC)


class ChatRepository:
    """Persistence for threads and their messages."""

    def __init__(self, db: AsyncDatabase) -> None:
        self._threads = db[THREADS]
        self._messages = db[MESSAGES]

    async def list_threads(self) -> list[Thread]:
        cursor = self._threads.find().sort("updated_at", DESCENDING)
        return [Thread.model_validate(doc) async for doc in cursor]

    async def get_thread(self, thread_id: str) -> Thread | None:
        doc = await self._threads.find_one({"_id": thread_id})
        return Thread.model_validate(doc) if doc else None

    async def create_thread(self, title: str) -> Thread:
        now = _now()
        doc = {
            "_id": str(uuid4()),
            "title": title,
            "auto_title": True,
            "created_at": now,
            "updated_at": now,
            "message_seq": 0,
        }
        await self._threads.insert_one(doc)
        return Thread.model_validate(doc)

    async def update_thread(
        self, thread_id: str, *, title: str | None = None, pinned: bool | None = None
    ) -> Thread | None:
        changes: dict[str, Any] = {}
        if title is not None:
            # A title the user chose is never replaced by a generated one.
            changes["title"] = title
            changes["auto_title"] = False
        if pinned is not None:
            changes["pinned_at"] = _now() if pinned else None
        if not changes:
            return await self.get_thread(thread_id)
        doc = await self._threads.find_one_and_update(
            {"_id": thread_id}, {"$set": changes}, return_document=ReturnDocument.AFTER
        )
        return Thread.model_validate(doc) if doc else None

    async def set_auto_title(self, thread_id: str, title: str) -> bool:
        """Applies a generated title, unless the user has renamed the thread meanwhile."""
        result = await self._threads.update_one(
            {"_id": thread_id, "auto_title": True},
            {"$set": {"title": title, "auto_title": False}},
        )
        return result.modified_count == 1

    async def delete_thread(self, thread_id: str) -> bool:
        result = await self._threads.delete_one({"_id": thread_id})
        await self._messages.delete_many({"thread_id": thread_id})
        return result.deleted_count == 1

    async def list_messages(self, thread_id: str) -> list[Message]:
        cursor = self._messages.find({"thread_id": thread_id}).sort("seq", ASCENDING)
        return [Message.model_validate(doc) async for doc in cursor]

    async def add_message(
        self,
        thread_id: str,
        role: Role,
        content: str,
        *,
        status: MessageStatus = "done",
        model: str | None = None,
        attachments: list[Attachment] | None = None,
    ) -> Message:
        now = _now()
        # A per-thread counter orders messages; timestamps can collide within a millisecond.
        thread = await self._threads.find_one_and_update(
            {"_id": thread_id},
            {"$inc": {"message_seq": 1}, "$set": {"updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        if thread is None:
            raise LookupError(f"thread {thread_id} not found")
        doc = {
            "_id": str(uuid4()),
            "thread_id": thread_id,
            "seq": thread["message_seq"],
            "role": role,
            "content": content,
            "status": status,
            "model": model,
            "parts": [{"type": "text", "text": content}] if content else [],
            "attachments": [attachment.model_dump() for attachment in attachments or []],
            "created_at": now,
        }
        await self._messages.insert_one(doc)
        return Message.model_validate(doc)

    async def finish_message(
        self,
        message_id: str,
        *,
        content: str,
        status: MessageStatus,
        parts: list[MessagePart],
        error: str | None = None,
    ) -> None:
        await self._messages.update_one(
            {"_id": message_id},
            {
                "$set": {
                    "content": content,
                    "status": status,
                    "parts": [part.model_dump() for part in parts],
                    "error": error,
                }
            },
        )

    async def truncate_messages(self, thread_id: str, from_message_id: str) -> int:
        """Deletes a message and everything after it (used for edit and regenerate)."""
        anchor = await self._messages.find_one({"_id": from_message_id, "thread_id": thread_id})
        if anchor is None:
            return 0
        result = await self._messages.delete_many(
            {"thread_id": thread_id, "seq": {"$gte": anchor["seq"]}}
        )
        return result.deleted_count
