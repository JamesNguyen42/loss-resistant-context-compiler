from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import ctxc_openhands.scenario as scenario_module
import ctxc_openhands.soak as soak_module
from ctxc_openhands.evidence import (
    EvidenceValidationError,
    canonical_json_bytes,
    finalize_evidence_report,
    validate_evidence_report,
)
from ctxc_openhands.soak import (
    MAX_SOAK_COMPACTIONS,
    MAX_SOAK_EVENTS,
    run_deterministic_soak,
)


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


@pytest.mark.parametrize(
    ("event_count", "compaction_count", "message"),
    [
        (MAX_SOAK_EVENTS + 1, 1, rf"event_count cannot exceed {MAX_SOAK_EVENTS}"),
        (
            MAX_SOAK_EVENTS,
            MAX_SOAK_COMPACTIONS + 1,
            rf"compaction_count cannot exceed {MAX_SOAK_COMPACTIONS}",
        ),
    ],
)
def test_soak_rejects_max_plus_one_before_creating_database(
    tmp_path: Path,
    event_count: int,
    compaction_count: int,
    message: str,
) -> None:
    database = tmp_path / "must-not-exist.sqlite3"

    with pytest.raises(ValueError, match=message):
        run_deterministic_soak(
            database,
            event_count=event_count,
            compaction_count=compaction_count,
        )

    assert not database.exists()


def test_soak_cli_argv_exactly_reconciles_effective_parameters(
    tmp_path: Path,
) -> None:
    report = run_deterministic_soak(
        tmp_path / "soak.sqlite3",
        event_count=8,
        compaction_count=2,
        restart_every_compactions=1,
    )
    parameters = report["command"]["parameters"]
    valid = _unsigned_copy(report)
    valid["command"] = {
        "producer": "cli",
        "name": "ctxc-openhands",
        "argv": [
            "soak",
            f"--database={parameters['database']}",
            "--events",
            "8",
            "--compactions=2",
            "--restart-every",
            "1",
            "--output",
            "retained.json",
        ],
        "parameters": parameters,
    }
    valid_report = finalize_evidence_report(valid)

    assert validate_evidence_report(valid_report) == valid_report

    invalid = _unsigned_copy(valid_report)
    invalid["command"]["argv"] = [
        "soak",
        "--database",
        parameters["database"],
        "--events",
        "9",
        "--compactions",
        "2",
        "--restart-every",
        "1",
    ]
    invalid_report = finalize_evidence_report(invalid)
    with pytest.raises(EvidenceValidationError, match="CLI soak counts"):
        validate_evidence_report(invalid_report)


@pytest.mark.parametrize(
    ("module", "runner", "kwargs"),
    [
        (
            scenario_module,
            scenario_module.run_offline_crash_scenario,
            {},
        ),
        (
            soak_module,
            soak_module.run_deterministic_soak,
            {"event_count": 2, "compaction_count": 1},
        ),
    ],
)
def test_evidence_producers_fail_closed_on_red_database_self_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    runner: Any,
    kwargs: dict[str, Any],
) -> None:
    monkeypatch.setattr(
        module,
        "_verify_evidence_mapping",
        lambda _report, *, database: {
            "passed": False,
            "database_verified": False,
            "issues": [f"forced-red:{database}"],
        },
    )
    database = tmp_path / f"{module.__name__.rsplit('.', 1)[-1]}.sqlite3"

    with pytest.raises(AssertionError, match="self-verification failed"):
        runner(database, **kwargs)

    assert database.exists()
