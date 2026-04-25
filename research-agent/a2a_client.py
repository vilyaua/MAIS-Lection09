"""A2A client helper — delegates to agents via Google's A2A protocol.

Unlike acp_client.py (which patches around acp-sdk bugs), this module
uses the a2a-sdk client as-is — no workarounds needed.

Usage:
    from a2a_client import delegate_a2a

    result = await delegate_a2a("planner", "Compare RAG approaches", base_url="http://localhost:8904")
"""

import logging

import httpx
from a2a.client import ClientConfig, create_client
from a2a.helpers import get_artifact_text, get_message_text, new_text_message
from a2a.types import Role, SendMessageRequest

logger = logging.getLogger("a2a_client")


async def delegate_a2a(skill_id: str, content: str, base_url: str) -> str:
    """Send a message to an A2A agent, routing to a specific skill.

    Args:
        skill_id: Which skill to invoke ("planner", "researcher", "critic").
        content: The text payload.
        base_url: A2A server URL, e.g. "http://localhost:8904".

    Returns:
        The agent's text response.
    """
    config = ClientConfig(
        streaming=False,
        httpx_client=httpx.AsyncClient(timeout=httpx.Timeout(120.0)),
    )
    client = await create_client(agent=base_url, client_config=config)

    try:
        message = new_text_message(content, role=Role.ROLE_USER)
        request = SendMessageRequest(
            message=message,
            metadata={"skill_id": skill_id},
        )

        parts: list[str] = []
        async for response in client.send_message(request):
            if response.HasField("message"):
                text = get_message_text(response.message)
                if text:
                    parts.append(text)
            elif response.HasField("task"):
                for artifact in response.task.artifacts:
                    text = get_artifact_text(artifact)
                    if text:
                        parts.append(text)
            elif response.HasField("artifact_update"):
                text = get_artifact_text(response.artifact_update.artifact)
                if text:
                    parts.append(text)

        result = "\n".join(parts)
        logger.info("A2A skill=%s returned %d chars", skill_id, len(result))
        return result
    finally:
        await client.close()
