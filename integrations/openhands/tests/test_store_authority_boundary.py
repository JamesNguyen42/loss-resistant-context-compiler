from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from typing import Any

import pytest
from context_compiler import SourceRecord, source_event_to_record

from ctxc_openhands import storage
from ctxc_openhands.authority import (
    AuthorityPolicy,
    issue_authority_receipt,
)
from ctxc_openhands.event_map import map_host_event
from ctxc_openhands.storage import (
    ImmutableEventError,
    SQLiteGenerationStore,
    StoreIntegrityError,
)

SESSION_ID = "direct-authority-boundary"
ISSUER = "independent-auth-gateway"
SECRET = b"direct-store-authority-boundary-key!" * 2
TIMESTAMP = "2026-07-27T12:00:00+00:00"


def _message_event() -> dict[str, Any]:
    return {
        "kind": "MessageEvent",
        "id": "message-1",
        "timestamp": TIMESTAMP,
        "source": "agent",
        "llm_message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "ordinary history"}],
        },
    }


def _action_event() -> dict[str, Any]:
    return {
        "kind": "ActionEvent",
        "id": "action-1",
        "timestamp": TIMESTAMP,
        "source": "agent",
        "thought": [],
        "tool_name": "terminal",
        "tool_call_id": "call-1",
        "tool_call": {
            "id": "call-1",
            "name": "terminal",
            "arguments": '{"command":"printf ok"}',
            "origin": "completion",
        },
        "llm_response_id": "response-1",
        "action": {"kind": "TerminalAction", "command": "printf ok"},
    }


def _observation_event() -> dict[str, Any]:
    return {
        "kind": "ObservationEvent",
        "id": "observation-1",
        "timestamp": TIMESTAMP,
        "source": "environment",
        "tool_name": "terminal",
        "tool_call_id": "call-1",
        "action_id": "action-1",
        "observation": {
            "kind": "TerminalObservation",
            "content": [{"type": "text", "text": "ok"}],
            "is_error": False,
        },
    }


def _token_event() -> dict[str, Any]:
    return {
        "kind": "TokenEvent",
        "id": "token-1",
        "timestamp": TIMESTAMP,
        "source": "agent",
        "prompt_token_ids": [1, 2],
        "response_token_ids": [3],
    }


def _policy() -> AuthorityPolicy:
    return AuthorityPolicy(
        issuer_secrets={ISSUER: SECRET},
        allowed_roles={ISSUER: frozenset({"tool"})},
        allowed_event_kinds={ISSUER: frozenset({"ObservationEvent"})},
        trusted_state_tools={ISSUER: frozenset({"terminal"})},
    )


def _mapped_record(
    event: dict[str, Any],
    *,
    authority_policy: AuthorityPolicy | None = None,
    authority_receipt: dict[str, Any] | None = None,
    sequence: int = 0,
) -> SourceRecord:
    mapped = map_host_event(
        event,
        sequence=sequence,
        session_id=SESSION_ID,
        authority_policy=authority_policy,
        authority_receipt=authority_receipt,
    )
    return source_event_to_record(mapped, default_sequence=sequence)


