from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import context_compiler.materialized_degradation_evaluation as degradation_evaluation
from context_compiler.connector import ExactTokenCounterAdapter
from context_compiler.context_window import ContextWindowBudget
from context_compiler.materialized_degradation_evaluation import (
    _EXPECTED_BUDGET,
    MATERIALIZED_DEGRADATION_SPEC_SHA256,
    _allocation_plan_sha256,
    _arm_result,
    _case_gold_manifest_sha256,
    _gold_manifest_set_sha256,
    _load_pack,
    _public_materialization,
    _raw_source_identity,
    _refused_arm_result,
    _retention_extensions,
    _source_records,
    _span_retention,
    evaluate_materialization_degradation,
    load_materialization_degradation_report,
    verify_materialization_degradation_report,
)
from context_compiler.models import SourceRecord

PACK_RAW_SHA256 = "a17dc61a05ddb0d20811e8ec64c7a2da5f0262e98f24a734189550abb6f7f466"
PACK_SELF_SHA256 = "b8ec4619c86c526293ce26ee3c7f9f5c2ef5ac8637e1d76d8f846f57f222b1cd"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _reseal(value: dict[str, Any]) -> None:
    unsigned = copy.deepcopy(value)
    unsigned.pop("report_sha256", None)
    value["report_sha256"] = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()


def test_fixed_three_arm_report_is_deterministic_and_preserves_boundaries() -> None:
    first = evaluate_materialization_degradation()
    second = evaluate_materialization_degradation()

    assert _canonical_bytes(first) == _canonical_bytes(second)
    assert first["schema"] == "ctxc-materialized-degradation-report-0.2"
    assert first["evaluator_package_version"] == "0.1.1a11"
    assert (
        first["report_sha256"]
        == hashlib.sha256(
            _canonical_bytes({key: item for key, item in first.items() if key != "report_sha256"})
        ).hexdigest()
    )
    assert first["evaluation_spec"]["spec_sha256"] == MATERIALIZED_DEGRADATION_SPEC_SHA256
    assert first["pack"] == {
        "schema": "ctxc-materialized-retention-pack-0.1",
        "pack_id": "ctxc-materialized-retention-naturalistic-v1",
        "corpus_kind": "repository-authored-synthetic-naturalistic-fixture",
        "raw_bytes": 192_498,
        "raw_sha256": PACK_RAW_SHA256,
        "pack_sha256": PACK_SELF_SHA256,
    }
    assert first["integrity_passed"] is True
    assert first["summary"]["failed_case_ids"] == []
    assert first["summary"]["case_count"] == 20
    assert first["claim_boundaries"] == {
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
    }

    expected_arms = [
        "strict",
        "lossless_compact_only",
        "lossless_compact_then_single_reallocation",
    ]
    assert [case["case_id"] for case in first["cases"]] == [
        f"case-{number:03d}" for number in range(11, 31)
    ]
    for case in first["cases"]:
        assert case["integrity_passed"] is True
        assert [arm["arm"] for arm in case["arms"]] == expected_arms
        for arm in case["arms"]:
            assert arm["deterministic_replay_byte_identical"] is True
            assert arm["inputs_unchanged_after_first_execution"] is True
            assert arm["inputs_unchanged_after_second_execution"] is True
            assert arm["inputs_unchanged"] is True
            assert arm["provider_boundaries_preserved"] is True
            assert arm["integrity_passed"] is True
            assert arm["tokenizer_identity"] == "unicode-codepoint-count-v1"
            assert arm["requested_memory_budget_planning_units"] == 1_200
            if arm["outcome"] == "refused":
                assert arm["receipt_bindings"] is None
                continue
            assert arm["reason"] is None
            assert arm["required_fact_retention"]["exact_retention_passed"] is True
            assert arm["protected_fact_retention"]["exact_retention_passed"] is True
            assert arm["correction_precedence"]["precedence_preserved"] is True
            assert arm["authority"]["boundaries_preserved"] is True
            assert arm["raw_source_identity"]["exact_ordered_identity"] is True
            assert arm["current_turn_exactly_once_and_last"] is True
            assert arm["omission_inventory"]["partition_exact"] is True
            assert arm["receipt_bindings"]["receipt_external_anchor_supplied"] is False
            assert arm["receipt_bindings"]["receipt_replay_verified"] is True
            assert (
                arm["receipt_bindings"]["allocation_plan_sha256"]
                == (case["allocation_plan_sha256"])
            )

        strict, compact, ladder = case["arms"]
        assert strict["degradation"] is None
        if compact["outcome"] == "accepted" and compact["degradation"] is not None:
            assert compact["degradation"]["rung"] == "lossless_compact"
            assert compact["effective_memory_budget_planning_units"] == 1_200
        if ladder["outcome"] == "accepted" and ladder["degradation"] is not None:
            assert ladder["degradation"]["rung"] in {
                "lossless_compact",
                "minimal_memory_reallocation_compact",
                "minimal_memory_reallocation_standard",
            }

    mandatory = {
        case["case_id"]: case
        for case in first["cases"]
        if case["frozen_strict_expected_outcome"] == "mandatory_components_do_not_fit"
    }
    assert set(mandatory) == {"case-028", "case-030"}
    for case in mandatory.values():
        assert case["mandatory_refusal_applicable"] is True
        assert all(
            arm["outcome"] == "refused" and arm["reason"] == "mandatory_components_do_not_fit"
            for arm in case["arms"]
        )
    assert first["summary"]["historical_strict_expectation_match_count"] == sum(
        case["historical_strict_expectation_matched"] is True for case in first["cases"]
    )
    assert first["summary"]["mandatory_refusal_expected_count"] == 2
    assert first["summary"]["mandatory_refusal_preserved_count"] == 2
    assert all(
        case["mandatory_refusal_preserved"] is None
        for case in first["cases"]
        if case["mandatory_refusal_applicable"] is False
    )
    assert first["failed_preflight"] == {
        "report_bytes_retained": False,
        "report_sha256": "370df110ad65914443fa681bed2cd795b1f3921ac72ff5e1eec0694ac774ba75",
        "spec_sha256": "60bc8d14985c8264bbb3430d40320d83fa1c9ec5839f5df77d5552b28d8a0e2d",
        "status": "failed",
    }


