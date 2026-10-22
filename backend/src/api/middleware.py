"""Logs every HTTP request and response, including bodies and unhandled tracebacks.

Written as plain ASGI middleware (not BaseHTTPMiddleware) so streaming responses pass
through untouched; their bodies are summarized rather than buffered.
"""

import logging
import time
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.log_config import request_id_var, truncate

logger = logging.getLogger("api.http")

TEXT_TYPES = ("application/json", "text/plain")


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp, *, body_max_chars: int) -> None:
        self.app = app
        self.body_max_chars = body_max_chars

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid4().hex[:12]
        token = request_id_var.set(request_id)
        method, path = scope["method"], scope["path"]
        query = scope["query_string"].decode()
        target = f"{path}?{query}" if query else path
        client = scope.get("client")
        started = time.perf_counter()

        request_body = bytearray()
        response_body = bytearray()
        response_status = 0
        response_type = ""
        streamed_bytes = 0
        logged_request = False

        def log_request() -> None:
            nonlocal logged_request
            if logged_request:
                return
            logged_request = True
            body = self._decode(bytes(request_body))
            logger.info(
                "--> %s %s from %s%s",
                method,
                target,
                f"{client[0]}:{client[1]}" if client else "unknown",
                f" body={body}" if body else "",
            )

        async def receive_logged() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                request_body.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    log_request()
            return message

        async def send_logged(message: Message) -> None:
            nonlocal response_status, response_type, streamed_bytes
            if message["type"] == "http.response.start":
                log_request()  # GETs never read a body, so log them here.
                response_status = message["status"]
                headers = MutableHeaders(scope=message)
                headers.append("X-Request-ID", request_id)
                response_type = headers.get("content-type", "")
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                streamed_bytes += len(chunk)
                if response_type.startswith(TEXT_TYPES):
                    response_body.extend(chunk)
            await send(message)

        try:
            await self.app(scope, receive_logged, send_logged)
        except Exception:
            log_request()
            logger.exception(
                "<-- %s %s 500 unhandled error after %.0fms",
                method,
                target,
                (time.perf_counter() - started) * 1000,
            )
            raise
        else:
            elapsed_ms = (time.perf_counter() - started) * 1000
            if response_type.startswith(TEXT_TYPES):
                detail = self._decode(bytes(response_body))
                summary = f" body={detail}" if detail else ""
            else:
                summary = f" ({response_type or 'no content type'}, {streamed_bytes} bytes)"
            level = logging.WARNING if response_status >= 400 else logging.INFO
            logger.log(
                level,
                "<-- %s %s %d in %.0fms%s",
                method,
                target,
                response_status,
                elapsed_ms,
                summary,
            )
        finally:
            request_id_var.reset(token)

    def _decode(self, body: bytes) -> str:
        if not body:
            return ""
        return truncate(body.decode("utf-8", errors="replace"), self.body_max_chars)
