"""Shared pytest fixtures."""

import pytest

from agent.tools.base import BaseTool, ToolResult


@pytest.fixture
def stub_tool():
    """A minimal always-succeeding tool for use in agent tests."""

    class _StubTool(BaseTool):
        name = "stub_tool"
        description = "Stub tool for testing."

        def run(self, query: str) -> ToolResult:
            return ToolResult(
                content=f"Result for: {query}",
                sources=[{"title": "Stub", "url": "http://stub.example.com", "type": "stub"}],
                success=True,
            )

    return _StubTool()
