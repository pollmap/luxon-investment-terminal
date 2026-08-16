from __future__ import annotations

import importlib
from datetime import date
from typing import Any

from packages.connectors.base import ConnectorDocument, ConnectorRequest, MarketConnector

PYKRX_SOURCE_URL = "https://github.com/sharebook-kr/pykrx"


class PyKrxConnector(MarketConnector):
    source = "pykrx"
    market = "KR"

    def __init__(self, stock_module: Any | None = None) -> None:
        self.stock = stock_module

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        stock = self.stock or _load_stock_module()
        ticker = _krx_code(request.ticker)
        start = f"{request.start_year}0101" if request.start_year else "19950101"
        end = f"{request.end_year}1231" if request.end_year else date.today().strftime("%Y%m%d")
        frame = _get_market_ohlcv(stock, start, end, ticker)
        if frame is None or frame.empty:
            raise ValueError(f"pykrx returned no OHLCV rows for {ticker} {start}:{end}")
        payload = frame.to_csv(index_label="date").encode("utf-8-sig")
        return [
            ConnectorDocument(
                source=self.source,
                market="KR",
                ticker=f"{ticker}.KS",
                identifier=(
                    f"{ticker}-{request.start_year or 'start'}-"
                    f"{request.end_year or 'end'}-ohlcv"
                ),
                url=PYKRX_SOURCE_URL,
                payload=payload,
                content_type="text/csv",
                metadata={
                    "krx_code": ticker,
                    "endpoint": _selected_endpoint(stock),
                    "interval": "daily",
                    "start_year": request.start_year,
                    "end_year": request.end_year,
                    "downloaded_date": date.today().isoformat(),
                    "source_note": "pykrx wrapper around KRX/Naver public market data",
                },
            )
        ]

    def collect_fundamentals(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        stock = self.stock or _load_stock_module()
        ticker = _krx_code(request.ticker)
        start = f"{request.start_year}0101" if request.start_year else "19950101"
        end = f"{request.end_year}1231" if request.end_year else date.today().strftime("%Y%m%d")
        frame = _get_market_fundamental(stock, start, end, ticker)
        if frame is None or frame.empty:
            raise ValueError(f"pykrx returned no fundamental rows for {ticker} {start}:{end}")
        payload = frame.to_csv(index_label="date").encode("utf-8-sig")
        return [
            ConnectorDocument(
                source=self.source,
                market="KR",
                ticker=f"{ticker}.KS",
                identifier=(
                    f"{ticker}-{request.start_year or 'start'}-"
                    f"{request.end_year or 'end'}-fundamental"
                ),
                url=PYKRX_SOURCE_URL,
                payload=payload,
                content_type="text/csv",
                metadata={
                    "krx_code": ticker,
                    "endpoint": _selected_fundamental_endpoint(stock),
                    "interval": "daily",
                    "start_year": request.start_year,
                    "end_year": request.end_year,
                    "downloaded_date": date.today().isoformat(),
                    "source_note": "pykrx wrapper around KRX public fundamental data",
                },
            )
        ]


def _load_stock_module() -> Any:
    try:
        return importlib.import_module("pykrx.stock")
    except ImportError as exc:
        raise RuntimeError(
            "pykrx is required for KR price collection. Install the ingestion extra "
            "or run with an environment that includes pykrx."
        ) from exc


def _get_market_ohlcv(stock: Any, start: str, end: str, ticker: str) -> Any:
    if hasattr(stock, "get_market_ohlcv"):
        return stock.get_market_ohlcv(start, end, ticker)
    if hasattr(stock, "get_market_ohlcv_by_date"):
        return stock.get_market_ohlcv_by_date(start, end, ticker)
    raise AttributeError("pykrx.stock does not expose get_market_ohlcv")


def _get_market_fundamental(stock: Any, start: str, end: str, ticker: str) -> Any:
    if hasattr(stock, "get_market_fundamental_by_date"):
        return stock.get_market_fundamental_by_date(start, end, ticker)
    if hasattr(stock, "get_market_fundamental"):
        return stock.get_market_fundamental(start, end, ticker)
    raise AttributeError("pykrx.stock does not expose get_market_fundamental")


def _selected_endpoint(stock: Any) -> str:
    if hasattr(stock, "get_market_ohlcv"):
        return "get_market_ohlcv"
    return "get_market_ohlcv_by_date"


def _selected_fundamental_endpoint(stock: Any) -> str:
    if hasattr(stock, "get_market_fundamental_by_date"):
        return "get_market_fundamental_by_date"
    return "get_market_fundamental"


def _krx_code(ticker: str) -> str:
    value = ticker.strip().upper()
    if "." in value:
        value = value.split(".", 1)[0]
    if not value.isdigit() or len(value) != 6:
        raise ValueError(f"KR ticker must be a 6-digit KRX code, got {ticker!r}")
    return value
