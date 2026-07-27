from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conformance import run_connector_conformance as conformance_runner
from conformance.schema_validation import (
    SchemaValidationError,
    audit_schema_documents,
    validate_instance,
)
from context_compiler import (
    CONNECTOR_REQUEST_SCHEMA,
    IncrementalCompiler,
    LocalAIConnector,
    SourceEvent,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIRECTORY = ROOT / "schemas"
CONFORMANCE_DIRECTORY = ROOT / "conformance"

CONNECTOR_SCHEMA_FILENAMES = {
    "connector-request.schema.json",
    "connector-response.schema.json",
    "localai-source-event.schema.json",
    "context-bundle.schema.json",
    "incremental-checkpoint.schema.json",
    *{
        f"connector-{operation}-{side}.schema.json"
        for operation in (
            "capabilities",
            "ingest-source-events",
            "compile-memory",
            "render-context",
            "verify-memory",
            "inspect-memory",
        )
        for side in ("payload", "result")
    },
}

_STAT_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_nlink",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


def _stat_copy(observed: object, **changes: int) -> SimpleNamespace:
    values = {name: getattr(observed, name) for name in _STAT_FIELDS}
    values.update(changes)
    return SimpleNamespace(**values)


def _schema_nodes(value: object):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _schema_nodes(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _schema_nodes(nested)


def test_connector_schema_collections_are_explicitly_bounded() -> None:
    for filename in CONNECTOR_SCHEMA_FILENAMES:
        schema = json.loads((SCHEMA_DIRECTORY / filename).read_text(encoding="utf-8"))
        for node in _schema_nodes(schema):
            if node.get("type") == "array":
                assert isinstance(node.get("maxItems"), int), (
                    filename,
                    node,
                )
                assert node["maxItems"] > 0


def test_connector_schema_graph_materializes_every_wire_contract() -> None:
    assert len(CONNECTOR_SCHEMA_FILENAMES) == 17
    for filename in CONNECTOR_SCHEMA_FILENAMES:
        schema = json.loads((SCHEMA_DIRECTORY / filename).read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == (
            f"https://example.invalid/loss-resistant-context-compiler/{filename}"
        )
        assert schema["type"] == "object"

    request = json.loads(
        (SCHEMA_DIRECTORY / "connector-request.schema.json").read_text(encoding="utf-8")
    )
    response = json.loads(
        (SCHEMA_DIRECTORY / "connector-response.schema.json").read_text(encoding="utf-8")
    )
    assert len(request["oneOf"]) == 6
    assert len(response["oneOf"]) == 7
    assert request["additionalProperties"] is False
    assert response["additionalProperties"] is False
    assert len(response["$defs"]["error"]["oneOf"]) == 10


def test_connector_response_schema_rejects_unredacted_error_details() -> None:
    documents = conformance_runner._validate_schema_graph()
    response = LocalAIConnector().handle_request(
        _request("redaction", "unsupported", {})
    )
    validate_instance(
        response,
        "connector-response.schema.json",
        documents=documents,
    )

    raw_message = copy.deepcopy(response)
    raw_message["error"]["message"] = "C:\\private\\tenant\\memory.json"
    with pytest.raises(SchemaValidationError):
        validate_instance(
            raw_message,
            "connector-response.schema.json",
            documents=documents,
        )

    raw_type = copy.deepcopy(response)
    raw_type["error"]["details"]["exception_type"] = "TenantBackendError"
    with pytest.raises(SchemaValidationError):
        validate_instance(
            raw_type,
            "connector-response.schema.json",
            documents=documents,
        )


def test_dependency_free_validator_uses_json_schema_numeric_equality() -> None:
    const_documents = {"numeric.schema.json": {"const": 1}}
    validate_instance(1.0, "numeric.schema.json", documents=const_documents)
    with pytest.raises(SchemaValidationError, match="required constant"):
        validate_instance(True, "numeric.schema.json", documents=const_documents)

    unique_documents = {"numeric.schema.json": {"type": "array", "uniqueItems": True}}
    with pytest.raises(SchemaValidationError, match="duplicate items"):
        validate_instance(
            [1, 1.0],
            "numeric.schema.json",
            documents=unique_documents,
        )
    validate_instance(
        [True, 1],
        "numeric.schema.json",
        documents=unique_documents,
    )

    integer_documents = {"numeric.schema.json": {"type": "integer"}}
    validate_instance(1.0, "numeric.schema.json", documents=integer_documents)
    validate_instance(-0.0, "numeric.schema.json", documents=integer_documents)
    with pytest.raises(SchemaValidationError, match="required type"):
        validate_instance(1.5, "numeric.schema.json", documents=integer_documents)
    with pytest.raises(SchemaValidationError, match="required type"):
        validate_instance(True, "numeric.schema.json", documents=integer_documents)
    with pytest.raises(SchemaValidationError, match="finite JSON number"):
        validate_instance(
            float("nan"),
            "numeric.schema.json",
            documents={"numeric.schema.json": {}},
        )


def test_schema_integral_float_semantics_do_not_weaken_runtime_exact_ints() -> None:
    documents = conformance_runner._validate_schema_graph()
    request = _request(
        "integral-float",
        "ingest_source_events",
        {"events": [{"role": "user", "content": "goal: exact", "sequence": 1.0}]},
    )
    validate_instance(
        request,
        "connector-request.schema.json",
        documents=documents,
    )

    response = LocalAIConnector().handle_request(request)
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_value"


def test_dependency_free_validator_asserts_rfc3339_date_time_format() -> None:
    documents = {"time.schema.json": {"type": "string", "format": "date-time"}}
    valid = (
        "2026-07-25T12:34:56Z",
        "1963-06-19T08:30:06.283185Z",
        "1937-01-01T12:00:27.87+00:20",
        "1990-12-31T15:59:50.123-08:00",
        "1998-12-31T23:59:60Z",
        "1998-12-31T15:59:60.123-08:00",
        "1963-06-19t08:30:06.283185z",
    )
    for value in valid:
        validate_instance(value, "time.schema.json", documents=documents)

    invalid = (
        "2026-02-30T12:34:56Z",
        "1963-6-19T08:30:06.283185Z",
        "1998-12-31T23:59:61Z",
        "1998-12-31T23:58:60Z",
        "1998-12-31T22:59:60Z",
        "1990-12-31T15:59:59-24:00",
        "1990-12-31T10:00:00+10:60",
        "1990-12-31T24:00:00Z",
        "1963-06-19T08:30:06.28123+01:00Z",
    )
    for value in invalid:
        with pytest.raises(SchemaValidationError, match="not a supported date-time"):
            validate_instance(value, "time.schema.json", documents=documents)

    documents["time.schema.json"]["format"] = "email"
    with pytest.raises(SchemaValidationError, match="unsupported schema format"):
        audit_schema_documents(frozenset(documents), documents=documents)


def test_dependency_free_validator_accepts_arbitrarily_large_json_integers() -> None:
    documents = {"numeric.schema.json": {"type": "number"}}
    validate_instance(10**10_000, "numeric.schema.json", documents=documents)


def test_dependency_free_validator_rejects_unknown_transitive_vocabulary() -> None:
    documents = {
        "root.schema.json": {"$ref": "nested.schema.json"},
        "nested.schema.json": {"type": "string", "contentEncoding": "base64"},
    }
    with pytest.raises(SchemaValidationError, match="unsupported schema keywords.*contentEncoding"):
        audit_schema_documents(
            frozenset({"root.schema.json"}),
            documents=documents,
        )


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        ({"type": "object", "required": "field"}, "required"),
        ({"enum": "value"}, "enum"),
        ({"type": "string", "pattern": "["}, "regular expression"),
        ({"type": []}, "type"),
        ({"minItems": -1}, "minItems"),
        ({"uniqueItems": 1}, "uniqueItems"),
    ],
)
def test_dependency_free_validator_rejects_malformed_supported_keywords(
    schema: dict,
    message: str,
) -> None:
    documents = {"invalid.schema.json": schema}
    with pytest.raises(SchemaValidationError, match=message):
        audit_schema_documents(frozenset(documents), documents=documents)
    with pytest.raises(SchemaValidationError, match=message):
        validate_instance(None, "invalid.schema.json", documents=documents)


@pytest.mark.parametrize(
    ("documents", "root"),
    [
        ({"self.schema.json": {"$ref": "#"}}, "self.schema.json"),
        (
            {
                "first.schema.json": {"$ref": "second.schema.json"},
                "second.schema.json": {"$ref": "first.schema.json"},
            },
            "first.schema.json",
        ),
        (
            {
                "tree.schema.json": {
                    "anyOf": [
                        {"type": "null"},
                        {"type": "array", "items": {"$ref": "#"}},
                    ]
                }
            },
            "tree.schema.json",
        ),
    ],
)
def test_dependency_free_validator_rejects_unsupported_reference_cycles(
    documents: dict[str, dict],
    root: str,
) -> None:
    with pytest.raises(SchemaValidationError, match="cyclic schema reference"):
        audit_schema_documents(frozenset({root}), documents=documents)
    with pytest.raises(SchemaValidationError, match="cyclic schema reference"):
        validate_instance(None, root, documents=documents)


def test_dependency_free_validator_resolves_acyclic_transitive_refs() -> None:
    documents = {
        "root.schema.json": {"$ref": "middle.schema.json"},
        "middle.schema.json": {"$ref": "leaf.schema.json"},
        "leaf.schema.json": {"type": "string"},
    }
    audit_schema_documents(frozenset({"root.schema.json"}), documents=documents)
    validate_instance("value", "root.schema.json", documents=documents)
    with pytest.raises(SchemaValidationError, match="required type"):
        validate_instance(1, "root.schema.json", documents=documents)

def test_connector_transcripts_cover_all_operations_and_66_negative_vectors() -> None:
    success = [
        json.loads(line)
        for line in (CONFORMANCE_DIRECTORY / "fixtures" / "golden-success.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    negative = [
        json.loads(line)
        for line in (CONFORMANCE_DIRECTORY / "fixtures" / "golden-negative.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert [row["request"]["operation"] for row in success] == [
        "capabilities",
        "ingest_source_events",
        "compile_memory",
        "render_context",
        "verify_memory",
        "inspect_memory",
    ]
    assert len(negative) == 66
    assert len({row["id"] for row in negative}) == 66


def test_conformance_fixture_reader_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "oversized.jsonl"
    fixture.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(conformance_runner, "MAX_CONFORMANCE_BYTES", 2)

    with pytest.raises(ValueError, match="exceeds 2 bytes"):
        conformance_runner._load_jsonl(fixture)


def test_conformance_fixture_reader_rejects_nonfinite_numbers(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "nonfinite.jsonl"
    fixture.write_text('{"value":1e9999}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="must be finite"):
        conformance_runner._load_jsonl(fixture)


@pytest.mark.parametrize("sign", ["", "-"])
def test_conformance_json_reader_accepts_640_digit_integers(sign: str) -> None:
    integer = sign + "9" * 640

    assert conformance_runner._strict_json_loads(
        f'{{"value":{integer}}}',
        label="conformance fixture",
    ) == {"value": int(integer)}


@pytest.mark.parametrize("sign", ["", "-"])
def test_conformance_json_reader_rejects_641_digit_integers(sign: str) -> None:
    integer = sign + "9" * 641

    with pytest.raises(
        ValueError,
        match=(
            "conformance JSON integer exceeds the supported length of 640 digits"
        ),
    ):
        conformance_runner._strict_json_loads(
            f'{{"value":{integer}}}',
            label="conformance fixture",
        )


def test_conformance_fixture_reader_rejects_duplicate_keys(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "duplicate.jsonl"
    fixture.write_text('{"id":"one","id":"two"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate JSON key 'id'"):
        conformance_runner._load_jsonl(fixture)


def test_conformance_fixture_reader_rejects_excessive_json_depth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "deep.jsonl"
    fixture.write_text('{"value":[[[[0]]]]}\n', encoding="utf-8")
    monkeypatch.setattr(conformance_runner, "MAX_CONFORMANCE_JSON_DEPTH", 4)

    with pytest.raises(ValueError, match="exceeds 4 JSON levels"):
        conformance_runner._load_jsonl(fixture)


def test_bounded_reader_allows_descriptor_only_change_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tmp_path / "document.json"
    document.write_text('{"safe":true}', encoding="utf-8")
    real_fstat = conformance_runner.os.fstat
    calls = 0

    def descriptor_stat(descriptor: int) -> object:
        nonlocal calls
        observed = real_fstat(descriptor)
        calls += 1
        if calls == 2:
            return _stat_copy(observed, st_ctime_ns=observed.st_ctime_ns + 1)
        return observed

    monkeypatch.setattr(conformance_runner.os, "fstat", descriptor_stat)

    assert conformance_runner._read_bounded_text(document, label="document") == ('{"safe":true}')


def test_bounded_reader_rejects_modification_time_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tmp_path / "document.json"
    document.write_text('{"safe":true}', encoding="utf-8")
    real_fstat = conformance_runner.os.fstat
    calls = 0

    def descriptor_stat(descriptor: int) -> object:
        nonlocal calls
        observed = real_fstat(descriptor)
        calls += 1
        if calls == 2:
            return _stat_copy(observed, st_mtime_ns=observed.st_mtime_ns + 1)
        return observed

    monkeypatch.setattr(conformance_runner.os, "fstat", descriptor_stat)

    with pytest.raises(ValueError, match="changed while reading"):
        conformance_runner._read_bounded_text(document, label="document")


def test_standalone_conformance_runner_proves_in_process_stdio_equivalence() -> None:
    completed = subprocess.run(
        [sys.executable, str(CONFORMANCE_DIRECTORY / "run_connector_conformance.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "golden_steps": 6,
        "negative_vectors": 66,
        "passed": True,
        "schemas": 17,
    }


def _request(request_id: object, operation: object, payload: object) -> dict:
    return {
        "schema": CONNECTOR_REQUEST_SCHEMA,
        "request_id": request_id,
        "operation": operation,
        "payload": payload,
    }


@pytest.mark.parametrize("suffix", ["\n", "\r", "\r\n", "\u2028", "\u2029"])
def test_connector_source_event_schema_rejects_trailing_line_terminators(
    suffix: str,
) -> None:
    documents = conformance_runner._validate_schema_graph()
    content = "goal: exact boundaries"
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    events = (
        ({"role": "user" + suffix, "content": content}, "invalid_value"),
        (
            {
                "role": "user",
                "content": content,
                "authority": {"authenticated": True, "issuer": "host" + suffix},
            },
            "invalid_value",
        ),
        (
            {
                "role": "user",
                "content": content,
                "content_sha256": content_sha256 + suffix,
            },
            "integrity_check_failed",
        ),
    )
    for index, (event, expected_code) in enumerate(events):
        request = _request(
            f"line-terminator-{index}",
            "ingest_source_events",
            {"events": [event]},
        )
        with pytest.raises(SchemaValidationError):
            validate_instance(
                request,
                "connector-request.schema.json",
                documents=documents,
            )
        response = LocalAIConnector().handle_request(request)
        assert response["ok"] is False
        assert response["error"]["code"] == expected_code


def test_connector_digest_patterns_have_exact_length_guards() -> None:
    documents = conformance_runner._validate_schema_graph()
    schema_names = CONNECTOR_SCHEMA_FILENAMES | {"compiled-memory.schema.json"}
    observed = 0
    for schema_name in schema_names:
        for node in _schema_nodes(documents[schema_name]):
            if node.get("pattern") != "^[a-f0-9]{64}$":
                continue
            observed += 1
            assert node["minLength"] == 64
            assert node["maxLength"] == 64
    assert observed >= 10


def _rehash_checkpoint(checkpoint: dict) -> None:
    payload = {key: value for key, value in checkpoint.items() if key != "checkpoint_sha256"}
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    checkpoint["checkpoint_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_response_schema_covers_echoed_invalid_json_identity_values() -> None:
    documents = conformance_runner._validate_schema_graph()
    connector = LocalAIConnector()
    responses = [
        connector.handle_request(_request([], "capabilities", {})),
        connector.handle_request(_request("valid", [], {})),
    ]
    for response in responses:
        validate_instance(
            response,
            "connector-response.schema.json",
            documents=documents,
        )

    successful = connector.handle_request(_request("valid", "capabilities", {}))
    successful["request_id"] = None
    with pytest.raises(SchemaValidationError, match="required type"):
        validate_instance(
            successful,
            "connector-response.schema.json",
            documents=documents,
        )


def test_compile_payload_schema_requires_state_and_verification() -> None:
    documents = conformance_runner._validate_schema_graph()
    valid = _request(
        "valid",
        "compile_memory",
        {"events": [{"role": "user", "content": "goal: Test"}]},
    )
    validate_instance(
        valid,
        "connector-request.schema.json",
        documents=documents,
    )

    for payload in ({}, {"events": [], "policy": {"verify": False}}):
        with pytest.raises(SchemaValidationError):
            validate_instance(
                _request("invalid", "compile_memory", payload),
                "connector-request.schema.json",
                documents=documents,
            )


def test_disabled_connector_verification_fails_before_ingestion() -> None:
    connector = LocalAIConnector()
    response = connector.handle_request(
        _request(
            "disabled",
            "compile_memory",
            {
                "session_id": "disabled-policy-session",
                "events": [{"role": "user", "content": "goal: Do not ingest"}],
                "policy": {"verify": False},
            },
        )
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_value"

    follow_up = connector.handle_request(
        _request(
            "follow-up",
            "compile_memory",
            {"session_id": "disabled-policy-session"},
        )
    )
    assert follow_up["ok"] is False
    assert follow_up["error"]["code"] == "unknown_session"


def test_checkpoint_canonical_records_reject_unknown_and_missing_fields() -> None:
    incremental = IncrementalCompiler(session_id="strict-checkpoint")
    incremental.ingest_source_events(
        [SourceEvent(role="user", content="goal: Preserve exact records")]
    )
    checkpoint = incremental.checkpoint()

    extra = copy.deepcopy(checkpoint)
    extra["source_records"][0]["unexpected"] = True
    _rehash_checkpoint(extra)
    with pytest.raises(ValueError, match="unknown unexpected"):
        IncrementalCompiler.from_checkpoint(extra)

    missing = copy.deepcopy(checkpoint)
    del missing["source_records"][0]["timestamp"]
    _rehash_checkpoint(missing)
    with pytest.raises(ValueError, match="missing timestamp"):
        IncrementalCompiler.from_checkpoint(missing)


def test_verify_source_records_reject_unknown_fields() -> None:
    connector = LocalAIConnector()
    ingested = connector.ingest_source_events(
        [SourceEvent(role="user", content="constraint: Keep exact records")],
        session_id="strict-source-records",
    )
    bundle = connector.compile_memory(session_id="strict-source-records")
    source_record = copy.deepcopy(ingested["source_records"][0])
    source_record["unexpected"] = True

    with pytest.raises(ValueError, match="unknown unexpected"):
        connector.verify_memory(bundle, source_records=[source_record])
