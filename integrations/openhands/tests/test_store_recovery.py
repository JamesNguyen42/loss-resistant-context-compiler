from __future__ import annotations

import pytest
from store_helpers import append_message, compile_generation, store_path

from ctxc_openhands.storage import SQLiteGenerationStore


class InjectedCrash(BaseException):
    pass


class OneShotFault:
    def __init__(self, point: str) -> None:
        self.point = point
        self.armed = False
        self.triggered = False

    def __call__(self, point: str) -> None:
        if self.armed and not self.triggered and point == self.point:
            self.triggered = True
            raise InjectedCrash(point)


@pytest.mark.parametrize(
    "point",
    [
        "append.before_event_insert",
        "append.after_event_insert",
        "append.after_head_cas",
        "append.before_commit",
    ],
)
def test_append_crash_before_commit_exposes_no_partial_event(
    tmp_path,
    point: str,
) -> None:
    fault = OneShotFault(point)
    path = store_path(tmp_path)
    store = SQLiteGenerationStore(path, fault_hook=fault)
    fault.armed = True

    with pytest.raises(InjectedCrash):
        append_message(store, 0)

    reopened = SQLiteGenerationStore(path)
    with pytest.raises(KeyError):
        reopened.snapshot("session-1")
    assert reopened.integrity_report()["passed"] is True


def test_append_crash_after_commit_is_safe_to_retry(tmp_path) -> None:
    fault = OneShotFault("write.after_commit")
    path = store_path(tmp_path)
    store = SQLiteGenerationStore(path, fault_hook=fault)
    fault.armed = True

    with pytest.raises(InjectedCrash):
        append_message(store, 0)

    reopened = SQLiteGenerationStore(path)
    result = append_message(reopened, 0)
    assert result.record_sha256
    assert reopened.snapshot("session-1").source_count == 1


@pytest.mark.parametrize(
    "point",
    [
        "activate.after_old_superseded",
        "activate.after_new_active",
        "activate.after_pointer_cas",
    ],
)
def test_activation_crash_exposes_exactly_old_generation(
    tmp_path,
    point: str,
) -> None:
    path = store_path(tmp_path)
    base = SQLiteGenerationStore(path)
    append_message(base, 0)
    first_id, _bundle, _replay = compile_generation(base, 1)
    append_message(base, 1)
    second_id, _bundle, _replay = compile_generation(base, 2, activate=False)

    fault = OneShotFault(point)
    crashing = SQLiteGenerationStore(path, fault_hook=fault)
    fault.armed = True
    with pytest.raises(InjectedCrash):
        crashing.activate_generation(
            generation_id=second_id,
            operation_id=f"crash-{point.rsplit('.', 1)[-1]}",
        )

    reopened = SQLiteGenerationStore(path)
    active = reopened.read_active("session-1")
    assert active is not None
    assert active.generation_id == first_id
    assert reopened.integrity_report()["passed"] is True
