"""Seeded SQLite-WAL crash/concurrency campaign and self-hashed report."""

from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import os
import platform
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Final

from context_compiler import (
    IncrementalCompiler,
    LocalAIConnector,
    source_event_to_record,
)

from .event_map import map_host_event
from .faults import (
    ABRUPT_EXIT_CODE,
    TRANSACTION_FAULT_DOMAINS,
    AbruptExitFault,
    DeterministicFaultController,
    InjectedFault,
    TransactionCrashSchedule,
    deterministic_transaction_schedules,
)
from .storage import (
    GenerationStateError,
    SQLiteGenerationStore,
    StaleGenerationError,
    StoreContentionError,
    _mint_independent_verification_receipt,
    _regular_file_identity,
    _reject_link_or_reparse_chain,
)

CRASH_CAMPAIGN_REPORT_SCHEMA: Final = "ctxc-openhands-crash-campaign-0.1"
CRASH_CAMPAIGN_VERIFICATION_SCHEMA: Final = (
    "ctxc-openhands-crash-campaign-verification-0.1"
)
MINIMUM_CAMPAIGN_SCHEDULES: Final = 1_024
MAXIMUM_CAMPAIGN_SCHEDULES: Final = 20_000
CAMPAIGN_WORKER_COUNT: Final = 3
MAX_CAMPAIGN_DATABASE_BYTES: Final = 512 * 1024 * 1024
MAX_CAMPAIGN_REPORT_BYTES: Final = 4 * 1024 * 1024
_MAX_REPORT_DEPTH: Final = 64
_MAX_REPORT_ITEMS: Final = 250_000
_MAX_REPORT_STRING_BYTES: Final = 1_048_576
_CAMPAIGN_SEED_MIN: Final = -(1 << 63)
_CAMPAIGN_SEED_MAX: Final = (1 << 63) - 1
_EXPECTED_WORKER_ERRORS = (
    GenerationStateError,
    StaleGenerationError,
    sqlite3.IntegrityError,
)

_REPORT_FIELDS: Final = frozenset(
    {
        "schema",
        "status",
        "runtime",
        "execution_path_network_capability",
        "network_isolation_enforced",
        "paid_service_use",
        "command",
        "seed",
        "schedule_count",
        "schedule_manifest_sha256",
        "schedule_sha256s",
        "domains",
        "interleaving",
        "outcome_totals",
        "append_outcome",
        "generation_outcome",
        "abrupt_subprocess",
        "database",
        "no_event_loss",
        "no_event_duplication",
        "old_or_new_verified_visibility_only",
        "integrity_report_sha256",
        "semantic_completeness_claimed",
        "report_sha256",
    }
)
_RUNTIME_FIELDS: Final = frozenset(
    {
        "python_implementation",
        "python_version",
        "platform_system",
        "platform_release",
        "platform_machine",
        "sqlite_version",
        "multiprocessing_start_method",
    }
)
_COMMAND_FIELDS: Final = frozenset(
    {
        "invocation_kind",
        "runner",
        "parameters",
        "equivalent_cli_argv",
        "producer_argv",
        "actual_process_argv_recorded",
    }
)
_COMMAND_PARAMETER_FIELDS: Final = frozenset(
    {"database", "schedule_count", "seed"}
)
_INTERLEAVING_FIELDS: Final = frozenset(
    {
        "mode",
        "worker_count",
        "multi_worker_schedule_count",
        "worker_attempt_count",
        "winner_identity_is_not_evidence",
    }
)
_OUTCOME_FIELDS: Final = frozenset(
    {
        "passed",
        "failed",
        "injected_faults_observed",
        "precommit_rollback_schedules",
        "postcommit_durable_schedules",
        "old_or_new_verified_visibility_checks",
        "integrity_checks",
    }
)
_APPEND_OUTCOME_FIELDS: Final = frozenset(
    {
        "expected_source_count",
        "source_count",
        "source_head_sha256",
        "unique_event_id_count",
        "contiguous_sequences",
    }
)
_GENERATION_OUTCOME_FIELDS: Final = frozenset(
    {
        "source_count",
        "source_head_sha256",
        "generation_count",
        "state_counts",
        "active_generation_id",
        "active_generation_verified",
    }
)
_ABRUPT_FIELDS: Final = frozenset(
    {
        "start_method",
        "expected_exit_code",
        "case_count",
        "passed",
        "failed",
        "generation_source_count",
        "generation_source_head_sha256",
        "active_generation_id",
        "cases",
    }
)
_DATABASE_FIELDS: Final = frozenset(
    {"sha256", "byte_length", "maximum_byte_length", "wal_checkpoint"}
)
_CHECKPOINT_FIELDS: Final = frozenset(
    {
        "mode",
        "busy",
        "log_frames",
        "checkpointed_frames",
        "wal_byte_length",
    }
)
_DOMAIN_FIELDS: Final = frozenset(
    {"schedule_count", "fault_points", "fault_point_outcomes"}
)
_APPEND_CASE_FIELDS: Final = frozenset(
    {
        "name",
        "operation",
        "fault_point",
        "visibility_before_retry",
        "final_source_count",
        "integrity_report_sha256",
        "passed",
    }
)
_COMMIT_CASE_FIELDS: Final = frozenset(
    {
        "name",
        "operation",
        "fault_point",
        "state_before_retry",
        "final_state",
        "final_source_count",
        "integrity_report_sha256",
        "passed",
    }
)
_ACTIVATION_CASE_FIELDS: Final = frozenset(
    {
        "name",
        "operation",
        "fault_point",
        "visible_before_retry",
        "expected_old_or_new",
        "final_generation_id",
        "final_source_count",
        "integrity_report_sha256",
        "passed",
    }
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _bounded_campaign_report(
    value: Any,
) -> tuple[dict[str, Any], bytes]:
    """Strictly detach one bounded acyclic JSON object before hashing it."""

    if not isinstance(value, Mapping):
        raise ValueError("campaign report must be an object")

    detached: Any = None
    pending: list[tuple[Any, Any, str | int | None, int]] = [
        (value, None, None, 1)
    ]
    seen_containers: set[int] = set()
    item_count = 0

    def assign(parent: Any, slot: str | int | None, child: Any) -> None:
        nonlocal detached
        if parent is None:
            detached = child
        else:
            parent[slot] = child

    while pending:
        item, parent, slot, depth = pending.pop()
        if depth > _MAX_REPORT_DEPTH:
            raise ValueError("campaign report exceeds the depth limit")
        item_count += 1
        if item_count > _MAX_REPORT_ITEMS:
            raise ValueError("campaign report exceeds the item limit")

        if isinstance(item, str):
            try:
                byte_length = len(item.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise ValueError(
                    "campaign report contains invalid UTF-8 text"
                ) from exc
            if byte_length > _MAX_REPORT_STRING_BYTES:
                raise ValueError("campaign report string exceeds the byte limit")
            assign(parent, slot, item)
            continue
        if item is None or isinstance(item, (bool, int)):
            assign(parent, slot, item)
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("campaign report contains a non-finite number")
            assign(parent, slot, item)
            continue

        if isinstance(item, Mapping):
            identity = id(item)
            if identity in seen_containers:
                raise ValueError(
                    "campaign report contains a cycle or shared container"
                )
            seen_containers.add(identity)
            target: dict[str, Any] = {}
            assign(parent, slot, target)
            entries: list[tuple[str, Any]] = []
            try:
                iterator = iter(item.items())
                while True:
                    try:
                        entry = next(iterator)
                    except StopIteration:
                        break
                    if len(entries) >= _MAX_REPORT_ITEMS:
                        raise ValueError("campaign report exceeds the item limit")
                    try:
                        key, child = entry
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            "campaign report mapping yielded an invalid item"
                        ) from exc
                    if not isinstance(key, str):
                        raise ValueError(
                            "campaign report object keys must be strings"
                        )
                    try:
                        key_bytes = len(key.encode("utf-8"))
                    except UnicodeEncodeError as exc:
                        raise ValueError(
                            "campaign report contains invalid UTF-8 text"
                        ) from exc
                    if key_bytes > _MAX_REPORT_STRING_BYTES:
                        raise ValueError(
                            "campaign report string exceeds the byte limit"
                        )
                    if key in target:
                        raise ValueError(
                            "campaign report mapping yielded a duplicate key"
                        )
                    target[key] = None
                    entries.append((key, child))
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(
                    "campaign report mapping could not be inspected"
                ) from exc
            item_count += len(entries)
            if item_count > _MAX_REPORT_ITEMS:
                raise ValueError("campaign report exceeds the item limit")
            for key, child in reversed(entries):
                pending.append((child, target, key, depth + 1))
            continue

        if isinstance(item, list):
            identity = id(item)
            if identity in seen_containers:
                raise ValueError(
                    "campaign report contains a cycle or shared container"
                )
            seen_containers.add(identity)
            children: list[Any] = []
            try:
                iterator = iter(item)
                while True:
                    try:
                        child = next(iterator)
                    except StopIteration:
                        break
                    if len(children) >= _MAX_REPORT_ITEMS:
                        raise ValueError("campaign report exceeds the item limit")
                    children.append(child)
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(
                    "campaign report array could not be inspected"
                ) from exc
            target_list: list[Any] = [None] * len(children)
            assign(parent, slot, target_list)
            for index in range(len(children) - 1, -1, -1):
                pending.append(
                    (children[index], target_list, index, depth + 1)
                )
            continue

        raise ValueError("campaign report contains a non-JSON value")

    try:
        raw = _canonical_json(detached).encode("utf-8")
    except (RecursionError, TypeError, UnicodeEncodeError, ValueError) as exc:
        raise ValueError("campaign report is not canonical JSON") from exc
    if len(raw) > MAX_CAMPAIGN_REPORT_BYTES:
        raise ValueError("campaign report exceeds the canonical byte limit")
    if not isinstance(detached, dict):
        raise ValueError("campaign report must detach to an object")
    return detached, raw

def _parse_campaign_cli_argv(argv: Sequence[str]) -> dict[str, Any]:
    """Strictly parse the bounded raw CLI vector retained in evidence."""

    if isinstance(argv, (str, bytes, bytearray)):
        raise ValueError("producer argv must be an array of strings")
    try:
        arguments = list(argv)
    except Exception as exc:
        raise ValueError("producer argv could not be inspected") from exc
    if not arguments or len(arguments) > 64:
        raise ValueError("producer argv must contain 1 to 64 arguments")
    for index, argument in enumerate(arguments):
        _bounded_identity(
            argument,
            label=f"producer argv item {index}",
            maximum_bytes=4096,
        )
    if arguments[0] != "fault-campaign":
        raise ValueError("producer argv must begin with fault-campaign")
    allowed = {
        "--database": "database",
        "--schedules": "schedule_count",
        "--seed": "seed",
        "--output": "output",
    }
    values: dict[str, str] = {}
    index = 1
    while index < len(arguments):
        token = arguments[index]
        if not token.startswith("--"):
            raise ValueError(
                "producer argv contains an unexpected positional argument"
            )
        if "=" in token:
            flag, raw_value = token.split("=", 1)
        else:
            flag = token
            index += 1
            if index >= len(arguments) or arguments[index].startswith("--"):
                raise ValueError(f"producer argv flag {flag} is missing its value")
            raw_value = arguments[index]
        field = allowed.get(flag)
        if field is None:
            raise ValueError(f"producer argv contains unknown flag {flag}")
        if field in values:
            raise ValueError(f"producer argv repeats flag {flag}")
        values[field] = _bounded_identity(
            raw_value,
            label=f"producer argv {flag} value",
            maximum_bytes=4096,
        )
        index += 1
    if "database" not in values:
        raise ValueError("producer argv is missing --database")
    try:
        schedule_count = int(values.get("schedule_count", "1024"))
        seed = int(values.get("seed", str(0xC7C0_5A17)), 0)
    except ValueError as exc:
        raise ValueError("producer argv has an invalid numeric value") from exc
    try:
        database = str(Path(values["database"]).expanduser().absolute())
    except (OSError, RuntimeError) as exc:
        raise ValueError("producer argv database path is invalid") from exc
    return {
        "database": database,
        "schedule_count": schedule_count,
        "seed": seed,
        "output": values.get("output"),
    }

def _bounded_identity(value: str, *, label: str, maximum_bytes: int) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    try:
        byte_length = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} is not valid UTF-8 text") from exc
    if "\x00" in value or byte_length > maximum_bytes:
        raise ValueError(f"{label} exceeds its bounded identity contract")
    return value


