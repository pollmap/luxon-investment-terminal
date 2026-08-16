from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from services.api import research_contracts
from services.api.contracts import ApiEnvelope, DataMode, DataState, DataStatus, FactValue
from services.api.main import app

client = TestClient(app)


def _consensus_trace(case: str = "median") -> dict:
    return {
        "source": "user_consensus_csv",
        "source_type": "user_consensus_csv",
        "source_document_id": f"consensus-005930-2027-{case}",
        "filing_id": f"consensus-005930-2027-{case}",
        "period": "FY2027E",
        "available_at": "2026-08-15T00:00:00+00:00",
        "unit": "KRW/share",
        "currency": "KRW",
        "method": "point_in_time_consensus_snapshot",
        "formula": "estimate value supplied by a validated consensus snapshot",
        "confidence": "0.9",
        "quality_flags": ["source_backed_consensus_snapshots"],
        "quality_status": "source_backed_consensus_snapshots",
        "estimate_case": case,
        "assumption_type": "external_consensus",
        "llm_generated_numbers": False,
    }


def _consensus_payload() -> dict:
    return {
        "ticker": "005930.KS",
        "forecast_year": 2027,
        "metric_name": "Adjusted Operating EPS",
        "cases": [
            {
                "case": "low",
                "growth_rate_pct": "3.0",
                "estimate_eps": "6100.25",
                "source_trace": _consensus_trace("low"),
            },
            {
                "case": "median",
                "growth_rate_pct": "5.0",
                "estimate_eps": "6400.50",
                "source_trace": _consensus_trace("median"),
            },
            {
                "case": "high",
                "growth_rate_pct": "7.0",
                "estimate_eps": "6700.75",
                "source_trace": _consensus_trace("high"),
            },
        ],
        "meta": {
            "data_mode": "source_backed",
            "quality_status": "source_backed_consensus_snapshots",
        },
    }


def test_fact_value_rejects_unsourced_non_null_number():
    with pytest.raises(ValidationError, match="source_trace"):
        FactValue(
            metric="consensus_adjusted_operating_eps",
            value=Decimal("1"),
            period="FY2027E",
            unit="KRW/share",
            currency="KRW",
        )


def test_fact_value_preserves_null_and_sourced_zero_as_distinct_values():
    missing = FactValue(metric="consensus_adjusted_operating_eps", value=None)
    sourced_zero = FactValue(
        metric="consensus_adjusted_operating_eps",
        value=Decimal("0"),
        period="FY2027E",
        unit="KRW/share",
        currency="KRW",
        source_trace=_consensus_trace(),
    )

    assert missing.value is None
    assert missing.source_trace is None
    assert sourced_zero.value == Decimal("0")
    assert sourced_zero.source_trace is not None


def test_api_envelope_rejects_ready_state_with_null_data():
    with pytest.raises(ValidationError, match="non-null data payload"):
        ApiEnvelope[dict](
            data=None,
            state=DataState(
                status=DataStatus.READY,
                available=True,
                data_mode=DataMode.SOURCE_BACKED,
            ),
        )


@pytest.mark.parametrize(
    ("status", "available", "data_mode", "data"),
    [
        (DataStatus.STALE, True, DataMode.SOURCE_BACKED, {"value": "old"}),
        (DataStatus.MISSING_KEY, False, DataMode.UNAVAILABLE, None),
        (DataStatus.RATE_LIMITED, False, DataMode.UNAVAILABLE, None),
        (DataStatus.UPSTREAM_ERROR, False, DataMode.UNAVAILABLE, None),
    ],
)
def test_extended_data_states_enforce_availability_and_nullability(
    status,
    available,
    data_mode,
    data,
):
    envelope = ApiEnvelope[dict](
        data=data,
        state=DataState(
            status=status,
            available=available,
            data_mode=data_mode,
            reason="explicit test reason",
        ),
    )

    assert envelope.state.status == status
    assert envelope.data == data


def test_extended_data_states_reject_incompatible_mode():
    with pytest.raises(ValidationError, match="requires data_mode=unavailable"):
        DataState(
            status=DataStatus.MISSING_KEY,
            available=False,
            data_mode=DataMode.SOURCE_BACKED,
            reason="key is absent",
        )


