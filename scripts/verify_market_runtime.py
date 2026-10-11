"""Check the running web proxy and its stream without requesting upstream data."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect


async def verify(origin: str) -> dict:
    parsed = urlsplit(origin)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("Use an explicit local preview origin")
    if (
        parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Use only the local origin, without credentials, path or query")
    origin = origin.rstrip("/")
    report = {
        "origin": origin,
        "checked_at": datetime.now(UTC).isoformat(),
        "checks": {},
        "messages": [],
        "live_broker_receipt_verified": False,
    }
    async with httpx.AsyncClient(base_url=origin, timeout=30) as client:
        for name, path in [
            ("search", "/search?q=삼성전자"),
            ("summary", "/securities/005930?view=summary"),
            ("valuation", "/securities/005930/analysis/valuation"),
            ("events", "/securities/005930/events"),
        ]:
            response = await client.get("/api/market/v1" + path)
            response.raise_for_status()
            data = response.json()
            check = {"status": response.status_code}
            if name == "search":
                codes = [row["code"] for row in data["rows"]]
                if not {"005930", "005935"}.issubset(codes):
                    raise AssertionError("Samsung ordinary/preferred search is incomplete")
                check["codes"] = codes
            elif name == "summary":
                if data["security"]["code"] != "005930":
                    raise AssertionError("Summary returned a different security")
                check["code"] = data["security"]["code"]
            else:
                check["availability"] = data.get("availability")
                check["status_label"] = data.get("status")
                if name == "events":
                    check["rows"] = len(data.get("rows", []))
            report["checks"][name] = check
    for attempt in range(2):
        async with connect(
            "ws://" + parsed.netloc + "/api/market/v1/stream",
            origin=origin,
            open_timeout=15,
            close_timeout=5,
        ) as websocket:
            await websocket.send(json.dumps({"codes": ["005930"], "channels": ["quotes", "depth"]}))
            received = set()
            for _ in range(8):
                message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
                kind = message.get("type")
                if kind not in {"snapshot", "depth"}:
                    continue
                if not message.get("epoch"):
                    raise AssertionError("Stream recovery epoch is missing")
                if kind == "snapshot" and not any(
                    row.get("code") == "005930" for row in message.get("rows", [])
                ):
                    raise AssertionError("Snapshot does not contain the subscribed security")
                if kind == "depth" and message.get("code") != "005930":
                    raise AssertionError("Depth belongs to a different security")
                received.add(kind)
                report["messages"].append(
                    {
                        "connection": attempt,
                        "type": kind,
                        "has_epoch": True,
                        "code": message.get("code"),
                    }
                )
                if received == {"snapshot", "depth"}:
                    break
            if received != {"snapshot", "depth"}:
                raise AssertionError("Quote and depth stream did not recover")
    report["local_websocket"] = "connected_and_reconnected"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(verify(args.origin))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
