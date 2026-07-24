from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.external_protocol import (
    DEFAULT_EXTERNAL_PROTOCOL,
    EXACT_QWEN_MODEL_ID,
    EXTERNAL_PROTOCOL_SCHEMA,
    ExternalProtocolError,
    _canonical_sha256,
    load_external_protocol,
    main,
)


def _sha(character: str) -> str:
    return character * 64


def _candidate(
    system: str,
    character: str,
    *,
    status: str,
) -> dict[str, Any]:
    return {
        "system": system,
        "display_name": system.title(),
        "repository": f"https://github.com/example/{system}",
        "status": status,
        "revision": character * 40,
        "revision_observed_at": "2026-07-24T00:00:00+00:00",
        "license_spdx": "MIT",
        "license_file_sha256": _sha(character),
        "dependency_lock_sha256": _sha(character),
        "adapter_revision": character * 40,
        "adapter_entrypoint_sha256": _sha(character),
        "adapter_source_tree_sha256": _sha(character),
        "adapter_runtime_executable_sha256": _sha(character),
        "adapter_command_sha256": _sha(character),
        "decision_reason": "Frozen result-blind fixture decision.",
    }


def _dataset(
    dataset_id: str,
    kind: str,
    character: str,
    *,
    status: str = "frozen",
) -> dict[str, Any]:
    frozen = status == "frozen"
    return {
        "id": dataset_id,
        "kind": kind,
        "status": status,
        "revision": f"fixture-{dataset_id}-v1" if frozen else None,
        "manifest_sha256": _sha(character) if frozen else None,
        "sample_size": 32 if frozen else None,
        "seeds": (
            [56_056]
            if kind == "synthetic"
            else [101]
            if kind in {"coding-task", "second-task"}
            else []
        ),
        "annotation": "Fixture annotation protocol.",
    }


def _protocol(
    tmp_path: Path,
    *,
    frozen: bool,
) -> tuple[dict[str, Any], Path, Path]:
    protocol_document = tmp_path / "protocol.md"
    protocol_document.write_text("# Frozen fixture\n", encoding="utf-8")
    document_sha256 = hashlib.sha256(
        protocol_document.read_bytes()
    ).hexdigest()
    systems = ("alpha", "beta", "delta", "gamma")
    status = "include" if frozen else "screening"
    candidates = [
        _candidate(system, character, status=status)
        for system, character in zip(
            systems,
            ("1", "2", "3", "4"),
            strict=True,
        )
    ]
    payload: dict[str, Any] = {
        "schema": EXTERNAL_PROTOCOL_SCHEMA,
        "protocol_id": "fixture-protocol-v1",
        "status": "frozen" if frozen else "draft",
        "created_at": "2026-07-24T00:00:00+00:00",
        "frozen_at": (
            "2026-07-24T01:00:00+00:00"
            if frozen
            else None
        ),
        "freeze_repository_commit": "a" * 40 if frozen else None,
        "protocol_document": {
            "path": protocol_document.name,
            "file_sha256": document_sha256,
        },
        "comparison_candidates": candidates,
        "registered_systems": list(systems) if frozen else [],
        "constraints": {
            "model_id": EXACT_QWEN_MODEL_ID,
            "quantization": "Q4_K_M",
            "model_context_length": 8_192,
            "inference_concurrency": 1,
            "adapter_process_concurrency": 1,
            "network_model_api": False,
            "execution_network_access": False,
            "paid_service": False,
            "model_service_cost_usd": 0,
            "retry_count": 0,
            "tokenizer_id": "character-estimate-v1",
            "active_token_budget": 900,
        },
        "datasets": [
            _dataset("coding-suite", "coding-task", "5"),
            _dataset("lrcbench", "synthetic", "6"),
            _dataset("natural-cohort", "natural-history", "7"),
            _dataset("second-suite", "second-task", "8"),
        ],
        "statistics": {
            "lrcbench_seed": 56_056,
            "minimum_synthetic_histories": 24,
            "default_synthetic_histories": 32,
            "paired_bootstrap_samples": 2_000,
            "bootstrap_lower_quantile": 0.025,
            "component_gain_threshold": 0.5,
            "minimum_compression_ratio": 5.0,
            "minimum_registered_systems": 4,
        },
        "runner": {
            "isolation_mode": "per_case",
            "timeout_seconds": 300,
            "poll_interval_seconds": 0.02,
            "network_isolation_mode": (
                "host-firewall" if frozen else None
            ),
            "network_isolation_evidence_sha256": (
                _sha("9") if frozen else None
            ),
            "max_stdout_bytes": 1_000_000,
            "max_stderr_bytes": 1_000_000,
            "max_candidate_bytes": 20_000_000,
            "max_memory_mb": 8_192 if frozen else None,
            "inference_service_memory_metric": (
                "working-set-bytes" if frozen else None
            ),
            "inference_service_executable_sha256": (
                _sha("a") if frozen else None
            ),
            "max_inference_service_memory_mb": (
                32_768 if frozen else None
            ),
            "shell_invocation": False,
            "overwrite_existing_outputs": False,
            "failure_policy": "registered-failures-are-non-wins",
            "offline_execution": True,
        },
        "blockers": (
            []
            if frozen
            else [
                {
                    "id": "fixture-blocker",
                    "description": "Fixture remains intentionally incomplete.",
                }
            ]
        ),
    }
    payload["protocol_sha256"] = _canonical_sha256(payload)
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload, path, protocol_document


