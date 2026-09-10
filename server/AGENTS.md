# Self-hosted server (`server/`)

FastAPI REST server wrapping the Python SDK. Docker only; there is no local non-Docker path.

## Commands

```bash
# Production image
make build        # docker build -t mem0-api-server .
make run_local    # docker run -p 8000:8000 with .env

# Development stack (FastAPI + PostgreSQL/pgvector + Neo4j)
docker-compose up
```

| Service | Port |
|---------|------|
| mem0 API | 8888 |
| PostgreSQL (pgvector) | 8432 |
| mem0-mcp (optional, `mcp/`) | 8767 |
| Neo4j HTTP | 8474 |
| Neo4j Bolt | 8687 |

## Conventions

- **Framework:** FastAPI on uvicorn, auto-reload in dev.
- **Stores:** PostgreSQL with the pgvector extension, Neo4j 5.x with the APOC plugin.
- **Hot reload:** `server/` is bind-mounted, so server code edits take effect on save (uvicorn `--reload`). `mem0/` is copied into the image at build time and installed editable, not bind-mounted — SDK edits need `docker-compose up --build` to take effect. The compose command must not reinstall `mem0ai` from PyPI at runtime; that overwrites the editable install with whatever's on PyPI, silently discarding any local/uncommitted SDK changes.
- Use Docker Compose for local work. Do not add a "run it with uvicorn directly" path.
- The server imports the Python SDK from the repo, so its conventions apply to any SDK code you touch: see [`../mem0/AGENTS.md`](../mem0/AGENTS.md).

Never commit `.env`. Credentials for the compose services belong in `.env.example` as placeholders only.

## MCP server (`mcp/`, optional)

Wraps the self-hosted REST API as MCP tools, for clients that speak MCP but
not this server's REST shape (the official Mem0 MCP server and agent plugins
only target the hosted Mem0 Platform's versioned API). Its own service in
`docker-compose.yaml`, not merged into `main.py` — the goal is zero diff on
files upstream (`mem0ai/mem0`) actively maintains, since this is a fork with
its own deployment concerns. See [`mcp/server.py`](mcp/server.py)'s module
docstring for its env vars. `MCP_TRANSPORT=stdio` runs it as a local process
for a client's own MCP config; `http` (what the compose service runs) serves
streamable-http. No separate server secret either way — a caller's own
dashboard-issued mem0 API key (Settings > API Keys) is the credential,
sent as `Authorization: Bearer <key>` for http or `MEM0_API_KEY` in env
for stdio, used directly as `X-API-Key` against the REST API. Mint one
labeled key per client so each is independently revocable.
