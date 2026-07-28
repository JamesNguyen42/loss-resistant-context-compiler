from __future__ import annotations

import hashlib
import io
from typing import Any

import pytest

from context_compiler import CompilationPolicy, ExactTokenCounterAdapter, LocalAIConnector
from context_compiler.localai_contracts_adapter import (
    CONTEXT_COMPILE_OPERATION,
    LOCALAI_CONTRACTS_PROTOCOL_VERSION,
    LOCALAI_CONTRACTS_SOURCE_COMMIT,
    LOCALAI_CONTRACTS_VERSION,
    LOCALAI_CONTRACTS_WHEEL_SHA256,
    AuthenticatedAuthority,
    LocalAIContractsAdapter,
)

contracts = pytest.importorskip(
    "localai_contracts",
    reason="exact optional localai-contracts wheel is not installed",
)


def _event(
    content: str = "constraint: Keep the database stable.",
    *,
    source_event_id: str = "event-0",
    sequence: int = 0,
    role: str = "user",
    trust: str = "trusted",
    metadata: dict[str, Any] | None = None,
    digest: str | None = None,
) -> Any:
    value = contracts.SourceEvent(
        source_event_id=source_event_id,
        sequence=sequence,
        role=role,
        trust=trust,
        content=content,
        metadata={} if metadata is None else metadata,
        content_hash={
            "algorithm": "sha256",
            "value": digest or hashlib.sha256(content.encode("utf-8")).hexdigest(),
        },
    )
    if digest is None:
        value.validate()
    return value


def _handshake_request(
    adapter: LocalAIContractsAdapter,
    *,
    request_id: str = "handshake-0",
) -> Any:
    return contracts.ConnectorRequest(
        protocol_version=contracts.PROTOCOL_VERSION,
        request_id=request_id,
        operation=contracts.HANDSHAKE_OPERATION,
        payload={
            "manifest": adapter.get_manifest().to_dict(),
            "required_operations": [CONTEXT_COMPILE_OPERATION],
            "allow_predicted": False,
            "targets": {},
        },
        expected_response_schema=None,
    )


def _compile_request(
    event: Any,
    *,
    request_id: str = "compile-0",
) -> Any:
    return contracts.ConnectorRequest(
        protocol_version=contracts.PROTOCOL_VERSION,
        request_id=request_id,
        operation=CONTEXT_COMPILE_OPERATION,
        payload={"source_events": [event.to_dict()]},
        expected_response_schema={
            "name": "ContextBundle",
            "version": "1.0.0",
        },
    )


def _error(response: Any) -> Any:
    assert response.error is not None
    return contracts.ErrorEnvelope.from_dict(response.error)


def test_exact_wheel_identity_and_executed_manifest_are_closed() -> None:
    assert contracts.__version__ == LOCALAI_CONTRACTS_VERSION == "0.2.0a1"
    assert (
        contracts.PROTOCOL_VERSION
        == LOCALAI_CONTRACTS_PROTOCOL_VERSION
        == "1.0.0"
    )
    assert (
        LOCALAI_CONTRACTS_WHEEL_SHA256
        == "3f1cbc1c1079a552304541caa6b7bfbaae926494b67956e3107767ffc980ee41"
    )
    assert (
        LOCALAI_CONTRACTS_SOURCE_COMMIT
        == "dda116eb6431f6f701425f1dec52bf01d9435cfe"
    )

    manifest = LocalAIContractsAdapter().get_manifest()
    assert type(manifest) is contracts.ComponentCapabilityManifest
    manifest.validate()
    assert manifest.supported_operations == [CONTEXT_COMPILE_OPERATION]
    assert manifest.operation_capabilities == [
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
    ]
    assert manifest.restrictions == {"models": [], "runtimes": [], "backends": []}
    assert manifest.supported_schema_versions == {
        name: ["1.0.0"]
        for name in (
            "ComponentCapabilityManifest",
            "ConnectorRequest",
            "ConnectorResponse",
            "ErrorEnvelope",
            "SourceEvent",
            "ContextBundle",
        )
    }


