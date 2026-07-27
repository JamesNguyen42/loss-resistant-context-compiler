from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files

import pytest

from ctxc_openhands.tokenizer import (
    CANONICAL_UTF8_BYTE_TOKENIZER_ID,
    CANONICAL_UTF8_BYTE_TOKENIZER_SCOPE,
    CANONICAL_UTF8_BYTE_VECTOR_RESOURCE_FILE_SHA256,
    CANONICAL_UTF8_BYTE_VECTOR_RESOURCE_FILENAME,
    CANONICAL_UTF8_BYTE_VECTOR_RESOURCE_PAYLOAD_SHA256,
    CANONICAL_UTF8_BYTE_VECTOR_SHA256,
    CANONICAL_UTF8_BYTE_VECTORS,
    TOKENIZER_VECTOR_SCHEMA,
    CanonicalUtf8ByteTokenizer,
    TokenizerVector,
    UnsupportedTokenizerError,
    require_supported_exact_tokenizer,
    validate_tokenizer_vectors,
)


def test_canonical_utf8_byte_vectors_replay_exactly() -> None:
    tokenizer = CanonicalUtf8ByteTokenizer()

    report = validate_tokenizer_vectors(tokenizer)

    assert report.passed
    assert report.issues == ()
    assert report.tokenizer_identity == CANONICAL_UTF8_BYTE_TOKENIZER_ID
    assert report.expected_vector_sha256 == CANONICAL_UTF8_BYTE_VECTOR_SHA256
    assert report.actual_vector_sha256 == CANONICAL_UTF8_BYTE_VECTOR_SHA256
    assert report.vector_count == len(CANONICAL_UTF8_BYTE_VECTORS)
    assert tokenizer.exact_scope == CANONICAL_UTF8_BYTE_TOKENIZER_SCOPE
    assert tokenizer.count_text("é🙂") == 6
    assert tokenizer.count_parts(("é", "🙂")) == (2, 4)

def test_packaged_vectors_are_byte_and_self_hash_bound_to_offline_scope() -> None:
    resource = (
        files("ctxc_openhands")
        .joinpath("data")
        .joinpath(CANONICAL_UTF8_BYTE_VECTOR_RESOURCE_FILENAME)
    )
    raw = resource.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        CANONICAL_UTF8_BYTE_VECTOR_RESOURCE_FILE_SHA256
    )

    value = json.loads(raw)
    assert set(value) == {
        "schema",
        "kind",
        "identity",
        "exact_scope",
        "accounting_mode",
        "claim_boundaries",
        "vectors",
        "vector_sha256",
        "integrity",
    }
    integrity = value.pop("integrity")
    canonical_payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert integrity == {
        "payload_canonicalization": (
            "UTF-8 JSON, sorted keys, separators comma/colon, "
            "ensure_ascii=false, integrity omitted"
        ),
        "payload_sha256": CANONICAL_UTF8_BYTE_VECTOR_RESOURCE_PAYLOAD_SHA256,
    }
    assert hashlib.sha256(canonical_payload).hexdigest() == (
        CANONICAL_UTF8_BYTE_VECTOR_RESOURCE_PAYLOAD_SHA256
    )

    assert value["schema"] == TOKENIZER_VECTOR_SCHEMA
    assert value["kind"] == "ctxc.openhands.tokenizer-vector-set"
    assert value["identity"] == CANONICAL_UTF8_BYTE_TOKENIZER_ID
    assert value["exact_scope"] == CANONICAL_UTF8_BYTE_TOKENIZER_SCOPE
    assert value["accounting_mode"] == "exact"
    assert value["claim_boundaries"] == {
        "offline_fake_runtime_only": True,
        "real_model_tokenizer": False,
        "provider_wire_exactness": False,
        "semantic_completeness": False,
    }
    assert value["vectors"] == [
        vector.to_dict() for vector in CANONICAL_UTF8_BYTE_VECTORS
    ]
    vector_payload = {
        "schema": value["schema"],
        "vectors": value["vectors"],
    }
    vector_bytes = json.dumps(
        vector_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert hashlib.sha256(vector_bytes).hexdigest() == value["vector_sha256"]
    assert value["vector_sha256"] == CANONICAL_UTF8_BYTE_VECTOR_SHA256
    assert all(
        vector["expected_tokens"] == len(vector["text"].encode("utf-8"))
        for vector in value["vectors"]
    )


def test_modified_vector_cannot_replay_under_pinned_digest() -> None:
    tokenizer = CanonicalUtf8ByteTokenizer()
    modified = list(CANONICAL_UTF8_BYTE_VECTORS)
    modified[0] = TokenizerVector("", 1)

    report = validate_tokenizer_vectors(tokenizer, modified)

    assert not report.passed
    assert any("count mismatch" in issue for issue in report.issues)
    assert any("vector digest mismatch" in issue for issue in report.issues)


@dataclass
class EstimatedTokenizer:
    identity: str = "estimated"
    exact_scope: str = "none"
    accounting_mode: str = "estimated"
    vector_sha256: str = "0" * 64

    def count_text(self, text: str) -> int:
        return len(text) // 4

    def count_parts(self, parts: tuple[str, ...]) -> tuple[int, ...]:
        return tuple(len(part) // 4 for part in parts)


def test_estimated_and_unregistered_tokenizers_are_rejected() -> None:
    estimated = EstimatedTokenizer()

    with pytest.raises(UnsupportedTokenizerError, match="explicitly supported exact"):
        require_supported_exact_tokenizer(estimated)

    estimated.accounting_mode = "exact"
    with pytest.raises(UnsupportedTokenizerError, match="not supported"):
        require_supported_exact_tokenizer(estimated)


@pytest.mark.parametrize("bad_parts", ["text", b"bytes", [object()]])
def test_tokenizer_rejects_non_string_part_sequences(bad_parts: object) -> None:
    tokenizer = CanonicalUtf8ByteTokenizer()

    with pytest.raises(TypeError):
        tokenizer.count_parts(bad_parts)  # type: ignore[arg-type]
