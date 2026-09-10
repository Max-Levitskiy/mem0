"""One-off script: verify user_id/agent_id defaulting behavior against a
running instance of the server (http transport) — specifically, that
add_memory tags both identifiers from server-side config/query-param while
search/list default only user_id, so a memory added by one client is still
found by a different one with neither side needing to coordinate
identifiers. Point it at a local `python server.py` (MCP_TRANSPORT=http)
with MEM0_DEFAULT_USER_ID set. Not part of the shipped image.

    MEM0_BASE_URL=https://your-deployment MEM0_API_KEY=... \
    MCP_TRANSPORT=http MCP_BEARER_TOKEN=test MCP_PORT=8899 \
    MEM0_DEFAULT_USER_ID=max python server.py &
    python test_identity_defaults.py [base_url] [bearer_token]
"""

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8899/mcp"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else "local-test-token-xyz"


async def call(url: str, tool: str, args: dict):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            text = res.content[0].text if res.content else "{}"
            return json.loads(text)


async def main():
    url_a = f"{BASE_URL}?agent=test-client-A"
    url_b = f"{BASE_URL}?agent=test-client-B"

    print("=== add_memory via client A, no user_id/agent_id passed ===")
    add_result = await call(url_a, "add_memory", {"messages": "I only drink oat milk lattes."})
    print(add_result)
    memory_ids = [m["id"] for m in add_result.get("results", []) if "id" in m]
    assert memory_ids, "expected a memory to be created"

    print("\n=== get_memories via client A, user_id=max: agent_id should read back as test-client-A ===")
    listed = await call(url_a, "get_memories", {"user_id": "max"})
    print(listed)
    for m in listed["results"]:
        assert m["user_id"] == "max", f"expected user_id=max, got {m['user_id']}"

    print("\n=== search_memories via client B (different agent), no user_id/agent_id passed ===")
    print("    (should still find it — reads must not auto-filter by agent_id)")
    search_result = await call(url_b, "search_memories", {"query": "oat milk"})
    print(search_result)
    found_ids = {r["id"] for r in search_result.get("results", [])}
    assert set(memory_ids) & found_ids, "cross-client recall failed: client B couldn't find client A's memory"
    print("OK: client B found the memory client A stored, without either specifying agent_id.")

    print("\n=== cleanup ===")
    for mid in memory_ids:
        res = await call(url_a, "delete_memory", {"memory_id": mid})
        print(mid, "->", res)


if __name__ == "__main__":
    asyncio.run(main())
