"""Supervisor Agent — orchestrates Plan -> Research -> Critique -> Save.

Calls sub-agents via ACP (acp_sdk.client.Client).
Calls save_report via MCP (ReportMCP) with HITL interrupt.
"""

import asyncio

from acp_sdk.client import Client as ACPClient
from acp_sdk.models import Message, MessagePart
from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt

from config import SUPERVISOR_PROMPT, Settings

settings = Settings()


async def _delegate_acp(agent_name: str, content: str) -> str:
    """Delegate to an ACP agent and return the response text."""
    async with ACPClient(base_url=settings.acp_url) as client:
        run = await client.run_sync(
            agent=agent_name,
            input=[Message(parts=[MessagePart(content=content)])],
        )
        # Collect output text from all messages
        parts = []
        for msg in run.output:
            for part in msg.parts:
                if hasattr(part, "content") and part.content:
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
    return asyncio.get_event_loop().run_until_complete(_delegate_acp("planner", request))


@tool
def delegate_to_researcher(request: str) -> str:
    """Delegate research execution to the Researcher agent via ACP.

    The Researcher follows the plan, searches web and knowledge base,
    and returns findings with source citations.
    """
    return asyncio.get_event_loop().run_until_complete(_delegate_acp("researcher", request))


@tool
def delegate_to_critic(findings: str) -> str:
    """Delegate research evaluation to the Critic agent via ACP.

    The Critic independently verifies findings for freshness, completeness,
    and structure. Returns APPROVE or REVISE verdict.
    """
    return asyncio.get_event_loop().run_until_complete(_delegate_acp("critic", findings))


@tool
def save_report(filename: str, content: str) -> str:
    """Save a Markdown research report via ReportMCP. Requires human approval."""
    decision = interrupt(
        {
            "tool": "save_report",
            "filename": filename,
            "content_preview": content,
        }
    )

    action = decision.get("type", "reject")

    if action == "approve":
        return asyncio.get_event_loop().run_until_complete(_call_report_mcp(filename, content))
    elif action == "edit":
        feedback = decision.get("feedback", "")
        return f"User requested changes: {feedback}. Please revise the report and call save_report again."
    else:
        reason = decision.get("message", "No reason given.")
        return f"Report rejected by user: {reason}"


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
)
