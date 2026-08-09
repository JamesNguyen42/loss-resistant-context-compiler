from __future__ import annotations

import hashlib
from dataclasses import fields

import pytest
from context_compiler import IncrementalCompiler, LocalAIConnector
from store_helpers import append_message, canonical_json, store_path

from ctxc_openhands.faults import (
    DeterministicFaultController,
    FaultDirective,
    InjectedFault,
)
from ctxc_openhands.storage import (
    SQLiteGenerationStore,
    _mint_independent_verification_receipt,
)


def _policy_and_tokenizer() -> tuple[str, str, LocalAIConnector]:
    connector = LocalAIConnector()
    policy = {
        field.name: getattr(connector.policy, field.name)
        for field in fields(connector.policy)
    }
    policy_sha = hashlib.sha256(canonical_json(policy).encode("utf-8")).hexdigest()
    tokenizer_identity = (
        connector.token_counter_id
        if connector.token_counter is not None
        else "character-estimate-v1"
    )
    return policy_sha, tokenizer_identity, connector


def _prepare_evidence(
    store: SQLiteGenerationStore,
    *,
    generation_id: str,
):
    policy_sha, tokenizer_identity, connector = _policy_and_tokenizer()
    prepared = store.prepare_generation(
        session_id="session-1",
        generation_id=generation_id,
        operation_id=f"{generation_id}:prepare",
        policy_sha256=policy_sha,
        tokenizer_identity=tokenizer_identity,
    )
    checkpoint = IncrementalCompiler(
        session_id="session-1",
        sources=prepared.snapshot.records,
    ).checkpoint()
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
    verification_receipt = _mint_independent_verification_receipt(
        generation_id=generation_id,
        checkpoint=checkpoint,
        bundle=bundle,
        replay_report=replay,
    )
    return checkpoint, bundle, verification_receipt


def test_prepare_postcommit_crash_retains_recoverable_prepared_state(tmp_path) -> None:
    path = store_path(tmp_path)
    base = SQLiteGenerationStore(path)
    append_message(base, 0)
    policy_sha, tokenizer_identity, _connector = _policy_and_tokenizer()
    controller = DeterministicFaultController()
    crashing = SQLiteGenerationStore(path, fault_hook=controller)
    controller.arm(FaultDirective("write.after_commit"), schedule_index=0)

    with pytest.raises(InjectedFault):
        crashing.prepare_generation(
            session_id="session-1",
            generation_id="prepare-after-commit",
            operation_id="prepare-after-commit:prepare",
            policy_sha256=policy_sha,
            tokenizer_identity=tokenizer_identity,
        )

    reopened = SQLiteGenerationStore(path)
    assert reopened.list_generations("session-1")[0]["state"] == "prepared"
    reopened.roll_back_provisional(
        generation_id="prepare-after-commit",
        operation_id="recover-prepared",
    )
    assert reopened.list_generations("session-1")[0]["state"] == "rolled_back"
    assert reopened.snapshot("session-1").source_count == 1


@pytest.mark.parametrize(
    ("point", "expected_state"),
    (
        ("verify.before_commit", "prepared"),
        ("write.before_commit", "prepared"),
        ("write.after_commit", "verified"),
    ),
)
def test_verify_crash_is_recoverable_without_visibility(
    tmp_path,
    point: str,
    expected_state: str,
) -> None:
    path = store_path(tmp_path)
    base = SQLiteGenerationStore(path)
    append_message(base, 0)
    checkpoint, bundle, verification_receipt = _prepare_evidence(
        base,
        generation_id="verify-crash",
    )
    controller = DeterministicFaultController()
    crashing = SQLiteGenerationStore(path, fault_hook=controller)
    controller.arm(FaultDirective(point), schedule_index=0)

    with pytest.raises(InjectedFault):
        crashing.record_verified(
            generation_id="verify-crash",
            operation_id=f"verify-crash:{point}",
            checkpoint=checkpoint,
            bundle=bundle,
            verification_receipt=verification_receipt,
        )

    reopened = SQLiteGenerationStore(path)
    assert reopened.read_active("session-1") is None
    assert reopened.list_generations("session-1")[0]["state"] == expected_state
    reopened.roll_back_provisional(
        generation_id="verify-crash",
        operation_id="recover-verify",
    )
    assert reopened.list_generations("session-1")[0]["state"] == "rolled_back"
    assert reopened.integrity_report()["passed"] is True


@pytest.mark.parametrize(
    ("point", "expected_state"),
    (
        ("commit_state.before_commit", "verified"),
        ("write.before_commit", "verified"),
        ("write.after_commit", "committed"),
    ),
)
def test_commit_crash_is_recoverable_without_visibility(
    tmp_path,
    point: str,
    expected_state: str,
) -> None:
    path = store_path(tmp_path)
    base = SQLiteGenerationStore(path)
    append_message(base, 0)
    checkpoint, bundle, verification_receipt = _prepare_evidence(
        base,
        generation_id="commit-crash",
    )
    base.record_verified(
        generation_id="commit-crash",
        operation_id="commit-crash:verify",
        checkpoint=checkpoint,
        bundle=bundle,
        verification_receipt=verification_receipt,
    )
    controller = DeterministicFaultController()
    crashing = SQLiteGenerationStore(path, fault_hook=controller)
    controller.arm(FaultDirective(point), schedule_index=0)

    with pytest.raises(InjectedFault):
        crashing.commit_generation(
            generation_id="commit-crash",
            operation_id=f"commit-crash:{point}",
        )

    reopened = SQLiteGenerationStore(path)
    assert reopened.read_active("session-1") is None
    assert reopened.list_generations("session-1")[0]["state"] == expected_state
    reopened.roll_back_provisional(
        generation_id="commit-crash",
        operation_id="recover-commit",
    )
    assert reopened.list_generations("session-1")[0]["state"] == "rolled_back"
    assert reopened.snapshot("session-1").source_count == 1
    assert reopened.integrity_report()["passed"] is True
