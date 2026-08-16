from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from packages.connectors.base import ConnectorDocument, ConnectorRequest, MarketConnector

MARCAP_REPO_URL = "https://github.com/financedata/marcap"
MARCAP_RAW_BASE_URL = "https://raw.githubusercontent.com/FinanceData/marcap/master/data"


class MarcapConnector(MarketConnector):
    source = "marcap"
    market = "KR"

    def __init__(
        self,
        client: httpx.Client | None = None,
        base_url: str = MARCAP_RAW_BASE_URL,
    ) -> None:
        self.client = client or httpx.Client(timeout=60.0, follow_redirects=True)
        self.base_url = base_url.rstrip("/")

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        start_year = request.start_year or 1995
        end_year = request.end_year or date.today().year
        if start_year > end_year:
            raise ValueError("start_year must be <= end_year")

        documents: list[ConnectorDocument] = []
        for year in range(start_year, end_year + 1):
            url = f"{self.base_url}/marcap-{year}.parquet"
            response = self.client.get(url)
            response.raise_for_status()
            payload = response.content
            if not payload.startswith(b"PAR1"):
                raise ValueError(f"marcap-{year} did not return a parquet payload")
            documents.append(
                ConnectorDocument(
                    source=self.source,
                    market="KR",
                    ticker=_display_ticker(request.ticker),
                    identifier=f"marcap-{year}",
                    url=url,
                    payload=payload,
                    content_type="application/vnd.apache.parquet",
                    metadata={
                        "year": year,
                        "interval": "annual_archive",
                        "format": "parquet",
                        "downloaded_date": date.today().isoformat(),
                        "source_repo": MARCAP_REPO_URL,
                        "source_note": (
                            "FinanceData marcap open dataset of KRX daily market cap "
                            "ranking parquet archives."
                        ),
                    },
                )
            )
        return documents


def _display_ticker(ticker: str) -> str:
    value = ticker.strip().upper()
    return value or "KR_MARKET"
