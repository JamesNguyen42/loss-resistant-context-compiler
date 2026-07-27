from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from context_compiler import LocalAIConnector

from ctxc_openhands.fake_runtime import (
    FAKE_RUNTIME_MODEL,
    FAKE_RUNTIME_PATH,
    FakeFinalRequest,
    OfflineFakeRuntime,
)
from ctxc_openhands.request_ledger import (
    REQUIRED_COMPONENT_CATEGORIES,
    RequestLedgerError,
)
from ctxc_openhands.session import OpenHandsSession
from ctxc_openhands.storage import SQLiteGenerationStore, StoreIntegrityError


def _message(index: int, text: str) -> dict:
    return {
        "kind": "MessageEvent",
        "id": f"message-{index}",
        "timestamp": f"2026-07-27T12:00:{index:02d}+00:00",
        "source": "agent",
        "llm_message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }


def _runtime(tmp_path: Path) -> tuple[OpenHandsSession, OfflineFakeRuntime]:
    session = OpenHandsSession(
        session_id="fake-session",
        store=SQLiteGenerationStore(tmp_path / "store.sqlite3"),
        connector_factory=LocalAIConnector,
    )
    session.ingest(
        _message(0, "Keep PostgreSQL authentication enabled."),
        request_id="append-0",
    )
    session.compact()
    return session, OfflineFakeRuntime(session)


def test_final_request_accounts_for_every_component_and_replays(
    tmp_path: Path,
) -> None:
    session, runtime = _runtime(tmp_path)
    request = runtime.prepare(
        request_id="request-1",
        prompts=("You are an offline fake runtime.",),
        current_turn="Continue.",
        retrieval=({"source": "fixture", "text": "retrieved"},),
        attachments=({"name": "fixture.txt", "sha256": "0" * 64},),
        tool_schemas=({"name": "bash", "input": {"type": "object"}},),
    )

    active = session.active()
    ledger = request.ledger.to_dict()
    replay = request.replay()
    receipt = runtime.dispatch(request)

    assert replay.passed is True
    assert tuple(dict(replay.component_counts)) == REQUIRED_COMPONENT_CATEGORIES
    assert request.model == FAKE_RUNTIME_MODEL
    assert request.path == FAKE_RUNTIME_PATH
    assert ledger["bindings"]["session_id"] == session.session_id
    assert ledger["bindings"]["generation_id"] == active.generation_id
    assert ledger["bindings"]["active_epoch"] == active.active_epoch
    assert ledger["bindings"]["source_head_sha256"] == active.source_head_sha256
    assert receipt.final_request_sha256 == request.final_request_sha256
    assert receipt.total_tokens == len(request.transport_payload.encode("utf-8"))
    assert dict(receipt.component_counts) == dict(replay.component_counts)


def test_request_views_cannot_mutate_immutable_dispatch(tmp_path: Path) -> None:
    _session, runtime = _runtime(tmp_path)
    request = runtime.prepare(request_id="request-1", current_turn="Continue.")
    original_payload = request.transport_payload
    original_ledger = request.ledger.to_dict()

    detached_parts = list(request.transport_parts)
    detached_parts[0]["text"] = "tampered"
    detached_ledger = request.ledger.to_dict()
    detached_ledger["transport"]["parts"][0]["text"] = "tampered"

    assert request.transport_payload == original_payload
    assert request.ledger.to_dict() == original_ledger
    assert request.replay().passed is True


def test_dispatch_rejects_successful_replay_without_total_tokens(
    tmp_path: Path,
    monkeypatch,
) -> None:
    session, runtime = _runtime(tmp_path)
    request = runtime.prepare(request_id="request-1", current_turn="Continue.")
    malformed = replace(request.replay(), total_tokens=None)
    assert malformed.passed is True
    monkeypatch.setattr(FakeFinalRequest, "replay", lambda _self: malformed)

    with pytest.raises(RuntimeError, match="omitted total_tokens"):
        runtime.dispatch(request)

    with pytest.raises(KeyError, match="unknown request ledger"):
        session.store.load_request_ledger(
            session_id=session.session_id,
            request_id=request.request_id,
            tokenizer=runtime.tokenizer,
        )


def test_hard_limit_overflow_is_refused(tmp_path: Path) -> None:
    _session, runtime = _runtime(tmp_path)

    with pytest.raises(RequestLedgerError, match="exceeds hard token limit"):
        runtime.prepare(
            request_id="too-large",
            current_turn="Continue.",
            reserved_output_tokens=0,
            safety_margin_tokens=0,
            hard_limit_tokens=1,
        )


def test_source_head_change_between_prepare_and_dispatch_fails_closed(
    tmp_path: Path,
) -> None:
    session, runtime = _runtime(tmp_path)
    request = runtime.prepare(request_id="stale", current_turn="Continue.")
    session.ingest(
        _message(1, "New immutable tail."),
        request_id="append-1",
    )

    with pytest.raises(StoreIntegrityError, match="bindings do not match"):
        runtime.dispatch(request)


def test_recent_tail_is_part_of_exact_request(tmp_path: Path) -> None:
    session, runtime = _runtime(tmp_path)
    before = runtime.prepare(request_id="before", current_turn="Continue.")
    session.ingest(
        _message(1, "Uncompacted recent tail."),
        request_id="append-1",
    )
    after = runtime.prepare(request_id="after", current_turn="Continue.")

    before_counts = dict(before.ledger["accounting"]["component_counts"])
    after_counts = dict(after.ledger["accounting"]["component_counts"])
    assert after_counts["recent_tail"] > before_counts["recent_tail"]
    assert after.final_request_sha256 != before.final_request_sha256


def test_exact_dispatch_retry_is_idempotent(tmp_path: Path) -> None:
    _session, runtime = _runtime(tmp_path)
    request = runtime.prepare(request_id="request-1", current_turn="Continue.")

    first = runtime.dispatch(request)
    second = runtime.dispatch(request)

    assert first == second
