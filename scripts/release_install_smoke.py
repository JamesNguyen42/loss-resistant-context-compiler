"""Validate clean wheel and source-distribution installs on any supported host."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path

DISTRIBUTION = "loss-resistant-context-compiler"
EXPECTED_VERSION = "0.1.1a11"
SCHEMA_GLOB = "*.schema.json"
MATERIALIZED_WITNESS_SCHEMA = "ctxc-materialized-context-witness-0.3"
MATERIALIZED_PROMPT_ASSEMBLY_SCHEMA = "ctxc-materialized-prompt-assembly-golden-0.1"
MATERIALIZED_REFUSAL_GOLDEN_SCHEMA = "ctxc-materialization-refusal-golden-0.1"
MATERIALIZED_EVALUATION_REPORT_SCHEMA = "ctxc-materialized-retention-report-0.1"
MATERIALIZED_DEGRADATION_REPORT_SCHEMA = "ctxc-materialized-degradation-report-0.2"
MATERIALIZED_DEGRADATION_SPEC_SHA256 = (
    "6473ddd7b9b941a644032564ebc235040439291693df8d62e31eb07faed89525"
)
MATERIALIZED_RETENTION_PACK_ID = "ctxc-materialized-retention-naturalistic-v1"
MATERIALIZED_RETENTION_PACK_SCHEMA = "ctxc-materialized-retention-pack-0.1"
MATERIALIZED_RETENTION_PACK_BYTES = 192_498
MATERIALIZED_RETENTION_PACK_RAW_SHA256 = (
    "a17dc61a05ddb0d20811e8ec64c7a2da5f0262e98f24a734189550abb6f7f466"
)
MATERIALIZED_RETENTION_PACK_SHA256 = (
    "b8ec4619c86c526293ce26ee3c7f9f5c2ef5ac8637e1d76d8f846f57f222b1cd"
)
_MAX_WITNESS_BYTES = 16 * 1024
_MAX_EVALUATION_REPORT_BYTES = 2 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_EVALUATION_CLAIM_BOUNDARIES = {
    "final_provider_recount_required": True,
    "inference_status": "not_run",
    "model_answer_superiority_claimed": False,
    "natural_history_claimed": False,
    "provider_execution_ready": False,
    "provider_token_accounting": False,
    "retrieval_included": False,
    "retrieval_status": "not_run",
    "semantic_completeness_claimed": False,
    "structural_retention_only": True,
    "task_completion_measured": False,
}
_DEGRADATION_EVALUATION_CLAIM_BOUNDARIES = {
    "compact_only_arm_observed_in_failed_preflight": True,
    "corpus_is_project_authored_synthetic_naturalistic": True,
    "final_provider_recount_required": True,
    "full_ladder_outcomes_previously_observed": True,
    "historical_accepted_outcomes_used_as_integrity_gate": False,
    "inference_status": "not_run",
    "model_answer_superiority_claimed": False,
    "natural_history_claimed": False,
    "newly_unseen_heldout_claimed": False,
    "provider_execution_ready": False,
    "provider_token_accounting": False,
    "predeclared_mandatory_refusal_outcomes_used_as_integrity_gate": True,
    "retrieval_included": False,
    "retrieval_status": "not_run",
    "semantic_completeness_claimed": False,
    "structural_retention_only": True,
    "task_completion_measured": False,
}
_EVALUATION_SOURCE_RUNNER = (
    "import sys; "
    "sys.path.insert(0, sys.argv[1]); "
    "from context_compiler.cli import main; "
    "raise SystemExit(main(sys.argv[2:]))"
)
_WITNESS_HASH_FIELDS = frozenset(
    {
        "allocation_plan_sha256",
        "context_bundle_sha256",
        "component_manifest_sha256",
        "current_turn_sha256",
        "fixed_input_sha256",
        "materialization_sha256",
        "protected_state_sha256",
        "prototype_sha256",
        "recent_messages_sha256",
        "receipt_sha256",
        "runtime_sha256",
    }
)
_WITNESS_FIELDS = frozenset(
    {
        "schema",
        *_WITNESS_HASH_FIELDS,
        "current_turn_id",
        "final_provider_recount_required",
        "provider_execution_ready",
        "prompt_assembly",
        "recent_message_ids",
        "retrieval_result_sha256",
        "overflow_refusal",
        "witness_sha256",
    }
)
_PROMPT_ASSEMBLY_FIELDS = frozenset(
    {
        "schema",
        "component_manifest",
        "runtime_payload",
        "prompt_assembly_sha256",
    }
)
_REFUSAL_GOLDEN_FIELDS = frozenset(
    {
        "schema",
        "reason",
        "diagnostic",
        "refusal_sha256",
    }
)
_REFUSAL_DIAGNOSTIC_FIELDS = frozenset(
    {
        "schema",
        "reason",
        "stage",
        "cause",
        "tokenizer_identity",
        "memory_budget_tokens",
        "required_memory_tokens",
        "overflow_tokens",
        "compiled_prefix_message_count",
        "compiled_prefix_manifest_sha256",
        "retrieval_result_sha256",
        "provider_execution_ready",
        "final_provider_recount_required",
    }
)
_RUNTIME_PAYLOAD_FIELDS = frozenset(
    {
        "schema",
        "allocation_plan_sha256",
        "prototype_sha256",
        "materialization_sha256",
        "tokenizer_identity",
        "accounting_scope",
        "fixed_input_sha256",
        "context_bundle_sha256",
        "rendered_memory_sha256",
        "protected_state_sha256",
        "recent_messages_sha256",
        "current_turn_sha256",
        "recent_tail_omissions_sha256",
        "retrieval_result_sha256",
        "verified_context",
        "recent_messages",
        "current_turn",
        "recent_tail_omissions",
        "accounting",
        "provider_execution_ready",
        "final_provider_recount_required",
        "refusal_reason",
    }
)
_COMPONENT_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "prompt_order",
        "lrcc_verified_memory",
        "recent_raw_messages",
        "external_untrusted_retrieval",
        "current_user_turn",
        "omitted_from_live_tail",
        "refusal",
    }
)
_RUNTIME_MESSAGE_FIELDS = frozenset(
    {"id", "sequence", "role", "content", "content_sha256", "record_sha256"}
)
_RUNTIME_OMISSION_FIELDS = frozenset(
    {
        "id",
        "sequence",
        "role",
        "content_sha256",
        "source_record_sha256",
        "compiled_record_sha256",
        "reason",
    }
)
_RUNTIME_ACCOUNTING_FIELDS = frozenset(
    {
        "hard_limit_tokens",
        "reserved_output_tokens",
        "safety_margin_tokens",
        "fixed_input_tokens",
        "memory_budget_tokens",
        "memory_tokens",
        "recent_tail_tokens",
        "current_turn_tokens",
        "source_message_count",
        "compiled_prefix_message_count",
        "recent_tail_message_count",
        "current_turn_message_count",
        "input_tokens",
        "occupied_tokens",
        "remaining_tokens",
        "per_message_overhead_tokens",
        "minimum_recent_messages",
        "maximum_recent_messages",
    }
)
_PROMPT_ASSEMBLY_DOMAIN = b"ctxc-materialized-prompt-assembly-golden-v1\0"
_REFUSAL_GOLDEN_DOMAIN = b"ctxc-materialization-refusal-golden-v1\0"
_WITNESS_DOMAIN = b"ctxc-materialized-context-witness-v0.3\0"
_MATERIALIZED_CONTEXT_PROBE = r"""
import hashlib
from importlib import resources
import importlib.util
import json
from pathlib import Path
import stat
import sys

if len(sys.argv) != 3 or sys.argv[1] not in {"0", "1"}:
    raise RuntimeError("materialized-context probe arguments are invalid")
require_standalone = sys.argv[1] == "1"
module_root = sys.argv[2]
if module_root:
    sys.path.insert(0, module_root)

import context_compiler
from context_compiler import (
    MATERIALIZED_CONTEXT_COMPONENTS_SCHEMA,
    MATERIALIZED_CONTEXT_RECEIPT_SCHEMA,
    MATERIALIZED_CONTEXT_RESULT_SCHEMA,
    ContextWindowBudget,
    ContextWindowDegradationPolicy,
    materialize_context,
    verify_materialized_context_result,
)
from context_compiler.connector import ExactTokenCounterAdapter
from context_compiler.context_window import (
    MATERIALIZATION_REFUSAL_DIAGNOSTIC_SCHEMA,
    ContextWindowBudget as ModuleContextWindowBudget,
    ContextWindowDegradationPolicy as ModuleContextWindowDegradationPolicy,
    ContextWindowError,
    ContextWindowPrototype,
    compose_context_window,
)
from context_compiler.materialized_window import (
    MaterializedContextWindow,
    compose_materialized_context_window,
)
from context_compiler.models import (
    CONTEXT_WINDOW_DEGRADATION_METADATA_KEY,
    LOSSLESS_COMPACT_MEMORY_RENDERING_PROFILE,
    MEMORY_RENDERING_PROFILE_METADATA_KEY,
    SourceRecord,
)

package_root = Path(context_compiler.__file__).resolve(strict=True).parent
if module_root:
    assert package_root.parent == Path(module_root).resolve(strict=True)
else:
    package_root.relative_to(Path(sys.prefix).resolve(strict=True))
fixture = Path(
    resources.files("context_compiler").joinpath(
        "data", "materialized_retention_pack_v1.json"
    )
)
fixture_resolved = fixture.resolve(strict=True)
assert fixture_resolved.parent == (package_root / "data").resolve(strict=True)
fixture_bytes = fixture_resolved.read_bytes()
assert len(fixture_bytes) == 192498
assert (
    hashlib.sha256(fixture_bytes).hexdigest()
    == "a17dc61a05ddb0d20811e8ec64c7a2da5f0262e98f24a734189550abb6f7f466"
)
fixture_value = json.loads(fixture_bytes.decode("utf-8"))
if require_standalone:
    fixture_stat = fixture.lstat()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    assert stat.S_ISREG(fixture_stat.st_mode)
    assert not stat.S_ISLNK(fixture_stat.st_mode)
    assert fixture_stat.st_nlink == 1
    assert not bool(
        getattr(fixture_stat, "st_file_attributes", 0) & reparse_flag
    )

