"""Tests for ResearchAgent — run loop, parsing, source deduplication, and tracer integration."""

from unittest.mock import MagicMock

from agent.core import AgentResponse, ResearchAgent
from agent.tools.base import BaseTool, ToolResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ACTION_TMPL = "Thought: Searching.\nAction: {tool}\nAction Input: {query}"
FINAL = "Thought: Done.\nFinal Answer: The answer is 42."


def _make_tool(
    name: str = "mock_tool",
    content: str = "Tool result",
    success: bool = True,
    sources: list | None = None,
) -> MagicMock:
    tool = MagicMock(spec=BaseTool)
    tool.name = name
    tool.description = f"{name} description"
    tool.run.return_value = ToolResult(
        content=content,
        sources=sources or [{"title": "Src", "url": f"http://{name}.test", "type": "test"}],
        success=success,
    )
    return tool


def _make_llm(*responses: str) -> MagicMock:
    llm = MagicMock()
    llm.complete.side_effect = list(responses)
    return llm


# ---------------------------------------------------------------------------
# ResearchAgent.run — happy paths
# ---------------------------------------------------------------------------


class TestAgentRunHappyPath:
    def test_single_action_then_final_answer(self):
        tool = _make_tool()
        llm = _make_llm(
            ACTION_TMPL.format(tool="mock_tool", query="test query"),
            FINAL,
        )
        agent = ResearchAgent(tools=[tool], llm=llm)
        response = agent.run("What is 42?")

        assert response.success is True
        assert response.answer == "The answer is 42."
        assert "mock_tool" in response.tools_used
        tool.run.assert_called_once_with("test query")

    def test_multiple_different_tools_used(self):
        tool1 = _make_tool("tool1")
        tool2 = _make_tool("tool2")
        llm = _make_llm(
            ACTION_TMPL.format(tool="tool1", query="q1"),
            ACTION_TMPL.format(tool="tool2", query="q2"),
            FINAL,
        )
        agent = ResearchAgent(tools=[tool1, tool2], llm=llm)
        response = agent.run("multi-tool question")

        assert "tool1" in response.tools_used
        assert "tool2" in response.tools_used

    def test_response_has_steps(self):
        tool = _make_tool()
        llm = _make_llm(
            ACTION_TMPL.format(tool="mock_tool", query="q"),
            FINAL,
        )
        agent = ResearchAgent(tools=[tool], llm=llm)
        response = agent.run("test")
        assert len(response.steps) >= 1

    def test_tools_used_list_has_no_duplicates(self):
        tool = _make_tool()
        llm = _make_llm(
            ACTION_TMPL.format(tool="mock_tool", query="first query"),
            ACTION_TMPL.format(tool="mock_tool", query="second query"),
            FINAL,
        )
        agent = ResearchAgent(tools=[tool], llm=llm)
        response = agent.run("test")
        assert response.tools_used.count("mock_tool") == 1

    def test_sources_collected_on_success(self):
        sources = [{"title": "T", "url": "http://src.test", "type": "wiki"}]
        tool = _make_tool(sources=sources)
        llm = _make_llm(ACTION_TMPL.format(tool="mock_tool", query="q"), FINAL)
        agent = ResearchAgent(tools=[tool], llm=llm)
        response = agent.run("test")
        assert response.sources == sources

    def test_failed_tool_sources_not_collected(self):
        sources = [{"title": "T", "url": "http://fail.test", "type": "wiki"}]
        tool = _make_tool(success=False, sources=sources)
        llm = _make_llm(ACTION_TMPL.format(tool="mock_tool", query="q"), FINAL)
        agent = ResearchAgent(tools=[tool], llm=llm)
        response = agent.run("test")
        assert response.sources == []

    def test_no_tracer_does_not_crash(self):
        tool = _make_tool()
        llm = _make_llm(ACTION_TMPL.format(tool="mock_tool", query="q"), FINAL)
        agent = ResearchAgent(tools=[tool], llm=llm, tracer=None)
        response = agent.run("test")
        assert response is not None


