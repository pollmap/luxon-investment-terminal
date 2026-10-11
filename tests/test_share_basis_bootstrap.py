"""fixture_non_production: first-issuer discovery never approves split units."""

import asyncio
import io
import zipfile
from types import SimpleNamespace

import pytest

from services.market_dashboard.fact_store import FactStore
from services.market_dashboard.share_basis_bootstrap import (
    bootstrap_target,
    describe_inputs,
    prepare_bootstrap,
    service_xbrl_intent,
)
from services.market_dashboard.store import Store
from services.market_dashboard.xbrl_facts import collect_xbrl

CODE, COMPANY = "000270", "00106641"
NS = "{https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full}"


def facts(periods=None):
    rows = []
    periods = periods or [
        ("2025-01-01", "2025-03-31"),
        ("2025-04-01", "2025-06-30"),
        ("2025-07-01", "2025-09-30"),
        ("2025-10-01", "2025-12-31"),
    ]
    for start, end in periods:
        for concept, value, unit in [
            ("ProfitLossAttributableToOwnersOfParent", "100", "{iso4217}KRW"),
            ("WeightedAverageShares", "10", "{http://www.xbrl.org/2003/instance}shares"),
            (
                "BasicEarningsLossPerShare",
                "10",
                "{iso4217}KRW/{http://www.xbrl.org/2003/instance}shares",
            ),
        ]:
            rows.append(
                {
                    "fact_id": concept + start,
                    "company_id": COMPANY,
                    "account_id": NS + concept,
                    "value": value,
                    "unit": unit,
                    "currency": "KRW",
                    "basis": "CFS",
                    "period_start": start,
                    "period_end": end,
                    "period_verified": True,
                    "dimensions": {NS + "ClassesOfShareCapitalAxis": NS + "OrdinarySharesMember"},
                    "filing_id": "20260314000001",
                    "published_at": "2026-03-14",
                    "per_share_basis": "stale_marker",
                    "source_trace": {"source": "fixture_non_production"},
                }
            )
    return rows


@pytest.fixture
def store(tmp_path):
    result = Store("sqlite://", tmp_path)
    result.put("universe", {"rows": [{"code": CODE, "name": "fixture", "market": "KOSPI"}]})
    result.put("dart-corporations", {"codes": {CODE: COMPANY}})
    FactStore(result)
    return result


def missing_report(store, receipt="20260312001224", company=COMPANY, state=None):
    digest = "a" * 64
    key = "financial-report:" + CODE + ":statement:2025:11011:CFS:" + digest
    store.put(
        key,
        {
            "rows": [
                {
                    "receipt": receipt,
                    "report_code": "11011",
                    "source_trace": {
                        "filing_id": digest,
                        "source_url": "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json?corp_code="
                        + company
                        + "&bsns_year=2025&reprt_code=11011&fs_div=CFS",
                    },
                }
            ]
        },
    )
    store.put(
        "official:" + CODE,
        {
            "financial_reports": {
                "2025:11011:CFS": {
                    "status": "ready",
                    "report_code": "11011",
                    "source_hash": digest,
                    "snapshot_key": key,
                    "year": "2025",
                }
            }
        },
    )
    store.put(
        "filing-index:" + CODE,
        {"rows": [{"receipt": receipt, "date": "20260312", "title": "사업보고서 (2025.12)"}]},
    )
    if state:
        store.put("xbrl-job:" + receipt, state)
    return receipt


def test_four_reconciled_quarters_only_create_review_work(store):
    FactStore(store).append(COMPANY, facts())
    task = prepare_bootstrap(store, CODE)
    assert task["status"] == "review_required"
    assert task["inputs"]["period_inputs_complete"]
    assert task["missing_proof"][0]["kind"] == "initial_share_unit_review"
    assert not store.get("share-basis:" + CODE)
    assert "verified" not in task and "value" not in task["inputs"]


def test_missing_real_periods_identified_without_guessing_quarter_codes():
    data = facts(
        [
            ("2025-01-01", "2025-03-31"),
            ("2025-04-01", "2025-06-30"),
            ("2026-01-01", "2026-03-31"),
            ("2026-04-01", "2026-06-30"),
        ]
    )
    for f in data:
        f["published_at"] = "2026-08-14"
    result = describe_inputs(data, COMPANY)
    assert not result["period_inputs_complete"]
    assert result["missing_period_ranges"] == [{"start": "2025-07-01", "end": "2025-12-31"}]
    assert result["candidate_window"] == {"start": "2025-07-01", "end": "2026-06-30"}


