from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import fields

import pytest
from context_compiler import IncrementalCompiler, LocalAIConnector
from store_helpers import append_message, canonical_json, compile_generation, store_path

from ctxc_openhands.replay import replay_final_request
from ctxc_openhands.request_ledger import (
    RequestLedgerError,
    build_final_request_ledger,
)
from ctxc_openhands.storage import (
    SQLiteGenerationStore,
    StoreIntegrityError,
    _mint_independent_verification_receipt,
)
from ctxc_openhands.tokenizer import CanonicalUtf8ByteTokenizer


def _prepared_evidence(
    store: SQLiteGenerationStore,
    *,
    generation_id: str,
    policy_sha256: str,
    tokenizer_identity: str,
):
    prepared = store.prepare_generation(
        session_id="session-1",
        generation_id=generation_id,
        operation_id=f"{generation_id}:prepare",
        policy_sha256=policy_sha256,
        tokenizer_identity=tokenizer_identity,
    )
    compiler = IncrementalCompiler(
        session_id="session-1",
        sources=prepared.snapshot.records,
    )
    checkpoint = compiler.checkpoint()
    connector = LocalAIConnector()
    bundle = connector.compile_memory(
        session_id="session-1",
        checkpoint=checkpoint,
    )
    replay = connector.verify_memory(
        bundle,
        session_id="session-1",
        checkpoint=checkpoint,
    )
    assert replay["passed"] is True
    return checkpoint, bundle, replay


def _connector_bindings() -> tuple[str, str]:
    connector = LocalAIConnector()
    policy = {
        field.name: getattr(connector.policy, field.name)
        for field in fields(connector.policy)
    }
    policy_sha = hashlib.sha256(canonical_json(policy).encode()).hexdigest()
    tokenizer = (
        connector.token_counter_id
        if connector.token_counter is not None
        else "character-estimate-v1"
    )
    return policy_sha, tokenizer


def _request_ledger(
    store: SQLiteGenerationStore,
    *,
    session_id: str | None = None,
    generation_id: str | None = None,
    active_epoch: int | None = None,
    source_head_sha256: str | None = None,
    semantic_result_digest: str | None = None,
):
    active = store.read_active("session-1")
    assert active is not None
    tokenizer = CanonicalUtf8ByteTokenizer()
    ledger = build_final_request_ledger(
        transport_parts=[
            {"category": "prompts", "text": "P"},
            {"category": "verified_memory", "text": "M"},
            {"category": "recent_tail", "text": ""},
            {"category": "current_turn", "text": "U"},
            {"category": "retrieval", "text": ""},
            {"category": "attachments", "text": ""},
            {"category": "tool_schemas", "text": "T"},
            {"category": "provider_framing", "text": "{}"},
        ],
        model="offline-fake",
        path="chat",
        session_id=active.session_id if session_id is None else session_id,
        generation_id=(
            active.generation_id if generation_id is None else generation_id
        ),
        active_epoch=active.active_epoch if active_epoch is None else active_epoch,
        source_head_sha256=(
            active.source_head_sha256
            if source_head_sha256 is None
            else source_head_sha256
        ),
        semantic_result_digest=(
            active.semantic_result_digest
            if semantic_result_digest is None
            else semantic_result_digest
        ),
        reserved_output_tokens=8,
        safety_margin_tokens=4,
        hard_limit_tokens=100,
        tokenizer=tokenizer,
    )
    return ledger, tokenizer


def test_verified_generation_rejects_prepared_policy_mismatch(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    checkpoint, bundle, replay = _prepared_evidence(
        store,
        generation_id="policy-mismatch",
        policy_sha256="0" * 64,
        tokenizer_identity="character-estimate-v1",
    )
    verification_receipt = _mint_independent_verification_receipt(
        generation_id="policy-mismatch",
        checkpoint=checkpoint,
        bundle=bundle,
        replay_report=replay,
    )

    with pytest.raises(StoreIntegrityError, match="policy, and tokenizer"):
        store.record_verified(
            generation_id="policy-mismatch",
            operation_id="policy-mismatch:verify",
            checkpoint=checkpoint,
            bundle=bundle,
            verification_receipt=verification_receipt,
        )

    listed = store.list_generations("session-1")
    assert listed[0]["state"] == "prepared"
    assert listed[0]["policy_sha256"] == "0" * 64


def test_verified_generation_rejects_prepared_tokenizer_mismatch(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    policy_sha, _tokenizer = _connector_bindings()
    checkpoint, bundle, replay = _prepared_evidence(
        store,
        generation_id="tokenizer-mismatch",
        policy_sha256=policy_sha,
        tokenizer_identity="different-tokenizer-v1",
    )
    verification_receipt = _mint_independent_verification_receipt(
        generation_id="tokenizer-mismatch",
        checkpoint=checkpoint,
        bundle=bundle,
        replay_report=replay,
    )

    with pytest.raises(StoreIntegrityError, match="policy, and tokenizer"):
        store.record_verified(
            generation_id="tokenizer-mismatch",
            operation_id="tokenizer-mismatch:verify",
            checkpoint=checkpoint,
            bundle=bundle,
            verification_receipt=verification_receipt,
        )

    listed = store.list_generations("session-1")
    assert listed[0]["state"] == "prepared"
    assert listed[0]["tokenizer_identity"] == "different-tokenizer-v1"


def test_direct_passed_mapping_cannot_verify_or_activate_generation(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    policy_sha, tokenizer_identity = _connector_bindings()
    checkpoint, bundle, replay = _prepared_evidence(
        store,
        generation_id="forged-verification",
        policy_sha256=policy_sha,
        tokenizer_identity=tokenizer_identity,
    )

    with pytest.raises(StoreIntegrityError, match="independently minted"):
        store.record_verified(
            generation_id="forged-verification",
            operation_id="forged-verification:verify",
            checkpoint=checkpoint,
            bundle=bundle,
            verification_receipt={**replay, "passed": True},
        )

    generations = store.list_generations("session-1")
    assert generations[0]["state"] == "prepared"
    assert store.read_active("session-1") is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"session_id": "other-session"},
        {"generation_id": "other-generation"},
        {"active_epoch": 999},
        {"source_head_sha256": "c" * 64},
        {"semantic_result_digest": "d" * 64},
    ],
)
def test_store_request_ledger_rejects_relational_binding_mismatch(
    tmp_path,
    overrides: dict[str, object],
) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    generation_id, _bundle, _replay = compile_generation(store, 1)
    ledger, tokenizer = _request_ledger(store, **overrides)  # type: ignore[arg-type]

    with pytest.raises(
        StoreIntegrityError,
        match="bindings do not match active store state",
    ):
        store.store_request_ledger(
            session_id="session-1",
            generation_id=generation_id,
            request_id="request-1",
            ledger=ledger,
            tokenizer=tokenizer,
        )

    with pytest.raises(KeyError, match="unknown request ledger"):
        store.load_request_ledger(
            session_id="session-1",
            request_id="request-1",
            tokenizer=tokenizer,
        )


