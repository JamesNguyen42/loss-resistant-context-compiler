from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

import context_compiler.file_lock as file_lock_module
from context_compiler import SourceArchive, SourceRecord
from context_compiler.file_lock import close_lock_file


def source(sequence: int) -> SourceRecord:
    return SourceRecord.create(
        id=f"source-{sequence}",
        sequence=sequence,
        role="user",
        content=f"goal: preserve lock record {sequence}",
    )


def test_close_lock_file_surfaces_unpaired_close_failure() -> None:
    with pytest.raises(OSError):
        close_lock_file(-1)


def test_close_lock_file_preserves_prior_error_and_adds_note() -> None:
    prior_error = ValueError("primary archive failure")

    close_lock_file(-1, prior_error=prior_error)

    assert prior_error.__notes__
    assert "descriptor close also failed" in prior_error.__notes__[0]


@pytest.mark.parametrize(
    "timeout",
    [True, -0.01, float("inf"), float("-inf"), float("nan"), "1"],
)
def test_archive_rejects_unsafe_lock_timeouts(
    tmp_path: Path,
    timeout: object,
) -> None:
    with pytest.raises(ValueError, match="finite non-negative"):
        SourceArchive(tmp_path / "archive", lock_timeout=timeout)  # type: ignore[arg-type]


def test_persistent_lock_marker_is_not_treated_as_lock_ownership(tmp_path: Path) -> None:
    directory = tmp_path / "archive"
    first = SourceArchive(directory)
    second = SourceArchive(directory)

    assert first.append([source(0)]) == 1
    assert first.lock_path.is_file()
    assert second.append([source(1)]) == 1
    assert [record.sequence for record in first.load()] == [0, 1]


def test_live_advisory_lock_times_out_then_releases(tmp_path: Path) -> None:
    directory = tmp_path / "archive"
    directory.mkdir()
    holder = SourceArchive(directory)
    contender = SourceArchive(directory, lock_timeout=0.05)
    descriptor = holder._acquire_lock()
    try:
        assert not os.get_inheritable(descriptor)
        with pytest.raises(TimeoutError, match="archive is locked"):
            contender.append([source(0)])
    finally:
        os.close(descriptor)

    assert contender.append([source(0)]) == 1


