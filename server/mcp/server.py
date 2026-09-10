"""MCP server for a self-hosted Mem0 deployment.

Wraps the self-hosted REST API (server/main.py — plain /memories, /search,
unversioned, X-API-Key auth) as MCP tools. The official Mem0 MCP server
(mcp.mem0.ai) and its agent plugins only talk to the hosted Mem0 Platform's
versioned API (/v1, /v2, /v3); a self-hosted deployment doesn't implement
that surface, so those clients can't reach it. This exists to bridge that
gap for this specific deployment, over either transport:

  MCP_TRANSPORT=stdio   local process, spawned by a client's own config
  MCP_TRANSPORT=http    streamable-http, for a deployed/remote server

Config (env vars):
  MEM0_BASE_URL      Base URL of the self-hosted REST API.
                      Defaults to http://mem0:8000 (the compose service name).
  MEM0_API_KEY       X-API-Key this server uses to call that API. Required.
  MCP_TRANSPORT      stdio (default) or http.
  MCP_BEARER_TOKEN   Required when MCP_TRANSPORT=http. Static bearer token
                      clients must send; guards against unauthenticated
                      internet traffic hitting (and billing) this endpoint.
  MCP_PORT           Port to listen on for http transport. Default 8000.
"""

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

MEM0_BASE_URL = os.environ.get("MEM0_BASE_URL", "http://mem0:8000").rstrip("/")
MEM0_API_KEY = os.environ.get("MEM0_API_KEY", "")
MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
MCP_BEARER_TOKEN = os.environ.get("MCP_BEARER_TOKEN", "")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))

if not MEM0_API_KEY:
    raise RuntimeError("MEM0_API_KEY is required")
if MCP_TRANSPORT == "http" and not MCP_BEARER_TOKEN:
    raise RuntimeError("MCP_BEARER_TOKEN is required when MCP_TRANSPORT=http")

mcp = FastMCP("mem0-self-hosted", host="0.0.0.0", port=MCP_PORT)

_client = httpx.Client(
    base_url=MEM0_BASE_URL,
    headers={"X-API-Key": MEM0_API_KEY, "Content-Type": "application/json"},
    timeout=30.0,
)


def _request(method: str, path: str, **kwargs) -> Any:
    resp = _client.request(method, path, **kwargs)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise RuntimeError(f"Mem0 server returned {resp.status_code}: {detail}")
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


def _normalize_messages(messages: str | list[dict[str, str]]) -> list[dict[str, str]]:
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    return messages


@mcp.tool()
def add_memory(
    messages: str | list[dict[str, str]],
    user_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    infer: bool | None = None,
) -> Any:
    """Save text or a conversation to Mem0. `messages` can be a plain string
    (stored as a single user message) or a list of {role, content} dicts for
    a full conversation. At least one of user_id, agent_id, run_id is required."""
    if not any([user_id, agent_id, run_id]):
        raise RuntimeError("At least one of user_id, agent_id, run_id is required.")
    body: dict[str, Any] = {"messages": _normalize_messages(messages)}
    for key, value in (
        ("user_id", user_id),
        ("agent_id", agent_id),
        ("run_id", run_id),
        ("metadata", metadata),
        ("infer", infer),
    ):
        if value is not None:
            body[key] = value
    return _request("POST", "/memories", json=body)


@mcp.tool()
def search_memories(
    query: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    top_k: int | None = None,
    threshold: float | None = None,
) -> Any:
    """Semantic search across stored memories. Scope the search with at least
    one of user_id, agent_id, run_id via the filters they map to."""
    filters = {k: v for k, v in (("user_id", user_id), ("agent_id", agent_id), ("run_id", run_id)) if v}
    body: dict[str, Any] = {"query": query, "filters": filters}
    if top_k is not None:
        body["top_k"] = top_k
    if threshold is not None:
        body["threshold"] = threshold
    return _request("POST", "/search", json=body)


@mcp.tool()
def get_memories(
    user_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    top_k: int | None = None,
) -> Any:
    """List memories for a given user_id, agent_id, or run_id (at least one required)."""
    if not any([user_id, agent_id, run_id]):
        raise RuntimeError("At least one of user_id, agent_id, run_id is required.")
    params = {k: v for k, v in (("user_id", user_id), ("agent_id", agent_id), ("run_id", run_id)) if v}
    if top_k is not None:
        params["top_k"] = top_k
    return _request("GET", "/memories", params=params)


@mcp.tool()
def get_memory(memory_id: str) -> Any:
    """Retrieve a single memory by its ID."""
    return _request("GET", f"/memories/{memory_id}")


@mcp.tool()
def get_memory_history(memory_id: str) -> Any:
    """Get the change history for a single memory."""
    return _request("GET", f"/memories/{memory_id}/history")


@mcp.tool()
def update_memory(
    memory_id: str,
    text: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Update a memory's text and/or metadata. Confirm the memory_id (e.g. via
    search_memories) before calling this — it overwrites in place."""
    body: dict[str, Any] = {}
    if text is not None:
        body["text"] = text
    if metadata is not None:
        body["metadata"] = metadata
    if not body:
        raise RuntimeError("Provide text and/or metadata to update.")
    return _request("PUT", f"/memories/{memory_id}", json=body)


@mcp.tool()
def delete_memory(memory_id: str) -> Any:
    """Delete a single memory by its ID."""
    return _request("DELETE", f"/memories/{memory_id}")


@mcp.tool()
def delete_all_memories(
    user_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
) -> Any:
    """Bulk-delete all memories for a given user_id, agent_id, or run_id
    (at least one required). Requires an admin API key on the server."""
    if not any([user_id, agent_id, run_id]):
        raise RuntimeError("At least one of user_id, agent_id, run_id is required.")
    params = {k: v for k, v in (("user_id", user_id), ("agent_id", agent_id), ("run_id", run_id)) if v}
    return _request("DELETE", "/memories", params=params)


def _build_http_app():
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.headers.get("authorization") != f"Bearer {MCP_BEARER_TOKEN}":
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return await call_next(request)

    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware)
    return app


if __name__ == "__main__":
    if MCP_TRANSPORT == "http":
        import uvicorn

        uvicorn.run(_build_http_app(), host="0.0.0.0", port=MCP_PORT)
    else:
        mcp.run(transport="stdio")
