from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from benchmarks.external_protocol import (
    EXACT_QWEN_MODEL_ID,
    EXTERNAL_PROTOCOL_SCHEMA,
    _canonical_sha256,
)

FIXTURE_NETWORK_ISOLATION_MODE = "host-firewall"
FIXTURE_NETWORK_ISOLATION_CONTENT = (
    b"fixture host firewall export: outbound network denied\n"
)
FIXTURE_NETWORK_ISOLATION_SHA256 = hashlib.sha256(
    FIXTURE_NETWORK_ISOLATION_CONTENT
).hexdigest()
FIXTURE_DEPENDENCY_LOCK_CONTENT = (
    b"fixture-package==1.0.0 --hash=sha256:"
    + b"a" * 64
    + b"\n"
)
FIXTURE_DEPENDENCY_LOCK_SHA256 = hashlib.sha256(
    FIXTURE_DEPENDENCY_LOCK_CONTENT
).hexdigest()
FIXTURE_ENVIRONMENT_ID = f"sha256:{FIXTURE_DEPENDENCY_LOCK_SHA256}"
FIXTURE_INFERENCE_SERVICE_MEMORY_METRIC = (
    "working-set-bytes" if os.name == "nt" else "resident-set-bytes"
)
FIXTURE_INFERENCE_SERVICE_EXECUTABLE_SHA256 = hashlib.sha256(
    Path(getattr(sys, "_base_executable", sys.executable)).read_bytes()
).hexdigest()
FIXTURE_MAX_INFERENCE_SERVICE_MEMORY_MB = 4_096


def _identity(system: str) -> str:
    return hashlib.sha256(system.encode("utf-8")).hexdigest()


def write_frozen_external_protocol(
    tmp_path: Path,
    systems: Sequence[str],
    *,
    adapter_revisions: Mapping[str, str] | None = None,
    environment_ids: Mapping[str, str] | None = None,
    adapter_entrypoint_sha256s: Mapping[str, str] | None = None,
    adapter_source_tree_sha256s: Mapping[str, str] | None = None,
    adapter_runtime_executable_sha256s: Mapping[str, str] | None = None,
    adapter_environment_sha256s: Mapping[str, str] | None = None,
    adapter_command_sha256s: Mapping[str, str] | None = None,
    synthetic_dataset_sha256: str = "9" * 64,
    max_memory_mb: int = 256,
    network_isolation_mode: str = FIXTURE_NETWORK_ISOLATION_MODE,
    network_isolation_evidence_sha256: str = (
        FIXTURE_NETWORK_ISOLATION_SHA256
    ),
    inference_service_memory_metric: str = (
        FIXTURE_INFERENCE_SERVICE_MEMORY_METRIC
    ),
    inference_service_executable_sha256: str = (
        FIXTURE_INFERENCE_SERVICE_EXECUTABLE_SHA256
    ),
    max_inference_service_memory_mb: int = (
        FIXTURE_MAX_INFERENCE_SERVICE_MEMORY_MB
    ),
) -> Path:
    registered = tuple(sorted(systems))
    if len(registered) < 4 or len(registered) != len(set(registered)):
        raise ValueError("fixture protocols require at least four unique systems")
    revisions = dict(adapter_revisions or {})
    environments = dict(environment_ids or {})
    entrypoints = dict(adapter_entrypoint_sha256s or {})
    source_trees = dict(adapter_source_tree_sha256s or {})
    runtimes = dict(adapter_runtime_executable_sha256s or {})
    process_environments = dict(adapter_environment_sha256s or {})
    commands = dict(adapter_command_sha256s or {})
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
                "adapter_entrypoint_sha256": entrypoints.get(
                    system,
                    identity,
                ),
                "adapter_source_tree_sha256": source_trees.get(
                    system,
                    _identity(f"{system}-source-tree"),
                ),
                "adapter_runtime_executable_sha256": runtimes.get(
                    system,
                    _identity(f"{system}-runtime"),
                ),
                "adapter_environment_sha256": process_environments.get(
                    system,
                    _identity(f"{system}-environment"),
                ),
                "adapter_command_sha256": commands.get(
                    system,
                    _identity(f"{system}-command"),
                ),
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
            "network_isolation_mode": network_isolation_mode,
            "network_isolation_evidence_sha256": (
                network_isolation_evidence_sha256
            ),
            "max_stdout_bytes": 1_000_000,
            "max_stderr_bytes": 1_000_000,
            "max_candidate_bytes": 20_000_000,
            "max_memory_mb": max_memory_mb,
            "inference_service_memory_metric": (
                inference_service_memory_metric
            ),
            "inference_service_executable_sha256": (
                inference_service_executable_sha256
            ),
            "max_inference_service_memory_mb": (
                max_inference_service_memory_mb
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