def test_process_death_releases_archive_lock(tmp_path: Path) -> None:
    directory = tmp_path / "archive"
    directory.mkdir()
    ready_path = tmp_path / "lock-ready"
    script = """
import sys
import time
from pathlib import Path
from context_compiler import SourceArchive

archive = SourceArchive(sys.argv[1])
descriptor = archive._acquire_lock()
Path(sys.argv[2]).write_text("ready", encoding="utf-8")
time.sleep(60)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(directory), str(ready_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5.0
        while not ready_path.exists():
            if child.poll() is not None:
                _stdout, stderr = child.communicate()
                pytest.fail(f"lock-holder process exited early: {stderr}")
            if time.monotonic() >= deadline:
                pytest.fail("lock-holder process did not become ready")
            time.sleep(0.01)

        contender = SourceArchive(directory, lock_timeout=0.05)
        with pytest.raises(TimeoutError, match="archive is locked"):
            contender.append([source(0)])

        child.kill()
        child.communicate(timeout=5.0)

        assert contender.append([source(0)]) == 1
        assert contender.lock_path.is_file()
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5.0)


def test_non_regular_lock_path_is_refused_without_writing_archive(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "archive"
    directory.mkdir()
    lock_path = directory / ".append.lock"
    lock_path.mkdir()
    archive = SourceArchive(directory, lock_timeout=0)

    with pytest.raises(OSError):
        archive.append([source(0)])

    assert lock_path.is_dir()
    assert not archive.events_path.exists()


def test_hard_linked_lock_marker_is_refused_without_mutating_archive(
    tmp_path: Path,
) -> None:
    archive = SourceArchive(tmp_path / "archive")
    assert archive.append([source(0)]) == 1
    committed = archive.events_path.read_bytes()
    alias = tmp_path / "lock-alias"
    try:
        os.link(archive.lock_path, alias)
    except OSError as exc:
        pytest.skip(f"hard links unavailable on this filesystem: {exc}")

    with pytest.raises(OSError, match="single-link regular file"):
        archive.append([source(1)])

    assert archive.events_path.read_bytes() == committed


def test_lock_open_retries_a_replacement_before_returning_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = SourceArchive(tmp_path / "archive")
    assert archive.append([source(0)]) == 1
    real_open = file_lock_module.os.open
    replacements = 0

    def replace_after_open(
        path: object,
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        nonlocal replacements
        candidate = Path(path)  # type: ignore[arg-type]
        same_target = candidate == archive.lock_path or (
            kwargs.get("dir_fd") is not None
            and candidate.name == archive.lock_path.name
        )
        if same_target and replacements == 0 and os.name == "nt":
            replacement = archive.directory / ".replacement.lock"
            replacement.write_bytes(b"")
            os.replace(replacement, archive.lock_path)
            replacements += 1
        descriptor = real_open(  # type: ignore[arg-type]
            path,
            flags,
            *args,
            **kwargs,
        )
        if same_target and replacements == 0:
            replacement = archive.directory / ".replacement.lock"
            replacement.write_bytes(b"")
            os.replace(replacement, archive.lock_path)
            replacements += 1
        return descriptor

    monkeypatch.setattr(
        file_lock_module.os,
        "open",
        replace_after_open,
    )

    assert archive.append([source(1)]) == 1
    assert replacements == 1
    assert [record.sequence for record in archive.load()] == [0, 1]


def test_open_lock_file_returns_the_current_replacement_cross_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "archive"
    directory.mkdir()
    lock_path = directory / ".append.lock"
    lock_path.write_bytes(b"original")
    real_open = file_lock_module.os.open
    replacements = 0
    target_opens = 0

    def replace_across_open_boundary(
        path: object,
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        nonlocal replacements, target_opens
        candidate = Path(path)  # type: ignore[arg-type]
        same_target = candidate == lock_path or (
            kwargs.get("dir_fd") is not None
            and candidate.name == lock_path.name
        )
        if same_target:
            target_opens += 1
        if same_target and replacements == 0 and os.name == "nt":
            replacement = directory / ".replacement.lock"
            replacement.write_bytes(b"replacement")
            os.replace(replacement, lock_path)
            replacements += 1
        descriptor = real_open(  # type: ignore[arg-type]
            path,
            flags,
            *args,
            **kwargs,
        )
        if same_target and replacements == 0:
            replacement = directory / ".replacement.lock"
            replacement.write_bytes(b"replacement")
            os.replace(replacement, lock_path)
            replacements += 1
        return descriptor

    monkeypatch.setattr(
        file_lock_module.os,
        "open",
        replace_across_open_boundary,
    )

    descriptor = file_lock_module.open_lock_file(lock_path)
    try:
        returned_stat = os.fstat(descriptor)
        current_stat = lock_path.stat()
        assert returned_stat.st_nlink == 1
        assert (returned_stat.st_dev, returned_stat.st_ino) == (
            current_stat.st_dev,
            current_stat.st_ino,
        )
        assert os.read(descriptor, len(b"replacement")) == b"replacement"
    finally:
        file_lock_module.close_lock_file(descriptor)

    assert replacements == 1
    assert target_opens == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX lock-file mode semantics")
def test_new_lock_file_is_owner_only_on_posix(tmp_path: Path) -> None:
    archive = SourceArchive(tmp_path / "archive")

    assert archive.append([source(0)]) == 1

    assert stat.S_IMODE(archive.lock_path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX O_NOFOLLOW semantics")
def test_symlink_lock_path_is_refused_without_touching_target(tmp_path: Path) -> None:
    directory = tmp_path / "archive"
    directory.mkdir()
    target = tmp_path / "outside"
    target.write_text("untouched", encoding="utf-8")
    (directory / ".append.lock").symlink_to(target)
    archive = SourceArchive(directory, lock_timeout=0)

    with pytest.raises(OSError):
        archive.append([source(0)])

    assert target.read_text(encoding="utf-8") == "untouched"
    assert not archive.events_path.exists()