# ---------------------------------------------------------------------------
# ResearchAgent.run — error / edge paths
# ---------------------------------------------------------------------------


class TestAgentRunEdgeCases:
    def test_unknown_tool_does_not_crash(self):
        real_tool = _make_tool("real_tool")
        llm = _make_llm(
            ACTION_TMPL.format(tool="ghost_tool", query="q"),
            FINAL,
        )
        agent = ResearchAgent(tools=[real_tool], llm=llm)
        response = agent.run("test")

        real_tool.run.assert_not_called()
        assert response.answer == "The answer is 42."

    def test_duplicate_query_blocked(self):
        tool = _make_tool()
        same_action = ACTION_TMPL.format(tool="mock_tool", query="test query")
        llm = _make_llm(same_action, same_action, FINAL)
        agent = ResearchAgent(tools=[tool], llm=llm)
        agent.run("test")
        # Same action+query should only execute tool once
        assert tool.run.call_count == 1

    def test_max_steps_forces_synthesis(self):
        tool = _make_tool()
        # Unique queries so nothing is deduped — fills all 8 slots
        actions = [
            ACTION_TMPL.format(tool="mock_tool", query=f"unique q{i}")
            for i in range(ResearchAgent.MAX_STEPS)
        ]
        llm = _make_llm(*actions, FINAL)  # synthesis call returns FINAL
        agent = ResearchAgent(tools=[tool], llm=llm)
        response = agent.run("relentless question")

        assert response.answer is not None
        assert response.answer != ""

    def test_malformed_output_recovered_with_nudge(self):
        tool = _make_tool()
        llm = _make_llm("I am confused and not following the format!", FINAL)
        agent = ResearchAgent(tools=[tool], llm=llm)
        response = agent.run("test")

        tool.run.assert_not_called()
        assert response.answer == "The answer is 42."

    def test_tracer_called_with_response(self):
        tool = _make_tool()
        llm = _make_llm(ACTION_TMPL.format(tool="mock_tool", query="q"), FINAL)
        tracer = MagicMock()
        agent = ResearchAgent(tools=[tool], llm=llm, tracer=tracer)
        agent.run("test")

        tracer.save.assert_called_once()
        saved_response = tracer.save.call_args[0][0]
        assert isinstance(saved_response, AgentResponse)

    def test_tracer_exception_does_not_propagate(self):
        tool = _make_tool()
        llm = _make_llm(ACTION_TMPL.format(tool="mock_tool", query="q"), FINAL)
        tracer = MagicMock()
        tracer.save.side_effect = OSError("disk full")
        agent = ResearchAgent(tools=[tool], llm=llm, tracer=tracer)

        response = agent.run("test")
        assert response is not None

    def test_question_preserved_in_response(self):
        tool = _make_tool()
        llm = _make_llm(ACTION_TMPL.format(tool="mock_tool", query="q"), FINAL)
        agent = ResearchAgent(tools=[tool], llm=llm)
        response = agent.run("What is the yield curve?")
        assert response.question == "What is the yield curve?"

    def test_duration_is_positive(self):
        tool = _make_tool()
        llm = _make_llm(ACTION_TMPL.format(tool="mock_tool", query="q"), FINAL)
        agent = ResearchAgent(tools=[tool], llm=llm)
        response = agent.run("test")
        assert response.total_duration_ms > 0


# ---------------------------------------------------------------------------
# ResearchAgent._parse
# ---------------------------------------------------------------------------


