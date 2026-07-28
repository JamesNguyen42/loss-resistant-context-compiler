"""Compare two release build outputs and retain an exact reproducibility report."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
import tarfile
import tomllib
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from benchmarks.json_io import (
    StrictJsonError,
    _open_regular_file,
    hash_bounded_regular_file,
)
from context_compiler import __version__ as _CORE_VERSION
from context_compiler.atomic import atomic_write_text
from scripts.release_artifact_manifest import ReleaseArtifactError, create_manifest

REPORT_SCHEMA = "ctxc-release-reproducibility-report-0.1"
PACKAGE_MATRIX_REPORT_SCHEMA = "ctxc-openhands-package-matrix-reproducibility-0.1"
PACKAGE_MATRIX_LANES = (
    "ctxc-openhands-Linux-python-3.12",
    "ctxc-openhands-Linux-python-3.13",
    "ctxc-openhands-macOS-python-3.12",
    "ctxc-openhands-macOS-python-3.13",
    "ctxc-openhands-Windows-python-3.12",
    "ctxc-openhands-Windows-python-3.13",
)
_REVISION_PLACEHOLDER = "0" * 40
_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_UPSTREAM_RESULTS = frozenset({"cancelled", "failure", "skipped", "success"})
_SAFE_MATRIX_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,254}\Z")
_PACKAGE_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}\Z")
_MATRIX_SUPPORT_FILES = (
    "doctor-live-source.json",
    "doctor-source.json",
    "openhands-ci-toolchain.txt",
    "openhands-clean-install-report.json",
)
_MATRIX_ARTIFACT_KEYS = (
    "root_wheel",
    "integration_wheel",
    "integration_sdist",
)
_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
_MAX_MATRIX_SUPPORT_BYTES = 4 * 1024 * 1024
_MAX_PROJECT_METADATA_BYTES = 256 * 1024
_MAX_MATRIX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
_MAX_MATRIX_DIRECTORY_ENTRIES = 64
_MAX_MATRIX_ISSUE_CHARS = 1024
_MAX_MEMBER_BYTES = 256 * 1024 * 1024
_MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
_MAX_MEMBERS = 20_000
_MAX_MEMBER_NAME_CHARS = 4096
_MAX_PAX_FIELDS = 32
_MAX_PAX_FIELD_CHARS = 4096
_CHUNK_BYTES = 1024 * 1024


class ReleaseReproducibilityError(ValueError):
    """Two candidate build outputs cannot establish byte reproducibility."""


@dataclass(frozen=True, slots=True)
class _MatrixSnapshot:
    package_identity: dict[str, str]
    lanes: list[dict[str, Any]]
    paths: dict[str, dict[str, Path]]
    file_snapshots: dict[str, dict[str, tuple[int, ...]]]
    signature: tuple[tuple[Any, ...], ...]


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


def _path_stat_snapshot(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        int(getattr(value, "st_file_attributes", 0) or 0),
    )


def _descriptor_stat_snapshot(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        int(getattr(value, "st_file_attributes", 0) or 0),
    )


def _descriptor_expected_snapshot(path_snapshot: tuple[int, ...]) -> tuple[int, ...]:
    return (*path_snapshot[:6], path_snapshot[7])


def _directory_stat_snapshot(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        int(getattr(value, "st_file_attributes", 0) or 0),
    )


def _is_reparse_or_junction(path: Path, value: os.stat_result) -> bool:
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if int(getattr(value, "st_file_attributes", 0)) & reparse_flag:
        return True
    try:
        return bool(hasattr(os.path, "isjunction") and os.path.isjunction(path))
    except OSError as exc:
        raise ReleaseReproducibilityError(
            "package matrix path could not be checked for directory indirection"
        ) from exc


@contextmanager
def _open_matrix_regular_file(
    path: Path,
    *,
    expected_snapshot: tuple[int, ...],
    label: str,
) -> Iterator[int]:
    try:
        initial = path.lstat()
    except OSError as exc:
        raise ReleaseReproducibilityError(f"{label} changed before exact comparison") from exc
    if (
        _path_stat_snapshot(initial) != expected_snapshot
        or not stat.S_ISREG(initial.st_mode)
        or stat.S_ISLNK(initial.st_mode)
        or initial.st_nlink != 1
        or _is_reparse_or_junction(path, initial)
    ):
        raise ReleaseReproducibilityError(f"{label} changed before exact comparison")
    try:
        with _open_regular_file(path, label=label) as (descriptor, opened_stat):
            if _descriptor_stat_snapshot(opened_stat) != _descriptor_expected_snapshot(
                expected_snapshot
            ):
                raise ReleaseReproducibilityError(
                    f"{label} changed while opening for exact comparison"
                )
            yield descriptor
            try:
                final = path.lstat()
            except OSError as exc:
                raise ReleaseReproducibilityError(
                    f"{label} changed after exact comparison"
                ) from exc
            if _path_stat_snapshot(final) != expected_snapshot:
                raise ReleaseReproducibilityError(f"{label} changed during exact comparison")
    except StrictJsonError as exc:
        raise ReleaseReproducibilityError(
            f"{label} could not be opened as its validated regular file"
        ) from exc


def _exact_matrix_file_equal(
    first: Path,
    second: Path,
    *,
    expected_bytes: int,
    first_snapshot: tuple[int, ...],
    second_snapshot: tuple[int, ...],
) -> bool:
    if expected_bytes <= 0 or expected_bytes > _MAX_ARCHIVE_BYTES:
        raise ReleaseReproducibilityError(
            "package matrix artifact size is outside the supported range"
        )
    observed = 0
    with (
        _open_matrix_regular_file(
            first,
            expected_snapshot=first_snapshot,
            label="reference package artifact",
        ) as left,
        _open_matrix_regular_file(
            second,
            expected_snapshot=second_snapshot,
            label="compared package artifact",
        ) as right,
    ):
        while True:
            read_bytes = min(_CHUNK_BYTES, expected_bytes - observed + 1)
            try:
                left_chunk = os.read(left, read_bytes)
                right_chunk = os.read(right, read_bytes)
            except OSError as exc:
                raise ReleaseReproducibilityError(
                    "package artifact could not be read during exact comparison"
                ) from exc
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return observed == expected_bytes
            observed += len(left_chunk)
            if observed > expected_bytes:
                raise ReleaseReproducibilityError(
                    "package artifact exceeded its validated size during comparison"
                )


def _project_version(path: Path, *, label: str) -> str:
    try:
        with _open_regular_file(path, label=label) as (descriptor, file_stat):
            if file_stat.st_size <= 0 or file_stat.st_size > _MAX_PROJECT_METADATA_BYTES:
                raise ReleaseReproducibilityError(f"{label} size is outside the supported range")
            chunks: list[bytes] = []
            observed = 0
            while True:
                try:
                    block = os.read(
                        descriptor,
                        min(
                            64 * 1024,
                            _MAX_PROJECT_METADATA_BYTES - observed + 1,
                        ),
                    )
                except OSError as exc:
                    raise ReleaseReproducibilityError(f"{label} could not be read") from exc
                if not block:
                    break
                observed += len(block)
                if observed > _MAX_PROJECT_METADATA_BYTES:
                    raise ReleaseReproducibilityError(f"{label} exceeds the supported byte limit")
                chunks.append(block)
            if observed != file_stat.st_size:
                raise ReleaseReproducibilityError(f"{label} did not yield its declared bytes")
    except StrictJsonError as exc:
        raise ReleaseReproducibilityError(f"{label} must be one stable regular file") from exc
    try:
        text = b"".join(chunks).decode("utf-8")
        document = tomllib.loads(text)
    except (RecursionError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseReproducibilityError(f"{label} is not valid UTF-8 TOML") from exc
    project = document.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(version, str) or _PACKAGE_VERSION_RE.fullmatch(version) is None:
        raise ReleaseReproducibilityError(f"{label} has an invalid project version")
    return version


def _configured_package_identity() -> dict[str, str]:
    source_root = Path(__file__).absolute().parent.parent
    core_version = _project_version(
        source_root / "pyproject.toml",
        label="core project metadata",
    )
    if core_version != _CORE_VERSION:
        raise ReleaseReproducibilityError("core project and public API versions do not match")
    integration_version = _project_version(
        source_root / "integrations" / "openhands" / "pyproject.toml",
        label="integration project metadata",
    )
    return {
        "core_distribution": "loss-resistant-context-compiler",
        "core_version": core_version,
        "root_wheel": (f"loss_resistant_context_compiler-{core_version}-py3-none-any.whl"),
        "integration_distribution": "ctxc-openhands",
        "integration_version": integration_version,
        "integration_wheel": (f"ctxc_openhands-{integration_version}-py3-none-any.whl"),
        "integration_sdist": f"ctxc_openhands-{integration_version}.tar.gz",
    }


def _matrix_directory_entries(
    path: Path,
    *,
    label: str,
) -> tuple[dict[str, tuple[str, os.stat_result]], tuple[Any, ...]]:
    try:
        initial = path.lstat()
    except OSError as exc:
        raise ReleaseReproducibilityError(f"{label} is missing") from exc
    if (
        not stat.S_ISDIR(initial.st_mode)
        or stat.S_ISLNK(initial.st_mode)
        or _is_reparse_or_junction(path, initial)
    ):
        raise ReleaseReproducibilityError(f"{label} must be a real directory")
    raw_entries: list[os.DirEntry[str]] = []
    try:
        with os.scandir(path) as iterator:
            for entry in iterator:
                raw_entries.append(entry)
                if len(raw_entries) > _MAX_MATRIX_DIRECTORY_ENTRIES:
                    raise ReleaseReproducibilityError(f"{label} has too many entries")
    except OSError as exc:
        raise ReleaseReproducibilityError(f"{label} could not be enumerated") from exc
    entries: dict[str, tuple[str, os.stat_result]] = {}
    for entry in raw_entries:
        name = entry.name
        if _SAFE_MATRIX_NAME_RE.fullmatch(name) is None or name in entries:
            raise ReleaseReproducibilityError(f"{label} has a noncanonical entry name")
        entry_path = path / name
        try:
            entry_stat = entry_path.lstat()
        except OSError as exc:
            raise ReleaseReproducibilityError(f"{label} entry could not be inspected") from exc
        if stat.S_ISLNK(entry_stat.st_mode) or _is_reparse_or_junction(entry_path, entry_stat):
            raise ReleaseReproducibilityError(f"{label} contains a linked or reparse entry")
        if stat.S_ISDIR(entry_stat.st_mode):
            kind = "directory"
        elif stat.S_ISREG(entry_stat.st_mode):
            if entry_stat.st_nlink != 1:
                raise ReleaseReproducibilityError(f"{label} contains a multiply linked file")
            kind = "file"
        else:
            raise ReleaseReproducibilityError(f"{label} contains a special entry")
        entries[name] = (kind, entry_stat)
    try:
        final = path.lstat()
    except OSError as exc:
        raise ReleaseReproducibilityError(f"{label} changed while being enumerated") from exc
    if _directory_stat_snapshot(initial) != _directory_stat_snapshot(final):
        raise ReleaseReproducibilityError(f"{label} changed while being enumerated")
    signature = (
        label,
        *_directory_stat_snapshot(final),
        tuple(sorted((name, kind) for name, (kind, _value) in entries.items())),
    )
    return entries, signature


def _require_matrix_entries(
    entries: dict[str, tuple[str, os.stat_result]],
    expected: set[str],
    *,
    label: str,
) -> None:
    observed = set(entries)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise ReleaseReproducibilityError(
            f"{label} inventory mismatch; missing_count={len(missing)}, "
            f"missing={missing[:8]!r}, extra_count={len(extra)}, extra={extra[:8]!r}"
        )


def _require_matrix_entry_kind(
    entries: dict[str, tuple[str, os.stat_result]],
    name: str,
    kind: str,
    *,
    label: str,
) -> os.stat_result:
    observed_kind, value = entries[name]
    if observed_kind != kind:
        raise ReleaseReproducibilityError(f"{label} {name!r} must be a {kind}")
    return value


def _hash_matrix_file(
    path: Path,
    *,
    label: str,
    expected_stat: os.stat_result,
    max_bytes: int,
) -> tuple[dict[str, Any], tuple[Any, ...], tuple[int, ...]]:
    try:
        initial = path.lstat()
    except OSError as exc:
        raise ReleaseReproducibilityError(f"{label} is missing") from exc
    if _path_stat_snapshot(initial) != _path_stat_snapshot(expected_stat):
        raise ReleaseReproducibilityError(f"{label} changed before hashing")
    if (
        not stat.S_ISREG(initial.st_mode)
        or stat.S_ISLNK(initial.st_mode)
        or initial.st_nlink != 1
        or _is_reparse_or_junction(path, initial)
    ):
        raise ReleaseReproducibilityError(f"{label} must be one unlinked regular file")
    try:
        evidence = hash_bounded_regular_file(path, max_bytes=max_bytes, label=label)
    except StrictJsonError as exc:
        raise ReleaseReproducibilityError(f"{label} could not be hashed within its limit") from exc
    if evidence.byte_count <= 0:
        raise ReleaseReproducibilityError(f"{label} cannot be empty")
    try:
        final = path.lstat()
    except OSError as exc:
        raise ReleaseReproducibilityError(f"{label} changed after hashing") from exc
    if _path_stat_snapshot(initial) != _path_stat_snapshot(final):
        raise ReleaseReproducibilityError(f"{label} changed while being hashed")
    record = {
        "filename": path.name,
        "bytes": evidence.byte_count,
        "sha256": evidence.file_sha256,
    }
    final_snapshot = _path_stat_snapshot(final)
    signature = (label, *final_snapshot, evidence.file_sha256)
    return record, signature, final_snapshot


def _single_expected_name(
    entries: dict[str, tuple[str, os.stat_result]],
    expected_name: str,
    *,
    label: str,
) -> str:
    if set(entries) != {expected_name} or entries[expected_name][0] != "file":
        raise ReleaseReproducibilityError(f"{label} must contain the configured artifact filename")
    return expected_name


def _openhands_artifact_names(
    entries: dict[str, tuple[str, os.stat_result]],
    *,
    expected_wheel: str,
    expected_sdist: str,
    label: str,
) -> tuple[str, str]:
    expected = {expected_wheel, expected_sdist}
    if set(entries) != expected or any(entries[name][0] != "file" for name in expected):
        raise ReleaseReproducibilityError(
            f"{label} must contain the configured integration wheel and sdist"
        )
    return expected_wheel, expected_sdist


def _matrix_snapshot(matrix_root: str | Path) -> _MatrixSnapshot:
    package_identity = _configured_package_identity()
    root = Path(os.path.abspath(os.fspath(Path(matrix_root).expanduser())))
    root_entries, root_signature = _matrix_directory_entries(
        root,
        label="package matrix root",
    )
    expected_lanes = set(PACKAGE_MATRIX_LANES)
    _require_matrix_entries(
        root_entries,
        expected_lanes,
        label="package matrix root",
    )
    lanes: list[dict[str, Any]] = []
    paths: dict[str, dict[str, Path]] = {}
    file_snapshots: dict[str, dict[str, tuple[int, ...]]] = {}
    signatures: list[tuple[Any, ...]] = [root_signature]
    directory_checks: list[tuple[Path, str, set[str], tuple[Any, ...]]] = [
        (root, "package matrix root", expected_lanes, root_signature)
    ]
    total_bytes = 0
    for lane in PACKAGE_MATRIX_LANES:
        _require_matrix_entry_kind(
            root_entries,
            lane,
            "directory",
            label="package matrix root",
        )
        lane_path = root / lane
        lane_entries, lane_signature = _matrix_directory_entries(
            lane_path,
            label=f"package lane {lane}",
        )
        expected_lane_entries = {"dist", *_MATRIX_SUPPORT_FILES}
        _require_matrix_entries(
            lane_entries,
            expected_lane_entries,
            label=f"package lane {lane}",
        )
        _require_matrix_entry_kind(
            lane_entries,
            "dist",
            "directory",
            label=f"package lane {lane}",
        )
        signatures.append(lane_signature)
        directory_checks.append(
            (lane_path, f"package lane {lane}", expected_lane_entries, lane_signature)
        )

        dist_path = lane_path / "dist"
        dist_entries, dist_signature = _matrix_directory_entries(
            dist_path,
            label=f"package lane {lane} dist",
        )
        _require_matrix_entries(
            dist_entries,
            {"core", "openhands"},
            label=f"package lane {lane} dist",
        )
        for name in ("core", "openhands"):
            _require_matrix_entry_kind(
                dist_entries,
                name,
                "directory",
                label=f"package lane {lane} dist",
            )
        signatures.append(dist_signature)
        directory_checks.append(
            (dist_path, f"package lane {lane} dist", {"core", "openhands"}, dist_signature)
        )

        core_path = dist_path / "core"
        core_entries, core_signature = _matrix_directory_entries(
            core_path,
            label=f"package lane {lane} core dist",
        )
        root_wheel_name = _single_expected_name(
            core_entries,
            package_identity["root_wheel"],
            label=f"package lane {lane} core dist",
        )
        signatures.append(core_signature)
        directory_checks.append(
            (
                core_path,
                f"package lane {lane} core dist",
                {root_wheel_name},
                core_signature,
            )
        )

        openhands_path = dist_path / "openhands"
        openhands_entries, openhands_signature = _matrix_directory_entries(
            openhands_path,
            label=f"package lane {lane} integration dist",
        )
        integration_wheel_name, integration_sdist_name = _openhands_artifact_names(
            openhands_entries,
            expected_wheel=package_identity["integration_wheel"],
            expected_sdist=package_identity["integration_sdist"],
            label=f"package lane {lane} integration dist",
        )
        signatures.append(openhands_signature)
        directory_checks.append(
            (
                openhands_path,
                f"package lane {lane} integration dist",
                {integration_wheel_name, integration_sdist_name},
                openhands_signature,
            )
        )

        file_specs = {
            "root_wheel": (
                core_path / root_wheel_name,
                core_entries[root_wheel_name][1],
                _MAX_ARCHIVE_BYTES,
            ),
            "integration_wheel": (
                openhands_path / integration_wheel_name,
                openhands_entries[integration_wheel_name][1],
                _MAX_ARCHIVE_BYTES,
            ),
            "integration_sdist": (
                openhands_path / integration_sdist_name,
                openhands_entries[integration_sdist_name][1],
                _MAX_ARCHIVE_BYTES,
            ),
        }
        for support_name in _MATRIX_SUPPORT_FILES:
            file_specs[support_name] = (
                lane_path / support_name,
                _require_matrix_entry_kind(
                    lane_entries,
                    support_name,
                    "file",
                    label=f"package lane {lane}",
                ),
                _MAX_MATRIX_SUPPORT_BYTES,
            )
        lane_files: dict[str, dict[str, Any]] = {}
        lane_paths: dict[str, Path] = {}
        lane_file_snapshots: dict[str, tuple[int, ...]] = {}
        for key, (path, expected_stat, max_bytes) in file_specs.items():
            if expected_stat.st_size <= 0:
                raise ReleaseReproducibilityError(f"package lane {lane} file {key} cannot be empty")
            if expected_stat.st_size > max_bytes:
                raise ReleaseReproducibilityError(
                    f"package lane {lane} file {key} exceeds its byte limit"
                )
            remaining_bytes = _MAX_MATRIX_TOTAL_BYTES - total_bytes
            if expected_stat.st_size > remaining_bytes:
                raise ReleaseReproducibilityError("package matrix exceeds the total byte limit")
            record, file_signature, file_snapshot = _hash_matrix_file(
                path,
                label=f"package lane {lane} file {key}",
                expected_stat=expected_stat,
                max_bytes=min(max_bytes, remaining_bytes),
            )
            lane_files[key] = record
            lane_paths[key] = path
            lane_file_snapshots[key] = file_snapshot
            signatures.append(file_signature)
            total_bytes += int(record["bytes"])
            if total_bytes > _MAX_MATRIX_TOTAL_BYTES:
                raise ReleaseReproducibilityError("package matrix exceeds the total byte limit")
        lanes.append({"lane": lane, "files": lane_files})
        paths[lane] = lane_paths
        file_snapshots[lane] = lane_file_snapshots

    for path, label, expected_names, expected_signature in directory_checks:
        final_entries, final_signature = _matrix_directory_entries(path, label=label)
        _require_matrix_entries(final_entries, expected_names, label=label)
        if final_signature != expected_signature:
            raise ReleaseReproducibilityError(
                "package matrix directories changed during validation"
            )
    return _MatrixSnapshot(
        package_identity=package_identity,
        lanes=lanes,
        paths=paths,
        file_snapshots=file_snapshots,
        signature=tuple(signatures),
    )


def _matrix_artifact_groups(snapshot: _MatrixSnapshot) -> list[dict[str, Any]]:
    reference_lane = PACKAGE_MATRIX_LANES[0]
    lane_records = {str(item["lane"]): item["files"] for item in snapshot.lanes}
    groups: list[dict[str, Any]] = []
    for kind in _MATRIX_ARTIFACT_KEYS:
        reference_record = lane_records[reference_lane][kind]
        records: list[dict[str, Any]] = []
        exact = True
        for lane in PACKAGE_MATRIX_LANES:
            record = lane_records[lane][kind]
            same_name = record["filename"] == reference_record["filename"]
            same_size = record["bytes"] == reference_record["bytes"]
            same_digest = record["sha256"] == reference_record["sha256"]
            same_bytes = (
                same_name
                and same_size
                and same_digest
                and (
                    lane == reference_lane
                    or _exact_matrix_file_equal(
                        snapshot.paths[reference_lane][kind],
                        snapshot.paths[lane][kind],
                        expected_bytes=int(reference_record["bytes"]),
                        first_snapshot=snapshot.file_snapshots[reference_lane][kind],
                        second_snapshot=snapshot.file_snapshots[lane][kind],
                    )
                )
            )
            exact = exact and same_bytes
            records.append(
                {
                    "lane": lane,
                    **record,
                    "matches_reference_bytes": same_bytes,
                }
            )
        groups.append(
            {
                "kind": kind,
                "reference_lane": reference_lane,
                "filename": reference_record["filename"],
                "byte_identical": exact,
                "lanes": records,
            }
        )
    return groups


def compare_package_matrix(
    matrix_root: str | Path,
    *,
    revision: str,
    upstream_result: str,
) -> dict[str, Any]:
    """Compare raw package bytes from the exact six hosted package lanes."""

    if not isinstance(revision, str) or _REVISION_RE.fullmatch(revision) is None:
        raise ReleaseReproducibilityError(
            "package matrix revision must be a lowercase 40-character commit"
        )
    if not isinstance(upstream_result, str) or upstream_result not in _UPSTREAM_RESULTS:
        raise ReleaseReproducibilityError("package matrix upstream result is invalid")
    first = _matrix_snapshot(matrix_root)
    groups = _matrix_artifact_groups(first)
    final = _matrix_snapshot(matrix_root)
    if (
        first.package_identity != final.package_identity
        or first.lanes != final.lanes
        or first.signature != final.signature
    ):
        raise ReleaseReproducibilityError("package matrix changed between validation passes")
    issues = [
        f"{group['kind']} package bytes differ across the required lanes"
        for group in groups
        if not group["byte_identical"]
    ]
    if upstream_result != "success":
        issues.append(f"offline-package matrix result was {upstream_result!r}, not 'success'")
    unsigned: dict[str, Any] = {
        "schema": PACKAGE_MATRIX_REPORT_SCHEMA,
        "revision": revision,
        "upstream_result": upstream_result,
        "package_identity": first.package_identity,
        "required_lanes": list(PACKAGE_MATRIX_LANES),
        "status": "passed" if not issues else "failed",
        "issues": issues,
        "artifact_groups": groups,
        "lanes": first.lanes,
    }
    return {**unsigned, "report_sha256": _canonical_sha256(unsigned)}


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


def _neutral_matrix_error(message: str, matrix_root: Path) -> str:
    result = message
    absolute_root = Path(os.path.abspath(os.fspath(matrix_root.expanduser())))
    candidates = {str(absolute_root), absolute_root.as_posix()}
    for candidate in sorted(candidates, key=len, reverse=True):
        if candidate:
            result = result.replace(candidate, "<matrix-root>")
    if len(result) > _MAX_MATRIX_ISSUE_CHARS:
        return result[: _MAX_MATRIX_ISSUE_CHARS - 3] + "..."
    return result


def _matrix_failure_report(
    message: str,
    *,
    revision: str | None,
    upstream_result: str | None,
) -> dict[str, Any]:
    safe_revision = (
        revision
        if isinstance(revision, str) and _REVISION_RE.fullmatch(revision) is not None
        else "invalid"
    )
    safe_upstream = (
        upstream_result
        if isinstance(upstream_result, str) and upstream_result in _UPSTREAM_RESULTS
        else "invalid"
    )
    unsigned: dict[str, Any] = {
        "schema": PACKAGE_MATRIX_REPORT_SCHEMA,
        "revision": safe_revision,
        "upstream_result": safe_upstream,
        "package_identity": {},
        "required_lanes": list(PACKAGE_MATRIX_LANES),
        "status": "failed",
        "issues": [message],
        "artifact_groups": [],
        "lanes": [],
    }
    return {**unsigned, "report_sha256": _canonical_sha256(unsigned)}


def _parser(*, matrix_mode: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    if matrix_mode:
        parser.add_argument("--matrix-root", type=Path, required=True)
        parser.add_argument("--revision", required=True)
        parser.add_argument("--upstream-result", required=True)
    else:
        parser.add_argument("--first-dist", type=Path, required=True)
        parser.add_argument("--second-dist", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    return parser


def _matrix_cli_requested(arguments: Sequence[str]) -> bool:
    matrix_flags = ("--matrix-root", "--revision", "--upstream-result")
    return any(
        argument == flag or argument.startswith(f"{flag}=")
        for argument in arguments
        for flag in matrix_flags
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    matrix_mode = _matrix_cli_requested(arguments)
    args = _parser(matrix_mode=matrix_mode).parse_args(arguments)
    if matrix_mode:
        try:
            report = compare_package_matrix(
                args.matrix_root,
                revision=args.revision,
                upstream_result=args.upstream_result,
            )
        except (OSError, ReleaseReproducibilityError, TypeError, ValueError) as exc:
            matrix_root = args.matrix_root or Path(".")
            report = _matrix_failure_report(
                _neutral_matrix_error(str(exc), matrix_root),
                revision=args.revision,
                upstream_result=args.upstream_result,
            )
    else:
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
                "schema": report["schema"],
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
