from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ConnectorRequest:
    ticker: str
    market: str
    start_year: int | None = None
    end_year: int | None = None
    force_refresh: bool = False


@dataclass(frozen=True)
class ConnectorDocument:
    source: str
    market: str
    ticker: str
    identifier: str
    url: str | None
    payload: bytes
    content_type: str
    metadata: dict


class MarketConnector(Protocol):
    source: str
    market: str

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        """Collect source documents without converting them into invented financial numbers."""

