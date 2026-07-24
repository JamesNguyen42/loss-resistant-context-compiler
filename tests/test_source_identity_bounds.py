from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from context_compiler import (
    MAX_SOURCE_ID_CHARS,
    MAX_SOURCE_ROLE_CHARS,
    MAX_SOURCE_TIMESTAMP_CHARS,
    ContextCompiler,
    LiteralModelExtractor,
    ModelExtractor,
    ProvenanceSpan,
    RedactionError,
    SourceRecord,
    redact_sources,
    verify_redaction_report_hash,
)
from context_compiler.io import load_sources, validate_artifact_envelope
from context_compiler.redaction import RedactionFinding

SCHEMA_DIRECTORY = Path(__file__).parents[1] / "schemas"


def _bounded_value(marker: str, maximum: int) -> str:
    return marker + ("x" * (maximum - len(marker)))


def test_identity_boundaries_preserve_opaque_unicode_and_control_characters() -> None:
    source_id = _bounded_value("\x00🧭", MAX_SOURCE_ID_CHARS)
    role = _bounded_value("\n役割", MAX_SOURCE_ROLE_CHARS)
    timestamp = _bounded_value("\u2028時刻", MAX_SOURCE_TIMESTAMP_CHARS)

    source = SourceRecord.create(
        id=source_id,
        sequence=0,
        role=role,
        content="constraint: preserve bounded identities",
        timestamp=timestamp,
    )
    restored = SourceRecord.from_dict(source.to_dict())
    span = ProvenanceSpan.from_source(source, 0, len(source.content))

    assert restored == source
    assert span.source_id == source_id
    assert len(source.id) == MAX_SOURCE_ID_CHARS
    assert len(source.role) == MAX_SOURCE_ROLE_CHARS
    assert len(source.timestamp or "") == MAX_SOURCE_TIMESTAMP_CHARS


@pytest.mark.parametrize(
    ("field", "maximum"),
    [
        ("id", MAX_SOURCE_ID_CHARS),
        ("role", MAX_SOURCE_ROLE_CHARS),
        ("timestamp", MAX_SOURCE_TIMESTAMP_CHARS),
    ],
)
def test_source_record_rejects_each_identity_field_one_character_over_limit(
    field: str,
    maximum: int,
) -> None:
    values: dict[str, Any] = {
        "id": "source",
        "sequence": 0,
        "role": "user",
        "content": "goal: stay bounded",
        "timestamp": None,
    }
    oversized = "z" * (maximum + 1)
    values[field] = oversized

    with pytest.raises(ValueError, match=rf"source (?:record )?{field} exceeds {maximum}"):
        SourceRecord.create(**values)

    payload = json.dumps(values, separators=(",", ":"))
    with pytest.raises(ValueError, match=rf"source (?:record )?{field} exceeds {maximum}"):
        load_sources(io.StringIO(payload))


def test_derived_source_id_holders_enforce_the_same_ceiling() -> None:
    oversized = "s" * (MAX_SOURCE_ID_CHARS + 1)

    with pytest.raises(ValueError, match="provenance source_id exceeds"):
        ProvenanceSpan(
            source_id=oversized,
            start=0,
            end=1,
            quote="x",
        )
    with pytest.raises(ValueError, match="redaction finding source_id exceeds"):
        RedactionFinding(
            source_sequence=0,
            source_id=oversized,
            start=0,
            end=1,
            detector="aws_access_key_id",
            redacted_characters=1,
            line_boundaries_preserved=0,
        )


def test_artifact_and_model_interchange_reject_oversized_source_ids() -> None:
    source = SourceRecord.create(
        id="source",
        sequence=0,
        role="user",
        content="constraint: keep source ids bounded",
    )
    oversized = "s" * (MAX_SOURCE_ID_CHARS + 1)
    artifact = ContextCompiler().compile([source]).to_dict()
    artifact["items"][0]["provenance"][0]["source_id"] = oversized

    with pytest.raises(ValueError, match="invalid_provenance_shape"):
        validate_artifact_envelope(artifact)

    coordinate = ModelExtractor(
        lambda _prompt: {
            "items": [
                {
                    "kind": "constraint",
                    "text": source.content,
                    "provenance": [
                        {
                            "source_id": oversized,
                            "start": 0,
                            "end": len(source.content),
                        }
                    ],
                }
            ]
        }
    ).extract([source])
    literal = LiteralModelExtractor(
        lambda _prompt: {
            "items": [
                {
                    "kind": "constraint",
                    "text": source.content,
                    "source_ids": [oversized],
                }
            ]
        }
    ).extract([source])

    assert coordinate.items == []
    assert "candidate source_id exceeds" in coordinate.rejected[0]["detail"]
    assert literal.items == []
    assert "candidate source_ids must not exceed" in literal.rejected[0]["detail"]


def test_redaction_report_parser_rejects_oversized_source_ids() -> None:
    source = SourceRecord.create(
        id="source",
        sequence=0,
        role="user",
        content='password="secret-value-123"',
    )
    report = redact_sources([source]).to_report()
    assert report["findings"]
    report["findings"][0]["source_id"] = "s" * (MAX_SOURCE_ID_CHARS + 1)

    with pytest.raises(RedactionError, match="source id exceeds"):
        verify_redaction_report_hash(report)


def test_packaged_schemas_track_source_identity_ceilings() -> None:
    schemas = {
        name: json.loads((SCHEMA_DIRECTORY / name).read_text(encoding="utf-8"))
        for name in (
            "compiled-memory.schema.json",
            "model-extraction.schema.json",
            "model-extraction-literal.schema.json",
            "redaction-report.schema.json",
            "source-archive-entry.schema.json",
            "source-event.schema.json",
        )
    }

    source_event = schemas["source-event.schema.json"]["properties"]
    source_archive = schemas["source-archive-entry.schema.json"]["$defs"][
        "canonicalSourceRecord"
    ]["properties"]
    assert source_event["id"]["anyOf"][0]["maxLength"] == MAX_SOURCE_ID_CHARS
    assert source_event["role"]["maxLength"] == MAX_SOURCE_ROLE_CHARS
    assert source_event["timestamp"]["maxLength"] == MAX_SOURCE_TIMESTAMP_CHARS
    assert source_archive["id"]["maxLength"] == MAX_SOURCE_ID_CHARS
    assert source_archive["role"]["maxLength"] == MAX_SOURCE_ROLE_CHARS
    assert source_archive["timestamp"]["maxLength"] == MAX_SOURCE_TIMESTAMP_CHARS

    assert (
        schemas["compiled-memory.schema.json"]["$defs"]["provenance"][
            "properties"
        ]["source_id"]["maxLength"]
        == MAX_SOURCE_ID_CHARS
    )
    assert (
        schemas["model-extraction.schema.json"]["properties"]["items"]["items"][
            "properties"
        ]["provenance"]["items"]["properties"]["source_id"]["maxLength"]
        == MAX_SOURCE_ID_CHARS
    )
    assert (
        schemas["model-extraction-literal.schema.json"]["properties"]["items"][
            "items"
        ]["properties"]["source_ids"]["items"]["maxLength"]
        == MAX_SOURCE_ID_CHARS
    )
    assert (
        schemas["redaction-report.schema.json"]["properties"]["findings"][
            "items"
        ]["properties"]["source_id"]["maxLength"]
        == MAX_SOURCE_ID_CHARS
    )
