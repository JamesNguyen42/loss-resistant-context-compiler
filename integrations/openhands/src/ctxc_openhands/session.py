"""Fail-closed OpenHands event ingestion and CtxC generation coordination.

This module deliberately has no OpenHands import.  Host objects cross the
boundary through :func:`ctxc_openhands.event_map.map_host_event`, which accepts
only the closed event inventory for the exact supported SDK revision.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from typing import Any

from context_compiler import (
    CompilationPolicy,
    IncrementalCompiler,
    LocalAIConnector,
    SourceRecord,
    source_event_to_record,
)

from .authority import AuthorityPolicy
from .deterministic import deterministic_bundle_copy
from .event_map import (
    EventMappingError,
    classify_host_event,
    map_host_event,
    validate_atomic_event_pair,
    validate_host_event_shape,
)
from .semantic import semantic_result_digest
from .storage import (
    ActiveGeneration,
    AppendResult,
    SQLiteGenerationStore,
    StoreIntegrityError,
    _decode_canonical_json,
    _mint_independent_verification_receipt,
)

ConnectorFactory = Callable[[], LocalAIConnector]


class SessionCoordinatorError(RuntimeError):
    """The host session could not advance without weakening an invariant."""


class TransientEventError(SessionCoordinatorError):
    """A known derived/transient host event was refused durable ingestion."""


class AtomicEventError(SessionCoordinatorError):
    """A tool result did not complete exactly one retained tool invocation."""


class VerificationFailedError(SessionCoordinatorError):
    """Independent replay did not verify a candidate generation."""


@dataclass(frozen=True, slots=True)
class CompactionResult:
    """The one verified generation made active by a compaction."""

    generation_id: str
    active_epoch: int
    source_count: int
    source_head_sha256: str
    bundle_sha256: str
    semantic_result_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "active_epoch": self.active_epoch,
            "source_count": self.source_count,
            "source_head_sha256": self.source_head_sha256,
            "bundle_sha256": self.bundle_sha256,
            "semantic_result_digest": self.semantic_result_digest,
        }


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


def _policy_value(policy: CompilationPolicy) -> dict[str, Any]:
    return {field.name: getattr(policy, field.name) for field in fields(policy)}


def _recorded_host_event(record: SourceRecord) -> dict[str, Any]:
    value = _decode_canonical_json(record.content, label="retained host event")
    if not isinstance(value, dict):
        raise StoreIntegrityError("retained host event is not a JSON object")
    return value


class OpenHandsSession:
    """Coordinate one durable OpenHands source history and verified generation.

    The per-instance lock serializes callback delivery.  SQLite still enforces
    source-head and active-generation compare-and-swap across processes.
    """

    def __init__(
        self,
        *,
        session_id: str,
        store: SQLiteGenerationStore,
        connector_factory: ConnectorFactory = LocalAIConnector,
        authority_policy: AuthorityPolicy | None = None,
    ) -> None:
        if not isinstance(session_id, str) or not session_id:
            raise TypeError("session_id must be a non-empty string")
        if len(session_id) > 256:
            raise ValueError("session_id exceeds 256 characters")
        if not isinstance(store, SQLiteGenerationStore):
            raise TypeError("store must be a SQLiteGenerationStore")
        if not callable(connector_factory):
            raise TypeError("connector_factory must be callable")
        if authority_policy is not None and not isinstance(authority_policy, AuthorityPolicy):
            raise TypeError("authority_policy must be an AuthorityPolicy or null")
        self.session_id = session_id
        self.store = store
        self.connector_factory = connector_factory
        self.authority_policy = authority_policy
        self._lock = threading.RLock()

    @staticmethod
    def _validate_result_pair(
        raw: Mapping[str, Any],
        retained_events: tuple[Mapping[str, Any], ...],
        *,
        session_id: str,
    ) -> None:
        tool_call_id = raw.get("tool_call_id")
        actions: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        for retained in retained_events:
            if retained.get("tool_call_id") != tool_call_id:
                continue
            if retained.get("kind") == "ActionEvent":
                actions.append(dict(retained))
            elif retained.get("kind") in {
                "ObservationEvent",
                "UserRejectObservation",
                "AgentErrorEvent",
            }:
                results.append(dict(retained))
        if len(actions) != 1:
            raise AtomicEventError("tool result must complete exactly one retained ActionEvent")
        if results:
            raise AtomicEventError("tool invocation already has a retained result")
        try:
            validate_atomic_event_pair(actions[0], raw, session_id=session_id)
        except EventMappingError as exc:
            raise AtomicEventError(str(exc)) from exc

    def ingest(
        self,
        event: Any,
        *,
        request_id: str,
        authority_receipt: Any | None = None,
        sequence: int | None = None,
    ) -> AppendResult:
        """Validate and append one exact host event.

        Known transient/derived events are explicitly refused instead of being
        silently persisted as source history.  Tool results are accepted only
        after exact linkage to one retained ActionEvent has been verified.
        """

        with self._lock:
            raw_event = validate_host_event_shape(event)
            resolved_sequence, existing = self.store.resolve_append_sequence(
                session_id=self.session_id,
                event_id=raw_event["id"],
            )
            if sequence is None:
                chosen_sequence = resolved_sequence
            else:
                if isinstance(sequence, bool) or not isinstance(sequence, int):
                    raise TypeError("sequence must be an integer or null")
                if sequence < 0:
                    raise ValueError("sequence must be non-negative")
                chosen_sequence = sequence
                if existing and chosen_sequence != resolved_sequence:
                    raise AtomicEventError(
                        "retry sequence differs from the retained immutable event"
                    )

            source_event = map_host_event(
                raw_event,
                sequence=chosen_sequence,
                session_id=self.session_id,
                authority_policy=self.authority_policy,
                authority_receipt=authority_receipt,
            )
            descriptor = classify_host_event(raw_event)
            if descriptor.transient:
                raise TransientEventError(
                    f"{descriptor.kind} is transient/derived and is not durable source history"
                )
            source_record = source_event_to_record(
                source_event,
                default_sequence=chosen_sequence,
            )
            raw = _recorded_host_event(source_record)
            if not existing and raw["kind"] in {
                "ObservationEvent",
                "UserRejectObservation",
                "AgentErrorEvent",
            }:
                retained_events = self.store.retained_atomic_events(
                    session_id=self.session_id,
                    result_event=raw,
                )
                self._validate_result_pair(
                    raw,
                    retained_events,
                    session_id=self.session_id,
                )
            return self.store.append_source_event(
                session_id=self.session_id,
                host_event=raw,
                source_record=source_record,
                request_id=request_id,
                authority_policy=self.authority_policy,
                authority_receipt=authority_receipt,
            )

    def _new_generation_id(self, *, source_count: int, source_head: str) -> str:
        prefix = f"g-{source_count}-{source_head[:20]}"
        used = {str(row["generation_id"]) for row in self.store.list_generations(self.session_id)}
        if prefix not in used:
            return prefix
        attempt = 2
        while f"{prefix}-a{attempt}" in used:
            attempt += 1
        return f"{prefix}-a{attempt}"

    def compact(
        self,
        *,
        generation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> CompactionResult:
        """Compile, independently replay, commit, and atomically activate."""

        with self._lock:
            snapshot = self.store.snapshot(self.session_id)
            compile_connector = self.connector_factory()
            if type(compile_connector) is not LocalAIConnector:
                raise TypeError("connector_factory must return an exact LocalAIConnector")
            policy = compile_connector.policy
            policy_sha = _sha256_json(_policy_value(policy))
            tokenizer_identity = (
                compile_connector.token_counter_id
                if compile_connector.token_counter is not None
                else "character-estimate-v1"
            )
            selected_generation = generation_id or self._new_generation_id(
                source_count=snapshot.source_count,
                source_head=snapshot.source_head_sha256,
            )
            prepared = self.store.prepare_generation(
                session_id=self.session_id,
                generation_id=selected_generation,
                operation_id=f"{selected_generation}:prepare",
                policy_sha256=policy_sha,
                tokenizer_identity=tokenizer_identity,
            )
            incremental = IncrementalCompiler(
                session_id=self.session_id,
                sources=prepared.snapshot.records,
            )
            checkpoint = incremental.checkpoint()
            bundle = compile_connector.compile_memory(
                session_id=self.session_id,
                checkpoint=checkpoint,
                timeout_seconds=timeout_seconds,
            )
            bundle = deterministic_bundle_copy(bundle)
            if bundle.bindings["compiler_policy_sha256"] != policy_sha:
                raise StoreIntegrityError("compiled bundle policy differs from the prepared policy")
            if bundle.bindings["tokenizer_identity"] != tokenizer_identity:
                raise StoreIntegrityError(
                    "compiled bundle tokenizer differs from the prepared tokenizer"
                )

            verify_connector = self.connector_factory()
            if (
                type(verify_connector) is not LocalAIConnector
                or verify_connector is compile_connector
            ):
                raise TypeError(
                    "connector_factory must return distinct exact LocalAIConnector instances"
                )
            replay = verify_connector.verify_memory(
                bundle,
                session_id=self.session_id,
                checkpoint=checkpoint,
            )
            if replay.get("passed") is not True:
                raise VerificationFailedError(
                    "independent replay failed; candidate remains non-active"
                )
            verification_receipt = _mint_independent_verification_receipt(
                generation_id=selected_generation,
                checkpoint=checkpoint,
                bundle=bundle,
                replay_report=replay,
            )
            semantic_sha = self.store.record_verified(
                generation_id=selected_generation,
                operation_id=f"{selected_generation}:verify",
                checkpoint=checkpoint,
                bundle=bundle,
                verification_receipt=verification_receipt,
            )
            expected_semantic = semantic_result_digest(bundle)
            if semantic_sha != expected_semantic:
                raise StoreIntegrityError("stored semantic result digest mismatch")
            self.store.commit_generation(
                generation_id=selected_generation,
                operation_id=f"{selected_generation}:commit",
            )
            active_epoch = self.store.activate_generation(
                generation_id=selected_generation,
                operation_id=f"{selected_generation}:activate",
            )
            active = self.store.read_active(self.session_id)
            if (
                active is None
                or active.generation_id != selected_generation
                or active.active_epoch != active_epoch
                or active.semantic_result_digest != semantic_sha
                or active.bundle.bundle_sha256 != bundle.bundle_sha256
            ):
                raise StoreIntegrityError("active generation does not match the verified candidate")
            return CompactionResult(
                generation_id=selected_generation,
                active_epoch=active_epoch,
                source_count=prepared.snapshot.source_count,
                source_head_sha256=prepared.snapshot.source_head_sha256,
                bundle_sha256=bundle.bundle_sha256,
                semantic_result_digest=semantic_sha,
            )

    def active(self) -> ActiveGeneration | None:
        """Read the only visible verified generation."""

        return self.store.read_active(self.session_id)


__all__ = [
    "AtomicEventError",
    "CompactionResult",
    "OpenHandsSession",
    "SessionCoordinatorError",
    "TransientEventError",
    "VerificationFailedError",
]
