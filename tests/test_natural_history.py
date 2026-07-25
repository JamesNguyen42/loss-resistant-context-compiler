from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

import benchmarks.natural_history as natural_history
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
from conformance.schema_validation import (
    SchemaValidationError,
    audit_schema_documents,
    validate_instance,
)

_DATA = Path("benchmarks/data/natural_history")
_SCHEMAS = Path("benchmarks/schemas/natural-history")


def _document(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(nested) for nested in value]
    return value


def _schema_documents() -> dict[str, dict]:
    return {
        path.name: _document(path)
        for path in sorted(_SCHEMAS.glob("*.schema.json"))
    }


def _schema_nodes(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _schema_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _schema_nodes(child)


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
    corpus_schema = _document(_SCHEMAS / "corpus.schema.json")
    license_spdx = corpus_schema["$defs"]["history"]["properties"][
        "origin"
    ]["properties"]["license_spdx"]["oneOf"][0]
    assert license_spdx["pattern"] == (
        r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,127}(?![\s\S])"
    )
    reviewed_at = corpus_schema["$defs"]["history"]["properties"][
        "privacy_review"
    ]["properties"]["reviewed_at_utc"]
    assert reviewed_at["format"] == "date-time"
    annotation = _document(_SCHEMAS / "annotation.schema.json")
    provenance = annotation["$defs"]["label"]["properties"]["provenance"]
    assert provenance["uniqueItems"] is True


def test_natural_history_schema_graph_validates_every_golden_fixture() -> None:
    documents = _schema_documents()
    audit_schema_documents(frozenset(documents), documents=documents)
    fixtures = (
        (DEFAULT_CORPUS, "corpus.schema.json"),
        (DEFAULT_ANNOTATIONS[0], "annotation.schema.json"),
        (DEFAULT_ANNOTATIONS[1], "annotation.schema.json"),
        (DEFAULT_ADJUDICATION, "adjudication.schema.json"),
        (DEFAULT_SPLIT, "split.schema.json"),
        (DEFAULT_GOLD_FREE, "gold-free.schema.json"),
        (DEFAULT_REPORT, "report.schema.json"),
    )

    for path, schema_name in fixtures:
        validate_instance(
            _document(path),
            schema_name,
            documents=documents,
        )


def test_natural_history_schema_scalar_boundaries_are_explicit() -> None:
    sha_pattern = r"^[0-9a-f]{64}$"
    identifier_pattern = r"^[a-z0-9][a-z0-9._:-]{0,255}(?![\s\S])"
    timestamp_pattern = (
        r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
        r"[0-9]{2}:[0-9]{2}:[0-5][0-9]Z(?![\s\S])"
    )
    counts = {"sha": 0, "identifier": 0, "timestamp": 0}

    for document in _schema_documents().values():
        for node in _schema_nodes(document):
            if node.get("pattern") == sha_pattern:
                counts["sha"] += 1
                assert node["minLength"] == 64
                assert node["maxLength"] == 64
            if (
                node.get("type") == "string"
                and node.get("minLength") == 1
                and node.get("maxLength") == 256
            ):
                counts["identifier"] += 1
                assert node["pattern"] == identifier_pattern
            if node.get("format") == "date-time":
                counts["timestamp"] += 1
                assert node["pattern"] == timestamp_pattern
                assert node["minLength"] == 20
                assert node["maxLength"] == 20

    assert counts == {"sha": 26, "identifier": 25, "timestamp": 7}


@pytest.mark.parametrize("suffix", ["\n", "\r", "\r\n", "\u2028", "\u2029"])
@pytest.mark.parametrize("boundary", ["identifier", "sha256", "spdx"])
def test_natural_history_schema_and_runtime_reject_trailing_terminators(
    suffix: str,
    boundary: str,
) -> None:
    document = _document(DEFAULT_CORPUS)
    if boundary == "identifier":
        document["histories"][0]["id"] += suffix
        document["collection_accounting"]["included_history_ids"][0] += suffix
        runtime_message = "is not a canonical identifier"
    elif boundary == "sha256":
        document["collection_accounting"]["intake_manifest_sha256"] += suffix
        runtime_message = "must be lowercase SHA-256"
    else:
        document["histories"][0]["origin"]["license_spdx"] += suffix
        runtime_message = "not a canonical SPDX license identifier"
    _resign(document, "corpus_sha256")

    with pytest.raises(SchemaValidationError):
        validate_instance(
            document,
            "corpus.schema.json",
            documents=_schema_documents(),
        )
    with pytest.raises(NaturalHistoryError, match=runtime_message):
        decode_corpus(document)


