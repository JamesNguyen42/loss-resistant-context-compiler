from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ctxc_openhands.evidence import (
    LIVE_DEPENDENCY_BLOCKER,
    validate_evidence_report,
    verify_evidence,
    write_evidence_report,
)
from ctxc_openhands.soak import SOAK_REPORT_SCHEMA, run_deterministic_soak


def _report_digest(report: dict) -> str:
    unsigned = {key: value for key, value in report.items() if key != "report_sha256"}
    raw = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_small_uninterrupted_and_restarted_soaks_are_semantically_equal(
    tmp_path: Path,
) -> None:
    uninterrupted_database = tmp_path / "uninterrupted.sqlite3"
    restarted_database = tmp_path / "restarted.sqlite3"
    uninterrupted = run_deterministic_soak(
        uninterrupted_database,
        event_count=20,
        compaction_count=4,
    )
    restarted = run_deterministic_soak(
        restarted_database,
        event_count=20,
        compaction_count=4,
        restart_every_compactions=1,
    )

    for index, (report, database) in enumerate(
        (
            (uninterrupted, uninterrupted_database),
            (restarted, restarted_database),
        )
    ):
        assert report["schema"] == SOAK_REPORT_SCHEMA
        assert report["evidence_kind"] == "deterministic-soak"
        assert report["status"] == "passed"
        assert report["command"]["producer"] == "library-api"
        assert report["command"]["argv"] == []
        assert report["command"]["parameters"]["database"] == str(database.absolute())
        assert report["command"]["parameters"]["events"] == 20
        assert report["command"]["parameters"]["compactions"] == 4
        assert report["runtime"]["python"]
        assert report["runtime"]["sqlite"]
        assert report["isolation"] == {
            "execution_path_network_capability": "none",
            "network_isolation_enforced": False,
            "paid_service_use": "none",
        }
        assert report["live_openhands"] == {
            "status": "blocked-not-run",
            "blocker": LIVE_DEPENDENCY_BLOCKER,
        }
        assert report["event_count"] == 20
        assert report["compaction_count"] == 4
        assert report["session"]["source_count"] == 20
        assert report["session"]["active_epoch"] == 4
        assert len(report["session"]["generation_states"]) == 4
        assert sum(
            state["state"] == "active"
            for state in report["session"]["generation_states"]
        ) == 1
        assert report["no_event_loss"] is True
        assert report["no_event_duplication"] is True
        assert report["contiguous_sequences"] is True
        assert report["semantic_completeness_claimed"] is False
        assert report["report_sha256"] == _report_digest(report)
        database_bytes = database.read_bytes()
        assert report["database"]["byte_length"] == len(database_bytes)
        assert report["database"]["sha256"] == hashlib.sha256(
            database_bytes
        ).hexdigest()
        assert validate_evidence_report(report) == report

        report_path = tmp_path / f"soak-{index}.json"
        write_evidence_report(report_path, report)
        verification = verify_evidence(report_path, database=database)
        assert verification["passed"] is True
        assert verification["scope"] == "json-and-database"
        assert verification["report_verified"] is True
        assert verification["database_supplied"] is True
        assert verification["database_verified"] is True
        assert verification["attestation_claimed"] is False
        assert verification["semantic_completeness_claimed"] is False
        assert verification["issues"] == []

    assert (
        uninterrupted["session"]["source_head_sha256"]
        == restarted["session"]["source_head_sha256"]
    )
    assert uninterrupted["generation_ids"] == restarted["generation_ids"]
    assert uninterrupted["bundle_sha256s"] == restarted["bundle_sha256s"]
    assert (
        uninterrupted["semantic_result_digests"]
        == restarted["semantic_result_digests"]
    )
    assert (
        uninterrupted["session"]["active_semantic_result_digest"]
        == restarted["session"]["active_semantic_result_digest"]
    )


@pytest.mark.parametrize(
    ("event_count", "compaction_count", "message"),
    [
        (0, 1, "event_count must be positive"),
        (1, 0, "compaction_count must be positive"),
        (2, 3, "cannot exceed"),
    ],
)
def test_soak_rejects_invalid_bounds(
    tmp_path: Path,
    event_count: int,
    compaction_count: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        run_deterministic_soak(
            tmp_path / "invalid.sqlite3",
            event_count=event_count,
            compaction_count=compaction_count,
        )


def test_soak_refuses_to_overwrite_database(tmp_path: Path) -> None:
    database = tmp_path / "existing.sqlite3"
    database.touch()

    with pytest.raises(FileExistsError, match="already exists"):
        run_deterministic_soak(
            database,
            event_count=1,
            compaction_count=1,
        )