def test_report_verification_requires_independent_digest_and_exact_replay() -> None:
    report = evaluate_materialization_degradation()
    expected = report["report_sha256"]
    assert (
        verify_materialization_degradation_report(
            report,
            expected_report_sha256=expected,
        )
        == report
    )

    with pytest.raises(ValueError, match="expected digest"):
        verify_materialization_degradation_report(
            report,
            expected_report_sha256="0" * 64,
        )

    tampered = copy.deepcopy(report)
    tampered["summary"]["arms"]["strict"]["accepted_case_count"] += 1
    _reseal(tampered)
    with pytest.raises(ValueError, match="deterministic replay"):
        verify_materialization_degradation_report(
            tampered,
            expected_report_sha256=tampered["report_sha256"],
        )


def test_report_loader_requires_canonical_create_once_bytes(tmp_path: Path) -> None:
    report = evaluate_materialization_degradation()
    path = tmp_path / "report.json"
    path.write_bytes(_canonical_bytes(report) + b"\n")

    assert (
        load_materialization_degradation_report(
            path,
            expected_report_sha256=report["report_sha256"],
        )
        == report
    )

    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="one canonical JSON line"):
        load_materialization_degradation_report(
            path,
            expected_report_sha256=report["report_sha256"],
        )


def test_report_verifier_rejects_mapping_subclasses_before_use() -> None:
    class ReportSubclass(dict[str, Any]):
        pass

    report = evaluate_materialization_degradation()
    with pytest.raises(TypeError, match="exact object"):
        verify_materialization_degradation_report(
            ReportSubclass(report),
            expected_report_sha256=report["report_sha256"],
        )


