from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts.release_install_smoke import (
    MATERIALIZED_WITNESS_SCHEMA,
    _artifact_install_command,
    _assert_artifact_snapshot,
    _build_tool_install_command,
    _decode_materialized_witness,
    _materialized_context_probe_command,
    _materialized_context_witness,
    _offline_build_inputs,
    _release_artifacts,
    _release_report,
    _require_matching_materialized_witnesses,
    _source_module_root,
    _subprocess_environment,
    _venv_python,
    _verified_artifact_snapshot,
)

WHEEL = "loss_resistant_context_compiler-0.1.0-py3-none-any.whl"
SDIST = "loss_resistant_context_compiler-0.1.0.tar.gz"


def _sample_materialized_witness() -> dict[str, object]:
    digest = "0" * 64
    return {
        "schema": MATERIALIZED_WITNESS_SCHEMA,
        "allocation_plan_sha256": digest,
        "context_bundle_sha256": digest,
        "current_turn_id": "message-4",
        "current_turn_sha256": digest,
        "final_provider_recount_required": True,
        "fixed_input_sha256": digest,
        "materialization_sha256": digest,
        "protected_state_sha256": digest,
        "provider_execution_ready": False,
        "prototype_sha256": digest,
        "recent_message_ids": ["message-3"],
        "recent_messages_sha256": digest,
        "retrieval_result_sha256": None,
        "runtime_sha256": digest,
    }


def _encoded_witness(value: object) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def _create_test_environment_with_current_pip(path: Path) -> Path:
    environment = dict(os.environ)
    environment.pop("SSLKEYLOGFILE", None)
    subprocess.run(
        [
            sys._base_executable,
            "-m",
            "venv",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return _venv_python(path)


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
        "--isolated",
        "install",
        "--disable-pip-version-check",
        "--no-deps",
        "--no-index",
        "--find-links",
        str(wheelhouse.resolve()),
        "--only-binary=:all:",
        "--require-hashes",
        "--force-reinstall",
        "-r",
        str(requirements.resolve()),
    ]
    for option in (
        "--isolated",
        "--force-reinstall",
        "--no-deps",
        "--no-index",
        "--only-binary=:all:",
        "--require-hashes",
    ):
        assert command.count(option) == 1
    assert "setuptools>=77" not in command
    assert "wheel>=0.41" not in command


def test_release_smoke_offline_bootstrap_replaces_modified_same_version_package(
    tmp_path: Path,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheel = wheelhouse / "sentinel_pkg-1.0-py3-none-any.whl"
    members = {
        "sentinel_pkg/__init__.py": b'MARKER = "wheel"\n',
        "sentinel_pkg-1.0.dist-info/METADATA": (
            b"Metadata-Version: 2.4\nName: sentinel-pkg\nVersion: 1.0\n\n"
        ),
        "sentinel_pkg-1.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\n"
            b"Generator: ctxc-test\n"
            b"Root-Is-Purelib: true\n"
            b"Tag: py3-none-any\n\n"
        ),
    }
    record_rows = []
    for name, payload in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
        encoded = digest.rstrip(b"=").decode("ascii")
        record_rows.append(f"{name},sha256={encoded},{len(payload)}\n")
    record_rows.append("sentinel_pkg-1.0.dist-info/RECORD,,\n")
    members["sentinel_pkg-1.0.dist-info/RECORD"] = "".join(record_rows).encode("ascii")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name, payload in members.items():
            output.writestr(name, payload)
    wheel.write_bytes(archive.getvalue())

    requirements = tmp_path / "requirements-build.lock"
    requirements.write_text(
        f"sentinel-pkg==1.0 --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}\n",
        encoding="ascii",
        newline="\n",
    )
    offline_inputs = _offline_build_inputs(wheelhouse, requirements)
    assert offline_inputs is not None
    environment = tmp_path / "environment"
    python = _create_test_environment_with_current_pip(environment)
    command, mode = _build_tool_install_command(python, offline_inputs)
    assert mode == "hash-pinned-offline-wheelhouse"
    subprocess_environment = dict(os.environ)
    subprocess_environment.pop("SSLKEYLOGFILE", None)

    subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=subprocess_environment,
    )
    located = subprocess.run(
        [
            str(python),
            "-c",
            "import sentinel_pkg; print(sentinel_pkg.__file__)",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=subprocess_environment,
    )
    module_path = Path(located.stdout.strip())
    original = module_path.read_bytes()
    module_path.write_bytes(original + b'SENTINEL = "modified"\n')
    assert b"SENTINEL" in module_path.read_bytes()

    subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=subprocess_environment,
    )

    assert module_path.read_bytes() == original


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
    assert wheel.count("--no-compile") == 1
    assert sdist.count("--no-compile") == 1
    assert "--no-build-isolation" not in wheel
    assert "--no-build-isolation" in sdist


