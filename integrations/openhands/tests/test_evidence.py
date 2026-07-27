from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

import ctxc_openhands.evidence as evidence_module
from ctxc_openhands.evidence import (
    EVIDENCE_VERIFICATION_SCHEMA,
    MAX_EVIDENCE_JSON_BYTES,
    EvidenceValidationError,
    canonical_json_bytes,
    finalize_evidence_report,
    load_evidence_report,
    validate_evidence_report,
    verify_evidence,
    write_evidence_report,
)
from ctxc_openhands.scenario import run_offline_crash_scenario
from ctxc_openhands.storage import SQLiteGenerationStore


@pytest.fixture()
def scenario_evidence(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    database = tmp_path / "scenario.sqlite3"
    report_path = tmp_path / "scenario.json"
    report = run_offline_crash_scenario(database)
    write_evidence_report(report_path, report)
    return report_path, database, report


def _resigned(report: dict[str, Any], **changes: Any) -> dict[str, Any]:
    unsigned = {
        key: value for key, value in report.items() if key != "report_sha256"
    }
    unsigned.update(changes)
    return finalize_evidence_report(unsigned)


def test_scenario_evidence_verifies_report_and_bound_database(
    scenario_evidence: tuple[Path, Path, dict[str, Any]],
) -> None:
    report_path, database, report = scenario_evidence

    result = verify_evidence(report_path, database=database)

    assert result["schema"] == EVIDENCE_VERIFICATION_SCHEMA
    assert result["passed"] is True
    assert result["scope"] == "json-and-database"
    assert result["report_verified"] is True
    assert result["database_supplied"] is True
    assert result["database_verified"] is True
    assert result["attestation_claimed"] is False
    assert result["semantic_completeness_claimed"] is False
    assert result["evidence_report_sha256"] == report["report_sha256"]
    assert result["issues"] == []
    assert result["limitations"] == []


def test_json_only_verification_is_explicitly_not_an_overall_pass(
    scenario_evidence: tuple[Path, Path, dict[str, Any]],
) -> None:
    report_path, _database, _report = scenario_evidence

    result = verify_evidence(report_path)

    assert result["passed"] is False
    assert result["scope"] == "json-only"
    assert result["report_verified"] is True
    assert result["database_supplied"] is False
    assert result["database_verified"] is False
    assert result["attestation_claimed"] is False
    assert result["limitations"] == [
        "database not supplied; SQLite bytes, source history, generations, "
        "and request ledgers were not verified"
    ]


def test_report_self_hash_tamper_fails_closed(
    scenario_evidence: tuple[Path, Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    _report_path, _database, report = scenario_evidence
    tampered = dict(report)
    tampered["status"] = "passed"
    path = tmp_path / "tampered.json"
    write_evidence_report(path, tampered)

    with pytest.raises(EvidenceValidationError, match="status must equal"):
        verify_evidence(path)


def test_resigned_unknown_and_missing_fields_fail_closed(
    scenario_evidence: tuple[Path, Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    _report_path, _database, report = scenario_evidence
    unknown = _resigned(report, unexpected="not allowed")
    unknown_path = tmp_path / "unknown.json"
    write_evidence_report(unknown_path, unknown)

    missing_unsigned = {
        key: value
        for key, value in report.items()
        if key not in {"report_sha256", "constraints"}
    }
    missing = finalize_evidence_report(missing_unsigned)
    missing_path = tmp_path / "missing.json"
    write_evidence_report(missing_path, missing)

    with pytest.raises(EvidenceValidationError, match="fields are not exact"):
        verify_evidence(unknown_path)
    with pytest.raises(EvidenceValidationError, match="fields are not exact"):
        verify_evidence(missing_path)


def test_noncanonical_duplicate_invalid_utf8_and_oversize_reports_fail_closed(
    scenario_evidence: tuple[Path, Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    _report_path, _database, report = scenario_evidence
    noncanonical = tmp_path / "pretty.json"
    noncanonical.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(b'{"schema":"a","schema":"b"}')
    invalid_utf8 = tmp_path / "invalid-utf8.json"
    invalid_utf8.write_bytes(b'{"schema":"\xff"}')
    oversize = tmp_path / "oversize.json"
    oversize.write_bytes(b" " * (MAX_EVIDENCE_JSON_BYTES + 2))

    with pytest.raises(EvidenceValidationError, match="canonical JSON"):
        load_evidence_report(noncanonical)
    with pytest.raises(EvidenceValidationError, match="duplicate JSON key"):
        load_evidence_report(duplicate)
    with pytest.raises(EvidenceValidationError, match="not valid UTF-8"):
        load_evidence_report(invalid_utf8)
    with pytest.raises(EvidenceValidationError, match="byte length is invalid"):
        load_evidence_report(oversize)


def test_deep_and_flat_bounded_json_fail_closed(tmp_path: Path) -> None:
    deep = tmp_path / "deep.json"
    deep.write_bytes(b'{"x":' + b"[" * 70 + b"0" + b"]" * 70 + b"}")
    flat = tmp_path / "flat.json"
    flat.write_bytes(
        b'{"x":['
        + b",".join(b"0" for _ in range(evidence_module._MAX_JSON_ITEMS + 1))
        + b"]}"
    )

    with pytest.raises(
        EvidenceValidationError,
        match="depth limit|bounded valid JSON",
    ):
        load_evidence_report(deep)
    with pytest.raises(EvidenceValidationError, match="item limit"):
        load_evidence_report(flat)


def test_wrong_database_and_sidecar_fail_without_opening_wrong_bytes(
    scenario_evidence: tuple[Path, Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    report_path, database, _report = scenario_evidence
    other_database = tmp_path / "other.sqlite3"
    run_offline_crash_scenario(other_database)

    wrong = verify_evidence(report_path, database=other_database)
    assert wrong["passed"] is False
    assert wrong["database_verified"] is False
    assert wrong["issues"] == [
        "database SHA-256 does not match the retained evidence"
    ]

    wal = Path(str(database) + "-wal")
    wal.write_bytes(b"not-a-valid-sidecar")
    with_sidecar = verify_evidence(report_path, database=database)
    assert with_sidecar["passed"] is False
    assert with_sidecar["database_verified"] is False
    assert with_sidecar["issues"] == [
        "database WAL/SHM sidecars must be absent before verification"
    ]


def test_database_mutation_during_reconciliation_is_not_verified(
    scenario_evidence: tuple[Path, Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path, database, _report = scenario_evidence
    real_store = SQLiteGenerationStore

    class MutatingStore:
        def __init__(self, path: str | Path, **kwargs: Any) -> None:
            self.path = Path(path)
            self.inner = real_store(path, **kwargs)
            self.mutated = False

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        def integrity_report(self) -> dict[str, Any]:
            value = self.inner.integrity_report()
            if not self.mutated:
                with self.path.open("ab") as handle:
                    handle.write(b"mutation")
                    handle.flush()
                    os.fsync(handle.fileno())
                self.mutated = True
            return value

    monkeypatch.setattr(evidence_module, "SQLiteGenerationStore", MutatingStore)
    result = verify_evidence(report_path, database=database)

    assert result["passed"] is False
    assert result["database_verified"] is False
    assert "database changed during SQLite reconciliation" in result["issues"]


def test_evidence_report_write_is_exclusive_and_race_safe(
    scenario_evidence: tuple[Path, Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    _report_path, _database, report = scenario_evidence
    target = tmp_path / "exclusive.json"
    target.write_bytes(b"retained-failure")

    with pytest.raises(FileExistsError):
        write_evidence_report(target, report)
    assert target.read_bytes() == b"retained-failure"

    raced = tmp_path / "race.json"

    def write_once() -> str:
        try:
            write_evidence_report(raced, report)
        except FileExistsError:
            return "exists"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _index: write_once(), range(2)))

    assert outcomes == ["created", "exists"]
    assert load_evidence_report(raced) == report


def test_database_binding_matches_checkpointed_file_bytes(
    scenario_evidence: tuple[Path, Path, dict[str, Any]],
) -> None:
    _report_path, database, report = scenario_evidence
    raw = database.read_bytes()

    assert report["database"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["database"]["byte_length"] == len(raw)
    assert report["database"]["checkpoint"]["mode"] == "TRUNCATE"
    assert report["database"]["checkpoint"]["busy"] == 0
    assert report["database"]["checkpoint"]["wal_bytes_after"] == 0
    assert not Path(str(database) + "-wal").exists()
    assert not Path(str(database) + "-shm").exists()
    assert validate_evidence_report(report) == report
    assert canonical_json_bytes(report) + b"\n" == (
        scenario_evidence[0].read_bytes()
    )
