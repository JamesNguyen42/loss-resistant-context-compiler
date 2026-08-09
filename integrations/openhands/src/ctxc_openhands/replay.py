"""Deterministic replay of an immutable final-request ledger."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .request_ledger import (
    FinalRequestLedger,
    RequestLedgerLimits,
    _analyze_ledger,
)


@dataclass(frozen=True, slots=True)
class RequestReplayReport:
    """Structured exact-replay result.

    Digest and count fields are recomputed values, never copied claims.  A
    failed replay remains failed and retains all issues found in that pass.
    """

    passed: bool
    issues: tuple[str, ...]
    component_counts: tuple[tuple[str, int], ...]
    total_tokens: int | None
    tokenizer_identity: str | None
    tokenizer_vector_sha256: str | None
    tool_schema_sha256: str | None
    transport_sha256: str | None
    final_request_sha256: str | None
    ledger_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": list(self.issues),
            "component_counts": dict(self.component_counts),
            "total_tokens": self.total_tokens,
            "tokenizer_identity": self.tokenizer_identity,
            "tokenizer_vector_sha256": self.tokenizer_vector_sha256,
            "tool_schema_sha256": self.tool_schema_sha256,
            "transport_sha256": self.transport_sha256,
            "final_request_sha256": self.final_request_sha256,
            "ledger_sha256": self.ledger_sha256,
        }


def replay_final_request(
    ledger: Mapping[str, Any] | FinalRequestLedger,
    *,
    tokenizer: object,
    transport_parts: Sequence[Mapping[str, str]] | None = None,
    limits: RequestLedgerLimits | None = None,
) -> RequestReplayReport:
    """Replay exact accounting and all request bindings synchronously.

    When ``transport_parts`` is supplied, it represents the immutable parts
    being sent now and is checked against the ledger's counts and digests.
    Otherwise replay reconstructs the final request from the parts in the
    ledger itself.
    """

    resolved_limits = limits or RequestLedgerLimits()
    analysis = _analyze_ledger(
        ledger,
        tokenizer=tokenizer,
        limits=resolved_limits,
        override_parts=transport_parts,
    )
    if transport_parts is not None:
        # Validate both the stored representation and the supplied final
        # transport.  A valid override must not hide malformed stored parts.
        stored = _analyze_ledger(
            ledger,
            tokenizer=tokenizer,
            limits=resolved_limits,
        )
        combined_issues = tuple(dict.fromkeys((*stored.issues, *analysis.issues)))
    else:
        combined_issues = analysis.issues
    return RequestReplayReport(
        passed=not combined_issues,
        issues=combined_issues,
        component_counts=analysis.component_counts,
        total_tokens=analysis.total_tokens,
        tokenizer_identity=analysis.tokenizer_identity,
        tokenizer_vector_sha256=analysis.tokenizer_vector_sha256,
        tool_schema_sha256=analysis.tool_schema_sha256,
        transport_sha256=analysis.transport_sha256,
        final_request_sha256=analysis.final_request_sha256,
        ledger_sha256=analysis.ledger_sha256,
    )
