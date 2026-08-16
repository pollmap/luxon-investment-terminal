from __future__ import annotations

import importlib
from datetime import date
from typing import Any

from packages.connectors.base import ConnectorDocument, ConnectorRequest, MarketConnector

FDR_SOURCE_URL = "https://github.com/financedata/financedatareader"


class FinanceDataReaderConnector(MarketConnector):
    source = "finance_data_reader"
    market = "GLOBAL"

    def __init__(self, fdr_module: Any | None = None) -> None:
        self.fdr = fdr_module

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        fdr = self.fdr or _load_fdr_module()
        symbol = _fdr_symbol(request.ticker, request.market)
        start = f"{request.start_year}-01-01" if request.start_year else "1995-01-01"
        end = f"{request.end_year}-12-31" if request.end_year else date.today().isoformat()
        frame = fdr.DataReader(symbol, start, end)
        if frame is None or frame.empty:
            raise ValueError(f"FinanceDataReader returned no price rows for {symbol} {start}:{end}")
        payload = frame.to_csv(index_label="date").encode("utf-8-sig")
        return [
            ConnectorDocument(
                source=self.source,
                market=request.market.upper(),
                ticker=_display_ticker(request.ticker, request.market),
                identifier=(
                    f"{symbol}-{request.start_year or 'start'}-"
                    f"{request.end_year or 'end'}-daily"
                ),
                url=FDR_SOURCE_URL,
                payload=payload,
                content_type="text/csv",
                metadata={
                    "fdr_symbol": symbol,
                    "interval": "daily",
                    "start_year": request.start_year,
                    "end_year": request.end_year,
                    "downloaded_date": date.today().isoformat(),
                    "source_note": "FinanceDataReader wrapper-derived daily market data",
                },
            )
        ]


def _load_fdr_module() -> Any:
    try:
        return importlib.import_module("FinanceDataReader")
    except ImportError as exc:
        raise RuntimeError(
            "FinanceDataReader is required for FDR price collection. Install the "
            "ingestion extra or run in an environment that includes finance-datareader."
        ) from exc


def _fdr_symbol(ticker: str, market: str) -> str:
    value = ticker.strip().upper()
    if market.upper() == "KR" and "." in value:
        return value.split(".", 1)[0]
    return value


def _display_ticker(ticker: str, market: str) -> str:
    value = ticker.strip().upper()
    if market.upper() == "KR" and "." not in value and value.isdigit() and len(value) == 6:
        return f"{value}.KS"
    return value
