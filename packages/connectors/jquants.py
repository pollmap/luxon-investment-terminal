from __future__ import annotations

import json
import os

import httpx

from packages.connectors.base import ConnectorDocument, ConnectorRequest, MarketConnector

DEFAULT_JQUANTS_CODES = {
    "7203.T": "72030",
    "6758.T": "67580",
    "6861.T": "68610",
    "8306.T": "83060",
    "7974.T": "79740",
}


JQUANTS_ENDPOINTS = {
    "statements": {
        "path": "/fins/statements",
        "payload_key": "statements",
        "identifier": "fins-statements",
    },
    "daily_quotes": {
        "path": "/prices/daily_quotes",
        "payload_key": "daily_quotes",
        "identifier": "daily-quotes",
    },
    "dividends": {
        "path": "/fins/dividend",
        "payload_key": "dividend",
        "identifier": "fins-dividend",
    },
}


class JQuantsConnector(MarketConnector):
    source = "jquants"
    market = "JP"

    def __init__(
        self,
        refresh_token: str | None = None,
        email: str | None = None,
        password: str | None = None,
        client: httpx.Client | None = None,
        base_url: str = "https://api.jquants.com/v1",
    ) -> None:
        self.refresh_token = refresh_token or os.getenv("JQUANTS_REFRESH_TOKEN")
        self.email = email or os.getenv("JQUANTS_EMAIL")
        self.password = password or os.getenv("JQUANTS_PASSWORD")
        self.client = client or httpx.Client(timeout=30)
        self.base_url = base_url.rstrip("/")

    def collect(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        return self.collect_statements(request)

    def collect_bundle(
        self,
        request: ConnectorRequest,
        endpoints: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> list[ConnectorDocument]:
        requested = list(endpoints or ("daily_quotes", "statements", "dividends"))
        id_token = self._id_token()
        documents: list[ConnectorDocument] = []
        for endpoint in requested:
            documents.append(self._collect_endpoint(request, endpoint, id_token=id_token))
        return documents

    def collect_statements(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        return [self._collect_endpoint(request, "statements")]

    def collect_daily_quotes(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        return [self._collect_endpoint(request, "daily_quotes")]

    def collect_dividends(self, request: ConnectorRequest) -> list[ConnectorDocument]:
        return [self._collect_endpoint(request, "dividends")]

    def _collect_endpoint(
        self,
        request: ConnectorRequest,
        endpoint: str,
        *,
        id_token: str | None = None,
    ) -> ConnectorDocument:
        endpoint_config = JQUANTS_ENDPOINTS.get(endpoint)
        if endpoint_config is None:
            raise ValueError(f"Unsupported J-Quants endpoint: {endpoint}")
        code = DEFAULT_JQUANTS_CODES.get(request.ticker.upper())
        if not code:
            raise LookupError(f"J-Quants local code is not configured for {request.ticker}")
        id_token = id_token or self._id_token()
        headers = {"Authorization": f"Bearer {id_token}"}
        params = {"code": code}
        if request.start_year:
            params["from"] = f"{request.start_year}-01-01"
        if request.end_year:
            params["to"] = f"{request.end_year}-12-31"
        response = self.client.get(
            f"{self.base_url}{endpoint_config['path']}",
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        payload = response.json()
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        rows = payload.get(str(endpoint_config["payload_key"])) or []
        return ConnectorDocument(
            source=self.source,
            market=self.market,
            ticker=request.ticker.upper(),
            identifier=(
                f"{code}-{endpoint_config['identifier']}-"
                f"{request.start_year or 'start'}-{request.end_year or 'end'}"
            ),
            url=str(response.url),
            payload=raw,
            content_type="application/json",
            metadata={
                "local_code": code,
                "endpoint": endpoint_config["path"],
                "payload_key": endpoint_config["payload_key"],
                "start_year": request.start_year,
                "end_year": request.end_year,
                "row_count": len(rows) if isinstance(rows, list) else None,
            },
        )

    def _id_token(self) -> str:
        refresh_token = self.refresh_token
        if not refresh_token:
            if not self.email or not self.password:
                raise RuntimeError(
                    "JQUANTS_REFRESH_TOKEN or JQUANTS_EMAIL/JQUANTS_PASSWORD is required"
                )
            auth_response = self.client.post(
                f"{self.base_url}/token/auth_user",
                json={"mailaddress": self.email, "password": self.password},
            )
            auth_response.raise_for_status()
            refresh_token = auth_response.json()["refreshToken"]
        token_response = self.client.post(
            f"{self.base_url}/token/auth_refresh",
            params={"refreshtoken": refresh_token},
        )
        token_response.raise_for_status()
        return token_response.json()["idToken"]
