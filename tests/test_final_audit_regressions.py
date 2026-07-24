from __future__ import annotations

import hashlib
import json

import pytest

from context_compiler import ContextCompiler, SourceRecord
from context_compiler.extractors import ExtractionResult
from context_compiler.io import verify_artifact_dict


def resign(artifact: dict[str, object]) -> None:
    unsigned = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    artifact["artifact_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()


def test_custom_counter_target_boundary_uses_unrounded_ratio() -> None:
    def boundary_counter(text: str) -> int:
        return 100_000 if text.startswith("<typed_memory") else 499_999

    sources = [SourceRecord.create(sequence=0, role="user", content="Fix timeout.")]
    memory = ContextCompiler(
        token_counter=boundary_counter,
        token_counter_id="boundary-v1",
    ).compile(sources)
    assert memory.compression.compression_ratio == 5.0
    assert memory.compression.target_met is False

    verified = verify_artifact_dict(
        memory.to_dict(),
        sources,
        token_counter=boundary_counter,
        token_counter_id="boundary-v1",
    )
    assert verified["passed"] is True


def test_recovered_item_count_is_replayed_from_typed_ledger() -> None:
    class EmptyExtractor:
        name = "empty"

        def extract(self, _sources: list[SourceRecord]) -> ExtractionResult:
            return ExtractionResult()

    sources = [
        SourceRecord.create(
            sequence=0,
            role="user",
            content="Constraint: Do not change the public API.",
        )
    ]
    artifact = ContextCompiler(extractor=EmptyExtractor()).compile(sources).to_dict()
    assert artifact["verification"]["recovered_items"] == 1
    artifact["verification"]["recovered_items"] = 0
    resign(artifact)

    verified = verify_artifact_dict(artifact, sources)
    assert verified["passed"] is False
    assert "embedded_verification_mismatch" in {
        issue["code"] for issue in verified["issues"]
    }


def test_frozen_nested_metadata_rejects_dictionary_union() -> None:
    source = SourceRecord.create(
        sequence=0,
        role="tool",
        content="output",
        metadata={"nested": {"origin": "api"}},
    )
    nested = source.metadata["nested"]
    with pytest.raises(TypeError, match="immutable"):
        nested |= {"changed": True}
    assert source.to_dict()["metadata"] == {"nested": {"origin": "api"}}
    source.ensure_integrity()


def test_invalid_sequence_metadata_fails_artifact_verification_without_crash() -> None:
    sources = [
        SourceRecord.create(
            sequence=0,
            role="user",
            content="Constraint: Database must be MySQL.",
        ),
        SourceRecord.create(
            sequence=1,
            role="user",
            content="Correction: database must be PostgreSQL.",
        ),
    ]
    artifact = ContextCompiler().compile(sources).to_dict()
    for item in artifact["items"]:
        if item["metadata"].get("source_sequence") == 1:
            item["metadata"]["source_sequence"] = "bad"
    resign(artifact)

    verified = verify_artifact_dict(artifact, sources)
    assert verified["passed"] is False
    assert "source_sequence_metadata_mismatch" in {
        issue["code"] for issue in verified["issues"]
    }


def test_explicit_empty_source_id_is_not_treated_as_omitted() -> None:
    with pytest.raises(ValueError, match="null or a non-empty string"):
        SourceRecord.from_dict({"id": "", "role": "user", "content": "Fix it."})
