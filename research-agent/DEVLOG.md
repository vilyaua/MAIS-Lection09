# DEVLOG

## 2026-04-12 — Initial project scaffold

- Created MCP+ACP architecture extending hw8
- `mcp_servers/search_mcp.py` — SearchMCP :8901 (web_search, read_url, knowledge_search + KB stats resource)
- `mcp_servers/report_mcp.py` — ReportMCP :8902 (save_report + output dir resource)
- `acp_server.py` — ACP server :8903 with 3 agents (planner, researcher, critic)
- `agents/` — agent definitions using create_agent + MCP tools via langchain-mcp-adapters
- `supervisor.py` — local Supervisor with ACP delegation tools + MCP save_report + HITL
- `mcp_utils.py` — MultiServerMCPClient helper for SearchMCP
- `main.py` — REPL with HITL approve/edit/reject
- `config.py` — settings + ports + prompts (reused from hw8)
- `schemas.py`, `retriever.py`, `ingest.py` — reused from hw8
- `docker-compose.yml` — 4 services: search-mcp, report-mcp, acp, supervisor
- Added `fastmcp>=3.0.0`, `acp-sdk>=1.0.0`, `langchain-mcp-adapters>=0.1.0` to requirements

## 2026-04-12 — Fixes for Docker networking and runtime issues

- Fixed uvicorn version conflict: pinned `>=0.35.0,<0.36.0` (fastmcp needs >=0.35, acp-sdk needs LoopSetupType removed in 0.36)
- Fixed ACP server binding: `0.0.0.0` instead of `127.0.0.1` for Docker access
- Added configurable service hostnames (`SEARCH_MCP_HOST`, `REPORT_MCP_HOST`, `ACP_HOST`) for Docker networking
- Fixed async event loop conflict: `asyncio.run()` in ThreadPoolExecutor instead of `get_event_loop().run_until_complete()`
- Worked around acp-sdk client serialization bug: replaced `acp_sdk.client.Client` with direct httpx calls
- Added `./logs` volume mount to all Docker services
- Verified full pipeline: Plan -> Research -> Critique (REVISE) -> Research -> Critique (APPROVE) via MCP+ACP

## 2026-04-12 — FastAPI web UI, logging, and async fixes

- Added `app.py` — FastAPI web UI with SSE streaming + HITL dialog (adapted from L08)
- Added web service to docker-compose on :8000
- Made MCP tools async with `run_in_executor` + `asyncio.wait_for` (fixes event loop deadlock)
- Added RotatingFileHandler to all servers (search_mcp, report_mcp, acp_server, supervisor)
- Added session ID tracing: `[session_id]` prefix in logs, session_id in SSE events
- Added colored tool badges in web UI: PLAN (purple), RESEARCH (blue), CRITIQUE (orange), SAVE (green)

## 2026-04-21 — Reviewer feedback fixes

- Replaced direct httpx POST to ACP `/runs` with `PatchedACPClient` (subclass of `acp_sdk.client.Client`)
  - Bug: SDK uses `content=model.model_dump_json()` without Content-Type header → 422
  - Fix: subclass injects `Content-Type: application/json` header on POST calls
  - New file: `acp_client.py`
- Replaced manual `interrupt()` in `save_report` with `HumanInTheLoopMiddleware`
  - Middleware declaratively intercepts `save_report` tool calls at agent level
  - `save_report` is now a plain function (no HITL logic inside)
  - Updated `main.py` to handle new interrupt format (`action_requests` / `decisions`)
  - Updated `app.py` — fixed interrupt payload parsing for middleware format, wrapped resume decisions, added None-check for node output during streaming
  - Updated `test_runner.py` — uses `PatchedACPClient` + `Message`/`MessagePart` instead of raw httpx, fully async

## 2026-04-13 — L8 vs L9 comparison

- Ran 5 test queries via test_runner.py
- Results: avg 62s (excluding outlier), 6462 chars reports, 2/5 revision rounds
- Created `L8_vs_L9_comparison.md` with cross-system analysis (L05/L08/L09)
- L09 is faster than L08 (62s vs 93.6s) — protocol overhead negligible vs LLM latency
