"""Validate the exact build-input wheelhouse used by OpenHands package CI.

This helper is deliberately validation-only. It never contacts a package index,
downloads an artifact, installs a distribution, or mutates the wheelhouse.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import stat
import struct
import subprocess
import unicodedata
import zipfile
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any

REPORT_SCHEMA = "ctxc-openhands-build-inputs-ci-0.1"
LOCK_FILENAME = "requirements-build.lock"
_MAX_LOCK_BYTES = 16 * 1024
_MAX_WHEEL_BYTES = 64 * 1024 * 1024
_MAX_WHEELHOUSE_BYTES = 192 * 1024 * 1024
_MAX_WHEEL_MEMBERS = 20_000
_MAX_WHEEL_MEMBER_BYTES = 64 * 1024 * 1024
_MAX_WHEEL_EXPANDED_BYTES = 256 * 1024 * 1024
_MAX_METADATA_BYTES = 2 * 1024 * 1024
_MAX_RECORD_BYTES = 8 * 1024 * 1024
_MAX_REPORT_BYTES = 256 * 1024
_MAX_ISSUE_CHARS = 1024
_MAX_PROBE_BYTES = 64 * 1024
_MAX_PROBE_DISTRIBUTIONS = 64
_CHUNK_BYTES = 1024 * 1024
_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_LOCK_LINE_RE = re.compile(
    rb"([a-z][a-z0-9-]*)==([0-9A-Za-z][0-9A-Za-z.!+_-]*) "
    rb"--hash=sha256:([0-9a-f]{64})\n"
)
_RECORD_DIGEST_RE = re.compile(r"sha256=([A-Za-z0-9_-]{43})\Z")
_RECORD_SIZE_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_SAFE_WHEELHOUSE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,254}\.whl\Z")
_SAFE_MEMBER_COMPONENT_RE = re.compile(r"[^/\x00-\x1f\x7f-\x9f]+")
_WINDOWS_RESERVED_COMPONENTS = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
    | {f"com{number}" for number in ("\u00b9", "\u00b2", "\u00b3")}
    | {f"lpt{number}" for number in ("\u00b9", "\u00b2", "\u00b3")}
)
_WINDOWS_INVALID_COMPONENT_CHARS = frozenset('<>:"|?*')
_NORMALIZED_NAME_RE = re.compile(r"[-_.]+")
_MAX_SOURCE_DATE_EPOCH = (1 << 32) - 1


class BuildInputError(ValueError):
    """The build-input lock, wheelhouse, or installed builder is not exact."""


@dataclass(frozen=True, slots=True)
class ApprovedWheel:
    name: str
    version: str
    filename: str
    dist_info: str
    tags: tuple[str, ...]


APPROVED_BUILD_WHEELS = (
    ApprovedWheel(
        "build",
        "1.5.0",
        "build-1.5.0-py3-none-any.whl",
        "build-1.5.0.dist-info",
        ("py3-none-any",),
    ),
    ApprovedWheel(
        "colorama",
        "0.4.6",
        "colorama-0.4.6-py2.py3-none-any.whl",
        "colorama-0.4.6.dist-info",
        ("py2-none-any", "py3-none-any"),
    ),
    ApprovedWheel(
        "packaging",
        "26.2",
        "packaging-26.2-py3-none-any.whl",
        "packaging-26.2.dist-info",
        ("py3-none-any",),
    ),
    ApprovedWheel(
        "pip",
        "25.0.1",
        "pip-25.0.1-py3-none-any.whl",
        "pip-25.0.1.dist-info",
        ("py3-none-any",),
    ),
    ApprovedWheel(
        "pyproject-hooks",
        "1.2.0",
        "pyproject_hooks-1.2.0-py3-none-any.whl",
        "pyproject_hooks-1.2.0.dist-info",
        ("py3-none-any",),
    ),
    ApprovedWheel(
        "setuptools",
        "83.0.0",
        "setuptools-83.0.0-py3-none-any.whl",
        "setuptools-83.0.0.dist-info",
        ("py3-none-any",),
    ),
    ApprovedWheel(
        "wheel",
        "0.47.0",
        "wheel-0.47.0-py3-none-any.whl",
        "wheel-0.47.0.dist-info",
        ("py3-none-any",),
    ),
)
_APPROVED_BY_NAME = {wheel.name: wheel for wheel in APPROVED_BUILD_WHEELS}
_APPROVED_FILENAMES = {wheel.filename for wheel in APPROVED_BUILD_WHEELS}


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class _LockRecord:
    approved: ApprovedWheel
    sha256: str


@dataclass(frozen=True, slots=True)
class _InputSnapshot:
    lock_filename: str
    lock_bytes: int
    lock_sha256: str
    wheels: tuple[dict[str, Any], ...]
    inventory_sha256: str
    signature: tuple[Any, ...]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _normalize_distribution_name(value: str) -> str:
    return _NORMALIZED_NAME_RE.sub("-", value).casefold()


def _is_reparse_or_junction(path: Path, path_stat: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(path_stat, "st_file_attributes", 0) & reparse_flag) or bool(
        hasattr(os.path, "isjunction") and os.path.isjunction(path)
    )


def _snapshot(path_stat: os.stat_result) -> _FileSnapshot:
    return _FileSnapshot(
        device=path_stat.st_dev,
        inode=path_stat.st_ino,
        mode=path_stat.st_mode,
        links=path_stat.st_nlink,
        size=path_stat.st_size,
        modified_ns=path_stat.st_mtime_ns,
    )


def _regular_file_state(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> tuple[Path, _FileSnapshot]:
    lexical = Path(os.path.abspath(os.fspath(path.expanduser())))
    try:
        initial = lexical.lstat()
    except OSError as exc:
        raise BuildInputError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(initial.st_mode)
        or stat.S_ISLNK(initial.st_mode)
        or _is_reparse_or_junction(lexical, initial)
        or initial.st_nlink != 1
        or initial.st_size <= 0
        or initial.st_size > max_bytes
    ):
        raise BuildInputError(f"{label} must be one bounded regular unlinked file")
    try:
        resolved = lexical.resolve(strict=True)
        resolved_stat = resolved.stat()
    except OSError as exc:
        raise BuildInputError(f"{label} could not be resolved") from exc
    observed = _snapshot(initial)
    if _snapshot(resolved_stat) != observed:
        raise BuildInputError(f"{label} changed while being resolved")
    return lexical, observed


def _read_regular_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> tuple[bytes, str, _FileSnapshot]:
    lexical, expected = _regular_file_state(path, label=label, max_bytes=max_bytes)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lexical, flags)
    except OSError as exc:
        raise BuildInputError(f"{label} could not be opened") from exc
    try:
        if _snapshot(os.fstat(descriptor)) != expected:
            raise BuildInputError(f"{label} changed while being opened")
        chunks: list[bytes] = []
        remaining = expected.size
        digest = hashlib.sha256()
        while remaining:
            chunk = os.read(descriptor, min(_CHUNK_BYTES, remaining))
            if not chunk:
                raise BuildInputError(f"{label} was truncated while being read")
            chunks.append(chunk)
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise BuildInputError(f"{label} exceeded its validated size")
        if _snapshot(os.fstat(descriptor)) != expected:
            raise BuildInputError(f"{label} changed while being read")
    finally:
        os.close(descriptor)
    try:
        final = lexical.lstat()
    except OSError as exc:
        raise BuildInputError(f"{label} changed after being read") from exc
    if _snapshot(final) != expected:
        raise BuildInputError(f"{label} changed after being read")
    return b"".join(chunks), digest.hexdigest(), expected


def _real_directory(path: Path, *, label: str) -> tuple[Path, tuple[int, int, int, int]]:
    lexical = Path(os.path.abspath(os.fspath(path.expanduser())))
    try:
        initial = lexical.lstat()
    except OSError as exc:
        raise BuildInputError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(initial.st_mode)
        or stat.S_ISLNK(initial.st_mode)
        or _is_reparse_or_junction(lexical, initial)
    ):
        raise BuildInputError(f"{label} must be one real directory")
    try:
        resolved = lexical.resolve(strict=True)
        resolved_stat = resolved.stat()
    except OSError as exc:
        raise BuildInputError(f"{label} could not be resolved") from exc
    identity = (initial.st_dev, initial.st_ino, initial.st_mode, initial.st_mtime_ns)
    if (resolved_stat.st_dev, resolved_stat.st_ino) != identity[:2]:
        raise BuildInputError(f"{label} changed while being resolved")
    return lexical, identity


def _parse_lock_bytes(payload: bytes) -> tuple[_LockRecord, ...]:
    if not payload or len(payload) > _MAX_LOCK_BYTES:
        raise BuildInputError("build requirements lock is outside its byte bound")
    if payload.startswith(b"\xef\xbb\xbf") or b"\r" in payload or not payload.endswith(b"\n"):
        raise BuildInputError("build requirements lock must be canonical LF text")
    raw_lines = payload.splitlines(keepends=True)
    if len(raw_lines) != len(APPROVED_BUILD_WHEELS):
        raise BuildInputError("build requirements lock must contain exactly seven records")
    records: list[_LockRecord] = []
    seen: set[str] = set()
    for index, (raw_line, approved) in enumerate(
        zip(raw_lines, APPROVED_BUILD_WHEELS, strict=True),
        start=1,
    ):
        match = _LOCK_LINE_RE.fullmatch(raw_line)
        if match is None:
            raise BuildInputError(f"build requirements lock record {index} is not canonical")
        name = match.group(1).decode("ascii")
        version = match.group(2).decode("ascii")
        digest = match.group(3).decode("ascii")
        normalized = _normalize_distribution_name(name)
        if normalized in seen:
            raise BuildInputError("build requirements lock contains a duplicate distribution")
        seen.add(normalized)
        if name != approved.name or version != approved.version:
            raise BuildInputError(
                "build requirements lock records are missing, extra, or reordered"
            )
        records.append(_LockRecord(approved=approved, sha256=digest))
    if seen != set(_APPROVED_BY_NAME):
        raise BuildInputError("build requirements lock does not name the approved closure")
    return tuple(records)


def parse_requirements_lock(path: str | Path) -> tuple[dict[str, str], ...]:
    """Return the strict seven-record lock without exposing a filesystem path."""

    payload, _digest, _state = _read_regular_file(
        Path(path),
        label="build requirements lock",
        max_bytes=_MAX_LOCK_BYTES,
    )
    records = _parse_lock_bytes(payload)
    return tuple(
        {
            "name": record.approved.name,
            "version": record.approved.version,
            "filename": record.approved.filename,
            "sha256": record.sha256,
        }
        for record in records
    )


def _windows_component_is_reserved(component: str) -> bool:
    stem = component.partition(".")[0].rstrip(" ").casefold()
    return stem in _WINDOWS_RESERVED_COMPONENTS


def _safe_wheel_member_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or len(value) > 4096
    ):
        raise BuildInputError("build wheel has an unsafe member name")
    path = PurePosixPath(value)
    if (
        path.as_posix() != value
        or not path.parts
        or any(component in {"", ".", ".."} for component in path.parts)
    ):
        raise BuildInputError("build wheel has an unsafe member name")
    for component in path.parts:
        if (
            _SAFE_MEMBER_COMPONENT_RE.fullmatch(component) is None
            or component.endswith((" ", "."))
            or any(character in _WINDOWS_INVALID_COMPONENT_CHARS for character in component)
            or _windows_component_is_reserved(component)
        ):
            raise BuildInputError("build wheel has a nonportable member name")
    return value


def _portable_member_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _validate_member_namespace(names: Sequence[str], *, label: str) -> None:
    """Check an already safe, count-bounded member inventory."""

    # Safe-name validation excludes NUL, so it preserves component ordering here.
    separator = "\x00"
    ordered = sorted((_portable_member_key(name).replace("/", separator), name) for name in names)
    for index in range(1, len(ordered)):
        prior_key, prior_name = ordered[index - 1]
        key, name = ordered[index]
        if key == prior_key or key.startswith(prior_key + separator):
            raise BuildInputError(f"{label} contains a namespace conflict")

        prior_key_parts = prior_key.split(separator)
        key_parts = key.split(separator)
        prior_parts = prior_name.split("/")
        parts = name.split("/")
        shared_directory_parts = min(len(prior_parts), len(parts)) - 1
        for part_index in range(shared_directory_parts):
            if prior_key_parts[part_index] != key_parts[part_index]:
                break
            if prior_parts[part_index] != parts[part_index]:
                raise BuildInputError(f"{label} contains a namespace conflict")


def _validate_zip_framing(payload: bytes) -> int:
    if len(payload) < 22 or not payload.startswith(b"PK\x03\x04"):
        raise BuildInputError("build wheel has invalid ZIP framing")
    try:
        (
            signature,
            disk_number,
            directory_disk,
            entries_on_disk,
            entries_total,
            directory_size,
            directory_offset,
            comment_size,
        ) = struct.unpack("<4s4H2LH", payload[-22:])
    except struct.error as exc:
        raise BuildInputError("build wheel has invalid ZIP framing") from exc
    if (
        signature != b"PK\x05\x06"
        or disk_number != 0
        or directory_disk != 0
        or entries_on_disk != entries_total
        or entries_total <= 0
        or entries_total > _MAX_WHEEL_MEMBERS
        or comment_size != 0
        or directory_offset + directory_size + 22 != len(payload)
        or payload[directory_offset : directory_offset + 4] != b"PK\x01\x02"
    ):
        raise BuildInputError("build wheel has unsupported or inconsistent ZIP framing")
    return entries_total


def _read_zip_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    *,
    max_bytes: int,
    label: str,
) -> bytes:
    if member.file_size < 0 or member.file_size > max_bytes:
        raise BuildInputError(f"{label} exceeds its byte bound")
    try:
        with archive.open(member, "r") as stream:
            payload = stream.read(member.file_size + 1)
    except (EOFError, OSError, RuntimeError, zipfile.BadZipFile, zlib.error) as exc:
        raise BuildInputError(f"{label} could not be read") from exc
    if len(payload) != member.file_size:
        raise BuildInputError(f"{label} expanded to an unexpected size")
    return payload

def _headers(payload: bytes, *, label: str) -> Any:
    if not payload or len(payload) > _MAX_METADATA_BYTES:
        raise BuildInputError(f"{label} is outside its byte bound")
    try:
        return BytesParser(policy=policy.compat32).parsebytes(payload, headersonly=True)
    except (LookupError, UnicodeError, ValueError) as exc:
        raise BuildInputError(f"{label} is malformed") from exc


def _one_header(document: Any, name: str, *, label: str) -> str:
    values = document.get_all(name, [])
    if (
        not isinstance(values, list)
        or len(values) != 1
        or not isinstance(values[0], str)
        or not values[0]
        or len(values[0]) > 256
    ):
        raise BuildInputError(f"{label} must contain exactly one {name} header")
    return values[0]


def _decode_record_digest(value: str) -> bytes:
    match = _RECORD_DIGEST_RE.fullmatch(value)
    if match is None:
        raise BuildInputError("build wheel RECORD hash is not canonical SHA-256")
    encoded = match.group(1)
    try:
        decoded = base64.b64decode(encoded + "=", altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise BuildInputError("build wheel RECORD hash is not canonical SHA-256") from exc
    if (
        len(decoded) != hashlib.sha256().digest_size
        or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != encoded
    ):
        raise BuildInputError("build wheel RECORD hash is not canonical SHA-256")
    return decoded


def _validate_record(
    payload: bytes,
    *,
    record_name: str,
    member_payloads: dict[str, bytes],
) -> None:
    if not payload or len(payload) > _MAX_RECORD_BYTES:
        raise BuildInputError("build wheel RECORD is outside its byte bound")
    try:
        text = payload.decode("utf-8", "strict")
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise BuildInputError("build wheel RECORD is malformed") from exc
    if len(rows) != len(member_payloads):
        raise BuildInputError("build wheel RECORD inventory does not match the archive")
    observed: set[str] = set()
    validated_rows: list[tuple[str, str, str]] = []
    for row in rows:
        if len(row) != 3:
            raise BuildInputError("build wheel RECORD row must contain exactly three fields")
        name = _safe_wheel_member_name(row[0])
        if name in observed:
            raise BuildInputError("build wheel RECORD contains a duplicate path")
        observed.add(name)
        validated_rows.append((name, row[1], row[2]))
    _validate_member_namespace(tuple(observed), label="build wheel RECORD")

    for name, digest_value, size_value in validated_rows:
        if name not in member_payloads:
            raise BuildInputError("build wheel RECORD names an absent archive member")
        if name == record_name:
            if (digest_value, size_value) != ("", ""):
                raise BuildInputError("build wheel RECORD self-row must be unhashed and unsized")
            continue
        digest = _decode_record_digest(digest_value)
        if _RECORD_SIZE_RE.fullmatch(size_value) is None:
            raise BuildInputError("build wheel RECORD size is not canonical")
        member_payload = member_payloads[name]
        if int(size_value) != len(member_payload):
            raise BuildInputError("build wheel RECORD size does not match archive bytes")
        if hashlib.sha256(member_payload).digest() != digest:
            raise BuildInputError("build wheel RECORD digest does not match archive bytes")
    if observed != set(member_payloads):
        raise BuildInputError("build wheel RECORD inventory does not match the archive")


def _validate_wheel_bytes(payload: bytes, approved: ApprovedWheel) -> None:
    framed_members = _validate_zip_framing(payload)
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            if archive.comment:
                raise BuildInputError("build wheel has an unexpected archive comment")
            members = archive.infolist()
            if (
                not members
                or len(members) != framed_members
                or len(members) > _MAX_WHEEL_MEMBERS
            ):
                raise BuildInputError("build wheel member count is outside its bound")
            seen: set[str] = set()
            portable: set[str] = set()
            expanded = 0
            validated_members: list[tuple[zipfile.ZipInfo, str]] = []
            for member in members:
                name = _safe_wheel_member_name(member.filename)
                portable_name = _portable_member_key(name)
                if name in seen or portable_name in portable:
                    raise BuildInputError("build wheel contains duplicate or colliding members")
                seen.add(name)
                portable.add(portable_name)
                if (
                    member.orig_filename != member.filename
                    or member.is_dir()
                    or member.flag_bits not in {0, 0x800}
                    or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or member.create_system not in {0, 3}
                ):
                    raise BuildInputError("build wheel contains an unsupported member")
                if member.create_system == 3:
                    raw_mode = (member.external_attr >> 16) & 0xFFFF
                    file_type = stat.S_IFMT(raw_mode)
                    if file_type not in {0, stat.S_IFREG}:
                        raise BuildInputError("build wheel contains a link or special member")
                if member.file_size < 0 or member.file_size > _MAX_WHEEL_MEMBER_BYTES:
                    raise BuildInputError("build wheel member exceeds its byte bound")
                expanded += member.file_size
                if expanded > _MAX_WHEEL_EXPANDED_BYTES:
                    raise BuildInputError("build wheel exceeds its expanded byte bound")
                validated_members.append((member, name))
            portable.clear()
            _validate_member_namespace(
                tuple(name for _member, name in validated_members),
                label="build wheel",
            )

            member_payloads: dict[str, bytes] = {}
            for member, name in validated_members:
                member_payloads[name] = _read_zip_member(
                    archive,
                    member,
                    max_bytes=_MAX_WHEEL_MEMBER_BYTES,
                    label="build wheel member",
                )
    except BuildInputError:
        raise
    except (EOFError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise BuildInputError("build wheel is not a valid bounded ZIP archive") from exc

    metadata_name = f"{approved.dist_info}/METADATA"
    wheel_name = f"{approved.dist_info}/WHEEL"
    record_name = f"{approved.dist_info}/RECORD"
    required = {metadata_name, wheel_name, record_name}
    if not required <= set(member_payloads):
        raise BuildInputError("build wheel is missing METADATA, WHEEL, or RECORD")
    foreign_dist_info = {
        PurePosixPath(name).parts[0]
        for name in member_payloads
        if PurePosixPath(name).parts[0].endswith(".dist-info")
    } - {approved.dist_info}
    if foreign_dist_info:
        raise BuildInputError("build wheel contains a foreign dist-info tree")

    metadata = _headers(member_payloads[metadata_name], label="build wheel METADATA")
    metadata_name_value = _one_header(metadata, "Name", label="build wheel METADATA")
    metadata_version = _one_header(metadata, "Version", label="build wheel METADATA")
    if (
        _normalize_distribution_name(metadata_name_value) != approved.name
        or metadata_version != approved.version
    ):
        raise BuildInputError("build wheel METADATA identity does not match its approved record")

    wheel_metadata = _headers(member_payloads[wheel_name], label="build wheel WHEEL")
    purelib = _one_header(
        wheel_metadata,
        "Root-Is-Purelib",
        label="build wheel WHEEL",
    )
    if purelib.casefold() != "true":
        raise BuildInputError("build wheel must be a pure universal wheel")
    tags = wheel_metadata.get_all("Tag", [])
    if (
        not isinstance(tags, list)
        or any(not isinstance(tag, str) or len(tag) > 128 for tag in tags)
        or tuple(sorted(tags)) != tuple(sorted(approved.tags))
    ):
        raise BuildInputError("build wheel WHEEL tags do not match its universal filename")
    _validate_record(
        member_payloads[record_name],
        record_name=record_name,
        member_payloads=member_payloads,
    )


def _directory_entries(
    directory: Path,
    *,
    identity: tuple[int, int, int, int],
) -> dict[str, os.stat_result]:
    try:
        initial = directory.lstat()
    except OSError as exc:
        raise BuildInputError("build wheelhouse changed before enumeration") from exc
    if (
        (initial.st_dev, initial.st_ino) != identity[:2]
        or not stat.S_ISDIR(initial.st_mode)
        or stat.S_ISLNK(initial.st_mode)
        or _is_reparse_or_junction(directory, initial)
    ):
        raise BuildInputError("build wheelhouse changed before enumeration")
    entries: dict[str, os.stat_result] = {}
    try:
        with os.scandir(directory) as iterator:
            for entry in iterator:
                if len(entries) >= len(APPROVED_BUILD_WHEELS) + 1:
                    raise BuildInputError("build wheelhouse contains extra entries")
                name = entry.name
                if _SAFE_WHEELHOUSE_NAME_RE.fullmatch(name) is None or name in entries:
                    raise BuildInputError("build wheelhouse contains a noncanonical entry")
                entry_path = directory / name
                entry_stat = entry_path.lstat()
                if (
                    not stat.S_ISREG(entry_stat.st_mode)
                    or stat.S_ISLNK(entry_stat.st_mode)
                    or _is_reparse_or_junction(entry_path, entry_stat)
                    or entry_stat.st_nlink != 1
                ):
                    raise BuildInputError(
                        "build wheelhouse entries must be regular unlinked files"
                    )
                entries[name] = entry_stat
    except BuildInputError:
        raise
    except OSError as exc:
        raise BuildInputError("build wheelhouse could not be enumerated") from exc
    if set(entries) != _APPROVED_FILENAMES:
        raise BuildInputError("build wheelhouse inventory is missing, renamed, or extra")
    return entries


def _validate_once(lock_path: Path, wheelhouse_path: Path) -> _InputSnapshot:
    if lock_path.name != LOCK_FILENAME:
        raise BuildInputError(f"build requirements lock must be named {LOCK_FILENAME}")
    lock_payload, lock_sha256, lock_state = _read_regular_file(
        lock_path,
        label="build requirements lock",
        max_bytes=_MAX_LOCK_BYTES,
    )
    lock_records = _parse_lock_bytes(lock_payload)
    wheelhouse, directory_identity = _real_directory(
        wheelhouse_path,
        label="build wheelhouse",
    )
    entries = _directory_entries(wheelhouse, identity=directory_identity)
    aggregate = sum(entry.st_size for entry in entries.values())
    if aggregate <= 0 or aggregate > _MAX_WHEELHOUSE_BYTES:
        raise BuildInputError("build wheelhouse exceeds its aggregate byte bound")

    wheel_records: list[dict[str, Any]] = []
    signatures: list[tuple[Any, ...]] = []
    lock_by_name = {record.approved.name: record for record in lock_records}
    for approved in APPROVED_BUILD_WHEELS:
        path = wheelhouse / approved.filename
        payload, digest, wheel_state = _read_regular_file(
            path,
            label=f"build wheel {approved.name}",
            max_bytes=_MAX_WHEEL_BYTES,
        )
        if digest != lock_by_name[approved.name].sha256:
            raise BuildInputError(f"build wheel {approved.name} does not match its lock hash")
        _validate_wheel_bytes(payload, approved)
        record = {
            "name": approved.name,
            "version": approved.version,
            "filename": approved.filename,
            "bytes": len(payload),
            "sha256": digest,
            "tags": list(approved.tags),
        }
        wheel_records.append(record)
        signatures.append((approved.filename, wheel_state, digest))

    final_entries = _directory_entries(wheelhouse, identity=directory_identity)
    for name, expected in entries.items():
        if _snapshot(final_entries[name]) != _snapshot(expected):
            raise BuildInputError("build wheelhouse changed during validation")
    inventory_sha256 = _canonical_sha256(wheel_records)
    return _InputSnapshot(
        lock_filename=lock_path.name,
        lock_bytes=len(lock_payload),
        lock_sha256=lock_sha256,
        wheels=tuple(wheel_records),
        inventory_sha256=inventory_sha256,
        signature=(
            lock_state,
            directory_identity,
            *signatures,
        ),
    )


_BUILDER_PROBE = """
import importlib.metadata
import json
import platform
import sys

