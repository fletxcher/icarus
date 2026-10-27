"""Local models served by Ollama: which ones a server has, found by asking it."""

import logging
import time

import httpx

from .models import ModelOption

logger = logging.getLogger(__name__)

PREFIX = "ollama/"
TIMEOUT_S = 3.0
CACHE_S = 30.0

_cache: dict[str, tuple[float, list[str]]] = {}


class OllamaUnavailable(Exception):
    """The Ollama server can't be reached or didn't answer as Ollama does."""


async def installed_models(base_url: str) -> list[str]:
    """Names of the models installed on the Ollama server (raises OllamaUnavailable)."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            response = await client.get(f"{base_url.rstrip('/')}/api/tags")
        response.raise_for_status()
        return sorted(model["name"] for model in response.json()["models"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        raise OllamaUnavailable(type(error).__name__) from error


async def model_options(base_url: str) -> list[ModelOption]:
    """The server's models as options; none (with a warning) when it can't be reached.
    Answers are reused for a short while so every request doesn't ask again."""
    cached = _cache.get(base_url)
    if cached is None or time.monotonic() - cached[0] > CACHE_S:
        try:
            names = await installed_models(base_url)
        except OllamaUnavailable as error:
            logger.warning("Ollama at %s unavailable: %s", base_url, error)
            names = []
        _cache[base_url] = cached = (time.monotonic(), names)
    return [
        ModelOption(
            id=f"{PREFIX}{name}",
            label=name,
            description="Runs on an Ollama server",
            provider="ollama",
        )
        for name in cached[1]
    ]


def forget(base_url: str) -> None:
    _cache.pop(base_url, None)
