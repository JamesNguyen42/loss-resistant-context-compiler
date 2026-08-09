from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from store_helpers import append_message, compile_generation, store_path

from ctxc_openhands.request_ledger import build_final_request_ledger
from ctxc_openhands.storage import SQLiteGenerationStore
from ctxc_openhands.tokenizer import CanonicalUtf8ByteTokenizer


def _mutate_immutable_row(
    path: Path,
    *,
    trigger: str,
    statement: str,
    parameters: Sequence[object],
) -> None:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            (trigger,),
        ).fetchone()
        assert row is not None and isinstance(row[0], str)
        trigger_sql = row[0]
        connection.execute(f'DROP TRIGGER "{trigger}"')
        connection.execute(statement, tuple(parameters))
        connection.execute(trigger_sql)
        connection.commit()
    finally:
        connection.close()


def _store_exact_ledger(
    store: SQLiteGenerationStore,
    *,
    generation_id: str,
) -> None:
    active = store.read_active("session-1")
    assert active is not None
    tokenizer = CanonicalUtf8ByteTokenizer()
    ledger = build_final_request_ledger(
        transport_parts=[
            {"category": "prompts", "text": "P"},
            {"category": "verified_memory", "text": "M"},
            {"category": "recent_tail", "text": ""},
            {"category": "current_turn", "text": "U"},
            {"category": "retrieval", "text": ""},
            {"category": "attachments", "text": ""},
            {"category": "tool_schemas", "text": "T"},
            {"category": "provider_framing", "text": "{}"},
        ],
        model="offline-fake",
        path="chat",
        session_id=active.session_id,
        generation_id=generation_id,
        active_epoch=active.active_epoch,
        source_head_sha256=active.source_head_sha256,
        semantic_result_digest=active.semantic_result_digest,
        reserved_output_tokens=8,
        safety_margin_tokens=4,
        hard_limit_tokens=100,
        tokenizer=tokenizer,
    )
    store.store_request_ledger(
        session_id="session-1",
        generation_id=generation_id,
        request_id="historical-request",
        ledger=ledger,
        tokenizer=tokenizer,
    )


def test_integrity_report_accepts_retained_prepared_crash_remnant(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    store.prepare_generation(
        session_id="session-1",
        generation_id="prepared-remnant",
        operation_id="prepared-remnant:prepare",
        policy_sha256="a" * 64,
        tokenizer_identity="character-estimate-v1",
    )

    report = store.integrity_report()

    assert report["passed"] is True
    assert report["generation_count"] == 1
    assert report["transition_count"] == 1
    assert report["request_ledger_count"] == 0
    assert report["operation_count"] == 1


def test_integrity_report_rejects_tampered_superseded_generation(tmp_path) -> None:
    path = store_path(tmp_path)
    store = SQLiteGenerationStore(path)
    append_message(store, 0)
    first, _bundle, _replay = compile_generation(store, 1)
    compile_generation(store, 2)
    _mutate_immutable_row(
        path,
        trigger="generations_verified_payload_once",
        statement=(
            "UPDATE generations SET bundle_sha256 = ? "
            "WHERE generation_id = ?"
        ),
        parameters=("f" * 64, first),
    )

    report = store.integrity_report()

    assert report["passed"] is False
    assert any(
        (
            "stored generation bundle digest mismatch" in issue
            or "transition evidence does not bind" in issue
        )
        for issue in report["issues"]
    )


def test_integrity_report_rejects_tampered_historical_transition(tmp_path) -> None:
    path = store_path(tmp_path)
    store = SQLiteGenerationStore(path)
    append_message(store, 0)
    generation_id, _bundle, _replay = compile_generation(store, 1)
    _mutate_immutable_row(
        path,
        trigger="transitions_no_update",
        statement=(
            "UPDATE generation_transitions SET evidence_sha256 = ? "
            "WHERE generation_id = ? AND ordinal = 0"
        ),
        parameters=("f" * 64, generation_id),
    )

    report = store.integrity_report()

    assert report["passed"] is False
    assert any(
        "transition evidence does not bind" in issue
        for issue in report["issues"]
    )


def test_integrity_report_replays_tampered_historical_request_ledger(
    tmp_path,
) -> None:
    path = store_path(tmp_path)
    store = SQLiteGenerationStore(path)
    append_message(store, 0)
    first, _bundle, _replay = compile_generation(store, 1)
    _store_exact_ledger(store, generation_id=first)
    compile_generation(store, 2)
    _mutate_immutable_row(
        path,
        trigger="request_ledgers_no_update",
        statement=(
            "UPDATE request_ledgers SET ledger_sha256 = ? "
            "WHERE session_id = ? AND request_id = ?"
        ),
        parameters=("f" * 64, "session-1", "historical-request"),
    )

    report = store.integrity_report()

    assert report["passed"] is False
    assert any(
        "request ledger binding or digest mismatch" in issue
        for issue in report["issues"]
    )


def test_integrity_report_rejects_payload_on_unverified_prepared_row(
    tmp_path,
) -> None:
    path = store_path(tmp_path)
    store = SQLiteGenerationStore(path)
    append_message(store, 0)
    store.prepare_generation(
        session_id="session-1",
        generation_id="prepared-tamper",
        operation_id="prepared-tamper:prepare",
        policy_sha256="a" * 64,
        tokenizer_identity="character-estimate-v1",
    )
    _mutate_immutable_row(
        path,
        trigger="generations_verified_payload_once",
        statement=(
            "UPDATE generations SET verification_passed = 1 "
            "WHERE generation_id = ?"
        ),
        parameters=("prepared-tamper",),
    )

    report = store.integrity_report()

    assert report["passed"] is False
    assert any(
        "unverified generation carries verification payload" in issue
        for issue in report["issues"]
    )


def test_integrity_report_rejects_tampered_append_operation(tmp_path) -> None:
    path = store_path(tmp_path)
    store = SQLiteGenerationStore(path)
    append_message(store, 0)
    _mutate_immutable_row(
        path,
        trigger="operations_no_update",
        statement=(
            "UPDATE operations SET payload_sha256 = ? "
            "WHERE session_id = ? AND request_id = ?"
        ),
        parameters=("f" * 64, "session-1", "append-0"),
    )

    report = store.integrity_report()

    assert report["passed"] is False
    assert any(
        "operation payload digest does not bind" in issue
        for issue in report["issues"]
    )
