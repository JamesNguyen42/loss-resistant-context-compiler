from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from benchmarks.phrase_eval import (
    DEFAULT_PHRASE_CORPUS,
    DEFAULT_PHRASE_CORPUS_SHA256,
    PHRASE_CORPUS_SCHEMA,
    PHRASE_REPORT_SCHEMA,
    PHRASE_VERIFICATION_SCHEMA,
    PhraseEvaluationError,
    decode_phrase_corpus,
    load_phrase_corpus,
    load_phrase_report,
    main,
    run_phrase_evaluation,
    verify_phrase_report,
)

_COMMITTED_REPORT = Path(
    "docs/results/novel-english-phrases-v1.json"
)
_COMMITTED_REPORT_SHA256 = (
    "3b137430c0430e65c090ff30b3fabd369fbfd7d23cf3299e580a90badab989ea"
)


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _corpus_document() -> dict:
    return json.loads(DEFAULT_PHRASE_CORPUS.read_text(encoding="utf-8"))


def _resign_corpus(document: dict) -> None:
    unsigned = copy.deepcopy(document)
    unsigned.pop("corpus_sha256", None)
    document["corpus_sha256"] = _canonical_sha256(unsigned)


def _resign_report(document: dict) -> None:
    unsigned = copy.deepcopy(document)
    unsigned.pop("report_sha256", None)
    document["report_sha256"] = _canonical_sha256(unsigned)


def test_bundled_phrase_corpus_is_frozen_balanced_and_api_free() -> None:
    corpus = load_phrase_corpus()
    positive_counts = Counter(
        atom.kind.value
        for case in corpus.cases
        for atom in case.expected
    )
    negative_counts = Counter(
        case.id.split("-")[1]
        for case in corpus.cases
        if not case.expected
    )

    assert corpus.schema == PHRASE_CORPUS_SCHEMA
    assert corpus.corpus_sha256 == DEFAULT_PHRASE_CORPUS_SHA256
    assert len(corpus.cases) == 64
    assert positive_counts == {
        "goal": 5,
        "constraint": 5,
        "unresolved": 5,
        "decision": 5,
        "confirmed_fact": 5,
        "discarded_attempt": 5,
        "exact_error": 5,
        "exact_reference": 5,
    }
    assert negative_counts == {
        "tool": 8,
        "authority": 8,
        "mention": 8,
    }
    assert corpus.authoring.draft_model == (
        "qwen/qwen3.6-35b-a3b@q4_k_m"
    )
    assert corpus.authoring.quantization == "Q4_K_M"
    assert corpus.authoring.transport == "lm-studio-cli"
    assert corpus.authoring.network_model_api is False
    assert corpus.authoring.model_service_cost_usd == 0.0


def test_phrase_evaluation_replays_the_committed_baseline() -> None:
    corpus = load_phrase_corpus()
    report = run_phrase_evaluation(corpus, package_version="0.1.0")
    committed = load_phrase_report(_COMMITTED_REPORT)

    assert report == committed
    assert report["schema"] == PHRASE_REPORT_SCHEMA
    assert report["report_sha256"] == _COMMITTED_REPORT_SHA256
    metrics = report["metrics"]
    assert {
        key: value
        for key, value in metrics.items()
        if key != "by_kind"
    } == {
        "expected_atoms": 40,
        "predicted_atoms": 41,
        "true_positives": 35,
        "false_positives": 6,
        "false_negatives": 5,
        "precision": 0.853659,
        "recall": 0.875,
        "f1": 0.864198,
        "case_exact_matches": 55,
        "case_exact_match_rate": 0.859375,
        "positive_case_exact_matches": 35,
        "positive_case_exact_match_rate": 0.875,
        "negative_cases_without_predictions": 20,
        "negative_case_accuracy": 0.833333,
        "verification_failures": 0,
    }

    assert metrics["by_kind"]["goal"]["recall"] == 1.0
    assert metrics["by_kind"]["exact_reference"]["precision"] == 1.0
    assert metrics["by_kind"]["decision"]["recall"] == 0.6
    assert metrics["by_kind"]["confirmed_fact"]["precision"] == 0.666667
    assert {
        case["id"]
        for case in report["cases"]
        if not case["exact_match"]
    } == {
        "p-decision-04",
        "p-decision-05",
        "p-fact-04",
        "p-discarded-04",
        "p-error-04",
        "n-mention-01",
        "n-mention-04",
        "n-mention-05",
        "n-mention-07",
    }


@pytest.mark.parametrize(
    "package_version",
    [
        "0.1.0",
        "0.1.1a1",
        "0.1.1a2",
        "0.1.1a3",
        "0.1.1a4",
        "0.1.1a5",
        "0.1.1a6",
        "0.1.1a7",
        "0.1.1a8",
        "0.1.1a9",
        "0.1.1a10",
        "0.1.1a11",
        "0.1.1a12",
        "0.1.1a13",
        "0.1.1a14",
        "0.1.1a15",
        "0.1.1a16",
        "0.1.1a17",
        "0.1.1a18",
        "0.1.1a19",
        "0.1.1a20",
        "0.1.1a21",
    ],
)
def test_phrase_evaluation_replays_each_supported_package_identity(
    package_version: str,
) -> None:
    corpus = load_phrase_corpus()
    report = run_phrase_evaluation(
        corpus,
        package_version=package_version,
    )

    assert report["system"]["package_version"] == package_version
    assert verify_phrase_report(report, corpus)["verified"] is True


