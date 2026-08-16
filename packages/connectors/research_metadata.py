from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from packages.connectors.base import ConnectorDocument, ConnectorRequest, MarketConnector

NAVER_WEBKR_ENDPOINT = "https://openapi.naver.com/v1/search/webkr.json"
HANKYUNG_CONSENSUS_URL = "https://consensus.hankyung.com/analysis/list"


class NaverResearchSearchConnector(MarketConnector):
    source = "naver_search_research"
    market = "KR"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        client: httpx.Client | None = None,
        endpoint: str = NAVER_WEBKR_ENDPOINT,
        display: int = 10,
    ) -> None:
        self.client_id = client_id or os.getenv("NAVER_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("NAVER_CLIENT_SECRET")
        self.client = client or httpx.Client(timeout=20)
        self.endpoint = endpoint
        self.display = display

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "NAVER_CLIENT_ID and NAVER_CLIENT_SECRET are required for "
                "Naver research metadata"
            )
        ticker = _kr_ticker_code(request.ticker)
        query = f"{ticker} 증권사 리포트 기업분석 컨센서스"
        response = self.client.get(
            self.endpoint,
            headers={
                "X-Naver-Client-Id": self.client_id,
                "X-Naver-Client-Secret": self.client_secret,
            },
            params={
                "query": query,
                "display": self.display,
                "sort": "date",
            },
        )
        response.raise_for_status()
        payload = response.json()
        document = {
            "query": query,
            "endpoint": self.endpoint,
            "ticker": _display_kr_ticker(request.ticker),
            "market": self.market,
            "collection_scope": "metadata_only",
            "financial_numbers_allowed": False,
            "items": _naver_items(payload),
            "raw": payload,
        }
        raw = json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return [
            ConnectorDocument(
                source=self.source,
                market=self.market,
                ticker=_display_kr_ticker(request.ticker),
                identifier=_research_identifier(
                    self.source,
                    ticker,
                    request.start_year,
                    request.end_year,
                ),
                url=str(response.url),
                payload=raw,
                content_type="application/json",
                metadata={
                    "source_type": "naver_openapi_web_search",
                    "form_type": "RESEARCH_LINK_METADATA",
                    "ticker_code": ticker,
                    "query": query,
                    "item_count": len(document["items"]),
                    "collection_scope": "metadata_only",
                    "financial_numbers_allowed": False,
                    "source_note": (
                        "Naver OpenAPI search result metadata only; not promoted "
                        "to financial facts or consensus values."
                    ),
                    "terms_note": "Review Naver OpenAPI terms before public redistribution.",
                    "downloaded_date": date.today().isoformat(),
                },
            )
        ]


class HankyungConsensusMetadataConnector(MarketConnector):
    source = "hankyung_consensus_metadata"
    market = "KR"

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        base_url: str = HANKYUNG_CONSENSUS_URL,
        max_items: int = 25,
    ) -> None:
        self.client = client or httpx.Client(timeout=20)
        self.base_url = base_url
        self.max_items = max_items

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        ticker = _kr_ticker_code(request.ticker)
        response = self.client.get(
            self.base_url,
            params={
                "search_text": ticker,
                "sdate": f"{request.start_year}-01-01" if request.start_year else "",
                "edate": f"{request.end_year}-12-31" if request.end_year else "",
            },
        )
        response.raise_for_status()
        html = response.text
        items = _hankyung_items(html, str(response.url), self.max_items)
        document = {
            "source_html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "ticker": _display_kr_ticker(request.ticker),
            "market": self.market,
            "collection_scope": "metadata_only",
            "financial_numbers_allowed": False,
            "items": items,
        }
        raw = json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return [
            ConnectorDocument(
                source=self.source,
                market=self.market,
                ticker=_display_kr_ticker(request.ticker),
                identifier=_research_identifier(
                    self.source,
                    ticker,
                    request.start_year,
                    request.end_year,
                ),
                url=str(response.url),
                payload=raw,
                content_type="application/json",
                metadata={
                    "source_type": "hankyung_consensus_public_metadata",
                    "form_type": "RESEARCH_LINK_METADATA",
                    "ticker_code": ticker,
                    "item_count": len(items),
                    "collection_scope": "metadata_only",
                    "financial_numbers_allowed": False,
                    "source_note": (
                        "Public research-list metadata only; report bodies, PDFs, "
                        "target prices, and estimates are not extracted."
                    ),
                    "terms_note": "Review Hankyung terms before product/public redistribution.",
                    "downloaded_date": date.today().isoformat(),
                },
            )
        ]


def _naver_items(payload: dict[str, Any]) -> list[dict[str, str]]:
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "title": _clean_html_text(str(item.get("title") or "")),
                "link": str(item.get("link") or ""),
                "description": _clean_html_text(str(item.get("description") or "")),
            }
        )
    return normalized


def _hankyung_items(html: str, base_url: str, max_items: int) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, str]] = []
    for anchor in soup.select("a[href]"):
        title = " ".join(anchor.get_text(" ", strip=True).split())
        href = str(anchor.get("href") or "").strip()
        if not title or not href:
            continue
        if _looks_like_research_link(title, href):
            items.append({"title": title, "link": urljoin(base_url, href)})
        if len(items) >= max_items:
            break
    return items


def _looks_like_research_link(title: str, href: str) -> bool:
    text = f"{title} {href}".lower()
    keywords = ("analysis", "report", "consensus", "리포트", "기업", "산업")
    return any(keyword in text for keyword in keywords)


def _clean_html_text(value: str) -> str:
    return BeautifulSoup(value, "lxml").get_text(" ", strip=True)


def _kr_ticker_code(ticker: str) -> str:
    value = ticker.strip().upper()
    if value.startswith("A") and value[1:].isdigit():
        value = value[1:]
    if "." in value:
        value = value.split(".", 1)[0]
    if not (value.isdigit() and len(value) == 6):
        raise ValueError(f"KR ticker code must be a six-digit code or .KS/.KQ ticker: {ticker}")
    return value


def _display_kr_ticker(ticker: str) -> str:
    value = ticker.strip().upper()
    if value.startswith("A") and value[1:].isdigit():
        value = value[1:]
    if "." in value:
        return value
    return f"{value}.KS"


def _research_identifier(
    source: str,
    ticker: str,
    start_year: int | None,
    end_year: int | None,
) -> str:
    return f"{source}-{ticker}-{start_year or 'start'}-{end_year or 'end'}"
