"""Web access for the agent: fetch a URL, or search the web.

The controlled way back onto the network, since run_bash has no network by default (see
sandbox.py). Standard library only, so the keyless path stays keyless. fetch() turns a page
into plain text; search() returns titles, URLs, and snippets (Tavily if TAVILY_API_KEY is
set, else a best-effort DuckDuckGo scrape). Both return an error string rather than
raising, so the model reads and adapts the way it does with every other tool.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

FETCH_TIMEOUT_SECONDS = 20
MAX_FETCH_CHARS = 20_000  # same ceiling the file tools use, so one page cannot flood context
MAX_FETCH_BYTES = 5_000_000  # stop reading absurdly large responses
USER_AGENT = "cc-mini/0.1 (a tiny coding agent)"


def _get(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[str, str]:
    """Fetch `url` and return (content_type, decoded body). Indirection so tests can patch it."""
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:  # noqa: S310 - scheme checked by callers
        charset = resp.headers.get_content_charset() or "utf-8"
        body = resp.read(MAX_FETCH_BYTES).decode(charset, errors="replace")
        return resp.headers.get_content_type(), body


class _TextExtractor(HTMLParser):
    """Pull readable text out of HTML: drop script/style/head, keep the visible words."""

    _SKIP = {"script", "style", "noscript", "head", "template"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            text = data.strip()
            if text:
                self.parts.append(text)


def _truncate(text: str) -> str:
    if len(text) <= MAX_FETCH_CHARS:
        return text
    return text[:MAX_FETCH_CHARS] + f"\n... [truncated, {len(text) - MAX_FETCH_CHARS} more chars]"


def fetch(url: str) -> str:
    """Fetch a URL and return its text (HTML is reduced to visible text)."""
    if not url.startswith(("http://", "https://")):
        return f"Error: url must start with http:// or https:// (got {url!r})"
    try:
        content_type, body = _get(url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return f"Error fetching {url}: {exc}"
    if "html" in content_type:
        parser = _TextExtractor()
        parser.feed(body)
        body = "\n".join(parser.parts)
    body = body.strip()
    return _truncate(body) if body else "(the page had no readable text)"


def _format_results(results: list[dict[str, str]]) -> str:
    if not results:
        return "(no results)"
    lines = []
    for r in results:
        lines.append(f"{r['title']}\n{r['url']}\n{r['snippet']}".strip())
    return "\n\n".join(lines)


def _tavily_search(query: str, api_key: str, max_results: int) -> list[dict[str, str]]:
    payload = json.dumps(
        {"api_key": api_key, "query": query, "max_results": max_results}
    ).encode()
    _, body = _get(
        "https://api.tavily.com/search", data=payload, headers={"Content-Type": "application/json"}
    )
    data = json.loads(body)
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in data.get("results", [])[:max_results]
    ]


class _DuckDuckGoParser(HTMLParser):
    """Scrape result titles, links, and snippets from the DuckDuckGo HTML endpoint."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._mode = ""  # "title" or "snippet" while inside the matching element
        self._open_tag = ""  # the tag that opened the current mode, so nested tags do not close it
        self._href = ""
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if self._mode:  # ignore nested tags; just keep collecting their text
            return
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        # Match both DuckDuckGo markups: the lite endpoint (result-link/result-snippet)
        # and the older html endpoint (result__a/result__snippet).
        if tag == "a" and ({"result-link", "result__a"} & set(classes)):
            self._mode, self._open_tag, self._href, self._buf = "title", tag, a.get("href", ""), []
        elif {"result-snippet", "result__snippet"} & set(classes):
            self._mode, self._open_tag, self._buf = "snippet", tag, []

    def handle_data(self, data: str) -> None:
        if self._mode:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._mode or tag != self._open_tag:
            return
        text = "".join(self._buf).strip()
        if self._mode == "title":
            self.results.append({"title": text, "url": _ddg_real_url(self._href), "snippet": ""})
        elif self._mode == "snippet" and self.results:
            self.results[-1]["snippet"] = text
        self._mode = self._open_tag = ""


def _ddg_real_url(href: str) -> str:
    """DuckDuckGo wraps result links in a /l/?uddg=<encoded> redirect; unwrap it."""
    parsed = urllib.parse.urlparse(href)
    params = urllib.parse.parse_qs(parsed.query)
    if "uddg" in params:
        return params["uddg"][0]
    if href.startswith("//"):
        return "https:" + href
    return href


def _duckduckgo_search(query: str, max_results: int) -> list[dict[str, str]]:
    # The lite endpoint returns parseable results over a plain GET; the full html endpoint
    # no longer does. Both are best-effort and unofficial, which is why a key (Tavily) is
    # the better path when search matters.
    url = "https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query})
    _, body = _get(url)
    parser = _DuckDuckGoParser()
    parser.feed(body)
    return parser.results[:max_results]


def search(query: str, max_results: int = 5) -> str:
    """Search the web and return titles, URLs, and snippets. Uses Tavily when
    TAVILY_API_KEY is set, otherwise a best-effort DuckDuckGo scrape."""
    api_key = os.environ.get("TAVILY_API_KEY", "")
    try:
        if api_key:
            results = _tavily_search(query, api_key, max_results)
        else:
            results = _duckduckgo_search(query, max_results)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        return f"Error searching for {query!r}: {exc}"
    return _format_results(results)
