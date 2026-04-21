# CLAUDE.md — MAIS Lection 09: MCP + ACP Multi-Agent System

## Goal

Take the multi-agent system from homework-8 (Supervisor + Planner, Researcher, Critic) and migrate to a protocol-based architecture:

- **MCP** (Model Context Protocol) — for tools (web_search, read_url, knowledge_search, save_report)
- **ACP** (Agent Communication Protocol) — for agent-to-agent communication
- **Supervisor** remains a local orchestrator calling agents via ACP

Same behavior as hw8 (Plan -> Research -> Critique -> HITL -> Save), but all communication goes through protocols.

## Project Layout

```
MAIS-Lection09/                      # repo root
├── CLAUDE.md
├── README.md
├── pyproject.toml                   # ruff config
├── .pre-commit-config.yaml
├── .gitignore
├── homework-lesson-9/               # assignment spec (read-only)
└── research-agent/                  # implementation
    ├── main.py                      # REPL + HITL interrupt/resume loop
    ├── supervisor.py                # Supervisor agent + ACP delegation tools + HumanInTheLoopMiddleware
    ├── acp_client.py                # PatchedACPClient — fixes acp-sdk 1.0.3 serialization bug
    ├── acp_server.py                # ACP server with 3 agents (planner, researcher, critic)
    ├── mcp_servers/
    │   ├── search_mcp.py            # SearchMCP :8901 — web_search, read_url, knowledge_search
    │   └── report_mcp.py            # ReportMCP :8902 — save_report (HITL gated)
    ├── agents/
    │   ├── __init__.py
    │   ├── planner.py               # Planner Agent definition (prompt + response_format)
    │   ├── research.py              # Research Agent definition
    │   └── critic.py                # Critic Agent definition
    ├── schemas.py                   # Pydantic models: ResearchPlan, CritiqueResult
    ├── mcp_utils.py                 # mcp_tools_to_langchain helper
    ├── config.py                    # Prompts + settings + ports
    ├── retriever.py                 # Reused from hw8
    ├── ingest.py                    # Reused from hw8
    ├── requirements.txt
    ├── Dockerfile
    ├── docker-compose.yml
    ├── VERSION
    ├── DEVLOG.md
    ├── .env.example
    ├── .env                         # OPENAI_API_KEY — NEVER commit
    └── data/                        # PDFs for RAG (from hw5/hw8)
```

## Architecture

```
User (REPL)
  |
  v
Supervisor Agent (local create_agent, NOT an ACP agent)
  |
  |-- delegate_to_planner(request)   --> ACP --> Planner Agent  --> MCP --> SearchMCP :8901
  |-- delegate_to_researcher(plan)   --> ACP --> Research Agent --> MCP --> SearchMCP :8901
  |-- delegate_to_critic(findings)   --> ACP --> Critic Agent   --> MCP --> SearchMCP :8901
  |       |-- APPROVE -> save_report
  |       +-- REVISE  -> back to researcher (max 2 rounds)
  +-- save_report(...)               --> MCP --> ReportMCP :8902 (HITL gated)
```

## What Changes from hw8

| hw8 | hw9 |
|-----|-----|
| Tools as Python functions in one process | Tools exposed as MCP servers (FastMCP) |
| Sub-agents as `@tool` wrappers for Supervisor | Sub-agents accessible via ACP server (`acp-sdk`) |
| Everything runs in one process | Each MCP/ACP server is a separate HTTP endpoint |
| Direct function calls | Discovery -> Delegate -> Collect via protocols |

## Key Implementation Details

### MCP Servers (tools)

| Server | Port | Tools | Resources |
|--------|------|-------|-----------|
| **SearchMCP** | 8901 | `web_search`, `read_url`, `knowledge_search` | `resource://knowledge-base-stats` |
| **ReportMCP** | 8902 | `save_report` | `resource://output-dir` |

- SearchMCP is shared — all 3 agents connect to the same server
- Built with `fastmcp` (FastMCP)
- Tool logic reused from hw8 `tools.py`

### ACP Server (agents)

- **One ACP server** on port 8903 with 3 agents
- Each agent:
  1. Connects to SearchMCP via `fastmcp.Client`
  2. Converts MCP tools to LangChain format (`mcp_tools_to_langchain`)
  3. Created via `create_agent` with system prompt from hw8
  4. Returns `Message(role="agent", ...)`
- Planner and Critic use `response_format` for structured output
- Built with `acp-sdk`

### Supervisor (orchestrator)

