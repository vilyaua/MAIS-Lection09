"""Supervisor Agent — orchestrates Plan -> Research -> Critique -> Save.

Calls sub-agents via ACP (PatchedACPClient — acp_sdk.client.Client subclass).
Calls save_report via MCP (ReportMCP).
HITL handled by HumanInTheLoopMiddleware on create_agent.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from acp_sdk.models import Message, MessagePart
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from acp_client import PatchedACPClient
from config import SUPERVISOR_PROMPT, Settings

settings = Settings()

# Thread pool for running async code from sync @tool functions
_executor = ThreadPoolExecutor(max_workers=4)


def _run_async(coro):
    """Run an async coroutine from a sync context."""
    return asyncio.run(coro)


def _run_async_in_thread(coro):
    """Run async code in a separate thread to avoid event loop conflicts."""
    future = _executor.submit(_run_async, coro)
    return future.result(timeout=300)


async def _delegate_acp(agent_name: str, content: str) -> str:
    """Delegate to an ACP agent via acp_sdk.client.Client (patched)."""
    input_messages = [Message(parts=[MessagePart(content=content, content_type="text/plain")])]
    async with PatchedACPClient(base_url=settings.acp_url) as client:
        run = await client.run_sync(agent=agent_name, input=input_messages)
        parts = []
        for msg in run.output or []:
            for part in msg.parts or []:
                if part.content:
                    parts.append(part.content)
        return "\n".join(parts)


async def _call_report_mcp(filename: str, content: str) -> str:
    """Call save_report on ReportMCP via fastmcp.Client."""
    from fastmcp import Client as MCPClient

    async with MCPClient(settings.report_mcp_url) as client:
        result = await client.call_tool("save_report", {"filename": filename, "content": content})
        return str(result)


@tool
def delegate_to_planner(request: str) -> str:
    """Delegate a research request to the Planner agent via ACP.

    The Planner decomposes the request into a structured research plan
    with specific search queries and sources to check.
    """
    return _run_async_in_thread(_delegate_acp("planner", request))


@tool
def delegate_to_researcher(request: str) -> str:
    """Delegate research execution to the Researcher agent via ACP.

    The Researcher follows the plan, searches web and knowledge base,
    and returns findings with source citations.
    """
    return _run_async_in_thread(_delegate_acp("researcher", request))


@tool
def delegate_to_critic(findings: str) -> str:
    """Delegate research evaluation to the Critic agent via ACP.

    The Critic independently verifies findings for freshness, completeness,
    and structure. Returns APPROVE or REVISE verdict.
    """
    return _run_async_in_thread(_delegate_acp("critic", findings))


@tool
def save_report(filename: str, content: str) -> str:
    """Save a Markdown research report via ReportMCP.

    This tool requires human approval before execution.
    """
    return _run_async_in_thread(_call_report_mcp(filename, content))


_supervisor_prompt = SUPERVISOR_PROMPT.format(
    max_revisions=settings.max_revision_rounds,
)

checkpointer = InMemorySaver()

supervisor = create_agent(
    model=settings.model_powerful,
    tools=[delegate_to_planner, delegate_to_researcher, delegate_to_critic, save_report],
    system_prompt=_supervisor_prompt,
    checkpointer=checkpointer,
    name="supervisor",
    middleware=[
        HumanInTheLoopMiddleware(
            interrupt_on={"save_report": True},
        ),
    ],
)
