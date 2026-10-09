"""Fixture-only protection of an offline maintenance report, never a DB rewrite."""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_timeseries_rewrite.py"
SPEC = importlib.util.spec_from_file_location("rewrite_preflight", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def fixture():
    observation = {
        "relation": {"heap_bytes": 20 * module.GIB, "index_bytes": module.GIB},
        "filesystem": {
            "free_bytes": 20 * module.GIB,
            "used_bytes": 80 * module.GIB,
            "total_bytes": 100 * module.GIB,
        },
        "samples": [
            {
                "rows": 10,
                "limit_reached": False,
                "groups": {
                    "prices": {"rows": 10, "row_bytes": 10000, "reference_rows": 0},
                },
            }
        ],
        "dependencies": {
            key: 0
            for key in (
                "foreign_keys",
                "user_triggers",
                "dependent_views",
                "publications",
                "replication_slots",
            )
        },
    }
    check = {"all_rows_equal": True, "all_source_trace_fields_equal": True}
    study = {
        "groups": [
            {
                "family": "prices",
                "old": {
                    "verification": check,
                    "index_valid": True,
                    "physical": [{"heap_bytes": 10000}],
                },
                "reference": {
                    "verification": check,
                    "index_valid": True,
                    "physical": [{"heap_bytes": 3000}, {"total_bytes": 1000}],
                },
            }
        ]
    }
    return observation, study


def test_even_ample_space_never_authorizes_apply():
    observation, study = fixture()
    observation["filesystem"].update(free_bytes=90 * module.GIB, used_bytes=10 * module.GIB)
    report = module.prepare_report(observation, study)
    assert report["permission_to_execute"] is False
    assert "whole_population_physical_upper_bound_unproved" in report["blockers"]


@pytest.mark.parametrize("family", ["investor_trading", "previously_unobserved_source"])
def test_unproven_family_retains_full_heap_without_savings(family):
    observation, study = fixture()
    observation["samples"][0]["groups"][family] = observation["samples"][0]["groups"].pop("prices")
    row = module.prepare_report(observation, study)["family_estimates"][0]
    assert row["planning_new_heap_bytes"] == observation["relation"]["heap_bytes"]
    assert row["planning_added_trace_bytes"] == 0


def test_unverified_physical_model_cannot_reduce_budget():
    observation, study = fixture()
    study["groups"][0]["reference"]["verification"]["all_rows_equal"] = False
    assert (
        module.prepare_report(observation, study)["family_estimates"][0]["policy"]
        == "retain_inline_no_savings"
    )


def test_existing_references_cannot_claim_second_compaction_saving():
    observation, study = fixture()
    observation["samples"][0]["groups"]["prices"]["reference_rows"] = 10
    row = module.prepare_report(observation, study)["family_estimates"][0]
    assert row["planning_new_heap_bytes"] == observation["relation"]["heap_bytes"]


def test_reservations_include_index_traces_wal_temp_and_4gib():
    observation, study = fixture()
    budget = module.prepare_report(observation, study)["sample_planning_scenario"]
    assert budget["new_index_bytes"] > observation["relation"]["index_bytes"]
    assert budget["added_trace_bytes"] > 0
    assert budget["additional_wal_bytes"] == 2 * module.GIB
    assert budget["temporary_sort_bytes"] == 2 * module.GIB
    assert budget["minimum_free_reserve_bytes"] >= 4 * module.GIB
    assert budget["required_incremental_bytes"] == sum(
        v for k, v in budget.items() if k not in ("required_incremental_bytes", "shortfall_bytes")
    )


@pytest.mark.parametrize("case", ["truncated", "empty", "bad_total", "negative", "nan"])
def test_invalid_or_censored_inputs_fail_closed(case):
    observation, study = fixture()
    if case == "truncated":
        observation["samples"][0]["limit_reached"] = True
    elif case == "empty":
        observation["samples"] = []
    elif case == "bad_total":
        observation["samples"][0]["rows"] = 11
    elif case == "negative":
        observation["filesystem"]["free_bytes"] = -1
    else:
        observation["relation"]["heap_bytes"] = float("nan")
    with pytest.raises(ValueError):
        module.prepare_report(observation, study)


def test_missing_dependencies_remain_blocking_and_inputs_unchanged():
    observation, study = fixture()
    observation.pop("dependencies")
    original = copy.deepcopy((observation, study))
    report = module.prepare_report(observation, study)
    assert "dependencies_missing_or_nonzero" in report["blockers"]
    assert (observation, study) == original


def test_strict_80_percent_threshold_requires_one_more_byte():
    observation, study = fixture()
    assert module.prepare_report(observation, study)["strict_below_80_percent_reclaim_bytes"] == 1


def test_resume_guard_includes_filesystem_reserved_blocks():
    observation, study = fixture()
    observation["filesystem"]["free_bytes"] -= module.GIB
    report = module.prepare_report(observation, study)
    assert report["strict_below_80_percent_reclaim_bytes"] == module.GIB + 1
    assert report["capacity_guard_used_percent"] == 81


def test_apply_option_is_not_accepted():
    result = subprocess.run([sys.executable, str(SCRIPT), "--apply"], capture_output=True)
    assert result.returncode == 2


def test_cli_preserves_existing_evidence(tmp_path):
    observation, study = fixture()
    source = tmp_path / "observation.json"
    physical = tmp_path / "study.json"
    output = tmp_path / "report.json"
    source.write_text(json.dumps(observation), encoding="utf8")
    physical.write_text(json.dumps(study), encoding="utf8")
    output.write_text("existing evidence", encoding="utf8")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--observation",
            str(source),
            "--physical-study",
            str(physical),
            "--output",
            str(output),
        ],
        capture_output=True,
    )
    assert result.returncode != 0
    assert output.read_text(encoding="utf8") == "existing evidence"
