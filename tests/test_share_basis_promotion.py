"""fixture_non_production: only explicit archived first-issuer reviews promote."""

import json
from copy import deepcopy

import pytest
from test_share_basis_bootstrap import facts

from services.market_dashboard.fact_store import FactStore
from services.market_dashboard.share_basis_bootstrap import describe_inputs, read_eps_facts
from services.market_dashboard.share_basis_promotion import (
    PROOFS,
    process_one_review,
    promote_initial_review,
    queue_initial_review,
)
from services.market_dashboard.share_basis_worker import validate_page
from services.market_dashboard.store import Store

CODE, COMPANY = "000270", "00106641"


@pytest.fixture
def context(tmp_path):
    store = Store("sqlite://", tmp_path)
    store.put("universe", {"rows": [{"code": CODE, "market": "KOSPI"}]})
    store.put("dart-corporations", {"codes": {CODE: COMPANY}})
    rows = facts(
        periods=[
            ("2024-01-01", "2024-03-31"),
            ("2024-04-01", "2024-06-30"),
            ("2024-07-01", "2024-09-30"),
            ("2024-10-01", "2024-12-31"),
        ]
    )
    for row in rows:
        row.update(published_at="2025-03-14", filing_id="20250314000001")
    FactStore(store).append(COMPANY, rows)
    inputs = describe_inputs(read_eps_facts(store, COMPANY), COMPANY)
    store.put(
        "share-basis-bootstrap:" + CODE,
        {
            "company_id": COMPANY,
            "status": "review_required",
            "inputs": inputs,
        },
    )
    url = "https://example.test/reviewed-issuer-document"
    digest = store.raw("fixture-initial-review", url, b"fixture_non_production reviewed source")
    proof = {
        "raw_source": "fixture-initial-review",
        "source_url": url,
        "sha256": digest,
        "excerpt": "fixture_non_production reviewed source",
    }
    query = {
        "corp_code": COMPANY,
        "bgn_de": "20240101",
        "end_de": "20250331",
        "pblntf_ty": "I",
        "last_reprt_at": "N",
        "page_no": "1",
        "page_count": "100",
    }
    payload = {"status": "013", "list": [], "total_count": 0}
    page_hash = store.raw(
        "dart-share-window",
        "https://opendart.fss.or.kr/api/list.json",
        json.dumps(payload).encode(),
    )
    page, _ = validate_page(payload, query, page_hash)
    review = {
        "status": "reviewed",
        "share_class": "ordinary",
        "security_code": CODE,
        "company_id": COMPANY,
        "reviewer": "fixture-reviewer",
        "reason": "fixture explicit decision",
        "reviewed_at": "2025-04-01T00:00:00+00:00",
        "input_fact_ids": inputs["input_fact_ids"],
        "evidence": {
            "verified": True,
            "no_split_interval": True,
            "basis_id": "fixture-unit-v1",
            "security_code": CODE,
            "company_id": COMPANY,
            "start": "2024-01-01",
            "end": "2025-03-31",
            "source_trace": {"source_url": url, "sha256": digest},
        },
        "proofs": {name: deepcopy(proof) for name in PROOFS},
        "disclosure_window": {
            "company_id": COMPANY,
            "begin": "20240101",
            "end": "2025-03-31",
            "pages": [page],
        },
    }
    return store, review


def test_discovery_does_not_promote_but_explicit_review_replays_all_archives(context):
    store, review = context
    assert process_one_review(store)["status"] == "idle"
    assert store.get("share-basis:" + CODE) is None
    key = queue_initial_review(store, CODE, review)
    assert store.get("share-basis:" + CODE) is None
    assert process_one_review(store)["status"] == "promoted"
    basis = store.get("share-basis:" + CODE)
    assert basis["initial_review_id"] == key and basis["basis_id"] == "fixture-unit-v1"
    assert basis["initial_disclosure_review"]["page_statuses"] == ["013"]
    assert process_one_review(store)["status"] == "idle"


@pytest.mark.parametrize(
    "change",
    [
        {"status": "candidate"},
        {"share_class": "preferred"},
        {"reviewer": ""},
        {"company_id": "00999999"},
        {"proofs": {}},
    ],
)
def test_missing_explicit_approval_or_identity_never_creates_basis(context, change):
    store, review = context
    with pytest.raises(ValueError):
        queue_initial_review(store, CODE, {**review, **change})
    assert store.get("share-basis:" + CODE) is None


def test_changed_input_requires_new_review_instead_of_automatic_retry(context):
    store, review = context
    queue_initial_review(store, CODE, review)
    store.put("official:" + CODE, {"new_version": True})
    assert process_one_review(store)["status"] == "inputs_changed"
    assert store.get("share-basis:" + CODE) is None
    assert store.get("share-basis-promotion:" + CODE)["status"] == "review_required"
    assert process_one_review(store)["status"] == "idle"


