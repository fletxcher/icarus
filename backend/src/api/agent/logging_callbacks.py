"""Logs the agent's model calls and tool calls, with timings, payloads and tracebacks."""

import json
import logging
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

from ..log_config import truncate

logger = logging.getLogger("api.agent")


class AgentLoggingHandler(AsyncCallbackHandler):
    """Attach per turn via `config={"callbacks": [AgentLoggingHandler(...)]}`."""

    def __init__(self, *, payload_max_chars: int) -> None:
        self._payload_max_chars = payload_max_chars
        self._started: dict[UUID, float] = {}
        self._tool_names: dict[UUID, str] = {}

    def _elapsed_ms(self, run_id: UUID) -> float:
        started = self._started.pop(run_id, None)
        return (time.perf_counter() - started) * 1000 if started else 0.0

    def _short(self, value: Any) -> str:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        return truncate(text, self._payload_max_chars)

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._started[run_id] = time.perf_counter()
        model = (metadata or {}).get("ls_model_name", "unknown")
        logger.info("model call: %s with %d messages", model, len(messages[0]) if messages else 0)

    async def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        elapsed = self._elapsed_ms(run_id)
        message = (
            getattr(response.generations[0][0], "message", None) if response.generations else None
        )
        usage = getattr(message, "usage_metadata", None) or {}
        tool_calls = [call["name"] for call in getattr(message, "tool_calls", None) or []]
        logger.info(
            "model done in %.0fms: tokens in=%s out=%s, tool calls=%s",
            elapsed,
            usage.get("input_tokens", "?"),
            usage.get("output_tokens", "?"),
            tool_calls or "none",
        )

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        logger.error(
            "model call failed after %.0fms",
            self._elapsed_ms(run_id),
            exc_info=(type(error), error, error.__traceback__),
        )

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        name = serialized.get("name", "unknown")
        self._started[run_id] = time.perf_counter()
        self._tool_names[run_id] = name
        logger.info("tool start: %s args=%s", name, self._short(inputs or input_str))

    async def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        name = self._tool_names.pop(run_id, "unknown")
        content = getattr(output, "content", output)
        logger.info(
            "tool done: %s in %.0fms result=%s",
            name,
            self._elapsed_ms(run_id),
            self._short(content),
        )

    async def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        name = self._tool_names.pop(run_id, "unknown")
        logger.error(
            "tool failed: %s after %.0fms",
            name,
            self._elapsed_ms(run_id),
            exc_info=(type(error), error, error.__traceback__),
        )
