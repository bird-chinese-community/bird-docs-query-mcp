import io
from pathlib import Path
from unittest.mock import patch

import urllib.error

import pytest

from fetcher import FetchResult, cached_fetch, fetch_text


def _body(data: bytes) -> io.BytesIO:
    """Return a file-like object that ``urllib.request.urlopen`` can yield."""
    return io.BytesIO(data)


def test_fetch_text_retries_then_succeeds():
    side_effects = [
        urllib.error.HTTPError("url", 503, "Service Unavailable", {}, None),
        urllib.error.HTTPError("url", 503, "Service Unavailable", {}, None),
        _body(b"hello"),
    ]

    with patch("fetcher.urllib.request.urlopen", side_effect=side_effects) as mock:
        with patch("fetcher.time.sleep"):
            result = fetch_text("https://example.com/doc", source="test")

    assert result.ok is True
    assert result.text == "hello"
    assert result.error is None
    assert mock.call_count == 3


def test_fetch_text_retries_exhausted():
    exc = urllib.error.HTTPError("url", 500, "Error", {}, None)
    with patch("fetcher.urllib.request.urlopen", side_effect=[exc, exc, exc]) as mock:
        with patch("fetcher.time.sleep"):
            result = fetch_text("https://example.com/doc", source="test")

    assert result.ok is False
    assert "HTTP 500" in result.error
    assert mock.call_count == 3


def test_fetch_text_timeout_eventually_fails():
    with patch(
        "fetcher.urllib.request.urlopen", side_effect=[TimeoutError, TimeoutError, TimeoutError]
    ) as mock:
        with patch("fetcher.time.sleep"):
            result = fetch_text("https://example.com/doc", source="test")

    assert result.ok is False
    assert "timeout" in result.error
    assert mock.call_count == 3


def test_fetch_text_exceeds_max_bytes():
    with patch(
        "fetcher.urllib.request.urlopen", return_value=_body(b"x" * (1024 + 1))
    ):
        result = fetch_text("https://example.com/doc", source="test", max_bytes=1024)

    assert result.ok is False
    assert "exceeds" in result.error


def test_cached_fetch_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("fetcher.CACHE_DIR", tmp_path)
    cache_path = tmp_path / "foo.txt"
    meta_path = tmp_path / "foo.txt.meta"
    cache_path.write_text("cached", encoding="utf-8")
    meta_path.write_text("9999999999.0", encoding="utf-8")

    with patch("fetcher.fetch_text") as mock_fetch:
        result = cached_fetch("https://example.com/doc", "foo.txt", source="test")

    assert result.ok is True
    assert result.text == "cached"
    assert result.used_cache is True
    mock_fetch.assert_not_called()


def test_cached_fetch_falls_back_to_network(tmp_path, monkeypatch):
    monkeypatch.setattr("fetcher.CACHE_DIR", tmp_path)

    with patch(
        "fetcher.fetch_text",
        return_value=FetchResult(ok=True, text="fresh", error=None, source="test"),
    ) as mock_fetch:
        result = cached_fetch("https://example.com/doc", "foo.txt", source="test")

    assert result.ok is True
    assert result.text == "fresh"
    assert result.used_cache is False
    mock_fetch.assert_called_once()


def test_cached_fetch_propagates_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("fetcher.CACHE_DIR", tmp_path)

    with patch(
        "fetcher.fetch_text",
        return_value=FetchResult(ok=False, text="", error="HTTP 503", source="test"),
    ) as mock_fetch:
        result = cached_fetch("https://example.com/doc", "foo.txt", source="test")

    assert result.ok is False
    assert "503" in result.error
    mock_fetch.assert_called_once()
