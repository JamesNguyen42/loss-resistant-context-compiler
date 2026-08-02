"""Offline, source-only verification for the pinned tau2-bench text cohort.

This module deliberately does not import tau2-bench, install its dependency
lock, construct a simulator, expose upstream Task objects, or execute a
candidate/evaluator.  It verifies a self-hashed descriptor and can reproduce
its source selection from a caller-provided local bare Git mirror.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .json_io import StrictJsonError, StrictJsonLimits, load_strict_json_file
from .literal_process import LiteralProcessLimits, run_literal_argv
from .swebench_repository import (
    RepositoryPreparationError,
    RepositoryPreparationLimits,
    VerifiedBareMirror,
    verify_local_bare_mirror,
)

SUITE_SCHEMA = "ctxc-tau2-suite-0.1"
SOURCE_VERIFICATION_SCHEMA = "ctxc-tau2-source-verification-0.1"
DEFAULT_SUITE = Path(__file__).with_name("suites") / "tau2_text_v1.json"

REPOSITORY = "sierra-research/tau2-bench"
REPOSITORY_URL = "https://github.com/sierra-research/tau2-bench"
TAG_NAME = "v1.0.1"
TAG_REF = "refs/tags/v1.0.1"
SUITE_ID = "tau2-text-v1.0.1-base"
DOMAINS = ("airline", "retail", "telecom")
DOMAIN_COUNTS = (("airline", 50), ("retail", 114), ("telecom", 114))
TASK_COUNT = 278
MANIFEST_BYTE_COUNT = 15_948
MANIFEST_SHA256 = "61336d42294a9265ea4b70748e7be0988a98b6064e20e366dbf5d4088b0426a5"
ORDERED_TASK_KEY_SHA256S_SHA256 = "4a6618d0064beb20de76f8a93ce69092eb9be283aedb5e024ef31cc4a5405b3d"
ORDERED_SOURCE_SHA256S_SHA256 = "1bceeb8c93ba607b4e8d924b723a5f7e81401a85e77ce1de51c6a54138c8dcbd"

PINNED_OBJECTS = MappingProxyType(
    {
        "tag_object": MappingProxyType(
            {
                "type": "tag",
                "oid": "b711c1ead46f55111bf765cf44d5da8bacc2d28c",
                "byte_count": 148,
                "sha256": "a6afd9ab0877d813e6bc167a4dca7b94b7cbba2caca8fece0c71af7f2f2a8307",
            }
        ),
        "commit_object": MappingProxyType(
            {
                "type": "commit",
                "oid": "fc0055dc4e0a316c3f83133267fbd6faaa770992",
                "byte_count": 7_714,
                "sha256": "e32392e98e5e47a91819f5dbfa966ad2bb6d6be390a3e40abf00cd7dd8a5c81e",
            }
        ),
        "root_tree_object": MappingProxyType(
            {
                "type": "tree",
                "oid": "4837da1c2b310152f63d3d7987f4325183ca6f7c",
                "byte_count": 1_029,
                "sha256": "492c077b280ef920acd125debea38fa06a60f4f60b76b45eb3d1ff6c6e174fa5",
            }
        ),
    }
)

PINNED_REQUIRED_FILES = (
    (
        "LICENSE",
        "100644",
        "f0323a32227c1327820da33d2fb9d3338a27ac84",
        1_072,
        "e67c5aa0074dfcaefd3c3a1aedb94cb539234aecd15d5a972574e3200e6252fe",
    ),
    (
        "pyproject.toml",
        "100644",
        "55a9d9cf6ebd8bf2326798f4923988917e431086",
        2_417,
        "23d59670b4ad7bbc0f57420fd9643a8d9188c24abe4f29c9c965544d8cc4cb8d",
    ),
    (
        "uv.lock",
        "100644",
        "09f0b4a86b4aaf82bf87511df1ff970ff23ec6bd",
        466_363,
        "62d3a8c4807b89e85703b3c03f2c21048a2da9736ca83d9af6f61adc74ac5314",
    ),
    (
        "src/tau2/domains/airline/environment.py",
        "100644",
        "e1cabffe7ed4ad895904aa5248319dd307e77a97",
        1_612,
        "64589faffbcb75c8ac7c95d2c94ad27f4bd70e23b9856a6928fa678a0f7fcc8d",
    ),
    (
        "src/tau2/domains/retail/environment.py",
        "100644",
        "a403300c800a06c314260c6215b0c39e175b4cdc",
        1_615,
        "5b819fb895a4ef49df71404c3d9678ab09e8578eec841b8ec9229c8f51e32405",
    ),
    (
        "src/tau2/domains/telecom/environment.py",
        "100644",
        "a408c9bd5cdfb0c09eeeb438301459356b51d088",
        7_286,
        "7bc335d717a8cb6bda54551fa2b5c12ff073f6287878d215f4181572e551abcd",
    ),
    (
        "data/tau2/domains/airline/tasks.json",
        "100644",
        "ea4ff5e3f5e3d97ca391846a6e8dd9eb3437dc80",
        155_528,
        "ccd8ba737b4cc371415af70151187788f728d6108d0916e73bb4317b40542052",
    ),
    (
        "data/tau2/domains/airline/split_tasks.json",
        "100644",
        "c83cce155671e66a6bf07450fd2845fe0eb1f229",
        1_443,
        "b22ced4d9a9850ac9aea31c53bdcb6d6009058140bd9acc7db37c1d36222ba8b",
    ),
    (
        "data/tau2/domains/retail/tasks.json",
        "100644",
        "e95a9f898b2d1f6568ea8faec78edc3767ce747d",
        345_982,
        "8e03ebce7901bd6218e7a7dc3105faa9324091a68058f7fe61c65262868812e8",
    ),
    (
        "data/tau2/domains/retail/split_tasks.json",
        "100644",
        "8b20704a17aa1940b1e58668f89dcef2ddaeeea3",
        3_263,
        "ed0580ec52575b63fbf76568af42490da6ee7783ecb4aa81af46961291358f20",
    ),
    (
        "data/tau2/domains/telecom/tasks.json",
        "100644",
        "6f41a5e3489e187fa247d729706509d8250a31c1",
        13_977_063,
        "37e562e1ae3242577407e1303b1548bc64e7ea68e37d36173e6747990ceaf8a4",
    ),
    (
        "data/tau2/domains/telecom/split_tasks.json",
        "100644",
        "a5b01687eaaa5c669f792bff33c0d6ec13be36fd",
        356_149,
        "605b488bb9a6acb3c7f4505240a855fdc8681d09aadb16a8f38b2efcfc5c3aec",
    ),
)

TASK_FIELDS = (
    "id",
    "description",
    "user_scenario",
    "initial_state",
    "evaluation_criteria",
    "annotations",
    "issues",
    "ticket",
)
CANDIDATE_SOURCE_FIELDS: tuple[str, ...] = ()
COORDINATOR_ONLY_FIELDS = ("id", "annotations", "issues", "ticket")
SIMULATOR_ONLY_FIELDS = ("user_scenario",)
ENVIRONMENT_ONLY_FIELDS = ("initial_state",)
EVALUATOR_ONLY_FIELDS = ("description", "evaluation_criteria")

REQUIRED_PATHS = (
    "LICENSE",
    "pyproject.toml",
    "uv.lock",
    "src/tau2/domains/airline/environment.py",
    "src/tau2/domains/retail/environment.py",
    "src/tau2/domains/telecom/environment.py",
    "data/tau2/domains/airline/tasks.json",
    "data/tau2/domains/airline/split_tasks.json",
    "data/tau2/domains/retail/tasks.json",
    "data/tau2/domains/retail/split_tasks.json",
    "data/tau2/domains/telecom/tasks.json",
    "data/tau2/domains/telecom/split_tasks.json",
)

TASK_PATHS = {domain: f"data/tau2/domains/{domain}/tasks.json" for domain in DOMAINS}
SPLIT_PATHS = {domain: f"data/tau2/domains/{domain}/split_tasks.json" for domain in DOMAINS}

EXCLUDED_DOMAINS = ("mock", "banking_knowledge", "telecom-workflow")
EXCLUDED_TASK_SETS = ("telecom_full", "telecom_small")
EXCLUDED_TASK_FILES = (
    "data/tau2/domains/airline/tasks_voice.json",
    "data/tau2/domains/banking_knowledge/tasks_voice.json",
    "data/tau2/domains/mock/tasks_voice.json",
    "data/tau2/domains/retail/tasks_voice.json",
    "data/tau2/domains/telecom/tasks_voice.json",
)
EXCLUDED_MODALITIES = ("voice", "audio-native")
EXCLUDED_SOURCE_PATH_PREFIXES = (
    "data/tau2/results/",
    "web/leaderboard/",
    "tests/",
)
CANDIDATE_EXCLUDED_CATEGORIES = (
    "upstream-checkout-or-mirror",
    "task-and-split-json",
    "domain-databases",
    "results-and-leaderboards",
    "simulator-and-grader-evidence",
)

_SHA1_RE = re.compile(r"[0-9a-f]{40}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_SUITE_LIMITS = StrictJsonLimits(
    max_bytes=2 * 1024 * 1024,
    max_line_chars=2 * 1024 * 1024,
    max_depth=32,
)
_MAX_GIT_OBJECT_BYTES = 32 * 1024 * 1024
_MAX_JSON_NODES = 2_000_000
_MAX_JSON_DEPTH = 96

_REQUIRED_FILE_FIELDS = ("path", "git_mode", "blob_oid", "byte_count", "sha256")
_RECORD_BINDING_FIELDS = (
    "ordinal",
    "domain",
    "task_key_sha256",
    "source_sha256",
)

_TRUE_CLAIMS = (
    "source_objects_pinned",
    "required_source_files_pinned",
    "loader_sources_pinned",
    "loader_order_manifest_reproduced",
    "selection_result_blind",
    "candidate_source_task_allowlist_empty",
    "fixed_exclusions_recorded",
)
_FALSE_CLAIMS = (
    "repository_origin_authenticated",
    "annotated_tag_signature_verified",
    "commit_signature_verified",
    "task_data_redistribution_reviewed",
    "dependency_lock_installed",
    "execution_environment_reproduced",
    "candidate_adapter_implemented",
    "candidate_boundary_runtime_verified",
    "simulator_model_authenticated",
    "evaluator_model_authenticated",
    "network_isolation_verified",
    "filesystem_isolation_verified",
    "candidate_executed",
    "evaluator_executed",
    "external_score_generated",
    "external_usefulness_claimed",
    "claim_ready",
    "self_hash_authenticates_author",
)


class Tau2Error(ValueError):
    """A tau2 source descriptor or local source observation is invalid."""


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
        raise Tau2Error("tau2 evidence is not canonical finite JSON") from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(nested) for key, nested in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(nested) for nested in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(nested) for nested in value]
    return value


@dataclass(frozen=True, slots=True)
class Tau2Suite:
    """One strictly decoded, self-hashed tau2 suite descriptor."""

    document: Mapping[str, Any]
    file_sha256: str | None = None

    @property
    def suite_id(self) -> str:
        return str(self.document["suite_id"])

    @property
    def suite_sha256(self) -> str:
        return str(self.document["suite_sha256"])

    @property
    def task_count(self) -> int:
        return int(self.document["selection"]["task_count"])

    def to_dict(self) -> dict[str, Any]:
        return _thaw_json(self.document)


@dataclass(frozen=True, slots=True)
class VerifiedTau2Source:
    """Manifest-only evidence derived from a verified local source mirror."""

    suite: Tau2Suite
    mirror: VerifiedBareMirror
    document: Mapping[str, Any]

    @property
    def task_count(self) -> int:
        return int(self.document["task_count"])

    @property
    def claim_ready(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return _thaw_json(self.document)


def _object(value: Any, *, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise Tau2Error(f"{label} fields are invalid")
    return value


def _string(
    value: Any,
    *,
    label: str,
    maximum: int = 4096,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise Tau2Error(f"{label} must be a string")
    if len(value) > maximum:
        raise Tau2Error(f"{label} exceeds its character limit")
    return value


def _integer(value: Any, *, label: str, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise Tau2Error(f"{label} must be an integer")
    if maximum is not None and value > maximum:
        raise Tau2Error(f"{label} exceeds its limit")
    return value


def _boolean(value: Any, *, expected: bool, label: str) -> None:
    if value is not expected:
        raise Tau2Error(f"{label} must be {expected!r}")


def _sha1(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA1_RE.fullmatch(value) is None:
        raise Tau2Error(f"{label} must be lowercase Git SHA-1")
    return value


def _sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise Tau2Error(f"{label} must be lowercase SHA-256")
    return value


def _string_array(
    value: Any,
    *,
    label: str,
    expected: tuple[str, ...] | None = None,
    maximum: int = 128,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) > maximum
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise Tau2Error(f"{label} must be a bounded array of strings")
    result = tuple(value)
    if len(set(result)) != len(result):
        raise Tau2Error(f"{label} must not contain duplicates")
    if expected is not None and result != expected:
        raise Tau2Error(f"{label} does not match the frozen policy")
    return result


def _decode_required_files(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(REQUIRED_PATHS):
        raise Tau2Error("suite.source.required_files must cover every frozen path")
    result: list[dict[str, Any]] = []
    paths: list[str] = []
    for index, raw in enumerate(value):
        item = _object(
            raw,
            fields=set(_REQUIRED_FILE_FIELDS),
            label=f"suite.source.required_files[{index}]",
        )
        path = _string(item["path"], label=f"required file {index} path", maximum=512)
        paths.append(path)
        if item["git_mode"] != "100644":
            raise Tau2Error(f"required file {index} must be a regular non-executable blob")
        _sha1(item["blob_oid"], label=f"required file {index} blob OID")
        _integer(
            item["byte_count"],
            label=f"required file {index} byte count",
            minimum=1,
            maximum=_MAX_GIT_OBJECT_BYTES,
        )
        _sha256(item["sha256"], label=f"required file {index} SHA-256")
        result.append(item)
    if tuple(paths) != REQUIRED_PATHS:
        raise Tau2Error("suite.source.required_files path order is not frozen")
    return result


def _decode_selection(value: Any) -> dict[str, Any]:
    selection = _object(
        value,
        fields={
            "policy",
            "domains",
            "split",
            "domain_counts",
            "task_count",
            "result_blind",
            "manifest_serialization",
            "manifest_byte_count",
            "manifest_sha256",
            "ordered_task_key_sha256s_sha256",
            "ordered_source_sha256s_sha256",
            "record_binding_fields",
            "record_bindings",
            "exclusions",
        },
        label="suite.selection",
    )
    if selection["policy"] != "domain-order-then-tasks-json-order-base-membership-v1":
        raise Tau2Error("suite.selection.policy is unsupported")
    _string_array(selection["domains"], label="suite.selection.domains", expected=DOMAINS)
    if selection["split"] != "base":
        raise Tau2Error("suite.selection.split must equal 'base'")
    if selection["domain_counts"] != [list(item) for item in DOMAIN_COUNTS]:
        raise Tau2Error("suite.selection.domain_counts mismatch")
    task_count = _integer(
        selection["task_count"],
        label="suite.selection.task_count",
        minimum=1,
    )
    if task_count != TASK_COUNT:
        raise Tau2Error("suite.selection.task_count mismatch")
    _boolean(selection["result_blind"], expected=True, label="suite.selection.result_blind")
    if selection["manifest_serialization"] != "domain-tab-task-id-lf-final-lf-v1":
        raise Tau2Error("suite.selection manifest serialization is unsupported")
    if selection["manifest_byte_count"] != MANIFEST_BYTE_COUNT:
        raise Tau2Error("suite.selection manifest byte count mismatch")
    if selection["manifest_sha256"] != MANIFEST_SHA256:
        raise Tau2Error("suite.selection manifest SHA-256 mismatch")
    _sha256(
        selection["ordered_task_key_sha256s_sha256"],
        label="ordered task-key SHA-256 aggregate",
    )
    _sha256(
        selection["ordered_source_sha256s_sha256"],
        label="ordered source SHA-256 aggregate",
    )
    _string_array(
        selection["record_binding_fields"],
        label="suite.selection.record_binding_fields",
        expected=_RECORD_BINDING_FIELDS,
    )
    bindings = selection["record_bindings"]
    if not isinstance(bindings, list) or len(bindings) != TASK_COUNT:
        raise Tau2Error("suite.selection.record_bindings must cover all 278 tasks")
    task_key_hashes: list[str] = []
    source_hashes: list[str] = []
    observed_counts = {domain: 0 for domain in DOMAINS}
    for ordinal, binding in enumerate(bindings):
        if not isinstance(binding, list) or len(binding) != 4:
            raise Tau2Error(f"suite.selection.record_bindings[{ordinal}] is invalid")
        if binding[0] != ordinal or isinstance(binding[0], bool):
            raise Tau2Error(f"suite.selection.record_bindings[{ordinal}] ordinal mismatch")
        domain = _string(binding[1], label=f"record binding {ordinal} domain", maximum=16)
        if domain not in DOMAINS:
            raise Tau2Error(f"record binding {ordinal} domain is unsupported")
        task_key_hash = _sha256(
            binding[2],
            label=f"record binding {ordinal} task-key SHA-256",
        )
        source_hash = _sha256(binding[3], label=f"record binding {ordinal} source SHA-256")
        if task_key_hash in task_key_hashes:
            raise Tau2Error("suite.selection.record_bindings contains duplicate task-key hashes")
        task_key_hashes.append(task_key_hash)
        source_hashes.append(source_hash)
        observed_counts[domain] += 1
    if tuple((domain, observed_counts[domain]) for domain in DOMAINS) != DOMAIN_COUNTS:
        raise Tau2Error("suite.selection.record_bindings domain counts mismatch")
    if _canonical_sha256(task_key_hashes) != selection["ordered_task_key_sha256s_sha256"]:
        raise Tau2Error("suite.selection ordered task-key hash aggregate mismatch")
    if _canonical_sha256(source_hashes) != selection["ordered_source_sha256s_sha256"]:
        raise Tau2Error("suite.selection ordered source aggregate mismatch")
    if selection["ordered_task_key_sha256s_sha256"] != ORDERED_TASK_KEY_SHA256S_SHA256:
        raise Tau2Error("suite.selection ordered task-key hashes are not pinned")
    if selection["ordered_source_sha256s_sha256"] != ORDERED_SOURCE_SHA256S_SHA256:
        raise Tau2Error("suite.selection ordered source hashes are not pinned")
    exclusions = _object(
        selection["exclusions"],
        fields={
            "domains",
            "task_sets",
            "task_files",
            "modalities",
            "source_path_prefixes",
            "candidate_excluded_categories",
        },
        label="suite.selection.exclusions",
    )
    _string_array(exclusions["domains"], label="excluded domains", expected=EXCLUDED_DOMAINS)
    _string_array(exclusions["task_sets"], label="excluded task sets", expected=EXCLUDED_TASK_SETS)
    _string_array(
        exclusions["task_files"], label="excluded task files", expected=EXCLUDED_TASK_FILES
    )
    _string_array(
        exclusions["modalities"], label="excluded modalities", expected=EXCLUDED_MODALITIES
    )
    _string_array(
        exclusions["source_path_prefixes"],
        label="excluded source path prefixes",
        expected=EXCLUDED_SOURCE_PATH_PREFIXES,
    )
    _string_array(
        exclusions["candidate_excluded_categories"],
        label="candidate-excluded categories",
        expected=CANDIDATE_EXCLUDED_CATEGORIES,
    )
    return selection


def decode_suite(value: Any) -> Tau2Suite:
    """Strictly decode one source descriptor and verify its canonical self-hash."""

    payload = _object(
        value,
        fields={
            "schema",
            "suite_id",
            "status",
            "source",
            "selection",
            "run_profile",
            "projection",
            "claim_boundary",
            "suite_sha256",
        },
        label="suite",
    )
    if payload["schema"] != SUITE_SCHEMA:
        raise Tau2Error(f"suite.schema must equal {SUITE_SCHEMA!r}")
    suite_id = _string(payload["suite_id"], label="suite.suite_id", maximum=128)
    if _IDENTIFIER_RE.fullmatch(suite_id) is None:
        raise Tau2Error("suite.suite_id is not canonical")
    if suite_id != SUITE_ID:
        raise Tau2Error("suite.suite_id does not match the frozen cohort")
    if payload["status"] != "source-pinned-not-executed":
        raise Tau2Error("suite.status must remain source-pinned-not-executed")

    source = _object(
        payload["source"],
        fields={
            "repository",
            "repository_url",
            "object_format",
            "tag_name",
            "tag_ref",
            "tag_object",
            "commit_object",
            "root_tree_object",
            "required_file_binding_fields",
            "required_files",
            "license_evidence",
        },
        label="suite.source",
    )
    expected_values = {
        "repository": REPOSITORY,
        "repository_url": REPOSITORY_URL,
        "object_format": "sha1",
        "tag_name": TAG_NAME,
        "tag_ref": TAG_REF,
    }
    for name, expected in expected_values.items():
        if source[name] != expected:
            raise Tau2Error(f"suite.source.{name} does not match the frozen source")
    for name, expected_type in (
        ("tag_object", "tag"),
        ("commit_object", "commit"),
        ("root_tree_object", "tree"),
    ):
        spec = _object(
            source[name],
            fields={"type", "oid", "byte_count", "sha256"},
            label=f"suite.source.{name}",
        )
        if spec["type"] != expected_type:
            raise Tau2Error(f"suite.source.{name}.type mismatch")
        _sha1(spec["oid"], label=f"suite.source.{name}.oid")
        _integer(
            spec["byte_count"],
            label=f"suite.source.{name}.byte_count",
            minimum=1,
            maximum=_MAX_GIT_OBJECT_BYTES,
        )
        _sha256(spec["sha256"], label=f"suite.source.{name}.sha256")
        if spec != dict(PINNED_OBJECTS[name]):
            raise Tau2Error(f"suite.source.{name} does not match pinned v1.0.1")
    _string_array(
        source["required_file_binding_fields"],
        label="suite.source.required_file_binding_fields",
        expected=_REQUIRED_FILE_FIELDS,
    )
    required_files = _decode_required_files(source["required_files"])
    observed_file_bindings = tuple(
        (
            item["path"],
            item["git_mode"],
            item["blob_oid"],
            item["byte_count"],
            item["sha256"],
        )
        for item in required_files
    )
    if observed_file_bindings != PINNED_REQUIRED_FILES:
        raise Tau2Error("suite.source.required_files do not match pinned v1.0.1")
    license_evidence = _object(
        source["license_evidence"],
        fields={
            "root_license_declared_spdx",
            "pyproject_declared_license",
            "task_data_coverage_legally_reviewed",
            "redistribution_reviewed",
        },
        label="suite.source.license_evidence",
    )
    if (
        license_evidence["root_license_declared_spdx"] != "MIT"
        or license_evidence["pyproject_declared_license"] != "MIT"
    ):
        raise Tau2Error("suite.source license declarations mismatch")
    _boolean(
        license_evidence["task_data_coverage_legally_reviewed"],
        expected=False,
        label="suite.source task data legal review",
    )
    _boolean(
        license_evidence["redistribution_reviewed"],
        expected=False,
        label="suite.source redistribution review",
    )

    _decode_selection(payload["selection"])

    run_profile = _object(
        payload["run_profile"],
        fields={
            "interaction_mode",
            "run_config_class",
            "task_set_name_policy",
            "task_split_name",
            "telecom_domain",
            "telecom_policy",
            "solo_mode",
            "enforce_communication_protocol",
            "voice_enabled",
            "task_ids",
            "num_tasks",
            "result_blind",
        },
        label="suite.run_profile",
    )
    frozen_profile = {
        "interaction_mode": "half-duplex-text",
        "run_config_class": "tau2.data_model.simulation.TextRunConfig",
        "task_set_name_policy": "same-as-domain",
        "task_split_name": "base",
        "telecom_domain": "telecom",
        "telecom_policy": "manual",
        "solo_mode": False,
        "enforce_communication_protocol": True,
        "voice_enabled": False,
        "task_ids": None,
        "num_tasks": None,
        "result_blind": True,
    }
    if run_profile != frozen_profile:
        raise Tau2Error("suite.run_profile does not match the frozen text profile")

    projection = _object(
        payload["projection"],
        fields={
            "source_task_fields",
            "candidate_source_fields",
            "coordinator_only_fields",
            "simulator_only_fields",
            "environment_only_fields",
            "evaluator_only_fields",
            "candidate_projection",
            "full_task_candidate_visible",
            "candidate_task_id_visible",
        },
        label="suite.projection",
    )
    policies = (
        ("source_task_fields", TASK_FIELDS),
        ("candidate_source_fields", CANDIDATE_SOURCE_FIELDS),
        ("coordinator_only_fields", COORDINATOR_ONLY_FIELDS),
        ("simulator_only_fields", SIMULATOR_ONLY_FIELDS),
        ("environment_only_fields", ENVIRONMENT_ONLY_FIELDS),
        ("evaluator_only_fields", EVALUATOR_ONLY_FIELDS),
    )
    for name, expected in policies:
        _string_array(projection[name], label=f"suite.projection.{name}", expected=expected)
    partition = (
        tuple(projection["candidate_source_fields"])
        + tuple(projection["coordinator_only_fields"])
        + tuple(projection["simulator_only_fields"])
        + tuple(projection["environment_only_fields"])
        + tuple(projection["evaluator_only_fields"])
    )
    if len(partition) != len(TASK_FIELDS) or set(partition) != set(TASK_FIELDS):
        raise Tau2Error("suite.projection does not partition every source task field")
    if projection["candidate_projection"] != "allowlist-empty-source-task-v1":
        raise Tau2Error("suite.projection candidate policy is unsafe")
    _boolean(
        projection["full_task_candidate_visible"],
        expected=False,
        label="suite.projection.full_task_candidate_visible",
    )
    _boolean(
        projection["candidate_task_id_visible"],
        expected=False,
        label="suite.projection.candidate_task_id_visible",
    )

    claims = _object(
        payload["claim_boundary"],
        fields=set(_TRUE_CLAIMS + _FALSE_CLAIMS),
        label="suite.claim_boundary",
    )
    for name in _TRUE_CLAIMS:
        _boolean(claims[name], expected=True, label=f"suite.claim_boundary.{name}")
    for name in _FALSE_CLAIMS:
        _boolean(claims[name], expected=False, label=f"suite.claim_boundary.{name}")

    claimed = _sha256(payload["suite_sha256"], label="suite.suite_sha256")
    unsigned = dict(payload)
    unsigned.pop("suite_sha256")
    if _canonical_sha256(unsigned) != claimed:
        raise Tau2Error("suite.suite_sha256 mismatch")
    return Tau2Suite(document=_freeze_json(payload))


def load_suite(path: str | Path = DEFAULT_SUITE) -> Tau2Suite:
    """Load one bounded descriptor without network access."""

    try:
        document = load_strict_json_file(
            path,
            limits=_SUITE_LIMITS,
            label="tau2 suite descriptor",
            allow_bom=False,
        )
    except StrictJsonError as exc:
        raise Tau2Error(str(exc)) from exc
    suite = decode_suite(document.value)
    return Tau2Suite(
        document=suite.document,
        file_sha256=document.file_sha256,
    )


def _task_id(value: Any, *, label: str) -> str:
    task_id = _string(value, label=label, maximum=4096)
    if any(character in task_id for character in ("\0", "\t", "\r", "\n")):
        raise Tau2Error(f"{label} contains a forbidden delimiter")
    return task_id


def _strict_json_bytes(encoded: bytes, *, label: str) -> Any:
    if not isinstance(encoded, bytes) or not encoded:
        raise Tau2Error(f"{label} must be non-empty bytes")
    if encoded.startswith(b"\xef\xbb\xbf"):
        raise Tau2Error(f"{label} must not contain a BOM")
    try:
        text = encoded.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise Tau2Error(f"{label} is not UTF-8") from exc

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise Tau2Error(f"{label} has duplicate key {key!r}")
            result[key] = value
        return result

    def parse_int(value: str) -> int:
        digits = value[1:] if value.startswith("-") else value
        if len(digits) > 640:
            raise Tau2Error(f"{label} has an oversized integer")
        return int(value)

    def parse_float(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise Tau2Error(f"{label} has a non-finite number")
        return result

    try:
        value = json.loads(
            text,
            object_pairs_hook=pairs_hook,
            parse_int=parse_int,
            parse_float=parse_float,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                Tau2Error(f"{label} has invalid constant {constant}")
            ),
        )
    except Tau2Error:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise Tau2Error(f"{label} is not strict JSON") from exc

    pending: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if nodes > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            raise Tau2Error(f"{label} exceeds structural limits")
        if isinstance(current, dict):
            pending.extend((nested, depth + 1) for nested in current.values())
        elif isinstance(current, list):
            pending.extend((nested, depth + 1) for nested in current)
    return value


def _validate_tasks(value: Any, *, domain: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise Tau2Error(f"{domain} tasks must be a non-empty array")
    common = {
        "id",
        "description",
        "user_scenario",
        "initial_state",
        "evaluation_criteria",
    }
    allowed = {
        "airline": {frozenset(common | {"annotations"})},
        "retail": {frozenset(common), frozenset(common | {"issues"})},
        "telecom": {frozenset(common | {"ticket"})},
    }[domain]
    tasks: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, task in enumerate(value):
        if not isinstance(task, dict) or frozenset(task) not in allowed:
            raise Tau2Error(f"{domain} task {index} fields are not frozen")
        task_id = _task_id(task.get("id"), label=f"{domain} task {index} ID")
        if task_id in ids:
            raise Tau2Error(f"{domain} tasks contain duplicate IDs")
        ids.add(task_id)
        tasks.append(task)
    return tasks


def _validate_split(value: Any, *, domain: str, task_ids: tuple[str, ...]) -> dict[str, list[str]]:
    expected_fields = {"train", "test", "base"}
    if domain == "telecom":
        expected_fields |= {"small", "full"}
    split = _object(value, fields=expected_fields, label=f"{domain} task split")
    known = set(task_ids)
    decoded: dict[str, list[str]] = {}
    for name, raw in split.items():
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise Tau2Error(f"{domain} split {name} must be an array of strings")
        items = [_task_id(item, label=f"{domain} split {name} task ID") for item in raw]
        if len(set(items)) != len(items):
            raise Tau2Error(f"{domain} split {name} contains duplicate IDs")
        if not set(items) <= known:
            raise Tau2Error(f"{domain} split {name} references unknown tasks")
        decoded[name] = items
    if set(decoded["train"]) & set(decoded["test"]):
        raise Tau2Error(f"{domain} train and test splits overlap")
    if decoded["base"] != decoded["train"] + decoded["test"]:
        raise Tau2Error(f"{domain} base split must equal train followed by test")
    if domain in {"airline", "retail"} and set(decoded["base"]) != known:
        raise Tau2Error(f"{domain} base split does not cover every task")
    if domain == "telecom" and set(decoded["full"]) != known:
        raise Tau2Error("telecom full split does not cover every task")
    return decoded


def _derive_selection(file_bytes: Mapping[str, bytes]) -> dict[str, Any]:
    bindings: list[list[Any]] = []
    domain_counts: list[list[Any]] = []
    manifest_rows: list[bytes] = []
    for domain in DOMAINS:
        tasks = _validate_tasks(
            _strict_json_bytes(file_bytes[TASK_PATHS[domain]], label=f"{domain} tasks"),
            domain=domain,
        )
        task_ids = tuple(str(task["id"]) for task in tasks)
        split = _validate_split(
            _strict_json_bytes(file_bytes[SPLIT_PATHS[domain]], label=f"{domain} split"),
            domain=domain,
            task_ids=task_ids,
        )
        base_ids = set(split["base"])
        selected = [task for task in tasks if str(task["id"]) in base_ids]
        domain_counts.append([domain, len(selected)])
        for task in selected:
            task_id = str(task["id"])
            raw_task_key = f"{domain}\t{task_id}".encode("utf-8", errors="strict")
            task_key_sha256 = hashlib.sha256(raw_task_key).hexdigest()
            manifest_rows.append(raw_task_key + b"\n")
            bindings.append(
                [
                    len(bindings),
                    domain,
                    task_key_sha256,
                    _canonical_sha256(task),
                ]
            )
    task_key_hashes = [str(binding[2]) for binding in bindings]
    source_hashes = [str(binding[3]) for binding in bindings]
    manifest = b"".join(manifest_rows)
    return {
        "policy": "domain-order-then-tasks-json-order-base-membership-v1",
        "domains": list(DOMAINS),
        "split": "base",
        "domain_counts": domain_counts,
        "task_count": len(bindings),
        "result_blind": True,
        "manifest_serialization": "domain-tab-task-id-lf-final-lf-v1",
        "manifest_byte_count": len(manifest),
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "ordered_task_key_sha256s_sha256": _canonical_sha256(task_key_hashes),
        "ordered_source_sha256s_sha256": _canonical_sha256(source_hashes),
        "record_binding_fields": list(_RECORD_BINDING_FIELDS),
        "record_bindings": bindings,
        "exclusions": {
            "domains": list(EXCLUDED_DOMAINS),
            "task_sets": list(EXCLUDED_TASK_SETS),
            "task_files": list(EXCLUDED_TASK_FILES),
            "modalities": list(EXCLUDED_MODALITIES),
            "source_path_prefixes": list(EXCLUDED_SOURCE_PATH_PREFIXES),
            "candidate_excluded_categories": list(CANDIDATE_EXCLUDED_CATEGORIES),
        },
    }


def _git_environment() -> dict[str, str]:
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "LANG": "C",
    }
    for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


class _GitReader:
    def __init__(self, mirror: VerifiedBareMirror) -> None:
        self.mirror = mirror
        self.tree_cache: dict[str, bytes] = {}

    def run(self, arguments: Sequence[str], *, maximum: int) -> bytes:
        if not 1 <= maximum <= 64 * 1024 * 1024:
            raise Tau2Error("Git output limit is invalid")
        result = run_literal_argv(
            [
                str(self.mirror.git_executable),
                "--no-pager",
                "--no-replace-objects",
                f"--git-dir={self.mirror.path}",
                *arguments,
            ],
            cwd=self.mirror.path.parent,
            environment=_git_environment(),
            limits=LiteralProcessLimits(
                timeout_seconds=self.mirror.limits.timeout_seconds,
                max_stdout_bytes=maximum,
                max_stderr_bytes=min(self.mirror.limits.max_worker_stderr_bytes, 1024 * 1024),
            ),
        )
        if (
            not result.process_succeeded
            or not result.stdout.complete
            or not result.stderr.complete
            or result.stderr.observed_bytes != 0
            or result.stdout.observed_bytes != result.stdout.captured_bytes
        ):
            raise Tau2Error("bounded Git command failed closed")
        return result.stdout.prefix

    def object(self, kind: str, oid: str, *, maximum: int) -> bytes:
        _sha1(oid, label=f"{kind} object OID")
        if kind not in {"tag", "commit", "tree", "blob"}:
            raise Tau2Error("Git object type is unsupported")
        body = self.run(("cat-file", kind, oid), maximum=maximum)
        digest = hashlib.sha1(usedforsecurity=False)
        digest.update(f"{kind} {len(body)}\0".encode("ascii"))
        digest.update(body)
        if digest.hexdigest() != oid:
            raise Tau2Error(f"{kind} object identity mismatch")
        return body

    def tree(self, oid: str) -> bytes:
        if oid not in self.tree_cache:
            self.tree_cache[oid] = self.object("tree", oid, maximum=_MAX_GIT_OBJECT_BYTES)
        return self.tree_cache[oid]


def _object_headers(body: bytes, *, label: str) -> list[bytes]:
    header_block, separator, _message = body.partition(b"\n\n")
    if not separator or not header_block:
        raise Tau2Error(f"{label} headers are malformed")
    return header_block.split(b"\n")


def _single_header(headers: list[bytes], name: bytes, *, label: str) -> bytes:
    prefix = name + b" "
    values = [line[len(prefix) :] for line in headers if line.startswith(prefix)]
    if len(values) != 1:
        raise Tau2Error(f"{label} must contain one {name.decode('ascii')} header")
    return values[0]


def _parse_tag(body: bytes) -> tuple[str, str]:
    headers = _object_headers(body, label="annotated tag")
    try:
        target = _single_header(headers, b"object", label="annotated tag").decode("ascii")
        kind = _single_header(headers, b"type", label="annotated tag").decode("ascii")
        name = _single_header(headers, b"tag", label="annotated tag").decode("utf-8")
    except UnicodeError as exc:
        raise Tau2Error("annotated tag headers are not canonical text") from exc
    _sha1(target, label="annotated tag target")
    if kind != "commit" or name != TAG_NAME:
        raise Tau2Error("annotated tag target or name mismatch")
    return target, name


def _parse_commit(body: bytes) -> str:
    headers = _object_headers(body, label="commit")
    try:
        tree = _single_header(headers, b"tree", label="commit").decode("ascii")
    except UnicodeError as exc:
        raise Tau2Error("commit tree header is not ASCII") from exc
    return _sha1(tree, label="commit root tree")


def _parse_tree(body: bytes) -> dict[bytes, tuple[str, str]]:
    result: dict[bytes, tuple[str, str]] = {}
    position = 0
    while position < len(body):
        space = body.find(b" ", position)
        nul = body.find(b"\0", space + 1) if space >= 0 else -1
        if space <= position or nul <= space + 1 or nul + 21 > len(body):
            raise Tau2Error("Git tree object framing is malformed")
        mode_raw = body[position:space]
        name = body[space + 1 : nul]
        oid = body[nul + 1 : nul + 21].hex()
        try:
            mode = mode_raw.decode("ascii")
            name.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise Tau2Error("Git tree entry is not canonical text") from exc
        if name in result or b"/" in name or name in {b".", b".."}:
            raise Tau2Error("Git tree entries are ambiguous")
        result[name] = (mode, oid)
        position = nul + 21
    if not result:
        raise Tau2Error("Git tree object is empty")
    return result


def _lookup_blob(reader: _GitReader, root_oid: str, path: str) -> tuple[str, str]:
    components = path.encode("utf-8", errors="strict").split(b"/")
    current = root_oid
    for index, component in enumerate(components):
        entries = _parse_tree(reader.tree(current))
        if component not in entries:
            raise Tau2Error(f"required path is absent from the pinned tree: {path}")
        mode, oid = entries[component]
        if index + 1 < len(components):
            if mode not in {"40000", "040000"}:
                raise Tau2Error(f"required path traverses a non-tree entry: {path}")
            current = oid
        else:
            return mode, oid
    raise Tau2Error("required path lookup failed")


def _compare_object(body: bytes, spec: Mapping[str, Any], *, label: str) -> None:
    if len(body) != spec["byte_count"]:
        raise Tau2Error(f"{label} byte count mismatch")
    if hashlib.sha256(body).hexdigest() != spec["sha256"]:
        raise Tau2Error(f"{label} SHA-256 mismatch")


def _source_verification_document(
    suite: Tau2Suite,
    mirror: VerifiedBareMirror,
    selection: dict[str, Any],
) -> dict[str, Any]:
    source = suite.document["source"]
    document: dict[str, Any] = {
        "schema": SOURCE_VERIFICATION_SCHEMA,
        "status": "source-verified-not-executed",
        "suite_id": suite.suite_id,
        "suite_sha256": suite.suite_sha256,
        "mirror_sha256": mirror.mirror_sha256,
        "tag_oid": source["tag_object"]["oid"],
        "commit_oid": source["commit_object"]["oid"],
        "root_tree_oid": source["root_tree_object"]["oid"],
        "task_count": selection["task_count"],
        "domain_counts": selection["domain_counts"],
        "manifest_byte_count": selection["manifest_byte_count"],
        "manifest_sha256": selection["manifest_sha256"],
        "ordered_task_key_sha256s_sha256": selection["ordered_task_key_sha256s_sha256"],
        "ordered_source_sha256s_sha256": selection["ordered_source_sha256s_sha256"],
        "evidence_state": {
            "local_bare_mirror_verified": True,
            "origin_url_matches_repository": True,
            "repository_origin_authenticated": False,
            "annotated_tag_chain_verified": True,
            "required_source_files_verified": True,
            "loader_order_manifest_reproduced": True,
            "full_tasks_exposed": False,
            "candidate_executed": False,
            "evaluator_executed": False,
            "external_score_generated": False,
            "claim_ready": False,
            "self_hash_authenticates_author": False,
        },
    }
    if suite.file_sha256 is not None:
        document["descriptor_file_sha256"] = suite.file_sha256
    document["verification_sha256"] = _canonical_sha256(document)
    return document


def verify_local_source(
    mirror_path: str | Path,
    git_executable: str | Path,
    *,
    suite: Tau2Suite | None = None,
    suite_path: str | Path | None = None,
    limits: RepositoryPreparationLimits | None = None,
) -> VerifiedTau2Source:
    """Reproduce the frozen selection from one caller-provided bare mirror."""

    if suite is not None and suite_path is not None:
        raise Tau2Error("suite and suite_path are mutually exclusive")
    if suite_path is not None:
        selected_suite = load_suite(suite_path)
    elif suite is None:
        selected_suite = load_suite()
    else:
        if not isinstance(suite, Tau2Suite):
            raise Tau2Error("suite must be a Tau2Suite")
        # Re-decode the document while intentionally discarding runtime file
        # evidence. Only load_suite can attach a hash observed from an opened
        # descriptor file.
        selected_suite = decode_suite(suite.to_dict())
    selected_limits = RepositoryPreparationLimits() if limits is None else limits
    try:
        mirror = verify_local_bare_mirror(
            mirror_path,
            REPOSITORY,
            git_executable,
            limits=selected_limits,
        )
    except RepositoryPreparationError as exc:
        raise Tau2Error(str(exc)) from exc
    reader = _GitReader(mirror)
    source = selected_suite.document["source"]
    expected_ref = f"{source['tag_object']['oid']}\n".encode("ascii")
    observed_ref = reader.run(("show-ref", "--hash", "--verify", TAG_REF), maximum=128)
    if observed_ref != expected_ref:
        raise Tau2Error("annotated tag ref does not match the descriptor")

    tag_body = reader.object("tag", source["tag_object"]["oid"], maximum=1024 * 1024)
    _compare_object(tag_body, source["tag_object"], label="annotated tag object")
    commit_oid, _tag_name = _parse_tag(tag_body)
    if commit_oid != source["commit_object"]["oid"]:
        raise Tau2Error("annotated tag commit target mismatch")
    commit_body = reader.object("commit", commit_oid, maximum=16 * 1024 * 1024)
    _compare_object(commit_body, source["commit_object"], label="commit object")
    root_oid = _parse_commit(commit_body)
    if root_oid != source["root_tree_object"]["oid"]:
        raise Tau2Error("commit root tree mismatch")
    root_body = reader.tree(root_oid)
    _compare_object(root_body, source["root_tree_object"], label="root tree object")

    file_bytes: dict[str, bytes] = {}
    for binding in source["required_files"]:
        path = str(binding["path"])
        mode, oid = _lookup_blob(reader, root_oid, path)
        if mode != binding["git_mode"] or oid != binding["blob_oid"]:
            raise Tau2Error(f"required path binding mismatch: {path}")
        body = reader.object(
            "blob",
            oid,
            maximum=min(_MAX_GIT_OBJECT_BYTES, int(binding["byte_count"]) + 1),
        )
        _compare_object(body, binding, label=f"required file {path}")
        file_bytes[path] = body

    try:
        project = tomllib.loads(file_bytes["pyproject.toml"].decode("utf-8", errors="strict"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise Tau2Error("pinned pyproject.toml is invalid") from exc
    project_metadata = project.get("project")
    if not isinstance(project_metadata, dict) or {
        "name": project_metadata.get("name"),
        "version": project_metadata.get("version"),
        "license": project_metadata.get("license"),
    } != {"name": "tau2", "version": "1.0.1", "license": "MIT"}:
        raise Tau2Error("pinned pyproject metadata mismatch")

    selection = _derive_selection(file_bytes)
    expected_selection = _thaw_json(selected_suite.document["selection"])
    if _canonical_json_bytes(selection) != _canonical_json_bytes(expected_selection):
        raise Tau2Error("local source selection does not match the descriptor")

    final_ref = reader.run(("show-ref", "--hash", "--verify", TAG_REF), maximum=128)
    if final_ref != observed_ref:
        raise Tau2Error("annotated tag ref changed during verification")
    try:
        final_mirror = verify_local_bare_mirror(
            mirror.path,
            REPOSITORY,
            mirror.git_executable,
            limits=selected_limits,
        )
    except RepositoryPreparationError as exc:
        raise Tau2Error(str(exc)) from exc
    if _canonical_json_bytes(final_mirror.to_dict()) != _canonical_json_bytes(mirror.to_dict()):
        raise Tau2Error("bare mirror evidence changed during verification")

    document = _source_verification_document(selected_suite, final_mirror, selection)
    return VerifiedTau2Source(
        suite=selected_suite,
        mirror=final_mirror,
        document=_freeze_json(document),
    )


def verify_suite(path: str | Path = DEFAULT_SUITE) -> dict[str, Any]:
    """Return a non-execution summary for one valid descriptor."""

    suite = load_suite(path)
    selection = suite.document["selection"]
    claims = suite.document["claim_boundary"]
    return {
        "schema": SOURCE_VERIFICATION_SCHEMA,
        "operation": "verify-suite",
        "status": "source-pinned-not-executed",
        "suite_id": suite.suite_id,
        "suite_sha256": suite.suite_sha256,
        "descriptor_file_sha256": suite.file_sha256,
        "task_count": suite.task_count,
        "domain_counts": _thaw_json(selection["domain_counts"]),
        "manifest_sha256": selection["manifest_sha256"],
        "candidate_source_task_allowlist_empty": claims["candidate_source_task_allowlist_empty"],
        "candidate_executed": claims["candidate_executed"],
        "evaluator_executed": claims["evaluator_executed"],
        "external_score_generated": claims["external_score_generated"],
        "claim_ready": claims["claim_ready"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    subparsers = parser.add_subparsers(dest="operation")
    subparsers.add_parser("verify-suite", help="verify only the committed descriptor")
    source = subparsers.add_parser("verify-source", help="verify a local bare source mirror")
    source.add_argument("--mirror", type=Path, required=True)
    source.add_argument("--git-executable", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        operation = args.operation or "verify-suite"
        if operation == "verify-suite":
            summary = verify_suite(args.suite)
        else:
            verified = verify_local_source(
                args.mirror,
                args.git_executable,
                suite_path=args.suite,
            )
            summary = verified.to_dict()
    except (OSError, Tau2Error, ValueError) as exc:
        sys.stderr.write(f"tau2 source verification: {exc}\n")
        return 1
    sys.stdout.buffer.write(_canonical_json_bytes(summary) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
