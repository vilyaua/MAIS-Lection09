"""Test runner for L09 (MCP+ACP): runs the same queries as L08 test_runner.

Calls ACP agents via PatchedACPClient (acp_sdk.client.Client subclass).
Also saves reports via ReportMCP.

Usage:
  python test_runner.py                # run all queries via ACP pipeline
  python test_runner.py --query "..."  # single query
"""

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from acp_sdk.models import Message, MessagePart

from acp_client import PatchedACPClient
from config import Settings

settings = Settings()

ACP_URL = settings.acp_url
REPORT_MCP_URL = settings.report_mcp_url
QUERIES_FILE = Path(__file__).parent / "test_queries.txt"
OUTPUT_DIR = Path(__file__).parent / "output"


def load_queries(path: Path) -> list[str]:
    return [q.strip() for q in path.read_text().splitlines() if q.strip()]


async def call_acp_agent(agent_name: str, content: str) -> dict:
    """Call an ACP agent via PatchedACPClient and return timing + result."""
    start = time.time()
    input_msgs = [Message(parts=[MessagePart(content=content, content_type="text/plain")])]
    async with PatchedACPClient(base_url=ACP_URL) as client:
        run = await client.run_sync(agent=agent_name, input=input_msgs)

    elapsed = time.time() - start

    parts = []
    for msg in run.output or []:
        for part in msg.parts or []:
            if part.content:
                parts.append(str(part.content))
    text = "\n".join(parts)

    return {"time_seconds": round(elapsed, 1), "result": text, "status": str(run.status)}


async def run_full_pipeline(query: str) -> dict:
    """Run the full Plan -> Research -> Critique -> (REVISE) -> Save pipeline."""
    start = time.time()
    tool_calls = []
    revisions = 0

    # 1. Plan
    print("    [plan]...", end="", flush=True)
    plan = await call_acp_agent("planner", query)
    tool_calls.append("plan")
    print(f" {plan['time_seconds']}s")

    # 2. Research
    print("    [research]...", end="", flush=True)
    research = await call_acp_agent("researcher", plan["result"])
    tool_calls.append("research")
    print(f" {research['time_seconds']}s")

    # 3. Critique loop (max 2 revisions)
    findings = research["result"]
    for _ in range(3):  # max 2 revisions + 1 initial
        print("    [critique]...", end="", flush=True)
        critique = await call_acp_agent("critic", findings)
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
            research = await call_acp_agent("researcher", revision_feedback)
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
        "system": "L09",
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
    parser = argparse.ArgumentParser(description="L09 MCP+ACP test runner")
    parser.add_argument("--query", type=str, help="Single query to test")
    args = parser.parse_args()

    queries = [args.query] if args.query else load_queries(QUERIES_FILE)

    # Verify connectivity
    import httpx

    try:
        resp = httpx.get(f"{ACP_URL}/agents", timeout=5)
        agents = resp.json()
        print(f"ACP: {len(agents['agents'])} agents registered")
    except Exception as e:
        print(f"ACP not reachable at {ACP_URL}: {e}")
        return

    results = []
    print(f"\nRunning {len(queries)} queries...\n")

    for i, query in enumerate(queries, 1):
        print(f"--- Query {i}/{len(queries)}: {query[:60]}...")
        try:
            r = await run_full_pipeline(query)
            print_result(r)
            results.append(r)
        except Exception as e:
            print(f"  [L09] ERROR: {e}")
        print()

    # Save results
    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    out_path = OUTPUT_DIR / f"test_results_L09_{timestamp}.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Results saved to {out_path}")

    # Summary
    if results:
        print("\n" + "=" * 60)
        print("SUMMARY (L09 MCP+ACP)")
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
