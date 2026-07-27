from __future__ import annotations

import hashlib
import json
import random
from typing import Any

import pytest

from context_compiler import (
    ContextCompiler,
    MemoryKind,
    ModelExtractor,
    ProvenanceSpan,
    SourceRecord,
)
from context_compiler.io import verify_artifact_dict

_JSON_LINE_SEPARATORS = ("\u0085", "\u2028", "\u2029")
_PYTHON_LINE_BOUNDARIES = (
    "\n",
    "\r\n",
    "\r",
    "\v",
    "\f",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x85",
    "\u2028",
    "\u2029",
)
_UNICODE_MARKERS = (
    "café",
    "東京",
    "🧭",
    "e\u0301",
    "Δοκιμή",
    "مرحبا",
)
_ID_ALPHABET = (
    "abcXYZ019 _-:/\\\"'<>&"
    "\x00\n\r\t"
    "\u0085\u2028\u2029"
    "é東京🧭\u0301"
)


def _random_source_id(seed: int) -> str:
    rng = random.Random(seed)
    separator = _JSON_LINE_SEPARATORS[seed % len(_JSON_LINE_SEPARATORS)]
    prefix = "".join(rng.choice(_ID_ALPHABET) for _ in range(12))
    suffix = "".join(rng.choice(_ID_ALPHABET) for _ in range(12))
    return f"{prefix}{separator}{suffix}"


def _assert_all_provenance_is_exact(
    memory: Any,
    sources: list[SourceRecord],
) -> None:
    source_map = {source.id: source for source in sources}
    for item in memory.items:
        for span in item.provenance:
            source = source_map[span.source_id]
            assert 0 <= span.start <= span.end <= len(source.content)
            assert span.quote == source.content[span.start : span.end]
            assert span.quote_sha256 == hashlib.sha256(
                span.quote.encode("utf-8")
            ).hexdigest()
            assert span.validates(source_map)


@pytest.mark.parametrize("seed", range(16), ids=lambda seed: f"seed-{seed:02d}")
def test_arbitrary_unicode_source_ids_round_trip_and_keep_json_lines(
    seed: int,
) -> None:
    rng = random.Random(seed)
    marker = rng.choice(_UNICODE_MARKERS)
    source_id = _random_source_id(seed)
    clause = f"Preserve marker {marker} exactly"
    content = f"ignored prelude {seed}\n\tconstraint \t: \t{clause}   "
    source = SourceRecord.create(
        id=source_id,
        sequence=0,
        role="user",
        content=content,
    )

    memory = ContextCompiler().compile([source])

    assert memory.verification.passed
    assert any(
        item.kind == MemoryKind.CONSTRAINT and item.text == clause
        for item in memory.items
    )
    _assert_all_provenance_is_exact(memory, [source])
    assert verify_artifact_dict(memory.to_dict(), [source])["passed"]

    prompt = memory.to_prompt()
    assert all(separator not in prompt for separator in _JSON_LINE_SEPARATORS)
    prompt_items = [
        json.loads(line[2:])
        for line in prompt.splitlines()
        if line.startswith("- ")
    ]
    assert len(prompt_items) == len(memory.active_items)
    assert any(
        any(reference.startswith(f"{source_id}:") for reference in item["provenance"])
        for item in prompt_items
    )


@pytest.mark.parametrize(
    "separator",
    _PYTHON_LINE_BOUNDARIES,
    ids=lambda value: value.encode("unicode_escape").decode("ascii"),
)
def test_every_python_line_boundary_preserves_atomic_bullet_provenance(
    separator: str,
) -> None:
    first = "must preserve café 🧭"
    second = "do not remove 東京 marker"
    content = (
        f"\tconstraints :{separator}"
        f"\t- {first}{separator}"
        f"  * {second}"
    )
    source = SourceRecord.create(
        id=f"line-boundary-{separator.encode('unicode_escape').decode('ascii')}",
        sequence=0,
        role="user",
        content=content,
    )

    memory = ContextCompiler().compile([source])
    constraint_texts = {
        item.text for item in memory.items if item.kind == MemoryKind.CONSTRAINT
    }

    assert memory.verification.passed
    assert {first, second} <= constraint_texts
    _assert_all_provenance_is_exact(memory, [source])
    assert verify_artifact_dict(memory.to_dict(), [source])["passed"]


