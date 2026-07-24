"""Append-only cold storage for source events referenced by compiled memory."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text
from .file_lock import close_lock_file, open_lock_file, try_lock_file
from .io import _decode_strict_json, _read_limited_text
from .limits import (
    SourceLimitError,
    SourceLimits,
    add_source_size,
    resolve_source_limits,
    source_value_size,
)
from .models import ProvenanceSpan, SourceRecord, source_digest

ARCHIVE_ENTRY_SCHEMA = "ctxc-source-archive-entry-0.1"
ARCHIVE_REPORT_SCHEMA = "ctxc-source-archive-report-0.1"
LEGACY_ARCHIVE_SCHEMA = "ctxc-source-archive-legacy-jsonl"
ARCHIVE_GENESIS_SHA256 = "0" * 64

_SHA256 = re.compile(r"[0-9a-f]{64}")
_ARCHIVE_OPEN_ATTEMPTS = 3
_SOURCE_RECORD_FIELDS = frozenset(
    {
        "id",
        "sequence",
        "role",
        "content",
        "timestamp",
        "metadata",
        "content_sha256",
        "record_sha256",
    }
)
_ARCHIVE_ENTRY_FIELDS = frozenset(
    {
        "schema",
        "position",
        "previous_entry_sha256",
        "source",
        "entry_sha256",
    }
)
_ARCHIVE_ENTRY_MARKERS = frozenset(
    {
        "schema",
        "position",
        "previous_entry_sha256",
        "source",
        "entry_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class ArchiveReport:
    passed: bool
    records: int
    digest: str
    issues: tuple[str, ...] = field(default_factory=tuple)
    schema: str = ARCHIVE_REPORT_SCHEMA
    archive_schema: str | None = None
    chain_head_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class _ArchiveState:
    records: tuple[SourceRecord, ...]
    archive_schema: str
    chain_head_sha256: str | None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _entry_payload(
    *,
    position: int,
    previous_entry_sha256: str,
    source: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": ARCHIVE_ENTRY_SCHEMA,
        "position": position,
        "previous_entry_sha256": previous_entry_sha256,
        "source": source,
    }


def _entry_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _validate_expected_chain_head(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("expected_chain_head must be a string or null")
    if _SHA256.fullmatch(value) is None:
        raise ValueError("expected_chain_head must be 64 lowercase hexadecimal characters")
    return value


class SourceArchive:
    """A bounded, logically append-only, hash-chained JSONL event archive.

    Compilation may discard low-value text from active context, but source
    events remain append-only here. Existing ids and sequence numbers can
    never be overwritten with different data. A caller-retained chain head can
    be supplied as an optimistic concurrency and rollback-detection anchor.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        lock_timeout: float = 5.0,
        source_limits: SourceLimits | None = None,
    ) -> None:
        if (
            isinstance(lock_timeout, bool)
            or not isinstance(lock_timeout, (int, float))
            or not math.isfinite(lock_timeout)
            or lock_timeout < 0
        ):
            raise ValueError("lock_timeout must be a finite non-negative number")
        self.directory = Path(directory)
        self.events_path = self.directory / "events.jsonl"
        self.lock_path = self.directory / ".append.lock"
        self.lock_timeout = float(lock_timeout)
        self.source_limits = resolve_source_limits(source_limits)

    def append(
        self,
        records: Iterable[SourceRecord],
        *,
        expected_chain_head: str | None = None,
    ) -> int:
        expected_head = _validate_expected_chain_head(expected_chain_head)
        incoming: list[SourceRecord] = []
        incoming_size = 0
        for index, record in enumerate(records):
            if index >= self.source_limits.max_records:
                raise SourceLimitError(
                    f"source record count exceeds {self.source_limits.max_records} records"
                )
            if not isinstance(record, SourceRecord):
                raise TypeError("archive records must be SourceRecord values")
            record.ensure_integrity()
            record_size = source_value_size(
                record.to_dict(),
                limits=self.source_limits,
                index=index,
            )
            incoming_size = add_source_size(
                incoming_size,
                record_size,
                limits=self.source_limits,
            )
            incoming.append(record)
        if not incoming and expected_head is None:
            return 0
        # Every incoming record and the optional anchor are validated before
        # creating or touching the archive. This prevents a stale or externally
        # mutated record from turning a successful append into an immediately
        # corrupt event log.
        self.directory.mkdir(parents=True, exist_ok=True)
        lock_fd = self._acquire_lock()
        archive_error: BaseException | None = None
        try:
            state = self._load_state()
            self._require_expected_head(state.chain_head_sha256, expected_head)
            existing = list(state.records)
            by_id = {record.id: record for record in existing}
            by_sequence = {record.sequence: record for record in existing}
            accepted: list[SourceRecord] = []
            for record in incoming:
                same_id = by_id.get(record.id)
                if same_id is not None:
                    if same_id == record:
                        continue
                    raise ValueError(f"immutable source id collision: {record.id}")
                same_sequence = by_sequence.get(record.sequence)
                if same_sequence is not None:
                    raise ValueError(
                        f"immutable source sequence collision: {record.sequence} "
                        f"({same_sequence.id} versus {record.id})"
                    )
                by_id[record.id] = record
                by_sequence[record.sequence] = record
                accepted.append(record)
            if not accepted:
                return 0
            combined = sorted(
                [*existing, *accepted],
                key=lambda entry: entry.sequence,
            )
            payload, _chain_head = self._serialize_records(combined)
            atomic_write_text(self.events_path, payload)
            return len(accepted)
        except BaseException as exc:
            archive_error = exc
            raise
        finally:
            close_lock_file(lock_fd, prior_error=archive_error)

    def load(
        self,
        *,
        expected_chain_head: str | None = None,
    ) -> list[SourceRecord]:
        expected_head = _validate_expected_chain_head(expected_chain_head)
        state = self._load_state()
        self._require_expected_head(state.chain_head_sha256, expected_head)
        return list(state.records)

    def _load_state(self) -> _ArchiveState:
        try:
            raw = self._read_events_text()
            if raw is None:
                return _ArchiveState((), ARCHIVE_ENTRY_SCHEMA, ARCHIVE_GENESIS_SHA256)
            values = self._decode_lines(raw)
            if not values:
                return _ArchiveState((), ARCHIVE_ENTRY_SCHEMA, ARCHIVE_GENESIS_SHA256)
            first = values[0]
            if isinstance(first, dict) and _ARCHIVE_ENTRY_MARKERS.intersection(first):
                return self._decode_chained(values)
            return self._decode_legacy(values)
        except SourceLimitError:
            raise
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid archive: {exc}") from exc

    def _read_events_text(self) -> str | None:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOINHERIT", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for attempt in range(_ARCHIVE_OPEN_ATTEMPTS):
            try:
                candidate_stat = self.events_path.lstat()
            except FileNotFoundError:
                return None
            if not stat.S_ISREG(candidate_stat.st_mode):
                raise ValueError(
                    f"archive events path must be a regular file: {self.events_path}"
                )
            if candidate_stat.st_nlink != 1:
                raise ValueError(
                    f"archive events path must not have hard links: {self.events_path}"
                )
            try:
                descriptor = os.open(self.events_path, flags)
            except FileNotFoundError:
                if attempt + 1 < _ARCHIVE_OPEN_ATTEMPTS:
                    continue
                raise ValueError(
                    f"archive events path changed while opening: {self.events_path}"
                ) from None
            except PermissionError:
                raise
            except OSError as exc:
                raise ValueError(
                    f"archive events path could not be opened safely: {self.events_path}"
                ) from exc
            try:
                opened_stat = os.fstat(descriptor)
                if not stat.S_ISREG(opened_stat.st_mode):
                    raise ValueError(
                        f"archive events path must be a regular file: {self.events_path}"
                    )
                if opened_stat.st_nlink != 1:
                    raise ValueError(
                        "archive events path must not have hard links: "
                        f"{self.events_path}"
                    )
                if (
                    candidate_stat.st_dev,
                    candidate_stat.st_ino,
                ) != (
                    opened_stat.st_dev,
                    opened_stat.st_ino,
                ):
                    if attempt + 1 < _ARCHIVE_OPEN_ATTEMPTS:
                        continue
                    raise ValueError(
                        f"archive events path changed while opening: {self.events_path}"
                    )
                if opened_stat.st_size > self.source_limits.max_input_bytes:
                    raise SourceLimitError(
                        f"archive exceeds {self.source_limits.max_input_bytes} bytes"
                    )
                os.set_inheritable(descriptor, False)
                stream = os.fdopen(
                    descriptor,
                    "r",
                    encoding="utf-8",
                    newline="",
                )
                descriptor = -1
                with stream:
                    return _read_limited_text(
                        stream,
                        max_input_bytes=self.source_limits.max_input_bytes,
                        max_line_chars=self.source_limits.max_line_chars,
                        label="archive",
                        limit_error=SourceLimitError,
                    )
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
        raise ValueError(
            f"archive events path changed while opening: {self.events_path}"
        )

    def _decode_lines(self, raw: str) -> list[Any]:
        values: list[Any] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            if len(values) >= self.source_limits.max_records:
                raise SourceLimitError(
                    f"source record count exceeds {self.source_limits.max_records} records"
                )
            try:
                value = _decode_strict_json(
                    line,
                    max_depth=self.source_limits.max_json_depth + 1,
                    label="archive JSON",
                    limit_error=SourceLimitError,
                )
            except SourceLimitError:
                raise
            except ValueError as exc:
                raise ValueError(
                    f"invalid JSON archive record at line {line_number}: {exc}"
                ) from exc
            values.append(value)
        return values

    def _decode_chained(self, values: list[Any]) -> _ArchiveState:
        records: list[SourceRecord] = []
        total_size = 0
        previous = ARCHIVE_GENESIS_SHA256
        for position, value in enumerate(values):
            if not isinstance(value, dict):
                raise TypeError(f"archive entry {position} must be an object")
            if set(value) != _ARCHIVE_ENTRY_FIELDS:
                raise ValueError(
                    f"archive entry {position} fields do not match {ARCHIVE_ENTRY_SCHEMA}"
                )
            if value["schema"] != ARCHIVE_ENTRY_SCHEMA:
                raise ValueError(f"archive entry {position} has an unsupported schema")
            claimed_position = value["position"]
            if (
                isinstance(claimed_position, bool)
                or not isinstance(claimed_position, int)
                or claimed_position != position
            ):
                raise ValueError(f"archive entry {position} has an invalid position")
            claimed_previous = value["previous_entry_sha256"]
            if not isinstance(claimed_previous, str) or _SHA256.fullmatch(
                claimed_previous
            ) is None:
                raise ValueError(
                    f"archive entry {position} has an invalid previous-entry hash"
                )
            if claimed_previous != previous:
                raise ValueError(
                    f"archive entry {position} does not link to the preceding entry"
                )
            claimed_digest = value["entry_sha256"]
            if not isinstance(claimed_digest, str) or _SHA256.fullmatch(
                claimed_digest
            ) is None:
                raise ValueError(f"archive entry {position} has an invalid entry hash")
            raw_source = value["source"]
            if not isinstance(raw_source, dict):
                raise TypeError(f"archive entry {position} source must be an object")
            if set(raw_source) != _SOURCE_RECORD_FIELDS:
                raise ValueError(f"archive entry {position} source fields are not canonical")
            payload = _entry_payload(
                position=position,
                previous_entry_sha256=claimed_previous,
                source=raw_source,
            )
            if _entry_sha256(payload) != claimed_digest:
                raise ValueError(f"archive entry {position} hash mismatch")
            raw_size = source_value_size(
                raw_source,
                limits=self.source_limits,
                index=position,
            )
            source = SourceRecord.from_dict(raw_source, default_sequence=position)
            normalized = source.to_dict()
            if normalized != raw_source:
                raise ValueError(f"archive entry {position} source is not canonical")
            normalized_size = source_value_size(
                normalized,
                limits=self.source_limits,
                index=position,
            )
            total_size = add_source_size(
                total_size,
                max(raw_size, normalized_size),
                limits=self.source_limits,
            )
            records.append(source)
            previous = claimed_digest
        self._validate_records(records, require_sorted=True)
        return _ArchiveState(tuple(records), ARCHIVE_ENTRY_SCHEMA, previous)

    def _decode_legacy(self, values: list[Any]) -> _ArchiveState:
        records: list[SourceRecord] = []
        total_size = 0
        for index, value in enumerate(values):
            raw_size = source_value_size(
                value,
                limits=self.source_limits,
                index=index,
            )
            source = SourceRecord.from_dict(value, default_sequence=index)
            normalized_size = source_value_size(
                source.to_dict(),
                limits=self.source_limits,
                index=index,
            )
            total_size = add_source_size(
                total_size,
                max(raw_size, normalized_size),
                limits=self.source_limits,
            )
            records.append(source)
        self._validate_records(records)
        records.sort(key=lambda record: record.sequence)
        return _ArchiveState(tuple(records), LEGACY_ARCHIVE_SCHEMA, None)

    def _validate_records(
        self,
        records: list[SourceRecord],
        *,
        require_sorted: bool = False,
    ) -> None:
        if len(records) > self.source_limits.max_records:
            raise SourceLimitError(
                f"source record count exceeds {self.source_limits.max_records} records"
            )
        ids = [record.id for record in records]
        sequences = [record.sequence for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("archive contains duplicate source ids")
        if len(sequences) != len(set(sequences)):
            raise ValueError("archive contains duplicate source sequences")
        if require_sorted and sequences != sorted(sequences):
            raise ValueError("archive source sequences are not in ascending order")
        total_size = 0
        for index, record in enumerate(records):
            record.ensure_integrity()
            record_size = source_value_size(
                record.to_dict(),
                limits=self.source_limits,
                index=index,
            )
            total_size = add_source_size(
                total_size,
                record_size,
                limits=self.source_limits,
            )

    def _serialize_records(self, records: list[SourceRecord]) -> tuple[str, str]:
        self._validate_records(records, require_sorted=True)
        lines: list[str] = []
        previous = ARCHIVE_GENESIS_SHA256
        for position, record in enumerate(records):
            payload = _entry_payload(
                position=position,
                previous_entry_sha256=previous,
                source=record.to_dict(),
            )
            digest = _entry_sha256(payload)
            entry = {**payload, "entry_sha256": digest}
            line = _canonical_json(entry)
            if len(line) > self.source_limits.max_line_chars:
                raise SourceLimitError(
                    "archive record exceeds "
                    f"{self.source_limits.max_line_chars} line characters"
                )
            lines.append(line)
            previous = digest
        payload = "".join(line + "\n" for line in lines)
        payload_bytes = len(payload.encode("utf-8"))
        if payload_bytes > self.source_limits.max_input_bytes:
            raise SourceLimitError(
                f"archive exceeds {self.source_limits.max_input_bytes} UTF-8 bytes"
            )
        return payload, previous

    @staticmethod
    def _require_expected_head(actual: str | None, expected: str | None) -> None:
        if expected is None:
            return
        if actual is None:
            raise ValueError(
                "archive has no chain head; append once without expected_chain_head "
                "to upgrade the legacy archive"
            )
        if actual != expected:
            raise ValueError(
                f"archive chain head mismatch: expected {expected}, found {actual}"
            )

    def verify(self, *, expected_chain_head: str | None = None) -> ArchiveReport:
        expected_head = _validate_expected_chain_head(expected_chain_head)
        try:
            state = self._load_state()
        except ValueError as exc:
            return ArchiveReport(False, 0, "", (str(exc),))
        records = list(state.records)
        issues: list[str] = []
        if expected_head is not None:
            if state.chain_head_sha256 is None:
                issues.append(
                    "archive has no chain head; the legacy format cannot satisfy "
                    "an expected-chain-head check"
                )
            elif state.chain_head_sha256 != expected_head:
                issues.append(
                    "archive chain head mismatch: "
                    f"expected {expected_head}, found {state.chain_head_sha256}"
                )
        return ArchiveReport(
            not issues,
            len(records),
            source_digest(records),
            tuple(issues),
            archive_schema=state.archive_schema,
            chain_head_sha256=state.chain_head_sha256,
        )

    def resolve(self, span: ProvenanceSpan) -> str:
        sources = {record.id: record for record in self.load()}
        if not span.validates(sources):
            raise ValueError("provenance span does not validate against the archive")
        return span.quote

    def _acquire_lock(self) -> int:
        deadline = time.monotonic() + self.lock_timeout
        lock_fd = open_lock_file(self.lock_path)
        try:
            while True:
                if try_lock_file(lock_fd):
                    return lock_fd
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"archive is locked: {self.lock_path}")
                time.sleep(min(0.025, remaining))
        except BaseException as exc:
            close_lock_file(lock_fd, prior_error=exc)
            raise
