from __future__ import annotations

import copy

import pytest
from context_compiler import LocalAIConnector

from ctxc_openhands.deterministic import deterministic_bundle_copy
from ctxc_openhands.semantic import (
    DETERMINISTIC_COMPILE_DURATION_SECONDS,
    DETERMINISTIC_COMPILED_AT,
    semantic_result_digest,
)

EVENTS = [
    {
        "id": "event-1",
        "sequence": 0,
        "role": "assistant",
        "content": "MUST keep authentication enabled.",
    }
]


def _bundle():
    return LocalAIConnector().compile_memory(
        session_id="deterministic-session",
        events=EVENTS,
    )


def test_only_observational_fields_are_normalized_and_rehashed() -> None:
    original = _bundle()
    original_value = original.to_dict()

    normalized = deterministic_bundle_copy(original)
    value = normalized.to_dict()

    assert original.to_dict() == original_value
    assert value["artifact"]["compiled_at"] == DETERMINISTIC_COMPILED_AT
    assert (
        value["artifact"]["compiler_metadata"]["metrics"][
            "compile_duration_seconds"
        ]
        == DETERMINISTIC_COMPILE_DURATION_SECONDS
    )
    assert value["artifact"]["artifact_sha256"] != original_value["artifact"][
        "artifact_sha256"
    ]
    assert value["bindings"]["artifact_sha256"] == value["artifact"][
        "artifact_sha256"
    ]
    assert value["bundle_sha256"] != original_value["bundle_sha256"]
    assert semantic_result_digest(normalized) == semantic_result_digest(original)


def test_fresh_normalized_bundles_are_byte_identical_and_verify() -> None:
    first = deterministic_bundle_copy(_bundle())
    second = deterministic_bundle_copy(_bundle())

    replay = LocalAIConnector().verify_memory(
        first,
        session_id="deterministic-session",
        events=EVENTS,
    )

    assert first.to_dict() == second.to_dict()
    assert first.bundle_sha256 == second.bundle_sha256
    assert replay["passed"] is True


def test_tampered_bundle_is_rejected_before_normalization() -> None:
    tampered = copy.deepcopy(_bundle().to_dict())
    tampered["artifact"]["source_count"] += 1

    with pytest.raises(ValueError, match="digest mismatch"):
        deterministic_bundle_copy(tampered)
