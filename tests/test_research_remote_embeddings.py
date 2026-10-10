"""Pure internal HTTP transport fixtures. No server, model download or provider."""

import json
import threading
import time

import httpx
import pytest

from services.market_dashboard.research_embeddings import (
    INTERNAL_URL,
    EmbeddingUnavailable,
    RemoteE5Adapter,
    load_from_env,
)


def remote(handler, **options):
    return RemoteE5Adapter(
        INTERNAL_URL,
        token="fixture-token",
        model_sha256="a" * 64,
        tokenizer_sha256="b" * 64,
        transport=httpx.MockTransport(handler),
        **options,
    )


def body(result, status=200, **headers):
    return httpx.Response(
        status, headers=headers, stream=httpx.ByteStream(json.dumps(result).encode())
    )


def test_remote_prefix_ownership_model_contract_offsets_and_no_redirect():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer fixture-token"
        assert request.url.host == "research-embedder"
        payload = json.loads(request.content)
        common = {"model_version": adapter.version, "dimensions": 384}
        if request.url.path == "/v1/tokenize":
            assert payload == {"text": "근거"}
            return body({**common, "offsets": [[0, 1], [1, 2]]})
        assert payload == {"text": "질문", "query": True}
        return body({**common, "vector": [1.0] + [0.0] * 383})

    adapter = remote(handler)
    try:
        assert adapter.offsets("근거") == [[0, 1], [1, 2]]
        assert adapter.encode("질문", query=True) == [1.0] + [0.0] * 383
        assert len(calls) == 2
    finally:
        adapter.close()


@pytest.mark.parametrize(
    "case", ["redirect", "different_model", "wrong_shape", "compressed", "oversize"]
)
def test_remote_failure_is_bounded_and_backoff_reuses_no_fake_vector(case):
    calls = []

    def handler(request):
        calls.append(1)
        if case == "redirect":
            return body({}, status=302, location="http://127.0.0.1/private")
        if case == "oversize":
            return httpx.Response(200, stream=httpx.ByteStream(b" " * (1024**2 + 1)))
        return body(
            {
                "model_version": "other" if case == "different_model" else adapter.version,
                "dimensions": 12 if case == "wrong_shape" else 384,
                "vector": [1] + [0] * 383,
            },
            **({"content-encoding": "gzip"} if case == "compressed" else {}),
        )

    adapter = remote(handler)
    try:
        with pytest.raises(EmbeddingUnavailable):
            adapter.encode("원문")
        with pytest.raises(EmbeddingUnavailable, match="backoff"):
            adapter.encode("원문")
        assert calls == [1]
    finally:
        adapter.close()


def test_timeout_returns_without_waiting_and_retains_capacity_until_child_exits():
    entered, release = threading.Event(), threading.Event()

    def handler(request):
        entered.set()
        release.wait(1)
        return body({})

    adapter = remote(handler, timeout_seconds=0.03)
    try:
        started = time.monotonic()
        with pytest.raises(EmbeddingUnavailable, match="timeout"):
            adapter.encode("원문")
        assert entered.is_set() and time.monotonic() - started < 0.5
        adapter.retry_after = 0  # isolate capacity from already-tested circuit protection
        with pytest.raises(EmbeddingUnavailable, match="busy"):
            adapter.encode("다음 원문")
    finally:
        release.set()
        adapter.close()


def test_remote_environment_is_no_io_construction_and_rejects_unpinned_host(monkeypatch):
    env = {
        "MARKET_RESEARCH_EMBEDDING_URL": INTERNAL_URL,
        "MARKET_INTERNAL_TOKEN": "fixture-token",
        "MARKET_RESEARCH_MODEL_SHA256": "a" * 64,
        "MARKET_RESEARCH_TOKENIZER_SHA256": "b" * 64,
    }
    adapter, reason = load_from_env(env)
    assert isinstance(adapter, RemoteE5Adapter) and reason is None
    adapter.close()
    for bad in (
        {"MARKET_INTERNAL_TOKEN": ""},
        {"MARKET_RESEARCH_EMBEDDING_URL": "http://localhost:8190"},
        {"MARKET_RESEARCH_MODEL_SHA256": "bad"},
    ):
        assert load_from_env({**env, **bad}) == (None, "embedding_configuration_invalid")


def test_local_runtime_requires_explicit_opt_in():
    env = {
        "MARKET_RESEARCH_MODEL_ONNX": "unused",
        "MARKET_RESEARCH_TOKENIZER_JSON": "unused",
        "MARKET_RESEARCH_MODEL_SHA256": "a" * 64,
        "MARKET_RESEARCH_TOKENIZER_SHA256": "b" * 64,
    }
    assert load_from_env(env) == (None, "local_embedding_disabled")
