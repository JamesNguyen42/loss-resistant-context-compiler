"""Strict verification for external-comparison preregistration manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from context_compiler.local_qwen import (
    QWEN_Q4_CONTEXT_LENGTH,
    QWEN_Q4_QUANTIZATION,
    QWEN_Q4_VARIANT,
)

from .external_runner import (
    _DARWIN_PRELIMIT_LAUNCHER,
    _DARWIN_PRELIMIT_LAUNCHER_PROTOCOL,
)
from .json_io import (
    StrictJsonError,
    StrictJsonLimits,
    hash_bounded_regular_file,
    load_strict_json_file,
)
from .lrcbench import TOKENIZER_ID

EXTERNAL_PROTOCOL_SCHEMA = "lrcbench-external-protocol-0.10"
DEFAULT_EXTERNAL_PROTOCOL = (
    Path(__file__).resolve().parent
    / "protocols"
    / "external-comparison-v1.json"
)
EXACT_QWEN_MODEL_ID = QWEN_Q4_VARIANT
EXACT_QWEN_QUANTIZATION = QWEN_Q4_QUANTIZATION
EXACT_QWEN_CONTEXT_LENGTH = QWEN_Q4_CONTEXT_LENGTH
_EXACT_DARWIN_PRELIMIT_SHELL_PREFIX = ("/bin/sh", "-p", "-c")
_EXACT_DARWIN_PRELIMIT_LAUNCHER_SHA256 = hashlib.sha256(
    _DARWIN_PRELIMIT_LAUNCHER.encode("utf-8")
).hexdigest()
_PROTOCOL_LIMITS = StrictJsonLimits(
    max_bytes=2 * 1024 * 1024,
    max_line_chars=256 * 1024,
    max_depth=16,
)
_DOCUMENT_MAX_BYTES = 2 * 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_REVISION_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}\Z")
_SYSTEM_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
_BLOCKER_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_REPOSITORY_RE = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?\Z"
)
_CANDIDATE_STATUSES = frozenset({"screening", "include", "exclude"})
_DATASET_STATUSES = frozenset({"pending", "frozen"})
_CLAIM_NETWORK_ISOLATION_MODES = frozenset(
    {"container-no-network", "network-namespace", "host-firewall"}
)
_RESERVED_SYSTEMS = frozenset({"compiler", "head", "tail", "extractive"})
_REQUIRED_DATASET_KINDS = (
    "synthetic",
    "natural-history",
    "coding-task",
    "second-task",
)
_TOP_LEVEL_FIELDS = {
    "schema",
    "protocol_id",
    "status",
    "created_at",
    "frozen_at",
    "freeze_repository_commit",
    "protocol_document",
    "comparison_candidates",
    "registered_systems",
    "constraints",
    "datasets",
    "statistics",
    "runner",
    "blockers",
    "protocol_sha256",
}
_DOCUMENT_FIELDS = {"path", "file_sha256"}
_CANDIDATE_FIELDS = {
    "system",
    "display_name",
    "repository",
    "status",
    "revision",
    "revision_observed_at",
    "license_spdx",
    "license_file_sha256",
    "dependency_lock_sha256",
    "adapter_revision",
    "adapter_entrypoint_sha256",
    "adapter_source_tree_sha256",
    "adapter_runtime_executable_sha256",
    "adapter_environment_sha256",
    "adapter_command_sha256",
    "decision_reason",
}
_CONSTRAINT_FIELDS = {
    "model_id",
    "quantization",
    "model_context_length",
    "inference_concurrency",
    "adapter_process_concurrency",
    "network_model_api",
    "execution_network_access",
    "paid_service",
    "model_service_cost_usd",
    "retry_count",
    "tokenizer_id",
    "active_token_budget",
}
_DATASET_FIELDS = {
    "id",
    "kind",
    "status",
    "revision",
    "manifest_sha256",
    "sample_size",
    "seeds",
    "annotation",
}
_STATISTIC_FIELDS = {
    "lrcbench_seed",
    "minimum_synthetic_histories",
    "default_synthetic_histories",
    "paired_bootstrap_samples",
    "bootstrap_lower_quantile",
    "component_gain_threshold",
    "minimum_compression_ratio",
    "minimum_registered_systems",
}
_RUNNER_FIELDS = {
    "isolation_mode",
    "timeout_seconds",
    "poll_interval_seconds",
    "network_isolation_mode",
    "network_isolation_evidence_sha256",
    "max_stdout_bytes",
    "max_stderr_bytes",
    "max_candidate_bytes",
    "max_memory_mb",
    "inference_service_memory_metric",
    "inference_service_executable_sha256",
    "max_inference_service_memory_mb",
    "adapter_shell_interpretation",
    "darwin_prelimit_shell_prefix",
    "darwin_prelimit_launcher_protocol",
    "darwin_prelimit_launcher_sha256",
    "overwrite_existing_outputs",
    "failure_policy",
    "offline_execution",
}
_BLOCKER_FIELDS = {"id", "description"}


class ExternalProtocolError(ValueError):
    """An external-comparison protocol is unsafe, incomplete, or inconsistent."""


@dataclass(frozen=True, slots=True)
class ExternalExecutionContract:
    """Model and runner controls that every retained external run must match."""

    model_id: str
    model_context_length: int
    tokenizer_id: str
    inference_concurrency: int
    retry_count: int
    model_service_cost_usd: float
    active_token_budget: int
    isolation_mode: str
    timeout_seconds: float
    poll_interval_seconds: float
    network_isolation_mode: str | None
    network_isolation_evidence_sha256: str | None
    inference_service_memory_metric: str | None
    inference_service_executable_sha256: str | None
    max_inference_service_memory_mb: int | None
    max_stdout_bytes: int
    max_stderr_bytes: int
    max_candidate_bytes: int
    max_memory_mb: int | None
    adapter_shell_interpretation: bool
    darwin_prelimit_shell_prefix: tuple[str, ...]
    darwin_prelimit_launcher_protocol: str
    darwin_prelimit_launcher_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "model_context_length": self.model_context_length,
            "tokenizer_id": self.tokenizer_id,
            "inference_concurrency": self.inference_concurrency,
            "retry_count": self.retry_count,
            "model_service_cost_usd": self.model_service_cost_usd,
            "active_token_budget": self.active_token_budget,
            "isolation_mode": self.isolation_mode,
            "timeout_seconds": self.timeout_seconds,
            "poll_interval_seconds": self.poll_interval_seconds,
            "network_isolation_mode": self.network_isolation_mode,
            "network_isolation_evidence_sha256": (
                self.network_isolation_evidence_sha256
            ),
            "inference_service_memory_metric": (
                self.inference_service_memory_metric
            ),
            "inference_service_executable_sha256": (
                self.inference_service_executable_sha256
            ),
            "max_inference_service_memory_mb": (
                self.max_inference_service_memory_mb
            ),
            "max_stdout_bytes": self.max_stdout_bytes,
            "max_stderr_bytes": self.max_stderr_bytes,
            "max_candidate_bytes": self.max_candidate_bytes,
            "max_memory_mb": self.max_memory_mb,
            "adapter_shell_interpretation": self.adapter_shell_interpretation,
            "darwin_prelimit_shell_prefix": list(self.darwin_prelimit_shell_prefix),
            "darwin_prelimit_launcher_protocol": (
                self.darwin_prelimit_launcher_protocol
            ),
            "darwin_prelimit_launcher_sha256": self.darwin_prelimit_launcher_sha256,
        }


@dataclass(frozen=True, slots=True)
class ExternalDatasetBinding:
    """Immutable identity fields for one protocol dataset slot."""

    id: str
    kind: str
    status: str
    revision: str | None
    manifest_sha256: str | None
    sample_size: int | None
    seeds: tuple[int, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "revision": self.revision,
            "manifest_sha256": self.manifest_sha256,
            "sample_size": self.sample_size,
            "seeds": list(self.seeds),
        }


@dataclass(frozen=True, slots=True)
class VerifiedExternalProtocol:
    """Security-relevant summary of one internally consistent protocol."""

    protocol_id: str
    status: str
    protocol_sha256: str
    file_sha256: str
    document_sha256: str
    synthetic_dataset_sha256: str | None
    datasets: tuple[ExternalDatasetBinding, ...]
    registered_systems: tuple[str, ...]
    adapter_revisions: tuple[tuple[str, str], ...]
    environment_ids: tuple[tuple[str, str], ...]
    adapter_entrypoint_sha256s: tuple[tuple[str, str], ...]
    adapter_source_tree_sha256s: tuple[tuple[str, str], ...]
    adapter_runtime_executable_sha256s: tuple[tuple[str, str], ...]
    adapter_environment_sha256s: tuple[tuple[str, str], ...]
    adapter_command_sha256s: tuple[tuple[str, str], ...]
    execution_contract: ExternalExecutionContract
    candidate_count: int
    blocker_ids: tuple[str, ...]
    claim_ready: bool

    def dataset_by_kind(self, kind: str) -> ExternalDatasetBinding:
        """Return the one strictly validated dataset binding for ``kind``."""

        matches = tuple(dataset for dataset in self.datasets if dataset.kind == kind)
        if len(matches) != 1:
            raise KeyError(kind)
        return matches[0]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": EXTERNAL_PROTOCOL_SCHEMA,
            "verified": True,
            "protocol_id": self.protocol_id,
            "status": self.status,
            "protocol_sha256": self.protocol_sha256,
            "file_sha256": self.file_sha256,
            "document_sha256": self.document_sha256,
            "synthetic_dataset_sha256": self.synthetic_dataset_sha256,
            "datasets": [dataset.to_dict() for dataset in self.datasets],
            "registered_systems": list(self.registered_systems),
            "adapter_revisions": {
                system: revision
                for system, revision in self.adapter_revisions
            },
            "environment_ids": {
                system: environment_id
                for system, environment_id in self.environment_ids
            },
            "adapter_entrypoint_sha256s": {
                system: digest
                for system, digest in self.adapter_entrypoint_sha256s
            },
            "adapter_source_tree_sha256s": {
                system: digest
                for system, digest in self.adapter_source_tree_sha256s
            },
            "adapter_runtime_executable_sha256s": {
                system: digest
                for system, digest in self.adapter_runtime_executable_sha256s
            },
            "adapter_environment_sha256s": {
                system: digest
                for system, digest in self.adapter_environment_sha256s
            },
            "adapter_command_sha256s": {
                system: digest
                for system, digest in self.adapter_command_sha256s
            },
            "execution_contract": self.execution_contract.to_dict(),
            "candidate_count": self.candidate_count,
            "blocker_ids": list(self.blocker_ids),
            "claim_ready": self.claim_ready,
        }


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _object(value: object, *, context: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise ExternalProtocolError(f"{context} must be an object")
    if set(value) != fields:
        raise ExternalProtocolError(f"{context} fields do not match the schema")
    return value


def _string(
    value: object,
    *,
    context: str,
    maximum: int = 4_096,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise ExternalProtocolError(f"{context} must be a bounded string")
    return value


def _optional_string(
    value: object,
    *,
    context: str,
    maximum: int = 4_096,
) -> str | None:
    if value is None:
        return None
    return _string(value, context=context, maximum=maximum)


def _integer(
    value: object,
    *,
    context: str,
    minimum: int = 0,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
    ):
        raise ExternalProtocolError(
            f"{context} must be an integer at least {minimum}"
        )
    return value


def _number(
    value: object,
    *,
    context: str,
    minimum: float = 0.0,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < minimum
    ):
        raise ExternalProtocolError(
            f"{context} must be finite and at least {minimum}"
        )
    return float(value)


def _timestamp(value: object, *, context: str) -> datetime:
    text = _string(value, context=context, maximum=64)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ExternalProtocolError(
            f"{context} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.utcoffset() is None:
        raise ExternalProtocolError(f"{context} must include a timezone")
    return parsed


def _sha256(value: object, *, context: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ExternalProtocolError(f"{context} must be lowercase SHA-256")
    return value


def _revision(value: object, *, context: str) -> str:
    if not isinstance(value, str) or _REVISION_RE.fullmatch(value) is None:
        raise ExternalProtocolError(
            f"{context} must be a lowercase immutable Git object id"
        )
    return value


def _validate_document(
    value: object,
    *,
    manifest_path: Path,
) -> str:
    document = _object(
        value,
        context="protocol_document",
        fields=_DOCUMENT_FIELDS,
    )
    relative_text = _string(
        document["path"],
        context="protocol_document.path",
        maximum=256,
    )
    relative = PurePosixPath(relative_text)
    if (
        relative.is_absolute()
        or "\\" in relative_text
        or ".." in relative.parts
        or relative_text != relative.as_posix()
    ):
        raise ExternalProtocolError(
            "protocol_document.path must be a normalized relative POSIX path"
        )
    expected_sha256 = _sha256(
        document["file_sha256"],
        context="protocol_document.file_sha256",
    )
    base = manifest_path.parent.resolve()
    resolved = (base / Path(*relative.parts)).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ExternalProtocolError(
            "protocol_document.path escapes the manifest directory"
        ) from exc
    try:
        evidence = hash_bounded_regular_file(
            resolved,
            max_bytes=_DOCUMENT_MAX_BYTES,
            label="external protocol document",
        )
    except StrictJsonError as exc:
        raise ExternalProtocolError(str(exc)) from exc
    if evidence.file_sha256 != expected_sha256:
        raise ExternalProtocolError(
            "protocol_document.file_sha256 does not match the retained document"
        )
    return expected_sha256


def _validate_candidates(
    value: object,
    *,
    frozen: bool,
) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 64
    ):
        raise ExternalProtocolError(
            "comparison_candidates must contain 1 to 64 entries"
        )
    candidates: dict[str, dict[str, Any]] = {}
    included: list[str] = []
    for index, raw in enumerate(value):
        context = f"comparison_candidates[{index}]"
        candidate = _object(
            raw,
            context=context,
            fields=_CANDIDATE_FIELDS,
        )
        system = _string(
            candidate["system"],
            context=f"{context}.system",
            maximum=64,
        )
        if _SYSTEM_RE.fullmatch(system) is None:
            raise ExternalProtocolError(f"{context}.system is invalid")
        if system in _RESERVED_SYSTEMS:
            raise ExternalProtocolError(
                f"{context}.system collides with a bundled system"
            )
        if system in candidates:
            raise ExternalProtocolError(
                f"comparison_candidates repeats system {system!r}"
            )
        _string(
            candidate["display_name"],
            context=f"{context}.display_name",
            maximum=128,
        )
        repository = _optional_string(
            candidate["repository"],
            context=f"{context}.repository",
            maximum=512,
        )
        if (
            repository is not None
            and _REPOSITORY_RE.fullmatch(repository) is None
        ):
            raise ExternalProtocolError(
                f"{context}.repository must be a GitHub HTTPS repository"
            )
        status = candidate["status"]
        if status not in _CANDIDATE_STATUSES:
            raise ExternalProtocolError(f"{context}.status is invalid")
        revision = candidate["revision"]
        if revision is not None:
            _revision(revision, context=f"{context}.revision")
        observed_at = candidate["revision_observed_at"]
        if observed_at is not None:
            _timestamp(observed_at, context=f"{context}.revision_observed_at")
        license_spdx = _optional_string(
            candidate["license_spdx"],
            context=f"{context}.license_spdx",
            maximum=64,
        )
        license_sha256 = candidate["license_file_sha256"]
        if license_sha256 is not None:
            _sha256(
                license_sha256,
                context=f"{context}.license_file_sha256",
            )
        dependency_sha256 = candidate["dependency_lock_sha256"]
        if dependency_sha256 is not None:
            _sha256(
                dependency_sha256,
                context=f"{context}.dependency_lock_sha256",
            )
        adapter_revision = candidate["adapter_revision"]
        if adapter_revision is not None:
            _revision(
                adapter_revision,
                context=f"{context}.adapter_revision",
            )
        adapter_entrypoint_sha256 = candidate[
            "adapter_entrypoint_sha256"
        ]
        if adapter_entrypoint_sha256 is not None:
            _sha256(
                adapter_entrypoint_sha256,
                context=f"{context}.adapter_entrypoint_sha256",
            )
        adapter_source_tree_sha256 = candidate[
            "adapter_source_tree_sha256"
        ]
        if adapter_source_tree_sha256 is not None:
            _sha256(
                adapter_source_tree_sha256,
                context=f"{context}.adapter_source_tree_sha256",
            )
        adapter_runtime_executable_sha256 = candidate[
            "adapter_runtime_executable_sha256"
        ]
        if adapter_runtime_executable_sha256 is not None:
            _sha256(
                adapter_runtime_executable_sha256,
                context=(
                    f"{context}.adapter_runtime_executable_sha256"
                ),
            )
        adapter_environment_sha256 = candidate[
            "adapter_environment_sha256"
        ]
        if adapter_environment_sha256 is not None:
            _sha256(
                adapter_environment_sha256,
                context=f"{context}.adapter_environment_sha256",
            )
        adapter_command_sha256 = candidate["adapter_command_sha256"]
        if adapter_command_sha256 is not None:
            _sha256(
                adapter_command_sha256,
                context=f"{context}.adapter_command_sha256",
            )
        _string(
            candidate["decision_reason"],
            context=f"{context}.decision_reason",
            maximum=2_048,
        )
        if status == "include":
            included.append(system)
            if (
                repository is None
                or revision is None
                or observed_at is None
                or license_spdx is None
                or license_sha256 is None
                or dependency_sha256 is None
                or adapter_revision is None
                or adapter_entrypoint_sha256 is None
                or adapter_source_tree_sha256 is None
                or adapter_runtime_executable_sha256 is None
                or adapter_environment_sha256 is None
                or adapter_command_sha256 is None
            ):
                raise ExternalProtocolError(
                    f"{context} included system lacks frozen identity evidence"
                )
        elif frozen and status == "screening":
            raise ExternalProtocolError(
                "frozen protocols cannot retain screening candidates"
            )
        candidates[system] = candidate
    if list(candidates) != sorted(candidates):
        raise ExternalProtocolError(
            "comparison_candidates must be sorted by system"
        )
    return candidates, tuple(sorted(included))


def _validate_registered(
    value: object,
    *,
    included: tuple[str, ...],
) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and _SYSTEM_RE.fullmatch(item)
        for item in value
    ):
        raise ExternalProtocolError(
            "registered_systems must contain valid system identifiers"
        )
    registered = tuple(value)
    if registered != tuple(sorted(set(registered))):
        raise ExternalProtocolError(
            "registered_systems must be unique and sorted"
        )
    if registered != included:
        raise ExternalProtocolError(
            "registered_systems must equal included comparison candidates"
        )
    return registered


def _validate_constraints(value: object) -> None:
    constraints = _object(
        value,
        context="constraints",
        fields=_CONSTRAINT_FIELDS,
    )
    required = {
        "model_id": EXACT_QWEN_MODEL_ID,
        "quantization": EXACT_QWEN_QUANTIZATION,
        "model_context_length": EXACT_QWEN_CONTEXT_LENGTH,
        "inference_concurrency": 1,
        "adapter_process_concurrency": 1,
        "network_model_api": False,
        "execution_network_access": False,
        "paid_service": False,
        "model_service_cost_usd": 0,
        "retry_count": 0,
        "tokenizer_id": TOKENIZER_ID,
    }
    for name, expected in required.items():
        if constraints[name] != expected or type(constraints[name]) is not type(
            expected
        ):
            raise ExternalProtocolError(
                f"constraints.{name} must equal {expected!r}"
            )
    if (
        isinstance(constraints["active_token_budget"], bool)
        or constraints["active_token_budget"] != 900
    ):
        raise ExternalProtocolError(
            "constraints.active_token_budget must equal 900"
        )


def _validate_datasets(
    value: object,
    *,
    frozen: bool,
) -> tuple[tuple[ExternalDatasetBinding, ...], str | None]:
    if not isinstance(value, list) or not 4 <= len(value) <= 8:
        raise ExternalProtocolError("datasets must contain 4 to 8 entries")
    seen_ids: set[str] = set()
    seen_kinds: set[str] = set()
    synthetic_dataset_sha256: str | None = None
    bindings: list[ExternalDatasetBinding] = []
    for index, raw in enumerate(value):
        context = f"datasets[{index}]"
        dataset = _object(raw, context=context, fields=_DATASET_FIELDS)
        dataset_id = _string(
            dataset["id"],
            context=f"{context}.id",
            maximum=128,
        )
        if _SYSTEM_RE.fullmatch(dataset_id) is None:
            raise ExternalProtocolError(f"{context}.id is invalid")
        kind = dataset["kind"]
        if kind not in _REQUIRED_DATASET_KINDS:
            raise ExternalProtocolError(f"{context}.kind is invalid")
        if dataset_id in seen_ids or kind in seen_kinds:
            raise ExternalProtocolError(
                "datasets must have unique ids and required kinds"
            )
        seen_ids.add(dataset_id)
        seen_kinds.add(kind)
        status = dataset["status"]
        if status not in _DATASET_STATUSES:
            raise ExternalProtocolError(f"{context}.status is invalid")
        revision = _optional_string(
            dataset["revision"],
            context=f"{context}.revision",
            maximum=256,
        )
        manifest_sha256 = dataset["manifest_sha256"]
        if manifest_sha256 is not None:
            validated_manifest_sha256 = _sha256(
                manifest_sha256,
                context=f"{context}.manifest_sha256",
            )
            if kind == "synthetic":
                synthetic_dataset_sha256 = validated_manifest_sha256
        sample_size = dataset["sample_size"]
        if sample_size is not None:
            _integer(
                sample_size,
                context=f"{context}.sample_size",
                minimum=1,
            )
        seeds = dataset["seeds"]
        if (
            not isinstance(seeds, list)
            or len(seeds) > 10_000
            or not all(
                isinstance(seed, int) and not isinstance(seed, bool) and seed >= 0
                for seed in seeds
            )
            or seeds != sorted(set(seeds))
        ):
            raise ExternalProtocolError(
                f"{context}.seeds must be unique sorted non-negative integers"
            )
        if kind == "synthetic" and seeds != [56_056]:
            raise ExternalProtocolError(
                f"{context}.seeds must equal [56056] for the synthetic cohort"
            )
        _string(
            dataset["annotation"],
            context=f"{context}.annotation",
            maximum=1_024,
        )
        if status == "frozen" and (
            revision is None
            or manifest_sha256 is None
            or sample_size is None
        ):
            raise ExternalProtocolError(
                f"{context} frozen dataset lacks immutable evidence"
            )
        if (
            status == "frozen"
            and kind == "synthetic"
            and sample_size != 32
        ):
            raise ExternalProtocolError(
                f"{context}.sample_size must equal 32 for the synthetic cohort"
            )
        if (
            status == "frozen"
            and kind in {"coding-task", "second-task"}
            and not seeds
        ):
            raise ExternalProtocolError(
                f"{context} frozen task suite must record task seeds"
            )
        if frozen and status != "frozen":
            raise ExternalProtocolError(
                "frozen protocols require every dataset to be frozen"
            )
        bindings.append(
            ExternalDatasetBinding(
                id=dataset_id,
                kind=str(kind),
                status=str(status),
                revision=revision,
                manifest_sha256=(
                    None
                    if manifest_sha256 is None
                    else str(manifest_sha256)
                ),
                sample_size=(
                    None if sample_size is None else int(sample_size)
                ),
                seeds=tuple(int(seed) for seed in seeds),
            )
        )
    if seen_kinds != set(_REQUIRED_DATASET_KINDS):
        raise ExternalProtocolError(
            "datasets must contain each required dataset kind exactly once"
        )
    return tuple(bindings), synthetic_dataset_sha256


def _validate_statistics(value: object) -> int:
    statistics = _object(
        value,
        context="statistics",
        fields=_STATISTIC_FIELDS,
    )
    required_integers = {
        "lrcbench_seed": 56_056,
        "minimum_synthetic_histories": 24,
        "default_synthetic_histories": 32,
        "paired_bootstrap_samples": 2_000,
        "minimum_registered_systems": 4,
    }
    for name, expected in required_integers.items():
        if (
            isinstance(statistics[name], bool)
            or statistics[name] != expected
        ):
            raise ExternalProtocolError(
                f"statistics.{name} must equal {expected}"
            )
    required_numbers = {
        "bootstrap_lower_quantile": 0.025,
        "component_gain_threshold": 0.5,
        "minimum_compression_ratio": 5.0,
    }
    for name, expected in required_numbers.items():
        observed = _number(
            statistics[name],
            context=f"statistics.{name}",
        )
        if observed != expected:
            raise ExternalProtocolError(
                f"statistics.{name} must equal {expected}"
            )
    return int(statistics["minimum_registered_systems"])


def _validate_runner(value: object, *, frozen: bool) -> None:
    runner = _object(value, context="runner", fields=_RUNNER_FIELDS)
    required = {
        "isolation_mode": "per_case",
        "timeout_seconds": 300,
        "poll_interval_seconds": 0.02,
        "max_stdout_bytes": 1_000_000,
        "max_stderr_bytes": 1_000_000,
        "max_candidate_bytes": 20_000_000,
        "adapter_shell_interpretation": False,
        "darwin_prelimit_shell_prefix": list(_EXACT_DARWIN_PRELIMIT_SHELL_PREFIX),
        "darwin_prelimit_launcher_protocol": _DARWIN_PRELIMIT_LAUNCHER_PROTOCOL,
        "darwin_prelimit_launcher_sha256": _EXACT_DARWIN_PRELIMIT_LAUNCHER_SHA256,
        "overwrite_existing_outputs": False,
        "failure_policy": "registered-failures-are-non-wins",
        "offline_execution": True,
    }
    for name, expected in required.items():
        if runner[name] != expected or type(runner[name]) is not type(expected):
            raise ExternalProtocolError(
                f"runner.{name} must equal {expected!r}"
            )
    max_memory_mb = runner["max_memory_mb"]
    if max_memory_mb is not None:
        _integer(
            max_memory_mb,
            context="runner.max_memory_mb",
            minimum=1,
        )
    network_mode = runner["network_isolation_mode"]
    network_evidence_sha256 = runner["network_isolation_evidence_sha256"]
    if network_mode is None and network_evidence_sha256 is None:
        pass
    elif (
        not isinstance(network_mode, str)
        or network_mode not in _CLAIM_NETWORK_ISOLATION_MODES
        or network_evidence_sha256 is None
    ):
        raise ExternalProtocolError(
            "runner network isolation requires a supported mode and evidence SHA-256"
        )
    else:
        _sha256(
            network_evidence_sha256,
            context="runner.network_isolation_evidence_sha256",
        )
    service_metric = runner["inference_service_memory_metric"]
    service_executable_sha256 = runner[
        "inference_service_executable_sha256"
    ]
    max_service_memory_mb = runner["max_inference_service_memory_mb"]
    service_fields = (
        service_metric,
        service_executable_sha256,
        max_service_memory_mb,
    )
    if all(field is None for field in service_fields):
        pass
    elif any(field is None for field in service_fields):
        raise ExternalProtocolError(
            "runner inference-service accounting requires a memory metric, "
            "executable SHA-256, and memory ceiling"
        )
    else:
        if service_metric not in {
            "resident-set-bytes",
            "working-set-bytes",
        }:
            raise ExternalProtocolError(
                "runner.inference_service_memory_metric is unsupported"
            )
        _sha256(
            service_executable_sha256,
            context="runner.inference_service_executable_sha256",
        )
        _integer(
            max_service_memory_mb,
            context="runner.max_inference_service_memory_mb",
            minimum=1,
        )
    if frozen and (
        max_memory_mb is None
        or any(field is None for field in service_fields)
        or network_mode is None
        or network_evidence_sha256 is None
    ):
        raise ExternalProtocolError(
            "frozen runner requires adapter memory, measured inference-service "
            "accounting, and retained network-isolation evidence"
        )


def _validate_blockers(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 64:
        raise ExternalProtocolError("blockers must be an array of at most 64 entries")
    ids: list[str] = []
    for index, raw in enumerate(value):
        context = f"blockers[{index}]"
        blocker = _object(raw, context=context, fields=_BLOCKER_FIELDS)
        blocker_id = _string(
            blocker["id"],
            context=f"{context}.id",
            maximum=64,
        )
        if _BLOCKER_RE.fullmatch(blocker_id) is None:
            raise ExternalProtocolError(f"{context}.id is invalid")
        _string(
            blocker["description"],
            context=f"{context}.description",
            maximum=1_024,
        )
        ids.append(blocker_id)
    if ids != sorted(set(ids)):
        raise ExternalProtocolError("blockers must be unique and sorted by id")
    return tuple(ids)


def load_external_protocol(
    path: str | Path = DEFAULT_EXTERNAL_PROTOCOL,
    *,
    require_frozen: bool = False,
) -> VerifiedExternalProtocol:
    """Strictly verify a draft or frozen external-comparison protocol."""

    manifest_path = Path(path).expanduser().resolve()
    try:
        document = load_strict_json_file(
            manifest_path,
            limits=_PROTOCOL_LIMITS,
            label="external comparison protocol",
        )
    except StrictJsonError as exc:
        raise ExternalProtocolError(str(exc)) from exc
    payload = _object(
        document.value,
        context="external comparison protocol",
        fields=_TOP_LEVEL_FIELDS,
    )
    if payload["schema"] != EXTERNAL_PROTOCOL_SCHEMA:
        raise ExternalProtocolError(
            f"schema must be {EXTERNAL_PROTOCOL_SCHEMA!r}"
        )
    protocol_id = _string(
        payload["protocol_id"],
        context="protocol_id",
        maximum=128,
    )
    if _SYSTEM_RE.fullmatch(protocol_id) is None:
        raise ExternalProtocolError("protocol_id is invalid")
    status = payload["status"]
    if status not in {"draft", "frozen"}:
        raise ExternalProtocolError("status must be 'draft' or 'frozen'")
    frozen = status == "frozen"
    created_at = _timestamp(payload["created_at"], context="created_at")
    frozen_at = payload["frozen_at"]
    freeze_commit = payload["freeze_repository_commit"]
    if frozen:
        parsed_frozen_at = _timestamp(frozen_at, context="frozen_at")
        if parsed_frozen_at < created_at:
            raise ExternalProtocolError("frozen_at cannot precede created_at")
        _revision(
            freeze_commit,
            context="freeze_repository_commit",
        )
    elif frozen_at is not None or freeze_commit is not None:
        raise ExternalProtocolError(
            "draft protocols cannot claim frozen identity fields"
        )
    document_sha256 = _validate_document(
        payload["protocol_document"],
        manifest_path=manifest_path,
    )
    candidates, included = _validate_candidates(
        payload["comparison_candidates"],
        frozen=frozen,
    )
    registered = _validate_registered(
        payload["registered_systems"],
        included=included,
    )
    _validate_constraints(payload["constraints"])
    datasets, synthetic_dataset_sha256 = _validate_datasets(
        payload["datasets"],
        frozen=frozen,
    )
    minimum_registered = _validate_statistics(payload["statistics"])
    _validate_runner(payload["runner"], frozen=frozen)
    blockers = _validate_blockers(payload["blockers"])
    if frozen:
        if blockers:
            raise ExternalProtocolError(
                "frozen protocols cannot contain unresolved blockers"
            )
        if len(registered) < minimum_registered:
            raise ExternalProtocolError(
                "frozen protocol has too few registered external systems"
            )
    elif not blockers:
        raise ExternalProtocolError(
            "draft protocols must enumerate unresolved blockers"
        )
    claimed_sha256 = _sha256(
        payload["protocol_sha256"],
        context="protocol_sha256",
    )
    unsigned = dict(payload)
    unsigned.pop("protocol_sha256")
    try:
        actual_sha256 = _canonical_sha256(unsigned)
    except (TypeError, ValueError) as exc:
        raise ExternalProtocolError(
            "external protocol is not canonical finite JSON"
        ) from exc
    if actual_sha256 != claimed_sha256:
        raise ExternalProtocolError("protocol_sha256 mismatch")
    if require_frozen and not frozen:
        raise ExternalProtocolError(
            "external comparison protocol is not claim-ready and frozen"
        )
    return VerifiedExternalProtocol(
        protocol_id=protocol_id,
        status=status,
        protocol_sha256=claimed_sha256,
        file_sha256=document.file_sha256,
        document_sha256=document_sha256,
        synthetic_dataset_sha256=synthetic_dataset_sha256,
        datasets=datasets,
        registered_systems=registered,
        adapter_revisions=tuple(
            (
                system,
                str(candidates[system]["adapter_revision"]),
            )
            for system in registered
        ),
        environment_ids=tuple(
            (
                system,
                f"sha256:{candidates[system]['dependency_lock_sha256']}",
            )
            for system in registered
        ),
        adapter_entrypoint_sha256s=tuple(
            (
                system,
                str(candidates[system]["adapter_entrypoint_sha256"]),
            )
            for system in registered
        ),
        adapter_source_tree_sha256s=tuple(
            (
                system,
                str(candidates[system]["adapter_source_tree_sha256"]),
            )
            for system in registered
        ),
        adapter_runtime_executable_sha256s=tuple(
            (
                system,
                str(
                    candidates[system][
                        "adapter_runtime_executable_sha256"
                    ]
                ),
            )
            for system in registered
        ),
        adapter_environment_sha256s=tuple(
            (
                system,
                str(candidates[system]["adapter_environment_sha256"]),
            )
            for system in registered
        ),
        adapter_command_sha256s=tuple(
            (
                system,
                str(candidates[system]["adapter_command_sha256"]),
            )
            for system in registered
        ),
        execution_contract=ExternalExecutionContract(
            model_id=str(payload["constraints"]["model_id"]),
            model_context_length=int(
                payload["constraints"]["model_context_length"]
            ),
            tokenizer_id=str(payload["constraints"]["tokenizer_id"]),
            inference_concurrency=int(
                payload["constraints"]["inference_concurrency"]
            ),
            retry_count=int(payload["constraints"]["retry_count"]),
            model_service_cost_usd=float(
                payload["constraints"]["model_service_cost_usd"]
            ),
            active_token_budget=int(
                payload["constraints"]["active_token_budget"]
            ),
            isolation_mode=str(payload["runner"]["isolation_mode"]),
            timeout_seconds=float(payload["runner"]["timeout_seconds"]),
            poll_interval_seconds=float(
                payload["runner"]["poll_interval_seconds"]
            ),
            network_isolation_mode=(
                None
                if payload["runner"]["network_isolation_mode"] is None
                else str(payload["runner"]["network_isolation_mode"])
            ),
            network_isolation_evidence_sha256=(
                None
                if payload["runner"]["network_isolation_evidence_sha256"]
                is None
                else str(
                    payload["runner"]["network_isolation_evidence_sha256"]
                )
            ),
            inference_service_memory_metric=(
                None
                if payload["runner"]["inference_service_memory_metric"]
                is None
                else str(
                    payload["runner"]["inference_service_memory_metric"]
                )
            ),
            inference_service_executable_sha256=(
                None
                if payload["runner"][
                    "inference_service_executable_sha256"
                ]
                is None
                else str(
                    payload["runner"][
                        "inference_service_executable_sha256"
                    ]
                )
            ),
            max_inference_service_memory_mb=(
                None
                if payload["runner"]["max_inference_service_memory_mb"]
                is None
                else int(
                    payload["runner"]["max_inference_service_memory_mb"]
                )
            ),
            max_stdout_bytes=int(payload["runner"]["max_stdout_bytes"]),
            max_stderr_bytes=int(payload["runner"]["max_stderr_bytes"]),
            max_candidate_bytes=int(
                payload["runner"]["max_candidate_bytes"]
            ),
            max_memory_mb=(
                None
                if payload["runner"]["max_memory_mb"] is None
                else int(payload["runner"]["max_memory_mb"])
            ),
            adapter_shell_interpretation=bool(
                payload["runner"]["adapter_shell_interpretation"]
            ),
            darwin_prelimit_shell_prefix=tuple(
                str(part)
                for part in payload["runner"]["darwin_prelimit_shell_prefix"]
            ),
            darwin_prelimit_launcher_protocol=str(
                payload["runner"]["darwin_prelimit_launcher_protocol"]
            ),
            darwin_prelimit_launcher_sha256=str(
                payload["runner"]["darwin_prelimit_launcher_sha256"]
            ),
        ),
        candidate_count=len(payload["comparison_candidates"]),
        blocker_ids=blockers,
        claim_ready=frozen,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a self-hashed external-comparison protocol",
    )
    parser.add_argument(
        "--verify",
        type=Path,
        default=DEFAULT_EXTERNAL_PROTOCOL,
        help="protocol JSON to verify",
    )
    parser.add_argument(
        "--require-frozen",
        action="store_true",
        help="reject an internally valid draft",
    )
    args = parser.parse_args(argv)
    try:
        verified = load_external_protocol(
            args.verify,
            require_frozen=args.require_frozen,
        )
    except (ExternalProtocolError, OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    sys.stdout.write(json.dumps(verified.to_dict(), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
