"""One-off script: verify user_id/agent_id defaulting behavior against a
running instance of the server (http transport) — specifically, that
add_memory tags both identifiers from server-side config/query-param
(user_id = personal identity, agent_id = project) while search/list
default only user_id, so a memory added under one project is still found
by a search that doesn't name a project. Point it at a local
`python server.py` (MCP_TRANSPORT=http) with MEM0_DEFAULT_USER_ID set.
Not part of the shipped image.

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
    url_project_a = f"{BASE_URL}?agent=test-project-A"
    url_project_b = f"{BASE_URL}?agent=test-project-B"

    print("=== add_memory under project A, no user_id/agent_id passed ===")
    add_result = await call(url_project_a, "add_memory", {"messages": "I only drink oat milk lattes."})
    print(add_result)
    memory_ids = [m["id"] for m in add_result.get("results", []) if "id" in m]
    assert memory_ids, "expected a memory to be created"

    print("\n=== get_memories under project A, user_id=max: agent_id should read back as test-project-A ===")
    listed = await call(url_project_a, "get_memories", {"user_id": "max"})
    print(listed)
    for m in listed["results"]:
        assert m["user_id"] == "max", f"expected user_id=max, got {m['user_id']}"

    print("\n=== search_memories under project B, no user_id/agent_id passed ===")
    print("    (should still find project A's memory — reads must not auto-filter by agent_id)")
    search_result = await call(url_project_b, "search_memories", {"query": "oat milk"})
    print(search_result)
    found_ids = {r["id"] for r in search_result.get("results", [])}
    assert set(memory_ids) & found_ids, "cross-project recall failed: project B couldn't find project A's memory"
    print("OK: a search under project B found the memory stored under project A, without either naming a project.")

    print("\n=== cleanup ===")
    for mid in memory_ids:
        res = await call(url_project_a, "delete_memory", {"memory_id": mid})
        print(mid, "->", res)


if __name__ == "__main__":
    asyncio.run(main())
