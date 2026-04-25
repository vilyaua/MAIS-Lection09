# MAIS-Lection09

## Homework: MCP + ACP Multi-Agent Research System

Extension of the Research Agent from Lesson 8 — migrates the multi-agent system to a **protocol-based architecture** using MCP (tools) and ACP (agent-to-agent communication).

### What's New vs Lesson 8

| Lesson 8                              | Lesson 9                                         |
|---------------------------------------|--------------------------------------------------|
| Tools as Python functions in one process | Tools exposed as MCP servers (FastMCP)          |
| Sub-agents as `@tool` wrappers        | Sub-agents via ACP (acp-sdk) or A2A (a2a-sdk)   |
| Everything in one process             | Each server is a separate HTTP endpoint          |
| Direct function calls                 | Discovery -> Delegate -> Collect via protocols   |

### Project Structure

- **`homework-lesson-9/`** — Original homework skeleton with task description
- **`research-agent/`** — Completed implementation
- **`CLAUDE.md`** — Implementation guide and specifications

### Quick Start

```bash
cd research-agent
pip install -r requirements.txt
cp .env.example .env   # add your OPENAI_API_KEY

# 1. Ingest documents
python ingest.py

# 2. Start MCP servers (separate terminals)
python mcp_servers/search_mcp.py   # :8901
python mcp_servers/report_mcp.py   # :8902

# 3. Start ACP server
python acp_server.py               # :8903

# 4. Run supervisor REPL
python main.py
```

### Docker

```bash
cd research-agent
cp .env.example .env   # add your OPENAI_API_KEY
docker compose build
docker compose --profile tools run --rm ingest
```

#### Running with ACP (default, homework requirement)

Set `AGENT_PROTOCOL=acp` in `.env` (or remove the variable — ACP is the default).

```bash
docker compose up                                    # starts SearchMCP, ReportMCP, ACP, Web UI
docker compose --profile cli run --rm supervisor     # interactive REPL (optional)
```

Services started: `search-mcp` (:8901), `report-mcp` (:8902), `acp` (:8903), `web` (:8000).

#### Running with A2A (recommended, actively maintained)

Set `AGENT_PROTOCOL=a2a` in `.env`.

```bash
docker compose --profile a2a up                      # starts SearchMCP, ReportMCP, A2A, Web UI
docker compose --profile cli run --rm supervisor     # interactive REPL (optional)
```

Services started: `search-mcp` (:8901), `report-mcp` (:8902), `a2a` (:8904), `web` (:8000).

> **Important:** The `AGENT_PROTOCOL` value in `.env` must match the running services.
> If `AGENT_PROTOCOL=a2a`, the A2A container must be running (`--profile a2a`).
> If `AGENT_PROTOCOL=acp`, the ACP container must be running (default, no profile needed).

#### Running Tests

```bash
# From host (requires Python + dependencies installed locally):
cd research-agent
python test_runner.py                     # all queries, uses AGENT_PROTOCOL from .env
python test_runner.py --protocol acp      # force ACP
python test_runner.py --protocol a2a      # force A2A
python test_runner.py --query "..."       # single query

# Web UI: open http://localhost:8000
```

### Architecture

```
User (REPL / Web UI)
  │
  ▼
Supervisor (local orchestrator)
  ├── delegate_to_planner   ──► ACP/A2A ──► Planner Agent  ──► MCP ──► SearchMCP :8901
  ├── delegate_to_researcher ──► ACP/A2A ──► Research Agent ──► MCP ──► SearchMCP :8901
  ├── delegate_to_critic    ──► ACP/A2A ──► Critic Agent   ──► MCP ──► SearchMCP :8901
  └── save_report           ──► MCP ──► ReportMCP :8902 (HITL gated)
```

See [`CLAUDE.md`](CLAUDE.md) for full architecture and implementation details.
