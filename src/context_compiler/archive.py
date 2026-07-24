"""Append-only cold storage for source events referenced by compiled memory."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .atomic import atomic_write_text
from .file_lock import close_lock_file, open_lock_file, try_lock_file
from .io import load_sources_path
from .limits import (
    SourceLimitError,
    SourceLimits,
    add_source_size,
    resolve_source_limits,
    source_value_size,
)
from .models import ProvenanceSpan, SourceRecord, source_digest


@dataclass(frozen=True, slots=True)
class ArchiveReport:
    passed: bool
    records: int
    digest: str
    issues: tuple[str, ...] = field(default_factory=tuple)


class SourceArchive:
    """A minimal immutable JSONL event archive.

    Compilation may discard low-value text from active context, but source
    events remain append-only here. Existing ids and sequence numbers can
    never be overwritten with different data.
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

    def append(self, records: Iterable[SourceRecord]) -> int:
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
        if not incoming:
            return 0
        # Every incoming record is validated before creating or touching the
        # archive. This prevents a stale or externally mutated record from
        # turning a successful append into an immediately corrupt event log.
        self.directory.mkdir(parents=True, exist_ok=True)
        lock_fd = self._acquire_lock()
        archive_error: BaseException | None = None
        try:
            existing = self.load() if self.events_path.exists() else []
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
            self._validate_records(combined)
            lines = [
                json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":"))
                for record in combined
            ]
            for line in lines:
                if len(line) > self.source_limits.max_line_chars:
                    raise SourceLimitError(
                        "archive record exceeds "
                        f"{self.source_limits.max_line_chars} line characters"
                    )
            payload = "".join(line + "\n" for line in lines)
            payload_bytes = len(payload.encode("utf-8"))
            if payload_bytes > self.source_limits.max_input_bytes:
                raise SourceLimitError(
                    f"archive exceeds {self.source_limits.max_input_bytes} UTF-8 bytes"
                )
            atomic_write_text(self.events_path, payload)
            return len(accepted)
        except BaseException as exc:
            archive_error = exc
            raise
        finally:
            close_lock_file(lock_fd, prior_error=archive_error)

    def load(self) -> list[SourceRecord]:
        if not self.events_path.exists():
            return []
        try:
            records = load_sources_path(
                self.events_path,
                limits=self.source_limits,
            )
        except SourceLimitError:
            raise
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid archive: {exc}") from exc
        ids = [record.id for record in records]
        sequences = [record.sequence for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("archive contains duplicate source ids")
        if len(sequences) != len(set(sequences)):
            raise ValueError("archive contains duplicate source sequences")
        return sorted(records, key=lambda record: record.sequence)

    def _validate_records(self, records: list[SourceRecord]) -> None:
        if len(records) > self.source_limits.max_records:
            raise SourceLimitError(
                f"source record count exceeds {self.source_limits.max_records} records"
            )
        total_size = 0
        for index, record in enumerate(records):
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

    def verify(self) -> ArchiveReport:
        try:
            records = self.load()
        except ValueError as exc:
            return ArchiveReport(False, 0, "", (str(exc),))
        return ArchiveReport(True, len(records), source_digest(records))

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
