"""Machine-readable compatibility policy for compiled-memory artifacts.

Artifact readers intentionally fail closed.  This module describes the exact
versions accepted by the current package without pretending that an unknown
version can be upgraded safely.
"""

from __future__ import annotations

import re
from typing import Any

from .models import SCHEMA_VERSION

ARTIFACT_SCHEMA_COMPATIBILITY_SCHEMA = "ctxc-artifact-schema-compatibility-0.1"

_VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
_READABLE_VERSIONS = (SCHEMA_VERSION,)
_WRITABLE_VERSIONS = (SCHEMA_VERSION,)


def _validated_version(version: str) -> str:
    if not isinstance(version, str):
        raise TypeError("artifact schema version must be a string")
    if not _VERSION_PATTERN.fullmatch(version):
        raise ValueError(
            "artifact schema version must use canonical MAJOR.MINOR decimal syntax"
        )
    return version


def artifact_schema_support(version: str) -> dict[str, Any]:
    """Return the current package's support decision for one artifact version."""

    checked_version = _validated_version(version)
    readable = checked_version in _READABLE_VERSIONS
    writable = checked_version in _WRITABLE_VERSIONS
    current = checked_version == SCHEMA_VERSION
    status = "current" if current else "unsupported"
    reason = (
        "current reader and writer support this artifact schema"
        if current
        else (
            "no reader, writer, or explicit migration is registered for this "
            "artifact schema"
        )
    )
    return {
        "schema": ARTIFACT_SCHEMA_COMPATIBILITY_SCHEMA,
        "artifact_schema_version": checked_version,
        "current_version": SCHEMA_VERSION,
        "status": status,
        "readable": readable,
        "writable": writable,
        "migration": {
            "available": False,
            "automatic": False,
            "target_version": None,
        },
        "reason": reason,
    }


def artifact_schema_registry() -> dict[str, Any]:
    """Return the complete supported artifact reader/writer window and policy."""

    return {
        "schema": ARTIFACT_SCHEMA_COMPATIBILITY_SCHEMA,
        "artifact_family": "compiled-memory",
        "current_version": SCHEMA_VERSION,
        "readable_versions": list(_READABLE_VERSIONS),
        "writable_versions": list(_WRITABLE_VERSIONS),
        "versions": [
            {
                "version": SCHEMA_VERSION,
                "status": "current",
                "readable": True,
                "writable": True,
                "compatible_omissions": [
                    "compiler_metadata.metrics",
                ],
            }
        ],
        "migration_policy": {
            "automatic": False,
            "silent": False,
            "available_migrations": [],
            "future_migration_requirements": [
                "preserve the original artifact",
                "require trusted source-bound replay",
                "record the origin artifact digest and migration identifier",
                "emit a new artifact digest",
            ],
        },
    }
