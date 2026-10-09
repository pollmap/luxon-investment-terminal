"""Local API load probe: 100 clients, source snapshots, no upstream requests."""

import asyncio
import json
import time
from pathlib import Path
import httpx


async def main():
    base = "http://127.0.0.1:8210/api/market/v1"
    report = {
        "concurrency": 100,
        "requests_per_endpoint": 300,
        "environment": "local SQLite, single API process; not cloud capacity",
    }
    async with httpx.AsyncClient(
        timeout=90, limits=httpx.Limits(max_connections=100, max_keepalive_connections=100)
    ) as client:
        for name, path in [("search", "/search?q=삼성"), ("detail", "/securities/005930")]:
            await client.get(base + path)
            semaphore = asyncio.Semaphore(100)

            async def get():
                async with semaphore:
                    start = time.perf_counter()
                    try:
                        response = await client.get(base + path)
                        status = response.status_code
                    except httpx.HTTPError:
                        status = 0
                    return (time.perf_counter() - start) * 1000, status

            results = await asyncio.gather(*(get() for _ in range(300)))
            times = sorted(r[0] for r in results)
            report[name] = {
                "p50_ms": round(times[149], 2),
                "p95_ms": round(times[284], 2),
                "max_ms": round(times[-1], 2),
                "errors": sum(r[1] != 200 for r in results),
            }
    path = Path("docs/market-load-results.json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


asyncio.run(main())
