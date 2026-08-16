from __future__ import annotations

import csv
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from services.api.contracts import (
    ApiEnvelope,
    DataMode,
    DataState,
    DataStatus,
    FactValue,
    SourceTrace,
)
from services.api.database import postgres_enabled
from services.api.local_consensus_provider import local_forecast_evidence_from_csv
from services.api.postgres_provider import forecast_evidence_from_postgres

router = APIRouter(tags=["research-contracts"])

PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMPORTS_DIR = PROJECT_ROOT / "storage" / "imports"
PEER_IMPORT_FILENAME = "peer_relationships.csv"

ConsensusCaseName = Literal["low", "median", "high", "current"]
PeerKind = Literal["business", "valuation"]
ConsensusEvidenceKind = Literal["external_consensus", "manual_assumption", "mixed"]

_BLOCKED_CONSENSUS_TOKENS = {
    "ai-generated",
    "ai_generated",
    "chatgpt",
    "claude",
    "fast graphs",
    "fastgraphs",
    "fixture",
    "gemini",
    "llm",
    "mock",
    "sample",
    "demo",
    "placeholder",
    "proxy",
    "no_verified_consensus_snapshot",
}

_VALID_PEER_QUALITY_STATUSES = {
    "source_backed_peer_classification",
    "user_verified_peer_classification",
}

_PEER_REQUIRED_COLUMNS = {
    "company_id",
    "peer_company_id",
    "peer_name",
    "kind",
    "relationship",
    "source",
    "source_document_id",
    "filing_id",
    "period",
    "available_at",
    "unit",
    "currency",
    "method",
    "formula",
    "quality_status",
}


class ConsensusCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: ConsensusCaseName
    estimate_eps: FactValue
    growth_rate_pct: FactValue | None = None
    assumption_type: Literal["external_consensus", "manual_assumption"]
    quality_status: str


class ConsensusData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    metric_key: str = "adjusted_operating_eps"
    metric_name: str = "Adjusted Operating EPS"
    forecast_year: int
    provider: str
    evidence_kind: ConsensusEvidenceKind
    quality_status: str
    cases: list[ConsensusCase] = Field(min_length=1)


class PeerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    name: str
    relationship: str
    facts: list[FactValue] = Field(default_factory=list)
    source_trace: SourceTrace


class PeerData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    kind: PeerKind
    peers: list[PeerRecord]


class ProviderContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    label: str
    capabilities: list[str]
    contract_available: bool
    configured: bool
    verification: Literal["configuration_only", "contract_only", "not_available"]
    required_env: list[str] = Field(default_factory=list)
    state: DataState


class ProvidersData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderContract]


@router.get(
    "/api/v1/companies/{company_id}/consensus",
    response_model=ApiEnvelope[ConsensusData],
)
def company_consensus(company_id: str) -> ApiEnvelope[ConsensusData]:
    ticker = company_id.strip().upper()
    data, unavailable_status = _load_consensus_data(ticker)
    if data is None:
        reason = (
            "The configured consensus read model could not be reached; fixture forecast "
            "proxies are intentionally excluded."
            if unavailable_status == DataStatus.UPSTREAM_ERROR
            else (
                "No validated point-in-time consensus snapshot is available. "
                "Fixture forecast proxies are intentionally excluded."
            )
        )
        return ApiEnvelope[ConsensusData](
            data=None,
            state=DataState(
                status=unavailable_status,
                available=False,
                data_mode=DataMode.UNAVAILABLE,
                reason=reason,
            ),
            meta={
                "company_id": ticker,
                "required_source": "point_in_time_consensus_snapshot",
                "fixture_fallback_used": False,
            },
        )

    partial = data.quality_status.lower().startswith("partial") or len(data.cases) < 3
    return ApiEnvelope[ConsensusData](
        data=data,
        state=DataState(
            status=DataStatus.PARTIAL if partial else DataStatus.READY,
            available=True,
            data_mode=DataMode.SOURCE_BACKED,
            reason=(
                "Only validated source-backed cases are returned; unavailable cases are omitted."
                if partial
                else None
            ),
        ),
        meta={
            "company_id": ticker,
            "fixture_fallback_used": False,
        },
    )


@router.get(
    "/api/v1/companies/{company_id}/peers",
    response_model=ApiEnvelope[PeerData],
)
def company_peers(company_id: str, kind: PeerKind) -> ApiEnvelope[PeerData]:
    ticker = company_id.strip().upper()
    data, state = _load_peer_data(ticker, kind)
    return ApiEnvelope[PeerData](
        data=data,
        state=state,
        meta={
            "company_id": ticker,
            "kind": kind,
            "required_contract": "source_backed_peer_classification",
            "source_path": f"storage/imports/{PEER_IMPORT_FILENAME}",
            "fixture_fallback_used": False,
        },
    )


