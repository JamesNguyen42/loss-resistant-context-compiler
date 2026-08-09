from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any

import pytest
from context_compiler import source_event_to_record
from store_helpers import source_record, store_path

from ctxc_openhands.atomic import derive_atomic_groups
from ctxc_openhands.event_map import classify_host_event, map_host_event
from ctxc_openhands.storage import (
    ImmutableEventError,
    IncompleteAtomicGroupError,
    SQLiteGenerationStore,
    StoreIntegrityError,
)

SESSION_ID = "atomic-session"
TIMESTAMP = "2026-07-27T12:00:00+00:00"


def _action(
    *,
    event_id: str = "action-1",
    tool_call_id: str = "call-1",
    tool_name: str = "terminal",
) -> dict[str, Any]:
    return {
        "kind": "ActionEvent",
        "id": event_id,
        "timestamp": TIMESTAMP,
        "source": "agent",
        "thought": [{"type": "text", "text": "Run the tool."}],
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "tool_call": {
            "id": tool_call_id,
            "name": tool_name,
            "arguments": "{}",
            "origin": "completion",
        },
        "llm_response_id": "response-1",
    }


def _observation(
    *,
    event_id: str = "observation-1",
    tool_call_id: str = "call-1",
    tool_name: str = "terminal",
    action_id: str = "action-1",
) -> dict[str, Any]:
    return {
        "kind": "ObservationEvent",
        "id": event_id,
        "timestamp": TIMESTAMP,
        "source": "environment",
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "action_id": action_id,
        "observation": {
            "kind": "TerminalObservation",
            "content": [{"type": "text", "text": "ok"}],
            "is_error": False,
        },
    }


def _rejection() -> dict[str, Any]:
    return {
        "kind": "UserRejectObservation",
        "id": "rejection-1",
        "timestamp": TIMESTAMP,
        "source": "environment",
        "tool_name": "terminal",
        "tool_call_id": "call-1",
        "action_id": "action-1",
        "rejection_reason": "Denied.",
        "rejection_source": "user",
    }


def _agent_error() -> dict[str, Any]:
    return {
        "kind": "AgentErrorEvent",
        "id": "error-1",
        "timestamp": TIMESTAMP,
        "source": "agent",
        "tool_name": "terminal",
        "tool_call_id": "call-1",
        "error": "Execution failed.",
    }


def _assistant_tool_call(
    *,
    event_id: str = "assistant-message-1",
    tool_call_id: str = "call-1",
    tool_name: str = "terminal",
) -> dict[str, Any]:
    return {
        "kind": "MessageEvent",
        "id": event_id,
        "timestamp": TIMESTAMP,
        "source": "agent",
        "llm_message": {
            "role": "assistant",
            "content": [],
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "name": tool_name,
                    "arguments": "{}",
                    "origin": "completion",
                }
            ],
        },
    }


def _tool_message(
    *,
    event_id: str = "tool-message-1",
    tool_call_id: str = "call-1",
    tool_name: str = "terminal",
) -> dict[str, Any]:
    return {
        "kind": "MessageEvent",
        "id": event_id,
        "timestamp": TIMESTAMP,
        "source": "environment",
        "llm_message": {
            "role": "tool",
            "content": [{"type": "text", "text": "ok"}],
            "tool_call_id": tool_call_id,
            "name": tool_name,
        },
    }


def _acp_event() -> dict[str, Any]:
    return {
        "kind": "ACPToolCallEvent",
        "id": "acp-event-1",
        "timestamp": TIMESTAMP,
        "source": "agent",
        "tool_call_id": "call-1",
        "title": "Read source",
        "status": "completed",
        "tool_kind": "read",
        "raw_input": {"path": "README.md"},
        "raw_output": {"bytes": 10},
        "content": [{"type": "text", "text": "contents"}],
        "is_error": False,
    }