@pytest.mark.parametrize(
    "package_version",
    [True, 1, 0.1, None, "0.1.1", "0.1.1a1 ", type("S", (str,), {})("0.1.1a1")],
)
def test_phrase_evaluation_rejects_other_package_identities(
    package_version: object,
) -> None:
    with pytest.raises(PhraseEvaluationError, match="package version is unsupported"):
        run_phrase_evaluation(
            load_phrase_corpus(),
            package_version=package_version,  # type: ignore[arg-type]
        )


def test_phrase_report_verification_replays_all_case_evidence() -> None:
    corpus = load_phrase_corpus()
    summary = verify_phrase_report(
        load_phrase_report(_COMMITTED_REPORT),
        corpus,
    )

    assert summary == {
        "schema": PHRASE_VERIFICATION_SCHEMA,
        "verified": True,
        "corpus_sha256": DEFAULT_PHRASE_CORPUS_SHA256,
        "report_sha256": _COMMITTED_REPORT_SHA256,
        "case_count": 64,
    }


def test_phrase_report_rejects_tampering_before_replay() -> None:
    corpus = load_phrase_corpus()
    report = load_phrase_report(_COMMITTED_REPORT)
    report["metrics"]["precision"] = 1.0

    with pytest.raises(
        PhraseEvaluationError,
        match="report SHA-256 mismatch",
    ):
        verify_phrase_report(report, corpus)


def test_self_consistent_phrase_report_mutation_fails_replay() -> None:
    corpus = load_phrase_corpus()
    report = load_phrase_report(_COMMITTED_REPORT)
    report["metrics"]["precision"] = 1.0
    _resign_report(report)

    with pytest.raises(
        PhraseEvaluationError,
        match="does not match deterministic replay",
    ):
        verify_phrase_report(report, corpus)


def test_phrase_corpus_rejects_digest_mismatch() -> None:
    document = _corpus_document()
    document["cases"][0]["content"] += " tampered"

    with pytest.raises(
        PhraseEvaluationError,
        match="corpus SHA-256 mismatch",
    ):
        decode_phrase_corpus(document)


def test_self_consistent_phrase_corpus_rejects_duplicate_case_id() -> None:
    document = _corpus_document()
    document["cases"][1]["id"] = document["cases"][0]["id"]
    _resign_corpus(document)

    with pytest.raises(PhraseEvaluationError, match="duplicate phrase case id"):
        decode_phrase_corpus(document)


def test_self_consistent_phrase_corpus_rejects_unknown_field() -> None:
    document = _corpus_document()
    document["unexpected"] = True
    _resign_corpus(document)

    with pytest.raises(
        PhraseEvaluationError,
        match="fields are invalid.*unknown unexpected",
    ):
        decode_phrase_corpus(document)


def test_phrase_corpus_rejects_partial_ordinary_gold_atom() -> None:
    document = _corpus_document()
    document["cases"][0]["expected"][0]["text"] = "streamline the image upload flow"
    _resign_corpus(document)

    with pytest.raises(
        PhraseEvaluationError,
        match="ordinary expected atom must equal",
    ):
        decode_phrase_corpus(document)


def test_phrase_corpus_rejects_gold_from_unauthorized_role() -> None:
    document = _corpus_document()
    document["cases"][0]["role"] = "assistant"
    _resign_corpus(document)

    with pytest.raises(
        PhraseEvaluationError,
        match="cannot author expected kind",
    ):
        decode_phrase_corpus(document)


def test_phrase_corpus_rejects_multiline_case() -> None:
    document = _corpus_document()
    document["cases"][-1]["content"] += "\u2028second line"
    _resign_corpus(document)

    with pytest.raises(
        PhraseEvaluationError,
        match="must contain one physical line",
    ):
        decode_phrase_corpus(document)


def test_phrase_cli_writes_and_verifies_report_atomically(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "phrase-report.json"

    assert main(["--json-out", str(output)]) == 0
    generated_stdout = json.loads(capsys.readouterr().out)
    assert generated_stdout == load_phrase_report(output)
    assert list(tmp_path.glob(".ctxc-*.tmp")) == []

    assert main(["--verify-report", str(output)]) == 0
    verification_stdout = json.loads(capsys.readouterr().out)
    assert verification_stdout["schema"] == PHRASE_VERIFICATION_SCHEMA
    assert verification_stdout["verified"] is True


def test_phrase_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    corpus_path = tmp_path / "duplicate.json"
    corpus_path.write_text(
        '{"schema":"ctxc-phrase-corpus-0.1",'
        '"schema":"ctxc-phrase-corpus-0.1"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate JSON object key"):
        load_phrase_corpus(corpus_path)