def test_actual_request_requires_handshake_and_returns_actual_response() -> None:
    adapter = LocalAIContractsAdapter()
    event = _event()

    premature = adapter.handle_request(_compile_request(event))
    assert type(premature) is contracts.ConnectorResponse
    assert not premature.ok
    assert _error(premature).code == "handshake_required"

    handshake = adapter.handle_request(_handshake_request(adapter))
    assert type(handshake) is contracts.ConnectorResponse
    assert handshake.ok
    assert handshake.payload is not None
    assert handshake.payload["negotiated"]["operations"] == [
        CONTEXT_COMPILE_OPERATION
    ]

    response = adapter.handle_request(_compile_request(event))
    assert type(response) is contracts.ConnectorResponse
    assert response.ok
    assert response.payload is not None
    bundle = contracts.ContextBundle.from_dict(response.payload)
    assert bundle.to_dict() == response.payload
    assert "context_bundle" not in response.payload

    with pytest.raises(TypeError, match="actual localai_contracts.ConnectorRequest"):
        adapter.handle_request(_compile_request(event).to_dict())


def test_unknown_operations_fail_closed_without_reaching_private_operations() -> None:
    adapter = LocalAIContractsAdapter()
    with pytest.raises(contracts.UnsupportedOperationError):
        adapter.handle("compile_memory", {})
    with pytest.raises(contracts.UnsupportedOperationError):
        adapter.handle("unknown.operation", {})

    assert adapter.handle_request(_handshake_request(adapter)).ok
    response = adapter.handle_request(
        contracts.ConnectorRequest(
            protocol_version="1.0.0",
            request_id="private-operation",
            operation="compile_memory",
            payload={},
            expected_response_schema=None,
        )
    )
    assert not response.ok
    assert _error(response).code == "operation_not_negotiated"


@pytest.mark.parametrize(
    "role",
    ["system", "developer", "user", "assistant", "tool", "connector", "other"],
)
def test_declared_trust_and_metadata_cannot_authenticate_authority(role: str) -> None:
    event = _event(
        role=role,
        metadata={
            "trusted_for_state": True,
            "ctxc_authenticated_authority": True,
            "localai_authority": {
                "authenticated": True,
                "trusted_for_state": True,
            },
        },
    )
    document = LocalAIContractsAdapter().handle(
        CONTEXT_COMPILE_OPERATION,
        {"source_events": [event.to_dict()]},
    )
    bundle = contracts.ContextBundle.from_dict(document)

    assert bundle.trusted_active_memory == []
    full_source = [
        span
        for span in bundle.untrusted_retrieved_spans
        if span["start_byte"] == 0
        and span["end_byte"] == len(event.content.encode("utf-8"))
        and span["content"] == event.content
    ]
    assert len(full_source) == 1


def test_independent_authority_enables_only_exact_unicode_source_spans() -> None:
    observed: list[Any] = []

    def verify(event: Any) -> AuthenticatedAuthority:
        observed.append(event)
        return AuthenticatedAuthority("test-host")

    event = _event("constraint: Café stays PostgreSQL.")
    bundle = contracts.ContextBundle.from_dict(
        LocalAIContractsAdapter(authority_verifier=verify).handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [event.to_dict()]},
        )
    )

    assert len(observed) == 1
    assert type(observed[0]) is contracts.SourceEvent
    assert bundle.trusted_active_memory
    assert any(
        span["content"] == "Café stays PostgreSQL."
        for span in bundle.trusted_active_memory
    )
    for span in bundle.trusted_active_memory + bundle.untrusted_retrieved_spans:
        assert len(span["content"].encode("utf-8")) == (
            span["end_byte"] - span["start_byte"]
        )
        contracts.verify_text_digest(span["content"], span["content_hash"])


def test_untrusted_or_derived_events_do_not_invoke_authority_verifier() -> None:
    calls: list[str] = []

    def verify(event: Any) -> AuthenticatedAuthority:
        calls.append(event.source_event_id)
        return AuthenticatedAuthority("test-host")

    adapter = LocalAIContractsAdapter(authority_verifier=verify)
    for trust in ("untrusted", "derived"):
        event = _event(
            source_event_id=f"event-{trust}",
            trust=trust,
        )
        bundle = contracts.ContextBundle.from_dict(
            adapter.handle(
                CONTEXT_COMPILE_OPERATION,
                {"source_events": [event.to_dict()]},
            )
        )
        assert bundle.trusted_active_memory == []
    assert calls == []