def _database_digest(path: Path) -> tuple[str, int]:
    _reject_link_or_reparse_chain(path, label="campaign database")
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise ValueError("campaign database must be a regular non-symbolic file")
    initial_identity = _regular_file_identity(path, label="campaign database")
    before = path.stat()
    if (before.st_dev, before.st_ino) != initial_identity:
        raise ValueError("campaign database identity changed before hashing")
    if before.st_nlink != 1:
        raise ValueError("campaign database must not have hard links")
    byte_length = before.st_size
    if byte_length <= 0 or byte_length > MAX_CAMPAIGN_DATABASE_BYTES:
        raise ValueError(
            "campaign database size must be between 1 and "
            f"{MAX_CAMPAIGN_DATABASE_BYTES} bytes"
        )
    digest = hashlib.sha256()
    observed = 0
    with path.open("rb") as stream:
        opened_before = os.fstat(stream.fileno())
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            observed += len(block)
            if observed > MAX_CAMPAIGN_DATABASE_BYTES:
                raise ValueError("campaign database exceeded its hash bound")
            digest.update(block)
        opened_after = os.fstat(stream.fileno())
    after = path.stat()
    identities = {
        (before.st_dev, before.st_ino),
        (opened_before.st_dev, opened_before.st_ino),
        (opened_after.st_dev, opened_after.st_ino),
        (after.st_dev, after.st_ino),
    }
    if (
        len(identities) != 1
        or observed != byte_length
        or opened_before.st_size != byte_length
        or opened_after.st_size != byte_length
        or after.st_size != byte_length
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
        or opened_before.st_mtime_ns != opened_after.st_mtime_ns
        or opened_before.st_ctime_ns != opened_after.st_ctime_ns
        or any(
            item.st_nlink != 1
            for item in (before, opened_before, opened_after, after)
        )
        or not all(
            stat.S_ISREG(item.st_mode)
            for item in (before, opened_before, opened_after, after)
        )
        or path.is_symlink()
    ):
        raise ValueError("campaign database changed while it was hashed")
    if _regular_file_identity(path, label="campaign database") != initial_identity:
        raise ValueError(
            "campaign database identity changed while it was hashed"
        )
    return digest.hexdigest(), byte_length


def _checkpoint_and_hash_database(path: Path) -> dict[str, Any]:
    _reject_link_or_reparse_chain(path, label="campaign database")
    initial_identity = _regular_file_identity(path, label="campaign database")
    _database_digest(path)
    connection = sqlite3.connect(
        path.as_uri() + "?mode=rw",
        uri=True,
        timeout=30,
        isolation_level=None,
    )
    try:
        row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        connection.close()
    if _regular_file_identity(path, label="campaign database") != initial_identity:
        raise AssertionError("campaign database identity changed during checkpoint")
    if row is None or len(row) != 3:
        raise AssertionError("SQLite returned an invalid WAL checkpoint result")
    busy, log_frames, checkpointed_frames = (int(value) for value in row)
    if busy != 0 or log_frames != checkpointed_frames:
        raise AssertionError("campaign WAL checkpoint did not complete")
    wal_path = Path(f"{path}-wal")
    shm_path = Path(f"{path}-shm")
    if wal_path.exists() or shm_path.exists():
        raise AssertionError(
            "campaign WAL/SHM sidecars remained after TRUNCATE checkpoint"
        )
    sha256, byte_length = _database_digest(path)
    return {
        "sha256": sha256,
        "byte_length": byte_length,
        "maximum_byte_length": MAX_CAMPAIGN_DATABASE_BYTES,
        "wal_checkpoint": {
            "mode": "TRUNCATE",
            "busy": busy,
            "log_frames": log_frames,
            "checkpointed_frames": checkpointed_frames,
            "wal_byte_length": 0,
        },
    }


def _exact_mapping(
    value: Any,
    *,
    fields: frozenset[str],
    label: str,
    issues: list[str],
) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        issues.append(f"{label} must be an object")
        return None
    actual = set(value)
    missing = sorted(fields - actual)
    unknown = sorted(actual - fields)
    if missing:
        issues.append(f"{label} is missing fields: {', '.join(missing)}")
    if unknown:
        issues.append(f"{label} has unknown fields: {', '.join(unknown)}")
    return value


