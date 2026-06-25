"""Tests for Tracer — file creation, JSON structure, slug generation, observation preview."""

import json
from pathlib import Path

from agent.core import AgentResponse, AgentStep
from agent.tracer import Tracer


def _make_step(
    num: int = 1,
    thought: str = "thinking",
    action: str = "wiki",
    action_input: str = "query",
    observation: str = "result",
    duration_ms: float = 50.0,
) -> AgentStep:
    s = AgentStep(step_num=num)
    s.thought = thought
    s.action = action
    s.action_input = action_input
    s.observation = observation
    s.duration_ms = duration_ms
    return s


def _make_response(
    question: str = "What is GDP?",
    answer: str = "GDP is gross domestic product.",
    steps: list | None = None,
    sources: list | None = None,
    tools_used: list | None = None,
    duration_ms: float = 1500.0,
    success: bool = True,
) -> AgentResponse:
    return AgentResponse(
        question=question,
        answer=answer,
        sources=sources or [],
        steps=steps or [],
        total_duration_ms=duration_ms,
        tools_used=tools_used or ["wikipedia_search"],
        success=success,
    )


class TestTracerInit:
    def test_creates_directory(self, tmp_path):
        target = tmp_path / "new_traces"
        assert not target.exists()
        Tracer(str(target))
        assert target.exists()

    def test_creates_nested_directory(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        Tracer(str(nested))
        assert nested.exists()

    def test_existing_directory_does_not_raise(self, tmp_path):
        Tracer(str(tmp_path))  # should not raise even if dir exists


class TestTracerSave:
    def test_creates_a_file(self, tmp_path):
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response())
        assert Path(filepath).exists()

    def test_returns_string_filepath(self, tmp_path):
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response())
        assert isinstance(filepath, str)
        assert str(tmp_path) in filepath

    def test_file_is_valid_json(self, tmp_path):
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response())
        with open(filepath) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_trace_top_level_keys(self, tmp_path):
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response())
        with open(filepath) as f:
            data = json.load(f)
        for key in (
            "question",
            "timestamp",
            "success",
            "total_duration_ms",
            "tools_used",
            "num_steps",
            "sources",
            "answer",
            "steps",
        ):
            assert key in data, f"Missing key: {key}"

    def test_question_and_answer_preserved(self, tmp_path):
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(
            _make_response(question="What is CPI?", answer="CPI measures prices.")
        )
        with open(filepath) as f:
            data = json.load(f)
        assert data["question"] == "What is CPI?"
        assert data["answer"] == "CPI measures prices."

    def test_duration_rounded_to_integer(self, tmp_path):
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response(duration_ms=1234.7))
        with open(filepath) as f:
            data = json.load(f)
        assert data["total_duration_ms"] == 1235

    def test_sources_included(self, tmp_path):
        sources = [{"title": "Fed Reserve", "url": "http://fed.gov", "type": "wikipedia"}]
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response(sources=sources))
        with open(filepath) as f:
            data = json.load(f)
        assert data["sources"] == sources

    def test_num_steps_matches_step_count(self, tmp_path):
        steps = [_make_step(i) for i in range(3)]
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response(steps=steps))
        with open(filepath) as f:
            data = json.load(f)
        assert data["num_steps"] == 3
        assert len(data["steps"]) == 3

    def test_step_fields(self, tmp_path):
        step = _make_step(
            num=2,
            thought="reasoning",
            action="fred_search",
            action_input="unemployment",
            observation="4.2%",
            duration_ms=75.0,
        )
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response(steps=[step]))
        with open(filepath) as f:
            data = json.load(f)
        s = data["steps"][0]
        assert s["step"] == 2
        assert s["thought"] == "reasoning"
        assert s["action"] == "fred_search"
        assert s["action_input"] == "unemployment"
        assert s["duration_ms"] == 75

    def test_short_observation_preview_no_ellipsis(self, tmp_path):
        step = _make_step(observation="short obs")
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response(steps=[step]))
        with open(filepath) as f:
            data = json.load(f)
        preview = data["steps"][0]["observation_preview"]
        assert preview == "short obs"
        assert not preview.endswith("...")

    def test_long_observation_truncated_at_600_with_ellipsis(self, tmp_path):
        long_obs = "x" * 1000
        step = _make_step(observation=long_obs)
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response(steps=[step]))
        with open(filepath) as f:
            data = json.load(f)
        preview = data["steps"][0]["observation_preview"]
        assert len(preview) == 603  # 600 chars + "..."
        assert preview.endswith("...")

    def test_observation_length_field_is_exact(self, tmp_path):
        obs = "hello world"
        step = _make_step(observation=obs)
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response(steps=[step]))
        with open(filepath) as f:
            data = json.load(f)
        assert data["steps"][0]["observation_length"] == len(obs)

    def test_exactly_600_char_observation_has_no_ellipsis(self, tmp_path):
        obs = "a" * 600
        step = _make_step(observation=obs)
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response(steps=[step]))
        with open(filepath) as f:
            data = json.load(f)
        preview = data["steps"][0]["observation_preview"]
        assert not preview.endswith("...")
        assert len(preview) == 600


class TestTracerSlug:
    def test_question_words_appear_in_filename(self, tmp_path):
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response(question="What is the Fed discount rate"))
        filename = Path(filepath).name
        assert "What_is_the_Fed" in filename

    def test_question_mark_removed_from_slug(self, tmp_path):
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response(question="What is GDP?"))
        filename = Path(filepath).name
        assert "?" not in filename

    def test_slash_becomes_dash_in_slug(self, tmp_path):
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response(question="GDP/CPI ratio"))
        filename = Path(filepath).name
        assert "/" not in filename

    def test_multiple_saves_create_separate_files(self, tmp_path):
        tracer = Tracer(str(tmp_path))
        tracer.save(_make_response(question="Question A here"))
        tracer.save(_make_response(question="Question B there"))
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 2

    def test_filename_includes_timestamp_prefix(self, tmp_path):
        tracer = Tracer(str(tmp_path))
        filepath = tracer.save(_make_response())
        filename = Path(filepath).name
        # Timestamp prefix: YYYYMMDD_HHMMSS_
        import re

        assert re.match(r"\d{8}_\d{6}_", filename)
