"""Tests for FREDTool — init, series resolution, data fetching, and error paths."""

import os
from unittest.mock import MagicMock, patch

import pytest

from agent.tools.fred import FREDTool

SERIES_INFO_RESP = {
    "seriess": [
        {
            "id": "UNRATE",
            "title": "Unemployment Rate",
            "units_short": "%",
            "frequency_short": "M",
        }
    ]
}


def _obs(n: int) -> dict:
    """Return n monthly observations with distinct numeric values."""
    return {
        "observations": [
            {"date": f"2024-{n - i:02d}-01", "value": str(round(4.0 + i * 0.1, 1))}
            for i in range(n)
        ]
    }


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data
    m.raise_for_status = MagicMock()
    return m


class TestFREDToolInit:
    def test_no_env_var_gives_empty_key(self):
        env = {k: v for k, v in os.environ.items() if k != "FRED_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            tool = FREDTool()
        assert tool.api_key == ""

    def test_placeholder_key_treated_as_missing(self):
        with patch.dict(os.environ, {"FRED_API_KEY": "your_fred_api_key_here"}):
            tool = FREDTool()
        assert tool.api_key == ""

    def test_empty_string_env_treated_as_missing(self):
        with patch.dict(os.environ, {"FRED_API_KEY": ""}):
            tool = FREDTool()
        assert tool.api_key == ""

    def test_real_key_preserved(self):
        with patch.dict(os.environ, {"FRED_API_KEY": "realkey123"}):
            tool = FREDTool()
        assert tool.api_key == "realkey123"


class TestFREDToolRunNoKey:
    def test_missing_key_returns_failure_with_instructions(self):
        env = {k: v for k, v in os.environ.items() if k != "FRED_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            tool = FREDTool()
        result = tool.run("unemployment")

        assert result.success is False
        assert "FRED_API_KEY" in result.content
        assert result.error == "FRED_API_KEY not configured"


class TestFREDToolResolveSeriesKeyword:
    def setup_method(self):
        with patch.dict(os.environ, {"FRED_API_KEY": "testkey"}):
            self.tool = FREDTool()

    @pytest.mark.parametrize(
        "query,expected",
        [
            ("unemployment rate in the US", "UNRATE"),
            ("UNEMPLOYMENT", "UNRATE"),
            ("US GDP data", "GDP"),
            ("gross domestic product", "GDP"),
            ("CPI last month", "CPIAUCSL"),
            ("inflation trend", "CPIAUCSL"),
            ("consumer price index", "CPIAUCSL"),
            ("federal funds rate", "FEDFUNDS"),
            ("Fed funds overnight", "FEDFUNDS"),
            ("10-year treasury yield", "DGS10"),
            ("10 year treasury", "DGS10"),
            ("2-year treasury", "DGS2"),
            ("yield curve spread", "T10Y2Y"),
            ("mortgage rate 30-year", "MORTGAGE30US"),
            ("30-year mortgage", "MORTGAGE30US"),
            ("M2 money supply", "M2SL"),
            ("money supply M2", "M2SL"),
        ],
    )
    def test_known_keyword_matched_case_insensitively(self, query, expected):
        assert self.tool._resolve_series(query) == expected

    def test_unknown_query_falls_back_to_api_search(self):
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.return_value = _mock_response({"seriess": [{"id": "CUSTOM99"}]})
            result = self.tool._resolve_series("some obscure indicator")
        assert result == "CUSTOM99"

    def test_api_search_no_results_returns_none(self):
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.return_value = _mock_response({"seriess": []})
            result = self.tool._resolve_series("absolutelyunknown xyz")
        assert result is None

    def test_api_search_exception_returns_none(self):
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.side_effect = ConnectionError("timeout")
            result = self.tool._resolve_series("some indicator")
        assert result is None


class TestFREDToolRunSeriesResolution:
    def setup_method(self):
        with patch.dict(os.environ, {"FRED_API_KEY": "testkey"}):
            self.tool = FREDTool()

    def test_known_keyword_routes_to_fetch(self):
        with patch.object(self.tool, "_fetch_series") as mock_fetch:
            mock_fetch.return_value = MagicMock(success=True, content="data", sources=[])
            self.tool.run("unemployment rate")
        mock_fetch.assert_called_once_with("UNRATE")

    def test_unresolvable_series_returns_failure(self):
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.return_value = _mock_response({"seriess": []})
            result = self.tool.run("xyzabsolutelyunknown")
        assert result.success is False
        assert "No FRED series found" in result.content


class TestFREDFetchSeries:
    def setup_method(self):
        with patch.dict(os.environ, {"FRED_API_KEY": "testkey"}):
            self.tool = FREDTool()

    def test_successful_fetch_structure(self):
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SERIES_INFO_RESP),
                _mock_response(_obs(3)),
            ]
            result = self.tool._fetch_series("UNRATE")

        assert result.success is True
        assert "Unemployment Rate" in result.content
        assert "UNRATE" in result.content
        assert len(result.sources) == 1
        assert result.sources[0]["type"] == "fred"

    def test_source_url_points_to_fred(self):
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SERIES_INFO_RESP),
                _mock_response(_obs(3)),
            ]
            result = self.tool._fetch_series("UNRATE")

        assert result.sources[0]["url"] == "https://fred.stlouisfed.org/series/UNRATE"

    def test_content_includes_markdown_table(self):
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SERIES_INFO_RESP),
                _mock_response(_obs(3)),
            ]
            result = self.tool._fetch_series("UNRATE")

        assert "| Date | Value |" in result.content
        assert "|------|-------|" in result.content

    def test_year_over_year_shown_with_12_or_more_obs(self):
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SERIES_INFO_RESP),
                _mock_response(_obs(12)),
            ]
            result = self.tool._fetch_series("UNRATE")

        assert "Change over past year" in result.content

    def test_year_over_year_skipped_with_fewer_than_12_obs(self):
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SERIES_INFO_RESP),
                _mock_response(_obs(5)),
            ]
            result = self.tool._fetch_series("UNRATE")

        assert "Change over past year" not in result.content

    def test_dot_values_excluded(self):
        obs_with_dots = {
            "observations": [
                {"date": "2024-03-01", "value": "4.2"},
                {"date": "2024-02-01", "value": "."},
                {"date": "2024-01-01", "value": "4.0"},
            ]
        }
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SERIES_INFO_RESP),
                _mock_response(obs_with_dots),
            ]
            result = self.tool._fetch_series("UNRATE")

        assert result.success is True
        # Dot row should not appear in the table
        assert " . " not in result.content

    def test_all_dot_values_returns_failure(self):
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SERIES_INFO_RESP),
                _mock_response({"observations": [{"date": "2024-01-01", "value": "."}]}),
            ]
            result = self.tool._fetch_series("UNRATE")

        assert result.success is False
        assert "No data available" in result.content

    def test_series_info_unavailable_falls_back_to_series_id(self):
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response({}, status_code=500),
                _mock_response(_obs(3)),
            ]
            result = self.tool._fetch_series("CUSTOM99")

        assert result.success is True
        assert "CUSTOM99" in result.content

    def test_units_appended_in_latest_line(self):
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SERIES_INFO_RESP),
                _mock_response(_obs(3)),
            ]
            result = self.tool._fetch_series("UNRATE")

        assert "%" in result.content

    def test_latest_observation_shown_in_header(self):
        obs = _obs(3)
        latest_date = obs["observations"][0]["date"]
        latest_val = obs["observations"][0]["value"]
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SERIES_INFO_RESP),
                _mock_response(obs),
            ]
            result = self.tool._fetch_series("UNRATE")

        assert latest_date in result.content
        assert latest_val in result.content

    def test_at_most_13_rows_in_table(self):
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SERIES_INFO_RESP),
                _mock_response(_obs(20)),
            ]
            result = self.tool._fetch_series("UNRATE")

        # Count data rows (lines with | date | value | pattern, minus 2 header rows)
        data_rows = [
            line
            for line in result.content.splitlines()
            if line.startswith("| 20")  # date rows start with | 20xx-
        ]
        assert len(data_rows) <= 13

    def test_network_failure_returns_failure_result(self):
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SERIES_INFO_RESP),
                ConnectionError("timeout"),
            ]
            result = self.tool._fetch_series("UNRATE")

        assert result.success is False
        assert result.error != ""

    def test_non_numeric_observation_values_skip_year_over_year(self):
        """Covers fred.py lines 148-149 — ValueError in float() silently skipped."""
        obs_non_numeric = {
            "observations": [
                {"date": f"2024-{12 - i:02d}-01", "value": "N/A" if i == 0 else str(4.0 + i * 0.1)}
                for i in range(12)
            ]
        }
        with patch("agent.tools.fred.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_response(SERIES_INFO_RESP),
                _mock_response(obs_non_numeric),
            ]
            result = self.tool._fetch_series("UNRATE")

        # Run completes without raising; year-over-year block is skipped
        assert result.success is True
        assert "Change over past year" not in result.content
