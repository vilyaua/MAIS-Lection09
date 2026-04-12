"""Critic Agent — evaluates research quality via independent verification.

Used inside the ACP server. Connects to SearchMCP for fact-checking.
"""

from datetime import datetime

from langchain.agents import create_agent

from config import CRITIC_PROMPT, Settings
from schemas import CritiqueResult

settings = Settings()


async def run_critic(findings: str, tools: list) -> str:
    """Run the critic agent with MCP tools and return structured critique."""
    prompt = CRITIC_PROMPT.format(current_date=datetime.now().strftime("%Y-%m-%d"))
    critic = create_agent(
        model=settings.model_powerful,
        tools=tools,
        system_prompt=prompt,
        response_format=CritiqueResult,
        name="critic",
    )
    result = await critic.ainvoke(
        {"messages": [{"role": "user", "content": findings}]},
        {"recursion_limit": 30},
    )
    structured: CritiqueResult = result["structured_response"]
    parts = [
        f"VERDICT: {structured.verdict}",
        f"Fresh: {structured.is_fresh} | Complete: {structured.is_complete} | "
        f"Well-structured: {structured.is_well_structured}",
    ]
    if structured.strengths:
        parts.append(f"Strengths: {'; '.join(structured.strengths)}")
    if structured.gaps:
        parts.append(f"Gaps: {'; '.join(structured.gaps)}")
    if structured.revision_requests:
        parts.append(f"Revision requests: {'; '.join(structured.revision_requests)}")
    return "\n".join(parts)
