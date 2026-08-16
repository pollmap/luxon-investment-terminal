from __future__ import annotations

from decimal import Decimal
from typing import Any

from backend.normalize.confidence import apply_penalties, base_confidence
from backend.normalize.enums import AmountBasis, NormalizationMethod, QualityStatus
from backend.normalize.policies import should_include_adjustment
from backend.normalize.schemas import (
    AdjustedEarningsRecord,
    AdjustmentRecord,
    NormalizationPolicy,
    NormalizationResult,
    SourceTrace,
)
from backend.normalize.tax import after_tax_impact, effective_tax_rate
from backend.normalize.taxonomy import TAXONOMY, match_category
from backend.normalize.waterfall import build_waterfall


class S2XbrlSpecialItemsStrategy:
    method_name = NormalizationMethod.S2_XBRL_SPECIAL_ITEMS.value

    def __init__(self, facts_by_year: dict[int, dict[str, Any]] | None = None) -> None:
        self.facts_by_year = facts_by_year or {}

    def normalize(
        self,
        ticker: str,
        policy: NormalizationPolicy,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> NormalizationResult:
        records: list[AdjustedEarningsRecord] = []
        for year, facts in sorted(self.facts_by_year.items()):
            if start_year and year < start_year:
                continue
            if end_year and year > end_year:
                continue
            gaap_ni = _decimal(facts.get("NetIncomeLossAvailableToCommonStockholdersBasic") or facts.get("NetIncomeLoss"))
            gaap_eps = _decimal(facts.get("EarningsPerShareDiluted"))
            shares = _decimal(facts.get("WeightedAverageNumberOfDilutedSharesOutstanding"))
            pretax_income = _decimal(facts.get("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"))
            tax_expense = _decimal(facts.get("IncomeTaxExpenseBenefit"))
            rate, rate_flags = effective_tax_rate(pretax_income, tax_expense)
            adjustments: list[AdjustmentRecord] = []
            for item in TAXONOMY.values():
                for tag in item.xbrl_tags:
                    if tag not in facts:
                        continue
                    raw = _decimal(facts[tag])
                    if raw is None:
                        continue
                    sign = -1 if "GainLoss" in tag and raw > 0 else 1
                    impact, tax_flags = after_tax_impact(
                        raw,
                        sign,
                        tax_rule=item.tax_rule,
                        effective_rate=rate,
                    )
                    included = should_include_adjustment(item, policy, company_excluded=False)
                    adjustments.append(
                        AdjustmentRecord(
                            security_id=ticker.upper(),
                            fiscal_year=year,
                            fiscal_period="FY",
                            item_label=tag,
                            normalized_label=tag,
                            canonical_category=item.canonical_category,
                            gaap_tag=tag,
                            raw_value=str(raw),
                            amount_basis=AmountBasis.PRETAX,
                            pretax_amount=abs(raw),
                            after_tax_impact=impact,
                            sign=sign,
                            policy_included=included,
                            source=NormalizationMethod.S2_XBRL_SPECIAL_ITEMS,
                            confidence=Decimal("0.55"),
                            warnings=tax_flags + rate_flags,
                        )
                    )
            if gaap_ni is None and gaap_eps is None:
                continue
            if shares is None and gaap_ni is not None and gaap_eps not in {None, Decimal("0")}:
                shares = gaap_ni / gaap_eps
                rate_flags.append("inferred_shares")
            included_impact = sum(
                (item.after_tax_impact or Decimal("0"))
                for item in adjustments
                if item.policy_included
            )
            adjusted_ni = gaap_ni + included_impact if gaap_ni is not None else None
            adjusted_eps = adjusted_ni / shares if adjusted_ni is not None and shares else gaap_eps
            flags = sorted({flag for item in adjustments for flag in item.warnings} | set(rate_flags))
            base = base_confidence(
                NormalizationMethod.S2_XBRL_SPECIAL_ITEMS,
                explicit_tax="inferred_tax_effect" not in flags,
            )
            score, penalties = apply_penalties(base, flags)
            source_trace = SourceTrace(source_type="sec_companyfacts")
            record = AdjustedEarningsRecord(
                security_id=ticker.upper(),
                ticker=ticker.upper(),
                fiscal_year=year,
                fiscal_period="FY",
                gaap_ni=gaap_ni,
                gaap_eps_diluted=gaap_eps,
                adjusted_ni=adjusted_ni,
                adjusted_eps=adjusted_eps,
                diluted_shares=shares,
                method=NormalizationMethod.S2_XBRL_SPECIAL_ITEMS,
                policy=policy.key,
                confidence=score,
                quality_status=QualityStatus.WARNING,
                flags=flags,
                formula="S2 adjusted NI = GAAP NI + after-tax mapped XBRL special items",
                source_trace=source_trace,
                metadata={"base_score": str(base), "penalties": penalties},
                adjustments=adjustments,
            )
            record.waterfall = build_waterfall(gaap_ni, shares, adjustments, source_trace)
            records.append(record)
        return NormalizationResult(ticker=ticker.upper(), policy=policy, series=records)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))

