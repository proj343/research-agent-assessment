"""Integration / end-to-end tests.

Uses real tool instances (WikipediaTool, ArxivTool, FREDTool) and a real
ResearchAgent, but mocks all HTTP calls (requests.get/post) and the LLM so
tests are deterministic and offline. This layer validates that the agent
correctly wires tool dispatch, accumulates sources, and writes traces —
things that unit tests with MagicMock tools cannot catch.
"""

import json
import os
from unittest.mock import MagicMock, patch

from agent.core import ResearchAgent
from agent.tools.arxiv import ArxivTool
from agent.tools.fred import FREDTool
from agent.tools.wikipedia import WikipediaTool
from agent.tracer import Tracer

# ---------------------------------------------------------------------------
# Shared HTTP fixture helpers
# ---------------------------------------------------------------------------

ATOM_NS = "http://www.w3.org/2005/Atom"


def _http(json_data=None, content: bytes = b"", status_code: int = 200) -> MagicMock:
    m = MagicMock()
    m.status_code = status_code
    m.content = content
    m.raise_for_status = MagicMock()
    if json_data is not None:
        m.json.return_value = json_data
    return m


def _wiki_search(titles: list[str]) -> MagicMock:
    return _http({"query": {"search": [{"title": t} for t in titles]}})


def _wiki_summary(title: str, extract: str) -> MagicMock:
    return _http(
        {
            "extract": extract,
            "content_urls": {
                "desktop": {"page": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"}
            },
        }
    )


def _arxiv_feed(papers: list[dict]) -> bytes:
    parts = [f'<?xml version="1.0" encoding="UTF-8"?><feed xmlns="{ATOM_NS}">']
    for p in papers:
        authors = "".join(f"<author><name>{a}</name></author>" for a in p.get("authors", ["A"]))
        parts.append(
            f"<entry>"
            f"<title>{p.get('title', 'Paper')}</title>"
            f"<summary>{p.get('summary', 'Abstract.')}</summary>"
            f"<id>{p.get('id', 'http://arxiv.org/abs/0000.0000')}</id>"
            f"<published>{p.get('published', '2024-01-01T00:00:00Z')}</published>"
            f"{authors}"
            f"</entry>"
        )
    parts.append("</feed>")
    return "".join(parts).encode()


def _fred_series_info(series_id: str, title: str) -> MagicMock:
    return _http(
        {"seriess": [{"id": series_id, "title": title, "units_short": "%", "frequency_short": "M"}]}
    )


def _fred_obs(n: int = 3) -> MagicMock:
    return _http(
        {
            "observations": [
                {"date": f"2024-{n - i:02d}-01", "value": str(4.0 + i * 0.1)} for i in range(n)
            ]
        }
    )


def _make_llm(*responses: str) -> MagicMock:
    llm = MagicMock()
    llm.complete.side_effect = list(responses)
    return llm


ACTION = "Thought: Searching.\nAction: {tool}\nAction Input: {query}"
FINAL = "Thought: Done.\nFinal Answer: {answer}"


# ---------------------------------------------------------------------------
# Wikipedia-only integration
# ---------------------------------------------------------------------------


class TestWikipediaIntegration:
    def test_agent_calls_real_wikipedia_tool_and_extracts_source(self, tmp_path):
        llm = _make_llm(
            ACTION.format(tool="wikipedia_search", query="FDIC history"),
            FINAL.format(answer="The FDIC insures deposits. [Wikipedia: FDIC]"),
        )
        tools = [WikipediaTool()]
        agent = ResearchAgent(tools=tools, llm=llm)

        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.side_effect = [
                _wiki_search(["FDIC"]),
                _wiki_summary("FDIC", "The FDIC protects bank depositors."),
            ]
            response = agent.run("What is the FDIC?")

        assert response.success is True
        assert "wikipedia_search" in response.tools_used
        assert len(response.sources) == 1
        assert response.sources[0]["type"] == "wikipedia"
        assert "en.wikipedia.org" in response.sources[0]["url"]

    def test_wikipedia_content_flows_into_llm_context(self, tmp_path):
        """The LLM's second call should receive the Wikipedia observation text."""
        llm = _make_llm(
            ACTION.format(tool="wikipedia_search", query="Federal Reserve"),
            FINAL.format(answer="Done."),
        )
        tools = [WikipediaTool()]
        agent = ResearchAgent(tools=tools, llm=llm)

        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.side_effect = [
                _wiki_search(["Federal Reserve"]),
                _wiki_summary("Federal Reserve", "The Fed controls monetary policy."),
            ]
            agent.run("About the Fed")

        # Second LLM call receives the Observation message
        second_call_messages = llm.complete.call_args_list[1][0][0]
        obs_msg = next(m for m in reversed(second_call_messages) if m["role"] == "user")
        assert "Observation:" in obs_msg["content"]
        assert "Fed controls monetary policy" in obs_msg["content"]


# ---------------------------------------------------------------------------
# ArXiv integration
# ---------------------------------------------------------------------------


class TestArxivIntegration:
    def test_agent_calls_real_arxiv_tool_and_extracts_source(self):
        llm = _make_llm(
            ACTION.format(tool="arxiv_search", query="credit risk ML"),
            FINAL.format(answer="Found ML papers. [arXiv: 2401.99999]"),
        )
        tools = [ArxivTool()]
        agent = ResearchAgent(tools=tools, llm=llm)

        feed = _arxiv_feed(
            [
                {
                    "title": "Credit Risk with ML",
                    "authors": ["Alice"],
                    "id": "http://arxiv.org/abs/2401.99999",
                    "published": "2024-01-15T00:00:00Z",
                }
            ]
        )

        with patch("agent.tools.arxiv.requests.get") as mock_get:
            mock_get.return_value = _http(content=feed)
            response = agent.run("Recent ML papers on credit risk")

        assert response.success is True
        assert "arxiv_search" in response.tools_used
        assert response.sources[0]["type"] == "arxiv"
        assert response.sources[0]["url"] == "http://arxiv.org/abs/2401.99999"


# ---------------------------------------------------------------------------
# FRED integration
# ---------------------------------------------------------------------------


class TestFREDIntegration:
    def test_agent_calls_real_fred_tool_and_extracts_source(self):
        with patch.dict(os.environ, {"FRED_API_KEY": "test_key"}):
            tools = [FREDTool()]

        llm = _make_llm(
            ACTION.format(tool="fred_search", query="unemployment rate"),
            FINAL.format(answer="US unemployment is 4%. [FRED: UNRATE]"),
        )
        agent = ResearchAgent(tools=tools, llm=llm)

        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.side_effect = [
                _fred_series_info("UNRATE", "Unemployment Rate"),
                _fred_obs(3),
            ]
            response = agent.run("What is the current US unemployment rate?")

        assert response.success is True
        assert "fred_search" in response.tools_used
        assert response.sources[0]["type"] == "fred"
        assert "UNRATE" in response.sources[0]["url"]

    def test_fred_without_api_key_still_completes_run(self):
        env = {k: v for k, v in os.environ.items() if k != "FRED_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            tools = [FREDTool()]

        llm = _make_llm(
            ACTION.format(tool="fred_search", query="unemployment"),
            FINAL.format(answer="Unable to retrieve FRED data — key not set."),
        )
        agent = ResearchAgent(tools=tools, llm=llm)
        # Should not raise even though tool returns failure
        response = agent.run("Unemployment rate?")
        assert response.answer != ""


# ---------------------------------------------------------------------------
# Multi-tool integration
# ---------------------------------------------------------------------------


class TestMultiToolIntegration:
    def test_both_wikipedia_and_arxiv_called_in_sequence(self):
        llm = _make_llm(
            ACTION.format(tool="wikipedia_search", query="quantitative easing"),
            ACTION.format(tool="arxiv_search", query="quantitative easing inflation"),
            FINAL.format(answer="QE affects inflation. [Wikipedia: QE] [arXiv: 2301.00001]"),
        )
        tools = [WikipediaTool(), ArxivTool()]
        agent = ResearchAgent(tools=tools, llm=llm)

        feed = _arxiv_feed(
            [
                {
                    "title": "QE and Inflation",
                    "id": "http://arxiv.org/abs/2301.00001",
                    "published": "2023-01-01T00:00:00Z",
                }
            ]
        )

        # Both tools import `requests` at module level, so they share the same
        # requests.get object. One patch covers all HTTP calls in call order:
        # Wikipedia search → Wikipedia summary → arXiv feed.
        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.side_effect = [
                _wiki_search(["Quantitative easing"]),
                _wiki_summary("Quantitative easing", "QE is monetary policy."),
                _http(content=feed),
            ]
            response = agent.run("What is QE and what do papers say about inflation?")

        assert "wikipedia_search" in response.tools_used
        assert "arxiv_search" in response.tools_used
        wiki_sources = [s for s in response.sources if s["type"] == "wikipedia"]
        arxiv_sources = [s for s in response.sources if s["type"] == "arxiv"]
        assert len(wiki_sources) == 1
        assert len(arxiv_sources) == 1

    def test_duplicate_sources_deduplicated_across_tool_calls(self):
        same_url = "http://arxiv.org/abs/2401.00001"
        llm = _make_llm(
            ACTION.format(tool="arxiv_search", query="credit risk"),
            ACTION.format(tool="arxiv_search", query="credit default"),  # different query
            FINAL.format(answer="Found papers."),
        )
        tools = [ArxivTool()]
        agent = ResearchAgent(tools=tools, llm=llm)

        feed = _arxiv_feed(
            [
                {
                    "title": "Credit Risk Paper",
                    "id": same_url,
                    "published": "2024-01-01T00:00:00Z",
                }
            ]
        )

        with patch("agent.tools.arxiv.requests.get") as mock_get:
            mock_get.return_value = _http(content=feed)
            response = agent.run("Credit risk research")

        urls = [s["url"] for s in response.sources]
        assert urls.count(same_url) == 1  # deduped


# ---------------------------------------------------------------------------
# Tracer integration
# ---------------------------------------------------------------------------


class TestTracerIntegration:
    def test_trace_file_written_after_successful_run(self, tmp_path):
        llm = _make_llm(
            ACTION.format(tool="wikipedia_search", query="FDIC"),
            FINAL.format(answer="The FDIC insures deposits."),
        )
        tools = [WikipediaTool()]
        tracer = Tracer(str(tmp_path))
        agent = ResearchAgent(tools=tools, llm=llm, tracer=tracer)

        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.side_effect = [
                _wiki_search(["FDIC"]),
                _wiki_summary("FDIC", "Federal Deposit Insurance Corporation."),
            ]
            agent.run("What is the FDIC?")

        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1

    def test_trace_file_contains_correct_question_and_answer(self, tmp_path):
        llm = _make_llm(
            ACTION.format(tool="wikipedia_search", query="Basel III"),
            FINAL.format(answer="Basel III sets capital requirements."),
        )
        tools = [WikipediaTool()]
        tracer = Tracer(str(tmp_path))
        agent = ResearchAgent(tools=tools, llm=llm, tracer=tracer)

        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.side_effect = [
                _wiki_search(["Basel III"]),
                _wiki_summary("Basel III", "Basel III is a regulatory framework."),
            ]
            agent.run("What are Basel III requirements?")

        trace_file = list(tmp_path.glob("*.json"))[0]
        with open(trace_file) as f:
            trace = json.load(f)

        assert "Basel III" in trace["question"]
        assert "Basel III sets capital requirements." in trace["answer"]
        assert trace["tools_used"] == ["wikipedia_search"]
        assert trace["num_steps"] >= 1

    def test_trace_step_contains_real_tool_output(self, tmp_path):
        llm = _make_llm(
            ACTION.format(tool="wikipedia_search", query="yield curve"),
            FINAL.format(answer="Done."),
        )
        tools = [WikipediaTool()]
        tracer = Tracer(str(tmp_path))
        agent = ResearchAgent(tools=tools, llm=llm, tracer=tracer)

        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.side_effect = [
                _wiki_search(["Yield curve"]),
                _wiki_summary("Yield curve", "A yield curve plots bond yields vs maturity."),
            ]
            agent.run("What is a yield curve?")

        trace_file = list(tmp_path.glob("*.json"))[0]
        with open(trace_file) as f:
            trace = json.load(f)

        # The observation preview in the trace should contain real Wikipedia content
        action_step = next(s for s in trace["steps"] if s["action"] == "wikipedia_search")
        assert "yield" in action_step["observation_preview"].lower()


# ---------------------------------------------------------------------------
# Regression: observation length limiting
# ---------------------------------------------------------------------------


class TestObservationTruncationRegression:
    def test_very_long_tool_output_truncated_in_llm_context(self):
        """Regression: tools returning large responses must be capped at 3000 chars
        before being injected into LLM messages (prevents rate-limit token blowup)."""
        huge_content = "A" * 6000

        class HugeTool(WikipediaTool):
            name = "wikipedia_search"

            def run(self, query):
                from agent.tools.base import ToolResult

                return ToolResult(content=huge_content, sources=[], success=True)

        llm = _make_llm(
            ACTION.format(tool="wikipedia_search", query="test"),
            FINAL.format(answer="Done."),
        )
        agent = ResearchAgent(tools=[HugeTool()], llm=llm)
        agent.run("test question")

        second_call_messages = llm.complete.call_args_list[1][0][0]
        obs_user_msg = next(m for m in reversed(second_call_messages) if m["role"] == "user")
        # The observation should be truncated: "A" * 3000 present but not 3001
        assert "A" * 3000 in obs_user_msg["content"]
        assert "A" * 3001 not in obs_user_msg["content"]


# ---------------------------------------------------------------------------
# Regression: case-insensitive and stripped dedup key
# ---------------------------------------------------------------------------


class TestDedupRegressions:
    def test_same_query_different_case_counts_as_duplicate(self):
        """Regression: dedup key uses .lower().strip() so mixed-case repeats are blocked."""
        call_count = [0]

        class CountingTool(WikipediaTool):
            name = "wikipedia_search"

            def run(self, query):
                call_count[0] += 1
                from agent.tools.base import ToolResult

                return ToolResult(content="result", sources=[], success=True)

        llm = _make_llm(
            ACTION.format(tool="wikipedia_search", query="Federal Reserve"),
            ACTION.format(tool="wikipedia_search", query="FEDERAL RESERVE"),  # same, different case
            FINAL.format(answer="Done."),
        )
        agent = ResearchAgent(tools=[CountingTool()], llm=llm)
        agent.run("test")

        assert call_count[0] == 1  # second call blocked as duplicate

    def test_same_query_with_extra_whitespace_counts_as_duplicate(self):
        call_count = [0]

        class CountingTool(WikipediaTool):
            name = "wikipedia_search"

            def run(self, query):
                call_count[0] += 1
                from agent.tools.base import ToolResult

                return ToolResult(content="result", sources=[], success=True)

        llm = _make_llm(
            ACTION.format(tool="wikipedia_search", query="gdp"),
            ACTION.format(tool="wikipedia_search", query="  gdp  "),  # whitespace variant
            FINAL.format(answer="Done."),
        )
        agent = ResearchAgent(tools=[CountingTool()], llm=llm)
        agent.run("test")

        assert call_count[0] == 1