def _runtime_identity() -> dict[str, str]:
    return {
        "python_implementation": _bounded_identity(
            platform.python_implementation(),
            label="python implementation",
            maximum_bytes=64,
        ),
        "python_version": _bounded_identity(
            platform.python_version(),
            label="python version",
            maximum_bytes=64,
        ),
        "platform_system": _bounded_identity(
            platform.system(),
            label="platform system",
            maximum_bytes=64,
        ),
        "platform_release": _bounded_identity(
            platform.release(),
            label="platform release",
            maximum_bytes=256,
        ),
        "platform_machine": _bounded_identity(
            platform.machine() or "unknown",
            label="platform machine",
            maximum_bytes=128,
        ),
        "sqlite_version": _bounded_identity(
            sqlite3.sqlite_version,
            label="SQLite version",
            maximum_bytes=64,
        ),
        "multiprocessing_start_method": "spawn",
    }


def _message(
    *,
    session_id: str,
    event_id: str,
    sequence: int,
) -> dict[str, Any]:
    return {
        "kind": "MessageEvent",
        "id": event_id,
        "timestamp": "2026-07-27T12:00:00+00:00",
        "source": "agent",
        "llm_message": {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Offline crash campaign history; no semantic "
                        f"completeness claim ({session_id}:{sequence})."
                    ),
                }
            ],
        },
    }


def _append(
    store: SQLiteGenerationStore,
    *,
    session_id: str,
    event_id: str,
    sequence: int,
    request_id: str,
) -> bool:
    event = _message(
        session_id=session_id,
        event_id=event_id,
        sequence=sequence,
    )
    mapped = map_host_event(
        event,
        sequence=sequence,
        session_id=session_id,
    )
    record = source_event_to_record(mapped, default_sequence=sequence)
    return store.append_source_event(
        session_id=session_id,
        host_event=event,
        source_record=record,
        request_id=request_id,
    ).accepted


@dataclass(frozen=True, slots=True)
class _GenerationEvidence:
    policy_sha256: str
    tokenizer_identity: str
    checkpoint: Mapping[str, Any]
    bundle: Any
    replay: Mapping[str, Any]


def _generation_evidence(
    store: SQLiteGenerationStore,
    *,
    session_id: str,
) -> _GenerationEvidence:
    connector = LocalAIConnector()
    policy = {
        field.name: getattr(connector.policy, field.name)
        for field in fields(connector.policy)
    }
    snapshot = store.snapshot(session_id)
    checkpoint = IncrementalCompiler(
        session_id=session_id,
        sources=snapshot.records,
    ).checkpoint()
    bundle = connector.compile_memory(
        session_id=session_id,
        checkpoint=checkpoint,
    )
    replay = connector.verify_memory(
        bundle,
        session_id=session_id,
        checkpoint=checkpoint,
    )
    if replay.get("passed") is not True:
        raise AssertionError("campaign generation evidence did not replay")
    tokenizer_identity = (
        connector.token_counter_id
        if connector.token_counter is not None
        else "character-estimate-v1"
    )
    return _GenerationEvidence(
        policy_sha256=hashlib.sha256(
            _canonical_json(policy).encode("utf-8")
        ).hexdigest(),
        tokenizer_identity=tokenizer_identity,
        checkpoint=checkpoint,
        bundle=bundle,
        replay=replay,
    )


def _generation_operation(
    store: SQLiteGenerationStore,
    *,
    domain: str,
    generation_id: str,
    operation_id: str,
    session_id: str,
    evidence: _GenerationEvidence,
) -> None:
    if domain == "generation.prepare":
        store.prepare_generation(
            session_id=session_id,
            generation_id=generation_id,
            operation_id=operation_id,
            policy_sha256=evidence.policy_sha256,
            tokenizer_identity=evidence.tokenizer_identity,
        )
        return
    if domain == "generation.verify":
        verification_receipt = _mint_independent_verification_receipt(
            generation_id=generation_id,
            checkpoint=evidence.checkpoint,
            bundle=evidence.bundle,
            replay_report=evidence.replay,
        )
        store.record_verified(
            generation_id=generation_id,
            operation_id=operation_id,
            checkpoint=evidence.checkpoint,
            bundle=evidence.bundle,
            verification_receipt=verification_receipt,
        )
        return
    if domain == "generation.commit":
        store.commit_generation(
            generation_id=generation_id,
            operation_id=operation_id,
        )
        return
    if domain == "generation.activate":
        store.activate_generation(
            generation_id=generation_id,
            operation_id=operation_id,
        )
        return
    raise ValueError(f"unsupported generation campaign domain: {domain}")


def _prepare_for_domain(
    store: SQLiteGenerationStore,
    *,
    domain: str,
    generation_id: str,
    session_id: str,
    evidence: _GenerationEvidence,
    operation_prefix: str,
) -> None:
    prerequisites = {
        "generation.prepare": (),
        "generation.verify": ("generation.prepare",),
        "generation.commit": (
            "generation.prepare",
            "generation.verify",
        ),
        "generation.activate": (
            "generation.prepare",
            "generation.verify",
            "generation.commit",
        ),
    }
    try:
        required = prerequisites[domain]
    except KeyError as exc:
        raise ValueError(f"unsupported generation campaign domain: {domain}") from exc
    for ordinal, prerequisite in enumerate(required):
        _generation_operation(
            store,
            domain=prerequisite,
            generation_id=generation_id,
            operation_id=f"{operation_prefix}:setup:{ordinal}",
            session_id=session_id,
            evidence=evidence,
        )


def _generation_state(
    store: SQLiteGenerationStore,
    *,
    session_id: str,
    generation_id: str,
) -> str | None:
    for row in store.list_generations(session_id):
        if row["generation_id"] == generation_id:
            return str(row["state"])
    return None


def _retry_contention(operation: Callable[[], Any]) -> Any:
    for attempt in range(3):
        try:
            return operation()
        except StoreContentionError:
            if attempt == 2:
                raise
            time.sleep(0)
    raise AssertionError("unreachable contention retry state")


def _simultaneous_workers(
    executor: ThreadPoolExecutor,
    schedule: TransactionCrashSchedule,
    operation: Callable[[str], Any],
) -> tuple[Any, ...]:
    barrier = threading.Barrier(CAMPAIGN_WORKER_COUNT)

    def invoke(actor: str, yields: int) -> Any:
        barrier.wait(timeout=30)
        for _ in range(yields):
            time.sleep(0)
        return operation(actor)

    futures = [
        executor.submit(invoke, actor, yields)
        for actor, yields in zip(
            schedule.worker_order,
            schedule.worker_yields,
            strict=True,
        )
    ]
    return tuple(future.result(timeout=45) for future in futures)


def _source_count_or_zero(
    store: SQLiteGenerationStore,
    *,
    session_id: str,
) -> int:
    try:
        return store.snapshot(session_id).source_count
    except KeyError:
        return 0


def _run_append_schedule(
    *,
    store: SQLiteGenerationStore,
    faulting_store: SQLiteGenerationStore,
    controller: DeterministicFaultController,
    executor: ThreadPoolExecutor,
    schedule: TransactionCrashSchedule,
    expected_count: int,
) -> None:
    session_id = "campaign-append"
    sequence = expected_count
    event_id = f"campaign-append-{sequence:05d}"
    primary_request = f"campaign-primary-{schedule.index:05d}"
    controller.arm(schedule.fault, schedule_index=schedule.index)
    try:
        _append(
            faulting_store,
            session_id=session_id,
            event_id=event_id,
            sequence=sequence,
            request_id=primary_request,
        )
    except InjectedFault as exc:
        if exc.point != schedule.fault.point or exc.schedule_index != schedule.index:
            raise AssertionError("campaign observed the wrong injected append fault") from exc
    else:
        raise AssertionError("campaign append fault was not reached")

    committed_by_primary = schedule.fault.point == "write.after_commit"
    if _source_count_or_zero(store, session_id=session_id) != (
        expected_count + int(committed_by_primary)
    ):
        raise AssertionError("append fault exposed a partial or missing commit")

    def worker(actor: str) -> bool:
        request_id = (
            primary_request
            if actor == "retry"
            else f"campaign-{actor}-{schedule.index:05d}"
        )
        return bool(
            _retry_contention(
                lambda: _append(
                    store,
                    session_id=session_id,
                    event_id=event_id,
                    sequence=sequence,
                    request_id=request_id,
                )
            )
        )

    _simultaneous_workers(executor, schedule, worker)
    snapshot = store.snapshot(session_id)
    if snapshot.source_count != expected_count + 1:
        raise AssertionError("concurrent append retry lost or duplicated an event")
    if snapshot.records[-1].id != event_id:
        raise AssertionError("concurrent append retry retained the wrong event")


