"""Lazy, fail-closed boundary to the optional ``localai-contracts`` package.

The ordinary :mod:`context_compiler` package does not import this module.  This
module, in turn, imports ``localai_contracts`` only when an adapter is created.
The legacy six-operation connector and its JSONL protocol remain separate.
"""

from __future__ import annotations

import hashlib
import importlib
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .connector import LocalAIConnector
from .connector import SourceEvent as PrivateSourceEvent

LOCALAI_CONTRACTS_DISTRIBUTION = "localai-contracts"
LOCALAI_CONTRACTS_VERSION = "0.2.0a1"
LOCALAI_CONTRACTS_PROTOCOL_VERSION = "1.0.0"
LOCALAI_CONTRACTS_WHEEL_SHA256 = (
    "3f1cbc1c1079a552304541caa6b7bfbaae926494b67956e3107767ffc980ee41"
)
LOCALAI_CONTRACTS_SOURCE_COMMIT = "dda116eb6431f6f701425f1dec52bf01d9435cfe"
CONTEXT_COMPILE_OPERATION = "context.compile"
MAX_CONTEXT_SOURCE_EVENTS = 8
MAX_CONTEXT_SPANS = 10_000
_PROJECTION_VERSION = "ctxc-localai-context-bundle-projection-v1"


class LocalAIContractsUnavailableError(RuntimeError):
    """The exact optional contract package is absent or incompatible."""


@dataclass(frozen=True, slots=True)
class AuthenticatedAuthority:
    """An out-of-band authentication decision made by the embedding host.

    Merely setting ``SourceEvent.trust`` or event metadata never constructs this
    value.  The host-owned verifier callback must return it independently.
    """

    issuer: str
    trusted_for_state: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.issuer, str)
            or not self.issuer
            or self.issuer != self.issuer.strip()
        ):
            raise TypeError("authority issuer must be a non-empty trimmed string")
        if len(self.issuer) > 256:
            raise ValueError("authority issuer exceeds 256 characters")
        if not isinstance(self.trusted_for_state, bool):
            raise TypeError("trusted_for_state must be boolean")


AuthorityVerifier = Callable[[Any], AuthenticatedAuthority | None]
ConnectorFactory = Callable[[], LocalAIConnector]


@lru_cache(maxsize=1)
def _load_contracts() -> Any:
    try:
        contracts = importlib.import_module("localai_contracts")
    except ModuleNotFoundError as exc:
        if exc.name == "localai_contracts":
            raise LocalAIContractsUnavailableError(
                "localai-contracts 0.2.0a1 is required for this optional adapter"
            ) from exc
        raise
    if getattr(contracts, "__version__", None) != LOCALAI_CONTRACTS_VERSION:
        raise LocalAIContractsUnavailableError(
            "the optional adapter requires localai-contracts 0.2.0a1 exactly"
        )
    if (
        getattr(contracts, "PROTOCOL_VERSION", None)
        != LOCALAI_CONTRACTS_PROTOCOL_VERSION
    ):
        raise LocalAIContractsUnavailableError(
            "the optional adapter requires localai-contracts protocol 1.0.0"
        )
    required = (
        "ComponentCapabilityManifest",
        "ConnectorRequest",
        "ConnectorResponse",
        "ConnectorServer",
        "ContextBundle",
        "DEFAULT_PARSE_LIMITS",
        "NdjsonConnectorServer",
        "ParseLimits",
        "SourceEvent",
        "SubjectOperationProbe",
        "SubjectOperationPurpose",
        "UnsupportedOperationError",
        "canonical_bytes",
        "parse_json",
    )
    if any(not hasattr(contracts, name) for name in required):
        raise LocalAIContractsUnavailableError(
            "the installed localai-contracts package lacks the required API"
        )
    return contracts


def _component_version() -> str:
    from . import __version__

    return __version__


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_record(value: str) -> dict[str, str]:
    return {"algorithm": "sha256", "value": value}


def _stable_id(kind: str, *parts: object) -> str:
    body = "\x00".join(str(part) for part in parts).encode("utf-8")
    return f"ctxc-{kind}-{_digest(body)}"


