from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langgraph.graph.state import CompiledStateGraph

from backend.agent.models import MODEL_IDS
from backend.agent.stream import stream_turn
from backend.chats.schemas import (
    MessageCreate,
    Thread,
    ThreadCreate,
    ThreadUpdate,
    ThreadWithMessages,
)
from backend.config import get_settings
from backend.dependencies import ServicesDep

router = APIRouter(prefix="/threads", tags=["threads"])


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


@router.delete("/{thread_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def truncate_messages(thread_id: str, message_id: str, services: ServicesDep) -> None:
    """Deletes a message and every later one, before an edit or regenerate."""
    if not await services.chats.truncate_messages(thread_id, message_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")


@dataclass(frozen=True)
class Turn:
    agent: CompiledStateGraph
    model: str


async def start_turn(thread_id: str, body: MessageCreate, services: ServicesDep) -> Turn:
    """Validates the request and stores the user's message.

    Runs as a dependency because once an SSE response starts streaming, errors can no
    longer change its status code.
    """
    if services.agent is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "MISTRAL_API_TOKEN is not set")
    if await services.chats.get_thread(thread_id) is None:
        raise _not_found()

    model = body.model or get_settings().default_model
    if model not in MODEL_IDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown model {model}")

    if body.content is not None:
        await services.chats.add_message(thread_id, "user", body.content)
    else:
        messages = await services.chats.list_messages(thread_id)
        if not messages or messages[-1].role != "user":
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Nothing to regenerate: the last message isn't a user's"
            )
    return Turn(agent=services.agent, model=model)


@router.post("/{thread_id}/messages", response_class=EventSourceResponse)
async def send_message(
    thread_id: str, turn: Annotated[Turn, Depends(start_turn)], services: ServicesDep
) -> AsyncIterator[ServerSentEvent]:
    """Adds a user message (omit `content` to regenerate) and streams the assistant's reply."""
    async for event in stream_turn(
        turn.agent, services.chats, thread_id=thread_id, model=turn.model
    ):
        yield event
