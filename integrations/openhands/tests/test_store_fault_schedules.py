from __future__ import annotations

import hashlib

import pytest
from store_helpers import (
    append_message,
    compile_generation,
    message_event,
    source_record,
    store_path,
)

from ctxc_openhands.faults import (
    APPEND_FAULT_POINTS,
    APPEND_POSTCOMMIT_POINTS,
    APPEND_PRECOMMIT_POINTS,
    GENERATION_POSTCOMMIT_POINTS,
    DeterministicFaultController,
    FaultDirective,
    InjectedFault,
    deterministic_crash_schedules,
)
from ctxc_openhands.storage import (
    GenerationStateError,
    SQLiteGenerationStore,
    StaleGenerationError,
)

_GENERIC_WRITE_POINTS = (
    "write.before_begin",
    "write.after_begin",
    "write.before_commit",
    "write.after_commit",
)


def _append(
    store: SQLiteGenerationStore,
    *,
    sequence: int,
    request_id: str,
) -> None:
    event = message_event(sequence)
    store.append_source_event(
        session_id="schedule-session",
        host_event=event,
        source_record=source_record(
            event, sequence, session_id="schedule-session"
        ),
        request_id=request_id,
    )


def test_schedule_generation_is_reproducible_bounded_and_self_hashed() -> None:
    first = deterministic_crash_schedules(count=1024, seed=0xC7C0)
    second = deterministic_crash_schedules(count=1024, seed=0xC7C0)

    assert first == second
    assert len(first) == 1024
    assert len({schedule.schedule_sha256 for schedule in first}) == 1024
    assert set(APPEND_FAULT_POINTS).issubset(
        {schedule.fault.point for schedule in first[: len(APPEND_FAULT_POINTS)]}
    )
    with pytest.raises(ValueError, match="between 1"):
        deterministic_crash_schedules(count=0, seed=1)
    with pytest.raises(ValueError, match="100000"):
        deterministic_crash_schedules(count=100_001, seed=1)


def test_1024_deterministic_crash_retry_and_contender_schedules(tmp_path) -> None:
    """Exercise 1,024 real WAL transactions under repeatable crash ordering."""

    controller = DeterministicFaultController()
    store = SQLiteGenerationStore(
        store_path(tmp_path),
        fault_hook=controller,
    )
    schedules = deterministic_crash_schedules(
        count=1024,
        seed=0x5A17,
        new_event_interval=8,
        fault_points=_GENERIC_WRITE_POINTS,
    )
    expected_count = 0

    for schedule in schedules:
        if schedule.introduces_event:
            sequence = expected_count
            expected_count += 1
        else:
            sequence = schedule.index % expected_count
        primary_id = f"primary-{sequence}"
        contender_id = (
            primary_id if schedule.reuse_request_id else f"contender-{schedule.index}"
        )

        controller.arm(schedule.fault, schedule_index=schedule.index)
        with pytest.raises(InjectedFault) as raised:
            _append(
                store,
                sequence=sequence,
                request_id=primary_id,
            )
        assert raised.value.point == schedule.fault.point
        assert raised.value.schedule_index == schedule.index
        assert controller.armed is False
        assert schedule.fault.point in controller.visited

        actor_requests = {
            "primary": primary_id,
            "recovery": primary_id,
            "contender": contender_id,
        }
        for actor in schedule.actor_order:
            _append(
                store,
                sequence=sequence,
                request_id=actor_requests[actor],
            )

        if schedule.index % 128 == 127:
            snapshot = store.snapshot("schedule-session")
            assert snapshot.source_count == expected_count
            assert [record.sequence for record in snapshot.records] == list(
                range(expected_count)
            )

    snapshot = store.snapshot("schedule-session")
    assert snapshot.source_count == 128
    assert [record.id for record in snapshot.records] == [
        f"event-{sequence}" for sequence in range(128)
    ]
    assert len({record.record_sha256 for record in snapshot.records}) == 128
    assert store.integrity_report()["passed"] is True


@pytest.mark.parametrize("point", APPEND_PRECOMMIT_POINTS)
def test_each_append_precommit_fault_rolls_back_without_loss(
    tmp_path,
    point: str,
) -> None:
    controller = DeterministicFaultController()
    store = SQLiteGenerationStore(
        store_path(tmp_path),
        fault_hook=controller,
    )
    controller.arm(FaultDirective(point), schedule_index=0)

    with pytest.raises(InjectedFault):
        _append(store, sequence=0, request_id="append-0")

    reopened = SQLiteGenerationStore(store_path(tmp_path))
    with pytest.raises(KeyError):
        reopened.snapshot("schedule-session")
    _append(reopened, sequence=0, request_id="append-0")
    assert reopened.snapshot("schedule-session").source_count == 1
    assert reopened.integrity_report()["passed"] is True


