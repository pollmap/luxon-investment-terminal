from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.normalize.edgar.client import EdgarConfigError
from backend.normalize.schemas import NormalizationPolicy
from backend.normalize.service import NormalizationService
from services.api.sample_data import sample_normalization_service


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m backend.normalize.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect-sec")
    collect.add_argument("--ticker", required=True)
    collect.add_argument("--years", default=None)
    collect.add_argument("--force-refresh", action="store_true")

    normalize = subparsers.add_parser("normalize")
    normalize.add_argument("--ticker", required=True)
    normalize.add_argument("--policy", default="street_comparable")
    normalize.add_argument("--years", default=None)
    normalize.add_argument("--fixture", action="store_true", help="Use bundled non-production fixtures instead of SEC")
    normalize.add_argument("--force-refresh", action="store_true")

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--ticker", required=True)
    inspect.add_argument("--year", type=int, required=True)

    export = subparsers.add_parser("export-golden")
    export.add_argument("--ticker", required=True)
    export.add_argument("--year", type=int, required=True)
    export.add_argument("--out", required=True)

    args = parser.parse_args()
    start_year, end_year = _parse_years(getattr(args, "years", None))

    if args.command == "collect-sec":
        try:
            docs = NormalizationService().collect_sec(
                args.ticker,
                start_year,
                end_year,
                args.force_refresh,
            )
        except EdgarConfigError as exc:
            raise SystemExit(str(exc)) from exc
        print(json.dumps([doc.model_dump(mode="json", exclude={"content"}) for doc in docs], indent=2))
        return

    if args.command == "normalize":
        service = _service_for(args.ticker, start_year, end_year, args.fixture, args.force_refresh)
        result = service.normalize(
            args.ticker,
            NormalizationPolicy(base_policy=args.policy),
            start_year,
            end_year,
        )
        print(result.model_dump_json(indent=2))
        return

    if args.command == "inspect":
        service = sample_normalization_service(args.ticker)
        result = service.normalize(
            args.ticker,
            NormalizationPolicy(),
            args.year,
            args.year,
        )
        print(result.model_dump_json(indent=2))
        return

    if args.command == "export-golden":
        service = sample_normalization_service(args.ticker)
        result = service.normalize(args.ticker, NormalizationPolicy(), args.year, args.year)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ticker": args.ticker.upper(),
            "fiscal_year": args.year,
            "pending": not bool(result.series),
            "result": result.model_dump(mode="json"),
        }
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {out}")


def _parse_years(value: str | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    if ":" in value:
        start, end = value.split(":", 1)
        return int(start), int(end)
    year = int(value)
    return year, year


def _service_for(
    ticker: str,
    start_year: int | None,
    end_year: int | None,
    fixture: bool,
    force_refresh: bool = False,
) -> NormalizationService:
    if fixture:
        return sample_normalization_service(ticker)
    service = NormalizationService()
    try:
        documents = service.collect_sec(ticker, start_year, end_year, force_refresh)
    except EdgarConfigError as exc:
        raise SystemExit(f"{exc}. Re-run with --fixture for bundled non-production fixtures.") from exc
    return NormalizationService(source_documents=documents)


if __name__ == "__main__":
    main()
