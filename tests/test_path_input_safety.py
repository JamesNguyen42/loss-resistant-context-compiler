from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import context_compiler.io as io_module
from context_compiler import ContextCompiler, SourceRecord
from context_compiler.io import load_artifact_path, load_sources_path


def source() -> SourceRecord:
    return SourceRecord.create(
        id="stable-path-source",
        sequence=0,
        role="user",
        content="constraint: serialized inputs must come from stable regular files",
    )


def write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    record = source()
    source_path = tmp_path / "sources.json"
    artifact_path = tmp_path / "artifact.json"
    source_path.write_text(json.dumps([record.to_dict()]), encoding="utf-8")
    artifact_path.write_text(
        json.dumps(ContextCompiler().compile([record]).to_dict()),
        encoding="utf-8",
    )
    return source_path, artifact_path


def test_source_and_artifact_loaders_reject_directories(tmp_path: Path) -> None:
    source_path = tmp_path / "sources.json"
    artifact_path = tmp_path / "artifact.json"
    source_path.mkdir()
    artifact_path.mkdir()

    with pytest.raises(ValueError, match="source input path must be a regular file"):
        load_sources_path(source_path)
    with pytest.raises(
        ValueError,
        match="compiled artifact input path must be a regular file",
    ):
        load_artifact_path(artifact_path)


@pytest.mark.parametrize("reparse_check", [1, 2])
def test_source_and_artifact_loaders_reject_regular_mode_reparse_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reparse_check: int,
) -> None:
    source_path, artifact_path = write_inputs(tmp_path)
    loaders: tuple[tuple[Callable[[Path], Any], Path], ...] = (
        (load_sources_path, source_path),
        (load_artifact_path, artifact_path),
    )

    for loader, path in loaders:
        checks = 0

        def simulated_reparse(_value: os.stat_result) -> bool:
            nonlocal checks
            checks += 1
            return checks == reparse_check

        monkeypatch.setattr(
            io_module,
            "_is_link_or_reparse",
            simulated_reparse,
            raising=False,
        )
        with pytest.raises(ValueError, match="path must be a regular file"):
            loader(path)
        assert checks == reparse_check


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and FIFO semantics")
def test_source_and_artifact_loaders_refuse_symlinks_and_fifos(
    tmp_path: Path,
) -> None:
    source_path, artifact_path = write_inputs(tmp_path)
    source_link = tmp_path / "source-link.json"
    artifact_link = tmp_path / "artifact-link.json"
    source_link.symlink_to(source_path)
    artifact_link.symlink_to(artifact_path)

    with pytest.raises(ValueError, match="must be a regular file"):
        load_sources_path(source_link)
    with pytest.raises(ValueError, match="must be a regular file"):
        load_artifact_path(artifact_link)

    source_fifo = tmp_path / "source-fifo.json"
    artifact_fifo = tmp_path / "artifact-fifo.json"
    swap_race = tmp_path / "swap-race.json"
    os.mkfifo(source_fifo)
    os.mkfifo(artifact_fifo)
    swap_race.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    script = """
import os
import sys
import context_compiler.io as io_module
from context_compiler.io import load_artifact_path, load_sources_path

for loader, path in (
    (load_sources_path, sys.argv[1]),
    (load_artifact_path, sys.argv[2]),
):
    try:
        loader(path)
    except ValueError as exc:
        assert "must be a regular file" in str(exc)
    else:
        raise AssertionError("special file was accepted")

race_path = sys.argv[3]
real_open = io_module.os.open
swapped = False
def swap_to_fifo_before_open(path, flags, *args, **kwargs):
    global swapped
    same_target = os.fspath(path) == race_path or (
        kwargs.get("dir_fd") is not None
        and os.fspath(path) == os.path.basename(race_path)
    )
    if same_target and not swapped:
        os.unlink(race_path)
        os.mkfifo(race_path)
        swapped = True
    return real_open(path, flags, *args, **kwargs)
io_module.os.open = swap_to_fifo_before_open
try:
    load_sources_path(race_path)
except ValueError as exc:
    assert "must be a regular file" in str(exc)
else:
    raise AssertionError("FIFO substitution was accepted")
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(source_fifo),
            str(artifact_fifo),
            str(swap_race),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_path_loaders_retry_atomic_replacement_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path, artifact_path = write_inputs(tmp_path)
    committed = {
        source_path: source_path.read_bytes(),
        artifact_path: artifact_path.read_bytes(),
    }
    replaced: set[Path] = set()
    real_open = io_module.os.open

    def replace_before_first_open(
        path: object,
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        candidate = Path(path)  # type: ignore[arg-type]
        matched = next(
            (
                target
                for target in committed
                if candidate == target
                or (
                    kwargs.get("dir_fd") is not None
                    and candidate.name == target.name
                )
            ),
            None,
        )
        if matched is not None and matched not in replaced:
            replacement = matched.with_name(
                f".{matched.name}.replacement"
            )
            replacement.write_bytes(committed[matched])
            os.replace(replacement, matched)
            replaced.add(matched)
        return real_open(  # type: ignore[arg-type]
            path,
            flags,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(io_module.os, "open", replace_before_first_open)

    assert load_sources_path(source_path) == [source()]
    assert load_artifact_path(artifact_path)["schema_version"] == "1.0"
    assert replaced == {source_path, artifact_path}


def test_path_loaders_detect_in_place_change_while_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path, artifact_path = write_inputs(tmp_path)
    targets = {
        "source input": source_path,
        "compiled artifact input": artifact_path,
    }
    changed: set[str] = set()
    real_read = io_module._read_limited_text

    def read_then_change(
        stream: Any,
        *,
        max_input_bytes: int,
        max_line_chars: int,
        label: str,
        limit_error: type[ValueError],
    ) -> str:
        raw = real_read(
            stream,
            max_input_bytes=max_input_bytes,
            max_line_chars=max_line_chars,
            label=label,
            limit_error=limit_error,
        )
        if label in targets and label not in changed:
            path = targets[label]
            path.write_bytes(path.read_bytes() + b" ")
            changed.add(label)
        return raw

    monkeypatch.setattr(io_module, "_read_limited_text", read_then_change)

    loaders: tuple[tuple[str, Callable[[Path], Any], Path], ...] = (
        ("source input", load_sources_path, source_path),
        ("compiled artifact input", load_artifact_path, artifact_path),
    )
    for label, loader, path in loaders:
        with pytest.raises(ValueError, match=f"{label} changed while"):
            loader(path)
    assert changed == set(targets)


def test_path_loaders_reject_gzip_bytes_without_decompression(tmp_path: Path) -> None:
    source_path = tmp_path / "sources.json.gz"
    artifact_path = tmp_path / "artifact.json.gz"
    compressed = gzip.compress(b'{"role":"user","content":"goal: no decompression"}')
    source_path.write_bytes(compressed)
    artifact_path.write_bytes(compressed)

    with pytest.raises(UnicodeDecodeError):
        load_sources_path(source_path)
    with pytest.raises(UnicodeDecodeError):
        load_artifact_path(artifact_path)