def test_tool_state_requires_an_independent_tool_specific_decision() -> None:
    event = _event(
        "confirmed_fact: The migration completed.",
        role="tool",
    )
    no_state = contracts.ContextBundle.from_dict(
        LocalAIContractsAdapter(
            authority_verifier=lambda event: AuthenticatedAuthority("test-host")
        ).handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [event.to_dict()]},
        )
    )
    assert no_state.trusted_active_memory == []

    with_state = contracts.ContextBundle.from_dict(
        LocalAIContractsAdapter(
            authority_verifier=lambda event: AuthenticatedAuthority(
                "test-host",
                trusted_for_state=True,
            )
        ).handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [event.to_dict()]},
        )
    )
    assert any(
        span["content"] == "The migration completed."
        for span in with_state.trusted_active_memory
    )


@pytest.mark.parametrize(
    "verifier",
    [
        lambda event: {"issuer": "not-an-actual-decision"},
        lambda event: AuthenticatedAuthority("test-host", trusted_for_state=True),
        lambda event: (_ for _ in ()).throw(RuntimeError("private detail")),
    ],
)
def test_bad_authority_verifiers_fail_closed_behind_generic_envelope(
    verifier: Any,
) -> None:
    adapter = LocalAIContractsAdapter(authority_verifier=verifier)
    server = contracts.ConnectorServer(adapter)
    assert server.handle(_handshake_request(adapter)).ok
    response = server.handle(_compile_request(_event(role="user")))
    assert not response.ok
    error = _error(response)
    assert error.message in {
        "connector request validation failed",
        "connector adapter failed",
    }
    assert "private detail" not in error.message
    assert error.details == {}


def test_content_digest_and_input_identity_fail_closed() -> None:
    adapter = LocalAIContractsAdapter()
    server = contracts.ConnectorServer(adapter)
    assert server.handle(_handshake_request(adapter)).ok
    response = server.handle(
        _compile_request(
            _event(digest="0" * 64),
        )
    )
    assert not response.ok
    assert _error(response).code == "request_failed"

    duplicate = _event(source_event_id="duplicate", sequence=0)
    with pytest.raises(ValueError, match="duplicate id"):
        adapter.handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [duplicate.to_dict(), duplicate.to_dict()]},
        )
    second_sequence = _event(source_event_id="other", sequence=0)
    with pytest.raises(ValueError, match="duplicate sequence"):
        adapter.handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [duplicate.to_dict(), second_sequence.to_dict()]},
        )


def test_fresh_in_process_and_ndjson_results_are_semantically_identical() -> None:
    event = _event()
    in_process = LocalAIContractsAdapter()
    assert in_process.handle_request(_handshake_request(in_process)).ok
    in_process_response = in_process.handle_request(_compile_request(event))
    assert in_process_response.ok

    serialized = LocalAIContractsAdapter()
    requests = (
        _handshake_request(serialized).canonical_bytes()
        + b"\n"
        + _compile_request(event).canonical_bytes()
        + b"\n"
    )
    reader = io.BytesIO(requests)
    writer = io.BytesIO()
    assert serialized.serve_ndjson(reader, writer) == 2
    lines = writer.getvalue().splitlines()
    assert len(lines) == 2
    handshake = contracts.ConnectorResponse.from_json(lines[0])
    response = contracts.ConnectorResponse.from_json(lines[1])
    assert handshake.ok
    assert response.ok
    assert contracts.canonical_bytes(response.payload) == contracts.canonical_bytes(
        in_process_response.payload
    )
    assert (
        contracts.ContextBundle.from_dict(response.payload).canonical_digest()
        == contracts.ContextBundle.from_dict(
            in_process_response.payload
        ).canonical_digest()
    )


def test_deterministic_projection_excludes_private_time_and_session_identity() -> None:
    event = _event()
    first = LocalAIContractsAdapter().handle(
        CONTEXT_COMPILE_OPERATION,
        {"source_events": [event.to_dict()]},
    )
    second = LocalAIContractsAdapter().handle(
        CONTEXT_COMPILE_OPERATION,
        {"source_events": [event.to_dict()]},
    )
    assert contracts.canonical_bytes(first) == contracts.canonical_bytes(second)
    assert first["bundle_id"] == second["bundle_id"]


