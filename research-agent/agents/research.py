"""Research Agent — executes the research plan using web + knowledge base.

Used inside the ACP server. Connects to SearchMCP for searches.
"""

from langchain.agents import create_agent

from config import RESEARCHER_PROMPT, Settings

settings = Settings()


async def run_researcher(request: str, tools: list) -> str:
    """Run the research agent with MCP tools and return findings."""
    researcher = create_agent(
        model=settings.model_fast,
        tools=tools,
        system_prompt=RESEARCHER_PROMPT,
        name="researcher",
    )
    result = await researcher.ainvoke(
        {"messages": [{"role": "user", "content": request}]},
        {"recursion_limit": 50},
    )
    return result["messages"][-1].content
