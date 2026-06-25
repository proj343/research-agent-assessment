"""Wikipedia tool — searches and retrieves article summaries."""

import requests
from .base import BaseTool, ToolResult, with_retry

HEADERS = {"User-Agent": "ResearchAgent/1.0 (research-agent-assessment; contact@example.com)"}
TIMEOUT = 12


class WikipediaTool(BaseTool):
    name = "wikipedia_search"
    description = (
        "Search Wikipedia for encyclopedic information about a topic. "
        "Best for factual, definitional, historical, and regulatory questions. "
        "Returns article summaries with source URLs."
    )

    @with_retry(max_attempts=3)
    def run(self, query: str) -> ToolResult:
        try:
            results = self._search(query)
            if not results:
                return ToolResult(
                    content=f"No Wikipedia articles found for: {query}",
                    sources=[],
                    success=False,
                )

            content_parts = []
            sources = []

            for title in results[:2]:
                summary = self._get_summary(title)
                if summary:
                    content_parts.append(summary["text"])
                    sources.append({
                        "title": title,
                        "url": summary["url"],
                        "type": "wikipedia",
                    })

            if not content_parts:
                return ToolResult(
                    content="Found articles but could not retrieve content.",
                    sources=[],
                    success=False,
                )

            return ToolResult(
                content="\n\n".join(content_parts),
                sources=sources,
                success=True,
            )

        except Exception as e:
            return ToolResult(
                content=f"Wikipedia search failed: {e}",
                sources=[],
                success=False,
                error=str(e),
            )

    def _search(self, query: str) -> list[str]:
        resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 3,
                "format": "json",
            },
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return [r["title"] for r in resp.json().get("query", {}).get("search", [])]

    def _get_summary(self, title: str) -> dict | None:
        encoded = title.replace(" ", "_")
        resp = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}",
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        extract = data.get("extract", "")[:4000]
        url = data.get("content_urls", {}).get("desktop", {}).get("page", f"https://en.wikipedia.org/wiki/{encoded}")
        return {"text": f"### {title}\n\n{extract}", "url": url}