def test_consensus_returns_source_backed_fact_values(monkeypatch):
    monkeypatch.setattr(
        research_contracts,
        "forecast_evidence_from_postgres",
        lambda _ticker: _consensus_payload(),
    )
    monkeypatch.setattr(
        research_contracts,
        "local_forecast_evidence_from_csv",
        lambda _ticker: None,
    )

    response = client.get("/api/v1/companies/005930.ks/consensus")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == {
        "status": "ready",
        "available": True,
        "data_mode": "source_backed",
        "reason": None,
    }
    assert payload["data"]["company_id"] == "005930.KS"
    assert payload["data"]["provider"] == "postgres"
    assert payload["data"]["evidence_kind"] == "external_consensus"
    median = next(row for row in payload["data"]["cases"] if row["case"] == "median")
    assert median["estimate_eps"]["value"] == "6400.50"
    assert median["estimate_eps"]["source_trace"]["available_at"] == (
        "2026-08-15T00:00:00Z"
    )
    assert median["growth_rate_pct"]["value"] == "5.0"
    assert payload["meta"]["fixture_fallback_used"] is False


def test_consensus_fails_closed_when_source_is_missing_even_if_fixtures_are_allowed(
    monkeypatch,
):
    monkeypatch.setenv("ALLOW_FIXTURE_FALLBACK", "true")
    monkeypatch.setattr(
        research_contracts,
        "forecast_evidence_from_postgres",
        lambda _ticker: None,
    )
    monkeypatch.setattr(
        research_contracts,
        "local_forecast_evidence_from_csv",
        lambda _ticker: None,
    )

    response = client.get("/api/v1/companies/AAPL/consensus")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert payload["state"]["status"] == "missing_source"
    assert payload["state"]["available"] is False
    assert "Fixture forecast proxies are intentionally excluded" in payload["state"]["reason"]


def test_consensus_rejects_fixture_proxy_numbers(monkeypatch):
    fixture_payload = _consensus_payload()
    for row in fixture_payload["cases"]:
        row["source_trace"]["source_type"] = "forecast_snapshot_fixture"
        row["source_trace"]["quality_status"] = "fixture_non_production_consensus_proxy"
    monkeypatch.setattr(
        research_contracts,
        "forecast_evidence_from_postgres",
        lambda _ticker: fixture_payload,
    )
    monkeypatch.setattr(
        research_contracts,
        "local_forecast_evidence_from_csv",
        lambda _ticker: None,
    )

    response = client.get("/api/v1/companies/AAPL/consensus")

    assert response.status_code == 200
    assert response.json()["data"] is None
    assert response.json()["state"]["status"] == "missing_source"
    assert "6100.25" not in response.text


def test_consensus_reports_upstream_error_without_fixture_fallback(monkeypatch):
    def fail_postgres(_ticker):
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(
        research_contracts,
        "forecast_evidence_from_postgres",
        fail_postgres,
    )
    monkeypatch.setattr(
        research_contracts,
        "local_forecast_evidence_from_csv",
        lambda _ticker: None,
    )

    response = client.get("/api/v1/companies/005930.KS/consensus")

    assert response.status_code == 200
    assert response.json()["data"] is None
    assert response.json()["state"]["status"] == "upstream_error"


