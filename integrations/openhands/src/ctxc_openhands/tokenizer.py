"""Exact-tokenizer contract and offline fake-runtime tokenizer.

The tokenizer shipped here is intentionally *not* a model tokenizer.  Its
exactness claim is limited to the canonical UTF-8 byte protocol used by the
offline fake runtime.  A real OpenHands/model adapter must provide and bind its
own exact tokenizer before it can produce an exact final-request ledger.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

TOKENIZER_VECTOR_SCHEMA = "ctxc-openhands-tokenizer-vectors-0.1"
CANONICAL_UTF8_BYTE_TOKENIZER_ID = "ctxc-openhands/offline-canonical-utf8-byte@1"
CANONICAL_UTF8_BYTE_TOKENIZER_SCOPE = "offline-fake-runtime-canonical-utf8-only"
CANONICAL_UTF8_BYTE_VECTOR_RESOURCE_FILENAME = (
    "canonical-utf8-byte-tokenizer-vectors.json"
)
CANONICAL_UTF8_BYTE_VECTOR_RESOURCE_PAYLOAD_SHA256 = (
    "583e9abb86005fc1901b0c965ec12155964af54dfdc39f583228e2feefa93b60"
)
CANONICAL_UTF8_BYTE_VECTOR_RESOURCE_FILE_SHA256 = (
    "663fd71947b47dfa142e80bf099e5cd16d9b80dd5620ffa28e2633c024b65e39"
)



class TokenizerError(ValueError):
    """Base error for exact-tokenizer validation."""


class UnsupportedTokenizerError(TokenizerError):
    """Raised when an exact ledger is requested with an unsupported tokenizer."""


@dataclass(frozen=True, slots=True)
class TokenizerVector:
    """One deterministic exact-tokenizer replay vector."""

    text: str
    expected_tokens: int

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("tokenizer vector text must be a string")
        if isinstance(self.expected_tokens, bool) or not isinstance(
            self.expected_tokens, int
        ):
            raise TypeError("tokenizer vector expected_tokens must be an integer")
        if self.expected_tokens < 0:
            raise ValueError("tokenizer vector expected_tokens must be non-negative")

    def to_dict(self) -> dict[str, object]:
        return {"text": self.text, "expected_tokens": self.expected_tokens}


@dataclass(frozen=True, slots=True)
class TokenizerVectorReport:
    """Structured result of replaying tokenizer vectors."""

    passed: bool
    tokenizer_identity: str
    expected_vector_sha256: str
    actual_vector_sha256: str
    vector_count: int
    issues: tuple[str, ...] = ()


@runtime_checkable
class ExactTokenizer(Protocol):
    """Protocol required for exact final-request accounting.

    ``count_parts`` is part of the contract because arbitrary model tokenizers
    need an explicit, exact attribution rule for component boundaries.  It must
    return non-negative counts whose sum equals ``count_text("".join(parts))``.
    """

    @property
    def identity(self) -> str:
        """Stable tokenizer and revision identity."""

    @property
    def exact_scope(self) -> str:
        """Narrow runtime/protocol scope in which counts are exact."""

    @property
    def accounting_mode(self) -> str:
        """Return exactly ``"exact"`` for an exact tokenizer."""

    @property
    def vector_sha256(self) -> str:
        """Digest of the pinned exact replay vectors."""

    def count_text(self, text: str) -> int:
        """Count one complete immutable transport string."""

    def count_parts(self, parts: Sequence[str]) -> tuple[int, ...]:
        """Attribute exact counts to ordered transport parts."""


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _vector_digest(vectors: Sequence[TokenizerVector]) -> str:
    payload = {
        "schema": TOKENIZER_VECTOR_SCHEMA,
        "vectors": [vector.to_dict() for vector in vectors],
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


CANONICAL_UTF8_BYTE_VECTORS = (
    TokenizerVector("", 0),
    TokenizerVector("a", 1),
    TokenizerVector("ASCII request", 13),
    TokenizerVector("\n", 1),
    TokenizerVector("é", 2),
    TokenizerVector("漢字", 6),
    TokenizerVector("🙂", 4),
    TokenizerVector('{"a":1}', 7),
    TokenizerVector("e\u0301", 3),
    TokenizerVector("\x00", 1),
)
CANONICAL_UTF8_BYTE_VECTOR_SHA256 = _vector_digest(CANONICAL_UTF8_BYTE_VECTORS)


@dataclass(frozen=True, slots=True)
class CanonicalUtf8ByteTokenizer:
    """Exact tokenizer for the offline canonical-UTF-8-byte fake runtime only."""

    @property
    def identity(self) -> str:
        return CANONICAL_UTF8_BYTE_TOKENIZER_ID

    @property
    def exact_scope(self) -> str:
        return CANONICAL_UTF8_BYTE_TOKENIZER_SCOPE

    @property
    def accounting_mode(self) -> str:
        return "exact"

    @property
    def vector_sha256(self) -> str:
        return CANONICAL_UTF8_BYTE_VECTOR_SHA256

    def count_text(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("tokenizer input must be a string")
        return len(text.encode("utf-8"))

    def count_parts(self, parts: Sequence[str]) -> tuple[int, ...]:
        if isinstance(parts, (str, bytes, bytearray)) or not isinstance(parts, Sequence):
            raise TypeError("tokenizer parts must be a sequence of strings")
        counts: list[int] = []
        for part in parts:
            if not isinstance(part, str):
                raise TypeError("tokenizer parts must contain only strings")
            counts.append(len(part.encode("utf-8")))
        return tuple(counts)


def require_supported_exact_tokenizer(tokenizer: object) -> ExactTokenizer:
    """Return the tokenizer after fail-closed exactness/support validation.

    The offline byte tokenizer is the only implementation shipped and accepted
    in this package revision.  Merely exposing an ``accounting_mode`` attribute
    is not sufficient to promote an arbitrary or estimated tokenizer to exact.
    """

    if type(tokenizer) is not CanonicalUtf8ByteTokenizer:
        mode = getattr(tokenizer, "accounting_mode", None)
        if mode != "exact":
            raise UnsupportedTokenizerError(
                "exact ledger requires an explicitly supported exact tokenizer; "
                f"received accounting_mode={mode!r}"
            )
        raise UnsupportedTokenizerError(
            "tokenizer is not supported for exact ledgers in this package revision"
        )
    if tokenizer.identity != CANONICAL_UTF8_BYTE_TOKENIZER_ID:
        raise UnsupportedTokenizerError("unsupported tokenizer identity")
    if tokenizer.exact_scope != CANONICAL_UTF8_BYTE_TOKENIZER_SCOPE:
        raise UnsupportedTokenizerError("unsupported tokenizer exactness scope")
    if tokenizer.vector_sha256 != CANONICAL_UTF8_BYTE_VECTOR_SHA256:
        raise UnsupportedTokenizerError("unsupported tokenizer vector digest")
    return tokenizer


def validate_tokenizer_vectors(
    tokenizer: object,
    vectors: Sequence[TokenizerVector] = CANONICAL_UTF8_BYTE_VECTORS,
) -> TokenizerVectorReport:
    """Replay exact vectors without converting a failed replay into success."""

    issues: list[str] = []
    identity = getattr(tokenizer, "identity", "<unsupported>")
    try:
        exact = require_supported_exact_tokenizer(tokenizer)
    except (TypeError, ValueError) as exc:
        return TokenizerVectorReport(
            passed=False,
            tokenizer_identity=str(identity),
            expected_vector_sha256=CANONICAL_UTF8_BYTE_VECTOR_SHA256,
            actual_vector_sha256="",
            vector_count=0,
            issues=(str(exc),),
        )

    if isinstance(vectors, (str, bytes, bytearray)) or not isinstance(vectors, Sequence):
        return TokenizerVectorReport(
            passed=False,
            tokenizer_identity=exact.identity,
            expected_vector_sha256=exact.vector_sha256,
            actual_vector_sha256="",
            vector_count=0,
            issues=("vectors must be a sequence of TokenizerVector values",),
        )

    normalized: list[TokenizerVector] = []
    for index, vector in enumerate(vectors):
        if not isinstance(vector, TokenizerVector):
            issues.append(f"vector {index} is not a TokenizerVector")
            continue
        normalized.append(vector)
        try:
            actual = exact.count_text(vector.text)
        except (TypeError, ValueError) as exc:
            issues.append(f"vector {index} failed: {exc}")
            continue
        if isinstance(actual, bool) or not isinstance(actual, int) or actual < 0:
            issues.append(f"vector {index} returned an invalid token count")
        elif actual != vector.expected_tokens:
            issues.append(
                f"vector {index} count mismatch: expected "
                f"{vector.expected_tokens}, found {actual}"
            )

    actual_digest = _vector_digest(normalized)
    if actual_digest != exact.vector_sha256:
        issues.append(
            "tokenizer vector digest mismatch: "
            f"expected {exact.vector_sha256}, found {actual_digest}"
        )
    return TokenizerVectorReport(
        passed=not issues,
        tokenizer_identity=exact.identity,
        expected_vector_sha256=exact.vector_sha256,
        actual_vector_sha256=actual_digest,
        vector_count=len(normalized),
        issues=tuple(issues),
    )
