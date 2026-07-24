from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import context_compiler.io as io_module
from context_compiler import SourceArchive, SourceRecord


def source() -> SourceRecord:
    return SourceRecord.create(
        id="archive-file-safety",
        sequence=0,
        role="user",
        content="constraint: archive events must be read from a stable regular file",
    )


def test_archive_rejects_a_non_regular_events_path_without_opening_it(
    tmp_path: Path,
) -> None:
    archive = SourceArchive(tmp_path / "archive")
    archive.directory.mkdir()
    archive.events_path.mkdir()

    report = archive.verify()

    assert report.passed is False
    assert "must be a regular file" in report.issues[0]
    with pytest.raises(ValueError, match="must be a regular file"):
        archive.load()


def test_archive_rejects_a_hard_linked_events_file(tmp_path: Path) -> None:
    original = SourceArchive(tmp_path / "original")
    assert original.append([source()]) == 1
    archive = SourceArchive(tmp_path / "archive")
    archive.directory.mkdir()
    try:
        os.link(original.events_path, archive.events_path)
    except OSError as exc:
        pytest.skip(f"hard links unavailable on this filesystem: {exc}")

    report = archive.verify()

    assert report.passed is False
    assert "must not have hard links" in report.issues[0]


def test_archive_retries_an_atomic_replacement_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = SourceArchive(tmp_path / "archive")
    record = source()
    assert archive.append([record]) == 1
    committed = archive.events_path.read_bytes()
    real_open = io_module.os.open
    replacements = 0

    def replace_before_first_open(
        path: object,
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        nonlocal replacements
        candidate = Path(path)  # type: ignore[arg-type]
        same_target = candidate == archive.events_path or (
            kwargs.get("dir_fd") is not None
            and candidate.name == archive.events_path.name
        )
        if same_target and replacements == 0:
            replacement = archive.directory / "replacement.jsonl"
            replacement.write_bytes(committed)
            os.replace(replacement, archive.events_path)
            replacements += 1
        return real_open(  # type: ignore[arg-type]
            path,
            flags,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(io_module.os, "open", replace_before_first_open)

    assert archive.load() == [record]
    assert replacements == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and FIFO semantics")
def test_archive_refuses_symlink_and_fifo_events_without_touching_targets(
    tmp_path: Path,
) -> None:
    original = SourceArchive(tmp_path / "original")
    assert original.append([source()]) == 1
    committed = original.events_path.read_bytes()

    symlink_archive = SourceArchive(tmp_path / "symlink-archive")
    symlink_archive.directory.mkdir()
    symlink_archive.events_path.symlink_to(original.events_path)
    symlink_report = symlink_archive.verify()
    assert symlink_report.passed is False
    assert "must be a regular file" in symlink_report.issues[0]
    assert original.events_path.read_bytes() == committed

    fifo_archive = SourceArchive(tmp_path / "fifo-archive")
    fifo_archive.directory.mkdir()
    os.mkfifo(fifo_archive.events_path)
    script = """
import sys
from context_compiler import SourceArchive

report = SourceArchive(sys.argv[1]).verify()
assert not report.passed
assert "must be a regular file" in report.issues[0]
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(fifo_archive.directory)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
