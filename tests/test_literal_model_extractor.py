from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from context_compiler import (
    ContextCompiler,
    LiteralModelExtractor,
    MemoryKind,
    ModelExtractor,
    SourceRecord,
)
from context_compiler.extractors import (
    LITERAL_MODEL_SYSTEM_INSTRUCTIONS,
    MODEL_SYSTEM_INSTRUCTIONS,
)

LITERAL_SCHEMA_PATH = (
    Path(__file__).parents[1]
    / "schemas"
    / "model-extraction-literal.schema.json"
)


def source(
    content: str,
    *,
    id: str = "source-0",
    sequence: int = 0,
    role: str = "user",
    metadata: dict[str, Any] | None = None,
) -> SourceRecord:
    return SourceRecord.create(
        id=id,
        sequence=sequence,
        role=role,
        content=content,
        metadata=metadata,
    )


def candidate(
    text: str,
    *,
    kind: str = "constraint",
    source_ids: list[Any] | None = None,
    **values: Any,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "text": text,
        "source_ids": ["source-0"] if source_ids is None else source_ids,
        **values,
    }


def extract(
    response: Any,
    sources: list[SourceRecord],
    **kwargs: Any,
):
    return LiteralModelExtractor(
        lambda _prompt: response,
        **kwargs,
    ).extract(sources)


def test_unique_unicode_literal_derives_python_character_offsets() -> None:
    record = source("🙂 Intro.\nKeep café Δ unchanged.")
    literal = "Keep café Δ unchanged."
    captured_prompts: list[str] = []

    def complete(prompt: str) -> dict[str, Any]:
        captured_prompts.append(prompt)
        return {"items": [candidate(literal)]}

    result = LiteralModelExtractor(
        complete,
        model_id="local-test",
    ).extract([record])

    assert result.rejected == []
    assert len(result.items) == 1
    item = result.items[0]
    span = item.provenance[0]
    assert span.start == record.content.index(literal)
    assert span.end == span.start + len(literal)
    assert span.quote == literal
    assert span.validates({record.id: record})
    assert item.text == literal
    assert item.metadata["extractor"] == "model-json-literal-v1"
    assert result.metadata == {
        "extractor": "model-json-literal-v1",
        "model_id": "local-test",
        "max_response_chars": 1_000_000,
        "max_candidates": 10_000,
        "provenance_mode": "unique-exact-literal",
        "max_locator_work_chars": 10_000_000,
        "candidates": 1,
    }
    prompt = json.loads(captured_prompts[0])
    assert prompt["instructions"] == LITERAL_MODEL_SYSTEM_INSTRUCTIONS
    assert prompt["sources"] == [
        {
            "source_id": record.id,
            "sequence": record.sequence,
            "role": record.role,
            "content": record.content,
        }
    ]


def test_multiple_sources_derive_one_exact_span_per_source() -> None:
    literal = "src/main.py:42"
    first = source(
        literal,
        id="first",
        sequence=2,
        role="assistant",
    )
    second = source(
        f"Reference: {literal}",
        id="second",
        sequence=5,
        role="tool",
    )
    response = {
        "items": [
            candidate(
                literal,
                kind="exact_reference",
                source_ids=["first", "second"],
                exact=False,
            )
        ]
    }

    result = extract(response, [second, first])

    assert result.rejected == []
    assert len(result.items) == 1
    item = result.items[0]
    assert item.exact is True
    assert [
        (span.source_id, span.start, span.end, span.quote)
        for span in item.provenance
    ] == [
        ("first", 0, len(literal), literal),
        ("second", len("Reference: "), len("Reference: ") + len(literal), literal),
    ]
    assert item.metadata["source_sequence"] == 5
    assert item.metadata["source_role"] == "assistant,tool"


@pytest.mark.parametrize(
    ("content", "literal"),
    (
        ("Keep the API stable. Keep the API stable.", "Keep the API stable."),
        ("aaaa", "aaa"),
    ),
)
def test_repeated_and_overlapping_literals_fail_closed(
    content: str,
    literal: str,
) -> None:
    result = extract(
        {"items": [candidate(literal, kind="context")]},
        [source(content)],
    )

    assert result.items == []
    assert result.rejected[0]["reason"] == "invalid_candidate"
    assert "more than once" in result.rejected[0]["detail"]
    assert result.metadata["failure_reason"] == "all_candidates_rejected"


