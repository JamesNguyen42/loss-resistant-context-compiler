from __future__ import annotations

import pytest

from ctxc_openhands.faults import (
    DeterministicFaultController,
    FaultDirective,
    InjectedFault,
    iter_fault_cycles,
)


def test_controller_fires_only_on_configured_occurrence_then_disarms() -> None:
    controller = DeterministicFaultController()
    controller.arm(
        FaultDirective("write.before_commit", occurrence=2),
        schedule_index=17,
    )

    controller("write.before_begin")
    controller("write.before_commit")
    with pytest.raises(InjectedFault) as raised:
        controller("write.before_commit")

    assert raised.value.point == "write.before_commit"
    assert raised.value.schedule_index == 17
    assert raised.value.occurrence == 2
    assert controller.armed is False
    assert controller.visited == (
        "write.before_begin",
        "write.before_commit",
        "write.before_commit",
    )
    controller("write.before_commit")


def test_controller_refuses_double_arm_and_invalid_directives() -> None:
    controller = DeterministicFaultController()
    controller.arm(FaultDirective("point"), schedule_index=0)
    with pytest.raises(RuntimeError, match="already armed"):
        controller.arm(FaultDirective("other"), schedule_index=1)
    controller.disarm()
    assert controller.armed is False

    with pytest.raises(ValueError, match="non-empty"):
        FaultDirective("")
    with pytest.raises(ValueError, match="1,000,000"):
        FaultDirective("point", occurrence=0)


def test_fault_cycles_are_reproducible_and_cover_each_point_per_cycle() -> None:
    points = ("one", "two", "three")
    first = iter_fault_cycles(points, seed=42)
    second = iter_fault_cycles(points, seed=42)
    first_six = tuple(next(first) for _ in range(6))
    second_six = tuple(next(second) for _ in range(6))

    assert first_six == second_six
    assert {directive.point for directive in first_six[:3]} == set(points)
    assert {directive.point for directive in first_six[3:]} == set(points)
