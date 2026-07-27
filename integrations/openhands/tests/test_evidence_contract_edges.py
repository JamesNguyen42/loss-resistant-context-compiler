from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ctxc_openhands.evidence import (
    EvidenceValidationError,
    canonical_json_bytes,
    finalize_evidence_report,
    validate_evidence_report,
)
from ctxc_openhands.scenario import run_offline_crash_scenario


def _unsigned_copy(report: dict[str, Any]) -> dict[str, Any]:
    return json.loads(
        canonical_json_bytes(
            {
                key: value
                for key, value in report.items()
                if key != "report_sha256"
            }
        )
    )


def test_generation_state_vocabulary_accepts_rolled_back_not_abandoned(
    tmp_path: Path,
) -> None:
    report = run_offline_crash_scenario(tmp_path / "scenario.sqlite3")
    rolled_back = _unsigned_copy(report)
    rolled_back["session"]["generation_states"][0]["state"] = "rolled_back"
    rolled_back_report = finalize_evidence_report(rolled_back)

    assert validate_evidence_report(rolled_back_report) == rolled_back_report

    abandoned = _unsigned_copy(report)
    abandoned["session"]["generation_states"][0]["state"] = "abandoned"
    abandoned_report = finalize_evidence_report(abandoned)
    with pytest.raises(EvidenceValidationError, match="generation state is unknown"):
        validate_evidence_report(abandoned_report)


def test_cli_producer_argv_must_exactly_reconcile_effective_parameters(
    tmp_path: Path,
) -> None:
    report = run_offline_crash_scenario(tmp_path / "scenario.sqlite3")
    database = report["command"]["parameters"]["database"]
    valid = _unsigned_copy(report)
    valid["command"] = {
        "producer": "cli",
        "name": "ctxc-openhands",
        "argv": [
            "offline-scenario",
            "--database",
            database,
            "--output=retained.json",
        ],
        "parameters": {"database": database},
    }
    valid_report = finalize_evidence_report(valid)
    assert validate_evidence_report(valid_report) == valid_report

    invalid_argvs = (
        [
            "offline-scenario",
            "--database",
            str(tmp_path / "wrong.sqlite3"),
        ],
        [
            "offline-scenario",
            "--database",
            database,
            "--database",
            database,
        ],
        ["offline-scenario", "--database", database, "--unknown", "value"],
        ["offline-scenario", "--database"],
    )
    for argv in invalid_argvs:
        invalid = _unsigned_copy(valid_report)
        invalid["command"]["argv"] = argv
        resigned = finalize_evidence_report(invalid)
        with pytest.raises(
            EvidenceValidationError,
            match="command|CLI|argument|option|database",
        ):
            validate_evidence_report(resigned)
