from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

from backend.normalize.edgar.client import EdgarClient
from backend.normalize.edgar.exhibit_finder import FilingDocument, looks_like_earnings_exhibit
from backend.normalize.schemas import SourceDocument

SEC_DATA = "https://data.sec.gov"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
FILING_INDEX_ROW_RE = (
    r"<tr>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>.*?"
    r"<a href=\"([^\"]+)\">(.*?)</a>.*?</td>\s*"
    r"<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>"
)


class EdgarCollector:
    def __init__(self, client: EdgarClient) -> None:
        self.client = client

    def ticker_to_cik(self, ticker: str) -> str:
        data = self.client.get_json("https://www.sec.gov/files/company_tickers.json")
        ticker_upper = ticker.upper()
        for item in data.values():
            if item["ticker"].upper() == ticker_upper:
                return str(item["cik_str"]).zfill(10)
        raise LookupError(f"ticker not found in SEC company_tickers.json: {ticker}")

    def submissions(self, ticker: str) -> dict:
        cik = self.ticker_to_cik(ticker)
        return self.client.get_json(f"{SEC_DATA}/submissions/CIK{cik}.json")

    def collect_earnings_exhibits(
        self,
        ticker: str,
        start_year: int | None = None,
        end_year: int | None = None,
        force_refresh: bool = False,
    ) -> list[SourceDocument]:
        data = self.submissions(ticker)
        cik_plain = str(data["cik"]).lstrip("0")
        recent = data.get("filings", {}).get("recent", {})
        documents: list[SourceDocument] = []
        for idx, form in enumerate(recent.get("form", [])):
            if form != "8-K":
                continue
            filing_date = recent["filingDate"][idx]
            year = int(filing_date[:4])
            if start_year and year < start_year:
                continue
            if end_year and year > end_year:
                continue
            items = str(recent.get("items", [""] * len(recent["form"]))[idx])
            if "2.02" not in items:
                continue
            accession = recent["accessionNumber"][idx]
            accession_nodash = accession.replace("-", "")
            index_url = f"{SEC_ARCHIVES}/{cik_plain}/{accession_nodash}/{accession}-index.html"
            index_html = self.client.get_text(index_url, force_refresh=force_refresh)
            for filing_doc in self._documents_from_index(cik_plain, accession, index_html):
                if not looks_like_earnings_exhibit(filing_doc):
                    continue
                content = self.client.get_text(filing_doc.url, force_refresh=force_refresh)
                digest = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
                local_path = self.client.raw_dir / ticker.upper() / accession
                local_path.mkdir(parents=True, exist_ok=True)
                file_path = local_path / _safe_filename(filing_doc.document)
                file_path.write_text(content, encoding="utf-8", errors="ignore")
                documents.append(
                    SourceDocument(
                        id=digest,
                        ticker=ticker.upper(),
                        accession_number=accession,
                        form_type=form,
                        filing_url=index_url,
                        source_url=filing_doc.url,
                        description=filing_doc.description,
                        document_type=filing_doc.document_type,
                        content=content,
                        local_path=str(file_path),
                        content_hash=digest,
                    )
                )
        return documents

    def _documents_from_index(
        self,
        cik_plain: str,
        accession: str,
        html: str,
    ) -> list[FilingDocument]:
        rows = re.findall(
            FILING_INDEX_ROW_RE,
            html,
            re.IGNORECASE | re.DOTALL,
        )
        docs: list[FilingDocument] = []
        for sequence, href, document, doc_type, description in rows:
            url = href if href.startswith("http") else f"https://www.sec.gov{href}"
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.netloc not in {"www.sec.gov", "sec.gov"}:
                continue
            docs.append(
                FilingDocument(
                    sequence=_clean(sequence),
                    document=_clean(document),
                    document_type=_clean(doc_type),
                    description=_clean(description),
                    url=url,
                )
            )
        return docs


def _clean(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()


def _safe_filename(value: str) -> str:
    clean = Path(value).name
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", clean).strip("._")
    return clean or "document.html"