rows = []
for distribution in importlib.metadata.distributions():
    name = distribution.metadata.get("Name")
    version = distribution.version
    if (
        not isinstance(name, str)
        or not isinstance(version, str)
        or not name
        or not version
        or len(name) > 128
        or len(version) > 128
    ):
        raise SystemExit(91)
    rows.append([name, version])
    if len(rows) > 64:
        raise SystemExit(92)
print(json.dumps({
    "implementation": platform.python_implementation(),
    "python_version": platform.python_version(),
    "executable": sys.executable,
    "distributions": rows,
}, sort_keys=True, separators=(",", ":")))
"""


def _run_builder_probe(builder_python: Path) -> dict[str, Any]:
    lexical = Path(os.path.abspath(os.fspath(builder_python.expanduser())))
    try:
        resolved = lexical.resolve(strict=True)
        target_stat = resolved.stat()
    except OSError as exc:
        raise BuildInputError("builder Python is unavailable") from exc
    if not stat.S_ISREG(target_stat.st_mode):
        raise BuildInputError("builder Python must resolve to a regular executable")
    environment = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONPYCACHEPREFIX"):
        environment.pop(name, None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    try:
        result = subprocess.run(
            [str(lexical), "-I", "-B", "-c", _BUILDER_PROBE],
            check=False,
            capture_output=True,
            timeout=30,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BuildInputError("builder Python inventory probe could not run") from exc
    if (
        result.returncode != 0
        or len(result.stdout) <= 0
        or len(result.stdout) > _MAX_PROBE_BYTES
        or len(result.stderr) > _MAX_PROBE_BYTES
    ):
        raise BuildInputError("builder Python inventory probe failed or exceeded its bound")
    try:
        document = json.loads(result.stdout.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildInputError("builder Python inventory probe emitted invalid JSON") from exc
    if not isinstance(document, dict) or set(document) != {
        "implementation",
        "python_version",
        "executable",
        "distributions",
    }:
        raise BuildInputError("builder Python inventory probe has an invalid shape")
    executable = document["executable"]
    try:
        observed_executable = Path(executable).resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise BuildInputError(
            "builder Python inventory probe reported an invalid executable"
        ) from exc
    if observed_executable != resolved:
        raise BuildInputError("builder Python inventory probe ran a different executable")
    implementation = document["implementation"]
    python_version = document["python_version"]
    distributions = document["distributions"]
    if (
        not isinstance(implementation, str)
        or not implementation
        or len(implementation) > 64
        or not isinstance(python_version, str)
        or not python_version
        or len(python_version) > 64
        or not isinstance(distributions, list)
        or len(distributions) > _MAX_PROBE_DISTRIBUTIONS
    ):
        raise BuildInputError("builder Python inventory probe values are outside their bounds")
    observed: dict[str, str] = {}
    for row in distributions:
        if (
            not isinstance(row, list)
            or len(row) != 2
            or any(not isinstance(value, str) or not value or len(value) > 128 for value in row)
        ):
            raise BuildInputError("builder Python distribution inventory is malformed")
        name = _normalize_distribution_name(row[0])
        if name in observed:
            raise BuildInputError("builder Python distribution inventory contains duplicates")
        observed[name] = row[1]
    expected = {wheel.name: wheel.version for wheel in APPROVED_BUILD_WHEELS}
    if observed != expected:
        raise BuildInputError("builder Python distribution inventory is missing, extra, or drifted")
    return {
        "verified": True,
        "implementation": implementation,
        "python_version": python_version,
        "distributions": [
            {"name": wheel.name, "version": wheel.version}
            for wheel in APPROVED_BUILD_WHEELS
        ],
    }


def verify_build_inputs(
    lock_path: str | Path,
    wheelhouse_path: str | Path,
    *,
    builder_python: str | Path | None = None,
) -> dict[str, Any]:
    """Verify the lock and wheelhouse twice, plus an optional exact builder."""

    lock = Path(lock_path)
    wheelhouse = Path(wheelhouse_path)
    first = _validate_once(lock, wheelhouse)
    builder = (
        {"verified": False, "reason": "not-requested"}
        if builder_python is None
        else _run_builder_probe(Path(builder_python))
    )
    second = _validate_once(lock, wheelhouse)
    if first != second:
        raise BuildInputError("build inputs changed between validation passes")
    return {
        "lock": {
            "filename": first.lock_filename,
            "bytes": first.lock_bytes,
            "sha256": first.lock_sha256,
            "record_count": len(APPROVED_BUILD_WHEELS),
        },
        "wheelhouse": {
            "file_count": len(first.wheels),
            "aggregate_bytes": sum(int(record["bytes"]) for record in first.wheels),
            "inventory_sha256": first.inventory_sha256,
            "wheels": [dict(record) for record in first.wheels],
        },
        "builder": builder,
    }


def _parse_revision(value: str) -> str:
    if _REVISION_RE.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("revision must be a lowercase 40-character commit")
    return value


def _parse_epoch(value: str) -> int:
    if re.fullmatch(r"0|[1-9][0-9]{0,9}", value) is None:
        raise argparse.ArgumentTypeError("source date epoch must be a canonical decimal integer")
    parsed = int(value)
    if parsed > _MAX_SOURCE_DATE_EPOCH:
        raise argparse.ArgumentTypeError("source date epoch is outside its supported range")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--builder-python", type=Path, required=True)
    parser.add_argument("--revision", type=_parse_revision, required=True)
    parser.add_argument("--source-date-epoch", type=_parse_epoch, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    return parser


def _write_report_exclusive(path: Path, report: dict[str, Any]) -> None:
    payload = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    if not payload or len(payload) > _MAX_REPORT_BYTES:
        raise BuildInputError("build-input report exceeds its byte bound")
    destination = Path(os.path.abspath(os.fspath(path.expanduser())))
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        raise BuildInputError("refusing to replace an existing build-input report") from None
    except OSError as exc:
        raise BuildInputError("build-input report could not be created exclusively") from exc
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise BuildInputError("build-input report write did not make progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _report(unsigned: dict[str, Any]) -> dict[str, Any]:
    return {**unsigned, "report_sha256": _canonical_sha256(unsigned)}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    base = {
        "schema": REPORT_SCHEMA,
        "revision": args.revision,
        "source_date_epoch": args.source_date_epoch,
        "validation_only": True,
        "acquisition_performed": False,
        "network_action_performed": False,
        "semantic_completeness_claimed": False,
    }
    try:
        inputs = verify_build_inputs(
            args.lock,
            args.wheelhouse,
            builder_python=args.builder_python,
        )
    except BuildInputError as exc:
        issue = str(exc)
        if len(issue) > _MAX_ISSUE_CHARS:
            issue = issue[:_MAX_ISSUE_CHARS]
        report = _report(
            {
                **base,
                "status": "failed",
                "passed": False,
                "issue": issue,
            }
        )
        _write_report_exclusive(args.json_out, report)
        print(_canonical_bytes({"passed": False, "report": args.json_out.name}).decode("utf-8"))
        return 2
    report = _report(
        {
            **base,
            "status": "passed",
            "passed": True,
            **inputs,
        }
    )
    _write_report_exclusive(args.json_out, report)
    print(_canonical_bytes({"passed": True, "report": args.json_out.name}).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
