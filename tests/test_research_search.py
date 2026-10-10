"""Synthetic local SQLite evidence; not provider validation or production evidence."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, inspect, select

from services.market_dashboard.market_context import DOCUMENTS, ContextRepository
from services.market_dashboard.research_embeddings import (
    E5OnnxAdapter,
    EmbeddingUnavailable,
    load_from_env,
    normalized_vector,
)
from services.market_dashboard.research_search import (
    CHUNKS,
    HEADS,
    LEXICAL_VERSION,
    ResearchRepository,
    SearchBudget,
    attach_routes,
    catalogue_state,
    chunk_section,
    evidence_url,
    nearest_vector_query,
    token_offsets,
)
from services.market_dashboard.store import Store


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # Explicit distinct receipt timestamps; wall-clock resolution is not a test oracle.
    ticks = iter(range(100000))

    def receipt_time():
        return (
            datetime(2026, 10, 11, tzinfo=UTC) + timedelta(microseconds=next(ticks))
        ).isoformat()

    monkeypatch.setattr("services.market_dashboard.market_context.now", receipt_time)
    monkeypatch.setattr("services.market_dashboard.research_search.now", receipt_time)
    store = Store("sqlite://", tmp_path)
    store.put(
        "universe",
        {
            "rows": [
                {"code": "005930", "name": "삼성전자", "market": "KOSPI", "security_type": "stock"},
                {
                    "code": "000660",
                    "name": "SK하이닉스",
                    "market": "KOSPI",
                    "security_type": "stock",
                },
                {"code": "999999", "name": "제외", "market": "KONEX"},
            ]
        },
    )
    context = ContextRepository(store)
    context.initialize()
    app = FastAPI()
    attach_routes(app, "/api/market/v1", lambda: store)
    with TestClient(app) as client:
        yield store, context, client
    store.engine.dispose()


def news(title="반도체 공급 계약", article="1", day="2026-10-01", body="비밀 기사 본문"):
    return {
        "source": "fixture_non_production",
        "source_digest": "a" * 64,
        "rows": [
            {
                "title": title,
                "url": "https://example.com/article/" + article,
                "published_at": day,
                "publisher": "fixture_non_production",
                "body": body,
                "client_secret": "NOT_PUBLIC",
            }
        ],
    }


def filing(text="영업현금흐름 개선 원문 근거", revision="b" * 64):
    return {
        "document_id": "c" * 64,
        "revision": revision,
        "kind": "filing",
        "title": "분기보고서",
        "url": "https://example.com/filing",
        "published_at": "2026-10-01",
        "source_hash": "d" * 64,
        "issuer": "fixture_non_production",
        "sections": [{"text": text, "section": "현금흐름", "page": 15}],
    }


def test_existing_title_fallback_never_writes_or_exposes_news_body(setup):
    store, context, client = setup
    context.ingest_news("005930", news())
    assert not inspect(store.engine).has_table(HEADS.name)
    result = client.get("/api/market/v1/research/search", params={"q": "반도체"}).json()
    assert result["status"] == "ready"
    assert result["mode"] == "lexical_only"
    assert result["coverage"]["index"] == "context_titles_only"
    assert result["available_documents"] == 1
    row = result["items"][0]
    assert row["code"] == "005930" and row["source_hash"] == "a" * 64
    assert row["excerpt"] == row["title"] == "반도체 공급 계약"
    assert "비밀 기사 본문" not in json.dumps(result, ensure_ascii=False)
    assert "NOT_PUBLIC" not in json.dumps(result)
    assert not inspect(store.engine).has_table(HEADS.name)
    detail = client.get("/api/market/v1/research/documents/" + row["id"]).json()
    assert detail["chunks"][0]["excerpt"] == row["title"]


def test_lexical_filters_latest_revision_and_unknown_date(setup):
    _, context, client = setup
    context.ingest_news("005930", news())
    context.ingest_news("005930", news("반도체 계약 정정"))
    context.ingest_news("000660", news("메모리 산업", "2", "2026-09-01"))
    result = client.get(
        "/api/market/v1/research/search",
        params={
            "q": "반도체",
            "code": "005930",
            "kind": "news",
            "from": "2026-10-01",
            "to": "2026-10-01",
        },
    ).json()
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "반도체 계약 정정"
    assert result["available_documents"] == 1
    other = client.get(
        "/api/market/v1/research/search", params={"q": "반도체", "code": "000660"}
    ).json()
    assert not other["items"]


@pytest.mark.parametrize(
    "params",
    [
        {"q": "x" * 201},
        {"q": " "},
        {"q": "a\n"},
        {"q": "a", "limit": 21},
        {"q": "a", "limit": 0},
        {"q": "a", "code": "../../"},
        {"q": "a", "kind": "secrets"},
        {"q": "a", "from": "2026-02-31"},
        {"q": "a", "from": "2026-10-02", "to": "2026-10-01"},
    ],
)
def test_invalid_input_is_bounded(params, setup):
    assert setup[2].get("/api/market/v1/research/search", params=params).status_code == 422


def test_sql_like_and_injection_are_plain_text(setup):
    store, context, client = setup
    context.ingest_news("005930", news())
    result = client.get(
        "/api/market/v1/research/search",
        params={"q": "%' OR 1=1; DROP TABLE market_context_documents; --"},
    )
    assert result.status_code == 200
    assert inspect(store.engine).has_table(DOCUMENTS.name)
    assert client.get("/api/market/v1/research/documents/" + "f" * 64).status_code == 404
    assert client.get("/api/market/v1/research/documents/invalid").status_code == 422


def test_indexed_evidence_retains_source_sections_and_versions(setup):
    store, _, client = setup
    repo = ResearchRepository(store)
    repo.initialize()
    assert repo.ingest(filing(), codes=["005930"]) == 1
    result = client.get("/api/market/v1/research/search", params={"q": "현금흐름"}).json()
    row = result["items"][0]
    assert row["excerpt"] == "영업현금흐름 개선 원문 근거"
    assert row["source_hash"] == "d" * 64 and row["revision"] == "b" * 64
    assert row["section"] == "현금흐름" and row["page"] == 15
    assert row["company"] == "삼성전자" and row["index_version"] == LEXICAL_VERSION
    repo.ingest(filing("현금흐름 정정된 구절", "e" * 64), codes=["005930"])
    revised = repo.search("현금흐름")["items"][0]
    assert revised["revision"] == "e" * 64
    with store.read_connection() as db:
        assert db.execute(select(func.count()).select_from(CHUNKS)).scalar_one() == 2
    old = repo.document("c" * 64, "b" * 64)
    assert old["revision"] == "b" * 64
    assert old["chunks"][0]["excerpt"] == "영업현금흐름 개선 원문 근거"


def test_context_projection_is_incremental_idempotent_and_stale_safe(setup):
    store, context, _ = setup
    context.ingest_news("005930", news())
    context.ingest_news("000660", news("반도체 투자", "2"))
    repo = ResearchRepository(store)
    repo.initialize()
    first = repo.project_context(limit=1)
    assert first["has_more"] and first["processed"] == 1
    second = repo.project_context(after=first["next"], limit=1)
    assert second["processed"] == 1 and not second["has_more"]
    repo.project_context(limit=10)
    with store.read_connection() as db:
        assert db.execute(select(func.count()).select_from(CHUNKS)).scalar_one() == 2
    context.ingest_news("005930", news("반도체 계약 취소 정정"))
    rows = repo.search("취소")["items"]
    assert rows[0]["title"] == "반도체 계약 취소 정정"
    assert rows[0]["index_version"] == "context_titles_only"


def test_lexical_chunk_boundaries_and_table_row_offsets():
    value = " ".join("원문" + str(i) for i in range(700))
    chunks = list(chunk_section({"text": value, "section": "원문", "page": 7}))
    assert [c["token_count"] for c in chunks] == [320, 320, 156]
    assert [(c["token_start"], c["token_end"]) for c in chunks] == [
        (0, 320),
        (272, 592),
        (544, 700),
    ]
    assert all(c["text"] == value[c["char_start"] : c["char_end"]] for c in chunks)
    table = list(
        chunk_section(
            {"kind": "table", "header": "연도 매출", "rows": [value, "2026 100"], "page": 8}
        )
    )
    assert all(c["token_count"] <= 320 and c["text"].startswith("연도 매출\n") for c in table)
    assert {c["row"] for c in table} == {0, 1}
    assert all(c["header_char_end"] == len("연도 매출") for c in table)


def test_actual_tokenizer_offsets_drive_model_chunking():
    class CharacterTokenizer:
        def offsets(self, text):
            return [(i, i + 1) for i in range(len(text))]

    text = "한글원문" * 200
    chunks = list(chunk_section({"text": text}, CharacterTokenizer()))
    assert chunks[0]["token_end"] == 320 and chunks[1]["token_start"] == 272
    assert chunks[0]["text"] == text[:320]


def test_corpus_limits_and_scope_reject_before_publication(setup):
    store = setup[0]
    repo = ResearchRepository(store)
    repo.initialize()
    with pytest.raises(ValueError):
        repo.ingest(filing(), codes=["999999"])
    without_hash = filing()
    without_hash.pop("source_hash")
    with pytest.raises(ValueError, match="source hash"):
        repo.ingest(without_hash, codes=["005930"])
    with pytest.raises(ValueError, match="header"):
        list(chunk_section({"kind": "table", "header": "word " * 200, "rows": ["1"]}))


def test_vector_rrf_and_failure_fallback(setup):
    class Embedding:
        version = "fixture_non_production:384"

        def offsets(self, text):
            return token_offsets(text)

        def encode(self, text, *, query=False):
            return [1.0] + [0.0] * 383

    store = setup[0]
    adapter = Embedding()
    repo = ResearchRepository(store, adapter=adapter)
    repo.initialize()
    repo.ingest(filing(), codes=["005930"])
    result = repo.search("영업현금흐름")
    assert result["mode"] == "hybrid_rrf" and result["fallback_reason"] is None
    assert '"_vector"' not in json.dumps(result)

    def unavailable(*args, **kwargs):
        raise EmbeddingUnavailable("fixture timeout")

    adapter.encode = unavailable
    fallback = repo.search("영업현금흐름")
    assert fallback["mode"] == "lexical_only"
    assert fallback["fallback_reason"] == "embedding_unavailable"
    assert fallback["items"] == result["items"]


def test_bounded_rate_and_concurrency():
    budget = SearchBudget(per_minute=2, concurrent=1)
    with budget.acquire():
        with pytest.raises(EmbeddingUnavailable):
            with budget.acquire():
                pass
    with budget.acquire():
        pass
    with pytest.raises(EmbeddingUnavailable):
        with budget.acquire():
            pass


def test_burst_waits_in_bounded_queue_without_expanding_execution_slots():
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    budget = SearchBudget(concurrent=4, queued=20, wait_seconds=0.75)
    barrier, lock = threading.Barrier(12), threading.Lock()
    active, peak = 0, 0

    def work(_):
        nonlocal active, peak
        barrier.wait(timeout=2)
        with budget.acquire():
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with lock:
                active -= 1
        return True

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert all(pool.map(work, range(12)))
    assert peak == 4
    assert active == budget.reserved == 0


def test_e5_prefix_masked_pooling_normalization_and_dimension():
    texts = []

    class Encoding:
        ids, attention_mask, type_ids = [1, 2, 0], [1, 1, 0], [0, 0, 0]

    class Tokenizer:
        def encode(self, text):
            texts.append(text)
            return Encoding()

    class Input:
        def __init__(self, name):
            self.name = name

    class Session:
        def get_inputs(self):
            return [Input("input_ids"), Input("attention_mask")]

        def run(self, _, values, options):
            assert values["input_ids"].shape == (1, 3)
            output = np.zeros((1, 3, 384))
            output[0, 0, 0], output[0, 1, 0], output[0, 2, 1] = 1, 3, 10000
            return [output]

    adapter = E5OnnxAdapter(
        Session(), Tokenizer(), model_sha256="a" * 64, tokenizer_sha256="b" * 64
    )
    assert adapter.encode("원문", query=True) == [1.0] + [0.0] * 383
    assert adapter.encode("원문") == [1.0] + [0.0] * 383
    assert texts == ["query: 원문", "passage: 원문"]
    for invalid in ([0] * 384, [1] * 383, [float("nan")] * 384):
        with pytest.raises(EmbeddingUnavailable):
            normalized_vector(invalid)


def test_local_model_hash_checked_before_loading_optional_runtime(tmp_path):
    model, tokenizer = tmp_path / "model.onnx", tmp_path / "tokenizer.json"
    model.write_bytes(b"fixture_non_production")
    tokenizer.write_text("{}")
    with pytest.raises(EmbeddingUnavailable, match="hash_mismatch"):
        E5OnnxAdapter.load(
            model,
            tokenizer,
            model_sha256="0" * 64,
            tokenizer_sha256=hashlib.sha256(b"{}").hexdigest(),
        )


def test_sql_filter_finds_old_title_and_body_before_candidate_bound(setup):
    store, context, _ = setup
    for i in range(8):
        context.ingest_news("005930", news("새로운 일반 기사", str(i), "2026-10-10"))
    context.ingest_news("005930", news("오래된 독립배당근거", "old", "2025-01-01"))
    repo = ResearchRepository(store, candidate_limit=2)
    old = repo.search("독립배당근거")
    assert len(old["items"]) == 1 and old["items"][0]["published_at"] == "2025-01-01"
    assert old["available_documents"] == 9 and not old["coverage"]["truncated"]
    repo.initialize()
    source = filing("장기공급계약해지 원문")
    source["published_at"] = "2024-01-01"
    repo.ingest(source, codes=["005930"])
    result = repo.search("장기공급계약해지")
    assert result["items"][0]["excerpt"] == "장기공급계약해지 원문"


def test_sqlite_trigram_candidate_typo_is_deterministic(setup):
    store, context, _ = setup
    context.ingest_news("005930", news("반도체공급계약"))
    repo = ResearchRepository(store)
    a, b = repo.search("반도체공급게약"), repo.search("반도체공급게약")
    assert a["items"] == b["items"] and len(a["items"]) == 1


def test_immutable_chunk_conflict_and_late_old_revision_keep_current(setup):
    store = setup[0]
    repo = ResearchRepository(store)
    repo.initialize()
    repo.ingest(filing(), codes=["005930"], recorded_at="2026-10-01T10:00:00Z")
    with pytest.raises(ValueError, match="Immutable"):
        repo.ingest(filing("같은 revision으로 숫자 변경"), codes=["005930"])
    repo.ingest(
        filing("새로운 근거", "e" * 64), codes=["005930"], recorded_at="2026-10-02T10:00:00Z"
    )
    repo.ingest(filing(), codes=["005930"], recorded_at="2026-10-01T10:00:00Z")
    assert repo.document("c" * 64)["revision"] == "e" * 64


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/a",
        "http://127.0.0.1/a",
        "http://127.1/a",
        "https://user:password@example.com",
        "https://app.internal/a",
        "http://[::1]/a",
        "https://example.com/a\nsecret",
    ],
)
def test_source_links_are_public_browser_urls_only(url):
    assert evidence_url(url) is None


def test_public_routes_issue_no_ddl_insert_or_update(setup):
    store, context, client = setup
    context.ingest_news("005930", news())
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(store.engine, "before_cursor_execute", capture)
    try:
        found = client.get("/api/market/v1/research/search", params={"q": "반도체"}).json()
        client.get("/api/market/v1/research/documents/" + found["items"][0]["id"])
    finally:
        event.remove(store.engine, "before_cursor_execute", capture)
    assert all(s.lstrip().upper().startswith(("SELECT", "PRAGMA")) for s in statements)


def test_page_and_source_metadata_cannot_be_arbitrary_private_objects(setup):
    repo = ResearchRepository(setup[0])
    repo.initialize()
    payload = filing()
    payload["sections"][0]["page"] = {"secret": "NOT_PUBLIC"}
    with pytest.raises(ValueError, match="page"):
        repo.ingest(payload, codes=["005930"])


def test_model_environment_loaded_once_before_requests_and_safe_fallback(setup, monkeypatch):
    import services.market_dashboard.research_embeddings as embeddings

    assert load_from_env({}) == (None, "embedding_not_configured")
    assert load_from_env({"MARKET_RESEARCH_MODEL_ONNX": "privatepath"}) == (
        None,
        "embedding_configuration_incomplete",
    )
    calls = []

    def failed(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("privatepath AND secret")

    monkeypatch.setattr(E5OnnxAdapter, "load", failed)
    for key in embeddings.ENV_KEYS:
        monkeypatch.setenv(key, "configured")
    monkeypatch.setenv("MARKET_RESEARCH_LOCAL_EMBEDDING", "1")
    app = FastAPI()
    attach_routes(app, "/api/market/v1", lambda: setup[0])
    setup[1].ingest_news("005930", news())
    with TestClient(app) as client:
        for _ in range(2):
            response = client.get("/api/market/v1/research/search", params={"q": "반도체"})
            assert response.json()["fallback_reason"] == "embedding_load_failed"
            assert "privatepath" not in response.text and "secret" not in response.text
    assert calls == [1]


def test_pg_nearest_vector_is_bound_and_filters_precede_limit():
    from sqlalchemy.dialects import postgresql

    query = nearest_vector_query(select(CHUNKS).where(CHUNKS.c.embedding_version == "pinned-v1"))
    compiled = query.compile(dialect=postgresql.dialect())
    assert "query_vector" in compiled.params and compiled.params["query_vector"] is None
    assert "CAST(%(query_vector)s AS vector)" in str(compiled)
    assert (
        str(compiled).index("WHERE")
        < str(compiled).index("ORDER BY")
        < str(compiled).index("LIMIT")
    )


def test_pg_catalogue_is_one_bounded_read_and_capabilities_are_not_cached():
    from sqlalchemy.dialects import postgresql

    class DB:
        calls = []
        indexed = False

        def execute(self, statement, parameters):
            compiled = statement.compile(dialect=postgresql.dialect())
            assert set(compiled.params) == {"heads", "chunks", "documents", "links"}
            assert parameters == {
                "heads": HEADS.name,
                "chunks": CHUNKS.name,
                "documents": DOCUMENTS.name,
                "links": "market_context_document_securities",
            }
            assert str(compiled).startswith("SELECT ")
            self.calls.append(str(compiled))
            return self

        def mappings(self):
            return self

        def one(self):
            return {
                "has_heads": self.indexed,
                "has_chunks": self.indexed,
                "has_documents": True,
                "has_links": True,
                "pg_trgm": self.indexed,
                "pg_vectors": self.indexed,
            }

    db = DB()
    assert catalogue_state(db, "postgresql") == (False, True, False, False)
    assert len(db.calls) == 1
    db.indexed = True
    assert catalogue_state(db, "postgresql") == (True, True, True, True)
    assert len(db.calls) == 2
    db.indexed = False
    assert catalogue_state(db, "postgresql") == (False, True, False, False)


def test_existing_titles_use_one_connection_and_new_index_and_deletion_are_visible(setup):
    store, context, _ = setup
    context.ingest_news("005930", news())
    checked_out = []

    def checkout(dbapi_connection, connection_record, connection_proxy):
        checked_out.append(1)

    event.listen(store.engine, "checkout", checkout)
    repo = ResearchRepository(store)
    try:
        before = repo.search("반도체")
        assert checked_out == [1]  # schema detection and search share one read connection
        checked_out.clear()
        repo.initialize()
        repo.project_context(limit=1)
        checked_out.clear()
        after = repo.search("반도체")
        assert checked_out == [1]
        assert after["items"][0]["title"] == before["items"][0]["title"]
        assert after["items"][0]["revision"] == before["items"][0]["revision"]
        assert after["coverage"]["index"] == "indexed_and_titles"
        with store.engine.begin() as db:
            db.execute(DOCUMENTS.delete())
        checked_out.clear()
        empty = repo.search("반도체")
        assert checked_out == [1]
        assert empty["available_documents"] == 0 and empty["items"] == []
    finally:
        event.remove(store.engine, "checkout", checkout)
