"""Runs one agent turn, streaming it as server-sent events and persisting the reply.

Events, in order:
- `start`        {"message_id"}                          assistant message created
- `reasoning`    {"text"}                                streamed model thinking
- `token`        {"text"}                                streamed answer text
- `tool_call`    {"id", "name", "args"}                  the agent invoked a tool
- `tool_result`  {"id", "name", "status", "content",     that tool finished ("success"/"error");
                  "artifact"}                            artifact: UI data such as a plot
- `done`         {"message_id"}                          reply complete

On a chat's first message, `title_first` adds a `title` {"title"} event ahead of these.
- `error`        {"message"}                             the turn failed
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi.sse import ServerSentEvent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from openrouter.errors import OpenRouterError

from ..chats.repository import ChatRepository
from ..chats.schemas import (
    Message,
    MessagePart,
    MessageStatus,
    ReasoningPart,
    TextPart,
    ToolPart,
)
from ..config import get_settings
from ..files.repository import FileRepository
from ..languages import Language
from .logging_callbacks import AgentLoggingHandler

logger = logging.getLogger(__name__)


def with_attachments(message: Message, texts: dict[str, str], max_chars: int) -> str:
    """The user's text followed by each attached file's contents."""
    blocks = [message.content]
    for attachment in message.attachments:
        text = texts.get(attachment.id)
        if text is None:
            blocks.append(f"[Attached file {attachment.name} is no longer available.]")
            continue
        if len(text) > max_chars:
            text = f"{text[:max_chars]}\n[... truncated, {len(text) - max_chars} more characters]"
        blocks.append(f"<file name={attachment.name!r}>\n{text}\n</file>")
    return "\n\n".join(blocks)


def to_langchain(
    messages: list[Message], file_texts: dict[str, str], *, file_max_chars: int
) -> list[BaseMessage]:
    """Rebuilds the conversation from stored messages.

    Stored history is the source of truth so edits and regenerations (which
    truncate it) apply to the next turn. Reasoning and tool calls from earlier
    turns are not replayed; their outcomes live in the assistant's text.
    Attached files are inlined into the user message they came with.
    """
    history: list[BaseMessage] = []
    for message in messages:
        if message.role == "user":
            history.append(HumanMessage(with_attachments(message, file_texts, file_max_chars)))
        elif message.content:
            history.append(AIMessage(message.content))
    return history


def split_message(message: AIMessage) -> tuple[str, str]:
    """Splits a (streamed) model message into (reasoning, answer text).

    Providers report reasoning differently, and LangChain's `.text` drops it:
    - Mistral: `{"type": "thinking", "thinking": [{"type": "text", ...}]}` content blocks.
    - OpenRouter: a `reasoning_content` string in `additional_kwargs`.
    """
    reasoning, text = [], []
    if isinstance(extra := message.additional_kwargs.get("reasoning_content"), str):
        reasoning.append(extra)
    content: str | list[Any] = message.content
    if isinstance(content, str):
        return "".join(reasoning), content
    for block in content:
        if isinstance(block, str):
            text.append(block)
        elif block.get("type") == "text":
            text.append(block.get("text", ""))
        elif block.get("type") in ("thinking", "reasoning"):
            inner = block.get("thinking") or block.get("reasoning") or ""
            if isinstance(inner, str):
                reasoning.append(inner)
            else:
                reasoning.extend(sub.get("text", "") for sub in inner if isinstance(sub, dict))
    return "".join(reasoning), "".join(text)


# What the user should know when a model provider rejects a request.
PROVIDER_ERRORS = {
    401: "{provider} rejected the API key. Check it in Settings, under API keys.",
    402: "{provider} requires credits or a payment method on this account.",
    429: "{provider}'s rate limit was exceeded. Wait a moment, or check the plan's limits.",
}


def _provider_failure(error: Exception) -> tuple[str, int, str | None] | None:
    """(provider, HTTP status, the provider's own message) for an API error response."""
    if isinstance(error, httpx.HTTPStatusError):  # Mistral's client
        return "Mistral", error.response.status_code, None
    if isinstance(error, OpenRouterError):
        return "OpenRouter", error.status_code, error.message
    # Ollama's client raises ResponseError carrying the HTTP status.
    names = {"ollama": "Ollama"}
    module = type(error).__module__.split(".")[0]
    status = getattr(error, "status_code", None)
    if module in names and isinstance(status, int):
        return names[module], status, None
    return None


def describe_error(error: Exception) -> str:
    """A user-facing explanation of why the turn failed."""
    if failure := _provider_failure(error):
        provider, status, detail = failure
        text = PROVIDER_ERRORS.get(status, "{provider} returned an error ({status}).")
        text = text.format(provider=provider, status=status)
        # OpenRouter's message says exactly what ran out (e.g. the daily free quota).
        return f"{text} ({detail})" if detail and status in PROVIDER_ERRORS else text
    if isinstance(error, httpx.TransportError):
        return "Couldn't reach the model provider. Check the server's network connection."
    return "The assistant failed to respond."


class ReplyBuilder:
    """Accumulates the reply's parts in the order they stream in."""

    def __init__(self) -> None:
        self.parts: list[MessagePart] = []
        self._tools: dict[str, ToolPart] = {}

    def add_reasoning(self, text: str) -> None:
        if self.parts and isinstance(self.parts[-1], ReasoningPart):
            self.parts[-1].text += text
        else:
            self.parts.append(ReasoningPart(text=text))

    def add_text(self, text: str) -> None:
        if self.parts and isinstance(self.parts[-1], TextPart):
            self.parts[-1].text += text
        else:
            self.parts.append(TextPart(text=text))

    def start_tool(self, call: dict[str, Any]) -> ToolPart:
        part = ToolPart(id=call["id"], name=call["name"], args=call["args"])
        self._tools[part.id] = part
        self.parts.append(part)
        return part

    def finish_tool(self, message: ToolMessage) -> None:
        if part := self._tools.get(message.tool_call_id):
            part.result = message.text
            part.status = "error" if message.status == "error" else "success"
            if isinstance(message.artifact, dict):
                part.artifact = message.artifact

    @property
    def content(self) -> str:
        """The answer text alone, used for history, copying and search."""
        texts = [part.text.strip() for part in self.parts if isinstance(part, TextPart)]
        return "\n\n".join(text for text in texts if text)

    def stop_running_tools(self) -> None:
        for part in self._tools.values():
            if part.status == "running":
                part.status = "error"
                part.result = part.result or "Interrupted before the tool finished."


async def stream_turn(
    agent: CompiledStateGraph,
    chats: ChatRepository,
    files: FileRepository,
    *,
    thread_id: str,
    model: str,
    language: Language | None = None,
) -> AsyncIterator[ServerSentEvent]:
    settings = get_settings()
    messages = await chats.list_messages(thread_id)
    file_ids = [attachment.id for message in messages for attachment in message.attachments]
    file_texts = await files.texts(file_ids) if file_ids else {}
    history = to_langchain(messages, file_texts, file_max_chars=settings.file_context_max_chars)
    reply = await chats.add_message(thread_id, "assistant", "", status="streaming", model=model)
    logger.info(
        "turn start: thread=%s reply=%s model=%s history=%d messages",
        thread_id,
        reply.id,
        model,
        len(history),
    )
    started = time.perf_counter()
    yield ServerSentEvent(event="start", data={"message_id": reply.id})

    builder = ReplyBuilder()
    status: MessageStatus = "error"
    error_message: str | None = None
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id, "model": model, "language": language},
        "callbacks": [AgentLoggingHandler(payload_max_chars=settings.log_body_max_chars)],
    }

    try:
        stream = agent.astream({"messages": history}, config, stream_mode=["messages", "updates"])
        async for mode, data in stream:
            if mode == "messages":
                chunk, metadata = data
                if metadata.get("langgraph_node") == "agent" and isinstance(chunk, AIMessage):
                    reasoning, text = split_message(chunk)
                    if reasoning:
                        builder.add_reasoning(reasoning)
                        yield ServerSentEvent(event="reasoning", data={"text": reasoning})
                    if text:
                        builder.add_text(text)
                        yield ServerSentEvent(event="token", data={"text": text})
                continue

            for update in data.values():
                for message in (update or {}).get("messages", []):
                    if isinstance(message, AIMessage):
                        for call in message.tool_calls:
                            part = builder.start_tool(call)
                            yield ServerSentEvent(
                                event="tool_call",
                                data={"id": part.id, "name": part.name, "args": part.args},
                            )
                    elif isinstance(message, ToolMessage):
                        builder.finish_tool(message)
                        yield ServerSentEvent(
                            event="tool_result",
                            data={
                                "id": message.tool_call_id,
                                "name": message.name,
                                "status": "error" if message.status == "error" else "success",
                                "content": message.text,
                                "artifact": message.artifact
                                if isinstance(message.artifact, dict)
                                else None,
                            },
                        )

        status = "done"
        yield ServerSentEvent(event="done", data={"message_id": reply.id})
    except asyncio.CancelledError:
        status = "stopped"  # The client disconnected or pressed stop.
        raise
    except Exception as error:
        logger.exception("agent turn failed for thread %s", thread_id)
        error_message = describe_error(error)
        yield ServerSentEvent(event="error", data={"message": error_message})
    finally:
        builder.stop_running_tools()
        content = builder.content
        log = logger.info if status == "done" else logger.warning
        log(
            "turn %s: thread=%s reply=%s in %.1fs, %d chars, %d parts",
            status,
            thread_id,
            reply.id,
            time.perf_counter() - started,
            len(content),
            len(builder.parts),
        )
        # Shielded so the reply is saved even when the request is being cancelled.
        await asyncio.shield(
            chats.finish_message(
                reply.id,
                content=content,
                status=status,
                parts=builder.parts,
                error=error_message,
            )
        )


