import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SERVER = Path(__file__).parent.parent / "server.py"


def test_stdio_initialize():
    """Verify the stdio MCP server responds to a JSON-RPC initialize request."""
    env = {
        **os.environ,
        "FASTMCP_SHOW_SERVER_BANNER": "false",
        "FASTMCP_CHECK_FOR_UPDATES": "off",
    }
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(SERVER.parent),
    )

    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"},
            },
        }
    )

    try:
        proc.stdin.write((payload + "\n").encode("utf-8"))
        proc.stdin.flush()
        proc.stdin.close()

        deadline = time.time() + 15
        response = None
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if msg.get("id") == 1:
                response = msg
                break

        assert response is not None, "did not receive initialize response"
        assert response["jsonrpc"] == "2.0"
        assert "result" in response
        assert "capabilities" in response["result"]
        assert "serverInfo" in response["result"]
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        assert proc.returncode == 0
