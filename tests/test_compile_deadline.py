from __future__ import annotations

import errno
import json
import math
import pickle
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

import context_compiler.isolation as isolation
import context_compiler.process_tree as process_tree
from context_compiler import (
    CompilationIsolationError,
    CompilationPolicy,
    ContextCompiler,
    SourceRecord,
)
from context_compiler.cli import main
from context_compiler.isolation import _decode_worker_artifact
from tests.deadline_fixtures import (
    BlockingExtractor,
    DescendantExtractor,
    MutatingExtractor,
)


class _FakeDarwinLibproc:
    def __init__(
        self,
        process_group_id: int,
        snapshots: list[tuple[int, ...]],
        *,
        statuses: dict[int, int] | None = None,
        effective_uids: dict[int, int] | None = None,
        reported_pids: dict[int, int] | None = None,
        reported_pgids: dict[int, int] | None = None,
        returned_info_size: int | None = None,
        forced_list_count: int | None = None,
    ) -> None:
        self.process_group_id = process_group_id
        self.snapshots = snapshots
        self.statuses = statuses or {}
        self.effective_uids = effective_uids or {}
        self.reported_pids = reported_pids or {}
        self.reported_pgids = reported_pgids or {}
        self.returned_info_size = returned_info_size
        self.forced_list_count = forced_list_count
        self.list_calls = 0
        self.info_calls: list[int] = []

    def proc_listpgrppids(
        self,
        process_group_id: int,
        pid_buffer: Any,
        _buffer_size: int,
    ) -> int:
        assert process_group_id == self.process_group_id
        if self.forced_list_count is not None:
            return self.forced_list_count
        if self.list_calls >= len(self.snapshots):
            raise AssertionError("unexpected additional process-group snapshot")
        members = self.snapshots[self.list_calls]
        self.list_calls += 1
        for index, process_id in enumerate(members):
            pid_buffer[index] = process_id
        return len(members)

    def proc_pidinfo(
        self,
        process_id: int,
        flavor: int,
        argument: int,
        process_info_pointer: Any,
        buffer_size: int,
    ) -> int:
        assert flavor == process_tree._DARWIN_PROC_PIDTBSDINFO
        assert (
            argument
            == process_tree._DARWIN_PROC_PIDTBSDINFO_INCLUDE_ZOMBIES
        )
        assert buffer_size == process_tree._DARWIN_PROC_BSD_INFO_SIZE
        self.info_calls.append(process_id)
        process_info = process_info_pointer._obj
        process_info.pbi_pid = self.reported_pids.get(process_id, process_id)
        process_info.pbi_pgid = self.reported_pgids.get(
            process_id,
            self.process_group_id,
        )
        process_info.pbi_status = self.statuses.get(
            process_id,
            process_tree._DARWIN_PROCESS_STATUS_ZOMBIE,
        )
        process_info.pbi_uid = self.effective_uids.get(process_id, 501)
        process_info.pbi_start_tvsec = 1_000_000 + process_id
        process_info.pbi_start_tvusec = process_id
        if self.returned_info_size is not None:
            return self.returned_info_size
        return buffer_size


def _install_fake_darwin_libproc(
    monkeypatch: pytest.MonkeyPatch,
    fake_libproc: _FakeDarwinLibproc,
) -> None:
    monkeypatch.setattr(process_tree.sys, "platform", "darwin")
    monkeypatch.setattr(
        process_tree,
        "_load_darwin_libproc",
        lambda _error_type: fake_libproc,
    )


def make_source() -> SourceRecord:
    return SourceRecord.create(
        id="deadline-source",
        sequence=0,
        role="user",
        content="constraint: Preserve the public API",
    )


def test_compile_deadline_requires_a_finite_positive_number() -> None:
    source = make_source()
    for invalid in (True, 0, -1, math.nan, math.inf, "1"):
        with pytest.raises(ValueError, match="finite positive"):
            ContextCompiler().compile(  # type: ignore[arg-type]
                [source],
                timeout_seconds=invalid,
            )


