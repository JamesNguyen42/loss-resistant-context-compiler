from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from context_compiler.context_window import CONTEXT_WINDOW_DEGRADATION_MODE
from scripts.release_install_smoke import (
    MATERIALIZED_DEGRADATION_POLICY,
    MATERIALIZED_DEGRADATION_REPORT_SCHEMA,
    MATERIALIZED_EVALUATION_REPORT_SCHEMA,
    MATERIALIZED_PROMPT_ASSEMBLY_SCHEMA,
    MATERIALIZED_REFUSAL_GOLDEN_SCHEMA,
    MATERIALIZED_RETENTION_PACK_BYTES,
    MATERIALIZED_RETENTION_PACK_ID,
    MATERIALIZED_RETENTION_PACK_RAW_SHA256,
    MATERIALIZED_RETENTION_PACK_SCHEMA,
    MATERIALIZED_RETENTION_PACK_SHA256,
    MATERIALIZED_WITNESS_SCHEMA,
    _artifact_install_command,
    _assert_artifact_snapshot,
    _assert_connector_utf8_output,
    _assert_report_utf8_output,
    _build_tool_install_command,
    _decode_materialized_degradation_evaluation_report,
    _decode_materialized_evaluation_report,
    _decode_materialized_witness,
    _materialized_context_probe_command,
    _materialized_context_witness,
    _materialized_degradation_evaluation_command,
    _materialized_degradation_evaluation_report,
    _materialized_evaluation_command,
    _materialized_evaluation_report,
    _offline_build_inputs,
    _release_artifacts,
    _release_report,
    _require_expected_materialized_witness_sha256,
    _require_matching_materialized_degradation_evaluation_reports,
    _require_matching_materialized_evaluation_reports,
    _require_matching_materialized_witnesses,
    _run_bounded_materialized_probe,
    _source_module_root,
    _subprocess_environment,
    _venv_python,
    _verified_artifact_snapshot,
)

WHEEL = "loss_resistant_context_compiler-0.1.1a15-py3-none-any.whl"
SDIST = "loss_resistant_context_compiler-0.1.1a15.tar.gz"


def test_release_smoke_uses_the_exact_materialization_degradation_policy() -> None:
    assert MATERIALIZED_DEGRADATION_POLICY == CONTEXT_WINDOW_DEGRADATION_MODE


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _domain_hash(domain: bytes, value: object) -> str:
    return hashlib.sha256(domain + _canonical_bytes(value)).hexdigest()


