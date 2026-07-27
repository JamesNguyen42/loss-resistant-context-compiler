from __future__ import annotations

from pathlib import Path

import pytest
from context_compiler import LocalAIConnector

from ctxc_openhands.event_map import EventMappingError
from ctxc_openhands.session import (
    AtomicEventError,
    OpenHandsSession,
    TransientEventError,
)
from ctxc_openhands.storage import (
    IncompleteAtomicGroupError,
    SQLiteGenerationStore,
)


def _timestamp(index: int) -> str:
    return f"2026-07-27T12:00:{index:02d}+00:00"


def _message(index: int, text: str = "Keep authentication enabled.") -> dict:
    return {
        "kind": "MessageEvent",
        "id": f"message-{index}",
        "timestamp": _timestamp(index),
        "source": "agent",
        "llm_message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }


def _action() -> dict:
    return {
        "kind": "ActionEvent",
        "id": "action-1",
        "timestamp": _timestamp(1),
        "source": "agent",
        "thought": [],
        "tool_name": "bash",
        "tool_call_id": "call-1",
        "tool_call": {
            "id": "call-1",
            "name": "bash",
            "arguments": '{"command":"pwd"}',
            "origin": "completion",
        },
        "llm_response_id": "response-1",
    }


def _observation(*, action_id: str = "action-1", tool_name: str = "bash") -> dict:
    return {
        "kind": "ObservationEvent",
        "id": "observation-1",
        "timestamp": _timestamp(2),
        "source": "environment",
        "tool_name": tool_name,
        "tool_call_id": "call-1",
        "observation": {"kind": "TerminalObservation", "output": "/workspace"},
        "action_id": action_id,
    }


def _session(path: Path, *, connector_factory=LocalAIConnector) -> OpenHandsSession:
    return OpenHandsSession(
        session_id="host-session",
        store=SQLiteGenerationStore(path),
        connector_factory=connector_factory,
    )


def test_message_ingest_and_compaction_activate_verified_generation(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path / "store.sqlite3")

    append = session.ingest(_message(0), request_id="append-message-0")
    compacted = session.compact()
    active = session.active()

    assert append.accepted is True
    assert compacted.source_count == 1
    assert compacted.active_epoch == 1
    assert active is not None
    assert active.generation_id == compacted.generation_id
    assert active.bundle.bindings["source_count"] == 1
    assert active.semantic_result_digest == compacted.semantic_result_digest
    assert active.tail == ()


def test_exact_retry_is_idempotent(tmp_path: Path) -> None:
    session = _session(tmp_path / "store.sqlite3")
    event = _message(0)

    first = session.ingest(event, request_id="append-first")
    second = session.ingest(event, request_id="append-retry")

    assert first.accepted is True
    assert second.accepted is False
    assert second.source_count == 1
    assert session.store.snapshot(session.session_id).source_count == 1


def test_incomplete_tool_call_cannot_be_compacted(tmp_path: Path) -> None:
    session = _session(tmp_path / "store.sqlite3")
    session.ingest(_action(), request_id="append-action")

    with pytest.raises(IncompleteAtomicGroupError):
        session.compact()

    assert session.store.list_generations(session.session_id) == ()


def test_matching_tool_result_completes_atomic_group(tmp_path: Path) -> None:
    session = _session(tmp_path / "store.sqlite3")
    session.ingest(_action(), request_id="append-action")
    session.ingest(_observation(), request_id="append-observation")

    compacted = session.compact()

    assert compacted.source_count == 2
    assert session.active() is not None


@pytest.mark.parametrize(
    ("action_id", "tool_name", "match"),
    [
        ("wrong-action", "bash", "action_id mismatch"),
        ("action-1", "python", "tool_name mismatch"),
    ],
)
def test_mismatched_tool_result_is_not_appended(
    tmp_path: Path,
    action_id: str,
    tool_name: str,
    match: str,
) -> None:
    session = _session(tmp_path / "store.sqlite3")
    session.ingest(_action(), request_id="append-action")

    with pytest.raises(AtomicEventError, match=match):
        session.ingest(
            _observation(action_id=action_id, tool_name=tool_name),
            request_id="append-result",
        )

    assert session.store.snapshot(session.session_id).source_count == 1


def test_result_without_retained_action_is_rejected(tmp_path: Path) -> None:
    session = _session(tmp_path / "store.sqlite3")

    with pytest.raises(AtomicEventError, match="exactly one retained ActionEvent"):
        session.ingest(_observation(), request_id="orphan-result")

    with pytest.raises(KeyError):
        session.store.snapshot(session.session_id)


def test_transient_event_is_validated_then_explicitly_refused(tmp_path: Path) -> None:
    session = _session(tmp_path / "store.sqlite3")
    event = {
        "kind": "StreamingDeltaEvent",
        "id": "delta-1",
        "timestamp": _timestamp(0),
        "source": "agent",
        "content": "partial",
    }

    with pytest.raises(TransientEventError, match="not durable source history"):
        session.ingest(event, request_id="delta")

    invalid = {**event, "unexpected": True}
    with pytest.raises(EventMappingError, match="invalid fields"):
        session.ingest(invalid, request_id="invalid-delta")


def test_compaction_uses_a_fresh_independent_verifier(tmp_path: Path) -> None:
    created: list[LocalAIConnector] = []

    def factory() -> LocalAIConnector:
        connector = LocalAIConnector()
        created.append(connector)
        return connector

    session = _session(tmp_path / "store.sqlite3", connector_factory=factory)
    session.ingest(_message(0), request_id="append")

    session.compact()

    assert len(created) == 2
    assert created[0] is not created[1]


def test_verifier_subclass_cannot_forge_passed_replay(
    tmp_path: Path,
) -> None:
    class ForgedPassingVerifier(LocalAIConnector):
        def verify_memory(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return {"passed": True, "issues": []}

    connectors = iter((LocalAIConnector(), ForgedPassingVerifier()))
    session = _session(
        tmp_path / "store.sqlite3",
        connector_factory=lambda: next(connectors),
    )
    session.ingest(_message(0), request_id="append")

    with pytest.raises(TypeError, match="distinct exact LocalAIConnector"):
        session.compact()

    assert session.active() is None
    generations = session.store.list_generations(session.session_id)
    assert len(generations) == 1
    assert generations[0]["state"] == "prepared"


def test_reopened_run_has_same_verified_semantic_result(tmp_path: Path) -> None:
    first = _session(tmp_path / "first.sqlite3")
    second = _session(tmp_path / "second.sqlite3")
    event = _message(0)
    first.ingest(event, request_id="append")
    second.ingest(event, request_id="append")

    first_result = first.compact()
    second_result = second.compact()

    assert first_result.bundle_sha256 == second_result.bundle_sha256
    assert (
        first_result.semantic_result_digest
        == second_result.semantic_result_digest
    )
