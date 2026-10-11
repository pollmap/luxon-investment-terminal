"""Offline, non-executing space review for a future time-series shadow copy.

Consumes already saved observations. This tool has no database/network client,
SQL execution or --apply mode. Sample estimates never authorize a rewrite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

GIB = 1024**3
MIB = 1024**2
NO_SAVINGS_FAMILIES = {"investor_trading"}


def natural(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def physical_models(study: dict) -> dict:
    models = {}
    for group in study.get("groups", []):
        family = group["family"]
        if family == "combined" or family in NO_SAVINGS_FAMILIES:
            continue
        old, new = group["old"], group["reference"]
        if not all(
            item.get("verification", {}).get("all_rows_equal") is True
            and item.get("verification", {}).get("all_source_trace_fields_equal") is True
            and item.get("index_valid") is True
            for item in (old, new)
        ):
            continue
        old_heap = natural(old["physical"][0]["heap_bytes"], "old heap")
        new_heap = natural(new["physical"][0]["heap_bytes"], "new heap")
        traces = sum(natural(p["total_bytes"], "trace total") for p in new["physical"][1:])
        if old_heap <= 0 or not new["physical"][1:]:
            continue
        measured = new_heap / old_heap
        # Explicit planning margins, not a confidence interval or physical bound.
        models[family] = {
            "sample_heap_ratio": measured,
            "planning_heap_ratio": min(1.0, max(measured * 1.25, measured + 0.10)),
            "planning_trace_ratio": traces / old_heap * 1.25,
        }
    return models


def prepare_report(observation: dict, study: dict) -> dict:
    relation, filesystem = observation["relation"], observation["filesystem"]
    heap = natural(relation["heap_bytes"], "heap")
    index = natural(relation["index_bytes"], "index")
    free = natural(filesystem["free_bytes"], "free")
    used = natural(filesystem["used_bytes"], "used")
    total = natural(filesystem["total_bytes"], "filesystem total")
    if heap == 0 or total == 0 or free + used > total:
        raise ValueError("invalid relation/filesystem observation")
    samples = observation.get("samples", [])
    if not samples or any(s.get("limit_reached") is not False for s in samples):
        raise ValueError("complete bounded samples required; truncated/unknown samples rejected")
    groups: dict = defaultdict(lambda: {"rows": 0, "row_bytes": 0, "reference_rows": 0})
    per_sample = []
    for sample in samples:
        sample_bytes = 0
        sample_rows = 0
        for family, group in sample["groups"].items():
            for key in ("rows", "row_bytes", "reference_rows"):
                groups[family][key] += natural(group[key], key)
            if group["reference_rows"] > group["rows"]:
                raise ValueError("reference count exceeds rows")
            sample_rows += group["rows"]
            sample_bytes += group["row_bytes"]
        if sample_rows != sample["rows"] or sample_bytes == 0:
            raise ValueError("sample totals inconsistent or empty")
        per_sample.append({k: v["row_bytes"] / sample_bytes for k, v in sample["groups"].items()})
    all_bytes = sum(v["row_bytes"] for v in groups.values())
    models = physical_models(study)
    rows = []
    for family, group in sorted(groups.items()):
        weight = group["row_bytes"] / all_bytes
        allocated_heap = math.ceil(heap * weight)
        model = models.get(family)
        # Counts cannot measure reference byte share precisely; this remains a scenario.
        ref_fraction = group["reference_rows"] / group["rows"] if group["rows"] else 1.0
        ratio = ref_fraction + (1 - ref_fraction) * model["planning_heap_ratio"] if model else 1.0
        rows.append(
            {
                "family": family,
                **group,
                "sample_byte_share": weight,
                "sample_share_min": min(s.get(family, 0.0) for s in per_sample),
                "sample_share_max": max(s.get(family, 0.0) for s in per_sample),
                "allocated_heap_estimate_bytes": allocated_heap,
                "model": model,
                "planning_new_heap_bytes": math.ceil(allocated_heap * ratio),
                "planning_added_trace_bytes": math.ceil(
                    allocated_heap * model["planning_trace_ratio"]
                )
                if model
                else 0,
                "policy": "sample_estimate_only" if model else "retain_inline_no_savings",
            }
        )
    # Counts are stale estimates; they are never used to assert actual row coverage.
    reservations = {
        "new_index_bytes": math.ceil(index * 1.20),
        "auxiliary_forks_bytes": 128 * MIB,
        "additional_wal_bytes": 2 * GIB,
        "temporary_sort_bytes": 2 * GIB,
        "minimum_free_reserve_bytes": 4 * GIB,
    }
    scenario = {
        "new_heap_bytes": sum(r["planning_new_heap_bytes"] for r in rows),
        "added_trace_bytes": sum(r["planning_added_trace_bytes"] for r in rows),
        **reservations,
    }
    scenario["required_incremental_bytes"] = sum(scenario.values())
    scenario["shortfall_bytes"] = max(0, scenario["required_incremental_bytes"] - free)
    # Keeping the original representation is the fallback for every unproved row.
    # This envelope is a reservation policy; WAL and page layout are not hard-capped.
    no_savings = {"new_heap_bytes": math.ceil(heap * 1.10), "added_trace_bytes": 0, **reservations}
    no_savings["required_incremental_bytes"] = sum(no_savings.values())
    no_savings["shortfall_bytes"] = max(0, no_savings["required_incremental_bytes"] - free)
    dependencies = observation.get("dependencies", {})
    expected = {
        "foreign_keys",
        "user_triggers",
        "dependent_views",
        "publications",
        "replication_slots",
    }
    dependency_clear = expected <= dependencies.keys() and all(
        dependencies[k] == 0 for k in expected
    )
    blockers = [
        "whole_population_physical_upper_bound_unproved",
        "writer_fence_and_backup_restore_not_verified_by_this_tool",
    ]
    if scenario["shortfall_bytes"]:
        blockers.append("sample_planning_scenario_exceeds_free_space")
    if no_savings["shortfall_bytes"]:
        blockers.append("no_savings_envelope_exceeds_free_space")
    if not dependency_clear:
        blockers.append("dependencies_missing_or_nonzero")
    return {
        "mode": "offline_preflight_only",
        "permission_to_execute": False,
        "status": "blocked_pending_full_rehearsal_and_maintenance_controls",
        "observed_at": observation.get("observed_at"),
        "release": observation.get("release"),
        "filesystem": filesystem,
        "relation": relation,
        "sample_rows_including_possible_page_overlap": sum(s["rows"] for s in samples),
        "family_estimates": rows,
        "sample_planning_scenario": scenario,
        "no_savings_planning_envelope": no_savings,
        # Match storage_budget.py, which includes filesystem-reserved blocks.
        "capacity_guard_used_percent": (total - free) / total * 100,
        "strict_below_80_percent_reclaim_bytes": max(0, total // 5 + 1 - free),
        "dependencies_observed_clear": dependency_clear,
        "blockers": blockers,
        "limits": [
            "SYSTEM samples are clustered, not a confidence interval; "
            "unobserved families remain unknown.",
            "Physical models use 3 companies, not the full universe; no full-table savings claim.",
            "Trace sharing may differ; reference fraction uses counts, not exact byte allocation.",
            "WAL reservation is not max_wal_size enforcement; "
            "retained WAL differs from generated WAL.",
            "Old table remains allocated until explicitly retired; rename returns no OS space.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--physical-study", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    inputs = {}
    evidence = []
    for label, path in (("observation", args.observation), ("study", args.physical_study)):
        raw = path.read_bytes()
        inputs[label] = json.loads(raw)
        evidence.append({"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest()})
    report = prepare_report(inputs["observation"], inputs["study"])
    report["evidence"] = evidence
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf8") as output:
            output.write(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
