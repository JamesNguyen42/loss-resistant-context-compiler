from __future__ import annotations

import copy
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

import context_compiler.materialized_evaluation as evaluation_module
from context_compiler.materialized_evaluation import (
    evaluate_materialization_retention,
    load_materialization_retention_report,
    verify_materialization_retention_report,
)

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


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _pack_bytes() -> bytes:
    resource = resources.files("context_compiler").joinpath(
        "data/materialized_retention_pack_v1.json"
    )
    return resource.read_bytes()


def _pack() -> dict[str, Any]:
    return json.loads(_pack_bytes())


def _reseal(report: dict[str, Any]) -> None:
    unsigned = copy.deepcopy(report)
    unsigned.pop("report_sha256", None)
    report["report_sha256"] = _canonical_sha256(unsigned)


def _assert_no_float(value: Any) -> None:
    if type(value) is dict:
        for key, item in value.items():
            assert type(key) is str
            _assert_no_float(item)
    elif type(value) is list:
        for item in value:
            _assert_no_float(item)
    else:
        assert type(value) is not float


def _iter_strings(value: Any):
    if type(value) is str:
        yield value
    elif type(value) is list:
        for item in value:
            yield from _iter_strings(item)
    elif type(value) is dict:
        for key, item in value.items():
            yield key
            yield from _iter_strings(item)


class _ExplodingDict(dict[str, Any]):
    def __iter__(self):
        raise AssertionError("mapping subclass was evaluated")

    def __getitem__(self, key):
        raise AssertionError("mapping subclass was evaluated")

    def get(self, key, default=None):
        raise AssertionError("mapping subclass was evaluated")

    def items(self):
        raise AssertionError("mapping subclass was evaluated")

    def keys(self):
        raise AssertionError("mapping subclass was evaluated")

    def values(self):
        raise AssertionError("mapping subclass was evaluated")


class _StringSubclass(str):
    pass


def test_frozen_pack_has_exact_identity_split_and_claim_boundaries() -> None:
    raw = _pack_bytes()
    pack = json.loads(raw)

    assert len(raw) == 192_498
    assert hashlib.sha256(raw).hexdigest() == PACK_RAW_SHA256
    assert pack["schema"] == "ctxc-materialized-retention-pack-0.1"
    assert pack["pack_id"] == "ctxc-materialized-retention-naturalistic-v1"
    assert pack["corpus_kind"] == ("repository-authored-synthetic-naturalistic-fixture")
    assert pack["license"] == {
        "origin": "project-authored",
        "raw_external_records": False,
        "spdx": "MIT",
    }
    assert pack["claim_boundaries"] == {
        "final_provider_recount_required": True,
        "model_answer_superiority": False,
        "natural_history": False,
        "provider_ready": False,
        "retrieval_included": False,
        "semantic_completeness": False,
    }
    assert pack["planning_unit_profile"] == "unicode-codepoint-count-v1"
    assert pack["split_policy"] == {
        "development_case_count": 6,
        "fixture_change_requires_new_pack_id": True,
        "group_disjoint": True,
        "heldout_case_count": 20,
        "name": "task-group-disjoint-4-6-20-v1",
        "result_tuning_prohibited": True,
        "train_case_count": 4,
    }
    assert pack["budget"] == {
        "fixed_input_tokens": 0,
        "hard_limit_tokens": 2_200,
        "maximum_recent_messages": 3,
        "memory_budget_tokens": 1_200,
        "minimum_recent_messages": 2,
        "per_message_overhead_tokens": 2,
        "reserved_output_tokens": 128,
        "safety_margin_tokens": 64,
    }

    unsigned = dict(pack)
    claimed = unsigned.pop("pack_sha256")
    assert claimed == PACK_SELF_SHA256
    assert _canonical_sha256(unsigned) == claimed

    cases = pack["cases"]
    assert len(cases) == 30
    assert [case["case_id"] for case in cases] == [f"case-{number:03d}" for number in range(1, 31)]
    by_split = {
        split: [case for case in cases if case["split"] == split]
        for split in ("train", "development", "heldout")
    }
    assert {key: len(value) for key, value in by_split.items()} == {
        "train": 4,
        "development": 6,
        "heldout": 20,
    }
    grouped = {
        split: {case["task_group_id"] for case in split_cases}
        for split, split_cases in by_split.items()
    }
    assert all(
        left.isdisjoint(right)
        for index, left in enumerate(grouped.values())
        for right in list(grouped.values())[index + 1 :]
    )
    assert [
        case["case_id"]
        for case in cases
        if case["expected_materialization_outcome"] == "mandatory_components_do_not_fit"
    ] == ["case-028", "case-030"]

    for case in cases:
        sources = case["sources"]
        assert 16 <= len(sources) <= 20
        assert [source["sequence"] for source in sources] == list(range(1, len(sources) + 1))
        assert len({source["id"] for source in sources}) == len(sources)
        assert sources[-1]["id"] == case["current_turn_id"]
        assert sources[-1]["role"] == "user"
        source_by_id = {source["id"]: source for source in sources}
        for atom in case["gold_atoms"]:
            source = source_by_id[atom["source_id"]]
            literal = source["content"][atom["start"] : atom["end"]]
            assert literal == atom["literal"]
            assert hashlib.sha256(literal.encode("utf-8")).hexdigest() == (atom["literal_sha256"])

    all_sources = [(case["case_id"], source) for case in cases for source in case["sources"]]
    all_atoms = [(case, atom) for case in cases for atom in case["gold_atoms"]]
    for case, atom in all_atoms:
        correction_source_ids = {
            candidate["source_id"]
            for candidate in case["gold_atoms"]
            if candidate["status"] == "active" and "correction" in candidate["categories"]
        }
        assert len(correction_source_ids) == 1
        occurrences = [
            (case_id, source["id"])
            for case_id, source in all_sources
            if atom["literal"] in source["content"]
        ]
        expected = {(case["case_id"], atom["source_id"])}
        if atom["status"] == "superseded":
            expected.add((case["case_id"], next(iter(correction_source_ids))))
        assert set(occurrences) == expected
        assert len(occurrences) == len(expected)

    literals = [atom["literal"] for _, atom in all_atoms]
    assert all(
        left not in right
        for left_index, left in enumerate(literals)
        for right_index, right in enumerate(literals)
        if left_index != right_index
    )