@pytest.mark.parametrize("kind", ["business", "valuation"])
def test_peers_returns_missing_contract_without_inferred_fixture_peers(kind):
    response = client.get(f"/api/v1/companies/005930.KS/peers?kind={kind}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert payload["state"]["status"] == "missing_contract"
    assert payload["meta"]["kind"] == kind
    assert payload["meta"]["required_contract"] == "source_backed_peer_classification"


def test_peers_rejects_unknown_kind():
    response = client.get("/api/v1/companies/005930.KS/peers?kind=sector")

    assert response.status_code == 422


def test_peers_returns_only_validated_source_traced_csv_rows(monkeypatch, tmp_path):
    peer_csv = "\n".join(
        [
            (
                "company_id,peer_company_id,peer_name,kind,relationship,source,"
                "source_type,source_document_id,filing_id,period,available_at,unit,"
                "currency,method,formula,confidence,quality_flags,quality_status,source_url"
            ),
            (
                "005930.KS,000660.KS,SK hynix,business,Memory semiconductor business,"
                "operator_peer_workpaper,user_verified_peer_workpaper,peer-doc-2026-08,"
                "peer-workpaper-2026-08,2026-08-15,2026-08-15T00:00:00+00:00,"
                "peer_relationship,not_applicable,user_verified_classification,"
                "peer relationship copied from a reviewed operator workpaper,0.9,"
                "source_backed_peer_classification,source_backed_peer_classification,"
                "https://example.com/peer-workpaper"
            ),
            (
                "005930.KS,000660.KS,SK hynix,valuation,Comparable semiconductor exposure,"
                "operator_peer_workpaper,user_verified_peer_workpaper,peer-doc-2026-08,"
                "peer-workpaper-2026-08,2026-08-15,2026-08-15T00:00:00+00:00,"
                "peer_relationship,not_applicable,user_verified_classification,"
                "peer relationship copied from a reviewed operator workpaper,0.9,"
                "source_backed_peer_classification,source_backed_peer_classification,"
                "https://example.com/peer-workpaper"
            ),
        ]
    )
    (tmp_path / research_contracts.PEER_IMPORT_FILENAME).write_text(
        peer_csv,
        encoding="utf-8",
    )
    monkeypatch.setattr(research_contracts, "IMPORTS_DIR", tmp_path)

    response = client.get("/api/v1/companies/005930.KS/peers?kind=business")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"]["status"] == "ready"
    assert payload["state"]["data_mode"] == "source_backed"
    assert payload["data"]["peers"][0]["company_id"] == "000660.KS"
    assert payload["data"]["peers"][0]["facts"] == []
    assert payload["data"]["peers"][0]["source_trace"]["source_document_id"] == (
        "peer-doc-2026-08"
    )


def test_valid_peer_contract_without_company_rows_returns_missing_source(
    monkeypatch,
    tmp_path,
):
    peer_csv = "\n".join(
        [
            (
                "company_id,peer_company_id,peer_name,kind,relationship,source,"
                "source_document_id,filing_id,period,available_at,unit,currency,method,"
                "formula,quality_status"
            ),
            (
                "000660.KS,005930.KS,Samsung Electronics,business,Memory semiconductor,"
                "operator_peer_workpaper,peer-doc,peer-filing,2026-08-15,"
                "2026-08-15T00:00:00+00:00,peer_relationship,not_applicable,"
                "user_verified_classification,reviewed operator classification,"
                "source_backed_peer_classification"
            ),
        ]
    )
    (tmp_path / research_contracts.PEER_IMPORT_FILENAME).write_text(
        peer_csv,
        encoding="utf-8",
    )
    monkeypatch.setattr(research_contracts, "IMPORTS_DIR", tmp_path)

    response = client.get("/api/v1/companies/005930.KS/peers?kind=business")

    assert response.status_code == 200
    assert response.json()["data"] is None
    assert response.json()["state"]["status"] == "missing_source"


def test_providers_exposes_configuration_state_without_secret_values(monkeypatch, tmp_path):
    secret_value = "must-not-appear-in-response"
    monkeypatch.setattr(research_contracts, "IMPORTS_DIR", tmp_path)
    monkeypatch.setattr(research_contracts, "postgres_enabled", lambda: False)
    monkeypatch.setenv("OPENDART_API_KEY", secret_value)
    for key in (
        "DART_API_KEY",
        "SEC_USER_AGENT",
        "FRED_API_KEY",
        "ECOS_API_KEY",
        "KOSIS_API_KEY",
        "ESTAT_APP_ID",
        "EDINET_API_KEY",
        "JQUANTS_REFRESH_TOKEN",
        "JQUANTS_EMAIL",
        "JQUANTS_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)

    response = client.get("/api/v1/system/providers")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"]["status"] == "configured"
    assert payload["state"]["data_mode"] == "configuration_only"
    providers = {
        row["provider_id"]: row for row in payload["data"]["providers"]
    }
    assert providers["opendart"]["configured"] is True
    assert providers["opendart"]["state"]["status"] == "configured"
    assert providers["postgres"]["state"]["status"] == "missing_key"
    assert providers["fnguide_direct"]["state"]["status"] == "missing_contract"
    assert providers["peer_classification"]["state"]["status"] == "missing_contract"
    assert secret_value not in response.text
    assert payload["meta"]["secret_values_exposed"] is False