@pytest.mark.parametrize(
    "literal",
    (
        "Keep the api stable.",
        "Keep  the API stable.",
        "Keep the API stable!",
        "Keep the cafe\u0301 stable.",
    ),
)
def test_matching_does_not_fold_case_whitespace_punctuation_or_unicode(
    literal: str,
) -> None:
    record = source("Keep the API stable. Keep the café stable.")

    result = extract(
        {"items": [candidate(literal)]},
        [record],
    )

    assert result.items == []
    assert "does not occur verbatim" in result.rejected[0]["detail"]


@pytest.mark.parametrize(
    "source_ids",
    (
        "source-0",
        [],
        [""],
        [True],
        [None],
        ["source-0", "source-0"],
        ["missing"],
    ),
)
def test_source_ids_are_strict_nonempty_unique_known_strings(
    source_ids: Any,
) -> None:
    record = source("Keep the API stable.")
    response = {
        "items": [
            candidate(
                record.content,
                source_ids=source_ids,
            )
        ]
    }

    result = extract(response, [record])

    assert result.items == []
    assert result.rejected[0]["reason"] == "invalid_candidate"


@pytest.mark.parametrize(
    "forged",
    (
        {"provenance": []},
        {"start": 0},
        {"end": 20},
        {"source_id": "source-0"},
    ),
)
def test_offset_and_provenance_fields_are_forbidden(
    forged: dict[str, Any],
) -> None:
    record = source("Keep the API stable.")
    value = candidate(record.content)
    value.update(forged)

    result = extract({"items": [value]}, [record])

    assert result.items == []
    assert "unknown keys" in result.rejected[0]["detail"]


def test_exact_kind_remains_intrinsically_exact() -> None:
    literal = "fatal: café Δ"
    record = source(literal, role="tool")
    response = {
        "items": [
            candidate(
                literal,
                kind="exact_error",
                exact=False,
            )
        ]
    }

    result = extract(response, [record])

    assert result.rejected == []
    assert result.items[0].kind == MemoryKind.EXACT_ERROR
    assert result.items[0].exact is True
    assert result.items[0].provenance[0].quote == literal


def test_exact_kind_cannot_select_a_non_atomic_unique_subliteral() -> None:
    record = source("HTTP status 503: upstream unavailable", role="tool")

    result = extract(
        {
            "items": [
                candidate(
                    "status 503: upstream unavailable",
                    kind="exact_error",
                )
            ]
        },
        [record],
    )

    assert result.items == []
    assert "not an atomic clause" in result.rejected[0]["detail"]


def test_unique_substring_still_requires_a_complete_atomic_clause() -> None:
    record = source("Please keep the API stable during migration.")

    result = extract(
        {"items": [candidate("keep the API stable")]},
        [record],
    )

    assert result.items == []
    assert "not an atomic clause" in result.rejected[0]["detail"]


def test_derived_span_does_not_bypass_role_authority() -> None:
    record = source("Never publish credentials.", role="tool")

    result = extract(
        {"items": [candidate(record.content)]},
        [record],
    )

    assert result.items == []
    assert "authoritative role" in result.rejected[0]["detail"]


def test_derived_span_does_not_promote_uncertainty_to_fact() -> None:
    record = source("The root cause may be clock skew.")

    result = extract(
        {
            "items": [
                candidate(
                    record.content,
                    kind="confirmed_fact",
                )
            ]
        },
        [record],
    )

    assert result.items == []
    assert "uncertain source language" in result.rejected[0]["detail"]


def test_aggregate_locator_work_is_bounded_before_candidate_decoding() -> None:
    record = source("Keep the API stable.")

    result = extract(
        {"items": [candidate(record.content)]},
        [record],
        max_locator_work_chars=len(record.content) - 1,
    )

    assert result.items == []
    assert result.rejected == [{"reason": "locator_work_limit"}]
    assert result.metadata["degraded"] is True
    assert result.metadata["failure_reason"] == "locator_work_limit"
    assert result.metadata["candidates"] == 1


