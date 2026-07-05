"""External tools the agent can call. Currently: web search via Tavily.

Kept as a thin, dependency-light wrapper (plain `requests`) rather than a
LangChain community integration, so it's easy to read, easy to swap for a
different search provider, and easy to unit test with a mock.
"""

from __future__ import annotations

import logging
from typing import List

import requests

from .schemas import SearchResult

logger = logging.getLogger(__name__)

TAVILY_ENDPOINT = "https://api.tavily.com/search"


class SearchToolError(RuntimeError):
    """Raised when the search provider fails or returns something unusable."""


def web_search(query: str, api_key: str, max_results: int = 5) -> List[SearchResult]:
    """Run a single web search query and return normalized results.

    Raises SearchToolError on network/HTTP failure so the agent graph can
    decide how to handle it (e.g. skip this query, don't crash the run).
    """
    if not api_key:
        raise SearchToolError(
            "TAVILY_API_KEY is not set. Get a free key at https://tavily.com and add it to .env"
        )

    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": False,
    }

    try:
        response = requests.post(TAVILY_ENDPOINT, json=payload, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Tavily search failed for query=%r: %s", query, exc)
        raise SearchToolError(f"Search provider error: {exc}") from exc

    data = response.json()
    results = []
    for item in data.get("results", [])[:max_results]:
        results.append(
            SearchResult(
                query=query,
                title=item.get("title", "Untitled"),
                url=item.get("url", ""),
                snippet=item.get("content", "")[:600],
                source="web",
            )
        )
    return results
