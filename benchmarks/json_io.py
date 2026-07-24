"""Shared strict, bounded JSON file loading for benchmark evidence."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class StrictJsonError(ValueError):
    """A JSON evidence file is unsafe, malformed, or outside its limits."""


@dataclass(frozen=True, slots=True)
class StrictJsonLimits:
    """Serialized JSON limits enforced before a decoded value is returned."""

    max_bytes: int
    max_line_chars: int
    max_depth: int

    def __post_init__(self) -> None:
        for name in ("max_bytes", "max_line_chars", "max_depth"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class StrictJsonDocument:
    """Decoded JSON plus evidence about the exact serialized file."""

    value: Any
    byte_count: int
    file_sha256: str


@dataclass(frozen=True, slots=True)
class StrictFileEvidence:
    """Bounded evidence for the exact bytes of one regular file."""

    byte_count: int
    file_sha256: str


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for key, value in pairs:
        if key in decoded:
            raise StrictJsonError(f"duplicate JSON object key: {key}")
        decoded[key] = value
    return decoded


def _finite_json_float(value: str) -> float:
    decoded = float(value)
    if not math.isfinite(decoded):
        raise StrictJsonError("JSON numbers must be finite")
    return decoded


def _reject_json_constant(value: str) -> None:
    raise StrictJsonError(f"non-standard JSON constant is forbidden: {value}")


def _validate_json_text(raw: str, *, limits: StrictJsonLimits, label: str) -> None:
    depth = 0
    line_chars = 0
    in_string = False
    escaped = False
    previous_was_cr = False
    for character in raw:
        if character == "\r":
            line_chars = 0
            previous_was_cr = True
        elif character == "\n":
            if not previous_was_cr:
                line_chars = 0
            previous_was_cr = False
        else:
            previous_was_cr = False
            line_chars += 1
            if line_chars > limits.max_line_chars:
                raise StrictJsonError(
                    f"{label} exceeds {limits.max_line_chars} characters on one line"
                )
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > limits.max_depth:
                raise StrictJsonError(
                    f"{label} exceeds JSON depth {limits.max_depth}"
                )
        elif character in "]}":
            depth = max(0, depth - 1)


@contextmanager
def _open_regular_file(
    path: Path,
    *,
    label: str,
) -> Iterator[tuple[int, os.stat_result]]:
    try:
        candidate_stat = path.lstat()
    except OSError as exc:
        raise StrictJsonError(f"could not inspect {label}: {path}") from exc
    if not stat.S_ISREG(candidate_stat.st_mode):
        raise StrictJsonError(f"{label} path must be a regular file: {path}")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StrictJsonError(f"could not open {label}: {path}") from exc

    primary_error: BaseException | None = None
    try:
        try:
            file_stat = os.fstat(descriptor)
        except OSError as exc:
            raise StrictJsonError(f"could not inspect open {label}: {path}") from exc
        if not stat.S_ISREG(file_stat.st_mode):
            raise StrictJsonError(f"{label} path must be a regular file: {path}")
        yield descriptor, file_stat
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(descriptor)
        except OSError as exc:
            if primary_error is None:
                raise StrictJsonError(f"could not close {label}: {path}") from exc


def _read_bounded_regular_file(
    path: Path,
    *,
    limits: StrictJsonLimits,
    label: str,
) -> bytes:
    with _open_regular_file(path, label=label) as (descriptor, file_stat):
        if file_stat.st_size > limits.max_bytes:
            raise StrictJsonError(f"{label} exceeds {limits.max_bytes} bytes")
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                block = os.read(
                    descriptor,
                    min(64 * 1024, limits.max_bytes - total + 1),
                )
            except OSError as exc:
                raise StrictJsonError(f"could not read {label}: {path}") from exc
            if not block:
                break
            total += len(block)
            if total > limits.max_bytes:
                raise StrictJsonError(f"{label} exceeds {limits.max_bytes} bytes")
            chunks.append(block)
        return b"".join(chunks)


def hash_bounded_regular_file(
    path: str | Path,
    *,
    max_bytes: int,
    label: str = "input file",
) -> StrictFileEvidence:
    """Hash one regular file while enforcing the limit during the read."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise TypeError("max_bytes must be an integer")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if not isinstance(label, str) or not label:
        raise TypeError("label must be a non-empty string")
    input_path = Path(path)
    with _open_regular_file(input_path, label=label) as (descriptor, file_stat):
        if file_stat.st_size > max_bytes:
            raise StrictJsonError(f"{label} exceeds {max_bytes} bytes")
        digest = hashlib.sha256()
        total = 0
        while True:
            try:
                block = os.read(
                    descriptor,
                    min(1024 * 1024, max_bytes - total + 1),
                )
            except OSError as exc:
                raise StrictJsonError(
                    f"could not read {label}: {input_path}"
                ) from exc
            if not block:
                break
            total += len(block)
            if total > max_bytes:
                raise StrictJsonError(f"{label} exceeds {max_bytes} bytes")
            digest.update(block)
        return StrictFileEvidence(
            byte_count=total,
            file_sha256=digest.hexdigest(),
        )


def load_strict_json_file(
    path: str | Path,
    *,
    limits: StrictJsonLimits,
    label: str = "JSON input",
) -> StrictJsonDocument:
    """Read one regular UTF-8 file and reject unsafe JSON before decoding."""

    if not isinstance(limits, StrictJsonLimits):
        raise TypeError("limits must be a StrictJsonLimits value")
    if not isinstance(label, str) or not label:
        raise TypeError("label must be a non-empty string")
    input_path = Path(path)
    encoded = _read_bounded_regular_file(
        input_path,
        limits=limits,
        label=label,
    )
    try:
        raw = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StrictJsonError(f"{label} must be valid UTF-8: {input_path}") from exc
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    if not raw.strip():
        raise StrictJsonError(f"{label} cannot be empty: {input_path}")
    _validate_json_text(raw, limits=limits, label=label)
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_strict_json_object,
            parse_float=_finite_json_float,
            parse_constant=_reject_json_constant,
        )
    except StrictJsonError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise StrictJsonError(f"{label} is not valid strict JSON: {exc}") from exc
    return StrictJsonDocument(
        value=value,
        byte_count=len(encoded),
        file_sha256=hashlib.sha256(encoded).hexdigest(),
    )