def _run_generation_schedule(
    *,
    store: SQLiteGenerationStore,
    faulting_store: SQLiteGenerationStore,
    controller: DeterministicFaultController,
    executor: ThreadPoolExecutor,
    schedule: TransactionCrashSchedule,
    session_id: str,
    evidence: _GenerationEvidence,
) -> None:
    generation_id = f"campaign-generation-{schedule.index:05d}"
    prefix = f"campaign:{schedule.index:05d}"
    _prepare_for_domain(
        store,
        domain=schedule.domain,
        generation_id=generation_id,
        session_id=session_id,
        evidence=evidence,
        operation_prefix=prefix,
    )
    old_active = store.read_active(session_id)
    controller.arm(schedule.fault, schedule_index=schedule.index)
    try:
        _generation_operation(
            faulting_store,
            domain=schedule.domain,
            generation_id=generation_id,
            operation_id=f"{prefix}:fault",
            session_id=session_id,
            evidence=evidence,
        )
    except InjectedFault as exc:
        if exc.point != schedule.fault.point or exc.schedule_index != schedule.index:
            raise AssertionError(
                "campaign observed the wrong injected generation fault"
            ) from exc
    else:
        raise AssertionError("campaign generation fault was not reached")

    if schedule.domain == "generation.activate":
        visible = store.read_active(session_id)
        if visible is None or old_active is None:
            raise AssertionError("activation campaign lost a verified generation")
        expected_visible = (
            generation_id
            if schedule.fault.point == "write.after_commit"
            else old_active.generation_id
        )
        if visible.generation_id != expected_visible:
            raise AssertionError(
                "activation crash exposed neither the expected old nor new generation"
            )

    def worker(actor: str) -> bool:
        try:
            _retry_contention(
                lambda: _generation_operation(
                    store,
                    domain=schedule.domain,
                    generation_id=generation_id,
                    operation_id=f"{prefix}:{actor}",
                    session_id=session_id,
                    evidence=evidence,
                )
            )
        except _EXPECTED_WORKER_ERRORS:
            return False
        return True

    results = _simultaneous_workers(executor, schedule, worker)
    expected_successes = int(schedule.fault.point != "write.after_commit")
    if sum(bool(result) for result in results) != expected_successes:
        raise AssertionError("generation contenders did not converge exactly once")
    expected_state = {
        "generation.prepare": "prepared",
        "generation.verify": "verified",
        "generation.commit": "committed",
        "generation.activate": "active",
    }[schedule.domain]
    if (
        _generation_state(
            store,
            session_id=session_id,
            generation_id=generation_id,
        )
        != expected_state
    ):
        raise AssertionError("generation transaction converged to the wrong state")
    if schedule.domain != "generation.activate":
        store.roll_back_provisional(
            generation_id=generation_id,
            operation_id=f"{prefix}:cleanup",
        )


def _abrupt_worker(
    database: str,
    *,
    operation: str,
    fault_point: str,
    session_id: str,
    generation_id: str | None = None,
    operation_id: str | None = None,
    event_id: str | None = None,
    request_id: str | None = None,
) -> None:
    hook = AbruptExitFault(fault_point)
    store = SQLiteGenerationStore(
        database,
        require_existing=True,
        busy_timeout_seconds=30,
        fault_hook=hook,
    )
    hook.arm()
    if operation == "append":
        if event_id is None or request_id is None:
            raise ValueError("abrupt append requires event_id and request_id")
        _append(
            store,
            session_id=session_id,
            event_id=event_id,
            sequence=0,
            request_id=request_id,
        )
    elif operation == "commit":
        if generation_id is None or operation_id is None:
            raise ValueError("abrupt commit requires generation and operation ids")
        store.commit_generation(
            generation_id=generation_id,
            operation_id=operation_id,
        )
    elif operation == "activate":
        if generation_id is None or operation_id is None:
            raise ValueError("abrupt activation requires generation and operation ids")
        store.activate_generation(
            generation_id=generation_id,
            operation_id=operation_id,
        )
    else:
        raise ValueError(f"unsupported abrupt worker operation: {operation}")
    raise AssertionError("abrupt worker returned without reaching its fault point")


def _spawn_abrupt_worker(database: Path, **kwargs: Any) -> None:
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_abrupt_worker,
        args=(str(database),),
        kwargs=kwargs,
    )
    process.start()
    process.join(timeout=45)
    if process.is_alive():
        process.terminate()
        process.join(timeout=10)
        raise AssertionError("abrupt crash worker exceeded its bounded timeout")
    if process.exitcode != ABRUPT_EXIT_CODE:
        raise AssertionError(
            "abrupt crash worker did not exit at the instrumented point "
            f"(exitcode={process.exitcode!r})"
        )


def _prepare_verified_candidate(
    store: SQLiteGenerationStore,
    *,
    session_id: str,
    generation_id: str,
    evidence: _GenerationEvidence,
    prefix: str,
) -> None:
    _prepare_for_domain(
        store,
        domain="generation.commit",
        generation_id=generation_id,
        session_id=session_id,
        evidence=evidence,
        operation_prefix=prefix,
    )


def _prepare_committed_candidate(
    store: SQLiteGenerationStore,
    *,
    session_id: str,
    generation_id: str,
    evidence: _GenerationEvidence,
    prefix: str,
) -> None:
    _prepare_for_domain(
        store,
        domain="generation.activate",
        generation_id=generation_id,
        session_id=session_id,
        evidence=evidence,
        operation_prefix=prefix,
    )


def _run_abrupt_subprocess_cases(
    store: SQLiteGenerationStore,
    *,
    database: Path,
) -> tuple[dict[str, Any], ...]:
    cases: list[dict[str, Any]] = []
    for name, point, durable_before_retry in (
        ("append-precommit", "append.after_head_cas", False),
        ("append-postcommit", "write.after_commit", True),
    ):
        session_id = f"abrupt-{name}"
        event_id = f"{session_id}-event"
        request_id = f"{session_id}-request"
        _spawn_abrupt_worker(
            database,
            operation="append",
            fault_point=point,
            session_id=session_id,
            event_id=event_id,
            request_id=request_id,
        )
        reopened = SQLiteGenerationStore(database, require_existing=True, busy_timeout_seconds=30)
        observed = _source_count_or_zero(reopened, session_id=session_id)
        if observed != int(durable_before_retry):
            raise AssertionError("abrupt append exposed a partial transaction")
        _append(
            reopened,
            session_id=session_id,
            event_id=event_id,
            sequence=0,
            request_id=request_id,
        )
        snapshot = reopened.snapshot(session_id)
        if snapshot.source_count != 1 or snapshot.records[0].id != event_id:
            raise AssertionError("abrupt append retry lost or duplicated its event")
        integrity = reopened.integrity_report()
        if integrity["passed"] is not True:
            raise AssertionError("abrupt append recovery failed store integrity")
        cases.append(
            {
                "name": name,
                "operation": "append",
                "fault_point": point,
                "visibility_before_retry": observed,
                "final_source_count": snapshot.source_count,
                "integrity_report_sha256": integrity["report_sha256"],
                "passed": True,
            }
        )

    session_id = "abrupt-generation"
    _append(
        store,
        session_id=session_id,
        event_id="abrupt-generation-source",
        sequence=0,
        request_id="abrupt-generation-source",
    )
    evidence = _generation_evidence(store, session_id=session_id)
    base_id = "abrupt-generation-base"
    _prepare_committed_candidate(
        store,
        session_id=session_id,
        generation_id=base_id,
        evidence=evidence,
        prefix="abrupt:base",
    )
    store.activate_generation(
        generation_id=base_id,
        operation_id="abrupt:base:activate",
    )

    for name, point, expected_before_retry in (
        ("commit-precommit", "commit_state.before_commit", "verified"),
        ("commit-postcommit", "write.after_commit", "committed"),
    ):
        generation_id = f"abrupt-{name}"
        prefix = f"abrupt:{name}"
        _prepare_verified_candidate(
            store,
            session_id=session_id,
            generation_id=generation_id,
            evidence=evidence,
            prefix=prefix,
        )
        _spawn_abrupt_worker(
            database,
            operation="commit",
            fault_point=point,
            session_id=session_id,
            generation_id=generation_id,
            operation_id=f"{prefix}:crash",
        )
        reopened = SQLiteGenerationStore(database, require_existing=True, busy_timeout_seconds=30)
        state = _generation_state(
            reopened,
            session_id=session_id,
            generation_id=generation_id,
        )
        if state != expected_before_retry:
            raise AssertionError("abrupt commit exposed an invalid state")
        if state == "verified":
            reopened.commit_generation(
                generation_id=generation_id,
                operation_id=f"{prefix}:recover",
            )
        reopened.roll_back_provisional(
            generation_id=generation_id,
            operation_id=f"{prefix}:cleanup",
        )
        snapshot = reopened.snapshot(session_id)
        if (
            snapshot.source_count != 1
            or snapshot.records[0].id != "abrupt-generation-source"
        ):
            raise AssertionError("abrupt commit lost or duplicated source history")
        integrity = reopened.integrity_report()
        if integrity["passed"] is not True:
            raise AssertionError("abrupt commit recovery failed store integrity")
        cases.append(
            {
                "name": name,
                "operation": "generation.commit",
                "fault_point": point,
                "state_before_retry": state,
                "final_state": "rolled_back",
                "final_source_count": snapshot.source_count,
                "integrity_report_sha256": integrity["report_sha256"],
                "passed": True,
            }
        )

    for name, point, expect_new_visible in (
        ("activate-precommit", "activate.after_new_active", False),
        ("activate-postcommit", "write.after_commit", True),
    ):
        generation_id = f"abrupt-{name}"
        prefix = f"abrupt:{name}"
        old = store.read_active(session_id)
        if old is None:
            raise AssertionError("abrupt activation setup has no old generation")
        _prepare_committed_candidate(
            store,
            session_id=session_id,
            generation_id=generation_id,
            evidence=evidence,
            prefix=prefix,
        )
        _spawn_abrupt_worker(
            database,
            operation="activate",
            fault_point=point,
            session_id=session_id,
            generation_id=generation_id,
            operation_id=f"{prefix}:crash",
        )
        reopened = SQLiteGenerationStore(database, require_existing=True, busy_timeout_seconds=30)
        visible = reopened.read_active(session_id)
        if visible is None:
            raise AssertionError("abrupt activation exposed no verified generation")
        expected_id = generation_id if expect_new_visible else old.generation_id
        if visible.generation_id != expected_id:
            raise AssertionError("abrupt activation exposed neither old nor new")
        if not expect_new_visible:
            reopened.activate_generation(
                generation_id=generation_id,
                operation_id=f"{prefix}:recover",
            )
        final = reopened.read_active(session_id)
        if final is None or final.generation_id != generation_id:
            raise AssertionError("abrupt activation recovery did not converge")
        snapshot = reopened.snapshot(session_id)
        if (
            snapshot.source_count != 1
            or snapshot.records[0].id != "abrupt-generation-source"
        ):
            raise AssertionError("abrupt activation lost or duplicated source history")
        integrity = reopened.integrity_report()
        if integrity["passed"] is not True:
            raise AssertionError("abrupt activation recovery failed store integrity")
        cases.append(
            {
                "name": name,
                "operation": "generation.activate",
                "fault_point": point,
                "visible_before_retry": visible.generation_id,
                "expected_old_or_new": [old.generation_id, generation_id],
                "final_generation_id": final.generation_id,
                "final_source_count": snapshot.source_count,
                "integrity_report_sha256": integrity["report_sha256"],
                "passed": True,
            }
        )
        store = reopened

    return tuple(cases)


