"""Measure complete HTTP reads and unchanged revalidation, never mix their claims."""

import argparse
import json
import statistics
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:3216")
    parser.add_argument("--output", required=True)
    parser.add_argument("--samples", type=int, default=8)
    args = parser.parse_args()
    results = {"base": args.base, "scope": "local HTTP; no phone or WAN emulation",
               "spacing_seconds": 1.1, "cases": []}
    with httpx.Client(timeout=60, trust_env=False, headers={"Accept-Encoding": "gzip"}) as client:
        for code in ("005930", "000660", "005935"):
            url = f"{args.base}/api/market/v1/securities/{code}"
            initial = client.get(url)
            initial.raise_for_status()
            etag = initial.headers.get("etag")
            for mode in ("full", "unchanged"):
                if mode == "unchanged" and not etag:
                    continue
                times, sizes, statuses = [], [], []
                for _ in range(args.samples):
                    time.sleep(1.1)
                    start = time.perf_counter()
                    response = client.get(url, headers={"If-None-Match": etag} if mode == "unchanged" else {})
                    times.append(round((time.perf_counter() - start) * 1000, 3))
                    assert response.status_code in (200, 304), response.status_code
                    if mode == "full":
                        assert response.json() == initial.json(), "Source changed; repeat stable-snapshot benchmark"
                    sizes.append(int(response.headers.get("content-length", 0)))
                    statuses.append(response.status_code)
                case = {"code": code, "mode": mode, "ms": times,
                        "median_ms": statistics.median(times), "body_bytes": sizes, "statuses": statuses}
                results["cases"].append(case)
                print(json.dumps(case), flush=True)
    Path(args.output).write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
