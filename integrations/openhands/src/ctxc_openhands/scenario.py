"""Recorded offline crash/restart scenario for the isolated integration."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from context_compiler import LocalAIConnector

from .evidence import (
    LIVE_DEPENDENCY_BLOCKER,
    OFFLINE_SCENARIO_SCHEMA,
    _verify_evidence_mapping,
    checkpoint_and_bind_database,
    finalize_evidence_report,
    producer_command,
    runtime_identity,
)
from .fake_runtime import OfflineFakeRuntime
from .session import OpenHandsSession
from .storage import SQLiteGenerationStore


class InjectedScenarioCrash(RuntimeError):
    """Intentional process-boundary crash point used by the offline scenario."""


class _ThirdActivationCrash:
    def __init__(self) -> None:
        self.activations = 0

    def __call__(self, point: str) -> None:
        if point != "activate.after_new_active":
            return
        self.activations += 1
        if self.activations == 3:
            raise InjectedScenarioCrash("third activation interrupted before commit")


def _message(index: int, text: str) -> dict[str, Any]:
    return {
        "kind": "MessageEvent",
        "id": f"scenario-message-{index}",
        "timestamp": f"2026-07-27T12:00:{index:02d}+00:00",
        "source": "agent",
        "llm_message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }


def run_offline_crash_scenario(
    database: str | Path,
    *, producer_argv: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run three compactions with an injected third-activation crash.

    The returned record is self-hashed and labels the run as an offline fake
    runtime diagnostic.  It is not evidence of a live OpenHands execution.
    """

    path = Path(database).expanduser().absolute()
    crash = _ThirdActivationCrash()
    store = SQLiteGenerationStore(path, fault_hook=crash, require_new=True)
    session = OpenHandsSession(
        session_id="offline-recorded-scenario",
        store=store,
        connector_factory=LocalAIConnector,
    )
    constraints = (
        "MUST keep PostgreSQL as the durable database.",
        "MUST keep authentication enabled for every connection.",
        "NEVER replace the PostgreSQL authentication constraint with an unauthenticated demo.",
    )
    compactions: list[dict[str, Any]] = []
    crashed = False
    for index, constraint in enumerate(constraints):
        session.ingest(
            _message(index, constraint),
            request_id=f"scenario-append-{index}",
        )
        try:
            result = session.compact()
        except InjectedScenarioCrash:
            if index != 2:
                raise
            crashed = True
            break
        compactions.append(
            {
                "ordinal": index + 1,
                **result.to_dict(),
                "recovered_after_injected_crash": False,
            }
        )
    if not crashed:
        raise AssertionError("scenario did not reach the injected crash")

    before_restart = store.read_active(session.session_id)
    if before_restart is None or before_restart.active_epoch != 2:
        raise AssertionError("crash exposed neither the expected old generation")
    states_before = store.list_generations(session.session_id)
    committed = tuple(
        row for row in states_before if row["state"] == "committed"
    )
    if len(committed) != 1:
        raise AssertionError("crash did not retain exactly one committed candidate")

    # Simulate a fresh process by constructing new store/session objects with
    # no fault hook. SQLite rolls back the interrupted activation transaction.
    recovered_store = SQLiteGenerationStore(
        path,
        require_existing=True,
    )
    recovered_session = OpenHandsSession(
        session_id=session.session_id,
        store=recovered_store,
        connector_factory=LocalAIConnector,
    )
    recovered_generation = str(committed[0]["generation_id"])
    recovered_epoch = recovered_store.activate_generation(
        generation_id=recovered_generation,
        operation_id=f"{recovered_generation}:scenario-recover",
    )
    after_restart = recovered_session.active()
    if (
        after_restart is None
        or after_restart.generation_id != recovered_generation
        or after_restart.active_epoch != recovered_epoch
        or after_restart.covered_source_count != len(constraints)
    ):
        raise AssertionError("recovered generation is not the complete verified prefix")
    compactions.append(
        {
            "ordinal": 3,
            "generation_id": recovered_generation,
            "active_epoch": recovered_epoch,
            "source_count": after_restart.covered_source_count,
            "source_head_sha256": after_restart.source_head_sha256,
            "bundle_sha256": after_restart.bundle.bundle_sha256,
            "semantic_result_digest": after_restart.semantic_result_digest,
            "recovered_after_injected_crash": True,
        }
    )

    snapshot = recovered_store.snapshot(recovered_session.session_id)
    expected_events = tuple(
        _message(index, constraint)
        for index, constraint in enumerate(constraints)
    )
    retained_events = tuple(
        json.loads(record.content)
        for record in snapshot.records
    )
    if retained_events != expected_events:
        raise AssertionError("source history did not retain the exact ordered events")
    if after_restart.bundle.certificate["semantic_completeness_claimed"] is not False:
        raise AssertionError("scenario bundle widened the semantic claim boundary")

    fake = OfflineFakeRuntime(recovered_session)
    final_request = fake.prepare(
        request_id="scenario-final-request",
        prompts=("Offline diagnostic only; preserve all authority boundaries.",),
        current_turn="Report the retained PostgreSQL and authentication constraints.",
        tool_schemas=(
            {
                "name": "read_only_constraint_report",
                "input": {"type": "object", "additionalProperties": False},
            },
        ),
    )
    replay = final_request.replay()
    receipt = fake.dispatch(final_request)
    if not replay.passed:
        raise AssertionError("offline scenario final request did not replay")

    generations = recovered_store.list_generations(recovered_session.session_id)
    integrity = recovered_store.integrity_report()
    ids = tuple(record.id for record in snapshot.records)
    sequences = tuple(record.sequence for record in snapshot.records)
    database_binding = checkpoint_and_bind_database(path)
    report = finalize_evidence_report({
        "schema": OFFLINE_SCENARIO_SCHEMA,
        "evidence_kind": "offline-crash-scenario",
        "status": "passed-offline-fake-runtime",
        "command": producer_command(
            library_name="ctxc_openhands.scenario.run_offline_crash_scenario",
            subcommand="offline-scenario",
            parameters={"database": str(path)},
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
            "session_id": recovered_session.session_id,
            "source_count": snapshot.source_count,
            "source_head_sha256": snapshot.source_head_sha256,
            "active_generation_id": after_restart.generation_id,
            "active_epoch": after_restart.active_epoch,
            "active_semantic_result_digest": after_restart.semantic_result_digest,
            "generation_states": [
                {
                    "generation_id": row["generation_id"],
                    "state": row["state"],
                }
                for row in generations
            ],
        },
        "forced_compaction_count": 3,
        "injected_crash_point": "activate.after_new_active",
        "old_generation_visible_after_crash": before_restart.generation_id,
        "recovered_generation": recovered_generation,
        "compactions": compactions,
        "constraints": list(constraints),
        "constraints_retained_exactly": True,
        "no_event_loss": len(snapshot.records) == len(constraints),
        "no_event_duplication": len(set(ids)) == len(ids),
        "contiguous_sequences": sequences == tuple(range(len(sequences))),
        "integrity_report_sha256": integrity["report_sha256"],
        "semantic_completeness_claimed": False,
        "final_request": {
            "request_id": final_request.request_id,
            "replay": replay.to_dict(),
            "fake_dispatch_receipt": receipt.to_dict(),
        },
    })
    verification = _verify_evidence_mapping(report, database=path)
    if verification.get("passed") is not True:
        raise AssertionError(
            "scenario retained-evidence self-verification failed: "
            f"{verification.get('issues', [])}"
        )
    return report


__all__ = [
    "LIVE_DEPENDENCY_BLOCKER",
    "OFFLINE_SCENARIO_SCHEMA",
    "InjectedScenarioCrash",
    "run_offline_crash_scenario",
]