class TestAgentParse:
    def setup_method(self):
        self.agent = ResearchAgent(tools=[], llm=MagicMock())

    def test_parse_final_answer(self):
        text = "Thought: Done.\nFinal Answer: The result is X."
        result = self.agent._parse(text)
        assert result["type"] == "final"
        assert result["answer"] == "The result is X."
        assert result["thought"] == "Done."

    def test_parse_action(self):
        text = "Thought: Need info.\nAction: wikipedia_search\nAction Input: Federal Reserve"
        result = self.agent._parse(text)
        assert result["type"] == "action"
        assert result["tool"] == "wikipedia_search"
        assert result["query"] == "Federal Reserve"
        assert result["thought"] == "Need info."

    def test_parse_final_answer_case_insensitive(self):
        text = "Thought: Done.\nfinal answer: finished."
        result = self.agent._parse(text)
        assert result["type"] == "final"

    def test_parse_action_case_insensitive(self):
        text = "Thought: Searching.\naction: fred_search\naction input: GDP"
        result = self.agent._parse(text)
        assert result["type"] == "action"

    def test_parse_unknown_when_no_pattern(self):
        text = "I am confused and rambling."
        result = self.agent._parse(text)
        assert result["type"] == "unknown"

    def test_parse_final_answer_takes_priority_over_action(self):
        text = (
            "Thought: Done.\n"
            "Action: some_tool\n"
            "Action Input: something\n"
            "Final Answer: The real answer."
        )
        result = self.agent._parse(text)
        assert result["type"] == "final"
        assert result["answer"] == "The real answer."

    def test_parse_multiline_final_answer_captured(self):
        text = "Thought: Done.\nFinal Answer: Line one.\nLine two.\nLine three."
        result = self.agent._parse(text)
        assert result["type"] == "final"
        assert "Line one." in result["answer"]
        assert "Line two." in result["answer"]

    def test_parse_action_without_thought(self):
        text = "Action: arxiv_search\nAction Input: machine learning credit risk"
        result = self.agent._parse(text)
        assert result["type"] == "action"
        assert result["thought"] == ""

    def test_parse_final_answer_without_thought(self):
        text = "Final Answer: Just the answer."
        result = self.agent._parse(text)
        assert result["type"] == "final"
        assert result["thought"] == ""

    def test_parse_action_input_multiword(self):
        text = "Thought: x\nAction: fred_search\nAction Input: federal funds rate target"
        result = self.agent._parse(text)
        assert result["query"] == "federal funds rate target"


# ---------------------------------------------------------------------------
# ResearchAgent._dedupe
# ---------------------------------------------------------------------------


class TestAgentDedupe:
    def setup_method(self):
        self.agent = ResearchAgent(tools=[], llm=MagicMock())

    def test_dedupes_by_url(self):
        sources = [
            {"title": "A", "url": "http://a.com"},
            {"title": "B", "url": "http://a.com"},
            {"title": "C", "url": "http://c.com"},
        ]
        result = self.agent._dedupe(sources)
        assert len(result) == 2
        assert result[0]["title"] == "A"
        assert result[1]["title"] == "C"

    def test_dedupes_by_title_when_no_url(self):
        sources = [
            {"title": "Same"},
            {"title": "Same"},
            {"title": "Different"},
        ]
        result = self.agent._dedupe(sources)
        assert len(result) == 2

    def test_preserves_insertion_order(self):
        sources = [
            {"url": "http://1.com", "title": "First"},
            {"url": "http://2.com", "title": "Second"},
            {"url": "http://3.com", "title": "Third"},
        ]
        result = self.agent._dedupe(sources)
        assert [r["title"] for r in result] == ["First", "Second", "Third"]

    def test_empty_list(self):
        assert self.agent._dedupe([]) == []

    def test_no_duplicates_unchanged(self):
        sources = [
            {"url": "http://a.com", "title": "A"},
            {"url": "http://b.com", "title": "B"},
        ]
        result = self.agent._dedupe(sources)
        assert len(result) == 2

    def test_url_takes_precedence_over_title_for_dedup_key(self):
        sources = [
            {"url": "http://same.com", "title": "Title A"},
            {"url": "http://same.com", "title": "Title B"},  # same url, different title
        ]
        result = self.agent._dedupe(sources)
        assert len(result) == 1
        assert result[0]["title"] == "Title A"
