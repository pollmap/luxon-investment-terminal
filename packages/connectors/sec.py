from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import httpx

from backend.normalize.edgar.client import EdgarClient
from backend.normalize.edgar.collector import EdgarCollector
from packages.connectors.base import ConnectorDocument, ConnectorRequest, MarketConnector

SEC_BULK_ARCHIVES = {
    "companyfacts": "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip",
    "submissions": "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip",
}


class SecEdgarConnector(MarketConnector):
    source = "sec_edgar"
    market = "US"

    def __init__(self, client: EdgarClient | None = None) -> None:
        self.client = client

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        collector = EdgarCollector(self.client or EdgarClient())
        documents = collector.collect_earnings_exhibits(
            request.ticker,
            request.start_year,
            request.end_year,
            force_refresh=request.force_refresh,
        )
        connector_docs: list[ConnectorDocument] = []
        for document in documents:
            payload = _payload_for(document.local_path, document.content)
            connector_docs.append(
                ConnectorDocument(
                    source=self.source,
                    market=self.market,
                    ticker=request.ticker.upper(),
                    identifier=document.accession_number or document.id,
                    url=document.source_url,
                    payload=payload,
                    content_type="text/html",
                    metadata={
                        "accession_number": document.accession_number,
                        "form_type": document.form_type,
                        "filing_url": document.filing_url,
                        "source_url": document.source_url,
                        "local_path": document.local_path,
                        "content_hash": document.content_hash,
                    },
                )
            )
        return connector_docs


class SecBulkConnector(MarketConnector):
    source = "sec_bulk"
    market = "US"

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        client: httpx.Client | None = None,
        archive_urls: dict[str, str] | None = None,
    ) -> None:
        self.user_agent = user_agent or os.getenv("SEC_USER_AGENT")
        if not self.user_agent:
            raise RuntimeError(
                "SEC_USER_AGENT is required for SEC bulk archive downloads. "
                "Example: PersonalFastGraphs/0.1 contact@example.com"
            )
        self.client = client or httpx.Client(timeout=120)
        self.archive_urls = archive_urls or SEC_BULK_ARCHIVES

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        archives = ["companyfacts"] if request.ticker.upper() != "SUBMISSIONS" else ["submissions"]
        return self.collect_bulk(archives, force_refresh=request.force_refresh)

    def collect_bulk(
        self,
        archives: list[str] | tuple[str, ...] | set[str] | None = None,
        *,
        force_refresh: bool = False,
    ) -> list[ConnectorDocument]:
        requested = [_normalize_archive_name(item) for item in (archives or self.archive_urls)]
        documents: list[ConnectorDocument] = []
        for archive in requested:
            url = self.archive_urls.get(archive)
            if url is None:
                raise ValueError(f"Unsupported SEC bulk archive: {archive}")
            response = self.client.get(url, headers={"User-Agent": self.user_agent})
            response.raise_for_status()
            payload = response.content
            documents.append(
                ConnectorDocument(
                    source=self.source,
                    market=self.market,
                    ticker="BULK",
                    identifier=f"sec-bulk-{archive}",
                    url=str(response.url),
                    payload=payload,
                    content_type="application/zip",
                    metadata={
                        "archive": archive,
                        "endpoint": url,
                        "source_type": "sec_edgar_bulk_archive",
                        "official_api_doc": (
                            "https://www.sec.gov/search-filings/"
                            "edgar-application-programming-interfaces"
                        ),
                        "content_length": len(payload),
                        "force_refresh": force_refresh,
                        "collected_at": datetime.now(UTC).isoformat(),
                    },
                )
            )
        return documents


def _payload_for(local_path: str | None, content: str | None) -> bytes:
    if local_path and Path(local_path).exists():
        return Path(local_path).read_bytes()
    return (content or "").encode("utf-8")


def _normalize_archive_name(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"companyfacts", "company-facts", "facts"}:
        return "companyfacts"
    if normalized in {"submission", "submissions", "filing-history", "filings"}:
        return "submissions"
    return normalized
