"""Tests for WikipediaTool — search, summary fetching, and error paths."""

from unittest.mock import MagicMock, patch

import pytest

from agent.tools.wikipedia import WikipediaTool

SEARCH_RESPONSE = {
    "query": {
        "search": [
            {"title": "Federal Reserve"},
            {"title": "Federal Reserve System"},
        ]
    }
}

SUMMARY_RESPONSE = {
    "extract": "The Federal Reserve is the central banking system of the United States.",
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Federal_Reserve"}},
}


def _mock_response(json_data=None, status_code=200):
    m = MagicMock()
    m.status_code = status_code
    if json_data is not None:
        m.json.return_value = json_data
    m.raise_for_status = MagicMock()
    return m


class TestWikipediaToolMeta:
    def test_name(self):
        assert WikipediaTool().name == "wikipedia_search"

    def test_description_mentions_wikipedia(self):
        assert "Wikipedia" in WikipediaTool().description


class TestWikipediaToolRun:
    def setup_method(self):
        self.tool = WikipediaTool()

    def test_successful_search_returns_content_and_sources(self):
        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SEARCH_RESPONSE),
                _mock_response(SUMMARY_RESPONSE),
                _mock_response(SUMMARY_RESPONSE),
            ]
            result = self.tool.run("Federal Reserve")

        assert result.success is True
        assert "Federal Reserve" in result.content
        assert len(result.sources) == 2

    def test_source_has_correct_type_and_url(self):
        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SEARCH_RESPONSE),
                _mock_response(SUMMARY_RESPONSE),
                _mock_response(SUMMARY_RESPONSE),
            ]
            result = self.tool.run("Federal Reserve")

        assert result.sources[0]["type"] == "wikipedia"
        assert result.sources[0]["url"] == "https://en.wikipedia.org/wiki/Federal_Reserve"

    def test_no_search_results_returns_failure(self):
        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.return_value = _mock_response({"query": {"search": []}})
            result = self.tool.run("xyznonexistentquery")

        assert result.success is False
        assert "No Wikipedia articles found" in result.content

    def test_all_summaries_fail_returns_failure(self):
        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SEARCH_RESPONSE),
                _mock_response(status_code=404),
                _mock_response(status_code=404),
            ]
            result = self.tool.run("something")

        assert result.success is False
        assert "could not retrieve content" in result.content

    def test_one_summary_succeeds_partial_result(self):
        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SEARCH_RESPONSE),
                _mock_response(SUMMARY_RESPONSE),
                _mock_response(status_code=404),
            ]
            result = self.tool.run("Federal Reserve")

        assert result.success is True
        assert len(result.sources) == 1

    def test_extract_truncated_to_2500_chars(self):
        long_summary = {
            "extract": "x" * 5000,
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Test"}},
        }
        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response({"query": {"search": [{"title": "Test"}]}}),
                _mock_response(long_summary),
            ]
            result = self.tool.run("test")

        # "### Test\n\n" prefix + up to 2500 chars of extract
        assert "x" * 2500 in result.content
        assert "x" * 2501 not in result.content

    def test_title_spaces_become_underscores_in_url(self):
        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response({"query": {"search": [{"title": "Credit Default Swap"}]}}),
                _mock_response(SUMMARY_RESPONSE),
            ]
            self.tool.run("credit default swap")

        second_call_url = mock_get.call_args_list[1][0][0]
        assert "Credit_Default_Swap" in second_call_url

    def test_fallback_url_when_content_urls_missing(self):
        summary_no_urls = {"extract": "Some content here."}
        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response({"query": {"search": [{"title": "Test Article"}]}}),
                _mock_response(summary_no_urls),
            ]
            result = self.tool.run("test")

        assert result.success is True
        assert result.sources[0]["url"] == "https://en.wikipedia.org/wiki/Test_Article"

    def test_only_first_two_articles_fetched(self):
        three_results = {
            "query": {
                "search": [
                    {"title": "Article A"},
                    {"title": "Article B"},
                    {"title": "Article C"},
                ]
            }
        }
        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(three_results),
                _mock_response(SUMMARY_RESPONSE),
                _mock_response(SUMMARY_RESPONSE),
            ]
            self.tool.run("something")

        # Only 2 summary fetches (for A and B), not 3
        assert mock_get.call_count == 3  # 1 search + 2 summaries

    def test_network_exception_propagates_after_retries(self):
        with patch("agent.tools.wikipedia.requests.get") as mock_get, patch("time.sleep"):
            mock_get.side_effect = ConnectionError("Connection refused")
            with pytest.raises(ConnectionError):
                self.tool.run("anything")

    def test_content_includes_article_title_as_heading(self):
        with patch("agent.tools.wikipedia.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response({"query": {"search": [{"title": "Basel III"}]}}),
                _mock_response(
                    {
                        "extract": "Basel III is a regulatory framework.",
                        "content_urls": {
                            "desktop": {"page": "https://en.wikipedia.org/wiki/Basel_III"}
                        },
                    }
                ),
            ]
            result = self.tool.run("Basel III")

        assert "### Basel III" in result.content
