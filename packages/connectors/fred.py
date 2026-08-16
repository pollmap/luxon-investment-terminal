from __future__ import annotations

import json
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from packages.connectors.base import ConnectorDocument, ConnectorRequest, MarketConnector


class FredConnector(MarketConnector):
    source = "fred"
    market = "GLOBAL"

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        base_url: str = "https://api.stlouisfed.org/fred",
    ) -> None:
        self.api_key = api_key or os.getenv("FRED_API_KEY")
        self.client = client or httpx.Client(timeout=30)
        self.base_url = base_url.rstrip("/")

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        if not self.api_key:
            raise RuntimeError("FRED_API_KEY is required for FRED collection")
        series_id = request.ticker.upper()
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
        }
        if request.start_year:
            params["observation_start"] = f"{request.start_year}-01-01"
        if request.end_year:
            params["observation_end"] = f"{request.end_year}-12-31"
        observations_response = self.client.get(f"{self.base_url}/series/observations", params=params)
        observations_response.raise_for_status()
        series_response = self.client.get(
            f"{self.base_url}/series",
            params={
                "series_id": series_id,
                "api_key": self.api_key,
                "file_type": "json",
            },
        )
        series_response.raise_for_status()
        payload = {
            "series_id": series_id,
            "series": series_response.json().get("seriess", []),
            "observations": observations_response.json().get("observations", []),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return [
            ConnectorDocument(
                source=self.source,
                market=self.market,
                ticker=series_id,
                identifier=f"{series_id}-{request.start_year or 'start'}-{request.end_year or 'end'}-observations",
                url=_redact_api_key(str(observations_response.url)),
                payload=raw,
                content_type="application/json",
                metadata={
                    "series_id": series_id,
                    "endpoint": "/series/observations",
                    "observation_start": params.get("observation_start"),
                    "observation_end": params.get("observation_end"),
                    "series_url": _redact_api_key(str(series_response.url)),
                },
            )
        ]


def _redact_api_key(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, "REDACTED" if key.lower() == "api_key" else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
