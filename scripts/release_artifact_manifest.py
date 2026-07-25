"""Create or verify bounded SHA-256 evidence for one wheel and one sdist."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benchmarks.json_io import (
    StrictJsonError,
    StrictJsonLimits,
    hash_bounded_regular_file,
    load_strict_json_file,
)
from context_compiler import __version__
from context_compiler.atomic import atomic_write_text

DISTRIBUTION = "loss-resistant-context-compiler"
NORMALIZED_DISTRIBUTION = "loss_resistant_context_compiler"
MANIFEST_SCHEMA = "ctxc-release-artifact-manifest-0.1"
_MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
_MAX_EVIDENCE_BYTES = 1024 * 1024
_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,254}\Z")
_MANIFEST_LIMITS = StrictJsonLimits(
    max_bytes=_MAX_EVIDENCE_BYTES,
    max_line_chars=256 * 1024,
    max_depth=16,
)
_TOP_LEVEL_FIELDS = {
    "schema",
    "distribution",
    "version",
    "revision",
    "checksum_algorithm",
    "checksums_sha256",
    "artifacts",
    "manifest_sha256",
}
_ARTIFACT_FIELDS = {"filename", "kind", "bytes", "sha256"}


class ReleaseArtifactError(ValueError):
    """Release archives or their retained checksum evidence are invalid."""


def _canonical_sha256(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReleaseArtifactError(
            "release artifact manifest is not canonical finite JSON"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _artifact_kind(path: Path) -> str:
    if path.name.endswith(".whl"):
        return "wheel"
    if path.name.endswith(".tar.gz"):
        return "sdist"
    raise ReleaseArtifactError(f"unsupported release artifact: {path.name}")


def _expected_filename(kind: str) -> str:
    if kind == "wheel":
        return f"{NORMALIZED_DISTRIBUTION}-{__version__}-py3-none-any.whl"
    return f"{NORMALIZED_DISTRIBUTION}-{__version__}.tar.gz"


def _path_identity(path_stat: os.stat_result) -> tuple[int, int]:
    return path_stat.st_dev, path_stat.st_ino


def _artifact_paths(dist_dir: Path) -> list[Path]:
    lexical = dist_dir.expanduser().absolute()
    try:
        lexical_stat = lexical.lstat()
    except OSError as exc:
        raise ReleaseArtifactError(
            f"release artifact directory does not exist: {dist_dir}"
        ) from exc
    is_junction = bool(hasattr(os.path, "isjunction") and os.path.isjunction(lexical))
    if not stat.S_ISDIR(lexical_stat.st_mode) or lexical.is_symlink() or is_junction:
        raise ReleaseArtifactError(
            f"release artifact directory must be a real directory: {lexical}"
        )
    try:
        directory = lexical.resolve(strict=True)
        resolved_stat = directory.stat()
    except OSError as exc:
        raise ReleaseArtifactError(
            f"release artifact directory cannot be resolved: {lexical}"
        ) from exc
    if _path_identity(lexical_stat) != _path_identity(resolved_stat):
        raise ReleaseArtifactError(f"release artifact directory changed while resolving: {lexical}")
    candidates = sorted(directory.glob("*.whl")) + sorted(directory.glob("*.tar.gz"))
    kinds = [_artifact_kind(path) for path in candidates]
    if kinds.count("wheel") != 1 or kinds.count("sdist") != 1:
        raise ReleaseArtifactError(
            "release evidence requires exactly one wheel and one source "
            f"distribution: {[path.name for path in candidates]}"
        )
    try:
        final_stat = lexical.lstat()
    except OSError as exc:
        raise ReleaseArtifactError(
            f"release artifact directory changed while enumerating: {lexical}"
        ) from exc
    if (
        _path_identity(final_stat) != _path_identity(lexical_stat)
        or not stat.S_ISDIR(final_stat.st_mode)
        or lexical.is_symlink()
        or bool(hasattr(os.path, "isjunction") and os.path.isjunction(lexical))
    ):
        raise ReleaseArtifactError(
            f"release artifact directory changed while enumerating: {lexical}"
        )
    return candidates


def _artifact_records_once(dist_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in _artifact_paths(dist_dir):
        kind = _artifact_kind(path)
        if _FILENAME_RE.fullmatch(path.name) is None:
            raise ReleaseArtifactError(f"release artifact filename is unsafe: {path.name!r}")
        expected_filename = _expected_filename(kind)
        if path.name != expected_filename:
            raise ReleaseArtifactError(
                f"{kind} filename must be {expected_filename!r}: {path.name}"
            )
        try:
            evidence = hash_bounded_regular_file(
                path,
                max_bytes=_MAX_ARTIFACT_BYTES,
                label=f"{kind} release artifact",
            )
        except StrictJsonError as exc:
            raise ReleaseArtifactError(str(exc)) from exc
        if evidence.byte_count <= 0:
            raise ReleaseArtifactError(f"{kind} release artifact cannot be empty")
        records.append(
            {
                "filename": path.name,
                "kind": kind,
                "bytes": evidence.byte_count,
                "sha256": evidence.file_sha256,
            }
        )
    return sorted(records, key=lambda record: str(record["filename"]))


def _artifact_records(dist_dir: Path) -> list[dict[str, Any]]:
    first = _artifact_records_once(dist_dir)
    second = _artifact_records_once(dist_dir)
    if first != second:
        raise ReleaseArtifactError("release artifacts changed between validation passes")
    return second


def _checksums_text(artifacts: Sequence[dict[str, Any]]) -> str:
    return "".join(f"{artifact['sha256']}  {artifact['filename']}\n" for artifact in artifacts)


def create_manifest(
    dist_dir: str | Path,
    *,
    revision: str,
) -> tuple[dict[str, Any], str]:
    """Create in-memory release evidence without writing or publishing it."""

    if not isinstance(revision, str) or _REVISION_RE.fullmatch(revision) is None:
        raise ReleaseArtifactError("release revision must be a lowercase 40-character commit")
    artifacts = _artifact_records(Path(dist_dir))
    checksums = _checksums_text(artifacts)
    unsigned: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "distribution": DISTRIBUTION,
        "version": __version__,
        "revision": revision,
        "checksum_algorithm": "SHA-256",
        "checksums_sha256": hashlib.sha256(checksums.encode("utf-8")).hexdigest(),
        "artifacts": artifacts,
    }
    return {
        **unsigned,
        "manifest_sha256": _canonical_sha256(unsigned),
    }, checksums


def _absolute_lexical_path(path: Path) -> Path:
    """Normalize dot components without following a link at the destination."""

    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _write_evidence(
    manifest: dict[str, Any],
    checksums: str,
    *,
    manifest_out: Path,
    checksums_out: Path,
) -> None:
    manifest_path = _absolute_lexical_path(manifest_out)
    checksums_path = _absolute_lexical_path(checksums_out)
    if manifest_path == checksums_path:
        raise ReleaseArtifactError("manifest and checksum outputs must be different paths")
    for path in (manifest_path, checksums_path):
        if not path.parent.is_dir():
            raise ReleaseArtifactError(
                f"release evidence output directory does not exist: {path.parent}"
            )
        if path.exists():
            raise ReleaseArtifactError(f"refusing to overwrite release evidence: {path}")
    atomic_write_text(checksums_path, checksums, overwrite=False)
    atomic_write_text(
        manifest_path,
        json.dumps(
            manifest,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        overwrite=False,
    )


def _decoded_manifest(path: Path) -> dict[str, Any]:
    try:
        document = load_strict_json_file(
            path,
            limits=_MANIFEST_LIMITS,
            label="release artifact manifest",
        )
    except (OSError, StrictJsonError) as exc:
        raise ReleaseArtifactError(str(exc)) from exc
    payload = document.value
    if not isinstance(payload, dict) or set(payload) != _TOP_LEVEL_FIELDS:
        raise ReleaseArtifactError("release artifact manifest fields do not match the schema")
    if payload["schema"] != MANIFEST_SCHEMA:
        raise ReleaseArtifactError("release artifact manifest schema is invalid")
    if (
        payload["distribution"] != DISTRIBUTION
        or payload["version"] != __version__
        or payload["checksum_algorithm"] != "SHA-256"
    ):
        raise ReleaseArtifactError("release artifact manifest package identity is invalid")
    if (
        not isinstance(payload["revision"], str)
        or _REVISION_RE.fullmatch(payload["revision"]) is None
    ):
        raise ReleaseArtifactError("release artifact manifest revision is invalid")
    for name in ("checksums_sha256", "manifest_sha256"):
        if not isinstance(payload[name], str) or _SHA256_RE.fullmatch(payload[name]) is None:
            raise ReleaseArtifactError(f"release artifact manifest {name} is invalid")
    raw_artifacts = payload["artifacts"]
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) != 2:
        raise ReleaseArtifactError("release artifact manifest must contain two artifacts")
    for index, artifact in enumerate(raw_artifacts):
        if not isinstance(artifact, dict) or set(artifact) != _ARTIFACT_FIELDS:
            raise ReleaseArtifactError(f"release artifact manifest artifacts[{index}] is invalid")
        if (
            not isinstance(artifact["filename"], str)
            or _FILENAME_RE.fullmatch(artifact["filename"]) is None
            or artifact["kind"] not in {"wheel", "sdist"}
            or isinstance(artifact["bytes"], bool)
            or not isinstance(artifact["bytes"], int)
            or artifact["bytes"] <= 0
            or not isinstance(artifact["sha256"], str)
            or _SHA256_RE.fullmatch(artifact["sha256"]) is None
        ):
            raise ReleaseArtifactError(f"release artifact manifest artifacts[{index}] is invalid")
    if [item["filename"] for item in raw_artifacts] != sorted(
        item["filename"] for item in raw_artifacts
    ):
        raise ReleaseArtifactError("release artifact manifest artifacts are not canonical")
    expected_artifacts = {
        (_expected_filename("wheel"), "wheel"),
        (_expected_filename("sdist"), "sdist"),
    }
    observed_artifacts = {(str(item["filename"]), str(item["kind"])) for item in raw_artifacts}
    if observed_artifacts != expected_artifacts:
        raise ReleaseArtifactError("release artifact manifest filenames do not match this package")
    unsigned = dict(payload)
    claimed = unsigned.pop("manifest_sha256")
    if _canonical_sha256(unsigned) != claimed:
        raise ReleaseArtifactError("release artifact manifest SHA-256 mismatch")
    return payload


def verify_manifest(
    dist_dir: str | Path,
    *,
    manifest_path: str | Path,
    checksums_path: str | Path,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Verify archive bytes and checksum evidence against a retained manifest."""

    payload = _decoded_manifest(Path(manifest_path))
    if expected_manifest_sha256 is not None and (
        not isinstance(expected_manifest_sha256, str)
        or _SHA256_RE.fullmatch(expected_manifest_sha256) is None
        or payload["manifest_sha256"] != expected_manifest_sha256
    ):
        raise ReleaseArtifactError("release artifact manifest does not match the trusted SHA-256")
    observed_artifacts = _artifact_records(Path(dist_dir))
    if observed_artifacts != payload["artifacts"]:
        raise ReleaseArtifactError("release artifact bytes do not match the retained manifest")
    expected_checksums = _checksums_text(observed_artifacts)
    expected_checksums_sha256 = hashlib.sha256(expected_checksums.encode("utf-8")).hexdigest()
    if expected_checksums_sha256 != payload["checksums_sha256"]:
        raise ReleaseArtifactError("release artifact checksum digest is inconsistent")
    try:
        checksum_evidence = hash_bounded_regular_file(
            checksums_path,
            max_bytes=_MAX_EVIDENCE_BYTES,
            label="release artifact checksum file",
        )
    except StrictJsonError as exc:
        raise ReleaseArtifactError(str(exc)) from exc
    if (
        checksum_evidence.file_sha256 != expected_checksums_sha256
        or checksum_evidence.byte_count != len(expected_checksums.encode("utf-8"))
    ):
        raise ReleaseArtifactError("release artifact checksum file does not match the manifest")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--dist-dir", type=Path, default=Path("dist"))
    create.add_argument("--revision", required=True)
    create.add_argument("--manifest-out", type=Path, required=True)
    create.add_argument("--checksums-out", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--dist-dir", type=Path, default=Path("dist"))
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--checksums", type=Path, required=True)
    verify.add_argument("--expected-manifest-sha256")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "create":
            manifest, checksums = create_manifest(
                args.dist_dir,
                revision=args.revision,
            )
            _write_evidence(
                manifest,
                checksums,
                manifest_out=args.manifest_out,
                checksums_out=args.checksums_out,
            )
            manifest = verify_manifest(
                args.dist_dir,
                manifest_path=args.manifest_out,
                checksums_path=args.checksums_out,
                expected_manifest_sha256=manifest["manifest_sha256"],
            )
        else:
            manifest = verify_manifest(
                args.dist_dir,
                manifest_path=args.manifest,
                checksums_path=args.checksums,
                expected_manifest_sha256=args.expected_manifest_sha256,
            )
    except (OSError, ReleaseArtifactError, TypeError, ValueError) as exc:
        sys.stderr.write(f"release artifact evidence: {exc}\n")
        return 2
    sys.stdout.write(
        json.dumps(
            {
                "schema": "ctxc-release-artifact-evidence-result-0.1",
                "status": "created" if args.mode == "create" else "verified",
                "manifest_sha256": manifest["manifest_sha256"],
                "revision": manifest["revision"],
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
