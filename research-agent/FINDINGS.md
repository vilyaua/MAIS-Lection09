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
  |-- delegate_to_planner   --[httpx]--> ACP :8903 --> Planner --[fastmcp.Client]--> SearchMCP :8901
  |-- delegate_to_researcher --[httpx]--> ACP :8903 --> Researcher --[fastmcp.Client]--> SearchMCP :8901
  |-- delegate_to_critic     --[httpx]--> ACP :8903 --> Critic --[fastmcp.Client]--> SearchMCP :8901
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

**Workaround:** Replaced `acp_sdk.client.Client` with direct `httpx.AsyncClient` calls:
```python
async with httpx.AsyncClient(timeout=300) as client:
    resp = await client.post(f"{acp_url}/runs", json={
        "agent_name": agent_name,
        "input": [{"parts": [{"content": content, "content_type": "text/plain"}]}],
        "mode": "sync",
    })
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
- [x] Supervisor orchestrates via ACP delegation (httpx)
- [x] Iterative Plan -> Research -> Critique -> REVISE -> Research -> APPROVE cycle
- [x] HITL interrupt on save_report via ReportMCP
- [x] Docker compose with 4 services + proper networking
- [x] Local development (localhost) and Docker (service names) both supported

## Dependency Versions (Tested)

| Package | Version | Notes |
|---------|---------|-------|
| fastmcp | 3.2.3 | MCP server + client |
| acp-sdk | 1.0.3 | ACP server only (client broken) |
| uvicorn | 0.35.0 | Pinned: only version compatible with both fastmcp and acp-sdk |
| langchain-mcp-adapters | latest | MCP-to-LangChain tool conversion |
| langchain | >=1.2.0 | create_agent, response_format |
| langgraph | >=0.4.0 | checkpointer, interrupt, Command |
| httpx | latest | ACP client workaround |

## Lessons Learned

1. **Archived SDKs are risky** — acp-sdk is frozen at 1.0.3 with known bugs and version conflicts. Plan for workarounds.
2. **Test protocol integration early** — the client/server compatibility issue wasn't visible until runtime.
3. **Docker networking is simple but non-obvious** — `localhost` confusion is the #1 Docker networking mistake.
4. **Async/sync bridging is tricky** — LangGraph's internal event loop makes calling async code from sync tools non-trivial.
5. **Version pinning windows can be narrow** — the uvicorn 0.35.x window is exactly one minor version wide.
