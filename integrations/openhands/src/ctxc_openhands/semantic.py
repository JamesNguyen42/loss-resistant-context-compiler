"""Stable semantic projection for independently verified ContextBundles.

Fresh core compilations intentionally record a wall-clock timestamp and
measured duration.  This module excludes only those observational values and
the self-hashes that necessarily bind them.  The isolated integration may make
a validated copy with only those observations canonicalized; it never mutates
the public core artifact or silently normalizes a semantic field.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from context_compiler import ContextBundle

SEMANTIC_RESULT_SCHEMA = "ctxc-openhands-semantic-result-0.1"
DETERMINISTIC_COMPILED_AT = "1970-01-01T00:00:00+00:00"
DETERMINISTIC_COMPILE_DURATION_SECONDS = 0.0
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _bundle_mapping(bundle: ContextBundle | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(bundle, ContextBundle):
        raw = bundle.to_dict()
    elif isinstance(bundle, Mapping):
        raw = copy.deepcopy(dict(bundle))
    else:
        raise TypeError("bundle must be a ContextBundle or mapping")
    # Validation rechecks the core self-hash and all fixed bundle fields before
    # any observational values are removed from the integration projection.
    return ContextBundle.from_dict(raw).to_dict()


def semantic_result_projection(
    bundle: ContextBundle | Mapping[str, Any],
) -> dict[str, Any]:
    """Return a canonical semantic projection of a valid ContextBundle."""

    raw = _bundle_mapping(bundle)
    raw.pop("bundle_sha256")

    artifact = raw["artifact"]
    artifact.pop("artifact_sha256")
    artifact.pop("compiled_at")
    metadata = artifact["compiler_metadata"]
    metrics = metadata.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("bundle artifact is missing compilation metrics")
    duration = metrics.pop("compile_duration_seconds", None)
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise ValueError("compile_duration_seconds must be a number")

    bindings = raw["bindings"]
    bound_artifact = bindings.pop("artifact_sha256", None)
    if not isinstance(bound_artifact, str) or _SHA256.fullmatch(bound_artifact) is None:
        raise ValueError("bundle binding artifact_sha256 is invalid")

    return {
        "schema": SEMANTIC_RESULT_SCHEMA,
        "bundle": raw,
        "excluded_observational_fields": [
            "artifact.compiled_at",
            "artifact.compiler_metadata.metrics.compile_duration_seconds",
        ],
        "excluded_derived_integrity_fields": [
            "artifact.artifact_sha256",
            "bindings.artifact_sha256",
            "bundle_sha256",
        ],
    }


def semantic_result_digest(
    bundle: ContextBundle | Mapping[str, Any],
) -> str:
    """Hash the stable projection without changing any public artifact digest."""

    projection = semantic_result_projection(bundle)
    return hashlib.sha256(_canonical_json(projection).encode("utf-8")).hexdigest()