def test_changed_archived_bytes_cannot_pass_replay(context):
    store, review = context
    queue_initial_review(store, CODE, review)
    proof = review["proofs"]["issuer_share_history"]
    (store.root / "raw" / proof["raw_source"] / (proof["sha256"] + ".bin")).write_bytes(b"changed")
    assert process_one_review(store)["status"] == "review_required"
    assert store.get("share-basis:" + CODE) is None


def test_archive_hash_does_not_approve_an_invented_excerpt(context):
    store, review = context
    review["proofs"]["ordinary_share_identity"]["excerpt"] = "No stock splits occurred"
    with pytest.raises(ValueError, match="excerpt is absent"):
        queue_initial_review(store, CODE, review)
    assert store.get("share-basis-promotion:" + CODE) is None
    assert store.get("share-basis:" + CODE) is None


@pytest.mark.parametrize("encoding", ["utf-8", "cp949"])
def test_reviewed_html_passage_uses_declared_encoding_and_visible_text(context, encoding):
    store, review = context
    proof = review["proofs"]["ordinary_share_identity"]
    content = "<table><tr><td>보통주</td><td>주식 수 100</td></tr></table>"
    proof["sha256"] = store.raw(proof["raw_source"], proof["source_url"], content.encode(encoding))
    proof.update(excerpt="보통주  주식 수 100", text_encoding=encoding)
    queue_initial_review(store, CODE, review)
    assert process_one_review(store)["status"] == "promoted"


@pytest.mark.parametrize("content", [
    b'<script>No splits</script><p>Other text</p>',
    b'<style>No splits</style><p>Other text</p>',
    b'<template>No splits</template><p>Other text</p>',
])
def test_hidden_html_is_not_a_reviewed_passage(context, content):
    store, review = context
    proof = review["proofs"]["ordinary_share_identity"]
    proof["sha256"] = store.raw(proof["raw_source"], proof["source_url"], content)
    proof["excerpt"] = "No splits"
    with pytest.raises(ValueError, match="excerpt is absent"):
        queue_initial_review(store, CODE, review)


def test_review_does_not_guess_a_lossy_text_encoding(context):
    store, review = context
    proof = review["proofs"]["ordinary_share_identity"]
    proof["sha256"] = store.raw(proof["raw_source"], proof["source_url"], "보통주".encode("cp949"))
    proof["excerpt"] = "보통주"
    with pytest.raises(ValueError, match="explicit text encoding"):
        queue_initial_review(store, CODE, review)


@pytest.mark.parametrize("excerpt", [1, True, [], None])
def test_review_excerpt_requires_an_explicit_text_passage(context, excerpt):
    store, review = context
    review["proofs"]["ordinary_share_identity"]["excerpt"] = excerpt
    with pytest.raises(ValueError, match="Explicit archived proof"):
        queue_initial_review(store, CODE, review)


def test_review_text_encoding_must_be_in_the_small_supported_set(context):
    store, review = context
    review["proofs"]["ordinary_share_identity"]["text_encoding"] = "utf-16"
    with pytest.raises(ValueError, match="encoding is unsupported"):
        queue_initial_review(store, CODE, review)


def test_credential_query_cannot_be_preserved_as_public_proof(context):
    store, review = context
    review["proofs"]["issuer_share_history"]["source_url"] += "?crtfc_key=fixture-not-a-secret"
    with pytest.raises(ValueError, match="Explicit archived proof"):
        queue_initial_review(store, CODE, review)
    assert store.get("share-basis-promotion:" + CODE) is None


def test_existing_basis_is_preserved_and_never_overwritten(context):
    store, review = context
    queue_initial_review(store, CODE, review)
    store.put("share-basis:" + CODE, {"basis_id": "existing-review"})
    assert promote_initial_review(store, CODE)["status"] == "existing_basis_preserved"
    assert store.get("share-basis:" + CODE) == {"basis_id": "existing-review"}


def test_incomplete_disclosure_or_period_inputs_cannot_be_approved(context):
    store, review = context
    bad = deepcopy(review)
    bad["disclosure_window"]["pages"] = []
    with pytest.raises(ValueError):
        queue_initial_review(store, CODE, bad)
    store.put(
        "share-basis-bootstrap:" + CODE,
        {"company_id": COMPANY, "inputs": {"period_inputs_complete": False}},
    )
    with pytest.raises(ValueError, match="Continuous"):
        queue_initial_review(store, CODE, review)


def test_split_window_requires_separate_split_unit_review(context):
    store, review = context
    review["evidence"]["splits"] = [{"effective_date": "2024-06-01"}]
    with pytest.raises(ValueError, match="separate review"):
        queue_initial_review(store, CODE, review)
