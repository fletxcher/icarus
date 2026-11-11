"""Fetching web pages for the agent, safely.

The server fetches addresses a model (and so, indirectly, a user) chose, so every address is
checked before it is requested, and again at each redirect: only http and https, only the
usual ports, and never a loopback, private-network or link-local address.
"""

import io
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from ..credentials.addresses import AddressProblem, check_address
from ..files.extract import Unreadable, extract

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; IcarusBot/1.0; +https://github.com/fletxcher/icarus)"
ALLOWED_PORTS = {80, 443}
MAX_REDIRECTS = 5
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/json")

AddressCheck = Callable[[str, int], Awaitable[None]]


async def public_address(host: str, port: int) -> None:
    await check_address(host, port, allow_private=False)


class FetchError(Exception):
    """The page couldn't be fetched; the message says why, for the model and the user."""


@dataclass(frozen=True)
class Page:
    url: str  # the address the content came from, after redirects
    status: int
    content_type: str
    body: bytes
    truncated: bool


@dataclass(frozen=True)
class Probe:
    """What a request to an address showed, without reading the content."""

    status: int | None  # None when there was no HTTP answer at all
    final_url: str
    problem: str = ""  # why there was none, or why the address was refused
    refused: bool = False  # True when the address was never requested (unsafe or invalid)


def validate_url(url: str) -> tuple[str, int]:
    """(host, port) of an http(s) address; raises FetchError for anything else."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("only http and https addresses can be opened")
    if parts.username or parts.password:
        raise FetchError("addresses with a user name or password aren't opened")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise FetchError("the address has an invalid port") from None
    if port not in ALLOWED_PORTS:
        raise FetchError(f"port {port} isn't one that is opened")
    return parts.hostname, port


class Fetcher:
    def __init__(
        self,
        *,
        timeout_s: float,
        max_bytes: int,
        max_pdf_bytes: int | None = None,
        address_check: AddressCheck = public_address,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes
        self._max_pdf_bytes = max_pdf_bytes or max_bytes
        self._address_check = address_check
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout_s,
            follow_redirects=False,  # each hop is checked here
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.5",
            },
            transport=self._transport,
        )

    async def _open(self, client: httpx.AsyncClient, url: str, method: str) -> httpx.Response:
        """The response of the address after any redirects, each checked before it is requested.
        The caller closes it."""
        for _ in range(MAX_REDIRECTS + 1):
            host, port = validate_url(url)
            try:
                await self._address_check(host, port)
            except AddressProblem as problem:
                raise FetchError(str(problem)) from None
            response = await client.send(client.build_request(method, url), stream=True)
            location = response.headers.get("location")
            if response.status_code in (301, 302, 303, 307, 308) and location:
                await response.aclose()
                url = urljoin(str(response.url), location)
                continue
            return response
        raise FetchError(f"more than {MAX_REDIRECTS} redirects")

    async def fetch(self, url: str) -> Page:
        """The page at the address. Raises FetchError."""
        try:
            async with self._client() as client:
                response = await self._open(client, url, "GET")
                try:
                    return await self._read(response)
                finally:
                    await response.aclose()
        except FetchError:
            raise
        except httpx.TimeoutException:
            raise FetchError("the page took too long to answer") from None
        except httpx.HTTPError as error:
            raise FetchError(f"the page couldn't be reached ({type(error).__name__})") from None

    async def _read(self, response: httpx.Response) -> Page:
        final = str(response.url)
        if response.status_code >= 400:
            raise FetchError(f"the page answered with HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if (
            content_type
            and not content_type.startswith(TEXT_TYPES)
            and content_type != "application/pdf"
        ):
            raise FetchError(f"the page is a {content_type} file, not text")
        limit = self._max_pdf_bytes if content_type == "application/pdf" else self._max_bytes
        chunks, size, truncated = [], 0, False
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > limit:
                chunks.append(chunk[: len(chunk) - (size - limit)])
                truncated = True
                break
            chunks.append(chunk)
        return Page(final, response.status_code, content_type, b"".join(chunks), truncated)

    async def probe(self, url: str) -> Probe:
        """Whether the address answers, without reading the content: HEAD first, then GET for the
        sites that refuse HEAD. Never raises."""
        last = Probe(None, url, "no answer")
        for method in ("HEAD", "GET"):
            try:
                async with self._client() as client:
                    response = await self._open(client, url, method)
                    last = Probe(response.status_code, str(response.url))
                    await response.aclose()
            except FetchError as problem:
                return Probe(None, url, str(problem), refused=True)
            except httpx.TimeoutException:
                last = Probe(None, url, "timed out")
            except httpx.HTTPError as error:
                last = Probe(None, url, f"couldn't connect ({type(error).__name__})")
            if last.status is not None and last.status not in (400, 403, 405, 501):
                return last
        return last


# --- turning a page into text --------------------------------------------------------------

SKIPPED = {"script", "style", "noscript", "svg", "template", "iframe", "form", "nav", "footer"}
BLOCKS = {
    "p", "div", "section", "article", "main", "header", "aside", "ul", "ol", "li", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr", "pre", "blockquote", "figure",
}  # fmt: skip


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip = 0
        self._out = io.StringIO()

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in SKIPPED:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCKS:
            self._out.write("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCKS:
            self._out.write("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._out.write(data)

    @property
    def text(self) -> str:
        lines = (
            re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in self._out.getvalue().split("\n")
        )
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def page_text(page: Page) -> tuple[str, str]:
    """(title, readable text) of a fetched page. Raises FetchError if it has none."""
    if page.content_type == "application/pdf":
        if page.truncated:
            raise FetchError("the PDF is too large to read here")
        try:
            return "", extract("page.pdf", page.body).text
        except Unreadable:
            raise FetchError("the PDF couldn't be read") from None
    raw = page.body.decode("utf-8", errors="replace")
    if page.content_type in ("text/plain", "application/json"):
        return "", raw.strip()
    parser = _Text()
    parser.feed(raw)
    parser.close()
    return re.sub(r"\s+", " ", parser.title).strip(), parser.text
