from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.release_artifact_manifest as release_module
from scripts.release_artifact_manifest import (
    ReleaseArtifactError,
    create_manifest,
    main,
    verify_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40
WHEEL = "loss_resistant_context_compiler-0.1.1a19-py3-none-any.whl"
SDIST = "loss_resistant_context_compiler-0.1.1a19.tar.gz"


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / WHEEL).write_bytes(b"wheel-bytes")
    (dist / SDIST).write_bytes(b"sdist-bytes")
    return dist


def test_release_artifact_manifest_round_trip_binds_both_archives(
    tmp_path: Path,
) -> None:
    dist = _dist(tmp_path)
    manifest, checksums = create_manifest(dist, revision=REVISION)
    manifest_path = tmp_path / "release-artifacts.json"
    checksums_path = tmp_path / "SHA256SUMS"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    checksums_path.write_text(checksums, encoding="utf-8", newline="\n")

    verified = verify_manifest(
        dist,
        manifest_path=manifest_path,
        checksums_path=checksums_path,
        expected_manifest_sha256=manifest["manifest_sha256"],
    )

    assert verified == manifest
    assert [artifact["kind"] for artifact in manifest["artifacts"]] == [
        "wheel",
        "sdist",
    ]
    assert manifest["checksums_sha256"] == hashlib.sha256(checksums.encode("utf-8")).hexdigest()
    assert checksums == (
        f"{hashlib.sha256(b'wheel-bytes').hexdigest()}  {WHEEL}\n"
        f"{hashlib.sha256(b'sdist-bytes').hexdigest()}  {SDIST}\n"
    )


def test_release_artifact_manifest_rejects_archive_substitution(
    tmp_path: Path,
) -> None:
    dist = _dist(tmp_path)
    manifest, checksums = create_manifest(dist, revision=REVISION)
    manifest_path = tmp_path / "release-artifacts.json"
    checksums_path = tmp_path / "SHA256SUMS"
    manifest_path.write_text(
        json.dumps(manifest),
        encoding="utf-8",
        newline="\n",
    )
    checksums_path.write_text(checksums, encoding="utf-8", newline="\n")
    (dist / WHEEL).write_bytes(b"substituted-wheel")

    with pytest.raises(
        ReleaseArtifactError,
        match="bytes do not match",
    ):
        verify_manifest(
            dist,
            manifest_path=manifest_path,
            checksums_path=checksums_path,
        )


def test_release_artifact_manifest_rejects_bad_identity_and_empty_archive(
    tmp_path: Path,
) -> None:
    dist = _dist(tmp_path)
    (dist / WHEEL).write_bytes(b"")

    with pytest.raises(ReleaseArtifactError, match="cannot be empty"):
        create_manifest(dist, revision=REVISION)
    with pytest.raises(ReleaseArtifactError, match="revision"):
        create_manifest(dist, revision="main")


def test_release_artifact_cli_refuses_to_overwrite_evidence(
    tmp_path: Path,
) -> None:
    dist = _dist(tmp_path)
    manifest_path = tmp_path / "release-artifacts.json"
    checksums_path = tmp_path / "SHA256SUMS"
    arguments = [
        "create",
        "--dist-dir",
        str(dist),
        "--revision",
        REVISION,
        "--manifest-out",
        str(manifest_path),
        "--checksums-out",
        str(checksums_path),
    ]

    assert main(arguments) == 0
    original_manifest = manifest_path.read_bytes()
    original_checksums = checksums_path.read_bytes()
    assert main(arguments) == 2
    assert manifest_path.read_bytes() == original_manifest
    assert checksums_path.read_bytes() == original_checksums


@pytest.mark.parametrize(
    "invalid_wheel",
    [
        "loss_resistant_context_compiler-0.1.1a19-not-a-valid-wheel.whl",
        "loss_resistant_context_compiler-0.1.1a19-cp311-cp311-win_amd64.whl",
        "loss_resistant_context_compiler-0.1.1a19-1-py3-none-any.whl",
    ],
)
def test_release_artifact_manifest_rejects_noncanonical_wheel_names(
    tmp_path: Path,
    invalid_wheel: str,
) -> None:
    dist = _dist(tmp_path)
    (dist / WHEEL).rename(dist / invalid_wheel)

    with pytest.raises(ReleaseArtifactError, match="filename must be"):
        create_manifest(dist, revision=REVISION)


