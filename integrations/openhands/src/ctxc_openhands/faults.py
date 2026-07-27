"""Deterministic crash and concurrency schedules for transaction validation.

The production store accepts a fault hook but never enables one itself.  This
module supplies a bounded, one-shot controller and reproducible schedule
descriptors for tests, offline diagnostics, and release evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Final

APPEND_PRECOMMIT_POINTS: Final[tuple[str, ...]] = (
    "write.before_begin",
    "write.after_begin",
    "append.after_begin",
    "append.before_event_insert",
    "append.after_event_insert",
    "append.after_head_cas",
    "append.before_commit",
    "write.before_commit",
)
APPEND_POSTCOMMIT_POINTS: Final[tuple[str, ...]] = ("write.after_commit",)
APPEND_FAULT_POINTS: Final[tuple[str, ...]] = (
    *APPEND_PRECOMMIT_POINTS,
    *APPEND_POSTCOMMIT_POINTS,
)
GENERATION_PRECOMMIT_POINTS: Final[tuple[str, ...]] = (
    "prepare.before_generation_insert",
    "prepare.before_commit",
    "verify.before_commit",
    "commit_state.before_commit",
    "activate.after_old_superseded",
    "activate.after_new_active",
    "activate.after_pointer_cas",
    "write.before_commit",
)
GENERATION_POSTCOMMIT_POINTS: Final[tuple[str, ...]] = ("write.after_commit",)

GENERATION_PREPARE_FAULT_POINTS: Final[tuple[str, ...]] = (
    "write.before_begin",
    "write.after_begin",
    "prepare.before_generation_insert",
    "prepare.before_commit",
    "write.before_commit",
    "write.after_commit",
)
GENERATION_VERIFY_FAULT_POINTS: Final[tuple[str, ...]] = (
    "write.before_begin",
    "write.after_begin",
    "verify.before_commit",
    "write.before_commit",
    "write.after_commit",
)
GENERATION_COMMIT_FAULT_POINTS: Final[tuple[str, ...]] = (
    "write.before_begin",
    "write.after_begin",
    "commit_state.before_commit",
    "write.before_commit",
    "write.after_commit",
)
GENERATION_ACTIVATE_FAULT_POINTS: Final[tuple[str, ...]] = (
    "write.before_begin",
    "write.after_begin",
    "activate.after_old_superseded",
    "activate.after_new_active",
    "activate.after_pointer_cas",
    "write.before_commit",
    "write.after_commit",
)
TRANSACTION_FAULT_DOMAINS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("append", APPEND_FAULT_POINTS),
    ("generation.prepare", GENERATION_PREPARE_FAULT_POINTS),
    ("generation.verify", GENERATION_VERIFY_FAULT_POINTS),
    ("generation.commit", GENERATION_COMMIT_FAULT_POINTS),
    ("generation.activate", GENERATION_ACTIVATE_FAULT_POINTS),
)

_ACTOR_ORDERS: Final[tuple[tuple[str, ...], ...]] = (
    ("primary", "recovery", "contender"),
    ("primary", "contender", "recovery"),
    ("contender", "primary", "recovery"),
)
_CAMPAIGN_WORKERS: Final[tuple[str, ...]] = ("recovery", "retry", "contender")
_MAX_SCHEDULES: Final = 100_000
ABRUPT_EXIT_CODE: Final = 86


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class InjectedFault(BaseException):
    """A deliberate crash boundary that ordinary exception handlers cannot hide."""

    def __init__(
        self,
        *,
        point: str,
        schedule_index: int,
        occurrence: int,
    ) -> None:
        super().__init__(
            f"injected fault at {point} "
            f"(schedule={schedule_index}, occurrence={occurrence})"
        )
        self.point = point
        self.schedule_index = schedule_index
        self.occurrence = occurrence


class AbruptExitFault:
    """Explicitly armed fault hook that terminates the current process.

    This hook is only for subprocess durability diagnostics. It starts
    disarmed so store construction cannot terminate a process at a generic
    ``write.*`` initialization point.
    """

    def __init__(
        self,
        point: str,
        *,
        exit_code: int = ABRUPT_EXIT_CODE,
    ) -> None:
        if not isinstance(point, str) or not point:
            raise ValueError("fault point must be a non-empty string")
        if (
            isinstance(exit_code, bool)
            or not isinstance(exit_code, int)
            or exit_code < 1
            or exit_code > 255
        ):
            raise ValueError("exit_code must be an integer from 1 to 255")
        self.point = point
        self.exit_code = exit_code
        self._armed = False

    def arm(self) -> None:
        """Arm the one-shot process exit."""

        if self._armed:
            raise RuntimeError("abrupt exit fault is already armed")
        self._armed = True

    def __call__(self, point: str) -> None:
        if not isinstance(point, str) or not point:
            raise ValueError("visited fault point must be a non-empty string")
        if self._armed and point == self.point:
            self._armed = False
            os._exit(self.exit_code)


@dataclass(frozen=True, slots=True)
class FaultDirective:
    """One fault point and the matching occurrence on which it fires."""

    point: str
    occurrence: int = 1

    def __post_init__(self) -> None:
        if not self.point or not isinstance(self.point, str):
            raise ValueError("fault point must be a non-empty string")
        if (
            isinstance(self.occurrence, bool)
            or not isinstance(self.occurrence, int)
            or self.occurrence < 1
            or self.occurrence > 1_000_000
        ):
            raise ValueError("fault occurrence must be an integer from 1 to 1,000,000")

def _schedule_digest(
    *,
    index: int,
    seed: int,
    fault: FaultDirective,
    actor_order: tuple[str, ...],
    introduces_event: bool,
    reuse_request_id: bool,
) -> str:
    payload = {
        "actor_order": list(actor_order),
        "fault": {
            "occurrence": fault.occurrence,
            "point": fault.point,
        },
        "index": index,
        "introduces_event": introduces_event,
        "reuse_request_id": reuse_request_id,
        "seed": seed,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()



@dataclass(frozen=True, slots=True)
class CrashConcurrencySchedule:
    """One reproducible crash/retry/contender ordering."""

    index: int
    seed: int
    fault: FaultDirective
    actor_order: tuple[str, ...]
    introduces_event: bool
    reuse_request_id: bool
    schedule_sha256: str

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int):
            raise TypeError("schedule index must be an integer")
        if self.index < 0:
            raise ValueError("schedule index must be non-negative")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError("schedule seed must be an integer")
        if self.actor_order not in _ACTOR_ORDERS:
            raise ValueError("actor order is not supported")
        expected = self._digest()
        if self.schedule_sha256 != expected:
            raise ValueError("schedule digest mismatch")

    def _digest(self) -> str:
        return _schedule_digest(
            index=self.index,
            seed=self.seed,
            fault=self.fault,
            actor_order=self.actor_order,
            introduces_event=self.introduces_event,
            reuse_request_id=self.reuse_request_id,
        )


def _transaction_schedule_digest(
    *,
    index: int,
    seed: int,
    domain: str,
    fault: FaultDirective,
    worker_order: tuple[str, ...],
    worker_yields: tuple[int, ...],
) -> str:
    payload = {
        "domain": domain,
        "fault": {
            "occurrence": fault.occurrence,
            "point": fault.point,
        },
        "index": index,
        "seed": seed,
        "worker_order": list(worker_order),
        "worker_yields": list(worker_yields),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TransactionCrashSchedule:
    """One transaction-domain fault plus a real three-worker race plan."""

    index: int
    seed: int
    domain: str
    fault: FaultDirective
    worker_order: tuple[str, ...]
    worker_yields: tuple[int, ...]
    schedule_sha256: str

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int):
            raise TypeError("schedule index must be an integer")
        if self.index < 0:
            raise ValueError("schedule index must be non-negative")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError("schedule seed must be an integer")
        domains = dict(TRANSACTION_FAULT_DOMAINS)
        if self.domain not in domains:
            raise ValueError("transaction fault domain is not supported")
        if self.fault.point not in domains[self.domain]:
            raise ValueError("fault point is not valid for the transaction domain")
        if (
            len(self.worker_order) != len(_CAMPAIGN_WORKERS)
            or set(self.worker_order) != set(_CAMPAIGN_WORKERS)
        ):
            raise ValueError(
                "worker order must contain each campaign worker exactly once"
            )
        if (
            len(self.worker_yields) != len(_CAMPAIGN_WORKERS)
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                or value > 2
                for value in self.worker_yields
            )
        ):
            raise ValueError("worker yields must contain three integers from 0 to 2")
        if self.schedule_sha256 != self._digest():
            raise ValueError("transaction schedule digest mismatch")

    def _digest(self) -> str:
        return _transaction_schedule_digest(
            index=self.index,
            seed=self.seed,
            domain=self.domain,
            fault=self.fault,
            worker_order=self.worker_order,
            worker_yields=self.worker_yields,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical report representation."""

        return {
            "index": self.index,
            "seed": self.seed,
            "domain": self.domain,
            "fault": {
                "point": self.fault.point,
                "occurrence": self.fault.occurrence,
            },
            "worker_order": list(self.worker_order),
            "worker_yields": list(self.worker_yields),
            "schedule_sha256": self.schedule_sha256,
        }


