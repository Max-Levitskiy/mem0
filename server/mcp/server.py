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
  MEM0_DEFAULT_USER_ID   Applied when a tool call doesn't pass user_id. This
                      is a single-user deployment, so every client should
                      write to and read from the same user_id — otherwise
                      "remember X in Claude Code, recall it in ChatGPT" only
                      works if every client happens to pass an identical
                      value, which isn't something an LLM reliably does on
                      its own. Changing this later orphans memories stored
                      under the old value; pick a stable value up front.
  MEM0_AGENT_ID       Fallback when a tool call doesn't pass agent_id and the
                      request has no `agent` query param either (see below).
                      This is the project/repo identity, matching the mem0
                      agent-plugin convention (one namespace per project) —
                      not "which AI client called this".

agent_id means project, sourced per transport:

  stdio (Claude Code, Codex, Pi/OMP — anything launched from a terminal):
  set MEM0_AGENT_ID in that project's .envrc (direnv). Since these tools
  are (re)launched fresh from within the project directory, the spawned
  server.py process inherits whatever .envrc exported for that shell —
  no per-project MCP config needed, just one line per project's .envrc:

      export MEM0_AGENT_ID=<project-name>

  http (Claude Desktop, ChatGPT, or any client not launched per-project):
  there's no shell to inherit from — these are persistent apps with no
  concept of "current directory". Two options, in order of reliability:
    - a `?agent=<project>` query param on that client's MCP URL, for a
      client you only ever use for one fixed project;
    - a custom-instruction / system-prompt line telling the model to pass
      agent_id explicitly when a conversation is clearly project-specific
      (see server/mcp/README or ask the deployer for the exact wording).
  Whichever wins, it's still just a default — an explicit agent_id in the
  tool call always overrides both.

user_id is NOT sourced from either of these — it must stay identical
across every client and every project for recall to work, which is
exactly what MEM0_DEFAULT_USER_ID guarantees regardless of source.

For local stdio use (a client's own MCP config, not the compose service),
drop a .env file next to this script with MEM0_BASE_URL/MEM0_API_KEY set to
your deployment's public URL and an API key from its dashboard — it's
loaded automatically. The deployed container gets its env from Docker/
Coolify directly, so this is a no-op there.
"""

import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import Context, FastMCP

load_dotenv(Path(__file__).parent / ".env")

MEM0_BASE_URL = os.environ.get("MEM0_BASE_URL", "http://mem0:8000").rstrip("/")
MEM0_API_KEY = os.environ.get("MEM0_API_KEY", "")
MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
MCP_BEARER_TOKEN = os.environ.get("MCP_BEARER_TOKEN", "")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))
MEM0_DEFAULT_USER_ID = os.environ.get("MEM0_DEFAULT_USER_ID", "")
MEM0_AGENT_ID = os.environ.get("MEM0_AGENT_ID", "")

if not MEM0_API_KEY:
    raise RuntimeError("MEM0_API_KEY is required")
if MCP_TRANSPORT == "http" and not MCP_BEARER_TOKEN:
    raise RuntimeError("MCP_BEARER_TOKEN is required when MCP_TRANSPORT=http")

mcp = FastMCP("mem0-self-hosted", host="0.0.0.0", port=MCP_PORT)


def _project_agent_id(ctx: Context) -> str | None:
    """agent_id (project identity) from MEM0_AGENT_ID — set per-project via
    .envrc for a stdio client — or a `?agent=` query param for an http
    client with no shell/cwd to inherit an env var from."""
    request = getattr(ctx.request_context, "request", None) if ctx else None
    if request is not None:
        from_query = request.query_params.get("agent")
        if from_query:
            return from_query
    return MEM0_AGENT_ID or None


def _resolve_write_ids(
    ctx: Context, user_id: str | None, agent_id: str | None, run_id: str | None
) -> tuple[str | None, str | None, str | None]:
    """For add_memory: default both user_id (who) and agent_id (which project),
    so every write is consistently tagged without the caller having to know
    or pass either."""
    resolved_user_id = user_id or MEM0_DEFAULT_USER_ID or None
    resolved_agent_id = agent_id or _project_agent_id(ctx)
    return resolved_user_id, resolved_agent_id, run_id


def _resolve_read_ids(
    ctx: Context, user_id: str | None, agent_id: str | None, run_id: str | None
) -> tuple[str | None, str | None, str | None]:
    """For search/list: default ONLY user_id, deliberately leaving agent_id
    unfiltered (None) unless the caller passes one explicitly. Mirrors the
    mem0 agent-plugin model this project_id convention comes from: a search
    is the union of project-scoped memory and cross-project personal
    memory, not project-scoped alone — auto-filtering by the current
    project here would hide the general "I prefer X" facts that should
    surface no matter which project you're currently in. Pass agent_id
    explicitly only to deliberately narrow to one project's memories."""
    resolved_user_id = user_id or MEM0_DEFAULT_USER_ID or None
    return resolved_user_id, agent_id, run_id


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
    ctx: Context,
    messages: str | list[dict[str, str]],
    user_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    infer: bool | None = None,
) -> Any:
    """Save text or a conversation to Mem0. `messages` can be a plain string
    (stored as a single user message) or a list of {role, content} dicts for
    a full conversation. user_id and agent_id default to this deployment's
    configured identity if omitted — pass them explicitly only to deviate
    from that (e.g. a specific run_id-scoped session)."""
    user_id, agent_id, run_id = _resolve_write_ids(ctx, user_id, agent_id, run_id)
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
    ctx: Context,
    query: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    top_k: int | None = None,
    threshold: float | None = None,
) -> Any:
    """Semantic search across stored memories. user_id defaults to this
    deployment's configured identity if omitted, so a plain call searches
    everything stored for that identity regardless of which project it was
    stored under. Pass agent_id to narrow to one project's memories, or
    run_id to narrow further to one session."""
    user_id, agent_id, run_id = _resolve_read_ids(ctx, user_id, agent_id, run_id)
    filters = {k: v for k, v in (("user_id", user_id), ("agent_id", agent_id), ("run_id", run_id)) if v}
    body: dict[str, Any] = {"query": query, "filters": filters}
    if top_k is not None:
        body["top_k"] = top_k
    if threshold is not None:
        body["threshold"] = threshold
    return _request("POST", "/search", json=body)


@mcp.tool()
def get_memories(
    ctx: Context,
    user_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    top_k: int | None = None,
) -> Any:
    """List memories. user_id defaults to this deployment's configured
    identity if omitted, listing everything stored for that identity
    regardless of which project it was stored under. Pass agent_id to
    narrow to one project's memories, or run_id to narrow further to one
    session."""
    user_id, agent_id, run_id = _resolve_read_ids(ctx, user_id, agent_id, run_id)
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
    (at least one required — deliberately not defaulted like the read/write
    tools, so this always needs an explicit, deliberate target instead of
    silently becoming "wipe everything for the default identity" when
    called with no arguments). Requires an admin API key on the server."""
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