def test_compile_deadline_requires_materialized_sources() -> None:
    source = make_source()

    with pytest.raises(TypeError, match="materialized list or tuple"):
        ContextCompiler().compile(
            (value for value in [source]),
            timeout_seconds=5,
        )


def test_isolated_compile_returns_an_equivalent_sealed_snapshot() -> None:
    source = make_source()
    direct = ContextCompiler().compile([source])
    isolated = ContextCompiler().compile([source], timeout_seconds=5)

    assert isolated.to_prompt() == direct.to_prompt()
    assert isolated.verification.to_dict() == direct.verification.to_dict()
    assert isolated.compression.to_dict() == direct.compression.to_dict()
    direct_metrics = dict(direct.compiler_metadata["metrics"])
    isolated_metrics = dict(isolated.compiler_metadata["metrics"])
    direct_metrics.pop("compile_duration_seconds")
    isolated_metrics.pop("compile_duration_seconds")
    assert isolated_metrics == direct_metrics
    assert [item.to_dict() for item in isolated.items] == [
        item.to_dict() for item in direct.items
    ]
    round_trip = pickle.loads(pickle.dumps(isolated))
    assert round_trip.to_dict() == isolated.to_dict()
    tampered = json.loads(isolated.to_json())
    tampered["source_count"] += 1
    with pytest.raises(CompilationIsolationError, match="digest"):
        _decode_worker_artifact(tampered)


def test_darwin_all_zombie_proof_accepts_a_stable_bounded_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leader_pid = 4400
    member_pid = 4401
    fake_libproc = _FakeDarwinLibproc(
        leader_pid,
        [
            (leader_pid, member_pid),
            (member_pid, leader_pid),
        ],
    )
    _install_fake_darwin_libproc(monkeypatch, fake_libproc)

    process_tree.prove_darwin_process_group_all_zombies(
        leader_pid,
        expected_leader_pid=leader_pid,
    )

    assert fake_libproc.list_calls == 2
    assert fake_libproc.info_calls == [
        leader_pid,
        member_pid,
        leader_pid,
        member_pid,
    ]


def test_darwin_all_zombie_proof_rejects_a_live_changed_euid_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leader_pid = 4410
    member_pid = 4411
    fake_libproc = _FakeDarwinLibproc(
        leader_pid,
        [(leader_pid, member_pid)],
        statuses={member_pid: 2},
        effective_uids={member_pid: 0},
    )
    _install_fake_darwin_libproc(monkeypatch, fake_libproc)

    with pytest.raises(process_tree.ProcessTreeError, match=f"live PID {member_pid}"):
        process_tree.prove_darwin_process_group_all_zombies(
            leader_pid,
            expected_leader_pid=leader_pid,
        )


@pytest.mark.parametrize(
    "returned_size",
    [0, process_tree._DARWIN_PROC_BSD_INFO_SIZE - 1],
)
def test_darwin_all_zombie_proof_rejects_inaccessible_or_incomplete_records(
    monkeypatch: pytest.MonkeyPatch,
    returned_size: int,
) -> None:
    leader_pid = 4420
    fake_libproc = _FakeDarwinLibproc(
        leader_pid,
        [(leader_pid,)],
        returned_info_size=returned_size,
    )
    _install_fake_darwin_libproc(monkeypatch, fake_libproc)

    with pytest.raises(process_tree.ProcessTreeError, match="exact Darwin BSD"):
        process_tree.prove_darwin_process_group_all_zombies(
            leader_pid,
            expected_leader_pid=leader_pid,
        )


def test_darwin_all_zombie_proof_rejects_a_truncated_enumeration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leader_pid = 4430
    fake_libproc = _FakeDarwinLibproc(
        leader_pid,
        [],
        forced_list_count=process_tree._DARWIN_PROCESS_GROUP_PID_CAPACITY,
    )
    _install_fake_darwin_libproc(monkeypatch, fake_libproc)

    with pytest.raises(process_tree.ProcessTreeError, match="truncated"):
        process_tree.prove_darwin_process_group_all_zombies(
            leader_pid,
            expected_leader_pid=leader_pid,
        )