@router.get(
    "/api/v1/system/providers",
    response_model=ApiEnvelope[ProvidersData],
)
def system_providers() -> ApiEnvelope[ProvidersData]:
    return ApiEnvelope[ProvidersData](
        data=ProvidersData(providers=_provider_contracts()),
        state=DataState(
            status=DataStatus.CONFIGURED,
            available=True,
            data_mode=DataMode.CONFIGURATION_ONLY,
            reason=(
                "Provider states report contract and configuration presence only; they do not "
                "assert live reachability or source-row coverage."
            ),
        ),
        meta={
            "verification_scope": "configuration_only",
            "secret_values_exposed": False,
        },
    )


def _load_consensus_data(ticker: str) -> tuple[ConsensusData | None, DataStatus]:
    upstream_error = False
    providers = (
        ("postgres", forecast_evidence_from_postgres, (OSError, RuntimeError, SQLAlchemyError)),
        ("local_consensus_csv", local_forecast_evidence_from_csv, (OSError, ValueError)),
    )
    for provider_name, loader, handled_errors in providers:
        try:
            raw_payload = loader(ticker)
        except handled_errors:
            if provider_name == "postgres":
                upstream_error = True
            continue
        if raw_payload is None:
            continue
        try:
            return (
                _consensus_data_from_payload(ticker, provider_name, raw_payload),
                DataStatus.READY,
            )
        except (
            AttributeError,
            InvalidOperation,
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            # A malformed or provenance-incomplete row is unavailable, not a license to
            # substitute fixture numbers. A later validated provider may still satisfy it.
            continue
    return (
        None,
        DataStatus.UPSTREAM_ERROR if upstream_error else DataStatus.MISSING_SOURCE,
    )


def _consensus_data_from_payload(
    ticker: str,
    provider_name: str,
    payload: dict[str, Any],
) -> ConsensusData:
    forecast_year = int(payload["forecast_year"])
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("consensus payload requires at least one case")

    cases: list[ConsensusCase] = []
    seen_cases: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise ValueError("consensus case must be an object")
        case_name = str(raw_case.get("case") or "").strip().lower()
        if case_name not in {"low", "median", "high", "current"}:
            raise ValueError("unsupported consensus case")
        if case_name in seen_cases:
            raise ValueError("duplicate consensus case")
        seen_cases.add(case_name)

        estimate_value = _required_decimal(raw_case.get("estimate_eps"))
        source_trace = SourceTrace.model_validate(raw_case.get("source_trace"))
        _assert_consensus_trace(source_trace)
        assumption_type = _consensus_assumption_type(source_trace)
        quality_status = source_trace.quality_status or "source_backed_consensus_snapshots"

        growth_value = _optional_decimal(raw_case.get("growth_rate_pct"))
        growth_fact = None
        if growth_value is not None:
            growth_trace = source_trace.model_copy(
                deep=True,
                update={
                    "unit": "percent",
                    "formula": "growth_rate_pct supplied by the same consensus snapshot",
                },
            )
            growth_fact = FactValue(
                metric="consensus_adjusted_operating_eps_growth_rate_pct",
                value=growth_value,
                period=source_trace.period,
                unit="percent",
                currency=source_trace.currency,
                source_trace=growth_trace,
            )

        cases.append(
            ConsensusCase(
                case=case_name,
                estimate_eps=FactValue(
                    metric="consensus_adjusted_operating_eps",
                    value=estimate_value,
                    period=source_trace.period,
                    unit=source_trace.unit,
                    currency=source_trace.currency,
                    source_trace=source_trace,
                ),
                growth_rate_pct=growth_fact,
                assumption_type=assumption_type,
                quality_status=quality_status,
            )
        )

    case_order = {"low": 0, "median": 1, "current": 2, "high": 3}
    cases.sort(key=lambda row: case_order[row.case])
    evidence_kinds = {row.assumption_type for row in cases}
    evidence_kind: ConsensusEvidenceKind = (
        next(iter(evidence_kinds)) if len(evidence_kinds) == 1 else "mixed"
    )
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    quality_status = str(
        meta.get("quality_status")
        or payload.get("quality_status")
        or cases[0].quality_status
    )
    return ConsensusData(
        company_id=ticker,
        metric_key="adjusted_operating_eps",
        metric_name=str(payload.get("metric_name") or "Adjusted Operating EPS"),
        forecast_year=forecast_year,
        provider=provider_name,
        evidence_kind=evidence_kind,
        quality_status=quality_status,
        cases=cases,
    )


def _assert_consensus_trace(source_trace: SourceTrace) -> None:
    source_trace.assert_storage_ready()
    source_trace.assert_point_in_time_ready()
    evidence = _trace_evidence_text(source_trace)
    if any(token in evidence for token in _BLOCKED_CONSENSUS_TOKENS):
        raise ValueError("fixture or unverified consensus evidence is blocked")
    if bool(getattr(source_trace, "llm_generated_numbers", False)):
        raise ValueError("LLM-generated consensus values are blocked")


def _consensus_assumption_type(
    source_trace: SourceTrace,
) -> Literal["external_consensus", "manual_assumption"]:
    evidence = " ".join(
        [
            source_trace.source,
            source_trace.method,
            source_trace.quality_status or "",
            str(getattr(source_trace, "assumption_type", "")),
        ]
    ).lower()
    return "manual_assumption" if "manual" in evidence else "external_consensus"


def _required_decimal(value: Any) -> Decimal:
    if value is None or str(value).strip() == "":
        raise ValueError("consensus estimate value is required")
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError("consensus estimate value must be finite")
    return parsed


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError("consensus growth value must be finite")
    return parsed


def _load_peer_data(ticker: str, kind: PeerKind) -> tuple[PeerData | None, DataState]:
    path = IMPORTS_DIR / PEER_IMPORT_FILENAME
    if not path.is_file():
        return None, _missing_peer_contract_state()

    try:
        rows = _validated_peer_rows(path)
    except (OSError, ValueError, ValidationError):
        return None, _missing_peer_contract_state()

    matches = [
        row
        for row in rows
        if row["company_id"] == ticker and row["kind"] == kind
    ]
    if not matches:
        return None, DataState(
            status=DataStatus.MISSING_SOURCE,
            available=False,
            data_mode=DataMode.UNAVAILABLE,
            reason=(
                "The peer import contract is valid, but it has no source-backed rows for "
                f"company_id={ticker} and kind={kind}."
            ),
        )

    peers = [
        PeerRecord(
            company_id=row["peer_company_id"],
            name=row["peer_name"],
            relationship=row["relationship"],
            facts=[],
            source_trace=row["source_trace"],
        )
        for row in matches
    ]
    peers.sort(key=lambda row: row.company_id)
    return PeerData(company_id=ticker, kind=kind, peers=peers), DataState(
        status=DataStatus.READY,
        available=True,
        data_mode=DataMode.SOURCE_BACKED,
    )


def _validated_peer_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        missing_columns = sorted(_PEER_REQUIRED_COLUMNS - fieldnames)
        if missing_columns:
            raise ValueError(
                "peer import is missing required columns: " + ", ".join(missing_columns)
            )
        raw_rows = list(reader)
    if not raw_rows:
        raise ValueError("peer import requires at least one row")

    rows: list[dict[str, Any]] = []
    seen_relationships: set[tuple[str, str, str]] = set()
    for index, raw_row in enumerate(raw_rows, start=2):
        company_id = _required_text(raw_row.get("company_id"), "company_id", index).upper()
        peer_company_id = _required_text(
            raw_row.get("peer_company_id"), "peer_company_id", index
        ).upper()
        if company_id == peer_company_id:
            raise ValueError(f"row {index}: a company cannot be its own peer")
        kind = _required_text(raw_row.get("kind"), "kind", index).lower()
        if kind not in {"business", "valuation"}:
            raise ValueError(f"row {index}: kind must be business or valuation")
        relationship_key = (company_id, peer_company_id, kind)
        if relationship_key in seen_relationships:
            raise ValueError(f"row {index}: duplicate peer relationship")
        seen_relationships.add(relationship_key)

        quality_flags = [
            value.strip()
            for value in str(raw_row.get("quality_flags") or "").split(";")
            if value.strip()
        ]
        source_trace = SourceTrace.model_validate(
            {
                "source": _required_text(raw_row.get("source"), "source", index),
                "source_type": raw_row.get("source_type") or None,
                "source_document_id": _required_text(
                    raw_row.get("source_document_id"), "source_document_id", index
                ),
                "filing_id": _required_text(raw_row.get("filing_id"), "filing_id", index),
                "period": _required_text(raw_row.get("period"), "period", index),
                "available_at": _required_text(
                    raw_row.get("available_at"), "available_at", index
                ),
                "unit": _required_text(raw_row.get("unit"), "unit", index),
                "currency": _required_text(raw_row.get("currency"), "currency", index),
                "method": _required_text(raw_row.get("method"), "method", index),
                "formula": _required_text(raw_row.get("formula"), "formula", index),
                "confidence": raw_row.get("confidence") or "0",
                "quality_flags": quality_flags,
                "quality_status": _required_text(
                    raw_row.get("quality_status"), "quality_status", index
                ),
                "source_url": raw_row.get("source_url") or None,
                "llm_generated_numbers": False,
            }
        )
        _assert_peer_trace(source_trace)
        rows.append(
            {
                "company_id": company_id,
                "peer_company_id": peer_company_id,
                "peer_name": _required_text(raw_row.get("peer_name"), "peer_name", index),
                "kind": kind,
                "relationship": _required_text(
                    raw_row.get("relationship"), "relationship", index
                ),
                "source_trace": source_trace,
            }
        )
    return rows


def _assert_peer_trace(source_trace: SourceTrace) -> None:
    source_trace.assert_storage_ready()
    source_trace.assert_point_in_time_ready()
    if (source_trace.quality_status or "").lower() not in _VALID_PEER_QUALITY_STATUSES:
        raise ValueError("peer evidence requires a validated quality status")
    evidence = _trace_evidence_text(source_trace)
    if any(token in evidence for token in _BLOCKED_CONSENSUS_TOKENS):
        raise ValueError("fixture or unverified peer evidence is blocked")
    if bool(getattr(source_trace, "llm_generated_numbers", False)):
        raise ValueError("LLM-generated peer values are blocked")


def _trace_evidence_text(source_trace: SourceTrace) -> str:
    return " ".join(
        [
            source_trace.source,
            source_trace.source_type or "",
            source_trace.source_document_id or "",
            source_trace.filing_id or "",
            source_trace.source_url or "",
            source_trace.method,
            source_trace.quality_status or "",
            *source_trace.quality_flags,
        ]
    ).lower()


def _required_text(value: Any, field: str, row_number: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"row {row_number}: {field} is required")
    return normalized


def _missing_peer_contract_state() -> DataState:
    return DataState(
        status=DataStatus.MISSING_CONTRACT,
        available=False,
        data_mode=DataMode.UNAVAILABLE,
        reason=(
            "No validated peer-classification CSV is available; peer relationships are not "
            "inferred from fixtures or sector labels."
        ),
    )


def _provider_contracts() -> list[ProviderContract]:
    local_consensus_present = any(IMPORTS_DIR.glob("consensus*.csv"))
    fnguide_import_present = any(IMPORTS_DIR.glob("fnguide*.*"))
    peer_import_present = (IMPORTS_DIR / PEER_IMPORT_FILENAME).is_file()
    jquants_configured = _env_present("JQUANTS_REFRESH_TOKEN") or (
        _env_present("JQUANTS_EMAIL") and _env_present("JQUANTS_PASSWORD")
    )

    providers = [
        _configured_provider(
            "postgres",
            "PostgreSQL read model",
            ["financial_facts", "consensus_snapshots", "api_read_models"],
            postgres_enabled(),
            ["DATA_BACKEND", "DATABASE_URL"],
        ),
        _configured_provider(
            "opendart",
            "OpenDART",
            ["kr_filings", "kr_financial_statements"],
            _env_present("OPENDART_API_KEY") or _env_present("DART_API_KEY"),
            ["OPENDART_API_KEY", "DART_API_KEY"],
        ),
        _contract_only_provider(
            "pykrx",
            "pykrx",
            ["kr_prices", "kr_dividends"],
        ),
        _contract_only_provider(
            "finance_datareader_marcap",
            "FinanceDataReader / marcap",
            ["kr_market_cap", "kr_prices"],
        ),
        _configured_provider(
            "sec_edgar",
            "SEC EDGAR",
            ["us_filings", "us_financial_statements"],
            _env_present("SEC_USER_AGENT"),
            ["SEC_USER_AGENT"],
        ),
        _configured_provider(
            "fred",
            "FRED",
            ["macro_series", "recession_periods"],
            _env_present("FRED_API_KEY"),
            ["FRED_API_KEY"],
        ),
        _configured_provider(
            "ecos",
            "Bank of Korea ECOS",
            ["kr_macro_series"],
            _env_present("ECOS_API_KEY"),
            ["ECOS_API_KEY"],
        ),
        _configured_provider(
            "kosis",
            "KOSIS",
            ["kr_statistics"],
            _env_present("KOSIS_API_KEY"),
            ["KOSIS_API_KEY"],
        ),
        _configured_provider(
            "estat",
            "e-Stat",
            ["jp_statistics"],
            _env_present("ESTAT_APP_ID"),
            ["ESTAT_APP_ID"],
        ),
        _configured_provider(
            "edinet",
            "EDINET",
            ["jp_filings"],
            _env_present("EDINET_API_KEY"),
            ["EDINET_API_KEY"],
        ),
        _configured_provider(
            "jquants",
            "J-Quants",
            ["jp_market_data", "jp_financials"],
            jquants_configured,
            ["JQUANTS_REFRESH_TOKEN", "JQUANTS_EMAIL", "JQUANTS_PASSWORD"],
        ),
        _file_provider(
            "local_consensus_import",
            "Validated consensus CSV import",
            ["point_in_time_consensus_snapshots"],
            local_consensus_present,
            "storage/imports/consensus*.csv",
        ),
        _file_provider(
            "fnguide_user_export_import",
            "User-supplied FnGuide export import",
            ["licensed_user_export"],
            fnguide_import_present,
            "storage/imports/fnguide*.*",
        ),
        _missing_contract_provider(
            "fnguide_direct",
            "FnGuide direct integration",
            ["kr_consensus", "kr_company_snapshot"],
            "No licensed endpoint, credentials, or verified response contract is available.",
        ),
        (
            _file_provider(
                "peer_classification",
                "Validated peer classification CSV",
                ["business_peers", "valuation_peers"],
                True,
                f"storage/imports/{PEER_IMPORT_FILENAME}",
            )
            if peer_import_present
            else _missing_contract_provider(
                "peer_classification",
                "Peer classification dataset",
                ["business_peers", "valuation_peers"],
                "No validated peer-classification CSV contract is available.",
            )
        ),
    ]
    return providers


def _configured_provider(
    provider_id: str,
    label: str,
    capabilities: list[str],
    configured: bool,
    required_env: list[str],
) -> ProviderContract:
    return ProviderContract(
        provider_id=provider_id,
        label=label,
        capabilities=capabilities,
        contract_available=True,
        configured=configured,
        verification="configuration_only",
        required_env=required_env,
        state=(
            DataState(
                status=DataStatus.CONFIGURED,
                available=True,
                data_mode=DataMode.CONFIGURATION_ONLY,
                reason="Configuration is present; live reachability is not asserted.",
            )
            if configured
            else DataState(
                status=DataStatus.MISSING_KEY,
                available=False,
                data_mode=DataMode.UNAVAILABLE,
                reason="A required provider credential or configuration key is absent.",
            )
        ),
    )


def _contract_only_provider(
    provider_id: str,
    label: str,
    capabilities: list[str],
) -> ProviderContract:
    return ProviderContract(
        provider_id=provider_id,
        label=label,
        capabilities=capabilities,
        contract_available=True,
        configured=True,
        verification="contract_only",
        state=DataState(
            status=DataStatus.CONFIGURED,
            available=True,
            data_mode=DataMode.CONFIGURATION_ONLY,
            reason=(
                "A keyless connector contract exists; live reachability and stored source rows "
                "are not asserted."
            ),
        ),
    )


def _file_provider(
    provider_id: str,
    label: str,
    capabilities: list[str],
    configured: bool,
    expected_path: str,
) -> ProviderContract:
    return ProviderContract(
        provider_id=provider_id,
        label=label,
        capabilities=capabilities,
        contract_available=True,
        configured=configured,
        verification="configuration_only",
        state=(
            DataState(
                status=DataStatus.CONFIGURED,
                available=True,
                data_mode=DataMode.CONFIGURATION_ONLY,
                reason=(
                    f"A candidate file exists at {expected_path}; validation occurs when read."
                ),
            )
            if configured
            else DataState(
                status=DataStatus.MISSING_SOURCE,
                available=False,
                data_mode=DataMode.UNAVAILABLE,
                reason=f"No candidate source file exists at {expected_path}.",
            )
        ),
    )


def _missing_contract_provider(
    provider_id: str,
    label: str,
    capabilities: list[str],
    reason: str,
) -> ProviderContract:
    return ProviderContract(
        provider_id=provider_id,
        label=label,
        capabilities=capabilities,
        contract_available=False,
        configured=False,
        verification="not_available",
        state=DataState(
            status=DataStatus.MISSING_CONTRACT,
            available=False,
            data_mode=DataMode.UNAVAILABLE,
            reason=reason,
        ),
    )


def _env_present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


__all__ = [
    "ConsensusData",
    "PeerData",
    "ProviderContract",
    "ProvidersData",
    "router",
]
