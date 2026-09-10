"""One-off script: exercise the deployed HTTP MCP server end to end. TOKEN
must be a real mem0 API key minted from the deployment's dashboard
(Settings > API Keys) — there's no separate MCP-only secret; the caller's
own bearer token is used directly as X-API-Key against the REST API. Not
part of the shipped image.

    python test_http_live.py [url] [mem0-api-key]
"""

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8767/mcp"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else ""

if not TOKEN:
    raise SystemExit("Usage: python test_http_live.py [url] <mem0-api-key>")


async def try_without_auth():
    print("=== no bearer token (should fail) ===")
    try:
        async with streamablehttp_client(URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
        print("UNEXPECTED: connected without auth!")
    except Exception as e:
        print(f"OK, rejected as expected: {type(e).__name__}: {e}")


async def try_with_auth():
    print("\n=== with bearer token ===")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with streamablehttp_client(URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools])

            res = await session.call_tool(
                "add_memory",
                {"messages": "I like to hike on weekends.", "user_id": "mcp-http-verify"},
            )
            add_text = res.content[0].text if res.content else "{}"
            print("add_memory:", add_text)
            add_data = json.loads(add_text)
            ids = [m["id"] for m in add_data.get("results", []) if "id" in m]

            res = await session.call_tool("search_memories", {"query": "hiking", "user_id": "mcp-http-verify"})
            print("search_memories:", res.content[0].text if res.content else res)

            for mid in ids:
                res = await session.call_tool("delete_memory", {"memory_id": mid})
                print("cleanup:", mid, "->", res.content[0].text if res.content else res)


async def main():
    await try_without_auth()
    await try_with_auth()


if __name__ == "__main__":
    asyncio.run(main())
