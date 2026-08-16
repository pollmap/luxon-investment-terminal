from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CliPlan:
    command: str
    status: str
    dry_run: bool
    source_trace_required: bool
    available_at_required: bool
    data_generation_allowed: bool
    execution_plan: list[str]
    next_command: str | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="luxon",
        description="LUXON Investment Terminal operator CLI.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output. Dry-runs always emit JSON.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Plan source ingestion.")
    ingest.add_argument("--market", choices=["US", "KR", "JP"], required=True)
    ingest.add_argument("--ticker", required=True)
    ingest.add_argument("--years", default="2020:2025")
    ingest.add_argument("--dry-run", action="store_true")

    backfill = subparsers.add_parser("backfill", help="Plan historical source backfill.")
    backfill.add_argument("--market", choices=["US", "KR", "JP"], required=True)
    backfill.add_argument("--tickers", required=True)
    backfill.add_argument("--years", default="2020:2025")
    backfill.add_argument("--dry-run", action="store_true")

    value = subparsers.add_parser("value", help="Plan deterministic valuation run.")
    value.add_argument("--ticker", required=True)
    value.add_argument("--metric", default="adjusted_operating")
    value.add_argument("--as-of", dest="as_of", default=None)
    value.add_argument("--dry-run", action="store_true")

    screen = subparsers.add_parser("screen", help="Plan source-traced screener run.")
    screen.add_argument("--preset", default="core_quality")
    screen.add_argument("--dry-run", action="store_true")

    audit = subparsers.add_parser("audit", help="Plan source trace inspection.")
    audit.add_argument("--fact-id", required=True)
    audit.add_argument("--dry-run", action="store_true")

    score = subparsers.add_parser("score", help="Plan source-traced scorecard run.")
    score.add_argument("--ticker", required=True)
    score.add_argument("--dry-run", action="store_true")

    return parser


def _plan_for(args: argparse.Namespace) -> CliPlan:
    command = str(args.command)
    if command == "ingest":
        steps = [
            f"collect raw source payloads for {args.market}:{args.ticker}",
            "persist raw payload append-only with content hash",
            "normalize facts only after source_trace and available_at are present",
        ]
        next_command = (
            "python -m services.ingestion_worker.cli run-source-e2e "
            f"--market {args.market} --tickers {args.ticker} --years {args.years} --dry-run"
        )
    elif command == "backfill":
        steps = [
            f"plan backfill for {args.market} tickers {args.tickers}",
            "collect source documents before metric promotion",
            "version restatements by filing and available_at",
        ]
        next_command = (
            "python -m services.ingestion_worker.cli data-lake-plan "
            f"--markets {args.market} --years {args.years} --format markdown"
        )
    elif command == "value":
        steps = [
            f"load source-traced metric series for {args.ticker}",
            f"select metric {args.metric}",
            "run deterministic valuation formulas without LLM-generated numbers",
            "return formula, input fact ids, method applicability, and confidence",
        ]
        next_command = (
            "curl \"http://127.0.0.1:8000/api/v1/companies/"
            f"{args.ticker}/valuation-map?metric={args.metric}\""
        )
    elif command == "screen":
        steps = [
            f"load source-backed universe for preset {args.preset}",
            "evaluate metric-to-value, metric-to-metric, and company-relative filters",
            "attach source_trace to every result row",
        ]
        next_command = "curl \"http://127.0.0.1:8000/api/v1/screener\""
    elif command == "audit":
        steps = [
            f"load fact {args.fact_id}",
            "expand source evidence, formula, quality, and input traces",
            "reject audit views that lack source_trace",
        ]
        next_command = f"curl \"http://127.0.0.1:8000/api/data-audit/{args.fact_id}\""
    elif command == "score":
        steps = [
            f"load scorecard inputs for {args.ticker}",
            "evaluate Past, Present, Future, Health, Dividend, and Macro axes",
            "connect each checklist result to Data Audit",
        ]
        next_command = f"curl \"http://127.0.0.1:8000/api/v1/companies/{args.ticker}/health-check\""
    else:
        raise ValueError(f"unsupported command: {command}")

    return CliPlan(
        command=command,
        status="planned",
        dry_run=bool(getattr(args, "dry_run", False)),
        source_trace_required=True,
        available_at_required=True,
        data_generation_allowed=False,
        execution_plan=steps,
        next_command=next_command,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    plan = _plan_for(args)

    if plan.dry_run:
        print(json.dumps(asdict(plan), ensure_ascii=False, indent=2))
        return 0

    message = {
        **asdict(plan),
        "status": "not_executed",
        "reason": (
            "The package CLI is a safe P0 operator facade. Use --dry-run, or run the "
            "source-specific services.ingestion_worker/backend.normalize commands listed "
            "in next_command for live execution."
        ),
    }
    output = json.dumps(message, ensure_ascii=False, indent=2)
    if getattr(args, "json", False):
        print(output)
    else:
        print(output, file=sys.stderr)
    return 2
