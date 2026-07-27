from __future__ import annotations

import hashlib
import json

import pytest
from context_compiler import LocalAIConnector
from store_helpers import append_message, compile_generation, store_path

from ctxc_openhands.recovery import RECOVERY_REPORT_SCHEMA, recover_session
from ctxc_openhands.session import OpenHandsSession
from ctxc_openhands.storage import SQLiteGenerationStore


def _report_digest(report: dict) -> str:
    unsigned = {key: value for key, value in report.items() if key != "report_sha256"}
    raw = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_recovery_inspection_is_non_mutating(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    generation_id, _bundle, _replay = compile_generation(
        store,
        1,
        activate=False,
    )

    report = recover_session(store, session_id="session-1")

    assert report.applied is False
    assert report.passed is True
    assert report.actions == (
        {
            "generation_id": generation_id,
            "state": "committed",
            "action": "would-recover",
        },
    )
    assert store.list_generations("session-1")[0]["state"] == "committed"
    assert store.read_active("session-1") is None


def test_recovery_activates_retained_committed_generation(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    generation_id, _bundle, _replay = compile_generation(
        store,
        1,
        activate=False,
    )

    report = recover_session(store, session_id="session-1", apply=True)

    active = store.read_active("session-1")
    assert report.passed is True
    assert active is not None
    assert active.generation_id == generation_id
    assert report.active_generation_id == generation_id
    assert report.actions[-1]["action"] == "activated"
    assert report.to_dict()["report_sha256"] == _report_digest(report.to_dict())


def test_recovery_rolls_back_unverified_prepare_then_recompiles(
    tmp_path,
) -> None:
    class ForgedPassingVerifier(LocalAIConnector):
        def verify_memory(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return {"passed": True, "issues": []}

    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    connectors = iter((LocalAIConnector(), ForgedPassingVerifier()))
    failing = OpenHandsSession(
        session_id="session-1",
        store=store,
        connector_factory=lambda: next(connectors),
    )
    with pytest.raises(TypeError, match="distinct exact LocalAIConnector"):
        failing.compact(generation_id="interrupted-prepared")

    report = recover_session(store, session_id="session-1", apply=True)

    states = {
        row["generation_id"]: row["state"]
        for row in store.list_generations("session-1")
    }
    active = store.read_active("session-1")
    assert report.passed is True
    assert states["interrupted-prepared"] == "rolled_back"
    assert active is not None
    assert active.covered_source_count == 1
    assert any(
        action["action"] == "recompiled-current-head"
        for action in report.actions
    )


def test_stale_committed_candidate_is_retained_rolled_back_before_current_recompile(
    tmp_path,
) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    first_id, _bundle, _replay = compile_generation(store, 1)
    append_message(store, 1)
    stale_id, _bundle, _replay = compile_generation(
        store,
        2,
        activate=False,
    )
    append_message(store, 2)

    report = recover_session(store, session_id="session-1", apply=True)

    states = {
        row["generation_id"]: row["state"]
        for row in store.list_generations("session-1")
    }
    active = store.read_active("session-1")
    assert report.passed is True
    assert states[first_id] == "superseded"
    assert states[stale_id] == "rolled_back"
    assert active is not None
    assert active.covered_source_count == 3
    assert active.generation_id not in {first_id, stale_id}
    assert any(
        action["action"] == "stale-rolled-back-retained"
        for action in report.actions
    )


def test_recovery_report_schema_and_hash(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)

    report = recover_session(store, session_id="session-1").to_dict()

    assert report["schema"] == RECOVERY_REPORT_SCHEMA
    assert report["report_sha256"] == _report_digest(report)
