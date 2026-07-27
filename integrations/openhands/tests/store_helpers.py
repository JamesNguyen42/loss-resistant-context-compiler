from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any

from context_compiler import (
    IncrementalCompiler,
    LocalAIConnector,
    SourceEvent,
    SourceRecord,
    source_event_to_record,
)

from ctxc_openhands.event_map import EventMappingError, map_host_event
from ctxc_openhands.storage import (
    SQLiteGenerationStore,
    _mint_independent_verification_receipt,
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def message_event(sequence: int, text: str = "ordinary history") -> dict[str, Any]:
    return {
        "kind": "MessageEvent",
        "id": f"event-{sequence}",
        "timestamp": f"2026-07-27T12:00:{sequence % 60:02d}+00:00",
        "source": "agent",
        "llm_message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }


def source_record(
    event: Mapping[str, Any],
    sequence: int,
    *,
    session_id: str = "session-1",
    atomic_groups: list[dict[str, Any]] | None = None,
) -> SourceRecord:
    try:
        mapped = map_host_event(
            event,
            sequence=sequence,
            session_id=session_id,
        )
        record = source_event_to_record(mapped, default_sequence=sequence)
    except EventMappingError:
        record = source_event_to_record(
            SourceEvent(
                id=str(event["id"]),
                sequence=sequence,
                role="assistant",
                content=canonical_json(event),
                timestamp=str(event["timestamp"]),
                metadata={"openhands": {"event_kind": event.get("kind", "unknown")}},
                authority={
                    "authenticated": False,
                    "trusted_for_state": False,
                },
            ),
            default_sequence=sequence,
        )
    if atomic_groups is None:
        return record
    record_data = record.to_dict()
    metadata = record_data["metadata"]
    host_metadata = metadata["openhands"]
    host_metadata.pop("atomic_group", None)
    host_metadata.pop("atomic_groups", None)
    if len(atomic_groups) == 1:
        host_metadata["atomic_group"] = atomic_groups[0]
    elif atomic_groups:
        host_metadata["atomic_groups"] = atomic_groups
    return SourceRecord.create(
        id=record.id,
        sequence=record.sequence,
        role=record.role,
        content=record.content,
        timestamp=record.timestamp,
        metadata=metadata,
    )


def append_message(
    store: SQLiteGenerationStore,
    sequence: int,
    *,
    session_id: str = "session-1",
    request_id: str | None = None,
    text: str = "ordinary history",
) -> SourceRecord:
    event = message_event(sequence, text)
    record = source_record(event, sequence, session_id=session_id)
    store.append_source_event(
        session_id=session_id,
        host_event=event,
        source_record=record,
        request_id=request_id or f"append-{sequence}",
    )
    return record


def compile_generation(
    store: SQLiteGenerationStore,
    generation_number: int,
    *,
    session_id: str = "session-1",
    activate: bool = True,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    generation_id = f"generation-{generation_number}"
    connector = LocalAIConnector()
    policy = {
        field.name: getattr(connector.policy, field.name)
        for field in fields(connector.policy)
    }
    prepared = store.prepare_generation(
        session_id=session_id,
        generation_id=generation_id,
        operation_id=f"prepare-{generation_number}",
        policy_sha256=hashlib.sha256(canonical_json(policy).encode()).hexdigest(),
        tokenizer_identity=(
            connector.token_counter_id
            if connector.token_counter is not None
            else "character-estimate-v1"
        ),
    )
    incremental = IncrementalCompiler(
        session_id=session_id,
        sources=prepared.snapshot.records,
    )
    checkpoint = incremental.checkpoint()
    bundle = connector.compile_memory(
        session_id=session_id,
        checkpoint=checkpoint,
    )
    replay = connector.verify_memory(
        bundle,
        session_id=session_id,
        checkpoint=checkpoint,
    )
    assert replay["passed"] is True
    verification_receipt = _mint_independent_verification_receipt(
        generation_id=generation_id,
        checkpoint=checkpoint,
        bundle=bundle,
        replay_report=replay,
    )
    store.record_verified(
        generation_id=generation_id,
        operation_id=f"verify-{generation_number}",
        checkpoint=checkpoint,
        bundle=bundle,
        verification_receipt=verification_receipt,
    )
    store.commit_generation(
        generation_id=generation_id,
        operation_id=f"commit-{generation_number}",
    )
    if activate:
        store.activate_generation(
            generation_id=generation_id,
            operation_id=f"activate-{generation_number}",
        )
    return generation_id, bundle.to_dict(), replay


def store_path(tmp_path: Path) -> Path:
    return tmp_path / "ctxc-openhands.sqlite3"