@pytest.mark.parametrize("value", (True, 1.5, "100", None))
def test_locator_work_limit_requires_a_positive_integer(value: Any) -> None:
    with pytest.raises(TypeError, match="must be an integer"):
        LiteralModelExtractor(
            lambda _prompt: {"items": []},
            max_locator_work_chars=value,  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match="must be positive"):
        LiteralModelExtractor(
            lambda _prompt: {"items": []},
            max_locator_work_chars=0,
        )


def test_strict_json_and_candidate_limits_are_inherited() -> None:
    record = source("Keep the API stable.")
    raw_candidate = json.dumps(candidate(record.content))
    duplicate = raw_candidate.replace(
        '"source_ids":',
        '"source_ids":["forged"],"source_ids":',
        1,
    )
    invalid_json = LiteralModelExtractor(
        lambda _prompt: '{"items":[' + duplicate + "]}",
    ).extract([record])
    too_many = LiteralModelExtractor(
        lambda _prompt: {
            "items": [
                candidate(record.content),
                candidate(record.content),
            ]
        },
        max_candidates=1,
    ).extract([record])

    assert invalid_json.items == []
    assert invalid_json.rejected[0]["reason"] == "invalid_json"
    assert too_many.items == []
    assert too_many.rejected == [{"reason": "too_many_candidates"}]


def test_invalid_literal_output_degrades_to_verified_deterministic_recovery() -> None:
    record = source("constraint: Keep the public API stable.")

    memory = ContextCompiler(
        extractor=LiteralModelExtractor(
            lambda _prompt: {
                "items": [
                    candidate(
                        "Paraphrased API requirement.",
                    )
                ]
            }
        )
    ).compile([record])

    assert memory.verification.passed is True
    assert any(
        item.kind == MemoryKind.CONSTRAINT
        and item.text == "Keep the public API stable."
        and "verifier-recovered" in item.tags
        for item in memory.items
    )
    assert memory.compiler_metadata["primary_rejections"][0]["reason"] == (
        "invalid_candidate"
    )
    assert any(
        issue.code == "primary_extractor_degraded"
        for issue in memory.verification.issues
    )


def test_valid_candidate_survives_alongside_invalid_candidate() -> None:
    record = source("Keep the API stable.")
    valid = candidate(record.content)
    invalid = deepcopy(valid)
    invalid["text"] = "Paraphrase."

    result = extract(
        {"items": [invalid, valid]},
        [record],
    )

    assert len(result.items) == 1
    assert result.items[0].text == record.content
    assert len(result.rejected) == 1
    assert "degraded" not in result.metadata


def test_existing_model_extractor_prompt_contract_is_unchanged() -> None:
    record = source("Keep the API stable.")
    prompts: list[str] = []

    ModelExtractor(
        lambda prompt: prompts.append(prompt) or {"items": []}
    ).extract([record])

    payload = json.loads(prompts[0])
    assert payload["instructions"] == MODEL_SYSTEM_INSTRUCTIONS
    assert '"provenance"' in MODEL_SYSTEM_INSTRUCTIONS
    assert '"source_ids"' not in MODEL_SYSTEM_INSTRUCTIONS


def test_literal_schema_matches_the_strict_runtime_shape() -> None:
    schema = json.loads(LITERAL_SCHEMA_PATH.read_text(encoding="utf-8"))
    item_schema = schema["properties"]["items"]["items"]

    assert schema["additionalProperties"] is False
    assert item_schema["required"] == ["kind", "text", "source_ids"]
    assert item_schema["additionalProperties"] is False
    assert set(item_schema["properties"]) == {
        "kind",
        "text",
        "priority",
        "confidence",
        "exact",
        "tags",
        "source_ids",
    }
    assert item_schema["properties"]["source_ids"] == {
        "type": "array",
        "minItems": 1,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1},
    }
