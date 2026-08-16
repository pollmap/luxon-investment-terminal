from __future__ import annotations

import json

from packages.cli.main import main


def test_nexus_cli_ingest_dry_run_preserves_data_contract(capsys) -> None:
    code = main(["ingest", "--market", "US", "--ticker", "AAPL", "--dry-run"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "ingest"
    assert payload["source_trace_required"] is True
    assert payload["available_at_required"] is True
    assert payload["data_generation_allowed"] is False
    assert "source_trace" in " ".join(payload["execution_plan"])


def test_nexus_cli_live_execution_is_explicitly_not_wired(capsys) -> None:
    code = main(["value", "--ticker", "AAPL"])

    assert code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "not_executed"
    assert payload["data_generation_allowed"] is False
    assert "next_command" in payload
