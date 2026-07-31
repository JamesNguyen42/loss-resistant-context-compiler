"""Immutable, digest-bound projection of a context-window prototype.

The artifact remains planning-only. It does not serialize a provider request,
bind retrieval evidence, or claim that the provider's final token limit has
been checked. A trusted host must perform that final recount over the exact
immutable request it will send.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .connector import ExactTokenCounterAdapter, decode_connector_request
from .context_window import (
    _MAX_CONTEXT_WINDOW_BYTES,
    _MAX_JSON_DEPTH,
    ContextWindowBudget,
    ContextWindowError,
    ContextWindowPrototype,
    _canonical_bytes,
    _detached,
    _digest,
    _sha256_or_none,
    compose_context_window,
)
from .limits import CompilationLimits, SourceLimits
from .models import CompilationPolicy, SourceRecord

MATERIALIZED_CONTEXT_WINDOW_SCHEMA = "loss-resistant-materialized-context-window-v1"
MATERIALIZED_CONTEXT_RESULT_SCHEMA = "loss-resistant-materialized-context-result-v1"
MATERIALIZED_CONTEXT_RECEIPT_SCHEMA = "loss-resistant-materialized-context-receipt-v1"
MATERIALIZED_CONTEXT_COMPONENTS_SCHEMA = "loss-resistant-materialized-context-components-v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FIELDS = frozenset(
    {
        "schema",
        "allocation_plan_sha256",
        "prototype",
        "materialization_sha256",
    }
)
_RESULT_FIELDS = frozenset(
    {
        "schema",
        "materialized_context",
        "runtime_payload",
        "component_manifest",
        "receipt",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "result_schema",
        "materialized_context_schema",
        "runtime_payload_schema",
        "component_manifest_schema",
        "allocation_plan_sha256",
        "prototype_sha256",
        "materialization_sha256",
        "context_bundle_sha256",
        "rendered_memory_sha256",
        "protected_state_sha256",
        "recent_messages_sha256",
        "current_turn_sha256",
        "recent_tail_omissions_sha256",
        "fixed_input_sha256",
        "runtime_payload_sha256",
        "component_manifest_sha256",
        "accounting_sha256",
        "tokenizer_identity",
        "source_message_count",
        "compiled_prefix_message_count",
        "recent_tail_message_count",
        "current_turn_message_count",
        "retrieval_result_sha256",
        "provider_execution_ready",
        "final_provider_recount_required",
        "refusal_reason",
        "receipt_sha256",
    }
)
_PROMPT_COMPONENT_ORDER = (
    "lrcc_verified_memory",
    "recent_raw_messages",
    "external_untrusted_retrieval",
    "current_user_turn",
)
_MAX_SERIALIZED_RESULT_BYTES = _MAX_CONTEXT_WINDOW_BYTES + 1


@dataclass(frozen=True, slots=True)
class MaterializedContextWindow:
    """A self-consistent LRCC plan bound to an optional host allocation."""

    prototype: ContextWindowPrototype
    allocation_plan_sha256: str | None = None
    materialization_sha256: str = ""
    schema: str = MATERIALIZED_CONTEXT_WINDOW_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != MATERIALIZED_CONTEXT_WINDOW_SCHEMA:
            raise ContextWindowError(f"unsupported materialized context schema: {self.schema!r}")
        if type(self.prototype) is not ContextWindowPrototype:
            raise TypeError("prototype must be an exact ContextWindowPrototype")
        checked = ContextWindowPrototype.from_dict(
            self.prototype.to_dict(),
            expected_prototype_sha256=self.prototype.prototype_sha256,
        )
        allocation = _sha256_or_none(
            self.allocation_plan_sha256,
            label="allocation_plan_sha256",
        )
        object.__setattr__(self, "prototype", checked)
        object.__setattr__(self, "allocation_plan_sha256", allocation)

        supplied = self.materialization_sha256
        if type(supplied) is not str:
            raise TypeError("materialization_sha256 must be an exact string")
        if supplied and _SHA256.fullmatch(supplied) is None:
            raise ContextWindowError(
                "materialization_sha256 must be 64 lowercase hexadecimal characters"
            )
        actual = _digest(self._unsigned_dict())
        if supplied and supplied != actual:
            raise ContextWindowError("materialized context digest mismatch")
        object.__setattr__(self, "materialization_sha256", actual)

    @property
    def tokenizer_identity(self) -> str:
        return self.prototype.tokenizer_identity

    @property
    def context_bundle(self) -> Any:
        return self.prototype.context_bundle

    @property
    def rendered_context(self) -> str:
        return self.prototype.rendered_context

    @property
    def recent_messages(self) -> tuple[SourceRecord, ...]:
        return self.prototype.recent_messages

    @property
    def current_turn(self) -> SourceRecord:
        return self.prototype.current_turn

    @property
    def accounting(self) -> Mapping[str, int]:
        return self.prototype.accounting

    @property
    def prototype_sha256(self) -> str:
        return self.prototype.prototype_sha256

    def _unsigned_dict(self) -> dict[str, Any]:
        if type(self.prototype) is not ContextWindowPrototype:
            raise ContextWindowError("prototype changed to an unsupported value")
        return {
            "schema": self.schema,
            "allocation_plan_sha256": self.allocation_plan_sha256,
            "prototype": self.prototype.to_dict(),
        }

    def _validated_snapshot(self) -> tuple[dict[str, Any], str]:
        claimed = self.materialization_sha256
        if type(claimed) is not str or _SHA256.fullmatch(claimed) is None:
            raise ContextWindowError("materialized context has an invalid digest")
        unsigned = self._unsigned_dict()
        if _digest(unsigned) != claimed:
            raise ContextWindowError("materialized context changed after creation")
        return _detached(unsigned), claimed

    def ensure_integrity(self) -> None:
        self._validated_snapshot()

    def to_dict(self) -> dict[str, Any]:
        value, claimed = self._validated_snapshot()
        value["materialization_sha256"] = claimed
        return value

    def to_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        expected_materialization_sha256: str | None = None,
    ) -> MaterializedContextWindow:
        if type(value) is not dict:
            raise TypeError("materialized context must be an exact object")
        actual = frozenset(value)
        if actual != _FIELDS:
            unknown = sorted(actual - _FIELDS)
            missing = sorted(_FIELDS - actual)
            raise ContextWindowError(
                f"materialized context fields are invalid; unknown={unknown}, missing={missing}"
            )
        raw = _detached(value)
        claimed = raw["materialization_sha256"]
        if type(claimed) is not str or _SHA256.fullmatch(claimed) is None:
            raise ContextWindowError("serialized materialized context requires a valid digest")
        expected = _sha256_or_none(
            expected_materialization_sha256,
            label="expected_materialization_sha256",
        )
        if expected is not None and expected != claimed:
            raise ContextWindowError("materialized context does not match the expected digest")
        unsigned = {key: entry for key, entry in raw.items() if key != "materialization_sha256"}
        if _digest(unsigned) != claimed:
            raise ContextWindowError("materialized context digest mismatch")
        prototype_value = raw["prototype"]
        if type(prototype_value) is not dict:
            raise TypeError("prototype must be an exact object")
        prototype = ContextWindowPrototype.from_dict(prototype_value)
        return cls(
            schema=raw["schema"],
            allocation_plan_sha256=raw["allocation_plan_sha256"],
            prototype=prototype,
            materialization_sha256=claimed,
        )

    @classmethod
    def from_prototype(
        cls,
        value: ContextWindowPrototype,
        *,
        allocation_plan_sha256: str | None = None,
    ) -> MaterializedContextWindow:
        if type(value) is not ContextWindowPrototype:
            raise TypeError("value must be an exact ContextWindowPrototype")
        value.ensure_integrity()
        return cls(
            prototype=value,
            allocation_plan_sha256=allocation_plan_sha256,
        )

    def runtime_payload(
        self,
        *,
        expected_allocation_plan_sha256: str,
    ) -> dict[str, Any]:
        """Return a bounded planning payload for an independent final recount."""

        expected_allocation = _sha256_or_none(
            expected_allocation_plan_sha256,
            label="expected_allocation_plan_sha256",
        )
        if expected_allocation is None:
            raise ContextWindowError(
                "runtime projection requires an independently expected allocation_plan_sha256",
                reason="allocation_digest_required",
            )
        unsigned, claimed = self._validated_snapshot()
        allocation = unsigned["allocation_plan_sha256"]
        if allocation is None:
            raise ContextWindowError(
                "runtime projection requires an independently expected allocation_plan_sha256",
                reason="allocation_digest_required",
            )
        if allocation != expected_allocation:
            raise ContextWindowError(
                "runtime projection does not match the independently expected "
                "allocation_plan_sha256",
                reason="allocation_digest_mismatch",
            )
        prototype = unsigned["prototype"]
        bundle = prototype["context_bundle"]
        payload = {
            "schema": "loss-resistant-runtime-context-plan-v1",
            "allocation_plan_sha256": allocation,
            "prototype_sha256": prototype["prototype_sha256"],
            "materialization_sha256": claimed,
            "tokenizer_identity": prototype["tokenizer_identity"],
            "accounting_scope": prototype["accounting_scope"],
            "fixed_input_sha256": prototype["fixed_input_sha256"],
            "context_bundle_sha256": (None if bundle is None else bundle["bundle_sha256"]),
            "rendered_memory_sha256": prototype["rendered_memory_sha256"],
            "protected_state_sha256": prototype["protected_state_sha256"],
            "recent_messages_sha256": prototype["recent_messages_sha256"],
            "current_turn_sha256": prototype["current_turn_sha256"],
            "recent_tail_omissions_sha256": prototype["recent_tail_omissions_sha256"],
            "retrieval_result_sha256": None,
            "verified_context": prototype["rendered_context"],
            "recent_messages": [
                {
                    "id": message["id"],
                    "sequence": message["sequence"],
                    "role": message["role"],
                    "content": message["content"],
                    "content_sha256": message["content_sha256"],
                    "record_sha256": message["record_sha256"],
                }
                for message in prototype["recent_messages"]
            ],
            "current_turn": {
                "id": prototype["current_turn"]["id"],
                "sequence": prototype["current_turn"]["sequence"],
                "role": prototype["current_turn"]["role"],
                "content": prototype["current_turn"]["content"],
                "content_sha256": prototype["current_turn"]["content_sha256"],
                "record_sha256": prototype["current_turn"]["record_sha256"],
            },
            "recent_tail_omissions": prototype["recent_tail_omissions"],
            "accounting": prototype["accounting"],
            "provider_execution_ready": False,
            "final_provider_recount_required": True,
            "refusal_reason": prototype["refusal_reason"],
        }
        return _detached(payload)

    def runtime_bytes(
        self,
        *,
        expected_allocation_plan_sha256: str,
    ) -> bytes:
        """Return canonical immutable bytes for the planning payload."""

        return _canonical_bytes(
            self.runtime_payload(
                expected_allocation_plan_sha256=expected_allocation_plan_sha256,
            )
        )


def _component_manifest(runtime_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": MATERIALIZED_CONTEXT_COMPONENTS_SCHEMA,
        "prompt_order": list(_PROMPT_COMPONENT_ORDER),
        "lrcc_verified_memory": {
            "runtime_field": "verified_context",
            "classification": "lrcc_verified_semantic_memory",
            "content_sha256": runtime_payload["rendered_memory_sha256"],
            "can_supply_system_or_developer_instructions": False,
        },
        "recent_raw_messages": {
            "runtime_field": "recent_messages",
            "classification": "untrusted_recent_history",
            "ordered_set_sha256": runtime_payload["recent_messages_sha256"],
            "source_roles_preserved": True,
            "provider_role_projection_allowed": False,
            "can_supply_system_or_developer_instructions": False,
        },
        "external_untrusted_retrieval": {
            "runtime_field": None,
            "classification": "untrusted_external_retrieval",
            "content": None,
            "retrieval_result_sha256": None,
            "host_binding_required": True,
            "can_mutate_lrcc_memory": False,
            "can_supply_system_or_developer_instructions": False,
        },
        "current_user_turn": {
            "runtime_field": "current_turn",
            "classification": "current_user_turn",
            "record_sha256": runtime_payload["current_turn_sha256"],
            "required_role": "user",
            "can_supply_system_or_developer_instructions": False,
        },
        "omitted_from_live_tail": {
            "runtime_field": "recent_tail_omissions",
            "classification": "compiled_source_accounting",
            "ordered_set_sha256": runtime_payload["recent_tail_omissions_sha256"],
            "contains_raw_content": False,
        },
        "refusal": {
            "runtime_field": "refusal_reason",
            "value": runtime_payload["refusal_reason"],
        },
    }


def _receipt(
    materialized_context: dict[str, Any],
    runtime_payload: dict[str, Any],
    component_manifest: dict[str, Any],
) -> dict[str, Any]:
    prototype = materialized_context["prototype"]
    accounting = runtime_payload["accounting"]
    unsigned = {
        "schema": MATERIALIZED_CONTEXT_RECEIPT_SCHEMA,
        "result_schema": MATERIALIZED_CONTEXT_RESULT_SCHEMA,
        "materialized_context_schema": materialized_context["schema"],
        "runtime_payload_schema": runtime_payload["schema"],
        "component_manifest_schema": component_manifest["schema"],
        "allocation_plan_sha256": runtime_payload["allocation_plan_sha256"],
        "prototype_sha256": runtime_payload["prototype_sha256"],
        "materialization_sha256": runtime_payload["materialization_sha256"],
        "context_bundle_sha256": runtime_payload["context_bundle_sha256"],
        "rendered_memory_sha256": runtime_payload["rendered_memory_sha256"],
        "protected_state_sha256": runtime_payload["protected_state_sha256"],
        "recent_messages_sha256": runtime_payload["recent_messages_sha256"],
        "current_turn_sha256": runtime_payload["current_turn_sha256"],
        "recent_tail_omissions_sha256": runtime_payload["recent_tail_omissions_sha256"],
        "fixed_input_sha256": runtime_payload["fixed_input_sha256"],
        "runtime_payload_sha256": _digest(runtime_payload),
        "component_manifest_sha256": _digest(component_manifest),
        "accounting_sha256": _digest(accounting),
        "tokenizer_identity": runtime_payload["tokenizer_identity"],
        "source_message_count": accounting["source_message_count"],
        "compiled_prefix_message_count": accounting["compiled_prefix_message_count"],
        "recent_tail_message_count": accounting["recent_tail_message_count"],
        "current_turn_message_count": accounting["current_turn_message_count"],
        "retrieval_result_sha256": runtime_payload["retrieval_result_sha256"],
        "provider_execution_ready": runtime_payload["provider_execution_ready"],
        "final_provider_recount_required": runtime_payload["final_provider_recount_required"],
        "refusal_reason": runtime_payload["refusal_reason"],
    }
    if prototype["prototype_sha256"] != unsigned["prototype_sha256"]:
        raise ContextWindowError("receipt prototype digest mismatch")
    return {**unsigned, "receipt_sha256": _digest(unsigned)}


def materialize_context(
    sources: Iterable[SourceRecord | Mapping[str, Any]],
    *,
    current_turn_id: str,
    budget: ContextWindowBudget,
    token_counter: ExactTokenCounterAdapter,
    allocation_plan_sha256: str,
    fixed_input_sha256: str | None = None,
    policy: CompilationPolicy | None = None,
    source_limits: SourceLimits | None = None,
    compilation_limits: CompilationLimits | None = None,
) -> dict[str, Any]:
    """Build one canonical consumer result without constructing a provider request."""

    materialized = compose_materialized_context_window(
        sources,
        current_turn_id=current_turn_id,
        budget=budget,
        token_counter=token_counter,
        fixed_input_sha256=fixed_input_sha256,
        allocation_plan_sha256=allocation_plan_sha256,
        policy=policy,
        source_limits=source_limits,
        compilation_limits=compilation_limits,
    )
    materialized_value = materialized.to_dict()
    runtime_payload = materialized.runtime_payload(
        expected_allocation_plan_sha256=allocation_plan_sha256,
    )
    component_manifest = _component_manifest(runtime_payload)
    receipt = _receipt(materialized_value, runtime_payload, component_manifest)
    result = {
        "schema": MATERIALIZED_CONTEXT_RESULT_SCHEMA,
        "materialized_context": materialized_value,
        "runtime_payload": runtime_payload,
        "component_manifest": component_manifest,
        "receipt": receipt,
    }
    return verify_materialized_context_result(
        result,
        expected_receipt_sha256=receipt["receipt_sha256"],
        expected_allocation_plan_sha256=allocation_plan_sha256,
    )


def _decode_materialized_context_result(value: bytes) -> dict[str, Any]:
    if type(value) is not bytes:
        raise TypeError("serialized materialized context result must be exact bytes")
    if len(value) > _MAX_SERIALIZED_RESULT_BYTES:
        raise ContextWindowError("serialized materialized context result exceeds the byte limit")
    if not value.endswith(b"\n"):
        raise ContextWindowError(
            "serialized materialized context result must be one canonical JSON line"
        )
    try:
        text = value[:-1].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContextWindowError(
            "serialized materialized context result must be canonical UTF-8 JSON"
        ) from exc
    try:
        decoded = decode_connector_request(
            text,
            max_request_bytes=_MAX_CONTEXT_WINDOW_BYTES,
            max_json_depth=_MAX_JSON_DEPTH,
        )
    except (TypeError, ValueError) as exc:
        raise ContextWindowError(
            "serialized materialized context result must be strict JSON"
        ) from exc
    if _canonical_bytes(decoded) + b"\n" != value:
        raise ContextWindowError("serialized materialized context result is not canonical")
    return decoded


def verify_materialized_context_result(
    value: Mapping[str, Any] | bytes,
    *,
    expected_receipt_sha256: str,
    expected_allocation_plan_sha256: str,
) -> dict[str, Any]:
    """Validate a consumer result against independent receipt and allocation digests."""

    if type(value) is bytes:
        value = _decode_materialized_context_result(value)
    elif type(value) is not dict:
        raise TypeError("materialized context result must be an exact object or exact bytes")
    _canonical_bytes(value)
    actual = frozenset(value)
    if actual != _RESULT_FIELDS:
        unknown = sorted(actual - _RESULT_FIELDS)
        missing = sorted(_RESULT_FIELDS - actual)
        raise ContextWindowError(
            f"materialized context result fields are invalid; unknown={unknown}, missing={missing}"
        )
    raw = _detached(value)
    if raw["schema"] != MATERIALIZED_CONTEXT_RESULT_SCHEMA:
        raise ContextWindowError("materialized context result schema is unsupported")

    receipt_value = raw["receipt"]
    if type(receipt_value) is not dict:
        raise TypeError("materialized context receipt must be an exact object")
    receipt_fields = frozenset(receipt_value)
    if receipt_fields != _RECEIPT_FIELDS:
        unknown = sorted(receipt_fields - _RECEIPT_FIELDS)
        missing = sorted(_RECEIPT_FIELDS - receipt_fields)
        raise ContextWindowError(
            f"materialized context receipt fields are invalid; unknown={unknown}, missing={missing}"
        )
    expected_receipt = _sha256_or_none(
        expected_receipt_sha256,
        label="expected_receipt_sha256",
    )
    if expected_receipt is None:
        raise ContextWindowError("an independently expected receipt digest is required")
    expected_allocation = _sha256_or_none(
        expected_allocation_plan_sha256,
        label="expected_allocation_plan_sha256",
    )
    if expected_allocation is None:
        raise ContextWindowError("an independently expected allocation digest is required")
    claimed_receipt = receipt_value["receipt_sha256"]
    if type(claimed_receipt) is not str or _SHA256.fullmatch(claimed_receipt) is None:
        raise ContextWindowError("materialized context receipt has an invalid digest")
    if claimed_receipt != expected_receipt:
        raise ContextWindowError("materialized context result does not match the expected receipt")
    receipt_unsigned = {
        key: entry for key, entry in receipt_value.items() if key != "receipt_sha256"
    }
    if _digest(receipt_unsigned) != claimed_receipt:
        raise ContextWindowError("materialized context receipt digest mismatch")

    materialized_value = raw["materialized_context"]
    if type(materialized_value) is not dict:
        raise TypeError("materialized_context must be an exact object")
    claimed_materialization = materialized_value.get("materialization_sha256")
    materialized = MaterializedContextWindow.from_dict(
        materialized_value,
        expected_materialization_sha256=claimed_materialization,
    )
    runtime_payload = materialized.runtime_payload(
        expected_allocation_plan_sha256=expected_allocation,
    )
    if _canonical_bytes(raw["runtime_payload"]) != _canonical_bytes(runtime_payload):
        raise ContextWindowError("runtime payload does not match the materialized context")
    component_manifest = _component_manifest(runtime_payload)
    if _canonical_bytes(raw["component_manifest"]) != _canonical_bytes(component_manifest):
        raise ContextWindowError("component manifest does not match the runtime payload")
    receipt = _receipt(materialized.to_dict(), runtime_payload, component_manifest)
    if _canonical_bytes(receipt_value) != _canonical_bytes(receipt):
        raise ContextWindowError("receipt does not match the materialized context result")
    return raw


def compose_materialized_context_window(
    sources: Iterable[SourceRecord | Mapping[str, Any]],
    *,
    current_turn_id: str,
    budget: ContextWindowBudget,
    token_counter: ExactTokenCounterAdapter,
    fixed_input_sha256: str | None = None,
    allocation_plan_sha256: str | None = None,
    policy: CompilationPolicy | None = None,
    source_limits: SourceLimits | None = None,
    compilation_limits: CompilationLimits | None = None,
) -> MaterializedContextWindow:
    """Compose and bind a planning artifact without executing a provider."""

    prototype = compose_context_window(
        sources,
        current_turn_id=current_turn_id,
        budget=budget,
        token_counter=token_counter,
        fixed_input_sha256=fixed_input_sha256,
        policy=policy,
        source_limits=source_limits,
        compilation_limits=compilation_limits,
    )
    return MaterializedContextWindow.from_prototype(
        prototype,
        allocation_plan_sha256=allocation_plan_sha256,
    )


__all__ = [
    "MATERIALIZED_CONTEXT_COMPONENTS_SCHEMA",
    "MATERIALIZED_CONTEXT_RECEIPT_SCHEMA",
    "MATERIALIZED_CONTEXT_RESULT_SCHEMA",
    "MATERIALIZED_CONTEXT_WINDOW_SCHEMA",
    "MaterializedContextWindow",
    "compose_materialized_context_window",
    "materialize_context",
    "verify_materialized_context_result",
]