def _append(
    store: SQLiteGenerationStore,
    event: dict[str, Any],
    sequence: int,
    *,
    groups: list[dict[str, Any]] | None = None,
) -> None:
    store.append_source_event(
        session_id=SESSION_ID,
        host_event=event,
        source_record=source_record(
            event,
            sequence,
            session_id=SESSION_ID,
            atomic_groups=groups,
        ),
        request_id=f"append-{sequence}-{event['id']}",
    )


def _prepare(store: SQLiteGenerationStore, generation: str = "generation-1") -> None:
    store.prepare_generation(
        session_id=SESSION_ID,
        generation_id=generation,
        operation_id=f"{generation}:prepare",
        policy_sha256=hashlib.sha256(b"policy").hexdigest(),
        tokenizer_identity="character-estimate-v1",
    )


@pytest.mark.parametrize(
    "result_factory",
    [_observation, _rejection, _agent_error],
)
def test_direct_store_accepts_each_valid_action_result_mapping(
    tmp_path,
    result_factory: Callable[[], dict[str, Any]],
) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    _append(store, _action(), 0)
    _append(store, result_factory(), 1)

    _prepare(store)
    assert store.snapshot(SESSION_ID).source_count == 2


def test_direct_store_accepts_valid_llm_tool_call_result_mapping(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    _append(store, _assistant_tool_call(), 0)
    _append(store, _tool_message(), 1)

    _prepare(store)
    assert store.snapshot(SESSION_ID).source_count == 2


def test_mapper_metadata_remains_compatible_but_is_not_the_authority(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    events = (_action(), _observation())
    for sequence, event in enumerate(events):
        mapped = map_host_event(
            event,
            sequence=sequence,
            session_id=SESSION_ID,
        )
        record = source_event_to_record(mapped, default_sequence=sequence)
        store.append_source_event(
            session_id=SESSION_ID,
            host_event=event,
            source_record=record,
            request_id=f"mapped-{sequence}",
        )

    _prepare(store)
    assert store.snapshot(SESSION_ID).source_count == 2


def test_missing_atomic_metadata_cannot_hide_canonical_call(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    _append(store, _action(), 0)

    with pytest.raises(IncompleteAtomicGroupError, match="incomplete"):
        _prepare(store)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("group_sha256", "f" * 64, "group_sha256"),
        ("role", "result", "role"),
        ("tool_call_id", "other-call", "tool_call_id"),
        ("action_id", "other-action", "action_id"),
        ("tool_name", "python", "tool_name"),
    ],
)
def test_declared_atomic_metadata_must_match_canonical_event(
    tmp_path,
    field: str,
    value: str,
    match: str,
) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    event = _action()
    group = derive_atomic_groups(event, session_id=SESSION_ID)[0]
    group[field] = value

    with pytest.raises(StoreIntegrityError, match=match):
        _append(store, event, 0, groups=[group])

    with pytest.raises(KeyError):
        store.snapshot(SESSION_ID)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("id", "other-call", "canonical id/name"),
        ("name", "python", "canonical id/name"),
    ],
)
def test_action_nested_tool_call_must_match_top_level_canonical_fields(
    tmp_path,
    field: str,
    value: str,
    match: str,
) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    event = _action()
    event["tool_call"][field] = value

    with pytest.raises(StoreIntegrityError, match=match):
        _append(store, event, 0)


@pytest.mark.parametrize(
    ("result", "match"),
    [
        (_observation(action_id="other-action"), "action_id mismatch"),
        (_observation(tool_name="python"), "tool_name mismatch"),
    ],
)
def test_canonical_pair_mismatches_fail_at_append_boundary(
    tmp_path,
    result: dict[str, Any],
    match: str,
) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    _append(store, _action(), 0)

    with pytest.raises(ImmutableEventError, match=match):
        _append(store, copy.deepcopy(result), 1)

    assert store.snapshot(SESSION_ID).source_count == 1