- Local `create_agent` — NOT an ACP agent
- Tools are wrappers over ACP calls via `PatchedACPClient` (subclass of `acp_sdk.client.Client`)
- `save_report` calls ReportMCP via MCP protocol — plain function, no HITL logic inside
- `checkpointer=InMemorySaver()` for HITL interrupt/resume

### HITL

- `HumanInTheLoopMiddleware` on `create_agent` — declaratively intercepts `save_report` tool calls
- Three decisions: approve, edit, reject
- Resume with `Command(resume={"decisions": [{"type": "..."}]})`

### ACP Client Fix (`acp_client.py`)

- `acp_sdk.client.Client.run_sync()` has a serialization bug: uses `content=model.model_dump_json()` without `Content-Type: application/json` → 422
- `PatchedACPClient` subclasses `Client` and injects the missing header on POST calls
- SDK repo archived at 1.0.3 (Aug 2025) — bug will never be fixed upstream

### mcp_utils.py

- `mcp_tools_to_langchain(mcp_client)` — converts MCP tool definitions to LangChain `@tool` format
- Pattern from lesson 9 lecture materials

### RAG Pipeline

- `ingest.py` and `retriever.py` reused from hw8 as-is
- `knowledge_search` in SearchMCP calls `retriever.retrieve()` internally

### Config & Prompts

- System prompts reused from hw8 `config.py`
- Ports: SearchMCP=8901, ReportMCP=8902, ACP=8903
- `OPENAI_API_KEY` for LangChain + embeddings

## Startup Order

```bash
# 1. Ingest (one-time)
python ingest.py

# 2. MCP servers (separate terminals or background)
python mcp_servers/search_mcp.py   # :8901
python mcp_servers/report_mcp.py   # :8902

# 3. ACP server
python acp_server.py               # :8903

# 4. Supervisor REPL
python main.py
```

## Dependencies (new vs hw8)

```
fastmcp>=3.0.0                    # MCP server + client
acp-sdk>=1.0.0                    # ACP server + client (client patched via PatchedACPClient)
langchain-mcp-adapters>=0.1.0     # MCP-to-LangChain tool conversion
uvicorn>=0.35.0,<0.36.0           # CRITICAL: only version compatible with both fastmcp and acp-sdk
```

Keep all hw8 deps: langchain, langgraph, faiss-cpu, rank_bm25, sentence-transformers, ddgs, trafilatura, etc.

## Known Issues

- **acp-sdk client patched** — `acp_sdk.client.Client.run_sync()` has a serialization bug (422 error). Server works fine. Fixed via `PatchedACPClient` subclass in `acp_client.py`.
- **uvicorn pin is narrow** — only 0.35.x works (fastmcp needs >=0.35, acp-sdk needs LoopSetupType removed in 0.36).
- **Async bridging** — LangGraph runs its own event loop, so `@tool` functions use `ThreadPoolExecutor` + `asyncio.run()` to call async ACP/MCP code.

See `research-agent/FINDINGS.md` for full technical details.

## Requirements Checklist

- [x] 2 MCP servers (SearchMCP, ReportMCP) with tools and resources
- [x] 1 ACP server with 3 agents (planner, researcher, critic)
- [x] Each ACP agent connects to SearchMCP via `langchain-mcp-adapters`
- [x] Each ACP agent created via `create_agent`
- [x] Supervisor orchestrates agents via ACP (`PatchedACPClient`)
- [x] Iterative Plan -> Research -> Critique cycle works via ACP
- [x] HITL on `save_report` via `HumanInTheLoopMiddleware`
- [x] `save_report` works through ReportMCP

## Important: Use Current APIs

Before writing any code, research the most recent docs and best practices as of
April 2026 for all key libraries:

- **FastMCP** — server/client API, tool/resource decorators, SSE transport
- **acp-sdk** — ACP server, agent registration, `Message` format, client delegation
- **LangChain `create_agent`** — `response_format`, `checkpointer`, middleware
- **LangGraph** — `interrupt()`, `Command(resume=...)`, `InMemorySaver`
- **mcp_tools_to_langchain** — conversion pattern from MCP to LangChain tools

APIs evolve fast. Do NOT rely on training data — fetch and verify current docs
before implementation.

## Do NOT

- Commit `.env` or API keys
- Make Supervisor an ACP agent — it stays local
- Skip MCP resources (knowledge-base-stats, output-dir)
- Skip structured output for Planner/Critic
- Forget HITL on save_report
