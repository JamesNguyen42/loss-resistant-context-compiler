from __future__ import annotations

from pathlib import Path

import pytest
from context_compiler import LocalAIConnector

from ctxc_openhands.callback import (
    DurableEventCallback,
    IntegrationPoisonedError,
)
from ctxc_openhands.session import OpenHandsSession
from ctxc_openhands.storage import SQLiteGenerationStore


def _message(index: int) -> dict:
    return {
        "kind": "MessageEvent",
        "id": f"message-{index}",
        "timestamp": f"2026-07-27T12:00:{index:02d}+00:00",
        "source": "agent",
        "llm_message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "ordinary history"}],
        },
    }


def _action() -> dict:
    return {
        "kind": "ActionEvent",
        "id": "action-1",
        "timestamp": "2026-07-27T12:00:01+00:00",
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


def _result() -> dict:
    return {
        "kind": "ObservationEvent",
        "id": "result-1",
        "timestamp": "2026-07-27T12:00:02+00:00",
        "source": "environment",
        "tool_name": "bash",
        "tool_call_id": "call-1",
        "observation": {"kind": "TerminalObservation", "output": "/workspace"},
        "action_id": "action-1",
    }


def _callback(tmp_path: Path) -> DurableEventCallback:
    session = OpenHandsSession(
        session_id="callback-session",
        store=SQLiteGenerationStore(tmp_path / "store.sqlite3"),
        connector_factory=LocalAIConnector,
    )
    return DurableEventCallback(session)


def test_valid_callback_ingests_without_poison(tmp_path: Path) -> None:
    callback = _callback(tmp_path)

    assert callback(_message(0)) is None

    assert callback.status() == {
        "poisoned": False,
        "callback_count": 1,
        "failure": None,
    }
    assert callback.session.store.snapshot(callback.session.session_id).source_count == 1
    callback.assert_dispatch_allowed()


def test_exact_host_class_validator_failure_is_nonthrowing_and_poisons(
    tmp_path: Path,
) -> None:
    session = OpenHandsSession(
        session_id="callback-session",
        store=SQLiteGenerationStore(tmp_path / "store.sqlite3"),
        connector_factory=LocalAIConnector,
    )

    def reject_impostor(_event: object) -> None:
        raise TypeError("event object is not an exact pinned SDK class")

    callback = DurableEventCallback(
        session,
        event_validator=reject_impostor,
    )

    assert callback(_message(0)) is None
    assert callback.poisoned is True
    assert callback.failure is not None
    assert callback.failure.error_type == "TypeError"
    with pytest.raises(KeyError):
        session.store.snapshot(session.session_id)


def test_unknown_event_never_raises_into_host_but_poisons_dispatch(
    tmp_path: Path,
) -> None:
    callback = _callback(tmp_path)
    unknown = {
        "kind": "FutureEvent",
        "id": "future-1",
        "timestamp": "2026-07-27T12:00:00+00:00",
        "source": "agent",
    }

    assert callback(unknown) is None
    assert callback(_message(0)) is None

    status = callback.status()
    assert status["poisoned"] is True
    assert status["callback_count"] == 2
    assert status["failure"]["event_id"] == "future-1"
    assert status["failure"]["error_type"] == "EventMappingError"
    with pytest.raises(IntegrationPoisonedError, match="reconcile"):
        callback.assert_dispatch_allowed()
    with pytest.raises(KeyError):
        callback.session.store.snapshot(callback.session.session_id)


def test_exact_persisted_log_reconciliation_fills_gap_and_clears_poison(
    tmp_path: Path,
) -> None:
    callback = _callback(tmp_path)

    # An orphan result is rejected locally, but the callback returns so the
    # host can persist it. The complete host log later supplies its action.
    assert callback(_result()) is None
    assert callback.poisoned is True

    assert callback.reconcile([_action(), _result()]) == 2

    assert callback.poisoned is False
    callback.assert_dispatch_allowed()
    snapshot = callback.session.store.snapshot(callback.session.session_id)
    assert tuple(record.id for record in snapshot.records) == ("action-1", "result-1")


def test_unreconcilable_unknown_event_remains_poisoned(tmp_path: Path) -> None:
    callback = _callback(tmp_path)
    unknown = {
        "kind": "FutureEvent",
        "id": "future-1",
        "timestamp": "2026-07-27T12:00:00+00:00",
        "source": "agent",
    }
    callback(unknown)

    with pytest.raises(ValueError, match="unsupported OpenHands event kind"):
        callback.reconcile([unknown])

    assert callback.poisoned is True


def test_failure_diagnostic_is_bounded(tmp_path: Path) -> None:
    callback = _callback(tmp_path)
    malformed = _message(0)
    malformed["unexpected"] = "x" * 10_000

    callback(malformed)

    failure = callback.failure
    assert failure is not None
    assert len(failure.error) <= 2048


class _SerializerBomb(BaseException):
    pass


class _ModelDumpBomb:
    def model_dump(self, **_kwargs: object) -> dict:
        raise _SerializerBomb("model_dump failed")


class _ToDictBomb:
    def to_dict(self) -> dict:
        raise _SerializerBomb("to_dict failed")


@pytest.mark.parametrize(
    "event_type",
    (_ModelDumpBomb, _ToDictBomb),
)
def test_hostile_serializer_is_nonthrowing_and_poisons(
    tmp_path: Path,
    event_type: type[object],
) -> None:
    callback = _callback(tmp_path)

    assert callback(event_type()) is None
    assert callback(_message(1)) is None

    failure = callback.failure
    assert failure is not None
    assert failure.error_type == "_SerializerBomb"
    assert callback.poisoned is True
    assert callback.status()["callback_count"] == 2
    with pytest.raises(KeyError):
        callback.session.store.snapshot(callback.session.session_id)


class _UnprintableError(BaseException):
    def __str__(self) -> str:
        raise RuntimeError("exception string failed")


def test_unprintable_exception_is_nonthrowing_and_keeps_dispatch_poisoned(
    tmp_path: Path,
) -> None:
    session = OpenHandsSession(
        session_id="callback-session",
        store=SQLiteGenerationStore(tmp_path / "store.sqlite3"),
        connector_factory=LocalAIConnector,
    )

    def reject(_event: object) -> None:
        raise _UnprintableError()

    callback = DurableEventCallback(session, event_validator=reject)

    assert callback(_message(0)) is None
    assert callback(_message(1)) is None

    failure = callback.failure
    assert failure is not None
    assert failure.error_type == "_UnprintableError"
    assert failure.error == "<exception string unavailable>"