def test_darwin_all_zombie_proof_rejects_raced_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leader_pid = 4440
    fake_libproc = _FakeDarwinLibproc(
        leader_pid,
        [
            (leader_pid, 4441),
            (leader_pid, 4442),
        ],
    )
    _install_fake_darwin_libproc(monkeypatch, fake_libproc)

    with pytest.raises(process_tree.ProcessTreeError, match="membership changed"):
        process_tree.prove_darwin_process_group_all_zombies(
            leader_pid,
            expected_leader_pid=leader_pid,
        )


@pytest.mark.parametrize(
    ("reported_pids", "reported_pgids", "message"),
    [
        ({4451: 9999}, {}, "PID changed"),
        ({}, {4451: 9999}, "left the anchored"),
    ],
)
def test_darwin_all_zombie_proof_validates_record_identity_and_group(
    monkeypatch: pytest.MonkeyPatch,
    reported_pids: dict[int, int],
    reported_pgids: dict[int, int],
    message: str,
) -> None:
    leader_pid = 4450
    member_pid = 4451
    fake_libproc = _FakeDarwinLibproc(
        leader_pid,
        [(leader_pid, member_pid)],
        reported_pids=reported_pids,
        reported_pgids=reported_pgids,
    )
    _install_fake_darwin_libproc(monkeypatch, fake_libproc)

    with pytest.raises(process_tree.ProcessTreeError, match=message):
        process_tree.prove_darwin_process_group_all_zombies(
            leader_pid,
            expected_leader_pid=leader_pid,
        )


