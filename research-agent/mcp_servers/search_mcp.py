"""SearchMCP server — web_search, read_url, knowledge_search.

Shared by all 3 agents (Planner, Researcher, Critic).
Run: python mcp_servers/search_mcp.py  (port 8901)
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import trafilatura
from ddgs import DDGS
from fastmcp import FastMCP

# Add parent dir so we can import config/retriever
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Settings

settings = Settings()
mcp = FastMCP("SearchMCP")
_executor = ThreadPoolExecutor(max_workers=4)


def _ddgs_search(query: str, max_results: int) -> list[dict]:
    return DDGS().text(query, max_results=max_results)


@mcp.tool
def web_search(query: str) -> str:
    """Search the web using DuckDuckGo. Returns titles, URLs, and snippets."""
    try:
        future = _executor.submit(_ddgs_search, query, settings.max_search_results)
        results = future.result(timeout=30)
        if not results:
            return "No results found."
        formatted = []
        for i, r in enumerate(results, 1):
            formatted.append(
                f"{i}. Title: {r.get('title', 'N/A')}\n"
                f"   URL: {r.get('href', 'N/A')}\n"
                f"   Snippet: {r.get('body', 'N/A')}"
            )
        result = "\n\n".join(formatted)
        if len(result) > settings.max_search_content_length:
            result = result[: settings.max_search_content_length] + "\n\n[... truncated]"
        return result
    except Exception as e:
        return f"Search error: {e}"


@mcp.tool
def read_url(url: str) -> str:
    """Fetch and extract the main text content from a web page."""
    try:
        config = trafilatura.settings.use_config()
        config.set("DEFAULT", "DOWNLOAD_TIMEOUT", "10")
        downloaded = trafilatura.fetch_url(url, config=config)
        if downloaded is None:
            return f"Error: Could not fetch URL: {url}"
        text = trafilatura.extract(downloaded)
        if not text:
            return f"Error: Could not extract text from: {url}"
        if len(text) > settings.max_url_content_length:
            text = text[: settings.max_url_content_length] + "\n\n[... truncated]"
        return text
    except Exception as e:
        return f"Error reading URL: {e}"


@mcp.tool
def knowledge_search(query: str) -> str:
    """Search the local knowledge base (RAG) using hybrid retrieval + reranking."""
    try:
        from retriever import retrieve

        results = retrieve(query)
        if not results:
            return "No relevant documents found in the knowledge base."
        formatted = []
        for i, doc in enumerate(results, 1):
            source = Path(doc.metadata.get("source", "unknown")).name
            page = doc.metadata.get("page", "?")
            formatted.append(f"{i}. [Source: {source}, Page: {page}]\n{doc.page_content}")
        result = "\n\n---\n\n".join(formatted)
        if len(result) > settings.max_search_content_length:
            result = result[: settings.max_search_content_length] + "\n\n[... truncated]"
        return result
    except Exception as e:
        return f"Knowledge search error: {e}"


@mcp.resource("resource://knowledge-base-stats")
def knowledge_base_stats() -> str:
    """Statistics about the ingested knowledge base."""
    index_dir = Path(settings.index_dir)
    chunks_path = index_dir / "bm25_chunks.pkl"
    if not chunks_path.exists():
        return json.dumps({"status": "not_ingested", "documents": 0})
    import pickle

    with open(chunks_path, "rb") as f:
        chunks = pickle.load(f)  # noqa: S301
    return json.dumps(
        {
            "status": "ready",
            "total_chunks": len(chunks),
            "index_dir": str(index_dir),
            "last_modified": chunks_path.stat().st_mtime,
        }
    )


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=settings.search_mcp_port)