def test_natural_history_schema_and_runtime_reject_uppercase_identifier() -> None:
    document = _document(DEFAULT_CORPUS)
    document["histories"][0]["id"] = "Fixture-public-api"
    document["collection_accounting"]["included_history_ids"][0] = (
        "Fixture-public-api"
    )
    _resign(document, "corpus_sha256")

    with pytest.raises(SchemaValidationError):
        validate_instance(
            document,
            "corpus.schema.json",
            documents=_schema_documents(),
        )
    with pytest.raises(NaturalHistoryError, match="canonical identifier"):
        decode_corpus(document)


def test_schema_integer_and_exact_int_runtime_boundary_is_documented() -> None:
    document = _document(DEFAULT_CORPUS)
    document["collection_accounting"]["attempted_history_count"] = 3.0
    _resign(document, "corpus_sha256")

    validate_instance(
        document,
        "corpus.schema.json",
        documents=_schema_documents(),
    )
    with pytest.raises(
        NaturalHistoryError,
        match="attempted_history_count must be an integer",
    ):
        decode_corpus(document)


@pytest.mark.parametrize(
    ("history_index", "field", "invalid_value"),
    [
        (0, "license_state", "authorized-by-consent"),
        (0, "license_spdx", None),
        (0, "license_evidence_sha256", None),
        (0, "consent_state", "documented"),
        (0, "consent_evidence_sha256", "f" * 64),
        (1, "license_state", "verified-compatible"),
        (1, "license_spdx", "MIT"),
        (1, "license_evidence_sha256", "f" * 64),
        (1, "consent_state", "not-required-public"),
        (1, "consent_evidence_sha256", None),
        (2, "license_state", "verified-compatible"),
        (2, "license_spdx", "MIT"),
        (2, "license_evidence_sha256", "f" * 64),
        (2, "consent_state", "not-required-public"),
        (2, "consent_evidence_sha256", "f" * 64),
    ],
)
def test_origin_schema_conditionals_match_runtime(
    history_index: int,
    field: str,
    invalid_value: object,
) -> None:
    document = _document(DEFAULT_CORPUS)
    document["histories"][history_index]["origin"][field] = invalid_value
    _resign(document, "corpus_sha256")

    with pytest.raises(SchemaValidationError):
        validate_instance(
            document,
            "corpus.schema.json",
            documents=_schema_documents(),
        )
    with pytest.raises(NaturalHistoryError):
        decode_corpus(document)


@pytest.mark.parametrize(
    ("kind", "expected_exact", "expected_protected"),
    [
        ("goal", False, True),
        ("constraint", False, True),
        ("user_correction", False, True),
        ("confirmed_fact", False, False),
        ("decision", False, False),
        ("unresolved", False, True),
        ("exact_error", True, True),
        ("exact_reference", True, True),
        ("discarded_attempt", False, False),
        ("progress", False, False),
        ("context", False, False),
    ],
)
@pytest.mark.parametrize("invalid_flag", ["exact", "protected"])
def test_annotation_flag_schema_conditionals_match_runtime(
    kind: str,
    expected_exact: bool,
    expected_protected: bool,
    invalid_flag: str,
) -> None:
    corpus = load_corpus()
    document = _document(DEFAULT_ANNOTATIONS[0])
    label = document["histories"][0]["labels"][0]
    label.update(
        kind=kind,
        exact=expected_exact,
        protected=expected_protected,
    )
    label[invalid_flag] = not label[invalid_flag]
    _resign(document, "annotation_sha256")

    with pytest.raises(SchemaValidationError):
        validate_instance(
            document,
            "annotation.schema.json",
            documents=_schema_documents(),
        )
    with pytest.raises(NaturalHistoryError):
        decode_annotation(document, corpus)


@pytest.mark.parametrize(
    "invalid_field",
    [
        "failure_code",
        "observed_label_count",
        "matched_label_count",
        "protected_matched",
        "exact_matched",
        "provenance_errors",
        "authority_violations",
        "stale_claims",
        "unresolved_to_fact",
        "active_characters",
    ],
)
def test_failed_report_schema_conditionals_match_runtime(
    invalid_field: str,
) -> None:
    corpus, _, _, adjudication, split = _loaded_contracts()
    document = _document(DEFAULT_REPORT)
    result = document["histories"][0]
    result[invalid_field] = None if invalid_field == "failure_code" else 0
    _resign(document, "report_sha256")

    with pytest.raises(SchemaValidationError):
        validate_instance(
            document,
            "report.schema.json",
            documents=_schema_documents(),
        )
    with pytest.raises(NaturalHistoryError):
        decode_report(document, corpus, adjudication, split)