@pytest.mark.parametrize("damage", ["company", "dimension", "preferred", "unit", "profit"])
def test_unverified_inputs_never_create_complete_periods(damage):
    data = facts()
    for f in data:
        if damage == "company":
            f["company_id"] = "00000000"
        if damage == "dimension":
            f["dimensions"][NS + "SegmentsAxis"] = NS + "SegmentMember"
        if damage == "preferred":
            f["dimensions"][NS + "ClassesOfShareCapitalAxis"] = NS + "PreferenceSharesMember"
        if damage == "unit":
            f["unit"] = "unverified"
        if damage == "profit" and f["account_id"].endswith("OwnersOfParent"):
            f["value"] = "1000"
    assert not describe_inputs(data, COMPANY)["period_inputs_complete"]


def test_missing_source_report_connects_existing_queue_and_preserves_provider_failure(store):
    receipt = missing_report(
        store, state={"status": "error", "error": "DartProviderUnavailable", "retry_after": "2099"}
    )
    task = prepare_bootstrap(store, CODE)
    assert task["status"] == "awaiting_inputs"
    assert task["reports"][0]["error"] == "DartProviderUnavailable"
    assert store.get("xbrl-request:" + receipt)["report_code"] == "11011"
    assert store.get("xbrl-bootstrap-intent:" + receipt)["mode"] == "collect"
    assert store.get("xbrl-job:" + receipt)["retry_after"] == "2099"


def test_wrong_company_source_cannot_queue_missing_filing(store):
    receipt = missing_report(store, company="00000000")
    assert not prepare_bootstrap(store, CODE)["reports"]
    assert not store.get("xbrl-request:" + receipt)


def test_existing_request_identity_is_never_overwritten(store):
    receipt = missing_report(store)
    wrong = {"code": "005930", "report_code": "11011"}
    store.put("xbrl-request:" + receipt, wrong)
    assert (
        prepare_bootstrap(store, CODE)["reports"][0]["intent_status"] == "request_identity_conflict"
    )
    assert store.get("xbrl-request:" + receipt) == wrong
    assert not store.get("xbrl-bootstrap-intent:" + receipt)


def test_same_day_revision_skips_fact_scan_but_new_source_or_next_day_reopens(store, monkeypatch):
    import services.market_dashboard.share_basis_bootstrap as module

    first = prepare_bootstrap(store, CODE, day="2026-10-09")
    calls = []
    monkeypatch.setattr(module, "read_eps_facts", lambda *a: calls.append(True) or [])
    assert prepare_bootstrap(store, CODE, day="2026-10-09") == first and not calls
    store.put("official:" + CODE, {"new_filing": True})
    prepare_bootstrap(store, CODE, day="2026-10-09")
    prepare_bootstrap(store, CODE, day="2026-10-10")
    assert len(calls) == 2


def test_rotation_includes_new_alphanumeric_companies_and_excludes_reviewed_konex(store):
    store.put(
        "universe",
        {
            "rows": [
                {"code": c, "market": m}
                for c, m in [
                    (CODE, "KOSPI"),
                    ("0001A0", "KOSDAQ"),
                    ("005930", "KOSPI"),
                    ("123456", "KONEX"),
                ]
            ]
        },
    )
    store.put(
        "dart-corporations",
        {
            "codes": {
                CODE: COMPANY,
                "0001A0": "00000001",
                "005930": "00126380",
                "123456": "00000002",
            }
        },
    )
    targets = {bootstrap_target(store, {"005930"}, cursor) for cursor in range(10)}
    assert targets == {CODE, "0001A0"}
    assert prepare_bootstrap(store, "123456")["status"] == "out_of_scope"


def test_corrected_receipt_gets_separate_intent(store):
    first = missing_report(store)
    prepare_bootstrap(store, CODE)
    corrected = missing_report(store, receipt="20260916000001")
    prepare_bootstrap(store, CODE)
    assert store.get("xbrl-bootstrap-intent:" + first)
    assert store.get("xbrl-bootstrap-intent:" + corrected)