def _unmapped_record(event: dict[str, Any]) -> SourceRecord:
    return SourceRecord.create(
        id=str(event["id"]),
        sequence=0,
        role="assistant",
        content=json.dumps(
            event,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        timestamp=str(event["timestamp"]),
        metadata={},
    )


def _replace_record(
    record: SourceRecord,
    *,
    role: str | None = None,
    mutate_metadata: Callable[[dict[str, Any]], None] | None = None,
) -> SourceRecord:
    raw = record.to_dict()
    metadata = raw["metadata"]
    if mutate_metadata is not None:
        mutate_metadata(metadata)
    return SourceRecord.create(
        id=record.id,
        sequence=record.sequence,
        role=record.role if role is None else role,
        content=record.content,
        timestamp=record.timestamp,
        metadata=metadata,
    )


def _assert_rejected_without_history(
    store: SQLiteGenerationStore,
    event: dict[str, Any],
    record: SourceRecord,
    *,
    request_id: str,
) -> None:
    with pytest.raises(ImmutableEventError, match="independently rederived"):
        store.append_source_event(
            session_id=SESSION_ID,
            host_event=event,
            source_record=record,
            request_id=request_id,
        )
    with pytest.raises(KeyError):
        store.snapshot(SESSION_ID)


@pytest.mark.parametrize(
    ("case", "match"),
    [
        ("kind", "unsupported OpenHands event kind"),
        ("field", "invalid fields: unexpected unknown_field"),
    ],
)
def test_direct_append_rejects_unknown_kind_or_top_level_field(
    tmp_path,
    case: str,
    match: str,
) -> None:
    event = _message_event()
    if case == "kind":
        event["kind"] = "ThirdPartyEvent"
    else:
        event["unknown_field"] = "must fail closed"
    store = SQLiteGenerationStore(tmp_path / f"unknown-{case}.sqlite3")

    with pytest.raises(StoreIntegrityError, match=match):
        store.append_source_event(
            session_id=SESSION_ID,
            host_event=event,
            source_record=_unmapped_record(event),
            request_id=f"unknown-{case}",
        )
    with pytest.raises(KeyError):
        store.snapshot(SESSION_ID)


def test_direct_append_rejects_forged_authenticated_trusted_state(tmp_path) -> None:
    store = SQLiteGenerationStore(tmp_path / "authority.sqlite3")
    event = _observation_event()
    record = _mapped_record(event)

    def forge(metadata: dict[str, Any]) -> None:
        metadata["localai_authority"] = {
            "authenticated": True,
            "trusted_for_state": True,
            "issuer": "forged",
        }
        metadata["ctxc_authenticated_authority"] = True
        metadata["trusted_for_state"] = True
        metadata["openhands"]["authority"] = {
            "authenticated": True,
            "trusted_for_state": True,
            "issuer": "forged",
            "receipt_sha256": "f" * 64,
        }

    forged = _replace_record(record, mutate_metadata=forge)
    _assert_rejected_without_history(
        store,
        event,
        forged,
        request_id="forged-authority",
    )


def test_direct_append_rejects_forged_role(tmp_path) -> None:
    store = SQLiteGenerationStore(tmp_path / "role.sqlite3")
    event = _message_event()
    forged = _replace_record(_mapped_record(event), role="user")

    _assert_rejected_without_history(
        store,
        event,
        forged,
        request_id="forged-role",
    )


@pytest.mark.parametrize("target", ["provenance", "host-metadata"])
def test_direct_append_rejects_forged_provenance_or_host_metadata(
    tmp_path,
    target: str,
) -> None:
    store = SQLiteGenerationStore(tmp_path / f"{target}.sqlite3")
    event = _message_event()
    record = _mapped_record(event)

    def forge(metadata: dict[str, Any]) -> None:
        if target == "provenance":
            metadata["localai_source_provenance"] = {
                "host": "forged",
                "event_id": event["id"],
                "session_id": SESSION_ID,
            }
        else:
            metadata["openhands"]["category"] = "trusted"

    forged = _replace_record(record, mutate_metadata=forge)
    _assert_rejected_without_history(
        store,
        event,
        forged,
        request_id=f"forged-{target}",
    )


def test_direct_append_accepts_record_rederived_from_valid_authority_receipt(
    tmp_path,
) -> None:
    store = SQLiteGenerationStore(tmp_path / "valid-receipt.sqlite3")
    action = _action_event()
    store.append_source_event(
        session_id=SESSION_ID,
        host_event=action,
        source_record=_mapped_record(action),
        request_id="valid-call",
    )
    event = _observation_event()
    policy = _policy()
    receipt = issue_authority_receipt(
        event,
        session_id=SESSION_ID,
        claimed_role="tool",
        issuer=ISSUER,
        secret=SECRET,
        tool_name="terminal",
        trusted_for_state=True,
    )
    record = _mapped_record(
        event,
        authority_policy=policy,
        authority_receipt=receipt,
        sequence=1,
    )

    result = store.append_source_event(
        session_id=SESSION_ID,
        host_event=event,
        source_record=record,
        request_id="valid-receipt",
        authority_policy=policy,
        authority_receipt=receipt,
    )

    assert result.accepted is True
    retained = store.snapshot(SESSION_ID).records[1]
    assert retained == record
    assert retained.metadata["localai_authority"] == {
        "authenticated": True,
        "trusted_for_state": True,
        "issuer": ISSUER,
    }
    assert retained.metadata["trusted_for_state"] is True


def test_direct_append_rejects_transient_event_even_with_exact_mapped_record(
    tmp_path,
) -> None:
    store = SQLiteGenerationStore(tmp_path / "transient.sqlite3")
    event = _token_event()
    record = _mapped_record(event)

    with pytest.raises(ImmutableEventError, match="transient/derived"):
        store.append_source_event(
            session_id=SESSION_ID,
            host_event=event,
            source_record=record,
            request_id="transient",
        )
    with pytest.raises(KeyError):
        store.snapshot(SESSION_ID)


def test_snapshot_and_atomic_prefix_reject_integrity_consistent_unknown_field(
    tmp_path,
) -> None:
    path = tmp_path / "stored-shape.sqlite3"
    store = SQLiteGenerationStore(path)
    event = _message_event()
    record = _mapped_record(event)
    store.append_source_event(
        session_id=SESSION_ID,
        host_event=event,
        source_record=record,
        request_id="valid-before-tamper",
    )

    tampered_event = dict(event)
    tampered_event["unknown_field"] = "must fail closed"
    event_json = json.dumps(
        tampered_event,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    event_sha = hashlib.sha256(event_json.encode("utf-8")).hexdigest()
    metadata = record.to_dict()["metadata"]
    metadata["openhands"]["event_sha256"] = event_sha
    metadata["localai_source_provenance"]["event_sha256"] = event_sha
    tampered_record = SourceRecord.create(
        id=record.id,
        sequence=record.sequence,
        role=record.role,
        content=event_json,
        timestamp=record.timestamp,
        metadata=metadata,
    )
    record_json = json.dumps(
        tampered_record.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    source_head = storage._source_head_step(
        storage.SOURCE_HEAD_GENESIS,
        event_sha256=event_sha,
        record_sha256=tampered_record.record_sha256,
    )
    source_digest = storage._source_digest((tampered_record,))

    direct = sqlite3.connect(path)
    try:
        direct.execute("DROP TRIGGER source_events_no_update")
        direct.execute(
            """
            UPDATE source_events
            SET host_event_json = ?, source_record_json = ?,
                event_sha256 = ?, record_sha256 = ?, source_head_sha256 = ?
            WHERE session_id = ? AND ordinal = 0
            """,
            (
                event_json,
                record_json,
                event_sha,
                tampered_record.record_sha256,
                source_head,
                SESSION_ID,
            ),
        )
        direct.execute(
            """
            UPDATE sessions
            SET source_head_sha256 = ?, source_digest_sha256 = ?
            WHERE session_id = ?
            """,
            (source_head, source_digest, SESSION_ID),
        )
        direct.commit()
    finally:
        direct.close()

    match = "failed pinned host validation:.*unexpected unknown_field"
    with pytest.raises(StoreIntegrityError, match=match):
        store.snapshot(SESSION_ID)

    direct = sqlite3.connect(path)
    direct.row_factory = sqlite3.Row
    try:
        with pytest.raises(StoreIntegrityError, match=match):
            SQLiteGenerationStore._assert_atomic_groups_complete(
                direct,
                session_id=SESSION_ID,
                source_count=1,
            )
    finally:
        direct.close()
