"""Setuptools PEP 517 delegation with opt-in deterministic sdist encoding."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
import unicodedata
import zlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

import setuptools.build_meta as _setuptools_backend

RESULT_SCHEMA = "ctxc-deterministic-sdist-result-0.1"
_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
_MAX_MEMBER_BYTES = 256 * 1024 * 1024
_MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
_MAX_MEMBERS = 20_000
_MAX_MEMBER_NAME_CHARS = 4096
_MAX_PAX_FIELDS = 32
_MAX_PAX_FIELD_CHARS = 4096
_MAX_PAX_PAYLOAD_BYTES = 512 * 1024
_MAX_TOTAL_PAX_BYTES = 16 * 1024 * 1024
_MAX_TAR_METADATA_BYTES = 64 * 1024 * 1024
_MAX_DECOMPRESSED_BYTES = _MAX_EXPANDED_BYTES + _MAX_TAR_METADATA_BYTES
_CHUNK_BYTES = 1024 * 1024
_MAX_GZIP_EPOCH = (1 << 32) - 1
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,254}\.tar\.gz\Z")
_PAX_MTIME = re.compile(r"-?[0-9]+(?:\.[0-9]+)?\Z")
_ALLOWED_INPUT_PAX_FIELDS = frozenset({"mtime", "path"})
_ZERO_BLOCK = bytes(tarfile.BLOCKSIZE)
_WINDOWS_RESERVED_COMPONENT = re.compile(
    r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z",
    re.IGNORECASE,
)
_WINDOWS_INVALID_COMPONENT_CHARS = frozenset('<>:"|?*')


class DeterministicSdistError(ValueError):
    """A source distribution cannot be normalized without weakening integrity."""


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    modified_ns: int


def _is_reparse_point(path_stat: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(path_stat, "st_file_attributes", 0) & reparse_flag)


def _snapshot_from_stat(path_stat: os.stat_result) -> _FileSnapshot:
    return _FileSnapshot(
        device=path_stat.st_dev,
        inode=path_stat.st_ino,
        mode=path_stat.st_mode,
        links=path_stat.st_nlink,
        size=path_stat.st_size,
        modified_ns=path_stat.st_mtime_ns,
    )


def _regular_file_snapshot(
    path: str | Path,
    *,
    label: str,
) -> tuple[Path, _FileSnapshot]:
    lexical = Path(path).expanduser().absolute()
    try:
        lexical_stat = lexical.lstat()
    except OSError as exc:
        raise DeterministicSdistError(f"{label} is unavailable: {lexical}") from exc
    if (
        not stat.S_ISREG(lexical_stat.st_mode)
        or stat.S_ISLNK(lexical_stat.st_mode)
        or _is_reparse_point(lexical_stat)
        or lexical_stat.st_nlink != 1
        or lexical_stat.st_size <= 0
        or lexical_stat.st_size > _MAX_ARCHIVE_BYTES
    ):
        raise DeterministicSdistError(
            f"{label} must be a bounded regular single-link file: {lexical}"
        )
    try:
        resolved = lexical.resolve(strict=True)
        resolved_stat = resolved.stat()
    except OSError as exc:
        raise DeterministicSdistError(f"{label} cannot be resolved: {lexical}") from exc
    snapshot = _snapshot_from_stat(lexical_stat)
    if _snapshot_from_stat(resolved_stat) != snapshot:
        raise DeterministicSdistError(f"{label} changed while resolving: {lexical}")
    return lexical, snapshot


def _assert_regular_file_snapshot(
    path: Path,
    expected: _FileSnapshot,
    *,
    label: str,
) -> None:
    _resolved, observed = _regular_file_snapshot(path, label=label)
    if observed != expected:
        raise DeterministicSdistError(f"{label} changed during deterministic build")


def _real_directory(path: str | Path, *, label: str) -> tuple[Path, tuple[int, int]]:
    lexical = Path(path).expanduser().absolute()
    try:
        lexical_stat = lexical.lstat()
    except OSError as exc:
        raise DeterministicSdistError(f"{label} is unavailable: {lexical}") from exc
    is_junction = bool(hasattr(os.path, "isjunction") and os.path.isjunction(lexical))
    if (
        not stat.S_ISDIR(lexical_stat.st_mode)
        or stat.S_ISLNK(lexical_stat.st_mode)
        or _is_reparse_point(lexical_stat)
        or is_junction
    ):
        raise DeterministicSdistError(f"{label} must be a real directory: {lexical}")
    try:
        resolved = lexical.resolve(strict=True)
        resolved_stat = resolved.stat()
    except OSError as exc:
        raise DeterministicSdistError(f"{label} cannot be resolved: {lexical}") from exc
    identity = lexical_stat.st_dev, lexical_stat.st_ino
    if (resolved_stat.st_dev, resolved_stat.st_ino) != identity:
        raise DeterministicSdistError(f"{label} changed while resolving: {lexical}")
    return resolved, identity


def _assert_directory_identity(
    path: Path,
    expected: tuple[int, int],
    *,
    label: str,
) -> None:
    _resolved, observed = _real_directory(path, label=label)
    if observed != expected:
        raise DeterministicSdistError(f"{label} changed during deterministic build")


def _parse_source_date_epoch(value: object) -> int:
    if isinstance(value, bool):
        raise DeterministicSdistError("SOURCE_DATE_EPOCH must be a decimal integer")
    if isinstance(value, int):
        epoch = value
    elif isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]{0,9}", value):
        epoch = int(value)
    else:
        raise DeterministicSdistError("SOURCE_DATE_EPOCH must be a decimal integer")
    if epoch < 0 or epoch > _MAX_GZIP_EPOCH:
        raise DeterministicSdistError(f"SOURCE_DATE_EPOCH must be between 0 and {_MAX_GZIP_EPOCH}")
    return epoch


def _source_date_epoch() -> int | None:
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    return None if raw is None else _parse_source_date_epoch(raw)


def _safe_member_name(name: object, *, expected_root: str) -> str:
    if (
        not isinstance(name, str)
        or not name
        or len(name) > _MAX_MEMBER_NAME_CHARS
        or name.startswith("/")
        or name.endswith("/")
        or "\\" in name
        or "\x00" in name
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in name)
    ):
        raise DeterministicSdistError("sdist contains an unsafe member name")
    path = PurePosixPath(name)
    if (
        path.as_posix() != name
        or not path.parts
        or path.parts[0] != expected_root
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise DeterministicSdistError(f"sdist contains an unsafe member name: {name!r}")
    for part in path.parts:
        if (
            part.endswith((" ", "."))
            or any(character in _WINDOWS_INVALID_COMPONENT_CHARS for character in part)
            or _WINDOWS_RESERVED_COMPONENT.fullmatch(part) is not None
        ):
            raise DeterministicSdistError(f"sdist contains a nonportable member name: {name!r}")
    return name


def _portable_member_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _validate_pax_headers(
    member: tarfile.TarInfo,
    *,
    allow_mtime: bool,
) -> None:
    headers = member.pax_headers
    if len(headers) > _MAX_PAX_FIELDS:
        raise DeterministicSdistError("sdist member has too many PAX fields")
    allowed = _ALLOWED_INPUT_PAX_FIELDS if allow_mtime else frozenset({"path"})
    if not set(headers) <= allowed:
        raise DeterministicSdistError(
            f"sdist member has unexpected PAX fields: {sorted(set(headers) - allowed)}"
        )
    for key, value in headers.items():
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or len(key) > _MAX_PAX_FIELD_CHARS
            or len(value) > _MAX_PAX_FIELD_CHARS
        ):
            raise DeterministicSdistError("sdist member has oversized PAX metadata")
        if key == "path" and value != member.name:
            raise DeterministicSdistError("sdist member PAX path is not canonical")
        if key == "mtime" and _PAX_MTIME.fullmatch(value) is None:
            raise DeterministicSdistError("sdist member PAX mtime is invalid")


def _write_bounded_decompressed(
    source: BinaryIO,
    target: BinaryIO,
) -> int:
    """Expand exactly one gzip member without permitting a decompression bomb."""

    decoder = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    expanded_bytes = 0
    pending = b""
    while not decoder.eof:
        if not pending:
            pending = source.read(_CHUNK_BYTES)
            if not pending:
                break
        remaining = _MAX_DECOMPRESSED_BYTES - expanded_bytes
        output = decoder.decompress(
            pending,
            min(_CHUNK_BYTES, remaining + 1),
        )
        pending = decoder.unconsumed_tail
        if output:
            expanded_bytes += len(output)
            if expanded_bytes > _MAX_DECOMPRESSED_BYTES:
                raise DeterministicSdistError("sdist exceeds the decompressed byte limit")
            target.write(output)
        if decoder.eof:
            if decoder.unused_data or pending or source.read(1):
                raise DeterministicSdistError(
                    "sdist contains trailing bytes or multiple gzip members"
                )
            break
    if not decoder.eof:
        raise DeterministicSdistError("source distribution has a truncated gzip member")
    if expanded_bytes == 0:
        raise DeterministicSdistError("source distribution expands to no tar data")
    target.flush()
    target.seek(0)
    return expanded_bytes


def _read_exact(stream: BinaryIO, byte_count: int, *, label: str) -> bytes:
    if byte_count < 0:
        raise DeterministicSdistError(f"{label} has an invalid byte count")
    chunks: list[bytes] = []
    remaining = byte_count
    while remaining:
        chunk = stream.read(min(_CHUNK_BYTES, remaining))
        if not chunk:
            raise DeterministicSdistError(f"{label} is truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _parse_pax_payload(
    payload: bytes,
    *,
    allow_mtime: bool,
) -> dict[str, str]:
    if not payload or len(payload) > _MAX_PAX_PAYLOAD_BYTES:
        raise DeterministicSdistError("sdist PAX payload is outside the byte limit")
    fields: dict[str, str] = {}
    position = 0
    while position < len(payload):
        if len(fields) >= _MAX_PAX_FIELDS:
            raise DeterministicSdistError("sdist member has too many PAX fields")
        separator = payload.find(b" ", position, min(len(payload), position + 12))
        if separator < 0:
            raise DeterministicSdistError("sdist member PAX framing is invalid")
        raw_length = payload[position:separator]
        if (
            not raw_length
            or not raw_length.isdigit()
            or (len(raw_length) > 1 and raw_length.startswith(b"0"))
        ):
            raise DeterministicSdistError("sdist member PAX length is invalid")
        record_length = int(raw_length)
        record_end = position + record_length
        if (
            record_length < 5
            or record_end > len(payload)
            or payload[record_end - 1 : record_end] != b"\n"
        ):
            raise DeterministicSdistError("sdist member PAX framing is invalid")
        framed = payload[separator + 1 : record_end - 1]
        raw_key, equals, raw_value = framed.partition(b"=")
        if not raw_key or equals != b"=":
            raise DeterministicSdistError("sdist member PAX field is invalid")
        try:
            key = raw_key.decode("utf-8", "strict")
            value = raw_value.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise DeterministicSdistError("sdist member PAX field is not valid UTF-8") from exc
        if len(key) > _MAX_PAX_FIELD_CHARS or len(value) > _MAX_PAX_FIELD_CHARS:
            raise DeterministicSdistError("sdist member has oversized PAX metadata")
        if key in fields:
            raise DeterministicSdistError(f"sdist member has a duplicate PAX field: {key!r}")
        fields[key] = value
        position = record_end
    allowed = _ALLOWED_INPUT_PAX_FIELDS if allow_mtime else frozenset({"path"})
    if not set(fields) <= allowed:
        raise DeterministicSdistError(
            f"sdist member has unexpected PAX fields: {sorted(set(fields) - allowed)}"
        )
    if "mtime" in fields and _PAX_MTIME.fullmatch(fields["mtime"]) is None:
        raise DeterministicSdistError("sdist member PAX mtime is invalid")
    return fields


def _skip_member_payload(
    stream: BinaryIO,
    *,
    payload_bytes: int,
    expanded_bytes: int,
    label: str,
) -> None:
    padded_bytes = (payload_bytes + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE
    padded_bytes *= tarfile.BLOCKSIZE
    current = stream.tell()
    if current < 0 or current + padded_bytes > expanded_bytes:
        raise DeterministicSdistError(f"{label} is truncated")
    stream.seek(padded_bytes, os.SEEK_CUR)


def _preflight_tar_stream(
    stream: BinaryIO,
    *,
    expanded_bytes: int,
    expected_root: str,
    allow_mtime_pax: bool,
) -> None:
    """Bound physical tar/PAX parsing before ``tarfile`` sees the stream."""

    if (
        expanded_bytes < 2 * tarfile.BLOCKSIZE
        or expanded_bytes > _MAX_DECOMPRESSED_BYTES
        or expanded_bytes % tarfile.BLOCKSIZE
    ):
        raise DeterministicSdistError("sdist tar byte count is outside the supported range")
    stream.seek(0)
    pending_pax: dict[str, str] | None = None
    logical_members = 0
    physical_headers = 0
    total_pax_bytes = 0
    tar_metadata_bytes = 0
    content_bytes = 0
    seen: set[str] = set()
    portable_names: dict[str, str] = {}
    member_types: dict[str, str] = {}

    while stream.tell() < expanded_bytes:
        header = _read_exact(
            stream,
            tarfile.BLOCKSIZE,
            label="sdist tar header",
        )
        if header == _ZERO_BLOCK:
            second = _read_exact(
                stream,
                tarfile.BLOCKSIZE,
                label="sdist tar end marker",
            )
            if second != _ZERO_BLOCK:
                raise DeterministicSdistError("sdist tar has an incomplete end marker")
            while stream.tell() < expanded_bytes:
                chunk = stream.read(min(_CHUNK_BYTES, expanded_bytes - stream.tell()))
                if not chunk:
                    raise DeterministicSdistError("sdist tar padding is truncated")
                if any(chunk):
                    raise DeterministicSdistError(
                        "sdist contains nonzero bytes after the tar end marker"
                    )
            if pending_pax is not None:
                raise DeterministicSdistError("sdist ends with an unapplied PAX header")
            break

        physical_headers += 1
        tar_metadata_bytes += tarfile.BLOCKSIZE
        if physical_headers > 2 * _MAX_MEMBERS or tar_metadata_bytes > _MAX_TAR_METADATA_BYTES:
            raise DeterministicSdistError("sdist physical metadata exceeds the supported range")
        try:
            physical = tarfile.TarInfo.frombuf(
                header,
                encoding="utf-8",
                errors="strict",
            )
        except (LookupError, UnicodeError, tarfile.HeaderError) as exc:
            raise DeterministicSdistError("sdist tar header is invalid") from exc

        if physical.type in {tarfile.XHDTYPE, tarfile.SOLARIS_XHDTYPE}:
            if pending_pax is not None:
                raise DeterministicSdistError("sdist has consecutive PAX headers")
            if physical.name != "././@PaxHeader":
                raise DeterministicSdistError("sdist has a noncanonical extended PAX header")
            if (
                physical.size <= 0
                or physical.size > _MAX_PAX_PAYLOAD_BYTES
                or total_pax_bytes + physical.size > _MAX_TOTAL_PAX_BYTES
            ):
                raise DeterministicSdistError("sdist PAX payload is outside the byte limit")
            payload = _read_exact(
                stream,
                physical.size,
                label="sdist PAX payload",
            )
            pending_pax = _parse_pax_payload(
                payload,
                allow_mtime=allow_mtime_pax,
            )
            total_pax_bytes += physical.size
            tar_metadata_bytes += physical.size
            if tar_metadata_bytes > _MAX_TAR_METADATA_BYTES:
                raise DeterministicSdistError("sdist physical metadata exceeds the supported range")
            padding = (-physical.size) % tarfile.BLOCKSIZE
            if padding and any(
                _read_exact(
                    stream,
                    padding,
                    label="sdist PAX padding",
                )
            ):
                raise DeterministicSdistError("sdist PAX padding is nonzero")
            continue
        if physical.type == tarfile.XGLTYPE:
            raise DeterministicSdistError("sdist has unexpected global PAX metadata")

        headers = pending_pax or {}
        pending_pax = None
        name = _safe_member_name(
            headers.get("path", physical.name),
            expected_root=expected_root,
        )
        if name in seen:
            raise DeterministicSdistError(f"sdist contains a duplicate member: {name!r}")
        seen.add(name)
        portable_key = _portable_member_key(name)
        prior_name = portable_names.get(portable_key)
        if prior_name is not None and prior_name != name:
            raise DeterministicSdistError(
                f"sdist member names collide portably: {prior_name!r}, {name!r}"
            )
        portable_names[portable_key] = name
        logical_members += 1
        if logical_members > _MAX_MEMBERS:
            raise DeterministicSdistError("sdist member count is outside the supported range")
        if not isinstance(physical.mode, int) or physical.mode < 0 or physical.mode > 0o7777:
            raise DeterministicSdistError("sdist member mode is invalid")
        if physical.type == tarfile.DIRTYPE:
            if physical.size != 0:
                raise DeterministicSdistError("sdist directory has nonzero content")
            member_types[name] = "directory"
        elif physical.type in {tarfile.REGTYPE, tarfile.AREGTYPE}:
            if physical.size < 0 or physical.size > _MAX_MEMBER_BYTES:
                raise DeterministicSdistError("sdist member exceeds the per-member byte limit")
            content_bytes += physical.size
            if content_bytes > _MAX_EXPANDED_BYTES:
                raise DeterministicSdistError("sdist exceeds the expanded byte limit")
            member_types[name] = "file"
        else:
            raise DeterministicSdistError("sdist contains a link or special member")
        _skip_member_payload(
            stream,
            payload_bytes=physical.size,
            expanded_bytes=expanded_bytes,
            label="sdist member content",
        )
    else:
        raise DeterministicSdistError("sdist tar is missing its end marker")

    required = {
        expected_root: "directory",
        f"{expected_root}/PKG-INFO": "file",
        f"{expected_root}/pyproject.toml": "file",
    }
    if any(member_types.get(name) != kind for name, kind in required.items()):
        raise DeterministicSdistError(
            "sdist is missing its root directory, PKG-INFO, or pyproject.toml"
        )


@contextmanager
def _preflighted_tar(
    raw_stream: BinaryIO,
    *,
    expected_root: str,
    allow_mtime_pax: bool,
) -> Iterator[tarfile.TarFile]:
    with tempfile.TemporaryFile(mode="w+b") as expanded:
        expanded_bytes = _write_bounded_decompressed(raw_stream, expanded)
        _preflight_tar_stream(
            expanded,
            expanded_bytes=expanded_bytes,
            expected_root=expected_root,
            allow_mtime_pax=allow_mtime_pax,
        )
        expanded.seek(0)
        try:
            with tarfile.open(fileobj=expanded, mode="r:") as archive:
                yield archive
        except (EOFError, OSError, tarfile.TarError) as exc:
            raise DeterministicSdistError(
                "source distribution is not a valid bounded tar.gz archive"
            ) from exc


def _validated_members(
    archive: tarfile.TarFile,
    *,
    expected_root: str,
    allow_mtime_pax: bool,
) -> list[tarfile.TarInfo]:
    if archive.pax_headers:
        raise DeterministicSdistError("sdist has unexpected global PAX metadata")
    members: list[tarfile.TarInfo] = []
    while len(members) <= _MAX_MEMBERS:
        member = archive.next()
        if member is None:
            break
        members.append(member)
    if not members or len(members) > _MAX_MEMBERS:
        raise DeterministicSdistError("sdist member count is outside the supported range")
    seen: set[str] = set()
    portable_names: dict[str, str] = {}
    member_types: dict[str, str] = {}
    expanded_bytes = 0
    for member in members:
        name = _safe_member_name(member.name, expected_root=expected_root)
        if name in seen:
            raise DeterministicSdistError(f"sdist contains a duplicate member: {name!r}")
        seen.add(name)
        portable_key = _portable_member_key(name)
        prior_name = portable_names.get(portable_key)
        if prior_name is not None and prior_name != name:
            raise DeterministicSdistError(
                f"sdist member names collide portably: {prior_name!r}, {name!r}"
            )
        portable_names[portable_key] = name
        _validate_pax_headers(member, allow_mtime=allow_mtime_pax)
        if member.type == tarfile.DIRTYPE:
            if member.size != 0:
                raise DeterministicSdistError("sdist directory has nonzero content")
            member_types[name] = "directory"
        elif member.type in {tarfile.REGTYPE, tarfile.AREGTYPE}:
            if member.size < 0 or member.size > _MAX_MEMBER_BYTES:
                raise DeterministicSdistError("sdist member exceeds the per-member byte limit")
            expanded_bytes += member.size
            if expanded_bytes > _MAX_EXPANDED_BYTES:
                raise DeterministicSdistError("sdist exceeds the expanded byte limit")
            member_types[name] = "file"
        else:
            raise DeterministicSdistError("sdist contains a link or special member")
        if not isinstance(member.mode, int) or member.mode < 0 or member.mode > 0o7777:
            raise DeterministicSdistError("sdist member mode is invalid")
    required = {
        expected_root: "directory",
        f"{expected_root}/PKG-INFO": "file",
        f"{expected_root}/pyproject.toml": "file",
    }
    if any(member_types.get(name) != kind for name, kind in required.items()):
        raise DeterministicSdistError(
            "sdist is missing its root directory, PKG-INFO, or pyproject.toml"
        )
    return members


def _bounded_stream_sha256(
    stream: BinaryIO,
    *,
    expected_bytes: int,
) -> str:
    digest = hashlib.sha256()
    observed = 0
    while True:
        chunk = stream.read(min(_CHUNK_BYTES, expected_bytes - observed + 1))
        if not chunk:
            break
        observed += len(chunk)
        if observed > expected_bytes:
            raise DeterministicSdistError("sdist member expanded beyond its declared size")
        digest.update(chunk)
    if observed != expected_bytes:
        raise DeterministicSdistError(
            f"sdist member expanded to {observed} bytes, expected {expected_bytes}"
        )
    return digest.hexdigest()


def _archive_inventory(
    path: Path,
    *,
    expected_root: str,
    allow_mtime_pax: bool,
) -> tuple[list[dict[str, Any]], _FileSnapshot]:
    lexical, snapshot = _regular_file_snapshot(path, label="source distribution")
    records: list[dict[str, Any]] = []
    try:
        with lexical.open("rb") as raw_stream:
            if _snapshot_from_stat(os.fstat(raw_stream.fileno())) != snapshot:
                raise DeterministicSdistError("source distribution changed while opening")
            with _preflighted_tar(
                raw_stream,
                expected_root=expected_root,
                allow_mtime_pax=allow_mtime_pax,
            ) as archive:
                members = _validated_members(
                    archive,
                    expected_root=expected_root,
                    allow_mtime_pax=allow_mtime_pax,
                )
                for member in members:
                    if member.isdir():
                        content_sha256 = None
                        member_type = "directory"
                    else:
                        extracted = archive.extractfile(member)
                        if extracted is None:
                            raise DeterministicSdistError("sdist member content is unavailable")
                        with extracted:
                            content_sha256 = _bounded_stream_sha256(
                                extracted,
                                expected_bytes=member.size,
                            )
                        member_type = "file"
                    records.append(
                        {
                            "name": member.name,
                            "type": member_type,
                            "mode": member.mode,
                            "size": member.size,
                            "sha256": content_sha256,
                        }
                    )
    except (EOFError, OSError, tarfile.TarError, zlib.error) as exc:
        raise DeterministicSdistError(
            "source distribution is not a valid bounded tar.gz archive"
        ) from exc
    _assert_regular_file_snapshot(lexical, snapshot, label="source distribution")
    return records, snapshot


def _write_normalized_archive(
    source_path: Path,
    raw_output: BinaryIO,
    *,
    expected_root: str,
    expected_source_snapshot: _FileSnapshot,
    source_date_epoch: int,
) -> None:
    try:
        with (
            source_path.open("rb") as raw_stream,
            _preflighted_tar(
                raw_stream,
                expected_root=expected_root,
                allow_mtime_pax=True,
            ) as source,
        ):
            if _snapshot_from_stat(os.fstat(raw_stream.fileno())) != expected_source_snapshot:
                raise DeterministicSdistError(
                    "source distribution changed while opening for normalization"
                )
            members = _validated_members(
                source,
                expected_root=expected_root,
                allow_mtime_pax=True,
            )
            with (
                gzip.GzipFile(
                    filename="",
                    mode="wb",
                    compresslevel=9,
                    fileobj=raw_output,
                    mtime=source_date_epoch,
                ) as compressed,
                tarfile.open(
                    fileobj=compressed,
                    mode="w",
                    format=tarfile.PAX_FORMAT,
                    encoding="utf-8",
                ) as target,
            ):
                for member in members:
                    normalized = tarfile.TarInfo(member.name)
                    normalized.type = member.type
                    normalized.mode = member.mode
                    normalized.uid = 0
                    normalized.gid = 0
                    normalized.uname = ""
                    normalized.gname = ""
                    normalized.mtime = source_date_epoch
                    normalized.size = member.size
                    if "path" in member.pax_headers:
                        normalized.pax_headers = {"path": member.name}
                    if member.isdir():
                        target.addfile(normalized)
                        continue
                    extracted = source.extractfile(member)
                    if extracted is None:
                        raise DeterministicSdistError("sdist member content is unavailable")
                    with extracted:
                        target.addfile(normalized, extracted)
            if _snapshot_from_stat(os.fstat(raw_stream.fileno())) != expected_source_snapshot:
                raise DeterministicSdistError("source distribution changed during normalization")
            raw_output.flush()
            os.fsync(raw_output.fileno())
    except (EOFError, OSError, tarfile.TarError, zlib.error) as exc:
        raise DeterministicSdistError("source distribution could not be normalized") from exc


def _validate_normalized_metadata(
    path: Path,
    *,
    expected_root: str,
    source_date_epoch: int,
) -> None:
    lexical, snapshot = _regular_file_snapshot(
        path,
        label="normalized source distribution",
    )
    try:
        with lexical.open("rb") as raw_stream:
            if _snapshot_from_stat(os.fstat(raw_stream.fileno())) != snapshot:
                raise DeterministicSdistError(
                    "normalized source distribution changed while opening"
                )
            header = raw_stream.read(10)
            if (
                len(header) != 10
                or header[:4] != b"\x1f\x8b\x08\x00"
                or int.from_bytes(header[4:8], "little") != source_date_epoch
            ):
                raise DeterministicSdistError("sdist gzip header is not canonical")
            raw_stream.seek(0)
            with _preflighted_tar(
                raw_stream,
                expected_root=expected_root,
                allow_mtime_pax=False,
            ) as archive:
                members = _validated_members(
                    archive,
                    expected_root=expected_root,
                    allow_mtime_pax=False,
                )
                for member in members:
                    if (
                        member.mtime != source_date_epoch
                        or member.uid != 0
                        or member.gid != 0
                        or member.uname
                        or member.gname
                    ):
                        raise DeterministicSdistError("sdist member metadata is not canonical")
            if _snapshot_from_stat(os.fstat(raw_stream.fileno())) != snapshot:
                raise DeterministicSdistError(
                    "normalized source distribution changed during validation"
                )
    except (EOFError, OSError, tarfile.TarError, zlib.error) as exc:
        raise DeterministicSdistError("normalized source distribution is invalid") from exc
    _assert_regular_file_snapshot(
        lexical,
        snapshot,
        label="normalized source distribution",
    )


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _normalize_sdist_archive(
    path: str | Path,
    source_date_epoch: int,
) -> Path:
    epoch = _parse_source_date_epoch(source_date_epoch)
    source_path, source_snapshot = _regular_file_snapshot(
        path,
        label="source distribution",
    )
    output_directory, directory_identity = _real_directory(
        source_path.parent,
        label="source distribution directory",
    )
    if source_path.parent.resolve(strict=True) != output_directory:
        raise DeterministicSdistError("source distribution is outside its validated directory")
    if _SAFE_FILENAME.fullmatch(source_path.name) is None:
        raise DeterministicSdistError("source distribution filename is unsafe")
    expected_root = source_path.name.removesuffix(".tar.gz")
    if "/" in expected_root or "\\" in expected_root:
        raise DeterministicSdistError("source distribution root is unsafe")
    original_inventory, observed_snapshot = _archive_inventory(
        source_path,
        expected_root=expected_root,
        allow_mtime_pax=True,
    )
    if observed_snapshot != source_snapshot:
        raise DeterministicSdistError("source distribution changed before deterministic build")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{source_path.name}.",
        suffix=".tmp",
        dir=output_directory,
    )
    temporary_path = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, stat.S_IMODE(source_snapshot.mode))
        with os.fdopen(descriptor, "wb") as raw_output:
            descriptor = -1
            _write_normalized_archive(
                source_path,
                raw_output,
                expected_root=expected_root,
                expected_source_snapshot=source_snapshot,
                source_date_epoch=epoch,
            )
        _assert_regular_file_snapshot(
            source_path,
            source_snapshot,
            label="source distribution",
        )
        normalized_inventory, _temporary_snapshot = _archive_inventory(
            temporary_path,
            expected_root=expected_root,
            allow_mtime_pax=False,
        )
        if normalized_inventory != original_inventory:
            raise DeterministicSdistError(
                "deterministic sdist rewrite changed member content or structure"
            )
        _validate_normalized_metadata(
            temporary_path,
            expected_root=expected_root,
            source_date_epoch=epoch,
        )
        temporary_bytes, temporary_sha256 = _bounded_file_sha256(temporary_path)
        _assert_regular_file_snapshot(
            temporary_path,
            _temporary_snapshot,
            label="normalized source distribution",
        )
        _assert_directory_identity(
            output_directory,
            directory_identity,
            label="source distribution directory",
        )
        _assert_regular_file_snapshot(
            source_path,
            source_snapshot,
            label="source distribution",
        )
        os.replace(temporary_path, source_path)
        _fsync_directory(output_directory)
        installed_path, installed_snapshot = _regular_file_snapshot(
            source_path,
            label="installed normalized source distribution",
        )
        if installed_snapshot != _temporary_snapshot:
            raise DeterministicSdistError(
                "installed normalized source distribution changed during replacement"
            )
        installed_inventory, observed_installed_snapshot = _archive_inventory(
            installed_path,
            expected_root=expected_root,
            allow_mtime_pax=False,
        )
        if (
            observed_installed_snapshot != installed_snapshot
            or installed_inventory != original_inventory
        ):
            raise DeterministicSdistError(
                "installed normalized source distribution changed after replacement"
            )
        installed_bytes, installed_sha256 = _bounded_file_sha256(installed_path)
        if installed_bytes != temporary_bytes or installed_sha256 != temporary_sha256:
            raise DeterministicSdistError(
                "installed normalized source distribution bytes changed after replacement"
            )
        _validate_normalized_metadata(
            installed_path,
            expected_root=expected_root,
            source_date_epoch=epoch,
        )
        _assert_regular_file_snapshot(
            installed_path,
            installed_snapshot,
            label="installed normalized source distribution",
        )
        return installed_path
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validated_backend_filename(value: object) -> str:
    if (
        not isinstance(value, str)
        or Path(value).name != value
        or _SAFE_FILENAME.fullmatch(value) is None
    ):
        raise DeterministicSdistError("Setuptools returned an unsafe sdist filename")
    return value


def build_sdist(
    sdist_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    """Delegate to Setuptools and normalize only when SOURCE_DATE_EPOCH is set."""

    epoch = _source_date_epoch()
    if epoch is None:
        return _setuptools_backend.build_sdist(
            sdist_directory,
            config_settings=config_settings,
        )
    directory, directory_identity = _real_directory(
        sdist_directory,
        label="source distribution directory",
    )
    filename = _setuptools_backend.build_sdist(
        str(directory),
        config_settings=config_settings,
    )
    _assert_directory_identity(
        directory,
        directory_identity,
        label="source distribution directory",
    )
    safe_filename = _validated_backend_filename(filename)
    _normalize_sdist_archive(directory / safe_filename, epoch)
    return safe_filename


def __getattr__(name: str) -> Any:
    """Delegate every other PEP 517 hook to Setuptools unchanged."""

    return getattr(_setuptools_backend, name)


def _bounded_file_sha256(path: Path) -> tuple[int, str]:
    resolved, snapshot = _regular_file_snapshot(path, label="source distribution")
    digest = hashlib.sha256()
    observed = 0
    with resolved.open("rb") as stream:
        if _snapshot_from_stat(os.fstat(stream.fileno())) != snapshot:
            raise DeterministicSdistError("source distribution changed while opening for hashing")
        while True:
            chunk = stream.read(_CHUNK_BYTES)
            if not chunk:
                break
            observed += len(chunk)
            if observed > _MAX_ARCHIVE_BYTES:
                raise DeterministicSdistError("source distribution exceeds the archive byte limit")
            digest.update(chunk)
        if _snapshot_from_stat(os.fstat(stream.fileno())) != snapshot:
            raise DeterministicSdistError("source distribution changed while hashing")
    if observed != snapshot.size:
        raise DeterministicSdistError("source distribution changed while hashing")
    _assert_regular_file_snapshot(
        resolved,
        snapshot,
        label="source distribution",
    )
    return observed, digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build one Setuptools sdist with deterministic archive metadata."
    )
    parser.add_argument("--sdist-dir", type=Path, required=True)
    parser.add_argument("--source-date-epoch")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    raw_epoch = (
        args.source_date_epoch
        if args.source_date_epoch is not None
        else os.environ.get("SOURCE_DATE_EPOCH")
    )
    if raw_epoch is None:
        sys.stderr.write("deterministic sdist build requires SOURCE_DATE_EPOCH\n")
        return 2
    try:
        epoch = _parse_source_date_epoch(raw_epoch)
        previous_epoch = os.environ.get("SOURCE_DATE_EPOCH")
        os.environ["SOURCE_DATE_EPOCH"] = str(epoch)
        try:
            filename = build_sdist(str(args.sdist_dir))
        finally:
            if previous_epoch is None:
                os.environ.pop("SOURCE_DATE_EPOCH", None)
            else:
                os.environ["SOURCE_DATE_EPOCH"] = previous_epoch
        directory, _identity = _real_directory(
            args.sdist_dir,
            label="source distribution directory",
        )
        byte_count, file_sha256 = _bounded_file_sha256(directory / filename)
    except (DeterministicSdistError, OSError, tarfile.TarError, ValueError) as exc:
        sys.stderr.write(f"deterministic sdist build: {exc}\n")
        return 1
    sys.stdout.write(
        json.dumps(
            {
                "schema": RESULT_SCHEMA,
                "status": "built",
                "filename": filename,
                "bytes": byte_count,
                "sha256": file_sha256,
                "source_date_epoch": epoch,
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