@pytest.mark.parametrize("point", APPEND_POSTCOMMIT_POINTS)
def test_each_append_postcommit_fault_is_exactly_once_on_retry(
    tmp_path,
    point: str,
) -> None:
    controller = DeterministicFaultController()
    store = SQLiteGenerationStore(
        store_path(tmp_path),
        fault_hook=controller,
    )
    controller.arm(FaultDirective(point), schedule_index=0)

    with pytest.raises(InjectedFault):
        _append(store, sequence=0, request_id="append-0")

    reopened = SQLiteGenerationStore(store_path(tmp_path))
    _append(reopened, sequence=0, request_id="append-0")
    snapshot = reopened.snapshot("schedule-session")
    assert snapshot.source_count == 1
    assert [record.id for record in snapshot.records] == ["event-0"]


@pytest.mark.parametrize(
    "point",
    (
        "activate.after_old_superseded",
        "activate.after_new_active",
        "activate.after_pointer_cas",
        "write.before_commit",
        *GENERATION_POSTCOMMIT_POINTS,
    ),
)
def test_activation_fault_exposes_exactly_old_or_new_verified_generation(
    tmp_path,
    point: str,
) -> None:
    path = store_path(tmp_path)
    base = SQLiteGenerationStore(path)
    append_message(base, 0)
    old_id, _bundle, _replay = compile_generation(base, 1)
    append_message(base, 1)
    new_id, _bundle, _replay = compile_generation(base, 2, activate=False)
    source_head = base.snapshot("session-1").source_head_sha256

    controller = DeterministicFaultController()
    crashing = SQLiteGenerationStore(path, fault_hook=controller)
    controller.arm(FaultDirective(point), schedule_index=0)
    with pytest.raises(InjectedFault):
        crashing.activate_generation(
            generation_id=new_id,
            operation_id=f"crash-{point.replace('.', '-')}",
        )

    reopened = SQLiteGenerationStore(path)
    active = reopened.read_active("session-1")
    assert active is not None
    assert active.generation_id in {old_id, new_id}
    assert active.source_head_sha256 == source_head
    assert active.bundle.bundle_sha256
    assert active.semantic_result_digest
    assert reopened.integrity_report()["passed"] is True

    if point in GENERATION_POSTCOMMIT_POINTS:
        assert active.generation_id == new_id
        reopened.rollback_generation(
            session_id="session-1",
            target_generation_id=old_id,
            operation_id="recover-explicit-rollback",
        )
    else:
        assert active.generation_id == old_id
        reopened.roll_back_provisional(
            generation_id=new_id,
            operation_id="recover-provisional",
        )
    recovered = reopened.read_active("session-1")
    assert recovered is not None
    assert recovered.generation_id == old_id
    assert [record.id for record in recovered.tail] == ["event-1"]


def test_stale_source_head_cas_preserves_sources_and_allows_recovery(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    old_id, _bundle, _replay = compile_generation(store, 1)
    append_message(store, 1)
    candidate_id, _bundle, _replay = compile_generation(
        store,
        2,
        activate=False,
    )
    append_message(store, 2)

    with pytest.raises(StaleGenerationError, match="compare-and-swap"):
        store.activate_generation(
            generation_id=candidate_id,
            operation_id="stale-source-head",
        )

    active = store.read_active("session-1")
    assert active is not None
    assert active.generation_id == old_id
    assert [record.id for record in active.tail] == ["event-1", "event-2"]
    store.roll_back_provisional(
        generation_id=candidate_id,
        operation_id="recover-stale-candidate",
    )
    states = {
        row["generation_id"]: row["state"]
        for row in store.list_generations("session-1")
    }
    assert states[candidate_id] == "rolled_back"
    assert store.snapshot("session-1").source_count == 3
    assert store.integrity_report()["passed"] is True


@pytest.mark.parametrize(
    "point",
    (
        "prepare.before_generation_insert",
        "prepare.before_commit",
    ),
)
def test_prepare_crash_leaves_no_provisional_generation(
    tmp_path,
    point: str,
) -> None:
    path = store_path(tmp_path)
    base = SQLiteGenerationStore(path)
    append_message(base, 0)
    controller = DeterministicFaultController()
    crashing = SQLiteGenerationStore(path, fault_hook=controller)
    controller.arm(FaultDirective(point), schedule_index=0)

    with pytest.raises(InjectedFault):
        crashing.prepare_generation(
            session_id="session-1",
            generation_id="crashed-prepare",
            operation_id="prepare-crash",
            policy_sha256=hashlib.sha256(b"policy").hexdigest(),
            tokenizer_identity="character-estimate-v1",
        )

    reopened = SQLiteGenerationStore(path)
    assert reopened.list_generations("session-1") == ()
    assert reopened.snapshot("session-1").source_count == 1
    assert reopened.integrity_report()["passed"] is True


def test_provisional_recovery_rejects_active_generation(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    generation_id, _bundle, _replay = compile_generation(store, 1)

    with pytest.raises(GenerationStateError, match="provisional"):
        store.roll_back_provisional(
            generation_id=generation_id,
            operation_id="invalid-active-recovery",
        )
