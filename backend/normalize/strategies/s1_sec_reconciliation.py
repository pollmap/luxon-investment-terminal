from __future__ import annotations

from decimal import Decimal

from backend.normalize.confidence import apply_penalties, base_confidence
from backend.normalize.enums import AmountBasis, NormalizationMethod, QualityStatus
from backend.normalize.parsers.reconciliation import ExtractedRow, find_reconciliation_tables
from backend.normalize.policies import should_include_adjustment
from backend.normalize.schemas import (
    AdjustedEarningsRecord,
    AdjustmentRecord,
    NormalizationPolicy,
    NormalizationResult,
    SourceDocument,
    SourceTrace,
)
from backend.normalize.tax import DEFAULT_US_STATUTORY_RATE, after_tax_impact
from backend.normalize.taxonomy import match_category, normalize_label
from backend.normalize.waterfall import build_waterfall


EPS_TOLERANCE_MIN = Decimal("0.01")


class S1SecReconciliationStrategy:
    method_name = NormalizationMethod.S1_SEC_RECONCILIATION.value

    def __init__(self, source_documents: list[SourceDocument] | None = None) -> None:
        self.source_documents = source_documents or []

    def normalize(
        self,
        ticker: str,
        policy: NormalizationPolicy,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> NormalizationResult:
        records: list[AdjustedEarningsRecord] = []
        warnings: list[str] = []
        for document in self.source_documents:
            if not document.content:
                continue
            tables = find_reconciliation_tables(document.content)
            if not tables:
                warnings.append(f"{document.id}: reconciliation_table_not_found")
                continue
            table = tables[0]
            row_map = _row_map(table.rows)
            fiscal_year = _fiscal_year_from_document(document, start_year, end_year)
            if fiscal_year is None:
                warnings.append(f"{document.id}: period_ambiguity")
                continue
            source_trace = SourceTrace(
                source_document_id=document.id,
                accession_number=document.accession_number,
                form_type=document.form_type,
                filing_url=document.filing_url,
                source_url=document.source_url,
                table_hash=table.table_hash,
            )
            adjustments = _build_adjustments(
                ticker=ticker,
                fiscal_year=fiscal_year,
                rows=table.rows,
                policy=policy,
                source_trace=source_trace,
            )
            gaap_ni = _value(row_map, "gaap_ni")
            gaap_eps = _value(row_map, "gaap_eps_diluted") or _value(row_map, "gaap_eps")
            company_adjusted_eps = _value(row_map, "adjusted_eps")
            adjusted_ni = _value(row_map, "adjusted_ni")
            diluted_shares = _value(row_map, "diluted_shares")
            included_impact = sum(
                (item.after_tax_impact or Decimal("0"))
                for item in adjustments
                if item.policy_included
            )
            reconstructed_adjusted_ni = (
                gaap_ni + included_impact if gaap_ni is not None else adjusted_ni
            )
            reconstructed_adjusted_eps = None
            flags: list[str] = []
            if reconstructed_adjusted_ni is not None and diluted_shares:
                reconstructed_adjusted_eps = reconstructed_adjusted_ni / diluted_shares
            final_adjusted_eps = (
                company_adjusted_eps
                if policy.use_company_reported_when_available and company_adjusted_eps is not None
                else reconstructed_adjusted_eps
            )
            if company_adjusted_eps is not None and reconstructed_adjusted_eps is not None:
                tolerance = max(EPS_TOLERANCE_MIN, abs(company_adjusted_eps) * Decimal("0.005"))
                if abs(company_adjusted_eps - reconstructed_adjusted_eps) > tolerance:
                    flags.append("eps_reconciliation_outside_tolerance")
            explicit_tax = any("explicit_tax_effect" in item.warnings for item in adjustments)
            direct_eps = company_adjusted_eps is not None
            detailed_bridge = bool(adjustments)
            base = base_confidence(
                NormalizationMethod.S1_SEC_RECONCILIATION,
                direct_eps=direct_eps,
                detailed_bridge=detailed_bridge,
                explicit_tax=explicit_tax,
                clear_period=True,
            )
            for item in adjustments:
                flags.extend(flag for flag in item.warnings if flag not in flags)
            score, penalties = apply_penalties(base, flags)
            record = AdjustedEarningsRecord(
                security_id=ticker.upper(),
                ticker=ticker.upper(),
                fiscal_year=fiscal_year,
                fiscal_period="FY",
                gaap_ni=gaap_ni,
                gaap_eps_diluted=gaap_eps,
                adjusted_ni=reconstructed_adjusted_ni,
                adjusted_eps=final_adjusted_eps,
                company_adjusted_eps=company_adjusted_eps,
                diluted_shares=diluted_shares,
                method=NormalizationMethod.S1_SEC_RECONCILIATION,
                policy=policy.key,
                exclude_sbc=policy.exclude_sbc,
                exclude_acquired_intangible_amortization=policy.exclude_acquired_intangible_amortization,
                sector_policy=policy.sector_policy,
                confidence=score,
                quality_status=(
                    QualityStatus.WARNING
                    if "eps_reconciliation_outside_tolerance" in flags or score < Decimal("0.80")
                    else QualityStatus.PASSED
                ),
                flags=sorted(set(flags)),
                warnings=table.warnings,
                formula="S1 company adjusted EPS preferred; reconstructed NI = GAAP NI + included after-tax adjustments",
                source_trace=source_trace,
                metadata={"base_score": str(base), "penalties": penalties, "table_score": table.candidate_score},
                adjustments=adjustments,
            )
            record.waterfall = build_waterfall(gaap_ni, diluted_shares, adjustments, source_trace)
            records.append(record)
        return NormalizationResult(ticker=ticker.upper(), policy=policy, series=records, warnings=warnings)


def _row_map(rows: list[ExtractedRow]) -> dict[str, ExtractedRow]:
    mapped: dict[str, ExtractedRow] = {}
    for row in rows:
        if row.row_type not in mapped and row.value is not None:
            mapped[row.row_type] = row
    return mapped


def _value(row_map: dict[str, ExtractedRow], key: str) -> Decimal | None:
    row = row_map.get(key)
    return row.value if row else None


def _fiscal_year_from_document(
    document: SourceDocument,
    start_year: int | None,
    end_year: int | None,
) -> int | None:
    for candidate in (document.metadata.get("fiscal_year"), document.metadata.get("year")):
        if candidate:
            year = int(candidate)
            if (start_year is None or year >= start_year) and (end_year is None or year <= end_year):
                return year
    if start_year == end_year and start_year is not None:
        return start_year
    return end_year or start_year


def _build_adjustments(
    ticker: str,
    fiscal_year: int,
    rows: list[ExtractedRow],
    policy: NormalizationPolicy,
    source_trace: SourceTrace,
) -> list[AdjustmentRecord]:
    adjustments: list[AdjustmentRecord] = []
    tax_effect_row = next((row for row in rows if row.row_type == "tax_effect"), None)
    tax_effect_allocations = _tax_effect_allocations(rows, tax_effect_row)
    for row in rows:
        if row.row_type not in {"adjustment_line", "discontinued_ops"} or row.value is None:
            continue
        taxonomy_item = match_category(row.label)
        sign = _sign_for_row(row)
        tax_effect = tax_effect_allocations.get(row.row_hash)
        impact, tax_flags = after_tax_impact(
            row.value,
            sign,
            tax_effect=tax_effect,
            tax_rule=taxonomy_item.tax_rule,
            effective_rate=DEFAULT_US_STATUTORY_RATE,
        )
        included = should_include_adjustment(taxonomy_item, policy, company_excluded=True)
        adjustments.append(
            AdjustmentRecord(
                security_id=ticker.upper(),
                fiscal_year=fiscal_year,
                fiscal_period="FY",
                item_label=row.label,
                normalized_label=normalize_label(row.label),
                canonical_category=taxonomy_item.canonical_category,
                raw_value=row.raw_value,
                amount_basis=AmountBasis.PRETAX,
                pretax_amount=abs(row.value),
                tax_effect=tax_effect,
                after_tax_impact=impact,
                sign=sign,
                policy_included=included,
                source=NormalizationMethod.S1_SEC_RECONCILIATION,
                source_trace=source_trace.model_copy(update={"row_hash": row.row_hash}),
                confidence=row.confidence,
                warnings=tax_flags,
            )
        )
    return adjustments


def _tax_effect_allocations(
    rows: list[ExtractedRow],
    tax_effect_row: ExtractedRow | None,
) -> dict[str, Decimal]:
    if tax_effect_row is None or tax_effect_row.value is None:
        return {}
    taxable_rows = [
        row
        for row in rows
        if row.row_type in {"adjustment_line", "discontinued_ops"}
        and row.value is not None
        and match_category(row.label).tax_rule == "after_tax"
    ]
    taxable_total = sum(abs(row.value or Decimal("0")) for row in taxable_rows)
    if not taxable_rows or taxable_total <= 0:
        return {}

    remaining_tax = abs(tax_effect_row.value)
    allocations: dict[str, Decimal] = {}
    for index, row in enumerate(taxable_rows):
        if index == len(taxable_rows) - 1:
            allocated = remaining_tax
        else:
            allocated = (abs(tax_effect_row.value) * abs(row.value or Decimal("0")) / taxable_total).quantize(
                Decimal("0.000001")
            )
            remaining_tax -= allocated
        allocations[row.row_hash] = allocated
    return allocations


def _sign_for_row(row: ExtractedRow) -> int:
    label = row.normalized_label
    if "gain" in label and "loss" not in label:
        return -1
    if row.row_type == "discontinued_ops":
        return -1
    return 1
