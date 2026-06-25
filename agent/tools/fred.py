"""FRED tool — retrieves Federal Reserve economic data series."""

import os
import requests
from .base import BaseTool, ToolResult, with_retry

BASE_URL = "https://api.stlouisfed.org/fred"
TIMEOUT = 12

# Common series for instant lookup without a search round-trip
KNOWN_SERIES = {
    "unemployment": "UNRATE",
    "unemployment rate": "UNRATE",
    "gdp": "GDP",
    "gross domestic product": "GDP",
    "cpi": "CPIAUCSL",
    "inflation": "CPIAUCSL",
    "consumer price": "CPIAUCSL",
    "federal funds rate": "FEDFUNDS",
    "fed funds": "FEDFUNDS",
    "discount rate": "MDISCRATE",
    "10-year treasury": "DGS10",
    "10 year treasury": "DGS10",
    "2-year treasury": "DGS2",
    "2 year treasury": "DGS2",
    "yield curve": "T10Y2Y",
    "mortgage rate": "MORTGAGE30US",
    "30-year mortgage": "MORTGAGE30US",
    "m2": "M2SL",
    "money supply": "M2SL",
}


class FREDTool(BaseTool):
    name = "fred_search"
    description = (
        "Search FRED (Federal Reserve Economic Data) for US economic data. "
        "Best for current and historical values of GDP, unemployment, interest rates, "
        "inflation, treasury yields, and other economic indicators. "
        "Requires FRED_API_KEY (free at fred.stlouisfed.org)."
    )

    def __init__(self):
        key = os.environ.get("FRED_API_KEY", "")
        self.api_key = key if key and key != "your_fred_api_key_here" else ""

    def run(self, query: str) -> ToolResult:
        if not self.api_key:
            return ToolResult(
                content=(
                    "FRED API key not set. Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html "
                    "and set FRED_API_KEY in your .env file. "
                    "I can attempt to answer economic data questions using Wikipedia instead."
                ),
                sources=[],
                success=False,
                error="FRED_API_KEY not configured",
            )

        series_id = self._resolve_series(query)
        if not series_id:
            return ToolResult(
                content=f"No FRED series found for: {query}",
                sources=[],
                success=False,
            )

        return self._fetch_series(series_id)

    def _resolve_series(self, query: str) -> str | None:
        query_lower = query.lower()
        for keyword, sid in KNOWN_SERIES.items():
            if keyword in query_lower:
                return sid

        try:
            resp = requests.get(
                f"{BASE_URL}/series/search",
                params={
                    "search_text": query,
                    "api_key": self.api_key,
                    "file_type": "json",
                    "limit": 5,
                    "order_by": "popularity",
                    "sort_order": "desc",
                },
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            seriess = resp.json().get("seriess", [])
            return seriess[0]["id"] if seriess else None
        except Exception:
            return None

    @with_retry(max_attempts=3)
    def _fetch_series(self, series_id: str) -> ToolResult:
        try:
            info_resp = requests.get(
                f"{BASE_URL}/series",
                params={"series_id": series_id, "api_key": self.api_key, "file_type": "json"},
                timeout=TIMEOUT,
            )
            series_info = info_resp.json().get("seriess", [{}])[0] if info_resp.status_code == 200 else {}
            series_name = series_info.get("title", series_id)
            units = series_info.get("units_short", "")
            frequency = series_info.get("frequency_short", "")

            obs_resp = requests.get(
                f"{BASE_URL}/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": self.api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 24,
                },
                timeout=TIMEOUT,
            )
            obs_resp.raise_for_status()
            observations = [o for o in obs_resp.json().get("observations", []) if o["value"] != "."][:13]

            if not observations:
                return ToolResult(
                    content=f"No data available for series {series_id}.",
                    sources=[],
                    success=False,
                )

            latest = observations[0]
            year_ago = observations[11] if len(observations) >= 12 else None

            rows = ["| Date | Value |", "|------|-------|"]
            for obs in observations:
                rows.append(f"| {obs['date']} | {obs['value']} {units} |")

            change_note = ""
            if year_ago:
                try:
                    delta = float(latest["value"]) - float(year_ago["value"])
                    pct = (delta / float(year_ago["value"])) * 100
                    change_note = f"\n**Change over past year**: {delta:+.2f} {units} ({pct:+.1f}%)"
                except ValueError:
                    pass

            content = (
                f"## {series_name} ({series_id})\n\n"
                f"**Latest**: {latest['value']} {units} as of {latest['date']}  \n"
                f"**Frequency**: {frequency}{change_note}\n\n"
                f"### Recent Observations\n" + "\n".join(rows)
            )

            return ToolResult(
                content=content,
                sources=[{
                    "title": f"{series_name} ({series_id})",
                    "url": f"https://fred.stlouisfed.org/series/{series_id}",
                    "type": "fred",
                }],
                success=True,
            )

        except Exception as e:
            return ToolResult(
                content=f"FRED data retrieval failed: {e}",
                sources=[],
                success=False,
                error=str(e),
            )
