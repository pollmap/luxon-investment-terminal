"""Synthetic DART responses: unavailable XML is evidence, not a cached filing."""

import hashlib
import io
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.market_dashboard import corporate_events
from services.market_dashboard.store import Store

NOW = "2026-10-09T12:00:00+00:00"
CODE = "005930"
RECEIPT = "20260901000001"
ERROR_XML = b"<result><status>014</status><message>File unavailable</message></result>"


def filing_zip():
    document = """<table>
    <tr><td>3. 1주당 배당금(원)</td><td>보통주식</td><td>374</td></tr>
    <tr><td>6. 배당기준일</td><td>2026-06-30</td></tr>
    <tr><td>7. 배당금지급 예정일자</td><td>2026-08-28</td></tr>
    <tr><td>배당락일</td><td>2026-06-29</td></tr>
    </table>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("report.xml", document.encode())
    return buffer.getvalue()


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(corporate_events, "now", lambda: NOW)
    store = Store("sqlite://", tmp_path)
    digest = store.raw("dart-events", "fixture_non_production", ERROR_XML)
    store.put("corporate-event-index:" + CODE, {
        "rows": [{"receipt": RECEIPT, "title": "현금배당결정", "date": "20260901"}],
        "status": "ready", "received_at": NOW,
    })
    store.put("corporate-events-job:" + CODE, {
        "documents": {RECEIPT: {
            "status": "document_unavailable", "raw_hash": digest,
            "retry_after": "2026-10-08T12:00:00+00:00",
        }},
        "complete": False,
    })
    yield SimpleNamespace(
        store=store, dart="fixture_non_production", error_hash=digest,
        corporations=AsyncMock(return_value={CODE: "fixture_non_production"}),
        _dart_get=AsyncMock(),
    )
    store.engine.dispose()


def raw_path(source, digest):
    return source.store.root / "raw" / "dart-events" / (digest + ".bin")


@pytest.mark.asyncio
async def test_expired_cached_error_fetches_filing_once_and_preserves_error_evidence(source):
    content = filing_zip()
    source._dart_get.return_value = SimpleNamespace(content=content, raise_for_status=Mock())

    await corporate_events.collect_events(source, CODE)

    source._dart_get.assert_awaited_once_with(
        "https://opendart.fss.or.kr/api/document.xml",
        params={"crtfc_key": "fixture_non_production", "rcept_no": RECEIPT},
    )
    job = source.store.get("corporate-events-job:" + CODE)
    assert job["documents"][RECEIPT]["status"] == "parsed"
    assert job["documents"][RECEIPT]["raw_hash"] == hashlib.sha256(content).hexdigest()
    assert job["complete"]
    assert raw_path(source, source.error_hash).read_bytes() == ERROR_XML
    assert raw_path(source, job["documents"][RECEIPT]["raw_hash"]).read_bytes() == content
    event = source.store.get("events:" + CODE)["rows"][0]
    assert event["amount"] == "374"
    assert event["payment_status"] == "announced"
    assert event["verified"] is False

    await corporate_events.collect_events(source, CODE)
    source._dart_get.assert_awaited_once()


@pytest.mark.asyncio
async def test_cached_error_does_not_bypass_document_retry_budget(source):
    job = source.store.get("corporate-events-job:" + CODE)
    job["documents"][RECEIPT]["retry_after"] = "2026-10-10T12:00:00+00:00"
    source.store.put("corporate-events-job:" + CODE, job)

    await corporate_events.collect_events(source, CODE)

    source._dart_get.assert_not_awaited()
    assert source.store.get("corporate-events-job:" + CODE)["documents"] == job["documents"]
    assert raw_path(source, source.error_hash).read_bytes() == ERROR_XML


@pytest.mark.asyncio
async def test_archive_only_does_not_refetch_error_or_promote_freshness(source):
    job = source.store.get("corporate-events-job:" + CODE)
    index = source.store.get("corporate-event-index:" + CODE)

    await corporate_events.collect_events(source, CODE, archive_only=True)

    source._dart_get.assert_not_awaited()
    source.corporations.assert_not_awaited()
    assert source.store.get("corporate-events-job:" + CODE) == job
    assert source.store.get("corporate-event-index:" + CODE) == index
    assert source.store.get("events:" + CODE, {}) == {}


@pytest.mark.asyncio
async def test_repeated_error_requests_once_then_respects_new_retry_budget(source):
    source._dart_get.return_value = SimpleNamespace(content=ERROR_XML, raise_for_status=Mock())

    await corporate_events.collect_events(source, CODE)
    await corporate_events.collect_events(source, CODE)

    source._dart_get.assert_awaited_once()
    job = source.store.get("corporate-events-job:" + CODE)
    assert not job["complete"]
    assert job["documents"][RECEIPT] == {
        "status": "document_unavailable", "raw_hash": source.error_hash,
        "retry_after": "2026-10-10T12:00:00+00:00",
    }
    assert source.store.get("events:" + CODE)["rows"] == []


@pytest.mark.asyncio
async def test_network_failure_does_not_overwrite_previous_job_or_events(source):
    source._dart_get.side_effect = TimeoutError("fixture provider unavailable")
    events = {"rows": [], "received_at": "2026-10-01T12:00:00+00:00", "status": "partial"}
    source.store.put("events:" + CODE, events)
    job = source.store.get("corporate-events-job:" + CODE)

    with pytest.raises(TimeoutError, match="fixture provider unavailable"):
        await corporate_events.collect_events(source, CODE)

    assert source.store.get("corporate-events-job:" + CODE) == job
    assert source.store.get("events:" + CODE) == events
    assert raw_path(source, source.error_hash).read_bytes() == ERROR_XML


@pytest.mark.asyncio
async def test_corrupt_archive_is_not_silently_replaced_by_retry(source):
    raw_path(source, source.error_hash).write_bytes(b"fixture corrupted archive")

    with pytest.raises(ValueError, match="archive hash mismatch"):
        await corporate_events.collect_events(source, CODE)

    source._dart_get.assert_not_awaited()