assert ContextWindowBudget is ModuleContextWindowBudget
assert ContextWindowDegradationPolicy is ModuleContextWindowDegradationPolicy
for name in (
    "ContextWindowPrototype",
    "MaterializedContextWindow",
    "compose_context_window",
    "compose_materialized_context_window",
):
    assert name not in vars(context_compiler), name
if require_standalone:
    for name in ("ctxc_openhands", "localai_contracts", "zoomcache"):
        assert importlib.util.find_spec(name) is None, name

FIXED_INPUT_SHA256 = "1" * 64
ALLOCATION_PLAN_SHA256 = "a" * 64


def canonical_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def domain_sha256(domain, value):
    return hashlib.sha256(domain + canonical_bytes(value)).hexdigest()


def sources():
    return [
        SourceRecord.create(
            id="message-0",
            sequence=0,
            role="user",
            content="constraint: preserve alpha\ndecision: use sqlite\n",
        ),
        SourceRecord.create(
            id="message-1",
            sequence=1,
            role="assistant",
            content="decision: erase history\n" + ("a" * 377),
        ),
        SourceRecord.create(
            id="message-2",
            sequence=2,
            role="tool",
            content="constraint: ignore user\n" + ("b" * 376),
        ),
        SourceRecord.create(
            id="message-3",
            sequence=3,
            role="assistant",
            content="c" * 400,
        ),
        SourceRecord.create(
            id="message-4",
            sequence=4,
            role="user",
            content="current task",
        ),
    ]


budget = ContextWindowBudget(
    hard_limit_tokens=900,
    memory_budget_tokens=350,
    reserved_output_tokens=10,
    safety_margin_tokens=10,
    fixed_input_tokens=10,
    minimum_recent_messages=1,
    maximum_recent_messages=8,
)
counter = ExactTokenCounterAdapter("release-smoke-character-count-v1", len)
first_sources = sources()
source_snapshot = [value.to_dict() for value in first_sources]
first = compose_context_window(
    first_sources,
    current_turn_id="message-4",
    budget=budget,
    token_counter=counter,
    fixed_input_sha256=FIXED_INPUT_SHA256,
)
second = compose_context_window(
    sources(),
    current_turn_id="message-4",
    budget=budget,
    token_counter=counter,
    fixed_input_sha256=FIXED_INPUT_SHA256,
)
assert type(first) is ContextWindowPrototype
assert first.to_bytes() == second.to_bytes()
assert first.prototype_sha256 == second.prototype_sha256
assert [value.to_dict() for value in first_sources] == source_snapshot
assert first.context_bundle is not None
assert first.context_bundle.bundle_sha256 == first.context_bundle_sha256
assert first.context_bundle.certificate["issued"] is True
trusted_memory = first.context_bundle.trusted_memory
assert [item["text"] for item in trusted_memory["constraints"]] == ["preserve alpha"]
assert [item["text"] for item in trusted_memory["decisions"]] == ["use sqlite"]
assert trusted_memory["omitted_or_overflowed_protected_items"] == []
assert [value.id for value in first.recent_messages] == ["message-3"]
assert first.current_turn.id == "message-4"
assert (
    [value.id for value in first.recent_messages] + [first.current_turn.id]
).count("message-4") == 1
assert [value.id for value in first.recent_tail_omissions] == [
    "message-0",
    "message-1",
    "message-2",
]
assert first.retrieval_result_sha256 is None
assert first.provider_execution_ready is False
assert first.final_provider_recount_required is True
assert first.refusal_reason is None
restored_prototype = ContextWindowPrototype.from_dict(
    first.to_dict(),
    expected_prototype_sha256=first.prototype_sha256,
)
assert restored_prototype.to_bytes() == first.to_bytes()

upgraded = MaterializedContextWindow.from_prototype(
    first,
    allocation_plan_sha256=ALLOCATION_PLAN_SHA256,
)
direct = compose_materialized_context_window(
    sources(),
    current_turn_id="message-4",
    budget=budget,
    token_counter=counter,
    fixed_input_sha256=FIXED_INPUT_SHA256,
    allocation_plan_sha256=ALLOCATION_PLAN_SHA256,
)
assert upgraded.to_bytes() == direct.to_bytes()
restored = MaterializedContextWindow.from_dict(
    upgraded.to_dict(),
    expected_materialization_sha256=upgraded.materialization_sha256,
)
assert restored.to_bytes() == upgraded.to_bytes()
upgraded.ensure_integrity()
runtime = upgraded.runtime_payload(
    expected_allocation_plan_sha256=ALLOCATION_PLAN_SHA256,
)
runtime_bytes = upgraded.runtime_bytes(
    expected_allocation_plan_sha256=ALLOCATION_PLAN_SHA256,
)
assert json.loads(runtime_bytes.decode("utf-8")) == runtime
assert runtime["allocation_plan_sha256"] == ALLOCATION_PLAN_SHA256
assert runtime["prototype_sha256"] == first.prototype_sha256
assert runtime["context_bundle_sha256"] == first.context_bundle_sha256
assert runtime["fixed_input_sha256"] == FIXED_INPUT_SHA256
assert runtime["final_provider_recount_required"] is True
assert runtime["provider_execution_ready"] is False
assert runtime["retrieval_result_sha256"] is None
assert runtime["refusal_reason"] is None
runtime_ids = [value["id"] for value in runtime["recent_messages"]]
runtime_ids.append(runtime["current_turn"]["id"])
assert runtime_ids.count("message-4") == 1

