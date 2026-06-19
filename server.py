# /// script
# requires-python = ">=3.10"
# dependencies = ["fastmcp"]
# ///

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal

# Avoid FastMCP's network-based update check, which can crash in SOCKS/proxied
# environments where the optional ``socksio`` package is not installed.
os.environ.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

CACHE_DIR = Path.home() / ".cache" / "bird-docs-query-mcp"
CACHE_TTL_SECONDS = 24 * 60 * 60

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


def fetch_text(url: str, timeout: int = 30, max_bytes: int = 10 * 1024 * 1024) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "bird-docs-query-mcp/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise RuntimeError(f"response from {url} exceeds {max_bytes} bytes")
        return data.decode("utf-8", errors="replace")


def cached_fetch(url: str, cache_name: str, refresh: bool = False) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / cache_name
    meta_path = CACHE_DIR / f"{cache_name}.meta"
    if not refresh and cache_path.exists() and meta_path.exists():
        try:
            mtime = float(meta_path.read_text(encoding="utf-8").strip())
            if time.time() - mtime < CACHE_TTL_SECONDS:
                return cache_path.read_text(encoding="utf-8")
        except Exception:
            pass
    text = fetch_text(url)
    cache_path.write_text(text, encoding="utf-8")
    meta_path.write_text(str(time.time()), encoding="utf-8")
    return text


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
    entries: list[DocEntry] = []

    if lang == "zh" and version in ("2", "auto"):
        try:
            text = cached_fetch("https://bird.xmsl.dev/llms.txt", "llms.txt", refresh)
            entries.extend(parse_llms_index(text))
        except Exception as exc:
            log(f"failed to fetch llms.txt: {exc}")

    if lang == "en" and version in ("2", "3", "auto"):
        try:
            text = cached_fetch(
                "https://raw.githubusercontent.com/bird-chinese-community/bird-doc-markdown/master/manifest.json",
                "manifest.json",
                refresh,
            )
            entries.extend(parse_markdown_manifest(text))
        except Exception as exc:
            log(f"failed to fetch manifest.json: {exc}")

    if not entries:
        versions_to_try = (
            ["2", "3"]
            if version == "auto"
            else [version]
            if version in OFFICIAL_HTML_URLS
            else []
        )
        for v in versions_to_try:
            try:
                url = OFFICIAL_HTML_URLS[v]
                text = cached_fetch(url, f"official-{v}.html", refresh)
                entries.extend(parse_official_html(text, url))
            except Exception as exc:
                log(f"failed to fetch official html for version {v}: {exc}")

    for entry in entries:
        entry.relevance = score(query, entry)

    entries.sort(key=lambda e: e.relevance, reverse=True)
    top = [e for e in entries if e.relevance > 0][:max_results]

    fallback = None
    if not top:
        if lang == "zh" and version == "3":
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
            {"lang": "en/zh", "version": "2/3", "source": "bird.nic.cz official HTML (fallback)"},
        ]
    }




if __name__ == "__main__":
    mcp.run()