def test_pack_resource_rejects_hard_links_without_touching_the_fixture(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw = _pack_bytes()
    target = tmp_path / "target.json"
    resource = tmp_path / "data" / "materialized_retention_pack_v1.json"
    resource.parent.mkdir()
    target.write_bytes(raw)
    try:
        os.link(target, resource)
    except OSError as exc:
        pytest.skip(f"hard-link creation is unavailable: {exc}")
    with monkeypatch.context() as scoped:
        scoped.setattr(evaluation_module.resources, "files", lambda _: tmp_path)
        with pytest.raises(ValueError, match="must not have hard links"):
            evaluation_module._pack_bytes()
    assert _pack_bytes() == raw


def test_pack_resource_rejects_oversize_before_reading(
    tmp_path: Path,
    monkeypatch,
) -> None:
    resource = tmp_path / "data" / "materialized_retention_pack_v1.json"
    resource.parent.mkdir()
    resource.write_bytes(b"x" * (256 * 1024 + 1))
    monkeypatch.setattr(evaluation_module.resources, "files", lambda _: tmp_path)

    with pytest.raises(ValueError, match="exceeds 262144 bytes"):
        evaluation_module._pack_bytes()


def test_pack_resource_read_errors_propagate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    resource = tmp_path / "data" / "materialized_retention_pack_v1.json"
    resource.parent.mkdir()
    resource.write_bytes(_pack_bytes())
    monkeypatch.setattr(evaluation_module.resources, "files", lambda _: tmp_path)

    @contextmanager
    def failing_open(*args, **kwargs):
        del args, kwargs
        raise OSError("synthetic package resource read failure")
        yield

    monkeypatch.setattr(evaluation_module, "_open_stable_text_path", failing_open)
    with pytest.raises(OSError, match="synthetic package resource read failure"):
        evaluation_module._pack_bytes()


def test_heldout_report_is_deterministic_detached_and_resource_read_only() -> None:
    before = _pack_bytes()
    first = evaluate_materialization_retention()
    second = evaluate_materialization_retention(selected_split="heldout")

    assert first == second
    assert _canonical_bytes(first) == _canonical_bytes(second)
    assert _pack_bytes() == before
    assert set(first) == {
        "budget",
        "cases",
        "claim_boundaries",
        "evaluator_package_version",
        "integrity_passed",
        "pack",
        "report_sha256",
        "schema",
        "selection",
        "summary",
    }
    assert first["schema"] == "ctxc-materialized-retention-report-0.1"
    assert first["evaluator_package_version"] == "0.1.1a12"
    assert first["integrity_passed"] is False
    assert first["pack"] == {
        "corpus_kind": "repository-authored-synthetic-naturalistic-fixture",
        "pack_id": "ctxc-materialized-retention-naturalistic-v1",
        "pack_sha256": PACK_SELF_SHA256,
        "planning_unit_profile": "unicode-codepoint-count-v1",
        "raw_bytes": 192_498,
        "raw_sha256": PACK_RAW_SHA256,
        "schema": "ctxc-materialized-retention-pack-0.1",
    }
    assert first["selection"]["split"] == "heldout"
    assert len(first["cases"]) == 20
    unsigned = dict(first)
    claimed = unsigned.pop("report_sha256")
    assert claimed == _canonical_sha256(unsigned)
    assert [case["case_id"] for case in first["cases"]] == [
        f"case-{number:03d}" for number in range(11, 31)
    ]
    case_ids = [case["case_id"] for case in first["cases"]]
    task_group_ids = [f"task-group-{number:02d}" for number in range(6, 16)]
    assert first["selection"] == {
        "case_count": 20,
        "case_ids": case_ids,
        "case_ids_sha256": _canonical_sha256(case_ids),
        "group_disjoint": True,
        "result_tuning_prohibited": True,
        "split": "heldout",
        "task_group_ids": task_group_ids,
        "task_group_ids_sha256": _canonical_sha256(task_group_ids),
    }
    assert all(
        set(case)
        == {
            "allocation_plan_sha256",
            "arms",
            "case_id",
            "current_turn_id",
            "expected_materialization_outcome",
            "expectation_matched",
            "gold_manifest_sha256",
            "integrity_passed",
            "observed_materialization_outcome",
            "source_count",
            "source_manifest_sha256",
            "split",
            "task_group_id",
        }
        for case in first["cases"]
    )
    assert all(
        [arm["arm"] for arm in case["arms"]] == ["full_history", "tail_only", "lrcc_materialized"]
        for case in first["cases"]
    )
    assert all(
        set(arm)
        == {
            "arm",
            "attempted_planning_units",
            "authority",
            "compiled_source_ids",
            "compiled_source_ids_sha256",
            "content_manifest_sha256",
            "current_turn_last",
            "current_turn_occurrences",
            "included_source_ids",
            "included_source_ids_sha256",
            "materialization_digests",
            "omitted_source_ids",
            "omitted_source_ids_sha256",
            "outcome",
            "planning_units",
            "reason",
            "retention",
        }
        for case in first["cases"]
        for arm in case["arms"]
    )
    assert (
        verify_materialization_retention_report(
            first,
            expected_report_sha256=first["report_sha256"],
        )
        == first
    )
    assert first is not second
    assert first["cases"] is not second["cases"]


def test_evaluation_is_concurrently_byte_identical() -> None:
    with ThreadPoolExecutor(max_workers=4) as executor:
        reports = list(
            executor.map(
                lambda _: evaluate_materialization_retention(selected_split="development"),
                range(4),
            )
        )
    encoded = [_canonical_bytes(report) for report in reports]
    assert encoded[1:] == encoded[:-1]
    assert len({report["report_sha256"] for report in reports}) == 1
    assert len({id(report) for report in reports}) == 4


def test_generated_report_is_bounded_before_and_after_digest(monkeypatch) -> None:
    bounded_calls: list[bool] = []
    original = evaluation_module._bounded_report_bytes

    def tracking_bound(value: Any) -> bytes:
        bounded_calls.append(type(value) is dict and "report_sha256" in value)
        return original(value)

    monkeypatch.setattr(evaluation_module, "_bounded_report_bytes", tracking_bound)
    report = evaluate_materialization_retention(selected_split="train")

    assert bounded_calls == [False, True]
    encoded = _canonical_bytes(report)
    assert len(encoded) <= evaluation_module._MAX_REPORT_BYTES
    assert original(report) == encoded


@pytest.mark.parametrize(
    "selected_split",
    (True, 1, None, "Heldout", "heldout\x00", _StringSubclass("heldout")),
)
def test_evaluation_rejects_split_coercion_and_subclasses(
    selected_split: object,
) -> None:
    with pytest.raises((TypeError, ValueError), match="selected_split"):
        evaluate_materialization_retention(selected_split=selected_split)  # type: ignore[arg-type]


def test_report_retains_every_case_and_no_raw_fixture_text_or_host_metadata() -> None:
    pack = _pack()
    report = evaluate_materialization_retention(selected_split="heldout")
    report_strings = set(_iter_strings(report))
    serialized = _canonical_bytes(report).decode("utf-8")

    assert [
        case["case_id"]
        for case in report["cases"]
        if case["expected_materialization_outcome"] == "mandatory_components_do_not_fit"
    ] == ["case-028", "case-030"]
    assert [
        case["case_id"]
        for case in report["cases"]
        if case["observed_materialization_outcome"] == "mandatory_components_do_not_fit"
    ] == ["case-028", "case-030"]
    assert [
        case["case_id"]
        for case in report["cases"]
        if case["observed_materialization_outcome"] == "compiled_memory_not_verified"
    ] == [
        *(f"case-{number:03d}" for number in range(11, 28)),
        "case-029",
    ]
    assert [case["case_id"] for case in report["cases"] if case["integrity_passed"] is True] == [
        "case-028",
        "case-030",
    ]
    for case in pack["cases"]:
        if case["split"] != "heldout":
            continue
        for source in case["sources"]:
            assert source["content"] not in report_strings
            assert source["content"] not in serialized
        for atom in case["gold_atoms"]:
            assert atom["literal"] not in report_strings
            assert atom["literal"] not in serialized

    assert not {
        "created_at",
        "cwd",
        "duration_seconds",
        "elapsed_seconds",
        "executable",
        "host_path",
        "platform",
        "python_version",
        "timestamp",
    }.intersection(report_strings)
    assert "C:\\Users\\" not in serialized
    assert "/home/" not in serialized
    assert "OneDrive" not in serialized
    _assert_no_float(report)


def test_report_accounting_partition_retention_and_authority_are_exact() -> None:
    pack = _pack()
    report = evaluate_materialization_retention(selected_split="heldout")
    pack_cases = {case["case_id"]: case for case in pack["cases"]}

    assert report["claim_boundaries"] == {
        "final_provider_recount_required": True,
        "inference_status": "not_run",
        "model_answer_superiority_claimed": False,
        "natural_history_claimed": False,
        "provider_execution_ready": False,
        "provider_token_accounting": False,
        "retrieval_included": False,
        "retrieval_status": "not_run",
        "semantic_completeness_claimed": False,
        "structural_retention_only": True,
        "task_completion_measured": False,
    }
    assert report["budget"] == pack["budget"]

    for case in report["cases"]:
        source_case = pack_cases[case["case_id"]]
        sources = source_case["sources"]
        all_ids = [source["id"] for source in sources]
        source_by_id = {source["id"]: source for source in sources}
        must_not_promote = sorted(
            source["id"] for source in sources if source["role"] in {"assistant", "tool"}
        )
        instruction_shaped_probes = sorted(
            atom["source_id"]
            for atom in source_case["gold_atoms"]
            if atom["authority_expectation"] == "untrusted_evidence_only"
        )

        assert case["source_count"] == len(sources) == 18
        assert case["current_turn_id"] == source_case["current_turn_id"]
        assert (
            case["expected_materialization_outcome"]
            == (source_case["expected_materialization_outcome"])
        )
        expected_observed = (
            "accepted"
            if case["expected_materialization_outcome"] == "accepted"
            else case["expected_materialization_outcome"]
        )
        assert case["expectation_matched"] is (
            case["observed_materialization_outcome"] == expected_observed
        )
        assert case["integrity_passed"] is case["expectation_matched"]
        assert case["source_manifest_sha256"] == _canonical_sha256(
            [
                {
                    "content_sha256": hashlib.sha256(source["content"].encode("utf-8")).hexdigest(),
                    "id": source["id"],
                    "role": source["role"],
                    "sequence": source["sequence"],
                }
                for source in sources
            ]
        )
        assert case["gold_manifest_sha256"] == _canonical_sha256(
            [
                {
                    "atom_id": atom["atom_id"],
                    "authority_expectation": atom["authority_expectation"],
                    "categories": atom["categories"],
                    "end": atom["end"],
                    "literal_sha256": atom["literal_sha256"],
                    "protected": atom["protected"],
                    "source_id": atom["source_id"],
                    "start": atom["start"],
                    "status": atom["status"],
                }
                for atom in source_case["gold_atoms"]
            ]
        )
        assert case["allocation_plan_sha256"] == _canonical_sha256(
            {
                "budget": report["budget"],
                "case_id": case["case_id"],
                "domain": "ctxc-materialized-retention-allocation-v1",
                "pack_sha256": PACK_SELF_SHA256,
            }
        )
        for arm in case["arms"]:
            planning = arm["planning_units"]
            assert all(type(value) is int for value in planning.values())
            occupied = (
                planning["fixed_input_planning_units"]
                + planning["content_planning_units"]
                + planning["reserved_output_planning_units"]
                + planning["safety_margin_planning_units"]
            )
            assert planning["occupied_planning_units"] == occupied
            assert planning["remaining_planning_units"] == (
                planning["hard_limit_planning_units"] - occupied
            )
            assert planning["hard_limit_overflow_planning_units"] == max(
                0,
                occupied - planning["hard_limit_planning_units"],
            )
            attempted = arm["attempted_planning_units"]
            if attempted is not None:
                assert all(type(value) is int for value in attempted.values())
                attempted_occupied = (
                    attempted["fixed_input_planning_units"]
                    + attempted["content_planning_units"]
                    + attempted["reserved_output_planning_units"]
                    + attempted["safety_margin_planning_units"]
                )
                assert attempted["occupied_planning_units"] == attempted_occupied
                assert attempted["remaining_planning_units"] == (
                    attempted["hard_limit_planning_units"] - attempted_occupied
                )
                assert attempted["hard_limit_overflow_planning_units"] == max(
                    0,
                    attempted_occupied - attempted["hard_limit_planning_units"],
                )

            included = arm["included_source_ids"]
            omitted = arm["omitted_source_ids"]
            compiled = arm["compiled_source_ids"]
            assert included == [source_id for source_id in all_ids if source_id in included]
            assert omitted == [source_id for source_id in all_ids if source_id in omitted]
            assert len(included) == len(set(included))
            assert len(omitted) == len(set(omitted))
            assert set(included).isdisjoint(omitted)
            assert set(included).union(omitted) == set(all_ids)
            assert arm["included_source_ids_sha256"] == _canonical_sha256(included)
            assert arm["omitted_source_ids_sha256"] == _canonical_sha256(omitted)
            assert arm["compiled_source_ids_sha256"] == _canonical_sha256(compiled)
            assert arm["content_manifest_sha256"] == _canonical_sha256(
                [
                    {
                        "content_sha256": hashlib.sha256(
                            source_by_id[source_id]["content"].encode("utf-8")
                        ).hexdigest(),
                        "id": source_id,
                        "role": source_by_id[source_id]["role"],
                        "sequence": source_by_id[source_id]["sequence"],
                    }
                    for source_id in included
                ]
            )

            if arm["outcome"] == "refused":
                assert arm["current_turn_occurrences"] == 0
                assert arm["current_turn_last"] is False
                assert arm["arm"] == "lrcc_materialized"
                assert planning["content_planning_units"] == 0
                assert attempted is not None
                minimum = report["budget"]["minimum_recent_messages"]
                attempted_sources = sources[-(minimum + 1) :]
                assert attempted["content_planning_units"] == (
                    report["budget"]["memory_budget_tokens"]
                    + sum(
                        len(source["content"]) + report["budget"]["per_message_overhead_tokens"]
                        for source in attempted_sources
                    )
                )
            else:
                assert arm["current_turn_occurrences"] == 1
                assert arm["current_turn_last"] is True
                assert attempted is None
            if arm["arm"] == "lrcc_materialized" and arm["outcome"] == "accepted":
                assert compiled == omitted
                assert arm["materialization_digests"] is not None
            elif arm["arm"] != "lrcc_materialized":
                assert compiled == []
                assert arm["materialization_digests"] is None
            else:
                assert arm["materialization_digests"] is None

            authority = arm["authority"]
            assert authority["must_not_promote_source_ids"] == must_not_promote
            assert authority["instruction_shaped_probe_source_ids"] == (instruction_shaped_probes)
            assert authority["violation_source_ids"] == []
            assert authority["boundaries_preserved"] is True

            retention = arm["retention"]
            retained = retention["retained_atom_ids"]
            missing = retention["missing_atom_ids"]
            assert retained == sorted(retained)
            assert missing == sorted(missing)
            assert set(retained).isdisjoint(missing)
            assert retention["expected_atom_count"] == 8
            assert retention["retained_atom_count"] == len(retained)
            assert retention["missing_atom_count"] == len(missing)
            assert len(retained) + len(missing) == 8
            for grouped in (
                retention["by_category"],
                retention["by_status"],
            ):
                for counts in grouped.values():
                    assert counts["retained_atom_ids"] == sorted(counts["retained_atom_ids"])
                    assert counts["retained_atom_count"] == len(counts["retained_atom_ids"])
                    assert counts["missing_atom_count"] == (
                        counts["expected_atom_count"] - counts["retained_atom_count"]
                    )


@pytest.mark.parametrize("state_status", ("active", "conflicting"))
@pytest.mark.parametrize("state_role", ("assistant", "tool"))
def test_live_selected_state_rejects_any_assistant_or_tool_provenance(
    state_role: str,
    state_status: str,
) -> None:
    other_role = "tool" if state_role == "assistant" else "assistant"
    state_source = f"{state_role}-source"
    evidence_source = f"{other_role}-evidence"
    bundle = {
        "artifact": {
            "selected_item_ids": ["selected-state", "selected-evidence"],
            "items": [
                {
                    "id": "selected-state",
                    "kind": "decision",
                    "status": state_status,
                    "provenance": [{"source_id": state_source}],
                },
                {
                    "id": "selected-evidence",
                    "kind": "evidence",
                    "status": "active",
                    "provenance": [{"source_id": evidence_source}],
                },
            ],
        }
    }
    authority = evaluation_module._authority(
        bundle,
        [
            {
                "authority_expectation": "untrusted_evidence_only",
                "source_id": evidence_source,
            }
        ],
        [
            {"id": state_source, "role": state_role},
            {"id": evidence_source, "role": other_role},
        ],
    )

    assert authority["live_selected_provenance_source_ids"] == [state_source]
    assert authority["must_not_promote_source_ids"] == sorted([state_source, evidence_source])
    assert authority["instruction_shaped_probe_source_ids"] == [evidence_source]
    assert authority["violation_source_ids"] == [state_source]
    assert authority["boundaries_preserved"] is False


@pytest.mark.parametrize("evidence_kind", ("exact_error", "exact_reference"))
def test_exact_untrusted_evidence_is_not_mislabeled_authoritative(
    evidence_kind: str,
) -> None:
    bundle = {
        "artifact": {
            "selected_item_ids": ["user-state", "tool-evidence"],
            "items": [
                {
                    "id": "user-state",
                    "kind": "constraint",
                    "status": "active",
                    "provenance": [{"source_id": "user-source"}],
                },
                {
                    "id": "tool-evidence",
                    "kind": evidence_kind,
                    "status": "active",
                    "provenance": [{"source_id": "tool-source"}],
                },
            ],
        }
    }
    authority = evaluation_module._authority(
        bundle,
        [
            {
                "authority_expectation": "untrusted_evidence_only",
                "source_id": "tool-source",
            }
        ],
        [
            {"id": "user-source", "role": "user"},
            {"id": "tool-source", "role": "tool"},
        ],
    )

    assert authority["live_selected_provenance_source_ids"] == ["user-source"]
    assert authority["must_not_promote_source_ids"] == ["tool-source"]
    assert authority["instruction_shaped_probe_source_ids"] == ["tool-source"]
    assert authority["violation_source_ids"] == []
    assert authority["boundaries_preserved"] is True


def test_summary_is_an_exact_aggregate_without_thresholds_or_winners() -> None:
    report = evaluate_materialization_retention(selected_split="heldout")
    summary = report["summary"]
    cases = report["cases"]

    assert summary["case_count"] == len(cases) == 20
    assert summary["expected_accepted_case_count"] == 18
    assert summary["expected_refusal_case_count"] == 2
    assert summary["observed_accepted_case_count"] == 0
    assert summary["observed_refusal_case_count"] == 20
    assert summary["unexpected_case_ids"] == [
        *(f"case-{number:03d}" for number in range(11, 28)),
        "case-029",
    ]
    assert not {
        "best_arm",
        "pass_threshold",
        "ranking",
        "superior_arm",
        "winner",
    }.intersection(summary)

    for arm_name, aggregate in summary["arms"].items():
        arms = [next(arm for arm in case["arms"] if arm["arm"] == arm_name) for case in cases]
        assert aggregate["case_count"] == len(arms)
        assert aggregate["accepted_case_count"] == sum(arm["outcome"] == "accepted" for arm in arms)
        assert aggregate["over_hard_limit_case_count"] == sum(
            arm["outcome"] == "over_hard_limit" for arm in arms
        )
        assert aggregate["refused_case_count"] == sum(arm["outcome"] == "refused" for arm in arms)
        assert aggregate["content_planning_units"] == sum(
            arm["planning_units"]["content_planning_units"] for arm in arms
        )
        assert aggregate["occupied_planning_units"] == sum(
            arm["planning_units"]["occupied_planning_units"] for arm in arms
        )
        attempted = [
            arm["attempted_planning_units"]
            for arm in arms
            if arm["attempted_planning_units"] is not None
        ]
        assert aggregate["attempted_content_planning_units"] == sum(
            value["content_planning_units"] for value in attempted
        )
        assert aggregate["attempted_occupied_planning_units"] == sum(
            value["occupied_planning_units"] for value in attempted
        )
        assert aggregate["attempted_hard_limit_overflow_planning_units"] == sum(
            value["hard_limit_overflow_planning_units"] for value in attempted
        )
        assert aggregate["included_source_count"] == sum(
            len(arm["included_source_ids"]) for arm in arms
        )
        assert aggregate["omitted_source_count"] == sum(
            len(arm["omitted_source_ids"]) for arm in arms
        )
        assert aggregate["compiled_source_count"] == sum(
            len(arm["compiled_source_ids"]) for arm in arms
        )
        assert aggregate["expected_atom_count"] == sum(
            arm["retention"]["expected_atom_count"] for arm in arms
        )
        assert aggregate["retained_atom_count"] == sum(
            arm["retention"]["retained_atom_count"] for arm in arms
        )
        assert aggregate["missing_atom_count"] == sum(
            arm["retention"]["missing_atom_count"] for arm in arms
        )
        assert aggregate["current_turn_exact_once_count"] == sum(
            arm["current_turn_occurrences"] == 1 for arm in arms
        )
        assert aggregate["current_turn_last_count"] == sum(
            arm["current_turn_last"] is True for arm in arms
        )
        assert aggregate["authority_boundary_violation_count"] == 0
        for category in ("correction", "detail", "identifier", "number", "path"):
            counts = aggregate["by_category"][category]
            expected = sum(
                arm["retention"]["by_category"][category]["expected_atom_count"] for arm in arms
            )
            retained = sum(
                arm["retention"]["by_category"][category]["retained_atom_count"] for arm in arms
            )
            assert counts == {
                "expected_atom_count": expected,
                "missing_atom_count": expected - retained,
                "retained_atom_count": retained,
            }


def test_report_verification_requires_independent_digest_and_semantic_shape() -> None:
    report = evaluate_materialization_retention(selected_split="train")

    digest_tamper = copy.deepcopy(report)
    digest_tamper["report_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="expected digest"):
        verify_materialization_retention_report(
            digest_tamper,
            expected_report_sha256=report["report_sha256"],
        )

    semantic_tamper = copy.deepcopy(report)
    semantic_tamper["claim_boundaries"]["semantic_completeness_claimed"] = True
    _reseal(semantic_tamper)
    with pytest.raises(ValueError, match="deterministic replay"):
        verify_materialization_retention_report(
            semantic_tamper,
            expected_report_sha256=semantic_tamper["report_sha256"],
        )

    missing = copy.deepcopy(report)
    missing["cases"].pop()
    _reseal(missing)
    with pytest.raises(ValueError, match="deterministic replay"):
        verify_materialization_retention_report(
            missing,
            expected_report_sha256=missing["report_sha256"],
        )

    reordered = copy.deepcopy(report)
    reordered["cases"].reverse()
    _reseal(reordered)
    with pytest.raises(ValueError, match="deterministic replay"):
        verify_materialization_retention_report(
            reordered,
            expected_report_sha256=reordered["report_sha256"],
        )

    unknown = copy.deepcopy(report)
    unknown["unexpected"] = None
    _reseal(unknown)
    with pytest.raises(ValueError, match="deterministic replay"):
        verify_materialization_retention_report(
            unknown,
            expected_report_sha256=unknown["report_sha256"],
        )

    bool_count = copy.deepcopy(report)
    bool_count["selection"]["case_count"] = True
    _reseal(bool_count)
    with pytest.raises(ValueError, match="deterministic replay"):
        verify_materialization_retention_report(
            bool_count,
            expected_report_sha256=bool_count["report_sha256"],
        )

    subclass = _ExplodingDict(report)
    with pytest.raises(TypeError, match="exact object"):
        verify_materialization_retention_report(
            subclass,
            expected_report_sha256=report["report_sha256"],
        )


def test_oversized_in_memory_report_fails_before_canonical_serialization(
    monkeypatch,
) -> None:
    canonicalization_attempted = False

    def fail_if_called(value: Any) -> bytes:
        nonlocal canonicalization_attempted
        canonicalization_attempted = True
        raise AssertionError("oversized report reached canonical serialization")

    monkeypatch.setattr(evaluation_module, "_canonical_bytes", fail_if_called)
    oversized = {
        "padding": ["x" * 60_000 for _ in range(40)],
        "report_sha256": "0" * 64,
        "selection": {"split": "train"},
    }

    with pytest.raises(ValueError, match="exceeds 2097152 UTF-8 JSON bytes"):
        verify_materialization_retention_report(
            oversized,
            expected_report_sha256="0" * 64,
        )
    assert canonicalization_attempted is False


def test_report_loader_rejects_noncanonical_duplicate_and_wrong_digest(
    tmp_path: Path,
) -> None:
    report = evaluate_materialization_retention(selected_split="train")
    expected = report["report_sha256"]
    canonical = _canonical_bytes(report)

    accepted = tmp_path / "accepted.json"
    accepted.write_bytes(canonical + b"\n")
    assert (
        load_materialization_retention_report(
            accepted,
            expected_report_sha256=expected,
        )
        == report
    )

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ValueError, match="canonical"):
        load_materialization_retention_report(
            noncanonical,
            expected_report_sha256=expected,
        )

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(b'{"budget":null,' + canonical[1:] + b"\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_materialization_retention_report(
            duplicate,
            expected_report_sha256=expected,
        )

    with pytest.raises(ValueError, match="expected digest"):
        load_materialization_retention_report(
            accepted,
            expected_report_sha256="f" * 64,
        )


def test_report_loader_rejects_hard_links(tmp_path: Path) -> None:
    report = evaluate_materialization_retention(selected_split="train")
    target = tmp_path / "target.json"
    report_path = tmp_path / "linked.json"
    target.write_bytes(_canonical_bytes(report) + b"\n")
    try:
        os.link(target, report_path)
    except OSError as exc:
        pytest.skip(f"hard-link creation is unavailable: {exc}")

    with pytest.raises(ValueError, match="must not have hard links"):
        load_materialization_retention_report(
            report_path,
            expected_report_sha256=report["report_sha256"],
        )


def test_report_loader_rejects_oversize_before_decoding(tmp_path: Path) -> None:
    report_path = tmp_path / "oversized.json"
    report_path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))

    with pytest.raises(ValueError, match="exceeds 2097152 bytes"):
        load_materialization_retention_report(
            report_path,
            expected_report_sha256="0" * 64,
        )


def test_report_loader_read_errors_propagate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_bytes(b"{}\n")

    @contextmanager
    def failing_open(*args, **kwargs):
        del args, kwargs
        raise OSError("synthetic report read failure")
        yield

    monkeypatch.setattr(evaluation_module, "_open_stable_text_path", failing_open)
    with pytest.raises(OSError, match="synthetic report read failure"):
        load_materialization_retention_report(
            report_path,
            expected_report_sha256="0" * 64,
        )
