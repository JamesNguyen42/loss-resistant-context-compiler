"""Fixed offline comparison of strict and opt-in materialization degradation.

The evaluator reuses the immutable project-authored synthetic-naturalistic
retention pack.  It performs no inference or retrieval and does not measure
provider tokens, task completion, semantic completeness, or answer quality.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import __version__
from .connector import ExactTokenCounterAdapter
from .context_window import (
    CONTEXT_WINDOW_DEGRADATION_MODE,
    LOSSLESS_COMPACT_MEMORY_RENDERING_PROFILE,
    ContextWindowBudget,
    ContextWindowDegradationPolicy,
    ContextWindowError,
    _compose_context_window_once,
    _is_exact_memory_overflow,
)
from .io import _open_stable_text_path
from .materialized_evaluation import (
    MATERIALIZED_RETENTION_PACK_BYTES,
    MATERIALIZED_RETENTION_PACK_ID,
    MATERIALIZED_RETENTION_PACK_RAW_SHA256,
    MATERIALIZED_RETENTION_PACK_SCHEMA,
    MATERIALIZED_RETENTION_PACK_SHA256,
    _authority,
    _bounded_report_bytes,
    _canonical_bytes,
    _canonical_sha256,
    _content_manifest_sha256,
    _decode_json_payload,
    _digest,
    _load_pack,
    _ordered_ids_sha256,
    _retention,
    _source_records,
)
from .materialized_window import materialize_context, verify_materialized_context_result
from .models import MemoryKind, SourceRecord

MATERIALIZED_DEGRADATION_REPORT_SCHEMA = "ctxc-materialized-degradation-report-0.2"
MATERIALIZED_DEGRADATION_REFUSAL_SCHEMA = "ctxc-materialized-degradation-refusal-0.1"
MATERIALIZED_DEGRADATION_SPEC_SCHEMA = "ctxc-materialization-degradation-evaluation-spec-0.2"
MATERIALIZED_DEGRADATION_SPEC_ID = "ctxc-materialization-degradation-heldout-v2"
MATERIALIZED_DEGRADATION_SPEC_SHA256 = (
    "6473ddd7b9b941a644032564ebc235040439291693df8d62e31eb07faed89525"
)
_FAILED_PREFLIGHT_SPEC_SHA256 = "60bc8d14985c8264bbb3430d40320d83fa1c9ec5839f5df77d5552b28d8a0e2d"
_FAILED_PREFLIGHT_REPORT_SHA256 = "370df110ad65914443fa681bed2cd795b1f3921ac72ff5e1eec0694ac774ba75"

_MAX_REPORT_BYTES = 2 * 1024 * 1024
_ARM_ORDER = (
    "strict",
    "lossless_compact_only",
    "lossless_compact_then_single_reallocation",
)
_HELDOUT_CASE_IDS = tuple(f"case-{number:03d}" for number in range(11, 31))
_HELDOUT_TASK_GROUP_IDS = tuple(f"task-group-{number:02d}" for number in range(6, 16))
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
_RECEIPT_DIGEST_FIELDS = (
    "accounting_sha256",
    "component_manifest_sha256",
    "context_bundle_sha256",
    "current_turn_sha256",
    "materialization_sha256",
    "protected_state_sha256",
    "prototype_sha256",
    "receipt_sha256",
    "recent_messages_sha256",
    "recent_tail_omissions_sha256",
    "rendered_memory_sha256",
    "runtime_payload_sha256",
)


def _evaluation_spec() -> dict[str, Any]:
    return {
        "arms": [
            {
                "arm": "strict",
                "degradation_mode": None,
                "maximum_rung": "strict",
                "requested_budget_unchanged": True,
            },
            {
                "arm": "lossless_compact_only",
                "degradation_mode": CONTEXT_WINDOW_DEGRADATION_MODE,
                "maximum_rung": "lossless_compact",
                "requested_budget_unchanged": True,
            },
            {
                "arm": "lossless_compact_then_single_reallocation",
                "degradation_mode": CONTEXT_WINDOW_DEGRADATION_MODE,
                "maximum_rung": "minimal_memory_reallocation",
                "requested_budget_unchanged": True,
            },
        ],
        "budget": dict(_EXPECTED_BUDGET),
        "claim_boundaries": {
            "corpus_is_project_authored_synthetic_naturalistic": True,
            "final_provider_recount_required": True,
            "model_answer_superiority": False,
            "natural_history_claimed": False,
            "provider_ready": False,
            "provider_token_accounting": False,
            "retrieval_included": False,
            "semantic_completeness": False,
        },
        "integrity_rules": {
            "accepted_arms_require_all_recent_and_current_records_exact": True,
            "accepted_arms_require_all_active_and_protected_atoms": True,
            "accepted_arms_require_authority_boundaries": True,
            "accepted_arms_require_current_turn_exactly_once_and_last": True,
            "accepted_arms_require_second_execution_receipt_binding": True,
            "all_arms_require_deterministic_replay": True,
            "historical_strict_acceptance_is_measurement_only": True,
            "mandatory_component_refusals_must_not_be_bypassed": True,
            "strict_arm_uses_default_no_policy_path": True,
        },
        "metrics": [
            "acceptance_or_refusal",
            "required_fact_exact_retention",
            "correction_precedence",
            "protected_fact_retention",
            "current_turn_exactly_once_and_last",
            "rendered_planning_units",
            "omission_inventory",
            "deterministic_result_bytes",
            "receipt_bindings",
        ],
        "oracle_binding": {
            "gold_manifest_set_domain": "ctxc-materialized-degradation-gold-manifest-set-v1",
            "gold_manifest_set_sha256": (
                "5ef0b2c3f60e98337845bedcf110da379fefba39fe8cae5d2706c011167353e5"
            ),
            "pack_id": MATERIALIZED_RETENTION_PACK_ID,
            "pack_raw_bytes": MATERIALIZED_RETENTION_PACK_BYTES,
            "pack_raw_sha256": MATERIALIZED_RETENTION_PACK_RAW_SHA256,
            "pack_schema": MATERIALIZED_RETENTION_PACK_SCHEMA,
            "pack_sha256": MATERIALIZED_RETENTION_PACK_SHA256,
        },
        "planning_unit_profile": "unicode-codepoint-count-v1",
        "schema": MATERIALIZED_DEGRADATION_SPEC_SCHEMA,
        "selection": {
            "case_count": 20,
            "case_ids": list(_HELDOUT_CASE_IDS),
            "case_ids_sha256": ("c620b0f98a3d8009e9b6cb0de5aee63d96980107699c01aebb50c54e18dae5a7"),
            "fixture_change_requires_new_spec_id": True,
            "result_tuning_prohibited": True,
            "split": "heldout",
            "task_group_ids": list(_HELDOUT_TASK_GROUP_IDS),
            "task_group_ids_sha256": (
                "85763a8d79182ad7cdec704c076f16c1b20042ec843cc71d3c1b6de12ec957b3"
            ),
        },
        "spec_id": MATERIALIZED_DEGRADATION_SPEC_ID,
    }


def _validated_spec() -> dict[str, Any]:
    spec = _evaluation_spec()
    if not hmac.compare_digest(_canonical_sha256(spec), MATERIALIZED_DEGRADATION_SPEC_SHA256):
        raise ValueError("materialized degradation evaluation specification changed")
    return {**spec, "spec_sha256": MATERIALIZED_DEGRADATION_SPEC_SHA256}


def _allocation_plan_sha256(case_id: str) -> str:
    return _canonical_sha256(
        {
            "domain": "ctxc-materialized-degradation-allocation-v1",
            "case_id": case_id,
            "evaluation_spec_sha256": MATERIALIZED_DEGRADATION_SPEC_SHA256,
            "pack_sha256": MATERIALIZED_RETENTION_PACK_SHA256,
            "budget": _EXPECTED_BUDGET,
        }
    )


def _public_materialization(
    arm: str,
    *,
    case: Mapping[str, Any],
    sources: list[SourceRecord],
    budget: ContextWindowBudget,
    counter: ExactTokenCounterAdapter,
    allocation_plan_sha256: str,
) -> dict[str, Any]:
    common = {
        "current_turn_id": case["current_turn_id"],
        "budget": budget,
        "token_counter": counter,
        "allocation_plan_sha256": allocation_plan_sha256,
    }
    if arm == "strict":
        return materialize_context(sources, **common)
    if arm == "lossless_compact_then_single_reallocation":
        return materialize_context(
            sources,
            **common,
            degradation_policy=ContextWindowDegradationPolicy(),
        )
    if arm != "lossless_compact_only":
        raise ValueError(f"unsupported materialized degradation evaluation arm: {arm!r}")

    try:
        return materialize_context(sources, **common)
    except ContextWindowError as strict_error:
        if not _is_exact_memory_overflow(strict_error):
            raise

    prototype = _compose_context_window_once(
        sources,
        current_turn_id=case["current_turn_id"],
        budget=budget,
        token_counter=counter,
        memory_rendering_profile=LOSSLESS_COMPACT_MEMORY_RENDERING_PROFILE,
        degradation_mode=CONTEXT_WINDOW_DEGRADATION_MODE,
        degradation_rung="lossless_compact",
        requested_memory_budget_tokens=budget.memory_budget_tokens,
    )
    public_result = materialize_context(
        sources,
        **common,
        degradation_policy=ContextWindowDegradationPolicy(),
    )
    if not hmac.compare_digest(
        prototype.to_bytes(),
        _canonical_bytes(public_result["materialized_context"]["prototype"]),
    ):
        raise ValueError("compact-only prototype differs from the verified public result")
    bundle = public_result["materialized_context"]["prototype"]["context_bundle"]
    if type(bundle) is not dict:
        raise ValueError("compact-only materialization did not produce a ContextBundle")
    metadata = bundle["artifact"]["compiler_metadata"]
    degradation = metadata.get("context_window_degradation")
    if (
        type(degradation) is not dict
        or degradation.get("rung") != "lossless_compact"
        or degradation.get("effective_memory_budget_tokens") != budget.memory_budget_tokens
    ):
        raise ValueError("compact-only materialization crossed the reallocation boundary")
    return public_result


def _refusal_value(error: ContextWindowError) -> dict[str, Any]:
    diagnostic = error.diagnostic
    if diagnostic is not None and type(diagnostic) is not dict:
        raise TypeError("materialized degradation refusal diagnostic must be an exact object")
    return {
        "schema": MATERIALIZED_DEGRADATION_REFUSAL_SCHEMA,
        "error_type": type(error).__name__,
        "message": str(error),
        "reason": error.reason or "invalid_context_window",
        "diagnostic": diagnostic,
    }


def _execute_arm(
    arm: str,
    *,
    case: Mapping[str, Any],
    sources: list[SourceRecord],
    budget: ContextWindowBudget,
    counter: ExactTokenCounterAdapter,
    allocation_plan_sha256: str,
) -> tuple[bytes, dict[str, Any] | None, dict[str, Any] | None]:
    try:
        result = _public_materialization(
            arm,
            case=case,
            sources=sources,
            budget=budget,
            counter=counter,
            allocation_plan_sha256=allocation_plan_sha256,
        )
    except ContextWindowError as error:
        refusal = _refusal_value(error)
        return _canonical_bytes(refusal), None, refusal
    return _canonical_bytes(result), result, None


def _selected_live_items(bundle: dict[str, Any] | None) -> list[dict[str, Any]]:
    if bundle is None:
        return []
    artifact = bundle["artifact"]
    selected = set(artifact["selected_item_ids"])
    return [
        item
        for item in artifact["items"]
        if item["id"] in selected and item["status"] in {"active", "conflicting"}
    ]


def _span_retention(
    gold: list[dict[str, Any]],
    sources: list[SourceRecord],
    *,
    raw_visible_records: list[dict[str, Any]],
    selected_live_items: list[dict[str, Any]],
) -> tuple[list[str], dict[str, list[str]], dict[str, list[int]]]:
    sources_by_id = {source.id: source for source in sources}
    raw_positions_by_source: dict[str, int] = {}
    for position, record in enumerate(raw_visible_records):
        if type(record) is not dict:
            continue
        source = sources_by_id.get(record.get("id"))
        if source is None:
            continue
        expected = {
            "id": source.id,
            "sequence": source.sequence,
            "role": source.role,
            "content": source.content,
            "content_sha256": source.content_sha256,
            "record_sha256": source.record_sha256,
        }
        if record == expected:
            raw_positions_by_source[source.id] = position
    retained: list[str] = []
    memory_item_ids: dict[str, list[str]] = {}
    raw_positions_by_atom: dict[str, list[int]] = {}
    for atom in gold:
        source = sources_by_id.get(atom["source_id"])
        if source is None:
            raise ValueError("gold atom source is absent from the frozen case")
        literal = atom["literal"]
        start = atom["start"]
        end = atom["end"]
        if (
            source.content[start:end] != literal
            or hashlib.sha256(literal.encode("utf-8")).hexdigest() != atom["literal_sha256"]
        ):
            raise ValueError("gold atom span differs from its frozen source")
        matched_items: list[str] = []
        for item in selected_live_items:
            if literal not in item["text"]:
                continue
            for span in item["provenance"]:
                if (
                    span["source_id"] == atom["source_id"]
                    and span["start"] <= start
                    and span["end"] >= end
                    and span["quote"][start - span["start"] : end - span["start"]] == literal
                ):
                    matched_items.append(item["id"])
                    break
        memory_item_ids[atom["atom_id"]] = sorted(set(matched_items))
        raw_position = raw_positions_by_source.get(atom["source_id"])
        raw_positions_by_atom[atom["atom_id"]] = [] if raw_position is None else [raw_position]
        if raw_position is not None or matched_items:
            retained.append(atom["atom_id"])
    return sorted(retained), memory_item_ids, raw_positions_by_atom


def _raw_source_identity(
    expected_sources: list[SourceRecord],
    observed_records: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = [
        {
            "id": source.id,
            "sequence": source.sequence,
            "role": source.role,
            "content": source.content,
            "content_sha256": source.content_sha256,
            "record_sha256": source.record_sha256,
        }
        for source in expected_sources
    ]
    observed_is_exact = type(observed_records) is list and all(
        type(record) is dict for record in observed_records
    )
    observed_sha256 = _canonical_sha256(observed_records) if observed_is_exact else None
    return {
        "record_count": len(observed_records),
        "expected_records_sha256": _canonical_sha256(expected),
        "observed_records_sha256": observed_sha256,
        "exact_ordered_identity": observed_is_exact and observed_records == expected,
    }


def _retention_extensions(
    gold: list[dict[str, Any]],
    provenance_retained_atom_ids: list[str],
    *,
    sources: list[SourceRecord],
    memory_item_ids_by_atom: dict[str, list[str]],
    raw_positions_by_atom: dict[str, list[int]],
    selected_live_items: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    retained = set(provenance_retained_atom_ids)
    required = sorted(
        atom["atom_id"] for atom in gold if atom["status"] == "active" or atom["protected"] is True
    )
    protected = sorted(atom["atom_id"] for atom in gold if atom["protected"] is True)
    active_corrections = sorted(
        atom["atom_id"]
        for atom in gold
        if atom["status"] == "active" and "correction" in atom["categories"]
    )
    active_by_id = {atom["atom_id"]: atom for atom in gold if atom["atom_id"] in active_corrections}
    sources_by_id = {source.id: source for source in sources}
    active_correction_sources = sorted({atom["source_id"] for atom in active_by_id.values()})
    superseded_sources = sorted(
        {atom["source_id"] for atom in gold if atom["status"] == "superseded"}
    )
    selected_live_source_ids = sorted(
        {span["source_id"] for item in selected_live_items for span in item["provenance"]}
    )
    active_corrections_retained = all(atom_id in retained for atom_id in active_corrections)
    prompt_item_ids = [
        item["id"]
        for kind in MemoryKind
        for item in selected_live_items
        if item["kind"] == kind.value
    ]
    memory_positions = {item_id: position for position, item_id in enumerate(prompt_item_ids)}
    raw_offset = len(prompt_item_ids)

    def atom_positions(atom_id: str) -> list[int]:
        return sorted(
            {
                *(memory_positions[item_id] for item_id in memory_item_ids_by_atom[atom_id]),
                *(raw_offset + position for position in raw_positions_by_atom[atom_id]),
            }
        )

    superseded_item_ids: set[str] = set()
    invalid_superseded_item_ids: set[str] = set()
    invalid_superseded_atom_ids: set[str] = set()
    correction_pairs: list[dict[str, Any]] = []
    for atom in gold:
        if atom["status"] != "superseded":
            continue
        matching_items = memory_item_ids_by_atom[atom["atom_id"]]
        superseded_item_ids.update(matching_items)
        superseded_source = sources_by_id[atom["source_id"]]
        linked = []
        for active_id in active_corrections:
            active = active_by_id[active_id]
            active_source = sources_by_id[active["source_id"]]
            if (
                active_source.sequence > superseded_source.sequence
                and atom["literal"] in active_source.content
                and active["literal"] in active_source.content
            ):
                linked.append(active_id)
        stale_positions = atom_positions(atom["atom_id"])
        linked_positions = {
            active_id: atom_positions(active_id) for active_id in linked if active_id in retained
        }
        later_linked = sorted(
            active_id
            for active_id, positions in linked_positions.items()
            if positions
            and all(any(position > stale for position in positions) for stale in stale_positions)
        )
        pair_valid = bool(stale_positions) and atom["protected"] is True and bool(later_linked)
        correction_pairs.append(
            {
                "superseded_atom_id": atom["atom_id"],
                "linked_active_correction_atom_ids": linked,
                "later_retained_active_correction_atom_ids": later_linked,
                "precedence_preserved": pair_valid,
            }
        )
        if not pair_valid:
            invalid_superseded_atom_ids.add(atom["atom_id"])
            invalid_superseded_item_ids.update(matching_items)
    return (
        {
            "atom_ids": required,
            "retained_atom_ids": [atom_id for atom_id in required if atom_id in retained],
            "retention_basis": "exact-source-span-or-exact-raw-source-v1",
            "exact_retention_passed": all(atom_id in retained for atom_id in required),
        },
        {
            "atom_ids": protected,
            "retained_atom_ids": [atom_id for atom_id in protected if atom_id in retained],
            "retention_basis": "exact-source-span-or-exact-raw-source-v1",
            "exact_retention_passed": all(atom_id in retained for atom_id in protected),
        },
        {
            "active_correction_atom_ids": active_corrections,
            "active_correction_source_ids": active_correction_sources,
            "active_correction_exact_retention_passed": active_corrections_retained,
            "superseded_source_ids": superseded_sources,
            "selected_live_source_ids": selected_live_source_ids,
            "selected_superseded_item_ids": sorted(superseded_item_ids),
            "invalid_superseded_atom_ids": sorted(invalid_superseded_atom_ids),
            "invalid_superseded_item_ids": sorted(invalid_superseded_item_ids),
            "correction_pairs": correction_pairs,
            "precedence_basis": "exact-linked-correction-and-final-prompt-order-v1",
            "precedence_preserved": (
                active_corrections_retained
                and bool(correction_pairs)
                and not invalid_superseded_atom_ids
            ),
        },
    )


def _accepted_arm_result(
    arm: str,
    *,
    case: dict[str, Any],
    result: dict[str, Any],
    replay_result: dict[str, Any] | None,
    execution_bytes: bytes,
    deterministic: bool,
    inputs_unchanged_after_first_execution: bool,
    inputs_unchanged_after_second_execution: bool,
    inputs_unchanged: bool,
    allocation_plan_sha256: str,
) -> dict[str, Any]:
    receipt = result["receipt"]
    replay_receipt_sha256 = (
        replay_result["receipt"]["receipt_sha256"]
        if replay_result is not None
        else receipt["receipt_sha256"]
    )
    verified = verify_materialized_context_result(
        result,
        expected_receipt_sha256=replay_receipt_sha256,
        expected_allocation_plan_sha256=allocation_plan_sha256,
    )
    if not hmac.compare_digest(_canonical_bytes(result), _canonical_bytes(verified)):
        raise ValueError("materialized degradation result changed during receipt verification")
    runtime = verified["runtime_payload"]
    prototype = verified["materialized_context"]["prototype"]
    bundle = prototype["context_bundle"]
    recent = runtime["recent_messages"]
    current = runtime["current_turn"]
    omissions = runtime["recent_tail_omissions"]
    recent_ids = [item["id"] for item in recent]
    omission_ids = [item["id"] for item in omissions]
    partition_ids = [*omission_ids, *recent_ids, current["id"]]
    source_ids = [source["id"] for source in case["sources"]]
    source_records = _source_records(case)
    raw_identity = _raw_source_identity(
        source_records[len(omission_ids) :],
        [*recent, current],
    )
    segments = [runtime["verified_context"]]
    segments.extend(item["content"] for item in recent)
    segments.append(current["content"])
    retention = _retention(case["gold_atoms"], segments)
    selected_live_items = _selected_live_items(bundle)
    provenance_retained, memory_item_ids, raw_positions = _span_retention(
        case["gold_atoms"],
        source_records,
        raw_visible_records=[*recent, current],
        selected_live_items=selected_live_items,
    )
    required, protected, correction = _retention_extensions(
        case["gold_atoms"],
        provenance_retained,
        sources=source_records,
        memory_item_ids_by_atom=memory_item_ids,
        raw_positions_by_atom=raw_positions,
        selected_live_items=selected_live_items,
    )
    authority = _authority(bundle, case["gold_atoms"], case["sources"])
    accounting = runtime["accounting"]
    degradation = None
    if bundle is not None:
        degradation = bundle["artifact"]["compiler_metadata"].get("context_window_degradation")
    current_exact = (
        current["id"] == case["current_turn_id"]
        and partition_ids.count(case["current_turn_id"]) == 1
        and partition_ids[-1] == case["current_turn_id"]
        and current["content"] == case["sources"][-1]["content"]
        and current["sequence"] == case["sources"][-1]["sequence"]
        and current["role"] == "user"
    )
    partition_exact = partition_ids == source_ids
    provider_boundaries = (
        runtime["retrieval_result_sha256"] is None
        and runtime["provider_execution_ready"] is False
        and runtime["final_provider_recount_required"] is True
    )
    receipt_replay_bound = replay_result is not None and hmac.compare_digest(
        receipt["receipt_sha256"], replay_receipt_sha256
    )
    integrity = all(
        (
            deterministic,
            inputs_unchanged,
            required["exact_retention_passed"],
            protected["exact_retention_passed"],
            correction["precedence_preserved"],
            authority["boundaries_preserved"],
            raw_identity["exact_ordered_identity"],
            current_exact,
            partition_exact,
            provider_boundaries,
            receipt_replay_bound,
        )
    )
    return {
        "arm": arm,
        "outcome": "accepted",
        "reason": None,
        "tokenizer_identity": runtime["tokenizer_identity"],
        "requested_memory_budget_planning_units": _EXPECTED_BUDGET["memory_budget_tokens"],
        "effective_memory_budget_planning_units": accounting["memory_budget_tokens"],
        "rendered_memory_planning_units": accounting["memory_tokens"],
        "recent_tail_planning_units": accounting["recent_tail_tokens"],
        "current_turn_planning_units": accounting["current_turn_tokens"],
        "occupied_planning_units": accounting["occupied_tokens"],
        "remaining_planning_units": accounting["remaining_tokens"],
        "degradation": degradation,
        "retention": retention,
        "required_fact_retention": required,
        "protected_fact_retention": protected,
        "correction_precedence": correction,
        "authority": authority,
        "raw_source_identity": raw_identity,
        "current_turn_exactly_once_and_last": current_exact,
        "omission_inventory": {
            "compiled_source_ids": omission_ids,
            "compiled_source_ids_sha256": _ordered_ids_sha256(omission_ids),
            "recent_source_ids": recent_ids,
            "recent_source_ids_sha256": _ordered_ids_sha256(recent_ids),
            "current_turn_id": current["id"],
            "partition_source_ids_sha256": _ordered_ids_sha256(partition_ids),
            "partition_exact": partition_exact,
        },
        "receipt_bindings": {
            "allocation_plan_sha256": receipt["allocation_plan_sha256"],
            "fixed_input_sha256": receipt["fixed_input_sha256"],
            "receipt_external_anchor_supplied": False,
            "receipt_replay_anchor": "second-deterministic-execution",
            "receipt_replay_verified": receipt_replay_bound,
            **{field: receipt[field] for field in _RECEIPT_DIGEST_FIELDS},
        },
        "canonical_execution_bytes": len(execution_bytes),
        "canonical_execution_sha256": hashlib.sha256(execution_bytes).hexdigest(),
        "deterministic_replay_byte_identical": deterministic,
        "inputs_unchanged_after_first_execution": inputs_unchanged_after_first_execution,
        "inputs_unchanged_after_second_execution": inputs_unchanged_after_second_execution,
        "inputs_unchanged": inputs_unchanged,
        "provider_boundaries_preserved": provider_boundaries,
        "integrity_passed": integrity,
    }


def _refused_arm_result(
    arm: str,
    *,
    refusal: dict[str, Any],
    execution_bytes: bytes,
    deterministic: bool,
    inputs_unchanged_after_first_execution: bool,
    inputs_unchanged_after_second_execution: bool,
    inputs_unchanged: bool,
) -> dict[str, Any]:
    provider_boundaries = refusal["diagnostic"] is None or (
        refusal["diagnostic"].get("retrieval_result_sha256") is None
        and refusal["diagnostic"].get("provider_execution_ready") is False
        and refusal["diagnostic"].get("final_provider_recount_required") is True
    )
    return {
        "arm": arm,
        "outcome": "refused",
        "reason": refusal["reason"],
        "tokenizer_identity": "unicode-codepoint-count-v1",
        "requested_memory_budget_planning_units": _EXPECTED_BUDGET["memory_budget_tokens"],
        "effective_memory_budget_planning_units": None,
        "rendered_memory_planning_units": None,
        "recent_tail_planning_units": None,
        "current_turn_planning_units": None,
        "occupied_planning_units": None,
        "remaining_planning_units": None,
        "degradation": None,
        "retention": None,
        "required_fact_retention": None,
        "protected_fact_retention": None,
        "correction_precedence": None,
        "authority": None,
        "raw_source_identity": None,
        "current_turn_exactly_once_and_last": None,
        "omission_inventory": None,
        "receipt_bindings": None,
        "refusal": refusal,
        "canonical_execution_bytes": len(execution_bytes),
        "canonical_execution_sha256": hashlib.sha256(execution_bytes).hexdigest(),
        "deterministic_replay_byte_identical": deterministic,
        "inputs_unchanged_after_first_execution": inputs_unchanged_after_first_execution,
        "inputs_unchanged_after_second_execution": inputs_unchanged_after_second_execution,
        "inputs_unchanged": inputs_unchanged,
        "provider_boundaries_preserved": provider_boundaries,
        "integrity_passed": deterministic and inputs_unchanged and provider_boundaries,
    }


def _arm_result(
    arm: str,
    *,
    case: dict[str, Any],
    sources: list[SourceRecord],
    budget: ContextWindowBudget,
    counter: ExactTokenCounterAdapter,
    allocation_plan_sha256: str,
) -> dict[str, Any]:
    source_snapshot = _canonical_bytes([source.to_dict() for source in sources])
    first_bytes, first_result, first_refusal = _execute_arm(
        arm,
        case=case,
        sources=sources,
        budget=budget,
        counter=counter,
        allocation_plan_sha256=allocation_plan_sha256,
    )
    inputs_unchanged_after_first_execution = hmac.compare_digest(
        source_snapshot,
        _canonical_bytes([source.to_dict() for source in sources]),
    )
    second_bytes, second_result, second_refusal = _execute_arm(
        arm,
        case=case,
        sources=sources,
        budget=budget,
        counter=counter,
        allocation_plan_sha256=allocation_plan_sha256,
    )
    deterministic = hmac.compare_digest(first_bytes, second_bytes)
    if (first_result is None) is not (second_result is None):
        deterministic = False
    if (first_refusal is None) is not (second_refusal is None):
        deterministic = False
    inputs_unchanged_after_second_execution = hmac.compare_digest(
        source_snapshot,
        _canonical_bytes([source.to_dict() for source in sources]),
    )
    inputs_unchanged = (
        inputs_unchanged_after_first_execution and inputs_unchanged_after_second_execution
    )
    if first_result is not None:
        return _accepted_arm_result(
            arm,
            case=case,
            result=first_result,
            replay_result=second_result,
            execution_bytes=first_bytes,
            deterministic=deterministic,
            inputs_unchanged_after_first_execution=inputs_unchanged_after_first_execution,
            inputs_unchanged_after_second_execution=inputs_unchanged_after_second_execution,
            inputs_unchanged=inputs_unchanged,
            allocation_plan_sha256=allocation_plan_sha256,
        )
    if first_refusal is None:
        raise RuntimeError("materialized degradation arm lost both result and refusal")
    return _refused_arm_result(
        arm,
        refusal=first_refusal,
        execution_bytes=first_bytes,
        deterministic=deterministic,
        inputs_unchanged_after_first_execution=inputs_unchanged_after_first_execution,
        inputs_unchanged_after_second_execution=inputs_unchanged_after_second_execution,
        inputs_unchanged=inputs_unchanged,
    )


def _case_gold_manifest_sha256(case: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {
            "domain": "ctxc-materialized-degradation-case-gold-manifest-v1",
            "atoms": [
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
            ],
        }
    )


def _gold_manifest_set_sha256(cases: list[dict[str, Any]]) -> str:
    return _canonical_sha256(
        {
            "domain": "ctxc-materialized-degradation-gold-manifest-set-v1",
            "cases": [
                {
                    "case_id": case["case_id"],
                    "gold_manifest_sha256": _case_gold_manifest_sha256(case),
                }
                for case in cases
            ],
        }
    )


def _case_result(case: dict[str, Any]) -> dict[str, Any]:
    sources = _source_records(case)
    budget = ContextWindowBudget(**_EXPECTED_BUDGET)
    counter = ExactTokenCounterAdapter("unicode-codepoint-count-v1", len)
    allocation = _allocation_plan_sha256(case["case_id"])
    arms = [
        _arm_result(
            arm,
            case=case,
            sources=sources,
            budget=budget,
            counter=counter,
            allocation_plan_sha256=allocation,
        )
        for arm in _ARM_ORDER
    ]
    by_arm = {arm["arm"]: arm for arm in arms}
    expected = case["expected_materialization_outcome"]
    strict = by_arm["strict"]
    strict_matches = (
        strict["outcome"] == "accepted"
        if expected == "accepted"
        else strict["outcome"] == "refused" and strict["reason"] == expected
    )
    mandatory_refusal_applicable = expected == "mandatory_components_do_not_fit"
    mandatory_refusal_preserved: bool | None = None
    if mandatory_refusal_applicable:
        mandatory_refusal_preserved = all(
            arm["outcome"] == "refused" and arm["reason"] == expected for arm in arms
        )
    integrity = mandatory_refusal_preserved is not False and all(
        arm["integrity_passed"] is True for arm in arms
    )
    return {
        "case_id": case["case_id"],
        "task_group_id": case["task_group_id"],
        "source_count": len(case["sources"]),
        "source_manifest_sha256": _content_manifest_sha256(case["sources"]),
        "gold_manifest_sha256": _case_gold_manifest_sha256(case),
        "current_turn_id": case["current_turn_id"],
        "frozen_strict_expected_outcome": expected,
        "historical_strict_expectation_matched": strict_matches,
        "mandatory_refusal_applicable": mandatory_refusal_applicable,
        "mandatory_refusal_preserved": mandatory_refusal_preserved,
        "allocation_plan_sha256": allocation,
        "arms": arms,
        "integrity_passed": integrity,
    }


def _summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    arms: dict[str, Any] = {}
    for arm_name in _ARM_ORDER:
        selected = [next(arm for arm in case["arms"] if arm["arm"] == arm_name) for case in cases]
        accepted = [arm for arm in selected if arm["outcome"] == "accepted"]
        refused = [arm for arm in selected if arm["outcome"] == "refused"]
        reasons = sorted({arm["reason"] for arm in refused})
        arms[arm_name] = {
            "case_count": len(selected),
            "accepted_case_count": len(accepted),
            "refused_case_count": len(refused),
            "accepted_case_ids": [
                case["case_id"]
                for case, arm in zip(cases, selected, strict=True)
                if arm["outcome"] == "accepted"
            ],
            "refused_case_ids": [
                case["case_id"]
                for case, arm in zip(cases, selected, strict=True)
                if arm["outcome"] == "refused"
            ],
            "refusal_reason_counts": {
                reason: sum(arm["reason"] == reason for arm in refused) for reason in reasons
            },
            "rendered_memory_planning_units": sum(
                arm["rendered_memory_planning_units"] for arm in accepted
            ),
            "occupied_planning_units": sum(arm["occupied_planning_units"] for arm in accepted),
            "required_fact_exact_retention_pass_count": sum(
                arm["required_fact_retention"]["exact_retention_passed"] for arm in accepted
            ),
            "protected_fact_exact_retention_pass_count": sum(
                arm["protected_fact_retention"]["exact_retention_passed"] for arm in accepted
            ),
            "correction_precedence_pass_count": sum(
                arm["correction_precedence"]["precedence_preserved"] for arm in accepted
            ),
            "raw_source_identity_pass_count": sum(
                arm["raw_source_identity"]["exact_ordered_identity"] for arm in accepted
            ),
            "current_turn_exactly_once_and_last_count": sum(
                arm["current_turn_exactly_once_and_last"] is True for arm in accepted
            ),
            "omitted_source_count": sum(
                len(arm["omission_inventory"]["compiled_source_ids"]) for arm in accepted
            ),
            "deterministic_replay_pass_count": sum(
                arm["deterministic_replay_byte_identical"] is True for arm in selected
            ),
            "inputs_unchanged_pass_count": sum(arm["inputs_unchanged"] is True for arm in selected),
            "receipt_replay_bound_count": sum(
                arm["receipt_bindings"]["receipt_replay_verified"] is True for arm in accepted
            ),
            "integrity_pass_count": sum(arm["integrity_passed"] is True for arm in selected),
        }
    failed_cases = sorted(case["case_id"] for case in cases if case["integrity_passed"] is not True)
    return {
        "case_count": len(cases),
        "failed_case_ids": failed_cases,
        "historical_strict_expectation_match_count": sum(
            case["historical_strict_expectation_matched"] is True for case in cases
        ),
        "mandatory_refusal_expected_count": sum(
            case["mandatory_refusal_applicable"] is True for case in cases
        ),
        "mandatory_refusal_preserved_count": sum(
            case["mandatory_refusal_preserved"] is True for case in cases
        ),
        "arms": arms,
    }


def _evaluate_materialization_degradation() -> dict[str, Any]:
    spec = _validated_spec()
    pack = _load_pack()
    if pack["budget"] != _EXPECTED_BUDGET:
        raise ValueError("materialized degradation evaluation budget differs from the frozen pack")
    cases_by_id = {case["case_id"]: case for case in pack["cases"]}
    selected = [cases_by_id[case_id] for case_id in _HELDOUT_CASE_IDS]
    if any(case["split"] != "heldout" for case in selected):
        raise ValueError("materialized degradation selection crossed the heldout split")
    if sorted({case["task_group_id"] for case in selected}) != list(_HELDOUT_TASK_GROUP_IDS):
        raise ValueError("materialized degradation task-group selection changed")
    if not hmac.compare_digest(
        _gold_manifest_set_sha256(selected),
        spec["oracle_binding"]["gold_manifest_set_sha256"],
    ):
        raise ValueError("materialized degradation gold manifest set changed")
    cases = [_case_result(case) for case in selected]
    summary = _summary(cases)
    unsigned = {
        "schema": MATERIALIZED_DEGRADATION_REPORT_SCHEMA,
        "evaluator_package_version": __version__,
        "evaluation_spec": spec,
        "failed_preflight": {
            "report_bytes_retained": False,
            "report_sha256": _FAILED_PREFLIGHT_REPORT_SHA256,
            "spec_sha256": _FAILED_PREFLIGHT_SPEC_SHA256,
            "status": "failed",
        },
        "pack": {
            "schema": pack["schema"],
            "pack_id": pack["pack_id"],
            "corpus_kind": pack["corpus_kind"],
            "raw_bytes": MATERIALIZED_RETENTION_PACK_BYTES,
            "raw_sha256": MATERIALIZED_RETENTION_PACK_RAW_SHA256,
            "pack_sha256": pack["pack_sha256"],
        },
        "cases": cases,
        "summary": summary,
        "claim_boundaries": {
            "structural_retention_only": True,
            "corpus_is_project_authored_synthetic_naturalistic": True,
            "full_ladder_outcomes_previously_observed": True,
            "compact_only_arm_observed_in_failed_preflight": True,
            "newly_unseen_heldout_claimed": False,
            "historical_accepted_outcomes_used_as_integrity_gate": False,
            "predeclared_mandatory_refusal_outcomes_used_as_integrity_gate": True,
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
        "integrity_passed": not summary["failed_case_ids"],
    }
    report = {**unsigned, "report_sha256": _canonical_sha256(unsigned)}
    _bounded_report_bytes(report)
    return report


def evaluate_materialization_degradation() -> dict[str, Any]:
    """Run the fixed three-arm degradation diagnostic over heldout fixtures."""

    return _evaluate_materialization_degradation()


def verify_materialization_degradation_report(
    value: Mapping[str, Any],
    *,
    expected_report_sha256: str,
) -> dict[str, Any]:
    """Verify one report against an independent digest and deterministic replay."""

    if type(value) is not dict:
        raise TypeError("materialized degradation report must be an exact object")
    canonical_value = _bounded_report_bytes(value)
    expected = _digest(expected_report_sha256, label="expected_report_sha256")
    claimed = _digest(value.get("report_sha256"), label="report_sha256")
    if not hmac.compare_digest(claimed, expected):
        raise ValueError("materialized degradation report does not match the expected digest")
    unsigned = {key: item for key, item in value.items() if key != "report_sha256"}
    if not hmac.compare_digest(claimed, _canonical_sha256(unsigned)):
        raise ValueError("materialized degradation report self-digest mismatch")
    recomputed = _evaluate_materialization_degradation()
    if not hmac.compare_digest(canonical_value, _bounded_report_bytes(recomputed)):
        raise ValueError("materialized degradation report differs from deterministic replay")
    return json.loads(_canonical_bytes(recomputed))


def load_materialization_degradation_report(
    path: str | Path,
    *,
    expected_report_sha256: str,
) -> dict[str, Any]:
    """Load a stable canonical report and verify it by deterministic replay."""

    with _open_stable_text_path(
        path,
        max_input_bytes=_MAX_REPORT_BYTES,
        label="materialized degradation report",
        limit_error=ValueError,
        require_single_link=True,
    ) as stream:
        raw_text = stream.read(_MAX_REPORT_BYTES + 1)
        if type(raw_text) is not str:
            raise TypeError("materialized degradation report stream must return text")
        try:
            raw = raw_text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("materialized degradation report is not valid UTF-8") from exc
    if len(raw) > _MAX_REPORT_BYTES:
        raise ValueError(f"materialized degradation report exceeds {_MAX_REPORT_BYTES} bytes")
    document = _decode_json_payload(
        raw,
        label="materialized degradation report",
        maximum=_MAX_REPORT_BYTES,
    )
    return verify_materialization_degradation_report(
        document,
        expected_report_sha256=expected_report_sha256,
    )


__all__ = [
    "MATERIALIZED_DEGRADATION_REPORT_SCHEMA",
    "MATERIALIZED_DEGRADATION_SPEC_ID",
    "MATERIALIZED_DEGRADATION_SPEC_SCHEMA",
    "MATERIALIZED_DEGRADATION_SPEC_SHA256",
    "evaluate_materialization_degradation",
    "load_materialization_degradation_report",
    "verify_materialization_degradation_report",
]
