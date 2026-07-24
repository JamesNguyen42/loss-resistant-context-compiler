from __future__ import annotations

import json
from dataclasses import replace

import pytest

from benchmarks.lrcbench import (
    REQUIRED_STRATA,
    AggregateMetrics,
    BenchmarkConfig,
    ExternalBaselineError,
    HistoryCase,
    HistoryMetrics,
    _aggregate,
    _make_certificate,
    dataset_digest,
    generate_histories,
    run_benchmark,
)


def history(
    case_id: str,
    *,
    critical_recalled: int,
    critical_total: int,
    exact_recalled: int | None = None,
    exact_total: int | None = None,
    quality: float,
    active_tokens: int = 100,
    perfect: bool = False,
) -> HistoryMetrics:
    exact_recalled = critical_recalled if exact_recalled is None else exact_recalled
    exact_total = critical_total if exact_total is None else exact_total
    critical_recall = critical_recalled / critical_total
    exact_recall = exact_recalled / exact_total
    return HistoryMetrics(
        case_id=case_id,
        critical_recalled=critical_recalled,
        critical_total=critical_total,
        exact_recalled=exact_recalled,
        exact_total=exact_total,
        valid_claims=10,
        claim_total=10,
        unresolved_promotions=0,
        unresolved_total=1,
        unsupported_critical_claims=0,
        authority_negative_total=1,
        authority_violations=0,
        authority_claim_total=10,
        semantically_supported_claims=10,
        unsupported_claims=0,
        stale_claims=0,
        inactive_atom_total=1,
        source_tokens=1_000,
        active_tokens=active_tokens,
        budget_compliant=True,
        compression_ratio=1_000 / active_tokens,
        critical_atom_recall=critical_recall,
        exact_literal_recall=exact_recall,
        provenance_validity=1.0,
        unresolved_to_fact_rate=0.0,
        unsupported_critical_claim_rate=0.0,
        authority_accuracy=1.0,
        authority_violation_rate=0.0,
        semantic_support_accuracy=1.0,
        unsupported_claim_rate=0.0,
        stale_claim_rate=0.0,
        quality_score=quality,
        perfect=perfect,
    )


def test_claim_estimands_are_history_weighted_for_points_and_efficiency() -> None:
    measured = (
        history(
            "small",
            critical_recalled=1,
            critical_total=1,
            quality=1.0,
            active_tokens=10,
        ),
        history(
            "large",
            critical_recalled=0,
            critical_total=100,
            quality=0.5,
            active_tokens=100,
        ),
    )

    aggregate = _aggregate("candidate", measured)

    assert aggregate.critical_atom_recall == pytest.approx(0.5)
    assert aggregate.critical_atom_recall != pytest.approx(1 / 101)
    assert aggregate.memory_quality_efficiency == pytest.approx(
        ((1.0 / 10) + (0.5 / 100)) / 2
    )


def test_bootstrap_quantile_is_explicit_and_bounded() -> None:
    assert BenchmarkConfig().bootstrap_lower_quantile == 0.025
    for value in (0.0, 0.5, -0.1, 1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="bootstrap_lower_quantile"):
            BenchmarkConfig(bootstrap_lower_quantile=value)


def test_benchmark_config_rejects_boolean_and_nonfinite_numeric_values() -> None:
    with pytest.raises(TypeError, match="histories must be an integer"):
        BenchmarkConfig(histories=True)
    with pytest.raises(TypeError, match="bootstrap_samples must be an integer"):
        BenchmarkConfig(bootstrap_samples=100.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="minimum_compression must be numeric"):
        BenchmarkConfig(minimum_compression=True)
    with pytest.raises(ValueError, match="minimum_compression must be finite"):
        BenchmarkConfig(minimum_compression=float("nan"))


def benchmark_fixture() -> tuple[
    BenchmarkConfig,
    tuple[HistoryCase, ...],
    AggregateMetrics,
    AggregateMetrics,
]:
    config = BenchmarkConfig(
        histories=24,
        messages_per_history=24,
        noise_lines_per_message=1,
        token_budget=900,
        bootstrap_samples=100,
    )
    cases = tuple(
        HistoryCase(
            id=f"case-{index:03d}",
            sources=(),
            gold_atoms=(),
            strata=tuple(REQUIRED_STRATA),
        )
        for index in range(config.histories)
    )
    candidate_histories = tuple(
        history(
            case.id,
            critical_recalled=10,
            critical_total=10,
            quality=1.0,
            perfect=True,
        )
        for case in cases
    )
    weak_histories = tuple(
        history(
            case.id,
            critical_recalled=8,
            critical_total=10,
            quality=0.4,
        )
        for case in cases
    )
    return (
        config,
        cases,
        _aggregate("compiler", candidate_histories),
        _aggregate("weak", weak_histories),
    )


