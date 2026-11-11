"""Web search through an API: Tavily or Brave, whichever has a key."""

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import SecretStr

logger = logging.getLogger(__name__)

SNIPPET_CHARS = 300


class SearchError(Exception):
    """The search couldn't be done; the message says why."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, limit: int) -> list[SearchResult]: ...


def _clean(text: object) -> str:
    return " ".join(str(text or "").split())[:SNIPPET_CHARS]


class Tavily:
    name = "Tavily"

    def __init__(self, key: SecretStr, *, timeout_s: float, transport=None) -> None:
        self._key, self._timeout_s, self._transport = key, timeout_s, transport

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
                json={"query": query, "max_results": limit, "search_depth": "basic"},
            )
        _check(response, self.name)
        found = response.json().get("results", [])
        return [
            SearchResult(_clean(item.get("title")), item["url"], _clean(item.get("content")))
            for item in found
            if item.get("url")
        ]


class Brave:
    name = "Brave Search"

    def __init__(self, key: SecretStr, *, timeout_s: float, transport=None) -> None:
        self._key, self._timeout_s, self._transport = key, timeout_s, transport

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "X-Subscription-Token": self._key.get_secret_value(),
                    "Accept": "application/json",
                },
                params={"q": query, "count": limit},
            )
        _check(response, self.name)
        found = response.json().get("web", {}).get("results", [])
        return [
            SearchResult(_clean(item.get("title")), item["url"], _clean(item.get("description")))
            for item in found
            if item.get("url")
        ]


def _check(response: httpx.Response, name: str) -> None:
    if response.status_code in (401, 403):
        raise SearchError(f"{name} rejected the server's API key")
    if response.status_code == 429:
        raise SearchError(f"{name}'s rate limit or monthly quota was reached")
    if response.status_code >= 400:
        raise SearchError(f"{name} answered with HTTP {response.status_code}")


def provider_for(
    tavily_key: SecretStr | None,
    brave_key: SecretStr | None,
    *,
    timeout_s: float,
    transport=None,
) -> SearchProvider | None:
    """Tavily if its key is set, else Brave, else None."""
    if tavily_key:
        return Tavily(tavily_key, timeout_s=timeout_s, transport=transport)
    if brave_key:
        return Brave(brave_key, timeout_s=timeout_s, transport=transport)
    return None
