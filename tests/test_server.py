import pytest
from fastmcp.client import Client

from server import mcp


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


async def test_query_bird_docs(client):
    result = await client.call_tool(
        "query_bird_docs",
        {
            "query": "BGP filter data types",
            "lang": "en",
            "version": "2",
            "max_results": 3,
        },
    )
    assert result.data is not None
    results = result.data["results"]
    assert isinstance(results, list)
    assert 0 < len(results) <= 3

    for item in results:
        assert set(item.keys()) >= {"title", "url", "source", "relevance"}

    top = results[0]
    text = f"{top['title']} {top['url']}".lower()
    assert any(k in text for k in ("data type", "bgp", "filter"))
