"""Bounded JSON and control-character-safe text views of compiled artifacts."""

from __future__ import annotations

import copy
import json
import unicodedata
from typing import Any

from .io import validate_artifact_envelope
from .limits import ArtifactLimits

ARTIFACT_INSPECTION_SCHEMA = "ctxc-artifact-inspection-0.1"


def _positive_integer(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _inspection_item(
    item: dict[str, Any],
    selected_ids: set[str],
    *,
    max_links: int,
) -> dict[str, Any]:
    metadata = item["metadata"]
    source_role = (
        metadata.get("source_role")
        if isinstance(metadata.get("source_role"), str)
        else None
    )
    tags = item["tags"][:max_links]
    supersedes = item["supersedes"][:max_links]
    conflicts_with = item["conflicts_with"][:max_links]
    provenance = item["provenance"][:max_links]
    selected = item["id"] in selected_ids
    return {
        "id": item["id"],
        "kind": item["kind"],
        "status": item["status"],
        "selected": selected,
        "selection_state": "selected" if selected else "not_selected",
        "protected": item["protected"],
        "exact": item["exact"],
        "priority": item["priority"],
        "confidence": item["confidence"],
        "text": item["text"],
        "source_role": source_role,
        "tags": list(tags),
        "tags_omitted": len(item["tags"]) - len(tags),
        "supersedes": list(supersedes),
        "supersedes_omitted": len(item["supersedes"]) - len(supersedes),
        "conflicts_with": list(conflicts_with),
        "conflicts_with_omitted": (
            len(item["conflicts_with"]) - len(conflicts_with)
        ),
        "provenance_count": len(item["provenance"]),
        "provenance": [
            {
                "source_id": span["source_id"],
                "start": span["start"],
                "end": span["end"],
                "quote_sha256": span["quote_sha256"],
            }
            for span in provenance
        ],
        "provenance_omitted": len(item["provenance"]) - len(provenance),
    }


def summarize_artifact(
    artifact: Any,
    *,
    include_items: bool = False,
    max_display_items: int = 100,
    max_display_links: int = 16,
    limits: ArtifactLimits | None = None,
) -> dict[str, Any]:
    """Return a detached, bounded summary of an integrity-checked artifact."""

    if not isinstance(include_items, bool):
        raise TypeError("include_items must be a boolean")
    max_display_items = _positive_integer(
        max_display_items,
        name="max_display_items",
    )
    max_display_links = _positive_integer(
        max_display_links,
        name="max_display_links",
    )
    artifact_value = validate_artifact_envelope(artifact, limits=limits)
    items = artifact_value["items"]
    selected = artifact_value["selected_item_ids"]
    compiler_metadata = artifact_value["compiler_metadata"]
    metrics = compiler_metadata.get("metrics", {})
    summary: dict[str, Any] = {
        "schema": ARTIFACT_INSPECTION_SCHEMA,
        "schema_version": artifact_value["schema_version"],
        "artifact_sha256": artifact_value["artifact_sha256"],
        "compiled_at": artifact_value["compiled_at"],
        "source_digest": artifact_value["source_digest"],
        "source_count": artifact_value["source_count"],
        "ledger_complete": artifact_value["ledger_complete"],
        "integrity": {
            "schema_supported": True,
            "shape_valid": True,
            "self_hash_valid": True,
        },
        "total_items": len(items),
        "selected_items": len(selected),
        "verification": copy.deepcopy(artifact_value["verification"]),
        "compression": copy.deepcopy(artifact_value["compression"]),
        "metrics": copy.deepcopy(metrics),
    }
    if include_items:
        displayed_items = items[:max_display_items]
        selected_ids = set(selected)
        summary["displayed_items"] = len(displayed_items)
        summary["omitted_items"] = len(items) - len(displayed_items)
        summary["max_display_links"] = max_display_links
        summary["item_details"] = [
            _inspection_item(
                item,
                selected_ids,
                max_links=max_display_links,
            )
            for item in displayed_items
        ]
    return summary


def _terminal_literal(value: str, max_chars: int) -> str:
    rendered = value[:max_chars]
    if len(value) > max_chars:
        rendered += "…"
    visible: list[str] = []
    for character in rendered:
        category = unicodedata.category(character)
        if category.startswith("C") or category in {"Zl", "Zp"}:
            codepoint = ord(character)
            visible.append(
                f"\\u{codepoint:04x}"
                if codepoint <= 0xFFFF
                else f"\\U{codepoint:08x}"
            )
        else:
            visible.append(character)
    return json.dumps("".join(visible), ensure_ascii=False)


def _terminal_values(
    values: list[str],
    max_chars: int,
    *,
    max_values: int = 8,
    omitted_values: int = 0,
) -> str:
    rendered = [
        _terminal_literal(value, max_chars)
        for value in values[:max_values]
    ]
    omitted = len(values) - len(rendered) + omitted_values
    if omitted:
        rendered.append(f"…(+{omitted})")
    return "[" + ", ".join(rendered) + "]"


def _render_summary_text(
    summary: dict[str, Any],
    *,
    max_text_chars: int,
) -> str:
    verification = summary["verification"]
    issues = verification["issues"]
    compression = summary["compression"]
    metrics = summary["metrics"]
    lines = [
        "Context compiler artifact",
        f"inspection_schema: {ARTIFACT_INSPECTION_SCHEMA}",
        f"artifact_sha256: {summary['artifact_sha256']}",
        f"schema_version: {_terminal_literal(summary['schema_version'], max_text_chars)}",
        f"compiled_at: {_terminal_literal(summary['compiled_at'], max_text_chars)}",
        f"source_digest: {summary['source_digest']}",
        f"source_count: {summary['source_count']}",
        f"ledger_complete: {str(summary['ledger_complete']).lower()}",
        (
            "integrity: schema_supported=true shape_valid=true "
            "self_hash_valid=true"
        ),
        (
            f"items: total={summary['total_items']} "
            f"selected={summary['selected_items']}"
        ),
        (
            f"verification: passed={str(verification['passed']).lower()} "
            f"issues={len(issues)} codes="
            + _terminal_values(
                sorted({issue["code"] for issue in issues}),
                max_text_chars,
            )
        ),
        (
            f"compression: ratio={compression['compression_ratio']} "
            f"active_tokens={compression['active_tokens_estimate']} "
            f"budget={compression['token_budget']} "
            f"overflow={compression['budget_overflow']} "
            f"target_met={str(compression['target_met']).lower()}"
        ),
    ]
    if metrics:
        lines.append(
            f"metrics: schema={_terminal_literal(metrics['schema'], max_text_chars)} "
            f"resolved={metrics['resolved_items']} "
            f"conflicts={metrics['detected_conflicts']} "
            f"recovered={metrics['recovery_added_items']} "
            f"duration_seconds={metrics['compile_duration_seconds']}"
        )

    item_details = summary.get("item_details")
    if not isinstance(item_details, list):
        lines.append("item_details: hidden (use --show-items)")
        return "\n".join(lines)

    lines.append(
        f"item_details: displaying={summary['displayed_items']} "
        f"omitted={summary['omitted_items']}"
    )
    for item in item_details:
        marker = "SELECTED" if item["selected"] else "NOT_SELECTED"
        lines.append(
            f"- [{marker}] kind={_terminal_literal(item['kind'], max_text_chars)} "
            f"status={_terminal_literal(item['status'], max_text_chars)} "
            f"id={_terminal_literal(item['id'], max_text_chars)}"
        )
        lines.append(
            f"  text={_terminal_literal(item['text'], max_text_chars)}"
        )
        lines.append(
            f"  protected={str(item['protected']).lower()} "
            f"exact={str(item['exact']).lower()} "
            f"priority={item['priority']} confidence={item['confidence']} "
            f"source_role="
            + (
                _terminal_literal(item["source_role"], max_text_chars)
                if item["source_role"] is not None
                else "null"
            )
        )
        provenance = [
            (
                f"{span['source_id']}:{span['start']}-{span['end']}"
                f"#{span['quote_sha256'][:10]}"
            )
            for span in item["provenance"]
        ]
        lines.append(
            "  provenance="
            + _terminal_values(
                provenance,
                max_text_chars,
                omitted_values=item["provenance_omitted"],
            )
        )
        for field in ("tags", "supersedes", "conflicts_with"):
            if item[field]:
                lines.append(
                    f"  {field}="
                    + _terminal_values(
                        item[field],
                        max_text_chars,
                        omitted_values=item[f"{field}_omitted"],
                    )
                )
    return "\n".join(lines)


def render_artifact_text(
    artifact: Any,
    *,
    include_items: bool = False,
    max_display_items: int = 100,
    max_display_links: int = 16,
    max_text_chars: int = 240,
    limits: ArtifactLimits | None = None,
) -> str:
    """Render a bounded artifact view that cannot emit raw control characters."""

    max_text_chars = _positive_integer(
        max_text_chars,
        name="max_text_chars",
    )
    summary = summarize_artifact(
        artifact,
        include_items=include_items,
        max_display_items=max_display_items,
        max_display_links=max_display_links,
        limits=limits,
    )
    return _render_summary_text(
        summary,
        max_text_chars=max_text_chars,
    )
