import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langgraph.graph.state import CompiledStateGraph

from ..agent.factory import UserAgent
from ..agent.stream import stream_turn, title_first
from ..auth.router import CurrentUser
from ..auth.schemas import User
from ..compute.describe import running_note
from ..dependencies import ServicesDep
from ..design.service import designs_note
from ..files.schemas import StoredFile
from ..limits.repository import WINDOW, LimitReached
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
async def list_threads(services: ServicesDep, user: CurrentUser) -> list[Thread]:
    return await services.chats.list_threads(user.id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_thread(body: ThreadCreate, services: ServicesDep, user: CurrentUser) -> Thread:
    return await services.chats.create_thread(user.id, body.title)


@router.get("/{thread_id}")
async def get_thread(
    thread_id: str, services: ServicesDep, user: CurrentUser
) -> ThreadWithMessages:
    thread = await services.chats.get_thread(thread_id, user.id)
    if thread is None:
        raise _not_found()
    messages = await services.chats.list_messages(thread_id)
    return ThreadWithMessages(**thread.model_dump(), messages=messages)


@router.patch("/{thread_id}")
async def update_thread(
    thread_id: str, body: ThreadUpdate, services: ServicesDep, user: CurrentUser
) -> Thread:
    thread = await services.chats.update_thread(
        thread_id, user.id, title=body.title, pinned=body.pinned
    )
    if thread is None:
        raise _not_found()
    return thread


@router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(thread_id: str, services: ServicesDep, user: CurrentUser) -> None:
    if not await services.chats.delete_thread(thread_id, user.id):
        raise _not_found()
    await services.files.delete_for_thread(thread_id)
    # The one way a simulation is really deleted: with the chat it was started in.
    simulations = await services.compute.simulations_for(user.id)
    await simulations.delete_for_thread(thread_id)


@router.delete("/{thread_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def truncate_messages(
    thread_id: str, message_id: str, services: ServicesDep, user: CurrentUser
) -> None:
    """Deletes a message and every later one, before an edit or regenerate."""
    if await services.chats.get_thread(thread_id, user.id) is None:
        raise _not_found()
    if not await services.chats.truncate_messages(thread_id, message_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")


@dataclass(frozen=True)
class Turn:
    agent: CompiledStateGraph
    model: str
    # What the agent is told about this chat's running simulations and designed parts (see
    # compute/describe.py and design/service.py).
    situation: str | None = None
    # Running when this turn starts a chat that still has a placeholder title.
    title: asyncio.Task[str | None] | None = None
    # Gives the counted message back; None when this turn isn't counted.
    refund: Callable[[], Awaitable[None]] | None = None


async def start_turn(
    thread_id: str, body: MessageCreate, services: ServicesDep, user: CurrentUser
) -> Turn:
    """Validates the request and stores the user's message.

    Runs as a dependency because once an SSE response starts streaming, errors can no
    longer change its status code.
    """
    thread = await services.chats.get_thread(thread_id, user.id)
    if thread is None:
        raise _not_found()
    setup = await services.agents.for_user(user.id)
    if setup.agent is None or setup.default_model is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No model is available. Add your own API key in Settings, under API keys.",
        )

    model = body.model or setup.default_model
    if model not in {option.id for option in setup.models}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown model {model}")

    if body.attachments and body.content is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Attachments need a message to go with them"
        )
    file_ids = list(dict.fromkeys(body.attachments))
    attachments = await services.files.get_many(file_ids, user.id)
    if len(attachments) != len(file_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown attachment")

    refund = await count_message(services, user, setup, model)
    try:
        turn = await store_and_build_turn(
            thread_id, body, services, user, thread, setup, model, attachments, file_ids
        )
    except Exception:
        # Nothing was answered, so the message isn't used up.
        if refund:
            await refund()
        raise
    return replace(turn, refund=refund)


async def count_message(
    services: ServicesDep, user: User, setup: UserAgent, model: str
) -> Callable[[], Awaitable[None]] | None:
    """Counts this turn against the user's daily limit (429 when it's used up) and returns what
    gives it back, or None when it isn't counted: no limit, an admin, or a model the user
    reaches with their own key."""
    limiter = services.limiter
    if limiter.unlimited or user.role == "admin":
        return None
    provider = next(option.provider for option in setup.models if option.id == model)
    own = {key.provider for key in await services.credentials.list_model_keys(user.id)}
    if provider in own:
        return None
    try:
        claim = await limiter.claim(user.id)
    except LimitReached as reached:
        wait = max(int((reached.resets_at - datetime.now(UTC)).total_seconds()), 1)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You've used your {limiter.limit} messages for the last "
            f"{int(WINDOW.total_seconds() // 3600)} hours. Add your own API key in Settings "
            "for unlimited messages, or try again later.",
            headers={"Retry-After": str(wait)},
        ) from None
    return partial(limiter.refund, user.id, claim)


async def store_and_build_turn(
    thread_id: str,
    body: MessageCreate,
    services: ServicesDep,
    user: User,
    thread: Thread,
    setup: UserAgent,
    model: str,
    attachments: list[StoredFile],
    file_ids: list[str],
) -> Turn:
    """Stores the user's message (if there's one) and gathers what the reply needs."""
    assert setup.agent is not None
    if body.content is not None:
        await services.chats.add_message(
            thread_id,
            "user",
            body.content,
            attachments=[file.as_attachment() for file in attachments],
        )
        await services.files.attach(file_ids, thread_id, user.id)
    messages = await services.chats.list_messages(thread_id)
    if not messages or messages[-1].role == "assistant":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Nothing to regenerate: the last message isn't a user's"
        )
    simulations = await services.compute.simulations_for(user.id)
    notes = [
        running_note(await simulations.running_in_thread(thread_id)),
        designs_note(await services.files.designs_in_thread(thread_id, user.id)),
    ]
    situation = "\n\n".join(note for note in notes if note) or None

    # Name the chat from its first message, alongside the reply.
    title = None
    if setup.titles and thread.auto_title and len(messages) == 1:
        title = setup.titles.start(thread_id, model, messages[0].content)
    return Turn(agent=setup.agent, model=model, situation=situation, title=title)


@router.post("/{thread_id}/messages", response_class=EventSourceResponse)
async def send_message(
    thread_id: str, turn: Annotated[Turn, Depends(start_turn)], services: ServicesDep
) -> AsyncIterator[ServerSentEvent]:
    """Adds a user message (omit `content` to regenerate) and streams the assistant's reply."""
    events = stream_turn(
        turn.agent,
        services.chats,
        services.files,
        thread_id=thread_id,
        model=turn.model,
        situation=turn.situation,
        on_error=turn.refund,
    )
    if turn.title is not None:
        events = title_first(events, turn.title, wait_s=TITLE_WAIT_S)
    async for event in events:
        yield event
