"""Bounded retained-evidence contracts for offline integration diagnostics.

The reports validated here are integrity-bound diagnostic records.  They are
not attestations, live OpenHands results, superiority evidence, or semantic
completeness claims.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .replay import replay_final_request
from .storage import (
    GENERATION_STATES,
    SQLiteGenerationStore,
    _regular_file_identity,
    _reject_link_or_reparse_chain,
)
from .tokenizer import CanonicalUtf8ByteTokenizer

OFFLINE_SCENARIO_SCHEMA = "ctxc-openhands-offline-scenario-0.2"
SOAK_REPORT_SCHEMA = "ctxc-openhands-soak-report-0.2"
EVIDENCE_VERIFICATION_SCHEMA = "ctxc-openhands-evidence-verification-0.1"

LIVE_DEPENDENCY_BLOCKER = (
    "No hash-pinned offline wheelhouse for openhands-ai==1.8.0, "
    "openhands-sdk==1.27.0, openhands-tools==1.27.0, and "
    "openhands-agent-server==1.27.0 is present. A real offline/live "
    "OpenHands demonstration was not executed."
)

MAX_EVIDENCE_JSON_BYTES = 4 * 1024 * 1024
MAX_SOAK_EVENTS = 10_000
MAX_SOAK_COMPACTIONS = 100
_MAX_JSON_DEPTH = 64
_MAX_JSON_ITEMS = 250_000
_MAX_STRING_BYTES = 1_048_576
_MAX_PATH_BYTES = 16_384
_MAX_SQLITE_BYTES = 16 * 1024 * 1024 * 1024
_DIGEST_LENGTH = 64
_HEX = frozenset("0123456789abcdef")
_REPLAY_FIELDS = frozenset(
    {
        "passed",
        "issues",
        "component_counts",
        "total_tokens",
        "tokenizer_identity",
        "tokenizer_vector_sha256",
        "tool_schema_sha256",
        "transport_sha256",
        "final_request_sha256",
        "ledger_sha256",
    }
)
_COMPONENT_CATEGORIES = frozenset(
    {
        "prompts",
        "verified_memory",
        "recent_tail",
        "current_turn",
        "retrieval",
        "attachments",
        "tool_schemas",
        "provider_framing",
    }
)


class EvidenceValidationError(ValueError):
    """Raised when retained evidence is malformed, unbounded, or tampered."""


def canonical_json_bytes(value: object) -> bytes:
    """Return the one canonical UTF-8 representation used by evidence hashes."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, UnicodeEncodeError, ValueError) as exc:
        raise EvidenceValidationError("evidence must be bounded canonical UTF-8 JSON") from exc


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _bounded_detach_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Detach caller-owned containers before canonical JSON can traverse them."""

    item_count = 0
    seen_containers: set[int] = set()

    def bump() -> None:
        nonlocal item_count
        item_count += 1
        if item_count > _MAX_JSON_ITEMS:
            raise EvidenceValidationError("evidence mapping exceeds the item limit")

    def text(item: object, *, label: str) -> str:
        if not isinstance(item, str):
            raise EvidenceValidationError(f"{label} must be a string")
        try:
            byte_length = len(item.encode("utf-8", errors="strict"))
        except UnicodeEncodeError as exc:
            raise EvidenceValidationError(f"{label} is not valid UTF-8 text") from exc
        if byte_length > _MAX_STRING_BYTES:
            raise EvidenceValidationError(f"{label} exceeds the byte limit")
        return item

    def detach(item: object, *, depth: int) -> object:
        if depth > _MAX_JSON_DEPTH:
            raise EvidenceValidationError("evidence mapping exceeds the depth limit")
        bump()
        if isinstance(item, str):
            return text(item, label="evidence string")
        if item is None or isinstance(item, (bool, int, float)):
            return item
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in seen_containers:
                raise EvidenceValidationError(
                    "evidence mapping contains a cyclic or shared container"
                )
            seen_containers.add(identity)
            result: dict[str, Any] = {}
            try:
                iterator = iter(item.items())
                while True:
                    try:
                        pair = next(iterator)
                    except StopIteration:
                        break
                    key, child = pair
                    bump()
                    detached_key = text(key, label="evidence mapping key")
                    if detached_key in result:
                        raise EvidenceValidationError(
                            f"duplicate evidence mapping key: {detached_key}"
                        )
                    result[detached_key] = detach(child, depth=depth + 1)
            except EvidenceValidationError:
                raise
            except (Exception, RecursionError) as exc:
                raise EvidenceValidationError(
                    "evidence mapping could not be traversed safely"
                ) from exc
            return result
        if isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in seen_containers:
                raise EvidenceValidationError(
                    "evidence mapping contains a cyclic or shared container"
                )
            seen_containers.add(identity)
            result_list: list[object] = []
            try:
                for child in item:
                    result_list.append(detach(child, depth=depth + 1))
            except EvidenceValidationError:
                raise
            except (Exception, RecursionError) as exc:
                raise EvidenceValidationError(
                    "evidence sequence could not be traversed safely"
                ) from exc
            return result_list
        raise EvidenceValidationError("evidence mapping contains a non-JSON value")

    detached = detach(value, depth=1)
    if not isinstance(detached, dict):  # pragma: no cover - input type invariant
        raise EvidenceValidationError("evidence report must be an object")
    return detached


def _self_hash(value: Mapping[str, Any]) -> str:
    return _sha256(
        canonical_json_bytes({key: item for key, item in value.items() if key != "report_sha256"})
    )


def finalize_evidence_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-detach a report and add its canonical self-hash."""

    detached_input = _bounded_detach_mapping(report)
    if "report_sha256" in detached_input:
        raise EvidenceValidationError("report must not already contain report_sha256")
    raw = canonical_json_bytes(detached_input)
    if len(raw) > MAX_EVIDENCE_JSON_BYTES:
        raise EvidenceValidationError("evidence report exceeds the JSON byte limit")
    detached = json.loads(raw.decode("utf-8"))
    if not isinstance(detached, dict):  # pragma: no cover - construction invariant
        raise AssertionError("evidence report did not detach to an object")
    detached["report_sha256"] = _self_hash(detached)
    return detached


def runtime_identity() -> dict[str, str]:
    """Return bounded runtime identity fields shared by offline reports."""

    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
    }