@pytest.mark.parametrize("seed", range(16), ids=lambda seed: f"seed-{seed:02d}")
def test_generated_clause_boundaries_retain_each_exact_atom(seed: int) -> None:
    rng = random.Random(seed)
    left = f"must preserve {rng.choice(_UNICODE_MARKERS)} marker-{seed}"
    right = f"do not remove {rng.choice(_UNICODE_MARKERS)} guard-{seed}"
    boundary_kind = seed % 3
    if boundary_kind == 0:
        boundary = f"{' ' * rng.randrange(3)};{' ' * rng.randrange(3)}"
        expected = (left, right)
    elif boundary_kind == 1:
        boundary = f".{' ' * (1 + rng.randrange(3))}"
        expected = (left + ".", right)
    else:
        boundary = f"{' ' * (1 + rng.randrange(2))}and{' ' * (1 + rng.randrange(2))}"
        expected = (left, right)
    content = f"noise prefix {seed}\nconstraint:\t{left}{boundary}{right}  "
    source = SourceRecord.create(
        id=f"clause-{seed}",
        sequence=0,
        role="user",
        content=content,
    )

    memory = ContextCompiler().compile([source])
    constraints = [
        item for item in memory.items if item.kind == MemoryKind.CONSTRAINT
    ]
    by_text = {item.text: item for item in constraints}

    assert memory.verification.passed
    assert set(expected) <= set(by_text)
    for text in expected:
        item = by_text[text]
        assert len(item.provenance) == 1
        span = item.provenance[0]
        assert span.start == content.index(text)
        assert span.end == span.start + len(text)
    _assert_all_provenance_is_exact(memory, [source])


@pytest.mark.parametrize("seed", range(16), ids=lambda seed: f"seed-{seed:02d}")
def test_generated_model_offsets_use_characters_and_reject_shifted_spans(
    seed: int,
) -> None:
    rng = random.Random(seed)
    marker = rng.choice(_UNICODE_MARKERS)
    prelude = f"🧪 prélude 東京 {seed}\n"
    clause = f"must preserve {marker} offset-{seed}"
    content = prelude + clause
    source = SourceRecord.create(
        id=_random_source_id(seed),
        sequence=0,
        role="user",
        content=content,
    )
    start = content.index(clause)
    end = start + len(clause)

    def candidate(candidate_start: object, candidate_end: object) -> dict[str, Any]:
        return {
            "kind": "constraint",
            "text": clause,
            "provenance": [
                {
                    "source_id": source.id,
                    "start": candidate_start,
                    "end": candidate_end,
                }
            ],
        }

    accepted = ModelExtractor(
        lambda _: {"items": [candidate(start, end)]}
    ).extract([source])
    byte_start = len(content[:start].encode("utf-8"))
    byte_end = len(content[:end].encode("utf-8"))
    invalid = (
        candidate(start + 1, end),
        candidate(start, end - 1),
        candidate(byte_start, byte_end),
        candidate(True, end),
    )
    rejected = ModelExtractor(
        lambda _: {"items": list(invalid)}
    ).extract([source])

    assert len(accepted.items) == 1
    assert accepted.rejected == []
    span = accepted.items[0].provenance[0]
    assert (span.start, span.end, span.quote) == (start, end, clause)
    assert rejected.items == []
    assert len(rejected.rejected) == len(invalid)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"source_id": ""}, "source_id"),
        ({"source_id": None}, "source_id"),
        ({"start": True}, "start must be an integer"),
        ({"start": 0.0}, "start must be an integer"),
        ({"start": "0"}, "start must be an integer"),
        ({"end": False}, "end must be an integer"),
        ({"end": 1.0}, "end must be an integer"),
        ({"end": "1"}, "end must be an integer"),
        ({"quote": None}, "quote must be a string"),
        ({"quote_sha256": None}, "quote_sha256 must be a string"),
    ],
)
def test_direct_provenance_span_rejects_noncanonical_types(
    kwargs: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "source_id": "source",
        "start": 0,
        "end": 1,
        "quote": "x",
        "quote_sha256": "",
    }
    values.update(kwargs)

    with pytest.raises(TypeError, match=message):
        ProvenanceSpan(**values)  # type: ignore[arg-type]
