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

from .connector import ExactTokenCounterAdapter
from .context_window import (
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
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FIELDS = frozenset(
    {
        "schema",
        "allocation_plan_sha256",
        "prototype",
        "materialization_sha256",
    }
)


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
    "MATERIALIZED_CONTEXT_WINDOW_SCHEMA",
    "MaterializedContextWindow",
    "compose_materialized_context_window",
]
