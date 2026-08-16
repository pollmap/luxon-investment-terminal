from __future__ import annotations

import json
import os

import httpx

from packages.connectors.base import ConnectorDocument, ConnectorRequest, MarketConnector


class EcosConnector(MarketConnector):
    source = "ecos"
    market = "KR"

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        base_url: str = "https://ecos.bok.or.kr/api",
    ) -> None:
        self.api_key = api_key or os.getenv("ECOS_API_KEY")
        self.client = client or httpx.Client(timeout=30)
        self.base_url = base_url.rstrip("/")

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        if not self.api_key:
            raise RuntimeError("ECOS_API_KEY is required for ECOS collection")
        spec = _parse_ecos_spec(request.ticker)
        start, end = _ecos_period_range(spec["cycle"], request.start_year, request.end_year)
        path = (
            f"/StatisticSearch/{self.api_key}/json/kr/1/100000/"
            f"{spec['stat_code']}/{spec['cycle']}/{start}/{end}"
        )
        if spec["item_code"]:
            path = f"{path}/{spec['item_code']}"
        response = self.client.get(f"{self.base_url}{path}")
        response.raise_for_status()
        payload = response.json()
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        redacted_url = _redact_key(str(response.url), self.api_key)
        return [
            ConnectorDocument(
                source=self.source,
                market=self.market,
                ticker=request.ticker.upper(),
                identifier=(
                    f"{spec['stat_code']}-{spec['cycle']}-{spec['item_code'] or 'ALL'}-"
                    f"{start}-{end}"
                ),
                url=redacted_url,
                payload=raw,
                content_type="application/json",
                metadata={
                    "stat_code": spec["stat_code"],
                    "cycle": spec["cycle"],
                    "item_code": spec["item_code"],
                    "period_start": start,
                    "period_end": end,
                    "endpoint": "/StatisticSearch",
                    "source_type": "ecos_official_api",
                    "url": redacted_url,
                },
            )
        ]


def _parse_ecos_spec(value: str) -> dict[str, str | None]:
    parts = [part.strip() for part in value.replace("|", ":").split(":") if part.strip()]
    if not parts:
        raise ValueError("ECOS series spec is required")
    return {
        "stat_code": parts[0],
        "cycle": (parts[1] if len(parts) > 1 else "A").upper(),
        "item_code": parts[2] if len(parts) > 2 else None,
    }


def _ecos_period_range(cycle: str, start_year: int | None, end_year: int | None) -> tuple[str, str]:
    start = start_year or end_year
    end = end_year or start_year
    if start is None or end is None:
        raise ValueError("start_year or end_year is required")
    if cycle == "M":
        return f"{start}01", f"{end}12"
    if cycle == "Q":
        return f"{start}Q1", f"{end}Q4"
    if cycle == "D":
        return f"{start}0101", f"{end}1231"
    return str(start), str(end)


def _redact_key(url: str, api_key: str) -> str:
    return url.replace(api_key, "REDACTED")
