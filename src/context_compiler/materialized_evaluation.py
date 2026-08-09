"""Deterministic structural-retention diagnostic for materialized context.

The bundled histories are project-authored synthetic-naturalistic fixtures.
This module performs no inference or retrieval and makes no claim about model
answers, task completion, semantic completeness, or provider readiness.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any

from . import __version__
from .connector import ExactTokenCounterAdapter
from .context_window import ContextWindowBudget, ContextWindowError
from .io import _decode_strict_json, _open_stable_text_path
from .limits import bounded_json_utf8_size
from .materialized_window import materialize_context, verify_materialized_context_result
from .models import SourceRecord

MATERIALIZED_RETENTION_REPORT_SCHEMA = "ctxc-materialized-retention-report-0.1"
MATERIALIZED_RETENTION_PACK_SCHEMA = "ctxc-materialized-retention-pack-0.1"
MATERIALIZED_RETENTION_PACK_ID = "ctxc-materialized-retention-naturalistic-v1"
MATERIALIZED_RETENTION_PACK_RAW_SHA256 = (
    "a17dc61a05ddb0d20811e8ec64c7a2da5f0262e98f24a734189550abb6f7f466"
)
MATERIALIZED_RETENTION_PACK_SHA256 = (
    "b8ec4619c86c526293ce26ee3c7f9f5c2ef5ac8637e1d76d8f846f57f222b1cd"
)
MATERIALIZED_RETENTION_PACK_BYTES = 192_498

_PACK_RESOURCE = "data/materialized_retention_pack_v1.json"
_MAX_PACK_BYTES = 256 * 1024
_MAX_REPORT_BYTES = 2 * 1024 * 1024
_MAX_JSON_DEPTH = 24
_MAX_JSON_NODES = 100_000
_MAX_JSON_STRING_CHARS = 64 * 1024
_MAX_JSON_ARRAY_ITEMS = 2_000
_MAX_JSON_OBJECT_ITEMS = 64
_MAX_ABSOLUTE_INTEGER = 10**12
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SPLITS = ("train", "development", "heldout")
_SELECTABLE_SPLITS = (*_SPLITS, "all")
_CATEGORIES = ("correction", "detail", "identifier", "number", "path")
_STATUSES = ("active", "must_not_promote", "superseded")
_STATE_KINDS = frozenset(
    {
        "confirmed_fact",
        "constraint",
        "decision",
        "goal",
        "unresolved",
        "user_correction",
    }
)
_LIVE_MEMORY_STATUSES = frozenset({"active", "conflicting"})
_TOP_FIELDS = frozenset(
    {
        "budget",
        "cases",
        "claim_boundaries",
        "corpus_kind",
        "license",
        "pack_id",
        "pack_sha256",
        "planning_unit_profile",
        "schema",
        "split_policy",
    }
)
_CASE_FIELDS = frozenset(
    {
        "case_id",
        "current_turn_id",
        "expected_materialization_outcome",
        "gold_atoms",
        "sources",
        "split",
        "task_group_id",
    }
)
_SOURCE_FIELDS = frozenset({"content", "id", "role", "sequence"})
_ATOM_FIELDS = frozenset(
    {
        "atom_id",
        "authority_expectation",
        "categories",
        "end",
        "literal",
        "literal_sha256",
        "protected",
        "source_id",
        "start",
        "status",
    }
)
_BUDGET_FIELDS = frozenset(
    {
        "fixed_input_tokens",
        "hard_limit_tokens",
        "maximum_recent_messages",
        "memory_budget_tokens",
        "minimum_recent_messages",
        "per_message_overhead_tokens",
        "reserved_output_tokens",
        "safety_margin_tokens",
    }
)
_EXPECTED_BUDGET = {
    "fixed_input_tokens": 0,
    "hard_limit_tokens": 2_200,
    "maximum_recent_messages": 3,
    "memory_budget_tokens": 1_200,
    "minimum_recent_messages": 2,
    "per_message_overhead_tokens": 2,
    "reserved_output_tokens": 128,
    "safety_margin_tokens": 64,
}
_EXPECTED_CLAIMS = {
    "final_provider_recount_required": True,
    "model_answer_superiority": False,
    "natural_history": False,
    "provider_ready": False,
    "retrieval_included": False,
    "semantic_completeness": False,
}
_EXPECTED_SPLIT_POLICY = {
    "development_case_count": 6,
    "fixture_change_requires_new_pack_id": True,
    "group_disjoint": True,
    "heldout_case_count": 20,
    "name": "task-group-disjoint-4-6-20-v1",
    "result_tuning_prohibited": True,
    "train_case_count": 4,
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _exact_fields(value: Any, expected: frozenset[str], *, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise TypeError(f"{label} must be an exact object")
    actual = frozenset(value)
    if actual != expected:
        unknown = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise ValueError(f"{label} fields are invalid; unknown={unknown}, missing={missing}")
    return value


def _plain_json(
    value: Any,
    *,
    depth: int = 0,
    nodes: list[int] | None = None,
    utf8_floor: list[int] | None = None,
    max_utf8_floor: int | None = None,
    label: str = "JSON value",
) -> None:
    if nodes is None:
        nodes = [0]
    if utf8_floor is None:
        utf8_floor = [0]

    def add_floor(amount: int) -> None:
        utf8_floor[0] += amount
        if max_utf8_floor is not None and utf8_floor[0] > max_utf8_floor:
            raise ValueError(f"{label} exceeds {max_utf8_floor} UTF-8 JSON bytes")

    nodes[0] += 1
    if nodes[0] > _MAX_JSON_NODES:
        raise ValueError(f"{label} exceeds {_MAX_JSON_NODES} JSON nodes")
    if depth > _MAX_JSON_DEPTH:
        raise ValueError(f"{label} exceeds nesting depth {_MAX_JSON_DEPTH}")
    if value is None:
        add_floor(4)
        return
    if type(value) is bool:
        add_floor(4 if value else 5)
        return
    if type(value) is int:
        if abs(value) > _MAX_ABSOLUTE_INTEGER:
            raise ValueError(f"{label} integer is outside the supported range")
        add_floor(len(str(value)))
        return
    if type(value) is str:
        if len(value) > _MAX_JSON_STRING_CHARS:
            raise ValueError(f"{label} string exceeds {_MAX_JSON_STRING_CHARS} characters")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{label} contains malformed Unicode") from exc
        add_floor(len(encoded) + 2)
        return
    if type(value) is list:
        if len(value) > _MAX_JSON_ARRAY_ITEMS:
            raise ValueError(f"{label} array exceeds {_MAX_JSON_ARRAY_ITEMS} items")
        add_floor(2 + max(0, len(value) - 1))
        for item in value:
            _plain_json(
                item,
                depth=depth + 1,
                nodes=nodes,
                utf8_floor=utf8_floor,
                max_utf8_floor=max_utf8_floor,
                label=label,
            )
        return
    if type(value) is dict:
        if len(value) > _MAX_JSON_OBJECT_ITEMS:
            raise ValueError(f"{label} object exceeds {_MAX_JSON_OBJECT_ITEMS} items")
        add_floor(2 + max(0, len(value) - 1) + len(value))
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"{label} object keys must be exact strings")
            _plain_json(
                key,
                depth=depth + 1,
                nodes=nodes,
                utf8_floor=utf8_floor,
                max_utf8_floor=max_utf8_floor,
                label=label,
            )
            _plain_json(
                item,
                depth=depth + 1,
                nodes=nodes,
                utf8_floor=utf8_floor,
                max_utf8_floor=max_utf8_floor,
                label=label,
            )
        return
    raise TypeError(f"{label} contains a non-JSON value: {type(value).__name__}")


def _bounded_report_bytes(value: Any) -> bytes:
    _plain_json(
        value,
        label="materialized retention report",
        max_utf8_floor=_MAX_REPORT_BYTES,
    )
    bounded_json_utf8_size(
        value,
        max_bytes=_MAX_REPORT_BYTES,
        max_depth=_MAX_JSON_DEPTH,
        label="materialized retention report",
        limit_error=ValueError,
    )
    return _canonical_bytes(value)


def _exact_string(value: Any, *, label: str, maximum: int = 4_096) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be an exact string")
    if not value or len(value) > maximum or value != value.strip():
        raise ValueError(f"{label} is empty, oversized, or noncanonical")
    if any(ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in value):
        raise ValueError(f"{label} contains control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} contains malformed Unicode") from exc
    return value


def _exact_integer(value: Any, *, label: str, minimum: int = 0) -> int:
    if type(value) is not int:
        raise TypeError(f"{label} must be an exact integer")
    if value < minimum or value > _MAX_ABSOLUTE_INTEGER:
        raise ValueError(f"{label} is outside the supported range")
    return value


def _digest(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be 64 lowercase hexadecimal characters")
    return value


def _decode_json_payload(raw: bytes, *, label: str, maximum: int) -> dict[str, Any]:
    if not raw or len(raw) > maximum:
        raise ValueError(f"{label} is outside its byte limit")
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1 or b"\r" in raw:
        raise ValueError(f"{label} must be one canonical JSON line")
    try:
        text = raw[:-1].decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    value = _decode_strict_json(
        text,
        max_depth=_MAX_JSON_DEPTH,
        label=label,
        limit_error=ValueError,
    )
    if type(value) is not dict:
        raise TypeError(f"{label} must decode to an exact object")
    _plain_json(value, label=label)
    if _canonical_bytes(value) != raw[:-1]:
        raise ValueError(f"{label} is not compact canonical JSON")
    return value


def _pack_bytes() -> bytes:
    resource = resources.files("context_compiler").joinpath(_PACK_RESOURCE)
    with (
        resources.as_file(resource) as resource_path,
        _open_stable_text_path(
            resource_path,
            max_input_bytes=_MAX_PACK_BYTES,
            label="materialized retention pack",
            limit_error=ValueError,
            require_single_link=True,
        ) as stream,
    ):
        text = stream.read(_MAX_PACK_BYTES + 1)
        if type(text) is not str:
            raise TypeError("materialized retention pack stream must return text")
        try:
            raw = text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("materialized retention pack must be valid UTF-8") from exc
        if len(raw) > _MAX_PACK_BYTES:
            raise ValueError("materialized retention pack exceeds its byte limit")
    if len(raw) != MATERIALIZED_RETENTION_PACK_BYTES:
        raise ValueError("materialized retention pack byte count mismatch")
    actual = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(actual, MATERIALIZED_RETENTION_PACK_RAW_SHA256):
        raise ValueError("materialized retention pack raw digest mismatch")
    return raw


def _validate_pack(value: dict[str, Any]) -> dict[str, Any]:
    _exact_fields(value, _TOP_FIELDS, label="materialized retention pack")
    if value["schema"] != MATERIALIZED_RETENTION_PACK_SCHEMA:
        raise ValueError("materialized retention pack schema is unsupported")
    if value["pack_id"] != MATERIALIZED_RETENTION_PACK_ID:
        raise ValueError("materialized retention pack id is unsupported")
    if value["corpus_kind"] != "repository-authored-synthetic-naturalistic-fixture":
        raise ValueError("materialized retention corpus kind is unsupported")
    if value["license"] != {
        "origin": "project-authored",
        "raw_external_records": False,
        "spdx": "MIT",
    }:
        raise ValueError("materialized retention license boundary is invalid")
    if value["planning_unit_profile"] != "unicode-codepoint-count-v1":
        raise ValueError("materialized retention planning-unit profile is unsupported")
    if value["claim_boundaries"] != _EXPECTED_CLAIMS:
        raise ValueError("materialized retention claim boundaries changed")
    if value["split_policy"] != _EXPECTED_SPLIT_POLICY:
        raise ValueError("materialized retention split policy changed")
    budget = _exact_fields(value["budget"], _BUDGET_FIELDS, label="pack budget")
    for name, expected in _EXPECTED_BUDGET.items():
        _exact_integer(budget[name], label=f"pack budget.{name}")
        if budget[name] != expected:
            raise ValueError(f"materialized retention budget {name} changed")

    claimed = _digest(value["pack_sha256"], label="pack_sha256")
    unsigned = {key: item for key, item in value.items() if key != "pack_sha256"}
    if claimed != MATERIALIZED_RETENTION_PACK_SHA256 or not hmac.compare_digest(
        claimed, _canonical_sha256(unsigned)
    ):
        raise ValueError("materialized retention pack self-digest mismatch")

    cases = value["cases"]
    if type(cases) is not list or len(cases) != 30:
        raise ValueError("materialized retention pack must contain exactly 30 cases")
    case_ids: set[str] = set()
    atom_ids: set[str] = set()
    groups_by_split = {name: set() for name in _SPLITS}
    split_counts = {name: 0 for name in _SPLITS}
    source_contents: dict[str, str] = {}
    atoms: list[dict[str, Any]] = []

    for index, case in enumerate(cases, start=1):
        _exact_fields(case, _CASE_FIELDS, label=f"case {index}")
        case_id = _exact_string(case["case_id"], label=f"case {index}.case_id")
        if case_id != f"case-{index:03d}" or case_id in case_ids:
            raise ValueError("materialized retention case ids are invalid or reordered")
        case_ids.add(case_id)
        split = case["split"]
        if type(split) is not str or split not in _SPLITS:
            raise ValueError(f"{case_id}.split is unsupported")
        group = _exact_string(case["task_group_id"], label=f"{case_id}.task_group_id")
        groups_by_split[split].add(group)
        split_counts[split] += 1
        expected_outcome = case["expected_materialization_outcome"]
        if expected_outcome not in {"accepted", "mandatory_components_do_not_fit"}:
            raise ValueError(f"{case_id} expected outcome is unsupported")

        sources = case["sources"]
        if type(sources) is not list or len(sources) != 18:
            raise ValueError(f"{case_id} must contain exactly 18 ordered sources")
        source_ids: set[str] = set()
        source_by_id: dict[str, dict[str, Any]] = {}
        for sequence, source in enumerate(sources, start=1):
            _exact_fields(source, _SOURCE_FIELDS, label=f"{case_id} source {sequence}")
            source_id = _exact_string(source["id"], label=f"{case_id} source id")
            if source_id in source_ids or source_id in source_contents:
                raise ValueError("materialized retention source ids must be globally unique")
            source_ids.add(source_id)
            _exact_integer(source["sequence"], label=f"{source_id}.sequence", minimum=1)
            if source["sequence"] != sequence:
                raise ValueError(f"{case_id} source sequence is not contiguous")
            if type(source["role"]) is not str or source["role"] not in {
                "assistant",
                "tool",
                "user",
            }:
                raise ValueError(f"{source_id}.role is unsupported")
            content = _exact_string(source["content"], label=f"{source_id}.content", maximum=16_384)
            if any(marker in content for marker in ("C:\\Users\\", "/home/", "OneDrive")):
                raise ValueError("materialized retention pack contains a private path")
            source_contents[source_id] = content
            source_by_id[source_id] = source
            SourceRecord.create(
                id=source_id,
                sequence=sequence,
                role=source["role"],
                content=content,
            ).ensure_integrity()
        current_id = _exact_string(case["current_turn_id"], label=f"{case_id}.current_turn_id")
        if sources[-1]["id"] != current_id or sources[-1]["role"] != "user":
            raise ValueError(f"{case_id} current turn must be the one final user source")
        if sum(source["id"] == current_id for source in sources) != 1:
            raise ValueError(f"{case_id} current turn is not unique")

        gold = case["gold_atoms"]
        if type(gold) is not list or len(gold) != 8:
            raise ValueError(f"{case_id} must contain exactly eight gold atoms")
        for atom in gold:
            _exact_fields(atom, _ATOM_FIELDS, label=f"{case_id} gold atom")
            atom_id = _exact_string(atom["atom_id"], label=f"{case_id} atom id")
            if atom_id in atom_ids:
                raise ValueError("materialized retention atom ids must be globally unique")
            atom_ids.add(atom_id)
            source_id = _exact_string(atom["source_id"], label=f"{atom_id}.source_id")
            if source_id not in source_by_id:
                raise ValueError(f"{atom_id} references an unknown source")
            start = _exact_integer(atom["start"], label=f"{atom_id}.start")
            end = _exact_integer(atom["end"], label=f"{atom_id}.end")
            literal = _exact_string(atom["literal"], label=f"{atom_id}.literal", maximum=16_384)
            if end <= start or source_contents[source_id][start:end] != literal:
                raise ValueError(f"{atom_id} exact source span mismatch")
            if hashlib.sha256(literal.encode("utf-8")).hexdigest() != _digest(
                atom["literal_sha256"], label=f"{atom_id}.literal_sha256"
            ):
                raise ValueError(f"{atom_id} literal digest mismatch")
            categories = atom["categories"]
            if (
                type(categories) is not list
                or not categories
                or categories != sorted(set(categories))
                or any(type(item) is not str or item not in _CATEGORIES for item in categories)
            ):
                raise ValueError(f"{atom_id} categories are invalid")
            if atom["status"] not in _STATUSES:
                raise ValueError(f"{atom_id} status is unsupported")
            if atom["authority_expectation"] not in {
                "authoritative_memory",
                "recent_raw_only",
                "untrusted_evidence_only",
            }:
                raise ValueError(f"{atom_id} authority expectation is unsupported")
            if type(atom["protected"]) is not bool:
                raise TypeError(f"{atom_id}.protected must be an exact boolean")
            if (atom["authority_expectation"] == "authoritative_memory") is not atom["protected"]:
                raise ValueError(f"{atom_id} protected/authority boundary is inconsistent")
            atoms.append(atom)

        current_cost = len(sources[-1]["content"]) + budget["per_message_overhead_tokens"]
        recent_cost = sum(
            len(source["content"]) + budget["per_message_overhead_tokens"]
            for source in sources[-(budget["minimum_recent_messages"] + 1) : -1]
        )
        mandatory_floor = (
            budget["fixed_input_tokens"]
            + budget["memory_budget_tokens"]
            + budget["reserved_output_tokens"]
            + budget["safety_margin_tokens"]
            + recent_cost
            + current_cost
        )
        observed_expected = (
            "mandatory_components_do_not_fit"
            if mandatory_floor > budget["hard_limit_tokens"]
            else "accepted"
        )
        if expected_outcome != observed_expected:
            raise ValueError(f"{case_id} expected outcome disagrees with its fixed budget")

    if split_counts != {"train": 4, "development": 6, "heldout": 20}:
        raise ValueError("materialized retention split counts changed")
    split_groups = list(groups_by_split.values())
    if any(
        left.intersection(right)
        for index, left in enumerate(split_groups)
        for right in split_groups[index + 1 :]
    ):
        raise ValueError("materialized retention task groups cross splits")
    all_groups = set().union(*split_groups)
    if len(all_groups) != 15:
        raise ValueError("materialized retention pack must contain 15 task groups")
    for group in all_groups:
        if sum(case["task_group_id"] == group for case in cases) != 2:
            raise ValueError("each materialized retention task group must contain two cases")

    for atom in atoms:
        matches = {
            source_id
            for source_id, content in source_contents.items()
            if atom["literal"] in content
        }
        expected_matches = {atom["source_id"]}
        if atom["status"] == "superseded":
            case_id = atom["atom_id"].split("-old-setting", 1)[0]
            expected_matches.add(f"{case_id}-m11")
        if matches != expected_matches:
            raise ValueError(f"{atom['atom_id']} has an unintended literal collision")
    return value


def _load_pack() -> dict[str, Any]:
    return _validate_pack(
        _decode_json_payload(
            _pack_bytes(),
            label="materialized retention pack",
            maximum=_MAX_PACK_BYTES,
        )
    )


def _source_records(case: Mapping[str, Any]) -> list[SourceRecord]:
    return [
        SourceRecord.create(
            id=source["id"],
            sequence=source["sequence"],
            role=source["role"],
            content=source["content"],
        )
        for source in case["sources"]
    ]


def _ordered_ids_sha256(values: list[str]) -> str:
    return _canonical_sha256(values)


def _content_manifest_sha256(sources: list[Mapping[str, Any]]) -> str:
    return _canonical_sha256(
        [
            {
                "id": source["id"],
                "sequence": source["sequence"],
                "role": source["role"],
                "content_sha256": hashlib.sha256(source["content"].encode("utf-8")).hexdigest(),
            }
            for source in sources
        ]
    )


def _retention(gold: list[dict[str, Any]], segments: list[str]) -> dict[str, Any]:
    retained = sorted(
        atom["atom_id"] for atom in gold if any(atom["literal"] in segment for segment in segments)
    )
    retained_set = set(retained)
    missing = sorted(atom["atom_id"] for atom in gold if atom["atom_id"] not in retained_set)

    def grouped(field: str, values: tuple[str, ...]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for value in values:
            if field == "categories":
                expected = sorted(atom["atom_id"] for atom in gold if value in atom[field])
            else:
                expected = sorted(atom["atom_id"] for atom in gold if atom[field] == value)
            kept = [atom_id for atom_id in expected if atom_id in retained_set]
            result[value] = {
                "expected_atom_count": len(expected),
                "retained_atom_count": len(kept),
                "missing_atom_count": len(expected) - len(kept),
                "retained_atom_ids": kept,
            }
        return result

    return {
        "expected_atom_count": len(gold),
        "retained_atom_count": len(retained),
        "missing_atom_count": len(missing),
        "retained_atom_ids": retained,
        "missing_atom_ids": missing,
        "by_category": grouped("categories", _CATEGORIES),
        "by_status": grouped("status", _STATUSES),
    }


def _authority(
    bundle: dict[str, Any] | None,
    gold: list[dict[str, Any]],
    sources: list[Mapping[str, Any]],
) -> dict[str, Any]:
    selected_sources: set[str] = set()
    if bundle is not None:
        artifact = bundle["artifact"]
        selected = set(artifact["selected_item_ids"])
        for item in artifact["items"]:
            if (
                item["id"] in selected
                and item["status"] in _LIVE_MEMORY_STATUSES
                and item["kind"] in _STATE_KINDS
            ):
                selected_sources.update(span["source_id"] for span in item["provenance"])
    instruction_shaped_probes = sorted(
        {
            atom["source_id"]
            for atom in gold
            if atom["authority_expectation"] == "untrusted_evidence_only"
        }
    )
    prohibited = sorted(
        source["id"] for source in sources if source["role"] in {"assistant", "tool"}
    )
    violations = sorted(selected_sources.intersection(prohibited))
    return {
        "authoritative_state_kinds": sorted(_STATE_KINDS),
        "live_selected_provenance_source_ids": sorted(selected_sources),
        "must_not_promote_source_ids": prohibited,
        "instruction_shaped_probe_source_ids": instruction_shaped_probes,
        "violation_source_ids": violations,
        "boundaries_preserved": not violations,
    }


def _planning(
    *,
    content_units: int,
    budget: Mapping[str, int],
) -> dict[str, int]:
    occupied = (
        budget["fixed_input_tokens"]
        + content_units
        + budget["reserved_output_tokens"]
        + budget["safety_margin_tokens"]
    )
    hard = budget["hard_limit_tokens"]
    return {
        "hard_limit_planning_units": hard,
        "fixed_input_planning_units": budget["fixed_input_tokens"],
        "content_planning_units": content_units,
        "reserved_output_planning_units": budget["reserved_output_tokens"],
        "safety_margin_planning_units": budget["safety_margin_tokens"],
        "occupied_planning_units": occupied,
        "remaining_planning_units": hard - occupied,
        "hard_limit_overflow_planning_units": max(0, occupied - hard),
    }


def _raw_arm(
    *,
    arm: str,
    all_sources: list[dict[str, Any]],
    included: list[dict[str, Any]],
    current_turn_id: str,
    gold: list[dict[str, Any]],
    budget: Mapping[str, int],
) -> dict[str, Any]:
    included_ids = [source["id"] for source in included]
    included_set = set(included_ids)
    omitted_ids = [source["id"] for source in all_sources if source["id"] not in included_set]
    units = sum(
        len(source["content"]) + budget["per_message_overhead_tokens"] for source in included
    )
    planning = _planning(content_units=units, budget=budget)
    return {
        "arm": arm,
        "outcome": (
            "accepted" if planning["hard_limit_overflow_planning_units"] == 0 else "over_hard_limit"
        ),
        "reason": (
            None if planning["hard_limit_overflow_planning_units"] == 0 else "hard_limit_overflow"
        ),
        "planning_units": planning,
        "attempted_planning_units": None,
        "included_source_ids": included_ids,
        "included_source_ids_sha256": _ordered_ids_sha256(included_ids),
        "omitted_source_ids": omitted_ids,
        "omitted_source_ids_sha256": _ordered_ids_sha256(omitted_ids),
        "compiled_source_ids": [],
        "compiled_source_ids_sha256": _ordered_ids_sha256([]),
        "content_manifest_sha256": _content_manifest_sha256(included),
        "current_turn_occurrences": included_ids.count(current_turn_id),
        "current_turn_last": bool(included_ids and included_ids[-1] == current_turn_id),
        "retention": _retention(gold, [source["content"] for source in included]),
        "authority": {
            "authoritative_state_kinds": sorted(_STATE_KINDS),
            "live_selected_provenance_source_ids": [],
            "must_not_promote_source_ids": sorted(
                source["id"] for source in all_sources if source["role"] in {"assistant", "tool"}
            ),
            "instruction_shaped_probe_source_ids": sorted(
                {
                    atom["source_id"]
                    for atom in gold
                    if atom["authority_expectation"] == "untrusted_evidence_only"
                }
            ),
            "violation_source_ids": [],
            "boundaries_preserved": True,
        },
        "materialization_digests": None,
    }


def _tail_sources(sources: list[dict[str, Any]], budget: Mapping[str, int]) -> list[dict[str, Any]]:
    history = sources[:-1]
    current = sources[-1]
    minimum = min(budget["minimum_recent_messages"], len(history))
    start = len(history) - minimum
    selected = history[start:] + [current]
    capacity = (
        budget["hard_limit_tokens"]
        - budget["fixed_input_tokens"]
        - budget["reserved_output_tokens"]
        - budget["safety_margin_tokens"]
    )
    occupied = sum(
        len(source["content"]) + budget["per_message_overhead_tokens"] for source in selected
    )
    while start > 0 and len(history) - start < budget["maximum_recent_messages"]:
        candidate = history[start - 1]
        candidate_units = len(candidate["content"]) + budget["per_message_overhead_tokens"]
        if occupied + candidate_units > capacity:
            break
        start -= 1
        occupied += candidate_units
        selected.insert(0, candidate)
    return selected


def _lrcc_arm(
    *,
    case: dict[str, Any],
    sources: list[SourceRecord],
    budget: ContextWindowBudget,
    counter: ExactTokenCounterAdapter,
    allocation_plan_sha256: str,
) -> tuple[dict[str, Any], str]:
    all_sources = case["sources"]
    all_ids = [source["id"] for source in all_sources]
    gold = case["gold_atoms"]
    try:
        result = materialize_context(
            sources,
            current_turn_id=case["current_turn_id"],
            budget=budget,
            token_counter=counter,
            allocation_plan_sha256=allocation_plan_sha256,
        )
    except ContextWindowError as exc:
        reason = exc.reason or "invalid_context_window"
        minimum = min(budget.minimum_recent_messages, len(all_sources) - 1)
        attempted_sources = all_sources[-(minimum + 1) :]
        attempted_content = budget.memory_budget_tokens + sum(
            len(source["content"]) + budget.per_message_overhead_tokens
            for source in attempted_sources
        )
        attempted_planning = _planning(
            content_units=attempted_content,
            budget={
                "fixed_input_tokens": budget.fixed_input_tokens,
                "hard_limit_tokens": budget.hard_limit_tokens,
                "reserved_output_tokens": budget.reserved_output_tokens,
                "safety_margin_tokens": budget.safety_margin_tokens,
            },
        )
        return (
            {
                "arm": "lrcc_materialized",
                "outcome": "refused",
                "reason": reason,
                "planning_units": _planning(
                    content_units=0,
                    budget={
                        "fixed_input_tokens": budget.fixed_input_tokens,
                        "hard_limit_tokens": budget.hard_limit_tokens,
                        "reserved_output_tokens": budget.reserved_output_tokens,
                        "safety_margin_tokens": budget.safety_margin_tokens,
                    },
                ),
                "attempted_planning_units": attempted_planning,
                "included_source_ids": [],
                "included_source_ids_sha256": _ordered_ids_sha256([]),
                "omitted_source_ids": all_ids,
                "omitted_source_ids_sha256": _ordered_ids_sha256(all_ids),
                "compiled_source_ids": [],
                "compiled_source_ids_sha256": _ordered_ids_sha256([]),
                "content_manifest_sha256": _content_manifest_sha256([]),
                "current_turn_occurrences": 0,
                "current_turn_last": False,
                "retention": _retention(gold, []),
                "authority": _authority(None, gold, all_sources),
                "materialization_digests": None,
            },
            reason,
        )

    receipt = result["receipt"]
    checked = verify_materialized_context_result(
        result,
        expected_receipt_sha256=receipt["receipt_sha256"],
        expected_allocation_plan_sha256=allocation_plan_sha256,
    )
    runtime = checked["runtime_payload"]
    recent = runtime["recent_messages"]
    current = runtime["current_turn"]
    included_ids = [source["id"] for source in recent] + [current["id"]]
    omitted_ids = [source["id"] for source in all_sources if source["id"] not in set(included_ids)]
    compiled_ids = [item["id"] for item in runtime["recent_tail_omissions"]]
    if omitted_ids != compiled_ids:
        raise ValueError("LRCC raw omission and compiled-source accounting disagree")
    segments = [runtime["verified_context"]]
    segments.extend(source["content"] for source in recent)
    segments.append(current["content"])
    accounting = runtime["accounting"]
    planning = {
        "hard_limit_planning_units": accounting["hard_limit_tokens"],
        "fixed_input_planning_units": accounting["fixed_input_tokens"],
        "content_planning_units": (
            accounting["memory_tokens"]
            + accounting["recent_tail_tokens"]
            + accounting["current_turn_tokens"]
        ),
        "reserved_output_planning_units": accounting["reserved_output_tokens"],
        "safety_margin_planning_units": accounting["safety_margin_tokens"],
        "occupied_planning_units": accounting["occupied_tokens"],
        "remaining_planning_units": accounting["remaining_tokens"],
        "hard_limit_overflow_planning_units": 0,
    }
    manifest_sources = [source for source in all_sources if source["id"] in set(included_ids)]
    bundle = checked["materialized_context"]["prototype"]["context_bundle"]
    return (
        {
            "arm": "lrcc_materialized",
            "outcome": "accepted",
            "reason": None,
            "planning_units": planning,
            "attempted_planning_units": None,
            "included_source_ids": included_ids,
            "included_source_ids_sha256": _ordered_ids_sha256(included_ids),
            "omitted_source_ids": omitted_ids,
            "omitted_source_ids_sha256": _ordered_ids_sha256(omitted_ids),
            "compiled_source_ids": compiled_ids,
            "compiled_source_ids_sha256": _ordered_ids_sha256(compiled_ids),
            "content_manifest_sha256": _content_manifest_sha256(manifest_sources),
            "current_turn_occurrences": included_ids.count(case["current_turn_id"]),
            "current_turn_last": bool(included_ids and included_ids[-1] == case["current_turn_id"]),
            "retention": _retention(gold, segments),
            "authority": _authority(bundle, gold, all_sources),
            "materialization_digests": {
                "accounting_sha256": receipt["accounting_sha256"],
                "component_manifest_sha256": receipt["component_manifest_sha256"],
                "context_bundle_sha256": receipt["context_bundle_sha256"],
                "current_turn_sha256": receipt["current_turn_sha256"],
                "materialization_sha256": receipt["materialization_sha256"],
                "protected_state_sha256": receipt["protected_state_sha256"],
                "prototype_sha256": receipt["prototype_sha256"],
                "receipt_sha256": receipt["receipt_sha256"],
                "recent_messages_sha256": receipt["recent_messages_sha256"],
                "recent_tail_omissions_sha256": receipt["recent_tail_omissions_sha256"],
                "rendered_memory_sha256": receipt["rendered_memory_sha256"],
                "runtime_payload_sha256": receipt["runtime_payload_sha256"],
            },
        },
        "accepted",
    )


def _case_result(case: dict[str, Any], budget_value: dict[str, int]) -> dict[str, Any]:
    sources = _source_records(case)
    counter = ExactTokenCounterAdapter("unicode-codepoint-count-v1", len)
    budget = ContextWindowBudget(**budget_value)
    allocation = _canonical_sha256(
        {
            "domain": "ctxc-materialized-retention-allocation-v1",
            "pack_sha256": MATERIALIZED_RETENTION_PACK_SHA256,
            "case_id": case["case_id"],
            "budget": budget_value,
        }
    )
    full = _raw_arm(
        arm="full_history",
        all_sources=case["sources"],
        included=case["sources"],
        current_turn_id=case["current_turn_id"],
        gold=case["gold_atoms"],
        budget=budget_value,
    )
    tail = _raw_arm(
        arm="tail_only",
        all_sources=case["sources"],
        included=_tail_sources(case["sources"], budget_value),
        current_turn_id=case["current_turn_id"],
        gold=case["gold_atoms"],
        budget=budget_value,
    )
    lrcc, observed = _lrcc_arm(
        case=case,
        sources=sources,
        budget=budget,
        counter=counter,
        allocation_plan_sha256=allocation,
    )
    expected = case["expected_materialization_outcome"]
    expected_observed = "accepted" if expected == "accepted" else expected
    current_ok = (
        lrcc["current_turn_occurrences"] == 1 and lrcc["current_turn_last"] is True
        if observed == "accepted"
        else True
    )
    integrity = (
        observed == expected_observed
        and current_ok
        and lrcc["authority"]["boundaries_preserved"] is True
    )
    return {
        "case_id": case["case_id"],
        "task_group_id": case["task_group_id"],
        "split": case["split"],
        "source_count": len(case["sources"]),
        "source_manifest_sha256": _content_manifest_sha256(case["sources"]),
        "gold_manifest_sha256": _canonical_sha256(
            [
                {
                    "atom_id": atom["atom_id"],
                    "literal_sha256": atom["literal_sha256"],
                    "source_id": atom["source_id"],
                    "start": atom["start"],
                    "end": atom["end"],
                    "status": atom["status"],
                    "categories": atom["categories"],
                    "authority_expectation": atom["authority_expectation"],
                    "protected": atom["protected"],
                }
                for atom in case["gold_atoms"]
            ]
        ),
        "current_turn_id": case["current_turn_id"],
        "expected_materialization_outcome": expected,
        "observed_materialization_outcome": observed,
        "expectation_matched": observed == expected_observed,
        "allocation_plan_sha256": allocation,
        "arms": [full, tail, lrcc],
        "integrity_passed": integrity,
    }


def _summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    arms: dict[str, Any] = {}
    for arm_name in ("full_history", "tail_only", "lrcc_materialized"):
        selected = [next(arm for arm in case["arms"] if arm["arm"] == arm_name) for case in cases]
        category = {
            name: {
                "expected_atom_count": sum(
                    arm["retention"]["by_category"][name]["expected_atom_count"] for arm in selected
                ),
                "retained_atom_count": sum(
                    arm["retention"]["by_category"][name]["retained_atom_count"] for arm in selected
                ),
            }
            for name in _CATEGORIES
        }
        for value in category.values():
            value["missing_atom_count"] = (
                value["expected_atom_count"] - value["retained_atom_count"]
            )
        arms[arm_name] = {
            "case_count": len(selected),
            "accepted_case_count": sum(arm["outcome"] == "accepted" for arm in selected),
            "over_hard_limit_case_count": sum(
                arm["outcome"] == "over_hard_limit" for arm in selected
            ),
            "refused_case_count": sum(arm["outcome"] == "refused" for arm in selected),
            "content_planning_units": sum(
                arm["planning_units"]["content_planning_units"] for arm in selected
            ),
            "occupied_planning_units": sum(
                arm["planning_units"]["occupied_planning_units"] for arm in selected
            ),
            "attempted_content_planning_units": sum(
                arm["attempted_planning_units"]["content_planning_units"]
                for arm in selected
                if arm["attempted_planning_units"] is not None
            ),
            "attempted_occupied_planning_units": sum(
                arm["attempted_planning_units"]["occupied_planning_units"]
                for arm in selected
                if arm["attempted_planning_units"] is not None
            ),
            "attempted_hard_limit_overflow_planning_units": sum(
                arm["attempted_planning_units"]["hard_limit_overflow_planning_units"]
                for arm in selected
                if arm["attempted_planning_units"] is not None
            ),
            "included_source_count": sum(len(arm["included_source_ids"]) for arm in selected),
            "omitted_source_count": sum(len(arm["omitted_source_ids"]) for arm in selected),
            "compiled_source_count": sum(len(arm["compiled_source_ids"]) for arm in selected),
            "expected_atom_count": sum(arm["retention"]["expected_atom_count"] for arm in selected),
            "retained_atom_count": sum(arm["retention"]["retained_atom_count"] for arm in selected),
            "missing_atom_count": sum(arm["retention"]["missing_atom_count"] for arm in selected),
            "current_turn_exact_once_count": sum(
                arm["current_turn_occurrences"] == 1 for arm in selected
            ),
            "current_turn_last_count": sum(arm["current_turn_last"] is True for arm in selected),
            "authority_boundary_violation_count": sum(
                len(arm["authority"]["violation_source_ids"]) for arm in selected
            ),
            "by_category": category,
        }
    unexpected = sorted(case["case_id"] for case in cases if case["integrity_passed"] is not True)
    return {
        "case_count": len(cases),
        "expected_accepted_case_count": sum(
            case["expected_materialization_outcome"] == "accepted" for case in cases
        ),
        "expected_refusal_case_count": sum(
            case["expected_materialization_outcome"] != "accepted" for case in cases
        ),
        "observed_accepted_case_count": sum(
            case["observed_materialization_outcome"] == "accepted" for case in cases
        ),
        "observed_refusal_case_count": sum(
            case["observed_materialization_outcome"] != "accepted" for case in cases
        ),
        "unexpected_case_ids": unexpected,
        "arms": arms,
    }


def _evaluate_materialization_retention(selected_split: str) -> dict[str, Any]:
    if type(selected_split) is not str or selected_split not in _SELECTABLE_SPLITS:
        raise ValueError("selected_split must be one of train, development, heldout, or all")
    pack = _load_pack()
    selected_cases = [
        case for case in pack["cases"] if selected_split == "all" or case["split"] == selected_split
    ]
    cases = [_case_result(case, pack["budget"]) for case in selected_cases]
    summary = _summary(cases)
    unsigned = {
        "schema": MATERIALIZED_RETENTION_REPORT_SCHEMA,
        "evaluator_package_version": __version__,
        "pack": {
            "schema": pack["schema"],
            "pack_id": pack["pack_id"],
            "corpus_kind": pack["corpus_kind"],
            "raw_bytes": MATERIALIZED_RETENTION_PACK_BYTES,
            "raw_sha256": MATERIALIZED_RETENTION_PACK_RAW_SHA256,
            "pack_sha256": pack["pack_sha256"],
            "planning_unit_profile": pack["planning_unit_profile"],
        },
        "selection": {
            "split": selected_split,
            "case_count": len(cases),
            "case_ids": [case["case_id"] for case in cases],
            "case_ids_sha256": _ordered_ids_sha256([case["case_id"] for case in cases]),
            "task_group_ids": sorted({case["task_group_id"] for case in cases}),
            "task_group_ids_sha256": _ordered_ids_sha256(
                sorted({case["task_group_id"] for case in cases})
            ),
            "group_disjoint": True,
            "result_tuning_prohibited": True,
        },
        "budget": dict(pack["budget"]),
        "cases": cases,
        "summary": summary,
        "claim_boundaries": {
            "structural_retention_only": True,
            "natural_history_claimed": False,
            "semantic_completeness_claimed": False,
            "model_answer_superiority_claimed": False,
            "task_completion_measured": False,
            "provider_token_accounting": False,
            "retrieval_included": False,
            "retrieval_status": "not_run",
            "inference_status": "not_run",
            "provider_execution_ready": False,
            "final_provider_recount_required": True,
        },
        "integrity_passed": not summary["unexpected_case_ids"],
    }
    unsigned_bytes = _bounded_report_bytes(unsigned)
    report = {
        **unsigned,
        "report_sha256": hashlib.sha256(unsigned_bytes).hexdigest(),
    }
    _bounded_report_bytes(report)
    return report


def evaluate_materialization_retention(*, selected_split: str = "heldout") -> dict[str, Any]:
    """Run the fixed offline structural diagnostic for one grouped split."""

    return _evaluate_materialization_retention(selected_split)


def verify_materialization_retention_report(
    value: Mapping[str, Any],
    *,
    expected_report_sha256: str,
) -> dict[str, Any]:
    """Recompute and verify a report against an independently retained digest."""

    if type(value) is not dict:
        raise TypeError("materialized retention report must be an exact object")
    canonical_value = _bounded_report_bytes(value)
    expected = _digest(expected_report_sha256, label="expected_report_sha256")
    claimed = _digest(value.get("report_sha256"), label="report_sha256")
    if not hmac.compare_digest(claimed, expected):
        raise ValueError("materialized retention report does not match the expected digest")
    unsigned = {key: item for key, item in value.items() if key != "report_sha256"}
    if not hmac.compare_digest(claimed, _canonical_sha256(unsigned)):
        raise ValueError("materialized retention report self-digest mismatch")
    selection = value.get("selection")
    if type(selection) is not dict or type(selection.get("split")) is not str:
        raise ValueError("materialized retention report selection is invalid")
    recomputed = _evaluate_materialization_retention(selection["split"])
    if not hmac.compare_digest(canonical_value, _bounded_report_bytes(recomputed)):
        raise ValueError("materialized retention report differs from deterministic replay")
    return json.loads(_canonical_bytes(recomputed))


def load_materialization_retention_report(
    path: str | Path,
    *,
    expected_report_sha256: str,
) -> dict[str, Any]:
    """Load one stable canonical report and verify it by deterministic replay."""

    with _open_stable_text_path(
        path,
        max_input_bytes=_MAX_REPORT_BYTES,
        label="materialized retention report",
        limit_error=ValueError,
        require_single_link=True,
    ) as stream:
        raw_text = stream.read(_MAX_REPORT_BYTES + 1)
        if type(raw_text) is not str:
            raise TypeError("materialized retention report stream must return text")
        try:
            raw = raw_text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("materialized retention report must be valid UTF-8") from exc
        if len(raw) > _MAX_REPORT_BYTES:
            raise ValueError("materialized retention report exceeds its byte limit")
    value = _decode_json_payload(
        raw,
        label="materialized retention report",
        maximum=_MAX_REPORT_BYTES,
    )
    return verify_materialization_retention_report(
        value,
        expected_report_sha256=expected_report_sha256,
    )


__all__ = [
    "MATERIALIZED_RETENTION_PACK_BYTES",
    "MATERIALIZED_RETENTION_PACK_ID",
    "MATERIALIZED_RETENTION_PACK_RAW_SHA256",
    "MATERIALIZED_RETENTION_PACK_SCHEMA",
    "MATERIALIZED_RETENTION_PACK_SHA256",
    "MATERIALIZED_RETENTION_REPORT_SCHEMA",
    "evaluate_materialization_retention",
    "load_materialization_retention_report",
    "verify_materialization_retention_report",
]
