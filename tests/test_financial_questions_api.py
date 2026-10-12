"""fixture_non_production: isolated SQLite and HTTP route contracts."""

import importlib
from copy import deepcopy

from fastapi.testclient import TestClient

from services.market_dashboard.issuer_documents import report_dependencies
from services.market_dashboard.store import Store

module = importlib.import_module("services.market_dashboard.app")
NS = "{https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full}"
PERIODS = [
    ("2025-01-01", "2025-03-31", "2025-05-15"),
    ("2025-04-01", "2025-06-30", "2025-08-15"),
    ("2025-07-01", "2025-09-30", "2025-11-15"),
    ("2025-10-01", "2025-12-31", "2026-03-15"),
]


def original_facts(basis="CFS", multiplier=1):
    return [
        {
            "account_id": NS + account,
            "company_id": "fixture_issuer",
            "basis": basis,
            "currency": "KRW",
            "unit": "{iso}KRW",
            "kind": "duration",
            "period_verified": True,
            "period_start": start,
            "period_end": end,
            "value": str(amount * multiplier),
            "published_at": published,
            "dimensions": {},
            "fact_id": f"{basis}:{account}:{index}",
            "source_trace": {
                "source": "fixture_non_production",
                "source_document_id": "ab" * 32,
                "period": f"{start}/{end}",
                "available_at": published,
                "unit": "KRW",
            },
        }
        for index, (start, end, published) in enumerate(PERIODS)
        for account, amount in [
            ("Revenue", 100),
            ("ProfitLoss", 10),
            ("OperatingIncomeLoss", 15),
            ("CashFlowsFromUsedInOperatingActivities", 20),
        ]
    ]


def seeded(tmp_path, monkeypatch):
    store = Store("sqlite://", tmp_path)
    store.put(
        "universe",
        {
            "rows": [
                {"code": "005930", "name": "삼성전자", "market": "KOSPI"},
                {"code": "005935", "name": "삼성전자우", "market": "KOSPI"},
                {"code": "000660", "name": "SK하이닉스", "market": "KOSPI"},
            ]
        },
    )
    store.put("dart-corporations", {"codes": {"005930": "fixture_issuer", "000660": "other"}})
    store.put("official:005930", {"xbrl_facts": original_facts() + original_facts("OFS", 2)})
    store.put("official:005935", {"per_share_facts": [{"value": 7, "source": "own_fixture"}]})
    monkeypatch.setattr(module, "get_store", lambda: store)
    return store, TestClient(module.app)


def amount(response, metric):
    return next(row["value"] for row in response.json()["rows"] if row["metric"] == metric)


def test_cfs_ofs_422_and_unlisted_404(tmp_path, monkeypatch):
    _, client = seeded(tmp_path, monkeypatch)
    path = "/api/market/v1/securities/005930/analysis/financial-questions"
    cfs = client.get(path)
    assert cfs.status_code == 200 and cfs.json()["basis"] == "CFS"
    assert cfs.json()["scope"] == "company_financial_totals"
    assert amount(cfs, "operating_cash") == 80
    assert amount(client.get(path, params={"basis": "OFS"}), "operating_cash") == 160
    assert client.get(path, params={"basis": "mixed"}).status_code == 422
    assert (
        client.get("/api/market/v1/securities/999999/analysis/financial-questions").status_code
        == 404
    )
    assert (
        client.get("/api/market/v1/securities/bad/analysis/financial-questions").status_code == 422
    )


def test_preferred_gets_issuer_totals_without_copying_eps_or_dividends(tmp_path, monkeypatch):
    store, client = seeded(tmp_path, monkeypatch)
    path = "/api/market/v1/securities/005935/analysis/financial-questions"
    original = deepcopy(store.get("official:005935"))
    result = client.get(path)
    assert result.status_code == 200 and result.json()["code"] == "005935"
    assert result.json()["issuer_link"]["issuer_security_code"] == "005930"
    assert result.json()["issuer_link"]["scope"] == "company_financial_totals"
    assert amount(result, "net_income") == 40
    assert store.get("official:005935") == original
    assert store.get("events:005935") is None
    assert {"official:005930", "official:005935", "universe", "dart-corporations"} <= set(
        report_dependencies(store, "005935")
    )
    store.put(
        "dart-corporations", {"codes": {"005930": "fixture_issuer", "005935": "native_issuer"}}
    )
    withdrawn = client.get(path)
    assert "issuer_link" not in withdrawn.json()
    assert withdrawn.json()["status"] == "not_available"


def test_etag_tracks_issuer_revision_but_not_another_company(tmp_path, monkeypatch):
    store, client = seeded(tmp_path, monkeypatch)
    path = "/api/market/v1/securities/005935/analysis/financial-questions"
    first = client.get(path)
    etag = first.headers["etag"]
    assert client.get(path, headers={"If-None-Match": etag}).status_code == 304
    store.put("official:000660", {"company": {"name": "unrelated correction"}})
    assert client.get(path, headers={"If-None-Match": etag}).status_code == 304
    source = store.get("official:005930")
    source["xbrl_facts"].append(
        {
            **source["xbrl_facts"][13],
            "value": "25",
            "published_at": "2026-04-01",
            "fact_id": "correction-fact",
            "source_trace": {
                **source["xbrl_facts"][13]["source_trace"],
                "available_at": "2026-04-01",
            },
        }
    )
    store.put("official:005930", source)
    changed = client.get(path, headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag
    assert amount(changed, "net_income") == 55
    assert client.get(path, params={"basis": "OFS"}).headers["etag"] != changed.headers["etag"]


def test_different_listing_name_revokes_issuer_link_without_other_values(tmp_path, monkeypatch):
    store, client = seeded(tmp_path, monkeypatch)
    path = "/api/market/v1/securities/005935/analysis/financial-questions"
    assert amount(client.get(path), "operating_cash") == 80
    listings = store.get("universe")
    listings["rows"][1]["name"] = "different company"
    store.put("universe", listings)
    result = client.get(path)
    assert "issuer_link" not in result.json()
    assert all(row["value"] is None for row in result.json()["rows"])
