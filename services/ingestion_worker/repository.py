from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from backend.normalize.schemas import (
    AdjustedEarningsRecord,
    AdjustmentRecord,
    SourceDocument,
    SourceTrace,
)
from services.api.database import get_engine, postgres_enabled


@dataclass(frozen=True)
class StoredSecurity:
    id: uuid.UUID
    ticker: str
    currency: str


class IngestionRepository:
    def __init__(self) -> None:
        if not postgres_enabled():
            raise RuntimeError("DATA_BACKEND=postgres and DATABASE_URL are required for DB ingestion")
        self.engine = get_engine()

    def start_run(self, market: str, source: str, ticker: str, metadata: dict[str, Any] | None = None) -> uuid.UUID:
        run_id = uuid.uuid4()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO ingestion_runs (id, market, source, ticker, status, started_at, metadata)
                    VALUES (:id, :market, :source, :ticker, 'running', :started_at, CAST(:metadata AS jsonb))
                    """
                ),
                {
                    "id": run_id,
                    "market": market,
                    "source": source,
                    "ticker": ticker.upper(),
                    "started_at": datetime.now(UTC),
                    "metadata": _json(metadata or {}),
                },
            )
        return run_id

    def finish_run(self, run_id: uuid.UUID, status: str = "succeeded", error_summary: str | None = None) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE ingestion_runs
                    SET status = :status, finished_at = :finished_at, error_summary = :error_summary
                    WHERE id = :id
                    """
                ),
                {
                    "id": run_id,
                    "status": status,
                    "finished_at": datetime.now(UTC),
                    "error_summary": error_summary,
                },
            )

    def ensure_security(self, ticker: str, name: str, country: str, currency: str, exchange: str | None = None) -> StoredSecurity:
        ticker = ticker.upper()
        with self.engine.begin() as connection:
            existing = connection.execute(
                text("SELECT id, ticker, currency FROM securities WHERE ticker = :ticker"),
                {"ticker": ticker},
            ).mappings().first()
            if existing:
                return StoredSecurity(id=existing["id"], ticker=existing["ticker"], currency=existing["currency"])
            company_id = uuid.uuid4()
            security_id = uuid.uuid4()
            connection.execute(
                text("INSERT INTO companies (id, name, country, created_at) VALUES (:id, :name, :country, :created_at)"),
                {"id": company_id, "name": name, "country": country, "created_at": datetime.now(UTC)},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO securities (id, company_id, ticker, exchange, currency)
                    VALUES (:id, :company_id, :ticker, :exchange, :currency)
                    """
                ),
                {
                    "id": security_id,
                    "company_id": company_id,
                    "ticker": ticker,
                    "exchange": exchange,
                    "currency": currency,
                },
            )
        return StoredSecurity(id=security_id, ticker=ticker, currency=currency)

    def store_source_document(self, security_id: uuid.UUID | None, document: SourceDocument, source_type: str) -> uuid.UUID:
        document_id = uuid.uuid4()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO source_documents (
                      id, security_id, source_type, accession_number, form_type, filing_url,
                      source_url, content_hash, local_path, filed_at, accepted_at, metadata
                    )
                    VALUES (
                      :id, :security_id, :source_type, :accession_number, :form_type, :filing_url,
                      :source_url, :content_hash, :local_path, :filed_at, :accepted_at, CAST(:metadata AS jsonb)
                    )
                    """
                ),
                {
                    "id": document_id,
                    "security_id": security_id,
                    "source_type": source_type,
                    "accession_number": document.accession_number,
                    "form_type": document.form_type,
                    "filing_url": document.filing_url,
                    "source_url": document.source_url,
                    "content_hash": document.content_hash or document.id,
                    "local_path": document.local_path,
                    "filed_at": document.filed_at,
                    "accepted_at": document.accepted_at,
                    "metadata": _json(document.metadata),
                },
            )
        return document_id

    def store_adjusted_earnings(
        self,
        security_id: uuid.UUID,
        source_document_id: uuid.UUID | None,
        record: AdjustedEarningsRecord,
    ) -> uuid.UUID:
        source_trace = _storage_ready_trace(
            record.source_trace,
            source=_enum_value(record.method),
            source_document_id=source_document_id,
            filing_id=_record_accession_number(record),
            form=record.source_trace.form or record.source_trace.form_type,
            period=f"{record.fiscal_period}{record.fiscal_year}",
            unit="per_share",
            currency=record.currency or "N/A",
            method=_enum_value(record.method),
            formula=record.formula,
            quality_status=_enum_value(record.quality_status),
        )
        with self.engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO adjusted_earnings (
                      id, security_id, source_document_id, fiscal_year, fiscal_period, period_start, period_end,
                      filed_at, accepted_at, accession_number, form_type, gaap_ni, gaap_eps_diluted,
                      adjusted_ni, adjusted_eps, company_adjusted_eps, diluted_shares, currency, scale,
                      method, policy, exclude_sbc, exclude_acquired_intangible_amortization, sector_policy,
                      confidence, quality_status, flags, warnings, formula, source_url, filing_url,
                      computed_at, parser_version, metadata, source_trace, created_at, updated_at
                    )
                    VALUES (
                      :id, :security_id, :source_document_id, :fiscal_year, :fiscal_period, :period_start, :period_end,
                      :filed_at, :accepted_at, :accession_number, :form_type, :gaap_ni, :gaap_eps_diluted,
                      :adjusted_ni, :adjusted_eps, :company_adjusted_eps, :diluted_shares, :currency, :scale,
                      :method, :policy, :exclude_sbc, :exclude_acquired_intangible_amortization, :sector_policy,
                      :confidence, :quality_status, CAST(:flags AS jsonb), CAST(:warnings AS jsonb), :formula, :source_url, :filing_url,
                      :computed_at, :parser_version, CAST(:metadata AS jsonb), CAST(:source_trace AS jsonb), :created_at, :updated_at
                    )
                    ON CONFLICT ON CONSTRAINT uq_adjusted_earnings_version DO UPDATE SET
                      adjusted_eps = EXCLUDED.adjusted_eps,
                      adjusted_ni = EXCLUDED.adjusted_ni,
                      confidence = EXCLUDED.confidence,
                      quality_status = EXCLUDED.quality_status,
                      flags = EXCLUDED.flags,
                      warnings = EXCLUDED.warnings,
                      source_trace = EXCLUDED.source_trace,
                      updated_at = EXCLUDED.updated_at
                    RETURNING id
                    """
                ),
                {
                    "id": record.id,
                    "security_id": security_id,
                    "source_document_id": source_document_id,
                    "fiscal_year": record.fiscal_year,
                    "fiscal_period": record.fiscal_period,
                    "period_start": record.period_start,
                    "period_end": record.period_end,
                    "filed_at": record.source_trace.filed_at,
                    "accepted_at": record.source_trace.accepted_at,
                    "accession_number": _record_accession_number(record),
                    "form_type": record.source_trace.form_type,
                    "gaap_ni": record.gaap_ni,
                    "gaap_eps_diluted": record.gaap_eps_diluted,
                    "adjusted_ni": record.adjusted_ni,
                    "adjusted_eps": record.adjusted_eps,
                    "company_adjusted_eps": record.company_adjusted_eps,
                    "diluted_shares": record.diluted_shares,
                    "currency": record.currency,
                    "scale": record.scale,
                    "method": _enum_value(record.method),
                    "policy": record.policy,
                    "exclude_sbc": record.exclude_sbc,
                    "exclude_acquired_intangible_amortization": record.exclude_acquired_intangible_amortization,
                    "sector_policy": _enum_value(record.sector_policy),
                    "confidence": record.confidence,
                    "quality_status": _enum_value(record.quality_status),
                    "flags": _json(record.flags),
                    "warnings": _json(record.warnings),
                    "formula": record.formula,
                    "source_url": record.source_trace.source_url,
                    "filing_url": record.source_trace.filing_url,
                    "computed_at": record.computed_at,
                    "parser_version": record.parser_version,
                    "metadata": _json(record.metadata),
                    "source_trace": _json(source_trace),
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                },
            ).mappings().one()
            return row["id"]

    def delete_adjustments_for(self, adjusted_earnings_id: uuid.UUID) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM adjustments WHERE adjusted_earnings_id = :adjusted_earnings_id"),
                {"adjusted_earnings_id": adjusted_earnings_id},
            )

    def store_adjustment(
        self,
        security_id: uuid.UUID,
        source_document_id: uuid.UUID | None,
        adjusted_earnings_id: uuid.UUID | None,
        record: AdjustmentRecord,
    ) -> None:
        source_trace = _storage_ready_trace(
            record.source_trace,
            source=_enum_value(record.source),
            source_document_id=source_document_id,
            filing_id=record.source_trace.filing_id
            or record.source_trace.accession_number
            or f"{_enum_value(record.source)}:{record.fiscal_year}:{record.fiscal_period}",
            form=record.source_trace.form or record.source_trace.form_type,
            period=f"{record.fiscal_period}{record.fiscal_year}",
            unit=record.raw_unit or record.currency or "amount",
            currency=record.currency or "N/A",
            method=_enum_value(record.source),
            formula="Adjustment line from adjusted earnings normalization bridge",
            quality_status="source_backed_adjustment",
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO adjustments (
                      id, security_id, source_document_id, adjusted_earnings_id, fiscal_year, fiscal_period,
                      period_start, period_end, filed_at, accepted_at, accession_number, form_type,
                      item_label, normalized_label, canonical_category, gaap_tag, raw_value, raw_unit,
                      currency, scale, amount_basis, pretax_amount, tax_effect, after_tax_impact,
                      sign, recurring_flag, asymmetric_flag, tax_flag, policy_included, source,
                      source_url, filing_url, table_hash, row_hash, parser_version, confidence,
                      warnings, metadata, source_trace, created_at, updated_at
                    )
                    VALUES (
                      :id, :security_id, :source_document_id, :adjusted_earnings_id, :fiscal_year, :fiscal_period,
                      :period_start, :period_end, :filed_at, :accepted_at, :accession_number, :form_type,
                      :item_label, :normalized_label, :canonical_category, :gaap_tag, :raw_value, :raw_unit,
                      :currency, :scale, :amount_basis, :pretax_amount, :tax_effect, :after_tax_impact,
                      :sign, :recurring_flag, :asymmetric_flag, :tax_flag, :policy_included, :source,
                      :source_url, :filing_url, :table_hash, :row_hash, :parser_version, :confidence,
                      CAST(:warnings AS jsonb), CAST(:metadata AS jsonb), CAST(:source_trace AS jsonb), :created_at, :updated_at
                    )
                    """
                ),
                {
                    "id": record.id,
                    "security_id": security_id,
                    "source_document_id": source_document_id,
                    "adjusted_earnings_id": adjusted_earnings_id,
                    "fiscal_year": record.fiscal_year,
                    "fiscal_period": record.fiscal_period,
                    "period_start": record.period_start,
                    "period_end": record.period_end,
                    "filed_at": record.source_trace.filed_at,
                    "accepted_at": record.source_trace.accepted_at,
                    "accession_number": record.source_trace.accession_number,
                    "form_type": record.source_trace.form_type,
                    "item_label": record.item_label,
                    "normalized_label": record.normalized_label,
                    "canonical_category": record.canonical_category,
                    "gaap_tag": record.gaap_tag,
                    "raw_value": record.raw_value,
                    "raw_unit": record.raw_unit,
                    "currency": record.currency,
                    "scale": record.scale,
                    "amount_basis": _enum_value(record.amount_basis),
                    "pretax_amount": record.pretax_amount,
                    "tax_effect": record.tax_effect,
                    "after_tax_impact": record.after_tax_impact,
                    "sign": record.sign,
                    "recurring_flag": record.recurring_flag,
                    "asymmetric_flag": record.asymmetric_flag,
                    "tax_flag": record.tax_flag,
                    "policy_included": record.policy_included,
                    "source": _enum_value(record.source),
                    "source_url": record.source_trace.source_url,
                    "filing_url": record.source_trace.filing_url,
                    "table_hash": record.source_trace.table_hash,
                    "row_hash": record.source_trace.row_hash,
                    "parser_version": record.parser_version,
                    "confidence": record.confidence,
                    "warnings": _json(record.warnings),
                    "metadata": _json(record.metadata),
                    "source_trace": _json(source_trace),
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                },
            )

    def store_raw_object(
        self,
        ingestion_run_id: uuid.UUID | None,
        source_document_id: uuid.UUID | None,
        market: str,
        source: str,
        ticker: str,
        identifier: str,
        source_url: str | None,
        local_path: str | None,
        content_hash: str,
        content_type: str,
        metadata: dict[str, Any] | None = None,
        blob_key: str | None = None,
        blob_url: str | None = None,
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO raw_objects (
                      id, ingestion_run_id, source_document_id, market, source, ticker, identifier,
                      source_url, blob_key, blob_url, local_path, content_hash, content_type, metadata, created_at
                    )
                    VALUES (
                      :id, :ingestion_run_id, :source_document_id, :market, :source, :ticker, :identifier,
                      :source_url, :blob_key, :blob_url, :local_path, :content_hash, :content_type,
                      CAST(:metadata AS jsonb), :created_at
                    )
                    ON CONFLICT ON CONSTRAINT uq_raw_objects_content_source_identifier DO UPDATE SET
                      blob_key = EXCLUDED.blob_key,
                      blob_url = EXCLUDED.blob_url,
                      local_path = EXCLUDED.local_path,
                      metadata = EXCLUDED.metadata
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "ingestion_run_id": ingestion_run_id,
                    "source_document_id": source_document_id,
                    "market": market,
                    "source": source,
                    "ticker": ticker.upper(),
                    "identifier": identifier,
                    "source_url": source_url,
                    "blob_key": blob_key,
                    "blob_url": blob_url,
                    "local_path": local_path,
                    "content_hash": content_hash,
                    "content_type": content_type,
                    "metadata": _json(metadata or {}),
                    "created_at": datetime.now(UTC),
                },
            )

    def store_metric_value(
        self,
        security_id: uuid.UUID,
        metric_key: str,
        fiscal_year: int,
        value: Decimal,
        unit: str,
        currency: str,
        formula: str,
        method: str,
        quality_status: str,
        source_trace: dict[str, Any],
        source_document_id: uuid.UUID | None = None,
    ) -> None:
        source_trace_payload = _storage_ready_trace(
            source_trace,
            source=method,
            source_document_id=source_document_id,
            filing_id=f"{method}:{metric_key}:{fiscal_year}",
            period=f"FY{fiscal_year}",
            unit=unit,
            currency=currency,
            method=method,
            formula=formula,
            quality_status=quality_status,
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO metric_values (
                      id, security_id, source_document_id, metric_key, fiscal_year, fiscal_period, value, unit, currency,
                      formula, method, quality_status, source_trace, created_at
                    )
                    VALUES (
                      :id, :security_id, :source_document_id, :metric_key, :fiscal_year, 'FY', :value, :unit, :currency,
                      :formula, :method, :quality_status, CAST(:source_trace AS jsonb), :created_at
                    )
                    ON CONFLICT ON CONSTRAINT uq_metric_values_version DO UPDATE SET
                      value = EXCLUDED.value,
                      source_trace = EXCLUDED.source_trace
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "security_id": security_id,
                    "source_document_id": source_document_id,
                    "metric_key": metric_key,
                    "fiscal_year": fiscal_year,
                    "value": value,
                    "unit": unit,
                    "currency": currency,
                    "formula": formula,
                    "method": method,
                    "quality_status": quality_status,
                    "source_trace": _json(source_trace_payload),
                    "created_at": datetime.now(UTC),
                },
            )

    def store_financial_fact(
        self,
        security_id: uuid.UUID,
        source_document_id: uuid.UUID | None,
        *,
        taxonomy: str,
        tag: str,
        label: str | None,
        fiscal_year: int,
        fiscal_period: str,
        period_start,
        period_end,
        filed_at,
        accession_number: str | None,
        form_type: str | None,
        frame: str | None,
        unit: str,
        currency: str,
        value: Decimal,
        source: str,
        source_url: str | None,
        quality_status: str,
        source_trace: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        source_trace_payload = _storage_ready_trace(
            source_trace,
            source=source,
            source_document_id=source_document_id,
            filing_id=accession_number or f"{source}:{taxonomy}:{tag}:{fiscal_year}:{fiscal_period}",
            form=form_type,
            period=f"{fiscal_period}{fiscal_year}",
            unit=unit,
            currency=currency,
            method=source,
            formula=f"{source} {taxonomy}:{tag} reported fact",
            quality_status=quality_status,
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO financial_facts (
                      id, security_id, source_document_id, taxonomy, tag, label,
                      fiscal_year, fiscal_period, period_start, period_end, filed_at,
                      accession_number, form_type, frame, unit, currency, value,
                      source, source_url, quality_status, source_trace, metadata, created_at
                    )
                    VALUES (
                      :id, :security_id, :source_document_id, :taxonomy, :tag, :label,
                      :fiscal_year, :fiscal_period, :period_start, :period_end, :filed_at,
                      :accession_number, :form_type, :frame, :unit, :currency, :value,
                      :source, :source_url, :quality_status, CAST(:source_trace AS jsonb),
                      CAST(:metadata AS jsonb), :created_at
                    )
                    ON CONFLICT ON CONSTRAINT uq_financial_facts_version DO UPDATE SET
                      value = EXCLUDED.value,
                      period_start = EXCLUDED.period_start,
                      period_end = EXCLUDED.period_end,
                      filed_at = EXCLUDED.filed_at,
                      source_trace = EXCLUDED.source_trace,
                      metadata = EXCLUDED.metadata
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "security_id": security_id,
                    "source_document_id": source_document_id,
                    "taxonomy": taxonomy,
                    "tag": tag,
                    "label": label,
                    "fiscal_year": fiscal_year,
                    "fiscal_period": fiscal_period,
                    "period_start": period_start,
                    "period_end": period_end,
                    "filed_at": filed_at,
                    "accession_number": accession_number,
                    "form_type": form_type,
                    "frame": frame,
                    "unit": unit,
                    "currency": currency,
                    "value": value,
                    "source": source,
                    "source_url": source_url,
                    "quality_status": quality_status,
                    "source_trace": _json(source_trace_payload),
                    "metadata": _json(metadata or {}),
                    "created_at": datetime.now(UTC),
                },
            )

    def store_price_bar(self, security_id: uuid.UUID, fiscal_year: int, trade_date, close_price: Decimal, currency: str, source: str, source_trace: dict[str, Any]) -> None:
        source_trace_payload = _storage_ready_trace(
            source_trace,
            source=source,
            filing_id=f"{source}:{trade_date}:{security_id}",
            period=str(trade_date),
            unit="price",
            currency=currency,
            method=source,
            formula="Source-backed close price observation",
            quality_status="source_backed_price",
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO price_bars (id, security_id, trade_date, fiscal_year, close_price, currency, source, source_trace, created_at)
                    VALUES (:id, :security_id, :trade_date, :fiscal_year, :close_price, :currency, :source, CAST(:source_trace AS jsonb), :created_at)
                    ON CONFLICT ON CONSTRAINT uq_price_bars_security_date_source DO UPDATE SET
                      close_price = EXCLUDED.close_price,
                      source_trace = EXCLUDED.source_trace
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "security_id": security_id,
                    "trade_date": trade_date,
                    "fiscal_year": fiscal_year,
                    "close_price": close_price,
                    "currency": currency,
                    "source": source,
                    "source_trace": _json(source_trace_payload),
                    "created_at": datetime.now(UTC),
                },
            )

    def store_dividend(self, security_id: uuid.UUID, fiscal_year: int, ex_date, amount: Decimal, currency: str, source: str, source_trace: dict[str, Any]) -> None:
        source_trace_payload = _storage_ready_trace(
            source_trace,
            source=source,
            filing_id=f"{source}:{ex_date}:{security_id}:dividend",
            period=str(ex_date),
            unit="per_share",
            currency=currency,
            method=source,
            formula="Source-backed dividend per share observation",
            quality_status="source_backed_dividend",
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO dividends (id, security_id, ex_date, fiscal_year, amount, currency, source, source_trace, created_at)
                    VALUES (:id, :security_id, :ex_date, :fiscal_year, :amount, :currency, :source, CAST(:source_trace AS jsonb), :created_at)
                    ON CONFLICT ON CONSTRAINT uq_dividends_security_date_source DO UPDATE SET
                      amount = EXCLUDED.amount,
                      source_trace = EXCLUDED.source_trace
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "security_id": security_id,
                    "ex_date": ex_date,
                    "fiscal_year": fiscal_year,
                    "amount": amount,
                    "currency": currency,
                    "source": source,
                    "source_trace": _json(source_trace_payload),
                    "created_at": datetime.now(UTC),
                },
            )

    def store_consensus_snapshot(
        self,
        security_id: uuid.UUID,
        metric_key: str,
        fiscal_year: int,
        snapshot_date,
        estimate_case: str,
        estimate_value: Decimal,
        unit: str,
        currency: str,
        source: str,
        quality_status: str,
        source_trace: dict[str, Any],
        fiscal_period: str = "FY",
        period_end=None,
        growth_rate_pct: Decimal | None = None,
        analyst_count: int | None = None,
        source_url: str | None = None,
        source_document_id: uuid.UUID | None = None,
    ) -> None:
        source_trace_payload = _storage_ready_trace(
            source_trace,
            source=source,
            source_document_id=source_document_id,
            filing_id=f"{source}:{metric_key}:{fiscal_year}:{snapshot_date}:{estimate_case}",
            period=f"{fiscal_period}{fiscal_year}",
            unit=unit,
            currency=currency,
            method=source,
            formula="Point-in-time estimate snapshot imported from source-backed file",
            quality_status=quality_status,
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO consensus_estimate_snapshots (
                      id, security_id, source_document_id, metric_key, fiscal_year, fiscal_period,
                      period_end, snapshot_date, estimate_case, estimate_value, growth_rate_pct,
                      analyst_count, unit, currency, source, source_url, quality_status,
                      source_trace, created_at
                    )
                    VALUES (
                      :id, :security_id, :source_document_id, :metric_key, :fiscal_year, :fiscal_period,
                      :period_end, :snapshot_date, :estimate_case, :estimate_value, :growth_rate_pct,
                      :analyst_count, :unit, :currency, :source, :source_url, :quality_status,
                      CAST(:source_trace AS jsonb), :created_at
                    )
                    ON CONFLICT ON CONSTRAINT uq_consensus_snapshot_version DO UPDATE SET
                      estimate_value = EXCLUDED.estimate_value,
                      growth_rate_pct = EXCLUDED.growth_rate_pct,
                      analyst_count = EXCLUDED.analyst_count,
                      quality_status = EXCLUDED.quality_status,
                      source_trace = EXCLUDED.source_trace
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "security_id": security_id,
                    "source_document_id": source_document_id,
                    "metric_key": metric_key,
                    "fiscal_year": fiscal_year,
                    "fiscal_period": fiscal_period,
                    "period_end": period_end,
                    "snapshot_date": snapshot_date,
                    "estimate_case": estimate_case,
                    "estimate_value": estimate_value,
                    "growth_rate_pct": growth_rate_pct,
                    "analyst_count": analyst_count,
                    "unit": unit,
                    "currency": currency,
                    "source": source,
                    "source_url": source_url,
                    "quality_status": quality_status,
                    "source_trace": _json(source_trace_payload),
                    "created_at": datetime.now(UTC),
                },
            )

    def store_macro_observation(
        self,
        series_id: str,
        observation_date,
        value: Decimal,
        source: str,
        source_trace: dict[str, Any],
        *,
        unit: str | None = None,
        frequency: str | None = None,
        source_url: str | None = None,
        source_document_id: uuid.UUID | None = None,
    ) -> None:
        source_trace_payload = _storage_ready_trace(
            source_trace,
            source=source,
            source_document_id=source_document_id,
            filing_id=f"{source}:{series_id}:{observation_date}",
            period=str(observation_date),
            unit=unit or "observation",
            currency="N/A",
            method=source,
            formula="Source-backed macro observation",
            quality_status="source_backed_macro",
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO macro_series (
                      id, series_id, observation_date, value, unit, frequency, source,
                      source_url, source_document_id, source_trace, created_at
                    )
                    VALUES (
                      :id, :series_id, :observation_date, :value, :unit, :frequency, :source,
                      :source_url, :source_document_id, CAST(:source_trace AS jsonb), :created_at
                    )
                    ON CONFLICT ON CONSTRAINT uq_macro_series_observation_source DO UPDATE SET
                      value = EXCLUDED.value,
                      unit = EXCLUDED.unit,
                      frequency = EXCLUDED.frequency,
                      source_url = EXCLUDED.source_url,
                      source_document_id = EXCLUDED.source_document_id,
                      source_trace = EXCLUDED.source_trace
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "series_id": series_id.upper(),
                    "observation_date": observation_date,
                    "value": value,
                    "unit": unit,
                    "frequency": frequency,
                    "source": source,
                    "source_url": source_url,
                    "source_document_id": source_document_id,
                    "source_trace": _json(source_trace_payload),
                    "created_at": datetime.now(UTC),
                },
            )

    def store_industry_observation(
        self,
        *,
        market: str,
        series_id: str,
        observation_date,
        value: Decimal,
        source: str,
        category: str,
        source_trace: dict[str, Any],
        unit: str | None = None,
        frequency: str | None = None,
        region: str | None = None,
        industry: str | None = None,
        source_url: str | None = None,
        source_document_id: uuid.UUID | None = None,
        dimensions: dict[str, Any] | None = None,
    ) -> None:
        source_trace_payload = _storage_ready_trace(
            source_trace,
            source=source,
            source_document_id=source_document_id,
            filing_id=f"{source}:{series_id}:{observation_date}",
            period=str(observation_date),
            unit=unit or "observation",
            currency="N/A",
            method=source,
            formula="Source-backed official statistics observation",
            quality_status="source_backed_official_statistics",
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO industry_series (
                      id, market, series_id, observation_date, value, unit, frequency,
                      category, region, industry, source, source_url, source_document_id,
                      dimensions, source_trace, created_at
                    )
                    VALUES (
                      :id, :market, :series_id, :observation_date, :value, :unit, :frequency,
                      :category, :region, :industry, :source, :source_url, :source_document_id,
                      CAST(:dimensions AS jsonb), CAST(:source_trace AS jsonb), :created_at
                    )
                    ON CONFLICT ON CONSTRAINT uq_industry_series_observation_source DO UPDATE SET
                      value = EXCLUDED.value,
                      unit = EXCLUDED.unit,
                      frequency = EXCLUDED.frequency,
                      category = EXCLUDED.category,
                      region = EXCLUDED.region,
                      industry = EXCLUDED.industry,
                      source_url = EXCLUDED.source_url,
                      source_document_id = EXCLUDED.source_document_id,
                      dimensions = EXCLUDED.dimensions,
                      source_trace = EXCLUDED.source_trace
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "market": market.upper(),
                    "series_id": series_id.upper(),
                    "observation_date": observation_date,
                    "value": value,
                    "unit": unit,
                    "frequency": frequency,
                    "category": category,
                    "region": region,
                    "industry": industry,
                    "source": source,
                    "source_url": source_url,
                    "source_document_id": source_document_id,
                    "dimensions": _json(dimensions or {}),
                    "source_trace": _json(source_trace_payload),
                    "created_at": datetime.now(UTC),
                },
            )

    def store_recession_period(
        self,
        series_id: str,
        start_date,
        end_date,
        source: str,
        source_trace: dict[str, Any],
        *,
        source_document_id: uuid.UUID | None = None,
    ) -> None:
        source_trace_payload = _storage_ready_trace(
            source_trace,
            source=source,
            source_document_id=source_document_id,
            filing_id=f"{source}:{series_id}:{start_date}:{end_date}",
            period=f"{start_date}:{end_date}",
            unit="indicator",
            currency="N/A",
            method=source,
            formula="Contiguous recession indicator observations equal to 1",
            quality_status="source_backed_macro",
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO recession_periods (
                      id, series_id, start_date, end_date, source, source_document_id,
                      source_trace, created_at
                    )
                    VALUES (
                      :id, :series_id, :start_date, :end_date, :source, :source_document_id,
                      CAST(:source_trace AS jsonb), :created_at
                    )
                    ON CONFLICT ON CONSTRAINT uq_recession_period_source DO UPDATE SET
                      end_date = EXCLUDED.end_date,
                      source_document_id = EXCLUDED.source_document_id,
                      source_trace = EXCLUDED.source_trace
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "series_id": series_id.upper(),
                    "start_date": start_date,
                    "end_date": end_date,
                    "source": source,
                    "source_document_id": source_document_id,
                    "source_trace": _json(source_trace_payload),
                    "created_at": datetime.now(UTC),
                },
            )


