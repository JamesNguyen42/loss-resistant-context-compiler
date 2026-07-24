"""Deterministic, integrity-gated comparison of compiled artifacts."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from typing import Any

from .io import validate_artifact_envelope
from .limits import ArtifactLimits

ARTIFACT_DIFF_SCHEMA = "ctxc-artifact-diff-0.1"
_TOP_LEVEL_COMPARISON_FIELDS = (
    "schema_version",
    "ledger_complete",
    "compiled_at",
    "source_digest",
    "source_count",
    "verification",
    "compression",
    "compiler_metadata",
)


def _canonical_sha256(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _payload_counts(artifact: dict[str, Any]) -> dict[str, Any]:
    items = artifact["items"]
    return {
        "items": len(items),
        "selected_items": len(artifact["selected_item_ids"]),
        "by_kind": dict(sorted(Counter(item["kind"] for item in items).items())),
        "by_status": dict(
            sorted(Counter(item["status"] for item in items).items())
        ),
    }


def _artifact_identity(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_sha256": artifact["artifact_sha256"],
        "schema_version": artifact["schema_version"],
        "compiled_at": artifact["compiled_at"],
        "source_digest": artifact["source_digest"],
        "source_count": artifact["source_count"],
        "ledger_complete": artifact["ledger_complete"],
        "payload": _payload_counts(artifact),
    }


def _verification_summary(verification: dict[str, Any]) -> dict[str, Any]:
    issues = verification["issues"]
    severity_counts = Counter(issue["severity"] for issue in issues)
    return {
        "passed": verification["passed"],
        "issue_count": len(issues),
        "error_count": severity_counts["error"],
        "warning_count": severity_counts["warning"],
        "info_count": severity_counts["info"],
        "issue_codes": sorted({issue["code"] for issue in issues}),
        "protected_candidates": verification["protected_candidates"],
        "protected_retained": verification["protected_retained"],
        "provenance_valid": verification["provenance_valid"],
        "provenance_total": verification["provenance_total"],
        "recovered_items": verification["recovered_items"],
    }


def _item_view(item: dict[str, Any], selected_ids: set[str]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "kind": item["kind"],
        "text": item["text"],
        "status": item["status"],
        "selected": item["id"] in selected_ids,
        "protected": item["protected"],
        "exact": item["exact"],
        "priority": item["priority"],
        "confidence": item["confidence"],
        "tags": list(item["tags"]),
        "supersedes": list(item["supersedes"]),
        "conflicts_with": list(item["conflicts_with"]),
        "metadata_sha256": _canonical_sha256(item["metadata"]),
        "provenance": [
            {
                "source_id": span["source_id"],
                "start": span["start"],
                "end": span["end"],
                "quote_sha256": span["quote_sha256"],
            }
            for span in item["provenance"]
        ],
    }


def diff_artifacts(
    before: Any,
    after: Any,
    *,
    include_item_details: bool = True,
    limits: ArtifactLimits | None = None,
) -> dict[str, Any]:
    """Compare two validated artifact payloads and return a self-hashed report.

    Item-set additions and removals describe serialized payloads. They represent
    complete ledger changes only when both artifacts have ``ledger_complete``
    set to true. Selection changes remain exact for active-only artifacts.
    """

    if not isinstance(include_item_details, bool):
        raise TypeError("include_item_details must be a boolean")
    before_artifact = validate_artifact_envelope(before, limits=limits)
    after_artifact = validate_artifact_envelope(after, limits=limits)

    before_items = {item["id"]: item for item in before_artifact["items"]}
    after_items = {item["id"]: item for item in after_artifact["items"]}
    before_ids = set(before_items)
    after_ids = set(after_items)
    added_ids = sorted(after_ids - before_ids)
    removed_ids = sorted(before_ids - after_ids)
    shared_ids = sorted(before_ids & after_ids)
    modified_ids = [
        item_id
        for item_id in shared_ids
        if before_items[item_id] != after_items[item_id]
    ]
    unchanged_shared_items = len(shared_ids) - len(modified_ids)

    before_selected = set(before_artifact["selected_item_ids"])
    after_selected = set(after_artifact["selected_item_ids"])
    selection_added = sorted(after_selected - before_selected)
    selection_removed = sorted(before_selected - after_selected)
    top_level_changed_fields = [
        field
        for field in _TOP_LEVEL_COMPARISON_FIELDS
        if before_artifact[field] != after_artifact[field]
    ]
    ledger_comparison_complete = bool(
        before_artifact["ledger_complete"]
        and after_artifact["ledger_complete"]
    )

    item_changes: dict[str, Any] | None = None
    if include_item_details:
        item_changes = {
            "payload_added": [
                _item_view(after_items[item_id], after_selected)
                for item_id in added_ids
            ],
            "payload_removed": [
                _item_view(before_items[item_id], before_selected)
                for item_id in removed_ids
            ],
            "payload_modified": [
                {
                    "id": item_id,
                    "changed_fields": sorted(
                        field
                        for field in (
                            before_items[item_id].keys()
                            | after_items[item_id].keys()
                        )
                        if before_items[item_id].get(field)
                        != after_items[item_id].get(field)
                    ),
                    "before": _item_view(
                        before_items[item_id],
                        before_selected,
                    ),
                    "after": _item_view(
                        after_items[item_id],
                        after_selected,
                    ),
                }
                for item_id in modified_ids
            ],
        }

    before_metrics = before_artifact["compiler_metadata"].get("metrics", {})
    after_metrics = after_artifact["compiler_metadata"].get("metrics", {})
    report: dict[str, Any] = {
        "schema": ARTIFACT_DIFF_SCHEMA,
        "details_included": include_item_details,
        "before": _artifact_identity(before_artifact),
        "after": _artifact_identity(after_artifact),
        "summary": {
            "artifact_changed": (
                before_artifact["artifact_sha256"]
                != after_artifact["artifact_sha256"]
            ),
            "source_history_changed": (
                before_artifact["source_digest"]
                != after_artifact["source_digest"]
                or before_artifact["source_count"]
                != after_artifact["source_count"]
            ),
            "ledger_comparison_complete": ledger_comparison_complete,
            "payload_added_items": len(added_ids),
            "payload_removed_items": len(removed_ids),
            "payload_modified_items": len(modified_ids),
            "unchanged_shared_items": unchanged_shared_items,
            "selection_added_items": len(selection_added),
            "selection_removed_items": len(selection_removed),
            "top_level_changed_fields": top_level_changed_fields,
        },
        "selection": {
            "added": selection_added,
            "removed": selection_removed,
        },
        "report_changes": {
            "verification": {
                "changed": (
                    before_artifact["verification"]
                    != after_artifact["verification"]
                ),
                "before": _verification_summary(
                    before_artifact["verification"]
                ),
                "after": _verification_summary(
                    after_artifact["verification"]
                ),
            },
            "compression": {
                "changed": (
                    before_artifact["compression"]
                    != after_artifact["compression"]
                ),
                "before": copy.deepcopy(before_artifact["compression"]),
                "after": copy.deepcopy(after_artifact["compression"]),
            },
            "metrics": {
                "changed": before_metrics != after_metrics,
                "before": copy.deepcopy(before_metrics),
                "after": copy.deepcopy(after_metrics),
            },
        },
        "item_changes": item_changes,
        "warnings": (
            []
            if ledger_comparison_complete
            else ["payload_changes_do_not_prove_complete_ledger_changes"]
        ),
    }
    report["diff_sha256"] = _canonical_sha256(report)
    return report