def test_release_smoke_subprocess_environment_is_standalone_and_no_bytecode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONHOME", "private-home")
    monkeypatch.setenv("PYTHONPATH", "private-path")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "0")

    environment = _subprocess_environment()

    assert "PYTHONHOME" not in environment
    assert "PYTHONPATH" not in environment
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert os.environ["PYTHONHOME"] == "private-home"
    assert os.environ["PYTHONPATH"] == "private-path"


def test_materialized_context_probe_command_is_isolated_and_module_qualified(
    tmp_path: Path,
) -> None:
    python = tmp_path / "python"
    command = _materialized_context_probe_command(
        python,
        module_root=tmp_path / "src",
        require_standalone=True,
    )

    assert command[:4] == [str(python), "-I", "-B", "-c"]
    assert command[-2:] == ["1", str(tmp_path / "src")]
    script = command[4]
    assert "from context_compiler.context_window import" in script
    assert "from context_compiler.materialized_window import" in script
    assert "from context_compiler.connector import" in script
    assert "from context_compiler.models import" in script
    assert "from context_compiler import" not in script
    assert "__pycache__" in script


def test_materialized_context_source_witness_is_byte_deterministic() -> None:
    source_root = _source_module_root()

    first = _materialized_context_witness(
        Path(sys.executable),
        module_root=source_root,
        require_standalone=False,
    )
    second = _materialized_context_witness(
        Path(sys.executable),
        module_root=source_root,
        require_standalone=False,
    )

    assert first == second
    assert first["schema"] == MATERIALIZED_WITNESS_SCHEMA
    assert first["current_turn_id"] == "message-4"
    assert first["recent_message_ids"] == ["message-3"]
    assert first["final_provider_recount_required"] is True
    assert first["provider_execution_ready"] is False
    assert first["retrieval_result_sha256"] is None


def test_materialized_context_witness_requires_canonical_exact_fields() -> None:
    valid = _sample_materialized_witness()

    assert _decode_materialized_witness(_encoded_witness(valid)) == valid

    unknown = dict(valid)
    unknown["unexpected"] = None
    with pytest.raises(ValueError, match="fields are invalid"):
        _decode_materialized_witness(_encoded_witness(unknown))

    noncanonical = json.dumps(valid, sort_keys=False) + "\n"
    with pytest.raises(ValueError, match="not canonical"):
        _decode_materialized_witness(noncanonical)

    bool_substitution = dict(valid)
    bool_substitution["final_provider_recount_required"] = 1
    with pytest.raises(ValueError, match="final provider recount"):
        _decode_materialized_witness(_encoded_witness(bool_substitution))

    with pytest.raises(ValueError, match="byte limit"):
        _decode_materialized_witness("x" * (4 * 1024 + 1))


def test_release_smoke_compares_source_wheel_and_sdist_witnesses() -> None:
    source = _sample_materialized_witness()
    _require_matching_materialized_witnesses(
        source,
        [dict(source), dict(source)],
    )

    changed = dict(source)
    changed["prototype_sha256"] = "1" * 64
    with pytest.raises(RuntimeError, match="differs from source"):
        _require_matching_materialized_witnesses(
            source,
            [dict(source), changed],
        )
    with pytest.raises(ValueError, match="exactly two"):
        _require_matching_materialized_witnesses(source, [dict(source)])


def test_release_smoke_report_shape_remains_compatible() -> None:
    artifacts = [
        {
            "artifact": WHEEL,
            "kind": "wheel",
            "status": "passed",
            "build_bootstrap": "not-applicable",
        },
        {
            "artifact": SDIST,
            "kind": "sdist",
            "status": "passed",
            "build_bootstrap": "hash-pinned-offline-wheelhouse",
        },
    ]

    report = _release_report(13, artifacts)

    assert set(report) == {"schema", "schema_count", "artifacts"}
    assert report["schema"] == "ctxc-release-install-smoke-0.1"
    assert report["schema_count"] == 13
    assert report["artifacts"] == artifacts
    assert all(
        set(value) == {"artifact", "kind", "status", "build_bootstrap"}
        for value in report["artifacts"]
    )
