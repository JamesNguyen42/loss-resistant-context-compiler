from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ctxc_openhands.session import AtomicEventError, OpenHandsSession
from ctxc_openhands.storage import SQLiteGenerationStore, StoreIntegrityError


def _timestamp(index: int) -> str:
    return (
        datetime(2026, 7, 27, 12, tzinfo=UTC) + timedelta(microseconds=index)
    ).isoformat()


def _message(index: int) -> dict[str, Any]:
    return {
        "kind": "MessageEvent",
        "id": f"event-{index}",
        "timestamp": _timestamp(index),
        "source": "agent",
        "llm_message": {
            "role": "assistant",
            "content": [{"type": "text", "text": f"message {index}"}],
        },
    }


def _action(index: int) -> dict[str, Any]:
    return {
        "kind": "ActionEvent",
        "id": f"action-{index}",
        "timestamp": _timestamp(index),
        "source": "agent",
        "thought": [],
        "tool_name": "terminal",
        "tool_call_id": f"call-{index}",
        "tool_call": {
            "id": f"call-{index}",
            "name": "terminal",
            "arguments": "{}",
            "origin": "completion",
        },
        "llm_response_id": f"response-{index}",
    }


def _observation(index: int) -> dict[str, Any]:
    return {
        "kind": "ObservationEvent",
        "id": f"result-{index}",
        "timestamp": _timestamp(index + 1),
        "source": "environment",
        "tool_name": "terminal",
        "tool_call_id": f"call-{index}",
        "observation": {
            "kind": "TerminalObservation",
            "output": "ok",
        },
        "action_id": f"action-{index}",
    }


def _session(path: Path) -> OpenHandsSession:
    return OpenHandsSession(
        session_id="scaling-session",
        store=SQLiteGenerationStore(path),
    )


def test_ingest_never_builds_or_decodes_a_full_snapshot_per_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path / "store.sqlite3")
    calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> object:
        calls.append("full-snapshot")
        raise AssertionError("ingest attempted to build or decode a full snapshot")

    with monkeypatch.context() as guarded:
        guarded.setattr(session.store, "snapshot", forbidden)
        guarded.setattr(session.store, "_session_snapshot", forbidden)
        guarded.setattr(session.store, "_records_from_rows", forbidden)
        for index in range(128):
            result = session.ingest(
                _message(index),
                request_id=f"append-message-{index}",
            )
            assert result.source_count == index + 1
        retry = session.ingest(
            _message(64),
            request_id="retry-message-64",
        )
        assert retry.accepted is False
        session.ingest(_action(128), request_id="append-action")
        session.ingest(_observation(128), request_id="append-result")

    assert calls == []
    snapshot = session.store.snapshot(session.session_id)
    assert snapshot.source_count == 130
    assert tuple(record.sequence for record in snapshot.records) == tuple(range(130))


def test_atomic_lookup_index_is_immutable_and_missing_rows_fail_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "store.sqlite3"
    session = _session(path)
    session.ingest(_action(1), request_id="append-action")

    direct = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            direct.execute("DELETE FROM source_atomic_groups")
        direct.rollback()
        direct.execute("DROP TRIGGER source_atomic_groups_no_delete")
        direct.execute("DELETE FROM source_atomic_groups")
        direct.commit()
    finally:
        direct.close()

    with pytest.raises(AtomicEventError, match="exactly one retained ActionEvent"):
        session.ingest(_observation(1), request_id="append-result")
    with pytest.raises(StoreIntegrityError, match="atomic group index differs"):
        session.store.snapshot(session.session_id)


def test_resolver_rejects_session_head_not_bound_to_tail(
    tmp_path: Path,
) -> None:
    path = tmp_path / "store.sqlite3"
    session = _session(path)
    session.ingest(_message(0), request_id="append-first")

    direct = sqlite3.connect(path)
    try:
        direct.execute(
            "UPDATE sessions SET source_head_sha256 = ?",
            ("f" * 64,),
        )
        direct.commit()
    finally:
        direct.close()

    with pytest.raises(StoreIntegrityError, match="immutable tail"):
        session.ingest(_message(1), request_id="append-second")

    direct = sqlite3.connect(path)
    try:
        assert direct.execute("SELECT COUNT(*) FROM source_events").fetchone() == (1,)
    finally:
        direct.close()