def test_exact_span_retention_rejects_source_substitution_and_stale_precedence() -> None:
    literal = "retry_limit=2"
    correction_content = "Correction: supersede retry_limit=2; use retry_limit=4."
    active_literal = "retry_limit=4"
    active_start = correction_content.index(active_literal)
    gold = [
        {
            "atom_id": "old",
            "literal": literal,
            "literal_sha256": hashlib.sha256(literal.encode()).hexdigest(),
            "source_id": "source-old",
            "start": 0,
            "end": len(literal),
            "status": "superseded",
            "categories": ["correction"],
            "protected": True,
        },
        {
            "atom_id": "new",
            "literal": active_literal,
            "literal_sha256": hashlib.sha256(active_literal.encode()).hexdigest(),
            "source_id": "source-new",
            "start": active_start,
            "end": active_start + len(active_literal),
            "status": "active",
            "categories": ["correction"],
            "protected": True,
        },
    ]
    sources = [
        SourceRecord.create(id="source-old", sequence=1, role="user", content=literal),
        SourceRecord.create(
            id="source-new",
            sequence=2,
            role="user",
            content=correction_content,
        ),
        SourceRecord.create(id="substitute", sequence=3, role="user", content=literal),
    ]
    substituted_item = {
        "id": "item-substitute",
        "kind": "constraint",
        "text": literal,
        "provenance": [
            {"source_id": "substitute", "start": 0, "end": len(literal), "quote": literal}
        ],
    }
    retained, _items, _raw = _span_retention(
        gold,
        sources,
        raw_visible_records=[],
        selected_live_items=[substituted_item],
    )
    assert retained == []

    stale_item = copy.deepcopy(substituted_item)
    stale_item["id"] = "item-old"
    stale_item["provenance"][0]["source_id"] = "source-old"
    retained, memory_items, raw_positions = _span_retention(
        gold,
        sources,
        raw_visible_records=[],
        selected_live_items=[stale_item],
    )
    _required, _protected, correction = _retention_extensions(
        gold,
        retained,
        sources=sources,
        memory_item_ids_by_atom=memory_items,
        raw_positions_by_atom=raw_positions,
        selected_live_items=[stale_item],
    )
    assert correction["invalid_superseded_item_ids"] == ["item-old"]
    assert correction["precedence_preserved"] is False

    exact_active_raw = {
        key: value
        for key, value in sources[1].to_dict().items()
        if key in {"id", "sequence", "role", "content", "content_sha256", "record_sha256"}
    }
    retained, memory_items, raw_positions = _span_retention(
        gold,
        sources,
        raw_visible_records=[exact_active_raw],
        selected_live_items=[stale_item],
    )
    _required, _protected, correction = _retention_extensions(
        gold,
        retained,
        sources=sources,
        memory_item_ids_by_atom=memory_items,
        raw_positions_by_atom=raw_positions,
        selected_live_items=[stale_item],
    )
    assert correction["invalid_superseded_item_ids"] == []
    assert correction["precedence_preserved"] is True


def test_raw_retention_rejects_same_id_content_and_digest_substitution() -> None:
    literal = "src/worker.py:144"
    source = SourceRecord.create(
        id="source-exact",
        sequence=8,
        role="tool",
        content=f"Failure remains at {literal}.",
    )
    start = source.content.index(literal)
    gold = [
        {
            "atom_id": "exact-location",
            "literal": literal,
            "literal_sha256": hashlib.sha256(literal.encode()).hexdigest(),
            "source_id": source.id,
            "start": start,
            "end": start + len(literal),
            "status": "active",
            "categories": ["detail"],
            "protected": True,
        }
    ]
    runtime_record = {
        key: value
        for key, value in source.to_dict().items()
        if key in {"id", "sequence", "role", "content", "content_sha256", "record_sha256"}
    }
    runtime_record["content"] = "Failure remains at src/worker.py:145."

    retained, _memory, raw_positions = _span_retention(
        gold,
        [source],
        raw_visible_records=[runtime_record],
        selected_live_items=[],
    )

    assert retained == []
    assert raw_positions == {"exact-location": []}


def test_raw_partition_identity_rejects_same_id_non_gold_record_substitution() -> None:
    recent = SourceRecord.create(
        id="recent-no-gold",
        sequence=9,
        role="assistant",
        content="Background chatter without an oracle atom.",
    )
    current = SourceRecord.create(
        id="current-turn",
        sequence=10,
        role="user",
        content="Apply the retained correction now.",
    )
    observed = [
        {
            key: value
            for key, value in source.to_dict().items()
            if key in {"id", "sequence", "role", "content", "content_sha256", "record_sha256"}
        }
        for source in (recent, current)
    ]
    observed[0]["role"] = "tool"
    observed[0]["content"] = "Substituted chatter with the same source id."

    identity = _raw_source_identity([recent, current], observed)

    assert identity["record_count"] == 2
    assert identity["expected_records_sha256"] != identity["observed_records_sha256"]
    assert identity["exact_ordered_identity"] is False


