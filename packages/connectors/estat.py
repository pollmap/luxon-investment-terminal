from __future__ import annotations

import json
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from packages.connectors.base import ConnectorDocument, ConnectorRequest, MarketConnector


class EStatConnector(MarketConnector):
    source = "estat"
    market = "JP"

    def __init__(
        self,
        app_id: str | None = None,
        client: httpx.Client | None = None,
        base_url: str = "https://api.e-stat.go.jp/rest/3.0/app/json",
    ) -> None:
        self.app_id = app_id or os.getenv("ESTAT_APP_ID")
        self.client = client or httpx.Client(timeout=30)
        self.base_url = base_url.rstrip("/")

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        if not self.app_id:
            raise RuntimeError("ESTAT_APP_ID is required for e-Stat collection")
        stats_data_id = request.ticker.strip()
        if not stats_data_id:
            raise ValueError("e-Stat statsDataId is required")
        params = {
            "appId": self.app_id,
            "statsDataId": stats_data_id,
            "lang": "E",
            "metaGetFlg": "Y",
            "cntGetFlg": "N",
            "explanationGetFlg": "Y",
        }
        response = self.client.get(f"{self.base_url}/getStatsData", params=params)
        response.raise_for_status()
        payload = response.json()
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        redacted_url = _redact_query_key(str(response.url), "appId")
        return [
            ConnectorDocument(
                source=self.source,
                market=self.market,
                ticker=stats_data_id,
                identifier=(
                    f"{stats_data_id}-{request.start_year or 'start'}-"
                    f"{request.end_year or 'end'}"
                ),
                url=redacted_url,
                payload=raw,
                content_type="application/json",
                metadata={
                    "stats_data_id": stats_data_id,
                    "requested_start_year": request.start_year,
                    "requested_end_year": request.end_year,
                    "endpoint": "/getStatsData",
                    "source_type": "estat_official_api",
                    "url": redacted_url,
                },
            )
        ]


def _redact_query_key(url: str, key_name: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, "REDACTED" if key.lower() == key_name.lower() else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
