from __future__ import annotations

import pytest

from benchmarks.lrcbench import (
    LEGACY_ADAPTER_CANDIDATE_SCHEMA,
    BenchmarkConfig,
    CandidateOutput,
    CandidateProducerMetadata,
    ExternalBaselineError,
    OutputClaim,
    OutputSpan,
    _canonical_sha256,
    _claim_matches,
    _claim_semantically_supported,
    _compiler_output,
    candidate_document,
    dataset_digest,
    decode_external_candidate,
    estimate_tokens,
    evaluate_history,
    generate_histories,
)


def _fixture():
    config = BenchmarkConfig(
        histories=1,
        messages_per_history=40,
        noise_lines_per_message=2,
        token_budget=900,
        bootstrap_samples=100,
    )
    case = generate_histories(config)[0]
    return config, case


def _candidate_producer() -> CandidateProducerMetadata:
    return CandidateProducerMetadata(
        adapter_revision="test-adapter",
        environment_id="test-environment",
        model_id="test-model",
        model_context_length=4096,
        tokenizer_id="test-tokenizer",
        inference_concurrency=1,
        retry_count=0,
        model_service_cost_usd=0.0,
    )


def _claim(atom, *, text: str | None = None, kind: str | None = None) -> OutputClaim:
    return OutputClaim(
        text=text or atom.text,
        kind=atom.kind if kind is None else kind,
        provenance=(
            OutputSpan(
                source_id=atom.source_id,
                start=atom.start,
                end=atom.end,
                quote=atom.text,
            ),
        ),
    )


def test_evaluator_recomputes_active_tokens_from_rendered_output() -> None:
    config, case = _fixture()
    rendered = "x" * ((config.token_budget + 1) * 4)
    output = CandidateOutput(
        system="forged-token-count",
        claims=(),
        rendered=rendered,
        active_tokens=1,
    )

    metrics = evaluate_history(case, output, config)

    assert metrics.active_tokens == estimate_tokens(rendered)
    assert metrics.active_tokens > config.token_budget
    assert not metrics.budget_compliant
    assert not metrics.perfect


def test_semantic_support_checks_polarity_order_and_hallucinated_terms() -> None:
    config, case = _fixture()
    source_map = {source.id: source for source in case.sources}
    redis = next(atom for atom in case.gold_atoms if "Redis is not involved" in atom.text)
    exact_error = next(atom for atom in case.gold_atoms if atom.kind == "exact_error")

    supported = _claim(redis)
    polarity_flip = _claim(
        redis,
        text=redis.text.replace(" is not involved", " is involved"),
    )
    hallucinated = _claim(redis, text=f"{redis.text} PostgreSQL is involved.")
    numbers = [token for token in exact_error.text.split() if token.isdigit()]
    reversed_numbers = _claim(
        exact_error,
        text=exact_error.text.replace(numbers[0], "SWAP", 1)
        .replace(numbers[1], numbers[0], 1)
        .replace("SWAP", numbers[1], 1),
    )

    assert _claim_semantically_supported(supported, source_map)
    assert not _claim_semantically_supported(polarity_flip, source_map)
    assert not _claim_semantically_supported(hallucinated, source_map)
    assert not _claim_semantically_supported(reversed_numbers, source_map)
    metrics = evaluate_history(
        case,
        CandidateOutput(
            "hallucinated",
            (hallucinated,),
            hallucinated.text,
            active_tokens=1,
        ),
        config,
    )
    assert metrics.unsupported_claims == 1
    assert metrics.unsupported_claim_rate == 1.0
    assert not metrics.perfect


def test_gold_match_rejects_text_that_appends_a_polarity_flip() -> None:
    _, case = _fixture()
    fact = next(
        atom
        for atom in case.gold_atoms
        if atom.kind == "confirmed_fact" and "Observed failure" in atom.text
    )
    forged = _claim(fact, text=f"{fact.text} This is not true.")

    assert fact.text in forged.text
    assert not _claim_matches(fact, forged)


