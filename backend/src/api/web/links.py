"""Checking the links the agent cites, so a made-up or dead link never reaches the user.

A link is kept when the agent got it from a tool in this chat (a search result or a page it
opened), or when a request to it shows the page exists. Links that don't load, and addresses the
server refuses to open, are removed from the answer, and the answer says so.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from ..config import Settings
from .fetch import Fetcher

logger = logging.getLogger(__name__)

# Kept: "ok" (2xx/3xx) and "restricted" (the site answered but refuses automated visitors, as many
# publishers do: 401, 403, 429). Removed: "dead" and "unsafe".
LinkState = Literal["ok", "restricted", "dead", "unsafe"]
MAX_LINKS = 20
CHECK_CONCURRENCY = 6
CHECK_TOTAL_S = 25.0

CODE = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)
MARKDOWN_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
BARE = re.compile(r"https?://[^\s<>\"'\]\)]+")
TRAILING = ".,;:!?"


def normalize(url: str) -> str:
    """The address without what doesn't change the page: case of the host, a fragment, a trailing
    slash."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


@dataclass
class SourceLog:
    """The addresses the tools returned or opened during one turn."""

    urls: set[str] = field(default_factory=set)

    def add(self, *urls: str) -> None:
        self.urls.update(normalize(url) for url in urls if url)

    def __contains__(self, url: str) -> bool:
        return normalize(url) in self.urls


@dataclass(frozen=True)
class LinkStatus:
    url: str
    state: LinkState
    status: int | None = None
    reason: str = ""

    @property
    def kept(self) -> bool:
        return self.state in ("ok", "restricted")


class LinkChecker:
    def __init__(self, fetcher: Fetcher) -> None:
        self._fetcher = fetcher
        self._cache: dict[str, LinkStatus] = {}

    async def check(self, url: str) -> LinkStatus:
        key = normalize(url)
        if key in self._cache:
            return self._cache[key]
        probe = await self._fetcher.probe(url)
        if probe.status is None:
            state: LinkState = "unsafe" if probe.refused else "dead"
            result = LinkStatus(url, state, None, probe.problem)
        elif probe.status < 400:
            result = LinkStatus(url, "ok", probe.status)
        elif probe.status in (401, 403, 429):
            result = LinkStatus(url, "restricted", probe.status, "blocks automated visitors")
        else:
            result = LinkStatus(url, "dead", probe.status, f"HTTP {probe.status}")
        self._cache[key] = result
        return result

    async def check_many(self, urls: list[str]) -> dict[str, LinkStatus]:
        gate = asyncio.Semaphore(CHECK_CONCURRENCY)

        async def one(url: str) -> LinkStatus:
            async with gate:
                return await self.check(url)

        results = await asyncio.wait_for(
            asyncio.gather(*(one(url) for url in urls)), timeout=CHECK_TOTAL_S
        )
        return dict(zip(urls, results, strict=True))


def link_checker_from(settings: Settings) -> LinkChecker:
    return LinkChecker(
        Fetcher(
            timeout_s=settings.web_timeout_s,
            max_bytes=settings.web_fetch_max_bytes,
            max_pdf_bytes=settings.web_fetch_max_pdf_bytes,
        )
    )


def _split_code(text: str) -> list[tuple[str, bool]]:
    """(piece, is_code) pieces; links in code (an example URL in a snippet) aren't citations."""
    return [(piece, index % 2 == 1) for index, piece in enumerate(CODE.split(text))]


def _bare_parts(match: re.Match[str]) -> tuple[str, str]:
    url = match.group(0)
    stripped = url.rstrip(TRAILING)
    return stripped, url[len(stripped) :]


def urls_in(text: str) -> list[str]:
    """Every distinct address that is written as a link or bare in the text, outside code."""
    found: list[str] = []
    for piece, is_code in _split_code(text):
        if is_code:
            continue
        for match in MARKDOWN_LINK.finditer(piece):
            found.append(match.group(2))
        for match in BARE.finditer(piece):
            found.append(_bare_parts(match)[0])
    return list(dict.fromkeys(found))


@dataclass(frozen=True)
class Verified:
    text: str
    removed: list[LinkStatus]

    @property
    def changed(self) -> bool:
        return bool(self.removed)


async def verify_text(text: str, *, known: set[str], checker: LinkChecker) -> Verified:
    """The text with every link that doesn't load taken out. Links in `known` (what the tools
    returned this turn, and what earlier answers already had checked) aren't requested again."""
    wanted = [url for url in urls_in(text) if normalize(url) not in known]
    if not wanted:
        return Verified(text, [])
    wanted, skipped = wanted[:MAX_LINKS], wanted[MAX_LINKS:]
    statuses = await checker.check_many(wanted)
    bad = {url: status for url, status in statuses.items() if not status.kept}
    # More links than are checked: unchecked ones can't be vouched for.
    for url in skipped:
        bad[url] = LinkStatus(url, "dead", None, "too many links to check")
    if not bad:
        return Verified(text, [])

    def markdown(match: re.Match[str]) -> str:
        return f"{match.group(1)} (link removed)" if match.group(2) in bad else match.group(0)

    def bare(match: re.Match[str]) -> str:
        url, rest = _bare_parts(match)
        return f"(link removed){rest}" if url in bad else match.group(0)

    pieces = []
    for piece, is_code in _split_code(text):
        if not is_code:
            piece = BARE.sub(bare, MARKDOWN_LINK.sub(markdown, piece))
        pieces.append(piece)
    return Verified("".join(pieces), list(bad.values()))