def test_release_artifact_manifest_rejects_linked_distribution_directory(
    tmp_path: Path,
) -> None:
    dist = _dist(tmp_path)
    linked = tmp_path / "linked-dist"
    try:
        linked.symlink_to(dist, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are unavailable on this host")

    with pytest.raises(ReleaseArtifactError, match="must be a real directory"):
        create_manifest(linked, revision=REVISION)


def test_release_artifact_manifest_rejects_distribution_junction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist = _dist(tmp_path)
    monkeypatch.setattr(
        release_module.os.path,
        "isjunction",
        lambda _path: True,
        raising=False,
    )

    with pytest.raises(ReleaseArtifactError, match="must be a real directory"):
        create_manifest(dist, revision=REVISION)


def test_release_artifact_manifest_revalidates_after_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist = _dist(tmp_path)
    wheel = dist / WHEEL
    real_hash = release_module.hash_bounded_regular_file
    replaced = False

    def replace_after_hash(path: str | Path, **kwargs: object):
        nonlocal replaced
        evidence = real_hash(path, **kwargs)
        if Path(path) == wheel and not replaced:
            replaced = True
            wheel.write_bytes(b"replacement-wheel")
        return evidence

    monkeypatch.setattr(
        release_module,
        "hash_bounded_regular_file",
        replace_after_hash,
    )

    with pytest.raises(ReleaseArtifactError, match="changed between validation passes"):
        create_manifest(dist, revision=REVISION)


def test_release_artifact_cli_retains_partial_pair_as_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist = _dist(tmp_path)
    manifest_path = tmp_path / "release-artifacts.json"
    checksums_path = tmp_path / "SHA256SUMS"
    real_atomic_write = release_module.atomic_write_text
    writes = 0

    def fail_second_write(*args: object, **kwargs: object) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("injected manifest write failure")
        real_atomic_write(*args, **kwargs)

    monkeypatch.setattr(release_module, "atomic_write_text", fail_second_write)

    assert (
        main(
            [
                "create",
                "--dist-dir",
                str(dist),
                "--revision",
                REVISION,
                "--manifest-out",
                str(manifest_path),
                "--checksums-out",
                str(checksums_path),
            ]
        )
        == 2
    )
    assert checksums_path.is_file()
    assert not manifest_path.exists()


def test_release_artifact_module_cli_round_trip(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    manifest_path = tmp_path / "release-artifacts.json"
    checksums_path = tmp_path / "SHA256SUMS"
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.release_artifact_manifest",
            "create",
            "--dist-dir",
            str(dist),
            "--revision",
            REVISION,
            "--manifest-out",
            str(manifest_path),
            "--checksums-out",
            str(checksums_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert create.returncode == 0, create.stderr
    created = json.loads(create.stdout)

    verify = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.release_artifact_manifest",
            "verify",
            "--dist-dir",
            str(dist),
            "--manifest",
            str(manifest_path),
            "--checksums",
            str(checksums_path),
            "--expected-manifest-sha256",
            created["manifest_sha256"],
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert verify.returncode == 0, verify.stderr
    assert json.loads(verify.stdout)["status"] == "verified"


def test_release_evidence_outputs_preserve_lexical_destinations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist = _dist(tmp_path)
    manifest, checksums = create_manifest(dist, revision=REVISION)
    manifest_path = tmp_path / "release-artifacts.json"
    checksums_path = tmp_path / "SHA256SUMS"
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    real_resolve = Path.resolve

    def redirect_output_resolution(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> Path:
        if path in {manifest_path, checksums_path}:
            return redirected / path.name
        return real_resolve(path, *args, **kwargs)

    writes: list[Path] = []

    def capture_write(path: str | Path, _value: str, **_kwargs: object) -> None:
        writes.append(Path(path))

    monkeypatch.setattr(Path, "resolve", redirect_output_resolution)
    monkeypatch.setattr(release_module, "atomic_write_text", capture_write)

    release_module._write_evidence(
        manifest,
        checksums,
        manifest_out=manifest_path,
        checksums_out=checksums_path,
    )

    assert writes == [checksums_path, manifest_path]
