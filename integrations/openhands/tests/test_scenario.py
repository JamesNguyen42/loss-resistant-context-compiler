from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ctxc_openhands.scenario import (
    LIVE_DEPENDENCY_BLOCKER,
    OFFLINE_SCENARIO_SCHEMA,
    run_offline_crash_scenario,
)


def _digest_without_self_hash(report: dict) -> str:
    unsigned = {key: value for key, value in report.items() if key != "report_sha256"}
    text = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_offline_scenario_records_three_compactions_and_recovery(
    tmp_path: Path,
) -> None:
    report = run_offline_crash_scenario(tmp_path / "scenario.sqlite3")

    assert report["schema"] == OFFLINE_SCENARIO_SCHEMA
    assert report["status"] == "passed-offline-fake-runtime"
    assert report["evidence_kind"] == "offline-crash-scenario"
    assert report["live_openhands"]["status"] == "blocked-not-run"
    assert report["live_openhands"]["blocker"] == LIVE_DEPENDENCY_BLOCKER
    assert report["forced_compaction_count"] == 3
    assert report["session"]["source_count"] == 3
    assert len(report["compactions"]) == 3
    assert report["compactions"][-1]["recovered_after_injected_crash"] is True
    assert report["constraints_retained_exactly"] is True
    assert report["semantic_completeness_claimed"] is False
    assert report["final_request"]["replay"]["passed"] is True
    assert report["command"]["producer"] == "library-api"
    assert report["command"]["argv"] == []
    assert report["isolation"] == {
        "execution_path_network_capability": "none",
        "network_isolation_enforced": False,
        "paid_service_use": "none",
    }
    assert report["database"]["byte_length"] > 0
    assert report["report_sha256"] == _digest_without_self_hash(report)


def test_scenario_refuses_to_overwrite_evidence_database(tmp_path: Path) -> None:
    database = tmp_path / "scenario.sqlite3"
    database.write_bytes(b"retained-failed-run")

    with pytest.raises(FileExistsError, match="already exists"):
        run_offline_crash_scenario(database)

    assert database.read_bytes() == b"retained-failed-run"
