"""ACP server with 3 agents: planner, researcher, critic.

Each agent connects to SearchMCP via langchain-mcp-adapters to get tools,
then runs a LangChain create_agent internally.

Run: python acp_server.py  (port 8903)
"""

import logging
from collections.abc import AsyncGenerator
from logging.handlers import RotatingFileHandler
from pathlib import Path

from acp_sdk.models import Message, MessagePart
from acp_sdk.server import Context, RunYield, RunYieldResume, Server

from agents.critic import run_critic
from agents.planner import run_planner
from agents.research import run_researcher
from config import Settings
from mcp_utils import get_search_mcp_client

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler("logs/acp_server.log", maxBytes=5_000_000, backupCount=3),
    ],
)
logger = logging.getLogger("acp_server")

settings = Settings()
server = Server()


async def _load_search_tools() -> list:
    """Load LangChain tools from SearchMCP."""
    client = get_search_mcp_client()
    return await client.get_tools()


@server.agent(
    name="planner",
    description="Decomposes a research request into a structured plan with search queries and sources.",
)
async def planner_agent(
    input: list[Message], context: Context
) -> AsyncGenerator[RunYield, RunYieldResume]:
    """Planner agent — connects to SearchMCP, returns ResearchPlan."""
    request = input[-1].parts[-1].content if input else ""
    tools = await _load_search_tools()
    result = await run_planner(request, tools)
    yield MessagePart(content=result)


@server.agent(
    name="researcher",
    description="Executes a research plan using web search and knowledge base, returns findings with citations.",
)
async def researcher_agent(
    input: list[Message], context: Context
) -> AsyncGenerator[RunYield, RunYieldResume]:
    """Research agent — connects to SearchMCP, returns findings."""
    request = input[-1].parts[-1].content if input else ""
    tools = await _load_search_tools()
    result = await run_researcher(request, tools)
    yield MessagePart(content=result)


@server.agent(
    name="critic",
    description="Evaluates research findings for freshness, completeness, and structure. Returns APPROVE or REVISE verdict.",
)
async def critic_agent(
    input: list[Message], context: Context
) -> AsyncGenerator[RunYield, RunYieldResume]:
    """Critic agent — connects to SearchMCP for verification, returns CritiqueResult."""
    findings = input[-1].parts[-1].content if input else ""
    tools = await _load_search_tools()
    result = await run_critic(findings, tools)
    yield MessagePart(content=result)


if __name__ == "__main__":
    server.run(host="0.0.0.0", port=settings.acp_port)  # noqa: S104