def test_intent_obeys_global_and_filing_backoff_and_only_uses_one_slot(store, monkeypatch):
    receipt = missing_report(store, state={"status": "error", "retry_after": "2099"})
    prepare_bootstrap(store, CODE)
    source = SimpleNamespace(store=store, dart="fixture_non_production")
    calls = []

    async def collect(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "ready"}

    monkeypatch.setattr("services.market_dashboard.xbrl_facts.collect_xbrl", collect)
    assert not asyncio.run(service_xbrl_intent(source))
    store.put("xbrl-job:" + receipt, {"status": "error"})
    store.put("dart-provider-retry", {"next_at": "2099"})
    assert not asyncio.run(service_xbrl_intent(source))
    store.put("dart-provider-retry", {})
    assert asyncio.run(service_xbrl_intent(source)) and len(calls) == 1
    assert not asyncio.run(service_xbrl_intent(source))
    assert store.get("xbrl-bootstrap-intent:" + receipt)["status"] == "processed"
    assert not store.get("share-basis:" + CODE)


@pytest.mark.parametrize("change", ["company", "publication", "raw_hash"])
def test_intent_revalidates_company_publication_and_archive(store, change):
    receipt = missing_report(store, state={"status": "ready", "raw_hash": "b" * 64})
    prepare_bootstrap(store, CODE)
    if change == "company":
        store.put("dart-corporations", {"codes": {CODE: "00000000"}})
    if change == "publication":
        store.put("filing-index:" + CODE, {"rows": [{"receipt": receipt, "date": "20260313"}]})
    if change == "raw_hash":
        store.put("xbrl-job:" + receipt, {"raw_hash": "c" * 64})
    assert not asyncio.run(
        service_xbrl_intent(SimpleNamespace(store=store, dart="fixture_non_production"))
    )


@pytest.mark.asyncio
async def test_successful_archive_can_replay_once_without_new_download(store):
    receipt = "20260312001224"
    url = "https://opendart.fss.or.kr/api/fnlttXbrl.xml?rcept_no=" + receipt + "&reprt_code=11011"
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as z:
        z.writestr("empty.xbrl", '<xbrl xmlns="http://www.xbrl.org/2003/instance"/>')
    digest = store.raw("dart-xbrl", url, content.getvalue())
    store.put(
        "xbrl-job:" + receipt, {"status": "ready", "publication": "20260312", "raw_hash": digest}
    )

    async def corporations():
        return {CODE: COMPANY}

    async def forbidden(*args, **kwargs):
        raise AssertionError("Do not redownload successful archive")

    source = SimpleNamespace(
        store=store, dart="fixture_non_production", corporations=corporations, _dart_get=forbidden
    )
    result = await collect_xbrl(
        source, CODE, receipt, "11011", publication="20260312", replay_revision="fixture-v1"
    )
    assert result["status"] == "unsupported_instance"
    assert result["bootstrap_replay_revision"] == "fixture-v1"
    (store.root / "raw/dart-xbrl" / (digest + ".bin")).write_bytes(b"corrupt")
    assert (
        await collect_xbrl(
            source, CODE, receipt, "11011", publication="20260312", replay_revision="fixture-v1"
        )
        == result
    )
    failed = await collect_xbrl(
        source, CODE, receipt, "11011", publication="20260312", replay_revision="fixture-v2"
    )
    assert failed["status"] == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("credentials", [None, "fixture_non_production"])
