# /// script
# requires-python = ">=3.10"
# dependencies = ["fastmcp>=3,<4"]
# ///

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal

# Avoid FastMCP's network-based update check, which can crash in SOCKS/proxied
# environments where the optional ``socksio`` package is not installed.
os.environ.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")
# Keep stdio transport clean of startup banners that some MCP hosts treat as errors.
os.environ.setdefault("FASTMCP_SHOW_SERVER_BANNER", "false")

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from fetcher import FetchResult, cached_fetch

OFFICIAL_HTML_URLS = {
    "2": "https://bird.nic.cz/doc/bird-2.19.1.html",
    "3": "https://bird.nic.cz/doc/bird-3.3.1.html",
}

mcp = FastMCP(
    "BIRD Docs Query",
    instructions=(
        "Query BIRD routing-daemon documentation. Use query_bird_docs() to find "
        "relevant sections, then fetch the returned URLs for details."
    ),
)


@dataclass
class DocEntry:
    title: str
    url: str
    source: str
    text: str = ""
    relevance: float = 0.0


def log(message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def parse_llms_index(text: str) -> list[DocEntry]:
    entries: list[DocEntry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        m = re.match(r"- \[(?P<title>[^\]]+)\]\((?P<path>[^)]+)\):?\s*(?P<desc>.*)", line)
        if not m:
            continue
        title = m.group("title")
        path = m.group("path")
        desc = m.group("desc")
        url = f"https://bird.xmsl.dev{path}"
        entries.append(
            DocEntry(title=title, url=url, source="bird.xmsl.dev", text=f"{title} {desc}")
        )
    return entries


def parse_markdown_manifest(text: str) -> list[DocEntry]:
    data = json.loads(text)
    entries: list[DocEntry] = []
    base = "https://raw.githubusercontent.com/bird-chinese-community/bird-doc-markdown/master"
    for item in data.get("files", []):
        if not isinstance(item, dict):
            continue
        path = item.get("path", "")
        if not path.endswith(".md"):
            continue
        parts = Path(path).parts
        title_parts = [p.replace("-", " ") for p in parts[1:-1]]
        filename = Path(path).stem.replace("-", " ")
        if filename == "index":
            title = " > ".join(title_parts) if title_parts else "index"
        else:
            title_parts.append(filename)
            title = " > ".join(title_parts)
        entries.append(
            DocEntry(title=title, url=f"{base}/{path}", source="bird-doc-markdown", text=title)
        )
    return entries


class HeadingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sections: list[tuple[str, str, str, str]] = []
        self._current_tag: str | None = None
        self._current_anchor: str = ""
        self._current_heading: str = ""
        self._current_body: str = ""
        self._capture_heading = False
        self._skip_depth = 0

    def _flush(self) -> None:
        heading = self._current_heading.strip()
        if self._current_tag and heading:
            self.sections.append(
                (
                    self._current_tag,
                    self._current_anchor,
                    heading,
                    self._current_body.strip(),
                )
            )
        self._current_tag = None
        self._current_anchor = ""
        self._current_heading = ""
        self._current_body = ""
        self._capture_heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1
            return
        if tag in ("h1", "h2", "h3"):
            self._flush()
            self._current_tag = tag
            self._current_anchor = ""
            for name, value in attrs:
                if name == "id":
                    self._current_anchor = value or ""
                    break
            self._current_heading = ""
            self._current_body = ""
            self._capture_heading = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in ("h1", "h2", "h3") and self._capture_heading:
            self._capture_heading = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._capture_heading:
            self._current_heading += data
        elif self._current_tag is not None:
            self._current_body += data

    def close(self) -> None:
        self._flush()
        super().close()


def parse_official_html(text: str, base_url: str) -> list[DocEntry]:
    parser = HeadingParser()
    parser.feed(text)
    parser.close()
    entries: list[DocEntry] = []
    for _tag, anchor, heading, body in parser.sections:
        url = f"{base_url}#{anchor}" if anchor else base_url
        entries.append(
            DocEntry(title=heading, url=url, source="bird.nic.cz", text=body)
        )
    return entries


STOP_WORDS = {"the", "a", "an", "in", "on", "at", "to", "of", "and", "or", "is", "are"}


def tokenize(text: str) -> set[str]:
    return {
        t.lower()
        for t in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", text)
        if t.lower() not in STOP_WORDS and len(t) > 1
    }


def score(query: str, entry: DocEntry) -> float:
    q_tokens = tokenize(query)
    if not q_tokens:
        return 0.0
    text_tokens = tokenize(entry.text)
    title_tokens = tokenize(entry.title)
    matches_in_text = len(q_tokens & text_tokens)
    matches_in_title = len(q_tokens & title_tokens)
    raw = matches_in_text + matches_in_title * 3.0
    return raw / (len(q_tokens) * 3.0)


def _query_docs(
    query: str,
    lang: str,
    version: str,
    max_results: int,
    refresh: bool,
) -> dict[str, Any]:
    max_results = max(1, min(50, max_results))
    entries: list[DocEntry] = []
    sources: list[dict[str, Any]] = []

    def add_source(name: str, result: FetchResult) -> None:
        status: dict[str, Any] = {
            "source": name,
            "ok": result.ok,
            "used_cache": result.used_cache,
        }
        if result.error:
            status["error"] = result.error
        sources.append(status)

    if lang == "zh" and version in ("2", "auto"):
        result = cached_fetch(
            "https://bird.xmsl.dev/llms.txt",
            "llms.txt",
            source="bird.xmsl.dev",
            refresh=refresh,
        )
        add_source("bird.xmsl.dev", result)
        if result.ok:
            entries.extend(parse_llms_index(result.text))

    if lang == "en" and version in ("2", "3", "auto"):
        result = cached_fetch(
            "https://raw.githubusercontent.com/bird-chinese-community/bird-doc-markdown/master/manifest.json",
            "manifest.json",
            source="bird-doc-markdown",
            refresh=refresh,
        )
        add_source("bird-doc-markdown", result)
        if result.ok:
            entries.extend(parse_markdown_manifest(result.text))

    if not entries:
        versions_to_try = (
            ["2", "3"]
            if version == "auto"
            else [version]
            if version in OFFICIAL_HTML_URLS
            else []
        )
        for v in versions_to_try:
            url = OFFICIAL_HTML_URLS[v]
            result = cached_fetch(url, f"official-{v}.html", source="bird.nic.cz", refresh=refresh)
            add_source("bird.nic.cz", result)
            if result.ok:
                entries.extend(parse_official_html(result.text, url))

    for entry in entries:
        entry.relevance = score(query, entry)

    entries.sort(key=lambda e: e.relevance, reverse=True)
    top = [e for e in entries if e.relevance > 0][:max_results]

    fallback = None
    if not top:
        if not any(s["ok"] for s in sources):
            fallback = "All documentation sources are currently unavailable. Please try again later."
        elif lang == "zh" and version == "3":
            fallback = (
                "Chinese BIRD3 community snapshot not available; "
                "try English BIRD3 results or official HTML."
            )
        else:
            fallback = "No matching docs found. Try broadening the query."

    return {
        "query": query,
        "lang": lang,
        "version": version,
        "results": [
            {
                "title": e.title,
                "url": e.url,
                "source": e.source,
                "relevance": round(e.relevance, 3),
            }
            for e in top
        ],
        "fallback": fallback,
        "sources": sources,
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
def query_bird_docs(
    query: str,
    lang: Literal["zh", "en"] = "en",
    version: Literal["2", "3", "auto"] = "auto",
    max_results: int = 5,
    refresh: bool = False,
) -> dict[str, Any]:
    """Find the most relevant BIRD documentation sections for a query.

    Args:
        query: Natural-language question or keyword (e.g. "BGP filter data types").
        lang: Preferred language - "zh" or "en".
        version: BIRD major version - "2", "3", or "auto".
        max_results: Maximum number of sections to return.
        refresh: Bypass the local 24-hour cache and re-fetch indexes.
    """
    return _query_docs(query, lang, version, max_results, refresh)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def list_bird_doc_sources() -> dict[str, Any]:
    """List available BIRD documentation sources."""
    return {
        "sources": [
            {"lang": "zh", "version": "2", "source": "bird.xmsl.dev/llms.txt"},
            {"lang": "en", "version": "2/3", "source": "bird-doc-markdown"},
            {"lang": "en", "version": "2/3", "source": "bird.nic.cz official HTML (fallback)"},
        ]
    }


if __name__ == "__main__":
    mcp.run()
