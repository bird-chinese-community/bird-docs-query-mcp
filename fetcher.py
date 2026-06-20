# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

"""HTTP fetch helpers with retry, caching, and explicit error reporting."""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FetchResult:
    """Result of a single fetch attempt.

    Attributes:
        ok: Whether the fetch succeeded and returned usable text.
        text: The decoded response body (empty if ``ok`` is False).
        error: Human-readable error message when ``ok`` is False.
        source: Logical source name for status reporting (e.g. "bird.nic.cz").
        used_cache: True when the response came from the local cache.
    """

    ok: bool
    text: str
    error: str | None
    source: str
    used_cache: bool = False


CACHE_DIR = Path.home() / ".cache" / "bird-docs-query-mcp"
CACHE_TTL_SECONDS = 24 * 60 * 60


def log(message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def fetch_text(
    url: str,
    *,
    source: str = "",
    retries: int = 3,
    timeout: int = 30,
    max_bytes: int = 10 * 1024 * 1024,
) -> FetchResult:
    """Fetch ``url`` with retries and bounded response size.

    Implements exponential backoff (1s, 2s, 4s) between retries.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "bird-docs-query-mcp/1.0"})
    last_error = ""

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read(max_bytes + 1)
                if len(data) > max_bytes:
                    return FetchResult(
                        ok=False,
                        text="",
                        error=f"response from {url} exceeds {max_bytes} bytes",
                        source=source,
                    )
                return FetchResult(
                    ok=True,
                    text=data.decode("utf-8", errors="replace"),
                    error=None,
                    source=source,
                )
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code} for {url}"
        except urllib.error.URLError as exc:
            last_error = f"URL error for {url}: {exc.reason}"
        except TimeoutError:
            last_error = f"timeout fetching {url}"
        except Exception as exc:
            last_error = f"error fetching {url}: {exc}"

        if attempt < retries - 1:
            delay = 2 ** attempt
            log(f"fetch retry for {url} after {delay}s: {last_error}")
            time.sleep(delay)

    return FetchResult(ok=False, text="", error=last_error, source=source)


def cached_fetch(
    url: str,
    cache_name: str,
    *,
    source: str = "",
    refresh: bool = False,
) -> FetchResult:
    """Fetch ``url`` through a 24-hour on-disk cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / cache_name
    meta_path = CACHE_DIR / f"{cache_name}.meta"

    if not refresh and cache_path.exists() and meta_path.exists():
        try:
            mtime = float(meta_path.read_text(encoding="utf-8").strip())
            if time.time() - mtime < CACHE_TTL_SECONDS:
                return FetchResult(
                    ok=True,
                    text=cache_path.read_text(encoding="utf-8"),
                    error=None,
                    source=source,
                    used_cache=True,
                )
        except Exception:
            pass

    result = fetch_text(url, source=source)
    if result.ok:
        cache_path.write_text(result.text, encoding="utf-8")
        meta_path.write_text(str(time.time()), encoding="utf-8")
    return result
