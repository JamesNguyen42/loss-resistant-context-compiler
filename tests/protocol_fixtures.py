from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from benchmarks.external_protocol import (
    EXACT_QWEN_MODEL_ID,
    EXTERNAL_PROTOCOL_SCHEMA,
    _canonical_sha256,
)


def _identity(system: str) -> str:
    return hashlib.sha256(system.encode("utf-8")).hexdigest()


def write_frozen_external_protocol(
    tmp_path: Path,
    systems: Sequence[str],
    *,
    adapter_revisions: Mapping[str, str] | None = None,
    environment_ids: Mapping[str, str] | None = None,
    synthetic_dataset_sha256: str = "9" * 64,
    max_memory_mb: int = 256,
) -> Path:
    registered = tuple(sorted(systems))
    if len(registered) < 4 or len(registered) != len(set(registered)):
        raise ValueError("fixture protocols require at least four unique systems")
    revisions = dict(adapter_revisions or {})
    environments = dict(environment_ids or {})
    protocol_document = tmp_path / "external-protocol.md"
    protocol_document.write_text("# Frozen external fixture\n", encoding="utf-8")
    candidates: list[dict[str, Any]] = []
    for system in registered:
        identity = _identity(system)
        environment_id = environments.get(system, f"sha256:{identity}")
        if (
            not environment_id.startswith("sha256:")
            or len(environment_id) != 71
        ):
            raise ValueError(
                "fixture environment ids must be canonical SHA-256 identities"
            )
        candidates.append(
            {
                "system": system,
                "display_name": system.title(),
                "repository": f"https://github.com/example/{system}",
                "status": "include",
                "revision": identity[:40],
                "revision_observed_at": "2026-07-24T00:00:00+00:00",
                "license_spdx": "MIT",
                "license_file_sha256": identity,
                "dependency_lock_sha256": environment_id.removeprefix(
                    "sha256:"
                ),
                "adapter_revision": revisions.get(system, identity[:40]),
                "decision_reason": "Frozen result-blind fixture decision.",
            }
        )
    payload: dict[str, Any] = {
        "schema": EXTERNAL_PROTOCOL_SCHEMA,
        "protocol_id": "fixture-protocol-v1",
        "status": "frozen",
        "created_at": "2026-07-24T00:00:00+00:00",
        "frozen_at": "2026-07-24T01:00:00+00:00",
        "freeze_repository_commit": "a" * 40,
        "protocol_document": {
            "path": protocol_document.name,
            "file_sha256": hashlib.sha256(
                protocol_document.read_bytes()
            ).hexdigest(),
        },
        "comparison_candidates": candidates,
        "registered_systems": list(registered),
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
            {
                "id": dataset_id,
                "kind": kind,
                "status": "frozen",
                "revision": f"fixture-{dataset_id}-v1",
                "manifest_sha256": (
                    synthetic_dataset_sha256
                    if kind == "synthetic"
                    else _identity(dataset_id)
                ),
                "sample_size": 32,
                "seeds": (
                    [56_056]
                    if kind == "synthetic"
                    else [101]
                    if kind in {"coding-task", "second-task"}
                    else []
                ),
                "annotation": "Fixture annotation protocol.",
            }
            for dataset_id, kind in (
                ("coding-suite", "coding-task"),
                ("lrcbench", "synthetic"),
                ("natural-cohort", "natural-history"),
                ("second-suite", "second-task"),
            )
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
            "max_stdout_bytes": 1_000_000,
            "max_stderr_bytes": 1_000_000,
            "max_candidate_bytes": 20_000_000,
            "max_memory_mb": max_memory_mb,
            "inference_service_accounting": (
                "Shared exact-model service is sampled and reported."
            ),
            "shell_invocation": False,
            "overwrite_existing_outputs": False,
            "failure_policy": "registered-failures-are-non-wins",
            "offline_execution": True,
        },
        "blockers": [],
    }
    payload["protocol_sha256"] = _canonical_sha256(payload)
    protocol_path = tmp_path / "external-protocol.json"
    protocol_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return protocol_path