def _resign(payload: dict[str, Any]) -> None:
    payload.pop("protocol_sha256", None)
    payload["protocol_sha256"] = _canonical_sha256(payload)


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_committed_draft_is_verified_but_not_claim_ready() -> None:
    verified = load_external_protocol(DEFAULT_EXTERNAL_PROTOCOL)

    assert verified.protocol_sha256 == (
        "094b6a708fe070427ad736a1aaad017c763b81ac884a7a9680c48b9e33beeba4"
    )
    assert verified.status == "draft"
    assert verified.claim_ready is False
    assert verified.synthetic_dataset_sha256 == (
        "421d49585ef9ac96fe2a378f79c18da1791e508789ac0290d3cc5018cda07761"
    )
    assert verified.registered_systems == ()
    assert verified.candidate_count == 4
    assert verified.blocker_ids == (
        "adapter-memory-limit",
        "candidate-adapters",
        "candidate-dependency-locks",
        "coding-task-suite",
        "downstream-samples",
        "inference-service-accounting",
        "natural-history-cohort",
        "network-isolation-evidence",
        "second-task-suite",
    )
    with pytest.raises(ExternalProtocolError, match="not claim-ready"):
        load_external_protocol(
            DEFAULT_EXTERNAL_PROTOCOL,
            require_frozen=True,
        )