@pytest.mark.parametrize("source_received_at", ["2026-03-12T09:00:00+00:00", None])
async def test_local_replay_during_provider_backoff_never_calls_any_provider(
    store, credentials, source_received_at
):
    import json

    receipt = missing_report(store)
    source_url = (
        "https://opendart.fss.or.kr/api/fnlttXbrl.xml?rcept_no=" + receipt + "&reprt_code=11011"
    )
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr(
            "fixture.xbrl",
            """<xbrl xmlns="http://www.xbrl.org/2003/instance"
          xmlns:i="https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full"
          xmlns:d="http://xbrl.org/2006/xbrldi" xmlns:iso="http://www.xbrl.org/2003/iso4217">
          <context id="a"><entity><identifier scheme="fixture_non_production">00106641</identifier>
          <segment><d:explicitMember dimension="i:ConsolidatedAndSeparateFinancialStatementsAxis">
          i:ConsolidatedMember</d:explicitMember></segment></entity>
          <period><startDate>2025-01-01</startDate><endDate>2025-12-31</endDate></period></context>
          <unit id="won"><measure>iso:KRW</measure></unit>
          <i:Revenue contextRef="a" unitRef="won" decimals="0">123456789</i:Revenue></xbrl>""",
        )
    digest = store.raw("dart-xbrl", source_url, content.getvalue())
    envelope = store.root / "raw/dart-xbrl" / (digest + ".json")
    metadata = json.loads(envelope.read_bytes())
    if source_received_at:
        metadata["received_at"] = source_received_at
    else:
        metadata.pop("received_at", None)
    envelope.write_text(json.dumps(metadata), encoding="utf8")
    original_metadata = envelope.read_bytes()
    store.put(
        "xbrl-job:" + receipt, {"status": "ready", "publication": "20260312", "raw_hash": digest}
    )
    prepare_bootstrap(store, CODE)
    provider_error = {"next_at": "2099", "status": "800", "source_hash": "fixture_non_production"}
    store.put("dart-provider-retry", provider_error)

    async def forbidden(*args, **kwargs):
        pytest.fail("Local replay must not call provider or refresh issuer identities")

    source = SimpleNamespace(
        store=store, dart=credentials, corporations=forbidden, _dart_get=forbidden
    )
    assert await service_xbrl_intent(source)
    intent = store.get("xbrl-bootstrap-intent:" + receipt)
    assert intent["status"] == "processed" and intent["collector_status"] == "ready"
    facts = FactStore(store).read(COMPANY)
    assert len(facts) == 1 and facts[0]["value"] == "123456789"
    assert facts[0]["source_trace"]["sha256"] == digest
    assert facts[0]["source_trace"].get("received_at") == source_received_at
    assert facts[0]["source_trace"]["source_url"] == source_url
    assert facts[0]["published_at"] == "20260312"
    assert store.get("xbrl-job:" + receipt)["received_at"] != source_received_at
    assert envelope.read_bytes() == original_metadata
    assert (store.root / "raw/dart-xbrl" / (digest + ".bin")).read_bytes() == content.getvalue()
    assert store.get("dart-provider-retry") == provider_error
    assert not await service_xbrl_intent(source)
    assert not store.get("share-basis:" + CODE)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "hash", "metadata", "company"])
async def test_local_replay_failure_has_no_network_fallback(store, damage):
    import json

    receipt = missing_report(store)
    source_url = (
        "https://opendart.fss.or.kr/api/fnlttXbrl.xml?rcept_no=" + receipt + "&reprt_code=11011"
    )
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("empty.xbrl", '<xbrl xmlns="http://www.xbrl.org/2003/instance"/>')
    digest = store.raw("dart-xbrl", source_url, content.getvalue())
    store.put(
        "xbrl-job:" + receipt, {"status": "ready", "publication": "20260312", "raw_hash": digest}
    )
    prepare_bootstrap(store, CODE)
    directory = store.root / "raw/dart-xbrl"
    if damage == "missing":
        (directory / (digest + ".bin")).unlink()
    elif damage == "hash":
        (directory / (digest + ".bin")).write_bytes(b"fixture_corruption")
    elif damage == "metadata":
        envelope = directory / (digest + ".json")
        metadata = json.loads(envelope.read_bytes())
        metadata["url"] = "https://example.test/wrong"
        envelope.write_text(json.dumps(metadata), encoding="utf8")
    else:
        store.put("dart-corporations", {"codes": {CODE: "00000000"}})

    async def forbidden(*args, **kwargs):
        pytest.fail("No network fallback for missing or invalid archive evidence")

    source = SimpleNamespace(store=store, dart=None, corporations=forbidden, _dart_get=forbidden)
    await service_xbrl_intent(source)
    intent = store.get("xbrl-bootstrap-intent:" + receipt)
    assert intent["status"] == (
        "identity_conflict" if damage == "company" else "archive_review_required"
    )
    assert not await service_xbrl_intent(source)
    assert not store.get("share-basis:" + CODE)


def test_failed_archive_replay_waits_for_new_evidence_instead_of_retrying_forever(
    store, monkeypatch
):
    receipt = missing_report(store, state={"status": "ready", "raw_hash": "b" * 64})
    prepare_bootstrap(store, CODE)
    calls = []

    async def failed(*args, **kwargs):
        calls.append(True)
        return {"status": "error", "error": "ValueError"}

    monkeypatch.setattr("services.market_dashboard.xbrl_facts.collect_xbrl", failed)
    source = SimpleNamespace(store=store, dart="fixture_non_production")
    assert asyncio.run(service_xbrl_intent(source))
    assert not asyncio.run(service_xbrl_intent(source))
    assert calls == [True]
    assert store.get("xbrl-bootstrap-intent:" + receipt)["status"] == "archive_review_required"
