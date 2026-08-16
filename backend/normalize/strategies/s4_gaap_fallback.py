from __future__ import annotations

from decimal import Decimal
from typing import Any

from backend.normalize.confidence import base_confidence
from backend.normalize.enums import NormalizationMethod, QualityStatus
from backend.normalize.schemas import AdjustedEarningsRecord, NormalizationPolicy, NormalizationResult, SourceTrace


class S4GaapFallbackStrategy:
    method_name = NormalizationMethod.S4_GAAP_FALLBACK.value

    def __init__(self, gaap_facts_by_year: dict[int, dict[str, Any]] | None = None) -> None:
        self.gaap_facts_by_year = gaap_facts_by_year or {}

    def normalize(
        self,
        ticker: str,
        policy: NormalizationPolicy,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> NormalizationResult:
        records: list[AdjustedEarningsRecord] = []
        for year, facts in sorted(self.gaap_facts_by_year.items()):
            if start_year and year < start_year:
                continue
            if end_year and year > end_year:
                continue
            gaap_eps = _decimal(facts.get("EarningsPerShareDiluted") or facts.get("gaap_eps_diluted"))
            if gaap_eps is None:
                continue
            gaap_ni = _decimal(facts.get("NetIncomeLoss") or facts.get("gaap_ni"))
            shares = _decimal(
                facts.get("WeightedAverageNumberOfDilutedSharesOutstanding") or facts.get("diluted_shares")
            )
            source_trace = SourceTrace(
                source_type=facts.get("source_type") or _default_source_type(ticker),
                source_url=facts.get("source_url") or _default_source_url(ticker),
                form_type=facts.get("form_type"),
            )
            records.append(
                AdjustedEarningsRecord(
                    security_id=ticker.upper(),
                    ticker=ticker.upper(),
                    fiscal_year=year,
                    fiscal_period="FY",
                    gaap_ni=gaap_ni,
                    gaap_eps_diluted=gaap_eps,
                    adjusted_ni=gaap_ni,
                    adjusted_eps=gaap_eps,
                    diluted_shares=shares,
                    currency=facts.get("currency") or _default_currency(ticker),
                    method=NormalizationMethod.S4_GAAP_FALLBACK,
                    policy=policy.key,
                    confidence=base_confidence(NormalizationMethod.S4_GAAP_FALLBACK),
                    quality_status=QualityStatus.FALLBACK,
                    flags=["gaap_fallback"],
                    warnings=["Adjusted EPS unavailable; using GAAP diluted EPS transparently"],
                    formula="S4 adjusted_eps = gaap_eps_diluted",
                    source_trace=source_trace,
                )
            )
        return NormalizationResult(ticker=ticker.upper(), policy=policy, series=records)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _default_currency(ticker: str) -> str:
    if ticker.upper().endswith(".KS"):
        return "KRW"
    if ticker.upper().endswith(".T"):
        return "JPY"
    return "USD"


def _default_source_type(ticker: str) -> str:
    if ticker.upper().endswith(".KS"):
        return "opendart_fixture"
    if ticker.upper().endswith(".T"):
        return "jquants_fixture"
    return "sec_companyfacts"


def _default_source_url(ticker: str) -> str | None:
    if ticker.upper().endswith(".KS"):
        return "fixture://opendart/005930"
    if ticker.upper().endswith(".T"):
        return "fixture://jquants/7203"
    return None