def test_complete_frozen_protocol_is_claim_ready(tmp_path: Path) -> None:
    _payload, path, _document = _protocol(tmp_path, frozen=True)

    verified = load_external_protocol(path, require_frozen=True)

    assert verified.claim_ready is True
    assert verified.status == "frozen"
    assert verified.registered_systems == (
        "alpha",
        "beta",
        "delta",
        "gamma",
    )
    assert verified.synthetic_dataset_sha256 == _sha("6")
    assert dict(verified.environment_ids) == {
        "alpha": "sha256:" + _sha("1"),
        "beta": "sha256:" + _sha("2"),
        "delta": "sha256:" + _sha("3"),
        "gamma": "sha256:" + _sha("4"),
    }
    assert dict(verified.adapter_entrypoint_sha256s) == {
        "alpha": _sha("1"),
        "beta": _sha("2"),
        "delta": _sha("3"),
        "gamma": _sha("4"),
    }
    assert dict(verified.adapter_source_tree_sha256s) == {
        "alpha": _sha("1"),
        "beta": _sha("2"),
        "delta": _sha("3"),
        "gamma": _sha("4"),
    }
    assert dict(verified.adapter_runtime_executable_sha256s) == {
        "alpha": _sha("1"),
        "beta": _sha("2"),
        "delta": _sha("3"),
        "gamma": _sha("4"),
    }
    assert dict(verified.adapter_command_sha256s) == {
        "alpha": _sha("1"),
        "beta": _sha("2"),
        "delta": _sha("3"),
        "gamma": _sha("4"),
    }
    assert verified.execution_contract.to_dict() == {
        "model_id": EXACT_QWEN_MODEL_ID,
        "model_context_length": 8_192,
        "tokenizer_id": "character-estimate-v1",
        "inference_concurrency": 1,
        "retry_count": 0,
        "model_service_cost_usd": 0.0,
        "active_token_budget": 900,
        "isolation_mode": "per_case",
        "timeout_seconds": 300.0,
        "poll_interval_seconds": 0.02,
        "network_isolation_mode": "host-firewall",
        "network_isolation_evidence_sha256": _sha("9"),
        "inference_service_memory_metric": "working-set-bytes",
        "inference_service_executable_sha256": _sha("a"),
        "max_inference_service_memory_mb": 32_768,
        "max_stdout_bytes": 1_000_000,
        "max_stderr_bytes": 1_000_000,
        "max_candidate_bytes": 20_000_000,
        "max_memory_mb": 8_192,
    }
    assert verified.blocker_ids == ()


def test_frozen_protocol_fails_closed_on_unresolved_controls(
    tmp_path: Path,
) -> None:
    base, path, _document = _protocol(tmp_path, frozen=True)
    mutations: tuple[
        tuple[str, Any, str],
        ...,
    ] = (
        (
            "screening candidate",
            lambda value: value["comparison_candidates"][0].update(
                status="screening"
            ),
            "screening candidates",
        ),
        (
            "bundled system collision",
            lambda value: value["comparison_candidates"][0].update(
                system="compiler"
            ),
            "collides with a bundled system",
        ),
        (
            "missing dependency lock",
            lambda value: value["comparison_candidates"][0].update(
                dependency_lock_sha256=None
            ),
            "lacks frozen identity",
        ),
        (
            "missing adapter entrypoint",
            lambda value: value["comparison_candidates"][0].update(
                adapter_entrypoint_sha256=None
            ),
            "lacks frozen identity",
        ),
        (
            "missing adapter source tree",
            lambda value: value["comparison_candidates"][0].update(
                adapter_source_tree_sha256=None
            ),
            "lacks frozen identity",
        ),
        (
            "missing adapter runtime",
            lambda value: value["comparison_candidates"][0].update(
                adapter_runtime_executable_sha256=None
            ),
            "lacks frozen identity",
        ),
        (
            "missing adapter command",
            lambda value: value["comparison_candidates"][0].update(
                adapter_command_sha256=None
            ),
            "lacks frozen identity",
        ),
        (
            "too few systems",
            lambda value: (
                value["comparison_candidates"].pop(),
                value["registered_systems"].pop(),
            ),
            "too few registered",
        ),
        (
            "pending dataset",
            lambda value: value["datasets"][0].update(
                status="pending",
                revision=None,
                manifest_sha256=None,
                sample_size=None,
            ),
            "every dataset",
        ),
        (
            "undersized synthetic cohort",
            lambda value: value["datasets"][1].update(sample_size=24),
            "sample_size must equal 32",
        ),
        (
            "missing downstream task seeds",
            lambda value: value["datasets"][0].update(seeds=[]),
            "must record task seeds",
        ),
        (
            "missing memory limit",
            lambda value: value["runner"].update(max_memory_mb=None),
            "requires adapter memory",
        ),
        (
            "missing service accounting",
            lambda value: value["runner"].update(
                inference_service_memory_metric=None,
                inference_service_executable_sha256=None,
                max_inference_service_memory_mb=None,
            ),
            "measured inference-service",
        ),
        (
            "partial service accounting",
            lambda value: value["runner"].update(
                inference_service_executable_sha256=None,
            ),
            "requires a memory metric",
        ),
        (
            "unsupported service metric",
            lambda value: value["runner"].update(
                inference_service_memory_metric="estimated-bytes",
            ),
            "memory_metric is unsupported",
        ),
        (
            "missing network isolation evidence",
            lambda value: value["runner"].update(
                network_isolation_mode=None,
                network_isolation_evidence_sha256=None,
            ),
            "network-isolation evidence",
        ),
        (
            "unsupported network isolation assertion",
            lambda value: value["runner"].update(
                network_isolation_mode="unverified"
            ),
            "supported mode and evidence",
        ),
        (
            "remaining blocker",
            lambda value: value["blockers"].append(
                {
                    "id": "still-blocked",
                    "description": "Not ready.",
                }
            ),
            "unresolved blockers",
        ),
        (
            "network model API",
            lambda value: value["constraints"].update(
                network_model_api=True
            ),
            "network_model_api",
        ),
        (
            "changed active token budget",
            lambda value: value["constraints"].update(
                active_token_budget=901
            ),
            "active_token_budget",
        ),
        (
            "changed runner polling cadence",
            lambda value: value["runner"].update(
                poll_interval_seconds=0.01
            ),
            "poll_interval_seconds",
        ),
    )
    for _label, mutate, error in mutations:
        payload = copy.deepcopy(base)
        mutate(payload)
        _resign(payload)
        _write(path, payload)
        with pytest.raises(ExternalProtocolError, match=error):
            load_external_protocol(path, require_frozen=True)


