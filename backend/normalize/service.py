from __future__ import annotations

from pathlib import Path

from backend.normalize.edgar.client import EdgarClient, EdgarConfigError
from backend.normalize.edgar.collector import EdgarCollector
from backend.normalize.schemas import NormalizationPolicy, NormalizationResult, SourceDocument
from backend.normalize.strategies.s1_sec_reconciliation import S1SecReconciliationStrategy
from backend.normalize.strategies.s2_xbrl_special_items import S2XbrlSpecialItemsStrategy
from backend.normalize.strategies.s3_market_standard import S3MarketStandardStrategy
from backend.normalize.strategies.s4_gaap_fallback import S4GaapFallbackStrategy


class NormalizationService:
    def __init__(
        self,
        source_documents: list[SourceDocument] | None = None,
        xbrl_facts_by_year: dict | None = None,
        gaap_facts_by_year: dict | None = None,
    ) -> None:
        self.source_documents = source_documents or []
        self.xbrl_facts_by_year = xbrl_facts_by_year or {}
        self.gaap_facts_by_year = gaap_facts_by_year or {}

    def normalize(
        self,
        ticker: str,
        policy: NormalizationPolicy | None = None,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> NormalizationResult:
        active_policy = policy or NormalizationPolicy()
        failed: list[str] = []
        for strategy in (
            S1SecReconciliationStrategy(self.source_documents),
            S2XbrlSpecialItemsStrategy(self.xbrl_facts_by_year),
            S3MarketStandardStrategy(),
            S4GaapFallbackStrategy(self.gaap_facts_by_year),
        ):
            result = strategy.normalize(ticker, active_policy, start_year, end_year)
            if result.series:
                result.failed_strategies = failed
                return result
            failed.append(strategy.method_name)
        return NormalizationResult(
            ticker=ticker.upper(),
            policy=active_policy,
            series=[],
            failed_strategies=failed,
            warnings=["No strategy produced adjusted earnings"],
        )

    def collect_sec(
        self,
        ticker: str,
        start_year: int | None,
        end_year: int | None,
        force_refresh: bool = False,
    ) -> list[SourceDocument]:
        try:
            collector = EdgarCollector(EdgarClient())
        except EdgarConfigError:
            raise
        return collector.collect_earnings_exhibits(ticker, start_year, end_year, force_refresh)


def load_source_documents_from_paths(
    paths: list[str | Path],
    ticker: str,
    fiscal_year: int,
) -> list[SourceDocument]:
    documents: list[SourceDocument] = []
    for path in paths:
        file_path = Path(path)
        documents.append(
            SourceDocument(
                id=file_path.stem,
                ticker=ticker.upper(),
                accession_number=file_path.stem,
                form_type="8-K",
                source_url=file_path.as_uri() if file_path.is_absolute() else str(file_path),
                content=file_path.read_text(encoding="utf-8"),
                local_path=str(file_path),
                metadata={"fiscal_year": fiscal_year},
            )
        )
    return documents
