"""Tests for ToolResult dataclass, with_retry decorator, and BaseTool ABC."""

from unittest.mock import call, patch

import pytest

from agent.tools.base import BaseTool, ToolResult, with_retry


class TestToolResult:
    def test_defaults(self):
        r = ToolResult(content="hello")
        assert r.content == "hello"
        assert r.sources == []
        assert r.success is True
        assert r.error == ""

    def test_sources_not_shared_between_instances(self):
        r1 = ToolResult(content="a")
        r2 = ToolResult(content="b")
        r1.sources.append({"x": 1})
        assert r2.sources == []

    def test_failure_fields(self):
        r = ToolResult(content="oops", success=False, error="bad thing happened")
        assert not r.success
        assert r.error == "bad thing happened"

    def test_custom_sources(self):
        src = [{"title": "T", "url": "http://x.com", "type": "wiki"}]
        r = ToolResult(content="ok", sources=src)
        assert r.sources == src


class TestWithRetry:
    def test_succeeds_on_first_attempt(self):
        calls = []

        @with_retry(max_attempts=3)
        def fn():
            calls.append(1)
            return "ok"

        assert fn() == "ok"
        assert len(calls) == 1

    def test_retries_until_success(self):
        attempts = []

        @with_retry(max_attempts=3)
        def fn():
            attempts.append(1)
            if len(attempts) < 3:
                raise ValueError("transient")
            return "success"

        with patch("agent.tools.base.time.sleep"):
            result = fn()
        assert result == "success"
        assert len(attempts) == 3

    def test_raises_last_exception_after_max_attempts(self):
        @with_retry(max_attempts=3)
        def fn():
            raise RuntimeError("always fails")

        with (
            patch("agent.tools.base.time.sleep"),
            pytest.raises(RuntimeError, match="always fails"),
        ):
            fn()

    def test_exponential_backoff_timing(self):
        @with_retry(max_attempts=3, backoff=2.0)
        def fn():
            raise ValueError("fail")

        with patch("agent.tools.base.time.sleep") as mock_sleep:
            with pytest.raises(ValueError):
                fn()
        # attempt 0 → wait 2**0 = 1s; attempt 1 → wait 2**1 = 2s; attempt 2 → raise
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0] == call(1.0)
        assert mock_sleep.call_args_list[1] == call(2.0)

    def test_429_triggers_three_second_wait(self):
        @with_retry(max_attempts=3)
        def fn():
            raise Exception("429 Too Many Requests")

        with patch("agent.tools.base.time.sleep") as mock_sleep:
            with pytest.raises(Exception):
                fn()
        for c in mock_sleep.call_args_list:
            assert c == call(3.0)

    def test_too_many_requests_string_triggers_three_second_wait(self):
        @with_retry(max_attempts=2)
        def fn():
            raise Exception("Too Many Requests from server")

        with patch("agent.tools.base.time.sleep") as mock_sleep:
            with pytest.raises(Exception):
                fn()
        assert mock_sleep.call_args_list[0] == call(3.0)

    def test_no_sleep_on_last_attempt(self):
        """Sleep is only called between attempts, never after the final failure."""

        @with_retry(max_attempts=3)
        def fn():
            raise ValueError("fail")

        with patch("agent.tools.base.time.sleep") as mock_sleep:
            with pytest.raises(ValueError):
                fn()
        # 3 attempts → 2 sleeps
        assert mock_sleep.call_count == 2

    def test_preserves_return_value(self):
        @with_retry(max_attempts=2)
        def fn():
            return {"key": "value"}

        with patch("agent.tools.base.time.sleep"):
            assert fn() == {"key": "value"}

    def test_passes_args_and_kwargs(self):
        @with_retry(max_attempts=2)
        def fn(a, b, c=0):
            return a + b + c

        assert fn(1, 2, c=3) == 6


class TestBaseTool:
    def test_cannot_instantiate_abstract_class(self):
        with pytest.raises(TypeError):
            BaseTool()  # type: ignore[abstract]

    def test_subclass_must_implement_run(self):
        class Incomplete(BaseTool):
            name = "incomplete"
            description = "missing run"

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_concrete_subclass_repr(self):
        from agent.tools.wikipedia import WikipediaTool

        t = WikipediaTool()
        assert "WikipediaTool" in repr(t)
        assert "wikipedia_search" in repr(t)

    def test_concrete_subclass_instantiates(self):
        from agent.tools.wikipedia import WikipediaTool

        t = WikipediaTool()
        assert t.name == "wikipedia_search"
        assert t.description
