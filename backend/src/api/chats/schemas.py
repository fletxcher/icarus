from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["user", "assistant"]
MessageStatus = Literal["streaming", "done", "stopped", "error"]


class MongoModel(BaseModel):
    """Reads Mongo's `_id` into `id`; responses still serialize it as `id`."""

    model_config = ConfigDict(validate_by_name=True)

    id: str = Field(validation_alias="_id")


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ReasoningPart(BaseModel):
    type: Literal["reasoning"] = "reasoning"
    text: str


class ToolPart(BaseModel):
    type: Literal["tool"] = "tool"
    id: str
    name: str
    args: dict[str, Any]
    status: Literal["running", "success", "error"] = "running"
    result: str | None = None


# A reply in the order it happened: reasoning, tool calls and text, possibly interleaved.
MessagePart = Annotated[TextPart | ReasoningPart | ToolPart, Field(discriminator="type")]


class Message(MongoModel):
    thread_id: str
    role: Role
    content: str
    status: MessageStatus
    model: str | None = None
    parts: list[MessagePart] = []
    # Why the reply failed, when status is "error".
    error: str | None = None
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
