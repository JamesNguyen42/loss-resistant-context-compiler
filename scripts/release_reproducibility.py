"""Compare two release build outputs and retain an exact reproducibility report."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import stat
import sys
import tarfile
import zipfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from benchmarks.json_io import StrictJsonError, hash_bounded_regular_file
from context_compiler.atomic import atomic_write_text
from scripts.release_artifact_manifest import ReleaseArtifactError, create_manifest

REPORT_SCHEMA = "ctxc-release-reproducibility-report-0.1"
_REVISION_PLACEHOLDER = "0" * 40
_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
_MAX_MEMBER_BYTES = 256 * 1024 * 1024
_MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
_MAX_MEMBERS = 20_000
_MAX_MEMBER_NAME_CHARS = 4096
_MAX_PAX_FIELDS = 32
_MAX_PAX_FIELD_CHARS = 4096
_CHUNK_BYTES = 1024 * 1024


class ReleaseReproducibilityError(ValueError):
    """Two candidate build outputs cannot establish byte reproducibility."""


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_member_name(name: str, *, label: str) -> tuple[str, ...]:
    if not isinstance(name, str) or not name or len(name) > _MAX_MEMBER_NAME_CHARS:
        raise ReleaseReproducibilityError(f"{label} has an invalid member name")
    if "\\" in name or "\x00" in name or name.startswith("/"):
        raise ReleaseReproducibilityError(f"{label} has an unsafe member name: {name!r}")
    parts = PurePosixPath(name).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ReleaseReproducibilityError(f"{label} has an unsafe member name: {name!r}")
    return parts


def _bounded_stream_sha256(stream: BinaryIO, *, expected_bytes: int, label: str) -> str:
    if expected_bytes < 0 or expected_bytes > _MAX_MEMBER_BYTES:
        raise ReleaseReproducibilityError(f"{label} exceeds the per-member byte limit")
    digest = hashlib.sha256()
    observed = 0
    while True:
        chunk = stream.read(min(_CHUNK_BYTES, expected_bytes - observed + 1))
        if not chunk:
            break
        observed += len(chunk)
        if observed > expected_bytes:
            raise ReleaseReproducibilityError(f"{label} expanded beyond its declared size")
        digest.update(chunk)
    if observed != expected_bytes:
        raise ReleaseReproducibilityError(
            f"{label} expanded to {observed} bytes, expected {expected_bytes}"
        )
    return digest.hexdigest()


def _validate_archive_file(path: Path, *, label: str) -> None:
    try:
        evidence = hash_bounded_regular_file(
            path,
            max_bytes=_MAX_ARCHIVE_BYTES,
            label=label,
        )
    except StrictJsonError as exc:
        raise ReleaseReproducibilityError(str(exc)) from exc
    if evidence.byte_count <= 0:
        raise ReleaseReproducibilityError(f"{label} cannot be empty")


def _pax_metadata(member: tarfile.TarInfo, *, label: str) -> list[list[str]]:
    fields = member.pax_headers
    if len(fields) > _MAX_PAX_FIELDS:
        raise ReleaseReproducibilityError(f"{label} has too many PAX metadata fields")
    result: list[list[str]] = []
    for key, value in sorted(fields.items()):
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or len(key) > _MAX_PAX_FIELD_CHARS
            or len(value) > _MAX_PAX_FIELD_CHARS
        ):
            raise ReleaseReproducibilityError(f"{label} has oversized PAX metadata")
        result.append([key, value])
    return result


def _sdist_inventory(path: Path) -> list[dict[str, Any]]:
    label = f"source distribution {path.name}"
    _validate_archive_file(path, label=label)
    expected_root = path.name.removesuffix(".tar.gz")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    expanded_bytes = 0
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > _MAX_MEMBERS:
                raise ReleaseReproducibilityError(
                    f"{label} member count is outside the supported range"
                )
            for member in members:
                member_label = f"{label} member {member.name!r}"
                parts = _safe_member_name(member.name, label=label)
                if parts[0] != expected_root:
                    raise ReleaseReproducibilityError(
                        f"{label} member is outside the canonical root: {member.name!r}"
                    )
                if member.name in seen:
                    raise ReleaseReproducibilityError(
                        f"{label} contains a duplicate member: {member.name!r}"
                    )
                seen.add(member.name)
                if not isinstance(member.mtime, (int, float)) or not math.isfinite(
                    float(member.mtime)
                ):
                    raise ReleaseReproducibilityError(f"{member_label} has an invalid mtime")
                if member.isdir():
                    member_type = "directory"
                    content_sha256 = None
                    if member.size != 0:
                        raise ReleaseReproducibilityError(
                            f"{member_label} has nonzero directory content"
                        )
                elif member.isfile():
                    member_type = "file"
                    expanded_bytes += member.size
                    if expanded_bytes > _MAX_EXPANDED_BYTES:
                        raise ReleaseReproducibilityError(
                            f"{label} exceeds the expanded byte limit"
                        )
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise ReleaseReproducibilityError(f"{member_label} content is unavailable")
                    with extracted:
                        content_sha256 = _bounded_stream_sha256(
                            extracted,
                            expected_bytes=member.size,
                            label=member_label,
                        )
                else:
                    raise ReleaseReproducibilityError(
                        f"{label} contains a link or special member: {member.name!r}"
                    )
                records.append(
                    {
                        "name": member.name,
                        "type": member_type,
                        "size": member.size,
                        "sha256": content_sha256,
                        "metadata": {
                            "mode": member.mode,
                            "mtime": member.mtime,
                            "uid": member.uid,
                            "gid": member.gid,
                            "uname": member.uname,
                            "gname": member.gname,
                            "pax_headers": _pax_metadata(member, label=member_label),
                        },
                    }
                )
    except (EOFError, OSError, tarfile.TarError) as exc:
        raise ReleaseReproducibilityError(f"{label} is not a valid bounded tar.gz archive") from exc
    required = {
        expected_root,
        f"{expected_root}/PKG-INFO",
        f"{expected_root}/pyproject.toml",
    }
    if not required <= seen:
        raise ReleaseReproducibilityError(
            f"{label} is missing its root directory, PKG-INFO, or pyproject.toml"
        )
    return sorted(records, key=lambda item: str(item["name"]))


def _wheel_inventory(path: Path) -> list[dict[str, Any]]:
    label = f"wheel {path.name}"
    _validate_archive_file(path, label=label)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    expanded_bytes = 0
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if not members or len(members) > _MAX_MEMBERS:
                raise ReleaseReproducibilityError(
                    f"{label} member count is outside the supported range"
                )
            for member in members:
                member_label = f"{label} member {member.filename!r}"
                _safe_member_name(member.filename.rstrip("/"), label=label)
                if member.filename in seen:
                    raise ReleaseReproducibilityError(
                        f"{label} contains a duplicate member: {member.filename!r}"
                    )
                seen.add(member.filename)
                unix_mode = (member.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(unix_mode):
                    raise ReleaseReproducibilityError(
                        f"{label} contains a symbolic-link member: {member.filename!r}"
                    )
                if member.is_dir():
                    member_type = "directory"
                    content_sha256 = None
                    if member.file_size != 0:
                        raise ReleaseReproducibilityError(
                            f"{member_label} has nonzero directory content"
                        )
                else:
                    member_type = "file"
                    expanded_bytes += member.file_size
                    if expanded_bytes > _MAX_EXPANDED_BYTES:
                        raise ReleaseReproducibilityError(
                            f"{label} exceeds the expanded byte limit"
                        )
                    with archive.open(member, mode="r") as extracted:
                        content_sha256 = _bounded_stream_sha256(
                            extracted,
                            expected_bytes=member.file_size,
                            label=member_label,
                        )
                records.append(
                    {
                        "name": member.filename,
                        "type": member_type,
                        "size": member.file_size,
                        "sha256": content_sha256,
                        "metadata": {
                            "date_time": list(member.date_time),
                            "compress_type": member.compress_type,
                            "external_attr": member.external_attr,
                            "create_system": member.create_system,
                            "flag_bits": member.flag_bits,
                        },
                    }
                )
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ReleaseReproducibilityError(f"{label} is not a valid bounded wheel archive") from exc
    dist_info = [name for name in seen if ".dist-info/" in name]
    required_suffixes = (".dist-info/METADATA", ".dist-info/WHEEL", ".dist-info/RECORD")
    if any(not any(name.endswith(suffix) for name in dist_info) for suffix in required_suffixes):
        raise ReleaseReproducibilityError(f"{label} is missing mandatory dist-info members")
    return sorted(records, key=lambda item: str(item["name"]))


def _exact_file_equal(first: Path, second: Path, *, expected_bytes: int) -> bool:
    if expected_bytes < 0 or expected_bytes > _MAX_ARCHIVE_BYTES:
        raise ReleaseReproducibilityError("release artifact size is outside the supported range")
    observed = 0
    with first.open("rb") as left, second.open("rb") as right:
        while True:
            left_chunk = left.read(_CHUNK_BYTES)
            right_chunk = right.read(_CHUNK_BYTES)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return observed == expected_bytes
            observed += len(left_chunk)
            if observed > expected_bytes:
                raise ReleaseReproducibilityError(
                    "release artifact changed while comparing exact bytes"
                )


def _first_inventory_mismatch(
    first: list[dict[str, Any]],
    second: list[dict[str, Any]],
    *,
    archive_kind: str,
) -> dict[str, Any]:
    first_by_name = {str(item["name"]): item for item in first}
    second_by_name = {str(item["name"]): item for item in second}
    if set(first_by_name) != set(second_by_name):
        return {
            "scope": "member_set",
            "first_only": sorted(set(first_by_name) - set(second_by_name))[:16],
            "second_only": sorted(set(second_by_name) - set(first_by_name))[:16],
        }
    for name in sorted(first_by_name):
        left = first_by_name[name]
        right = second_by_name[name]
        content_fields = [
            field for field in ("type", "size", "sha256") if left[field] != right[field]
        ]
        if content_fields:
            return {
                "scope": "member_content",
                "member": name,
                "differing_fields": content_fields,
            }
        if left["metadata"] != right["metadata"]:
            metadata_fields = sorted(
                field
                for field in set(left["metadata"]) | set(right["metadata"])
                if left["metadata"].get(field) != right["metadata"].get(field)
            )
            return {
                "scope": f"{archive_kind}_metadata",
                "member": name,
                "differing_fields": metadata_fields,
            }
    return {"scope": f"{archive_kind}_container_encoding"}


def _snapshot(dist_dir: str | Path) -> tuple[dict[str, dict[str, Any]], dict[str, Path]]:
    directory = Path(dist_dir)
    try:
        manifest, _checksums = create_manifest(directory, revision=_REVISION_PLACEHOLDER)
    except (OSError, ReleaseArtifactError, StrictJsonError, ValueError) as exc:
        raise ReleaseReproducibilityError(str(exc)) from exc
    records = {str(item["kind"]): dict(item) for item in manifest["artifacts"]}
    paths = {
        kind: directory.expanduser().absolute() / str(record["filename"])
        for kind, record in records.items()
    }
    return records, paths


def compare_release_builds(first_dist: str | Path, second_dist: str | Path) -> dict[str, Any]:
    """Compare two exact release artifact pairs and return a self-hashed report."""

    first_records, first_paths = _snapshot(first_dist)
    second_records, second_paths = _snapshot(second_dist)
    artifacts: list[dict[str, Any]] = []
    for kind in ("sdist", "wheel"):
        first_record = first_records[kind]
        second_record = second_records[kind]
        if first_record["filename"] != second_record["filename"]:
            raise ReleaseReproducibilityError(f"{kind} filenames differ between builds")
        if kind == "sdist":
            first_inventory = _sdist_inventory(first_paths[kind])
            second_inventory = _sdist_inventory(second_paths[kind])
            archive_kind = "tar"
        else:
            first_inventory = _wheel_inventory(first_paths[kind])
            second_inventory = _wheel_inventory(second_paths[kind])
            archive_kind = "zip"
        exact = first_record["bytes"] == second_record["bytes"] and _exact_file_equal(
            first_paths[kind],
            second_paths[kind],
            expected_bytes=int(first_record["bytes"]),
        )
        artifact: dict[str, Any] = {
            "filename": first_record["filename"],
            "kind": kind,
            "first_bytes": first_record["bytes"],
            "second_bytes": second_record["bytes"],
            "first_sha256": first_record["sha256"],
            "second_sha256": second_record["sha256"],
            "byte_identical": exact,
        }
        if not exact:
            artifact["first_mismatch"] = _first_inventory_mismatch(
                first_inventory,
                second_inventory,
                archive_kind=archive_kind,
            )
        artifacts.append(artifact)
    final_first_records, _ = _snapshot(first_dist)
    final_second_records, _ = _snapshot(second_dist)
    if final_first_records != first_records or final_second_records != second_records:
        raise ReleaseReproducibilityError("release artifacts changed during comparison")
    unsigned: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "status": "passed" if all(item["byte_identical"] for item in artifacts) else "failed",
        "artifacts": artifacts,
    }
    return {**unsigned, "report_sha256": _canonical_sha256(unsigned)}


def _failure_report(message: str) -> dict[str, Any]:
    unsigned = {
        "schema": REPORT_SCHEMA,
        "status": "failed",
        "artifacts": [],
        "error": message,
    }
    return {**unsigned, "report_sha256": _canonical_sha256(unsigned)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-dist", type=Path, required=True)
    parser.add_argument("--second-dist", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = compare_release_builds(args.first_dist, args.second_dist)
    except (OSError, ReleaseReproducibilityError, TypeError, ValueError) as exc:
        report = _failure_report(str(exc))
    try:
        atomic_write_text(
            args.json_out,
            json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            overwrite=False,
        )
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write(f"release reproducibility report: {exc}\n")
        return 2
    sys.stdout.write(
        json.dumps(
            {
                "schema": REPORT_SCHEMA,
                "status": report["status"],
                "report_sha256": report["report_sha256"],
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
