"""Offline, source-bound SWE-bench ingestion and gold-free projection.

This module deliberately stops before repository preparation, agent execution,
or grading.  The official source artifact must be downloaded separately.  No
network client, Hugging Face credential handling, or benchmark gold is exposed
through the candidate task document.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any

from context_compiler.atomic import atomic_write_text

from .json_io import (
    StrictJsonError,
    StrictJsonLimits,
    load_strict_json_file,
    read_bounded_regular_file,
)

SUITE_SCHEMA = "ctxc-swebench-suite-0.1"
TASK_INPUT_SCHEMA = "ctxc-swebench-task-input-0.1"
VERIFICATION_SCHEMA = "ctxc-swebench-verification-0.1"

DEFAULT_SUITE = Path(__file__).with_name("suites") / "swebench_verified_v1.json"

SOURCE_FIELDS = (
    "repo",
    "instance_id",
    "base_commit",
    "patch",
    "test_patch",
    "problem_statement",
    "hints_text",
    "created_at",
    "version",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "environment_setup_commit",
    "difficulty",
)
CANDIDATE_SOURCE_FIELDS = ("problem_statement",)
CANDIDATE_GENERATED_FIELDS = ("opaque_task_id",)
COORDINATOR_ONLY_FIELDS = (
    "repo",
    "instance_id",
    "base_commit",
    "hints_text",
    "created_at",
    "version",
    "environment_setup_commit",
    "difficulty",
)
EVALUATOR_ONLY_FIELDS = (
    "patch",
    "test_patch",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
)

_SUITE_LIMITS = StrictJsonLimits(
    max_bytes=2 * 1024 * 1024,
    max_line_chars=512 * 1024,
    max_depth=16,
)
_SNAPSHOT_LIMITS = StrictJsonLimits(
    max_bytes=16 * 1024 * 1024,
    max_line_chars=16 * 1024 * 1024,
    max_depth=8,
)
_TASK_LIMITS = StrictJsonLimits(
    max_bytes=32 * 1024 * 1024,
    max_line_chars=32 * 1024 * 1024,
    max_depth=8,
)
_MAX_SOURCE_ROWS = 10_000
_MAX_SOURCE_STRING_CHARS = 1_000_000
_OPAQUE_KEY_BYTES = 32
_OPAQUE_DOMAIN = b"ctxc-swebench-opaque-task-v1\0"
_KEY_FINGERPRINT_DOMAIN = b"ctxc-swebench-key-fingerprint-v1\0"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
_IDENTIFIER_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_REPOSITORY_RE = re.compile(
    r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z"
)
_INSTANCE_RE = re.compile(
    r"[A-Za-z0-9_.-]{1,100}__[A-Za-z0-9_.-]{1,100}-[0-9]{1,12}\Z"
)
_UTC_TIMESTAMP_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-5][0-9]Z\Z"
)
_OPAQUE_ID_RE = re.compile(r"task-[0-9a-f]{64}\Z")


class SweBenchError(ValueError):
    """SWE-bench source or projection evidence is malformed or inconsistent."""


@dataclass(frozen=True, slots=True)
class SweBenchSuite:
    """One strictly decoded, self-hashed suite descriptor."""

    document: Mapping[str, Any]
    file_sha256: str | None

    @property
    def suite_id(self) -> str:
        return str(self.document["suite_id"])

    @property
    def suite_sha256(self) -> str:
        return str(self.document["suite_sha256"])

    @property
    def row_count(self) -> int:
        return int(self.document["canonical_snapshot"]["row_count"])

    @property
    def snapshot_sha256(self) -> str:
        return str(self.document["canonical_snapshot"]["sha256"])

    @property
    def source_artifact_sha256(self) -> str:
        return str(self.document["dataset"]["artifact"]["sha256"])

    def to_dict(self) -> dict[str, Any]:
        return _thaw_json(self.document)


@dataclass(frozen=True, slots=True)
class VerifiedSweBenchSource:
    """Exact decoded rows from the suite's canonical local snapshot."""

    suite: SweBenchSuite
    records: tuple[Mapping[str, str], ...]
    file_sha256: str
    byte_count: int


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise SweBenchError("SWE-bench evidence is not canonical finite JSON") from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(nested) for key, nested in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(nested) for nested in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(nested) for nested in value]
    return value


