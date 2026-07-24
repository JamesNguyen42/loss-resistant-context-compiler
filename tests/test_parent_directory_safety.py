from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

import benchmarks.json_io as benchmark_json
from benchmarks.json_io import (
    StrictJsonError,
    StrictJsonLimits,
    load_strict_json_file,
)
from context_compiler import PathBoundaryError, SourceArchive, SourceRecord
from context_compiler.atomic import atomic_write_text
from context_compiler.io import load_sources_path
from context_compiler.path_safety import ParentDirectoryGuard


def source_payload() -> list[dict[str, object]]:
    return [
        SourceRecord.create(
            id="parent-boundary-source",
            sequence=0,
            role="user",
            content="constraint: reject changed parent directories",
        ).to_dict()
    ]


def swap_parent_on_guard_check(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: Path,
    trigger: int,
    replacement: Callable[[Path], None],
) -> Path:
    original_verify = ParentDirectoryGuard.verify
    absolute_target = target.absolute()
    calls = 0
    moved_parent = target.parent.with_name(f"{target.parent.name}-moved")

    def verify_with_swap(self: ParentDirectoryGuard) -> None:
        nonlocal calls
        if self.target == absolute_target:
            calls += 1
            if calls == trigger:
                target.parent.rename(moved_parent)
                target.parent.mkdir()
                replacement(target)
        original_verify(self)

    monkeypatch.setattr(
        ParentDirectoryGuard,
        "verify",
        verify_with_swap,
    )
    return moved_parent


def test_source_loader_rejects_parent_substitution_before_accepting_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "trusted"
    directory.mkdir()
    source_path = directory / "sources.json"
    source_path.write_text(json.dumps(source_payload()), encoding="utf-8")

    moved = swap_parent_on_guard_check(
        monkeypatch,
        target=source_path,
        trigger=3,
        replacement=lambda path: path.write_text(
            json.dumps([{"role": "user", "content": "goal: attacker"}]),
            encoding="utf-8",
        ),
    )

    with pytest.raises(ValueError, match="ancestor directory changed"):
        load_sources_path(source_path)

    assert (moved / source_path.name).read_text(encoding="utf-8") != (
        source_path.read_text(encoding="utf-8")
    )


def test_benchmark_reader_rejects_parent_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "trusted"
    directory.mkdir()
    evidence_path = directory / "evidence.json"
    evidence_path.write_text('{"trusted":true}', encoding="utf-8")
    limits = StrictJsonLimits(
        max_bytes=1_024,
        max_line_chars=1_024,
        max_depth=8,
    )

    swap_parent_on_guard_check(
        monkeypatch,
        target=evidence_path,
        trigger=3,
        replacement=lambda path: path.write_text(
            '{"trusted":false}',
            encoding="utf-8",
        ),
    )

    with pytest.raises(StrictJsonError, match="ancestor directory changed"):
        load_strict_json_file(
            evidence_path,
            limits=limits,
            label="benchmark evidence",
        )


def test_atomic_writer_refuses_install_after_parent_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "trusted"
    directory.mkdir()
    output_path = directory / "result.json"

    moved = swap_parent_on_guard_check(
        monkeypatch,
        target=output_path,
        trigger=3,
        replacement=lambda _path: None,
    )

    with pytest.raises(ValueError, match="ancestor directory changed"):
        atomic_write_text(output_path, '{"trusted":true}\n')

    assert not output_path.exists()
    assert not (moved / output_path.name).exists()


def test_atomic_writer_creates_missing_parents_under_the_guard(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "one" / "two" / "result.json"

    atomic_write_text(output_path, "complete")

    assert output_path.read_text(encoding="utf-8") == "complete"


def test_linked_ancestor_is_rejected_by_shared_read_and_write_paths(
    tmp_path: Path,
) -> None:
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    source_path = real_directory / "sources.json"
    source_path.write_text(json.dumps(source_payload()), encoding="utf-8")
    linked_directory = tmp_path / "linked"
    try:
        linked_directory.symlink_to(real_directory, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory links unavailable on this filesystem: {exc}")

    linked_source = linked_directory / source_path.name
    with pytest.raises(ValueError, match="link or reparse point"):
        load_sources_path(linked_source)
    with pytest.raises(StrictJsonError, match="link or reparse point"):
        load_strict_json_file(
            linked_source,
            limits=StrictJsonLimits(
                max_bytes=4_096,
                max_line_chars=4_096,
                max_depth=16,
            ),
            label="benchmark evidence",
        )
    with pytest.raises(ValueError, match="link or reparse point"):
        atomic_write_text(linked_directory / "output.json", "unsafe")
    assert not (real_directory / "output.json").exists()


def test_benchmark_reader_detects_in_place_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text('{"version":1}', encoding="utf-8")
    original_read = benchmark_json.os.read
    reads = 0

    def mutate_after_read(descriptor: int, amount: int) -> bytes:
        nonlocal reads
        block = original_read(descriptor, amount)
        reads += 1
        if reads == 1:
            evidence_path.write_text(
                '{"version":200}',
                encoding="utf-8",
            )
        return block

    monkeypatch.setattr(benchmark_json.os, "read", mutate_after_read)

    with pytest.raises(StrictJsonError, match="changed while it was being read"):
        load_strict_json_file(
            evidence_path,
            limits=StrictJsonLimits(
                max_bytes=1_024,
                max_line_chars=1_024,
                max_depth=8,
            ),
            label="benchmark evidence",
        )


def test_archive_load_preserves_the_path_boundary_error_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = SourceArchive(tmp_path / "archive")

    def unsafe_parent() -> str:
        raise PathBoundaryError("archive ancestor changed during access")

    monkeypatch.setattr(archive, "_read_events_text", unsafe_parent)

    with pytest.raises(PathBoundaryError, match="ancestor changed"):
        archive.load()
    report = archive.verify()
    assert report.passed is False
    assert report.issues == ("archive ancestor changed during access",)
