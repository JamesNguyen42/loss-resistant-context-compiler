"""Deterministic event/compaction soak for the offline integration."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from context_compiler import CompilationPolicy, LocalAIConnector

from .evidence import (
    LIVE_DEPENDENCY_BLOCKER,
    MAX_SOAK_COMPACTIONS,
    MAX_SOAK_EVENTS,
    SOAK_REPORT_SCHEMA,
    _verify_evidence_mapping,
    checkpoint_and_bind_database,
    finalize_evidence_report,
    producer_command,
    runtime_identity,
)
from .session import OpenHandsSession
from .storage import SQLiteGenerationStore


def _connector() -> LocalAIConnector:
    # Discarded ordinary candidates stay recoverable from the immutable source
    # store but need not be copied into every soak artifact.
    return LocalAIConnector(
        policy=CompilationPolicy(include_discarded=False),
    )


def _message(index: int) -> dict[str, Any]:
    timestamp = datetime(2026, 7, 27, 12, tzinfo=UTC) + timedelta(
        microseconds=index
    )
    return {
        "kind": "MessageEvent",
        "id": f"soak-event-{index:05d}",
        "timestamp": timestamp.isoformat(),
        "source": "agent",
        "llm_message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Deterministic ordinary soak history."}],
        },
    }


def _compaction_thresholds(
    *,
    event_count: int,
    compaction_count: int,
) -> tuple[int, ...]:
    # Ceiling division spreads compactions deterministically and always ends on
    # the complete event prefix.
    return tuple(
        (ordinal * event_count + compaction_count - 1) // compaction_count
        for ordinal in range(1, compaction_count + 1)
    )


def run_deterministic_soak(
    database: str | Path,
    *,
    event_count: int = 10_000,
    compaction_count: int = 100,
    restart_every_compactions: int | None = None,
    producer_argv: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run at most 10,000 events/100 compactions and return verified evidence."""

    for name, value, maximum in (
        ("event_count", event_count, MAX_SOAK_EVENTS),
        ("compaction_count", compaction_count, MAX_SOAK_COMPACTIONS),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
        if value <= 0:
            raise ValueError(f"{name} must be positive")
        if value > maximum:
            raise ValueError(f"{name} cannot exceed {maximum}")
    if compaction_count > event_count:
        raise ValueError("compaction_count cannot exceed event_count")
    if restart_every_compactions is not None:
        if (
            isinstance(restart_every_compactions, bool)
            or not isinstance(restart_every_compactions, int)
        ):
            raise TypeError("restart_every_compactions must be an integer or null")
        if restart_every_compactions <= 0:
            raise ValueError("restart_every_compactions must be positive")

    path = Path(database).expanduser().absolute()
    session_id = "deterministic-soak"
    store = SQLiteGenerationStore(path, require_new=True)
    session = OpenHandsSession(
        session_id=session_id,
        store=store,
        connector_factory=_connector,
    )
    thresholds = _compaction_thresholds(
        event_count=event_count,
        compaction_count=compaction_count,
    )
    compaction_index = 0
    semantic_digests: list[str] = []
    bundle_digests: list[str] = []
    generation_ids: list[str] = []
    for index in range(event_count):
        session.ingest(
            _message(index),
            request_id=f"soak-append-{index:05d}",
        )
        if index + 1 != thresholds[compaction_index]:
            continue
        compacted = session.compact()
        if compacted.source_count != index + 1:
            raise AssertionError("compaction did not cover the scheduled source prefix")
        semantic_digests.append(compacted.semantic_result_digest)
        bundle_digests.append(compacted.bundle_sha256)
        generation_ids.append(compacted.generation_id)
        compaction_index += 1
        if (
            restart_every_compactions is not None
            and compaction_index < compaction_count
            and compaction_index % restart_every_compactions == 0
        ):
            store = SQLiteGenerationStore(path, require_existing=True)
            session = OpenHandsSession(
                session_id=session_id,
                store=store,
                connector_factory=_connector,
            )
        if compaction_index == compaction_count:
            break

    if compaction_index != compaction_count:
        raise AssertionError("soak did not execute every scheduled compaction")
    snapshot = store.snapshot(session_id)
    active = session.active()
    generations = store.list_generations(session_id)
    integrity = store.integrity_report()
    ids = tuple(record.id for record in snapshot.records)
    sequences = tuple(record.sequence for record in snapshot.records)
    if (
        snapshot.source_count != event_count
        or len(set(ids)) != event_count
        or sequences != tuple(range(event_count))
        or active is None
        or active.covered_source_count != event_count
        or active.tail
        or not integrity["passed"]
    ):
        raise AssertionError("soak source/generation invariants did not hold")
    states = [str(row["state"]) for row in generations]
    if (
        len(generations) != compaction_count
        or states.count("active") != 1
        or states.count("superseded") != compaction_count - 1
    ):
        raise AssertionError("soak generation state counts are invalid")

    database_binding = checkpoint_and_bind_database(path)
    report = finalize_evidence_report(
        {
            "schema": SOAK_REPORT_SCHEMA,
            "evidence_kind": "deterministic-soak",
            "status": "passed",
            "command": producer_command(
                library_name="ctxc_openhands.soak.run_deterministic_soak",
                subcommand="soak",
                parameters={
                    "database": str(path),
                    "events": event_count,
                    "compactions": compaction_count,
                    "restart_every_compactions": restart_every_compactions,
                },
                argv=producer_argv,
            ),
            "runtime": runtime_identity(),
            "isolation": {
                "execution_path_network_capability": "none",
                "network_isolation_enforced": False,
                "paid_service_use": "none",
            },
            "live_openhands": {
                "status": "blocked-not-run",
                "blocker": LIVE_DEPENDENCY_BLOCKER,
            },
            "database": database_binding,
            "session": {
                "session_id": session_id,
                "source_count": snapshot.source_count,
                "source_head_sha256": snapshot.source_head_sha256,
                "active_generation_id": active.generation_id,
                "active_epoch": active.active_epoch,
                "active_semantic_result_digest": active.semantic_result_digest,
                "generation_states": [
                    {
                        "generation_id": str(row["generation_id"]),
                        "state": str(row["state"]),
                    }
                    for row in generations
                ],
            },
            "event_count": event_count,
            "compaction_count": compaction_count,
            "restart_every_compactions": restart_every_compactions,
            "generation_ids": generation_ids,
            "bundle_sha256s": bundle_digests,
            "semantic_result_digests": semantic_digests,
            "no_event_loss": True,
            "no_event_duplication": True,
            "contiguous_sequences": True,
            "integrity_report_sha256": integrity["report_sha256"],
            "semantic_completeness_claimed": False,
        }
    )
    verification = _verify_evidence_mapping(report, database=path)
    if verification.get("passed") is not True:
        raise AssertionError(
            "soak retained-evidence self-verification failed: "
            f"{verification.get('issues', [])}"
        )
    return report


__all__ = [
    "MAX_SOAK_COMPACTIONS",
    "MAX_SOAK_EVENTS",
    "SOAK_REPORT_SCHEMA",
    "run_deterministic_soak",
]
