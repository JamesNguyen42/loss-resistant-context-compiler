from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.natural_history import (
    ADJUDICATION_SCHEMA,
    ANNOTATION_SCHEMA,
    CORPUS_SCHEMA,
    DEFAULT_ADJUDICATION,
    DEFAULT_ANNOTATIONS,
    DEFAULT_CORPUS,
    DEFAULT_GOLD_FREE,
    DEFAULT_REPORT,
    DEFAULT_SPLIT,
    GOLD_FREE_SCHEMA,
    REPORT_SCHEMA,
    SPLIT_SCHEMA,
    VERIFICATION_SCHEMA,
    NaturalHistoryError,
    decode_adjudication,
    decode_annotation,
    decode_corpus,
    decode_gold_free,
    decode_report,
    decode_split,
    load_adjudication,
    load_annotation,
    load_corpus,
    load_gold_free,
    load_report,
    load_split,
    main,
    verify_kit,
)

_DATA = Path("benchmarks/data/natural_history")
_SCHEMAS = Path("benchmarks/schemas/natural-history")


def _document(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resign(document: dict, field: str) -> None:
    unsigned = copy.deepcopy(document)
    unsigned.pop(field, None)
    document[field] = _digest(unsigned)


def _loaded_contracts():
    corpus = load_corpus()
    first = load_annotation(DEFAULT_ANNOTATIONS[0], corpus)
    second = load_annotation(DEFAULT_ANNOTATIONS[1], corpus)
    adjudication = load_adjudication(
        DEFAULT_ADJUDICATION,
        corpus,
        first,
        second,
    )
    split = load_split(DEFAULT_SPLIT, corpus)
    return corpus, first, second, adjudication, split


def test_golden_kit_is_fixture_only_and_retains_failed_histories() -> None:
    summary = verify_kit()

    assert summary["schema"] == VERIFICATION_SCHEMA
    assert summary["verified"] is True
    assert summary["fixture_only"] is True
    assert summary["natural_history_claimed"] is False
    assert summary["semantic_completeness_claimed"] is False
    assert summary["claim_ready"] is False
    assert summary["history_count"] == 3
    assert summary["attempted_history_count"] == 3
    assert summary["explicit_exclusion_count"] == 0
    assert summary["source_count"] == 6
    assert summary["independent_annotator_count"] == 2
    assert summary["adjudicated_label_count"] == 6

    corpus, first, second, adjudication, split = _loaded_contracts()
    report = load_report(
        DEFAULT_REPORT,
        corpus,
        adjudication,
        split,
    )
    assert [item["outcome"] for item in report["histories"]] == [
        "failed",
        "failed",
        "failed",
    ]
    assert report["summary"]["all_histories_retained"] is True
    assert report["summary"]["failed_history_count"] == 3
    assert report["summary"]["claim_ready"] is False
    assert first.annotator["id"] != second.annotator["id"]


def test_all_six_json_schemas_are_strict_and_versioned() -> None:
    expected = {
        "corpus.schema.json": CORPUS_SCHEMA,
        "annotation.schema.json": ANNOTATION_SCHEMA,
        "adjudication.schema.json": ADJUDICATION_SCHEMA,
        "split.schema.json": SPLIT_SCHEMA,
        "gold-free.schema.json": GOLD_FREE_SCHEMA,
        "report.schema.json": REPORT_SCHEMA,
    }
    for name, schema_name in expected.items():
        document = _document(_SCHEMAS / name)
        assert document["$schema"] == (
            "https://json-schema.org/draft/2020-12/schema"
        )
        assert document["additionalProperties"] is False
        assert schema_name in json.dumps(document)
    adjudication = _document(_SCHEMAS / "adjudication.schema.json")
    decisions = adjudication["properties"]["histories"]["items"][
        "properties"
    ]["decisions"]
    assert decisions["maxItems"] == 20_000



def test_corpus_rejects_tampering_before_shape_validation() -> None:
    document = _document(DEFAULT_CORPUS)
    document["histories"][0]["sources"][0]["content"] += " tampered"

    with pytest.raises(NaturalHistoryError, match="corpus SHA-256 mismatch"):
        decode_corpus(document)


def test_corpus_rejects_self_consistent_unknown_fields() -> None:
    document = _document(DEFAULT_CORPUS)
    document["unexpected"] = True
    _resign(document, "corpus_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="fields are invalid.*unknown unexpected",
    ):
        decode_corpus(document)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda document: document["histories"][0]["origin"].update(
                license_state="unreviewed"
            ),
            "lacks verified license evidence",
        ),
        (
            lambda document: document["histories"][1]["origin"].update(
                consent_evidence_sha256=None
            ),
            "lacks documented consent evidence",
        ),
        (
            lambda document: document["histories"][2][
                "privacy_review"
            ].update(state="pending"),
            "privacy review is not approved",
        ),
        (
            lambda document: document["histories"][0][
                "privacy_review"
            ].update(personal_data_review="skipped"),
            "personal-data review is incomplete",
        ),
    ],
)
def test_corpus_rejects_license_consent_and_privacy_failures(
    mutate,
    message: str,
) -> None:
    document = _document(DEFAULT_CORPUS)
    mutate(document)
    _resign(document, "corpus_sha256")

    with pytest.raises(NaturalHistoryError, match=message):
        decode_corpus(document)


