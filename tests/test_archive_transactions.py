from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

import context_compiler.atomic as atomic_module
from context_compiler import SourceArchive, SourceRecord


def source(sequence: int) -> SourceRecord:
    return SourceRecord.create(
        id=f"source-{sequence}",
        sequence=sequence,
        role="user",
        content=f"goal: preserve archive record {sequence}",
    )


def physical_sequences(path: Path) -> list[int]:
    return [
        json.loads(line)["sequence"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_archive_atomic_commit_rewrites_complete_sorted_history(tmp_path: Path) -> None:
    archive = SourceArchive(tmp_path / "archive")

    assert archive.append([source(2), source(0)]) == 2
    assert physical_sequences(archive.events_path) == [0, 2]
    assert archive.append([source(1)]) == 1

    assert physical_sequences(archive.events_path) == [0, 1, 2]
    assert [record.sequence for record in archive.load()] == [0, 1, 2]
    assert archive.events_path.read_bytes().endswith(b"\n")
    assert list(archive.directory.glob(".ctxc-*.tmp")) == []


def test_archive_replace_failure_preserves_committed_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = SourceArchive(tmp_path / "archive")
    first = source(0)
    assert archive.append([first]) == 1
    committed = archive.events_path.read_bytes()

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("injected archive replace failure")

    monkeypatch.setattr(atomic_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected archive replace failure"):
        archive.append([source(1)])

    assert archive.events_path.read_bytes() == committed
    assert archive.load() == [first]
    assert list(archive.directory.glob(".ctxc-*.tmp")) == []
    assert not archive.lock_path.exists()


def test_archive_file_fsync_failure_preserves_committed_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = SourceArchive(tmp_path / "archive")
    first = source(0)
    assert archive.append([first]) == 1
    committed = archive.events_path.read_bytes()

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("injected archive fsync failure")

    monkeypatch.setattr(atomic_module.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="injected archive fsync failure"):
        archive.append([source(1)])

    assert archive.events_path.read_bytes() == committed
    assert archive.load() == [first]
    assert list(archive.directory.glob(".ctxc-*.tmp")) == []
    assert not archive.lock_path.exists()


def test_archive_is_old_then_new_at_replace_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = SourceArchive(tmp_path / "archive")
    assert archive.append([source(0)]) == 1
    old_bytes = archive.events_path.read_bytes()
    real_replace = atomic_module.os.replace
    observations: list[list[int]] = []

    def observe_replace(temporary: object, target: object) -> None:
        assert archive.events_path.read_bytes() == old_bytes
        observations.append(physical_sequences(archive.events_path))
        real_replace(temporary, target)
        observations.append(physical_sequences(archive.events_path))

    monkeypatch.setattr(atomic_module.os, "replace", observe_replace)

    assert archive.append([source(1)]) == 1
    assert observations == [[0], [0, 1]]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode semantics")
def test_archive_atomic_replace_preserves_existing_mode(tmp_path: Path) -> None:
    archive = SourceArchive(tmp_path / "archive")
    assert archive.append([source(0)]) == 1
    archive.events_path.chmod(0o640)

    assert archive.append([source(1)]) == 1

    assert stat.S_IMODE(archive.events_path.stat().st_mode) == 0o640


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync semantics")
def test_post_replace_directory_fsync_failure_leaves_complete_new_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = SourceArchive(tmp_path / "archive")
    assert archive.append([source(0)]) == 1
    real_fsync = atomic_module.os.fsync
    calls = 0

    def fail_directory_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(atomic_module.os, "fsync", fail_directory_fsync)

    with pytest.raises(OSError, match="injected directory fsync failure"):
        archive.append([source(1)])

    assert [record.sequence for record in archive.load()] == [0, 1]
    assert list(archive.directory.glob(".ctxc-*.tmp")) == []
    assert not archive.lock_path.exists()
