from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.release_install_smoke import (
    _artifact_install_command,
    _assert_artifact_snapshot,
    _build_tool_install_command,
    _offline_build_inputs,
    _release_artifacts,
    _verified_artifact_snapshot,
)

WHEEL = "loss_resistant_context_compiler-0.1.0-py3-none-any.whl"
SDIST = "loss_resistant_context_compiler-0.1.0.tar.gz"


def test_release_smoke_rejects_symlinked_artifact(tmp_path: Path) -> None:
    target = tmp_path / "target.whl"
    target.write_bytes(b"wheel")
    linked = tmp_path / WHEEL
    try:
        linked.symlink_to(target)
    except OSError:
        pytest.skip("file symlinks are unavailable on this host")

    with pytest.raises(ValueError, match="lexical regular single-link"):
        _verified_artifact_snapshot(linked)


def test_release_smoke_rejects_hard_linked_artifact(tmp_path: Path) -> None:
    target = tmp_path / "target.whl"
    target.write_bytes(b"wheel")
    linked = tmp_path / WHEEL
    try:
        os.link(target, linked)
    except OSError:
        pytest.skip("hard links are unavailable on this host")

    with pytest.raises(ValueError, match="lexical regular single-link"):
        _verified_artifact_snapshot(linked)


def test_release_smoke_detects_artifact_replacement(tmp_path: Path) -> None:
    artifact = tmp_path / WHEEL
    artifact.write_bytes(b"first-wheel")
    snapshot = _verified_artifact_snapshot(artifact)

    artifact.unlink()
    artifact.write_bytes(b"replacement-wheel")

    with pytest.raises(ValueError, match="changed during smoke install"):
        _assert_artifact_snapshot(artifact, snapshot)


def test_release_smoke_rejects_linked_distribution_directory(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / WHEEL).write_bytes(b"wheel")
    (dist / SDIST).write_bytes(b"sdist")
    linked = tmp_path / "linked-dist"
    try:
        linked.symlink_to(dist, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this host")

    with pytest.raises(ValueError, match="lexical real directory"):
        _release_artifacts(linked)


def test_release_smoke_accepts_one_real_wheel_and_sdist(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / WHEEL
    sdist = dist / SDIST
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")

    assert _release_artifacts(dist) == [wheel, sdist]
    assert _verified_artifact_snapshot(wheel)[0] == wheel.resolve()
    assert _verified_artifact_snapshot(sdist)[0] == sdist.resolve()


def test_release_smoke_offline_build_inputs_must_be_a_pair(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    requirements = tmp_path / "build-requirements.txt"
    requirements.write_text(
        "setuptools==83.0.0 --hash=sha256:" + "0" * 64 + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be supplied together"):
        _offline_build_inputs(wheelhouse, None)
    with pytest.raises(ValueError, match="must be supplied together"):
        _offline_build_inputs(None, requirements)


def test_release_smoke_builds_offline_bootstrap_command_with_hash_enforcement(
    tmp_path: Path,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    requirements = tmp_path / "build-requirements.txt"
    requirements.write_text(
        "setuptools==83.0.0 --hash=sha256:" + "0" * 64 + "\n",
        encoding="utf-8",
    )
    offline_inputs = _offline_build_inputs(wheelhouse, requirements)
    assert offline_inputs is not None
    python = tmp_path / "venv" / "python"

    command, mode = _build_tool_install_command(python, offline_inputs)

    assert mode == "hash-pinned-offline-wheelhouse"
    assert command == [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-index",
        "--find-links",
        str(wheelhouse.resolve()),
        "--only-binary=:all:",
        "--require-hashes",
        "-r",
        str(requirements.resolve()),
    ]
    assert "setuptools>=77" not in command
    assert "wheel>=0.41" not in command


def test_release_smoke_preserves_explicit_online_bootstrap_diagnostic(tmp_path: Path) -> None:
    python = tmp_path / "venv" / "python"

    command, mode = _build_tool_install_command(python, None)

    assert mode == "online-lower-bounds"
    assert command[-2:] == ["setuptools>=77", "wheel>=0.41"]
    assert "--no-index" not in command
    assert "--require-hashes" not in command


def test_release_smoke_artifact_install_never_contacts_an_index(tmp_path: Path) -> None:
    python = tmp_path / "venv" / "python"
    wheel = _artifact_install_command(python, tmp_path / WHEEL, kind="wheel")
    sdist = _artifact_install_command(python, tmp_path / SDIST, kind="sdist")

    assert "--no-index" in wheel
    assert "--no-index" in sdist
    assert "--no-deps" in wheel
    assert "--no-deps" in sdist
    assert "--no-build-isolation" not in wheel
    assert "--no-build-isolation" in sdist