@pytest.mark.parametrize(
    "invalid_field",
    [
        "failure_code",
        "observed_label_count",
        "matched_label_count",
        "protected_matched",
        "exact_matched",
        "provenance_errors",
        "authority_violations",
        "stale_claims",
        "unresolved_to_fact",
        "active_characters",
    ],
)
def test_scored_report_schema_conditionals_match_runtime(
    invalid_field: str,
) -> None:
    corpus, _, _, adjudication, split = _loaded_contracts()
    document = _document(DEFAULT_REPORT)
    result = document["histories"][0]
    result["outcome"] = "scored"
    result["failure_code"] = None
    for metric in (
        "observed_label_count",
        "matched_label_count",
        "protected_matched",
        "exact_matched",
        "provenance_errors",
        "authority_violations",
        "stale_claims",
        "unresolved_to_fact",
        "active_characters",
    ):
        result[metric] = 0
    result[invalid_field] = (
        "not-evaluated-contract-fixture"
        if invalid_field == "failure_code"
        else None
    )
    _resign(document, "report_sha256")

    with pytest.raises(SchemaValidationError):
        validate_instance(
            document,
            "report.schema.json",
            documents=_schema_documents(),
        )
    with pytest.raises(NaturalHistoryError):
        decode_report(document, corpus, adjudication, split)


def test_report_schema_conditionals_accept_scored_runtime_shape() -> None:
    corpus, _, _, adjudication, split = _loaded_contracts()
    document = _document(DEFAULT_REPORT)
    for result in document["histories"]:
        result["outcome"] = "scored"
        result["failure_code"] = None
        for metric in (
            "observed_label_count",
            "matched_label_count",
            "protected_matched",
            "exact_matched",
            "provenance_errors",
            "authority_violations",
            "stale_claims",
            "unresolved_to_fact",
            "active_characters",
        ):
            result[metric] = 0
    document["summary"].update(
        scored_history_count=len(document["histories"]),
        failed_history_count=0,
        complete_without_failures=True,
    )
    _resign(document, "report_sha256")

    validate_instance(
        document,
        "report.schema.json",
        documents=_schema_documents(),
    )
    decode_report(document, corpus, adjudication, split)


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
    serialized = json.dumps(_plain_json(export), sort_keys=True)

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


def test_corpus_rejects_invalid_spdx_syntax_and_calendar_timestamps() -> None:
    document = _document(DEFAULT_CORPUS)
    document["histories"][0]["origin"]["license_spdx"] = "not reviewed"
    _resign(document, "corpus_sha256")
    with pytest.raises(
        NaturalHistoryError,
        match="not a canonical SPDX license identifier",
    ):
        decode_corpus(document)

    document = _document(DEFAULT_CORPUS)
    document["histories"][0]["privacy_review"][
        "reviewed_at_utc"
    ] = "2026-02-30T20:00:00Z"
    _resign(document, "corpus_sha256")
    with pytest.raises(
        NaturalHistoryError,
        match="valid UTC whole-second timestamp",
    ):
        decode_corpus(document)


def test_validated_evidence_metadata_is_deeply_immutable() -> None:
    corpus, first, _, adjudication, split = _loaded_contracts()
    gold_free = load_gold_free(DEFAULT_GOLD_FREE, corpus, split)
    report = load_report(DEFAULT_REPORT, corpus, adjudication, split)

    with pytest.raises(TypeError):
        corpus.evidence_state["kind"] = "natural-corpus"
    with pytest.raises(AttributeError):
        corpus.collection_accounting["included_history_ids"].append(
            "silently-added-history"
        )
    with pytest.raises(TypeError):
        first.annotator["id"] = "replacement-annotator"
    with pytest.raises(TypeError):
        adjudication.adjudicator["complete"] = False
    with pytest.raises(TypeError):
        split.policy["test_gold_sealed"] = False
    assert isinstance(gold_free, Mapping)
    with pytest.raises(TypeError):
        gold_free["histories"][0]["sources"][0]["content"] = "tampered"
    assert isinstance(report, Mapping)
    with pytest.raises(TypeError):
        report["summary"]["claim_ready"] = True


