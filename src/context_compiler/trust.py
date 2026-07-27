"""Detached trust manifests for externally anchored artifact/source bindings."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

from .io import (
    _decode_strict_json,
    _read_limited_path_text,
    _read_limited_text,
    validate_artifact_envelope,
    verify_artifact_dict,
)
from .limits import (
    ArtifactLimits,
    SourceLimits,
    bounded_json_utf8_size,
)
from .models import (
    SCHEMA_VERSION,
    SourceRecord,
    source_digest,
    utc_now,
)

TRUST_MANIFEST_SCHEMA = "ctxc-trust-manifest-0.1"
TRUST_VERIFICATION_SCHEMA = "ctxc-trust-verification-0.1"
TRUST_MANIFEST_MAX_BYTES = 64 * 1024
TRUST_MANIFEST_MAX_LINE_CHARS = 64 * 1024
TRUST_MANIFEST_MAX_DEPTH = 8

_SHA256 = re.compile(r"[0-9a-f]{64}")
_UTC_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|\+00:00)"
)
_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "created_at",
        "artifact_schema_version",
        "artifact_sha256",
        "source_digest",
        "source_count",
        "ledger_complete",
        "archive_chain_head_sha256",
        "manifest_sha256",
    }
)


class TrustManifestError(ValueError):
    """Raised when a detached trust manifest is unsafe or inconsistent."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (RecursionError, TypeError, ValueError, OverflowError) as exc:
        raise TrustManifestError(
            f"trust manifest cannot be encoded as canonical JSON: {exc}"
        ) from exc


def _validate_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise TrustManifestError(
            f"{label} must be 64 lowercase hexadecimal characters"
        )
    return value


def _validate_optional_sha256(value: Any, *, label: str) -> str | None:
    if value is None:
        return None
    return _validate_sha256(value, label=label)


