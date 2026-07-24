"""Shared resource limits for source-history ingestion."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

_MIB = 1024 * 1024


class SourceLimitError(ValueError):
    """Raised when source history exceeds a configured resource boundary."""


@dataclass(frozen=True, slots=True)
class SourceLimits:
    """Hard limits applied before source history enters compilation.

    ``max_input_bytes`` bounds UTF-8 JSON/JSONL input and archive files.
    ``max_record_bytes`` and ``max_total_record_bytes`` bound the canonical
    JSON size of records supplied through either serialized input or the
    direct Python API. ``max_json_depth`` is enforced before serialized JSON
    decoding and while direct Python values are traversed.
    """

    max_input_bytes: int = 64 * _MIB
    max_records: int = 100_000
    max_line_chars: int = 1 * _MIB
    max_record_bytes: int = 4 * _MIB
    max_total_record_bytes: int = 64 * _MIB
    max_json_depth: int = 128

    def __post_init__(self) -> None:
        for name in (
            "max_input_bytes",
            "max_records",
            "max_line_chars",
            "max_record_bytes",
            "max_total_record_bytes",
            "max_json_depth",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_input_bytes": self.max_input_bytes,
            "max_records": self.max_records,
            "max_line_chars": self.max_line_chars,
            "max_record_bytes": self.max_record_bytes,
            "max_total_record_bytes": self.max_total_record_bytes,
            "max_json_depth": self.max_json_depth,
        }


DEFAULT_SOURCE_LIMITS = SourceLimits()


def resolve_source_limits(limits: SourceLimits | None) -> SourceLimits:
    if limits is None:
        return DEFAULT_SOURCE_LIMITS
    if not isinstance(limits, SourceLimits):
        raise TypeError("source limits must be a SourceLimits value")
    return limits


def _json_string_utf8_size(value: str, add: Any) -> None:
    add(2)
    for character in value:
        codepoint = ord(character)
        if character in {'"', "\\"} or character in {"\b", "\f", "\n", "\r", "\t"}:
            add(2)
        elif codepoint <= 0x1F:
            add(6)
        elif codepoint <= 0x7F:
            add(1)
        elif codepoint <= 0x7FF:
            add(2)
        elif 0xD800 <= codepoint <= 0xDFFF:
            raise TypeError("source records cannot contain unpaired Unicode surrogates")
        elif codepoint <= 0xFFFF:
            add(3)
        else:
            add(4)


def bounded_json_utf8_size(
    value: Any,
    *,
    max_bytes: int,
    max_depth: int,
    label: str,
) -> int:
    """Measure compact UTF-8 JSON without allocating the serialized value."""

    size = 0
    active_containers: set[int] = set()

    def add(amount: int) -> None:
        nonlocal size
        size += amount
        if size > max_bytes:
            raise SourceLimitError(f"{label} exceeds {max_bytes} UTF-8 JSON bytes")

    def visit(current: Any, depth: int) -> None:
        if current is None:
            add(4)
            return
        if current is True:
            add(4)
            return
        if current is False:
            add(5)
            return
        if isinstance(current, str):
            _json_string_utf8_size(current, add)
            return
        if isinstance(current, int) and not isinstance(current, bool):
            try:
                encoded = str(current)
            except ValueError as exc:
                raise TypeError("source integers must have a bounded JSON representation") from exc
            add(len(encoded))
            return
        if isinstance(current, float):
            if not math.isfinite(current):
                raise TypeError("source numbers must be finite")
            add(len(json.dumps(current, allow_nan=False)))
            return
        if isinstance(current, list):
            if depth >= max_depth:
                raise SourceLimitError(
                    f"{label} exceeds supported JSON nesting depth of {max_depth}"
                )
            container_id = id(current)
            if container_id in active_containers:
                raise TypeError("source records cannot contain cyclic JSON values")
            active_containers.add(container_id)
            try:
                add(2)
                for index, entry in enumerate(current):
                    if index:
                        add(1)
                    visit(entry, depth + 1)
            finally:
                active_containers.remove(container_id)
            return
        if isinstance(current, dict):
            if depth >= max_depth:
                raise SourceLimitError(
                    f"{label} exceeds supported JSON nesting depth of {max_depth}"
                )
            if not all(isinstance(key, str) for key in current):
                raise TypeError("source object keys must be strings")
            container_id = id(current)
            if container_id in active_containers:
                raise TypeError("source records cannot contain cyclic JSON values")
            active_containers.add(container_id)
            try:
                add(2)
                for index, (key, entry) in enumerate(current.items()):
                    if index:
                        add(1)
                    _json_string_utf8_size(key, add)
                    add(1)
                    visit(entry, depth + 1)
            finally:
                active_containers.remove(container_id)
            return
        raise TypeError("source records must contain only JSON values")

    try:
        visit(value, 0)
    except RecursionError as exc:
        raise TypeError("source records exceed the supported JSON nesting depth") from exc
    return size


def source_value_size(value: Any, *, limits: SourceLimits, index: int) -> int:
    return bounded_json_utf8_size(
        value,
        max_bytes=limits.max_record_bytes,
        max_depth=limits.max_json_depth,
        label=f"source record {index}",
    )


def add_source_size(
    total: int,
    record_size: int,
    *,
    limits: SourceLimits,
) -> int:
    updated = total + record_size
    if updated > limits.max_total_record_bytes:
        raise SourceLimitError(
            f"source records exceed {limits.max_total_record_bytes} total UTF-8 JSON bytes"
        )
    return updated
