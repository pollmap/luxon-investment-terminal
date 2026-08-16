from __future__ import annotations

import json
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from packages.connectors.base import ConnectorDocument, ConnectorRequest, MarketConnector


class KosisConnector(MarketConnector):
    source = "kosis"
    market = "KR"

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        base_url: str = "https://kosis.kr/openapi",
    ) -> None:
        self.api_key = api_key or os.getenv("KOSIS_API_KEY")
        self.client = client or httpx.Client(timeout=30)
        self.base_url = base_url.rstrip("/")

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        if not self.api_key:
            raise RuntimeError("KOSIS_API_KEY is required for KOSIS collection")
        spec = _parse_kosis_spec(request.ticker)
        params = {
            "method": "getList",
            "apiKey": self.api_key,
            "format": "json",
            "jsonVD": "Y",
            "prdSe": "Y",
            "startPrdDe": str(request.start_year or request.end_year),
            "endPrdDe": str(request.end_year or request.start_year),
        }
        if spec["org_id"] and spec["tbl_id"]:
            params["orgId"] = spec["org_id"]
            params["tblId"] = spec["tbl_id"]
        else:
            params["userStatsId"] = spec["user_stats_id"]
        response = self.client.get(f"{self.base_url}/statisticsData.do", params=params)
        response.raise_for_status()
        payload = response.json()
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        redacted_url = _redact_query_key(str(response.url), "apiKey")
        return [
            ConnectorDocument(
                source=self.source,
                market=self.market,
                ticker=request.ticker.upper(),
                identifier=(
                    f"{spec['org_id'] or 'USER'}-{spec['tbl_id'] or spec['user_stats_id']}-"
                    f"{params['startPrdDe']}-{params['endPrdDe']}"
                ),
                url=redacted_url,
                payload=raw,
                content_type="application/json",
                metadata={
                    "org_id": spec["org_id"],
                    "tbl_id": spec["tbl_id"],
                    "user_stats_id": spec["user_stats_id"],
                    "period_start": params["startPrdDe"],
                    "period_end": params["endPrdDe"],
                    "endpoint": "/statisticsData.do",
                    "source_type": "kosis_official_api",
                    "url": redacted_url,
                },
            )
        ]


def _parse_kosis_spec(value: str) -> dict[str, str | None]:
    parts = [part.strip() for part in value.replace("|", ":").split(":") if part.strip()]
    if len(parts) >= 2:
        return {"org_id": parts[0], "tbl_id": parts[1], "user_stats_id": None}
    if parts:
        return {"org_id": None, "tbl_id": None, "user_stats_id": parts[0]}
    raise ValueError("KOSIS table spec is required")


def _redact_query_key(url: str, key_name: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, "REDACTED" if key.lower() == key_name.lower() else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