def _validate_created_at(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise TrustManifestError(
            "trust manifest created_at must be a non-empty string of at most "
            "64 characters"
        )
    if _UTC_TIMESTAMP.fullmatch(value) is None:
        raise TrustManifestError(
            "trust manifest created_at must be an RFC 3339 timestamp with "
            "an explicit UTC offset"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TrustManifestError(
            "trust manifest created_at must be an ISO-8601 timestamp"
        ) from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
    ):
        raise TrustManifestError(
            "trust manifest created_at must include an explicit UTC offset"
        )
    return value


def _require_bounded_json(value: Any) -> None:
    try:
        bounded_json_utf8_size(
            value,
            max_bytes=TRUST_MANIFEST_MAX_BYTES,
            max_depth=TRUST_MANIFEST_MAX_DEPTH,
            label="trust manifest",
            limit_error=TrustManifestError,
        )
    except TrustManifestError:
        raise
    except (TypeError, ValueError) as exc:
        raise TrustManifestError(
            f"trust manifest must contain only bounded JSON values: {exc}"
        ) from exc


def _validate_manifest_shape(manifest: Any) -> dict[str, Any]:
    _require_bounded_json(manifest)
    if not isinstance(manifest, dict):
        raise TrustManifestError("trust manifest must be a JSON object")
    missing = sorted(_MANIFEST_FIELDS - manifest.keys())
    unknown = sorted(
        (key for key in manifest if key not in _MANIFEST_FIELDS),
        key=repr,
    )
    if missing:
        raise TrustManifestError(
            "trust manifest is missing required fields: "
            + ", ".join(missing)
        )
    if unknown:
        raise TrustManifestError(
            "trust manifest contains unknown fields: "
            + ", ".join(map(repr, unknown))
        )
    if manifest["schema"] != TRUST_MANIFEST_SCHEMA:
        raise TrustManifestError(
            f"unsupported trust manifest schema: {manifest['schema']!r}"
        )
    _validate_created_at(manifest["created_at"])
    if manifest["artifact_schema_version"] != SCHEMA_VERSION:
        raise TrustManifestError(
            "trust manifest artifact_schema_version must be "
            f"{SCHEMA_VERSION!r}"
        )
    _validate_sha256(
        manifest["artifact_sha256"],
        label="trust manifest artifact_sha256",
    )
    _validate_sha256(
        manifest["source_digest"],
        label="trust manifest source_digest",
    )
    source_count = manifest["source_count"]
    if (
        isinstance(source_count, bool)
        or not isinstance(source_count, int)
        or source_count < 0
    ):
        raise TrustManifestError(
            "trust manifest source_count must be a non-negative integer"
        )
    if manifest["ledger_complete"] is not True:
        raise TrustManifestError(
            "trust manifest ledger_complete must be true"
        )
    _validate_optional_sha256(
        manifest["archive_chain_head_sha256"],
        label="trust manifest archive_chain_head_sha256",
    )
    _validate_sha256(
        manifest["manifest_sha256"],
        label="trust manifest manifest_sha256",
    )
    return manifest


def trust_manifest_sha256(manifest: Any) -> str:
    """Hash the exact manifest payload while excluding its self-hash field."""

    _require_bounded_json(manifest)
    if not isinstance(manifest, dict):
        raise TrustManifestError("trust manifest must be a JSON object")
    unsigned = {
        key: value
        for key, value in manifest.items()
        if key != "manifest_sha256"
    }
    canonical = _canonical_json(unsigned)
    if len(canonical.encode("utf-8")) > TRUST_MANIFEST_MAX_BYTES:
        raise TrustManifestError(
            f"trust manifest exceeds {TRUST_MANIFEST_MAX_BYTES} canonical "
            "UTF-8 JSON bytes"
        )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_trust_manifest(manifest: Any) -> dict[str, Any]:
    """Validate strict shape, field contracts, and the manifest self-hash."""

    validated = _validate_manifest_shape(manifest)
    actual = trust_manifest_sha256(validated)
    if not hmac.compare_digest(validated["manifest_sha256"], actual):
        raise TrustManifestError(
            "trust manifest digest mismatch: content does not match "
            "manifest_sha256"
        )
    return validated


def _require_sources(sources: Any) -> list[SourceRecord]:
    if not isinstance(sources, list):
        raise TypeError("sources must be a list of SourceRecord values")
    prepared = list(sources)
    if not all(isinstance(source, SourceRecord) for source in prepared):
        raise TypeError("sources must be a list of SourceRecord values")
    for source in prepared:
        source.ensure_integrity()
    return prepared


def create_trust_manifest(
    artifact: Any,
    sources: list[SourceRecord],
    *,
    archive_chain_head_sha256: str | None = None,
    created_at: str | None = None,
    source_limits: SourceLimits | None = None,
    artifact_limits: ArtifactLimits | None = None,
) -> dict[str, Any]:
    """Bind one fully replay-verified artifact to its exact trusted sources."""

    prepared_sources = _require_sources(sources)
    validated_artifact = validate_artifact_envelope(
        artifact,
        limits=artifact_limits,
    )
    if validated_artifact["ledger_complete"] is not True:
        raise TrustManifestError(
            "trust manifests require a complete artifact ledger"
        )
    replay = verify_artifact_dict(
        validated_artifact,
        prepared_sources,
        source_limits=source_limits,
        artifact_limits=artifact_limits,
    )
    if replay["passed"] is not True:
        codes = sorted(
            {
                str(issue.get("code", "unknown"))
                for issue in replay.get("issues", [])
                if isinstance(issue, dict)
            }
        )
        suffix = f": {', '.join(codes)}" if codes else ""
        raise TrustManifestError(
            "cannot create a trust manifest for an artifact that fails "
            f"independent replay{suffix}"
        )
    archive_head = _validate_optional_sha256(
        archive_chain_head_sha256,
        label="archive_chain_head_sha256",
    )
    timestamp = _validate_created_at(
        utc_now() if created_at is None else created_at
    )
    manifest: dict[str, Any] = {
        "schema": TRUST_MANIFEST_SCHEMA,
        "created_at": timestamp,
        "artifact_schema_version": validated_artifact["schema_version"],
        "artifact_sha256": validated_artifact["artifact_sha256"],
        "source_digest": source_digest(prepared_sources),
        "source_count": len(prepared_sources),
        "ledger_complete": True,
        "archive_chain_head_sha256": archive_head,
    }
    manifest["manifest_sha256"] = trust_manifest_sha256(manifest)
    return validate_trust_manifest(manifest)


def _issue(
    issues: list[dict[str, str]],
    code: str,
    message: str,
) -> None:
    issues.append({"code": code, "message": message})


def verify_trust_manifest(
    manifest: Any,
    artifact: Any,
    sources: list[SourceRecord],
    *,
    expected_manifest_sha256: str | None,
    archive_chain_head_sha256: str | None = None,
    source_limits: SourceLimits | None = None,
    artifact_limits: ArtifactLimits | None = None,
) -> dict[str, Any]:
    """Verify a manifest only when its digest is anchored outside the file."""

    prepared_sources = _require_sources(sources)
    expected_digest = (
        None
        if expected_manifest_sha256 is None
        else _validate_sha256(
            expected_manifest_sha256,
            label="expected_manifest_sha256",
        )
    )
    actual_archive_head = _validate_optional_sha256(
        archive_chain_head_sha256,
        label="archive_chain_head_sha256",
    )
    issues: list[dict[str, str]] = []
    checks = {
        "manifest_shape": False,
        "manifest_self_hash": False,
        "external_anchor": False,
        "artifact_envelope": False,
        "artifact_replay": False,
        "artifact_binding": False,
        "source_binding": False,
        "archive_binding": False,
    }

    actual_manifest_digest: str | None = None
    try:
        actual_manifest_digest = trust_manifest_sha256(manifest)
    except (TrustManifestError, TypeError, ValueError) as exc:
        _issue(issues, "invalid_manifest_json", str(exc))

    shaped_manifest: dict[str, Any] | None = None
    try:
        shaped_manifest = _validate_manifest_shape(manifest)
    except (TrustManifestError, TypeError, ValueError) as exc:
        _issue(issues, "invalid_manifest_shape", str(exc))
    else:
        checks["manifest_shape"] = True
        if (
            actual_manifest_digest is not None
            and hmac.compare_digest(
                shaped_manifest["manifest_sha256"],
                actual_manifest_digest,
            )
        ):
            checks["manifest_self_hash"] = True
        else:
            _issue(
                issues,
                "manifest_digest_mismatch",
                "trust manifest content does not match manifest_sha256",
            )

    if expected_digest is None:
        _issue(
            issues,
            "missing_external_anchor",
            "verification requires an externally retained manifest SHA-256",
        )
    elif (
        actual_manifest_digest is not None
        and hmac.compare_digest(expected_digest, actual_manifest_digest)
    ):
        checks["external_anchor"] = True
    else:
        _issue(
            issues,
            "external_anchor_mismatch",
            "trust manifest content does not match the externally retained "
            "SHA-256",
        )

    validated_artifact: dict[str, Any] | None = None
    try:
        validated_artifact = validate_artifact_envelope(
            artifact,
            limits=artifact_limits,
        )
    except (TypeError, ValueError) as exc:
        _issue(issues, "invalid_artifact_envelope", str(exc))
    else:
        checks["artifact_envelope"] = True
        replay = verify_artifact_dict(
            validated_artifact,
            prepared_sources,
            source_limits=source_limits,
            artifact_limits=artifact_limits,
        )
        if replay["passed"] is True:
            checks["artifact_replay"] = True
        else:
            codes = sorted(
                {
                    str(issue.get("code", "unknown"))
                    for issue in replay.get("issues", [])
                    if isinstance(issue, dict)
                }
            )
            _issue(
                issues,
                "artifact_replay_failed",
                "independent artifact replay failed"
                + (f": {', '.join(codes)}" if codes else ""),
            )

    actual_source_digest = source_digest(prepared_sources)
    if shaped_manifest is not None:
        if (
            validated_artifact is not None
            and shaped_manifest["artifact_schema_version"]
            == validated_artifact["schema_version"]
            and shaped_manifest["artifact_sha256"]
            == validated_artifact["artifact_sha256"]
            and shaped_manifest["ledger_complete"] is True
            and validated_artifact["ledger_complete"] is True
        ):
            checks["artifact_binding"] = True
        else:
            _issue(
                issues,
                "artifact_binding_mismatch",
                "trust manifest does not bind the supplied complete artifact",
            )
        if (
            shaped_manifest["source_digest"] == actual_source_digest
            and shaped_manifest["source_count"] == len(prepared_sources)
        ):
            checks["source_binding"] = True
        else:
            _issue(
                issues,
                "source_binding_mismatch",
                "trust manifest does not bind the supplied source set",
            )
        if (
            shaped_manifest["archive_chain_head_sha256"]
            == actual_archive_head
        ):
            checks["archive_binding"] = True
        else:
            _issue(
                issues,
                "archive_binding_mismatch",
                "trust manifest archive chain head does not match the "
                "supplied archive state",
            )

    passed = all(checks.values())
    return {
        "schema": TRUST_VERIFICATION_SCHEMA,
        "passed": passed,
        "anchored": (
            checks["manifest_self_hash"]
            and checks["external_anchor"]
        ),
        "expected_manifest_sha256": expected_digest,
        "declared_manifest_sha256": (
            manifest.get("manifest_sha256")
            if (
                isinstance(manifest, dict)
                and isinstance(manifest.get("manifest_sha256"), str)
            )
            else None
        ),
        "actual_manifest_sha256": actual_manifest_digest,
        "artifact_sha256": (
            validated_artifact.get("artifact_sha256")
            if validated_artifact is not None
            else None
        ),
        "source_digest": actual_source_digest,
        "source_count": len(prepared_sources),
        "archive_chain_head_sha256": actual_archive_head,
        "checks": checks,
        "issues": issues,
    }


def load_trust_manifest(
    stream: TextIO,
) -> Any:
    """Strictly load one bounded trust manifest without accepting its claims."""

    raw = _read_limited_text(
        stream,
        max_input_bytes=TRUST_MANIFEST_MAX_BYTES,
        max_line_chars=TRUST_MANIFEST_MAX_LINE_CHARS,
        label="trust manifest input",
        limit_error=TrustManifestError,
    )
    if not raw.strip():
        raise TrustManifestError("trust manifest input cannot be empty")
    return _decode_trust_json(raw)


def _decode_trust_json(raw: str) -> Any:
    try:
        return _decode_strict_json(
            raw,
            max_depth=TRUST_MANIFEST_MAX_DEPTH,
            label="trust manifest JSON",
            limit_error=TrustManifestError,
        )
    except TrustManifestError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise TrustManifestError(str(exc)) from exc


def load_trust_manifest_path(
    path: str | Path,
) -> Any:
    """Load a trust manifest through the shared stable regular-file boundary."""

    raw = _read_limited_path_text(
        path,
        max_input_bytes=TRUST_MANIFEST_MAX_BYTES,
        max_line_chars=TRUST_MANIFEST_MAX_LINE_CHARS,
        label="trust manifest input",
        limit_error=TrustManifestError,
    )
    if not raw.strip():
        raise TrustManifestError("trust manifest input cannot be empty")
    return _decode_trust_json(raw)