def test_annotation_label_ids_are_unique_across_the_document() -> None:
    corpus = load_corpus()
    document = _document(DEFAULT_ANNOTATIONS[0])
    document["histories"][1]["labels"][0]["id"] = document["histories"][
        0
    ]["labels"][0]["id"]
    _resign(document, "annotation_sha256")

    with pytest.raises(NaturalHistoryError, match="duplicate label id"):
        decode_annotation(document, corpus)


def test_annotation_rejects_duplicate_provenance_spans() -> None:
    corpus = load_corpus()
    document = _document(DEFAULT_ANNOTATIONS[0])
    provenance = document["histories"][0]["labels"][0]["provenance"]
    provenance.append(copy.deepcopy(provenance[0]))
    _resign(document, "annotation_sha256")

    with pytest.raises(NaturalHistoryError, match="repeats a provenance span"):
        decode_annotation(document, corpus)


def test_adjudicator_identity_evidence_and_chronology_are_distinct() -> None:
    corpus = load_corpus()
    first = load_annotation(DEFAULT_ANNOTATIONS[0], corpus)
    second = load_annotation(DEFAULT_ANNOTATIONS[1], corpus)

    document = _document(DEFAULT_ADJUDICATION)
    document["adjudicator"]["attestation_sha256"] = first.annotator[
        "attestation_sha256"
    ]
    _resign(document, "adjudication_sha256")
    with pytest.raises(
        NaturalHistoryError,
        match="distinct attestation evidence",
    ):
        decode_adjudication(document, corpus, first, second)

    document = _document(DEFAULT_ADJUDICATION)
    document["adjudicator"]["completed_at_utc"] = "2026-07-24T21:04:59Z"
    _resign(document, "adjudication_sha256")
    with pytest.raises(
        NaturalHistoryError,
        match="cannot precede either independent annotation",
    ):
        decode_adjudication(document, corpus, first, second)


def test_adjudication_enforces_aggregate_label_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = load_corpus()
    first = load_annotation(DEFAULT_ANNOTATIONS[0], corpus)
    second = load_annotation(DEFAULT_ANNOTATIONS[1], corpus)
    monkeypatch.setattr(natural_history, "_MAX_TOTAL_LABELS", 1)

    with pytest.raises(
        NaturalHistoryError,
        match="adjudication exceeds 1 final labels",
    ):
        decode_adjudication(
            _document(DEFAULT_ADJUDICATION),
            corpus,
            first,
            second,
        )


def test_report_rejects_boolean_count_aliases_and_reverse_time() -> None:
    corpus, _, _, adjudication, split = _loaded_contracts()

    document = _document(DEFAULT_REPORT)
    document["histories"][0]["exact_expected"] = True
    _resign(document, "report_sha256")
    with pytest.raises(
        NaturalHistoryError,
        match="exact_expected must be an integer",
    ):
        decode_report(document, corpus, adjudication, split)

    document = _document(DEFAULT_REPORT)
    document["summary"]["exact_matched"] = False
    _resign(document, "report_sha256")
    with pytest.raises(
        NaturalHistoryError,
        match="summary exact_matched must be an integer",
    ):
        decode_report(document, corpus, adjudication, split)

    document = _document(DEFAULT_REPORT)
    document["run"]["finished_at_utc"] = "2026-07-24T22:29:59Z"
    _resign(document, "report_sha256")
    with pytest.raises(
        NaturalHistoryError,
        match="finished_at_utc precedes started_at_utc",
    ):
        decode_report(document, corpus, adjudication, split)


def test_direct_decoder_preflight_rejects_before_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document(DEFAULT_CORPUS)
    document["histories"][0]["sources"][0]["content"] = "x" * (
        natural_history._MAX_SOURCE_CHARS + 1
    )

    def fail_hash(_: object) -> str:
        pytest.fail("canonical hashing ran before direct-decoder preflight")

    monkeypatch.setattr(natural_history, "_canonical_sha256", fail_hash)
    with pytest.raises(
        NaturalHistoryError,
        match="exceeds 1000000 characters before hashing",
    ):
        decode_corpus(document)


def test_direct_decoder_preflight_bounds_nodes_before_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document(DEFAULT_CORPUS)

    def fail_hash(_: object) -> str:
        pytest.fail("canonical hashing ran before direct-decoder preflight")

    monkeypatch.setattr(natural_history, "_MAX_DIRECT_JSON_NODES", 10)
    monkeypatch.setattr(natural_history, "_canonical_sha256", fail_hash)
    with pytest.raises(NaturalHistoryError, match="exceeds 10 JSON nodes"):
        decode_corpus(document)