def _domain_report(
    schedules: Sequence[TransactionCrashSchedule],
) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    for domain, points in TRANSACTION_FAULT_DOMAINS:
        selected = [schedule for schedule in schedules if schedule.domain == domain]
        counts = Counter(schedule.fault.point for schedule in selected)
        reports[domain] = {
            "schedule_count": len(selected),
            "fault_points": list(points),
            "fault_point_outcomes": {
                point: counts.get(point, 0) for point in points
            },
        }
    return reports


def run_crash_concurrency_campaign(
    database: str | os.PathLike[str],
    *,
    schedule_count: int = MINIMUM_CAMPAIGN_SCHEDULES,
    seed: int = 0xC7C0_5A17,
    producer_argv: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run the bounded seeded campaign and return a self-hashed report."""

    if isinstance(schedule_count, bool) or not isinstance(schedule_count, int):
        raise TypeError("schedule_count must be an integer")
    if schedule_count < MINIMUM_CAMPAIGN_SCHEDULES:
        raise ValueError(
            f"schedule_count must be at least {MINIMUM_CAMPAIGN_SCHEDULES}"
        )
    if schedule_count > MAXIMUM_CAMPAIGN_SCHEDULES:
        raise ValueError(
            f"schedule_count must be at most {MAXIMUM_CAMPAIGN_SCHEDULES}"
        )
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer")
    if seed < _CAMPAIGN_SEED_MIN or seed > _CAMPAIGN_SEED_MAX:
        raise ValueError("seed must fit a signed 64-bit integer")
    if producer_argv is None:
        captured_argv: list[str] = []
    else:
        if isinstance(producer_argv, (str, bytes, bytearray)):
            raise TypeError("producer_argv must be a sequence of strings or null")
        captured_argv = list(producer_argv)
        if not captured_argv or len(captured_argv) > 64:
            raise ValueError("producer_argv must contain 1 to 64 arguments")
        for index, item in enumerate(captured_argv):
            _bounded_identity(
                item,
                label=f"producer_argv item {index}",
                maximum_bytes=4096,
            )
        if captured_argv[0] != "fault-campaign":
            raise ValueError("producer_argv must begin with fault-campaign")
    path = Path(database).expanduser().absolute()
    if captured_argv:
        parsed_argv = _parse_campaign_cli_argv(captured_argv)
        if (
            parsed_argv["database"] != str(path)
            or parsed_argv["schedule_count"] != schedule_count
            or parsed_argv["seed"] != seed
        ):
            raise ValueError(
                "producer argv parameters do not match the campaign invocation"
            )

    schedules = deterministic_transaction_schedules(
        count=schedule_count,
        seed=seed,
    )
    controller = DeterministicFaultController()
    store = SQLiteGenerationStore(path, require_new=True, busy_timeout_seconds=30)
    faulting_store = SQLiteGenerationStore(
        path,
        require_existing=True,
        busy_timeout_seconds=30,
        fault_hook=controller,
    )

    generation_session = "campaign-generation"
    _append(
        store,
        session_id=generation_session,
        event_id="campaign-generation-source",
        sequence=0,
        request_id="campaign-generation-source",
    )
    evidence = _generation_evidence(store, session_id=generation_session)
    base_generation = "campaign-generation-base"
    _prepare_committed_candidate(
        store,
        session_id=generation_session,
        generation_id=base_generation,
        evidence=evidence,
        prefix="campaign:base",
    )
    store.activate_generation(
        generation_id=base_generation,
        operation_id="campaign:base:activate",
    )

    append_count = 0
    integrity_check_count = 0
    with ThreadPoolExecutor(
        max_workers=CAMPAIGN_WORKER_COUNT,
        thread_name_prefix="ctxc-crash-campaign",
    ) as executor:
        for schedule in schedules:
            if schedule.domain == "append":
                _run_append_schedule(
                    store=store,
                    faulting_store=faulting_store,
                    controller=controller,
                    executor=executor,
                    schedule=schedule,
                    expected_count=append_count,
                )
                append_count += 1
            else:
                _run_generation_schedule(
                    store=store,
                    faulting_store=faulting_store,
                    controller=controller,
                    executor=executor,
                    schedule=schedule,
                    session_id=generation_session,
                    evidence=evidence,
                )
            if schedule.index % 128 == 127:
                integrity = store.integrity_report()
                integrity_check_count += 1
                if integrity["passed"] is not True:
                    raise AssertionError("campaign checkpoint integrity check failed")

    append_snapshot = store.snapshot("campaign-append")
    append_ids = tuple(record.id for record in append_snapshot.records)
    if (
        append_snapshot.source_count != append_count
        or len(set(append_ids)) != append_count
        or tuple(record.sequence for record in append_snapshot.records)
        != tuple(range(append_count))
    ):
        raise AssertionError("campaign final append chain lost or duplicated an event")
    generation_snapshot = store.snapshot(generation_session)
    if generation_snapshot.source_count != 1:
        raise AssertionError("campaign generation source history changed")

    abrupt_cases = _run_abrupt_subprocess_cases(store, database=path)
    final_store = SQLiteGenerationStore(path, require_existing=True, busy_timeout_seconds=30)
    integrity = final_store.integrity_report()
    integrity_check_count += 1
    if integrity["passed"] is not True:
        raise AssertionError("campaign final integrity check failed")

    generations = final_store.list_generations(generation_session)
    state_counts = Counter(str(row["state"]) for row in generations)
    active = final_store.read_active(generation_session)
    if active is None:
        raise AssertionError("campaign lost its active verified generation")
    schedule_payload = [schedule.to_dict() for schedule in schedules]
    abrupt_generation_snapshot = final_store.snapshot("abrupt-generation")
    abrupt_generation_active = final_store.read_active("abrupt-generation")
    if abrupt_generation_active is None:
        raise AssertionError("campaign lost the abrupt-case active generation")
    database_metadata = _checkpoint_and_hash_database(path)
    database_parameter = _bounded_identity(
        str(path), label="campaign database path", maximum_bytes=4096
    )
    domain_report = _domain_report(schedules)
    activation_schedules = domain_report["generation.activate"]["schedule_count"]
    postcommit_schedules = sum(
        schedule.fault.point == "write.after_commit" for schedule in schedules
    )
    report: dict[str, Any] = {
        "schema": CRASH_CAMPAIGN_REPORT_SCHEMA,
        "status": "passed",
        "runtime": _runtime_identity(),
        "execution_path_network_capability": "none",
        "network_isolation_enforced": False,
        "paid_service_use": "none",
        "command": {
            "invocation_kind": "cli" if captured_argv else "library-api",
            "runner": (
                "ctxc_openhands.crash_campaign."
                "run_crash_concurrency_campaign"
            ),
            "parameters": {
                "database": database_parameter,
                "schedule_count": schedule_count,
                "seed": seed,
            },
            "equivalent_cli_argv": [
                "ctxc-openhands",
                "fault-campaign",
                "--database",
                database_parameter,
                "--schedules",
                str(schedule_count),
                "--seed",
                str(seed),
            ],
            "producer_argv": captured_argv,
            "actual_process_argv_recorded": False,
        },
        "seed": seed,
        "schedule_count": schedule_count,
        "schedule_manifest_sha256": _sha256_json(schedule_payload),
        "schedule_sha256s": [
            schedule.schedule_sha256 for schedule in schedules
        ],
        "domains": domain_report,
        "interleaving": {
            "mode": "three-worker-simultaneous-start-barrier",
            "worker_count": CAMPAIGN_WORKER_COUNT,
            "multi_worker_schedule_count": schedule_count,
            "worker_attempt_count": schedule_count * CAMPAIGN_WORKER_COUNT,
            "winner_identity_is_not_evidence": True,
        },
        "outcome_totals": {
            "passed": schedule_count,
            "failed": 0,
            "injected_faults_observed": schedule_count,
            "precommit_rollback_schedules": schedule_count - postcommit_schedules,
            "postcommit_durable_schedules": postcommit_schedules,
            "old_or_new_verified_visibility_checks": activation_schedules,
            "integrity_checks": integrity_check_count,
        },
        "append_outcome": {
            "expected_source_count": append_count,
            "source_count": append_snapshot.source_count,
            "source_head_sha256": append_snapshot.source_head_sha256,
            "unique_event_id_count": len(set(append_ids)),
            "contiguous_sequences": True,
        },
        "generation_outcome": {
            "source_count": generation_snapshot.source_count,
            "source_head_sha256": generation_snapshot.source_head_sha256,
            "generation_count": len(generations),
            "state_counts": dict(sorted(state_counts.items())),
            "active_generation_id": active.generation_id,
            "active_generation_verified": bool(active.semantic_result_digest),
        },
        "abrupt_subprocess": {
            "start_method": "spawn",
            "expected_exit_code": ABRUPT_EXIT_CODE,
            "case_count": len(abrupt_cases),
            "passed": len(abrupt_cases),
            "failed": 0,
            "generation_source_count": abrupt_generation_snapshot.source_count,
            "generation_source_head_sha256": (
                abrupt_generation_snapshot.source_head_sha256
            ),
            "active_generation_id": abrupt_generation_active.generation_id,
            "cases": list(abrupt_cases),
        },
        "database": database_metadata,
        "no_event_loss": True,
        "no_event_duplication": True,
        "old_or_new_verified_visibility_only": True,
        "integrity_report_sha256": integrity["report_sha256"],
        "semantic_completeness_claimed": False,
    }
    report["report_sha256"] = _sha256_json(report)
    report, _report_bytes = _bounded_campaign_report(report)
    verification = verify_crash_campaign_report(report, database=path)
    if verification["passed"] is not True:
        raise AssertionError(
            "generated crash campaign report failed verification: "
            + "; ".join(verification["issues"])
        )
    return report


def _is_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_report_shape(
    report: Mapping[str, Any],
    *,
    issues: list[str],
) -> None:
    _exact_mapping(report, fields=_REPORT_FIELDS, label="report", issues=issues)

    runtime = _exact_mapping(
        report.get("runtime"),
        fields=_RUNTIME_FIELDS,
        label="runtime",
        issues=issues,
    )
    if runtime is not None:
        runtime_bounds = {
            "python_implementation": 64,
            "python_version": 64,
            "platform_system": 64,
            "platform_release": 256,
            "platform_machine": 128,
            "sqlite_version": 64,
            "multiprocessing_start_method": 64,
        }
        for field, maximum in runtime_bounds.items():
            try:
                _bounded_identity(
                    runtime.get(field),
                    label=f"runtime.{field}",
                    maximum_bytes=maximum,
                )
            except (AttributeError, TypeError, ValueError) as exc:
                issues.append(str(exc))
        if runtime.get("multiprocessing_start_method") != "spawn":
            issues.append("runtime multiprocessing start method is not spawn")

    command = _exact_mapping(
        report.get("command"),
        fields=_COMMAND_FIELDS,
        label="command",
        issues=issues,
    )
    parameters = None
    parsed_producer_argv: dict[str, Any] | None = None
    if command is not None:
        parameters = _exact_mapping(
            command.get("parameters"),
            fields=_COMMAND_PARAMETER_FIELDS,
            label="command.parameters",
            issues=issues,
        )
        if command.get("runner") != (
            "ctxc_openhands.crash_campaign.run_crash_concurrency_campaign"
        ):
            issues.append("command runner identity mismatch")
        invocation_kind = command.get("invocation_kind")
        producer_argv = command.get("producer_argv")
        if invocation_kind not in {"library-api", "cli"}:
            issues.append("command invocation provenance is inaccurate")
        if command.get("actual_process_argv_recorded") is not False:
            issues.append("actual process argv is overstated")
        if not isinstance(producer_argv, list) or len(producer_argv) > 64:
            issues.append("producer argv is not a bounded array")
        else:
            for index, item in enumerate(producer_argv):
                try:
                    _bounded_identity(
                        item,
                        label=f"command.producer_argv[{index}]",
                        maximum_bytes=4096,
                    )
                except (TypeError, ValueError) as exc:
                    issues.append(str(exc))
            if invocation_kind == "library-api" and producer_argv:
                issues.append("library invocation must not claim CLI argv")
            if invocation_kind == "cli":
                try:
                    parsed_producer_argv = _parse_campaign_cli_argv(
                        producer_argv
                    )
                except (TypeError, ValueError) as exc:
                    issues.append(str(exc))
    if parameters is not None:
        database_parameter = parameters.get("database")
        try:
            _bounded_identity(
                database_parameter,
                label="command database path",
                maximum_bytes=4096,
            )
        except (TypeError, ValueError) as exc:
            issues.append(str(exc))
        expected_argv = [
            "ctxc-openhands",
            "fault-campaign",
            "--database",
            database_parameter,
            "--schedules",
            str(parameters.get("schedule_count")),
            "--seed",
            str(parameters.get("seed")),
        ]
        if command is not None and command.get("equivalent_cli_argv") != expected_argv:
            issues.append("equivalent CLI argv does not match exact parameters")
        if (
            parameters.get("schedule_count") != report.get("schedule_count")
            or parameters.get("seed") != report.get("seed")
        ):
            issues.append("command parameters do not match report parameters")
        if parsed_producer_argv is not None and (
            parsed_producer_argv["database"] != database_parameter
            or parsed_producer_argv["schedule_count"]
            != parameters.get("schedule_count")
            or parsed_producer_argv["seed"] != parameters.get("seed")
        ):
            issues.append(
                "producer argv parameters do not match exact command parameters"
            )

    domains = report.get("domains")
    expected_domains = dict(TRANSACTION_FAULT_DOMAINS)
    if not isinstance(domains, Mapping):
        issues.append("domains must be an object")
    else:
        actual_domains = set(domains)
        if actual_domains != set(expected_domains):
            issues.append("domains have missing or unknown transaction domains")
        for domain, points in expected_domains.items():
            summary = _exact_mapping(
                domains.get(domain),
                fields=_DOMAIN_FIELDS,
                label=f"domains.{domain}",
                issues=issues,
            )
            if summary is None:
                continue
            if summary.get("fault_points") != list(points):
                issues.append(f"domains.{domain}.fault_points mismatch")
            outcomes = summary.get("fault_point_outcomes")
            if not isinstance(outcomes, Mapping) or set(outcomes) != set(points):
                issues.append(
                    f"domains.{domain}.fault_point_outcomes fields mismatch"
                )

    _exact_mapping(
        report.get("interleaving"),
        fields=_INTERLEAVING_FIELDS,
        label="interleaving",
        issues=issues,
    )
    _exact_mapping(
        report.get("outcome_totals"),
        fields=_OUTCOME_FIELDS,
        label="outcome_totals",
        issues=issues,
    )
    append = _exact_mapping(
        report.get("append_outcome"),
        fields=_APPEND_OUTCOME_FIELDS,
        label="append_outcome",
        issues=issues,
    )
    if append is not None and not _is_digest(append.get("source_head_sha256")):
        issues.append("append source head digest is invalid")
    generation = _exact_mapping(
        report.get("generation_outcome"),
        fields=_GENERATION_OUTCOME_FIELDS,
        label="generation_outcome",
        issues=issues,
    )
    if generation is not None:
        if not _is_digest(generation.get("source_head_sha256")):
            issues.append("generation source head digest is invalid")
        states = generation.get("state_counts")
        if not isinstance(states, Mapping) or set(states) != {
            "active",
            "rolled_back",
            "superseded",
        }:
            issues.append("generation state-count fields mismatch")

    abrupt = _exact_mapping(
        report.get("abrupt_subprocess"),
        fields=_ABRUPT_FIELDS,
        label="abrupt_subprocess",
        issues=issues,
    )
    expected_cases = (
        ("append-precommit", "append", _APPEND_CASE_FIELDS),
        ("append-postcommit", "append", _APPEND_CASE_FIELDS),
        ("commit-precommit", "generation.commit", _COMMIT_CASE_FIELDS),
        ("commit-postcommit", "generation.commit", _COMMIT_CASE_FIELDS),
        (
            "activate-precommit",
            "generation.activate",
            _ACTIVATION_CASE_FIELDS,
        ),
        (
            "activate-postcommit",
            "generation.activate",
            _ACTIVATION_CASE_FIELDS,
        ),
    )
    if abrupt is not None:
        if not _is_digest(abrupt.get("generation_source_head_sha256")):
            issues.append("abrupt generation source head digest is invalid")
        cases = abrupt.get("cases")
        if not isinstance(cases, list) or len(cases) != len(expected_cases):
            issues.append("abrupt subprocess case list is incomplete")
        else:
            for index, (expected_name, expected_operation, fields_) in enumerate(
                expected_cases
            ):
                case = _exact_mapping(
                    cases[index],
                    fields=fields_,
                    label=f"abrupt_subprocess.cases[{index}]",
                    issues=issues,
                )
                if case is None:
                    continue
                if (
                    case.get("name") != expected_name
                    or case.get("operation") != expected_operation
                    or case.get("passed") is not True
                    or case.get("final_source_count") != 1
                ):
                    issues.append(f"abrupt subprocess case {index} summary mismatch")
                if not _is_digest(case.get("integrity_report_sha256")):
                    issues.append(
                        f"abrupt subprocess case {index} integrity digest is invalid"
                    )

    database = _exact_mapping(
        report.get("database"),
        fields=_DATABASE_FIELDS,
        label="database",
        issues=issues,
    )
    if database is not None:
        if not _is_digest(database.get("sha256")):
            issues.append("database SHA-256 is invalid")
        byte_length = database.get("byte_length")
        if (
            isinstance(byte_length, bool)
            or not isinstance(byte_length, int)
            or byte_length < 1
            or byte_length > MAX_CAMPAIGN_DATABASE_BYTES
        ):
            issues.append("database byte length is outside the bound")
        if database.get("maximum_byte_length") != MAX_CAMPAIGN_DATABASE_BYTES:
            issues.append("database maximum byte length mismatch")
        checkpoint = _exact_mapping(
            database.get("wal_checkpoint"),
            fields=_CHECKPOINT_FIELDS,
            label="database.wal_checkpoint",
            issues=issues,
        )
        if checkpoint is not None and (
            checkpoint.get("mode") != "TRUNCATE"
            or checkpoint.get("busy") != 0
            or checkpoint.get("log_frames")
            != checkpoint.get("checkpointed_frames")
            or checkpoint.get("wal_byte_length") != 0
        ):
            issues.append("database WAL checkpoint summary is invalid")

    if report.get("execution_path_network_capability") != "none":
        issues.append("execution-path network capability is invalid")
    if report.get("network_isolation_enforced") is not False:
        issues.append("network isolation is overstated")
    if report.get("paid_service_use") != "none":
        issues.append("paid service use is not none")
    for digest_field in (
        "schedule_manifest_sha256",
        "integrity_report_sha256",
        "report_sha256",
    ):
        if not _is_digest(report.get(digest_field)):
            issues.append(f"{digest_field} is invalid")


def _reconcile_campaign_database(
    report: Mapping[str, Any],
    database: str | os.PathLike[str],
    *,
    issues: list[str],
) -> tuple[str | None, int | None]:
    path = Path(database).expanduser().absolute()
    try:
        sha256, byte_length = _database_digest(path)
    except (OSError, TypeError, ValueError) as exc:
        issues.append(f"database hash failed before open: {exc}")
        return None, None
    metadata = report.get("database")
    if not isinstance(metadata, Mapping):
        issues.append("database metadata is missing")
        return sha256, byte_length
    if (
        metadata.get("sha256") != sha256
        or metadata.get("byte_length") != byte_length
    ):
        issues.append("database digest or byte length mismatch")
        return sha256, byte_length
    wal_path = Path(f"{path}-wal")
    shm_path = Path(f"{path}-shm")
    if wal_path.exists() or shm_path.exists():
        issues.append("database WAL/SHM sidecars must be absent")
        return sha256, byte_length

    try:
        with tempfile.TemporaryDirectory(prefix="ctxc-campaign-verify-") as temporary:
            copy_path = Path(temporary) / "campaign.sqlite3"
            shutil.copyfile(path, copy_path)
            copy_sha256, copy_length = _database_digest(copy_path)
            if copy_sha256 != sha256 or copy_length != byte_length:
                issues.append("database changed while the verification copy was made")
                return sha256, byte_length
            store = SQLiteGenerationStore(copy_path, require_existing=True, busy_timeout_seconds=30)
            integrity = store.integrity_report()
            if (
                integrity["passed"] is not True
                or integrity["report_sha256"]
                != report.get("integrity_report_sha256")
            ):
                issues.append("database integrity report does not match evidence")

            append = report.get("append_outcome")
            append_snapshot = store.snapshot("campaign-append")
            if not isinstance(append, Mapping) or (
                append_snapshot.source_count != append.get("source_count")
                or append_snapshot.source_head_sha256
                != append.get("source_head_sha256")
                or len({record.id for record in append_snapshot.records})
                != append.get("unique_event_id_count")
                or tuple(record.sequence for record in append_snapshot.records)
                != tuple(range(append_snapshot.source_count))
            ):
                issues.append("database append source chain does not match evidence")

            generation = report.get("generation_outcome")
            generation_snapshot = store.snapshot("campaign-generation")
            generations = store.list_generations("campaign-generation")
            active = store.read_active("campaign-generation")
            states = Counter(str(row["state"]) for row in generations)
            if not isinstance(generation, Mapping) or (
                active is None
                or generation_snapshot.source_count != generation.get("source_count")
                or generation_snapshot.source_head_sha256
                != generation.get("source_head_sha256")
                or len(generations) != generation.get("generation_count")
                or dict(sorted(states.items())) != generation.get("state_counts")
                or active.generation_id != generation.get("active_generation_id")
                or not active.semantic_result_digest
            ):
                issues.append("database generation state does not match evidence")

            abrupt = report.get("abrupt_subprocess")
            abrupt_snapshot = store.snapshot("abrupt-generation")
            abrupt_active = store.read_active("abrupt-generation")
            if not isinstance(abrupt, Mapping) or (
                abrupt_active is None
                or abrupt_snapshot.source_count
                != abrupt.get("generation_source_count")
                or abrupt_snapshot.source_head_sha256
                != abrupt.get("generation_source_head_sha256")
                or abrupt_active.generation_id != abrupt.get("active_generation_id")
            ):
                issues.append("database abrupt generation state does not match evidence")
            for name in ("append-precommit", "append-postcommit"):
                snapshot = store.snapshot(f"abrupt-{name}")
                if snapshot.source_count != 1 or len(snapshot.records) != 1:
                    issues.append(f"database abrupt {name} source count mismatch")
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        issues.append(f"database reconciliation failed: {type(exc).__name__}: {exc}")
    try:
        final_sha256, final_byte_length = _database_digest(path)
    except (OSError, TypeError, ValueError) as exc:
        issues.append(f"database hash failed after verification: {exc}")
    else:
        if final_sha256 != sha256 or final_byte_length != byte_length:
            issues.append(
                "database changed while campaign evidence was verified"
            )
    if wal_path.exists() or shm_path.exists():
        issues.append("database WAL/SHM sidecars appeared during verification")
    return sha256, byte_length


def verify_crash_campaign_report(
    report: Any,
    *,
    database: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Verify bounds, seeded schedule identity, outcomes, and the self-hash."""

    try:
        bounded_report, _canonical_report = _bounded_campaign_report(report)
    except Exception as exc:
        message = str(exc)
        try:
            safe_message = (
                message
                if message.startswith("campaign report ")
                and len(message.encode("utf-8")) <= 512
                else "campaign report could not be bounded safely"
            )
        except (UnicodeEncodeError, ValueError):
            safe_message = "campaign report could not be bounded safely"
        verification: dict[str, Any] = {
            "schema": CRASH_CAMPAIGN_VERIFICATION_SCHEMA,
            "scope": (
                "report-and-database" if database is not None else "report-only"
            ),
            "passed": False,
            "issues": [safe_message],
            "report_verified": False,
            "database_supplied": database is not None,
            "database_verified": False,
            "report_sha256": None,
            "actual_report_sha256": None,
            "semantic_completeness_claimed": False,
            "attestation_claimed": False,
            "database_sha256": None,
            "database_byte_length": None,
        }
        verification["verification_sha256"] = _sha256_json(verification)
        return verification

    report = bounded_report
    issues: list[str] = []
    normalized = dict(report)
    claimed_digest = normalized.pop("report_sha256", None)
    actual_digest = _sha256_json(normalized)
    if claimed_digest != actual_digest:
        issues.append("report self-hash mismatch")
    _validate_report_shape(report, issues=issues)
    if report.get("schema") != CRASH_CAMPAIGN_REPORT_SCHEMA:
        issues.append("unsupported report schema")
    if report.get("status") != "passed":
        issues.append("campaign status is not passed")
    if report.get("semantic_completeness_claimed") is not False:
        issues.append("semantic completeness claim boundary widened")

    count = report.get("schedule_count")
    seed = report.get("seed")
    schedules: tuple[TransactionCrashSchedule, ...] = ()
    valid_count = not isinstance(count, bool) and isinstance(count, int)
    if not valid_count or count < MINIMUM_CAMPAIGN_SCHEDULES:
        issues.append("schedule_count is below the required bound")
        valid_count = False
    elif count > MAXIMUM_CAMPAIGN_SCHEDULES:
        issues.append("schedule_count exceeds the evidence-size bound")
        valid_count = False
    elif isinstance(seed, bool) or not isinstance(seed, int):
        issues.append("seed is not an integer")
        valid_count = False
    elif seed < _CAMPAIGN_SEED_MIN or seed > _CAMPAIGN_SEED_MAX:
        issues.append("seed does not fit a signed 64-bit integer")
        valid_count = False
    else:
        try:
            schedules = deterministic_transaction_schedules(
                count=count,
                seed=seed,
            )
        except (TypeError, ValueError) as exc:
            issues.append(f"seeded schedule regeneration failed: {exc}")
    if schedules:
        payload = [schedule.to_dict() for schedule in schedules]
        if report.get("schedule_manifest_sha256") != _sha256_json(payload):
            issues.append("schedule manifest digest mismatch")
        if report.get("schedule_sha256s") != [
            schedule.schedule_sha256 for schedule in schedules
        ]:
            issues.append("schedule digest list mismatch")
        if report.get("domains") != _domain_report(schedules):
            issues.append("transaction domain totals mismatch")
        postcommit_schedules = sum(
            schedule.fault.point == "write.after_commit"
            for schedule in schedules
        )
        activation_schedules = sum(
            schedule.domain == "generation.activate" for schedule in schedules
        )
        append_schedules = sum(
            schedule.domain == "append" for schedule in schedules
        )
        outcomes = report.get("outcome_totals")
        expected_outcomes = {
            "passed": count,
            "failed": 0,
            "injected_faults_observed": count,
            "precommit_rollback_schedules": count - postcommit_schedules,
            "postcommit_durable_schedules": postcommit_schedules,
            "old_or_new_verified_visibility_checks": activation_schedules,
            "integrity_checks": count // 128 + 1,
        }
        if outcomes != expected_outcomes:
            issues.append("campaign detailed outcome totals mismatch")
        append = report.get("append_outcome")
        if not isinstance(append, Mapping) or (
            append.get("expected_source_count") != append_schedules
            or append.get("source_count") != append_schedules
            or append.get("unique_event_id_count") != append_schedules
            or append.get("contiguous_sequences") is not True
        ):
            issues.append("append outcome totals mismatch")
        generation = report.get("generation_outcome")
        generation_schedules = count - append_schedules
        if not isinstance(generation, Mapping) or (
            generation.get("source_count") != 1
            or generation.get("generation_count") != generation_schedules + 1
            or generation.get("state_counts")
            != {
                "active": 1,
                "rolled_back": generation_schedules - activation_schedules,
                "superseded": activation_schedules,
            }
            or generation.get("active_generation_verified") is not True
        ):
            issues.append("generation outcome totals mismatch")

    interleaving = report.get("interleaving")
    if not isinstance(interleaving, Mapping):
        issues.append("interleaving summary is missing")
    elif (
        not valid_count
        or interleaving.get("worker_count") != CAMPAIGN_WORKER_COUNT
        or interleaving.get("multi_worker_schedule_count") != count
        or interleaving.get("worker_attempt_count")
        != count * CAMPAIGN_WORKER_COUNT
    ):
        issues.append("multi-worker interleaving totals mismatch")
    outcomes = report.get("outcome_totals")
    if not isinstance(outcomes, Mapping):
        issues.append("outcome totals are missing")
    elif (
        not valid_count
        or outcomes.get("passed") != count
        or outcomes.get("failed") != 0
        or outcomes.get("injected_faults_observed") != count
    ):
        issues.append("campaign outcome totals mismatch")
    abrupt = report.get("abrupt_subprocess")
    if not isinstance(abrupt, Mapping):
        issues.append("abrupt subprocess summary is missing")
    elif (
        abrupt.get("case_count") != 6
        or abrupt.get("passed") != 6
        or abrupt.get("failed") != 0
        or abrupt.get("expected_exit_code") != ABRUPT_EXIT_CODE
    ):
        issues.append("abrupt subprocess outcomes are incomplete")
    for key in (
        "no_event_loss",
        "no_event_duplication",
        "old_or_new_verified_visibility_only",
    ):
        if report.get(key) is not True:
            issues.append(f"{key} is not true")

    report_verified = not issues
    database_issue_start = len(issues)
    database_sha256 = None
    database_byte_length = None
    if database is None:
        issues.append("database not supplied; JSON-only verification is incomplete")
    else:
        database_sha256, database_byte_length = _reconcile_campaign_database(
            report,
            database,
            issues=issues,
        )
    database_verified = (
        database is not None
        and report_verified
        and len(issues) == database_issue_start
    )
    verification: dict[str, Any] = {
        "schema": CRASH_CAMPAIGN_VERIFICATION_SCHEMA,
        "scope": "report-and-database" if database is not None else "report-only",
        "passed": report_verified and database_verified,
        "issues": issues,
        "report_verified": report_verified,
        "database_supplied": database is not None,
        "database_verified": database_verified,
        "report_sha256": claimed_digest,
        "actual_report_sha256": actual_digest,
        "semantic_completeness_claimed": False,
        "attestation_claimed": False,
        "database_sha256": database_sha256,
        "database_byte_length": database_byte_length,
    }
    verification["verification_sha256"] = _sha256_json(verification)
    return verification


__all__ = [
    "CAMPAIGN_WORKER_COUNT",
    "CRASH_CAMPAIGN_REPORT_SCHEMA",
    "CRASH_CAMPAIGN_VERIFICATION_SCHEMA",
    "MAXIMUM_CAMPAIGN_SCHEDULES",
    "MINIMUM_CAMPAIGN_SCHEDULES",
    "run_crash_concurrency_campaign",
    "verify_crash_campaign_report",
]