def test_correction_precedence_rejects_unlinked_active_fact() -> None:
    old_literal = "retry_limit=2"
    active_literal = "retry_limit=4"
    old_source = SourceRecord.create(
        id="source-old",
        sequence=1,
        role="user",
        content=old_literal,
    )
    active_source = SourceRecord.create(
        id="source-new",
        sequence=2,
        role="user",
        content=f"Use {active_literal}.",
    )
    gold = [
        {
            "atom_id": "old",
            "literal": old_literal,
            "literal_sha256": hashlib.sha256(old_literal.encode()).hexdigest(),
            "source_id": old_source.id,
            "start": 0,
            "end": len(old_literal),
            "status": "superseded",
            "categories": ["correction"],
            "protected": True,
        },
        {
            "atom_id": "new",
            "literal": active_literal,
            "literal_sha256": hashlib.sha256(active_literal.encode()).hexdigest(),
            "source_id": active_source.id,
            "start": 4,
            "end": 4 + len(active_literal),
            "status": "active",
            "categories": ["correction"],
            "protected": True,
        },
    ]
    items = [
        {
            "id": "item-old",
            "kind": "constraint",
            "text": old_literal,
            "provenance": [
                {
                    "source_id": old_source.id,
                    "start": 0,
                    "end": len(old_literal),
                    "quote": old_literal,
                }
            ],
        },
        {
            "id": "item-new",
            "kind": "user_correction",
            "text": active_literal,
            "provenance": [
                {
                    "source_id": active_source.id,
                    "start": 0,
                    "end": len(active_source.content),
                    "quote": active_source.content,
                }
            ],
        },
    ]
    retained, memory_items, raw_positions = _span_retention(
        gold,
        [old_source, active_source],
        raw_visible_records=[],
        selected_live_items=items,
    )
    _required, _protected, correction = _retention_extensions(
        gold,
        retained,
        sources=[old_source, active_source],
        memory_item_ids_by_atom=memory_items,
        raw_positions_by_atom=raw_positions,
        selected_live_items=items,
    )

    assert correction["active_correction_exact_retention_passed"] is True
    assert correction["invalid_superseded_atom_ids"] == ["old"]
    assert correction["precedence_preserved"] is False


def test_correction_precedence_rejects_linked_correction_rendered_first() -> None:
    old_literal = "retry_limit=2"
    active_literal = "retry_limit=4"
    old_source = SourceRecord.create(
        id="source-old",
        sequence=1,
        role="user",
        content=old_literal,
    )
    correction_content = f"Correction: replace {old_literal} with {active_literal}."
    active_source = SourceRecord.create(
        id="source-new",
        sequence=2,
        role="user",
        content=correction_content,
    )
    active_start = correction_content.index(active_literal)
    gold = [
        {
            "atom_id": "old",
            "literal": old_literal,
            "literal_sha256": hashlib.sha256(old_literal.encode()).hexdigest(),
            "source_id": old_source.id,
            "start": 0,
            "end": len(old_literal),
            "status": "superseded",
            "categories": ["correction"],
            "protected": True,
        },
        {
            "atom_id": "new",
            "literal": active_literal,
            "literal_sha256": hashlib.sha256(active_literal.encode()).hexdigest(),
            "source_id": active_source.id,
            "start": active_start,
            "end": active_start + len(active_literal),
            "status": "active",
            "categories": ["correction"],
            "protected": True,
        },
    ]
    items = [
        {
            "id": "item-old",
            "kind": "constraint",
            "text": old_literal,
            "provenance": [
                {
                    "source_id": old_source.id,
                    "start": 0,
                    "end": len(old_literal),
                    "quote": old_literal,
                }
            ],
        },
        {
            "id": "item-new",
            "kind": "goal",
            "text": active_literal,
            "provenance": [
                {
                    "source_id": active_source.id,
                    "start": 0,
                    "end": len(active_source.content),
                    "quote": active_source.content,
                }
            ],
        },
    ]
    retained, memory_items, raw_positions = _span_retention(
        gold,
        [old_source, active_source],
        raw_visible_records=[],
        selected_live_items=items,
    )
    _required, _protected, correction = _retention_extensions(
        gold,
        retained,
        sources=[old_source, active_source],
        memory_item_ids_by_atom=memory_items,
        raw_positions_by_atom=raw_positions,
        selected_live_items=items,
    )

    assert correction["active_correction_exact_retention_passed"] is True
    assert correction["correction_pairs"][0]["linked_active_correction_atom_ids"] == ["new"]
    assert correction["correction_pairs"][0]["later_retained_active_correction_atom_ids"] == []
    assert correction["precedence_preserved"] is False


