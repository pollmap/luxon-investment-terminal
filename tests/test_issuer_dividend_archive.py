"""Fixture-only offline replay: a retained IR source must not need DART/network."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.market_dashboard import corporate_events, issuer_dividends
from services.market_dashboard.store import Store

OBSERVED = "2026-04-20T00:00:00+00:00"
NOW = "2026-10-10T00:00:00+00:00"


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(issuer_dividends, "now", lambda: NOW)
    monkeypatch.setattr(corporate_events, "now", lambda: NOW)
    monkeypatch.setattr("services.market_dashboard.store.now", lambda: OBSERVED)
    store = Store("sqlite://", tmp_path)
    store.put(
        "universe",
        {
            "rows": [
                {"code": c, "name": n, "market": "KOSPI"}
                for c, (n, _) in issuer_dividends.SYMBOLS.items()
            ]
        },
    )
    records = [
        ["2025.02.28", "2025.04.18", "6000", "6050", "6100", "6050"],
        # Retain the anomalous source row as a candidate, not a paid event.
        ["2018.06.30", "2019.08.14", "1000", "1000", "1000", "1000"],
        # A later replay clock cannot confirm a payment future at collection.
        ["2026.05.31", "2026.06.30", "2500", "2500", "2500", "2500"],
    ]
    content = (
        "fixture_non_production<table><tr>"
        + "".join("<th>" + v + "</th>" for v in issuer_dividends.HEADERS)
        + "</tr>"
        + "".join("<tr>" + "".join("<td>" + v + "</td>" for v in row) + "</tr>" for row in records)
        + "</table>"
    ).encode()
    digest = store.raw("issuer-dividends", issuer_dividends.URL, content)
    evidence = {
        "source": "hyundai_motor_ir",
        "source_url": issuer_dividends.URL,
        "sha256": digest,
        "available_at": OBSERVED,
        "availability_basis": "observed_at",
    }
    # Existing releases have observations but no archive hash in the job.
    store.put(
        "dividend-observations:005380",
        {"rows": issuer_dividends.parse_history(content, evidence), "received_at": OBSERVED},
    )
    job = {
        "status": "error",
        "error": "ReadTimeout",
        "next_at": "2099",
        "received_at": OBSERVED,
        "version": issuer_dividends.VERSION,
    }
    store.put("issuer-dividends-job:005380", job)
    provider = {"status": "800", "next_at": "2099"}
    store.put("dart-provider-retry", provider)
    source = SimpleNamespace(
        store=store,
        dart="",
        client=SimpleNamespace(get=AsyncMock()),
        corporations=AsyncMock(),
        _dart_get=AsyncMock(),
    )
    yield source, digest, content, job, provider
    store.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_archive", [True, False])
async def test_corporate_archive_path_recovers_all_mapped_series_without_network(
    archive, explicit_archive
):
    source, digest, content, job, provider = archive
    if not explicit_archive:
        import hashlib

        from services.market_dashboard.kind_dividends import NOTICES, VERSION

        source.store.put("corporate-events-job:005380", {"retry_after": "2099"})
        # Each independent provider keeps its own retry budget. Do not use
        # DART's backoff as an excuse to disable the normal KIND collector.
        for notice in NOTICES:
            if notice["code"] == "005380":
                receipt = hashlib.sha256(notice["url"].encode()).hexdigest()[:16]
                source.store.put(
                    "kind-dividends-job:005380:" + receipt, {"version": VERSION, "next_at": "2099"}
                )
    await corporate_events.collect_events(source, "005380", archive_only=explicit_archive)
    for code, amount in zip(
        issuer_dividends.SYMBOLS, ("6000", "6050", "6100", "6050"), strict=True
    ):
        observations = source.store.get("dividend-observations:" + code)["rows"]
        assert len(observations) == 2
        assert len([r for r in observations if not r["reviewed"]]) == 1
        (event,) = source.store.get("events:" + code)["rows"]
        assert event["amount"] == amount
        assert event["payment_date"] == "2025-04-18"
        assert event["record_date"] == "2025-02-28"
        assert not event.get("ex_date") and not event["verified"]
        assert event["source_trace"]["sha256"] == digest
        assert event["source_trace"]["available_at"] == OBSERVED
    assert source.store.get("issuer-dividends-job:005380") == job
    assert source.store.get("dart-provider-retry") == provider
    assert not source.store.get("corporate-event-index:005380")
    source.client.get.assert_not_awaited()


def test_new_job_pointer_can_rebuild_missing_observations_from_compressed_archive(archive):
    import gzip

    from services.market_dashboard.issuer_dividends import replay_issuer_dividends

    source, digest, content, job, _ = archive
    source.store.put("dividend-observations:005380", {"rows": []})
    source.store.put("issuer-dividends-job:005380", {**job, "raw_hash": digest})
    folder = source.store.root / "raw/issuer-dividends"
    (folder / (digest + ".bin.gz")).write_bytes(gzip.compress(content))
    (folder / (digest + ".bin")).unlink()
    result = replay_issuer_dividends(source.store, "005380")
    assert result["status"] == "ready"
    assert len(source.store.get("events:005380")["rows"]) == 1


def test_listing_mismatch_is_rejected_before_any_issuer_projection(archive):
    from services.market_dashboard.issuer_dividends import replay_issuer_dividends

    source, _, _, _, _ = archive
    source.store.put("universe", {"rows": [{"code": "005380", "name": "다른 회사"}]})
    assert replay_issuer_dividends(source.store, "005380")["status"] == "error"
    assert all(not source.store.get("events:" + code) for code in issuer_dividends.SYMBOLS)


@pytest.mark.asyncio
async def test_bad_preferred_column_does_not_partially_write_ordinary_projection(archive):
    import hashlib

    from services.market_dashboard.issuer_dividends import replay_issuer_dividends

    source, _, content, job, _ = archive
    content = content.replace(b"<td>6100</td>", b"<td>not-a-number</td>")
    digest = source.store.raw("issuer-dividends", issuer_dividends.URL, content)
    assert digest == hashlib.sha256(content).hexdigest()
    source.store.put("issuer-dividends-job:005380", {**job, "raw_hash": digest})
    before = source.store.get("dividend-observations:005380")
    assert replay_issuer_dividends(source.store, "005380")["status"] == "error"
    assert source.store.get("dividend-observations:005380") == before
    assert all(not source.store.get("events:" + code) for code in issuer_dividends.SYMBOLS)
    source.corporations.assert_not_awaited()
    source._dart_get.assert_not_awaited()
    assert (source.store.root / "raw/issuer-dividends" / (digest + ".bin")).read_bytes() == content


@pytest.mark.asyncio
async def test_repeated_replay_is_idempotent_and_preserves_dart_amount_and_schedule(archive):
    source, _, _, _, _ = archive
    source.store.put(
        "events:005380",
        {
            "received_at": OBSERVED,
            "rows": [
                {
                    "id": "fixture-dart",
                    "kind": "dividend",
                    "security_code": "005380",
                    "share_class": "ordinary",
                    "record_date": "2025-02-28",
                    "currency": "KRW",
                    "amount": "7000",
                    "payment_date": "2025-04-17",
                    "scheduled_payment_date": "2025-04-17",
                    "payment_status": "announced",
                    "verified": False,
                    "source_trace": {"source": "fixture_non_production"},
                }
            ],
        },
    )
    await corporate_events.collect_events(source, "005380", archive_only=True)
    keys = [
        kind + code
        for code in issuer_dividends.SYMBOLS
        for kind in ("events:", "dividend-observations:")
    ]
    revisions = source.store.revisions(keys)
    saved = {key: source.store.get(key) for key in keys}
    await corporate_events.collect_events(source, "005380", archive_only=True)
    assert source.store.revisions(keys) == revisions
    assert {key: source.store.get(key) for key in keys} == saved
    (row,) = saved["events:005380"]["rows"]
    assert row["amount"] == "7000" and row["payment_status"] == "announced"
    assert row["payment_date"] == row["scheduled_payment_date"] == "2025-04-17"


@pytest.mark.parametrize("damage", ["bytes", "url", "hash", "time", "missing"])
def test_corrupted_or_unattributed_archive_cannot_replace_good_rows(archive, damage):
    source, digest, _, job, _ = archive
    from services.market_dashboard.issuer_dividends import replay_issuer_dividends

    folder = source.store.root / "raw/issuer-dividends"
    path = folder / (digest + ".json")
    metadata = json.loads(path.read_bytes())
    if damage == "bytes":
        (folder / (digest + ".bin")).write_bytes(b"fixture_corruption")
    elif damage == "missing":
        path.unlink()
    else:
        metadata[{"url": "url", "hash": "sha256", "time": "received_at"}[damage]] = "changed"
        path.write_text(json.dumps(metadata), encoding="utf-8")
    before = source.store.get("dividend-observations:005380")
    result = replay_issuer_dividends(source.store, "005380")
    assert result["status"] in ("archive_missing", "error")
    assert source.store.get("dividend-observations:005380") == before
    assert source.store.get("issuer-dividends-job:005380") == job
    assert not source.store.get("events:005380")
    source.client.get.assert_not_awaited()
