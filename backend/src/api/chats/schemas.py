from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from ..files.schemas import Attachment
from ..mongo import MongoModel

Role = Literal["user", "assistant"]
MessageStatus = Literal["streaming", "done", "stopped", "error"]


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
    # Structured output for the UI (e.g. create_plot's Plotly figure); not sent to the model.
    artifact: dict[str, Any] | None = None


# A reply in the order it happened: reasoning, tool calls and text, possibly interleaved.
MessagePart = Annotated[TextPart | ReasoningPart | ToolPart, Field(discriminator="type")]


class Message(MongoModel):
    thread_id: str
    role: Role
    content: str
    status: MessageStatus
    model: str | None = None
    parts: list[MessagePart] = []
    attachments: list[Attachment] = []
    # Why the reply failed, when status is "error".
    error: str | None = None
    created_at: datetime


class Thread(MongoModel):
    title: str
    # True while the title is a placeholder that may be replaced by a generated one.
    auto_title: bool = False
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
    # Ids from POST /api/files, sent along with `content`.
    attachments: list[str] = Field(default=[], max_length=10)
