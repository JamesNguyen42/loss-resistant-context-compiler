from __future__ import annotations

import copy

import pytest
from context_compiler import LocalAIConnector

from ctxc_openhands.semantic import (
    SEMANTIC_RESULT_SCHEMA,
    semantic_result_digest,
    semantic_result_projection,
)


def _bundle() -> dict:
    connector = LocalAIConnector()
    return connector.compile_memory(
        session_id="semantic-test",
        events=[
            {
                "id": "event-1",
                "sequence": 0,
                "role": "user",
                "content": "Keep PostgreSQL authentication enabled.",
            }
        ],
    ).to_dict()


def test_fresh_compilations_have_one_semantic_digest() -> None:
    first = _bundle()
    second = _bundle()

    assert first["bundle_sha256"] != second["bundle_sha256"]
    assert semantic_result_digest(first) == semantic_result_digest(second)


def test_projection_names_every_excluded_field() -> None:
    projection = semantic_result_projection(_bundle())

    assert projection["schema"] == SEMANTIC_RESULT_SCHEMA
    assert projection["excluded_observational_fields"] == [
        "artifact.compiled_at",
        "artifact.compiler_metadata.metrics.compile_duration_seconds",
    ]
    assert projection["excluded_derived_integrity_fields"] == [
        "artifact.artifact_sha256",
        "bindings.artifact_sha256",
        "bundle_sha256",
    ]


def test_semantic_mutation_is_not_normalized_away() -> None:
    original = _bundle()
    mutated = copy.deepcopy(original)
    mutated["artifact"]["source_count"] = 2

    with pytest.raises(ValueError, match="digest mismatch"):
        semantic_result_digest(mutated)


def test_invalid_bundle_is_rejected_before_projection() -> None:
    with pytest.raises((TypeError, ValueError)):
        semantic_result_projection({"schema": "not-a-bundle"})