def _json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _record_accession_number(record: AdjustedEarningsRecord) -> str:
    if record.source_trace.accession_number:
        return record.source_trace.accession_number
    return (
        f"NO_ACCESSION:{_enum_value(record.method)}:"
        f"{record.policy}:{record.fiscal_year}:{record.fiscal_period}"
    )


def _storage_ready_trace(
    source_trace: SourceTrace | dict[str, Any] | None,
    **defaults: Any,
) -> dict[str, Any]:
    payload = (
        source_trace.model_dump(mode="python")
        if isinstance(source_trace, SourceTrace)
        else dict(source_trace or {})
    )
    source_document_id = defaults.pop("source_document_id", None)
    if source_document_id is not None and _missing_trace_value(payload.get("source_document_id")):
        payload["source_document_id"] = str(source_document_id)
    for key, value in defaults.items():
        if value is not None and _missing_trace_value(payload.get(key)):
            payload[key] = str(value)
    if _missing_trace_value(payload.get("source_document_id")):
        payload["source_document_id"] = _logical_source_document_id(payload)
    if _missing_trace_value(payload.get("available_at")):
        for key in ("accepted_at", "filed_at", "ingested_at"):
            if not _missing_trace_value(payload.get(key)):
                payload["available_at"] = payload[key]
                break
    trace = SourceTrace(**payload)
    trace.assert_storage_ready()
    return trace.model_dump(mode="json")


def _missing_trace_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "unknown", "n/a", "na", "none"}
    return False


def _logical_source_document_id(payload: dict[str, Any]) -> str:
    source = str(payload.get("source") or payload.get("source_type") or "source").strip()
    filing_id = str(payload.get("filing_id") or payload.get("accession_number") or "").strip()
    period = str(payload.get("period") or "").strip()
    if filing_id and period:
        return f"{source}:{filing_id}:{period}"
    if filing_id:
        return f"{source}:{filing_id}"
    source_url = str(payload.get("source_url") or payload.get("filing_url") or "").strip()
    if source_url:
        return f"{source}:{source_url}"
    return f"{source}:source_document_pending"
