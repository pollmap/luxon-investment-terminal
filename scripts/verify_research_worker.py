"""Local actual-model smoke. No provider/production data and no persisted token."""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Evidence output already exists")
    model = args.model_dir.resolve()
    manifest = json.loads((model / "manifest.json").read_text())
    with socket.socket() as check:
        check.bind(("127.0.0.1", 8190))
    token = secrets.token_hex(32)
    env = dict(
        os.environ,
        MARKET_INTERNAL_TOKEN=token,
        MARKET_RESEARCH_MODEL_ONNX=str(model / "model-int8.onnx"),
        MARKET_RESEARCH_TOKENIZER_JSON=str(model / "tokenizer.json"),
        MARKET_RESEARCH_MODEL_SHA256=manifest["model_sha256"],
        MARKET_RESEARCH_TOKENIZER_SHA256=manifest["tokenizer_sha256"],
        MARKET_RESEARCH_BIND="127.0.0.1",
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "services.market_dashboard.research_worker"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def call(path, body=None, authenticated=True):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + token
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request("http://127.0.0.1:8190" + path, data=data, headers=headers)
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=1.5) as response:
                return (
                    response.status,
                    json.loads(response.read(1024**2)),
                    (time.perf_counter() - started) * 1000,
                )
        except urllib.error.HTTPError as error:
            return error.code, {}, (time.perf_counter() - started) * 1000

    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                if call("/health")[0] == 200:
                    break
            except (OSError, TimeoutError):
                pass
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Local worker did not become ready")
            time.sleep(0.1)
        assert call("/v1/embed", {"text": "검수 문장"}, authenticated=False)[0] == 401
        assert call("/v1/unsupported", {"text": "검수 문장"})[0] == 404
        assert call("/v1/embed", {"text": "문장", "query": "true"})[0] == 422
        status, offsets, tokenize_ms = call("/v1/tokenize", {"text": "영업현금흐름과 설비투자"})
        assert status == 200 and offsets["offsets"]
        samples = []
        for value, query in [
            ("영업현금흐름과 설비투자", True),
            ("영업활동 현금과 설비 취득 지출", False),
            ("주당 배당금의 지급일과 권리일", False),
        ]:
            status, result, ms = call("/v1/embed", {"text": value, "query": query})
            assert status == 200 and result["dimensions"] == 384 and len(result["vector"]) == 384
            assert abs(sum(x * x for x in result["vector"]) - 1) < 1e-6
            samples.append({"vector": result["vector"], "ms": ms})
        scores = [
            sum(x * y for x, y in zip(samples[0]["vector"], row["vector"], strict=True))
            for row in samples[1:]
        ]
        assert scores[0] > scores[1]
        report = {
            "actual_int8_inference": True,
            "production_evidence": False,
            "test_scope": "local synthetic text / authentication / body contract",
            "model_revision": manifest["repository_revision"],
            "model_sha256": manifest["model_sha256"],
            "dimensions": 384,
            "tokenize_ms": tokenize_ms,
            "inference_ms": [r["ms"] for r in samples],
            "similarity_comparison": scores,
            "checks": {"unauthorized": 401, "unknown_path": 404, "wrong_query_type": 422},
            "quality_50_questions_passed": False,
            "vps_resource_cap_verified": False,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
