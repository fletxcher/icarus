from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["user", "assistant"]
MessageStatus = Literal["streaming", "done", "stopped", "error"]


class MongoModel(BaseModel):
    """Reads Mongo's `_id` into `id`; responses still serialize it as `id`."""

    model_config = ConfigDict(validate_by_name=True)

    id: str = Field(validation_alias="_id")


class ToolCall(BaseModel):
    id: str
    name: str
    args: dict[str, Any]
    result: str | None = None


class Message(MongoModel):
    thread_id: str
    role: Role
    content: str
    status: MessageStatus
    model: str | None = None
    tool_calls: list[ToolCall] = []
    created_at: datetime


class Thread(MongoModel):
    title: str
    created_at: datetime
    updated_at: datetime
    pinned_at: datetime | None = None


class ThreadWithMessages(Thread):
    messages: list[Message]


class ThreadCreate(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=200)


class ThreadUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None


class MessageCreate(BaseModel):
    """A new user turn. Omit `content` to regenerate a reply to the existing history."""

    content: str | None = Field(default=None, min_length=1)
    model: str | None = None
