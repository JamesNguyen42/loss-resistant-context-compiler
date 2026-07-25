"""Offline, result-blind audit for retained external compatibility evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .external_protocol import ExternalProtocolError, load_external_protocol
from .external_runner import (
    AdapterEntrypointEvidence,
    AdapterRuntimeEvidence,
    AdapterSourceEvidence,
    AdapterSourceFileEvidence,
    DependencyLockEvidence,
    ExternalRunManifest,
    InferenceServiceAccounting,
    NetworkIsolationEvidence,
    ProcessEnvironmentEvidence,
    RunnerIdentity,
    RunnerLimits,
)
from .json_io import StrictJsonError, StrictJsonLimits, load_strict_json_file

AUDIT_SCHEMA = "lrcbench-external-compatibility-audit-0.1"
DEFAULT_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_JSON_LIMITS = StrictJsonLimits(
    max_bytes=16 * 1024 * 1024,
    max_line_chars=2 * 1024 * 1024,
    max_depth=128,
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_EXACT_MODEL_ID = "qwen/qwen3.6-35b-a3b@q4_k_m"
_REQUIRED_ADAPTER_ARGUMENTS = (
    "--acon-source-root",
    "--dependency-lock",
    "--network-isolation-evidence",
    "--inference-service-pid",
    "--max-inference-service-memory-mb",
    "--lms-executable",
)
_COMPATIBILITY_FIELDS = {
    "schema",
    "preflight_id",
    "system",
    "display_name",
    "status",
    "result_blind",
    "comparative_output_generated",
    "comparative_output_inspected",
    "claim_ready",
    "source",
    "license",
    "dependencies",
    "interface",
    "upstream_execution",
    "required_diagnostic_contract",
    "diagnostic_adapter",
    "blockers",
    "decision",
}
_SOURCE_FIELDS = {
    "repository",
    "revision",
    "commit_url",
    "tree_url",
    "local_tree_state",
    "clean_tree_required",
}
_LICENSE_FIELDS = {"spdx", "file_url", "file_sha256"}
_CONTRACT_FIELDS = {
    "model_id",
    "quantization",
    "model_context_length",
    "inference_concurrency",
    "retry_count",
    "model_service_cost_usd",
    "adapter_isolation",
    "adapter_memory_limit_mb",
    "timeout_seconds",
    "network_access",
    "network_isolation_evidence_required",
    "inference_service_pid_and_memory_evidence_required",
}
_COMPATIBILITY_EXPECTATIONS = {
    "acon": {
        "display_name": "ACON",
        "preflight_id": "acon-d63f9ae-result-blind",
        "repository": "https://github.com/microsoft/acon",
        "revision": "d63f9ae18959dc7215ff62899c94c5e8c56847ae",
        "license_sha256": "c2cfccb812fe482101a8f04597dfc5a9991a6b2748266c47ac91b6a5aae15383",
        "blockers": (
            "clean pinned ACON source tree is not retained locally",
            "resolved hash-pinned offline dependency lock and wheelhouse are absent",
            "adapter environment and portable command digests are not frozen",
            "exact Qwen service executable digest and memory ceiling are not frozen",
            "enforced network-isolation evidence is not retained",
            "ACON summary output does not expose exact source provenance",
        ),
        "dependency_fields": {
            "declaration_url",
            "declared_python",
            "resolved_lock_state",
            "offline_wheelhouse_state",
            "notes",
        },
        "interface_fields": {
            "entrypoint_url",
            "prompt_url",
            "adapter_seam",
            "rendered_context_available",
            "exact_source_provenance_available",
        },
        "upstream_fields": {
            "quickstart_url",
            "documented_command",
            "documented_paid_or_network_secret",
            "matched_offline_command_state",
        },
        "matched_offline_command_state": "adapter-required",
        "decision": (
            "Remain in result-blind screening; do not include, exclude, score, "
            "or claim superiority from this preflight."
        ),
    },
    "ama-agent": {
        "display_name": "AMA-Agent",
        "preflight_id": "ama-agent-ddfd319-result-blind",
        "repository": "https://github.com/AMA-Bench/AMA-Bench",
        "revision": "ddfd319e0be33424288c13806f1eafc63e625b59",
        "license_sha256": "3c8bff7214b9a3f79e2b1e76413104fff6df8b315d6598b2e8d16a9cadd2e244",
        "blockers": (
            "clean pinned AMA-Agent source tree is not retained locally",
            "resolved hash-pinned offline dependency lock and wheelhouse are absent",
            "documented vLLM Qwen3-32B two-GPU 32000-context runtime does not "
            "match the frozen exact-Qwen 8192 one-slot contract",
            "upstream ModelClient defaults to three retries unless every call path is adapted",
            "generation and retrieval require multiple LLM calls and optional "
            "embedding dependencies",
            "adapter environment, command, service, and network-isolation evidence are not frozen",
            "AMA-Agent retrieved context does not expose LRCBench exact source spans",
        ),
        "dependency_fields": {
            "declaration_url",
            "declared_python",
            "declared_platform",
            "resolved_lock_state",
            "offline_wheelhouse_state",
            "notes",
        },
        "interface_fields": {
            "entrypoint_url",
            "model_client_url",
            "adapter_seam",
            "rendered_context_available",
            "exact_source_provenance_available",
        },
        "upstream_fields": {
            "readme_url",
            "local_config_url",
            "documented_command",
            "documented_runtime",
            "matched_offline_command_state",
        },
        "matched_offline_command_state": "unresolved",
        "decision": (
            "Remain in result-blind screening; ACON is the narrower first "
            "diagnostic adapter. Do not score or make a superiority claim."
        ),
    },
}
_BLOCKER_FIELDS = {
    "schema",
    "system",
    "repository",
    "revision",
    "attempted_command",
    "attempted_at_host_runtime",
    "status",
    "runner_invoked",
    "candidate_created",
    "manifest_created",
    "result_blind",
    "comparative_output_generated",
    "blockers",
    "disposition",
}
_CASE_RUN_FIELDS = {
    "candidate_bytes",
    "candidate_path",
    "candidate_payload_sha256",
    "candidate_sha256",
    "candidate_valid",
    "case_id",
    "command",
    "corpus_file_sha256",
    "corpus_path",
    "corpus_sha256",
    "duration_seconds",
    "exit_code",
    "process_succeeded",
    "started_at",
    "stderr_bytes",
    "stderr_sha256",
    "stdout_bytes",
    "stdout_sha256",
    "termination_reason",
    "validation_error",
}


class CompatibilityAuditError(ValueError):
    """Retained compatibility evidence is malformed or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class AuditBlocker:
    """One deterministic reason the retained evidence remains non-scoreable."""

    id: str
    detail: str


