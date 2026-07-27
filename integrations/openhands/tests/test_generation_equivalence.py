from __future__ import annotations

from pathlib import Path

import pytest
from context_compiler import IncrementalCompiler, LocalAIConnector

from ctxc_openhands.recovery import recover_session
from ctxc_openhands.session import OpenHandsSession
from ctxc_openhands.storage import SQLiteGenerationStore


class InjectedActivationCrash(BaseException):
    pass


class OneShotActivationCrash:
    def __init__(self) -> None:
        self.triggered = False

    def __call__(self, point: str) -> None:
        if not self.triggered and point == "activate.after_new_active":
            self.triggered = True
            raise InjectedActivationCrash(point)


def _event(index: int) -> dict:
    return {
        "kind": "MessageEvent",
        "id": f"equivalence-{index}",
        "timestamp": f"2026-07-27T12:00:{index:02d}+00:00",
        "source": "agent",
        "llm_message": {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": f"Deterministic history item {index}.",
                }
            ],
        },
    }


def _new_session(path: Path, *, fault_hook=None) -> OpenHandsSession:  # type: ignore[no-untyped-def]
    return OpenHandsSession(
        session_id="equivalence-session",
        store=SQLiteGenerationStore(path, fault_hook=fault_hook),
        connector_factory=LocalAIConnector,
    )


def _append_range(
    session: OpenHandsSession,
    start: int,
    end: int,
) -> None:
    for index in range(start, end):
        session.ingest(_event(index), request_id=f"append-{index}")


def test_all_resume_modes_produce_identical_verified_bundles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _new_session(tmp_path / "batch.sqlite3")
    _append_range(batch, 0, 6)
    batch.compact()

    uninterrupted = _new_session(tmp_path / "uninterrupted.sqlite3")
    _append_range(uninterrupted, 0, 3)
    uninterrupted.compact()
    _append_range(uninterrupted, 3, 6)
    uninterrupted.compact()

    reopened_path = tmp_path / "reopened.sqlite3"
    reopened = _new_session(reopened_path)
    _append_range(reopened, 0, 3)
    reopened = _new_session(reopened_path)
    _append_range(reopened, 3, 6)
    reopened.compact()

    checkpoint_resumed = _new_session(tmp_path / "checkpoint-resumed.sqlite3")
    _append_range(checkpoint_resumed, 0, 6)
    original_checkpoint = IncrementalCompiler.checkpoint

    def checkpoint_after_roundtrip(self: IncrementalCompiler) -> dict:
        serialized = original_checkpoint(self)
        resumed = IncrementalCompiler.from_checkpoint(
            serialized,
            compiler=self.compiler,
        )
        return original_checkpoint(resumed)

    with monkeypatch.context() as scoped:
        scoped.setattr(IncrementalCompiler, "checkpoint", checkpoint_after_roundtrip)
        checkpoint_resumed.compact()

    crash_path = tmp_path / "crash.sqlite3"
    crash = OneShotActivationCrash()
    crashing = _new_session(crash_path, fault_hook=crash)
    _append_range(crashing, 0, 6)
    with pytest.raises(InjectedActivationCrash):
        crashing.compact()
    crash_reopened = _new_session(crash_path)
    recovery = recover_session(
        crash_reopened.store,
        session_id=crash_reopened.session_id,
        apply=True,
    )
    assert recovery.passed is True

    sessions = (
        batch,
        uninterrupted,
        reopened,
        checkpoint_resumed,
        crash_reopened,
    )
    active = tuple(session.active() for session in sessions)
    assert all(generation is not None for generation in active)
    bundles = [generation.bundle.to_dict() for generation in active if generation]
    heads = [generation.source_head_sha256 for generation in active if generation]

    assert len(set(heads)) == 1
    assert all(bundle == bundles[0] for bundle in bundles[1:])
    assert len({bundle["bundle_sha256"] for bundle in bundles}) == 1
    assert all(generation.covered_source_count == 6 for generation in active if generation)
    assert all(generation.tail == () for generation in active if generation)
