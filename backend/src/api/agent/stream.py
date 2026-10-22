"""Runs one agent turn, streaming it as server-sent events and persisting the reply.

Events, in order:
- `start`        {"message_id"}                          assistant message created
- `reasoning`    {"text"}                                streamed model thinking
- `token`        {"text"}                                streamed answer text
- `tool_call`    {"id", "name", "args"}                  the agent invoked a tool
- `tool_result`  {"id", "name", "status", "content"}     that tool finished ("success"/"error")
- `done`         {"message_id"}                          reply complete
- `error`        {"message"}                             the turn failed
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi.sse import ServerSentEvent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from api.agent.logging_callbacks import AgentLoggingHandler
from api.chats.repository import ChatRepository
from api.chats.schemas import (
    Message,
    MessagePart,
    MessageStatus,
    ReasoningPart,
    TextPart,
    ToolPart,
)
from api.config import get_settings

logger = logging.getLogger(__name__)


def to_langchain(messages: list[Message]) -> list[BaseMessage]:
    """Rebuilds the conversation from stored messages.

    Stored history is the source of truth so edits and regenerations (which
    truncate it) apply to the next turn. Reasoning and tool calls from earlier
    turns are not replayed; their outcomes live in the assistant's text.
    """
    history: list[BaseMessage] = []
    for message in messages:
        if message.role == "user":
            history.append(HumanMessage(message.content))
        elif message.content:
            history.append(AIMessage(message.content))
    return history


def split_content(content: str | list[Any]) -> tuple[str, str]:
    """Splits a (streamed) message's content into (reasoning, answer text).

    Mistral sends thinking as `{"type": "thinking", "thinking": [{"type": "text", ...}]}`
    blocks, which LangChain's `.text` drops.
    """
    if isinstance(content, str):
        return "", content
    reasoning, text = [], []
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


# What the user should know when the model provider rejects a request.
PROVIDER_ERRORS = {
    401: "Mistral rejected the API key. Check MISTRAL_API_TOKEN.",
    402: "Mistral requires a payment method on this account.",
    429: "Mistral's rate limit was exceeded. Wait a moment, or check the plan's limits.",
}


def describe_error(error: Exception) -> str:
    """A user-facing explanation of why the turn failed."""
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return PROVIDER_ERRORS.get(status, f"Mistral returned an error ({status}).")
    if isinstance(error, httpx.TransportError):
        return "Couldn't reach Mistral. Check the server's network connection."
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
    *,
    thread_id: str,
    model: str,
) -> AsyncIterator[ServerSentEvent]:
    history = to_langchain(await chats.list_messages(thread_id))
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
        "configurable": {"thread_id": thread_id, "model": model},
        "callbacks": [AgentLoggingHandler(payload_max_chars=get_settings().log_body_max_chars)],
    }

    try:
        stream = agent.astream({"messages": history}, config, stream_mode=["messages", "updates"])
        async for mode, data in stream:
            if mode == "messages":
                chunk, metadata = data
                if metadata.get("langgraph_node") == "agent" and isinstance(chunk, AIMessage):
                    reasoning, text = split_content(chunk.content)
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