def _canonical_sha256(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CompatibilityAuditError("retained evidence is not canonical finite JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CompatibilityAuditError(f"{label} must be a JSON object")
    return value


def _exact_object(
    value: object,
    *,
    label: str,
    fields: set[str],
) -> dict[str, Any]:
    payload = _object(value, label=label)
    if set(payload) != fields:
        raise CompatibilityAuditError(f"{label} fields do not match the schema")
    return payload


def _bounded_string(
    value: object,
    *,
    label: str,
    maximum: int = 4_096,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise CompatibilityAuditError(f"{label} must be a bounded string")
    return value


def _exact_value(
    payload: dict[str, Any],
    name: str,
    expected: object,
    *,
    label: str,
) -> None:
    observed = payload.get(name)
    if observed != expected or type(observed) is not type(expected):
        raise CompatibilityAuditError(f"{label} {name} must remain {expected!r}")


def _load_object_evidence(
    root: Path,
    relative: str,
) -> tuple[dict[str, Any], str]:
    path = root / relative
    try:
        document = load_strict_json_file(
            path,
            limits=_JSON_LIMITS,
            label=relative,
        )
    except (OSError, StrictJsonError) as exc:
        raise CompatibilityAuditError(str(exc)) from exc
    return _object(document.value, label=relative), document.file_sha256


def _load_object(root: Path, relative: str) -> dict[str, Any]:
    payload, _file_sha256 = _load_object_evidence(root, relative)
    return payload


def _sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise CompatibilityAuditError(f"{label} must be a lowercase SHA-256")
    return value


def _revision(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _REVISION_RE.fullmatch(value) is None:
        raise CompatibilityAuditError(f"{label} must be a lowercase 40-character revision")
    return value


def _compatibility_record(
    payload: dict[str, Any],
    *,
    expected_system: str,
) -> dict[str, Any]:
    label = f"{expected_system} compatibility record"
    expectation = _COMPATIBILITY_EXPECTATIONS[expected_system]
    payload = _exact_object(
        payload,
        label=label,
        fields=_COMPATIBILITY_FIELDS,
    )
    if payload["schema"] != "lrcbench-compatibility-preflight-0.1":
        raise CompatibilityAuditError(f"{label} schema is invalid")
    _exact_value(payload, "system", expected_system, label=label)
    _exact_value(
        payload,
        "display_name",
        expectation["display_name"],
        label=label,
    )
    _exact_value(
        payload,
        "preflight_id",
        expectation["preflight_id"],
        label=label,
    )
    required_flags = {
        "status": "screening",
        "result_blind": True,
        "comparative_output_generated": False,
        "comparative_output_inspected": False,
        "claim_ready": False,
    }
    for name, expected in required_flags.items():
        _exact_value(payload, name, expected, label=label)

    source = _exact_object(
        payload["source"],
        label=f"{label}.source",
        fields=_SOURCE_FIELDS,
    )
    repository = str(expectation["repository"])
    _exact_value(source, "repository", repository, label=f"{label}.source")
    revision = _revision(
        source["revision"],
        label=f"{label}.source.revision",
    )
    _exact_value(
        source,
        "revision",
        expectation["revision"],
        label=f"{label}.source",
    )
    _exact_value(
        source,
        "commit_url",
        f"{repository}/commit/{revision}",
        label=f"{label}.source",
    )
    _exact_value(
        source,
        "tree_url",
        f"{repository}/tree/{revision}",
        label=f"{label}.source",
    )
    _exact_value(
        source,
        "local_tree_state",
        "not-materialized",
        label=f"{label}.source",
    )
    _exact_value(
        source,
        "clean_tree_required",
        True,
        label=f"{label}.source",
    )

    license_record = _exact_object(
        payload["license"],
        label=f"{label}.license",
        fields=_LICENSE_FIELDS,
    )
    _exact_value(license_record, "spdx", "MIT", label=f"{label}.license")
    _exact_value(
        license_record,
        "file_url",
        f"{repository}/blob/{revision}/LICENSE",
        label=f"{label}.license",
    )
    license_sha256 = _sha256(
        license_record["file_sha256"],
        label=f"{label}.license.file_sha256",
    )
    _exact_value(
        license_record,
        "file_sha256",
        expectation["license_sha256"],
        label=f"{label}.license",
    )

    dependency_fields = expectation["dependency_fields"]
    assert isinstance(dependency_fields, set)
    dependencies = _exact_object(
        payload["dependencies"],
        label=f"{label}.dependencies",
        fields=dependency_fields,
    )
    _exact_value(
        dependencies,
        "resolved_lock_state",
        "absent",
        label=f"{label}.dependencies",
    )
    _exact_value(
        dependencies,
        "offline_wheelhouse_state",
        "absent",
        label=f"{label}.dependencies",
    )
    for name, value in dependencies.items():
        _bounded_string(value, label=f"{label}.dependencies.{name}")
    declaration_url = str(dependencies["declaration_url"])
    if not declaration_url.startswith(f"{repository}/blob/{revision}/"):
        raise CompatibilityAuditError(
            f"{label}.dependencies.declaration_url is not revision-pinned"
        )

    interface_fields = expectation["interface_fields"]
    assert isinstance(interface_fields, set)
    interface = _exact_object(
        payload["interface"],
        label=f"{label}.interface",
        fields=interface_fields,
    )
    _exact_value(
        interface,
        "rendered_context_available",
        True,
        label=f"{label}.interface",
    )
    _exact_value(
        interface,
        "exact_source_provenance_available",
        False,
        label=f"{label}.interface",
    )
    for name, value in interface.items():
        if name in {
            "rendered_context_available",
            "exact_source_provenance_available",
        }:
            continue
        text = _bounded_string(value, label=f"{label}.interface.{name}")
        if name.endswith("_url") and not text.startswith(f"{repository}/blob/{revision}/"):
            raise CompatibilityAuditError(f"{label}.interface.{name} is not revision-pinned")

    upstream_fields = expectation["upstream_fields"]
    assert isinstance(upstream_fields, set)
    upstream = _exact_object(
        payload["upstream_execution"],
        label=f"{label}.upstream_execution",
        fields=upstream_fields,
    )
    _exact_value(
        upstream,
        "matched_offline_command_state",
        expectation["matched_offline_command_state"],
        label=f"{label}.upstream_execution",
    )
    for name, value in upstream.items():
        text = _bounded_string(
            value,
            label=f"{label}.upstream_execution.{name}",
        )
        if name.endswith("_url") and not text.startswith(f"{repository}/blob/{revision}/"):
            raise CompatibilityAuditError(
                f"{label}.upstream_execution.{name} is not revision-pinned"
            )

    contract = _exact_object(
        payload["required_diagnostic_contract"],
        label=f"{label}.required_diagnostic_contract",
        fields=_CONTRACT_FIELDS,
    )
    exact_contract = {
        "model_id": _EXACT_MODEL_ID,
        "quantization": "Q4_K_M",
        "model_context_length": 8192,
        "inference_concurrency": 1,
        "retry_count": 0,
        "model_service_cost_usd": 0,
        "adapter_isolation": "per-case",
        "adapter_memory_limit_mb": 32768,
        "timeout_seconds": 300,
        "network_access": False,
        "network_isolation_evidence_required": True,
        "inference_service_pid_and_memory_evidence_required": True,
    }
    for name, expected in exact_contract.items():
        _exact_value(
            contract,
            name,
            expected,
            label=f"{label}.required_diagnostic_contract",
        )

    diagnostic_adapter = payload["diagnostic_adapter"]
    if expected_system == "acon":
        adapter = _exact_object(
            diagnostic_adapter,
            label=f"{label}.diagnostic_adapter",
            fields={"path", "candidate_schema", "claims", "scoring_permitted"},
        )
        adapter_expectations = {
            "path": "benchmarks/adapters/acon_diagnostic/adapter.py",
            "candidate_schema": "lrcbench-candidate-output-0.1",
            "claims": "empty-by-design",
            "scoring_permitted": False,
        }
        for name, expected in adapter_expectations.items():
            _exact_value(
                adapter,
                name,
                expected,
                label=f"{label}.diagnostic_adapter",
            )
    elif diagnostic_adapter is not None:
        raise CompatibilityAuditError(f"{label}.diagnostic_adapter must remain null")

    blockers = payload["blockers"]
    if (
        not isinstance(blockers, list)
        or not 1 <= len(blockers) <= 64
        or not all(isinstance(item, str) for item in blockers)
        or len(set(blockers)) != len(blockers)
    ):
        raise CompatibilityAuditError(f"{label} must retain unique blockers")
    for index, blocker in enumerate(blockers):
        _bounded_string(
            blocker,
            label=f"{label}.blockers[{index}]",
            maximum=1_024,
        )
    expected_blockers = expectation["blockers"]
    if not isinstance(expected_blockers, tuple) or blockers != list(expected_blockers):
        raise CompatibilityAuditError(f"{label} blockers do not match retained evidence")
    _exact_value(
        payload,
        "decision",
        expectation["decision"],
        label=label,
    )
    return {
        "system": expected_system,
        "repository": repository,
        "revision": revision,
        "license_sha256": license_sha256,
        "resolved_lock_state": dependencies["resolved_lock_state"],
        "offline_wheelhouse_state": dependencies["offline_wheelhouse_state"],
    }


def _protocol_candidates(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_candidates = payload.get("comparison_candidates")
    if not isinstance(raw_candidates, list):
        raise CompatibilityAuditError("external protocol comparison_candidates must be an array")
    candidates: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_candidates):
        candidate = _object(
            raw,
            label=f"external protocol comparison_candidates[{index}]",
        )
        system = candidate.get("system")
        if not isinstance(system, str) or not system:
            raise CompatibilityAuditError(
                f"external protocol comparison_candidates[{index}].system is invalid"
            )
        if system in candidates:
            raise CompatibilityAuditError(f"external protocol repeats candidate {system!r}")
        candidates[system] = candidate
    return candidates


def _validate_preflight_blocker(
    payload: dict[str, Any],
    *,
    acon: dict[str, Any],
) -> None:
    payload = _exact_object(
        payload,
        label="ACON preflight blocker",
        fields=_BLOCKER_FIELDS,
    )
    if payload["schema"] != "lrcbench-external-execution-blocker-0.1":
        raise CompatibilityAuditError("ACON preflight blocker schema is invalid")
    identity = {
        "system": "acon",
        "repository": acon["repository"],
        "revision": acon["revision"],
    }
    for name, expected in identity.items():
        _exact_value(
            payload,
            name,
            expected,
            label="ACON preflight blocker",
        )
    required = {
        "status": "blocked-before-external-runner",
        "runner_invoked": False,
        "candidate_created": False,
        "manifest_created": False,
        "result_blind": True,
        "comparative_output_generated": False,
    }
    for name, expected in required.items():
        _exact_value(
            payload,
            name,
            expected,
            label="ACON preflight blocker",
        )
    attempted_command = payload["attempted_command"]
    expected_command = [
        ".venv/Scripts/python.exe",
        "benchmarks/adapters/acon_diagnostic/adapter.py",
        "preflight",
    ]
    if attempted_command != expected_command:
        raise CompatibilityAuditError(
            "ACON preflight blocker command is not the retained executable preflight"
        )
    _bounded_string(
        payload["attempted_at_host_runtime"],
        label="ACON preflight blocker attempted_at_host_runtime",
        maximum=128,
    )
    expected_blockers = [
        "missing --acon-source-root",
        "adapter runtime must be exactly Python 3.11",
        "missing --dependency-lock",
        "missing --network-isolation-evidence",
        "missing positive --inference-service-pid",
        "missing --max-inference-service-memory-mb",
        "missing --lms-executable",
    ]
    if payload["blockers"] != expected_blockers:
        raise CompatibilityAuditError("ACON preflight blocker must retain the exact blockers")
    expected_disposition = (
        "Do not invoke the external runner until every blocker is resolved "
        "and retained as evidence; do not score this preflight."
    )
    _exact_value(
        payload,
        "disposition",
        expected_disposition,
        label="ACON preflight blocker",
    )


def _validate_failed_manifest(
    payload: dict[str, Any],
    *,
    acon: dict[str, Any],
) -> None:
    expected_fields = {
        "schema",
        "manifest_sha256",
        *ExternalRunManifest.__dataclass_fields__,
    }
    payload = _exact_object(
        payload,
        label="ACON failed manifest",
        fields=expected_fields,
    )
    if payload["schema"] != "lrcbench-external-run-manifest-0.13":
        raise CompatibilityAuditError("ACON failed manifest schema is invalid")
    claimed_sha256 = _sha256(
        payload["manifest_sha256"],
        label="ACON failed manifest manifest_sha256",
    )
    unsigned = dict(payload)
    unsigned.pop("manifest_sha256")
    if _canonical_sha256(unsigned) != claimed_sha256:
        raise CompatibilityAuditError("ACON failed manifest SHA-256 mismatch")

    fixed_values = {
        "system": "acon-diagnostic",
        "isolation_mode": "per_case",
        "corpus_schema": "lrcbench-corpus-0.3",
        "candidate_schema": "lrcbench-candidate-output-0.2",
        "process_succeeded": False,
        "candidate_valid": False,
        "ready_for_scoring": False,
        "claim_metadata_complete": False,
        "memory_limit_enforced": True,
    }
    for name, expected in fixed_values.items():
        _exact_value(payload, name, expected, label="ACON failed manifest")
    if payload["candidate_sha256"] is not None or payload["candidate_bytes"] is not None:
        raise CompatibilityAuditError("ACON failed manifest cannot retain a candidate payload")

    identity_payload = _exact_object(
        payload["identity"],
        label="ACON failed manifest identity",
        fields=set(RunnerIdentity.__dataclass_fields__),
    )
    try:
        RunnerIdentity(**identity_payload)
    except (TypeError, ValueError) as exc:
        raise CompatibilityAuditError(f"ACON failed manifest identity is invalid: {exc}") from exc
    expected_identity = {
        "adapter_revision": acon["revision"],
        "environment_id": "unrecorded",
        "model_id": _EXACT_MODEL_ID,
        "model_context_length": 8192,
        "tokenizer_id": "character-estimate-v1",
        "inference_concurrency": 1,
        "retry_count": 0,
        "model_service_cost_usd": 0.0,
    }
    for name, expected in expected_identity.items():
        _exact_value(
            identity_payload,
            name,
            expected,
            label="ACON failed manifest identity",
        )

    limits_payload = _exact_object(
        payload["limits"],
        label="ACON failed manifest limits",
        fields=set(RunnerLimits.__dataclass_fields__),
    )
    try:
        limits = RunnerLimits(**limits_payload)
    except (TypeError, ValueError) as exc:
        raise CompatibilityAuditError(f"ACON failed manifest limits are invalid: {exc}") from exc
    expected_limits = {
        "timeout_seconds": 300.0,
        "max_stdout_bytes": 1_000_000,
        "max_stderr_bytes": 1_000_000,
        "max_candidate_bytes": 20_000_000,
        "max_memory_mb": 32_768,
        "poll_interval_seconds": 0.02,
    }
    for name, expected in expected_limits.items():
        observed = limits_payload[name]
        if observed != expected or type(observed) is not type(expected):
            raise CompatibilityAuditError(f"ACON failed manifest limits {name} is invalid")
    if limits.max_memory_mb is None:
        raise CompatibilityAuditError("ACON failed manifest lost its adapter memory ceiling")

    dependency_payload = _exact_object(
        payload["dependency_lock"],
        label="ACON failed manifest dependency_lock",
        fields=set(DependencyLockEvidence.__dataclass_fields__),
    )
    try:
        dependency = DependencyLockEvidence(**dependency_payload)
    except (TypeError, ValueError) as exc:
        raise CompatibilityAuditError(
            f"ACON failed manifest dependency lock is invalid: {exc}"
        ) from exc
    if dependency.claim_evidence_complete:
        raise CompatibilityAuditError("ACON retained failure cannot claim dependency-lock evidence")

    entrypoint_payload = _exact_object(
        payload["adapter_entrypoint"],
        label="ACON failed manifest adapter_entrypoint",
        fields=set(AdapterEntrypointEvidence.__dataclass_fields__),
    )
    try:
        entrypoint = AdapterEntrypointEvidence(**entrypoint_payload)
    except (TypeError, ValueError) as exc:
        raise CompatibilityAuditError(
            f"ACON failed manifest adapter entrypoint is invalid: {exc}"
        ) from exc

    source_payload = _exact_object(
        payload["adapter_source"],
        label="ACON failed manifest adapter_source",
        fields=set(AdapterSourceEvidence.__dataclass_fields__),
    )
    raw_source_files = source_payload["files"]
    if not isinstance(raw_source_files, list):
        raise CompatibilityAuditError("ACON failed manifest adapter_source.files must be an array")
    source_files: list[AdapterSourceFileEvidence] = []
    for index, raw_source_file in enumerate(raw_source_files):
        source_file_payload = _exact_object(
            raw_source_file,
            label=f"ACON failed manifest adapter_source.files[{index}]",
            fields=set(AdapterSourceFileEvidence.__dataclass_fields__),
        )
        try:
            source_files.append(AdapterSourceFileEvidence(**source_file_payload))
        except (TypeError, ValueError) as exc:
            raise CompatibilityAuditError(
                f"ACON failed manifest adapter source file is invalid: {exc}"
            ) from exc
    try:
        source = AdapterSourceEvidence(
            **{
                **source_payload,
                "files": tuple(source_files),
            }
        )
    except (TypeError, ValueError) as exc:
        raise CompatibilityAuditError(
            f"ACON failed manifest adapter source is invalid: {exc}"
        ) from exc
    entrypoint_matches = [
        item
        for item in source.files
        if item.relative_path == "adapter.py"
        and item.file_sha256 == entrypoint.entrypoint_sha256
        and item.file_bytes == entrypoint.entrypoint_bytes
    ]
    if len(entrypoint_matches) != 1:
        raise CompatibilityAuditError(
            "ACON failed manifest source does not bind its adapter entrypoint"
        )

    runtime_payload = _exact_object(
        payload["adapter_runtime"],
        label="ACON failed manifest adapter_runtime",
        fields=set(AdapterRuntimeEvidence.__dataclass_fields__),
    )
    try:
        runtime = AdapterRuntimeEvidence(**runtime_payload)
    except (TypeError, ValueError) as exc:
        raise CompatibilityAuditError(
            f"ACON failed manifest adapter runtime is invalid: {exc}"
        ) from exc

    environment_payload = _exact_object(
        payload["process_environment"],
        label="ACON failed manifest process_environment",
        fields=set(ProcessEnvironmentEvidence.__dataclass_fields__),
    )
    variable_names = environment_payload["variable_names"]
    if not isinstance(variable_names, list):
        raise CompatibilityAuditError("ACON failed manifest environment names must be an array")
    try:
        ProcessEnvironmentEvidence(
            **{
                **environment_payload,
                "variable_names": tuple(variable_names),
            }
        )
    except (TypeError, ValueError) as exc:
        raise CompatibilityAuditError(
            f"ACON failed manifest process environment is invalid: {exc}"
        ) from exc

    network_payload = _exact_object(
        payload["network_isolation"],
        label="ACON failed manifest network_isolation",
        fields=set(NetworkIsolationEvidence.__dataclass_fields__),
    )
    try:
        network = NetworkIsolationEvidence(**network_payload)
    except (TypeError, ValueError) as exc:
        raise CompatibilityAuditError(
            f"ACON failed manifest network isolation is invalid: {exc}"
        ) from exc
    if network.mode != "unverified":
        raise CompatibilityAuditError(
            "ACON retained failure cannot claim verified network isolation"
        )

    service_payload = _exact_object(
        payload["inference_service"],
        label="ACON failed manifest inference_service",
        fields=set(InferenceServiceAccounting.__dataclass_fields__),
    )
    try:
        service = InferenceServiceAccounting(**service_payload)
    except (TypeError, ValueError) as exc:
        raise CompatibilityAuditError(
            f"ACON failed manifest inference service is invalid: {exc}"
        ) from exc
    if service.claim_evidence_complete:
        raise CompatibilityAuditError(
            "ACON retained failure cannot claim measured inference service evidence"
        )

    command = payload["command"]
    command_contract = payload["command_contract"]
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
        or not isinstance(command_contract, list)
        or not command_contract
        or not all(isinstance(part, str) and part for part in command_contract)
    ):
        raise CompatibilityAuditError("ACON failed manifest command is invalid")
    command_sha256 = _sha256(
        payload["command_sha256"],
        label="ACON failed manifest command_sha256",
    )
    if _canonical_sha256(command_contract) != command_sha256:
        raise CompatibilityAuditError("ACON failed manifest command contract SHA-256 mismatch")
    if command[0] != runtime.executable_path:
        raise CompatibilityAuditError(
            "ACON failed manifest command does not use its retained runtime"
        )

    case_count = payload["case_count"]
    case_runs = payload["case_runs"]
    if (
        isinstance(case_count, bool)
        or not isinstance(case_count, int)
        or case_count <= 0
        or not isinstance(case_runs, list)
        or len(case_runs) != case_count
    ):
        raise CompatibilityAuditError("ACON failed manifest case accounting is invalid")
    for index, raw in enumerate(case_runs):
        case = _exact_object(
            raw,
            label=f"ACON failed manifest case_runs[{index}]",
            fields=_CASE_RUN_FIELDS,
        )
        if case["process_succeeded"] is not False:
            raise CompatibilityAuditError(
                f"ACON failed manifest case_runs[{index}] became successful"
            )
        if case["candidate_valid"] is not False:
            raise CompatibilityAuditError(
                f"ACON failed manifest case_runs[{index}] became candidate-valid"
            )
        if not isinstance(case["command"], list) or not all(
            isinstance(part, str) and part for part in case["command"]
        ):
            raise CompatibilityAuditError(
                f"ACON failed manifest case_runs[{index}] command is invalid"
            )
        for name in (
            "corpus_file_sha256",
            "corpus_sha256",
            "stderr_sha256",
            "stdout_sha256",
        ):
            _sha256(
                case[name],
                label=f"ACON failed manifest case_runs[{index}].{name}",
            )
        for name in ("stderr_bytes", "stdout_bytes"):
            value = case[name]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CompatibilityAuditError(
                    f"ACON failed manifest case_runs[{index}].{name} is invalid"
                )

    for name in (
        "working_directory",
        "started_at",
        "corpus_path",
        "candidate_path",
        "python_version",
        "platform",
        "termination_reason",
    ):
        _bounded_string(
            payload[name],
            label=f"ACON failed manifest {name}",
        )
    for name in (
        "corpus_sha256",
        "corpus_file_sha256",
        "dataset_sha256",
        "stdout_sha256",
        "stderr_sha256",
    ):
        _sha256(payload[name], label=f"ACON failed manifest {name}")


def _missing_adapter_arguments(command: list[str]) -> list[str]:
    missing: list[str] = []
    for argument in _REQUIRED_ADAPTER_ARGUMENTS:
        positions = [index for index, part in enumerate(command) if part == argument]
        if len(positions) != 1:
            missing.append(argument)
            continue
        position = positions[0]
        if position + 1 >= len(command):
            missing.append(argument)
            continue
        value = command[position + 1]
        if not value or value.startswith("--"):
            missing.append(argument)
            continue
        if argument in {
            "--inference-service-pid",
            "--max-inference-service-memory-mb",
        }:
            try:
                parsed = int(value)
            except ValueError:
                missing.append(argument)
                continue
            if parsed <= 0 or str(parsed) != value:
                missing.append(argument)
    return missing


def audit_repository(
    repository_root: str | Path = DEFAULT_REPOSITORY_ROOT,
) -> dict[str, Any]:
    """Audit retained external evidence without running or scoring any adapter."""

    root = Path(repository_root).expanduser().resolve()
    protocol_path = root / "benchmarks/protocols/external-comparison-v1.json"
    try:
        verified_protocol = load_external_protocol(protocol_path)
    except ExternalProtocolError as exc:
        raise CompatibilityAuditError(str(exc)) from exc
    protocol, protocol_file_sha256 = _load_object_evidence(
        root,
        "benchmarks/protocols/external-comparison-v1.json",
    )
    if protocol_file_sha256 != verified_protocol.file_sha256:
        raise CompatibilityAuditError("external protocol changed between verified reads")
    acon_payload = _load_object(root, "benchmarks/compatibility/acon-v1.json")
    ama_payload = _load_object(
        root,
        "benchmarks/compatibility/ama-agent-v1.json",
    )
    preflight_blocker = _load_object(
        root,
        "benchmarks/compatibility/acon-diagnostic-blocker.json",
    )
    failed_manifest = _load_object(
        root,
        "benchmarks/compatibility/acon-diagnostic-failed-manifest.json",
    )

    acon = _compatibility_record(acon_payload, expected_system="acon")
    ama = _compatibility_record(ama_payload, expected_system="ama-agent")
    _validate_preflight_blocker(preflight_blocker, acon=acon)
    _validate_failed_manifest(failed_manifest, acon=acon)
    candidates = _protocol_candidates(protocol)

    blockers: list[AuditBlocker] = []
    if verified_protocol.status != "frozen":
        blockers.append(
            AuditBlocker(
                "external-protocol-draft",
                "The external comparison protocol remains draft and cannot authorize claims.",
            )
        )
    for record in (acon, ama):
        system = str(record["system"])
        candidate = candidates.get(system)
        if candidate is None:
            raise CompatibilityAuditError(f"external protocol has no {system!r} candidate")
        if (
            candidate.get("repository") != record["repository"]
            or candidate.get("revision") != record["revision"]
        ):
            raise CompatibilityAuditError(
                f"{system} protocol source identity does not match compatibility evidence"
            )
        protocol_license_sha256 = _sha256(
            candidate.get("license_file_sha256"),
            label=f"{system} protocol license_file_sha256",
        )
        if protocol_license_sha256 != record["license_sha256"]:
            blockers.append(
                AuditBlocker(
                    f"{system}-license-hash-mismatch",
                    "Compatibility LICENSE SHA-256 "
                    f"{record['license_sha256']} does not match draft protocol "
                    f"SHA-256 {protocol_license_sha256}.",
                )
            )
        if (
            record["resolved_lock_state"] != "present"
            or record["offline_wheelhouse_state"] != "present"
        ):
            blockers.append(
                AuditBlocker(
                    f"{system}-dependency-lock-absent",
                    "No resolved, hash-pinned dependency lock and offline wheelhouse are retained.",
                )
            )

    if failed_manifest["process_succeeded"] is False:
        blockers.append(
            AuditBlocker(
                "acon-external-run-failed",
                "The retained external-runner attempt exited nonzero and "
                "produced no valid candidate.",
            )
        )
    if failed_manifest["claim_metadata_complete"] is False:
        blockers.append(
            AuditBlocker(
                "acon-external-run-claim-controls-incomplete",
                "The retained failed run does not satisfy claim-control evidence.",
            )
        )
    dependency_lock = _object(
        failed_manifest.get("dependency_lock"),
        label="ACON failed manifest dependency_lock",
    )
    if dependency_lock.get("evidence_sha256") is None:
        blockers.append(
            AuditBlocker(
                "acon-external-run-dependency-lock-absent",
                "The retained failed run has no dependency-lock evidence digest.",
            )
        )
    network = _object(
        failed_manifest.get("network_isolation"),
        label="ACON failed manifest network_isolation",
    )
    if network.get("mode") == "unverified":
        blockers.append(
            AuditBlocker(
                "acon-external-run-network-unverified",
                "The retained failed run has no externally enforced network-isolation evidence.",
            )
        )
    service = _object(
        failed_manifest.get("inference_service"),
        label="ACON failed manifest inference_service",
    )
    if service.get("process_id") is None or service.get("sample_count") == 0:
        blockers.append(
            AuditBlocker(
                "acon-external-run-service-unmeasured",
                "The retained failed run has no bound inference-service "
                "identity or memory samples.",
            )
        )
    command = failed_manifest["command"]
    assert isinstance(command, list)
    missing_arguments = _missing_adapter_arguments(command)
    if missing_arguments:
        blockers.append(
            AuditBlocker(
                "acon-external-run-command-missing-preflight-arguments",
                "The retained external-run command omits or has invalid values for: "
                + ", ".join(missing_arguments)
                + ".",
            )
        )
    python_version = str(failed_manifest["python_version"])
    if not python_version.startswith("3.11."):
        blockers.append(
            AuditBlocker(
                "acon-external-run-runtime-mismatch",
                "The retained failed run used Python "
                f"{python_version}; the pinned adapter requires Python 3.11.",
            )
        )

    ordered_blockers = sorted(blockers, key=lambda blocker: blocker.id)
    return {
        "schema": AUDIT_SCHEMA,
        "status": "blocked" if ordered_blockers else "clear-for-review",
        "result_blind": True,
        "comparative_output_generated": False,
        "comparative_output_inspected": False,
        "ready_for_scoring": False,
        "external_execution_performed_by_audit": False,
        "protocol_sha256": verified_protocol.protocol_sha256,
        "attempts": [
            {
                "kind": "adapter-preflight",
                "status": preflight_blocker["status"],
                "runner_invoked": preflight_blocker["runner_invoked"],
                "candidate_created": preflight_blocker["candidate_created"],
                "manifest_created": preflight_blocker["manifest_created"],
            },
            {
                "kind": "external-runner",
                "status": "failed",
                "started_at": failed_manifest["started_at"],
                "manifest_sha256": failed_manifest["manifest_sha256"],
                "candidate_valid": failed_manifest["candidate_valid"],
                "ready_for_scoring": failed_manifest["ready_for_scoring"],
            },
        ],
        "checks": [
            "strict bounded JSON parsing",
            "draft protocol self-verification",
            "source revision and repository reconciliation",
            "license digest reconciliation",
            "failed manifest self-hash verification",
            "failed case and non-scoreable boundary verification",
            "external command prerequisite coverage",
        ],
        "blockers": [asdict(blocker) for blocker in ordered_blockers],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=DEFAULT_REPOSITORY_ROOT,
    )
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = audit_repository(args.repository_root)
    except (CompatibilityAuditError, OSError, TypeError, ValueError) as exc:
        report = {
            "schema": AUDIT_SCHEMA,
            "status": "invalid",
            "result_blind": True,
            "comparative_output_generated": False,
            "comparative_output_inspected": False,
            "ready_for_scoring": False,
            "external_execution_performed_by_audit": False,
            "error": str(exc),
        }
    sys.stdout.write(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.compact else 2,
        )
        + "\n"
    )
    return 0 if report["status"] == "clear-for-review" else 2


if __name__ == "__main__":
    raise SystemExit(main())