def test_gold_manifest_digest_domains_have_fixed_canonical_vectors() -> None:
    atom = {
        "atom_id": "atom-1",
        "literal_sha256": hashlib.sha256(b"x").hexdigest(),
        "source_id": "source-1",
        "start": 0,
        "end": 1,
        "status": "active",
        "categories": ["detail"],
        "authority_expectation": "authoritative_memory",
        "protected": True,
    }
    case = {"case_id": "case-vector", "gold_atoms": [atom]}
    case_digest = _case_gold_manifest_sha256(case)
    assert case_digest == "3846c24db37504106158266bb258827c8cd3cf680d61a3e962c9763bb12fd618"
    assert case_digest != hashlib.sha256(_canonical_bytes([atom])).hexdigest()

    set_digest = _gold_manifest_set_sha256([case])
    assert set_digest == "2eb6a7b9d2598a537a20bb1b7d3307412359564b15ccdf656d997d60071326ad"
    assert (
        set_digest
        != hashlib.sha256(
            _canonical_bytes([{"case_id": "case-vector", "gold_manifest_sha256": case_digest}])
        ).hexdigest()
    )


def test_input_mutation_after_first_execution_cannot_be_hidden_by_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = [SourceRecord.create(id="source-1", sequence=1, role="user", content="one")]
    injected = SourceRecord.create(id="source-2", sequence=2, role="assistant", content="two")
    call_count = 0
    refusal = {
        "schema": "ctxc-materialized-degradation-refusal-0.1",
        "error_type": "ContextWindowError",
        "message": "refused",
        "reason": "compiled_memory_not_verified",
        "diagnostic": None,
    }

    def execute(*args: object, **kwargs: object) -> tuple[bytes, None, dict[str, Any]]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            sources.append(injected)
        else:
            sources.pop()
        return _canonical_bytes(refusal), None, copy.deepcopy(refusal)

    monkeypatch.setattr(degradation_evaluation, "_execute_arm", execute)
    result = _arm_result(
        "strict",
        case={"case_id": "mutation-probe"},
        sources=sources,
        budget=ContextWindowBudget(**_EXPECTED_BUDGET),
        counter=ExactTokenCounterAdapter("unicode-codepoint-count-v1", len),
        allocation_plan_sha256="0" * 64,
    )

    assert result["inputs_unchanged_after_first_execution"] is False
    assert result["inputs_unchanged_after_second_execution"] is True
    assert result["inputs_unchanged"] is False
    assert result["integrity_passed"] is False
    assert [source.id for source in sources] == ["source-1"]


def test_compact_only_success_path_and_refusal_boundaries_are_gated() -> None:
    case = next(case for case in _load_pack()["cases"] if case["case_id"] == "case-011")
    budget_values = dict(_EXPECTED_BUDGET)
    budget_values["memory_budget_tokens"] = 1_502
    result = _public_materialization(
        "lossless_compact_only",
        case=case,
        sources=_source_records(case),
        budget=ContextWindowBudget(**budget_values),
        counter=ExactTokenCounterAdapter("unicode-codepoint-count-v1", len),
        allocation_plan_sha256=_allocation_plan_sha256(case["case_id"]),
    )
    metadata = result["materialized_context"]["prototype"]["context_bundle"]["artifact"][
        "compiler_metadata"
    ]
    assert metadata["context_window_degradation"]["rung"] == "lossless_compact"

    refusal = _refused_arm_result(
        "strict",
        refusal={
            "schema": "ctxc-materialized-degradation-refusal-0.1",
            "error_type": "ContextWindowError",
            "message": "refused",
            "reason": "compiled_memory_not_verified",
            "diagnostic": {
                "retrieval_result_sha256": "0" * 64,
                "provider_execution_ready": True,
                "final_provider_recount_required": False,
            },
        },
        execution_bytes=b"{}",
        deterministic=True,
        inputs_unchanged_after_first_execution=True,
        inputs_unchanged_after_second_execution=True,
        inputs_unchanged=True,
    )
    assert refusal["provider_boundaries_preserved"] is False
    assert refusal["integrity_passed"] is False