def test_contract_fixture_cannot_claim_natural_or_frozen_evidence() -> None:
    document = _document(DEFAULT_CORPUS)
    document["evidence_state"]["natural_history_claimed"] = True
    document["evidence_state"]["frozen_for_evaluation"] = True
    _resign(document, "corpus_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="contract fixture cannot claim",
    ):
        decode_corpus(document)


def test_corpus_and_report_refuse_semantic_completeness_claims() -> None:
    corpus_document = _document(DEFAULT_CORPUS)
    corpus_document["evidence_state"]["semantic_completeness_claimed"] = True
    _resign(corpus_document, "corpus_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="cannot claim semantic completeness",
    ):
        decode_corpus(corpus_document)

    corpus, _, _, adjudication, split = _loaded_contracts()
    report = _document(DEFAULT_REPORT)
    report["run"]["semantic_completeness_claimed"] = True
    _resign(report, "report_sha256")
    with pytest.raises(
        NaturalHistoryError,
        match="cannot claim semantic completeness",
    ):
        decode_report(report, corpus, adjudication, split)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda document: document["collection_accounting"].update(
                attempted_history_count=4
            ),
            "does not reconcile attempted, included, and excluded",
        ),
        (
            lambda document: document["collection_accounting"][
                "included_history_ids"
            ].pop(),
            "must list every included history canonically",
        ),
    ],
)
def test_collection_accounting_prevents_silent_intake_drops(
    mutation,
    message: str,
) -> None:
    document = _document(DEFAULT_CORPUS)
    mutation(document)
    _resign(document, "corpus_sha256")

    with pytest.raises(NaturalHistoryError, match=message):
        decode_corpus(document)


def test_annotation_rejects_non_exact_character_span() -> None:
    corpus = load_corpus()
    document = _document(DEFAULT_ANNOTATIONS[0])
    document["histories"][0]["labels"][0]["provenance"][0]["start"] = 11
    _resign(document, "annotation_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="not an exact character span",
    ):
        decode_annotation(document, corpus)


def test_annotation_rejects_quote_hash_mismatch() -> None:
    corpus = load_corpus()
    document = _document(DEFAULT_ANNOTATIONS[0])
    document["histories"][0]["labels"][0]["provenance"][0][
        "quote_sha256"
    ] = "0" * 64
    _resign(document, "annotation_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="quote SHA-256 mismatch",
    ):
        decode_annotation(document, corpus)


def test_annotation_cannot_silently_drop_an_empty_or_labeled_history() -> None:
    corpus = load_corpus()
    document = _document(DEFAULT_ANNOTATIONS[0])
    document["histories"].pop()
    _resign(document, "annotation_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="include every history exactly once",
    ):
        decode_annotation(document, corpus)


def test_independent_annotation_pair_requires_distinct_people_and_evidence() -> None:
    corpus = load_corpus()
    first = load_annotation(DEFAULT_ANNOTATIONS[0], corpus)
    second_document = _document(DEFAULT_ANNOTATIONS[1])
    second_document["annotator"]["id"] = first.annotator["id"]
    _resign(second_document, "annotation_sha256")
    second = decode_annotation(second_document, corpus)
    adjudication_document = _document(DEFAULT_ADJUDICATION)

    with pytest.raises(
        NaturalHistoryError,
        match="distinct annotator ids",
    ):
        decode_adjudication(
            adjudication_document,
            corpus,
            first,
            second,
        )


def test_adjudication_rejects_missing_disagreement_decision() -> None:
    corpus = load_corpus()
    first = load_annotation(DEFAULT_ANNOTATIONS[0], corpus)
    second = load_annotation(DEFAULT_ANNOTATIONS[1], corpus)
    document = _document(DEFAULT_ADJUDICATION)
    document["histories"][1]["decisions"] = []
    _resign(document, "adjudication_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="incomplete decisions",
    ):
        decode_adjudication(document, corpus, first, second)


def test_adjudication_rejects_unresolved_extra_final_label() -> None:
    corpus = load_corpus()
    first = load_annotation(DEFAULT_ANNOTATIONS[0], corpus)
    second = load_annotation(DEFAULT_ANNOTATIONS[1], corpus)
    document = _document(DEFAULT_ADJUDICATION)
    document["histories"][1]["decisions"][0]["resolution"] = "reject"
    document["histories"][1]["decisions"][0]["final_label_sha256"] = None
    _resign(document, "adjudication_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="final labels do not match",
    ):
        decode_adjudication(document, corpus, first, second)


def _split_for_mutated_corpus(corpus_document: dict) -> tuple[object, dict]:
    _resign(corpus_document, "corpus_sha256")
    corpus = decode_corpus(corpus_document)
    split_document = _document(DEFAULT_SPLIT)
    split_document["corpus_sha256"] = corpus.corpus_sha256
    _resign(split_document, "split_sha256")
    return corpus, split_document


