# Internal OpenAlgo market-data service

Pinned upstream: `marketcalls/openalgo` commit `ad2a3f5057fa6689189e28b19a007c4c26b6ff95` (AGPL-3.0).

This service imports the real upstream `history_service`, `quotes_service`, `depth_service`, and `intervals_service`. `server.py` is a read-only Flask host; it does not load the trading application. The `broker/toss/api/data.py` plugin reads the authenticated internal normalized-data API. It never owns a brokerage token.

`TOSS_KR` means integrated Korean equity data. Timestamps use Asia/Seoul. Unavailable open interest is null, not zero. The full pinned upstream source archive, license, host, adapter and requirements are included in each deployment/source bundle. Our host and adapter are available under AGPL-3.0 as part of this OpenAlgo integration.

Routes: POST `/api/v1/quotes`, `/depth`, `/history`, `/intervals`. Authentication: `X-Market-Key`, shared through a private file. Order and account routes return 404. The service is only on the private Docker network.

Flow: public bars API → OpenAlgo service → Toss adapter → **private canonical snapshot API**. The private route never calls OpenAlgo or a public chart route. Stored fallback preserves scope and adjustment semantics; the response identifies the fallback internally.

Required environment: `OPENALGO_SOURCE`, `OPENALGO_STATE_DIR`, `MARKET_CANONICAL_URL`, `MARKET_INTERNAL_KEY_FILE`. Only the data collector uses Toss credentials.