def producer_command(
    *,
    library_name: str,
    subcommand: str,
    parameters: Mapping[str, Any],
    argv: Sequence[str] | None,
) -> dict[str, Any]:
    """Bind either an actual captured CLI argv or an honest library call."""

    for label, value in (("library_name", library_name), ("subcommand", subcommand)):
        if not isinstance(value, str) or not value:
            raise EvidenceValidationError(f"{label} must be a non-empty string")
    if not isinstance(parameters, Mapping):
        raise EvidenceValidationError("producer parameters must be an object")
    try:
        detached_parameters = json.loads(canonical_json_bytes(dict(parameters)))
    except (TypeError, ValueError) as exc:
        raise EvidenceValidationError("producer parameters must be canonical JSON") from exc
    if argv is None:
        return {
            "producer": "library-api",
            "name": library_name,
            "argv": [],
            "parameters": detached_parameters,
        }
    if isinstance(argv, (str, bytes, bytearray)) or not isinstance(argv, Sequence):
        raise EvidenceValidationError("producer argv must be a sequence of strings")
    captured = list(argv)
    if not captured or len(captured) > 64:
        raise EvidenceValidationError("producer argv must contain 1 to 64 arguments")
    for index, item in enumerate(captured):
        if not isinstance(item, str) or not item:
            raise EvidenceValidationError(f"producer argv item {index} must be a non-empty string")
        if len(item.encode("utf-8")) > _MAX_PATH_BYTES:
            raise EvidenceValidationError(f"producer argv item {index} exceeds the byte limit")
    if captured[0] != subcommand:
        raise EvidenceValidationError(
            "captured CLI argv does not begin with the producing subcommand"
        )
    return {
        "producer": "cli",
        "name": "ctxc-openhands",
        "argv": captured,
        "parameters": detached_parameters,
    }


def _hash_file(path: Path) -> tuple[str, int]:
    try:
        expected_identity = _regular_file_identity(path, label="evidence database")
        before = path.lstat()
    except (OSError, ValueError) as exc:
        raise EvidenceValidationError(f"cannot stat evidence database: {exc}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise EvidenceValidationError("evidence database must be a regular non-symbolic file")
    if before.st_size <= 0 or before.st_size > _MAX_SQLITE_BYTES:
        raise EvidenceValidationError("evidence database byte length is invalid")
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_SQLITE_BYTES:
                    raise EvidenceValidationError("evidence database exceeds the byte limit")
                digest.update(chunk)
            opened = os.fstat(handle.fileno())
    except OSError as exc:
        raise EvidenceValidationError(f"cannot hash evidence database: {exc}") from exc
    try:
        actual_identity = _regular_file_identity(path, label="evidence database")
        after = path.lstat()
    except (OSError, ValueError) as exc:
        raise EvidenceValidationError(f"cannot restat evidence database: {exc}") from exc
    if (
        opened.st_size != total
        or before.st_size != total
        or after.st_size != total
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
        or expected_identity != (opened.st_dev, opened.st_ino)
        or expected_identity != actual_identity
        or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        or getattr(opened, "st_nlink", 1) not in (0, 1)
        or getattr(after, "st_nlink", 1) not in (0, 1)
        or not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(after.st_mode)
    ):
        raise EvidenceValidationError("evidence database changed while hashing")
    return digest.hexdigest(), total


def checkpoint_and_bind_database(database: str | Path) -> dict[str, Any]:
    """Checkpoint WAL safely, then bind the immutable main database bytes."""

    path = Path(database).expanduser().absolute()
    if not path.exists():
        raise EvidenceValidationError("evidence database does not exist")
    uri = path.as_uri() + "?mode=rw"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=30.0)
        try:
            connection.execute("PRAGMA busy_timeout=30000")
            journal_row = connection.execute("PRAGMA journal_mode").fetchone()
            if journal_row is None or str(journal_row[0]).lower() != "wal":
                raise EvidenceValidationError("evidence database must use SQLite WAL journal mode")
            row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise EvidenceValidationError(f"SQLite WAL checkpoint failed: {exc}") from exc
    if (
        row is None
        or len(row) != 3
        or any(isinstance(item, bool) or not isinstance(item, int) for item in row)
    ):
        raise EvidenceValidationError("SQLite returned an invalid WAL checkpoint result")
    busy, log_frames, checkpointed_frames = (int(item) for item in row)
    if busy != 0 or log_frames != checkpointed_frames:
        raise EvidenceValidationError(
            "SQLite WAL checkpoint did not checkpoint every available frame"
        )
    wal_path = Path(str(path) + "-wal")
    shm_path = Path(str(path) + "-shm")
    try:
        wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
    except OSError as exc:
        raise EvidenceValidationError(f"cannot inspect SQLite WAL: {exc}") from exc
    if wal_path.exists() or shm_path.exists():
        raise EvidenceValidationError(
            "SQLite WAL/SHM sidecars remained after the evidence checkpoint"
        )
    digest, byte_length = _hash_file(path)
    return {
        "sha256": digest,
        "byte_length": byte_length,
        "checkpoint": {
            "mode": "TRUNCATE",
            "busy": busy,
            "log_frames": log_frames,
            "checkpointed_frames": checkpointed_frames,
            "wal_bytes_after": wal_bytes,
        },
    }


def write_evidence_report(path: str | Path, value: Mapping[str, Any]) -> None:
    """Create one canonical report without overwriting earlier evidence."""

    target = Path(path).expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        _reject_link_or_reparse_chain(target, label="evidence report path")
    except ValueError as exc:
        raise EvidenceValidationError(f"unsafe evidence report path: {exc}") from exc
    payload = canonical_json_bytes(value) + b"\n"
    if len(payload) > MAX_EVIDENCE_JSON_BYTES + 1:
        raise EvidenceValidationError("evidence report exceeds the JSON byte limit")
    try:
        with target.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise FileExistsError(
            f"evidence report already exists: {target}"
        ) from None


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise EvidenceValidationError(f"non-finite JSON number is not allowed: {value}")