def test_split_rejects_repository_leakage() -> None:
    corpus_document = _document(DEFAULT_CORPUS)
    corpus_document["histories"][1]["repository_id"] = corpus_document[
        "histories"
    ][0]["repository_id"]
    corpus, split_document = _split_for_mutated_corpus(corpus_document)

    with pytest.raises(NaturalHistoryError, match="repository .* leaks"):
        decode_split(split_document, corpus)


def test_split_rejects_task_group_leakage() -> None:
    corpus_document = _document(DEFAULT_CORPUS)
    corpus_document["histories"][2]["task_group_id"] = corpus_document[
        "histories"
    ][1]["task_group_id"]
    corpus, split_document = _split_for_mutated_corpus(corpus_document)

    with pytest.raises(NaturalHistoryError, match="task group .* leaks"):
        decode_split(split_document, corpus)


def test_split_cannot_silently_drop_a_history() -> None:
    corpus = load_corpus()
    document = _document(DEFAULT_SPLIT)
    document["assignments"].pop()
    _resign(document, "split_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="assign every history exactly once",
    ):
        decode_split(document, corpus)


def test_gold_free_export_contains_no_gold_keys() -> None:
    corpus, _, _, _, split = _loaded_contracts()
    export = load_gold_free(DEFAULT_GOLD_FREE, corpus, split)
    serialized = json.dumps(export, sort_keys=True)

    for forbidden in (
        '"labels"',
        '"annotation"',
        '"adjudication"',
        '"provenance"',
        '"quote_sha256"',
    ):
        assert forbidden not in serialized


def test_gold_free_export_rejects_structural_label_leakage() -> None:
    corpus, _, _, _, split = _loaded_contracts()
    document = _document(DEFAULT_GOLD_FREE)
    document["histories"][0]["labels"] = []
    _resign(document, "gold_free_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="label-bearing keys: labels",
    ):
        decode_gold_free(document, corpus, split)


def test_gold_free_export_cannot_silently_drop_a_history() -> None:
    corpus, _, _, _, split = _loaded_contracts()
    document = _document(DEFAULT_GOLD_FREE)
    document["histories"].pop()
    _resign(document, "gold_free_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="does not match corpus and split",
    ):
        decode_gold_free(document, corpus, split)


def test_report_cannot_silently_drop_a_failed_history() -> None:
    corpus, _, _, adjudication, split = _loaded_contracts()
    document = _document(DEFAULT_REPORT)
    document["histories"].pop()
    _resign(document, "report_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="retain every history exactly once",
    ):
        decode_report(document, corpus, adjudication, split)


def test_failed_report_history_cannot_carry_partial_scored_metrics() -> None:
    corpus, _, _, adjudication, split = _loaded_contracts()
    document = _document(DEFAULT_REPORT)
    document["histories"][0]["observed_label_count"] = 1
    _resign(document, "report_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="cannot carry scored metrics",
    ):
        decode_report(document, corpus, adjudication, split)


def test_self_consistent_report_summary_forgery_is_rejected() -> None:
    corpus, _, _, adjudication, split = _loaded_contracts()
    document = _document(DEFAULT_REPORT)
    document["summary"]["failed_history_count"] = 0
    document["summary"]["complete_without_failures"] = True
    _resign(document, "report_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="summary does not reconcile",
    ):
        decode_report(document, corpus, adjudication, split)


def test_scored_report_rejects_inconsistent_matched_subcounts() -> None:
    corpus, _, _, adjudication, split = _loaded_contracts()
    document = _document(DEFAULT_REPORT)
    result = document["histories"][0]
    result.update(
        {
            "outcome": "scored",
            "failure_code": None,
            "observed_label_count": 1,
            "matched_label_count": 0,
            "protected_matched": 1,
            "exact_matched": 0,
            "provenance_errors": 0,
            "authority_violations": 0,
            "stale_claims": 0,
            "unresolved_to_fact": 0,
            "active_characters": 1,
        }
    )
    _resign(document, "report_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="protected matched count exceeds all matched labels",
    ):
        decode_report(document, corpus, adjudication, split)


def test_loader_rejects_duplicate_keys_and_nonfinite_numbers(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema":"ctxc-natural-history-corpus-0.1",'
        '"schema":"ctxc-natural-history-corpus-0.1"}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate JSON object key"):
        load_corpus(duplicate)

    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"value":NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="non-standard JSON constant"):
        load_corpus(nonfinite)


def test_cli_atomically_exports_gold_free_and_verifies_full_kit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "gold-free.json"

    assert main(["--export-gold-free", str(output)]) == 0
    emitted = json.loads(capsys.readouterr().out)
    corpus, _, _, _, split = _loaded_contracts()
    assert emitted == load_gold_free(output, corpus, split)
    assert list(tmp_path.glob(".ctxc-*.tmp")) == []

    assert main([]) == 0
    verification = json.loads(capsys.readouterr().out)
    assert verification["verified"] is True
    assert verification["fixture_only"] is True
