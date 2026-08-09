"""Crash-consistent SQLite-WAL source and generation storage.

The store owns integration durability only.  Immutable host events and their
canonical ``SourceRecord`` values remain the source of truth; bundles,
checkpoints, request ledgers, and indexes are derived evidence.  Readers follow
only the session's active pointer and never select a "latest" generation.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_compiler import (
    ContextBundle,
    IncrementalCompiler,
    SourceRecord,
)

from .atomic import (
    AtomicBindingError,
    IncompleteAtomicBindings,
    bind_atomic_groups,
    derive_atomic_groups,
    validate_atomic_pair,
    validate_atomic_prefix,
    validate_stored_atomic_groups,
)
from .event_map import EventMappingError, validate_host_event_shape
from .semantic import semantic_result_digest

if TYPE_CHECKING:
    from .authority import AuthorityPolicy

STORE_SCHEMA_VERSION = 2
SOURCE_HEAD_GENESIS = "0" * 64
SOURCE_HEAD_DOMAIN = b"ctxc-openhands-source-head-v1\x00"
REHYDRATION_SCHEMA = "ctxc-openhands-rehydrated-evidence-0.1"
INTEGRITY_REPORT_SCHEMA = "ctxc-openhands-store-integrity-0.1"
GENERATION_STATES = frozenset(
    {"prepared", "verified", "committed", "active", "superseded", "rolled_back"}
)
PROVISIONAL_STATES = frozenset({"prepared", "verified", "committed"})

_VERIFICATION_PAYLOAD_FIELDS = (
    "checkpoint_json",
    "checkpoint_sha256",
    "bundle_json",
    "bundle_sha256",
    "semantic_result_sha256",
    "replay_json",
    "replay_sha256",
)
_ALLOWED_GENERATION_TRANSITIONS = {
    None: frozenset({"prepared"}),
    "prepared": frozenset({"verified", "rolled_back"}),
    "verified": frozenset({"committed", "rolled_back"}),
    "committed": frozenset({"active", "rolled_back"}),
    "active": frozenset({"superseded"}),
    "superseded": frozenset({"active"}),
    "rolled_back": frozenset(),
}

_SHA256 = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+~-]{0,511}")
_MAX_JSON_BYTES = 32 * 1024 * 1024
_MAX_JSON_DEPTH = 64
_MAX_JSON_ITEMS = 500_000
_MAX_JSON_COLLECTION_ITEMS = 100_000
_MAX_SQLITE_INTEGER = (1 << 63) - 1

_REQUIRED_STORE_TABLES = frozenset(
    {
        "metadata",
        "sessions",
        "source_events",
        "source_atomic_groups",
        "generations",
        "generation_transitions",
        "request_ledgers",
        "operations",
    }
)
_REQUIRED_STORE_INDEXES = frozenset(
    {
        "source_atomic_groups_lookup",
        "source_atomic_groups_one_role",
        "one_active_generation",
    }
)
_REQUIRED_STORE_TRIGGERS = frozenset(
    {
        "source_events_no_update",
        "source_events_no_delete",
        "source_atomic_groups_no_update",
        "source_atomic_groups_no_delete",
        "transitions_no_update",
        "transitions_no_delete",
        "request_ledgers_no_update",
        "request_ledgers_no_delete",
        "operations_no_update",
        "operations_no_delete",
        "generations_no_delete",
        "generations_identity_immutable",
        "generations_valid_state_transition",
        "generations_verified_payload_once",
    }
)

FaultHook = Callable[[str], None]


class StoreError(RuntimeError):
    """Base error for durable integration state."""


class StoreIntegrityError(StoreError):
    """Stored state, a digest, or a required SQLite invariant is invalid."""


class StoreContentionError(StoreError):
    """The bounded SQLite writer wait expired."""


class StaleGenerationError(StoreError):
    """A source-head, parent-generation, or epoch compare-and-swap lost."""


class ImmutableEventError(StoreError):
    """An event id, sequence, or idempotency key collided with different bytes."""


class GenerationStateError(StoreError):
    """A generation transition is not permitted."""


class IncompleteAtomicGroupError(StoreError):
    """A tool call/result group is incomplete at a model-visible boundary."""


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    session_id: str
    source_count: int
    source_head_sha256: str
    source_digest_sha256: str
    active_generation_id: str | None
    active_epoch: int
    records: tuple[SourceRecord, ...]


@dataclass(frozen=True, slots=True)
class PreparedGeneration:
    generation_id: str
    parent_generation_id: str | None
    expected_active_epoch: int
    snapshot: SessionSnapshot


@dataclass(frozen=True, slots=True)
class ActiveGeneration:
    session_id: str
    generation_id: str
    active_epoch: int
    source_head_sha256: str
    covered_source_count: int
    bundle: ContextBundle
    semantic_result_digest: str
    tail: tuple[SourceRecord, ...]


@dataclass(frozen=True, slots=True)
class AppendResult:
    accepted: bool
    source_count: int
    source_head_sha256: str
    event_id: str
    sequence: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "source_count": self.source_count,
            "source_head_sha256": self.source_head_sha256,
            "event_id": self.event_id,
            "sequence": self.sequence,
        }


_INDEPENDENT_VERIFICATION_SEAL = object()


@dataclass(frozen=True, slots=True)
class _IndependentVerificationReceipt:
    """Internal capability binding one independently replayed candidate."""

    generation_id: str
    checkpoint_sha256: str
    bundle_sha256: str
    semantic_result_sha256: str
    replay_json: str
    replay_sha256: str
    _seal: object


def _validate_json_structure(value: Any) -> None:
    pending = [(value, 0)]
    item_count = 0
    while pending:
        item, depth = pending.pop()
        if depth > _MAX_JSON_DEPTH:
            raise StoreIntegrityError(
                f"JSON exceeds the maximum depth of {_MAX_JSON_DEPTH}"
            )
        item_count += 1
        if item_count > _MAX_JSON_ITEMS:
            raise StoreIntegrityError(
                f"JSON exceeds the maximum item count of {_MAX_JSON_ITEMS}"
            )
        if isinstance(item, dict):
            if len(item) > _MAX_JSON_COLLECTION_ITEMS:
                raise StoreIntegrityError("JSON object exceeds the collection limit")
            if not all(isinstance(key, str) for key in item):
                raise StoreIntegrityError("JSON object keys must be strings")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, (list, tuple)):
            if len(item) > _MAX_JSON_COLLECTION_ITEMS:
                raise StoreIntegrityError("JSON array exceeds the collection limit")
            pending.extend((child, depth + 1) for child in item)
        elif item is None or isinstance(item, (bool, int, str)):
            continue
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise StoreIntegrityError("JSON contains a non-finite number")
        else:
            raise StoreIntegrityError(
                f"JSON contains unsupported type {type(item).__name__}"
            )


def _canonical_json(value: Any) -> str:
    _validate_json_structure(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        encoded_bytes = encoded.encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeError) as exc:
        raise StoreIntegrityError(f"value is not canonical JSON: {exc}") from exc
    if len(encoded_bytes) > _MAX_JSON_BYTES:
        raise StoreIntegrityError(f"canonical JSON exceeds {_MAX_JSON_BYTES} bytes")
    return encoded


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode_canonical_json(value: Any, *, label: str) -> Any:
    if not isinstance(value, str):
        raise StoreIntegrityError(f"{label} must be canonical JSON text")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise StoreIntegrityError(f"{label} is not valid UTF-8 text") from exc
    if len(encoded) > _MAX_JSON_BYTES:
        raise StoreIntegrityError(f"{label} exceeds {_MAX_JSON_BYTES} bytes")
    try:
        decoded = json.loads(
            value,
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except (RecursionError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StoreIntegrityError(f"{label} is invalid JSON: {exc}") from exc
    if _canonical_json(decoded) != value:
        raise StoreIntegrityError(f"{label} is not canonical JSON")
    return decoded

def _validated_stored_host_event(
    value: Any,
    *,
    label: str,
) -> dict[str, Any]:
    try:
        return validate_host_event_shape(value)
    except EventMappingError as exc:
        raise StoreIntegrityError(
            f"{label} failed pinned host validation: {exc}"
        ) from exc


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_text(_canonical_json(value))


def _identifier(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} has an invalid identifier")
    return value


def _digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be 64 lowercase hexadecimal characters")
    return value


def _non_negative_int(value: Any, *, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_SQLITE_INTEGER
    ):
        raise ValueError(f"{label} must be a non-negative signed 64-bit integer")
    return value


def _source_head_step(
    previous: str,
    *,
    event_sha256: str,
    record_sha256: str,
) -> str:
    _digest(previous, label="previous source head")
    _digest(event_sha256, label="event digest")
    _digest(record_sha256, label="record digest")
    payload = (
        SOURCE_HEAD_DOMAIN
        + previous.encode("ascii")
        + b"\x00"
        + event_sha256.encode("ascii")
        + b"\x00"
        + record_sha256.encode("ascii")
    )
    return hashlib.sha256(payload).hexdigest()


def _source_record(value: SourceRecord | Mapping[str, Any]) -> SourceRecord:
    if isinstance(value, SourceRecord):
        record = value
    elif isinstance(value, Mapping):
        record = SourceRecord.from_dict(dict(value))
    else:
        raise TypeError("source_record must be a SourceRecord or mapping")
    record.ensure_integrity()
    return record


def _rederive_source_record(
    event: Mapping[str, Any],
    *,
    sequence: int,
    session_id: str,
    authority_policy: AuthorityPolicy | None,
    authority_receipt: Any | None,
) -> SourceRecord:
    from context_compiler import source_event_to_record

    from .authority import AuthorityPolicy
    from .event_map import (
        EventMappingError,
        classify_host_event,
        map_host_event,
    )

    if (
        authority_policy is not None
        and not isinstance(authority_policy, AuthorityPolicy)
    ):
        raise TypeError("authority_policy must be an AuthorityPolicy or null")
    try:
        mapped = map_host_event(
            event,
            sequence=sequence,
            session_id=session_id,
            authority_policy=authority_policy,
            authority_receipt=authority_receipt,
        )
        descriptor = classify_host_event(event)
    except EventMappingError as exc:
        raise StoreIntegrityError(
            f"host event failed pinned validation: {exc}"
        ) from exc
    if descriptor.transient:
        raise ImmutableEventError(
            f"{descriptor.kind} is transient/derived and cannot be durable source history"
        )
    return source_event_to_record(mapped, default_sequence=sequence)


def _source_digest(records: Sequence[SourceRecord]) -> str:
    """Reproduce the public artifact source binding from public record fields."""

    ordered = sorted(records, key=lambda record: record.sequence)
    for record in ordered:
        record.ensure_integrity()
    return _sha256_json(
        [
            [record.sequence, record.id, record.record_sha256]
            for record in ordered
        ]
    )


def _mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise TypeError(f"{label} field names must be strings")
    # Round-trip to detach nested caller values and reject non-JSON data.
    return _decode_canonical_json(_canonical_json(dict(value)), label=label)


def _passed_replay_issues_are_non_error(report: Mapping[str, Any]) -> bool:
    issues = report.get("issues")
    if not isinstance(issues, list):
        return False
    for issue in issues:
        if (
            not isinstance(issue, dict)
            or issue.get("severity") not in {"info", "warning"}
        ):
            return False
    return True


def _mint_independent_verification_receipt(
    *,
    generation_id: str,
    checkpoint: Mapping[str, Any],
    bundle: ContextBundle | Mapping[str, Any],
    replay_report: Mapping[str, Any],
) -> _IndependentVerificationReceipt:
    """Seal evidence produced by a fresh verifier for the store transition.

    This deliberately private boundary converts the independently produced
    replay result into a capability. ``record_verified`` never accepts a
    caller-supplied ``passed`` mapping directly.
    """

    checked_generation_id = _identifier(generation_id, label="generation_id")
    report = _mapping(replay_report, label="independent replay report")
    if (
        report.get("passed") is not True
        or report.get("bundle_digest_valid") is not True
        or report.get("bindings_valid") is not True
        or not _passed_replay_issues_are_non_error(report)
    ):
        raise StoreIntegrityError(
            "independent replay did not establish valid bundle bindings"
        )
    certificate = report.get("certificate")
    if (
        not isinstance(certificate, dict)
        or certificate.get("semantic_completeness_claimed") is not False
    ):
        raise StoreIntegrityError(
            "independent replay report has an invalid claim boundary"
        )

    raw_checkpoint = _mapping(checkpoint, label="incremental checkpoint")
    normalized_checkpoint = IncrementalCompiler.from_checkpoint(
        raw_checkpoint
    ).checkpoint()
    checkpoint_sha = _sha256_text(_canonical_json(normalized_checkpoint))

    if isinstance(bundle, ContextBundle):
        parsed_bundle = ContextBundle.from_dict(bundle.to_dict())
    elif isinstance(bundle, Mapping):
        parsed_bundle = ContextBundle.from_dict(dict(bundle))
    else:
        raise TypeError("bundle must be a ContextBundle or mapping")
    replay_json = _canonical_json(report)
    return _IndependentVerificationReceipt(
        generation_id=checked_generation_id,
        checkpoint_sha256=checkpoint_sha,
        bundle_sha256=parsed_bundle.bundle_sha256,
        semantic_result_sha256=semantic_result_digest(parsed_bundle),
        replay_json=replay_json,
        replay_sha256=_sha256_text(replay_json),
        _seal=_INDEPENDENT_VERIFICATION_SEAL,
    )


def _atomic_groups(
    record: SourceRecord,
    event: Mapping[str, Any],
    *,
    session_id: str,
) -> tuple[dict[str, Any], ...]:
    try:
        return bind_atomic_groups(
            event,
            record.to_dict()["metadata"],
            session_id=session_id,
        )
    except AtomicBindingError as exc:
        raise StoreIntegrityError(str(exc)) from exc


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _is_link_or_reparse(path: Path) -> bool:
    try:
        status = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(status.st_mode):
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    # OneDrive cloud placeholders are reparse points but do not redirect path
    # resolution. Reject name-surrogate tags (symlinks, junctions, and
    # equivalent redirects), not every reparse attribute.
    reparse_tag = getattr(status, "st_reparse_tag", 0)
    return bool(reparse_tag and reparse_tag & 0x20000000)


def _reject_link_or_reparse_chain(path: Path, *, label: str) -> None:
    current = path
    first = True
    while True:
        if _is_link_or_reparse(current):
            location = label if first else f"{label} parent"
            raise ValueError(f"{location} must not be a symbolic link or reparse point")
        if current == current.parent:
            return
        current = current.parent
        first = False


def _regular_file_identity(path: Path, *, label: str) -> tuple[int, int]:
    _reject_link_or_reparse_chain(path, label=label)
    try:
        status = path.lstat()
    except FileNotFoundError:
        raise FileNotFoundError(f"{label} does not exist: {path}") from None
    if not stat.S_ISREG(status.st_mode):
        raise ValueError(f"{label} must be a regular file")
    link_count = getattr(status, "st_nlink", 1)
    if link_count not in (0, 1):
        raise ValueError(f"{label} must not have hard-link aliases")
    return status.st_dev, status.st_ino


class SQLiteGenerationStore:
    """Local-filesystem SQLite store with explicit generation visibility."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        busy_timeout_seconds: float = 5.0,
        fault_hook: FaultHook | None = None,
        require_existing: bool = False,
        require_new: bool = False,
    ) -> None:
        if (
            isinstance(busy_timeout_seconds, bool)
            or not isinstance(busy_timeout_seconds, (int, float))
            or not math.isfinite(busy_timeout_seconds)
            or busy_timeout_seconds < 0
            or busy_timeout_seconds > 60
        ):
            raise ValueError(
                "busy_timeout_seconds must be a finite number between 0 and 60"
            )
        if not isinstance(require_existing, bool):
            raise TypeError("require_existing must be a boolean")
        if not isinstance(require_new, bool):
            raise TypeError("require_new must be a boolean")
        if require_existing and require_new:
            raise ValueError(
                "require_existing and require_new are mutually exclusive"
            )
        raw_path = Path(path)
        if str(raw_path) == ":memory:":
            raise ValueError("the durable generation store requires a file path")
        if os.name == "nt" and str(raw_path).startswith(("\\\\", "//")):
            raise ValueError("UNC/network paths are unsupported for SQLite WAL")
        self.path = raw_path.expanduser().absolute()
        self.busy_timeout_ms = int(float(busy_timeout_seconds) * 1000)
        self._reserved_identity: tuple[int, int] | None = None
        self._store_identity: tuple[int, int] | None = None
        self._fault_hook = fault_hook
        self.require_existing = require_existing
        self.require_new = require_new
        self._create_connection_allowed = self._prepare_path()
        self._initialize()

    def _fault(self, point: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)

    def _prepare_path(self) -> bool:
        _reject_link_or_reparse_chain(self.path, label="SQLite store path")
        present = _path_entry_exists(self.path)
        if self.require_existing:
            if not present:
                raise FileNotFoundError(f"SQLite store does not exist: {self.path}")
        elif self.require_new and present:
            raise FileExistsError(f"SQLite store already exists: {self.path}")
        if not present:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _reject_link_or_reparse_chain(self.path, label="SQLite store path")
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            try:
                status = os.fstat(descriptor)
                self._reserved_identity = (status.st_dev, status.st_ino)
            finally:
                os.close(descriptor)
            actual = _regular_file_identity(self.path, label="SQLite store path")
            if actual != self._reserved_identity:
                raise StoreIntegrityError(
                    "reserved SQLite store path changed before initialization"
                )
            return True
        self._store_identity = _regular_file_identity(
            self.path,
            label="SQLite store path",
        )
        return False

    def _assert_store_path_identity(self, *, phase: str) -> None:
        expected_identity = (
            self._reserved_identity
            if self._create_connection_allowed
            else self._store_identity
        )
        actual_identity = _regular_file_identity(
            self.path,
            label="SQLite store path",
        )
        if expected_identity is None or actual_identity != expected_identity:
            raise StoreIntegrityError(
                f"SQLite store path identity changed {phase}"
            )

    @staticmethod
    def _validate_existing_store(
        connection: sqlite3.Connection,
        *,
        schema_sql: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        objects: dict[str, set[str]] = {
            "table": set(),
            "index": set(),
            "trigger": set(),
        }
        for row in rows:
            object_type = row["type"]
            if object_type in objects:
                objects[object_type].add(row["name"])

        if "metadata" not in objects["table"]:
            raise StoreIntegrityError("SQLite store metadata table is missing")
        metadata_rows = connection.execute(
            "SELECT key, value FROM metadata ORDER BY key"
        ).fetchall()
        if len(metadata_rows) != 2:
            raise StoreIntegrityError("SQLite store metadata is incomplete or unexpected")
        values = {row["key"]: row["value"] for row in metadata_rows}
        if set(values) != {"schema_version", "store_uuid"}:
            raise StoreIntegrityError("SQLite store metadata keys are invalid")
        if values["schema_version"] != str(STORE_SCHEMA_VERSION):
            raise StoreIntegrityError("unsupported SQLite store schema version")
        try:
            _identifier(values["store_uuid"], label="store UUID")
        except (TypeError, ValueError) as exc:
            raise StoreIntegrityError("SQLite store UUID is invalid") from exc

        required_objects = (
            ("table", _REQUIRED_STORE_TABLES),
            ("index", _REQUIRED_STORE_INDEXES),
            ("trigger", _REQUIRED_STORE_TRIGGERS),
        )
        for object_type, required in required_objects:
            missing = sorted(required - objects[object_type])
            if missing:
                raise StoreIntegrityError(
                    f"SQLite store is missing required {object_type} objects: "
                    + ", ".join(missing)
                )

        reference = sqlite3.connect(":memory:", isolation_level=None)
        reference.row_factory = sqlite3.Row
        try:
            reference.executescript(schema_sql)
            reference_rows = reference.execute(
                """
                SELECT type, name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                """
            ).fetchall()
        finally:
            reference.close()

        def normalized_definitions(
            source_rows: Sequence[sqlite3.Row],
        ) -> dict[tuple[str, str], str]:
            definitions: dict[tuple[str, str], str] = {}
            for source_row in source_rows:
                object_type = source_row["type"]
                name = source_row["name"]
                sql = source_row["sql"]
                if (
                    object_type not in {"table", "index", "trigger"}
                    or not isinstance(name, str)
                    or not isinstance(sql, str)
                ):
                    raise StoreIntegrityError(
                        "SQLite store contains an invalid schema object"
                    )
                normalized = "\n".join(
                    line.strip()
                    for line in sql.strip().splitlines()
                    if line.strip()
                )
                definitions[(object_type, name)] = normalized
            return definitions

        expected_definitions = normalized_definitions(reference_rows)
        actual_definitions = normalized_definitions(rows)
        missing_definitions = sorted(
            set(expected_definitions).difference(actual_definitions)
        )
        unexpected_definitions = sorted(
            set(actual_definitions).difference(expected_definitions)
        )
        if missing_definitions or unexpected_definitions:
            raise StoreIntegrityError(
                "SQLite store schema object set differs from the frozen schema"
            )
        for key, expected_sql in expected_definitions.items():
            if actual_definitions[key] != expected_sql:
                raise StoreIntegrityError(
                    f"SQLite store {key[0]} definition mismatch: {key[1]}"
                )

        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
        if journal_mode is None or str(journal_mode[0]).casefold() != "wal":
            raise StoreIntegrityError("SQLite store journal_mode must already be WAL")
        quick_check = connection.execute("PRAGMA quick_check(1)").fetchone()
        if quick_check is None or quick_check[0] != "ok":
            raise StoreIntegrityError("SQLite store quick_check failed")

    def _connect_read_only(self) -> sqlite3.Connection:
        self._assert_store_path_identity(phase="before read-only open")
        try:
            connection = sqlite3.connect(
                f"{self.path.as_uri()}?mode=ro",
                uri=True,
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
                check_same_thread=False,
            )
        except sqlite3.Error as exc:
            raise StoreIntegrityError("failed to open existing SQLite store read-only") from exc
        connection.row_factory = sqlite3.Row
        try:
            self._assert_store_path_identity(phase="during read-only open")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            return connection
        except BaseException:
            connection.close()
            raise

    def _connect(self) -> sqlite3.Connection:
        self._assert_store_path_identity(phase="before open")
        database: str | Path
        uri = False
        if self._create_connection_allowed:
            database = self.path
        else:
            database = f"{self.path.as_uri()}?mode=rw"
            uri = True
        try:
            connection = sqlite3.connect(
                database,
                uri=uri,
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
                check_same_thread=False,
            )
        except sqlite3.Error as exc:
            raise StoreIntegrityError("failed to open existing SQLite store") from exc
        connection.row_factory = sqlite3.Row
        try:
            self._assert_store_path_identity(phase="during open")
            if self._create_connection_allowed:
                mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            else:
                mode_row = connection.execute("PRAGMA journal_mode").fetchone()
                if mode_row is None:
                    raise StoreIntegrityError("SQLite journal_mode is unavailable")
                mode = mode_row[0]
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA read_uncommitted=OFF")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            actual = {
                "journal_mode": str(mode).casefold(),
                "synchronous": connection.execute(
                    "PRAGMA synchronous"
                ).fetchone()[0],
                "foreign_keys": connection.execute(
                    "PRAGMA foreign_keys"
                ).fetchone()[0],
                "read_uncommitted": connection.execute(
                    "PRAGMA read_uncommitted"
                ).fetchone()[0],
                "trusted_schema": connection.execute(
                    "PRAGMA trusted_schema"
                ).fetchone()[0],
                "busy_timeout": connection.execute(
                    "PRAGMA busy_timeout"
                ).fetchone()[0],
            }
            expected = {
                "journal_mode": "wal",
                "synchronous": 2,
                "foreign_keys": 1,
                "read_uncommitted": 0,
                "trusted_schema": 0,
                "busy_timeout": self.busy_timeout_ms,
            }
            if actual != expected:
                raise StoreIntegrityError(
                    f"required SQLite pragmas did not take effect: {actual!r}"
                )
            return connection
        except BaseException:
            connection.close()
            raise

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            self._fault("write.before_begin")
            connection.execute("BEGIN IMMEDIATE")
            self._fault("write.after_begin")
            yield connection
            self._fault("write.before_commit")
            connection.execute("COMMIT")
            self._fault("write.after_commit")
        except sqlite3.OperationalError as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            if "locked" in str(exc).casefold() or "busy" in str(exc).casefold():
                raise StoreContentionError("SQLite writer contention") from exc
            raise
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            source_count INTEGER NOT NULL DEFAULT 0 CHECK (source_count >= 0),
            source_head_sha256 TEXT NOT NULL,
            source_digest_sha256 TEXT,
            active_generation_id TEXT,
            active_epoch INTEGER NOT NULL DEFAULT 0 CHECK (active_epoch >= 0),
            FOREIGN KEY (session_id, active_generation_id)
                REFERENCES generations (session_id, generation_id)
                DEFERRABLE INITIALLY DEFERRED
        ) STRICT;

        CREATE TABLE IF NOT EXISTS source_events (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
            event_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence >= 0),
            event_kind TEXT NOT NULL,
            host_event_json TEXT NOT NULL,
            event_sha256 TEXT NOT NULL,
            source_record_json TEXT NOT NULL,
            record_sha256 TEXT NOT NULL,
            previous_head_sha256 TEXT NOT NULL,
            source_head_sha256 TEXT NOT NULL,
            atomic_groups_json TEXT NOT NULL,
            PRIMARY KEY (session_id, ordinal),
            UNIQUE (session_id, event_id),
            UNIQUE (session_id, sequence)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS source_atomic_groups (
            session_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
            group_sha256 TEXT NOT NULL,
            role TEXT NOT NULL CHECK (
                role IN ('call', 'result', 'llm-call', 'llm-result')
            ),
            PRIMARY KEY (session_id, ordinal, group_sha256, role),
            FOREIGN KEY (session_id, ordinal)
                REFERENCES source_events(session_id, ordinal)
        ) STRICT;

        CREATE INDEX IF NOT EXISTS source_atomic_groups_lookup
            ON source_atomic_groups(session_id, group_sha256, ordinal);

        CREATE UNIQUE INDEX IF NOT EXISTS source_atomic_groups_one_role
            ON source_atomic_groups(session_id, group_sha256, role);

        CREATE TABLE IF NOT EXISTS generations (
            generation_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            state TEXT NOT NULL CHECK (
                state IN (
                    'prepared', 'verified', 'committed',
                    'active', 'superseded', 'rolled_back'
                )
            ),
            parent_generation_id TEXT,
            expected_active_epoch INTEGER NOT NULL CHECK (expected_active_epoch >= 0),
            captured_source_count INTEGER NOT NULL CHECK (captured_source_count >= 0),
            captured_source_head_sha256 TEXT NOT NULL,
            captured_source_digest_sha256 TEXT NOT NULL,
            policy_sha256 TEXT NOT NULL,
            tokenizer_identity TEXT NOT NULL,
            checkpoint_json TEXT,
            checkpoint_sha256 TEXT,
            bundle_json TEXT,
            bundle_sha256 TEXT,
            semantic_result_sha256 TEXT,
            replay_json TEXT,
            replay_sha256 TEXT,
            verification_passed INTEGER NOT NULL DEFAULT 0
                CHECK (verification_passed IN (0, 1)),
            UNIQUE (session_id, generation_id),
            FOREIGN KEY (session_id, parent_generation_id)
                REFERENCES generations(session_id, generation_id)
        ) STRICT;

        CREATE UNIQUE INDEX IF NOT EXISTS one_active_generation
            ON generations(session_id) WHERE state = 'active';

        CREATE TABLE IF NOT EXISTS generation_transitions (
            generation_id TEXT NOT NULL REFERENCES generations(generation_id),
            ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
            from_state TEXT,
            to_state TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL,
            PRIMARY KEY (generation_id, ordinal)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS request_ledgers (
            session_id TEXT NOT NULL,
            generation_id TEXT NOT NULL,
            active_epoch INTEGER NOT NULL CHECK (active_epoch >= 0),
            request_id TEXT NOT NULL,
            ledger_json TEXT NOT NULL,
            ledger_sha256 TEXT NOT NULL,
            final_request_sha256 TEXT NOT NULL,
            PRIMARY KEY (session_id, request_id),
            FOREIGN KEY (session_id, generation_id)
                REFERENCES generations(session_id, generation_id)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS operations (
            session_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            result_json TEXT NOT NULL,
            PRIMARY KEY (session_id, request_id)
        ) STRICT;

        CREATE TRIGGER IF NOT EXISTS source_events_no_update
        BEFORE UPDATE ON source_events BEGIN
            SELECT RAISE(ABORT, 'source events are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS source_events_no_delete
        BEFORE DELETE ON source_events BEGIN
            SELECT RAISE(ABORT, 'source events are append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS source_atomic_groups_no_update
        BEFORE UPDATE ON source_atomic_groups BEGIN
            SELECT RAISE(ABORT, 'source atomic groups are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS source_atomic_groups_no_delete
        BEFORE DELETE ON source_atomic_groups BEGIN
            SELECT RAISE(ABORT, 'source atomic groups are append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS transitions_no_update
        BEFORE UPDATE ON generation_transitions BEGIN
            SELECT RAISE(ABORT, 'generation transitions are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS transitions_no_delete
        BEFORE DELETE ON generation_transitions BEGIN
            SELECT RAISE(ABORT, 'generation transitions are append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS request_ledgers_no_update
        BEFORE UPDATE ON request_ledgers BEGIN
            SELECT RAISE(ABORT, 'request ledgers are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS request_ledgers_no_delete
        BEFORE DELETE ON request_ledgers BEGIN
            SELECT RAISE(ABORT, 'request ledgers are append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS operations_no_update
        BEFORE UPDATE ON operations BEGIN
            SELECT RAISE(ABORT, 'operations are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS operations_no_delete
        BEFORE DELETE ON operations BEGIN
            SELECT RAISE(ABORT, 'operations are append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS generations_no_delete
        BEFORE DELETE ON generations BEGIN
            SELECT RAISE(ABORT, 'generations are retained');
        END;
        CREATE TRIGGER IF NOT EXISTS generations_identity_immutable
        BEFORE UPDATE OF
            generation_id, session_id, parent_generation_id,
            expected_active_epoch, captured_source_count,
            captured_source_head_sha256, captured_source_digest_sha256,
            policy_sha256, tokenizer_identity
        ON generations BEGIN
            SELECT RAISE(ABORT, 'generation identity is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS generations_valid_state_transition
        BEFORE UPDATE OF state ON generations
        WHEN NOT (
            (OLD.state = 'prepared' AND NEW.state IN ('verified', 'rolled_back'))
            OR (OLD.state = 'verified' AND NEW.state IN ('committed', 'rolled_back'))
            OR (OLD.state = 'committed' AND NEW.state IN ('active', 'rolled_back'))
            OR (OLD.state = 'active' AND NEW.state = 'superseded')
            OR (OLD.state = 'superseded' AND NEW.state = 'active')
        ) BEGIN
            SELECT RAISE(ABORT, 'invalid generation state transition');
        END;
        CREATE TRIGGER IF NOT EXISTS generations_verified_payload_once
        BEFORE UPDATE OF
            checkpoint_json, checkpoint_sha256, bundle_json, bundle_sha256,
            semantic_result_sha256, replay_json, replay_sha256,
            verification_passed
        ON generations
        WHEN NOT (OLD.state = 'prepared' AND NEW.state = 'verified') BEGIN
            SELECT RAISE(ABORT, 'verified generation payload is immutable');
        END;
        """
        if not self._create_connection_allowed:
            connection = self._connect_read_only()
            try:
                self._validate_existing_store(
                    connection,
                    schema_sql=schema,
                )
                self._assert_store_path_identity(
                    phase="during existing-store validation"
                )
            except sqlite3.Error as exc:
                raise StoreIntegrityError(
                    "existing SQLite store schema inspection failed"
                ) from exc
            finally:
                connection.close()
            return

        connection = self._connect()
        initialized = False
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + schema)
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO NOTHING",
                (str(STORE_SCHEMA_VERSION),),
            )
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES('store_uuid', ?) "
                "ON CONFLICT(key) DO NOTHING",
                (str(uuid.uuid4()),),
            )
            self._validate_existing_store(
                connection,
                schema_sql=schema,
            )
            connection.execute("COMMIT")
            initialized = True
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise StoreIntegrityError("new SQLite store initialization failed") from exc
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        if initialized:
            actual_identity = _regular_file_identity(
                self.path,
                label="SQLite store path",
            )
            if (
                self._reserved_identity is None
                or actual_identity != self._reserved_identity
            ):
                raise StoreIntegrityError(
                    "reserved SQLite store path changed during initialization"
                )
            self._store_identity = actual_identity
            self._reserved_identity = None
            self._create_connection_allowed = False

    @property
    def store_uuid(self) -> str:
        with self._read() as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'store_uuid'"
            ).fetchone()
        if row is None:
            raise StoreIntegrityError("store UUID is missing")
        return _identifier(row["value"], label="store UUID")

    def _ensure_session(self, connection: sqlite3.Connection, session_id: str) -> None:
        connection.execute(
            """
            INSERT INTO sessions(
                session_id, source_count, source_head_sha256,
                source_digest_sha256, active_generation_id, active_epoch
            ) VALUES(?, 0, ?, NULL, NULL, 0)
            ON CONFLICT(session_id) DO NOTHING
            """,
            (session_id, SOURCE_HEAD_GENESIS),
        )

    def resolve_append_sequence(
        self,
        *,
        session_id: str,
        event_id: str,
    ) -> tuple[int, bool]:
        """Resolve a new ordinal or an immutable retry without decoding history."""

        session_id = _identifier(session_id, label="session_id")
        event_id = _identifier(event_id, label="event id")
        with self._read() as connection:
            session = connection.execute(
                """
                SELECT source_count, source_head_sha256
                FROM sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            existing = connection.execute(
                """
                SELECT ordinal, sequence FROM source_events
                WHERE session_id = ? AND event_id = ?
                """,
                (session_id, event_id),
            ).fetchone()
            tail = connection.execute(
                """
                SELECT ordinal, source_head_sha256
                FROM source_events
                WHERE session_id = ? ORDER BY ordinal DESC LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        if session is None:
            if existing is not None or tail is not None:
                raise StoreIntegrityError("source row exists without its session")
            return 0, False
        source_count = _non_negative_int(
            session["source_count"],
            label="session source count",
        )
        session_head = _digest(
            session["source_head_sha256"],
            label="session source head",
        )
        expected_tail = None if source_count == 0 else source_count - 1
        actual_tail = (
            None
            if tail is None
            else _non_negative_int(tail["ordinal"], label="source tail ordinal")
        )
        if actual_tail != expected_tail:
            raise StoreIntegrityError("session source count does not match its tail ordinal")
        if source_count == 0:
            if session_head != SOURCE_HEAD_GENESIS:
                raise StoreIntegrityError("empty session does not carry the genesis head")
        elif tail is None or _digest(
            tail["source_head_sha256"],
            label="source tail head",
        ) != session_head:
            raise StoreIntegrityError(
                "session source head does not match its immutable tail"
            )
        if existing is None:
            return source_count, False
        ordinal = _non_negative_int(
            existing["ordinal"],
            label="existing event ordinal",
        )
        sequence = _non_negative_int(
            existing["sequence"],
            label="existing event sequence",
        )
        if ordinal != sequence or sequence >= source_count:
            raise StoreIntegrityError("existing event sequence is outside the immutable prefix")
        return sequence, True

    def retained_atomic_events(
        self,
        *,
        session_id: str,
        result_event: Mapping[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        """Return locally verified members for one ordinary result group.

        The normalized table is only an index. Every referenced source row and
        its independently derived group membership are revalidated before use.
        """

        session_id = _identifier(session_id, label="session_id")
        event = _validated_stored_host_event(
            _mapping(result_event, label="result_event"),
            label="result event",
        )
        try:
            groups = derive_atomic_groups(event, session_id=session_id)
        except AtomicBindingError as exc:
            raise StoreIntegrityError(str(exc)) from exc
        result_groups = tuple(group for group in groups if group["role"] == "result")
        if len(result_groups) != 1:
            raise StoreIntegrityError(
                "ordinary result lookup requires exactly one derived result group"
            )
        group_sha256 = _digest(
            result_groups[0]["group_sha256"],
            label="atomic group digest",
        )
        with self._read() as connection:
            rows = connection.execute(
                """
                SELECT events.*, groups.group_sha256 AS indexed_group_sha256,
                       groups.role AS indexed_role
                FROM source_atomic_groups AS groups
                JOIN source_events AS events
                  ON events.session_id = groups.session_id
                 AND events.ordinal = groups.ordinal
                WHERE groups.session_id = ? AND groups.group_sha256 = ?
                ORDER BY groups.ordinal, groups.role
                """,
                (session_id, group_sha256),
            ).fetchall()
        retained: list[dict[str, Any]] = []
        for row in rows:
            ordinal = _non_negative_int(
                row["ordinal"],
                label="indexed source ordinal",
            )
            stored_event = _validated_stored_host_event(
                _decode_canonical_json(
                    row["host_event_json"],
                    label=f"indexed source event {ordinal}",
                ),
                label=f"indexed source event {ordinal}",
            )
            stored_groups = _decode_canonical_json(
                row["atomic_groups_json"],
                label=f"indexed atomic groups {ordinal}",
            )
            try:
                verified_groups = validate_stored_atomic_groups(
                    stored_event,
                    stored_groups,
                    session_id=session_id,
                )
            except AtomicBindingError as exc:
                raise StoreIntegrityError(str(exc)) from exc
            indexed_role = row["indexed_role"]
            if not any(
                group["group_sha256"] == group_sha256
                and group["role"] == indexed_role
                for group in verified_groups
            ):
                raise StoreIntegrityError(
                    "atomic group index differs from canonical event semantics"
                )
            record_data = _decode_canonical_json(
                row["source_record_json"],
                label=f"indexed source record {ordinal}",
            )
            record = SourceRecord.from_dict(
                record_data,
                default_sequence=ordinal,
            )
            record.ensure_integrity()
            if (
                row["session_id"] != session_id
                or row["sequence"] != ordinal
                or record.sequence != ordinal
                or record.id != row["event_id"]
                or record.content != row["host_event_json"]
                or record.record_sha256 != row["record_sha256"]
                or stored_event.get("id") != row["event_id"]
                or stored_event.get("kind") != row["event_kind"]
                or _sha256_text(row["host_event_json"]) != row["event_sha256"]
            ):
                raise StoreIntegrityError("indexed atomic source binding mismatch")
            retained.append(stored_event)
        return tuple(retained)

    @staticmethod
    def _assert_atomic_append_allowed(
        connection: sqlite3.Connection,
        *,
        session_id: str,
        ordinal: int,
        event: Mapping[str, Any],
        groups: Sequence[Mapping[str, Any]],
    ) -> None:
        """Validate atomic uniqueness and result binding inside the write lock."""

        role_family = {
            "call": "ordinary",
            "result": "ordinary",
            "llm-call": "llm",
            "llm-result": "llm",
        }
        for group in groups:
            group_sha256 = _digest(
                group["group_sha256"],
                label="atomic group digest",
            )
            role = group["role"]
            rows = connection.execute(
                """
                SELECT events.*, indexed.group_sha256 AS indexed_group_sha256,
                       indexed.role AS indexed_role
                FROM source_atomic_groups AS indexed
                JOIN source_events AS events
                  ON events.session_id = indexed.session_id
                 AND events.ordinal = indexed.ordinal
                WHERE indexed.session_id = ? AND indexed.group_sha256 = ?
                ORDER BY indexed.ordinal, indexed.role
                """,
                (session_id, group_sha256),
            ).fetchall()

            retained: list[
                tuple[int, dict[str, Any], tuple[dict[str, Any], ...], str]
            ] = []
            for row in rows:
                retained_ordinal = _non_negative_int(
                    row["ordinal"],
                    label="indexed source ordinal",
                )
                stored_event = _validated_stored_host_event(
                    _decode_canonical_json(
                        row["host_event_json"],
                        label=f"indexed source event {retained_ordinal}",
                    ),
                    label=f"indexed source event {retained_ordinal}",
                )
                stored_groups = _decode_canonical_json(
                    row["atomic_groups_json"],
                    label=f"indexed atomic groups {retained_ordinal}",
                )
                try:
                    verified_groups = validate_stored_atomic_groups(
                        stored_event,
                        stored_groups,
                        session_id=session_id,
                    )
                except AtomicBindingError as exc:
                    raise StoreIntegrityError(str(exc)) from exc
                indexed_role = row["indexed_role"]
                if not any(
                    candidate["group_sha256"] == group_sha256
                    and candidate["role"] == indexed_role
                    for candidate in verified_groups
                ):
                    raise StoreIntegrityError(
                        "atomic group index differs from canonical event semantics"
                    )
                record = SourceRecord.from_dict(
                    _decode_canonical_json(
                        row["source_record_json"],
                        label=f"indexed source record {retained_ordinal}",
                    ),
                    default_sequence=retained_ordinal,
                )
                record.ensure_integrity()
                if (
                    row["session_id"] != session_id
                    or row["sequence"] != retained_ordinal
                    or record.sequence != retained_ordinal
                    or record.id != row["event_id"]
                    or record.content != row["host_event_json"]
                    or record.record_sha256 != row["record_sha256"]
                    or stored_event.get("id") != row["event_id"]
                    or stored_event.get("kind") != row["event_kind"]
                    or _sha256_text(row["host_event_json"]) != row["event_sha256"]
                ):
                    raise StoreIntegrityError("indexed atomic source binding mismatch")
                retained.append(
                    (
                        retained_ordinal,
                        stored_event,
                        verified_groups,
                        indexed_role,
                    )
                )

            if any(
                role_family.get(indexed_role) != role_family.get(role)
                for _, _, _, indexed_role in retained
            ):
                raise ImmutableEventError(
                    "atomic group mixes incompatible event families"
                )
            if any(indexed_role == role for _, _, _, indexed_role in retained):
                raise ImmutableEventError(
                    f"atomic group already has retained atomic role {role}"
                )
            if role in {"call", "llm-call"}:
                if retained:
                    raise ImmutableEventError(
                        "atomic call requires an unused atomic group"
                    )
                continue

            expected_call_role = "call" if role == "result" else "llm-call"
            calls = tuple(
                member for member in retained if member[3] == expected_call_role
            )
            if len(retained) != 1 or len(calls) != 1:
                raise ImmutableEventError(
                    "atomic result requires exactly one retained matching call"
                )
            call_ordinal, call_event, call_groups, _ = calls[0]
            try:
                validate_atomic_pair(
                    (call_ordinal, call_event, list(call_groups)),
                    (ordinal, event, list(groups)),
                    session_id=session_id,
                )
            except AtomicBindingError as exc:
                raise ImmutableEventError(str(exc)) from exc

    def append_source_event(
        self,
        *,
        session_id: str,
        host_event: Mapping[str, Any],
        source_record: SourceRecord | Mapping[str, Any],
        request_id: str,
        authority_policy: AuthorityPolicy | None = None,
        authority_receipt: Any | None = None,
    ) -> AppendResult:
        """Append one immutable event with exact-byte idempotency."""

        session_id = _identifier(session_id, label="session_id")
        request_id = _identifier(request_id, label="request_id")
        event = _mapping(host_event, label="host_event")
        event_id = _identifier(event.get("id"), label="event id")
        event_kind = _identifier(event.get("kind"), label="event kind")
        record = _source_record(source_record)
        if record.id != event_id:
            raise ImmutableEventError("host event id and SourceRecord id differ")
        event_json = _canonical_json(event)
        if record.content != event_json:
            raise ImmutableEventError(
                "SourceRecord content must equal the canonical host event"
            )
        record_json = _canonical_json(record.to_dict())
        event_sha = _sha256_text(event_json)
        groups = _atomic_groups(record, event, session_id=session_id)
        expected_record = _rederive_source_record(
            event,
            sequence=record.sequence,
            session_id=session_id,
            authority_policy=authority_policy,
            authority_receipt=authority_receipt,
        )
        if record_json != _canonical_json(expected_record.to_dict()):
            raise ImmutableEventError(
                "SourceRecord must exactly equal the independently rederived host mapping"
            )
        groups_json = _canonical_json(list(groups))
        payload_sha = _sha256_json(
            {
                "session_id": session_id,
                "host_event": event,
                "source_record": record.to_dict(),
                "atomic_groups": list(groups),
            }
        )
        with self._write() as connection:
            self._fault("append.after_begin")
            prior_operation = connection.execute(
                """
                SELECT payload_sha256, result_json
                FROM operations WHERE session_id = ? AND request_id = ?
                """,
                (session_id, request_id),
            ).fetchone()
            if prior_operation is not None:
                if prior_operation["payload_sha256"] != payload_sha:
                    raise ImmutableEventError(
                        "idempotency request id reused with different payload"
                    )
                result = _decode_canonical_json(
                    prior_operation["result_json"],
                    label="operation result",
                )
                return AppendResult(**result)

            self._ensure_session(connection, session_id)
            session = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise StoreIntegrityError("session row is missing after creation")
            collision = connection.execute(
                """
                SELECT * FROM source_events
                WHERE session_id = ? AND (event_id = ? OR sequence = ?)
                """,
                (session_id, event_id, record.sequence),
            ).fetchone()
            if collision is not None:
                exact = (
                    collision["event_id"] == event_id
                    and collision["sequence"] == record.sequence
                    and collision["host_event_json"] == event_json
                    and collision["source_record_json"] == record_json
                    and collision["atomic_groups_json"] == groups_json
                )
                if not exact:
                    raise ImmutableEventError(
                        "immutable event id or sequence collision"
                    )
                result = AppendResult(
                    accepted=False,
                    source_count=session["source_count"],
                    source_head_sha256=session["source_head_sha256"],
                    event_id=event_id,
                    sequence=record.sequence,
                )
            else:
                if record.sequence != session["source_count"]:
                    raise ImmutableEventError(
                        "source sequence must equal the current append ordinal"
                    )
                self._assert_atomic_append_allowed(
                    connection,
                    session_id=session_id,
                    ordinal=record.sequence,
                    event=event,
                    groups=groups,
                )
                new_head = _source_head_step(
                    session["source_head_sha256"],
                    event_sha256=event_sha,
                    record_sha256=record.record_sha256,
                )
                self._fault("append.before_event_insert")
                connection.execute(
                    """
                    INSERT INTO source_events(
                        session_id, ordinal, event_id, sequence, event_kind,
                        host_event_json, event_sha256, source_record_json,
                        record_sha256, previous_head_sha256,
                        source_head_sha256, atomic_groups_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        record.sequence,
                        event_id,
                        record.sequence,
                        event_kind,
                        event_json,
                        event_sha,
                        record_json,
                        record.record_sha256,
                        session["source_head_sha256"],
                        new_head,
                        groups_json,
                    ),
                )
                for group in groups:
                    connection.execute(
                        """
                        INSERT INTO source_atomic_groups(
                            session_id, ordinal, group_sha256, role
                        ) VALUES(?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            record.sequence,
                            group["group_sha256"],
                            group["role"],
                        ),
                    )
                self._fault("append.after_event_insert")
                cursor = connection.execute(
                    """
                    UPDATE sessions
                    SET source_count = source_count + 1,
                        source_head_sha256 = ?,
                        source_digest_sha256 = NULL
                    WHERE session_id = ?
                      AND source_count = ?
                      AND source_head_sha256 = ?
                    """,
                    (
                        new_head,
                        session_id,
                        session["source_count"],
                        session["source_head_sha256"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise StaleGenerationError("source-head compare-and-swap lost")
                self._fault("append.after_head_cas")
                result = AppendResult(
                    accepted=True,
                    source_count=session["source_count"] + 1,
                    source_head_sha256=new_head,
                    event_id=event_id,
                    sequence=record.sequence,
                )
            result_json = _canonical_json(result.to_dict())
            connection.execute(
                """
                INSERT INTO operations(
                    session_id, request_id, payload_sha256, result_json
                ) VALUES(?, ?, ?, ?)
                """,
                (session_id, request_id, payload_sha, result_json),
            )
            self._fault("append.before_commit")
            return result

    @staticmethod
    def _assert_atomic_index_matches(
        connection: sqlite3.Connection,
        *,
        session_id: str,
        source_count: int,
    ) -> None:
        source_rows = connection.execute(
            """
            SELECT ordinal, host_event_json, atomic_groups_json
            FROM source_events
            WHERE session_id = ? AND ordinal < ?
            ORDER BY ordinal
            """,
            (session_id, source_count),
        ).fetchall()
        indexed_rows = connection.execute(
            """
            SELECT ordinal, group_sha256, role
            FROM source_atomic_groups
            WHERE session_id = ? AND ordinal < ?
            ORDER BY ordinal, group_sha256, role
            """,
            (session_id, source_count),
        ).fetchall()
        if len(source_rows) != source_count:
            raise StoreIntegrityError("atomic index source prefix is incomplete")
        expected: list[tuple[int, str, str]] = []
        for row in source_rows:
            ordinal = _non_negative_int(
                row["ordinal"],
                label="atomic index source ordinal",
            )
            event = _validated_stored_host_event(
                _decode_canonical_json(
                    row["host_event_json"],
                    label=f"atomic index host event {ordinal}",
                ),
                label=f"atomic index host event {ordinal}",
            )
            stored_groups = _decode_canonical_json(
                row["atomic_groups_json"],
                label=f"atomic index groups {ordinal}",
            )
            try:
                groups = validate_stored_atomic_groups(
                    event,
                    stored_groups,
                    session_id=session_id,
                )
            except AtomicBindingError as exc:
                raise StoreIntegrityError(str(exc)) from exc
            expected.extend(
                (ordinal, group["group_sha256"], group["role"])
                for group in groups
            )
        actual = [
            (
                _non_negative_int(
                    row["ordinal"],
                    label="indexed atomic ordinal",
                ),
                _digest(
                    row["group_sha256"],
                    label="indexed atomic group digest",
                ),
                row["role"],
            )
            for row in indexed_rows
        ]
        if actual != sorted(expected):
            raise StoreIntegrityError(
                "atomic group index differs from the immutable source prefix"
            )

    def _records_from_rows(
        self,
        rows: Sequence[sqlite3.Row],
        *,
        expected_head: str,
    ) -> tuple[SourceRecord, ...]:
        previous = SOURCE_HEAD_GENESIS
        records: list[SourceRecord] = []
        seen_ids: set[str] = set()
        for ordinal, row in enumerate(rows):
            if row["ordinal"] != ordinal or row["sequence"] != ordinal:
                raise StoreIntegrityError("source event ordinals are not contiguous")
            event = _decode_canonical_json(
                row["host_event_json"],
                label=f"source event {ordinal}",
            )
            event = _validated_stored_host_event(
                event,
                label=f"source event {ordinal}",
            )
            record_data = _decode_canonical_json(
                row["source_record_json"],
                label=f"source record {ordinal}",
            )
            record = SourceRecord.from_dict(record_data, default_sequence=ordinal)
            record.ensure_integrity()
            if record.id in seen_ids:
                raise StoreIntegrityError("duplicate source id")
            seen_ids.add(record.id)
            if (
                record.id != row["event_id"]
                or record.sequence != ordinal
                or record.record_sha256 != row["record_sha256"]
                or record.content != row["host_event_json"]
                or event.get("id") != record.id
                or event.get("kind") != row["event_kind"]
                or _sha256_text(row["host_event_json"]) != row["event_sha256"]
                or row["previous_head_sha256"] != previous
            ):
                raise StoreIntegrityError("source event binding mismatch")
            head = _source_head_step(
                previous,
                event_sha256=row["event_sha256"],
                record_sha256=record.record_sha256,
            )
            if row["source_head_sha256"] != head:
                raise StoreIntegrityError("source head chain mismatch")
            stored_groups = _decode_canonical_json(
                row["atomic_groups_json"],
                label=f"source atomic groups {ordinal}",
            )
            try:
                validate_stored_atomic_groups(
                    event,
                    stored_groups,
                    session_id=row["session_id"],
                )
            except AtomicBindingError as exc:
                raise StoreIntegrityError(str(exc)) from exc
            previous = head
            records.append(record)
        if previous != expected_head:
            raise StoreIntegrityError("session source head does not match event chain")
        return tuple(records)

    def _session_snapshot(self, session_id: str) -> SessionSnapshot:
        session_id = _identifier(session_id, label="session_id")
        with self._read() as connection:
            session = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise KeyError(f"unknown session: {session_id}")
            rows = connection.execute(
                """
                SELECT * FROM source_events
                WHERE session_id = ? ORDER BY ordinal
                """,
                (session_id,),
            ).fetchall()
            self._assert_atomic_index_matches(
                connection,
                session_id=session_id,
                source_count=session["source_count"],
            )
        if len(rows) != session["source_count"]:
            raise StoreIntegrityError("session source count does not match event rows")
        records = self._records_from_rows(
            rows,
            expected_head=session["source_head_sha256"],
        )
        digest = _source_digest(records)
        stored_digest = session["source_digest_sha256"]
        if stored_digest is not None and stored_digest != digest:
            raise StoreIntegrityError("session source digest mismatch")
        return SessionSnapshot(
            session_id=session_id,
            source_count=len(records),
            source_head_sha256=session["source_head_sha256"],
            source_digest_sha256=digest,
            active_generation_id=session["active_generation_id"],
            active_epoch=session["active_epoch"],
            records=records,
        )

    def snapshot(self, session_id: str) -> SessionSnapshot:
        """Return a verified immutable source snapshot."""

        return self._session_snapshot(session_id)

    @staticmethod
    def _assert_atomic_groups_complete(
        connection: sqlite3.Connection,
        *,
        session_id: str,
        source_count: int,
    ) -> None:
        SQLiteGenerationStore._assert_atomic_index_matches(
            connection,
            session_id=session_id,
            source_count=source_count,
        )
        rows = connection.execute(
            """
            SELECT ordinal, host_event_json, atomic_groups_json
            FROM source_events
            WHERE session_id = ? AND ordinal < ?
            ORDER BY ordinal
            """,
            (session_id, source_count),
        ).fetchall()
        entries: list[tuple[int, Mapping[str, Any], Any]] = []
        for row in rows:
            event = _decode_canonical_json(
                row["host_event_json"],
                label=f"atomic host event at ordinal {row['ordinal']}",
            )
            event = _validated_stored_host_event(
                event,
                label=f"atomic host event at ordinal {row['ordinal']}",
            )
            groups = _decode_canonical_json(
                row["atomic_groups_json"],
                label=f"atomic groups at ordinal {row['ordinal']}",
            )
            entries.append((row["ordinal"], event, groups))
        try:
            validate_atomic_prefix(entries, session_id=session_id)
        except IncompleteAtomicBindings as exc:
            raise IncompleteAtomicGroupError(str(exc)) from exc
        except AtomicBindingError as exc:
            raise StoreIntegrityError(str(exc)) from exc

    def _transition(
        self,
        connection: sqlite3.Connection,
        *,
        generation_id: str,
        from_state: str | None,
        to_state: str,
        operation_id: str,
        reason_code: str,
        evidence_sha256: str,
    ) -> None:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(ordinal), -1) + 1 AS next_ordinal
            FROM generation_transitions WHERE generation_id = ?
            """,
            (generation_id,),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO generation_transitions(
                generation_id, ordinal, from_state, to_state,
                operation_id, reason_code, evidence_sha256
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generation_id,
                row["next_ordinal"],
                from_state,
                to_state,
                operation_id,
                reason_code,
                evidence_sha256,
            ),
        )

    def prepare_generation(
        self,
        *,
        session_id: str,
        generation_id: str,
        operation_id: str,
        policy_sha256: str,
        tokenizer_identity: str,
    ) -> PreparedGeneration:
        """Capture one source head and create an invisible prepared generation."""

        generation_id = _identifier(generation_id, label="generation_id")
        operation_id = _identifier(operation_id, label="operation_id")
        policy_sha256 = _digest(policy_sha256, label="policy_sha256")
        tokenizer_identity = _identifier(
            tokenizer_identity,
            label="tokenizer_identity",
        )
        snapshot = self._session_snapshot(session_id)
        with self._write() as connection:
            session = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (snapshot.session_id,),
            ).fetchone()
            if (
                session is None
                or session["source_count"] != snapshot.source_count
                or session["source_head_sha256"] != snapshot.source_head_sha256
                or session["active_generation_id"] != snapshot.active_generation_id
                or session["active_epoch"] != snapshot.active_epoch
            ):
                raise StaleGenerationError(
                    "source or active-generation snapshot changed before prepare"
                )
            self._assert_atomic_groups_complete(
                connection,
                session_id=snapshot.session_id,
                source_count=snapshot.source_count,
            )
            connection.execute(
                """
                UPDATE sessions SET source_digest_sha256 = ?
                WHERE session_id = ? AND source_head_sha256 = ?
                """,
                (
                    snapshot.source_digest_sha256,
                    snapshot.session_id,
                    snapshot.source_head_sha256,
                ),
            )
            self._fault("prepare.before_generation_insert")
            connection.execute(
                """
                INSERT INTO generations(
                    generation_id, session_id, state, parent_generation_id,
                    expected_active_epoch, captured_source_count,
                    captured_source_head_sha256, captured_source_digest_sha256,
                    policy_sha256, tokenizer_identity
                ) VALUES(?, ?, 'prepared', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    generation_id,
                    snapshot.session_id,
                    snapshot.active_generation_id,
                    snapshot.active_epoch,
                    snapshot.source_count,
                    snapshot.source_head_sha256,
                    snapshot.source_digest_sha256,
                    policy_sha256,
                    tokenizer_identity,
                ),
            )
            evidence = _sha256_json(
                {
                    "generation_id": generation_id,
                    "session_id": snapshot.session_id,
                    "source_head_sha256": snapshot.source_head_sha256,
                    "source_digest_sha256": snapshot.source_digest_sha256,
                    "source_count": snapshot.source_count,
                    "parent_generation_id": snapshot.active_generation_id,
                    "expected_active_epoch": snapshot.active_epoch,
                }
            )
            self._transition(
                connection,
                generation_id=generation_id,
                from_state=None,
                to_state="prepared",
                operation_id=operation_id,
                reason_code="source-snapshot-captured",
                evidence_sha256=evidence,
            )
            self._fault("prepare.before_commit")
        return PreparedGeneration(
            generation_id=generation_id,
            parent_generation_id=snapshot.active_generation_id,
            expected_active_epoch=snapshot.active_epoch,
            snapshot=snapshot,
        )

    @staticmethod
    def _validate_checkpoint(
        checkpoint: Mapping[str, Any],
        *,
        session_id: str,
        source_count: int,
        source_digest_sha256: str,
    ) -> tuple[str, str]:
        raw = _mapping(checkpoint, label="incremental checkpoint")
        restored = IncrementalCompiler.from_checkpoint(raw)
        normalized = restored.checkpoint()
        if (
            normalized["session_id"] != session_id
            or normalized["source_count"] != source_count
            or normalized["source_digest"] != source_digest_sha256
        ):
            raise StoreIntegrityError(
                "checkpoint does not bind the prepared source snapshot"
            )
        text = _canonical_json(normalized)
        return text, _sha256_text(text)

    @staticmethod
    def _validate_bundle(
        bundle: ContextBundle | Mapping[str, Any],
        *,
        session_id: str,
        source_count: int,
        source_digest_sha256: str,
        policy_sha256: str,
        tokenizer_identity: str,
    ) -> tuple[ContextBundle, str, str]:
        if isinstance(bundle, ContextBundle):
            parsed = ContextBundle.from_dict(bundle.to_dict())
        elif isinstance(bundle, Mapping):
            parsed = ContextBundle.from_dict(dict(bundle))
        else:
            raise TypeError("bundle must be a ContextBundle or mapping")
        raw = parsed.to_dict()
        bindings = raw["bindings"]
        if (
            bindings["session_id"] != session_id
            or bindings["source_count"] != source_count
            or bindings["source_digest"] != source_digest_sha256
            or bindings["compiler_policy_sha256"] != policy_sha256
            or bindings["tokenizer_identity"] != tokenizer_identity
        ):
            raise StoreIntegrityError(
                "bundle does not bind the prepared source snapshot, policy, and tokenizer"
            )
        text = _canonical_json(raw)
        return parsed, text, semantic_result_digest(parsed)

    def record_verified(
        self,
        *,
        generation_id: str,
        operation_id: str,
        checkpoint: Mapping[str, Any],
        bundle: ContextBundle | Mapping[str, Any],
        verification_receipt: object,
    ) -> str:
        """Persist receipt-bound replay evidence and transition to verified."""

        generation_id = _identifier(generation_id, label="generation_id")
        operation_id = _identifier(operation_id, label="operation_id")
        if (
            not isinstance(
                verification_receipt,
                _IndependentVerificationReceipt,
            )
            or verification_receipt._seal is not _INDEPENDENT_VERIFICATION_SEAL
            or verification_receipt.generation_id != generation_id
        ):
            raise StoreIntegrityError(
                "generation requires an independently minted verification receipt"
            )
        receipt = verification_receipt
        report = _decode_canonical_json(
            receipt.replay_json,
            label="independent replay report",
        )
        certificate = report.get("certificate") if isinstance(report, dict) else None
        if (
            not isinstance(report, dict)
            or report.get("passed") is not True
            or report.get("bundle_digest_valid") is not True
            or report.get("bindings_valid") is not True
            or not _passed_replay_issues_are_non_error(report)
            or not isinstance(certificate, dict)
            or certificate.get("semantic_completeness_claimed") is not False
            or _sha256_text(receipt.replay_json) != receipt.replay_sha256
        ):
            raise StoreIntegrityError("verification receipt replay evidence is invalid")
        with self._read() as connection:
            generation = connection.execute(
                "SELECT * FROM generations WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
        if generation is None:
            raise KeyError(f"unknown generation: {generation_id}")
        if generation["state"] != "prepared":
            raise GenerationStateError("only a prepared generation can be verified")
        checkpoint_json, checkpoint_sha = self._validate_checkpoint(
            checkpoint,
            session_id=generation["session_id"],
            source_count=generation["captured_source_count"],
            source_digest_sha256=generation["captured_source_digest_sha256"],
        )
        parsed, bundle_json, semantic_sha = self._validate_bundle(
            bundle,
            session_id=generation["session_id"],
            source_count=generation["captured_source_count"],
            source_digest_sha256=generation["captured_source_digest_sha256"],
            policy_sha256=generation["policy_sha256"],
            tokenizer_identity=generation["tokenizer_identity"],
        )
        replay_json = _canonical_json(report)
        replay_sha = _sha256_text(replay_json)
        if (
            receipt.checkpoint_sha256 != checkpoint_sha
            or receipt.bundle_sha256 != parsed.bundle_sha256
            or receipt.semantic_result_sha256 != semantic_sha
            or receipt.replay_json != replay_json
            or receipt.replay_sha256 != replay_sha
        ):
            raise StoreIntegrityError(
                "verification receipt does not bind the candidate evidence"
            )
        evidence = _sha256_json(
            {
                "checkpoint_sha256": checkpoint_sha,
                "bundle_sha256": parsed.bundle_sha256,
                "semantic_result_sha256": semantic_sha,
                "replay_sha256": replay_sha,
            }
        )
        with self._write() as connection:
            cursor = connection.execute(
                """
                UPDATE generations SET
                    state = 'verified',
                    checkpoint_json = ?,
                    checkpoint_sha256 = ?,
                    bundle_json = ?,
                    bundle_sha256 = ?,
                    semantic_result_sha256 = ?,
                    replay_json = ?,
                    replay_sha256 = ?,
                    verification_passed = 1
                WHERE generation_id = ? AND state = 'prepared'
                """,
                (
                    checkpoint_json,
                    checkpoint_sha,
                    bundle_json,
                    parsed.bundle_sha256,
                    semantic_sha,
                    replay_json,
                    replay_sha,
                    generation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise GenerationStateError("generation left prepared state")
            self._transition(
                connection,
                generation_id=generation_id,
                from_state="prepared",
                to_state="verified",
                operation_id=operation_id,
                reason_code="independent-replay-passed",
                evidence_sha256=evidence,
            )
            self._fault("verify.before_commit")
        return semantic_sha

    def _validated_generation_row(
        self,
        row: sqlite3.Row,
    ) -> tuple[ContextBundle, dict[str, Any]]:
        if row["verification_passed"] != 1:
            raise StoreIntegrityError("generation lacks passed verification evidence")
        for field in (
            "checkpoint_json",
            "checkpoint_sha256",
            "bundle_json",
            "bundle_sha256",
            "semantic_result_sha256",
            "replay_json",
            "replay_sha256",
        ):
            if row[field] is None:
                raise StoreIntegrityError(f"generation is missing {field}")
        checkpoint = _decode_canonical_json(
            row["checkpoint_json"],
            label="stored checkpoint",
        )
        if _sha256_text(row["checkpoint_json"]) != row["checkpoint_sha256"]:
            raise StoreIntegrityError("stored checkpoint digest mismatch")
        self._validate_checkpoint(
            checkpoint,
            session_id=row["session_id"],
            source_count=row["captured_source_count"],
            source_digest_sha256=row["captured_source_digest_sha256"],
        )
        bundle_raw = _decode_canonical_json(
            row["bundle_json"],
            label="stored ContextBundle",
        )
        bundle, normalized, semantic_sha = self._validate_bundle(
            bundle_raw,
            session_id=row["session_id"],
            source_count=row["captured_source_count"],
            source_digest_sha256=row["captured_source_digest_sha256"],
            policy_sha256=row["policy_sha256"],
            tokenizer_identity=row["tokenizer_identity"],
        )
        if (
            normalized != row["bundle_json"]
            or bundle.bundle_sha256 != row["bundle_sha256"]
            or semantic_sha != row["semantic_result_sha256"]
        ):
            raise StoreIntegrityError("stored generation bundle digest mismatch")
        replay = _decode_canonical_json(
            row["replay_json"],
            label="stored replay report",
        )
        certificate = replay.get("certificate") if isinstance(replay, dict) else None
        if (
            not isinstance(replay, dict)
            or replay.get("passed") is not True
            or replay.get("bundle_digest_valid") is not True
            or replay.get("bindings_valid") is not True
            or not _passed_replay_issues_are_non_error(replay)
            or not isinstance(certificate, dict)
            or certificate.get("semantic_completeness_claimed") is not False
            or _sha256_text(row["replay_json"]) != row["replay_sha256"]
        ):
            raise StoreIntegrityError("stored replay evidence is invalid")
        return bundle, replay

    def commit_generation(
        self,
        *,
        generation_id: str,
        operation_id: str,
    ) -> None:
        """Make a verified generation durable and eligible, but still invisible."""

        generation_id = _identifier(generation_id, label="generation_id")
        operation_id = _identifier(operation_id, label="operation_id")
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM generations WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown generation: {generation_id}")
            if row["state"] != "verified":
                raise GenerationStateError("only a verified generation can be committed")
            self._validated_generation_row(row)
            cursor = connection.execute(
                """
                UPDATE generations SET state = 'committed'
                WHERE generation_id = ? AND state = 'verified'
                """,
                (generation_id,),
            )
            if cursor.rowcount != 1:
                raise GenerationStateError("generation left verified state")
            evidence = _sha256_json(
                {
                    "bundle_sha256": row["bundle_sha256"],
                    "replay_sha256": row["replay_sha256"],
                    "semantic_result_sha256": row["semantic_result_sha256"],
                }
            )
            self._transition(
                connection,
                generation_id=generation_id,
                from_state="verified",
                to_state="committed",
                operation_id=operation_id,
                reason_code="verified-evidence-reloaded",
                evidence_sha256=evidence,
            )
            self._fault("commit_state.before_commit")

    def activate_generation(
        self,
        *,
        generation_id: str,
        operation_id: str,
    ) -> int:
        """Atomically swap the active pointer after all compare-and-swaps pass."""

        generation_id = _identifier(generation_id, label="generation_id")
        operation_id = _identifier(operation_id, label="operation_id")
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM generations WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown generation: {generation_id}")
            if row["state"] != "committed":
                raise GenerationStateError("only a committed generation can be active")
            self._validated_generation_row(row)
            session = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (row["session_id"],),
            ).fetchone()
            if session is None:
                raise StoreIntegrityError("generation session row is missing")
            if (
                session["source_head_sha256"]
                != row["captured_source_head_sha256"]
                or session["source_count"] != row["captured_source_count"]
                or session["active_generation_id"] != row["parent_generation_id"]
                or session["active_epoch"] != row["expected_active_epoch"]
            ):
                raise StaleGenerationError(
                    "generation activation compare-and-swap lost"
                )
            old = row["parent_generation_id"]
            if old is not None:
                old_cursor = connection.execute(
                    """
                    UPDATE generations SET state = 'superseded'
                    WHERE generation_id = ? AND session_id = ? AND state = 'active'
                    """,
                    (old, row["session_id"]),
                )
                if old_cursor.rowcount != 1:
                    raise StaleGenerationError("parent generation is not active")
                self._transition(
                    connection,
                    generation_id=old,
                    from_state="active",
                    to_state="superseded",
                    operation_id=operation_id,
                    reason_code="new-generation-activation",
                    evidence_sha256=row["bundle_sha256"],
                )
                self._fault("activate.after_old_superseded")
            new_cursor = connection.execute(
                """
                UPDATE generations SET state = 'active'
                WHERE generation_id = ? AND state = 'committed'
                """,
                (generation_id,),
            )
            if new_cursor.rowcount != 1:
                raise GenerationStateError("candidate is no longer committed")
            self._fault("activate.after_new_active")
            session_cursor = connection.execute(
                """
                UPDATE sessions
                SET active_generation_id = ?, active_epoch = active_epoch + 1
                WHERE session_id = ?
                  AND source_head_sha256 = ?
                  AND source_count = ?
                  AND active_epoch = ?
                  AND (
                      active_generation_id = ?
                      OR (active_generation_id IS NULL AND ? IS NULL)
                  )
                """,
                (
                    generation_id,
                    row["session_id"],
                    row["captured_source_head_sha256"],
                    row["captured_source_count"],
                    row["expected_active_epoch"],
                    old,
                    old,
                ),
            )
            if session_cursor.rowcount != 1:
                raise StaleGenerationError("active pointer compare-and-swap lost")
            new_epoch = row["expected_active_epoch"] + 1
            self._transition(
                connection,
                generation_id=generation_id,
                from_state="committed",
                to_state="active",
                operation_id=operation_id,
                reason_code="active-pointer-cas-won",
                evidence_sha256=row["bundle_sha256"],
            )
            self._fault("activate.after_pointer_cas")
            return new_epoch

    def read_active(self, session_id: str) -> ActiveGeneration | None:
        """Return exactly the active verified generation plus an atomic tail."""

        session_id = _identifier(session_id, label="session_id")
        with self._read() as connection:
            session = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise KeyError(f"unknown session: {session_id}")
            generation_id = session["active_generation_id"]
            if generation_id is None:
                return None
            row = connection.execute(
                """
                SELECT * FROM generations
                WHERE generation_id = ? AND session_id = ?
                """,
                (generation_id, session_id),
            ).fetchone()
            if row is None or row["state"] != "active":
                raise StoreIntegrityError(
                    "active pointer does not reference an active generation"
                )
            self._assert_atomic_groups_complete(
                connection,
                session_id=session_id,
                source_count=session["source_count"],
            )
            source_rows = connection.execute(
                """
                SELECT * FROM source_events
                WHERE session_id = ? ORDER BY ordinal
                """,
                (session_id,),
            ).fetchall()
            bundle, _replay = self._validated_generation_row(row)
        records = self._records_from_rows(
            source_rows,
            expected_head=session["source_head_sha256"],
        )
        if row["captured_source_count"] > len(records):
            raise StoreIntegrityError("active generation covers a missing source prefix")
        prefix = records[: row["captured_source_count"]]
        if _source_digest(prefix) != row["captured_source_digest_sha256"]:
            raise StoreIntegrityError("active generation source prefix digest mismatch")
        return ActiveGeneration(
            session_id=session_id,
            generation_id=generation_id,
            active_epoch=session["active_epoch"],
            source_head_sha256=session["source_head_sha256"],
            covered_source_count=row["captured_source_count"],
            bundle=bundle,
            semantic_result_digest=row["semantic_result_sha256"],
            tail=records[row["captured_source_count"] :],
        )

    def rollback_generation(
        self,
        *,
        session_id: str,
        target_generation_id: str,
        operation_id: str,
    ) -> int:
        """Explicitly reactivate a retained verified generation without rewinding sources."""

        session_id = _identifier(session_id, label="session_id")
        target_generation_id = _identifier(
            target_generation_id,
            label="target_generation_id",
        )
        operation_id = _identifier(operation_id, label="operation_id")
        with self._write() as connection:
            session = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None or session["active_generation_id"] is None:
                raise GenerationStateError("session has no active generation")
            current = connection.execute(
                "SELECT * FROM generations WHERE generation_id = ?",
                (session["active_generation_id"],),
            ).fetchone()
            target = connection.execute(
                "SELECT * FROM generations WHERE generation_id = ?",
                (target_generation_id,),
            ).fetchone()
            if (
                current is None
                or current["state"] != "active"
                or target is None
                or target["state"] != "superseded"
                or target["session_id"] != session_id
            ):
                raise GenerationStateError("rollback target is not retained superseded state")
            self._validated_generation_row(target)
            if target["captured_source_count"] > session["source_count"]:
                raise StoreIntegrityError("rollback target covers a future source prefix")
            connection.execute(
                "UPDATE generations SET state = 'superseded' "
                "WHERE generation_id = ? AND state = 'active'",
                (current["generation_id"],),
            )
            connection.execute(
                "UPDATE generations SET state = 'active' "
                "WHERE generation_id = ? AND state = 'superseded'",
                (target_generation_id,),
            )
            cursor = connection.execute(
                """
                UPDATE sessions
                SET active_generation_id = ?, active_epoch = active_epoch + 1
                WHERE session_id = ? AND active_generation_id = ? AND active_epoch = ?
                """,
                (
                    target_generation_id,
                    session_id,
                    current["generation_id"],
                    session["active_epoch"],
                ),
            )
            if cursor.rowcount != 1:
                raise StaleGenerationError("rollback pointer compare-and-swap lost")
            self._transition(
                connection,
                generation_id=current["generation_id"],
                from_state="active",
                to_state="superseded",
                operation_id=operation_id,
                reason_code="explicit-rollback",
                evidence_sha256=target["bundle_sha256"],
            )
            self._transition(
                connection,
                generation_id=target_generation_id,
                from_state="superseded",
                to_state="active",
                operation_id=operation_id,
                reason_code="explicit-rollback-target-reverified",
                evidence_sha256=target["bundle_sha256"],
            )
            return session["active_epoch"] + 1

    def roll_back_provisional(
        self,
        *,
        generation_id: str,
        operation_id: str,
        reason_code: str = "explicit-provisional-rollback",
    ) -> None:
        """Retain but make one non-active generation terminally ineligible."""

        generation_id = _identifier(generation_id, label="generation_id")
        operation_id = _identifier(operation_id, label="operation_id")
        reason_code = _identifier(reason_code, label="reason_code")
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM generations WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown generation: {generation_id}")
            if row["state"] not in PROVISIONAL_STATES:
                raise GenerationStateError("only a provisional generation can roll back")
            cursor = connection.execute(
                """
                UPDATE generations SET state = 'rolled_back'
                WHERE generation_id = ? AND state = ?
                """,
                (generation_id, row["state"]),
            )
            if cursor.rowcount != 1:
                raise GenerationStateError("generation state changed during rollback")
            evidence = row["bundle_sha256"] or row["captured_source_head_sha256"]
            self._transition(
                connection,
                generation_id=generation_id,
                from_state=row["state"],
                to_state="rolled_back",
                operation_id=operation_id,
                reason_code=reason_code,
                evidence_sha256=evidence,
            )

    def store_request_ledger(
        self,
        *,
        session_id: str,
        generation_id: str,
        request_id: str,
        ledger: Mapping[str, Any],
        tokenizer: object,
    ) -> None:
        """Persist one validated exact ledger bound to the active epoch/head."""

        from .request_ledger import validate_final_request_ledger

        session_id = _identifier(session_id, label="session_id")
        generation_id = _identifier(generation_id, label="generation_id")
        request_id = _identifier(request_id, label="request_id")
        validated = validate_final_request_ledger(ledger, tokenizer=tokenizer)
        raw = validated.to_dict()
        with self._write() as connection:
            session = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            generation = connection.execute(
                "SELECT * FROM generations WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
            if (
                session is None
                or generation is None
                or generation["state"] != "active"
                or session["active_generation_id"] != generation_id
            ):
                raise GenerationStateError("request ledger requires the active generation")
            bindings = raw["bindings"]
            if (
                bindings["session_id"] != session_id
                or bindings["generation_id"] != generation_id
                or bindings["active_epoch"] != session["active_epoch"]
                or bindings["source_head_sha256"]
                != session["source_head_sha256"]
                or bindings["semantic_result_digest"]
                != generation["semantic_result_sha256"]
            ):
                raise StoreIntegrityError(
                    "request ledger bindings do not match active store state"
                )
            ledger_json = validated.to_json()
            try:
                connection.execute(
                    """
                    INSERT INTO request_ledgers(
                        session_id, generation_id, active_epoch, request_id,
                        ledger_json, ledger_sha256, final_request_sha256
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        generation_id,
                        session["active_epoch"],
                        request_id,
                        ledger_json,
                        raw["ledger_sha256"],
                        raw["final_request_sha256"],
                    ),
                )
            except sqlite3.IntegrityError as exc:
                prior = connection.execute(
                    """
                    SELECT ledger_json FROM request_ledgers
                    WHERE session_id = ? AND request_id = ?
                    """,
                    (session_id, request_id),
                ).fetchone()
                if prior is None or prior["ledger_json"] != ledger_json:
                    raise ImmutableEventError(
                        "request id reused with a different final ledger"
                    ) from exc

    def load_request_ledger(
        self,
        *,
        session_id: str,
        request_id: str,
        tokenizer: object,
    ) -> Mapping[str, Any]:
        """Reload and strictly replay one retained immutable request ledger."""

        from .request_ledger import validate_final_request_ledger

        session_id = _identifier(session_id, label="session_id")
        request_id = _identifier(request_id, label="request_id")
        with self._read() as connection:
            row = connection.execute(
                """
                SELECT ledgers.*, generations.semantic_result_sha256
                FROM request_ledgers AS ledgers
                JOIN generations
                  ON generations.session_id = ledgers.session_id
                 AND generations.generation_id = ledgers.generation_id
                WHERE ledgers.session_id = ? AND ledgers.request_id = ?
                """,
                (session_id, request_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown request ledger: {request_id}")
        raw = _decode_canonical_json(
            row["ledger_json"],
            label="stored request ledger",
        )
        if not isinstance(raw, dict) or _canonical_json(raw) != row["ledger_json"]:
            raise StoreIntegrityError("stored request ledger is not canonical JSON")
        validated = validate_final_request_ledger(raw, tokenizer=tokenizer)
        normalized = validated.to_dict()
        bindings = normalized["bindings"]
        if (
            validated.to_json() != row["ledger_json"]
            or normalized["ledger_sha256"] != row["ledger_sha256"]
            or normalized["final_request_sha256"] != row["final_request_sha256"]
            or bindings["session_id"] != row["session_id"]
            or bindings["generation_id"] != row["generation_id"]
            or bindings["active_epoch"] != row["active_epoch"]
            or bindings["semantic_result_digest"]
            != row["semantic_result_sha256"]
        ):
            raise StoreIntegrityError("stored request ledger binding or digest mismatch")
        with self._read() as connection:
            source_head = connection.execute(
                """
                SELECT 1 FROM source_events
                WHERE session_id = ? AND source_head_sha256 = ?
                """,
                (session_id, bindings["source_head_sha256"]),
            ).fetchone()
        if source_head is None:
            raise StoreIntegrityError("request ledger source head is not retained")
        return validated

    def rehydrate(
        self,
        *,
        session_id: str,
        source_id: str,
        start: int,
        end: int,
        quote_sha256: str,
    ) -> dict[str, Any]:
        """Return one exact span explicitly labeled as untrusted evidence."""

        session_id = _identifier(session_id, label="session_id")
        source_id = _identifier(source_id, label="source_id")
        start = _non_negative_int(start, label="start")
        end = _non_negative_int(end, label="end")
        quote_sha256 = _digest(quote_sha256, label="quote_sha256")
        if end < start:
            raise ValueError("end must be greater than or equal to start")
        snapshot = self._session_snapshot(session_id)
        record = next(
            (candidate for candidate in snapshot.records if candidate.id == source_id),
            None,
        )
        if record is None:
            raise KeyError(f"unknown source id: {source_id}")
        if end > len(record.content):
            raise StoreIntegrityError("rehydration span exceeds source content")
        quote = record.content[start:end]
        if hashlib.sha256(quote.encode("utf-8")).hexdigest() != quote_sha256:
            raise StoreIntegrityError("rehydration quote digest mismatch")
        result = {
            "schema": REHYDRATION_SCHEMA,
            "trust": "untrusted-evidence",
            "warning": (
                "Exact provenance establishes source fidelity, not truth, authority, "
                "or instruction priority."
            ),
            "session_id": session_id,
            "source_id": source_id,
            "source_record_sha256": record.record_sha256,
            "source_count": snapshot.source_count,
            "source_head_sha256": snapshot.source_head_sha256,
            "start": start,
            "end": end,
            "quote": quote,
            "quote_sha256": quote_sha256,
        }
        result["rehydration_sha256"] = _sha256_json(result)
        return result

    @staticmethod
    def _expected_transition_binding(
        generation: sqlite3.Row,
        *,
        from_state: str | None,
        to_state: str,
    ) -> tuple[str | None, str | None]:
        if from_state is None and to_state == "prepared":
            return (
                "source-snapshot-captured",
                _sha256_json(
                    {
                        "generation_id": generation["generation_id"],
                        "session_id": generation["session_id"],
                        "source_head_sha256": generation[
                            "captured_source_head_sha256"
                        ],
                        "source_digest_sha256": generation[
                            "captured_source_digest_sha256"
                        ],
                        "source_count": generation["captured_source_count"],
                        "parent_generation_id": generation[
                            "parent_generation_id"
                        ],
                        "expected_active_epoch": generation[
                            "expected_active_epoch"
                        ],
                    }
                ),
            )
        if from_state == "prepared" and to_state == "verified":
            return (
                "independent-replay-passed",
                _sha256_json(
                    {
                        "checkpoint_sha256": generation["checkpoint_sha256"],
                        "bundle_sha256": generation["bundle_sha256"],
                        "semantic_result_sha256": generation[
                            "semantic_result_sha256"
                        ],
                        "replay_sha256": generation["replay_sha256"],
                    }
                ),
            )
        if from_state == "verified" and to_state == "committed":
            return (
                "verified-evidence-reloaded",
                _sha256_json(
                    {
                        "bundle_sha256": generation["bundle_sha256"],
                        "replay_sha256": generation["replay_sha256"],
                        "semantic_result_sha256": generation[
                            "semantic_result_sha256"
                        ],
                    }
                ),
            )
        if from_state == "committed" and to_state == "active":
            return "active-pointer-cas-won", generation["bundle_sha256"]
        if from_state == "superseded" and to_state == "active":
            return (
                "explicit-rollback-target-reverified",
                generation["bundle_sha256"],
            )
        if to_state == "rolled_back":
            return (
                None,
                generation["bundle_sha256"]
                or generation["captured_source_head_sha256"],
            )
        return None, None

    def _validate_historical_transition_chain(
        self,
        generation: sqlite3.Row,
        transitions: Sequence[sqlite3.Row],
    ) -> tuple[str, ...]:
        if not transitions:
            raise StoreIntegrityError("generation has no retained transition history")
        current: str | None = None
        visited: list[str] = []
        for expected_ordinal, transition in enumerate(transitions):
            ordinal = _non_negative_int(
                transition["ordinal"],
                label="generation transition ordinal",
            )
            if ordinal != expected_ordinal:
                raise StoreIntegrityError(
                    "generation transition ordinals are not contiguous"
                )
            from_state = transition["from_state"]
            if from_state != current:
                raise StoreIntegrityError(
                    "generation transition from_state does not match prior state"
                )
            to_state = transition["to_state"]
            if not isinstance(to_state, str) or to_state not in GENERATION_STATES:
                raise StoreIntegrityError("generation transition has an invalid state")
            if to_state not in _ALLOWED_GENERATION_TRANSITIONS[current]:
                raise StoreIntegrityError("generation transition edge is not permitted")
            _identifier(
                transition["operation_id"],
                label="generation transition operation_id",
            )
            reason = _identifier(
                transition["reason_code"],
                label="generation transition reason_code",
            )
            evidence = _digest(
                transition["evidence_sha256"],
                label="generation transition evidence",
            )
            expected_reason, expected_evidence = self._expected_transition_binding(
                generation,
                from_state=from_state,
                to_state=to_state,
            )
            if expected_reason is not None and reason != expected_reason:
                raise StoreIntegrityError(
                    "generation transition reason does not match its edge"
                )
            if expected_evidence is not None and evidence != expected_evidence:
                raise StoreIntegrityError(
                    "generation transition evidence does not bind its edge"
                )
            current = to_state
            visited.append(to_state)
        if current != generation["state"]:
            raise StoreIntegrityError(
                "generation transition history does not end at the stored state"
            )
        return tuple(visited)

    def _historical_integrity_details(
        self,
        connection: sqlite3.Connection,
        sessions: Sequence[sqlite3.Row],
    ) -> tuple[dict[str, int], list[str]]:
        from .request_ledger import validate_final_request_ledger
        from .tokenizer import CanonicalUtf8ByteTokenizer

        issues: list[str] = []
        generations = connection.execute(
            """
            SELECT * FROM generations
            ORDER BY session_id, generation_id
            """
        ).fetchall()
        transitions = connection.execute(
            """
            SELECT * FROM generation_transitions
            ORDER BY generation_id, ordinal
            """
        ).fetchall()
        ledgers = connection.execute(
            """
            SELECT * FROM request_ledgers
            ORDER BY session_id, request_id
            """
        ).fetchall()
        operations = connection.execute(
            """
            SELECT * FROM operations
            ORDER BY session_id, request_id
            """
        ).fetchall()
        counts = {
            "generation_count": len(generations),
            "transition_count": len(transitions),
            "request_ledger_count": len(ledgers),
            "operation_count": len(operations),
        }

        session_data: dict[
            str,
            tuple[
                sqlite3.Row,
                tuple[SourceRecord, ...],
                Sequence[sqlite3.Row],
                tuple[str, ...],
            ],
        ] = {}
        source_rows_by_event: dict[tuple[str, str], sqlite3.Row] = {}
        for session_index, session in enumerate(sessions):
            label = f"session[{session_index}]"
            try:
                session_id = _identifier(
                    session["session_id"],
                    label=f"{label} session_id",
                )
                source_count = _non_negative_int(
                    session["source_count"],
                    label=f"{label} source_count",
                )
                source_head = _digest(
                    session["source_head_sha256"],
                    label=f"{label} source_head_sha256",
                )
                active_epoch = _non_negative_int(
                    session["active_epoch"],
                    label=f"{label} active_epoch",
                )
                active_id = session["active_generation_id"]
                if active_id is not None:
                    _identifier(active_id, label=f"{label} active_generation_id")
                stored_digest = session["source_digest_sha256"]
                if stored_digest is not None:
                    _digest(
                        stored_digest,
                        label=f"{label} source_digest_sha256",
                    )
                source_rows = connection.execute(
                    """
                    SELECT * FROM source_events
                    WHERE session_id = ? ORDER BY ordinal
                    """,
                    (session_id,),
                ).fetchall()
                if len(source_rows) != source_count:
                    raise StoreIntegrityError(
                        "session source count does not match retained rows"
                    )
                self._assert_atomic_index_matches(
                    connection,
                    session_id=session_id,
                    source_count=source_count,
                )
                records = self._records_from_rows(
                    source_rows,
                    expected_head=source_head,
                )
                computed_digest = _source_digest(records)
                if (
                    stored_digest is not None
                    and stored_digest != computed_digest
                ):
                    raise StoreIntegrityError("session source digest mismatch")
                prefix_heads = (SOURCE_HEAD_GENESIS,) + tuple(
                    _digest(
                        row["source_head_sha256"],
                        label=f"{label} retained source head",
                    )
                    for row in source_rows
                )
                if active_epoch > _MAX_SQLITE_INTEGER:
                    raise StoreIntegrityError("session active epoch is out of range")
                session_data[session_id] = (
                    session,
                    records,
                    source_rows,
                    prefix_heads,
                )
                source_rows_by_event.update(
                    ((session_id, row["event_id"]), row)
                    for row in source_rows
                )
            except (KeyError, StoreError, TypeError, ValueError) as exc:
                issues.append(f"{label}: {exc}")

        transition_groups: dict[str, list[sqlite3.Row]] = {}
        for transition_index, transition in enumerate(transitions):
            try:
                generation_id = _identifier(
                    transition["generation_id"],
                    label="transition generation_id",
                )
            except (KeyError, TypeError, ValueError) as exc:
                issues.append(f"transition[{transition_index}]: {exc}")
                continue
            transition_groups.setdefault(generation_id, []).append(transition)

        generation_by_id: dict[str, sqlite3.Row] = {}
        generation_states: dict[str, tuple[str, ...]] = {}
        for generation_index, generation in enumerate(generations):
            label = f"generation[{generation_index}]"
            try:
                generation_id = _identifier(
                    generation["generation_id"],
                    label=f"{label} generation_id",
                )
                session_id = _identifier(
                    generation["session_id"],
                    label=f"{label} session_id",
                )
                state = generation["state"]
                if not isinstance(state, str) or state not in GENERATION_STATES:
                    raise StoreIntegrityError("generation state is invalid")
                parent_id = generation["parent_generation_id"]
                if parent_id is not None:
                    _identifier(
                        parent_id,
                        label=f"{label} parent_generation_id",
                    )
                expected_epoch = _non_negative_int(
                    generation["expected_active_epoch"],
                    label=f"{label} expected_active_epoch",
                )
                source_count = _non_negative_int(
                    generation["captured_source_count"],
                    label=f"{label} captured_source_count",
                )
                source_head = _digest(
                    generation["captured_source_head_sha256"],
                    label=f"{label} captured_source_head_sha256",
                )
                source_digest = _digest(
                    generation["captured_source_digest_sha256"],
                    label=f"{label} captured_source_digest_sha256",
                )
                _digest(
                    generation["policy_sha256"],
                    label=f"{label} policy_sha256",
                )
                _identifier(
                    generation["tokenizer_identity"],
                    label=f"{label} tokenizer_identity",
                )
                data = session_data.get(session_id)
                if data is None:
                    raise StoreIntegrityError(
                        "generation session source history is unavailable"
                    )
                session, records, _source_rows, prefix_heads = data
                if source_count > len(records):
                    raise StoreIntegrityError(
                        "generation captures a missing source prefix"
                    )
                if source_head != prefix_heads[source_count]:
                    raise StoreIntegrityError(
                        "generation captured source head is not retained"
                    )
                if source_digest != _source_digest(records[:source_count]):
                    raise StoreIntegrityError(
                        "generation captured source digest mismatch"
                    )
                if expected_epoch > session["active_epoch"]:
                    raise StoreIntegrityError(
                        "generation expected epoch exceeds current session epoch"
                    )

                visited = self._validate_historical_transition_chain(
                    generation,
                    transition_groups.get(generation_id, ()),
                )
                generation_states[generation_id] = visited
                verification_values = tuple(
                    generation[field] for field in _VERIFICATION_PAYLOAD_FIELDS
                )
                verified_history = "verified" in visited
                if state == "prepared" or (
                    state == "rolled_back" and not verified_history
                ):
                    if (
                        generation["verification_passed"] != 0
                        or any(value is not None for value in verification_values)
                    ):
                        raise StoreIntegrityError(
                            "unverified generation carries verification payload"
                        )
                else:
                    self._validated_generation_row(generation)
                generation_by_id[generation_id] = generation
            except (KeyError, StoreError, TypeError, ValueError) as exc:
                issues.append(f"{label}: {exc}")

        for generation_id, generation in generation_by_id.items():
            parent_id = generation["parent_generation_id"]
            if parent_id is None:
                continue
            parent = generation_by_id.get(parent_id)
            if (
                parent is None
                or parent["session_id"] != generation["session_id"]
            ):
                issues.append(
                    f"generation {generation_id}: parent generation is not retained "
                    "in the same session"
                )

        for generation_id in transition_groups:
            if generation_id not in generation_by_id:
                issues.append(
                    f"transition history references invalid generation {generation_id}"
                )

        for session_id, data in session_data.items():
            session = data[0]
            session_generations = [
                generation
                for generation in generation_by_id.values()
                if generation["session_id"] == session_id
            ]
            active_rows = [
                generation
                for generation in session_generations
                if generation["state"] == "active"
            ]
            active_id = session["active_generation_id"]
            if active_id is None:
                if active_rows:
                    issues.append(
                        f"session {session_id}: active state exists without pointer"
                    )
            elif (
                len(active_rows) != 1
                or active_rows[0]["generation_id"] != active_id
            ):
                issues.append(
                    f"session {session_id}: active pointer/state relation is invalid"
                )
            activation_count = sum(
                state == "active"
                for generation in session_generations
                for state in generation_states.get(
                    generation["generation_id"],
                    (),
                )
            )
            if activation_count != session["active_epoch"]:
                issues.append(
                    f"session {session_id}: active epoch does not match retained "
                    "activation transitions"
                )

        tokenizer = CanonicalUtf8ByteTokenizer()
        for ledger_index, ledger_row in enumerate(ledgers):
            label = f"request_ledger[{ledger_index}]"
            try:
                session_id = _identifier(
                    ledger_row["session_id"],
                    label=f"{label} session_id",
                )
                generation_id = _identifier(
                    ledger_row["generation_id"],
                    label=f"{label} generation_id",
                )
                _identifier(
                    ledger_row["request_id"],
                    label=f"{label} request_id",
                )
                active_epoch = _non_negative_int(
                    ledger_row["active_epoch"],
                    label=f"{label} active_epoch",
                )
                raw = _decode_canonical_json(
                    ledger_row["ledger_json"],
                    label=f"{label} JSON",
                )
                if not isinstance(raw, dict):
                    raise StoreIntegrityError("request ledger must be an object")
                validated = validate_final_request_ledger(
                    raw,
                    tokenizer=tokenizer,
                )
                normalized = validated.to_dict()
                bindings = normalized["bindings"]
                generation = generation_by_id.get(generation_id)
                session = session_data.get(session_id)
                if generation is None or session is None:
                    raise StoreIntegrityError(
                        "request ledger generation/session is not retained"
                    )
                if generation["session_id"] != session_id:
                    raise StoreIntegrityError(
                        "request ledger generation belongs to another session"
                    )
                if (
                    validated.to_json() != ledger_row["ledger_json"]
                    or normalized["ledger_sha256"]
                    != ledger_row["ledger_sha256"]
                    or normalized["final_request_sha256"]
                    != ledger_row["final_request_sha256"]
                    or bindings["session_id"] != session_id
                    or bindings["generation_id"] != generation_id
                    or bindings["active_epoch"] != active_epoch
                    or bindings["semantic_result_digest"]
                    != generation["semantic_result_sha256"]
                    or active_epoch < 1
                    or active_epoch > session[0]["active_epoch"]
                    or "active"
                    not in generation_states.get(generation_id, ())
                    or bindings["source_head_sha256"] not in session[3]
                ):
                    raise StoreIntegrityError(
                        "request ledger binding or digest mismatch"
                    )
            except (KeyError, StoreError, TypeError, ValueError) as exc:
                issues.append(f"{label}: {exc}")

        for operation_index, operation in enumerate(operations):
            label = f"operation[{operation_index}]"
            try:
                session_id = _identifier(
                    operation["session_id"],
                    label=f"{label} session_id",
                )
                _identifier(
                    operation["request_id"],
                    label=f"{label} request_id",
                )
                payload_sha = _digest(
                    operation["payload_sha256"],
                    label=f"{label} payload_sha256",
                )
                result = _decode_canonical_json(
                    operation["result_json"],
                    label=f"{label} result",
                )
                expected_fields = {
                    "accepted",
                    "source_count",
                    "source_head_sha256",
                    "event_id",
                    "sequence",
                }
                if not isinstance(result, dict) or set(result) != expected_fields:
                    raise StoreIntegrityError(
                        "operation result fields are invalid"
                    )
                if type(result["accepted"]) is not bool:
                    raise StoreIntegrityError(
                        "operation accepted flag must be boolean"
                    )
                source_count = _non_negative_int(
                    result["source_count"],
                    label=f"{label} result source_count",
                )
                source_head = _digest(
                    result["source_head_sha256"],
                    label=f"{label} result source_head",
                )
                event_id = _identifier(
                    result["event_id"],
                    label=f"{label} result event_id",
                )
                sequence = _non_negative_int(
                    result["sequence"],
                    label=f"{label} result sequence",
                )
                session = session_data.get(session_id)
                if (
                    session is None
                    or source_count < 1
                    or source_count > len(session[1])
                    or source_head != session[3][source_count]
                    or sequence >= source_count
                ):
                    raise StoreIntegrityError(
                        "operation result source binding is invalid"
                    )
                source_row = source_rows_by_event.get((session_id, event_id))
                if source_row is None or source_row["sequence"] != sequence:
                    raise StoreIntegrityError(
                        "operation event is not retained at its sequence"
                    )
                if result["accepted"] is True and source_count != sequence + 1:
                    raise StoreIntegrityError(
                        "accepted operation result has an invalid source count"
                    )
                host_event = _decode_canonical_json(
                    source_row["host_event_json"],
                    label=f"{label} host event",
                )
                source_record = _decode_canonical_json(
                    source_row["source_record_json"],
                    label=f"{label} source record",
                )
                atomic_groups = _decode_canonical_json(
                    source_row["atomic_groups_json"],
                    label=f"{label} atomic groups",
                )
                expected_payload = _sha256_json(
                    {
                        "session_id": session_id,
                        "host_event": host_event,
                        "source_record": source_record,
                        "atomic_groups": atomic_groups,
                    }
                )
                if payload_sha != expected_payload:
                    raise StoreIntegrityError(
                        "operation payload digest does not bind retained source"
                    )
            except (KeyError, StoreError, TypeError, ValueError) as exc:
                issues.append(f"{label}: {exc}")

        return counts, issues

    def integrity_report(self) -> dict[str, Any]:
        """Check SQLite, foreign keys, event chains, active pointers, and evidence."""

        issues: list[str] = []
        with self._read() as connection:
            integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
            foreign_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            sessions = connection.execute(
                "SELECT * FROM sessions ORDER BY session_id"
            ).fetchall()
            try:
                historical_counts, historical_issues = (
                    self._historical_integrity_details(connection, sessions)
                )
            except (KeyError, StoreError, TypeError, ValueError) as exc:
                historical_counts = {}
                historical_issues = [
                    f"historical integrity inspection failed: {exc}"
                ]
        integrity_values = tuple(str(row[0]) for row in integrity_rows)
        if integrity_values != ("ok",):
            issues.extend(f"sqlite integrity: {value}" for value in integrity_values)
        if foreign_rows:
            issues.append(f"foreign_key_check returned {len(foreign_rows)} rows")
        issues.extend(historical_issues)
        for row in sessions:
            session_id = row["session_id"]
            try:
                snapshot = self._session_snapshot(session_id)
                active = self.read_active(session_id)
                if snapshot.active_generation_id is not None and active is None:
                    issues.append(f"{session_id}: active pointer returned no generation")
            except (KeyError, StoreError, TypeError, ValueError) as exc:
                issues.append(f"{session_id}: {exc}")
        report = {
            "schema": INTEGRITY_REPORT_SCHEMA,
            "passed": not issues,
            "issues": issues,
            "session_count": len(sessions),
            "store_uuid": self.store_uuid,
            "sqlite_integrity": list(integrity_values),
            "foreign_key_issue_count": len(foreign_rows),
        }
        report.update(historical_counts)
        report["report_sha256"] = _sha256_json(report)
        return report

    def list_generations(self, session_id: str) -> tuple[dict[str, Any], ...]:
        """Return bounded generation state metadata without bundle contents."""

        session_id = _identifier(session_id, label="session_id")
        with self._read() as connection:
            rows = connection.execute(
                """
                SELECT generation_id, state, parent_generation_id,
                       expected_active_epoch, captured_source_count,
                       captured_source_head_sha256, captured_source_digest_sha256,
                       policy_sha256, tokenizer_identity,
                       semantic_result_sha256, verification_passed
                FROM generations WHERE session_id = ?
                ORDER BY rowid
                """,
                (session_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def backup(self, destination: str | os.PathLike[str]) -> None:
        """Exclusively create a consistent backup using SQLite's backup API."""

        target = Path(destination).expanduser().absolute()
        if target == self.path:
            raise ValueError("backup destination must differ from the live store")
        _reject_link_or_reparse_chain(target, label="backup destination")
        if _path_entry_exists(target):
            raise FileExistsError(f"backup destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        _reject_link_or_reparse_chain(target, label="backup destination")
        descriptor = os.open(
            target,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            target_identity = _regular_file_identity(target, label="backup destination")
        finally:
            os.close(descriptor)
        _reject_link_or_reparse_chain(target, label="backup destination")
        source: sqlite3.Connection | None = None
        destination_connection: sqlite3.Connection | None = None
        try:
            source = self._connect()
            destination_connection = sqlite3.connect(
                f"{target.as_uri()}?mode=rw",
                uri=True,
            )
            destination_connection.execute("PRAGMA synchronous=FULL")
            source.backup(destination_connection)
            destination_connection.commit()
            mode = destination_connection.execute(
                "PRAGMA journal_mode=WAL"
            ).fetchone()
            if mode is None or str(mode[0]).casefold() != "wal":
                raise StoreIntegrityError("backup could not enter WAL mode")
            checkpoint = destination_connection.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone()
            if checkpoint is None or checkpoint[0] != 0:
                raise StoreIntegrityError("backup WAL checkpoint failed")
        finally:
            if destination_connection is not None:
                destination_connection.close()
            if source is not None:
                source.close()
        if (
            _regular_file_identity(target, label="backup destination")
            != target_identity
        ):
            raise StoreIntegrityError("backup destination identity changed")
        validated = SQLiteGenerationStore(target, require_existing=True)
        report = validated.integrity_report()
        if not report["passed"]:
            raise StoreIntegrityError("backup failed post-write integrity validation")
        if (
            _regular_file_identity(target, label="backup destination")
            != target_identity
        ):
            raise StoreIntegrityError(
                "backup destination identity changed during validation"
            )


__all__ = [
    "GENERATION_STATES",
    "INTEGRITY_REPORT_SCHEMA",
    "PROVISIONAL_STATES",
    "REHYDRATION_SCHEMA",
    "SOURCE_HEAD_GENESIS",
    "STORE_SCHEMA_VERSION",
    "ActiveGeneration",
    "AppendResult",
    "GenerationStateError",
    "ImmutableEventError",
    "IncompleteAtomicGroupError",
    "PreparedGeneration",
    "SQLiteGenerationStore",
    "SessionSnapshot",
    "StaleGenerationError",
    "StoreContentionError",
    "StoreError",
    "StoreIntegrityError",
]
