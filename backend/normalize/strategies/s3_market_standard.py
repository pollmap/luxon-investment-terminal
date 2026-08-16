from backend.normalize.schemas import NormalizationPolicy, NormalizationResult


class S3MarketStandardStrategy:
    method_name = "S3_MARKET_STANDARD"

    def normalize(
        self,
        ticker: str,
        policy: NormalizationPolicy,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> NormalizationResult:
        return NormalizationResult(
            ticker=ticker.upper(),
            policy=policy,
            series=[],
            warnings=["S3 KR/JP market-standard mapping is scaffolded for future connectors"],
        )