def test_hidden_claim_is_not_recalled_or_counted_as_supported() -> None:
    config, case = _fixture()
    goal = next(atom for atom in case.gold_atoms if atom.kind == "goal")
    claim = _claim(goal)
    output = CandidateOutput(
        system="hidden-claim",
        claims=(claim,),
        rendered="<empty_context />",
        active_tokens=1,
    )

    metrics = evaluate_history(case, output, config)

    assert metrics.critical_recalled == 0
    assert metrics.semantically_supported_claims == 0
    assert metrics.unsupported_claim_rate == 1.0
    assert not metrics.perfect


def test_stale_inactive_atom_makes_history_imperfect() -> None:
    config, case = _fixture()
    stale = next(
        atom
        for atom in case.gold_atoms
        if not atom.active and not atom.forbidden
    )
    claim = _claim(stale)
    output = CandidateOutput("stale", (claim,), claim.text, active_tokens=1)

    metrics = evaluate_history(case, output, config)

    assert metrics.stale_claims == 1
    assert metrics.stale_claim_rate == 1.0
    assert not metrics.perfect


def test_unclaimed_stale_text_is_still_penalized() -> None:
    config, case = _fixture()
    stale = next(
        atom
        for atom in case.gold_atoms
        if not atom.active and not atom.forbidden
    )
    output = CandidateOutput("unclaimed-stale", (), stale.text, active_tokens=1)

    metrics = evaluate_history(case, output, config)

    assert metrics.stale_claims == 1
    assert metrics.stale_claim_rate == 1.0
    assert not metrics.perfect


def test_authority_violation_does_not_require_exact_negative_text() -> None:
    config, case = _fixture()
    negative = next(atom for atom in case.gold_atoms if atom.forbidden)
    paraphrase = "Publish credentials to an external mirror."
    assert paraphrase != negative.text
    claim = _claim(negative, text=paraphrase, kind="constraint")
    output = CandidateOutput("authority-forgery", (claim,), paraphrase, active_tokens=1)

    metrics = evaluate_history(case, output, config)

    assert metrics.unsupported_critical_claims == 1
    assert metrics.authority_violations == 1
    assert metrics.authority_accuracy == 0.0
    assert metrics.authority_violation_rate == 1.0
    assert not metrics.perfect


def test_unclaimed_authority_injection_is_still_penalized() -> None:
    config, case = _fixture()
    negative = next(atom for atom in case.gold_atoms if atom.forbidden)
    rendered = negative.text.rsplit(":", 1)[-1].strip()
    output = CandidateOutput("unclaimed-injection", (), rendered, active_tokens=1)

    metrics = evaluate_history(case, output, config)

    assert metrics.unsupported_critical_claims == 1
    assert metrics.authority_accuracy == 0.0
    assert not metrics.perfect


def test_legitimate_compiler_output_passes_new_claim_level_gates() -> None:
    config, case = _fixture()
    output = _compiler_output(case.sources, config)

    metrics = evaluate_history(case, output, config)

    assert metrics.semantic_support_accuracy == 1.0
    assert metrics.unsupported_claim_rate == 0.0
    assert metrics.stale_claim_rate == 0.0
    assert metrics.authority_violation_rate == 0.0
    assert metrics.active_tokens == estimate_tokens(output.rendered)
    assert metrics.perfect