def _check_structure(value: object) -> None:
    pending: list[tuple[object, int]] = [(value, 1)]
    seen = 0
    while pending:
        item, depth = pending.pop()
        if depth > _MAX_JSON_DEPTH:
            raise EvidenceValidationError("evidence JSON exceeds the depth limit")
        seen += 1
        if seen > _MAX_JSON_ITEMS:
            raise EvidenceValidationError("evidence JSON exceeds the item limit")
        if isinstance(item, str):
            try:
                size = len(item.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise EvidenceValidationError("evidence contains invalid UTF-8 text") from exc
            if size > _MAX_STRING_BYTES:
                raise EvidenceValidationError("evidence string exceeds the byte limit")
        elif isinstance(item, dict):
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif item is None or isinstance(item, (bool, int, float)):
            continue
        else:  # pragma: no cover - JSON decoder excludes other types
            raise EvidenceValidationError("evidence contains a non-JSON value")


def load_evidence_report(path: str | Path) -> dict[str, Any]:
    """Load one bounded report and require exact canonical UTF-8 JSON bytes."""

    target = Path(path).expanduser().absolute()
    try:
        expected_identity = _regular_file_identity(
            target,
            label="evidence report path",
        )
        before = target.lstat()
    except OSError as exc:
        raise EvidenceValidationError(f"cannot stat evidence report: {exc}") from exc
    except ValueError as exc:
        raise EvidenceValidationError(f"unsafe evidence report path: {exc}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise EvidenceValidationError("evidence report must be a regular non-symbolic file")
    if before.st_size <= 0 or before.st_size > MAX_EVIDENCE_JSON_BYTES + 1:
        raise EvidenceValidationError("evidence report byte length is invalid")
    try:
        with target.open("rb") as stream:
            raw = stream.read(MAX_EVIDENCE_JSON_BYTES + 2)
            opened = os.fstat(stream.fileno())
        actual_identity = _regular_file_identity(
            target,
            label="evidence report path",
        )
        after = target.lstat()
    except OSError as exc:
        raise EvidenceValidationError(f"cannot read evidence report: {exc}") from exc
    except ValueError as exc:
        raise EvidenceValidationError(f"unsafe evidence report path: {exc}") from exc
    if (
        len(raw) != before.st_size
        or len(raw) != opened.st_size
        or len(raw) != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
        or expected_identity != (opened.st_dev, opened.st_ino)
        or expected_identity != actual_identity
        or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        or getattr(opened, "st_nlink", 1) not in (0, 1)
        or getattr(after, "st_nlink", 1) not in (0, 1)
        or not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(after.st_mode)
    ):
        raise EvidenceValidationError("evidence report identity or bytes changed while reading")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise EvidenceValidationError("evidence report is not valid UTF-8") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        raise EvidenceValidationError("evidence report is not bounded valid JSON") from exc
    if not isinstance(value, dict):
        raise EvidenceValidationError("evidence report must be an object")
    _check_structure(value)
    canonical = canonical_json_bytes(value)
    if raw not in (canonical, canonical + b"\n"):
        raise EvidenceValidationError(
            "evidence report must use canonical JSON with at most one final newline"
        )
    return value


def _expect_fields(value: object, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceValidationError(f"{label} must be an object")
    keys = set(value)
    if keys != fields:
        missing = sorted(fields - keys)
        unknown = sorted(str(key) for key in keys - fields)
        detail: list[str] = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if unknown:
            detail.append("unknown " + ", ".join(unknown))
        raise EvidenceValidationError(f"{label} fields are not exact: {'; '.join(detail)}")
    return value


def _string(
    value: object,
    label: str,
    *,
    exact: str | None = None,
    max_bytes: int = _MAX_STRING_BYTES,
) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceValidationError(f"{label} must be a non-empty string")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise EvidenceValidationError(f"{label} must be valid UTF-8") from exc
    if size > max_bytes:
        raise EvidenceValidationError(f"{label} exceeds the byte limit")
    if exact is not None and value != exact:
        raise EvidenceValidationError(f"{label} must equal {exact!r}")
    return value


def _integer(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = (1 << 63) - 1,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvidenceValidationError(f"{label} must be an integer")
    if value < minimum or value > maximum:
        raise EvidenceValidationError(f"{label} is out of bounds")
    return value


def _false(value: object, label: str) -> None:
    if value is not False:
        raise EvidenceValidationError(f"{label} must be false")


def _true(value: object, label: str) -> None:
    if value is not True:
        raise EvidenceValidationError(f"{label} must be true")


def _digest(value: object, label: str) -> str:
    text = _string(value, label, max_bytes=_DIGEST_LENGTH)
    if len(text) != _DIGEST_LENGTH or any(character not in _HEX for character in text):
        raise EvidenceValidationError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _validate_runtime(value: object) -> None:
    runtime = _expect_fields(
        value,
        frozenset({"python", "implementation", "platform", "sqlite"}),
        "runtime",
    )
    for key, item in runtime.items():
        _string(item, f"runtime.{key}", max_bytes=4096)


def _parse_cli_options(
    argv: object,
    *,
    subcommand: str,
    allowed: frozenset[str],
    required: frozenset[str],
) -> dict[str, str]:
    if not isinstance(argv, list) or not argv or argv[0] != subcommand:
        raise EvidenceValidationError(
            "CLI command argv must begin with the producing subcommand"
        )
    parsed: dict[str, str] = {}
    index = 1
    while index < len(argv):
        token = argv[index]
        if not isinstance(token, str) or not token.startswith("--") or token == "--":
            raise EvidenceValidationError("CLI command argv contains a positional argument")
        if "=" in token:
            option, value = token.split("=", 1)
            index += 1
        else:
            option = token
            if index + 1 >= len(argv):
                raise EvidenceValidationError(f"CLI option {option} is missing its value")
            value = argv[index + 1]
            if not isinstance(value, str) or value.startswith("--"):
                raise EvidenceValidationError(f"CLI option {option} is missing its value")
            index += 2
        if option not in allowed:
            raise EvidenceValidationError(f"CLI option is not allowed: {option}")
        if option in parsed:
            raise EvidenceValidationError(f"CLI option is duplicated: {option}")
        if not isinstance(value, str) or not value:
            raise EvidenceValidationError(f"CLI option {option} has an empty value")
        _string(value, f"CLI option {option}", max_bytes=_MAX_PATH_BYTES)
        parsed[option] = value
    missing = required - parsed.keys()
    if missing:
        raise EvidenceValidationError(
            "CLI command argv is missing required option(s): "
            + ", ".join(sorted(missing))
        )
    return parsed


def _normalized_cli_path(value: str, *, label: str) -> Path:
    try:
        return Path(value).expanduser().absolute()
    except (OSError, RuntimeError, ValueError) as exc:
        raise EvidenceValidationError(f"{label} is not a valid path") from exc


def _decimal_cli_integer(value: str, *, label: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise EvidenceValidationError(f"{label} must be a base-10 positive integer")
    return int(value, 10)


def _validate_common(
    value: dict[str, Any],
    *,
    library_name: str,
    subcommand: str,
) -> None:
    _string(value["status"], "status")
    command = _expect_fields(
        value["command"],
        frozenset({"producer", "name", "argv", "parameters"}),
        "command",
    )
    producer = _string(command["producer"], "command.producer")
    argv = command["argv"]
    if not isinstance(argv, list) or len(argv) > 64:
        raise EvidenceValidationError("command.argv must be a bounded array")
    for index, item in enumerate(argv):
        _string(item, f"command.argv[{index}]", max_bytes=_MAX_PATH_BYTES)
    if producer == "library-api":
        _string(command["name"], "command.name", exact=library_name)
        if argv:
            raise EvidenceValidationError("library-api command argv must be empty")
    elif producer == "cli":
        _string(command["name"], "command.name", exact="ctxc-openhands")
        if not argv or argv[0] != subcommand:
            raise EvidenceValidationError(
                "CLI command argv must begin with the producing subcommand"
            )
    else:
        raise EvidenceValidationError("command.producer is unsupported")
    if not isinstance(command["parameters"], dict):
        raise EvidenceValidationError("command.parameters must be an object")
    _validate_runtime(value["runtime"])
    isolation = _expect_fields(
        value["isolation"],
        frozenset(
            {
                "execution_path_network_capability",
                "network_isolation_enforced",
                "paid_service_use",
            }
        ),
        "isolation",
    )
    _string(
        isolation["execution_path_network_capability"],
        "isolation.execution_path_network_capability",
        exact="none",
    )
    _false(
        isolation["network_isolation_enforced"],
        "isolation.network_isolation_enforced",
    )
    _string(
        isolation["paid_service_use"],
        "isolation.paid_service_use",
        exact="none",
    )
    live = _expect_fields(
        value["live_openhands"], frozenset({"status", "blocker"}), "live_openhands"
    )
    _string(live["status"], "live_openhands.status", exact="blocked-not-run")
    _string(
        live["blocker"],
        "live_openhands.blocker",
        exact=LIVE_DEPENDENCY_BLOCKER,
    )
    database = _expect_fields(
        value["database"],
        frozenset({"sha256", "byte_length", "checkpoint"}),
        "database",
    )
    _digest(database["sha256"], "database.sha256")
    _integer(database["byte_length"], "database.byte_length", minimum=1)
    checkpoint = _expect_fields(
        database["checkpoint"],
        frozenset(
            {
                "mode",
                "busy",
                "log_frames",
                "checkpointed_frames",
                "wal_bytes_after",
            }
        ),
        "database.checkpoint",
    )
    _string(checkpoint["mode"], "database.checkpoint.mode", exact="TRUNCATE")
    for field in ("busy", "log_frames", "checkpointed_frames", "wal_bytes_after"):
        _integer(checkpoint[field], f"database.checkpoint.{field}")
    if (
        checkpoint["busy"] != 0
        or checkpoint["log_frames"] != checkpoint["checkpointed_frames"]
        or checkpoint["wal_bytes_after"] != 0
    ):
        raise EvidenceValidationError(
            "database checkpoint must show a complete non-busy empty-WAL checkpoint"
        )
    _false(value["semantic_completeness_claimed"], "semantic_completeness_claimed")
    _digest(value["integrity_report_sha256"], "integrity_report_sha256")


def _validate_session(value: object) -> None:
    session = _expect_fields(
        value,
        frozenset(
            {
                "session_id",
                "source_count",
                "source_head_sha256",
                "active_generation_id",
                "active_epoch",
                "active_semantic_result_digest",
                "generation_states",
            }
        ),
        "session",
    )
    _string(session["session_id"], "session.session_id", max_bytes=256)
    _integer(session["source_count"], "session.source_count", minimum=1)
    _digest(session["source_head_sha256"], "session.source_head_sha256")
    _string(
        session["active_generation_id"],
        "session.active_generation_id",
        max_bytes=256,
    )
    _integer(session["active_epoch"], "session.active_epoch", minimum=1)
    _digest(
        session["active_semantic_result_digest"],
        "session.active_semantic_result_digest",
    )
    states = session["generation_states"]
    if not isinstance(states, list) or not states or len(states) > 10_000:
        raise EvidenceValidationError("session.generation_states must be a bounded non-empty array")
    seen: set[str] = set()
    active_count = 0
    for index, item in enumerate(states):
        state = _expect_fields(
            item, frozenset({"generation_id", "state"}), f"generation state {index}"
        )
        generation_id = _string(state["generation_id"], f"generation state {index}.generation_id")
        if generation_id in seen:
            raise EvidenceValidationError("generation state ids must be unique")
        seen.add(generation_id)
        state_name = _string(state["state"], f"generation state {index}.state")
        if state_name not in GENERATION_STATES:
            raise EvidenceValidationError("generation state is unknown")
        active_count += state_name == "active"
    if active_count != 1:
        raise EvidenceValidationError(
            "generation states must contain exactly one active generation"
        )


def _validate_replay(value: object) -> None:
    replay = _expect_fields(value, _REPLAY_FIELDS, "final_request.replay")
    _true(replay["passed"], "final_request.replay.passed")
    if replay["issues"] != []:
        raise EvidenceValidationError("passing final request replay must have no issues")
    counts = replay["component_counts"]
    if not isinstance(counts, dict) or set(counts) != _COMPONENT_CATEGORIES:
        raise EvidenceValidationError("final request component fields are not exact")
    for category, count in counts.items():
        _integer(count, f"final_request.replay.component_counts.{category}")
    _integer(replay["total_tokens"], "final_request.replay.total_tokens")
    _string(
        replay["tokenizer_identity"],
        "final_request.replay.tokenizer_identity",
    )
    for field in (
        "tokenizer_vector_sha256",
        "tool_schema_sha256",
        "transport_sha256",
        "final_request_sha256",
        "ledger_sha256",
    ):
        _digest(replay[field], f"final_request.replay.{field}")


def _validate_scenario_report(report: dict[str, Any]) -> None:
    fields = frozenset(
        {
            "schema",
            "evidence_kind",
            "status",
            "command",
            "runtime",
            "isolation",
            "live_openhands",
            "database",
            "session",
            "forced_compaction_count",
            "injected_crash_point",
            "old_generation_visible_after_crash",
            "recovered_generation",
            "compactions",
            "constraints",
            "constraints_retained_exactly",
            "no_event_loss",
            "no_event_duplication",
            "contiguous_sequences",
            "integrity_report_sha256",
            "semantic_completeness_claimed",
            "final_request",
            "report_sha256",
        }
    )
    _expect_fields(report, fields, "scenario report")
    _string(report["schema"], "schema", exact=OFFLINE_SCENARIO_SCHEMA)
    _string(
        report["evidence_kind"],
        "evidence_kind",
        exact="offline-crash-scenario",
    )
    _string(
        report["status"],
        "status",
        exact="passed-offline-fake-runtime",
    )
    _validate_common(
        report,
        library_name="ctxc_openhands.scenario.run_offline_crash_scenario",
        subcommand="offline-scenario",
    )
    parameters = _expect_fields(
        report["command"]["parameters"],
        frozenset({"database"}),
        "command.parameters",
    )
    database_parameter = _string(
        parameters["database"],
        "command.parameters.database",
        max_bytes=_MAX_PATH_BYTES,
    )
    if report["command"]["producer"] == "cli":
        cli_options = _parse_cli_options(
            report["command"]["argv"],
            subcommand="offline-scenario",
            allowed=frozenset({"--database", "--output"}),
            required=frozenset({"--database"}),
        )
        if _normalized_cli_path(
            cli_options["--database"],
            label="CLI database",
        ) != _normalized_cli_path(
            database_parameter,
            label="command.parameters.database",
        ):
            raise EvidenceValidationError(
                "CLI database does not match command.parameters.database"
            )
    _validate_session(report["session"])
    _integer(
        report["forced_compaction_count"],
        "forced_compaction_count",
        minimum=3,
        maximum=3,
    )
    _string(
        report["injected_crash_point"],
        "injected_crash_point",
        exact="activate.after_new_active",
    )
    _string(
        report["old_generation_visible_after_crash"],
        "old_generation_visible_after_crash",
    )
    _string(report["recovered_generation"], "recovered_generation")
    compactions = report["compactions"]
    if not isinstance(compactions, list) or len(compactions) != 3:
        raise EvidenceValidationError("scenario compactions must contain three entries")
    for index, item in enumerate(compactions):
        compaction = _expect_fields(
            item,
            frozenset(
                {
                    "ordinal",
                    "generation_id",
                    "active_epoch",
                    "source_count",
                    "source_head_sha256",
                    "bundle_sha256",
                    "semantic_result_digest",
                    "recovered_after_injected_crash",
                }
            ),
            f"scenario compaction {index}",
        )
        _integer(compaction["ordinal"], f"scenario compaction {index}.ordinal")
        _string(
            compaction["generation_id"],
            f"scenario compaction {index}.generation_id",
        )
        _integer(
            compaction["active_epoch"],
            f"scenario compaction {index}.active_epoch",
            minimum=1,
        )
        _integer(
            compaction["source_count"],
            f"scenario compaction {index}.source_count",
            minimum=1,
        )
        for field in (
            "source_head_sha256",
            "bundle_sha256",
            "semantic_result_digest",
        ):
            _digest(compaction[field], f"scenario compaction {index}.{field}")
        expected_recovered = index == 2
        if compaction["recovered_after_injected_crash"] is not expected_recovered:
            raise EvidenceValidationError(
                "only the third scenario compaction may be crash-recovered"
            )
    constraints = report["constraints"]
    if (
        not isinstance(constraints, list)
        or len(constraints) != 3
        or any(not isinstance(item, str) or not item for item in constraints)
    ):
        raise EvidenceValidationError("scenario constraints must contain three strings")
    _true(report["constraints_retained_exactly"], "constraints_retained_exactly")
    _true(report["no_event_loss"], "no_event_loss")
    _true(report["no_event_duplication"], "no_event_duplication")
    _true(report["contiguous_sequences"], "contiguous_sequences")
    final_request = _expect_fields(
        report["final_request"],
        frozenset({"request_id", "replay", "fake_dispatch_receipt"}),
        "final_request",
    )
    request_id = _string(final_request["request_id"], "final_request.request_id")
    _validate_replay(final_request["replay"])
    receipt = _expect_fields(
        final_request["fake_dispatch_receipt"],
        frozenset(
            {
                "request_id",
                "final_request_sha256",
                "response_sha256",
                "total_tokens",
                "component_counts",
            }
        ),
        "final_request.fake_dispatch_receipt",
    )
    if _string(receipt["request_id"], "receipt.request_id") != request_id:
        raise EvidenceValidationError("fake dispatch receipt request id mismatch")
    _digest(receipt["final_request_sha256"], "receipt.final_request_sha256")
    _digest(receipt["response_sha256"], "receipt.response_sha256")
    _integer(receipt["total_tokens"], "receipt.total_tokens")
    counts = receipt["component_counts"]
    if not isinstance(counts, dict) or set(counts) != _COMPONENT_CATEGORIES:
        raise EvidenceValidationError("fake dispatch component fields are not exact")
    for category, count in counts.items():
        _integer(count, f"receipt.component_counts.{category}")
    replay = final_request["replay"]
    if (
        receipt["final_request_sha256"] != replay["final_request_sha256"]
        or receipt["total_tokens"] != replay["total_tokens"]
        or receipt["component_counts"] != replay["component_counts"]
    ):
        raise EvidenceValidationError("fake dispatch receipt does not bind the exact replay")


def _validate_soak_report(report: dict[str, Any]) -> None:
    fields = frozenset(
        {
            "schema",
            "evidence_kind",
            "status",
            "command",
            "runtime",
            "isolation",
            "live_openhands",
            "database",
            "session",
            "event_count",
            "compaction_count",
            "restart_every_compactions",
            "generation_ids",
            "bundle_sha256s",
            "semantic_result_digests",
            "no_event_loss",
            "no_event_duplication",
            "contiguous_sequences",
            "integrity_report_sha256",
            "semantic_completeness_claimed",
            "report_sha256",
        }
    )
    _expect_fields(report, fields, "soak report")
    _string(report["schema"], "schema", exact=SOAK_REPORT_SCHEMA)
    _string(report["evidence_kind"], "evidence_kind", exact="deterministic-soak")
    _string(report["status"], "status", exact="passed")
    _validate_common(
        report,
        library_name="ctxc_openhands.soak.run_deterministic_soak",
        subcommand="soak",
    )
    parameters = _expect_fields(
        report["command"]["parameters"],
        frozenset(
            {
                "database",
                "events",
                "compactions",
                "restart_every_compactions",
            }
        ),
        "command.parameters",
    )
    database_parameter = _string(
        parameters["database"],
        "command.parameters.database",
        max_bytes=_MAX_PATH_BYTES,
    )
    event_count = _integer(
        parameters["events"],
        "command.parameters.events",
        minimum=1,
        maximum=MAX_SOAK_EVENTS,
    )
    compaction_count = _integer(
        parameters["compactions"],
        "command.parameters.compactions",
        minimum=1,
        maximum=MAX_SOAK_COMPACTIONS,
    )
    restart = parameters["restart_every_compactions"]
    if restart is not None:
        _integer(restart, "command.parameters.restart_every_compactions", minimum=1)
    if report["event_count"] != event_count or report["compaction_count"] != compaction_count:
        raise EvidenceValidationError("soak command counts do not match report counts")
    if report["restart_every_compactions"] != restart:
        raise EvidenceValidationError("soak restart parameter does not match report")
    if report["command"]["producer"] == "cli":
        cli_options = _parse_cli_options(
            report["command"]["argv"],
            subcommand="soak",
            allowed=frozenset(
                {
                    "--database",
                    "--events",
                    "--compactions",
                    "--restart-every",
                    "--output",
                }
            ),
            required=frozenset({"--database"}),
        )
        if _normalized_cli_path(
            cli_options["--database"],
            label="CLI database",
        ) != _normalized_cli_path(
            database_parameter,
            label="command.parameters.database",
        ):
            raise EvidenceValidationError(
                "CLI database does not match command.parameters.database"
            )
        cli_events = _decimal_cli_integer(
            cli_options.get("--events", str(MAX_SOAK_EVENTS)),
            label="CLI --events",
        )
        cli_compactions = _decimal_cli_integer(
            cli_options.get("--compactions", str(MAX_SOAK_COMPACTIONS)),
            label="CLI --compactions",
        )
        cli_restart = (
            None
            if "--restart-every" not in cli_options
            else _decimal_cli_integer(
                cli_options["--restart-every"],
                label="CLI --restart-every",
            )
        )
        if cli_events != event_count or cli_compactions != compaction_count:
            raise EvidenceValidationError(
                "CLI soak counts do not match command.parameters"
            )
        if cli_restart != restart:
            raise EvidenceValidationError(
                "CLI soak restart does not match command.parameters"
            )
    _validate_session(report["session"])
    _integer(report["event_count"], "event_count", minimum=1, maximum=MAX_SOAK_EVENTS)
    _integer(
        report["compaction_count"],
        "compaction_count",
        minimum=1,
        maximum=MAX_SOAK_COMPACTIONS,
    )
    if report["compaction_count"] > report["event_count"]:
        raise EvidenceValidationError("compaction_count cannot exceed event_count")
    for field in ("generation_ids", "bundle_sha256s", "semantic_result_digests"):
        items = report[field]
        if not isinstance(items, list) or len(items) != report["compaction_count"]:
            raise EvidenceValidationError(f"{field} must contain exactly compaction_count entries")
        for index, item in enumerate(items):
            if field == "generation_ids":
                _string(item, f"{field}[{index}]")
            else:
                _digest(item, f"{field}[{index}]")
    if len(set(report["generation_ids"])) != report["compaction_count"]:
        raise EvidenceValidationError("soak generation ids must be unique")
    _true(report["no_event_loss"], "no_event_loss")
    _true(report["no_event_duplication"], "no_event_duplication")
    _true(report["contiguous_sequences"], "contiguous_sequences")


def validate_evidence_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Validate exact fields, types, digests, and self-hash for one report."""

    detached_input = _bounded_detach_mapping(report)
    raw = canonical_json_bytes(detached_input)
    if len(raw) > MAX_EVIDENCE_JSON_BYTES:
        raise EvidenceValidationError("evidence report exceeds the JSON byte limit")
    detached = json.loads(raw.decode("utf-8"))
    if not isinstance(detached, dict):  # pragma: no cover - mapping input invariant
        raise EvidenceValidationError("evidence report must be an object")
    _check_structure(detached)
    schema = detached.get("schema")
    if schema == OFFLINE_SCENARIO_SCHEMA:
        _validate_scenario_report(detached)
    elif schema == SOAK_REPORT_SCHEMA:
        _validate_soak_report(detached)
    else:
        raise EvidenceValidationError(f"unsupported evidence schema: {schema!r}")
    expected = _self_hash(detached)
    actual = _digest(detached.get("report_sha256"), "report_sha256")
    if actual != expected:
        raise EvidenceValidationError(
            f"report self-hash mismatch: expected {expected}, found {actual}"
        )
    return detached


def _bounded_database_inventory(
    store: SQLiteGenerationStore,
    *,
    session_id: str,
    expected_generation_count: int,
) -> tuple[
    tuple[str, ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
]:
    """Read only the report-bounded session and generation inventory."""

    connection = store._connect_read_only()
    try:
        connection.execute("BEGIN")
        session_rows = connection.execute(
            "SELECT session_id FROM sessions ORDER BY session_id LIMIT 2"
        ).fetchall()
        generation_rows = connection.execute(
            """
            SELECT generation_id, state, parent_generation_id,
                   expected_active_epoch, captured_source_count,
                   captured_source_head_sha256, bundle_sha256,
                   semantic_result_sha256, verification_passed
            FROM generations
            WHERE session_id = ?
            ORDER BY expected_active_epoch, generation_id
            LIMIT ?
            """,
            (session_id, expected_generation_count + 1),
        ).fetchall()
        activation_rows = connection.execute(
            """
            SELECT transitions.generation_id, transitions.from_state,
                   transitions.to_state, transitions.operation_id,
                   transitions.reason_code, transitions.evidence_sha256
            FROM generation_transitions AS transitions
            JOIN generations
              ON generations.generation_id = transitions.generation_id
            WHERE generations.session_id = ?
              AND transitions.to_state = 'active'
            ORDER BY generations.expected_active_epoch, transitions.ordinal
            LIMIT ?
            """,
            (session_id, expected_generation_count + 1),
        ).fetchall()
    finally:
        connection.close()
    return (
        tuple(str(row["session_id"]) for row in session_rows),
        tuple(dict(row) for row in generation_rows),
        tuple(dict(row) for row in activation_rows),
    )


def _generation_lineage_issues(
    generations: Sequence[Mapping[str, Any]],
    *,
    expected_count: int,
) -> list[str]:
    issues: list[str] = []
    if len(generations) != expected_count:
        return ["database generation count does not match report"]
    for index, row in enumerate(generations):
        expected_parent = (
            None if index == 0 else generations[index - 1]["generation_id"]
        )
        expected_state = "active" if index == expected_count - 1 else "superseded"
        if (
            row["parent_generation_id"] != expected_parent
            or row["expected_active_epoch"] != index
        ):
            issues.append("database ordered generation lineage is invalid")
            break
        if row["state"] != expected_state:
            issues.append("database ordered generation states are invalid")
            break
        if row["verification_passed"] != 1:
            issues.append("database generation lacks passed verification")
            break
    return issues


def _scenario_generation_issues(
    report: Mapping[str, Any],
    generations: Sequence[Mapping[str, Any]],
) -> list[str]:
    if len(generations) != 3:
        return []
    issues: list[str] = []
    fields = (
        "ordinal",
        "generation_id",
        "active_epoch",
        "source_count",
        "source_head_sha256",
        "bundle_sha256",
        "semantic_result_digest",
    )
    actual = [
        {
            "ordinal": index + 1,
            "generation_id": row["generation_id"],
            "active_epoch": row["expected_active_epoch"] + 1,
            "source_count": row["captured_source_count"],
            "source_head_sha256": row["captured_source_head_sha256"],
            "bundle_sha256": row["bundle_sha256"],
            "semantic_result_digest": row["semantic_result_sha256"],
        }
        for index, row in enumerate(generations)
    ]
    expected = [
        {field: compaction[field] for field in fields}
        for compaction in report["compactions"]
    ]
    if actual != expected:
        issues.append("database scenario compactions do not match report")
    generation_ids = tuple(str(row["generation_id"]) for row in generations)
    if tuple(row["captured_source_count"] for row in generations) != (1, 2, 3):
        issues.append("database scenario compaction source counts are invalid")
    if report["old_generation_visible_after_crash"] != generation_ids[1]:
        issues.append("database scenario old-generation claim does not match")
    if report["recovered_generation"] != generation_ids[2]:
        issues.append("database scenario recovered-generation claim does not match")
    return issues


def _activation_transition_issues(
    report: Mapping[str, Any],
    generations: Sequence[Mapping[str, Any]],
    activations: Sequence[Mapping[str, Any]],
) -> list[str]:
    expected_count = 3 if report["schema"] == OFFLINE_SCENARIO_SCHEMA else report[
        "compaction_count"
    ]
    if len(generations) != expected_count or len(activations) != expected_count:
        return ["database activation transition count does not match report"]
    for index, (generation, activation) in enumerate(
        zip(generations, activations, strict=True)
    ):
        generation_id = generation["generation_id"]
        # This binds the durable recovery code path. It is not independent
        # attestation that an operating-system process actually crashed.
        operation_suffix = (
            "scenario-recover"
            if report["schema"] == OFFLINE_SCENARIO_SCHEMA and index == 2
            else "activate"
        )
        if (
            activation["generation_id"] != generation_id
            or activation["from_state"] != "committed"
            or activation["to_state"] != "active"
            or activation["operation_id"] != f"{generation_id}:{operation_suffix}"
            or activation["reason_code"] != "active-pointer-cas-won"
            or activation["evidence_sha256"] != generation["bundle_sha256"]
        ):
            return ["database activation transitions do not match report contract"]
    return []


def _soak_generation_issues(
    report: Mapping[str, Any],
    generations: Sequence[Mapping[str, Any]],
    *,
    source_count: int,
) -> list[str]:
    issues: list[str] = []
    event_count = report["event_count"]
    compaction_count = report["compaction_count"]
    if source_count != event_count:
        issues.append("database soak event count does not match report")
    if len(generations) != compaction_count:
        return issues
    expected_source_counts = tuple(
        (ordinal * event_count + compaction_count - 1) // compaction_count
        for ordinal in range(1, compaction_count + 1)
    )
    if (
        tuple(row["captured_source_count"] for row in generations)
        != expected_source_counts
    ):
        issues.append("database soak compaction schedule does not match report")
    if [row["generation_id"] for row in generations] != report["generation_ids"]:
        issues.append("database soak generation ids do not match report")
    if [row["bundle_sha256"] for row in generations] != report["bundle_sha256s"]:
        issues.append("database soak bundle digests do not match report")
    if (
        [row["semantic_result_sha256"] for row in generations]
        != report["semantic_result_digests"]
    ):
        issues.append("database soak semantic digests do not match report")
    return issues


def _database_issues(
    report: Mapping[str, Any],
    *,
    database: Path,
) -> list[str]:
    issues: list[str] = []
    expected_database = report["database"]

    # The byte binding is deliberately checked before SQLite opens the file.
    try:
        actual_sha256, actual_bytes = _hash_file(database)
    except EvidenceValidationError as exc:
        return [str(exc)]
    if actual_sha256 != expected_database["sha256"]:
        return ["database SHA-256 does not match the retained evidence"]
    if actual_bytes != expected_database["byte_length"]:
        return ["database byte length does not match the retained evidence"]
    sidecars = (Path(str(database) + "-wal"), Path(str(database) + "-shm"))
    if any(sidecar.exists() for sidecar in sidecars):
        return ["database WAL/SHM sidecars must be absent before verification"]

    try:
        store = SQLiteGenerationStore(database, require_existing=True)
        session = report["session"]
        session_id = session["session_id"]
        expected_generation_count = (
            3
            if report["schema"] == OFFLINE_SCENARIO_SCHEMA
            else report["compaction_count"]
        )
        session_ids, generations, activations = _bounded_database_inventory(
            store,
            session_id=session_id,
            expected_generation_count=expected_generation_count,
        )
        snapshot = store.snapshot(session_id)
        active = store.read_active(session_id)
        integrity = store.integrity_report()
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        RuntimeError,
        sqlite3.Error,
    ) as exc:
        return [f"database integrity reconciliation failed: {exc}"]

    if session_ids != (session_id,):
        issues.append("database session inventory does not match report")
    if snapshot.source_count != session["source_count"]:
        issues.append("database source count does not match report")
    if snapshot.source_head_sha256 != session["source_head_sha256"]:
        issues.append("database source head does not match report")
    ids = tuple(record.id for record in snapshot.records)
    sequences = tuple(record.sequence for record in snapshot.records)
    no_loss = len(snapshot.records) == session["source_count"]
    no_duplication = len(set(ids)) == len(ids)
    contiguous = sequences == tuple(range(len(sequences)))
    if no_loss is not report["no_event_loss"]:
        issues.append("database event-loss result does not match report")
    if no_duplication is not report["no_event_duplication"]:
        issues.append("database event-duplication result does not match report")
    if contiguous is not report["contiguous_sequences"]:
        issues.append("database sequence-contiguity result does not match report")
    if active is None:
        issues.append("database has no active verified generation")
    else:
        if active.generation_id != session["active_generation_id"]:
            issues.append("database active generation does not match report")
        if active.active_epoch != session["active_epoch"]:
            issues.append("database active epoch does not match report")
        if active.active_epoch != expected_generation_count:
            issues.append(
                "database active epoch does not match ordered generation count"
            )
        if active.semantic_result_digest != session["active_semantic_result_digest"]:
            issues.append("database active semantic digest does not match report")
        if active.covered_source_count != snapshot.source_count or active.tail:
            issues.append("database active generation does not cover the exact source head")
    actual_states = [
        {"generation_id": row["generation_id"], "state": row["state"]} for row in generations
    ]
    if actual_states != session["generation_states"]:
        issues.append("database generation states do not match report")
    issues.extend(
        _generation_lineage_issues(
            generations,
            expected_count=expected_generation_count,
        )
    )
    issues.extend(
        _activation_transition_issues(
            report,
            generations,
            activations,
        )
    )
    if not integrity["passed"]:
        issues.append("database integrity report did not pass")
    if integrity["report_sha256"] != report["integrity_report_sha256"]:
        issues.append("database integrity report digest does not match report")

    if report["schema"] == OFFLINE_SCENARIO_SCHEMA:
        issues.extend(_scenario_generation_issues(report, generations))
        constraints = report["constraints"]
        expected_ids = tuple(f"scenario-message-{index}" for index in range(len(constraints)))
        expected_events = tuple(
            {
                "kind": "MessageEvent",
                "id": f"scenario-message-{index}",
                "timestamp": f"2026-07-27T12:00:{index:02d}+00:00",
                "source": "agent",
                "llm_message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": constraint}],
                },
            }
            for index, constraint in enumerate(constraints)
        )
        retained_events: list[object] = []
        try:
            for record in snapshot.records:
                retained = json.loads(
                    record.content,
                    object_pairs_hook=_reject_pairs,
                    parse_constant=_reject_constant,
                )
                if canonical_json_bytes(retained).decode("utf-8") != record.content:
                    raise EvidenceValidationError("retained scenario event is not canonical JSON")
                retained_events.append(retained)
        except (EvidenceValidationError, json.JSONDecodeError, RecursionError) as exc:
            issues.append(f"database scenario source decoding failed: {exc}")
        else:
            if (
                ids != expected_ids
                or sequences != tuple(range(len(constraints)))
                or tuple(retained_events) != expected_events
            ):
                issues.append("database does not retain the exact ordered scenario constraints")
        final_request = report["final_request"]
        try:
            tokenizer = CanonicalUtf8ByteTokenizer()
            ledger = store.load_request_ledger(
                session_id=session_id,
                request_id=final_request["request_id"],
                tokenizer=tokenizer,
            )
            replay = replay_final_request(ledger, tokenizer=tokenizer).to_dict()
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            issues.append(f"database final-request replay failed: {exc}")
        else:
            if replay != final_request["replay"]:
                issues.append("database final-request replay does not match report")
            receipt = final_request["fake_dispatch_receipt"]
            if (
                receipt["request_id"] != final_request["request_id"]
                or receipt["final_request_sha256"] != replay["final_request_sha256"]
                or receipt["total_tokens"] != replay["total_tokens"]
                or receipt["component_counts"] != replay["component_counts"]
            ):
                issues.append("fake dispatch receipt does not match database replay")
    else:
        issues.extend(
            _soak_generation_issues(
                report,
                generations,
                source_count=snapshot.source_count,
            )
        )
    if any(sidecar.exists() for sidecar in sidecars):
        issues.append("database WAL/SHM sidecars remained after verification")
    try:
        final_sha256, final_bytes = _hash_file(database)
    except EvidenceValidationError as exc:
        issues.append(f"cannot rehash database after verification: {exc}")
    else:
        if final_sha256 != actual_sha256 or final_bytes != actual_bytes:
            issues.append("database changed during SQLite reconciliation")
    return issues


def _verification_report(
    *,
    report: Mapping[str, Any],
    database_supplied: bool,
    database_verified: bool,
    issues: Sequence[str],
) -> dict[str, Any]:
    scope = "json-and-database" if database_supplied else "json-only"
    value: dict[str, Any] = {
        "schema": EVIDENCE_VERIFICATION_SCHEMA,
        "passed": database_supplied and database_verified and not issues,
        "scope": scope,
        "evidence_schema": report["schema"],
        "evidence_report_sha256": report["report_sha256"],
        "report_verified": True,
        "database_supplied": database_supplied,
        "database_verified": database_verified,
        "attestation_claimed": False,
        "semantic_completeness_claimed": False,
        "issues": list(issues),
        "limitations": (
            []
            if database_supplied
            else [
                "database not supplied; SQLite bytes, source history, generations, "
                "and request ledgers were not verified"
            ]
        ),
    }
    value["verification_sha256"] = _sha256(canonical_json_bytes(value))
    return value


def _verify_evidence_mapping(
    loaded: Mapping[str, Any],
    *,
    database: str | Path | None = None,
) -> dict[str, Any]:
    schema = loaded.get("schema")
    if schema not in {OFFLINE_SCENARIO_SCHEMA, SOAK_REPORT_SCHEMA}:
        # Crash-campaign verification remains colocated with its fault model.
        from .crash_campaign import (
            CRASH_CAMPAIGN_REPORT_SCHEMA,
            verify_crash_campaign_report,
        )

        if schema != CRASH_CAMPAIGN_REPORT_SCHEMA:
            raise EvidenceValidationError(f"unsupported evidence schema: {schema!r}")
        result = verify_crash_campaign_report(loaded, database=database)
        if not isinstance(result, Mapping):
            raise EvidenceValidationError("crash-campaign verifier returned a non-object result")
        return dict(result)

    report = validate_evidence_report(loaded)
    if database is None:
        return _verification_report(
            report=report,
            database_supplied=False,
            database_verified=False,
            issues=(),
        )
    database_path = Path(database).expanduser().absolute()
    issues = _database_issues(report, database=database_path)
    return _verification_report(
        report=report,
        database_supplied=True,
        database_verified=not issues,
        issues=issues,
    )


def verify_evidence(
    report_path: str | Path,
    *,
    database: str | Path | None = None,
) -> dict[str, Any]:
    """Verify retained evidence without widening its diagnostic claim scope."""

    loaded = load_evidence_report(report_path)
    return _verify_evidence_mapping(loaded, database=database)


__all__ = [
    "EVIDENCE_VERIFICATION_SCHEMA",
    "LIVE_DEPENDENCY_BLOCKER",
    "MAX_EVIDENCE_JSON_BYTES",
    "MAX_SOAK_COMPACTIONS",
    "MAX_SOAK_EVENTS",
    "OFFLINE_SCENARIO_SCHEMA",
    "SOAK_REPORT_SCHEMA",
    "EvidenceValidationError",
    "canonical_json_bytes",
    "checkpoint_and_bind_database",
    "finalize_evidence_report",
    "load_evidence_report",
    "producer_command",
    "runtime_identity",
    "validate_evidence_report",
    "verify_evidence",
    "write_evidence_report",
]
