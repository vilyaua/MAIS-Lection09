# MAIS-Lection09

## Homework: MCP + ACP Multi-Agent Research System

Extension of the Research Agent from Lesson 8 — migrates the multi-agent system to a **protocol-based architecture** using MCP (tools) and ACP (agent-to-agent communication).

### What's New vs Lesson 8

| Lesson 8                              | Lesson 9                                         |
|---------------------------------------|--------------------------------------------------|
| Tools as Python functions in one process | Tools exposed as MCP servers (FastMCP)          |
| Sub-agents as `@tool` wrappers        | Sub-agents via ACP server (acp-sdk)              |
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
docker compose build
docker compose --profile tools run --rm ingest
docker compose up                                    # starts SearchMCP, ReportMCP, ACP
docker compose --profile cli run --rm supervisor     # interactive REPL
```

See [`CLAUDE.md`](CLAUDE.md) for full architecture and implementation details.
