"""Web search for the agent: fetching pages safely, and checking every link it cites."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pydantic import SecretStr

from api.agent.prompts import build_system_prompt
from api.agent.tools import web_tools
from api.credentials.addresses import AddressProblem
from api.web.fetch import Fetcher, FetchError, Page, page_text, validate_url
from api.web.links import (
    LinkChecker,
    LinkStatus,
    SourceLog,
    normalize,
    urls_in,
    verify_text,
)
from api.web.search import Brave, SearchError, SearchResult, Tavily, provider_for
from tests.conftest import use_scripted_agent
from tests.test_chats_api import create_thread, read_events

HTML = """<html><head><title> The  Reynolds number </title><style>p{}</style></head>
<body><nav>Home | About</nav><script>alert(1)</script>
<h1>Reynolds number</h1><p>Re = rho v L / mu.</p><p>It compares inertia to viscosity.</p>
<footer>Copyright</footer></body></html>"""


async def public_only(host: str, port: int) -> None:
    """The real rule without DNS: names are public, numbers are checked."""
    if host.startswith(("10.", "127.", "169.254.", "192.168.")) or host == "internal.test":
        raise AddressProblem(f"{host} is on the server's own network.")


def fetcher(handler, **changes) -> Fetcher:
    options = {"timeout_s": 5, "max_bytes": 100_000, "address_check": public_only} | changes
    return Fetcher(transport=httpx.MockTransport(handler), **options)


def ok_page(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=HTML, headers={"content-type": "text/html; charset=utf-8"})


def fetch(f: Fetcher, url: str) -> Page:
    return asyncio.run(f.fetch(url))


# --- fetching ------------------------------------------------------------------------------


def test_a_page_becomes_its_title_and_readable_text():
    page = fetch(fetcher(ok_page), "https://example.org/re")

    title, text = page_text(page)

    assert title == "The Reynolds number"
    assert "Re = rho v L / mu." in text and "inertia to viscosity" in text
    # Scripts, styles, navigation and footers aren't the content.
    assert "alert" not in text and "Home" not in text and "Copyright" not in text


def test_redirects_are_followed_and_the_final_address_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return ok_page(request)

    page = fetch(fetcher(handler), "https://example.org/old")

    assert page.url == "https://example.org/new" and page.status == 200


def test_a_redirect_to_the_servers_own_network_is_refused_at_that_hop():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    with pytest.raises(FetchError, match="own network"):
        fetch(fetcher(handler), "https://example.org/start")

    assert seen == ["https://example.org/start"]  # the second address was never requested


def test_a_redirect_loop_stops():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(FetchError, match="redirects"):
        fetch(fetcher(handler), "https://example.org/")


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://example.org/file", "only http and https"),
        ("file:///etc/passwd", "only http and https"),
        ("https://user:secret@example.org/", "user name or password"),
        ("http://example.org:8080/", "port 8080"),
        ("https://example.org:22/", "port 22"),
        ("not a url", "only http and https"),
    ],
)
def test_addresses_that_are_not_ordinary_web_pages_are_refused(url: str, message: str):
    with pytest.raises(FetchError, match=message):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "http://10.0.0.5/admin", "http://169.254.169.254/", "http://[::1]/"],
)
def test_private_addresses_are_refused_by_the_default_rule(url: str):
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return ok_page(request)

    real = Fetcher(timeout_s=5, max_bytes=1000, transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError):
        asyncio.run(real.fetch(url))

    assert called == []


def test_what_is_not_text_or_not_found_is_explained():
    def image(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})

    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    with pytest.raises(FetchError, match="image/png"):
        fetch(fetcher(image), "https://example.org/a.png")
    with pytest.raises(FetchError, match="HTTP 404"):
        fetch(fetcher(missing), "https://example.org/gone")


def test_a_huge_page_is_cut_at_the_limit():
    def big(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 500_000, headers={"content-type": "text/plain"})

    page = fetch(fetcher(big, max_bytes=1000), "https://example.org/big")

    assert page.truncated and len(page.body) == 1000


def test_a_pdf_gets_a_larger_limit_than_a_page_and_one_cut_short_is_not_read():
    def pdf(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"%PDF-" + b"x" * 5000, headers={"content-type": "application/pdf"}
        )

    whole = fetch(fetcher(pdf, max_bytes=1000, max_pdf_bytes=10_000), "https://example.org/a.pdf")
    cut = fetch(fetcher(pdf, max_bytes=1000, max_pdf_bytes=2000), "https://example.org/a.pdf")

    assert not whole.truncated and len(whole.body) == 5005
    assert cut.truncated and len(cut.body) == 2000
    with pytest.raises(FetchError, match="too large"):
        page_text(cut)


def test_a_probe_tells_live_pages_from_dead_ones_without_reading_them():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/live":
            return httpx.Response(200)
        if path == "/no-head":  # some sites refuse HEAD but serve GET
            return httpx.Response(405 if request.method == "HEAD" else 200)
        if path == "/blocked":
            return httpx.Response(403)
        if path == "/boom":
            raise httpx.ConnectError("refused")
        return httpx.Response(404)

    f = fetcher(handler)

    def probe(path: str):
        return asyncio.run(f.probe(f"https://example.org{path}"))

    assert probe("/live").status == 200
    assert probe("/no-head").status == 200
    assert probe("/blocked").status == 403
    assert probe("/missing").status == 404
    down = probe("/boom")
    assert down.status is None and not down.refused
    private = asyncio.run(f.probe("http://10.1.2.3/"))
    assert private.status is None and private.refused


# --- checking links ------------------------------------------------------------------------


class FakeChecker(LinkChecker):
    """Answers from a table, and remembers what it was asked."""

    def __init__(self, table: dict[str, str]) -> None:
        self.table = table
        self.asked: list[str] = []

    async def check(self, url: str) -> LinkStatus:
        self.asked.append(url)
        state = self.table.get(url, "dead")
        return LinkStatus(url, state, 200 if state == "ok" else None, "")  # type: ignore[arg-type]


def verify(text: str, table: dict[str, str], known: set[str] | None = None):
    checker = FakeChecker(table)
    result = asyncio.run(verify_text(text, known=known or set(), checker=checker))
    return result, checker


def test_links_are_found_as_markdown_and_bare_but_not_in_code():
    text = (
        "See [NASA](https://nasa.gov/a) and https://example.org/b, also (https://example.org/c). "
        "Again [again](https://nasa.gov/a).\n```\nrequests.get('https://in-a-fence.test/x')\n```\n"
        "and `https://inline.test/y` is code."
    )

    assert urls_in(text) == [
        "https://nasa.gov/a",
        "https://example.org/b",
        "https://example.org/c",
    ]


def test_addresses_that_differ_only_in_case_fragment_or_slash_are_the_same():
    assert normalize("HTTPS://Example.org/a/#top") == normalize("https://example.org/a")
    assert normalize("https://example.org/a?x=1") != normalize("https://example.org/a")


def test_links_that_do_not_load_are_removed_and_the_rest_kept():
    text = (
        "Real: [NASA](https://nasa.gov/ok). Made up: [Paper](https://fake.test/paper) and "
        "https://fake.test/bare. Publisher: [Journal](https://journal.test/x)."
    )
    table = {
        "https://nasa.gov/ok": "ok",
        "https://journal.test/x": "restricted",  # answers, but refuses robots
    }

    result, _ = verify(text, table)

    assert result.text == (
        "Real: [NASA](https://nasa.gov/ok). Made up: Paper (link removed) and "
        "(link removed). Publisher: [Journal](https://journal.test/x)."
    )
    assert {link.url for link in result.removed} == {
        "https://fake.test/paper",
        "https://fake.test/bare",
    }


def test_a_trailing_full_stop_is_not_part_of_the_address():
    result, checker = verify("Read https://example.org/page.", {"https://example.org/page": "ok"})

    assert not result.changed and checker.asked == ["https://example.org/page"]
    gone, _ = verify("Read https://example.org/page.", {})
    assert gone.text == "Read (link removed)."


def test_unsafe_addresses_are_removed_like_dead_ones():
    result, _ = verify("[meta](http://169.254.169.254/x)", {"http://169.254.169.254/x": "unsafe"})

    assert result.text == "meta (link removed)"


def test_links_the_tools_returned_are_not_requested_again():
    text = "From search: [A](https://a.test/1) and a guess: [B](https://b.test/2)."
    known = {normalize("https://a.test/1")}

    result, checker = verify(text, {}, known)

    assert checker.asked == ["https://b.test/2"]
    assert result.text == "From search: [A](https://a.test/1) and a guess: B (link removed)."


def test_code_is_never_changed_even_if_it_holds_a_dead_address():
    text = "Use `https://dead.test/api` or\n```\ncurl https://dead.test/api\n```\nsee https://dead.test/doc"

    result, checker = verify(text, {})

    assert "`https://dead.test/api`" in result.text and "curl https://dead.test/api" in result.text
    assert result.text.endswith("see (link removed)")
    assert checker.asked == ["https://dead.test/doc"]


def test_text_without_links_is_left_alone_and_checks_nothing():
    result, checker = verify("No links here, only numbers: 1.5e6.", {})

    assert not result.changed and checker.asked == []


def test_checker_results_come_from_the_probe_and_are_remembered():
    class Probing:
        calls = 0

        async def probe(self, url):
            from api.web.fetch import Probe

            Probing.calls += 1
            status = {"/live": 200, "/blocked": 429, "/gone": 404}.get(url[url.rindex("/") :])
            return Probe(status, url) if status else Probe(None, url, "timed out")

    checker = LinkChecker(Probing())  # type: ignore[arg-type]

    async def go():
        return [
            await checker.check("https://x.test/live"),
            await checker.check("https://x.test/blocked"),
            await checker.check("https://x.test/gone"),
            await checker.check("https://x.test/nothing"),
            await checker.check("https://x.test/live#section"),
        ]

    states = [status.state for status in asyncio.run(go())]

    assert states == ["ok", "restricted", "dead", "dead", "ok"]
    assert Probing.calls == 4  # the repeat, with a fragment, came from memory


# --- search --------------------------------------------------------------------------------


def test_tavily_results_are_read_from_its_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [{"title": "A  page", "url": "https://a.test/", "content": "x " * 400}]
            },
        )

    provider = Tavily(SecretStr("tk"), timeout_s=5, transport=httpx.MockTransport(handler))

    [result] = asyncio.run(provider.search("naca 2412", 4))

    assert seen["auth"] == "Bearer tk" and seen["body"]["query"] == "naca 2412"
    assert seen["body"]["max_results"] == 4
    assert (
        result.title == "A page" and result.url == "https://a.test/" and len(result.snippet) <= 300
    )


def test_brave_results_are_read_from_its_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers["x-subscription-token"]
        seen["q"] = request.url.params["q"]
        return httpx.Response(
            200,
            json={
                "web": {"results": [{"title": "T", "url": "https://b.test/", "description": "d"}]}
            },
        )

    provider = Brave(SecretStr("bk"), timeout_s=5, transport=httpx.MockTransport(handler))

    assert asyncio.run(provider.search("cantera", 3)) == [SearchResult("T", "https://b.test/", "d")]
    assert seen == {"token": "bk", "q": "cantera"}


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "rejected"), (429, "quota"), (500, "HTTP 500")],
)
def test_search_service_errors_are_explained(status: int, message: str):
    transport = httpx.MockTransport(lambda request: httpx.Response(status))
    provider = Tavily(SecretStr("tk"), timeout_s=5, transport=transport)

    with pytest.raises(SearchError, match=message):
        asyncio.run(provider.search("x", 3))


def test_tavily_is_used_when_its_key_is_set_and_brave_otherwise():
    assert isinstance(provider_for(SecretStr("t"), SecretStr("b"), timeout_s=5), Tavily)
    assert isinstance(provider_for(None, SecretStr("b"), timeout_s=5), Brave)
    assert provider_for(None, None, timeout_s=5) is None


# --- the tools -----------------------------------------------------------------------------


class FakeSearch:
    name = "fake"

    def __init__(self, results: list[SearchResult] | Exception) -> None:
        self.results = results

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        if isinstance(self.results, Exception):
            raise self.results
        return self.results


def make_tools(results, table: dict[str, str], handler=ok_page):
    tools = web_tools(
        FakeSearch(results),
        fetcher(handler),
        FakeChecker(table),
        max_results=6,
        max_chars=60,
    )
    return {tool.name: tool for tool in tools}


def call(tool, args: dict, log: SourceLog | None = None) -> str:
    config = {"configurable": {"sources": log}} if log is not None else {}
    return asyncio.run(tool.ainvoke(args, config))


def test_search_lists_only_links_that_load_and_records_them_as_sources():
    results = [
        SearchResult("Live", "https://a.test/1", "about re"),
        SearchResult("Dead", "https://b.test/2", "gone"),
        SearchResult("Walled", "https://c.test/3", "paywall"),
    ]
    tools = make_tools(results, {"https://a.test/1": "ok", "https://c.test/3": "restricted"})
    log = SourceLog()

    text = call(tools["web_search"], {"query": "reynolds number"}, log)

    assert "1. [Live](https://a.test/1)" in text and "about re" in text
    assert "https://b.test/2" not in text and "1 more result(s) were left out" in text
    assert "blocks automated visitors" in text
    assert "https://a.test/1" in log and "https://c.test/3" in log and "https://b.test/2" not in log


def test_search_failures_and_empty_results_are_reported_plainly():
    assert "none of their pages load" in call(
        make_tools([SearchResult("D", "https://d.test/", "")], {})["web_search"], {"query": "xx"}
    )
    assert "No results" in call(make_tools([], {})["web_search"], {"query": "xx"})
    failing = make_tools(SearchError("Tavily's rate limit or monthly quota was reached"), {})
    assert "quota" in call(failing["web_search"], {"query": "xx"})
    unreachable = make_tools(httpx.ConnectError("x"), {})
    assert "couldn't be reached" in call(unreachable["web_search"], {"query": "xx"})


def test_fetch_page_returns_the_text_and_records_the_address_it_ended_at():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/short":
            return httpx.Response(301, headers={"location": "/re"})
        return ok_page(request)

    tools = make_tools([], {}, handler)
    log = SourceLog()

    text = call(tools["fetch_page"], {"url": "https://example.org/short"}, log)

    assert text.startswith("Page: The Reynolds number\nAddress: https://example.org/re")
    assert "longer; this is the start" in text  # cut at max_chars=60
    assert "https://example.org/short" in log and "https://example.org/re" in log


def test_fetch_page_says_why_a_page_could_not_be_opened():
    tools = make_tools([], {})

    text = call(tools["fetch_page"], {"url": "http://internal.test/admin"})

    assert text.startswith("Couldn't open http://internal.test/admin") and "own network" in text


def test_the_prompt_explains_citing_only_when_the_tools_exist():
    tools = list(make_tools([], {}).values())

    prompt = build_system_prompt(tools, {})
    assert "Using the web:" in prompt
    # Unsure about a library's syntax: read its documentation first, don't guess.
    assert "Never guess the syntax" in prompt and "official docs" in prompt
    assert "Using the web:" not in build_system_prompt([], {})


# --- the answer is checked before it is final ----------------------------------------------


class Table:
    """Stands in for the checker the stream builds: only /good loads."""

    asked: list[str] = []

    def __init__(self, *args) -> None:
        pass

    async def check_many(self, urls: list[str]):
        Table.asked += urls
        return {
            url: LinkStatus(url, "ok" if url.endswith("/good") else "dead", None, "HTTP 404")
            for url in urls
        }


def test_a_made_up_link_is_taken_out_of_the_answer_and_the_client_is_told(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    Table.asked = []
    monkeypatch.setattr("api.agent.stream.link_checker_from", lambda settings: Table())
    use_scripted_agent(
        client,
        [AIMessage("See [good](https://real.test/good) and [fake](https://made.up/paper).")],
    )
    thread_id = create_thread(client)

    events = read_events(client, thread_id, {"content": "sources?"})

    [fix] = [data for name, data in events if name == "replace_text"]
    assert fix["text"] == "See [good](https://real.test/good) and fake (link removed)."
    assert events[-1][0] == "done" and events.index(("replace_text", fix)) < len(events) - 1
    stored = client.get(f"/api/threads/{thread_id}").json()["messages"][-1]
    assert stored["content"] == fix["text"]
    assert stored["parts"][0]["text"] == fix["text"]


def test_an_answer_whose_links_all_load_is_not_touched(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    Table.asked = []
    monkeypatch.setattr("api.agent.stream.link_checker_from", lambda settings: Table())
    use_scripted_agent(client, [AIMessage("Only [one](https://real.test/good) link.")])

    events = read_events(client, create_thread(client), {"content": "x"})

    assert not [name for name, _ in events if name == "replace_text"]


def test_links_an_earlier_answer_already_had_are_not_checked_again(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    Table.asked = []
    monkeypatch.setattr("api.agent.stream.link_checker_from", lambda settings: Table())
    use_scripted_agent(
        client,
        [
            AIMessage("First: [a](https://real.test/good)."),
            AIMessage("Again: [a](https://real.test/good) and [b](https://real.test/good2)."),
        ],
    )
    thread_id = create_thread(client)

    read_events(client, thread_id, {"content": "one"})
    first_round = list(Table.asked)
    read_events(client, thread_id, {"content": "two"})

    assert first_round == ["https://real.test/good"]
    assert Table.asked[len(first_round) :] == ["https://real.test/good2"]


def test_a_failing_check_never_breaks_the_reply(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    class Broken:
        async def check_many(self, urls):
            raise RuntimeError("network down")

    monkeypatch.setattr("api.agent.stream.link_checker_from", lambda settings: Broken())
    use_scripted_agent(client, [AIMessage("See [x](https://real.test/good).")])

    events = read_events(client, create_thread(client), {"content": "x"})

    assert events[-1][0] == "done"
    assert not [name for name, _ in events if name == "replace_text"]