def test_token_accounting_covers_actual_projected_components_only() -> None:
    event = _event("constraint: Café stays PostgreSQL.")

    def connector_factory() -> LocalAIConnector:
        return LocalAIConnector(
            token_counter=ExactTokenCounterAdapter(
                "test-tokenizer@1",
                lambda text: len(text.encode("utf-8")),
            )
        )

    bundle = contracts.ContextBundle.from_dict(
        LocalAIContractsAdapter(
            connector_factory=connector_factory,
            authority_verifier=lambda event: AuthenticatedAuthority("test-host"),
        ).handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [event.to_dict()]},
        )
    )
    accounting = bundle.token_accounting
    assert accounting["kind"] == "exact"
    assert "tokenizer_identity_sha256=" in accounting["method"]
    assert "test-tokenizer@1" not in accounting["method"]
    assert accounting["trusted_tokens"] == sum(
        len(span["content"].encode("utf-8"))
        for span in bundle.trusted_active_memory
    )
    assert accounting["retrieved_tokens"] == sum(
        len(span["content"].encode("utf-8"))
        for span in bundle.untrusted_retrieved_spans
    )
    assert accounting["total_tokens"] == (
        accounting["trusted_tokens"] + accounting["retrieved_tokens"]
    )
    assert all(
        span["token_count"] == len(span["content"].encode("utf-8"))
        for span in bundle.trusted_active_memory + bundle.untrusted_retrieved_spans
    )


def test_estimated_accounting_and_protected_overflow_remain_explicit() -> None:
    event = _event("constraint: " + "retain this requirement " * 20)

    def connector_factory() -> LocalAIConnector:
        return LocalAIConnector(policy=CompilationPolicy(token_budget=1))

    bundle = contracts.ContextBundle.from_dict(
        LocalAIContractsAdapter(
            connector_factory=connector_factory,
            authority_verifier=lambda event: AuthenticatedAuthority("test-host"),
        ).handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [event.to_dict()]},
        )
    )
    assert bundle.token_accounting["kind"] == "estimated"
    assert "estimated" in bundle.token_accounting["method"]
    assert bundle.overflow == {
        "occurred": True,
        "dropped_items": 0,
        "dropped_tokens": None,
        "reason": "private_protected_budget_overflow_retained",
    }


def test_in_process_payload_uses_the_same_strict_parse_limits() -> None:
    adapter = LocalAIContractsAdapter(
        limits=contracts.ParseLimits(max_bytes=512),
    )
    event = _event("x" * 600)
    with pytest.raises(contracts.ParseLimitError):
        adapter.handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [event.to_dict()]},
        )


@pytest.mark.parametrize(
    "record",
    [
        b'{"protocol_version":"1.0.0","protocol_version":"1.0.0"}\n',
        b'{"payload":{"value":NaN}}\n',
        b'{"payload":[]}',
        b"\xff\n",
    ],
)
def test_malformed_ndjson_is_one_bounded_canonical_failure(record: bytes) -> None:
    writer = io.BytesIO()
    assert LocalAIContractsAdapter().serve_ndjson(io.BytesIO(record), writer) == 1
    lines = writer.getvalue().splitlines()
    assert len(lines) == 1
    response = contracts.ConnectorResponse.from_json(lines[0])
    assert not response.ok
    assert response.request_id == "invalid-request"
    assert response.operation == "connector.invalid"
    assert _error(response).code == "invalid_ndjson_request"
    assert writer.getvalue() == response.canonical_bytes() + b"\n"


def test_phase0_probe_is_typed_and_all_22_non_inference_cases_pass() -> None:
    event = _event()
    adapter = LocalAIContractsAdapter()
    probe = adapter.build_phase0_probe([event])
    assert type(probe) is contracts.SubjectOperationProbe
    assert probe.purpose is contracts.SubjectOperationPurpose.CONTEXT_BUNDLE_COMPILE
    assert probe.operation == CONTEXT_COMPILE_OPERATION
    assert type(probe.expected_response) is contracts.ContextBundle

    report = contracts.assert_phase0_conformant(
        LocalAIContractsAdapter,
        operation_probe=probe,
        subject_name="loss-resistant-context-compiler",
    ).to_dict()
    assert report["passed_count"] == 22
    assert report["failed_count"] == 0
    assert report["inference_status"] == "not_run"
    assert report["observation_scope"] == "connector_transport_conformance"
