from __future__ import annotations

import json
import os
from datetime import date

import httpx

from packages.connectors.base import ConnectorDocument, ConnectorRequest, MarketConnector

DEFAULT_EDINET_CODES = {
    "7203.T": "E02144",
    "6758.T": "E01777",
    "6861.T": "E01967",
    "8306.T": "E03606",
    "7974.T": "E02367",
}

EDINET_DOWNLOAD_TYPES = {
    "xbrl": {"type": "1", "content_type": "application/zip", "label": "xbrl_zip"},
    "csv": {"type": "5", "content_type": "application/zip", "label": "xbrl_to_csv_zip"},
}

DEFAULT_DOC_TYPE_CODES = {"120"}


class EdinetConnector(MarketConnector):
    source = "edinet"
    market = "JP"

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        base_url: str = "https://api.edinet-fsa.go.jp/api/v2",
    ) -> None:
        self.api_key = api_key or os.getenv("EDINET_API_KEY")
        self.client = client or httpx.Client(timeout=30)
        self.base_url = base_url.rstrip("/")

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        return self.collect_metadata(request)

    def collect_metadata(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        edinet_code = DEFAULT_EDINET_CODES.get(request.ticker.upper())
        if not edinet_code:
            raise LookupError(f"EDINET code is not configured for {request.ticker}")
        start_year, end_year = _year_range(request)
        documents: list[ConnectorDocument] = []
        for year in range(start_year, end_year + 1):
            for query_date in _annual_window(year):
                payload, url = self._document_list(query_date)
                matched = [
                    item
                    for item in payload.get("results", [])
                    if str(item.get("edinetCode", "")).upper() == edinet_code
                ]
                if not matched:
                    continue
                raw = json.dumps(
                    {"query_date": query_date.isoformat(), "results": matched},
                    ensure_ascii=False,
                ).encode("utf-8")
                documents.append(
                    ConnectorDocument(
                        source=self.source,
                        market=self.market,
                        ticker=request.ticker.upper(),
                        identifier=f"{edinet_code}-{query_date.isoformat()}",
                        url=url,
                        payload=raw,
                        content_type="application/json",
                        metadata={
                            "edinet_code": edinet_code,
                            "query_date": query_date.isoformat(),
                            "endpoint": "/documents.json",
                            "document_type": "metadata_list",
                            "row_count": len(matched),
                        },
                    )
                )
        return documents

    def collect_bundle(
        self,
        request: ConnectorRequest,
        download_types: list[str] | tuple[str, ...] | set[str] | None = None,
        doc_type_codes: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> list[ConnectorDocument]:
        edinet_code = DEFAULT_EDINET_CODES.get(request.ticker.upper())
        if not edinet_code:
            raise LookupError(f"EDINET code is not configured for {request.ticker}")
        requested_downloads = {item.lower() for item in (download_types or ("metadata", "csv"))}
        allowed_doc_types = {str(item) for item in (doc_type_codes or DEFAULT_DOC_TYPE_CODES)}
        start_year, end_year = _year_range(request)
        documents: list[ConnectorDocument] = []
        seen_doc_ids: set[str] = set()
        for year in range(start_year, end_year + 1):
            for query_date in _annual_window(year):
                payload, url = self._document_list(query_date)
                matched = [
                    item
                    for item in payload.get("results", [])
                    if str(item.get("edinetCode", "")).upper() == edinet_code
                    and str(item.get("docTypeCode", "")) in allowed_doc_types
                ]
                if matched and "metadata" in requested_downloads:
                    raw = json.dumps(
                        {"query_date": query_date.isoformat(), "results": matched},
                        ensure_ascii=False,
                    ).encode("utf-8")
                    documents.append(
                        ConnectorDocument(
                            source=self.source,
                            market=self.market,
                            ticker=request.ticker.upper(),
                            identifier=f"{edinet_code}-{query_date.isoformat()}",
                            url=url,
                            payload=raw,
                            content_type="application/json",
                            metadata={
                                "edinet_code": edinet_code,
                                "query_date": query_date.isoformat(),
                                "endpoint": "/documents.json",
                                "document_type": "metadata_list",
                                "row_count": len(matched),
                            },
                        )
                    )
                for row in matched:
                    doc_id = str(row.get("docID") or "").strip()
                    if not doc_id or doc_id in seen_doc_ids:
                        continue
                    seen_doc_ids.add(doc_id)
                    for download_type in sorted(requested_downloads - {"metadata"}):
                        config = EDINET_DOWNLOAD_TYPES.get(download_type)
                        if config is None:
                            raise ValueError(f"Unsupported EDINET download type: {download_type}")
                        csv_missing = str(row.get("csvFlag")) not in {"1", "true", "True"}
                        if download_type == "csv" and csv_missing:
                            continue
                        xbrl_missing = str(row.get("xbrlFlag")) not in {"1", "true", "True"}
                        if download_type == "xbrl" and xbrl_missing:
                            continue
                        documents.append(
                            self._download_document(
                                request.ticker.upper(),
                                doc_id,
                                download_type,
                                row,
                                query_date,
                            )
                        )
        return documents

    def _document_list(self, query_date: date) -> tuple[dict, str]:
        params = {"date": query_date.isoformat(), "type": "2"}
        if self.api_key:
            params["Subscription-Key"] = self.api_key
        response = self.client.get(f"{self.base_url}/documents.json", params=params)
        response.raise_for_status()
        return response.json(), _redact_query_key(str(response.url), "Subscription-Key")

    def _download_document(
        self,
        ticker: str,
        doc_id: str,
        download_type: str,
        row: dict,
        query_date: date,
    ) -> ConnectorDocument:
        config = EDINET_DOWNLOAD_TYPES[download_type]
        params = {"type": config["type"]}
        if self.api_key:
            params["Subscription-Key"] = self.api_key
        response = self.client.get(f"{self.base_url}/documents/{doc_id}", params=params)
        response.raise_for_status()
        return ConnectorDocument(
            source=self.source,
            market=self.market,
            ticker=ticker,
            identifier=f"{doc_id}-{config['label']}",
            url=_redact_query_key(str(response.url), "Subscription-Key"),
            payload=response.content,
            content_type=response.headers.get("content-type") or str(config["content_type"]),
            metadata={
                "doc_id": doc_id,
                "edinet_code": row.get("edinetCode"),
                "sec_code": row.get("secCode"),
                "filer_name": row.get("filerName"),
                "doc_type_code": row.get("docTypeCode"),
                "doc_description": row.get("docDescription"),
                "period_start": row.get("periodStart"),
                "period_end": row.get("periodEnd"),
                "submit_date_time": row.get("submitDateTime"),
                "query_date": query_date.isoformat(),
                "endpoint": "/documents/{docID}",
                "document_type": config["label"],
                "download_type": download_type,
            },
        )


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


def _annual_window(year: int) -> list[date]:
    return [date(year, 6, 28), date(year, 6, 29), date(year, 6, 30), date(year, 7, 1)]