def _object(value: Any, *, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SweBenchError(f"{label} must be an object")
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        unknown = sorted(actual - fields)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise SweBenchError(f"{label} fields are invalid: {'; '.join(details)}")
    return value


def _string(
    value: Any,
    *,
    label: str,
    maximum: int = 4_096,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise SweBenchError(f"{label} must be {qualifier}")
    if len(value) > maximum:
        raise SweBenchError(f"{label} exceeds {maximum} characters")
    return value


def _boolean(value: Any, *, expected: bool, label: str) -> None:
    if value is not expected:
        raise SweBenchError(f"{label} must equal {expected!r}")


def _integer(value: Any, *, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SweBenchError(f"{label} must be an integer")
    if value < minimum:
        raise SweBenchError(f"{label} must be at least {minimum}")
    return value


def _sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise SweBenchError(f"{label} must be lowercase SHA-256")
    return value


def _string_array(
    value: Any,
    *,
    label: str,
    expected: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) > 128
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise SweBenchError(f"{label} must be a bounded array of strings")
    result = tuple(value)
    if len(set(result)) != len(result):
        raise SweBenchError(f"{label} must not contain duplicates")
    if expected is not None and result != expected:
        raise SweBenchError(f"{label} does not match the frozen field policy")
    return result


def decode_suite(value: Any, *, file_sha256: str | None = None) -> SweBenchSuite:
    """Strictly decode one suite descriptor and verify its canonical self-hash."""

    payload = _object(
        value,
        fields={
            "schema",
            "suite_id",
            "status",
            "dataset",
            "canonical_snapshot",
            "selection",
            "projection",
            "harness",
            "claim_boundary",
            "suite_sha256",
        },
        label="suite",
    )
    if payload["schema"] != SUITE_SCHEMA:
        raise SweBenchError(f"suite.schema must equal {SUITE_SCHEMA!r}")
    suite_id = _string(payload["suite_id"], label="suite.suite_id", maximum=128)
    if _IDENTIFIER_RE.fullmatch(suite_id) is None:
        raise SweBenchError("suite.suite_id is not a canonical identifier")
    if payload["status"] != "source-pinned-not-claim-ready":
        raise SweBenchError("suite.status must remain source-pinned-not-claim-ready")

    dataset = _object(
        payload["dataset"],
        fields={
            "id",
            "revision",
            "revision_date",
            "split",
            "repository_url",
            "artifact",
            "card",
            "license_evidence",
        },
        label="suite.dataset",
    )
    dataset_id = _string(dataset["id"], label="suite.dataset.id", maximum=256)
    revision = _string(dataset["revision"], label="suite.dataset.revision", maximum=40)
    if _COMMIT_RE.fullmatch(revision) is None:
        raise SweBenchError("suite.dataset.revision must be a 40-character commit")
    revision_date = _string(
        dataset["revision_date"],
        label="suite.dataset.revision_date",
        maximum=10,
    )
    try:
        parsed_revision_date = date.fromisoformat(revision_date)
    except ValueError as exc:
        raise SweBenchError(
            "suite.dataset.revision_date must be a real YYYY-MM-DD date"
        ) from exc
    if parsed_revision_date.isoformat() != revision_date:
        raise SweBenchError("suite.dataset.revision_date must be YYYY-MM-DD")
    if dataset["split"] != "test":
        raise SweBenchError("suite.dataset.split must equal 'test'")
    repository_url = _string(
        dataset["repository_url"],
        label="suite.dataset.repository_url",
        maximum=512,
    )
    if repository_url != f"https://huggingface.co/datasets/{dataset_id}":
        raise SweBenchError("suite.dataset.repository_url does not match dataset.id")

    artifact = _object(
        dataset["artifact"],
        fields={"path", "url", "media_type", "byte_count", "sha256"},
        label="suite.dataset.artifact",
    )
    artifact_path = _string(
        artifact["path"], label="suite.dataset.artifact.path", maximum=512
    )
    artifact_url = _string(
        artifact["url"], label="suite.dataset.artifact.url", maximum=1_024
    )
    if (
        artifact_url
        != f"{repository_url}/resolve/{revision}/{artifact_path}"
    ):
        raise SweBenchError("suite.dataset.artifact.url is not revision-pinned")
    if artifact["media_type"] != "application/vnd.apache.parquet":
        raise SweBenchError("suite.dataset.artifact.media_type is unsupported")
    artifact_bytes = _integer(
        artifact["byte_count"],
        label="suite.dataset.artifact.byte_count",
        minimum=1,
    )
    if artifact_bytes > 32 * 1024 * 1024:
        raise SweBenchError("suite.dataset.artifact.byte_count exceeds the safety limit")
    _sha256(artifact["sha256"], label="suite.dataset.artifact.sha256")

    card = _object(
        dataset["card"],
        fields={
            "path",
            "url",
            "byte_count",
            "sha256",
            "declared_license_spdx",
        },
        label="suite.dataset.card",
    )
    card_path = _string(card["path"], label="suite.dataset.card.path", maximum=256)
    if card["url"] != f"{repository_url}/blob/{revision}/{card_path}":
        raise SweBenchError("suite.dataset.card.url is not revision-pinned")
    _integer(card["byte_count"], label="suite.dataset.card.byte_count", minimum=1)
    _sha256(card["sha256"], label="suite.dataset.card.sha256")
    if card["declared_license_spdx"] is not None:
        raise SweBenchError("suite.dataset.card must not invent an SPDX license")
    license_evidence = _object(
        dataset["license_evidence"],
        fields={
            "status",
            "redistribution_reviewed",
            "project_code_license_does_not_cover_dataset_rows",
        },
        label="suite.dataset.license_evidence",
    )
    if license_evidence["status"] != "not-declared-in-pinned-dataset-card":
        raise SweBenchError("suite.dataset.license_evidence.status is unsupported")
    _boolean(
        license_evidence["redistribution_reviewed"],
        expected=False,
        label="suite.dataset.license_evidence.redistribution_reviewed",
    )
    _boolean(
        license_evidence["project_code_license_does_not_cover_dataset_rows"],
        expected=True,
        label=(
            "suite.dataset.license_evidence."
            "project_code_license_does_not_cover_dataset_rows"
        ),
    )

    snapshot = _object(
        payload["canonical_snapshot"],
        fields={
            "serialization",
            "decoder",
            "decoder_version",
            "byte_count",
            "sha256",
            "row_count",
            "ordered_fields",
            "field_type",
            "field_nullable_in_parquet",
            "observed_null_count",
            "order",
            "first_instance_id",
            "last_instance_id",
            "ordered_instance_ids_sha256",
            "ordered_source_sha256s_sha256",
            "ordered_candidate_input_sha256s_sha256",
            "record_binding_fields",
            "record_bindings",
        },
        label="suite.canonical_snapshot",
    )
    required_snapshot_values = {
        "serialization": "utf8-json-jcs-profile-sort-keys-no-trailing-newline",
        "decoder": "pyarrow",
        "field_type": "string",
        "field_nullable_in_parquet": True,
        "observed_null_count": 0,
        "order": "physical-parquet-row-order",
    }
    for name, expected in required_snapshot_values.items():
        if snapshot[name] != expected or type(snapshot[name]) is not type(expected):
            raise SweBenchError(
                f"suite.canonical_snapshot.{name} must equal {expected!r}"
            )
    _string(
        snapshot["decoder_version"],
        label="suite.canonical_snapshot.decoder_version",
        maximum=64,
    )
    snapshot_bytes = _integer(
        snapshot["byte_count"],
        label="suite.canonical_snapshot.byte_count",
        minimum=2,
    )
    if snapshot_bytes > _SNAPSHOT_LIMITS.max_bytes:
        raise SweBenchError("suite.canonical_snapshot.byte_count exceeds the safety limit")
    _sha256(snapshot["sha256"], label="suite.canonical_snapshot.sha256")
    row_count = _integer(
        snapshot["row_count"],
        label="suite.canonical_snapshot.row_count",
        minimum=1,
    )
    if row_count > _MAX_SOURCE_ROWS:
        raise SweBenchError("suite.canonical_snapshot.row_count exceeds the safety limit")
    _string_array(
        snapshot["ordered_fields"],
        label="suite.canonical_snapshot.ordered_fields",
        expected=SOURCE_FIELDS,
    )
    for name in (
        "first_instance_id",
        "last_instance_id",
    ):
        instance_id = _string(
            snapshot[name],
            label=f"suite.canonical_snapshot.{name}",
            maximum=256,
        )
        if _INSTANCE_RE.fullmatch(instance_id) is None:
            raise SweBenchError(f"suite.canonical_snapshot.{name} is invalid")
    for name in (
        "ordered_instance_ids_sha256",
        "ordered_source_sha256s_sha256",
        "ordered_candidate_input_sha256s_sha256",
    ):
        _sha256(snapshot[name], label=f"suite.canonical_snapshot.{name}")
    _string_array(
        snapshot["record_binding_fields"],
        label="suite.canonical_snapshot.record_binding_fields",
        expected=(
            "ordinal",
            "instance_id",
            "source_sha256",
            "candidate_input_sha256",
        ),
    )
    raw_bindings = snapshot["record_bindings"]
    if not isinstance(raw_bindings, list) or len(raw_bindings) != row_count:
        raise SweBenchError(
            "suite.canonical_snapshot.record_bindings must cover every row"
        )
    binding_ids: list[str] = []
    binding_source_hashes: list[str] = []
    binding_candidate_hashes: list[str] = []
    for ordinal, binding in enumerate(raw_bindings):
        if not isinstance(binding, list) or len(binding) != 4:
            raise SweBenchError(
                f"suite.canonical_snapshot.record_bindings[{ordinal}] is invalid"
            )
        if binding[0] != ordinal or isinstance(binding[0], bool):
            raise SweBenchError(
                f"suite.canonical_snapshot.record_bindings[{ordinal}] ordinal mismatch"
            )
        instance_id = _string(
            binding[1],
            label=(
                f"suite.canonical_snapshot.record_bindings[{ordinal}].instance_id"
            ),
            maximum=256,
        )
        if _INSTANCE_RE.fullmatch(instance_id) is None:
            raise SweBenchError(
                f"suite.canonical_snapshot.record_bindings[{ordinal}] instance ID is invalid"
            )
        binding_ids.append(instance_id)
        binding_source_hashes.append(
            _sha256(
                binding[2],
                label=(
                    f"suite.canonical_snapshot.record_bindings[{ordinal}].source_sha256"
                ),
            )
        )
        binding_candidate_hashes.append(
            _sha256(
                binding[3],
                label=(
                    "suite.canonical_snapshot.record_bindings"
                    f"[{ordinal}].candidate_input_sha256"
                ),
            )
        )
    if len(set(binding_ids)) != len(binding_ids):
        raise SweBenchError("suite.canonical_snapshot.record_bindings has duplicate IDs")
    binding_anchors = {
        "ordered_instance_ids_sha256": _canonical_sha256(binding_ids),
        "ordered_source_sha256s_sha256": _canonical_sha256(
            binding_source_hashes
        ),
        "ordered_candidate_input_sha256s_sha256": _canonical_sha256(
            binding_candidate_hashes
        ),
    }
    for name, observed in binding_anchors.items():
        if observed != snapshot[name]:
            raise SweBenchError(
                f"suite.canonical_snapshot.record_bindings {name} mismatch"
            )
    if (
        binding_ids[0] != snapshot["first_instance_id"]
        or binding_ids[-1] != snapshot["last_instance_id"]
    ):
        raise SweBenchError(
            "suite.canonical_snapshot.record_bindings endpoint IDs mismatch"
        )

    selection = _object(
        payload["selection"],
        fields={
            "policy",
            "population_count",
            "selected_count",
            "seed",
            "result_blind",
            "ordered_selected_instance_ids_sha256",
        },
        label="suite.selection",
    )
    if selection["policy"] != "all-records-in-physical-order":
        raise SweBenchError("suite.selection.policy is unsupported")
    for name in ("population_count", "selected_count"):
        count = _integer(
            selection[name],
            label=f"suite.selection.{name}",
            minimum=1,
        )
        if count != row_count:
            raise SweBenchError(f"suite.selection.{name} must equal row_count")
    if selection["seed"] is not None:
        raise SweBenchError("suite.selection.seed must be null for full-cohort selection")
    _boolean(
        selection["result_blind"],
        expected=True,
        label="suite.selection.result_blind",
    )
    selected_ids_sha256 = _sha256(
        selection["ordered_selected_instance_ids_sha256"],
        label="suite.selection.ordered_selected_instance_ids_sha256",
    )
    if selected_ids_sha256 != snapshot["ordered_instance_ids_sha256"]:
        raise SweBenchError("suite.selection selected IDs do not match the source")

    projection = _object(
        payload["projection"],
        fields={
            "candidate_source_fields",
            "candidate_generated_fields",
            "coordinator_only_fields",
            "evaluator_only_fields",
            "opaque_id_scheme",
            "candidate_projection",
            "hints_enabled",
            "canonical_instance_id_candidate_visible",
        },
        label="suite.projection",
    )
    _string_array(
        projection["candidate_source_fields"],
        label="suite.projection.candidate_source_fields",
        expected=CANDIDATE_SOURCE_FIELDS,
    )
    _string_array(
        projection["candidate_generated_fields"],
        label="suite.projection.candidate_generated_fields",
        expected=CANDIDATE_GENERATED_FIELDS,
    )
    _string_array(
        projection["coordinator_only_fields"],
        label="suite.projection.coordinator_only_fields",
        expected=COORDINATOR_ONLY_FIELDS,
    )
    _string_array(
        projection["evaluator_only_fields"],
        label="suite.projection.evaluator_only_fields",
        expected=EVALUATOR_ONLY_FIELDS,
    )
    partition = (
        tuple(projection["candidate_source_fields"])
        + tuple(projection["coordinator_only_fields"])
        + tuple(projection["evaluator_only_fields"])
    )
    if set(partition) != set(SOURCE_FIELDS) or len(partition) != len(SOURCE_FIELDS):
        raise SweBenchError("suite.projection must classify every source field exactly once")
    if projection["opaque_id_scheme"] != "hmac-sha256-v1":
        raise SweBenchError("suite.projection.opaque_id_scheme is unsupported")
    if projection["candidate_projection"] != "allowlist-not-subtraction":
        raise SweBenchError("suite.projection.candidate_projection is unsafe")
    _boolean(
        projection["hints_enabled"],
        expected=False,
        label="suite.projection.hints_enabled",
    )
    _boolean(
        projection["canonical_instance_id_candidate_visible"],
        expected=False,
        label="suite.projection.canonical_instance_id_candidate_visible",
    )

    harness = _object(
        payload["harness"],
        fields={
            "repository_url",
            "tag",
            "revision",
            "license_spdx",
            "license_path",
            "license_sha256",
            "entrypoint_path",
            "entrypoint_sha256",
            "review_status",
        },
        label="suite.harness",
    )
    if harness["repository_url"] != "https://github.com/SWE-bench/SWE-bench":
        raise SweBenchError("suite.harness.repository_url is unsupported")
    _string(harness["tag"], label="suite.harness.tag", maximum=64)
    harness_revision = _string(
        harness["revision"], label="suite.harness.revision", maximum=40
    )
    if _COMMIT_RE.fullmatch(harness_revision) is None:
        raise SweBenchError("suite.harness.revision must be a 40-character commit")
    if harness["license_spdx"] != "MIT":
        raise SweBenchError("suite.harness.license_spdx must equal 'MIT'")
    for name in ("license_path", "entrypoint_path"):
        _string(harness[name], label=f"suite.harness.{name}", maximum=512)
    for name in ("license_sha256", "entrypoint_sha256"):
        _sha256(harness[name], label=f"suite.harness.{name}")
    if harness["review_status"] != "pinned-not-security-reviewed":
        raise SweBenchError("suite.harness.review_status overstates review")

    claim = _object(
        payload["claim_boundary"],
        fields={
            "credential_free_source_download",
            "source_bytes_pinned",
            "snapshot_derivation_locally_reproduced",
            "candidate_gold_fields_excluded",
            "opaque_ids_prevent_public_relinking",
            "opaque_key_entropy_verified",
            "pretraining_contamination_excluded",
            "candidate_mount_isolation_verified",
            "repository_snapshots_prepared",
            "network_isolation_verified",
            "grader_security_reviewed",
            "hidden_test_patch_application_verified",
            "dataset_redistribution_reviewed",
            "model_evaluation_executed",
            "external_score_generated",
            "external_usefulness_claimed",
            "claim_ready",
            "self_hash_authenticates_author",
        },
        label="suite.claim_boundary",
    )
    for name in (
        "credential_free_source_download",
        "source_bytes_pinned",
        "snapshot_derivation_locally_reproduced",
        "candidate_gold_fields_excluded",
    ):
        _boolean(claim[name], expected=True, label=f"suite.claim_boundary.{name}")
    for name in (
        "opaque_ids_prevent_public_relinking",
        "opaque_key_entropy_verified",
        "pretraining_contamination_excluded",
        "candidate_mount_isolation_verified",
        "repository_snapshots_prepared",
        "network_isolation_verified",
        "grader_security_reviewed",
        "hidden_test_patch_application_verified",
        "dataset_redistribution_reviewed",
        "model_evaluation_executed",
        "external_score_generated",
        "external_usefulness_claimed",
        "claim_ready",
        "self_hash_authenticates_author",
    ):
        _boolean(claim[name], expected=False, label=f"suite.claim_boundary.{name}")

    claimed = _sha256(payload["suite_sha256"], label="suite.suite_sha256")
    unsigned = dict(payload)
    unsigned.pop("suite_sha256")
    if _canonical_sha256(unsigned) != claimed:
        raise SweBenchError("suite.suite_sha256 mismatch")
    if file_sha256 is not None:
        _sha256(file_sha256, label="suite file SHA-256")
    return SweBenchSuite(
        document=_freeze_json(payload),
        file_sha256=file_sha256,
    )


def load_suite(path: str | Path = DEFAULT_SUITE) -> SweBenchSuite:
    """Load a bounded local suite descriptor without network access."""

    try:
        document = load_strict_json_file(
            path,
            limits=_SUITE_LIMITS,
            label="SWE-bench suite descriptor",
        )
    except StrictJsonError as exc:
        raise SweBenchError(str(exc)) from exc
    return decode_suite(document.value, file_sha256=document.file_sha256)


def _validate_record(
    raw: Any,
    *,
    ordinal: int,
) -> dict[str, str]:
    row = _object(raw, fields=set(SOURCE_FIELDS), label=f"source row {ordinal}")
    result: dict[str, str] = {}
    for field in SOURCE_FIELDS:
        value = _string(
            row[field],
            label=f"source row {ordinal}.{field}",
            maximum=_MAX_SOURCE_STRING_CHARS,
            allow_empty=(field == "hints_text"),
        )
        result[field] = value
    if _REPOSITORY_RE.fullmatch(result["repo"]) is None:
        raise SweBenchError(f"source row {ordinal}.repo is invalid")
    if _INSTANCE_RE.fullmatch(result["instance_id"]) is None:
        raise SweBenchError(f"source row {ordinal}.instance_id is invalid")
    for field in ("base_commit", "environment_setup_commit"):
        if _COMMIT_RE.fullmatch(result[field]) is None:
            raise SweBenchError(f"source row {ordinal}.{field} is invalid")
    if _UTC_TIMESTAMP_RE.fullmatch(result["created_at"]) is None:
        raise SweBenchError(f"source row {ordinal}.created_at is invalid")
    return result


def _validate_records(
    value: Any,
    suite: SweBenchSuite,
) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        raise SweBenchError("SWE-bench source snapshot must be an array")
    if len(value) != suite.row_count:
        raise SweBenchError(
            "SWE-bench source snapshot row count does not match the suite"
        )
    if len(value) > _MAX_SOURCE_ROWS:
        raise SweBenchError("SWE-bench source snapshot exceeds the row limit")
    records = tuple(
        _validate_record(raw, ordinal=ordinal)
        for ordinal, raw in enumerate(value)
    )
    ids = [record["instance_id"] for record in records]
    if len(set(ids)) != len(ids):
        raise SweBenchError("SWE-bench source snapshot has duplicate instance IDs")
    snapshot = suite.document["canonical_snapshot"]
    if ids[0] != snapshot["first_instance_id"] or ids[-1] != snapshot["last_instance_id"]:
        raise SweBenchError("SWE-bench source snapshot endpoint IDs do not match")
    source_hashes = [_canonical_sha256(record) for record in records]
    candidate_hashes = [
        _canonical_sha256({"problem_statement": record["problem_statement"]})
        for record in records
    ]
    anchors = {
        "ordered_instance_ids_sha256": _canonical_sha256(ids),
        "ordered_source_sha256s_sha256": _canonical_sha256(source_hashes),
        "ordered_candidate_input_sha256s_sha256": _canonical_sha256(
            candidate_hashes
        ),
    }
    for name, observed in anchors.items():
        if observed != snapshot[name]:
            raise SweBenchError(f"SWE-bench source snapshot {name} mismatch")
    expected_bindings = tuple(
        (ordinal, ids[ordinal], source_hashes[ordinal], candidate_hashes[ordinal])
        for ordinal in range(len(records))
    )
    observed_bindings = tuple(
        tuple(binding) for binding in snapshot["record_bindings"]
    )
    if observed_bindings != expected_bindings:
        raise SweBenchError("SWE-bench source snapshot record binding mismatch")
    return records


def load_source_snapshot(
    path: str | Path,
    suite: SweBenchSuite | None = None,
) -> VerifiedSweBenchSource:
    """Load and bind the exact canonical JSON projection of the pinned Parquet."""

    selected_suite = suite or load_suite()
    try:
        document = load_strict_json_file(
            path,
            limits=_SNAPSHOT_LIMITS,
            label="SWE-bench canonical source snapshot",
        )
    except StrictJsonError as exc:
        raise SweBenchError(str(exc)) from exc
    snapshot = selected_suite.document["canonical_snapshot"]
    if document.byte_count != snapshot["byte_count"]:
        raise SweBenchError("SWE-bench canonical source snapshot byte count mismatch")
    if document.file_sha256 != snapshot["sha256"]:
        raise SweBenchError("SWE-bench canonical source snapshot SHA-256 mismatch")
    records = _validate_records(document.value, selected_suite)
    return VerifiedSweBenchSource(
        suite=selected_suite,
        records=tuple(_freeze_json(record) for record in records),
        file_sha256=document.file_sha256,
        byte_count=document.byte_count,
    )


def _revalidate_verified_source(
    source: VerifiedSweBenchSource,
) -> tuple[SweBenchSuite, tuple[dict[str, str], ...]]:
    """Rebind a publicly constructible source value before using its rows."""

    if not isinstance(source, VerifiedSweBenchSource):
        raise SweBenchError("source must be a VerifiedSweBenchSource")
    if not isinstance(source.suite, SweBenchSuite):
        raise SweBenchError("verified source suite must be a SweBenchSuite")
    suite = decode_suite(
        source.suite.to_dict(),
        file_sha256=source.suite.file_sha256,
    )
    snapshot = suite.document["canonical_snapshot"]
    if source.byte_count != snapshot["byte_count"]:
        raise SweBenchError("verified source byte count does not match the suite")
    if source.file_sha256 != snapshot["sha256"]:
        raise SweBenchError("verified source SHA-256 does not match the suite")
    records = _validate_records(
        [_thaw_json(record) for record in source.records],
        suite,
    )
    encoded = _canonical_json_bytes(records)
    if len(encoded) != source.byte_count:
        raise SweBenchError("verified source rows do not match the byte count")
    if hashlib.sha256(encoded).hexdigest() != source.file_sha256:
        raise SweBenchError("verified source rows do not match the source SHA-256")
    return suite, records


def _decode_parquet_bytes(encoded: bytes, suite: SweBenchSuite) -> list[dict[str, Any]]:
    try:
        pyarrow = importlib.import_module("pyarrow")
        parquet = importlib.import_module("pyarrow.parquet")
    except ModuleNotFoundError as exc:
        if exc.name == "pyarrow" or (exc.name or "").startswith("pyarrow."):
            raise SweBenchError(
                "materializing the pinned Parquet requires the optional "
                "pyarrow version recorded by the suite"
            ) from exc
        raise
    expected_version = suite.document["canonical_snapshot"]["decoder_version"]
    if getattr(pyarrow, "__version__", None) != expected_version:
        raise SweBenchError(
            f"pyarrow must equal the pinned decoder version {expected_version}"
        )
    try:
        table = parquet.read_table(pyarrow.BufferReader(encoded))
    except Exception as exc:
        raise SweBenchError("could not decode the pinned SWE-bench Parquet") from exc
    if table.num_rows != suite.row_count:
        raise SweBenchError("decoded Parquet row count does not match the suite")
    if tuple(table.column_names) != SOURCE_FIELDS:
        raise SweBenchError("decoded Parquet field order does not match the suite")
    for field in table.schema:
        if str(field.type) != "string" or field.nullable is not True:
            raise SweBenchError("decoded Parquet field schema does not match the suite")
    if any(table.column(name).null_count for name in table.column_names):
        raise SweBenchError("decoded Parquet unexpectedly contains null values")
    try:
        return table.to_pylist()
    except Exception as exc:
        raise SweBenchError("could not materialize decoded SWE-bench rows") from exc


def materialize_source_snapshot(
    parquet_path: str | Path,
    output_path: str | Path,
    *,
    suite: SweBenchSuite | None = None,
) -> dict[str, Any]:
    """Verify local official bytes and write their exact canonical JSON rows."""

    selected_suite = suite or load_suite()
    artifact = selected_suite.document["dataset"]["artifact"]
    try:
        source = read_bounded_regular_file(
            parquet_path,
            max_bytes=int(artifact["byte_count"]),
            label="SWE-bench Parquet source",
        )
    except StrictJsonError as exc:
        raise SweBenchError(str(exc)) from exc
    if source.byte_count != artifact["byte_count"]:
        raise SweBenchError("SWE-bench Parquet byte count mismatch")
    if source.file_sha256 != artifact["sha256"]:
        raise SweBenchError("SWE-bench Parquet SHA-256 mismatch")
    rows = _decode_parquet_bytes(source.value, selected_suite)
    records = _validate_records(rows, selected_suite)
    encoded = _canonical_json_bytes(records)
    snapshot = selected_suite.document["canonical_snapshot"]
    if len(encoded) != snapshot["byte_count"]:
        raise SweBenchError("materialized source snapshot byte count mismatch")
    if hashlib.sha256(encoded).hexdigest() != snapshot["sha256"]:
        raise SweBenchError("materialized source snapshot SHA-256 mismatch")
    try:
        atomic_write_text(
            output_path,
            encoded.decode("utf-8", errors="strict"),
            overwrite=False,
        )
    except FileExistsError as exc:
        raise SweBenchError("source snapshot output already exists") from exc
    return {
        "schema": VERIFICATION_SCHEMA,
        "operation": "materialize-source-snapshot",
        "verified": True,
        "suite_id": selected_suite.suite_id,
        "suite_sha256": selected_suite.suite_sha256,
        "source_artifact_sha256": source.file_sha256,
        "snapshot_sha256": snapshot["sha256"],
        "task_count": len(records),
        "claim_ready": False,
    }


def load_opaque_key(path: str | Path) -> bytes:
    """Load an exact 32-byte opaque-ID key without claiming key entropy."""

    try:
        document = read_bounded_regular_file(
            path,
            max_bytes=_OPAQUE_KEY_BYTES,
            label="SWE-bench opaque-ID key",
        )
    except StrictJsonError as exc:
        raise SweBenchError(str(exc)) from exc
    if document.byte_count != _OPAQUE_KEY_BYTES:
        raise SweBenchError(
            f"SWE-bench opaque-ID key must contain exactly {_OPAQUE_KEY_BYTES} bytes"
        )
    if hmac.compare_digest(document.value, b"\0" * _OPAQUE_KEY_BYTES):
        raise SweBenchError("SWE-bench opaque-ID key must not be all zero bytes")
    return document.value


def _opaque_task_id(key: bytes, suite: SweBenchSuite, instance_id: str) -> str:
    message = (
        _OPAQUE_DOMAIN
        + suite.suite_sha256.encode("ascii")
        + b"\0"
        + instance_id.encode("utf-8", errors="strict")
    )
    return "task-" + hmac.new(key, message, hashlib.sha256).hexdigest()


def task_input_document(
    source: VerifiedSweBenchSource,
    *,
    opaque_key: bytes,
) -> dict[str, Any]:
    """Construct task payloads exclusively from the frozen public allowlist."""

    if not isinstance(opaque_key, bytes) or len(opaque_key) != _OPAQUE_KEY_BYTES:
        raise SweBenchError(
            f"opaque_key must contain exactly {_OPAQUE_KEY_BYTES} bytes"
        )
    if hmac.compare_digest(opaque_key, b"\0" * _OPAQUE_KEY_BYTES):
        raise SweBenchError("opaque_key must not be all zero bytes")
    suite, records = _revalidate_verified_source(source)
    tasks = [
        {
            "opaque_task_id": _opaque_task_id(
                opaque_key,
                suite,
                str(record["instance_id"]),
            ),
            "problem_statement": str(record["problem_statement"]),
        }
        for record in records
    ]
    if len({task["opaque_task_id"] for task in tasks}) != len(tasks):
        raise SweBenchError("opaque task ID collision")
    key_fingerprint = hashlib.sha256(
        _KEY_FINGERPRINT_DOMAIN + opaque_key
    ).hexdigest()
    unsigned: dict[str, Any] = {
        "schema": TASK_INPUT_SCHEMA,
        "suite_id": suite.suite_id,
        "suite_sha256": suite.suite_sha256,
        "source_snapshot_sha256": source.file_sha256,
        "source_record_sha256s_sha256": suite.document[
            "canonical_snapshot"
        ]["ordered_source_sha256s_sha256"],
        "candidate_input_sha256s_sha256": suite.document[
            "canonical_snapshot"
        ]["ordered_candidate_input_sha256s_sha256"],
        "opaque_id_scheme": "hmac-sha256-v1",
        "opaque_key_fingerprint": key_fingerprint,
        "candidate_payload_fields": list(
            CANDIDATE_GENERATED_FIELDS + CANDIDATE_SOURCE_FIELDS
        ),
        "task_count": len(tasks),
        "tasks": tasks,
        "evidence_state": {
            "candidate_payloads_gold_free": True,
            "result_blind": True,
            "repository_snapshots_prepared": False,
            "execution_performed": False,
            "grading_performed": False,
            "claim_ready": False,
        },
    }
    unsigned["task_input_sha256"] = _canonical_sha256(unsigned)
    return unsigned


def _scan_forbidden_keys(value: Any, *, path: str = "tasks") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in EVALUATOR_ONLY_FIELDS:
                raise SweBenchError(f"{path} contains forbidden evaluator field {key}")
            _scan_forbidden_keys(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _scan_forbidden_keys(nested, path=f"{path}[{index}]")


def decode_task_input(value: Any, *, suite: SweBenchSuite | None = None) -> dict[str, Any]:
    """Strictly verify a self-hashed candidate-task export."""

    payload = _object(
        value,
        fields={
            "schema",
            "suite_id",
            "suite_sha256",
            "source_snapshot_sha256",
            "source_record_sha256s_sha256",
            "candidate_input_sha256s_sha256",
            "opaque_id_scheme",
            "opaque_key_fingerprint",
            "candidate_payload_fields",
            "task_count",
            "tasks",
            "evidence_state",
            "task_input_sha256",
        },
        label="task input",
    )
    if payload["schema"] != TASK_INPUT_SCHEMA:
        raise SweBenchError(f"task input.schema must equal {TASK_INPUT_SCHEMA!r}")
    selected_suite = suite or load_suite()
    if payload["suite_id"] != selected_suite.suite_id:
        raise SweBenchError("task input.suite_id mismatch")
    if payload["suite_sha256"] != selected_suite.suite_sha256:
        raise SweBenchError("task input.suite_sha256 mismatch")
    if payload["source_snapshot_sha256"] != selected_suite.snapshot_sha256:
        raise SweBenchError("task input.source_snapshot_sha256 mismatch")
    expected_record_hash = selected_suite.document["canonical_snapshot"][
        "ordered_source_sha256s_sha256"
    ]
    if payload["source_record_sha256s_sha256"] != expected_record_hash:
        raise SweBenchError("task input.source_record_sha256s_sha256 mismatch")
    expected_candidate_hash = selected_suite.document["canonical_snapshot"][
        "ordered_candidate_input_sha256s_sha256"
    ]
    if payload["candidate_input_sha256s_sha256"] != expected_candidate_hash:
        raise SweBenchError("task input.candidate_input_sha256s_sha256 mismatch")
    if payload["opaque_id_scheme"] != "hmac-sha256-v1":
        raise SweBenchError("task input.opaque_id_scheme is unsupported")
    _sha256(payload["opaque_key_fingerprint"], label="task input.opaque_key_fingerprint")
    _string_array(
        payload["candidate_payload_fields"],
        label="task input.candidate_payload_fields",
        expected=CANDIDATE_GENERATED_FIELDS + CANDIDATE_SOURCE_FIELDS,
    )
    task_count = _integer(payload["task_count"], label="task input.task_count", minimum=1)
    if task_count != selected_suite.row_count:
        raise SweBenchError("task input.task_count does not match the suite")
    tasks = payload["tasks"]
    if not isinstance(tasks, list) or len(tasks) != task_count:
        raise SweBenchError("task input.tasks does not match task_count")
    opaque_ids: list[str] = []
    for ordinal, raw in enumerate(tasks):
        task = _object(
            raw,
            fields=set(CANDIDATE_GENERATED_FIELDS + CANDIDATE_SOURCE_FIELDS),
            label=f"task input.tasks[{ordinal}]",
        )
        opaque_id = _string(
            task["opaque_task_id"],
            label=f"task input.tasks[{ordinal}].opaque_task_id",
            maximum=69,
        )
        if _OPAQUE_ID_RE.fullmatch(opaque_id) is None:
            raise SweBenchError(
                f"task input.tasks[{ordinal}].opaque_task_id is invalid"
            )
        _string(
            task["problem_statement"],
            label=f"task input.tasks[{ordinal}].problem_statement",
            maximum=_MAX_SOURCE_STRING_CHARS,
        )
        opaque_ids.append(opaque_id)
    if len(set(opaque_ids)) != len(opaque_ids):
        raise SweBenchError("task input.tasks has duplicate opaque task IDs")
    observed_candidate_hash = _canonical_sha256(
        [
            _canonical_sha256(
                {"problem_statement": task["problem_statement"]}
            )
            for task in tasks
        ]
    )
    if observed_candidate_hash != expected_candidate_hash:
        raise SweBenchError("task input problem statements do not match the suite")
    _scan_forbidden_keys(tasks)
    evidence = _object(
        payload["evidence_state"],
        fields={
            "candidate_payloads_gold_free",
            "result_blind",
            "repository_snapshots_prepared",
            "execution_performed",
            "grading_performed",
            "claim_ready",
        },
        label="task input.evidence_state",
    )
    for name in ("candidate_payloads_gold_free", "result_blind"):
        _boolean(evidence[name], expected=True, label=f"task input.evidence_state.{name}")
    for name in (
        "repository_snapshots_prepared",
        "execution_performed",
        "grading_performed",
        "claim_ready",
    ):
        _boolean(evidence[name], expected=False, label=f"task input.evidence_state.{name}")
    claimed = _sha256(payload["task_input_sha256"], label="task input.task_input_sha256")
    unsigned = dict(payload)
    unsigned.pop("task_input_sha256")
    if _canonical_sha256(unsigned) != claimed:
        raise SweBenchError("task input.task_input_sha256 mismatch")
    return payload


def load_task_input(
    path: str | Path,
    *,
    suite: SweBenchSuite | None = None,
) -> dict[str, Any]:
    """Load one bounded task input document from a local regular file."""

    try:
        document = load_strict_json_file(
            path,
            limits=_TASK_LIMITS,
            label="SWE-bench task input",
        )
    except StrictJsonError as exc:
        raise SweBenchError(str(exc)) from exc
    return decode_task_input(document.value, suite=suite)


def write_task_input(
    path: str | Path,
    document: Mapping[str, Any],
    *,
    source: VerifiedSweBenchSource,
    opaque_key: bytes,
) -> None:
    """Write a source/key-reconciled task document as stable pretty JSON."""

    decoded = verify_task_input_binding(
        _thaw_json(document),
        source,
        opaque_key=opaque_key,
    )
    rendered = json.dumps(
        decoded,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"
    try:
        atomic_write_text(path, rendered, overwrite=False)
    except FileExistsError as exc:
        raise SweBenchError("task input output already exists") from exc


def verify_task_input_binding(
    document: Any,
    source: VerifiedSweBenchSource,
    *,
    opaque_key: bytes,
) -> dict[str, Any]:
    """Recompute a task export from source rows and its separately retained key."""

    decoded = decode_task_input(document, suite=source.suite)
    expected = task_input_document(source, opaque_key=opaque_key)
    if _canonical_json_bytes(decoded) != _canonical_json_bytes(expected):
        raise SweBenchError("task input does not match the bound source and opaque key")
    return expected


def verify_suite(path: str | Path = DEFAULT_SUITE) -> dict[str, Any]:
    """Return the non-claim-bearing status of one valid source descriptor."""

    suite = load_suite(path)
    claim = suite.document["claim_boundary"]
    return {
        "schema": VERIFICATION_SCHEMA,
        "operation": "verify-suite",
        "verified": True,
        "suite_id": suite.suite_id,
        "suite_sha256": suite.suite_sha256,
        "descriptor_file_sha256": suite.file_sha256,
        "source_artifact_sha256": suite.source_artifact_sha256,
        "task_count": suite.row_count,
        "candidate_gold_fields_excluded": claim[
            "candidate_gold_fields_excluded"
        ],
        "opaque_ids_prevent_public_relinking": claim[
            "opaque_ids_prevent_public_relinking"
        ],
        "pretraining_contamination_excluded": claim[
            "pretraining_contamination_excluded"
        ],
        "model_evaluation_executed": claim["model_evaluation_executed"],
        "external_score_generated": claim["external_score_generated"],
        "claim_ready": claim["claim_ready"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify and project a pinned local SWE-bench source",
    )
    parser.add_argument(
        "--suite",
        type=Path,
        default=DEFAULT_SUITE,
        help="self-hashed suite descriptor",
    )
    subparsers = parser.add_subparsers(dest="operation")
    subparsers.add_parser("verify-suite", help="verify only the suite descriptor")
    materialize = subparsers.add_parser(
        "materialize-source",
        help="verify local Parquet and write its canonical JSON snapshot",
    )
    materialize.add_argument("--parquet", type=Path, required=True)
    materialize.add_argument("--output", type=Path, required=True)
    project = subparsers.add_parser(
        "project",
        help="write gold-free task payloads from the canonical snapshot",
    )
    project.add_argument("--snapshot", type=Path, required=True)
    project.add_argument("--opaque-key-file", type=Path, required=True)
    project.add_argument("--output", type=Path, required=True)
    verify_task = subparsers.add_parser(
        "verify-task-input",
        help="verify an existing gold-free task document",
    )
    verify_task.add_argument("--input", type=Path, required=True)
    verify_task.add_argument("--snapshot", type=Path, required=True)
    verify_task.add_argument("--opaque-key-file", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        suite = load_suite(args.suite)
        operation = args.operation or "verify-suite"
        if operation == "verify-suite":
            summary = verify_suite(args.suite)
        elif operation == "materialize-source":
            summary = materialize_source_snapshot(
                args.parquet,
                args.output,
                suite=suite,
            )
        elif operation == "project":
            source = load_source_snapshot(args.snapshot, suite=suite)
            key = load_opaque_key(args.opaque_key_file)
            document = task_input_document(source, opaque_key=key)
            write_task_input(
                args.output,
                document,
                source=source,
                opaque_key=key,
            )
            summary = {
                "schema": VERIFICATION_SCHEMA,
                "operation": "project",
                "verified": True,
                "suite_id": suite.suite_id,
                "suite_sha256": suite.suite_sha256,
                "task_input_sha256": document["task_input_sha256"],
                "task_count": document["task_count"],
                "candidate_payloads_gold_free": True,
                "claim_ready": False,
            }
        else:
            document = load_task_input(args.input, suite=suite)
            source = load_source_snapshot(args.snapshot, suite=suite)
            key = load_opaque_key(args.opaque_key_file)
            verify_task_input_binding(
                document,
                source,
                opaque_key=key,
            )
            summary = {
                "schema": VERIFICATION_SCHEMA,
                "operation": "verify-task-input",
                "verified": True,
                "suite_id": suite.suite_id,
                "task_input_sha256": document["task_input_sha256"],
                "task_count": document["task_count"],
                "source_binding_verified": True,
                "claim_ready": False,
            }
    except (OSError, SweBenchError) as exc:
        print(f"SWE-bench verification failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
