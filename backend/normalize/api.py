from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.normalize.schemas import NormalizationPolicy
from services.api.postgres_provider import (
    adjusted_series_from_postgres,
    adjusted_waterfall_from_postgres,
)
from services.api.sample_data import sample_normalization_result

router = APIRouter(prefix="/api/security", tags=["adjusted-earnings"])


class NormalizeAdjustedRequest(BaseModel):
    policy: NormalizationPolicy | None = None
    period_type: str = "annual"
    start_year: int | None = None
    end_year: int | None = None
    force_refresh: bool = False
    fixture: bool = False
    persist: bool = False


@router.get("/{ticker}/adjusted")
def get_adjusted_series(
    ticker: str,
    policy: str = "street_comparable",
    exclude_sbc: bool = False,
    exclude_acquired_intangible_amortization: bool = False,
    start_year: int | None = None,
    end_year: int | None = None,
) -> dict:
    normalization_policy = NormalizationPolicy(
        base_policy=policy,
        exclude_sbc=exclude_sbc,
        exclude_acquired_intangible_amortization=exclude_acquired_intangible_amortization,
    )
    db_payload = adjusted_series_from_postgres(
        ticker,
        normalization_policy.key,
        start_year,
        end_year,
    )
    if db_payload is not None:
        return db_payload
    result = sample_normalization_result(ticker, normalization_policy, start_year, end_year)
    return result.model_dump(mode="json")


@router.get("/{ticker}/adjusted/waterfall")
def get_adjusted_waterfall(
    ticker: str,
    fiscal_year: int = Query(...),
    fiscal_period: str = "FY",
    policy: str = "street_comparable",
) -> dict:
    normalization_policy = NormalizationPolicy(base_policy=policy)
    db_payload = adjusted_waterfall_from_postgres(
        ticker,
        fiscal_year,
        fiscal_period,
        normalization_policy.key,
    )
    if db_payload is not None:
        return db_payload
    result = sample_normalization_result(
        ticker,
        normalization_policy,
        fiscal_year,
        fiscal_year,
    )
    for record in result.series:
        if record.fiscal_year == fiscal_year and record.fiscal_period == fiscal_period:
            return {
                "ticker": ticker.upper(),
                "fiscal_year": fiscal_year,
                "waterfall": [step.model_dump(mode="json") for step in record.waterfall],
            }
    raise HTTPException(status_code=404, detail="waterfall not found")


@router.post("/{ticker}/normalize-adjusted")
def normalize_adjusted(ticker: str, body: NormalizeAdjustedRequest | None = None) -> dict:
    request = body or NormalizeAdjustedRequest()
    normalization_policy = request.policy or NormalizationPolicy()
    start_year = request.start_year
    end_year = request.end_year
    if request.period_type != "annual":
        raise HTTPException(
            status_code=400,
            detail="Only annual normalization is supported in this phase.",
        )
    if request.fixture:
        result = sample_normalization_result(ticker, normalization_policy, start_year, end_year)
        return result.model_dump(mode="json") | {"meta": {"source": "fixture_non_production"}}

    if start_year is None or end_year is None:
        raise HTTPException(
            status_code=400,
            detail="start_year and end_year are required for worker normalization.",
        )
    years = f"{start_year}:{end_year}"
    command = [
        "python",
        "-m",
        "services.ingestion_worker.cli",
        "normalize-us",
        "--ticker",
        ticker.upper(),
        "--years",
        years,
        "--policy",
        normalization_policy.base_policy.value,
    ]
    if request.force_refresh:
        command.append("--force-refresh")
    if request.persist:
        command.append("--persist")
    return JSONResponse(
        status_code=202,
        content={
            "ticker": ticker.upper(),
            "status": "worker_required",
            "command": command,
            "meta": {
                "source": "ingestion_worker_control_plane",
                "persist_requested": request.persist,
                "message": "Run the ingestion worker outside the Vercel request path.",
            },
        },
    )
