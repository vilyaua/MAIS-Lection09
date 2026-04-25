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

## 2026-04-23 — Parallel A2A implementation (v0.5.0)

- Added Google A2A protocol (a2a-sdk) as alternative to archived ACP (acp-sdk 1.0.3)
  - ACP merged into A2A under Linux Foundation in Aug 2025
  - a2a-sdk v1.0.1 is actively maintained; no bugs, no uvicorn pin needed
- New files:
  - `a2a_server.py` — A2A server with `AgentExecutor` subclass, `AgentCard` with 3 skills (planner/researcher/critic), Starlette app on :8904
  - `a2a_client.py` — clean helper using `a2a-sdk` client (no patching needed unlike `acp_client.py`)
- Updated files:
  - `supervisor.py` — dual protocol support via `AGENT_PROTOCOL` env var ("acp" or "a2a"); lazy imports so both SDKs are optional
  - `test_runner.py` — `--protocol acp|a2a` CLI flag; unified `call_agent()` dispatcher
  - `config.py` — added `agent_protocol`, `a2a_host`, `a2a_port`, `a2a_url` settings
  - `requirements.txt` — added `a2a-sdk[http-server]>=1.0.0`
  - `docker-compose.yml` — added `a2a` service (profile: a2a), updated web/supervisor with `A2A_HOST`
  - `.env.example` — documented `AGENT_PROTOCOL` toggle
- ACP code left intact for homework compliance; A2A is the recommended path forward
- Bumped VERSION 0.4.1 -> 0.5.0

## 2026-04-25 — A2A fixes, documentation, test run

- Fixed A2A client timeout: `a2a_client.py` now passes `httpx.AsyncClient(timeout=120s)` to `ClientConfig` (default was ~5s, too short for LLM+MCP calls)
- Fixed A2A agent card hostname: added `A2A_HOST=a2a` to `docker-compose.yml` a2a service so the `AgentCard` URL resolves correctly inside Docker network
- Updated `README.md` with separate Docker instructions for ACP and A2A protocols, test runner usage, architecture diagram
- Documented the requirement that `AGENT_PROTOCOL` in `.env` must match running containers
- Ran 5 test queries via A2A: all passed (Plan → Research → Critique → Save), 3/5 had 1 revision round, reports 4.8–8.2 KB

## 2026-04-13 — L8 vs L9 comparison

- Ran 5 test queries via test_runner.py
- Results: avg 62s (excluding outlier), 6462 chars reports, 2/5 revision rounds
- Created `L8_vs_L9_comparison.md` with cross-system analysis (L05/L08/L09)
- L09 is faster than L08 (62s vs 93.6s) — protocol overhead negligible vs LLM latency