def test_gold_free_direct_decoder_normalizes_depth_and_cycles() -> None:
    corpus, _, _, _, split = _loaded_contracts()
    deeply_nested: dict[str, object] = {}
    cursor = deeply_nested
    for _ in range(natural_history._DOCUMENT_LIMITS.max_depth + 1):
        nested: dict[str, object] = {}
        cursor["nested"] = nested
        cursor = nested
    with pytest.raises(NaturalHistoryError, match="exceeds JSON depth"):
        decode_gold_free(deeply_nested, corpus, split)

    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(NaturalHistoryError, match="acyclic JSON tree"):
        decode_gold_free(cyclic, corpus, split)


def test_corpus_privacy_review_must_follow_dated_sources() -> None:
    document = _document(DEFAULT_CORPUS)
    document["histories"][0]["sources"][0][
        "timestamp"
    ] = "2026-07-24T20:00:01Z"
    _resign(document, "corpus_sha256")

    with pytest.raises(
        NaturalHistoryError,
        match="privacy review precedes dated source content",
    ):
        decode_corpus(document)


def test_annotation_and_split_must_follow_all_privacy_reviews() -> None:
    corpus = load_corpus()
    annotation = _document(DEFAULT_ANNOTATIONS[0])
    annotation["annotator"]["completed_at_utc"] = "2026-07-24T20:01:59Z"
    _resign(annotation, "annotation_sha256")
    with pytest.raises(
        NaturalHistoryError,
        match="annotation precedes corpus privacy review",
    ):
        decode_annotation(annotation, corpus)

    split = _document(DEFAULT_SPLIT)
    split["policy"]["created_at_utc"] = "2026-07-24T20:01:59Z"
    _resign(split, "split_sha256")
    with pytest.raises(
        NaturalHistoryError,
        match="split precedes corpus privacy review",
    ):
        decode_split(split, corpus)


def test_report_must_start_after_adjudication_and_split() -> None:
    corpus, _, _, adjudication, split = _loaded_contracts()
    document = _document(DEFAULT_REPORT)
    document["run"]["started_at_utc"] = "2026-07-24T21:59:59Z"
    _resign(document, "report_sha256")
    with pytest.raises(
        NaturalHistoryError,
        match="starts before adjudication completed",
    ):
        decode_report(document, corpus, adjudication, split)

    split_document = _document(DEFAULT_SPLIT)
    split_document["policy"]["created_at_utc"] = "2026-07-24T23:00:00Z"
    _resign(split_document, "split_sha256")
    late_split = decode_split(split_document, corpus)
    document = _document(DEFAULT_REPORT)
    document["split_sha256"] = late_split.split_sha256
    _resign(document, "report_sha256")
    with pytest.raises(
        NaturalHistoryError,
        match="starts before split creation",
    ):
        decode_report(document, corpus, adjudication, late_split)


def test_annotation_requires_canonical_provenance_order() -> None:
    corpus_document = _document(DEFAULT_CORPUS)
    sources = corpus_document["histories"][0]["sources"]
    extra_source = copy.deepcopy(sources[0])
    extra_source.update(
        {
            "id": "fixture-public-api-s2",
            "sequence": 2,
            "timestamp": "2026-07-24T18:02:00Z",
        }
    )
    sources.append(extra_source)
    _resign(corpus_document, "corpus_sha256")
    corpus = decode_corpus(corpus_document)

    annotation = _document(DEFAULT_ANNOTATIONS[0])
    annotation["corpus_sha256"] = corpus.corpus_sha256
    provenance = annotation["histories"][0]["labels"][0]["provenance"]
    extra_span = copy.deepcopy(provenance[0])
    extra_span["source_id"] = extra_source["id"]
    provenance.append(extra_span)
    _resign(annotation, "annotation_sha256")
    decode_annotation(annotation, corpus)

    provenance.reverse()
    _resign(annotation, "annotation_sha256")
    with pytest.raises(
        NaturalHistoryError,
        match="provenance is not in canonical source order",
    ):
        decode_annotation(annotation, corpus)


def test_cli_atomically_exports_gold_free_and_verifies_full_kit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "gold-free.json"

    assert main(["--export-gold-free", str(output)]) == 0
    emitted = json.loads(capsys.readouterr().out)
    corpus, _, _, _, split = _loaded_contracts()
    assert emitted == _plain_json(load_gold_free(output, corpus, split))
    assert list(tmp_path.glob(".ctxc-*.tmp")) == []

    assert main([]) == 0
    verification = json.loads(capsys.readouterr().out)
    assert verification["verified"] is True
    assert verification["fixture_only"] is True
