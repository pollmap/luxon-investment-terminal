"""fixture_non_production: old raw payloads must pass hash and company checks."""

import json

import pytest

from scripts.replay_dart_period_archives import replay
from services.market_dashboard.dart_periods import normalize_period_accounts
from services.market_dashboard.raw_archive import compact_one
from services.market_dashboard.store import Store


def test_replay_is_idempotent_and_keeps_existing_company_data(tmp_path):
    store = Store(url="sqlite://", root=tmp_path)
    store.put("universe", {"rows": [{"code": "005930"}]})
    store.put("official:005930", {"company": {"corp_code": "00126380", "ceo_nm": "fixture"}})
    rows = [
        {
            "stock_code": "005930",
            "corp_code": "00126380",
            "sj_div": "IS",
            "fs_div": "CFS",
            "account_nm": "매출액",
            "currency": "KRW",
            "rcept_no": "20260314000001",
            "thstrm_dt": "2025.01.01 ~ 2025.12.31",
            "thstrm_amount": "123,456",
        }
    ]
    store.raw(
        "dart",
        "https://opendart.fss.or.kr/api/fnlttMultiAcnt.json?bsns_year=2025",
        json.dumps({"status": "000", "list": rows}).encode(),
    )
    assert replay(store)["facts"] == 1 and "period_facts" not in store.get("official:005930")
    assert replay(store, apply=True)["facts"] == 1
    replay(store, apply=True)
    assert len(store.get("official:005930")["period_facts"]) == 1
    assert store.get("official:005930")["company"]["ceo_nm"] == "fixture"
    envelope = next((tmp_path / "raw" / "dart").glob("*.json"))
    document = json.loads(envelope.read_text(encoding="utf-8"))
    document["payload"] += " "
    envelope.write_text(json.dumps(document), encoding="utf-8")
    assert replay(store)["errors"]["archive_hash_mismatch"] == 1


def test_ytd_period_requires_ytd_amount():
    row = {
        "sj_div": "IS",
        "fs_div": "CFS",
        "account_nm": "매출액",
        "currency": "KRW",
        "rcept_no": "20260814000001",
        "thstrm_dt": "2026.01.01 ~ 2026.06.30",
        "thstrm_amount": "1",
    }
    assert normalize_period_accounts([row], "https://example.invalid", "fixture") == []
    row["thstrm_add_amount"] = "3"
    assert normalize_period_accounts([row], "https://example.invalid", "fixture")[0]["value"] == 3


def saved_period_archive(store, receipt="20260314000001", value="123,456"):
    row = {
        "stock_code": "005930",
        "corp_code": "00126380",
        "sj_div": "IS",
        "fs_div": "CFS",
        "account_nm": "매출액",
        "currency": "KRW",
        "rcept_no": receipt,
        "thstrm_dt": "2025.01.01 ~ 2025.12.31",
        "thstrm_amount": value,
    }
    digest = store.raw(
        "dart",
        "https://opendart.fss.or.kr/api/fnlttMultiAcnt.json"
        "?bsns_year=2025&crtfc_key=fixture_not_real_secret",
        json.dumps({"status": "000", "list": [row]}).encode(),
    )
    return store.root / "raw" / "dart" / (digest + ".json")


def test_replay_compressed_archives_keeps_facts_receipts_and_first_received_time(tmp_path):
    store = Store(url="sqlite://", root=tmp_path)
    store.put("universe", {"rows": [{"code": "005930"}]})
    store.put("official:005930", {"company": {"corp_code": "00126380"}})
    first = saved_period_archive(store)
    corrected = saved_period_archive(store, "20260414000001", "123,457")
    original_metadata = {path.stem: json.loads(path.read_bytes()) for path in (first, corrected)}
    before = replay(store, apply=True)
    facts_before = store.get("official:005930")["period_facts"]
    for path in (first, corrected):
        compact_one(path, opt_in_sources=("dart",))
    after = replay(store, apply=True)
    facts_after = store.get("official:005930")["period_facts"]
    assert {k: v for k, v in before.items() if k != "audited_at"} == {
        k: v for k, v in after.items() if k != "audited_at"
    }

    # Replay processing time changes; original receipt/publication evidence must not.
    def stable(facts):
        return [
            {
                **fact,
                "source_trace": {
                    k: v for k, v in fact["source_trace"].items() if k != "ingested_at"
                },
            }
            for fact in facts
        ]

    assert stable(facts_before) == stable(facts_after) and after["facts"] == 2
    assert {fact["filing_id"] for fact in facts_after} == {"20260314000001", "20260414000001"}
    assert {fact["source_trace"]["received_at"] for fact in facts_after} == {
        item["received_at"] for item in original_metadata.values()
    }
    assert "fixture_not_real_secret" not in json.dumps(facts_after)


@pytest.mark.parametrize("kind", ["compressed", "metadata", "empty", "bad_json"])
def test_bad_archive_does_not_erase_valid_fact_or_abort_other_archives(tmp_path, kind):
    store = Store(url="sqlite://", root=tmp_path)
    store.put("universe", {"rows": [{"code": "005930"}]})
    good = saved_period_archive(store)
    bad = saved_period_archive(store, "20260414000001", "999")
    if kind == "compressed":
        compact_one(bad, opt_in_sources=("dart",))
        (bad.parent / (bad.stem + ".bin.gz")).write_bytes(b"broken compressed fixture")
    elif kind == "metadata":
        bad.write_text(
            '{"url":"https://opendart.fss.or.kr/api/fnlttMultiAcnt.json",', encoding="utf8"
        )
    else:
        bad.unlink()
        content = b"" if kind == "empty" else b"not json fixture"
        store.raw("dart", "https://opendart.fss.or.kr/api/fnlttMultiAcnt.json", content)
    good_before = good.read_bytes()
    result = replay(store, apply=True)
    assert result["facts"] == 1 and result["errors"]
    assert len(store.get("official:005930")["period_facts"]) == 1
    assert good.read_bytes() == good_before
