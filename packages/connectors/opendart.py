from __future__ import annotations

import json
import os
from typing import Any

import httpx

from packages.connectors.base import ConnectorDocument, ConnectorRequest, MarketConnector

DEFAULT_CORP_CODES = {
    "005930.KS": "00126380",
    "000660.KS": "00164779",
    "402340.KS": "01596425",
    "005380.KS": "00164742",
    "028260.KS": "00149655",
    "032830.KS": "00126256",
    "373220.KS": "01515323",
    "207940.KS": "00877059",
    "329180.KS": "01390344",
    "009155.KS": "00126371",
    "009150.KS": "00126371",
    "000270.KS": "00106641",
    "068270.KS": "00413046",
    "105560.KS": "00688996",
    "035420.KS": "00266961",
    "005490.KS": "00155319",
}


class OpenDartConnector(MarketConnector):
    source = "opendart"
    market = "KR"

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        base_url: str = "https://opendart.fss.or.kr/api",
    ) -> None:
        self.api_key = api_key or os.getenv("OPENDART_API_KEY") or os.getenv("DART_API_KEY")
        self.client = client or httpx.Client(timeout=30)
        self.base_url = base_url.rstrip("/")

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        if not self.api_key:
            raise RuntimeError(
                "OPENDART_API_KEY or DART_API_KEY is required for OpenDART collection"
            )
        corp_code = DEFAULT_CORP_CODES.get(request.ticker.upper())
        if not corp_code:
            raise LookupError(f"OpenDART corp_code is not configured for {request.ticker}")
        start_year, end_year = _year_range(request)
        documents: list[ConnectorDocument] = []
        for year in range(start_year, end_year + 1):
            params = {
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": "11011",
                "fs_div": "CFS",
            }
            response = self.client.get(f"{self.base_url}/fnlttSinglAcntAll.json", params=params)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            documents.append(
                ConnectorDocument(
                    source=self.source,
                    market=self.market,
                    ticker=request.ticker.upper(),
                    identifier=f"{corp_code}-{year}-11011-CFS",
                    url=_redact_query_key(str(response.url), "crtfc_key"),
                    payload=raw,
                    content_type="application/json",
                    metadata={
                        "corp_code": corp_code,
                        "bsns_year": year,
                        "reprt_code": "11011",
                        "fs_div": "CFS",
                        "status": payload.get("status"),
                        "message": payload.get("message"),
                    },
                )
            )
        return documents

    def collect_dividends(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        if not self.api_key:
            raise RuntimeError(
                "OPENDART_API_KEY or DART_API_KEY is required for OpenDART collection"
            )
        corp_code = DEFAULT_CORP_CODES.get(request.ticker.upper())
        if not corp_code:
            raise LookupError(f"OpenDART corp_code is not configured for {request.ticker}")
        start_year, end_year = _year_range(request)
        documents: list[ConnectorDocument] = []
        for year in range(start_year, end_year + 1):
            params = {
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": "11011",
            }
            response = self.client.get(f"{self.base_url}/alotMatter.json", params=params)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            documents.append(
                ConnectorDocument(
                    source="opendart_dividends",
                    market=self.market,
                    ticker=request.ticker.upper(),
                    identifier=f"{corp_code}-{year}-alotMatter",
                    url=_redact_query_key(str(response.url), "crtfc_key"),
                    payload=raw,
                    content_type="application/json",
                    metadata={
                        "corp_code": corp_code,
                        "bsns_year": year,
                        "reprt_code": "11011",
                        "endpoint": "alotMatter",
                        "status": payload.get("status"),
                        "message": payload.get("message"),
                    },
                )
            )
        return documents


def _year_range(request: ConnectorRequest) -> tuple[int, int]:
    start = request.start_year or request.end_year
    end = request.end_year or request.start_year
    if start is None or end is None:
        raise ValueError("start_year or end_year is required")
    return start, end


def _redact_query_key(url: str, key: str) -> str:
    marker = f"{key}="
    if marker not in url:
        return url
    prefix, rest = url.split(marker, 1)
    suffix = ""
    if "&" in rest:
        _, suffix = rest.split("&", 1)
        suffix = f"&{suffix}"
    return f"{prefix}{marker}REDACTED{suffix}"
