from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from context_compiler import LocalAIConnector

import ctxc_openhands.evidence as evidence_module
from ctxc_openhands.evidence import (
    EVIDENCE_VERIFICATION_SCHEMA,
    MAX_EVIDENCE_JSON_BYTES,
    EvidenceValidationError,
    canonical_json_bytes,
    checkpoint_and_bind_database,
    finalize_evidence_report,
    load_evidence_report,
    validate_evidence_report,
    verify_evidence,
    write_evidence_report,
)
from ctxc_openhands.fake_runtime import OfflineFakeRuntime
from ctxc_openhands.scenario import run_offline_crash_scenario
from ctxc_openhands.session import OpenHandsSession
from ctxc_openhands.soak import run_deterministic_soak
from ctxc_openhands.storage import SQLiteGenerationStore


@pytest.fixture()
def scenario_evidence(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    database = tmp_path / "scenario.sqlite3"
    report_path = tmp_path / "scenario.json"
    report = run_offline_crash_scenario(database)
    write_evidence_report(report_path, report)
    return report_path, database, report


@pytest.fixture()
def soak_evidence(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    database = tmp_path / "soak.sqlite3"
    report_path = tmp_path / "soak.json"
    report = run_deterministic_soak(
        database,
        event_count=6,
        compaction_count=3,
        restart_every_compactions=2,
    )
    write_evidence_report(report_path, report)
    return report_path, database, report


def _resigned(report: dict[str, Any], **changes: Any) -> dict[str, Any]:
    unsigned = {
        key: value for key, value in report.items() if key != "report_sha256"
    }
    unsigned.update(changes)
    return finalize_evidence_report(unsigned)


def _detached(report: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(canonical_json_bytes(report))
    assert isinstance(value, dict)
    return value


def _scenario_message(index: int, constraint: str) -> dict[str, Any]:
    return {
        "kind": "MessageEvent",
        "id": f"scenario-message-{index}",
        "timestamp": f"2026-07-27T12:00:{index:02d}+00:00",
        "source": "agent",
        "llm_message": {
            "role": "assistant",
            "content": [{"type": "text", "text": constraint}],
        },
    }


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


@pytest.mark.parametrize(
    ("error", "message"),
    (
        (
            sqlite3.OperationalError("injected ledger read failure"),
            "injected ledger read failure",
        ),
        (OSError("injected ledger I/O failure"), "injected ledger I/O failure"),
    ),
)
def test_scenario_ledger_storage_error_is_structured_and_fails_closed(
    scenario_evidence: tuple[Path, Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    message: str,
) -> None:
    report_path, database, _report = scenario_evidence

    class FailingLedgerStore(SQLiteGenerationStore):
        def load_request_ledger(self, **_kwargs: Any) -> Any:
            raise error

    monkeypatch.setattr(evidence_module, "SQLiteGenerationStore", FailingLedgerStore)
    result = verify_evidence(report_path, database=database)

    assert result["passed"] is False
    assert result["scope"] == "json-and-database"
    assert result["report_verified"] is True
    assert result["database_supplied"] is True
    assert result["database_verified"] is False
    assert result["issues"] == [f"database final-request replay failed: {message}"]


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


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("ordinal", 99),
        ("generation_id", "forged-generation"),
        ("active_epoch", 99),
        ("source_count", 99),
        ("source_head_sha256", "0" * 64),
        ("bundle_sha256", "1" * 64),
        ("semantic_result_digest", "2" * 64),
    ],
)
def test_resigned_scenario_compaction_claims_are_bound_to_database_rows(
    scenario_evidence: tuple[Path, Path, dict[str, Any]],
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    _report_path, database, report = scenario_evidence
    forged = _detached(report)
    forged["compactions"][0][field] = replacement
    resigned = _resigned(forged)
    path = tmp_path / f"forged-scenario-{field}.json"
    write_evidence_report(path, resigned)

    result = verify_evidence(path, database=database)

    assert result["passed"] is False
    assert result["database_verified"] is False
    assert "database scenario compactions do not match report" in result["issues"]


@pytest.mark.parametrize(
    ("field", "replacement", "expected_issue"),
    [
        (
            "old_generation_visible_after_crash",
            "forged-old-generation",
            "database scenario old-generation claim does not match",
        ),
        (
            "recovered_generation",
            "forged-recovered-generation",
            "database scenario recovered-generation claim does not match",
        ),
    ],
)
def test_resigned_scenario_generation_claims_are_bound_to_database(
    scenario_evidence: tuple[Path, Path, dict[str, Any]],
    tmp_path: Path,
    field: str,
    replacement: str,
    expected_issue: str,
) -> None:
    _report_path, database, report = scenario_evidence
    resigned = _resigned(report, **{field: replacement})
    path = tmp_path / f"forged-scenario-{field}.json"
    write_evidence_report(path, resigned)

    result = verify_evidence(path, database=database)

    assert result["passed"] is False
    assert result["database_verified"] is False
    assert expected_issue in result["issues"]


def test_extra_rollback_activations_cannot_be_resealed_as_three_compactions(
    scenario_evidence: tuple[Path, Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    _report_path, database, report = scenario_evidence
    store = SQLiteGenerationStore(database, require_existing=True)
    generation_ids = [
        str(item["generation_id"]) for item in report["session"]["generation_states"]
    ]
    store.rollback_generation(
        session_id=report["session"]["session_id"],
        target_generation_id=generation_ids[0],
        operation_id="test:rollback-to-first",
    )
    final_epoch = store.rollback_generation(
        session_id=report["session"]["session_id"],
        target_generation_id=generation_ids[2],
        operation_id="test:rollback-to-final",
    )
    assert final_epoch == 5
    integrity = store.integrity_report()
    assert integrity["passed"] is True

    forged = _detached(report)
    forged["session"]["active_epoch"] = final_epoch
    forged["integrity_report_sha256"] = integrity["report_sha256"]
    forged["database"] = checkpoint_and_bind_database(database)
    resigned = _resigned(forged)
    path = tmp_path / "forged-extra-activations.json"
    write_evidence_report(path, resigned)

    result = verify_evidence(path, database=database)

    assert result["passed"] is False
    assert result["database_verified"] is False
    assert (
        "database active epoch does not match ordered generation count"
        in result["issues"]
    )
    assert (
        "database activation transition count does not match report"
        in result["issues"]
    )


def test_uninterrupted_compactions_cannot_be_relabelled_as_crash_recovery(
    scenario_evidence: tuple[Path, Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    _report_path, _database, report = scenario_evidence
    database = tmp_path / "uninterrupted.sqlite3"
    store = SQLiteGenerationStore(database, require_new=True)
    session = OpenHandsSession(
        session_id=report["session"]["session_id"],
        store=store,
        connector_factory=LocalAIConnector,
    )
    generation_ids: list[str] = []
    for index, constraint in enumerate(report["constraints"]):
        session.ingest(
            _scenario_message(index, constraint),
            request_id=f"scenario-append-{index}",
        )
        generation_ids.append(session.compact().generation_id)
    assert generation_ids == [
        item["generation_id"] for item in report["compactions"]
    ]

    fake = OfflineFakeRuntime(session)
    final_request = fake.prepare(
        request_id="scenario-final-request",
        prompts=("Offline diagnostic only; preserve all authority boundaries.",),
        current_turn=(
            "Report the retained PostgreSQL and authentication constraints."
        ),
        tool_schemas=(
            {
                "name": "read_only_constraint_report",
                "input": {"type": "object", "additionalProperties": False},
            },
        ),
    )
    replay = final_request.replay()
    receipt = fake.dispatch(final_request)
    assert replay.to_dict() == report["final_request"]["replay"]
    assert receipt.to_dict() == report["final_request"]["fake_dispatch_receipt"]
    integrity = store.integrity_report()
    assert integrity["passed"] is True

    forged = _detached(report)
    forged["command"]["parameters"]["database"] = str(database.absolute())
    forged["integrity_report_sha256"] = integrity["report_sha256"]
    forged["database"] = checkpoint_and_bind_database(database)
    resigned = _resigned(forged)
    path = tmp_path / "forged-uninterrupted-as-crash.json"
    write_evidence_report(path, resigned)

    result = verify_evidence(path, database=database)

    assert result["passed"] is False
    assert result["database_verified"] is False
    assert result["issues"] == [
        "database activation transitions do not match report contract"
    ]


def test_extra_database_session_cannot_be_silently_omitted_from_report(
    scenario_evidence: tuple[Path, Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    _report_path, database, report = scenario_evidence
    store = SQLiteGenerationStore(database, require_existing=True)
    extra = OpenHandsSession(
        session_id="unreported-extra-session",
        store=store,
        connector_factory=LocalAIConnector,
    )
    extra.ingest(
        _scenario_message(9, "Unreported side history."),
        request_id="extra-session-append",
    )
    integrity = store.integrity_report()
    assert integrity["passed"] is True

    forged = _detached(report)
    forged["integrity_report_sha256"] = integrity["report_sha256"]
    forged["database"] = checkpoint_and_bind_database(database)
    resigned = _resigned(forged)
    path = tmp_path / "forged-missing-session.json"
    write_evidence_report(path, resigned)

    result = verify_evidence(path, database=database)

    assert result["passed"] is False
    assert result["database_verified"] is False
    assert result["issues"] == [
        "database session inventory does not match report"
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "event-count",
        "compaction-count",
        "generation-id",
        "bundle-digest",
        "semantic-digest",
    ],
)
def test_resigned_soak_claims_are_bound_to_database_rows(
    soak_evidence: tuple[Path, Path, dict[str, Any]],
    tmp_path: Path,
    mutation: str,
) -> None:
    _report_path, database, report = soak_evidence
    forged = _detached(report)
    expected_issue: str
    if mutation == "event-count":
        forged["event_count"] = 7
        forged["command"]["parameters"]["events"] = 7
        expected_issue = "database soak event count does not match report"
    elif mutation == "compaction-count":
        forged["compaction_count"] = 2
        forged["command"]["parameters"]["compactions"] = 2
        forged["generation_ids"] = forged["generation_ids"][:2]
        forged["bundle_sha256s"] = forged["bundle_sha256s"][:2]
        forged["semantic_result_digests"] = forged["semantic_result_digests"][:2]
        expected_issue = "database generation count does not match report"
    elif mutation == "generation-id":
        forged["generation_ids"][0] = "forged-generation"
        expected_issue = "database soak generation ids do not match report"
    elif mutation == "bundle-digest":
        forged["bundle_sha256s"][0] = "3" * 64
        expected_issue = "database soak bundle digests do not match report"
    else:
        forged["semantic_result_digests"][0] = "4" * 64
        expected_issue = "database soak semantic digests do not match report"
    resigned = _resigned(forged)
    path = tmp_path / f"forged-soak-{mutation}.json"
    write_evidence_report(path, resigned)

    result = verify_evidence(path, database=database)

    assert result["passed"] is False
    assert result["database_verified"] is False
    assert expected_issue in result["issues"]
