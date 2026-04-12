"""Planner Agent — decomposes a user request into a structured ResearchPlan.

Used inside the ACP server. Connects to SearchMCP for preliminary searches.
"""

from langchain.agents import create_agent

from config import PLANNER_PROMPT, Settings
from schemas import ResearchPlan

settings = Settings()


async def run_planner(request: str, tools: list) -> str:
    """Run the planner agent with MCP tools and return formatted plan."""
    planner = create_agent(
        model=settings.model_powerful,
        tools=tools,
        system_prompt=PLANNER_PROMPT,
        response_format=ResearchPlan,
        name="planner",
    )
    result = await planner.ainvoke(
        {"messages": [{"role": "user", "content": request}]},
        {"recursion_limit": 30},
    )
    structured: ResearchPlan = result["structured_response"]
    return (
        f"RESEARCH PLAN:\n"
        f"Goal: {structured.goal}\n"
        f"Search queries: {', '.join(structured.search_queries)}\n"
        f"Sources: {', '.join(structured.sources_to_check)}\n"
        f"Output format: {structured.output_format}"
    )