def test_protocol_hash_document_binding_and_paths_fail_closed(
    tmp_path: Path,
) -> None:
    base, path, document = _protocol(tmp_path, frozen=True)

    hash_tamper = copy.deepcopy(base)
    hash_tamper["comparison_candidates"][0][
        "decision_reason"
    ] = "Changed without re-signing."
    _write(path, hash_tamper)
    with pytest.raises(ExternalProtocolError, match="protocol_sha256 mismatch"):
        load_external_protocol(path)

    tampered = copy.deepcopy(base)
    tampered["statistics"]["paired_bootstrap_samples"] = 2_001
    _write(path, tampered)
    with pytest.raises(ExternalProtocolError, match="paired_bootstrap_samples"):
        load_external_protocol(path)

    _write(path, base)
    document.write_text("# Changed fixture\n", encoding="utf-8")
    with pytest.raises(ExternalProtocolError, match="retained document"):
        load_external_protocol(path)

    document.write_text("# Frozen fixture\n", encoding="utf-8")
    traversal = copy.deepcopy(base)
    traversal["protocol_document"]["path"] = "../outside.md"
    _resign(traversal)
    _write(path, traversal)
    with pytest.raises(ExternalProtocolError, match="relative POSIX"):
        load_external_protocol(path)


def test_protocol_loader_rejects_duplicate_and_nonfinite_json(
    tmp_path: Path,
) -> None:
    payload, path, _document = _protocol(tmp_path, frozen=True)
    encoded = json.dumps(payload)
    duplicate = encoded.replace(
        "{",
        f'{{"schema":"{EXTERNAL_PROTOCOL_SCHEMA}",',
        1,
    )
    path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(ExternalProtocolError, match="duplicate JSON object key"):
        load_external_protocol(path)

    nonfinite = encoded.replace(
        '"model_service_cost_usd": 0',
        '"model_service_cost_usd": NaN',
    )
    path.write_text(nonfinite, encoding="utf-8")
    with pytest.raises(ExternalProtocolError, match="non-standard JSON"):
        load_external_protocol(path)


def test_protocol_cli_reports_draft_and_requires_freeze(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--verify", str(DEFAULT_EXTERNAL_PROTOCOL)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["verified"] is True
    assert output["claim_ready"] is False

    with pytest.raises(SystemExit) as raised:
        main(
            [
                "--verify",
                str(DEFAULT_EXTERNAL_PROTOCOL),
                "--require-frozen",
            ]
        )
    assert raised.value.code == 2
    assert "not claim-ready" in capsys.readouterr().err
