"""Helper to load MCP tools as LangChain tools via langchain-mcp-adapters."""

from langchain_mcp_adapters.client import MultiServerMCPClient

from config import Settings

settings = Settings()


def get_search_mcp_client() -> MultiServerMCPClient:
    """Create a MultiServerMCPClient connected to SearchMCP."""
    return MultiServerMCPClient(
        {
            "search": {
                "url": settings.search_mcp_url,
                "transport": "http",
            },
        }
    )


def get_report_mcp_client() -> MultiServerMCPClient:
    """Create a MultiServerMCPClient connected to ReportMCP."""
    return MultiServerMCPClient(
        {
            "report": {
                "url": settings.report_mcp_url,
                "transport": "http",
            },
        }
    )
