from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp.client import Client

from server import (
    DocEntry,
    mcp,
    parse_llms_index,
    parse_markdown_manifest,
    parse_official_html,
    score,
    tokenize,
)


@pytest.fixture
async def client():
    async with Client(mcp) as c:
        yield c


async def test_list_tools(client):
    tools = await client.list_tools()
    names = [t.name for t in tools]
    assert "query_bird_docs" in names
    assert "list_bird_doc_sources" in names


async def test_list_bird_doc_sources(client):
    result = await client.call_tool("list_bird_doc_sources", {})
    assert result.data is not None
    assert any("bird.xmsl.dev" in str(s) for s in result.data["sources"])


def test_parse_llms_index():
    fixture_path = Path(__file__).parent / "fixtures" / "llms.txt"
    entries = parse_llms_index(fixture_path.read_text(encoding="utf-8"))
    assert len(entries) == 2
    assert entries[0].title == "第五章·第二节 数据类型"
    assert entries[0].url == "https://bird.xmsl.dev/docs/user-guide/5-2-data-types.md"
    assert entries[0].source == "bird.xmsl.dev"
    assert "数据类型" in entries[0].text


def test_parse_markdown_manifest():
    fixture_path = Path(__file__).parent / "fixtures" / "manifest.json"
    entries = parse_markdown_manifest(fixture_path.read_text(encoding="utf-8"))
    assert len(entries) == 2
    titles = [e.title for e in entries]
    assert "filters > data types" in titles
    assert "protocols > bgp" in titles
    assert all(e.source == "bird-doc-markdown" for e in entries)
    assert all(e.url.startswith("https://raw.githubusercontent.com/") for e in entries)


def test_parse_official_html():
    html = '<h2 id="foo">Heading</h2><p>body</p>'
    entries = parse_official_html(html, "https://bird.nic.cz/doc/bird-2.19.1.html")
    assert len(entries) == 1
    assert entries[0].title == "Heading"
    assert entries[0].url == "https://bird.nic.cz/doc/bird-2.19.1.html#foo"
    assert entries[0].source == "bird.nic.cz"
    assert entries[0].text == "body"


def test_tokenize_and_score():
    assert tokenize("The quick BGP filter") == {"quick", "bgp", "filter"}
    assert tokenize("BGP bgp") == {"bgp"}
    assert tokenize("中文 文档") == {"中文", "文档"}

    entry = DocEntry(title="BGP filter guide", url="", source="", text="BGP filters")
    assert score("BGP filter", entry) > score("OSPF", entry)
    assert score("BGP filter", entry) > 0


@pytest.mark.parametrize(
    "lang,fixture_name,query",
    [
        ("zh", "llms.txt", "数据类型"),
        ("en", "manifest.json", "data types"),
    ],
)
async def test_query_bird_docs_mocked(client, lang, fixture_name, query):
    fixtures_dir = Path(__file__).parent / "fixtures"

    def mock_fetch(url, cache_name, refresh=False):
        if cache_name == "llms.txt":
            return (fixtures_dir / "llms.txt").read_text(encoding="utf-8")
        if cache_name == "manifest.json":
            return (fixtures_dir / "manifest.json").read_text(encoding="utf-8")
        return ""

    with patch("server.cached_fetch", side_effect=mock_fetch):
        result = await client.call_tool(
            "query_bird_docs",
            {
                "query": query,
                "lang": lang,
                "version": "2",
                "max_results": 5,
            },
        )
    assert result.data is not None
    results = result.data["results"]
    assert isinstance(results, list)
    assert len(results) > 0
    if lang == "zh":
        assert any(
            "数据类型" in r["title"] or "5-2-data-types.md" in r["url"]
            for r in results
        )
    else:
        assert any(
            "data types" in r["title"].lower() or "data-types.md" in r["url"]
            for r in results
        )
