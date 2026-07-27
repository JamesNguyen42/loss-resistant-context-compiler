"""Byte-stable integration copy of an already valid core ContextBundle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from context_compiler import ContextBundle

from .semantic import (
    DETERMINISTIC_COMPILE_DURATION_SECONDS,
    DETERMINISTIC_COMPILED_AT,
    semantic_result_digest,
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def deterministic_bundle_copy(
    bundle: ContextBundle | Mapping[str, Any],
) -> ContextBundle:
    """Return a byte-stable copy after validating the original self-hashes.

    Only the wall-clock compilation timestamp and measured duration are
    canonicalized. Every affected artifact, binding, and bundle digest is then
    recomputed by the public ``ContextBundle`` constructor. The caller must
    independently replay-verify the returned copy before committing it.
    """

    if isinstance(bundle, ContextBundle):
        raw = bundle.to_dict()
    elif isinstance(bundle, Mapping):
        raw = ContextBundle.from_dict(dict(bundle)).to_dict()
    else:
        raise TypeError("bundle must be a ContextBundle or mapping")
    semantic_before = semantic_result_digest(raw)
    raw.pop("bundle_sha256")
    artifact = raw["artifact"]
    compiled_at = artifact.get("compiled_at")
    if not isinstance(compiled_at, str) or not compiled_at:
        raise ValueError("bundle artifact compiled_at must be a non-empty string")
    metadata = artifact.get("compiler_metadata")
    if not isinstance(metadata, dict):
        raise ValueError("bundle artifact is missing compiler metadata")
    metrics = metadata.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("bundle artifact is missing compilation metrics")
    duration = metrics.get("compile_duration_seconds")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise ValueError("compile_duration_seconds must be a number")

    artifact["compiled_at"] = DETERMINISTIC_COMPILED_AT
    metrics["compile_duration_seconds"] = DETERMINISTIC_COMPILE_DURATION_SECONDS
    artifact["artifact_sha256"] = hashlib.sha256(
        _canonical_json(
            {key: value for key, value in artifact.items() if key != "artifact_sha256"}
        ).encode("utf-8")
    ).hexdigest()
    raw["bindings"]["artifact_sha256"] = artifact["artifact_sha256"]
    normalized = ContextBundle(
        schema=raw["schema"],
        protocol_version=raw["protocol_version"],
        artifact=artifact,
        trusted_memory=raw["trusted_memory"],
        bindings=raw["bindings"],
        certificate=raw["certificate"],
        token_accounting=raw["token_accounting"],
    )
    if semantic_result_digest(normalized) != semantic_before:
        raise AssertionError("observational normalization changed verified semantics")
    return normalized


__all__ = ["deterministic_bundle_copy"]
