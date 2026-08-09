from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from conformance.run_connector_conformance import (
    _read_bounded_text,
    _strict_json_loads,
)
from conformance.schema_validation import (
    SchemaValidationError,
    audit_schema_documents,
    validate_instance,
)
from scripts.validate_localai_contracts_install import (
    _clean_connector_environment,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = (
    ROOT / "conformance" / "fixtures" / "golden-localai-contract-conversion-v1.json"
)
AUDIT_SCHEMA_PATH = ROOT / "schemas" / "localai-contract-conversion-audit.schema.json"

GOLDEN_BYTE_LENGTH = 5_716
GOLDEN_RAW_SHA256 = "2e337dd2a20b246d827840639bde6d11e29b39ca2861a6fb986ccb16e98f8762"
GOLDEN_CANONICAL_SHA256 = (
    "05292bab6287bedf22767b473eceda35332e9ca9de547f01fe2f0559d5827264"
)


def _load_strict_json(raw: bytes) -> Any:
    return _strict_json_loads(
        raw.decode("utf-8", errors="strict"),
        label="conversion-audit test JSON",
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", errors="strict")


def _load_audit_schema() -> dict[str, Any]:
    schema = _strict_json_loads(
        _read_bounded_text(AUDIT_SCHEMA_PATH, label="conversion-audit schema"),
        label="conversion-audit schema",
    )
    assert isinstance(schema, dict)
    return schema


def _source_event_record_schema() -> dict[str, Any]:
    audit_schema = _load_audit_schema()
    return {
        "$schema": audit_schema["$schema"],
        "$defs": audit_schema["$defs"],
        "$ref": "#/$defs/source_event_record",
    }


def _claim_boundary_schema() -> dict[str, Any]:
    audit_schema = _load_audit_schema()
    return {
        "$schema": audit_schema["$schema"],
        "$defs": audit_schema["$defs"],
        "$ref": "#/$defs/claim_boundary",
    }


def _claim_boundary() -> dict[str, Any]:
    return {
        "self_hash": "mutation_detection_only_not_authentication",
        "audit_path_pattern_semantics": (
            "rfc6901_after_braced_ordinal_is_replaced_by_zero_based_record_index"
        ),
        "output_path_semantics": "rfc6901_against_caller_supplied_context_bundle",
        "caller_evidence_bound_path_patterns": [
            "/input_payload_sha256",
            "/source_event_conversion/records/{ordinal}/input_document_sha256",
            "/source_event_conversion/records/{ordinal}/round_trip_document_sha256",
            "/context_bundle_conversion/output_document_sha256",
            "/context_bundle_conversion/output_round_trip_sha256",
        ],
        "provider_asserted_path_patterns": [
            "/source_event_conversion/records/{ordinal}/private_document_sha256",
            "/source_event_conversion/records/{ordinal}/round_trip_exact",
            "/source_event_conversion/records/{ordinal}/loss_detected",
            "/source_event_conversion/records/{ordinal}/independently_authenticated",
            "/source_event_conversion/records/{ordinal}/trusted_for_state",
            "/source_event_conversion/all_round_trips_exact",
            "/source_event_conversion/loss_detected",
            "/context_bundle_conversion/private_document_sha256",
            "/context_bundle_conversion/private_bundle_sha256",
            "/context_bundle_conversion/private_round_trip_supported",
            "/context_bundle_conversion/loss_detected",
            "/context_bundle_conversion/omitted_private_values_retained",
        ],
        "provider_assertion_dependent_path_patterns": [
            "/source_event_conversion/records/{ordinal}/execution_role_normalized",
            "/context_bundle_conversion/source_coverage",
        ],
        "provider_asserted_output_paths": [
            "/omissions",
            "/overflow",
            "/policy_identity/digest",
        ],
        "provider_assertion_dependent_output_paths": [
            "/trusted_active_memory",
            "/untrusted_retrieved_spans",
        ],
        "digest_privacy": (
            "sensitive_unsalted_linkable_dictionary_testable_not_safe_telemetry"
        ),
    }


def _source_event_record(
    *, trusted_for_state: bool, independently_authenticated: bool
) -> dict[str, Any]:
    digest = "0" * 64
    return {
        "ordinal": 0,
        "input_document_sha256": digest,
        "private_document_sha256": digest,
        "round_trip_document_sha256": digest,
        "round_trip_exact": True,
        "loss_detected": False,
        "independently_authenticated": independently_authenticated,
        "trusted_for_state": trusted_for_state,
        "execution_role_normalized": False,
    }


def test_conversion_golden_raw_bytes_and_canonical_self_hash_are_frozen() -> None:
    raw = _read_bounded_text(GOLDEN_PATH, label="conversion golden").encode(
        "utf-8"
    )

    assert len(raw) == GOLDEN_BYTE_LENGTH
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")
    assert b"\r" not in raw
    assert hashlib.sha256(raw).hexdigest() == GOLDEN_RAW_SHA256

    document = _load_strict_json(raw)
    assert isinstance(document, dict)
    assert set(document) == {
        "schema",
        "fixed_compiled_at",
        "source_events",
        "expected",
        "golden_sha256",
    }
    assert document["schema"] == "ctxc-localai-conversion-golden-0.1"
    assert len(document["source_events"]) == 7
    assert document["golden_sha256"] == GOLDEN_CANONICAL_SHA256

    unsigned = {key: value for key, value in document.items() if key != "golden_sha256"}
    canonical_sha256 = hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()
    assert canonical_sha256 == GOLDEN_CANONICAL_SHA256


@pytest.mark.parametrize(
    "raw",
    [
        b'{"key":1,"key":2}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":-Infinity}',
        b'{"value":1e9999}',
    ],
)
def test_strict_json_loader_rejects_duplicate_keys_and_nonfinite_numbers(raw: bytes) -> None:
    with pytest.raises(ValueError):
        _load_strict_json(raw)


def test_clean_connector_environment_removes_import_path_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONHOME", "shadow-home")
    monkeypatch.setenv("pythonpath", "shadow-path")
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", "shadow-cache")

    environment = _clean_connector_environment()

    assert not {
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONPYCACHEPREFIX",
    } & {name.upper() for name in environment}
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert environment["PYTHONSAFEPATH"] == "1"


def test_conversion_audit_schema_passes_dependency_free_schema_audit() -> None:
    schema = _load_audit_schema()
    documents = {AUDIT_SCHEMA_PATH.name: schema}

    audit_schema_documents(frozenset(documents), documents=documents)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert "Provider-local diagnostic evidence" in schema["description"]


def test_claim_boundary_is_exact_and_machine_readable() -> None:
    schema_name = "claim-boundary.schema.json"
    documents = {schema_name: _claim_boundary_schema()}
    claim_boundary = _claim_boundary()

    validate_instance(claim_boundary, schema_name, documents=documents)

    claim_boundary["provider_asserted_path_patterns"] = []
    with pytest.raises(SchemaValidationError):
        validate_instance(claim_boundary, schema_name, documents=documents)


@pytest.mark.parametrize(
    ("trusted_for_state", "independently_authenticated"),
    [(False, False), (False, True), (True, True)],
)
def test_source_event_trust_states_allowed_by_audit_schema(
    trusted_for_state: bool,
    independently_authenticated: bool,
) -> None:
    schema_name = "source-event-record.schema.json"
    documents = {schema_name: _source_event_record_schema()}
    record = _source_event_record(
        trusted_for_state=trusted_for_state,
        independently_authenticated=independently_authenticated,
    )

    validate_instance(record, schema_name, documents=documents)


def test_trusted_source_event_requires_independent_authentication() -> None:
    schema_name = "source-event-record.schema.json"
    documents = {schema_name: _source_event_record_schema()}
    record = _source_event_record(
        trusted_for_state=True,
        independently_authenticated=False,
    )

    with pytest.raises(SchemaValidationError):
        validate_instance(record, schema_name, documents=documents)
