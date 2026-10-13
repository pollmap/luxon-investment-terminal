"""Bounded job-metadata audit; source receipts are not full-history validation."""

import json
import re
from collections import Counter
from datetime import datetime


def terminal_claim(kind, state):
    return bool(
        state.get("backfill_complete")
        or (
            kind in ("history", "raw-history")
            and state.get("status") == "ready"
            and not state.get("next_before")
        )
    )


def terminal_receipt_evidenced(kind, snapshot, code):
    if not isinstance(snapshot, dict):
        return False
    terminal = snapshot.get("history_terminal")
    if not isinstance(terminal, dict) or terminal.get("source") != "toss":
        return False
    cursor = "next_before" if kind in ("history", "raw-history") else "next_until"
    explicit = (
        "explicit_next_before_null" if cursor == "next_before" else "explicit_next_until_null"
    )
    if terminal.get(explicit) is not True or snapshot.get(cursor) is not None:
        return False
    if snapshot.get("gap_next_before") or snapshot.get("gap_status") in (
        "collecting",
        "source_gap",
    ):
        return False
    try:
        received = datetime.fromisoformat(terminal["received_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if received.tzinfo is None:
        return False
    aliases = [terminal.get(field) for field in ("sha256", "source_document_id")]
    aliases = [value for value in aliases if value is not None]
    if not aliases or any(
        not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for value in aliases
    ):
        return False
    if len(set(aliases)) != 1:
        return False
    venue = (
        "통합"
        if kind in ("history", "raw-history", "investor-trading")
        else "KRX"
        if kind == "program-trades"
        else "국내"
    )
    expected = {"code": code, "kind": kind, "venue": venue}
    if kind in ("history", "raw-history"):
        expected["adjustment"] = "source" if kind == "history" else "raw"
    return all(terminal.get(field) == value for field, value in expected.items())


def build_report(store, kinds, observed_at):
    codes = set(store.listed_codes())
    jobs = store.list_prefix("toss-backfill:")
    report = {
        "at": observed_at,
        "universe": len(codes),
        "basis": "job_metadata_and_explicit_terminal_receipts",
        "independently_verified_complete_count": None,
        "last_page_received_meaning": (
            "logical_terminal_receipt_metadata_only_not_original_or_whole_history_validation"
        ),
        "kinds": {},
    }
    for kind in kinds:
        states = {
            key.split(":")[1]: value
            for key, value in jobs.items()
            if key.endswith(":" + kind) and key.split(":")[1] in codes
        }
        counts = Counter()
        for code, state in states.items():
            if state.get("status") == "error":
                outcome = (
                    "authentication_error"
                    if state.get("http_status") in (401, 403)
                    else "provider_404_unresolved"
                    if state.get("http_status") == 404
                    else "request_or_format_error"
                )
            elif state.get("status") == "not_in_provider_catalogue":
                outcome = "provider_catalogue_omission"
            elif state.get("status") == "no_data":
                outcome = "source_empty"
            elif terminal_claim(kind, state):
                key = (
                    f"bars:{code}:통합:" + ("day" if kind == "history" else "raw")
                    if kind in ("history", "raw-history")
                    else f"toss:{kind}:{code}"
                )
                snapshot = store.metadata(key, {}) or {}
                outcome = (
                    "last_page_received"
                    if terminal_receipt_evidenced(kind, snapshot, code)
                    else "terminal_unverified"
                )
            else:
                outcome = "older_pages_pending"
            counts[outcome] += 1
        counts["no_collection_job"] = len(codes) - len(states)
        report["kinds"][kind] = {
            "metadata_terminal_receipt": counts["last_page_received"],
            "securities": dict(counts),
            "reported_rows": sum(r.get("rows", 0) for r in states.values()),
            "last_page_fraction": counts["last_page_received"] / len(codes) if codes else 0,
            "provider_catalogue_omission_fraction": counts["provider_catalogue_omission"]
            / len(codes)
            if codes
            else 0,
            "error_types": dict(
                Counter(
                    f"{v.get('error')}:{v.get('http_status')}"
                    for v in states.values()
                    if v.get("status") == "error"
                )
            ),
            "error_samples": [
                {k: v.get(k) for k in ("error", "http_status", "failures", "attempted_at")}
                | {"code": c}
                for c, v in states.items()
                if v.get("status") == "error"
            ][:5],
        }
    report["related_company_repair"] = {
        key.split(":", 1)[1]: {"rows": len(value.get("rows", [])), "reason": value.get("reason")}
        for key, value in store.list_prefix("corporate-event-quarantine:").items()
    }
    report["public_events_402340"] = len(store.get("events:402340", {}).get("rows", []))
    report["progress"] = store.get("toss-backfill-progress", {})
    return report


def main():
    from services.market_dashboard.store import Store, now
    from services.market_dashboard.toss_backfill import KINDS

    print(json.dumps(build_report(Store(), KINDS, now()), ensure_ascii=False))


if __name__ == "__main__":
    main()