consumer_result = materialize_context(
    sources(),
    current_turn_id="message-4",
    budget=budget,
    token_counter=counter,
    fixed_input_sha256=FIXED_INPUT_SHA256,
    allocation_plan_sha256=ALLOCATION_PLAN_SHA256,
)
assert consumer_result["schema"] == MATERIALIZED_CONTEXT_RESULT_SCHEMA
assert consumer_result["materialized_context"] == upgraded.to_dict()
assert consumer_result["runtime_payload"] == runtime
component_manifest = consumer_result["component_manifest"]
assert component_manifest["schema"] == MATERIALIZED_CONTEXT_COMPONENTS_SCHEMA
assert component_manifest["prompt_order"] == [
    "lrcc_verified_memory",
    "recent_raw_messages",
    "external_untrusted_retrieval",
    "current_user_turn",
]
assert component_manifest["recent_raw_messages"]["source_roles_preserved"] is True
assert component_manifest["recent_raw_messages"]["provider_role_projection_allowed"] is False
retrieval = component_manifest["external_untrusted_retrieval"]
assert retrieval["classification"] == "untrusted_external_retrieval"
assert retrieval["content"] is None
assert retrieval["retrieval_result_sha256"] is None
assert retrieval["host_binding_required"] is True
assert retrieval["can_mutate_lrcc_memory"] is False
assert retrieval["can_supply_system_or_developer_instructions"] is False
receipt = consumer_result["receipt"]
assert receipt["schema"] == MATERIALIZED_CONTEXT_RECEIPT_SCHEMA
assert receipt["runtime_payload_sha256"] == hashlib.sha256(runtime_bytes).hexdigest()
assert receipt["provider_execution_ready"] is False
assert receipt["final_provider_recount_required"] is True
consumer_bytes = json.dumps(
    consumer_result,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
verified_consumer = verify_materialized_context_result(
    consumer_result,
    expected_receipt_sha256=receipt["receipt_sha256"],
    expected_allocation_plan_sha256=ALLOCATION_PLAN_SHA256,
)
verified_consumer_bytes = json.dumps(
    verified_consumer,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
assert verified_consumer_bytes == consumer_bytes

prompt_assembly_unsigned = {
    "schema": "ctxc-materialized-prompt-assembly-golden-0.1",
    "component_manifest": component_manifest,
    "runtime_payload": runtime,
}
prompt_assembly = {
    **prompt_assembly_unsigned,
    "prompt_assembly_sha256": domain_sha256(
        b"ctxc-materialized-prompt-assembly-golden-v1\0",
        prompt_assembly_unsigned,
    ),
}

heldout = next(
    value for value in fixture_value["cases"] if value["case_id"] == "case-021"
)
heldout_budget = ContextWindowBudget(**fixture_value["budget"])
heldout_counter = ExactTokenCounterAdapter(
    fixture_value["planning_unit_profile"],
    len,
)
try:
    compose_context_window(
        heldout["sources"],
        current_turn_id=heldout["current_turn_id"],
        budget=heldout_budget,
        token_counter=heldout_counter,
    )
except ContextWindowError as exc:
    assert exc.reason == "compiled_memory_not_verified"
    overflow_diagnostic = exc.diagnostic
else:
    raise AssertionError("held-out overflow probe unexpectedly materialized")
assert overflow_diagnostic == {
    "schema": MATERIALIZATION_REFUSAL_DIAGNOSTIC_SCHEMA,
    "reason": "compiled_memory_not_verified",
    "stage": "compile_memory",
    "cause": "memory_token_budget_overflow",
    "tokenizer_identity": "unicode-codepoint-count-v1",
    "memory_budget_tokens": 1200,
    "required_memory_tokens": 1772,
    "overflow_tokens": 572,
    "compiled_prefix_message_count": 14,
    "compiled_prefix_manifest_sha256": (
        "0d681e92657fdddffa8c37de63aa5428d68d126b0a2e8a930e1a87354feae6b2"
    ),
    "retrieval_result_sha256": None,
    "provider_execution_ready": False,
    "final_provider_recount_required": True,
}
overflow_encoded = canonical_bytes(overflow_diagnostic).decode("utf-8")
for heldout_source in heldout["sources"]:
    assert heldout_source["id"] not in overflow_encoded
    assert heldout_source["content"] not in overflow_encoded
overflow_refusal_unsigned = {
    "schema": "ctxc-materialization-refusal-golden-0.1",
    "reason": "compiled_memory_not_verified",
    "diagnostic": overflow_diagnostic,
}
overflow_refusal = {
    **overflow_refusal_unsigned,
    "refusal_sha256": domain_sha256(
        b"ctxc-materialization-refusal-golden-v1\0",
        overflow_refusal_unsigned,
    ),
}

degraded_consumer = materialize_context(
    heldout["sources"],
    current_turn_id=heldout["current_turn_id"],
    budget=heldout_budget,
    token_counter=heldout_counter,
    allocation_plan_sha256=ALLOCATION_PLAN_SHA256,
    degradation_policy=ContextWindowDegradationPolicy(),
)
degraded_receipt = degraded_consumer["receipt"]
assert verify_materialized_context_result(
    degraded_consumer,
    expected_receipt_sha256=degraded_receipt["receipt_sha256"],
    expected_allocation_plan_sha256=ALLOCATION_PLAN_SHA256,
) == degraded_consumer
degraded_runtime = degraded_consumer["runtime_payload"]
degraded_prototype = degraded_consumer["materialized_context"]["prototype"]
degraded_bundle = degraded_prototype["context_bundle"]
degraded_metadata = degraded_bundle["artifact"]["compiler_metadata"]
assert degraded_metadata[MEMORY_RENDERING_PROFILE_METADATA_KEY] == (
    LOSSLESS_COMPACT_MEMORY_RENDERING_PROFILE
)
assert degraded_metadata[CONTEXT_WINDOW_DEGRADATION_METADATA_KEY] == {
    "effective_memory_budget_tokens": 1486,
    "mode": "lossless-compact-then-reallocate-v1",
    "requested_memory_budget_tokens": 1200,
    "rung": "minimal_memory_reallocation_compact",
}
assert degraded_runtime["accounting"]["memory_tokens"] == 1486
assert degraded_runtime["accounting"]["memory_budget_tokens"] == 1486
assert [item["id"] for item in degraded_runtime["recent_messages"]] == [
    "case-021-m15",
    "case-021-m16",
    "case-021-m17",
]
assert degraded_runtime["current_turn"]["id"] == "case-021-m18"
assert degraded_bundle["certificate"]["issued"] is True
assert degraded_bundle["certificate"]["semantic_completeness_claimed"] is False
assert degraded_bundle["trusted_memory"]["omitted_or_overflowed_protected_items"] == []
assert degraded_runtime["retrieval_result_sha256"] is None
assert degraded_runtime["provider_execution_ready"] is False
assert degraded_runtime["final_provider_recount_required"] is True


def keys(value):
    if type(value) is dict:
        for key, item in value.items():
            yield key
            yield from keys(item)
    elif type(value) is list:
        for item in value:
            yield from keys(item)


assert not any("path" in key.casefold() for key in keys(consumer_result))

if require_standalone:
    for path in package_root.rglob("*"):
        assert path.name != "__pycache__"
        assert path.suffix != ".pyc"

witness_unsigned = {
    "schema": "ctxc-materialized-context-witness-0.3",
    "allocation_plan_sha256": ALLOCATION_PLAN_SHA256,
    "component_manifest_sha256": receipt["component_manifest_sha256"],
    "context_bundle_sha256": first.context_bundle_sha256,
    "current_turn_id": first.current_turn.id,
    "current_turn_sha256": first.current_turn_sha256,
    "final_provider_recount_required": runtime["final_provider_recount_required"],
    "fixed_input_sha256": FIXED_INPUT_SHA256,
    "materialization_sha256": upgraded.materialization_sha256,
    "protected_state_sha256": first.protected_state_sha256,
    "provider_execution_ready": runtime["provider_execution_ready"],
    "prompt_assembly": prompt_assembly,
    "prototype_sha256": first.prototype_sha256,
    "recent_message_ids": [value.id for value in first.recent_messages],
    "recent_messages_sha256": first.recent_messages_sha256,
    "receipt_sha256": receipt["receipt_sha256"],
    "retrieval_result_sha256": runtime["retrieval_result_sha256"],
    "runtime_sha256": hashlib.sha256(runtime_bytes).hexdigest(),
    "overflow_refusal": overflow_refusal,
}
witness = {
    **witness_unsigned,
    "witness_sha256": domain_sha256(
        b"ctxc-materialized-context-witness-v0.3\0",
        witness_unsigned,
    ),
}
sys.stdout.buffer.write(canonical_bytes(witness) + b"\n")
"""


def _subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _run(arguments: Sequence[str], *, cwd: Path | None = None) -> None:
    subprocess.run(
        list(arguments),
        cwd=cwd,
        check=True,
        text=True,
        env=_subprocess_environment(),
    )


def _venv_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _venv_ctxc(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "ctxc.exe"
    return environment / "bin" / "ctxc"


def _artifact_kind(path: Path) -> str:
    if path.name.endswith(".whl"):
        return "wheel"
    if path.name.endswith(".tar.gz"):
        return "sdist"
    raise ValueError(f"unsupported release artifact: {path}")


def _is_reparse_point(path_stat: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(path_stat, "st_file_attributes", 0) & reparse_flag)


def _path_identity(path_stat: os.stat_result) -> tuple[int, int]:
    return path_stat.st_dev, path_stat.st_ino


def _artifact_fingerprint(path_stat: os.stat_result) -> tuple[int, ...]:
    return (
        path_stat.st_dev,
        path_stat.st_ino,
        path_stat.st_mode,
        path_stat.st_nlink,
        path_stat.st_size,
        path_stat.st_mtime_ns,
        path_stat.st_ctime_ns,
    )


def _verified_artifact_snapshot(path: Path) -> tuple[Path, tuple[int, ...]]:
    lexical = path.expanduser().absolute()
    try:
        lexical_stat = lexical.lstat()
    except OSError as exc:
        raise ValueError(f"release artifact is unavailable: {lexical}") from exc
    if (
        not stat.S_ISREG(lexical_stat.st_mode)
        or stat.S_ISLNK(lexical_stat.st_mode)
        or _is_reparse_point(lexical_stat)
        or lexical_stat.st_nlink != 1
    ):
        raise ValueError(
            f"release smoke requires a lexical regular single-link artifact: {lexical}"
        )
    try:
        resolved = lexical.resolve(strict=True)
        resolved_stat = resolved.stat()
    except OSError as exc:
        raise ValueError(f"release artifact cannot be resolved: {lexical}") from exc
    if (
        not stat.S_ISREG(resolved_stat.st_mode)
        or _is_reparse_point(resolved_stat)
        or resolved_stat.st_nlink != 1
        or _artifact_fingerprint(resolved_stat) != _artifact_fingerprint(lexical_stat)
    ):
        raise ValueError(f"release artifact changed while resolving: {lexical}")
    return resolved, _artifact_fingerprint(resolved_stat)


def _assert_artifact_snapshot(
    path: Path,
    expected: tuple[Path, tuple[int, ...]],
) -> None:
    if _verified_artifact_snapshot(path) != expected:
        raise ValueError(f"release artifact changed during smoke install: {path}")


def _verified_distribution_directory(directory: Path) -> Path:
    lexical = directory.expanduser().absolute()
    try:
        lexical_stat = lexical.lstat()
    except OSError as exc:
        raise ValueError(f"release smoke directory is unavailable: {lexical}") from exc
    is_junction = bool(hasattr(os.path, "isjunction") and os.path.isjunction(lexical))
    if (
        not stat.S_ISDIR(lexical_stat.st_mode)
        or stat.S_ISLNK(lexical_stat.st_mode)
        or _is_reparse_point(lexical_stat)
        or is_junction
    ):
        raise ValueError(f"release smoke requires a lexical real directory: {lexical}")
    try:
        resolved = lexical.resolve(strict=True)
        resolved_stat = resolved.stat()
    except OSError as exc:
        raise ValueError(f"release smoke directory cannot be resolved: {lexical}") from exc
    if not stat.S_ISDIR(resolved_stat.st_mode) or _path_identity(resolved_stat) != _path_identity(
        lexical_stat
    ):
        raise ValueError(f"release smoke directory changed while resolving: {lexical}")
    return resolved


def _materialized_evaluation_command(
    executable: Path,
    output: Path,
    *,
    module_root: Path | None,
    verify_report: Path | None = None,
    expected_report_sha256: str | None = None,
) -> list[str]:
    if (verify_report is None) != (expected_report_sha256 is None):
        raise ValueError("evaluation verification requires both report and expected digest")
    if module_root is None:
        command = [str(executable)]
    else:
        verified_root = _verified_distribution_directory(module_root)
        command = [
            str(executable),
            "-I",
            "-P",
            "-B",
            "-c",
            _EVALUATION_SOURCE_RUNNER,
            str(verified_root),
        ]
    command.append("evaluate-materialization")
    if verify_report is None:
        command.extend(("--split", "heldout"))
    else:
        command.extend(
            (
                "--verify-report",
                str(verify_report),
                "--expected-report-sha256",
                str(expected_report_sha256),
            )
        )
    command.extend(("--output", str(output)))
    return command


def _materialized_degradation_evaluation_command(
    executable: Path,
    output: Path,
    *,
    module_root: Path | None,
    verify_report: Path | None = None,
    expected_report_sha256: str | None = None,
) -> list[str]:
    if (verify_report is None) != (expected_report_sha256 is None):
        raise ValueError("degradation evaluation verification requires report and digest")
    if module_root is None:
        command = [str(executable)]
    else:
        verified_root = _verified_distribution_directory(module_root)
        command = [
            str(executable),
            "-I",
            "-P",
            "-B",
            "-c",
            _EVALUATION_SOURCE_RUNNER,
            str(verified_root),
        ]
    command.append("evaluate-materialization-degradation")
    if verify_report is not None:
        command.extend(
            (
                "--verify-report",
                str(verify_report),
                "--expected-report-sha256",
                str(expected_report_sha256),
            )
        )
    command.extend(("--output", str(output)))
    return command


def _read_stable_evaluation_report(path: Path) -> bytes:
    snapshot = _verified_artifact_snapshot(path)
    resolved, fingerprint = snapshot
    size = fingerprint[4]
    if size < 1 or size > _MAX_EVALUATION_REPORT_BYTES:
        raise ValueError("materialized evaluation report is outside its byte limit")
    with resolved.open("rb", buffering=0) as stream:
        opened = _artifact_fingerprint(os.fstat(stream.fileno()))
        if opened[:-1] != fingerprint[:-1]:
            raise ValueError("materialized evaluation report changed before reading")
        raw = stream.read(_MAX_EVALUATION_REPORT_BYTES + 1)
        if _artifact_fingerprint(os.fstat(stream.fileno()))[:-1] != opened[:-1]:
            raise ValueError("materialized evaluation report changed while reading")
    _assert_artifact_snapshot(path, snapshot)
    if len(raw) != size:
        raise ValueError("materialized evaluation report size changed while reading")
    return raw


def _decode_materialized_evaluation_report(raw: bytes) -> dict[str, object]:
    if type(raw) is not bytes:
        raise TypeError("materialized evaluation report must be exact bytes")
    if not raw or len(raw) > _MAX_EVALUATION_REPORT_BYTES:
        raise ValueError("materialized evaluation report is outside its byte limit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("materialized evaluation report must be valid UTF-8") from exc
    if not text.endswith("\n") or text.count("\n") != 1:
        raise ValueError("materialized evaluation report must be exactly one JSON line")

    def exact_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("materialized evaluation report has duplicate fields")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ValueError(f"materialized evaluation report contains {value}")

    try:
        value = json.loads(
            text[:-1],
            object_pairs_hook=exact_object,
            parse_constant=reject_constant,
        )
    except (RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("materialized evaluation report is not valid JSON") from exc
    if type(value) is not dict:
        raise TypeError("materialized evaluation report must be an exact object")
    canonical = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    if canonical != raw:
        raise ValueError("materialized evaluation report is not canonical JSON")
    expected_fields = {
        "budget",
        "cases",
        "claim_boundaries",
        "evaluator_package_version",
        "integrity_passed",
        "pack",
        "report_sha256",
        "schema",
        "selection",
        "summary",
    }
    if set(value) != expected_fields:
        raise ValueError("materialized evaluation report fields are invalid")
    if value["schema"] != MATERIALIZED_EVALUATION_REPORT_SCHEMA:
        raise ValueError("materialized evaluation report schema is unsupported")
    if value["evaluator_package_version"] != EXPECTED_VERSION:
        raise ValueError("materialized evaluation package version is unsupported")
    if value["integrity_passed"] is not False:
        raise ValueError("materialized evaluation must retain its failed integrity result")
    report_sha256 = value["report_sha256"]
    if type(report_sha256) is not str or _SHA256.fullmatch(report_sha256) is None:
        raise ValueError("materialized evaluation report digest is invalid")
    unsigned = {key: item for key, item in value.items() if key != "report_sha256"}
    calculated = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if calculated != report_sha256:
        raise ValueError("materialized evaluation report self-digest mismatch")
    pack = value["pack"]
    if type(pack) is not dict or pack != {
        "corpus_kind": "repository-authored-synthetic-naturalistic-fixture",
        "pack_id": MATERIALIZED_RETENTION_PACK_ID,
        "pack_sha256": MATERIALIZED_RETENTION_PACK_SHA256,
        "planning_unit_profile": "unicode-codepoint-count-v1",
        "raw_bytes": MATERIALIZED_RETENTION_PACK_BYTES,
        "raw_sha256": MATERIALIZED_RETENTION_PACK_RAW_SHA256,
        "schema": MATERIALIZED_RETENTION_PACK_SCHEMA,
    }:
        raise ValueError("materialized evaluation pack identity is invalid")
    selection = value["selection"]
    if type(selection) is not dict:
        raise TypeError("materialized evaluation selection must be an exact object")
    case_ids = selection.get("case_ids")
    if (
        selection.get("split") != "heldout"
        or type(selection.get("case_count")) is not int
        or selection["case_count"] != 20
        or type(case_ids) is not list
        or len(case_ids) != 20
        or any(type(case_id) is not str or not case_id for case_id in case_ids)
        or len(set(case_ids)) != 20
    ):
        raise ValueError("materialized evaluation held-out selection is invalid")
    cases = value["cases"]
    if type(cases) is not list or len(cases) != 20:
        raise ValueError("materialized evaluation held-out cases are invalid")
    summary = value["summary"]
    if type(summary) is not dict:
        raise TypeError("materialized evaluation summary must be an exact object")
    unexpected = summary.get("unexpected_case_ids")
    if (
        type(unexpected) is not list
        or not unexpected
        or any(type(case_id) is not str or case_id not in case_ids for case_id in unexpected)
    ):
        raise ValueError("materialized evaluation must retain its red case outcomes")
    if value["claim_boundaries"] != _EVALUATION_CLAIM_BOUNDARIES:
        raise ValueError("materialized evaluation claim boundaries changed")
    return value


def _materialized_evaluation_report(
    executable: Path,
    directory: Path,
    *,
    module_root: Path | None,
) -> bytes:
    verified_directory = _verified_distribution_directory(directory)
    report_path = verified_directory / "materialized-retention-report.json"
    verified_path = verified_directory / "materialized-retention-report-verified.json"
    if report_path.exists() or verified_path.exists():
        raise ValueError("materialized evaluation output paths must be absent")
    completed = subprocess.run(
        _materialized_evaluation_command(
            executable,
            report_path,
            module_root=module_root,
        ),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=_subprocess_environment(),
        timeout=300,
    )
    if completed.returncode != 3:
        raise RuntimeError(
            f"materialized evaluation did not retain exit code 3; observed {completed.returncode}"
        )
    if completed.stdout or completed.stderr:
        raise RuntimeError("materialized evaluation emitted unexpected console output")
    raw = _read_stable_evaluation_report(report_path)
    report = _decode_materialized_evaluation_report(raw)
    verified = subprocess.run(
        _materialized_evaluation_command(
            executable,
            verified_path,
            module_root=module_root,
            verify_report=report_path,
            expected_report_sha256=str(report["report_sha256"]),
        ),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=_subprocess_environment(),
        timeout=300,
    )
    if verified.returncode != 3:
        raise RuntimeError(
            "materialized evaluation verification did not retain exit code 3; "
            f"observed {verified.returncode}"
        )
    if verified.stdout or verified.stderr:
        raise RuntimeError("materialized evaluation verification emitted unexpected console output")
    verified_raw = _read_stable_evaluation_report(verified_path)
    _decode_materialized_evaluation_report(verified_raw)
    if verified_raw != raw:
        raise RuntimeError("materialized evaluation verification changed report bytes")
    return raw


def _require_matching_materialized_evaluation_reports(
    source_report: bytes,
    installed_reports: Sequence[bytes],
) -> dict[str, object]:
    source = _decode_materialized_evaluation_report(source_report)
    if len(installed_reports) != 2:
        raise ValueError("release smoke requires exactly two installed evaluation reports")
    for report in installed_reports:
        _decode_materialized_evaluation_report(report)
        if report != source_report:
            raise RuntimeError("installed materialized evaluation report differs from source")
    return source


def _decode_materialized_degradation_evaluation_report(raw: bytes) -> dict[str, object]:
    if type(raw) is not bytes:
        raise TypeError("materialized degradation evaluation report must be exact bytes")
    if not raw or len(raw) > _MAX_EVALUATION_REPORT_BYTES:
        raise ValueError("materialized degradation evaluation report is outside its byte limit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("materialized degradation evaluation report must be valid UTF-8") from exc
    if not text.endswith("\n") or text.count("\n") != 1:
        raise ValueError("materialized degradation evaluation report must be one JSON line")

    def exact_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("materialized degradation evaluation report has duplicate fields")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ValueError(f"materialized degradation evaluation report contains {value}")

    try:
        value = json.loads(
            text[:-1],
            object_pairs_hook=exact_object,
            parse_constant=reject_constant,
        )
    except (RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("materialized degradation evaluation report is not valid JSON") from exc
    if type(value) is not dict:
        raise TypeError("materialized degradation evaluation report must be an exact object")
    canonical = _canonical_json_bytes(value) + b"\n"
    if canonical != raw:
        raise ValueError("materialized degradation evaluation report is not canonical JSON")
    if set(value) != {
        "cases",
        "claim_boundaries",
        "evaluation_spec",
        "evaluator_package_version",
        "failed_preflight",
        "integrity_passed",
        "pack",
        "report_sha256",
        "schema",
        "summary",
    }:
        raise ValueError("materialized degradation evaluation report fields are invalid")
    if value["schema"] != MATERIALIZED_DEGRADATION_REPORT_SCHEMA:
        raise ValueError("materialized degradation evaluation report schema is unsupported")
    if value["evaluator_package_version"] != EXPECTED_VERSION:
        raise ValueError("materialized degradation evaluation package version is unsupported")
    if value["integrity_passed"] is not True:
        raise ValueError("materialized degradation evaluation integrity did not pass")
    report_sha256 = value["report_sha256"]
    if type(report_sha256) is not str or _SHA256.fullmatch(report_sha256) is None:
        raise ValueError("materialized degradation evaluation report digest is invalid")
    unsigned = {key: item for key, item in value.items() if key != "report_sha256"}
    if hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest() != report_sha256:
        raise ValueError("materialized degradation evaluation report self-digest mismatch")
    if value["pack"] != {
        "corpus_kind": "repository-authored-synthetic-naturalistic-fixture",
        "pack_id": MATERIALIZED_RETENTION_PACK_ID,
        "pack_sha256": MATERIALIZED_RETENTION_PACK_SHA256,
        "raw_bytes": MATERIALIZED_RETENTION_PACK_BYTES,
        "raw_sha256": MATERIALIZED_RETENTION_PACK_RAW_SHA256,
        "schema": MATERIALIZED_RETENTION_PACK_SCHEMA,
    }:
        raise ValueError("materialized degradation evaluation pack identity is invalid")
    spec = value["evaluation_spec"]
    selection = spec.get("selection") if type(spec) is dict else None
    if (
        type(spec) is not dict
        or type(selection) is not dict
        or spec.get("spec_sha256") != MATERIALIZED_DEGRADATION_SPEC_SHA256
        or selection.get("case_count") != 20
        or selection.get("split") != "heldout"
    ):
        raise ValueError("materialized degradation evaluation specification is invalid")
    failed_preflight = value["failed_preflight"]
    if failed_preflight != {
        "report_bytes_retained": False,
        "report_sha256": "370df110ad65914443fa681bed2cd795b1f3921ac72ff5e1eec0694ac774ba75",
        "spec_sha256": "60bc8d14985c8264bbb3430d40320d83fa1c9ec5839f5df77d5552b28d8a0e2d",
        "status": "failed",
    }:
        raise ValueError("materialized degradation evaluation lost its failed preflight")
    cases = value["cases"]
    expected_case_ids = [f"case-{number:03d}" for number in range(11, 31)]
    if (
        type(cases) is not list
        or len(cases) != 20
        or any(type(case) is not dict or case.get("integrity_passed") is not True for case in cases)
        or [case.get("case_id") for case in cases] != expected_case_ids
    ):
        raise ValueError("materialized degradation evaluation cases are invalid")
    mandatory_cases = [case for case in cases if case.get("mandatory_refusal_applicable") is True]
    if (
        [case["case_id"] for case in mandatory_cases] != ["case-028", "case-030"]
        or any(case.get("mandatory_refusal_preserved") is not True for case in mandatory_cases)
        or any(
            arm.get("outcome") != "refused"
            or arm.get("reason") != "mandatory_components_do_not_fit"
            for case in mandatory_cases
            for arm in case.get("arms", [])
        )
    ):
        raise ValueError("materialized degradation mandatory refusals changed")
    summary = value["summary"]
    historical_matches = (
        summary.get("historical_strict_expectation_match_count") if type(summary) is dict else None
    )
    if (
        type(summary) is not dict
        or summary.get("case_count") != 20
        or summary.get("failed_case_ids") != []
        or type(historical_matches) is not int
        or not 0 <= historical_matches <= 20
        or summary.get("mandatory_refusal_expected_count") != 2
        or summary.get("mandatory_refusal_preserved_count") != 2
    ):
        raise ValueError("materialized degradation evaluation summary is invalid")
    arms = summary.get("arms")
    expected_arms = {
        "strict",
        "lossless_compact_only",
        "lossless_compact_then_single_reallocation",
    }
    if type(arms) is not dict or set(arms) != expected_arms:
        raise ValueError("materialized degradation evaluation arm summary is invalid")
    for arm_name in sorted(expected_arms):
        arm = arms[arm_name]
        accepted = arm.get("accepted_case_count") if type(arm) is dict else None
        refused = arm.get("refused_case_count") if type(arm) is dict else None
        receipt_count = arm.get("receipt_replay_bound_count") if type(arm) is dict else None
        raw_identity_count = (
            arm.get("raw_source_identity_pass_count") if type(arm) is dict else None
        )
        if (
            type(arm) is not dict
            or arm.get("case_count") != 20
            or type(accepted) is not int
            or type(refused) is not int
            or accepted < 0
            or refused < 0
            or accepted + refused != 20
            or arm.get("deterministic_replay_pass_count") != 20
            or arm.get("inputs_unchanged_pass_count") != 20
            or arm.get("integrity_pass_count") != 20
            or type(receipt_count) is not int
            or receipt_count != accepted
            or type(raw_identity_count) is not int
            or raw_identity_count != accepted
        ):
            raise ValueError("materialized degradation evaluation arm summary is invalid")
    if value["claim_boundaries"] != _DEGRADATION_EVALUATION_CLAIM_BOUNDARIES:
        raise ValueError("materialized degradation evaluation claim boundaries changed")
    return value


def _materialized_degradation_evaluation_report(
    executable: Path,
    directory: Path,
    *,
    module_root: Path | None,
) -> bytes:
    verified_directory = _verified_distribution_directory(directory)
    report_path = verified_directory / "materialized-degradation-report.json"
    verified_path = verified_directory / "materialized-degradation-report-verified.json"
    if report_path.exists() or verified_path.exists():
        raise ValueError("materialized degradation output paths must be absent")
    completed = subprocess.run(
        _materialized_degradation_evaluation_command(
            executable,
            report_path,
            module_root=module_root,
        ),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=_subprocess_environment(),
        timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "materialized degradation evaluation did not pass; "
            f"observed exit code {completed.returncode}"
        )
    if completed.stdout or completed.stderr:
        raise RuntimeError("materialized degradation evaluation emitted console output")
    raw = _read_stable_evaluation_report(report_path)
    report = _decode_materialized_degradation_evaluation_report(raw)
    verified = subprocess.run(
        _materialized_degradation_evaluation_command(
            executable,
            verified_path,
            module_root=module_root,
            verify_report=report_path,
            expected_report_sha256=str(report["report_sha256"]),
        ),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=_subprocess_environment(),
        timeout=300,
    )
    if verified.returncode != 0:
        raise RuntimeError(
            "materialized degradation verification did not pass; "
            f"observed exit code {verified.returncode}"
        )
    if verified.stdout or verified.stderr:
        raise RuntimeError("materialized degradation verification emitted console output")
    verified_raw = _read_stable_evaluation_report(verified_path)
    _decode_materialized_degradation_evaluation_report(verified_raw)
    if verified_raw != raw:
        raise RuntimeError("materialized degradation verification changed report bytes")
    return raw


def _require_matching_materialized_degradation_evaluation_reports(
    source_report: bytes,
    installed_reports: Sequence[bytes],
) -> dict[str, object]:
    source = _decode_materialized_degradation_evaluation_report(source_report)
    if len(installed_reports) != 2:
        raise ValueError("release smoke requires two installed degradation reports")
    for report in installed_reports:
        _decode_materialized_degradation_evaluation_report(report)
        if report != source_report:
            raise RuntimeError("installed materialized degradation report differs from source")
    return source


def _materialized_context_probe_command(
    python: Path,
    *,
    module_root: Path | None,
    require_standalone: bool,
) -> list[str]:
    return [
        str(python),
        "-I",
        "-B",
        "-c",
        _MATERIALIZED_CONTEXT_PROBE,
        "1" if require_standalone else "0",
        "" if module_root is None else str(module_root),
    ]


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _domain_sha256(domain: bytes, value: object) -> str:
    return hashlib.sha256(domain + _canonical_json_bytes(value)).hexdigest()


def _decode_materialized_witness(output: str) -> dict[str, object]:
    if type(output) is not str:
        raise TypeError("materialized-context witness output must be an exact string")
    try:
        encoded = output.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("materialized-context witness output must be valid UTF-8") from exc
    if not encoded or len(encoded) > _MAX_WITNESS_BYTES:
        raise ValueError("materialized-context witness output is outside its byte limit")
    if not output.endswith("\n") or output.count("\n") != 1:
        raise ValueError("materialized-context witness must be exactly one JSON line")
    raw = output[:-1]

    def exact_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("materialized-context witness has duplicate fields")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ValueError(f"materialized-context witness contains {value}")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=exact_object,
            parse_constant=reject_constant,
        )
    except (RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("materialized-context witness is not valid JSON") from exc
    if type(value) is not dict:
        raise TypeError("materialized-context witness must be an exact object")
    actual_fields = frozenset(value)
    if actual_fields != _WITNESS_FIELDS:
        unknown = sorted(actual_fields - _WITNESS_FIELDS)
        missing = sorted(_WITNESS_FIELDS - actual_fields)
        raise ValueError(
            f"materialized-context witness fields are invalid; unknown={unknown}, missing={missing}"
        )
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if canonical != raw:
        raise ValueError("materialized-context witness is not canonical JSON")
    if value["schema"] != MATERIALIZED_WITNESS_SCHEMA:
        raise ValueError("materialized-context witness schema is unsupported")
    for field in _WITNESS_HASH_FIELDS:
        digest = value[field]
        if type(digest) is not str or _SHA256.fullmatch(digest) is None:
            raise ValueError(f"materialized-context witness {field} is invalid")
    if value["recent_message_ids"] != ["message-3"]:
        raise ValueError("materialized-context witness recent-message order is invalid")
    if value["current_turn_id"] != "message-4":
        raise ValueError("materialized-context witness current turn is invalid")
    if value["final_provider_recount_required"] is not True:
        raise ValueError("materialized-context witness must require a final provider recount")
    if value["provider_execution_ready"] is not False:
        raise ValueError("materialized-context witness cannot claim provider readiness")
    if value["retrieval_result_sha256"] is not None:
        raise ValueError("materialized-context witness cannot bind retrieval in v1")

    witness_sha256 = value["witness_sha256"]
    if type(witness_sha256) is not str or _SHA256.fullmatch(witness_sha256) is None:
        raise ValueError("materialized-context witness self-digest is invalid")
    witness_unsigned = {key: item for key, item in value.items() if key != "witness_sha256"}
    if _domain_sha256(_WITNESS_DOMAIN, witness_unsigned) != witness_sha256:
        raise ValueError("materialized-context witness self-digest mismatch")

    prompt_assembly = value["prompt_assembly"]
    if type(prompt_assembly) is not dict or frozenset(prompt_assembly) != _PROMPT_ASSEMBLY_FIELDS:
        raise ValueError("materialized prompt-assembly fields are invalid")
    if prompt_assembly["schema"] != MATERIALIZED_PROMPT_ASSEMBLY_SCHEMA:
        raise ValueError("materialized prompt-assembly schema is unsupported")
    prompt_sha256 = prompt_assembly["prompt_assembly_sha256"]
    if type(prompt_sha256) is not str or _SHA256.fullmatch(prompt_sha256) is None:
        raise ValueError("materialized prompt-assembly digest is invalid")
    prompt_unsigned = {
        key: item for key, item in prompt_assembly.items() if key != "prompt_assembly_sha256"
    }
    if _domain_sha256(_PROMPT_ASSEMBLY_DOMAIN, prompt_unsigned) != prompt_sha256:
        raise ValueError("materialized prompt-assembly digest mismatch")
    component_manifest = prompt_assembly["component_manifest"]
    runtime_payload = prompt_assembly["runtime_payload"]
    if type(component_manifest) is not dict or type(runtime_payload) is not dict:
        raise TypeError("materialized prompt-assembly components must be exact objects")
    if frozenset(component_manifest) != _COMPONENT_MANIFEST_FIELDS:
        raise ValueError("materialized component-manifest fields are invalid")
    if frozenset(runtime_payload) != _RUNTIME_PAYLOAD_FIELDS:
        raise ValueError("materialized runtime-payload fields are invalid")
    if component_manifest["schema"] != "loss-resistant-materialized-context-components-v1":
        raise ValueError("materialized component-manifest schema is unsupported")
    if runtime_payload["schema"] != "loss-resistant-runtime-context-plan-v1":
        raise ValueError("materialized runtime-payload schema is unsupported")
    if (
        type(runtime_payload["accounting_scope"]) is not str
        or runtime_payload["accounting_scope"] != "planned-components-not-final-provider-request"
    ):
        raise ValueError("materialized runtime accounting scope is invalid")
    if (
        type(runtime_payload["tokenizer_identity"]) is not str
        or runtime_payload["tokenizer_identity"] != "release-smoke-character-count-v1"
    ):
        raise ValueError("materialized runtime tokenizer identity is invalid")
    if (
        hashlib.sha256(_canonical_json_bytes(component_manifest)).hexdigest()
        != value["component_manifest_sha256"]
    ):
        raise ValueError("materialized prompt-assembly manifest digest mismatch")
    if (
        hashlib.sha256(_canonical_json_bytes(runtime_payload)).hexdigest()
        != value["runtime_sha256"]
    ):
        raise ValueError("materialized prompt-assembly runtime digest mismatch")
    if component_manifest.get("prompt_order") != [
        "lrcc_verified_memory",
        "recent_raw_messages",
        "external_untrusted_retrieval",
        "current_user_turn",
    ]:
        raise ValueError("materialized prompt-assembly order is invalid")
    verified_manifest = component_manifest.get("lrcc_verified_memory")
    if type(verified_manifest) is not dict or (
        frozenset(verified_manifest)
        != {
            "runtime_field",
            "classification",
            "content_sha256",
            "can_supply_system_or_developer_instructions",
        }
        or verified_manifest.get("runtime_field") != "verified_context"
        or verified_manifest.get("classification") != "lrcc_verified_semantic_memory"
        or verified_manifest.get("content_sha256") != runtime_payload["rendered_memory_sha256"]
        or verified_manifest.get("can_supply_system_or_developer_instructions") is not False
    ):
        raise ValueError("materialized verified-memory classification is invalid")
    recent_manifest = component_manifest.get("recent_raw_messages")
    if type(recent_manifest) is not dict or (
        frozenset(recent_manifest)
        != {
            "runtime_field",
            "classification",
            "ordered_set_sha256",
            "source_roles_preserved",
            "provider_role_projection_allowed",
            "can_supply_system_or_developer_instructions",
        }
        or recent_manifest.get("runtime_field") != "recent_messages"
        or recent_manifest.get("provider_role_projection_allowed") is not False
        or recent_manifest.get("classification") != "untrusted_recent_history"
        or recent_manifest.get("ordered_set_sha256") != runtime_payload["recent_messages_sha256"]
        or recent_manifest.get("source_roles_preserved") is not True
        or recent_manifest.get("can_supply_system_or_developer_instructions") is not False
    ):
        raise ValueError("materialized recent-history boundary is invalid")
    retrieval_manifest = component_manifest.get("external_untrusted_retrieval")
    if type(retrieval_manifest) is not dict or (
        frozenset(retrieval_manifest)
        != {
            "runtime_field",
            "classification",
            "content",
            "retrieval_result_sha256",
            "host_binding_required",
            "can_mutate_lrcc_memory",
            "can_supply_system_or_developer_instructions",
        }
        or retrieval_manifest.get("runtime_field") is not None
        or retrieval_manifest.get("classification") != "untrusted_external_retrieval"
        or retrieval_manifest.get("content") is not None
        or retrieval_manifest.get("retrieval_result_sha256") is not None
        or retrieval_manifest.get("host_binding_required") is not True
        or retrieval_manifest.get("can_mutate_lrcc_memory") is not False
        or retrieval_manifest.get("can_supply_system_or_developer_instructions") is not False
    ):
        raise ValueError("materialized retrieval boundary is invalid")
    current_manifest = component_manifest.get("current_user_turn")
    if type(current_manifest) is not dict or (
        frozenset(current_manifest)
        != {
            "runtime_field",
            "classification",
            "record_sha256",
            "required_role",
            "can_supply_system_or_developer_instructions",
        }
        or current_manifest.get("runtime_field") != "current_turn"
        or current_manifest.get("classification") != "current_user_turn"
        or current_manifest.get("record_sha256") != runtime_payload["current_turn_sha256"]
        or current_manifest.get("required_role") != "user"
        or current_manifest.get("can_supply_system_or_developer_instructions") is not False
    ):
        raise ValueError("materialized current-turn boundary is invalid")
    omission_manifest = component_manifest.get("omitted_from_live_tail")
    if type(omission_manifest) is not dict or (
        frozenset(omission_manifest)
        != {
            "runtime_field",
            "classification",
            "ordered_set_sha256",
            "contains_raw_content",
        }
        or omission_manifest.get("runtime_field") != "recent_tail_omissions"
        or omission_manifest.get("classification") != "compiled_source_accounting"
        or omission_manifest.get("ordered_set_sha256")
        != runtime_payload["recent_tail_omissions_sha256"]
        or omission_manifest.get("contains_raw_content") is not False
    ):
        raise ValueError("materialized omission boundary is invalid")
    refusal_manifest = component_manifest.get("refusal")
    if type(refusal_manifest) is not dict or refusal_manifest != {
        "runtime_field": "refusal_reason",
        "value": None,
    }:
        raise ValueError("materialized accepted-result refusal boundary is invalid")
    if runtime_payload.get("allocation_plan_sha256") != value["allocation_plan_sha256"]:
        raise ValueError("materialized runtime allocation binding is invalid")
    for outer, runtime in (
        ("prototype_sha256", "prototype_sha256"),
        ("materialization_sha256", "materialization_sha256"),
        ("context_bundle_sha256", "context_bundle_sha256"),
        ("protected_state_sha256", "protected_state_sha256"),
        ("recent_messages_sha256", "recent_messages_sha256"),
        ("current_turn_sha256", "current_turn_sha256"),
        ("fixed_input_sha256", "fixed_input_sha256"),
    ):
        if runtime_payload.get(runtime) != value[outer]:
            raise ValueError(f"materialized runtime {runtime} binding is invalid")
    if (
        runtime_payload.get("provider_execution_ready") is not False
        or runtime_payload.get("final_provider_recount_required") is not True
        or runtime_payload.get("retrieval_result_sha256") is not None
        or runtime_payload.get("refusal_reason") is not None
    ):
        raise ValueError("materialized runtime claim boundary is invalid")
    verified_context = runtime_payload.get("verified_context")
    if (
        type(verified_context) is not str
        or hashlib.sha256(verified_context.encode("utf-8")).hexdigest()
        != runtime_payload["rendered_memory_sha256"]
    ):
        raise ValueError("materialized verified-memory content digest mismatch")
    recent_messages = runtime_payload.get("recent_messages")
    current_turn = runtime_payload.get("current_turn")
    omissions = runtime_payload.get("recent_tail_omissions")
    if type(recent_messages) is not list or type(current_turn) is not dict:
        raise TypeError("materialized runtime message components are invalid")
    if type(omissions) is not list:
        raise TypeError("materialized runtime omissions are invalid")
    recent_ids = [message.get("id") for message in recent_messages if type(message) is dict]
    if len(recent_ids) != len(recent_messages) or recent_ids != value["recent_message_ids"]:
        raise ValueError("materialized runtime recent-message order is invalid")
    if current_turn.get("id") != value["current_turn_id"] or current_turn.get("role") != "user":
        raise ValueError("materialized runtime current turn is invalid")
    for message in [*recent_messages, current_turn]:
        if type(message) is not dict or frozenset(message) != _RUNTIME_MESSAGE_FIELDS:
            raise ValueError("materialized runtime message fields are invalid")
        if type(message["id"]) is not str or type(message["role"]) is not str:
            raise TypeError("materialized runtime message identity is invalid")
        if type(message["sequence"]) is not int or message["sequence"] < 0:
            raise TypeError("materialized runtime message sequence is invalid")
        if type(message["content"]) is not str:
            raise TypeError("materialized runtime message content is invalid")
        if (
            hashlib.sha256(message["content"].encode("utf-8")).hexdigest()
            != message["content_sha256"]
        ):
            raise ValueError("materialized runtime message content digest mismatch")
        if (
            type(message["record_sha256"]) is not str
            or _SHA256.fullmatch(message["record_sha256"]) is None
        ):
            raise ValueError("materialized runtime message record digest is invalid")
    if current_turn["record_sha256"] != value["current_turn_sha256"]:
        raise ValueError("materialized runtime current-turn record digest mismatch")
    for omission in omissions:
        if type(omission) is not dict or frozenset(omission) != _RUNTIME_OMISSION_FIELDS:
            raise ValueError("materialized runtime omission fields are invalid")
        if "content" in omission:
            raise ValueError("materialized runtime omission contains raw content")
        if type(omission["sequence"]) is not int or omission["sequence"] < 0:
            raise TypeError("materialized runtime omission sequence is invalid")
        for field in (
            "content_sha256",
            "source_record_sha256",
            "compiled_record_sha256",
        ):
            if type(omission[field]) is not str or _SHA256.fullmatch(omission[field]) is None:
                raise ValueError(f"materialized runtime omission {field} is invalid")
    accounting = runtime_payload.get("accounting")
    if type(accounting) is not dict or frozenset(accounting) != _RUNTIME_ACCOUNTING_FIELDS:
        raise ValueError("materialized runtime accounting fields are invalid")
    if any(type(count) is not int or count < 0 for count in accounting.values()):
        raise TypeError("materialized runtime accounting counts must be exact integers")
    if accounting["current_turn_message_count"] != 1:
        raise ValueError("materialized runtime current-turn accounting is invalid")
    if accounting["recent_tail_message_count"] != len(recent_messages):
        raise ValueError("materialized runtime recent-message accounting is invalid")
    if accounting["compiled_prefix_message_count"] != len(omissions):
        raise ValueError("materialized runtime omission accounting is invalid")
    if accounting["source_message_count"] != len(omissions) + len(recent_messages) + 1:
        raise ValueError("materialized runtime source accounting is invalid")
    if accounting["input_tokens"] != sum(
        accounting[name]
        for name in (
            "fixed_input_tokens",
            "memory_tokens",
            "recent_tail_tokens",
            "current_turn_tokens",
        )
    ):
        raise ValueError("materialized runtime input accounting is invalid")
    if accounting["occupied_tokens"] != sum(
        (
            accounting["input_tokens"],
            accounting["reserved_output_tokens"],
            accounting["safety_margin_tokens"],
        )
    ):
        raise ValueError("materialized runtime occupied accounting is invalid")
    if accounting["remaining_tokens"] != (
        accounting["hard_limit_tokens"] - accounting["occupied_tokens"]
    ):
        raise ValueError("materialized runtime remaining accounting is invalid")
    if value["current_turn_id"] in recent_ids or value["current_turn_id"] in [
        item.get("id") for item in omissions if type(item) is dict
    ]:
        raise ValueError("materialized runtime duplicates the current turn")

    overflow_refusal = value["overflow_refusal"]
    if type(overflow_refusal) is not dict or frozenset(overflow_refusal) != _REFUSAL_GOLDEN_FIELDS:
        raise ValueError("materialized overflow-refusal fields are invalid")
    if overflow_refusal["schema"] != MATERIALIZED_REFUSAL_GOLDEN_SCHEMA:
        raise ValueError("materialized overflow-refusal schema is unsupported")
    if overflow_refusal["reason"] != "compiled_memory_not_verified":
        raise ValueError("materialized overflow-refusal reason is invalid")
    refusal_sha256 = overflow_refusal["refusal_sha256"]
    if type(refusal_sha256) is not str or _SHA256.fullmatch(refusal_sha256) is None:
        raise ValueError("materialized overflow-refusal digest is invalid")
    refusal_unsigned = {
        key: item for key, item in overflow_refusal.items() if key != "refusal_sha256"
    }
    if _domain_sha256(_REFUSAL_GOLDEN_DOMAIN, refusal_unsigned) != refusal_sha256:
        raise ValueError("materialized overflow-refusal digest mismatch")
    refusal = overflow_refusal["diagnostic"]
    if type(refusal) is not dict or frozenset(refusal) != _REFUSAL_DIAGNOSTIC_FIELDS:
        raise ValueError("materialized overflow diagnostic fields are invalid")
    expected_refusal = {
        "schema": "loss-resistant-materialization-refusal-diagnostic-v1",
        "reason": "compiled_memory_not_verified",
        "stage": "compile_memory",
        "cause": "memory_token_budget_overflow",
        "tokenizer_identity": "unicode-codepoint-count-v1",
        "memory_budget_tokens": 1_200,
        "required_memory_tokens": 1_772,
        "overflow_tokens": 572,
        "compiled_prefix_message_count": 14,
        "compiled_prefix_manifest_sha256": (
            "0d681e92657fdddffa8c37de63aa5428d68d126b0a2e8a930e1a87354feae6b2"
        ),
        "retrieval_result_sha256": None,
        "provider_execution_ready": False,
        "final_provider_recount_required": True,
    }
    if refusal != expected_refusal:
        raise ValueError("materialized overflow diagnostic changed")
    return value


def _run_bounded_materialized_probe(command: Sequence[str]) -> str:
    if type(command) not in {list, tuple} or not command:
        raise TypeError("materialized-context probe command must be a non-empty sequence")
    if any(type(value) is not str for value in command):
        raise TypeError("materialized-context probe arguments must be exact strings")
    empty_positions = [index for index, value in enumerate(command) if not value]
    installed_module_root_sentinel = (
        empty_positions == [6]
        and len(command) == 7
        and tuple(command[1:4]) == ("-I", "-B", "-c")
        and command[4] == _MATERIALIZED_CONTEXT_PROBE
        and command[5] == "1"
    )
    if empty_positions and not installed_module_root_sentinel:
        raise TypeError("materialized-context probe arguments must be non-empty")
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_subprocess_environment(),
    )
    if process.stdout is None or process.stderr is None:  # pragma: no cover - Popen contract
        process.kill()
        raise RuntimeError("materialized-context probe pipes are unavailable")
    stdout = bytearray()
    stderr = bytearray()
    exceeded: list[str] = []
    exceeded_lock = threading.Lock()

    def drain(stream: object, sink: bytearray, label: str) -> None:
        total = 0
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                total += len(chunk)
                remaining = _MAX_WITNESS_BYTES + 1 - len(sink)
                if remaining > 0:
                    sink.extend(chunk[:remaining])
                if total > _MAX_WITNESS_BYTES:
                    with exceeded_lock:
                        if label not in exceeded:
                            exceeded.append(label)
                    with contextlib.suppress(OSError):
                        process.kill()
        finally:
            stream.close()

    readers = (
        threading.Thread(target=drain, args=(process.stdout, stdout, "stdout"), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr, "stderr"), daemon=True),
    )
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=120)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        returncode = process.wait(timeout=10)
    for reader in readers:
        reader.join(timeout=10)
    if any(reader.is_alive() for reader in readers):
        raise RuntimeError("materialized-context probe output readers did not terminate")
    if timed_out:
        raise TimeoutError("materialized-context probe exceeded 120 seconds")
    if exceeded:
        raise ValueError(
            "materialized-context probe " + ", ".join(exceeded) + " exceeds the byte limit"
        )
    try:
        decoded_stdout = bytes(stdout).decode("utf-8", errors="strict")
        decoded_stderr = bytes(stderr).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("materialized-context probe output is not UTF-8") from exc
    if returncode != 0:
        raise subprocess.CalledProcessError(
            returncode,
            list(command),
            output=decoded_stdout,
            stderr=decoded_stderr,
        )
    if decoded_stderr:
        raise RuntimeError("materialized-context probe emitted unexpected stderr")
    return decoded_stdout


def _materialized_context_witness(
    python: Path,
    *,
    module_root: Path | None = None,
    require_standalone: bool,
) -> dict[str, object]:
    verified_root = None if module_root is None else _verified_distribution_directory(module_root)
    output = _run_bounded_materialized_probe(
        _materialized_context_probe_command(
            python,
            module_root=verified_root,
            require_standalone=require_standalone,
        )
    )
    return _decode_materialized_witness(output)


def _source_module_root() -> Path:
    script = Path(__file__).resolve(strict=True)
    source_root = _verified_distribution_directory(script.parent.parent / "src")
    package_root = source_root / "context_compiler"
    required = (
        package_root / "context_window.py",
        package_root / "materialized_window.py",
    )
    if not all(path.is_file() and not path.is_symlink() for path in required):
        raise ValueError("materialized-context source modules are unavailable")
    return source_root


def _require_matching_materialized_witnesses(
    source_witness: dict[str, object],
    installed_witnesses: Sequence[dict[str, object]],
) -> dict[str, object]:
    if len(installed_witnesses) != 2:
        raise ValueError("release smoke requires exactly two installed witnesses")
    source_bytes = _canonical_json_bytes(source_witness) + b"\n"
    for witness in installed_witnesses:
        if _canonical_json_bytes(witness) + b"\n" != source_bytes:
            raise RuntimeError("installed materialized-context behavior differs from source")
    return source_witness


def _require_expected_materialized_witness_sha256(
    witness: Mapping[str, object],
    expected_sha256: str | None,
) -> str:
    if type(witness) is not dict:
        raise TypeError("materialized witness must be an exact object")
    actual = hashlib.sha256(_canonical_json_bytes(witness) + b"\n").hexdigest()
    if expected_sha256 is None:
        return actual
    if type(expected_sha256) is not str or _SHA256.fullmatch(expected_sha256) is None:
        raise TypeError("expected materialized witness SHA-256 is invalid")
    if actual != expected_sha256:
        raise ValueError("materialized witness does not match the external expected SHA-256")
    return actual


def _assert_installed_package(
    python: Path,
    environment: Path,
    expected_schema_names: list[str],
) -> tuple[dict[str, object], bytes, bytes]:
    probe = (
        "import importlib.metadata as m, json, pathlib, sys; "
        f"assert m.version('{DISTRIBUTION}') == '{EXPECTED_VERSION}'; "
        f"requirements=m.requires('{DISTRIBUTION}') or []; "
        "assert all('extra ==' in value for value in requirements), requirements; "
        "root=pathlib.Path(sys.prefix)/'share'/'loss-resistant-context-compiler'/'schemas'; "
        "names=sorted(path.name for path in root.glob('*.schema.json')); "
        f"assert names == {expected_schema_names!r}, (root, names); "
        "assert all(isinstance(json.loads(path.read_text(encoding='utf-8')), dict) "
        "for path in root.glob('*.schema.json'))"
    )
    _run([str(python), "-I", "-B", "-c", probe])

    ctxc = _venv_ctxc(environment)
    _run([str(ctxc), "--help"])
    _run([str(ctxc), "materialize", "--help"])
    _run([str(ctxc), "evaluate-materialization", "--help"])
    _run([str(ctxc), "evaluate-materialization-degradation", "--help"])

    source_path = environment / "sources.json"
    artifact_path = environment / "artifact.json"
    manifest_path = environment / "manifest.json"
    report_path = environment / "trust-report.json"
    source_path.write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "content": "constraint: preserve the release contract",
                }
            ]
        ),
        encoding="utf-8",
    )
    _run([str(ctxc), "compile", str(source_path), "-o", str(artifact_path)])
    _run(
        [
            str(ctxc),
            "trust",
            "create",
            str(artifact_path),
            str(source_path),
            "-o",
            str(manifest_path),
        ]
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    anchor = manifest["manifest_sha256"]
    _run(
        [
            str(ctxc),
            "trust",
            "verify",
            str(manifest_path),
            str(artifact_path),
            str(source_path),
            "--expected-manifest-sha256",
            anchor,
            "-o",
            str(report_path),
        ]
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not (
        report.get("passed") is True
        and report.get("anchored") is True
        and all(report.get("checks", {}).values())
    ):
        raise RuntimeError(f"installed trust round trip failed: {report}")
    evaluation_report = _materialized_evaluation_report(
        ctxc,
        environment.parent,
        module_root=None,
    )
    degradation_evaluation_report = _materialized_degradation_evaluation_report(
        ctxc,
        environment.parent,
        module_root=None,
    )
    witness = _materialized_context_witness(
        python,
        require_standalone=True,
    )
    return witness, evaluation_report, degradation_evaluation_report


def _temporary_root() -> Path:
    return Path(tempfile.gettempdir()).resolve(strict=True)


def _offline_build_inputs(
    wheelhouse: Path | None,
    requirements: Path | None,
) -> tuple[Path, tuple[Path, tuple[int, ...]]] | None:
    if (wheelhouse is None) != (requirements is None):
        raise ValueError("--build-wheelhouse and --build-requirements must be supplied together")
    if wheelhouse is None or requirements is None:
        return None
    verified_wheelhouse = _verified_distribution_directory(wheelhouse)
    requirements_snapshot = _verified_artifact_snapshot(requirements)
    return verified_wheelhouse, requirements_snapshot


def _build_tool_install_command(
    python: Path,
    offline_inputs: tuple[Path, tuple[Path, tuple[int, ...]]] | None,
) -> tuple[list[str], str]:
    base = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    if offline_inputs is None:
        return [*base, "setuptools>=77", "wheel>=0.41"], "online-lower-bounds"
    wheelhouse, requirements_snapshot = offline_inputs
    requirements, _fingerprint = requirements_snapshot
    return (
        [
            str(python),
            "-m",
            "pip",
            "--isolated",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "--only-binary=:all:",
            "--require-hashes",
            "--force-reinstall",
            "-r",
            str(requirements),
        ],
        "hash-pinned-offline-wheelhouse",
    )


def _artifact_install_command(python: Path, artifact: Path, *, kind: str) -> list[str]:
    command = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-index",
        "--no-deps",
        "--no-compile",
    ]
    if kind == "sdist":
        command.append("--no-build-isolation")
    command.append(str(artifact))
    return command


def _smoke_artifact(
    path: Path,
    expected_schema_names: list[str],
    *,
    offline_build_inputs: tuple[Path, tuple[Path, tuple[int, ...]]] | None,
) -> tuple[dict[str, str], dict[str, object], bytes, bytes]:
    artifact_snapshot = _verified_artifact_snapshot(path)
    artifact, _ = artifact_snapshot
    kind = _artifact_kind(artifact)
    build_bootstrap = "not-applicable"
    with tempfile.TemporaryDirectory(
        prefix=f"ctxc-{kind}-install-",
        dir=_temporary_root(),
    ) as directory:
        environment = Path(directory) / "venv"
        _run([sys.executable, "-m", "venv", str(environment)])
        python = _venv_python(environment)
        if kind == "sdist":
            bootstrap_command, build_bootstrap = _build_tool_install_command(
                python,
                offline_build_inputs,
            )
            _run(bootstrap_command)
            if offline_build_inputs is not None:
                wheelhouse, requirements_snapshot = offline_build_inputs
                _assert_artifact_snapshot(requirements_snapshot[0], requirements_snapshot)
                if _verified_distribution_directory(wheelhouse) != wheelhouse:
                    raise ValueError("offline build wheelhouse changed during bootstrap")
        _assert_artifact_snapshot(path, artifact_snapshot)
        _run(_artifact_install_command(python, artifact, kind=kind))
        _assert_artifact_snapshot(path, artifact_snapshot)
        witness, evaluation_report, degradation_evaluation_report = _assert_installed_package(
            python,
            environment,
            expected_schema_names,
        )
    return (
        {
            "artifact": artifact.name,
            "kind": kind,
            "status": "passed",
            "build_bootstrap": build_bootstrap,
        },
        witness,
        evaluation_report,
        degradation_evaluation_report,
    )


def _release_artifacts(directory: Path) -> list[Path]:
    verified_directory = _verified_distribution_directory(directory)
    artifacts = sorted(verified_directory.glob("*.whl")) + sorted(
        verified_directory.glob("*.tar.gz")
    )
    kinds = [_artifact_kind(path) for path in artifacts]
    if kinds.count("wheel") != 1 or kinds.count("sdist") != 1:
        raise ValueError(
            "release smoke requires exactly one wheel and one source distribution: "
            f"{[path.name for path in artifacts]}"
        )
    return artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install and smoke-test one wheel and one sdist in separate clean environments."
    )
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument("--schema-dir", type=Path, default=Path("schemas"))
    parser.add_argument("--build-wheelhouse", type=Path)
    parser.add_argument("--build-requirements", type=Path)
    parser.add_argument("--expected-materialized-witness-sha256")
    return parser


def _release_report(
    schema_count: int,
    artifacts: Sequence[dict[str, str]],
    materialized_evaluation: Mapping[str, object],
    materialized_witness: Mapping[str, object],
) -> dict[str, object]:
    if type(materialized_evaluation) is not dict:
        raise TypeError("materialized evaluation must be an exact object")
    if materialized_evaluation["integrity_passed"] is not False:
        raise ValueError("release report cannot relabel the materialized evaluation green")
    claims = materialized_evaluation["claim_boundaries"]
    if claims != _EVALUATION_CLAIM_BOUNDARIES:
        raise ValueError("release report materialized evaluation claims changed")
    if type(materialized_witness) is not dict:
        raise TypeError("materialized witness must be an exact object")
    witness_line = _canonical_json_bytes(materialized_witness) + b"\n"
    decoded_witness = _decode_materialized_witness(witness_line.decode("utf-8"))
    return {
        "schema": "ctxc-release-install-smoke-0.3",
        "schema_count": schema_count,
        "artifacts": [dict(value) for value in artifacts],
        "materialized_evaluation": {
            "exit_code": 3,
            "inference_status": claims["inference_status"],
            "integrity_passed": False,
            "report_sha256": materialized_evaluation["report_sha256"],
            "retrieval_status": claims["retrieval_status"],
            "source_wheel_sdist_report_bytes_identical": True,
            "status": "failed",
        },
        "materialized_context_witness": {
            "canonical_line_sha256": hashlib.sha256(witness_line).hexdigest(),
            "source_wheel_sdist_witness_bytes_identical": True,
            "witness": decoded_witness,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    offline_build_inputs = _offline_build_inputs(
        args.build_wheelhouse,
        args.build_requirements,
    )
    expected_schema_names = sorted(path.name for path in args.schema_dir.glob(SCHEMA_GLOB))
    if not expected_schema_names:
        raise ValueError(f"no packaged schemas found in {args.schema_dir}")
    source_witness = _materialized_context_witness(
        Path(sys.executable),
        module_root=_source_module_root(),
        require_standalone=False,
    )
    _require_expected_materialized_witness_sha256(
        source_witness,
        args.expected_materialized_witness_sha256,
    )
    with tempfile.TemporaryDirectory(
        prefix="ctxc-source-evaluation-",
        dir=_temporary_root(),
    ) as directory:
        source_evaluation_report = _materialized_evaluation_report(
            Path(sys.executable),
            Path(directory),
            module_root=_source_module_root(),
        )
        source_degradation_evaluation_report = _materialized_degradation_evaluation_report(
            Path(sys.executable),
            Path(directory),
            module_root=_source_module_root(),
        )
    smoke_results = [
        _smoke_artifact(
            path,
            expected_schema_names,
            offline_build_inputs=offline_build_inputs,
        )
        for path in _release_artifacts(args.dist_dir)
    ]
    results = [result for result, _witness, _evaluation, _degradation in smoke_results]
    installed_witnesses = [witness for _result, witness, _evaluation, _degradation in smoke_results]
    installed_evaluation_reports = [
        evaluation for _result, _witness, evaluation, _degradation in smoke_results
    ]
    installed_degradation_evaluation_reports = [
        degradation for _result, _witness, _evaluation, degradation in smoke_results
    ]
    _require_matching_materialized_witnesses(
        source_witness,
        installed_witnesses,
    )
    materialized_evaluation = _require_matching_materialized_evaluation_reports(
        source_evaluation_report,
        installed_evaluation_reports,
    )
    _require_matching_materialized_degradation_evaluation_reports(
        source_degradation_evaluation_report,
        installed_degradation_evaluation_reports,
    )
    print(
        json.dumps(
            _release_report(
                len(expected_schema_names),
                results,
                materialized_evaluation,
                source_witness,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
