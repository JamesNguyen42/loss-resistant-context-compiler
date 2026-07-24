from __future__ import annotations

import pytest

from context_compiler import ContextCompiler, SourceRecord
from context_compiler.io import verify_artifact_dict


def word_counter(text: str) -> int:
    return len(text.split())


def test_named_custom_counter_round_trips_independent_verification() -> None:
    sources = [
        SourceRecord.create(
            sequence=0,
            role="user",
            content="Fix authentication timeout. Do not change the public API.",
        )
    ]
    memory = ContextCompiler(
        token_counter=word_counter,
        token_counter_id="words-v1",
    ).compile(sources)
    artifact = memory.to_dict()

    verified = verify_artifact_dict(
        artifact,
        sources,
        token_counter=word_counter,
        token_counter_id="words-v1",
    )
    assert verified["passed"] is True
    assert verified["compression_valid"] is True


def test_custom_counter_requires_matching_callback_and_id() -> None:
    sources = [SourceRecord.create(sequence=0, role="user", content="Fix timeout.")]
    artifact = ContextCompiler(
        token_counter=word_counter,
        token_counter_id="words-v1",
    ).compile(sources).to_dict()

    missing = verify_artifact_dict(artifact, sources)
    wrong_id = verify_artifact_dict(
        artifact,
        sources,
        token_counter=word_counter,
        token_counter_id="words-v2",
    )
    assert missing["passed"] is False
    assert wrong_id["passed"] is False
    assert "unverifiable_token_counter" in {issue["code"] for issue in missing["issues"]}
    assert "unverifiable_token_counter" in {issue["code"] for issue in wrong_id["issues"]}


def test_custom_counter_result_is_strict() -> None:
    source = SourceRecord.create(sequence=0, role="user", content="Fix timeout.")
    with pytest.raises(TypeError, match="must return an integer"):
        ContextCompiler(token_counter=lambda _text: True).compile([source])
