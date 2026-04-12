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
