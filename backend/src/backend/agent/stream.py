"""Runs one agent turn, streaming it as server-sent events and persisting the reply.

Events, in order:
- `start`        {"message_id"}                   assistant message created
- `token`        {"text"}                         streamed answer text
- `tool_call`    {"id", "name", "args"}           the agent invoked a tool
- `tool_result`  {"id", "name", "content"}        that tool finished
- `done`         {"message_id"}                   reply complete
- `error`        {"message"}                      the turn failed
"""

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi.sse import ServerSentEvent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from backend.chats.repository import ChatRepository
from backend.chats.schemas import Message, MessageStatus, ToolCall

logger = logging.getLogger(__name__)


def to_langchain(messages: list[Message]) -> list[BaseMessage]:
    """Rebuilds the conversation from stored messages.

    Stored history is the source of truth so edits and regenerations (which
    truncate it) apply to the next turn. Tool calls from earlier turns are not
    replayed; their outcomes live in the assistant's text.
    """
    history: list[BaseMessage] = []
    for message in messages:
        if message.role == "user":
            history.append(HumanMessage(message.content))
        elif message.content:
            history.append(AIMessage(message.content))
    return history


async def stream_turn(
    agent: CompiledStateGraph,
    chats: ChatRepository,
    *,
    thread_id: str,
    model: str,
) -> AsyncIterator[ServerSentEvent]:
    history = to_langchain(await chats.list_messages(thread_id))
    reply = await chats.add_message(thread_id, "assistant", "", status="streaming", model=model)
    yield ServerSentEvent(event="start", data={"message_id": reply.id})

    parts: list[str] = []
    tool_calls: dict[str, ToolCall] = {}
    status: MessageStatus = "error"
    config = {"configurable": {"thread_id": thread_id, "model": model}}

    try:
        stream = agent.astream({"messages": history}, config, stream_mode=["messages", "updates"])
        async for mode, data in stream:
            if mode == "messages":
                chunk, metadata = data
                if metadata.get("langgraph_node") == "agent" and isinstance(chunk, AIMessage):
                    if text := chunk.text:
                        parts.append(text)
                        yield ServerSentEvent(event="token", data={"text": text})
                continue

            for update in data.values():
                for message in update.get("messages", []):
                    if isinstance(message, AIMessage):
                        for call in message.tool_calls:
                            tool_call = ToolCall(
                                id=call["id"], name=call["name"], args=call["args"]
                            )
                            tool_calls[tool_call.id] = tool_call
                            # Keep text from before and after the tool call apart.
                            if parts and not parts[-1].endswith("\n"):
                                parts.append("\n\n")
                            yield ServerSentEvent(event="tool_call", data=tool_call.model_dump())
                    elif isinstance(message, ToolMessage):
                        result = message.text
                        if tool_call := tool_calls.get(message.tool_call_id):
                            tool_call.result = result
                        yield ServerSentEvent(
                            event="tool_result",
                            data={
                                "id": message.tool_call_id,
                                "name": message.name,
                                "content": result,
                            },
                        )

        status = "done"
        yield ServerSentEvent(event="done", data={"message_id": reply.id})
    except asyncio.CancelledError:
        status = "stopped"  # The client disconnected or pressed stop.
        raise
    except Exception:
        logger.exception("agent turn failed for thread %s", thread_id)
        yield ServerSentEvent(event="error", data={"message": "The assistant failed to respond."})
    finally:
        # Shielded so the reply is saved even when the request is being cancelled.
        await asyncio.shield(
            chats.finish_message(
                reply.id,
                content="".join(parts).strip(),
                status=status,
                tool_calls=list(tool_calls.values()),
            )
        )
