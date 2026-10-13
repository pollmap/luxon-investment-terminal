"""Synthetic receipt metadata only; no Store, provider, or database calls."""

import runpy
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from ops.market.audit_toss_coverage import build_report, terminal_receipt_evidenced


def receipt(kind="history", code="005930"):
    explicit = (
        "explicit_next_before_null"
        if kind in ("history", "raw-history")
        else "explicit_next_until_null"
    )
    context = {
        "code": code,
        "kind": kind,
        "venue": "통합"
        if kind in ("history", "raw-history", "investor-trading")
        else "KRX"
        if kind == "program-trades"
        else "국내",
    }
    if kind in ("history", "raw-history"):
        context["adjustment"] = "source" if kind == "history" else "raw"
    return {
        "history_terminal": {
            **context,
            explicit: True,
            "source": "toss",
            "sha256": "a" * 64,
            "received_at": "2026-10-13T15:00:00+09:00",
        }
    }


def fake_store(state, snapshot=None):
    calls = []
    store = SimpleNamespace(
        listed_codes=lambda: ["005930"],
        list_prefix=lambda prefix: (
            {"toss-backfill:005930:history": state} if prefix == "toss-backfill:" else {}
        ),
        metadata=lambda key, default: calls.append(key) or snapshot or {},
        get=lambda key, default: default,
    )
    return store, calls


@pytest.mark.parametrize(
    "state",
    [
        {"status": "ready", "rows": 100, "next_before": None},
        {"status": "ready", "rows": 100, "backfill_complete": True},
        {"status": "ready", "rows": 100, "backfill_complete": True, "next_until": None},
    ],
)
def test_legacy_completion_flags_never_certify_end_and_remain_unmodified(state):
    before = deepcopy(state)
    store, calls = fake_store(state)
    report = build_report(store, ("history",), "fixture-time")
    assert report["kinds"]["history"]["securities"]["terminal_unverified"] == 1
    assert report["kinds"]["history"]["last_page_fraction"] == 0
    assert report["kinds"]["history"]["metadata_terminal_receipt"] == 0
    assert report["independently_verified_complete_count"] is None
    assert calls == ["bars:005930:통합:day"]
    assert state == before


@pytest.mark.parametrize(
    "kind",
    [
        "history",
        "raw-history",
        "investor-trading",
        "program-trades",
        "short-selling",
        "credit-trades",
        "securities-lending",
    ],
)
def test_only_explicit_terminal_source_receipt_is_evidenced(kind):
    assert terminal_receipt_evidenced(kind, receipt(kind), "005930")


@pytest.mark.parametrize(
    "patch",
    [
        {"source": "fixture_non_production"},
        {"sha256": "fixture"},
        {"received_at": "2026-10-13T15:00:00"},
        {"received_at": None},
        {"explicit_next_before_null": False},
        {"explicit_next_before_null": None},
        {"code": "000660"},
        {"kind": "raw-history"},
        {"venue": "KRX"},
        {"adjustment": "raw"},
        {"source_document_id": "b" * 64},
        {"source_document_id": "toss:fixture-document"},
    ],
)
def test_invalid_or_unanchored_receipts_never_certify_end(patch):
    source = receipt()
    source["history_terminal"].update(patch)
    assert not terminal_receipt_evidenced("history", source, "005930")


@pytest.mark.parametrize(
    "patch",
    [
        {"next_before": "2026-01-01T00:00:00+09:00"},
        {"gap_next_before": "2026-01-01T00:00:00+09:00"},
        {"gap_status": "source_gap"},
    ],
)
def test_remaining_cursor_and_gaps_block_terminal_claim(patch):
    assert not terminal_receipt_evidenced("history", {**receipt(), **patch}, "005930")


def test_source_document_reference_can_anchor_a_terminal_receipt():
    source = receipt()
    source["history_terminal"].pop("sha256")
    source["history_terminal"]["source_document_id"] = "a" * 64
    assert terminal_receipt_evidenced("history", source, "005930")


def test_matching_sha_aliases_can_anchor_a_terminal_receipt():
    source = receipt()
    source["history_terminal"]["source_document_id"] = "a" * 64
    assert terminal_receipt_evidenced("history", source, "005930")


@pytest.mark.parametrize("field", ["code", "kind", "venue", "adjustment", "sha256"])
def test_incomplete_legacy_receipt_context_is_not_terminal_evidence(field):
    source = receipt()
    source["history_terminal"].pop(field)
    assert not terminal_receipt_evidenced("history", source, "005930")


@pytest.mark.parametrize("snapshot", [None, [], {"history_terminal": "legacy"}])
def test_non_object_snapshot_or_receipt_is_not_terminal_evidence(snapshot):
    assert not terminal_receipt_evidenced("history", snapshot, "005930")


def test_evidenced_terminal_is_not_promoted_to_whole_history_validation():
    state = {"status": "ready", "rows": 100, "backfill_complete": True}
    store, _ = fake_store(state, receipt())
    report = build_report(store, ("history",), "fixture-time")
    assert report["kinds"]["history"]["securities"]["last_page_received"] == 1
    assert report["kinds"]["history"]["metadata_terminal_receipt"] == 1
    assert report["last_page_received_meaning"] == (
        "logical_terminal_receipt_metadata_only_not_original_or_whole_history_validation"
    )
    assert report["independently_verified_complete_count"] is None


def test_unfinished_job_does_not_read_historical_series():
    store, calls = fake_store({"status": "collecting", "rows": 100, "next_before": "cursor"})
    report = build_report(store, ("history",), "fixture-time")
    assert report["kinds"]["history"]["securities"]["older_pages_pending"] == 1
    assert calls == []


def test_import_does_not_construct_store():
    module = runpy.run_path(str(Path(__file__).parents[1] / "ops/market/audit_toss_coverage.py"))
    assert "Store" not in module
