import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langgraph.graph.state import CompiledStateGraph

from ..agent.stream import stream_turn, title_first
from ..dependencies import ServicesDep
from .schemas import (
    MessageCreate,
    Thread,
    ThreadCreate,
    ThreadUpdate,
    ThreadWithMessages,
)

router = APIRouter(prefix="/threads", tags=["threads"])

# How long a chat's first reply is held so its new title can be shown first.
TITLE_WAIT_S = 5.0


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")


@router.get("")
async def list_threads(services: ServicesDep) -> list[Thread]:
    return await services.chats.list_threads()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_thread(body: ThreadCreate, services: ServicesDep) -> Thread:
    return await services.chats.create_thread(body.title)


@router.get("/{thread_id}")
async def get_thread(thread_id: str, services: ServicesDep) -> ThreadWithMessages:
    thread = await services.chats.get_thread(thread_id)
    if thread is None:
        raise _not_found()
    messages = await services.chats.list_messages(thread_id)
    return ThreadWithMessages(**thread.model_dump(), messages=messages)


@router.patch("/{thread_id}")
async def update_thread(thread_id: str, body: ThreadUpdate, services: ServicesDep) -> Thread:
    thread = await services.chats.update_thread(thread_id, title=body.title, pinned=body.pinned)
    if thread is None:
        raise _not_found()
    return thread


@router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(thread_id: str, services: ServicesDep) -> None:
    if not await services.chats.delete_thread(thread_id):
        raise _not_found()
    await services.files.delete_for_thread(thread_id)


@router.delete("/{thread_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def truncate_messages(thread_id: str, message_id: str, services: ServicesDep) -> None:
    """Deletes a message and every later one, before an edit or regenerate."""
    if not await services.chats.truncate_messages(thread_id, message_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")


@dataclass(frozen=True)
class Turn:
    agent: CompiledStateGraph
    model: str
    # Running when this turn starts a chat that still has a placeholder title.
    title: asyncio.Task[str | None] | None = None


async def start_turn(thread_id: str, body: MessageCreate, services: ServicesDep) -> Turn:
    """Validates the request and stores the user's message.

    Runs as a dependency because once an SSE response starts streaming, errors can no
    longer change its status code.
    """
    if services.agent is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No model provider is configured (set MISTRAL_API_TOKEN or OPENROUTER_API_KEY)",
        )
    thread = await services.chats.get_thread(thread_id)
    if thread is None:
        raise _not_found()

    model = body.model or services.default_model
    if model not in {option.id for option in services.models}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown model {model}")

    if body.attachments and body.content is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Attachments need a message to go with them"
        )
    file_ids = list(dict.fromkeys(body.attachments))
    attachments = await services.files.get_many(file_ids)
    if len(attachments) != len(file_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown attachment")

    if body.content is not None:
        await services.chats.add_message(
            thread_id,
            "user",
            body.content,
            attachments=[file.as_attachment() for file in attachments],
        )
        await services.files.attach(file_ids, thread_id)
    messages = await services.chats.list_messages(thread_id)
    if not messages or messages[-1].role != "user":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Nothing to regenerate: the last message isn't a user's"
        )

    # Name the chat from its first message, alongside the reply.
    title = None
    if services.titles and thread.auto_title and len(messages) == 1:
        title = services.titles.start(thread_id, model, messages[0].content)
    return Turn(agent=services.agent, model=model, title=title)


@router.post("/{thread_id}/messages", response_class=EventSourceResponse)
async def send_message(
    thread_id: str, turn: Annotated[Turn, Depends(start_turn)], services: ServicesDep
) -> AsyncIterator[ServerSentEvent]:
    """Adds a user message (omit `content` to regenerate) and streams the assistant's reply."""
    events = stream_turn(
        turn.agent, services.chats, services.files, thread_id=thread_id, model=turn.model
    )
    if turn.title is not None:
        events = title_first(events, turn.title, wait_s=TITLE_WAIT_S)
    async for event in events:
        yield event
