from __future__ import annotations

from datetime import date

import httpx

from packages.connectors.base import ConnectorDocument, ConnectorRequest, MarketConnector


MARKET_SUFFIXES = {
    "US": "us",
    "JP": "jp",
}


class StooqConnector(MarketConnector):
    source = "stooq"
    market = "GLOBAL"

    def __init__(
        self,
        client: httpx.Client | None = None,
        base_url: str = "https://stooq.com/q/d/l/",
    ) -> None:
        self.client = client or httpx.Client(timeout=30, follow_redirects=True)
        self.base_url = base_url

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        symbol = _stooq_symbol(request.ticker, request.market)
        params = {
            "s": symbol,
            "i": "d",
        }
        if request.start_year:
            params["d1"] = f"{request.start_year}0101"
        if request.end_year:
            params["d2"] = f"{request.end_year}1231"
        response = self.client.get(self.base_url, params=params)
        response.raise_for_status()
        payload = response.content
        if not _looks_like_stooq_csv(payload):
            raise ValueError(f"Stooq did not return a daily CSV for {symbol}")
        return [
            ConnectorDocument(
                source=self.source,
                market=request.market.upper(),
                ticker=_display_ticker(request.ticker),
                identifier=(
                    f"{symbol}-{request.start_year or 'start'}-"
                    f"{request.end_year or 'end'}-daily"
                ),
                url=str(response.url),
                payload=payload,
                content_type="text/csv",
                metadata={
                    "stooq_symbol": symbol,
                    "interval": "daily",
                    "start_year": request.start_year,
                    "end_year": request.end_year,
                    "downloaded_date": date.today().isoformat(),
                },
            )
        ]


def _stooq_symbol(ticker: str, market: str) -> str:
    value = ticker.strip().lower()
    if "." in value or value.startswith("^"):
        return value
    suffix = MARKET_SUFFIXES.get(market.upper())
    return f"{value}.{suffix}" if suffix else value


def _display_ticker(ticker: str) -> str:
    return ticker.strip().split(".", 1)[0].upper()


def _looks_like_stooq_csv(payload: bytes) -> bool:
    head = payload[:128].decode("utf-8", errors="ignore").strip().lower()
    return head.startswith("date,open,high,low,close")
