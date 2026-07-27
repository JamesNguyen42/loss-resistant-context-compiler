"""Explicit recovery for retained provisional SQLite generations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from context_compiler import LocalAIConnector

from .session import OpenHandsSession
from .storage import (
    PROVISIONAL_STATES,
    SQLiteGenerationStore,
    StaleGenerationError,
    StoreError,
)

RECOVERY_REPORT_SCHEMA = "ctxc-openhands-recovery-report-0.1"
ConnectorFactory = Callable[[], LocalAIConnector]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    session_id: str
    applied: bool
    passed: bool
    actions: tuple[dict[str, Any], ...]
    issues: tuple[str, ...]
    active_generation_id: str | None
    active_epoch: int
    source_count: int
    report_sha256: str
    schema: str = RECOVERY_REPORT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "session_id": self.session_id,
            "applied": self.applied,
            "passed": self.passed,
            "actions": [dict(action) for action in self.actions],
            "issues": list(self.issues),
            "active_generation_id": self.active_generation_id,
            "active_epoch": self.active_epoch,
            "source_count": self.source_count,
            "report_sha256": self.report_sha256,
        }


def _report(
    *,
    session_id: str,
    applied: bool,
    passed: bool,
    actions: list[dict[str, Any]],
    issues: list[str],
    active_generation_id: str | None,
    active_epoch: int,
    source_count: int,
) -> RecoveryReport:
    payload = {
        "schema": RECOVERY_REPORT_SCHEMA,
        "session_id": session_id,
        "applied": applied,
        "passed": passed,
        "actions": actions,
        "issues": issues,
        "active_generation_id": active_generation_id,
        "active_epoch": active_epoch,
        "source_count": source_count,
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return RecoveryReport(
        session_id=session_id,
        applied=applied,
        passed=passed,
        actions=tuple(dict(action) for action in actions),
        issues=tuple(issues),
        active_generation_id=active_generation_id,
        active_epoch=active_epoch,
        source_count=source_count,
        report_sha256=digest,
    )


def recover_session(
    store: SQLiteGenerationStore,
    *,
    session_id: str,
    apply: bool = False,
    connector_factory: ConnectorFactory = LocalAIConnector,
) -> RecoveryReport:
    """Inspect or explicitly recover retained provisional generations.

    Verified/committed candidates are completed through the normal transition
    and source-head CAS. Prepared candidates contain no verified result; an
    applied recovery terminally retains them as rolled back, then recompiles
    the current complete source head through a new generation.
    """

    if not isinstance(store, SQLiteGenerationStore):
        raise TypeError("store must be a SQLiteGenerationStore")
    if not isinstance(session_id, str) or not session_id:
        raise TypeError("session_id must be a non-empty string")
    if not isinstance(apply, bool):
        raise TypeError("apply must be a boolean")
    if not callable(connector_factory):
        raise TypeError("connector_factory must be callable")
    before = store.integrity_report()
    snapshot = store.snapshot(session_id)
    generations = store.list_generations(session_id)
    provisional = [
        row for row in generations if row["state"] in PROVISIONAL_STATES
    ]
    actions: list[dict[str, Any]] = []
    issues: list[str] = list(before["issues"])
    if not apply:
        for row in provisional:
            actions.append(
                {
                    "generation_id": row["generation_id"],
                    "state": row["state"],
                    "action": "would-recover",
                }
            )
        return _report(
            session_id=session_id,
            applied=False,
            passed=before["passed"],
            actions=actions,
            issues=issues,
            active_generation_id=snapshot.active_generation_id,
            active_epoch=snapshot.active_epoch,
            source_count=snapshot.source_count,
        )
    if not before["passed"]:
        issues.append("recovery refused because preflight integrity did not pass")
        return _report(
            session_id=session_id,
            applied=True,
            passed=False,
            actions=actions,
            issues=issues,
            active_generation_id=snapshot.active_generation_id,
            active_epoch=snapshot.active_epoch,
            source_count=snapshot.source_count,
        )

    prepared_seen = False
    session = OpenHandsSession(
        session_id=session_id,
        store=store,
        connector_factory=connector_factory,
    )
    for row in provisional:
        generation_id = str(row["generation_id"])
        state = str(row["state"])
        try:
            if state == "verified":
                store.commit_generation(
                    generation_id=generation_id,
                    operation_id=f"{generation_id}:recover-commit",
                )
                state = "committed"
                actions.append(
                    {
                        "generation_id": generation_id,
                        "state": "verified",
                        "action": "committed",
                    }
                )
            if state == "committed":
                epoch = store.activate_generation(
                    generation_id=generation_id,
                    operation_id=f"{generation_id}:recover-activate",
                )
                actions.append(
                    {
                        "generation_id": generation_id,
                        "state": "committed",
                        "action": "activated",
                        "active_epoch": epoch,
                    }
                )
            elif state == "prepared":
                prepared_seen = True
                store.roll_back_provisional(
                    generation_id=generation_id,
                    operation_id=f"{generation_id}:recover-rollback",
                    reason_code="prepared-without-verified-result",
                )
                actions.append(
                    {
                        "generation_id": generation_id,
                        "state": "prepared",
                        "action": "rolled-back-retained",
                    }
                )
        except StaleGenerationError:
            store.roll_back_provisional(
                generation_id=generation_id,
                operation_id=f"{generation_id}:recover-stale-rollback",
                reason_code="source-head-or-active-cas-stale",
            )
            actions.append(
                {
                    "generation_id": generation_id,
                    "state": state,
                    "action": "stale-rolled-back-retained",
                }
            )
            prepared_seen = True
        except (KeyError, StoreError, TypeError, ValueError) as exc:
            issues.append(f"{generation_id}: {exc}")

    current = store.snapshot(session_id)
    active = session.active()
    needs_current_generation = (
        prepared_seen
        and (
            active is None
            or active.covered_source_count != current.source_count
            or bool(active.tail)
        )
    )
    if needs_current_generation and not issues:
        try:
            compacted = session.compact()
        except (KeyError, StoreError, TypeError, ValueError, RuntimeError) as exc:
            issues.append(f"recompile-current-head: {exc}")
        else:
            actions.append(
                {
                    "generation_id": compacted.generation_id,
                    "state": "active",
                    "action": "recompiled-current-head",
                    "active_epoch": compacted.active_epoch,
                }
            )

    after = store.integrity_report()
    issues.extend(issue for issue in after["issues"] if issue not in issues)
    final_snapshot = store.snapshot(session_id)
    final_active = session.active()
    remaining = [
        row["generation_id"]
        for row in store.list_generations(session_id)
        if row["state"] in PROVISIONAL_STATES
    ]
    if remaining:
        issues.append("provisional generations remain: " + ", ".join(remaining))
    passed = after["passed"] and not issues
    return _report(
        session_id=session_id,
        applied=True,
        passed=passed,
        actions=actions,
        issues=issues,
        active_generation_id=(
            None if final_active is None else final_active.generation_id
        ),
        active_epoch=final_snapshot.active_epoch,
        source_count=final_snapshot.source_count,
    )


__all__ = [
    "RECOVERY_REPORT_SCHEMA",
    "RecoveryReport",
    "recover_session",
]