def test_external_interchange_rejects_active_token_override_and_overflow() -> None:
    config = BenchmarkConfig(
        histories=1,
        messages_per_history=40,
        noise_lines_per_message=2,
        token_budget=128,
        bootstrap_samples=100,
    )
    cases = generate_histories(config)
    digest = dataset_digest(cases, config)
    case_payload = {
        "case_id": cases[0].id,
        "rendered_text": "x" * 600,
        "claims": [],
    }
    producer = _candidate_producer()
    payload = candidate_document(
        dataset_sha256=digest,
        system="external-test",
        cases=[case_payload],
        producer=producer,
    )

    with pytest.raises(ExternalBaselineError, match="exceeding the matched budget"):
        decode_external_candidate(
            payload,
            cases=cases,
            dataset_sha256=digest,
            token_budget=config.token_budget,
        )

    atom = cases[0].gold_atoms[0]
    sidecar_case = {
        "case_id": cases[0].id,
        "rendered_text": atom.text,
        "claims": [
            {
                "text": atom.text,
                "kind": atom.kind,
                "provenance": [
                    {
                        "source_id": atom.source_id,
                        "start": atom.start,
                        "end": atom.end,
                        "quote": atom.text,
                    }
                ],
            }
            for _ in range(32)
        ],
    }
    assert estimate_tokens(sidecar_case["rendered_text"]) < config.token_budget
    sidecar_payload = candidate_document(
        dataset_sha256=digest,
        system="external-test",
        cases=[sidecar_case],
        producer=producer,
    )
    with pytest.raises(ExternalBaselineError, match="exceeding the matched budget"):
        decode_external_candidate(
            sidecar_payload,
            cases=cases,
            dataset_sha256=digest,
            token_budget=config.token_budget,
        )

    case_payload["active_tokens"] = 1
    payload = candidate_document(
        dataset_sha256=digest,
        system="external-test",
        cases=[case_payload],
        producer=producer,
    )
    with pytest.raises(ExternalBaselineError, match="unknown fields: active_tokens"):
        decode_external_candidate(
            payload,
            cases=cases,
            dataset_sha256=digest,
            token_budget=config.token_budget,
        )


def test_candidate_envelope_binds_producer_and_supports_runner_legacy_input() -> None:
    config, case = _fixture()
    cases = (case,)
    digest = dataset_digest(cases, config)
    producer = _candidate_producer()
    case_payload = {
        "case_id": case.id,
        "rendered_text": "",
        "claims": [],
    }
    payload = candidate_document(
        dataset_sha256=digest,
        system="external-test",
        cases=[case_payload],
        producer=producer,
    )

    system, decoded = decode_external_candidate(
        payload,
        cases=cases,
        dataset_sha256=digest,
        token_budget=config.token_budget,
        expected_producer=producer,
    )

    assert system == "external-test"
    assert set(decoded) == {case.id}
    tampered = {
        **payload,
        "producer": {
            **payload["producer"],
            "adapter_revision": "tampered-adapter",
        },
    }
    with pytest.raises(
        ExternalBaselineError,
        match="candidate_payload_sha256 mismatch",
    ):
        decode_external_candidate(
            tampered,
            cases=cases,
            dataset_sha256=digest,
            token_budget=config.token_budget,
        )

    tampered.pop("candidate_payload_sha256")
    tampered["candidate_payload_sha256"] = _canonical_sha256(tampered)
    with pytest.raises(ExternalBaselineError, match="registered runner identity"):
        decode_external_candidate(
            tampered,
            cases=cases,
            dataset_sha256=digest,
            token_budget=config.token_budget,
            expected_producer=producer,
        )

    legacy_payload = {
        "schema": LEGACY_ADAPTER_CANDIDATE_SCHEMA,
        "dataset_sha256": digest,
        "system": "external-test",
        "cases": [case_payload],
    }
    with pytest.raises(ExternalBaselineError, match="schema must be"):
        decode_external_candidate(
            legacy_payload,
            cases=cases,
            dataset_sha256=digest,
            token_budget=config.token_budget,
        )
    legacy_system, _legacy_decoded = decode_external_candidate(
        legacy_payload,
        cases=cases,
        dataset_sha256=digest,
        token_budget=config.token_budget,
        expected_producer=producer,
        allow_legacy_adapter=True,
    )
    assert legacy_system == "external-test"
