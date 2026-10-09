"""fixture_non_production: explicit reviewed note bindings and source replay."""

import asyncio
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from services.market_dashboard.eps_calculation import eps_inputs
from services.market_dashboard.share_basis_bootstrap_notes import (
    BINDINGS,
    collect_bootstrap_note,
    parse_reviewed_note,
)
from services.market_dashboard.store import Store


@pytest.fixture
def note():
    binding = deepcopy(BINDINGS[1])
    labels = binding["labels"]
    table = (
        "<table><tr><td></td><td>보통주</td></tr>"
        + "".join(
            "<tr><td>" + labels[role] + "</td><td>" + value + "</td></tr>"
            for role, value in [("profit", "10,000"), ("shares", "100"), ("eps", "100")]
        )
        + "</table>"
    )
    content = (
        "<html>fixture_non_production " + binding["title"] + " (단위 : 원)" + table * 2 + "</html>"
    ).encode()
    period = ("fixture_non_production 연결 손익계산서 " + binding["period_label"]).encode()
    binding.update(
        sha256=hashlib.sha256(content).hexdigest(), period_sha256=hashlib.sha256(period).hexdigest()
    )
    return binding, content, period


def test_reviewed_note_reconciles_period_units_and_never_sets_share_basis(note):
    binding, content, period = note
    facts = parse_reviewed_note(content, period, binding)
    assert len(facts) == 3
    for i, f in enumerate(facts):
        f["fact_id"] = str(i)
    result = eps_inputs(facts)
    assert len(result["rows"]) == 1 and result["rows"][0]["value"] == "100"
    assert result["rows"][0]["per_share_basis"] is None
    assert all(f["source_trace"]["period_source_sha256"] == binding["period_sha256"] for f in facts)


@pytest.mark.parametrize(
    "damage",
    [
        "hash",
        "period_hash",
        "receipt",
        "period_receipt",
        "title",
        "period",
        "scope",
        "unit",
        "preferred",
        "columns",
        "amount",
    ],
)
def test_source_or_semantic_changes_fail_closed(note, damage):
    binding, content, period = note
    if damage == "hash":
        content += b"changed"
    elif damage == "period_hash":
        period += b"changed"
    elif damage == "receipt":
        binding["url"] = binding["url"].replace(binding["filing_id"], "20260312001225")
    elif damage == "period_receipt":
        binding["period_url"] = binding["period_url"].replace(
            binding["filing_id"], "20260312001225"
        )
    elif damage == "title":
        binding["title"] = "99. 다른 주석 (연결)"
    elif damage == "period":
        binding["period_label"] = "2024.01.01 부터 2024.12.31 까지"
    else:
        before, after = {
            "scope": ("(연결)", "(별도)"),
            "unit": ("(단위 : 원)", "(단위 : 백만원)"),
            "preferred": ("보통주", "우선주"),
            "columns": ("<td>100</td>", "<td>100</td><td>100</td>"),
            "amount": ("10,000", "20,000"),
        }[damage]
        content = content.decode().replace(before, after).encode()
        # Semantic checks still apply if someone updates only a document hash.
        binding["sha256"] = hashlib.sha256(content).hexdigest()
    with pytest.raises(ValueError):
        parse_reviewed_note(content, period, binding)


@pytest.fixture
def context(tmp_path, note, monkeypatch):
    binding, content, period = note
    monkeypatch.setattr(
        "services.market_dashboard.share_basis_bootstrap_notes.BINDINGS", (binding,)
    )
    store = Store("sqlite://", tmp_path)
    store.put("universe", {"rows": [{"code": binding["code"], "market": "KOSPI"}]})
    store.put("dart-corporations", {"codes": {binding["code"]: binding["company_id"]}})
    store.put(
        "filing-index:" + binding["code"],
        {"rows": [{"receipt": binding["filing_id"], "date": "20260312"}]},
    )
    calls = []

    async def get(url, params):
        calls.append(url)
        return httpx.Response(
            200,
            content=content if url == binding["url"] else period,
            request=httpx.Request("GET", url),
        )

    return (
        SimpleNamespace(store=store, dart="fixture_non_production", _dart_get=get),
        binding,
        calls,
    )


def test_actual_collector_path_preserves_evidence_and_is_idempotent(context):
    source, binding, calls = context
    result = asyncio.run(collect_bootstrap_note(source, binding["code"]))
    assert result["status"] == "ready" and result["inserted"] == 3
    assert len(calls) == 2
    again = asyncio.run(collect_bootstrap_note(source, binding["code"]))
    assert again["status"] == "no_pending_reviewed_notes" and len(calls) == 2
    assert not source.store.get("share-basis:" + binding["code"])
    facts = source.store.get("official:" + binding["code"])["xbrl_facts"]
    assert all(f["source_trace"]["sha256"] == binding["sha256"] for f in facts)


