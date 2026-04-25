# Implementation Findings: MCP + ACP Multi-Agent System

## Overview

Migration of the hw8 multi-agent research system (Supervisor + Planner, Researcher, Critic) from a single-process architecture to protocol-based communication using MCP (Model Context Protocol) for tools and ACP (Agent Communication Protocol) for agent-to-agent communication.

## Architecture Achieved

```
User (REPL)
  |
  v
Supervisor (local create_agent, LangGraph)
  |
  |-- delegate_to_planner   --[ACP :8903 or A2A :8904]--> Planner --[fastmcp.Client]--> SearchMCP :8901
  |-- delegate_to_researcher --[ACP :8903 or A2A :8904]--> Researcher --[fastmcp.Client]--> SearchMCP :8901
  |-- delegate_to_critic     --[ACP :8903 or A2A :8904]--> Critic --[fastmcp.Client]--> SearchMCP :8901
  |-- save_report            --[fastmcp.Client]--> ReportMCP :8902 (HITL gated)
```

All communication goes through HTTP protocols. Each component is a separate process/container.

## Key Technical Findings

### 1. uvicorn Version Conflict (Critical)

**Problem:** `fastmcp>=3.0.0` requires `uvicorn>=0.35`. `acp-sdk 1.0.3` uses `uvicorn.config.LoopSetupType`, which was renamed to `LoopFactoryType` in uvicorn 0.36.0 (PR #2435).

**Solution:** Pin `uvicorn>=0.35.0,<0.36.0` — the only window satisfying both.

**Root cause:** acp-sdk 1.0.3 was the last release (Aug 2025, repo archived). It will never be updated to support newer uvicorn.

### 2. acp-sdk Client Serialization Bug

**Problem:** `acp_sdk.client.Client.run_sync()` sends the request body in a format the server rejects with 422 Unprocessable Content. The server endpoint works fine via curl with the same JSON payload.

**Symptom:** `ACPError: 1 validation error: Input should be a valid dictionary or object to extract fields from`

**Investigation:** The SDK client serializes the Pydantic models to bytes, but the server's FastAPI endpoint expects a JSON dict. Likely a httpx/pydantic version incompatibility in the archived SDK.

**Fix:** Subclassed `acp_sdk.client.Client` as `PatchedACPClient` (see `acp_client.py`).
The subclass wraps the internal httpx client's `post` method to inject
`Content-Type: application/json` when `content=` is used (the SDK's pattern).
This allows using the official SDK client API (`run_sync`, `Message`, etc.)
while working around the serialization bug.

```python
from acp_client import PatchedACPClient

async with PatchedACPClient(base_url=settings.acp_url) as client:
    run = await client.run_sync(agent=agent_name, input=input_messages)
```

**Note:** `acp_sdk.server.Server` works perfectly — only the client is broken.

### 3. Async Event Loop Nesting

**Problem:** LangChain's `create_agent` runs inside a LangGraph event loop. The `@tool` functions are sync, but need to call async code (ACP/MCP clients). Using `asyncio.get_event_loop().run_until_complete()` fails because there's already a running loop.

**Solution:** Run async code in a separate thread via `ThreadPoolExecutor`:
```python
_executor = ThreadPoolExecutor(max_workers=4)

def _run_async_in_thread(coro):
    future = _executor.submit(asyncio.run, coro)
    return future.result(timeout=300)
```

### 4. Docker Networking

**Problem:** Containers in docker-compose share a network but `localhost` inside a container refers to itself, not other containers.

**Solution:** Made service hostnames configurable via environment variables:
```yaml
environment:
  - SEARCH_MCP_HOST=search-mcp   # Docker service name
  - ACP_HOST=acp
```
Defaults to `localhost` for local development.

### 5. ACP Server Bind Address

**Problem:** `acp_sdk.server.Server.run()` defaults to `127.0.0.1`, making it unreachable from other Docker containers.

**Solution:** `server.run(host="0.0.0.0", port=8903)`

### 6. FastMCP HTTP Transport

**Finding:** FastMCP 3.2.3 supports `http` transport natively:
```python
mcp.run(transport="http", host="0.0.0.0", port=8901)
```
The MCP endpoint is at `/mcp` (e.g., `http://localhost:8901/mcp`).

### 7. langchain-mcp-adapters

**Finding:** `MultiServerMCPClient.get_tools()` converts MCP tools to LangChain format automatically. Works well with `create_agent`. The tools are async but LangGraph handles them correctly.

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({"search": {"url": "http://...:8901/mcp", "transport": "http"}})
tools = await client.get_tools()
agent = create_agent(model, tools=tools, ...)
```

## What Works

- [x] SearchMCP server (FastMCP) with 3 tools + KB stats resource
- [x] ReportMCP server (FastMCP) with save_report tool + output dir resource
- [x] ACP server with 3 agents (planner, researcher, critic)
- [x] Each ACP agent connects to SearchMCP via langchain-mcp-adapters
- [x] Each ACP agent uses create_agent with structured output (Planner, Critic)
- [x] Supervisor orchestrates via ACP delegation (PatchedACPClient)
- [x] Iterative Plan -> Research -> Critique -> REVISE -> Research -> APPROVE cycle
- [x] HITL on save_report via HumanInTheLoopMiddleware
- [x] Docker compose with 4 services + proper networking
- [x] Local development (localhost) and Docker (service names) both supported

## Dependency Versions (Tested)

| Package | Version | Notes |
|---------|---------|-------|
| fastmcp | 3.2.3 | MCP server + client |
| acp-sdk | 1.0.3 | ACP server + PatchedACPClient (client bug fixed via subclass) |
| uvicorn | 0.35.0 | Pinned: only version compatible with both fastmcp and acp-sdk |
| langchain-mcp-adapters | latest | MCP-to-LangChain tool conversion |
| langchain | >=1.2.0 | create_agent, response_format |
| langgraph | >=0.4.0 | checkpointer, interrupt, Command |
| langchain (middleware) | >=1.2.0 | HumanInTheLoopMiddleware for HITL |

## A2A Migration (Parallel Implementation)

### Background

ACP (Agent Communication Protocol) by IBM/BeeAI was archived at v1.0.3 in August 2025. The ACP team formally merged with Google's A2A (Agent-to-Agent) protocol under the Linux Foundation's LF AI & Data umbrella. The successor SDK is `a2a-sdk` (v1.0.1, April 2026).

### ACP → A2A Mapping

| ACP (acp-sdk 1.0.3) | A2A (a2a-sdk 1.0.1) |
|---|---|
| `acp_sdk.server.Server` + `@server.agent()` | `AgentExecutor` subclass + Starlette app |
| One server, multiple agents | One server, multiple **skills** on one `AgentCard` |
| `acp_sdk.client.Client.run_sync()` | `create_client()` + `send_message()` (async iterator) |
| `acp_sdk.models.Message`, `MessagePart` | `a2a.types.Message`, `Part` (protobuf) |
| Agent routing by name parameter | Skill routing via `metadata["skill_id"]` |
| Pydantic models | Protobuf types (`a2a.types.a2a_pb2`) |

### What A2A Fixes

1. **No client serialization bug** — `a2a-sdk` client works out of the box (no `PatchedACPClient` needed)
2. **No uvicorn version conflict** — A2A uses Starlette directly, no dependency on `LoopSetupType`
3. **Actively maintained** — Google + 50+ partners under Linux Foundation
4. **Richer protocol** — supports streaming, task lifecycle, artifacts, agent discovery via AgentCard

### Implementation Notes

- A2A server runs on port 8904 (alongside ACP on 8903)
- `AgentCard` with 3 skills replaces 3 `@server.agent()` decorators
- Skill routing via `context.metadata["skill_id"]` in the executor
- `TaskUpdater` helper manages the task lifecycle (start_work → add_artifact → complete)
- Client uses `ClientConfig(streaming=False, httpx_client=httpx.AsyncClient(timeout=120s))` — default httpx timeout is too short for LLM+MCP calls
- Protocol toggle: `AGENT_PROTOCOL=acp|a2a` in `.env`

## Lessons Learned

1. **Archived SDKs are risky** — acp-sdk is frozen at 1.0.3 with known bugs and version conflicts. Plan for workarounds.
2. **Test protocol integration early** — the client/server compatibility issue wasn't visible until runtime.
3. **Docker networking is simple but non-obvious** — `localhost` confusion is the #1 Docker networking mistake.
4. **Async/sync bridging is tricky** — LangGraph's internal event loop makes calling async code from sync tools non-trivial.
5. **Version pinning windows can be narrow** — the uvicorn 0.35.x window is exactly one minor version wide.
6. **Subclassing beats monkey-patching** — `PatchedACPClient` cleanly fixes the SDK's serialization bug while preserving the official API surface.
7. **Middleware > manual interrupt()** — `HumanInTheLoopMiddleware` is declarative and keeps HITL logic out of tool functions.