def _sample_materialized_witness() -> dict[str, object]:
    digest = "0" * 64
    verified_context = "verified memory"
    rendered_memory_sha256 = hashlib.sha256(verified_context.encode("utf-8")).hexdigest()
    recent_content = "recent data"
    current_content = "current request"
    recent_message = {
        "id": "message-3",
        "sequence": 3,
        "role": "assistant",
        "content": recent_content,
        "content_sha256": hashlib.sha256(recent_content.encode("utf-8")).hexdigest(),
        "record_sha256": digest,
    }
    current_turn = {
        "id": "message-4",
        "sequence": 4,
        "role": "user",
        "content": current_content,
        "content_sha256": hashlib.sha256(current_content.encode("utf-8")).hexdigest(),
        "record_sha256": digest,
    }
    omission = {
        "id": "message-0",
        "sequence": 0,
        "role": "user",
        "content_sha256": digest,
        "source_record_sha256": digest,
        "compiled_record_sha256": digest,
        "reason": "compiled_into_verified_memory",
    }
    accounting = {
        "hard_limit_tokens": 100,
        "reserved_output_tokens": 10,
        "safety_margin_tokens": 10,
        "fixed_input_tokens": 1,
        "memory_budget_tokens": 40,
        "memory_tokens": 15,
        "recent_tail_tokens": 11,
        "current_turn_tokens": 15,
        "source_message_count": 3,
        "compiled_prefix_message_count": 1,
        "recent_tail_message_count": 1,
        "current_turn_message_count": 1,
        "input_tokens": 42,
        "occupied_tokens": 62,
        "remaining_tokens": 38,
        "per_message_overhead_tokens": 0,
        "minimum_recent_messages": 1,
        "maximum_recent_messages": 1,
    }
    component_manifest = {
        "schema": "loss-resistant-materialized-context-components-v1",
        "prompt_order": [
            "lrcc_verified_memory",
            "recent_raw_messages",
            "external_untrusted_retrieval",
            "current_user_turn",
        ],
        "lrcc_verified_memory": {
            "runtime_field": "verified_context",
            "classification": "lrcc_verified_semantic_memory",
            "content_sha256": rendered_memory_sha256,
            "can_supply_system_or_developer_instructions": False,
        },
        "recent_raw_messages": {
            "runtime_field": "recent_messages",
            "classification": "untrusted_recent_history",
            "ordered_set_sha256": digest,
            "source_roles_preserved": True,
            "provider_role_projection_allowed": False,
            "can_supply_system_or_developer_instructions": False,
        },
        "external_untrusted_retrieval": {
            "runtime_field": None,
            "classification": "untrusted_external_retrieval",
            "content": None,
            "retrieval_result_sha256": None,
            "host_binding_required": True,
            "can_mutate_lrcc_memory": False,
            "can_supply_system_or_developer_instructions": False,
        },
        "current_user_turn": {
            "runtime_field": "current_turn",
            "classification": "current_user_turn",
            "record_sha256": digest,
            "required_role": "user",
            "can_supply_system_or_developer_instructions": False,
        },
        "omitted_from_live_tail": {
            "runtime_field": "recent_tail_omissions",
            "classification": "compiled_source_accounting",
            "ordered_set_sha256": digest,
            "contains_raw_content": False,
        },
        "refusal": {"runtime_field": "refusal_reason", "value": None},
    }
    runtime_payload = {
        "schema": "loss-resistant-runtime-context-plan-v1",
        "allocation_plan_sha256": digest,
        "prototype_sha256": digest,
        "materialization_sha256": digest,
        "tokenizer_identity": "release-smoke-character-count-v1",
        "accounting_scope": "planned-components-not-final-provider-request",
        "context_bundle_sha256": digest,
        "rendered_memory_sha256": rendered_memory_sha256,
        "protected_state_sha256": digest,
        "recent_messages_sha256": digest,
        "current_turn_sha256": digest,
        "recent_tail_omissions_sha256": digest,
        "fixed_input_sha256": digest,
        "verified_context": verified_context,
        "provider_execution_ready": False,
        "final_provider_recount_required": True,
        "retrieval_result_sha256": None,
        "refusal_reason": None,
        "recent_messages": [recent_message],
        "current_turn": current_turn,
        "recent_tail_omissions": [omission],
        "accounting": accounting,
    }
    prompt_unsigned = {
        "schema": MATERIALIZED_PROMPT_ASSEMBLY_SCHEMA,
        "component_manifest": component_manifest,
        "runtime_payload": runtime_payload,
    }
    prompt_assembly = {
        **prompt_unsigned,
        "prompt_assembly_sha256": _domain_hash(
            b"ctxc-materialized-prompt-assembly-golden-v1\0",
            prompt_unsigned,
        ),
    }
    refusal_diagnostic = {
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
    refusal_unsigned = {
        "schema": MATERIALIZED_REFUSAL_GOLDEN_SCHEMA,
        "reason": "compiled_memory_not_verified",
        "diagnostic": refusal_diagnostic,
    }
    overflow_refusal = {
        **refusal_unsigned,
        "refusal_sha256": _domain_hash(
            b"ctxc-materialization-refusal-golden-v1\0",
            refusal_unsigned,
        ),
    }
    unsigned = {
        "schema": MATERIALIZED_WITNESS_SCHEMA,
        "allocation_plan_sha256": digest,
        "component_manifest_sha256": hashlib.sha256(
            _canonical_bytes(component_manifest)
        ).hexdigest(),
        "context_bundle_sha256": digest,
        "current_turn_id": "message-4",
        "current_turn_sha256": digest,
        "final_provider_recount_required": True,
        "fixed_input_sha256": digest,
        "materialization_sha256": digest,
        "protected_state_sha256": digest,
        "provider_execution_ready": False,
        "prompt_assembly": prompt_assembly,
        "prototype_sha256": digest,
        "recent_message_ids": ["message-3"],
        "recent_messages_sha256": digest,
        "receipt_sha256": digest,
        "retrieval_result_sha256": None,
        "runtime_sha256": hashlib.sha256(_canonical_bytes(runtime_payload)).hexdigest(),
        "overflow_refusal": overflow_refusal,
    }
    return {
        **unsigned,
        "witness_sha256": _domain_hash(
            b"ctxc-materialized-context-witness-v0.3\0",
            unsigned,
        ),
    }


def _encoded_witness(value: object) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def _reseal_witness(value: dict[str, object]) -> None:
    prompt = value["prompt_assembly"]
    assert type(prompt) is dict
    value["component_manifest_sha256"] = hashlib.sha256(
        _canonical_bytes(prompt["component_manifest"])
    ).hexdigest()
    value["runtime_sha256"] = hashlib.sha256(
        _canonical_bytes(prompt["runtime_payload"])
    ).hexdigest()
    prompt_unsigned = {key: item for key, item in prompt.items() if key != "prompt_assembly_sha256"}
    prompt["prompt_assembly_sha256"] = _domain_hash(
        b"ctxc-materialized-prompt-assembly-golden-v1\0",
        prompt_unsigned,
    )
    refusal = value["overflow_refusal"]
    assert type(refusal) is dict
    refusal_unsigned = {key: item for key, item in refusal.items() if key != "refusal_sha256"}
    refusal["refusal_sha256"] = _domain_hash(
        b"ctxc-materialization-refusal-golden-v1\0",
        refusal_unsigned,
    )
    unsigned = {key: item for key, item in value.items() if key != "witness_sha256"}
    value["witness_sha256"] = _domain_hash(
        b"ctxc-materialized-context-witness-v0.3\0",
        unsigned,
    )


def _sample_evaluation_report_bytes() -> bytes:
    case_ids = [f"retention-case-{index:03d}" for index in range(1, 21)]
    unsigned = {
        "schema": MATERIALIZED_EVALUATION_REPORT_SCHEMA,
        "evaluator_package_version": "0.1.1a15",
        "pack": {
            "schema": MATERIALIZED_RETENTION_PACK_SCHEMA,
            "pack_id": MATERIALIZED_RETENTION_PACK_ID,
            "corpus_kind": "repository-authored-synthetic-naturalistic-fixture",
            "raw_bytes": MATERIALIZED_RETENTION_PACK_BYTES,
            "raw_sha256": MATERIALIZED_RETENTION_PACK_RAW_SHA256,
            "pack_sha256": MATERIALIZED_RETENTION_PACK_SHA256,
            "planning_unit_profile": "unicode-codepoint-count-v1",
        },
        "selection": {
            "split": "heldout",
            "case_count": 20,
            "case_ids": case_ids,
        },
        "budget": {},
        "cases": [{"case_id": case_id} for case_id in case_ids],
        "summary": {"unexpected_case_ids": [case_ids[0]]},
        "claim_boundaries": {
            "structural_retention_only": True,
            "natural_history_claimed": False,
            "semantic_completeness_claimed": False,
            "model_answer_superiority_claimed": False,
            "task_completion_measured": False,
            "provider_token_accounting": False,
            "retrieval_included": False,
            "retrieval_status": "not_run",
            "inference_status": "not_run",
            "provider_execution_ready": False,
            "final_provider_recount_required": True,
        },
        "integrity_passed": False,
    }
    unsigned_bytes = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    value = {
        **unsigned,
        "report_sha256": hashlib.sha256(unsigned_bytes).hexdigest(),
    }
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _create_test_environment_with_current_pip(path: Path) -> Path:
    environment = dict(os.environ)
    environment.pop("SSLKEYLOGFILE", None)
    subprocess.run(
        [
            sys._base_executable,
            "-m",
            "venv",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return _venv_python(path)


def test_release_smoke_rejects_symlinked_artifact(tmp_path: Path) -> None:
    target = tmp_path / "target.whl"
    target.write_bytes(b"wheel")
    linked = tmp_path / WHEEL
    try:
        linked.symlink_to(target)
    except OSError:
        pytest.skip("file symlinks are unavailable on this host")

    with pytest.raises(ValueError, match="lexical regular single-link"):
        _verified_artifact_snapshot(linked)


def test_release_smoke_rejects_hard_linked_artifact(tmp_path: Path) -> None:
    target = tmp_path / "target.whl"
    target.write_bytes(b"wheel")
    linked = tmp_path / WHEEL
    try:
        os.link(target, linked)
    except OSError:
        pytest.skip("hard links are unavailable on this host")

    with pytest.raises(ValueError, match="lexical regular single-link"):
        _verified_artifact_snapshot(linked)


def test_release_smoke_detects_artifact_replacement(tmp_path: Path) -> None:
    artifact = tmp_path / WHEEL
    artifact.write_bytes(b"first-wheel")
    snapshot = _verified_artifact_snapshot(artifact)

    artifact.unlink()
    artifact.write_bytes(b"replacement-wheel")

    with pytest.raises(ValueError, match="changed during smoke install"):
        _assert_artifact_snapshot(artifact, snapshot)


def test_release_smoke_rejects_linked_distribution_directory(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / WHEEL).write_bytes(b"wheel")
    (dist / SDIST).write_bytes(b"sdist")
    linked = tmp_path / "linked-dist"
    try:
        linked.symlink_to(dist, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this host")

    with pytest.raises(ValueError, match="lexical real directory"):
        _release_artifacts(linked)


def test_release_smoke_accepts_one_real_wheel_and_sdist(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / WHEEL
    sdist = dist / SDIST
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")

    assert _release_artifacts(dist) == [wheel, sdist]
    assert _verified_artifact_snapshot(wheel)[0] == wheel.resolve()
    assert _verified_artifact_snapshot(sdist)[0] == sdist.resolve()


def test_release_smoke_offline_build_inputs_must_be_a_pair(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    requirements = tmp_path / "build-requirements.txt"
    requirements.write_text(
        "setuptools==83.0.0 --hash=sha256:" + "0" * 64 + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be supplied together"):
        _offline_build_inputs(wheelhouse, None)
    with pytest.raises(ValueError, match="must be supplied together"):
        _offline_build_inputs(None, requirements)


def test_release_smoke_builds_offline_bootstrap_command_with_hash_enforcement(
    tmp_path: Path,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    requirements = tmp_path / "build-requirements.txt"
    requirements.write_text(
        "setuptools==83.0.0 --hash=sha256:" + "0" * 64 + "\n",
        encoding="utf-8",
    )
    offline_inputs = _offline_build_inputs(wheelhouse, requirements)
    assert offline_inputs is not None
    python = tmp_path / "venv" / "python"

    command, mode = _build_tool_install_command(python, offline_inputs)

    assert mode == "hash-pinned-offline-wheelhouse"
    assert command == [
        str(python),
        "-m",
        "pip",
        "--isolated",
        "install",
        "--disable-pip-version-check",
        "--no-deps",
        "--no-index",
        "--find-links",
        str(wheelhouse.resolve()),
        "--only-binary=:all:",
        "--require-hashes",
        "--force-reinstall",
        "-r",
        str(requirements.resolve()),
    ]
    for option in (
        "--isolated",
        "--force-reinstall",
        "--no-deps",
        "--no-index",
        "--only-binary=:all:",
        "--require-hashes",
    ):
        assert command.count(option) == 1
    assert "setuptools>=77" not in command
    assert "wheel>=0.41" not in command


def test_release_smoke_offline_bootstrap_replaces_modified_same_version_package(
    tmp_path: Path,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheel = wheelhouse / "sentinel_pkg-1.0-py3-none-any.whl"
    members = {
        "sentinel_pkg/__init__.py": b'MARKER = "wheel"\n',
        "sentinel_pkg-1.0.dist-info/METADATA": (
            b"Metadata-Version: 2.4\nName: sentinel-pkg\nVersion: 1.0\n\n"
        ),
        "sentinel_pkg-1.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\n"
            b"Generator: ctxc-test\n"
            b"Root-Is-Purelib: true\n"
            b"Tag: py3-none-any\n\n"
        ),
    }
    record_rows = []
    for name, payload in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
        encoded = digest.rstrip(b"=").decode("ascii")
        record_rows.append(f"{name},sha256={encoded},{len(payload)}\n")
    record_rows.append("sentinel_pkg-1.0.dist-info/RECORD,,\n")
    members["sentinel_pkg-1.0.dist-info/RECORD"] = "".join(record_rows).encode("ascii")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name, payload in members.items():
            output.writestr(name, payload)
    wheel.write_bytes(archive.getvalue())

    requirements = tmp_path / "requirements-build.lock"
    requirements.write_text(
        f"sentinel-pkg==1.0 --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}\n",
        encoding="ascii",
        newline="\n",
    )
    offline_inputs = _offline_build_inputs(wheelhouse, requirements)
    assert offline_inputs is not None
    environment = tmp_path / "environment"
    python = _create_test_environment_with_current_pip(environment)
    command, mode = _build_tool_install_command(python, offline_inputs)
    assert mode == "hash-pinned-offline-wheelhouse"
    subprocess_environment = dict(os.environ)
    subprocess_environment.pop("SSLKEYLOGFILE", None)

    subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=subprocess_environment,
    )
    located = subprocess.run(
        [
            str(python),
            "-c",
            "import sentinel_pkg; print(sentinel_pkg.__file__)",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=subprocess_environment,
    )
    module_path = Path(located.stdout.strip())
    original = module_path.read_bytes()
    module_path.write_bytes(original + b'SENTINEL = "modified"\n')
    assert b"SENTINEL" in module_path.read_bytes()

    subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=subprocess_environment,
    )

    assert module_path.read_bytes() == original


def test_release_smoke_preserves_explicit_online_bootstrap_diagnostic(tmp_path: Path) -> None:
    python = tmp_path / "venv" / "python"

    command, mode = _build_tool_install_command(python, None)

    assert mode == "online-lower-bounds"
    assert command[-2:] == ["setuptools>=77", "wheel>=0.41"]
    assert "--no-index" not in command
    assert "--require-hashes" not in command


def test_release_smoke_artifact_install_never_contacts_an_index(tmp_path: Path) -> None:
    python = tmp_path / "venv" / "python"
    wheel = _artifact_install_command(python, tmp_path / WHEEL, kind="wheel")
    sdist = _artifact_install_command(python, tmp_path / SDIST, kind="sdist")

    assert "--no-index" in wheel
    assert "--no-index" in sdist
    assert "--no-deps" in wheel
    assert "--no-deps" in sdist
    assert wheel.count("--no-compile") == 1
    assert sdist.count("--no-compile") == 1
    assert "--no-build-isolation" not in wheel
    assert "--no-build-isolation" in sdist


def test_release_smoke_subprocess_environment_is_standalone_and_no_bytecode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONHOME", "private-home")
    monkeypatch.setenv("PYTHONPATH", "private-path")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "0")

    environment = _subprocess_environment()

    assert "PYTHONHOME" not in environment
    assert "PYTHONPATH" not in environment
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert os.environ["PYTHONHOME"] == "private-home"
    assert os.environ["PYTHONPATH"] == "private-path"


def test_installed_connector_probe_forces_and_verifies_exact_utf8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["arguments"] = arguments
        observed.update(kwargs)
        response = {
            "schema": "ctxc-connector-response-0.1",
            "request_id": "release-utf8-output",
            "operation": "capabilities",
            "ok": True,
            "result": {},
            "error": None,
        }
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout=(json.dumps(response, ensure_ascii=True, separators=(",", ":")) + "\n").encode(
                "utf-8"
            ),
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", run)
    ctxc = tmp_path / "ctxc"

    _assert_connector_utf8_output(ctxc)

    assert observed["arguments"] == [str(ctxc), "connector", "--stdio"]
    assert observed["check"] is False
    assert observed["timeout"] == 30
    environment = observed["env"]
    assert type(environment) is dict
    assert environment["PYTHONUTF8"] == "0"
    assert environment["PYTHONIOENCODING"] == "utf-16:strict"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "PYTHONHOME" not in environment
    assert "PYTHONPATH" not in environment


def test_installed_report_probe_forces_and_verifies_exact_utf8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["arguments"] = arguments
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout="constraint: preserve caf\u00e9, \U0001f9ea, and e\u0301\n".encode("utf-8"),
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", run)
    ctxc = tmp_path / "ctxc"
    artifact = tmp_path / "artifact.json"

    _assert_report_utf8_output(ctxc, artifact)

    assert observed["arguments"] == [
        str(ctxc),
        "inspect",
        str(artifact),
        "--format",
        "text",
        "--show-items",
    ]
    assert observed["check"] is False
    assert observed["timeout"] == 30
    environment = observed["env"]
    assert type(environment) is dict
    assert environment["PYTHONUTF8"] == "0"
    assert environment["PYTHONIOENCODING"] == "utf-16:strict"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "PYTHONHOME" not in environment
    assert "PYTHONPATH" not in environment


@pytest.mark.parametrize(
    "stdout",
    [
        b"\xef\xbb\xbfconstraint: preserve caf\xc3\xa9, \xf0\x9f\xa7\xaa, and e\xcc\x81\n",
        b"constraint: preserve caf\xc3\xa9, \xf0\x9f\xa7\xaa, and e\xcc\x81\r\n",
        b"constraint: preserve caf\xc3\xa9, \xf0\x9f\xa7\xaa, and e\xcc\x81\x00\n",
        b"constraint: preserve caf\xff, \xf0\x9f\xa7\xaa, and e\xcc\x81\n",
        b"missing the required Unicode content\n",
    ],
)
def test_installed_report_probe_rejects_noncanonical_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: bytes,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda arguments, **_kwargs: subprocess.CompletedProcess(
            arguments,
            0,
            stdout=stdout,
            stderr=b"",
        ),
    )

    with pytest.raises(ValueError):
        _assert_report_utf8_output(tmp_path / "ctxc", tmp_path / "artifact.json")


def test_materialized_context_probe_command_is_isolated_and_module_qualified(
    tmp_path: Path,
) -> None:
    python = tmp_path / "python"
    command = _materialized_context_probe_command(
        python,
        module_root=tmp_path / "src",
        require_standalone=True,
    )

    assert command[:4] == [str(python), "-I", "-B", "-c"]
    assert command[-2:] == ["1", str(tmp_path / "src")]
    script = command[4]
    assert "from context_compiler import" in script
    assert "materialize_context" in script
    assert "ContextWindowDegradationPolicy" in script
    assert "minimal_memory_reallocation_compact" in script
    assert '"effective_memory_budget_tokens": 1486' in script
    assert "verify_materialized_context_result" in script
    assert "MATERIALIZED_CONTEXT_RESULT_SCHEMA" in script
    assert "from context_compiler.context_window import" in script
    assert "from context_compiler.materialized_window import" in script
    assert "from context_compiler.connector import" in script
    assert "from context_compiler.models import" in script
    assert "materialized_retention_pack_v1.json" in script
    assert MATERIALIZED_RETENTION_PACK_RAW_SHA256 in script
    assert "__pycache__" in script
    assert "package_root.parent == Path(module_root).resolve(strict=True)" in script
    assert "package_root.relative_to(Path(sys.prefix).resolve(strict=True))" in script

    installed = _materialized_context_probe_command(
        python,
        module_root=None,
        require_standalone=True,
    )
    assert installed[:6] == [str(python), "-I", "-B", "-c", script, "1"]
    assert installed[6] == ""


def test_bounded_probe_accepts_only_the_closed_empty_module_root_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReachedPopen(RuntimeError):
        pass

    def reached_popen(*_args: object, **_kwargs: object) -> None:
        raise ReachedPopen("validated command reached Popen")

    monkeypatch.setattr(subprocess, "Popen", reached_popen)
    command = _materialized_context_probe_command(
        tmp_path / "python",
        module_root=None,
        require_standalone=True,
    )
    with pytest.raises(ReachedPopen, match="reached Popen"):
        _run_bounded_materialized_probe(command)
    with pytest.raises(ReachedPopen, match="reached Popen"):
        _run_bounded_materialized_probe(tuple(command))

    invalid = (
        ["", *command[1:]],
        [*command, ""],
        [*command[:-2], "", ""],
        [*command[:5], "0", ""],
    )
    for value in invalid:
        with pytest.raises(TypeError, match="non-empty"):
            _run_bounded_materialized_probe(value)

    class StringSubclass(str):
        pass

    with pytest.raises(TypeError, match="exact strings"):
        _run_bounded_materialized_probe([*command[:-1], StringSubclass("")])


def test_materialized_context_probe_output_is_bounded_before_decoding() -> None:
    command = [
        sys.executable,
        "-I",
        "-B",
        "-c",
        "import sys; sys.stdout.buffer.write(b'x' * (16 * 1024 + 1))",
    ]
    with pytest.raises(ValueError, match="stdout exceeds the byte limit"):
        _run_bounded_materialized_probe(command)


def test_materialized_evaluation_commands_bind_source_and_installed_cli(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    python = tmp_path / "python"
    report = tmp_path / "report.json"
    source = _materialized_evaluation_command(
        python,
        report,
        module_root=source_root,
    )

    assert source[:5] == [str(python), "-I", "-P", "-B", "-c"]
    assert source[6] == str(source_root.resolve())
    assert source[-5:] == [
        "evaluate-materialization",
        "--split",
        "heldout",
        "--output",
        str(report),
    ]
    assert "context_compiler.cli" in source[5]

    ctxc = tmp_path / "ctxc"
    verified = tmp_path / "verified.json"
    installed = _materialized_evaluation_command(
        ctxc,
        verified,
        module_root=None,
        verify_report=report,
        expected_report_sha256="a" * 64,
    )
    assert installed == [
        str(ctxc),
        "evaluate-materialization",
        "--verify-report",
        str(report),
        "--expected-report-sha256",
        "a" * 64,
        "--output",
        str(verified),
    ]

    with pytest.raises(ValueError, match="requires both"):
        _materialized_evaluation_command(
            ctxc,
            verified,
            module_root=None,
            verify_report=report,
        )


def test_materialized_evaluation_report_decoder_preserves_red_result() -> None:
    raw = _sample_evaluation_report_bytes()
    report = _decode_materialized_evaluation_report(raw)

    assert report["integrity_passed"] is False
    assert report["selection"]["split"] == "heldout"
    assert report["summary"]["unexpected_case_ids"]
    assert report["claim_boundaries"]["inference_status"] == "not_run"
    assert report["claim_boundaries"]["retrieval_status"] == "not_run"

    noncanonical = json.dumps(json.loads(raw), sort_keys=False).encode("utf-8") + b"\n"
    with pytest.raises(ValueError, match="not canonical"):
        _decode_materialized_evaluation_report(noncanonical)

    with pytest.raises(ValueError, match="duplicate fields"):
        _decode_materialized_evaluation_report(b'{"schema":1,"schema":2}\n')

    digest_mismatch = json.loads(raw)
    digest_mismatch["report_sha256"] = "0" * 64
    digest_mismatch_raw = (
        json.dumps(
            digest_mismatch,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    with pytest.raises(ValueError, match="self-digest mismatch"):
        _decode_materialized_evaluation_report(digest_mismatch_raw)

    green = json.loads(raw)
    green["integrity_passed"] = True
    green_raw = (
        json.dumps(
            green,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    with pytest.raises(ValueError, match="retain its failed"):
        _decode_materialized_evaluation_report(green_raw)


def test_materialized_evaluation_source_smoke_replays_exact_red_report(
    tmp_path: Path,
) -> None:
    raw = _materialized_evaluation_report(
        Path(sys.executable),
        tmp_path,
        module_root=_source_module_root(),
    )
    report = _decode_materialized_evaluation_report(raw)

    assert report["integrity_passed"] is False
    assert report["claim_boundaries"]["inference_status"] == "not_run"
    assert report["claim_boundaries"]["retrieval_status"] == "not_run"
    assert (tmp_path / "materialized-retention-report.json").read_bytes() == raw
    assert (tmp_path / "materialized-retention-report-verified.json").read_bytes() == raw


def test_materialized_degradation_source_smoke_replays_exact_green_report(
    tmp_path: Path,
) -> None:
    command = _materialized_degradation_evaluation_command(
        Path(sys.executable),
        tmp_path / "shape.json",
        module_root=_source_module_root(),
    )
    assert command[-3:] == [
        "evaluate-materialization-degradation",
        "--output",
        str(tmp_path / "shape.json"),
    ]

    raw = _materialized_degradation_evaluation_report(
        Path(sys.executable),
        tmp_path,
        module_root=_source_module_root(),
    )
    report = _decode_materialized_degradation_evaluation_report(raw)

    assert report["schema"] == MATERIALIZED_DEGRADATION_REPORT_SCHEMA
    assert report["integrity_passed"] is True
    assert set(report["summary"]["arms"]) == {
        "strict",
        "lossless_compact_only",
        "lossless_compact_then_single_reallocation",
    }
    for arm in report["summary"]["arms"].values():
        assert arm["case_count"] == 20
        assert arm["accepted_case_count"] + arm["refused_case_count"] == 20
        assert arm["deterministic_replay_pass_count"] == 20
        assert arm["inputs_unchanged_pass_count"] == 20
        assert arm["integrity_pass_count"] == 20
        assert arm["receipt_replay_bound_count"] == arm["accepted_case_count"]
        assert arm["raw_source_identity_pass_count"] == arm["accepted_case_count"]
    assert report["summary"]["mandatory_refusal_expected_count"] == 2
    assert report["summary"]["mandatory_refusal_preserved_count"] == 2
    mandatory = {
        case["case_id"]: case
        for case in report["cases"]
        if case["mandatory_refusal_applicable"] is True
    }
    assert set(mandatory) == {"case-028", "case-030"}
    assert all(
        arm["outcome"] == "refused" and arm["reason"] == "mandatory_components_do_not_fit"
        for case in mandatory.values()
        for arm in case["arms"]
    )
    assert report["claim_boundaries"]["inference_status"] == "not_run"
    assert report["claim_boundaries"]["retrieval_status"] == "not_run"
    assert (tmp_path / "materialized-degradation-report.json").read_bytes() == raw
    assert (tmp_path / "materialized-degradation-report-verified.json").read_bytes() == raw

    assert (
        _require_matching_materialized_degradation_evaluation_reports(
            raw,
            [bytes(raw), bytes(raw)],
        )
        == report
    )

    changed = json.loads(raw)
    changed["summary"]["arms"]["strict"]["accepted_case_count"] = 1
    unsigned = {key: value for key, value in changed.items() if key != "report_sha256"}
    changed["report_sha256"] = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    changed_raw = _canonical_bytes(changed) + b"\n"
    with pytest.raises(ValueError, match="arm summary is invalid"):
        _decode_materialized_degradation_evaluation_report(changed_raw)

    for field in ("receipt_replay_bound_count", "raw_source_identity_pass_count"):
        changed = json.loads(raw)
        arm = changed["summary"]["arms"]["strict"]
        arm[field] = float(arm["accepted_case_count"])
        unsigned = {key: value for key, value in changed.items() if key != "report_sha256"}
        changed["report_sha256"] = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
        with pytest.raises(ValueError, match="arm summary is invalid"):
            _decode_materialized_degradation_evaluation_report(_canonical_bytes(changed) + b"\n")

        changed = json.loads(raw)
        arm = changed["summary"]["arms"]["strict"]
        arm["accepted_case_count"] = 0
        arm["refused_case_count"] = 20
        arm["receipt_replay_bound_count"] = 0
        arm["raw_source_identity_pass_count"] = 0
        arm[field] = False
        unsigned = {key: value for key, value in changed.items() if key != "report_sha256"}
        changed["report_sha256"] = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
        with pytest.raises(ValueError, match="arm summary is invalid"):
            _decode_materialized_degradation_evaluation_report(_canonical_bytes(changed) + b"\n")


def test_materialized_context_source_witness_is_byte_deterministic() -> None:
    source_root = _source_module_root()

    first = _materialized_context_witness(
        Path(sys.executable),
        module_root=source_root,
        require_standalone=False,
    )
    second = _materialized_context_witness(
        Path(sys.executable),
        module_root=source_root,
        require_standalone=False,
    )

    assert first == second
    assert first["schema"] == MATERIALIZED_WITNESS_SCHEMA
    assert first["current_turn_id"] == "message-4"
    assert first["recent_message_ids"] == ["message-3"]
    assert first["final_provider_recount_required"] is True
    assert first["provider_execution_ready"] is False
    assert first["retrieval_result_sha256"] is None
    assert len(first["component_manifest_sha256"]) == 64
    assert len(first["receipt_sha256"]) == 64
    prompt = first["prompt_assembly"]
    assert prompt["component_manifest"]["prompt_order"] == [
        "lrcc_verified_memory",
        "recent_raw_messages",
        "external_untrusted_retrieval",
        "current_user_turn",
    ]
    assert prompt["runtime_payload"]["current_turn"]["id"] == "message-4"
    assert prompt["runtime_payload"]["retrieval_result_sha256"] is None
    assert (
        prompt["component_manifest"]["recent_raw_messages"]["provider_role_projection_allowed"]
        is False
    )
    refusal = first["overflow_refusal"]["diagnostic"]
    assert refusal["reason"] == "compiled_memory_not_verified"
    assert refusal["required_memory_tokens"] == 1_772
    assert refusal["memory_budget_tokens"] == 1_200
    assert refusal["overflow_tokens"] == 572
    assert refusal["provider_execution_ready"] is False
    assert refusal["final_provider_recount_required"] is True


def test_materialized_context_witness_requires_canonical_exact_fields() -> None:
    valid = _sample_materialized_witness()

    assert _decode_materialized_witness(_encoded_witness(valid)) == valid

    unknown = dict(valid)
    unknown["unexpected"] = None
    with pytest.raises(ValueError, match="fields are invalid"):
        _decode_materialized_witness(_encoded_witness(unknown))

    noncanonical = json.dumps(valid, sort_keys=False) + "\n"
    with pytest.raises(ValueError, match="not canonical"):
        _decode_materialized_witness(noncanonical)

    bool_substitution = dict(valid)
    bool_substitution["final_provider_recount_required"] = 1
    with pytest.raises(ValueError, match="final provider recount"):
        _decode_materialized_witness(_encoded_witness(bool_substitution))

    with pytest.raises(ValueError, match="byte limit"):
        _decode_materialized_witness("x" * (16 * 1024 + 1))

    duplicate = _encoded_witness(valid).replace(
        '"schema":"ctxc-materialized-context-witness-0.3"',
        '"schema":"ctxc-materialized-context-witness-0.3",'
        '"schema":"ctxc-materialized-context-witness-0.3"',
        1,
    )
    with pytest.raises(ValueError, match="duplicate fields"):
        _decode_materialized_witness(duplicate)

    with pytest.raises(ValueError, match="not canonical"):
        _decode_materialized_witness(_encoded_witness(valid).replace("\n", "\r\n"))


def test_materialized_context_witness_rejects_nested_resealed_tamper() -> None:
    valid = _sample_materialized_witness()

    changed = copy.deepcopy(valid)
    changed["prompt_assembly"]["unexpected"] = None
    _reseal_witness(changed)
    with pytest.raises(ValueError, match="prompt-assembly fields"):
        _decode_materialized_witness(_encoded_witness(changed))

    changed = copy.deepcopy(valid)
    changed["prompt_assembly"]["runtime_payload"]["retrieval_result_sha256"] = "1" * 64
    _reseal_witness(changed)
    with pytest.raises(ValueError, match="retrieval boundary|claim boundary"):
        _decode_materialized_witness(_encoded_witness(changed))

    changed = copy.deepcopy(valid)
    changed["prompt_assembly"]["component_manifest"]["recent_raw_messages"][
        "provider_role_projection_allowed"
    ] = True
    _reseal_witness(changed)
    with pytest.raises(ValueError, match="recent-history boundary"):
        _decode_materialized_witness(_encoded_witness(changed))

    changed = copy.deepcopy(valid)
    changed["prompt_assembly"]["runtime_payload"]["accounting_scope"] = "final-provider-request"
    _reseal_witness(changed)
    with pytest.raises(ValueError, match="accounting scope"):
        _decode_materialized_witness(_encoded_witness(changed))

    changed = copy.deepcopy(valid)
    changed["prompt_assembly"]["runtime_payload"]["tokenizer_identity"] = "different-counter"
    _reseal_witness(changed)
    with pytest.raises(ValueError, match="tokenizer identity"):
        _decode_materialized_witness(_encoded_witness(changed))

    changed = copy.deepcopy(valid)
    changed["prompt_assembly"]["runtime_payload"]["recent_messages"][0]["content"] = "changed"
    _reseal_witness(changed)
    with pytest.raises(ValueError, match="content digest"):
        _decode_materialized_witness(_encoded_witness(changed))

    changed = copy.deepcopy(valid)
    changed["overflow_refusal"]["diagnostic"]["overflow_tokens"] = 573
    _reseal_witness(changed)
    with pytest.raises(ValueError, match="overflow diagnostic changed"):
        _decode_materialized_witness(_encoded_witness(changed))

    changed = copy.deepcopy(valid)
    changed["overflow_refusal"]["diagnostic"]["memory_budget_tokens"] = True
    _reseal_witness(changed)
    with pytest.raises(ValueError, match="overflow diagnostic changed"):
        _decode_materialized_witness(_encoded_witness(changed))


def test_resealed_structural_substitution_cannot_match_source_witness() -> None:
    source = _sample_materialized_witness()
    changed = copy.deepcopy(source)
    current = changed["prompt_assembly"]["runtime_payload"]["current_turn"]
    current["content"] = "different current request"
    current["content_sha256"] = hashlib.sha256(current["content"].encode("utf-8")).hexdigest()
    _reseal_witness(changed)

    assert _decode_materialized_witness(_encoded_witness(changed)) == changed
    with pytest.raises(RuntimeError, match="differs from source"):
        _require_matching_materialized_witnesses(
            source,
            [copy.deepcopy(source), changed],
        )


def test_release_smoke_compares_source_wheel_and_sdist_witnesses() -> None:
    source = _sample_materialized_witness()
    _require_matching_materialized_witnesses(
        source,
        [dict(source), dict(source)],
    )

    changed = dict(source)
    changed["prototype_sha256"] = "1" * 64
    with pytest.raises(RuntimeError, match="differs from source"):
        _require_matching_materialized_witnesses(
            source,
            [dict(source), changed],
        )
    with pytest.raises(ValueError, match="exactly two"):
        _require_matching_materialized_witnesses(source, [dict(source)])


def test_materialized_witness_consumption_requires_external_expected_digest() -> None:
    witness = _sample_materialized_witness()
    expected = hashlib.sha256(_canonical_bytes(witness) + b"\n").hexdigest()

    assert _require_expected_materialized_witness_sha256(witness, expected) == expected
    assert _require_expected_materialized_witness_sha256(witness, None) == expected
    with pytest.raises(ValueError, match="external expected"):
        _require_expected_materialized_witness_sha256(witness, "0" * 64)
    with pytest.raises(TypeError, match="expected materialized witness"):
        _require_expected_materialized_witness_sha256(witness, True)


def test_release_smoke_compares_source_wheel_and_sdist_evaluation_reports() -> None:
    source = _sample_evaluation_report_bytes()
    report = _require_matching_materialized_evaluation_reports(
        source,
        [bytes(source), bytes(source)],
    )
    assert report["integrity_passed"] is False

    changed = json.loads(source)
    changed["summary"]["unexpected_case_ids"].append(changed["selection"]["case_ids"][1])
    unsigned = {key: value for key, value in changed.items() if key != "report_sha256"}
    changed["report_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    changed_raw = (
        json.dumps(
            changed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    with pytest.raises(RuntimeError, match="differs from source"):
        _require_matching_materialized_evaluation_reports(
            source,
            [bytes(source), changed_raw],
        )
    with pytest.raises(ValueError, match="exactly two"):
        _require_matching_materialized_evaluation_reports(source, [bytes(source)])


def test_release_smoke_report_shape_remains_compatible() -> None:
    artifacts = [
        {
            "artifact": WHEEL,
            "kind": "wheel",
            "status": "passed",
            "build_bootstrap": "not-applicable",
        },
        {
            "artifact": SDIST,
            "kind": "sdist",
            "status": "passed",
            "build_bootstrap": "hash-pinned-offline-wheelhouse",
        },
    ]

    evaluation = _decode_materialized_evaluation_report(_sample_evaluation_report_bytes())
    witness = _sample_materialized_witness()
    report = _release_report(13, artifacts, evaluation, witness)

    assert set(report) == {
        "schema",
        "schema_count",
        "artifacts",
        "materialized_evaluation",
        "materialized_context_witness",
    }
    assert report["schema"] == "ctxc-release-install-smoke-0.3"
    assert report["schema_count"] == 13
    assert report["artifacts"] == artifacts
    assert all(
        set(value) == {"artifact", "kind", "status", "build_bootstrap"}
        for value in report["artifacts"]
    )
    assert report["materialized_evaluation"] == {
        "exit_code": 3,
        "inference_status": "not_run",
        "integrity_passed": False,
        "report_sha256": evaluation["report_sha256"],
        "retrieval_status": "not_run",
        "source_wheel_sdist_report_bytes_identical": True,
        "status": "failed",
    }
    assert report["materialized_context_witness"] == {
        "canonical_line_sha256": hashlib.sha256(_canonical_bytes(witness) + b"\n").hexdigest(),
        "source_wheel_sdist_witness_bytes_identical": True,
        "witness": witness,
    }

    green = dict(evaluation)
    green["integrity_passed"] = True
    with pytest.raises(ValueError, match="cannot relabel"):
        _release_report(13, artifacts, green, witness)