async def title_first(
    events: AsyncIterator[ServerSentEvent],
    title: asyncio.Task[str | None],
    *,
    wait_s: float,
) -> AsyncIterator[ServerSentEvent]:
    """Sends the chat's new title (`title` event) before the reply.

    The reply keeps generating meanwhile; its events are held for up to `wait_s` while
    the title is pending. A slower title is sent as soon as it's ready instead, and
    always before the reply's final event.
    """
    queue: asyncio.Queue[ServerSentEvent | None] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for event in events:
                await queue.put(event)
        finally:
            await queue.put(None)

    pump_task = asyncio.create_task(pump())
    sent_title = False

    async def title_event(wait: bool) -> AsyncIterator[ServerSentEvent]:
        nonlocal sent_title
        if sent_title or not (wait or title.done()):
            return
        sent_title = True
        try:
            generated = await title
        except Exception:
            # A title is optional; never let it break the reply.
            logger.warning("title task failed", exc_info=True)
            return
        if generated:
            yield ServerSentEvent(event="title", data={"title": generated})

    try:
        await asyncio.wait({title}, timeout=wait_s)
        async for event in title_event(wait=False):
            yield event
        while (event := await queue.get()) is not None:
            # The last event (done or error) never goes out ahead of the title.
            async for title_update in title_event(wait=event.event in ("done", "error")):
                yield title_update
            yield event
    finally:
        # Stops generation if the client went away mid-reply (the reply is saved as stopped).
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump_task