class DeterministicFaultController:
    """Thread-safe, explicitly armed, one-shot implementation of ``FaultHook``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._directive: FaultDirective | None = None
        self._schedule_index = -1
        self._matching_occurrences = 0
        self._visited: list[str] = []

    def arm(self, directive: FaultDirective, *, schedule_index: int) -> None:
        if not isinstance(directive, FaultDirective):
            raise TypeError("directive must be a FaultDirective")
        if (
            isinstance(schedule_index, bool)
            or not isinstance(schedule_index, int)
            or schedule_index < 0
        ):
            raise ValueError("schedule_index must be a non-negative integer")
        with self._lock:
            if self._directive is not None:
                raise RuntimeError("fault controller is already armed")
            self._directive = directive
            self._schedule_index = schedule_index
            self._matching_occurrences = 0
            self._visited = []

    def disarm(self) -> None:
        with self._lock:
            self._directive = None
            self._schedule_index = -1
            self._matching_occurrences = 0

    @property
    def armed(self) -> bool:
        with self._lock:
            return self._directive is not None

    @property
    def visited(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._visited)

    def __call__(self, point: str) -> None:
        if not isinstance(point, str) or not point:
            raise ValueError("visited fault point must be a non-empty string")
        with self._lock:
            directive = self._directive
            if directive is None:
                return
            self._visited.append(point)
            if point != directive.point:
                return
            self._matching_occurrences += 1
            if self._matching_occurrences != directive.occurrence:
                return
            schedule_index = self._schedule_index
            occurrence = self._matching_occurrences
            self._directive = None
            self._schedule_index = -1
            raise InjectedFault(
                point=point,
                schedule_index=schedule_index,
                occurrence=occurrence,
            )


def deterministic_crash_schedules(
    *,
    count: int,
    seed: int,
    new_event_interval: int = 8,
    fault_points: Sequence[str] = APPEND_FAULT_POINTS,
) -> tuple[CrashConcurrencySchedule, ...]:
    """Return bounded pseudo-random schedules with stable digests.

    Every fault point appears before any point repeats when ``count`` permits.
    ``introduces_event`` is periodic so a large schedule run can use a bounded
    number of durable writes while still exercising every schedule against the
    real store.
    """

    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError("count must be an integer")
    if count < 1 or count > _MAX_SCHEDULES:
        raise ValueError(f"count must be between 1 and {_MAX_SCHEDULES}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer")
    if (
        isinstance(new_event_interval, bool)
        or not isinstance(new_event_interval, int)
        or new_event_interval < 1
        or new_event_interval > count
    ):
        raise ValueError("new_event_interval must be between 1 and count")
    normalized = tuple(fault_points)
    if not normalized or any(not isinstance(point, str) or not point for point in normalized):
        raise ValueError("fault_points must contain non-empty strings")
    if len(set(normalized)) != len(normalized):
        raise ValueError("fault_points must not contain duplicates")

    rng = random.Random(seed)
    shuffled_points: list[str] = []
    while len(shuffled_points) < count:
        cycle = list(normalized)
        rng.shuffle(cycle)
        shuffled_points.extend(cycle)

    schedules: list[CrashConcurrencySchedule] = []
    for index in range(count):
        point = shuffled_points[index]
        actor_order = rng.choice(_ACTOR_ORDERS)
        introduces_event = index % new_event_interval == 0
        reuse_request_id = bool(rng.getrandbits(1))
        fault = FaultDirective(point)
        digest = _schedule_digest(
            index=index,
            seed=seed,
            fault=fault,
            actor_order=actor_order,
            introduces_event=introduces_event,
            reuse_request_id=reuse_request_id,
        )
        schedules.append(
            CrashConcurrencySchedule(
                index=index,
                seed=seed,
                fault=fault,
                actor_order=actor_order,
                introduces_event=introduces_event,
                reuse_request_id=reuse_request_id,
                schedule_sha256=digest,
            )
        )
    return tuple(schedules)


def deterministic_transaction_schedules(
    *,
    count: int,
    seed: int,
) -> tuple[TransactionCrashSchedule, ...]:
    """Return seeded schedules spanning append and every generation transition.

    A complete cycle covers each exact ``(domain, fault point)`` pair once.
    Every worker plan contains three simultaneous contenders; yield counts
    vary their post-barrier scheduling without making a winner authoritative.
    """

    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError("count must be an integer")
    if count < 1 or count > _MAX_SCHEDULES:
        raise ValueError(f"count must be between 1 and {_MAX_SCHEDULES}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer")

    rng = random.Random(seed)
    domain_points = [
        (domain, point)
        for domain, points in TRANSACTION_FAULT_DOMAINS
        for point in points
    ]
    selected: list[tuple[str, str]] = []
    while len(selected) < count:
        cycle = list(domain_points)
        rng.shuffle(cycle)
        selected.extend(cycle)

    schedules: list[TransactionCrashSchedule] = []
    for index, (domain, point) in enumerate(selected[:count]):
        worker_order = list(_CAMPAIGN_WORKERS)
        rng.shuffle(worker_order)
        normalized_order = tuple(worker_order)
        worker_yields = tuple(rng.randrange(3) for _ in _CAMPAIGN_WORKERS)
        fault = FaultDirective(point)
        digest = _transaction_schedule_digest(
            index=index,
            seed=seed,
            domain=domain,
            fault=fault,
            worker_order=normalized_order,
            worker_yields=worker_yields,
        )
        schedules.append(
            TransactionCrashSchedule(
                index=index,
                seed=seed,
                domain=domain,
                fault=fault,
                worker_order=normalized_order,
                worker_yields=worker_yields,
                schedule_sha256=digest,
            )
        )
    return tuple(schedules)


def iter_fault_cycles(
    fault_points: Sequence[str],
    *,
    seed: int,
) -> Iterator[FaultDirective]:
    """Yield a deterministic unbounded permutation cycle for soak harnesses."""

    normalized = tuple(fault_points)
    if not normalized or any(not isinstance(point, str) or not point for point in normalized):
        raise ValueError("fault_points must contain non-empty strings")
    if len(set(normalized)) != len(normalized):
        raise ValueError("fault_points must not contain duplicates")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer")
    rng = random.Random(seed)
    while True:
        cycle = list(normalized)
        rng.shuffle(cycle)
        for point in cycle:
            yield FaultDirective(point)


__all__ = [
    "ABRUPT_EXIT_CODE",
    "APPEND_FAULT_POINTS",
    "APPEND_POSTCOMMIT_POINTS",
    "APPEND_PRECOMMIT_POINTS",
    "GENERATION_ACTIVATE_FAULT_POINTS",
    "GENERATION_COMMIT_FAULT_POINTS",
    "GENERATION_POSTCOMMIT_POINTS",
    "GENERATION_PREPARE_FAULT_POINTS",
    "GENERATION_PRECOMMIT_POINTS",
    "GENERATION_VERIFY_FAULT_POINTS",
    "TRANSACTION_FAULT_DOMAINS",
    "AbruptExitFault",
    "CrashConcurrencySchedule",
    "DeterministicFaultController",
    "FaultDirective",
    "InjectedFault",
    "TransactionCrashSchedule",
    "deterministic_crash_schedules",
    "deterministic_transaction_schedules",
    "iter_fault_cycles",
]