class LocalAIContractsAdapter:
    """Execute the one canonical, non-inference ``context.compile`` operation."""

    def __init__(
        self,
        *,
        connector_factory: ConnectorFactory | None = None,
        authority_verifier: AuthorityVerifier | None = None,
        limits: Any | None = None,
    ) -> None:
        contracts = _load_contracts()
        if connector_factory is not None and not callable(connector_factory):
            raise TypeError("connector_factory must be callable")
        if authority_verifier is not None and not callable(authority_verifier):
            raise TypeError("authority_verifier must be callable")
        selected_limits = (
            contracts.DEFAULT_PARSE_LIMITS if limits is None else limits
        )
        if type(selected_limits) is not contracts.ParseLimits:
            raise TypeError("limits must be localai_contracts.ParseLimits")

        self._contracts = contracts
        self._connector_factory = connector_factory or LocalAIConnector
        self._authority_verifier = authority_verifier
        self._limits = selected_limits
        self._request_server = contracts.ConnectorServer(self)
        self._request_lock = threading.RLock()

    @property
    def limits(self) -> Any:
        """Return the immutable parser/frame limits shared by both transports."""

        return self._limits

    def get_manifest(self) -> Any:
        """Return an actual validated ``ComponentCapabilityManifest``."""

        contracts = self._contracts
        manifest = contracts.ComponentCapabilityManifest(
            component_name="loss-resistant-context-compiler",
            component_version=_component_version(),
            supported_operations=[CONTEXT_COMPILE_OPERATION],
            supported_schema_versions={
                name: [LOCALAI_CONTRACTS_PROTOCOL_VERSION]
                for name in (
                    "ComponentCapabilityManifest",
                    "ConnectorRequest",
                    "ConnectorResponse",
                    "ErrorEnvelope",
                    "SourceEvent",
                    "ContextBundle",
                )
            },
            restrictions={"models": [], "runtimes": [], "backends": []},
            operation_capabilities=[
                {
                    "operation": CONTEXT_COMPILE_OPERATION,
                    "evidence": "executed",
                    "limitations": [
                        "Canonical trust is not authentication.",
                        "Only independently authenticated exact source spans may "
                        "enter trusted active memory.",
                        "Accounting covers projected span-content components only.",
                    ],
                    "disabled_reason": None,
                }
            ],
            limitations=[
                {
                    "code": "no_inference",
                    "message": "The adapter performs no model inference.",
                    "operation": CONTEXT_COMPILE_OPERATION,
                },
                {
                    "code": "no_semantic_completeness",
                    "message": "The projection makes no semantic-completeness claim.",
                    "operation": CONTEXT_COMPILE_OPERATION,
                },
                {
                    "code": "private_certificate_not_projected",
                    "message": (
                        "The private CtxC artifact and detector-scoped retention "
                        "certificate remain outside the canonical projection."
                    ),
                    "operation": CONTEXT_COMPILE_OPERATION,
                },
            ],
        )
        manifest.validate()
        return manifest

    def handle(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Execute an already-negotiated canonical operation.

        Callers should use ``ConnectorServer``, :meth:`handle_request`, or
        :meth:`serve_ndjson`; those endpoints enforce handshake-first state.
        """

        if operation != CONTEXT_COMPILE_OPERATION:
            raise self._contracts.UnsupportedOperationError(
                "the canonical operation is unsupported"
            )
        bounded = self._bounded_json(payload, label="context.compile payload")
        if not isinstance(bounded, dict) or set(bounded) != {"source_events"}:
            raise ValueError(
                "context.compile payload must contain only source_events"
            )
        documents = bounded["source_events"]
        if (
            not isinstance(documents, list)
            or not documents
            or len(documents) > MAX_CONTEXT_SOURCE_EVENTS
        ):
            raise ValueError(
                f"context.compile requires 1 to {MAX_CONTEXT_SOURCE_EVENTS} "
                "source events"
            )

        events, private_events, authenticated = self._map_source_events(documents)
        payload_digest = _digest(self._contracts.canonical_bytes(bounded))
        connector = self._connector_factory()
        if not isinstance(connector, LocalAIConnector):
            raise TypeError("connector_factory must return LocalAIConnector")
        rich_bundle = connector.compile_memory(
            session_id=f"localai-contracts-{payload_digest}",
            events=private_events,
        )
        replay = connector.verify_memory(rich_bundle, events=private_events)
        if replay.get("passed") is not True:
            raise ValueError("private CtxC replay verification failed")

        projected = self._project_bundle(
            rich_bundle=rich_bundle,
            events=events,
            authenticated=authenticated,
            connector=connector,
        )
        encoded = projected.canonical_bytes()
        if len(encoded) > self._limits.max_bytes:
            raise self._contracts.ParseLimitError(
                "canonical ContextBundle exceeds the configured frame bound"
            )
        parsed = self._contracts.parse_json(encoded, limits=self._limits)
        checked = self._contracts.ContextBundle.from_dict(parsed)
        return checked.to_dict()

    def handle_request(self, request: Any) -> Any:
        """Accept an actual ``ConnectorRequest`` and return ``ConnectorResponse``."""

        if type(request) is not self._contracts.ConnectorRequest:
            raise TypeError(
                "request must be an actual localai_contracts.ConnectorRequest"
            )
        with self._request_lock:
            response = self._request_server.handle(request)
        if type(response) is not self._contracts.ConnectorResponse:
            raise TypeError("connector server returned an invalid response type")
        return response

    def serve_ndjson(self, reader: Any, writer: Any) -> int:
        """Serve bounded canonical NDJSON until EOF and return records consumed."""

        server = self._contracts.NdjsonConnectorServer(
            self,
            limits=self._limits,
        )
        consumed = 0
        while server.serve_once(reader, writer):
            consumed += 1
        return consumed

    def build_phase0_probe(self, source_events: Sequence[Any]) -> Any:
        """Build the mandatory typed non-inference probe for the wheel runner."""

        if (
            not isinstance(source_events, Sequence)
            or isinstance(source_events, (str, bytes, bytearray))
            or not source_events
            or len(source_events) > MAX_CONTEXT_SOURCE_EVENTS
        ):
            raise ValueError(
                f"probe requires 1 to {MAX_CONTEXT_SOURCE_EVENTS} SourceEvents"
            )
        documents: list[dict[str, Any]] = []
        for event in source_events:
            if type(event) is not self._contracts.SourceEvent:
                raise TypeError(
                    "probe events must be actual localai_contracts.SourceEvent values"
                )
            event.validate()
            documents.append(event.to_dict())
        payload = {"source_events": documents}
        expected = self._contracts.ContextBundle.from_dict(
            self.handle(CONTEXT_COMPILE_OPERATION, payload)
        )
        return self._contracts.SubjectOperationProbe(
            purpose=self._contracts.SubjectOperationPurpose.CONTEXT_BUNDLE_COMPILE,
            operation=CONTEXT_COMPILE_OPERATION,
            payload=payload,
            expected_response=expected,
        )

    def _bounded_json(self, value: Any, *, label: str) -> Any:
        try:
            encoded = self._contracts.canonical_bytes(value)
            if len(encoded) > self._limits.max_bytes:
                raise self._contracts.ParseLimitError(
                    f"{label} exceeds the configured byte bound"
                )
            return self._contracts.parse_json(encoded, limits=self._limits)
        except self._contracts.LocalAIContractsError:
            raise
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} is not bounded canonical JSON") from exc

    def _map_source_events(
        self,
        documents: list[Any],
    ) -> tuple[list[Any], list[PrivateSourceEvent], dict[str, bool]]:
        contracts = self._contracts
        parsed_events: list[Any] = []
        private_events: list[PrivateSourceEvent] = []
        authenticated: dict[str, bool] = {}
        seen_ids: set[str] = set()
        seen_sequences: set[int] = set()

        for document in documents:
            if not isinstance(document, dict):
                raise TypeError("source_events entries must be objects")
            event = contracts.SourceEvent.from_dict(document)
            event.validate()
            if event.to_dict() != document:
                raise ValueError("SourceEvent normalization changed the document")
            if event.source_event_id in seen_ids:
                raise ValueError("source_events contains a duplicate id")
            if event.sequence in seen_sequences:
                raise ValueError("source_events contains a duplicate sequence")
            seen_ids.add(event.source_event_id)
            seen_sequences.add(event.sequence)

            authority: AuthenticatedAuthority | None = None
            if event.trust == "trusted" and self._authority_verifier is not None:
                decision_event = contracts.SourceEvent.from_dict(event.to_dict())
                decision = self._authority_verifier(decision_event)
                if decision is not None and type(decision) is not AuthenticatedAuthority:
                    raise TypeError(
                        "authority_verifier must return AuthenticatedAuthority or None"
                    )
                authority = decision
                if (
                    authority is not None
                    and authority.trusted_for_state
                    and event.role != "tool"
                ):
                    raise ValueError(
                        "trusted_for_state is restricted to authenticated tool events"
                    )

            is_authenticated = authority is not None
            authenticated[event.source_event_id] = is_authenticated
            canonical_document = event.to_dict()
            metadata = {
                "localai_contracts_event": {
                    "schema_version": event.schema_version,
                    "original_role": event.role,
                    "declared_trust": event.trust,
                    "metadata": canonical_document["metadata"],
                    "content_hash": canonical_document["content_hash"],
                    "independently_authenticated": is_authenticated,
                    "authority_issuer": (
                        authority.issuer if authority is not None else None
                    ),
                }
            }
            private_authority = (
                {
                    "authenticated": True,
                    "trusted_for_state": authority.trusted_for_state,
                    "issuer": authority.issuer,
                }
                if authority is not None
                else {"authenticated": False, "trusted_for_state": False}
            )
            private_role = event.role if is_authenticated else "assistant"
            content_digest = event.content_hash
            private_events.append(
                PrivateSourceEvent(
                    id=event.source_event_id,
                    sequence=event.sequence,
                    role=private_role,
                    content=event.content,
                    metadata=metadata,
                    authority=private_authority,
                    provenance={
                        "producer": "localai-contracts-adapter",
                        "schema_version": event.schema_version,
                        "source_event_id": event.source_event_id,
                        "original_role": event.role,
                        "declared_trust": event.trust,
                    },
                    content_sha256=(
                        content_digest["value"]
                        if content_digest["algorithm"] == "sha256"
                        else ""
                    ),
                )
            )
            parsed_events.append(event)

        return parsed_events, private_events, authenticated

    def _project_bundle(
        self,
        *,
        rich_bundle: Any,
        events: list[Any],
        authenticated: dict[str, bool],
        connector: LocalAIConnector,
    ) -> Any:
        events_by_id = {event.source_event_id: event for event in events}
        trusted_spans: list[dict[str, Any]] = []
        retrieved_spans: list[dict[str, Any]] = []
        provenance: list[dict[str, Any]] = []

        for event in sorted(
            events,
            key=lambda item: (item.sequence, item.source_event_id),
        ):
            content_hash = _sha256_text(event.content)
            span_id = _stable_id(
                "source",
                event.source_event_id,
                event.sequence,
                content_hash,
            )
            span = {
                "span_id": span_id,
                "source_event_id": event.source_event_id,
                "start_byte": 0,
                "end_byte": len(event.content.encode("utf-8")),
                "content": event.content,
                "content_hash": _digest_record(content_hash),
                "token_count": None,
            }
            retrieved_spans.append(span)
            provenance.append(
                {
                    "item_id": span_id,
                    "source_event_id": event.source_event_id,
                    "transform": "exact_source_event_projection",
                    "producer": {
                        "name": "loss-resistant-context-compiler",
                        "version": _component_version(),
                    },
                }
            )

        rich_spans = rich_bundle.trusted_memory.get("source_spans")
        if not isinstance(rich_spans, list):
            raise TypeError("private trusted-memory source spans are invalid")
        for raw in sorted(
            rich_spans,
            key=lambda item: (
                item.get("source_id", ""),
                item.get("start", -1),
                item.get("end", -1),
                item.get("quote_sha256", ""),
            ),
        ):
            if not isinstance(raw, dict):
                raise TypeError("private trusted-memory span must be an object")
            source_id = raw.get("source_id")
            source = events_by_id.get(source_id)
            if source is None:
                raise ValueError("private span references an unknown source event")
            start = raw.get("start")
            end = raw.get("end")
            quote = raw.get("quote")
            if (
                isinstance(start, bool)
                or not isinstance(start, int)
                or isinstance(end, bool)
                or not isinstance(end, int)
                or start < 0
                or end < start
                or end > len(source.content)
                or not isinstance(quote, str)
            ):
                raise ValueError("private span has invalid character offsets")
            if source.content[start:end] != quote:
                raise ValueError("private span does not match exact source content")
            quote_hash = _sha256_text(quote)
            if raw.get("quote_sha256") != quote_hash:
                raise ValueError("private span quote digest mismatch")
            start_byte = len(source.content[:start].encode("utf-8"))
            end_byte = len(source.content[:end].encode("utf-8"))
            span_id = _stable_id(
                "active",
                source_id,
                start_byte,
                end_byte,
                quote_hash,
            )
            span = {
                "span_id": span_id,
                "source_event_id": source_id,
                "start_byte": start_byte,
                "end_byte": end_byte,
                "content": quote,
                "content_hash": _digest_record(quote_hash),
                "token_count": None,
            }
            if authenticated.get(source_id) is True:
                trusted_spans.append(span)
            else:
                retrieved_spans.append(span)
            provenance.append(
                {
                    "item_id": span_id,
                    "source_event_id": source_id,
                    "transform": "verified_active_exact_source_span_projection",
                    "producer": {
                        "name": "loss-resistant-context-compiler",
                        "version": _component_version(),
                    },
                }
            )

        if len(trusted_spans) + len(retrieved_spans) > MAX_CONTEXT_SPANS:
            raise ValueError("canonical ContextBundle exceeds the span count bound")

        exact_counter = connector.token_counter
        if exact_counter is None:
            counter = _estimated_tokens
            accounting_kind = "estimated"
            accounting_method = (
                "ctxc_projected_span_contents_estimated_"
                "ceil_unicode_characters_divided_by_4;framing_excluded"
            )
        else:
            counter = exact_counter
            accounting_kind = "exact"
            identity = connector.token_counter_id
            if not isinstance(identity, str) or not identity:
                raise ValueError("exact token counter lacks a stable identity")
            accounting_method = (
                "ctxc_projected_span_contents_exact_"
                f"tokenizer_identity_sha256={_sha256_text(identity)};"
                "framing_excluded"
            )

        for span in trusted_spans + retrieved_spans:
            span["token_count"] = counter(span["content"])
        trusted_tokens = sum(span["token_count"] for span in trusted_spans)
        retrieved_tokens = sum(span["token_count"] for span in retrieved_spans)

        omissions, overflow = self._project_omissions_and_overflow(
            rich_bundle,
            exact_accounting=exact_counter is not None,
        )
        policy_digest = rich_bundle.bindings.get("compiler_policy_sha256")
        if not isinstance(policy_digest, str) or len(policy_digest) != 64:
            raise ValueError("private compiler policy identity is invalid")
        private_source_digest = rich_bundle.bindings.get("source_digest")
        if not isinstance(private_source_digest, str) or len(private_source_digest) != 64:
            raise ValueError("private source-set identity is invalid")
        canonical_source_documents = [
            event.to_dict()
            for event in sorted(
                events,
                key=lambda item: (item.sequence, item.source_event_id),
            )
        ]
        source_digest = _digest(
            self._contracts.canonical_bytes(canonical_source_documents)
        )

        body = {
            "trusted_active_memory": trusted_spans,
            "untrusted_retrieved_spans": retrieved_spans,
            "provenance": sorted(
                provenance,
                key=lambda item: (
                    item["source_event_id"],
                    item["item_id"],
                    item["transform"],
                ),
            ),
            "token_accounting": {
                "kind": accounting_kind,
                "method": accounting_method,
                "trusted_tokens": trusted_tokens,
                "retrieved_tokens": retrieved_tokens,
                "total_tokens": trusted_tokens + retrieved_tokens,
            },
            "omissions": omissions,
            "overflow": overflow,
            "policy_identity": {
                "name": "ctxc-private-compilation-policy",
                "version": "localai-context-bundle-0.1",
                "digest": _digest_record(policy_digest),
            },
            "source_store_identity": {
                "name": "localai-contracts-exact-source-events",
                "version": LOCALAI_CONTRACTS_PROTOCOL_VERSION,
                "digest": _digest_record(source_digest),
            },
        }
        bundle_digest = _digest(
            _PROJECTION_VERSION.encode("utf-8")
            + b"\x00"
            + self._contracts.canonical_bytes(body)
        )
        projected = self._contracts.ContextBundle(
            bundle_id=f"ctxc-context-{bundle_digest}",
            **body,
        )
        projected.validate()
        return projected

    @staticmethod
    def _project_omissions_and_overflow(
        rich_bundle: Any,
        *,
        exact_accounting: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        raw_entries = rich_bundle.trusted_memory.get(
            "omitted_or_overflowed_protected_items"
        )
        if not isinstance(raw_entries, list):
            raise TypeError("private protected omission ledger is invalid")
        omissions_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        overflow_values: set[int] = set()
        for entry in raw_entries:
            if not isinstance(entry, dict):
                raise TypeError("private protected omission entry is invalid")
            reason = entry.get("reason")
            item = entry.get("item")
            if not isinstance(item, dict):
                raise TypeError("private omitted item is invalid")
            provenance = item.get("provenance")
            if not isinstance(provenance, list) or not provenance:
                raise ValueError("private omitted item lacks exact provenance")
            source_ids = sorted(
                {
                    span.get("source_id")
                    for span in provenance
                    if isinstance(span, dict)
                    and isinstance(span.get("source_id"), str)
                    and span.get("source_id")
                }
            )
            if not source_ids:
                raise ValueError("private omitted item has no valid source event")
            if reason == "omitted":
                for source_id in source_ids:
                    key = (source_id, "private_protected_item_not_selected")
                    omissions_by_key[key] = {
                        "source_event_id": source_id,
                        "reason": key[1],
                        "estimated_tokens": None,
                    }
            elif reason == "protected_budget_overflow":
                value = entry.get("overflow_tokens")
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise ValueError("private protected overflow is invalid")
                overflow_values.add(value)
            else:
                raise ValueError("private protected omission reason is unsupported")

        if len(overflow_values) > 1:
            raise ValueError("private protected overflow totals disagree")
        if overflow_values:
            amount = next(iter(overflow_values))
            overflow = {
                "occurred": True,
                "dropped_items": 0,
                "dropped_tokens": amount if exact_accounting else None,
                "reason": "private_protected_budget_overflow_retained",
            }
        else:
            overflow = {
                "occurred": False,
                "dropped_items": 0,
                "dropped_tokens": 0,
                "reason": None,
            }
        omissions = [
            omissions_by_key[key] for key in sorted(omissions_by_key)
        ]
        return omissions, overflow


def _estimated_tokens(text: str) -> int:
    if not isinstance(text, str):
        raise TypeError("projected span content must be a string")
    return 0 if not text else max(1, (len(text) + 3) // 4)


__all__ = [
    "AuthenticatedAuthority",
    "AuthorityVerifier",
    "CONTEXT_COMPILE_OPERATION",
    "LOCALAI_CONTRACTS_DISTRIBUTION",
    "LOCALAI_CONTRACTS_PROTOCOL_VERSION",
    "LOCALAI_CONTRACTS_SOURCE_COMMIT",
    "LOCALAI_CONTRACTS_VERSION",
    "LOCALAI_CONTRACTS_WHEEL_SHA256",
    "LocalAIContractsAdapter",
    "LocalAIContractsUnavailableError",
    "MAX_CONTEXT_SOURCE_EVENTS",
]
