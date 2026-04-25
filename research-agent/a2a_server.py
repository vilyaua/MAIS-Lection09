"""A2A server with 3 skills: planner, researcher, critic.

Parallel implementation to acp_server.py using Google's A2A protocol (a2a-sdk).
Each skill connects to SearchMCP via langchain-mcp-adapters to get tools,
then runs a LangChain create_agent internally.

ACP merged into A2A under the Linux Foundation in Aug 2025.
See: https://github.com/a2aproject/a2a-python

Run: python a2a_server.py  (port 8904)
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn
from a2a.helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
)
from starlette.applications import Starlette

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
        RotatingFileHandler("logs/a2a_server.log", maxBytes=5_000_000, backupCount=3),
    ],
)
logger = logging.getLogger("a2a_server")

settings = Settings()


async def _load_search_tools() -> list:
    """Load LangChain tools from SearchMCP."""
    client = get_search_mcp_client()
    return await client.get_tools()


# ---------------------------------------------------------------------------
# Skill handlers
# ---------------------------------------------------------------------------


async def _handle_planner(user_text: str) -> str:
    tools = await _load_search_tools()
    return await run_planner(user_text, tools)


async def _handle_researcher(user_text: str) -> str:
    tools = await _load_search_tools()
    return await run_researcher(user_text, tools)


async def _handle_critic(user_text: str) -> str:
    tools = await _load_search_tools()
    return await run_critic(user_text, tools)


SKILL_HANDLERS = {
    "planner": _handle_planner,
    "researcher": _handle_researcher,
    "critic": _handle_critic,
}


# ---------------------------------------------------------------------------
# AgentExecutor — routes to skill based on metadata["skill_id"]
# ---------------------------------------------------------------------------


class ResearchAgentExecutor(AgentExecutor):
    """Multi-skill executor: planner, researcher, critic."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        skill_id = context.metadata.get("skill_id", "planner")
        user_text = context.get_user_input()

        logger.info("Executing skill=%s, input=%s...", skill_id, user_text[:80])

        task = context.current_task or new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=context.task_id,
            context_id=context.context_id,
        )
        await updater.start_work()

        handler = SKILL_HANDLERS.get(skill_id)
        if not handler:
            await updater.add_artifact(
                parts=[
                    Part(text=f"Unknown skill '{skill_id}'. Available: planner, researcher, critic")
                ],
                name="error",
                last_chunk=True,
            )
            await updater.complete()
            return

        try:
            result = await handler(user_text)
        except Exception:
            logger.exception("Skill %s failed", skill_id)
            await updater.add_artifact(
                parts=[Part(text=f"Error executing skill '{skill_id}'")],
                name="error",
                last_chunk=True,
            )
            await updater.fail()
            return

        await updater.add_artifact(
            parts=[Part(text=result)],
            name=f"{skill_id}_result",
            last_chunk=True,
        )
        await updater.complete()
        logger.info("Skill %s completed, result length=%d", skill_id, len(result))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=context.task_id or "",
            context_id=context.context_id or "",
        )
        await updater.cancel()


# ---------------------------------------------------------------------------
# AgentCard — describes the agent and its skills for discovery
# ---------------------------------------------------------------------------

agent_card = AgentCard(
    name="Research Agent Team",
    description="Multi-agent research system with planner, researcher, and critic skills. "
    "Each skill connects to SearchMCP for web search and knowledge base access.",
    version="1.0.0",
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    capabilities=AgentCapabilities(streaming=True),
    supported_interfaces=[
        AgentInterface(
            protocol_binding="JSONRPC",
            url=f"http://{settings.a2a_host}:{settings.a2a_port}",
        ),
    ],
    skills=[
        AgentSkill(
            id="planner",
            name="Research Planner",
            description="Decomposes a research request into a structured plan "
            "with search queries and sources.",
            tags=["planning", "research"],
        ),
        AgentSkill(
            id="researcher",
            name="Research Executor",
            description="Executes a research plan using web search and knowledge base, "
            "returns findings with citations.",
            tags=["research", "search"],
        ),
        AgentSkill(
            id="critic",
            name="Research Critic",
            description="Evaluates research findings for freshness, completeness, "
            "and structure. Returns APPROVE or REVISE verdict.",
            tags=["evaluation", "critique"],
        ),
    ],
)


# ---------------------------------------------------------------------------
# Starlette app
# ---------------------------------------------------------------------------

request_handler = DefaultRequestHandler(
    agent_executor=ResearchAgentExecutor(),
    task_store=InMemoryTaskStore(),
    agent_card=agent_card,
)

routes = []
routes.extend(create_agent_card_routes(agent_card))
routes.extend(create_jsonrpc_routes(request_handler, "/"))

a2a_app = Starlette(routes=routes)


if __name__ == "__main__":
    logger.info("Starting A2A server on port %d with 3 skills", settings.a2a_port)
    uvicorn.run(a2a_app, host="0.0.0.0", port=settings.a2a_port)  # noqa: S104
