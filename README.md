# BIRD Docs Query MCP

A stdio MCP server that finds relevant BIRD routing-daemon documentation sections.

## Usage

```json
{
  "mcpServers": {
    "bird-docs-query": {
      "command": "uv",
      "args": ["run", "server.py"]
    }
  }
}
```

> Run the command from the repository root so that `server.py` resolves correctly.

## Tools

- `query_bird_docs(query, lang="en", version="auto", max_results=5, refresh=false)`
- `list_bird_doc_sources()`

## Data sources

- Chinese BIRD2: https://bird.xmsl.dev/llms.txt
- English BIRD2/BIRD3: https://github.com/bird-chinese-community/bird-doc-markdown
- Official HTML fallback: https://bird.nic.cz/doc/bird-{version}.html