def test_result_before_call_is_not_an_atomic_pair(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))

    with pytest.raises(ImmutableEventError, match="exactly one retained matching call"):
        _append(store, _observation(), 0)

    _append(store, _action(), 0)
    assert store.snapshot(SESSION_ID).source_count == 1


@pytest.mark.parametrize(
    ("call", "result"),
    [
        (_action(), _tool_message()),
        (_assistant_tool_call(), _observation()),
    ],
)
def test_cross_family_call_result_substitution_fails_closed(
    tmp_path,
    call: dict[str, Any],
    result: dict[str, Any],
) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    _append(store, copy.deepcopy(call), 0)
    with pytest.raises(ImmutableEventError, match="incompatible event families"):
        _append(store, copy.deepcopy(result), 1)

    assert store.snapshot(SESSION_ID).source_count == 1


@pytest.mark.parametrize(
    ("first", "duplicate"),
    [
        (_action(), _action(event_id="action-2")),
        (
            _assistant_tool_call(),
            _assistant_tool_call(event_id="assistant-message-2"),
        ),
    ],
)
def test_duplicate_call_role_fails_closed_on_append(
    tmp_path,
    first: dict[str, Any],
    duplicate: dict[str, Any],
) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    _append(store, copy.deepcopy(first), 0)

    with pytest.raises(ImmutableEventError, match="retained atomic role"):
        _append(store, copy.deepcopy(duplicate), 1)

    assert store.snapshot(SESSION_ID).source_count == 1


@pytest.mark.parametrize(
    ("call", "result", "duplicate"),
    [
        (_action(), _observation(), _rejection()),
        (
            _assistant_tool_call(),
            _tool_message(),
            _tool_message(event_id="tool-message-2"),
        ),
    ],
)
def test_duplicate_result_role_fails_closed_on_append(
    tmp_path,
    call: dict[str, Any],
    result: dict[str, Any],
    duplicate: dict[str, Any],
) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    _append(store, copy.deepcopy(call), 0)
    _append(store, copy.deepcopy(result), 1)

    with pytest.raises(ImmutableEventError, match="retained atomic role"):
        _append(store, copy.deepcopy(duplicate), 2)

    assert store.snapshot(SESSION_ID).source_count == 2


def test_forged_group_on_non_atomic_event_is_rejected(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    event = {
        "kind": "MessageEvent",
        "id": "message-1",
        "timestamp": TIMESTAMP,
        "source": "agent",
        "llm_message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "No tool call."}],
        },
    }
    forged = derive_atomic_groups(_action(), session_id=SESSION_ID)[0]

    with pytest.raises(StoreIntegrityError, match="count differs"):
        _append(store, event, 0, groups=[forged])