def test_load_request_ledger_revalidates_and_replays_exact_bytes(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    generation_id, _bundle, _replay = compile_generation(store, 1)
    ledger, tokenizer = _request_ledger(store)
    store.store_request_ledger(
        session_id="session-1",
        generation_id=generation_id,
        request_id="request-1",
        ledger=ledger,
        tokenizer=tokenizer,
    )

    loaded = store.load_request_ledger(
        session_id="session-1",
        request_id="request-1",
        tokenizer=tokenizer,
    )
    assert loaded.to_dict() == ledger.to_dict()
    report = replay_final_request(loaded, tokenizer=tokenizer)
    assert report.passed is True
    assert report.final_request_sha256 == ledger["final_request_sha256"]
    with pytest.raises(KeyError, match="unknown request ledger"):
        store.load_request_ledger(
            session_id="session-1",
            request_id="missing",
            tokenizer=tokenizer,
        )


def test_load_request_ledger_rejects_wrong_exact_tokenizer(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    generation_id, _bundle, _replay = compile_generation(store, 1)
    ledger, tokenizer = _request_ledger(store)
    store.store_request_ledger(
        session_id="session-1",
        generation_id=generation_id,
        request_id="request-1",
        ledger=ledger,
        tokenizer=tokenizer,
    )

    class WrongTokenizer(CanonicalUtf8ByteTokenizer):
        identity = "wrong-tokenizer"

    with pytest.raises(RequestLedgerError):
        store.load_request_ledger(
            session_id="session-1",
            request_id="request-1",
            tokenizer=WrongTokenizer(),
        )


def test_load_request_ledger_detects_storage_tampering(tmp_path) -> None:
    path = store_path(tmp_path)
    store = SQLiteGenerationStore(path)
    append_message(store, 0)
    generation_id, _bundle, _replay = compile_generation(store, 1)
    ledger, tokenizer = _request_ledger(store)
    store.store_request_ledger(
        session_id="session-1",
        generation_id=generation_id,
        request_id="request-1",
        ledger=ledger,
        tokenizer=tokenizer,
    )

    direct = sqlite3.connect(path)
    try:
        direct.execute("DROP TRIGGER request_ledgers_no_update")
        direct.execute(
            """
            UPDATE request_ledgers
            SET ledger_sha256 = ?
            WHERE session_id = ? AND request_id = ?
            """,
            ("f" * 64, "session-1", "request-1"),
        )
        direct.commit()
    finally:
        direct.close()

    with pytest.raises(StoreIntegrityError, match="binding or digest mismatch"):
        store.load_request_ledger(
            session_id="session-1",
            request_id="request-1",
            tokenizer=tokenizer,
        )


def test_load_request_ledger_detects_relational_epoch_tampering(tmp_path) -> None:
    path = store_path(tmp_path)
    store = SQLiteGenerationStore(path)
    append_message(store, 0)
    generation_id, _bundle, _replay = compile_generation(store, 1)
    ledger, tokenizer = _request_ledger(store)
    store.store_request_ledger(
        session_id="session-1",
        generation_id=generation_id,
        request_id="request-1",
        ledger=ledger,
        tokenizer=tokenizer,
    )

    direct = sqlite3.connect(path)
    try:
        direct.execute("DROP TRIGGER request_ledgers_no_update")
        direct.execute(
            """
            UPDATE request_ledgers
            SET active_epoch = active_epoch + 1
            WHERE session_id = ? AND request_id = ?
            """,
            ("session-1", "request-1"),
        )
        direct.commit()
    finally:
        direct.close()

    with pytest.raises(StoreIntegrityError, match="binding or digest mismatch"):
        store.load_request_ledger(
            session_id="session-1",
            request_id="request-1",
            tokenizer=tokenizer,
        )