def test_external_certificate_requires_independent_strict_majority_wins() -> None:
    config, cases, candidate, weak = benchmark_fixture()
    systems = ["external-a", "external-b", "external-c", "external-d"]
    results = [
        candidate,
        replace(weak, system="external-a"),
        replace(weak, system="external-b"),
        replace(weak, system="external-c"),
        replace(candidate, system="external-d"),
    ]

    certificate = _make_certificate(
        config,
        "a" * 64,
        cases,
        results,
        external_systems=systems,
    )

    assert certificate.issued
    assert certificate.external_wins == 3
    assert certificate.external_required_wins == 3
    assert certificate.external_majority_passed
    assert {
        comparison.system: comparison.decision
        for comparison in certificate.comparisons
    } == {
        "external-a": "win",
        "external-b": "win",
        "external-c": "win",
        "external-d": "tie",
    }


def test_missing_registered_external_output_is_an_invalid_non_win() -> None:
    config, cases, candidate, weak = benchmark_fixture()
    systems = ["external-a", "external-b", "external-c", "external-missing"]
    results = [
        candidate,
        replace(weak, system="external-a"),
        replace(weak, system="external-b"),
        replace(candidate, system="external-c"),
    ]

    certificate = _make_certificate(
        config,
        "b" * 64,
        cases,
        results,
        external_systems=systems,
    )

    assert not certificate.issued
    assert certificate.external_wins == 2
    assert certificate.external_required_wins == 3
    assert certificate.external_majority_passed is False
    missing = next(
        comparison
        for comparison in certificate.comparisons
        if comparison.system == "external-missing"
    )
    assert missing.decision == "invalid"
    assert "no valid candidate output" in missing.reasons[0]


def test_valid_candidate_cannot_bypass_a_failed_or_missing_run_manifest() -> None:
    config, cases, candidate, weak = benchmark_fixture()
    certificate = _make_certificate(
        config,
        "c" * 64,
        cases,
        [candidate, replace(weak, system="external-a")],
        external_systems=("external-a",),
        external_failures={
            "external-a": "no validated external run manifest was supplied"
        },
    )

    comparison = next(
        value for value in certificate.comparisons if value.system == "external-a"
    )
    assert not certificate.issued
    assert comparison.decision == "invalid"
    assert any(
        "no validated external run manifest" in reason
        for reason in comparison.reasons
    )


def test_external_manifest_hash_is_bound_into_certificate_evidence() -> None:
    config, cases, candidate, weak = benchmark_fixture()
    results = [candidate, replace(weak, system="external-a")]

    first = _make_certificate(
        config,
        "d" * 64,
        cases,
        results,
        external_systems=("external-a",),
        external_manifest_sha256={"external-a": "1" * 64},
    )
    second = _make_certificate(
        config,
        "d" * 64,
        cases,
        results,
        external_systems=("external-a",),
        external_manifest_sha256={"external-a": "2" * 64},
    )

    assert first.issued and second.issued
    assert first.evidence_sha256 != second.evidence_sha256
    assert first.external_manifests[0].manifest_sha256 == "1" * 64


def test_external_scoring_requires_an_explicit_registered_comparison_set() -> None:
    with pytest.raises(ExternalBaselineError, match="explicitly registered"):
        run_benchmark(
            BenchmarkConfig(
                histories=1,
                messages_per_history=24,
                noise_lines_per_message=1,
                bootstrap_samples=100,
            ),
            external_baseline_paths=("not-read-without-registration.json",),
        )


def test_registered_missing_and_degenerate_outputs_remain_external_nonwins(
    tmp_path,
) -> None:
    config = BenchmarkConfig(
        histories=1,
        messages_per_history=24,
        noise_lines_per_message=1,
        token_budget=900,
        bootstrap_samples=100,
    )
    cases = generate_histories(config)
    digest = dataset_digest(cases, config)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(
        json.dumps(
            {
                "schema": "lrcbench-candidate-output-0.1",
                "dataset_sha256": digest,
                "system": "registered-empty",
                "cases": [
                    {
                        "case_id": case.id,
                        "rendered_text": "",
                        "claims": [],
                    }
                    for case in cases
                ],
            }
        ),
        encoding="utf-8",
    )

    report = run_benchmark(
        config,
        external_baseline_paths=(candidate_path,),
        expected_external_systems=("registered-empty", "registered-missing"),
    )

    assert report.certificate.scope == "external-inclusive"
    assert not report.certificate.issued
    assert report.certificate.external_wins == 0
    assert report.certificate.external_required_wins == 2
    assert {
        comparison.system: comparison.decision
        for comparison in report.certificate.comparisons
        if comparison.external
    } == {
        "registered-empty": "invalid",
        "registered-missing": "invalid",
    }


@pytest.mark.parametrize("name", [" leading", "trailing ", "two words", "compiler"])
def test_registered_external_names_are_canonical_and_do_not_collide(name: str) -> None:
    with pytest.raises(ExternalBaselineError, match="valid identifiers|collide"):
        run_benchmark(
            BenchmarkConfig(
                histories=1,
                messages_per_history=24,
                noise_lines_per_message=1,
                bootstrap_samples=100,
            ),
            expected_external_systems=(name,),
        )
