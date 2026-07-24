from __future__ import annotations

import os
from pathlib import Path

import pytest

import context_compiler.atomic as atomic_module
from context_compiler.atomic import atomic_write_text


def test_atomic_writer_requires_text_and_boolean_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "output.json"

    with pytest.raises(TypeError, match="must be a string"):
        atomic_write_text(output, b"bytes")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must be a boolean"):
        atomic_write_text(output, "value", overwrite=1)  # type: ignore[arg-type]

    assert not output.exists()


def test_exclusive_atomic_install_creates_complete_file(tmp_path: Path) -> None:
    output = tmp_path / "output.json"

    atomic_write_text(output, '{"complete":true}\n', overwrite=False)

    assert output.read_text(encoding="utf-8") == '{"complete":true}\n'
    assert list(tmp_path.glob(".ctxc-*.tmp")) == []


def test_exclusive_atomic_install_preserves_existing_file(tmp_path: Path) -> None:
    output = tmp_path / "output.json"
    output.write_text("committed", encoding="utf-8")

    with pytest.raises(FileExistsError):
        atomic_write_text(output, "replacement", overwrite=False)

    assert output.read_text(encoding="utf-8") == "committed"
    assert list(tmp_path.glob(".ctxc-*.tmp")) == []


def test_exclusive_atomic_install_loses_race_without_clobbering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output.json"
    real_link = atomic_module.os.link

    def create_racer_then_link(
        temporary: object,
        target: object,
        **kwargs: object,
    ) -> None:
        destination_descriptor = kwargs.get("dst_dir_fd")
        if isinstance(destination_descriptor, int):
            descriptor = os.open(
                target,  # type: ignore[arg-type]
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
                dir_fd=destination_descriptor,
            )
            try:
                os.write(descriptor, b"racer")
            finally:
                os.close(descriptor)
        else:
            Path(target).write_text(  # type: ignore[arg-type]
                "racer",
                encoding="utf-8",
            )
        real_link(temporary, target, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(atomic_module.os, "link", create_racer_then_link)

    with pytest.raises(FileExistsError):
        atomic_write_text(output, "ours", overwrite=False)

    assert output.read_text(encoding="utf-8") == "racer"
    assert list(tmp_path.glob(".ctxc-*.tmp")) == []


def test_exclusive_atomic_install_cleans_temp_after_link_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output.json"

    def fail_link(
        _temporary: object,
        _target: object,
        **_kwargs: object,
    ) -> None:
        raise OSError("injected link failure")

    monkeypatch.setattr(atomic_module.os, "link", fail_link)

    with pytest.raises(OSError, match="injected link failure"):
        atomic_write_text(output, "uncommitted", overwrite=False)

    assert not output.exists()
    assert list(tmp_path.glob(".ctxc-*.tmp")) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync semantics")
def test_exclusive_post_link_fsync_failure_leaves_complete_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output.json"
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
        atomic_write_text(output, "complete", overwrite=False)

    assert output.read_text(encoding="utf-8") == "complete"
    assert list(tmp_path.glob(".ctxc-*.tmp")) == []