def test_malformed_result_missing_action_id_is_rejected_on_append(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    event = _observation()
    del event["action_id"]

    with pytest.raises(StoreIntegrityError, match="action_id"):
        _append(store, event, 0)


def test_tool_call_id_mismatch_is_an_orphan_result(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    _append(store, _action(), 0)

    with pytest.raises(ImmutableEventError, match="exactly one retained matching call"):
        _append(store, _observation(tool_call_id="other-call"), 1)

    assert store.snapshot(SESSION_ID).source_count == 1


def test_acp_trajectory_cannot_be_falsely_completed_by_observation(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    _append(store, _acp_event(), 0)
    _prepare(store, "acp-standalone")

    with pytest.raises(ImmutableEventError, match="exactly one retained matching call"):
        _append(store, _observation(), 1)

    assert store.snapshot(SESSION_ID).source_count == 1


class _PauseAfterBegin:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()
        self.armed = False

    def arm(self) -> None:
        self.armed = True

    def __call__(self, point: str) -> None:
        if self.armed and point == "append.after_begin" and not self.entered.is_set():
            self.entered.set()
            if not self.release.wait(30):
                raise TimeoutError("timed out waiting to release append transaction")


class _SignalBeforeBegin:
    def __init__(self) -> None:
        self.entered = Event()
        self.armed = False

    def arm(self) -> None:
        self.armed = True

    def __call__(self, point: str) -> None:
        if self.armed and point == "write.before_begin":
            self.entered.set()


def test_waiting_writer_cannot_append_a_second_result_role(tmp_path) -> None:
    path = store_path(tmp_path)
    seed_store = SQLiteGenerationStore(path, require_new=True)
    _append(seed_store, _action(), 0)
    first_hook = _PauseAfterBegin()
    second_hook = _SignalBeforeBegin()
    first_store = SQLiteGenerationStore(
        path,
        require_existing=True,
        fault_hook=first_hook,
    )
    second_store = SQLiteGenerationStore(
        path,
        require_existing=True,
        fault_hook=second_hook,
    )
    first_hook.arm()
    second_hook.arm()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_append, first_store, _observation(), 1)
        assert first_hook.entered.wait(30)
        second = pool.submit(_append, second_store, _rejection(), 2)
        assert second_hook.entered.wait(30)
        first_hook.release.set()
        first.result(timeout=30)
        with pytest.raises(ImmutableEventError, match="retained atomic role"):
            second.result(timeout=30)

    verified = SQLiteGenerationStore(path, require_existing=True)
    snapshot = verified.snapshot(SESSION_ID)
    assert snapshot.source_count == 2
    assert tuple(record.id for record in snapshot.records) == (
        "action-1",
        "observation-1",
    )
    assert verified.integrity_report()["passed"] is True


def test_result_writer_cannot_overtake_its_call(tmp_path) -> None:
    path = store_path(tmp_path)
    SQLiteGenerationStore(path, require_new=True)
    result_hook = _PauseAfterBegin()
    call_hook = _SignalBeforeBegin()
    result_store = SQLiteGenerationStore(
        path,
        require_existing=True,
        fault_hook=result_hook,
    )
    call_store = SQLiteGenerationStore(
        path,
        require_existing=True,
        fault_hook=call_hook,
    )
    result_hook.arm()
    call_hook.arm()

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = pool.submit(_append, result_store, _observation(), 0)
        assert result_hook.entered.wait(30)
        call = pool.submit(_append, call_store, _action(), 0)
        assert call_hook.entered.wait(30)
        result_hook.release.set()
        with pytest.raises(
            ImmutableEventError,
            match="exactly one retained matching call",
        ):
            result.result(timeout=30)
        call.result(timeout=30)

    verified = SQLiteGenerationStore(path, require_existing=True)
    assert verified.snapshot(SESSION_ID).source_count == 1
    _append(verified, _observation(), 1)
    assert verified.snapshot(SESSION_ID).source_count == 2
    assert verified.integrity_report()["passed"] is True


def test_mapped_acp_trajectory_is_retained_but_has_no_false_terminal_pair(
    tmp_path,
) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    event = _acp_event()
    mapped = map_host_event(event, sequence=0, session_id=SESSION_ID)
    record = source_event_to_record(mapped, default_sequence=0)
    assert classify_host_event(event).atomic_role == "none"
    assert "atomic_group" not in mapped.metadata["openhands"]
    store.append_source_event(
        session_id=SESSION_ID,
        host_event=event,
        source_record=record,
        request_id="mapped-acp",
    )

    assert store.snapshot(SESSION_ID).source_count == 1
    _prepare(store, "mapped-acp-standalone")


def test_stored_group_tampering_is_detected_from_canonical_host_event(tmp_path) -> None:
    path = store_path(tmp_path)
    store = SQLiteGenerationStore(path)
    _append(store, _action(), 0)

    direct = sqlite3.connect(path)
    try:
        direct.execute("DROP TRIGGER source_events_no_update")
        direct.execute(
            "UPDATE source_events SET atomic_groups_json = ?",
            (json.dumps([], separators=(",", ":")),),
        )
        direct.commit()
    finally:
        direct.close()

    with pytest.raises(StoreIntegrityError, match="canonical host event semantics"):
        store.snapshot(SESSION_ID)
