"""FRED tool — retrieves Federal Reserve economic data series."""

import logging
import os
import re
from datetime import date

import requests

from .base import BaseTool, ToolResult, with_retry

logger = logging.getLogger(__name__)

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
    "discount rate": "DPCREDIT",
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
        """Resolve ``query`` to a FRED series ID and return recent observations."""
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
        logger.warning(f"FRED query={query!r} -> series={series_id}")
        if not series_id:
            return ToolResult(
                content=f"No FRED series found for: {query}",
                sources=[],
                success=False,
            )

        year = self._extract_year(query)
        if year:
            return self._fetch_series(series_id, obs_start=f"{year}-01-01", obs_end=f"{year}-12-31")
        return self._fetch_series(series_id)

    @staticmethod
    def _extract_year(query: str) -> str | None:
        """Return a 4-digit year if the query references a specific historical year."""
        m = re.search(r"\b(19[5-9]\d|20[0-2]\d)\b", query)
        return m.group(1) if m else None

    def _resolve_series(self, query: str) -> str | None:
        """Map a natural-language query to a FRED series ID via keyword lookup then API search."""
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
    def _fetch_series(
        self, series_id: str, obs_start: str | None = None, obs_end: str | None = None
    ) -> ToolResult:
        """Fetch metadata and observations for ``series_id``, optionally within a date range."""
        info_resp = requests.get(
            f"{BASE_URL}/series",
            params={"series_id": series_id, "api_key": self.api_key, "file_type": "json"},
            timeout=TIMEOUT,
        )
        series_info = (
            info_resp.json().get("seriess", [{}])[0] if info_resp.status_code == 200 else {}
        )
        series_name = series_info.get("title", series_id)
        units = series_info.get("units_short", "")
        frequency = series_info.get("frequency_short", "")

        today = date.today().isoformat()
        obs_params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 24,
            "realtime_start": today,
            "realtime_end": today,
        }
        if obs_start:
            obs_params["observation_start"] = obs_start
        if obs_end:
            obs_params["observation_end"] = obs_end

        obs_resp = requests.get(
            f"{BASE_URL}/series/observations",
            params=obs_params,
            timeout=TIMEOUT,
        )
        obs_resp.raise_for_status()
        observations = [o for o in obs_resp.json().get("observations", []) if o["value"] != "."][
            :13
        ]
        logger.warning(
            f"FRED {series_id}: latest={observations[0]['date']}={observations[0]['value']} "
            f"(realtime={obs_params.get('realtime_start')})"
            if observations
            else f"FRED {series_id}: no observations"
        )

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
                change_note = f"\nChange over period: {delta:+.2f} {units} ({pct:+.1f}%)"
            except ValueError:
                pass

        avg_note = ""
        vals = []
        for obs in observations:
            try:
                vals.append(float(obs["value"]))
            except ValueError:
                pass
        if len(vals) >= 2:
            avg = sum(vals) / len(vals)
            if obs_start and obs_end and len(vals) == 12 and frequency == "M":
                year = obs_start[:4]
                avg_note = f"\nOFFICIAL ANNUAL AVERAGE for {year}: {avg:.1f} {units} (computed from 12 monthly observations — use this value)"
            else:
                avg_note = f"\nAverage across {len(vals)} observations: {avg:.1f} {units}"

        if obs_start and obs_end and len(vals) == 12 and frequency == "M":
            headline = (
                f"ANSWER: {series_name} annual average for {obs_start[:4]} was {avg:.1f} {units}."
            )
            mandate = f"You MUST use {avg:.1f} {units} as the annual average in your Final Answer."
        else:
            headline = f"ANSWER: {series_name} is {latest['value']} {units} as of {latest['date']}."
            mandate = (
                f"You MUST use {latest['value']} {units} in your Final Answer — this is live data."
            )

        content = f"{headline}\nSource: FRED series {series_id}.{change_note}{avg_note}\n{mandate}"

        return ToolResult(
            content=content,
            sources=[
                {
                    "title": f"{series_name} ({series_id})",
                    "url": f"https://fred.stlouisfed.org/series/{series_id}",
                    "series_id": series_id,
                    "latest_value": f"{latest['value']} {units}",
                    "latest_date": latest["date"],
                    "type": "fred",
                }
            ],
            success=True,
        )