def test_isolated_compile_uses_the_resolved_default_temporary_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_temporary_directory = isolation.tempfile.TemporaryDirectory
    observed_roots: list[Path | None] = []

    def temporary_directory(*args: object, **kwargs: object) -> object:
        root = kwargs.get("dir")
        observed_roots.append(Path(root) if root is not None else None)
        return original_temporary_directory(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(isolation.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        isolation.tempfile,
        "TemporaryDirectory",
        temporary_directory,
    )

    result = ContextCompiler().compile([make_source()], timeout_seconds=5)

    assert result.verification.passed is True
    assert observed_roots == [tmp_path.resolve(strict=True)]


def test_isolation_temporary_root_resolves_a_directory_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical_root = tmp_path / "physical-temp"
    physical_root.mkdir()
    alias = tmp_path / "temp-alias"
    try:
        alias.symlink_to(physical_root, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this host")
    monkeypatch.setattr(isolation.tempfile, "gettempdir", lambda: str(alias))

    assert isolation._resolved_temporary_root() == physical_root.resolve(strict=True)
    result = ContextCompiler().compile([make_source()], timeout_seconds=5)
    assert result.verification.passed is True


def test_posix_normal_completion_cleans_group_before_reaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []

    class ExitedLeader:
        pid = 4312
        returncode = None

        def wait(self, *, timeout: float) -> int:
            events.append(("wait", self.pid))
            self.returncode = 0
            return 0

    leader = ExitedLeader()

    def capture_signal(process_group_id: int, requested_signal: int) -> None:
        assert leader.returncode is None, "numeric PGID used after leader reap"
        events.append(("signal", requested_signal))

    monkeypatch.setattr(isolation.os, "killpg", capture_signal, raising=False)

    isolation._finalize_posix_compile_process(  # type: ignore[arg-type]
        leader,
        leader_exited=True,
    )

    assert events == [
        ("signal", getattr(signal, "SIGKILL", 9)),
        ("wait", 4312),
    ]


def test_posix_finalizer_never_signals_an_already_reaped_pgid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReapedLeader:
        pid = 4318
        returncode = 0

    def reject_signal(_process_group_id: int, _requested_signal: int) -> None:
        raise AssertionError("numeric PGID must not be used after leader reap")

    monkeypatch.setattr(isolation.os, "killpg", reject_signal, raising=False)

    with pytest.raises(CompilationIsolationError, match="reaped before process-group cleanup"):
        isolation._finalize_posix_compile_process(  # type: ignore[arg-type]
            ReapedLeader(),
            leader_exited=True,
        )


def test_failed_group_cleanup_preserves_error_after_direct_child_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    cleanup_error = CompilationIsolationError("group proof rejected")

    class UnreapedLeader:
        pid = 4310
        returncode = None

        def kill(self) -> None:
            events.append("direct-kill")
            raise OSError("simulated direct-kill race")

        def wait(self, *, timeout: float) -> int:
            events.append("direct-wait")
            raise subprocess.TimeoutExpired(("worker",), timeout)

    def reject_group_cleanup(
        _process: object,
        *,
        leader_exited: bool,
    ) -> None:
        assert leader_exited is True
        events.append("group-cleanup")
        raise cleanup_error

    monkeypatch.setattr(
        isolation,
        "_finalize_posix_compile_process",
        reject_group_cleanup,
    )

    with pytest.raises(CompilationIsolationError) as captured:
        isolation._finalize_posix_compile_process_fail_closed(  # type: ignore[arg-type]
            UnreapedLeader(),
            leader_exited=True,
        )

    assert captured.value is cleanup_error
    assert events == ["group-cleanup", "direct-kill", "direct-wait"]


@pytest.mark.parametrize(
    ("failure_stage", "message", "expected_events"),
    [
        (
            "first-wait",
            "could not reap",
            ["wait-1"],
        ),
        (
            "direct-kill",
            "could not kill",
            ["wait-1", "direct-kill", "wait-2"],
        ),
        (
            "second-wait",
            "could not reap",
            ["wait-1", "direct-kill", "wait-2"],
        ),
        (
            "second-timeout",
            "did not exit after direct kill",
            ["wait-1", "direct-kill", "wait-2"],
        ),
    ],
)
def test_compile_reap_normalizes_post_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    message: str,
    expected_events: list[str],
) -> None:
    events: list[str] = []

    class Process:
        pid = 4311
        args = ("python", "-m", "context_compiler.isolation_worker")

        def wait(self, *, timeout: float) -> int:
            call_number = sum(event.startswith("wait-") for event in events) + 1
            events.append(f"wait-{call_number}")
            if failure_stage == "first-wait":
                raise OSError(errno.EIO, "sensitive first-wait detail")
            if call_number == 1:
                raise subprocess.TimeoutExpired(self.args, timeout)
            if failure_stage == "second-wait":
                raise OSError(errno.EIO, "sensitive second-wait detail")
            if failure_stage == "second-timeout":
                raise subprocess.TimeoutExpired(self.args, timeout)
            return 0

        def kill(self) -> None:
            events.append("direct-kill")
            if failure_stage == "direct-kill":
                raise OSError(errno.EIO, "sensitive direct-kill detail")

    def reject_group_signal(*_args: object) -> None:
        raise AssertionError("reaping must not retry a numeric process-group ID")

    monkeypatch.setattr(isolation.os, "killpg", reject_group_signal, raising=False)

    with pytest.raises(CompilationIsolationError, match=message) as captured:
        isolation._reap_compile_process(Process())  # type: ignore[arg-type]

    assert "sensitive" not in str(captured.value)
    assert events == expected_events


def test_compile_group_cleanup_stops_after_darwin_zombie_probe_eperm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []
    proofs: list[tuple[int, int]] = []

    class ExitedLeader:
        pid = 4313
        returncode = None

        def wait(self, *, timeout: float) -> int:
            events.append(("wait", self.pid))
            self.returncode = 0
            return 0

    leader = ExitedLeader()

    def terminate_then_deny_probe(
        process_group_id: int,
        requested_signal: int,
    ) -> None:
        assert leader.returncode is None, "numeric PGID used after leader reap"
        events.append(("signal", requested_signal))
        if requested_signal == 0:
            raise PermissionError(errno.EPERM, "Darwin all-zombie process group")

    monkeypatch.setattr(
        isolation.os,
        "killpg",
        terminate_then_deny_probe,
        raising=False,
    )
    monkeypatch.setattr(isolation.sys, "platform", "darwin")
    monkeypatch.setattr(
        isolation,
        "prove_darwin_process_group_all_zombies",
        lambda process_group_id, *, expected_leader_pid, error_type: proofs.append(
            (process_group_id, expected_leader_pid)
        ),
    )

    isolation._finalize_posix_compile_process(  # type: ignore[arg-type]
        leader,
        leader_exited=False,
    )

    assert events == [
        ("signal", getattr(signal, "SIGTERM", 15)),
        ("signal", 0),
        ("wait", 4313),
    ]
    assert proofs == [(4313, 4313)]


def test_darwin_final_sigkill_eperm_rejection_does_not_reap_before_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []

    class ExitedLeader:
        pid = 4319
        returncode = None

        def wait(self, *, timeout: float) -> int:
            events.append(("wait", self.pid))
            return 0

    def deny_signal(
        process_group_id: int,
        requested_signal: int,
    ) -> None:
        events.append(("signal", requested_signal))
        raise PermissionError(errno.EPERM, "mixed-permission process group")

    def reject_live_member(
        process_group_id: int,
        *,
        expected_leader_pid: int,
        error_type: type[RuntimeError],
    ) -> None:
        assert process_group_id == expected_leader_pid == 4319
        raise error_type("anchored Darwin process group still contains live PID 4320")

    monkeypatch.setattr(isolation.os, "killpg", deny_signal, raising=False)
    monkeypatch.setattr(isolation.sys, "platform", "darwin")
    monkeypatch.setattr(
        isolation,
        "prove_darwin_process_group_all_zombies",
        reject_live_member,
    )

    with pytest.raises(CompilationIsolationError, match="live PID 4320"):
        isolation._finalize_posix_compile_process(  # type: ignore[arg-type]
            ExitedLeader(),
            leader_exited=True,
        )

    assert events == [
        ("signal", getattr(signal, "SIGKILL", 9)),
        ("signal", 0),
    ]


def test_darwin_successful_sigkill_rejects_a_live_unsignalable_survivor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []

    class ExitedLeader:
        pid = 4321
        returncode = None

        def wait(self, *, timeout: float) -> int:
            events.append(("wait", self.pid))
            return 0

    def signal_then_deny_probe(
        process_group_id: int,
        requested_signal: int,
    ) -> None:
        assert process_group_id == 4321
        events.append(("signal", requested_signal))
        if requested_signal == 0:
            raise PermissionError(errno.EPERM, "unsignalable live survivor")

    def reject_live_member(
        process_group_id: int,
        *,
        expected_leader_pid: int,
        error_type: type[RuntimeError],
    ) -> None:
        assert process_group_id == expected_leader_pid == 4321
        raise error_type("anchored Darwin process group still contains live PID 4322")

    monkeypatch.setattr(
        isolation.os,
        "killpg",
        signal_then_deny_probe,
        raising=False,
    )
    monkeypatch.setattr(isolation.sys, "platform", "darwin")
    monkeypatch.setattr(
        isolation,
        "prove_darwin_process_group_all_zombies",
        reject_live_member,
    )

    with pytest.raises(CompilationIsolationError, match="live PID 4322"):
        isolation._finalize_posix_compile_process(  # type: ignore[arg-type]
            ExitedLeader(),
            leader_exited=True,
        )

    assert events == [
        ("signal", getattr(signal, "SIGKILL", 9)),
        ("signal", 0),
    ]


def test_compile_group_cleanup_force_kills_after_grace_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[int] = []
    monotonic_values = iter((10.0, 11.0))

    class LiveLeader:
        pid = 4314

    def capture_signal(_process_group_id: int, requested_signal: int) -> None:
        signals.append(requested_signal)

    monkeypatch.setattr(isolation.os, "killpg", capture_signal, raising=False)
    monkeypatch.setattr(
        isolation.time,
        "monotonic",
        lambda: next(monotonic_values),
    )

    isolation._terminate_compile_posix_process_tree(  # type: ignore[arg-type]
        LiveLeader(),
        leader_exited=False,
    )

    assert signals == [
        getattr(signal, "SIGTERM", 15),
        0,
        getattr(signal, "SIGKILL", 9),
    ]


def test_compile_group_cleanup_fails_closed_on_initial_term_eperm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LiveLeader:
        pid = 4315

    def deny_term(_process_group_id: int, _requested_signal: int) -> None:
        raise PermissionError(errno.EPERM, "ambiguous initial signal denial")

    monkeypatch.setattr(isolation.os, "killpg", deny_term, raising=False)

    with pytest.raises(CompilationIsolationError, match="could not terminate"):
        isolation._terminate_compile_posix_process_tree(  # type: ignore[arg-type]
            LiveLeader(),
            leader_exited=False,
        )


def test_darwin_initial_term_eperm_accepts_only_a_fresh_exited_zombie_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []
    proofs: list[tuple[int, int]] = []

    class RacedLeader:
        pid = 4323

    def deny_term(process_group_id: int, requested_signal: int) -> None:
        events.append(("signal", requested_signal))
        raise PermissionError(errno.EPERM, "leader exited before SIGTERM")

    def observe_exit(id_type: int, process_id: int, options: int) -> object:
        assert id_type == 1
        assert options == 2 | 4 | 8
        events.append(("waitid", process_id))
        return object()

    monkeypatch.setattr(isolation.sys, "platform", "darwin")
    monkeypatch.setattr(isolation.os, "killpg", deny_term, raising=False)
    monkeypatch.setattr(isolation.os, "P_PID", 1, raising=False)
    monkeypatch.setattr(isolation.os, "WEXITED", 2, raising=False)
    monkeypatch.setattr(isolation.os, "WNOHANG", 4, raising=False)
    monkeypatch.setattr(isolation.os, "WNOWAIT", 8, raising=False)
    monkeypatch.setattr(isolation.os, "waitid", observe_exit, raising=False)
    monkeypatch.setattr(
        isolation,
        "prove_darwin_process_group_all_zombies",
        lambda process_group_id, *, expected_leader_pid, error_type: proofs.append(
            (process_group_id, expected_leader_pid)
        ),
    )

    isolation._terminate_compile_posix_process_tree(  # type: ignore[arg-type]
        RacedLeader(),
        leader_exited=False,
    )

    assert events == [
        ("signal", getattr(signal, "SIGTERM", 15)),
        ("waitid", 4323),
    ]
    assert proofs == [(4323, 4323)]


def test_darwin_initial_term_eperm_rejects_a_still_live_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []

    class LiveLeader:
        pid = 4324

    def deny_term(process_group_id: int, requested_signal: int) -> None:
        events.append(("signal", requested_signal))
        raise PermissionError(errno.EPERM, "live unsignalable member")

    def observe_live(id_type: int, process_id: int, options: int) -> None:
        assert id_type == 1
        assert options == 2 | 4 | 8
        events.append(("waitid", process_id))
        return None

    def reject_proof(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a live leader must not enter all-zombie proof")

    monkeypatch.setattr(isolation.sys, "platform", "darwin")
    monkeypatch.setattr(isolation.os, "killpg", deny_term, raising=False)
    monkeypatch.setattr(isolation.os, "P_PID", 1, raising=False)
    monkeypatch.setattr(isolation.os, "WEXITED", 2, raising=False)
    monkeypatch.setattr(isolation.os, "WNOHANG", 4, raising=False)
    monkeypatch.setattr(isolation.os, "WNOWAIT", 8, raising=False)
    monkeypatch.setattr(isolation.os, "waitid", observe_live, raising=False)
    monkeypatch.setattr(
        isolation,
        "prove_darwin_process_group_all_zombies",
        reject_proof,
    )

    with pytest.raises(CompilationIsolationError, match="could not terminate"):
        isolation._terminate_compile_posix_process_tree(  # type: ignore[arg-type]
            LiveLeader(),
            leader_exited=False,
        )

    assert events == [
        ("signal", getattr(signal, "SIGTERM", 15)),
        ("waitid", 4324),
    ]


@pytest.mark.parametrize(
    ("leader_exited", "platform_name", "failure_signal", "message"),
    [
        (False, "linux", getattr(signal, "SIGTERM", 15), "could not terminate"),
        (False, "linux", 0, "could not verify termination"),
        (
            True,
            "linux",
            getattr(signal, "SIGKILL", 9),
            "could not force-terminate",
        ),
        (True, "darwin", 0, "could not verify force-termination"),
    ],
)
def test_compile_group_cleanup_wraps_non_permission_signal_errors(
    monkeypatch: pytest.MonkeyPatch,
    leader_exited: bool,
    platform_name: str,
    failure_signal: int,
    message: str,
) -> None:
    events: list[int] = []

    class Leader:
        pid = 4325

    def fail_at_selected_signal(
        _process_group_id: int,
        requested_signal: int,
    ) -> None:
        events.append(requested_signal)
        if requested_signal == failure_signal:
            raise OSError(errno.EIO, "sensitive operating-system detail")

    monkeypatch.setattr(isolation.sys, "platform", platform_name)
    monkeypatch.setattr(
        isolation.os,
        "killpg",
        fail_at_selected_signal,
        raising=False,
    )

    with pytest.raises(CompilationIsolationError, match=message) as captured:
        isolation._terminate_compile_posix_process_tree(  # type: ignore[arg-type]
            Leader(),
            leader_exited=leader_exited,
        )

    assert "sensitive operating-system detail" not in str(captured.value)
    if leader_exited and failure_signal == 0:
        assert events == [getattr(signal, "SIGKILL", 9), 0]
    else:
        assert events[-1] == failure_signal


@pytest.mark.parametrize(
    ("leader_exited", "platform_name", "failure_signal", "message"),
    [
        (False, "linux", getattr(signal, "SIGTERM", 15), "could not terminate"),
        (False, "linux", 0, "could not verify termination"),
        (
            True,
            "linux",
            getattr(signal, "SIGKILL", 9),
            "could not force-terminate",
        ),
        (True, "darwin", 0, "could not verify force-termination"),
    ],
)
def test_compile_group_cleanup_wraps_non_eperm_permission_errors(
    monkeypatch: pytest.MonkeyPatch,
    leader_exited: bool,
    platform_name: str,
    failure_signal: int,
    message: str,
) -> None:
    events: list[int] = []

    class Leader:
        pid = 4316

    def deny_at_selected_signal(
        _process_group_id: int,
        requested_signal: int,
    ) -> None:
        events.append(requested_signal)
        if requested_signal == failure_signal:
            raise PermissionError(
                errno.EACCES,
                "sensitive unexpected permission detail",
            )

    monkeypatch.setattr(isolation.sys, "platform", platform_name)
    monkeypatch.setattr(
        isolation.os,
        "killpg",
        deny_at_selected_signal,
        raising=False,
    )

    with pytest.raises(CompilationIsolationError, match=message) as captured:
        isolation._terminate_compile_posix_process_tree(  # type: ignore[arg-type]
            Leader(),
            leader_exited=leader_exited,
        )

    assert "sensitive unexpected permission detail" not in str(captured.value)
    if leader_exited and failure_signal == 0:
        assert events == [getattr(signal, "SIGKILL", 9), 0]
    else:
        assert events[-1] == failure_signal


def test_posix_wait_observes_exit_without_reaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[int, int, int]] = []

    class Leader:
        pid = 4317
        args = ("python", "-m", "context_compiler.isolation_worker")

        def wait(self, *, timeout: float) -> int:
            raise AssertionError("WNOWAIT observer must not reap the leader")

    def waitid(id_type: int, process_id: int, options: int) -> object:
        observed.append((id_type, process_id, options))
        return object()

    monkeypatch.setattr(isolation.os, "P_PID", 1, raising=False)
    monkeypatch.setattr(isolation.os, "WEXITED", 2, raising=False)
    monkeypatch.setattr(isolation.os, "WNOHANG", 4, raising=False)
    monkeypatch.setattr(isolation.os, "WNOWAIT", 8, raising=False)
    monkeypatch.setattr(isolation.os, "waitid", waitid, raising=False)

    isolation._wait_posix_worker_without_reaping(  # type: ignore[arg-type]
        Leader(),
        timeout=5,
    )

    assert observed == [(1, 4317, 2 | 4 | 8)]


def test_posix_wait_rejects_an_exit_observed_after_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monotonic_values = iter((10.0, 10.5, 11.0))
    waitid_calls: list[int] = []

    class Leader:
        pid = 4326
        args = ("python", "-m", "context_compiler.isolation_worker")

    def waitid(_id_type: int, process_id: int, _options: int) -> object:
        waitid_calls.append(process_id)
        return object()

    monkeypatch.setattr(
        isolation.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(isolation.os, "P_PID", 1, raising=False)
    monkeypatch.setattr(isolation.os, "WEXITED", 2, raising=False)
    monkeypatch.setattr(isolation.os, "WNOHANG", 4, raising=False)
    monkeypatch.setattr(isolation.os, "WNOWAIT", 8, raising=False)
    monkeypatch.setattr(isolation.os, "waitid", waitid, raising=False)

    with pytest.raises(subprocess.TimeoutExpired):
        isolation._wait_posix_worker_without_reaping(  # type: ignore[arg-type]
            Leader(),
            timeout=1.0,
        )

    assert waitid_calls == [4326]


def test_posix_wait_does_not_probe_after_an_expired_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monotonic_values = iter((10.0, 10.0))

    class Leader:
        pid = 4327
        args = ("python", "-m", "context_compiler.isolation_worker")

    def reject_waitid(*_args: object) -> object:
        raise AssertionError("an expired deadline must not observe the worker")

    monkeypatch.setattr(
        isolation.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(isolation.os, "waitid", reject_waitid, raising=False)

    with pytest.raises(subprocess.TimeoutExpired):
        isolation._wait_posix_worker_without_reaping(  # type: ignore[arg-type]
            Leader(),
            timeout=0.0,
        )


def test_timeout_terminates_blocked_extractor_before_late_side_effect(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "late-marker.txt"
    compiler = ContextCompiler(BlockingExtractor(str(marker)))

    with pytest.raises(
        TimeoutError,
        match="owned process-boundary cleanup completed",
    ):
        compiler.compile([make_source()], timeout_seconds=0.5)

    time.sleep(2)
    assert not marker.exists()


def test_timeout_terminates_extractor_descendant_tree(tmp_path: Path) -> None:
    started = tmp_path / "extractor-started.txt"
    orphan_marker = tmp_path / "orphan-marker.txt"
    compiler = ContextCompiler(
        DescendantExtractor(str(started), str(orphan_marker))
    )

    with pytest.raises(
        TimeoutError,
        match="owned process-boundary cleanup completed",
    ):
        compiler.compile([make_source()], timeout_seconds=1)

    assert started.is_file()
    time.sleep(3.2)
    assert not orphan_marker.exists()


def test_isolation_prevents_extractor_mutation_of_caller_sources() -> None:
    source = make_source()
    original = source.to_dict()
    compiler = ContextCompiler(
        MutatingExtractor(),
        policy=CompilationPolicy(
            fail_on_primary_extractor_error=True,
        ),
    )

    with pytest.raises(ValueError, match="content hash mismatch"):
        compiler.compile([source], timeout_seconds=5)

    assert source.to_dict() == original
    source.ensure_integrity()


def test_cli_compile_deadline_succeeds_and_reports_timeout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sources_path = tmp_path / "sources.json"
    output_path = tmp_path / "artifact.json"
    sources_path.write_text(
        json.dumps([{"role": "user", "content": "goal: Stay responsive"}]),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "compile",
                str(sources_path),
                "--compile-timeout-seconds",
                "5",
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    assert json.loads(output_path.read_text(encoding="utf-8"))[
        "schema_version"
    ] == "1.0"
    assert capsys.readouterr().err == ""

    assert (
        main(
            [
                "compile",
                str(sources_path),
                "--compile-timeout-seconds",
                "0.000000001",
                "--error-format",
                "json",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.err)
    assert captured.out == ""
    assert diagnostic["category"] == "timeout"
    assert diagnostic["code"] == "operation_timed_out"