@pytest.mark.parametrize("change", ["company", "publication", "correction", "scope"])
def test_collector_rechecks_company_publication_correction_and_scope(context, change):
    source, binding, calls = context
    if change == "company":
        source.store.put("dart-corporations", {"codes": {binding["code"]: "00000000"}})
    if change == "publication":
        source.store.put(
            "filing-index:" + binding["code"],
            {"rows": [{"receipt": binding["filing_id"], "date": "20260313"}]},
        )
    if change == "correction":
        source.store.put(
            "filing-index:" + binding["code"],
            {"rows": [{"receipt": "20260916000001", "date": "20260916"}]},
        )
    if change == "scope":
        source.store.put("universe", {"rows": [{"code": binding["code"], "market": "KONEX"}]})
    asyncio.run(collect_bootstrap_note(source, binding["code"]))
    assert not calls and not source.store.get("official:" + binding["code"])


def test_provider_failure_retains_old_facts_and_backs_off(context):
    source, binding, calls = context
    source.store.put("official:" + binding["code"], {"company": {"name": "unchanged"}})

    async def failed(*args, **kwargs):
        calls.append("failed")
        raise RuntimeError("never persist credential message")

    source._dart_get = failed
    result = asyncio.run(collect_bootstrap_note(source, binding["code"]))
    asyncio.run(collect_bootstrap_note(source, binding["code"]))
    assert result["error"] == "RuntimeError" and calls == ["failed"]
    assert source.store.get("official:" + binding["code"]) == {"company": {"name": "unchanged"}}
    assert "credential" not in str(result)


def test_provider_backoff_does_not_turn_maintenance_into_missing_source(context):
    source, binding, calls = context
    source.store.put("dart-provider-retry", {"next_at": "2099"})
    assert (
        asyncio.run(collect_bootstrap_note(source, binding["code"]))["status"]
        == "provider_retry_wait"
    )
    assert not calls and not source.store.get("bootstrap-eps-note:" + binding["filing_id"])


@pytest.mark.parametrize("archive_only", [False, True])
@pytest.mark.parametrize("guard", ["credentials", "provider", "filing"])
def test_complete_archives_replay_without_network_despite_external_guards(
    context, note, archive_only, guard
):
    source, binding, calls = context
    _, content, period = note
    source.store.raw("dart-bootstrap-eps", binding["url"], content)
    source.store.raw("dart-bootstrap-eps", binding["period_url"], period)
    if guard == "credentials":
        source.dart = ""
    if guard == "provider":
        source.store.put("dart-provider-retry", {"next_at": "2099"})
    if guard == "filing":
        source.store.put(
            "bootstrap-eps-note:" + binding["filing_id"],
            {"status": "error", "error": "DartProviderUnavailable", "retry_after": "2099"},
        )
    result = asyncio.run(collect_bootstrap_note(source, binding["code"], archive_only=archive_only))
    assert result["status"] == "ready" and result["inserted"] == 3
    assert not calls and not source.store.get("share-basis:" + binding["code"])


@pytest.mark.parametrize("cached", [0, 1])
def test_archive_only_missing_document_cannot_fetch_or_append_partial_facts(context, note, cached):
    source, binding, calls = context
    if cached:
        source.store.raw("dart-bootstrap-eps", binding["url"], note[1])
    result = asyncio.run(collect_bootstrap_note(source, binding["code"], archive_only=True))
    assert result["status"] == "archive_missing"
    assert result["sha256"] == binding["period_sha256" if cached else "sha256"]
    assert not calls and not source.store.get("official:" + binding["code"])


@pytest.mark.parametrize("damage", ["hash", "url", "envelope_hash", "missing_envelope"])
def test_offline_archive_identity_failure_does_not_fallback_to_network(context, note, damage):
    source, binding, calls = context
    source.store.raw("dart-bootstrap-eps", binding["url"], note[1])
    source.store.raw("dart-bootstrap-eps", binding["period_url"], note[2])
    folder = source.store.root / "raw/dart-bootstrap-eps"
    envelope = folder / (binding["sha256"] + ".json")
    if damage == "hash":
        (folder / (binding["sha256"] + ".bin")).write_bytes(b"changed")
    elif damage == "missing_envelope":
        envelope.unlink()
    else:
        metadata = json.loads(envelope.read_bytes())
        metadata["url" if damage == "url" else "sha256"] = "changed"
        envelope.write_text(json.dumps(metadata), encoding="utf-8")
    result = asyncio.run(collect_bootstrap_note(source, binding["code"], archive_only=True))
    assert result["status"] == "error"
    assert not calls and not source.store.get("official:" + binding["code"])


def test_missing_archive_still_requires_credentials(context):
    source, binding, calls = context
    source.dart = ""
    assert (
        asyncio.run(collect_bootstrap_note(source, binding["code"]))["status"]
        == "credentials_missing"
    )
    assert not calls
