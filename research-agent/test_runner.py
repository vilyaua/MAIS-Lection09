"""Test runner for L09: runs the same queries as L08 test_runner.

Supports both ACP (acp-sdk) and A2A (a2a-sdk) protocols.
Also saves reports via ReportMCP.

Usage:
  python test_runner.py                      # run all queries (uses AGENT_PROTOCOL from .env)
  python test_runner.py --protocol a2a       # force A2A protocol
  python test_runner.py --protocol acp       # force ACP protocol
  python test_runner.py --query "..."        # single query
"""

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from config import Settings

settings = Settings()

REPORT_MCP_URL = settings.report_mcp_url
QUERIES_FILE = Path(__file__).parent / "test_queries.txt"
OUTPUT_DIR = Path(__file__).parent / "output"


def load_queries(path: Path) -> list[str]:
    return [q.strip() for q in path.read_text().splitlines() if q.strip()]


# ---------------------------------------------------------------------------
# ACP agent calls (legacy)
# ---------------------------------------------------------------------------


async def call_acp_agent(agent_name: str, content: str) -> dict:
    """Call an ACP agent via PatchedACPClient and return timing + result."""
    from acp_sdk.models import Message, MessagePart

    from acp_client import PatchedACPClient

    start = time.time()
    input_msgs = [Message(parts=[MessagePart(content=content, content_type="text/plain")])]
    async with PatchedACPClient(base_url=settings.acp_url) as client:
        run = await client.run_sync(agent=agent_name, input=input_msgs)

    elapsed = time.time() - start

    parts = []
    for msg in run.output or []:
        for part in msg.parts or []:
            if part.content:
                parts.append(str(part.content))
    text = "\n".join(parts)

    return {"time_seconds": round(elapsed, 1), "result": text, "status": str(run.status)}


# ---------------------------------------------------------------------------
# A2A agent calls (current)
# ---------------------------------------------------------------------------


async def call_a2a_agent(skill_id: str, content: str) -> dict:
    """Call an A2A agent skill and return timing + result."""
    from a2a_client import delegate_a2a

    start = time.time()
    text = await delegate_a2a(skill_id, content, base_url=settings.a2a_url)
    elapsed = time.time() - start

    return {"time_seconds": round(elapsed, 1), "result": text, "status": "completed"}


# ---------------------------------------------------------------------------
# Unified caller
# ---------------------------------------------------------------------------


async def call_agent(agent_name: str, content: str, protocol: str) -> dict:
    if protocol == "a2a":
        return await call_a2a_agent(agent_name, content)
    return await call_acp_agent(agent_name, content)


async def run_full_pipeline(query: str, protocol: str) -> dict:
    """Run the full Plan -> Research -> Critique -> (REVISE) -> Save pipeline."""
    start = time.time()
    tool_calls = []
    revisions = 0

    # 1. Plan
    print("    [plan]...", end="", flush=True)
    plan = await call_agent("planner", query, protocol)
    tool_calls.append("plan")
    print(f" {plan['time_seconds']}s")

    # 2. Research
    print("    [research]...", end="", flush=True)
    research = await call_agent("researcher", plan["result"], protocol)
    tool_calls.append("research")
    print(f" {research['time_seconds']}s")

    # 3. Critique loop (max 2 revisions)
    findings = research["result"]
    for _ in range(3):  # max 2 revisions + 1 initial
        print("    [critique]...", end="", flush=True)
        critique = await call_agent("critic", findings, protocol)
        tool_calls.append("critique")
        print(f" {critique['time_seconds']}s")

        if "VERDICT: APPROVE" in critique["result"]:
            break

        if "VERDICT: REVISE" in critique["result"]:
            revisions += 1
            revision_feedback = (
                f"Revision based on critique feedback:\n{critique['result']}\n\n"
                f"Original plan:\n{plan['result']}"
            )
            print(f"    [research] (revision {revisions})...", end="", flush=True)
            research = await call_agent("researcher", revision_feedback, protocol)
            tool_calls.append("research")
            findings = research["result"]
            print(f" {research['time_seconds']}s")

    # 4. Save report via ReportMCP (skip HITL for testing)
    from fastmcp import Client

    async with Client(REPORT_MCP_URL) as client:
        slug = query[:40].strip().replace(" ", "_").lower()
        await client.call_tool("save_report", {"filename": f"test_{slug}.md", "content": findings})
    tool_calls.append("save_report")

    elapsed = time.time() - start

    return {
        "system": f"L09-{protocol.upper()}",
        "query": query,
        "time_seconds": round(elapsed, 1),
        "tool_calls": tool_calls,
        "tool_call_count": len(tool_calls),
        "report_length": len(findings),
        "revisions": revisions,
    }


def print_result(r: dict):
    print(
        f"  [{r['system']}] {r['time_seconds']}s | {r['tool_call_count']} calls | "
        f"{r['report_length']} chars | revisions: {r['revisions']} | "
        f"tools: {', '.join(r['tool_calls'])}"
    )


async def main():
    parser = argparse.ArgumentParser(description="L09 MCP+ACP/A2A test runner")
    parser.add_argument("--query", type=str, help="Single query to test")
    parser.add_argument(
        "--protocol",
        type=str,
        choices=["acp", "a2a"],
        default=settings.agent_protocol,
        help=f"Agent protocol to use (default: {settings.agent_protocol})",
    )
    args = parser.parse_args()

    protocol = args.protocol
    queries = [args.query] if args.query else load_queries(QUERIES_FILE)

    # Verify connectivity
    import httpx

    if protocol == "acp":
        check_url = f"{settings.acp_url}/agents"
    else:
        check_url = f"{settings.a2a_url}/.well-known/agent-card.json"

    try:
        resp = httpx.get(check_url, timeout=5)
        if protocol == "acp":
            agents = resp.json()
            print(f"ACP: {len(agents['agents'])} agents registered")
        else:
            card = resp.json()
            print(f"A2A: agent '{card['name']}' with {len(card.get('skills', []))} skills")
    except Exception as e:
        print(f"{protocol.upper()} not reachable at {check_url}: {e}")
        return

    results = []
    print(f"\nRunning {len(queries)} queries via {protocol.upper()}...\n")

    for i, query in enumerate(queries, 1):
        print(f"--- Query {i}/{len(queries)}: {query[:60]}...")
        try:
            r = await run_full_pipeline(query, protocol)
            print_result(r)
            results.append(r)
        except Exception as e:
            print(f"  [{protocol.upper()}] ERROR: {e}")
        print()

    # Save results
    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    out_path = OUTPUT_DIR / f"test_results_L09_{protocol}_{timestamp}.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Results saved to {out_path}")

    # Summary
    if results:
        print("\n" + "=" * 60)
        print(f"SUMMARY (L09 {protocol.upper()})")
        print("=" * 60)
        avg_time = sum(r["time_seconds"] for r in results) / len(results)
        avg_calls = sum(r["tool_call_count"] for r in results) / len(results)
        avg_len = sum(r["report_length"] for r in results) / len(results)
        total_revisions = sum(r["revisions"] for r in results)
        print(f"{'Avg time (s)':30s} {avg_time:10.1f}")
        print(f"{'Avg tool calls':30s} {avg_calls:10.1f}")
        print(f"{'Avg report length (chars)':30s} {avg_len:10.0f}")
        print(f"{'Total revision rounds':30s} {total_revisions:10d}")


if __name__ == "__main__":
    asyncio.run(main())
