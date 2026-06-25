"""arXiv tool — searches academic papers and returns abstracts."""

import requests
import xml.etree.ElementTree as ET
from .base import BaseTool, ToolResult, with_retry

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
TIMEOUT = 20


class ArxivTool(BaseTool):
    name = "arxiv_search"
    description = (
        "Search arXiv for peer-reviewed academic papers. "
        "Best for questions about recent research, methodologies, machine learning, "
        "and academic findings. Returns paper titles, authors, abstracts, and URLs."
    )

    @with_retry(max_attempts=3)
    def run(self, query: str) -> ToolResult:
        try:
            resp = requests.get(
                "http://export.arxiv.org/api/query",
                params={
                    "search_query": f"all:{query}",
                    "start": 0,
                    "max_results": 5,
                    "sortBy": "relevance",
                    "sortOrder": "descending",
                },
                timeout=TIMEOUT,
            )
            resp.raise_for_status()

            root = ET.fromstring(resp.content)
            entries = root.findall("atom:entry", ATOM_NS)

            if not entries:
                return ToolResult(
                    content=f"No arXiv papers found for: {query}",
                    sources=[],
                    success=False,
                )

            content_parts = []
            sources = []

            for entry in entries[:4]:
                title = entry.find("atom:title", ATOM_NS).text.strip().replace("\n", " ")
                summary = entry.find("atom:summary", ATOM_NS).text.strip()[:2000]
                paper_url = entry.find("atom:id", ATOM_NS).text.strip()
                published = entry.find("atom:published", ATOM_NS).text[:10]
                authors = [a.find("atom:name", ATOM_NS).text for a in entry.findall("atom:author", ATOM_NS)]
                author_str = ", ".join(authors[:4])
                if len(authors) > 4:
                    author_str += " et al."

                content_parts.append(
                    f"### {title}\n"
                    f"**Authors**: {author_str}  \n"
                    f"**Published**: {published}  \n"
                    f"**URL**: {paper_url}\n\n"
                    f"{summary}"
                )
                sources.append({
                    "title": title,
                    "authors": authors,
                    "url": paper_url,
                    "published": published,
                    "type": "arxiv",
                })

            return ToolResult(
                content="\n\n---\n\n".join(content_parts),
                sources=sources,
                success=True,
            )

        except Exception as e:
            return ToolResult(
                content=f"arXiv search failed: {e}",
                sources=[],
                success=False,
                error=str(e),
            )
