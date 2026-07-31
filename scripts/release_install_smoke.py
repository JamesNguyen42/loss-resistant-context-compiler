"""Validate clean wheel and source-distribution installs on any supported host."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

DISTRIBUTION = "loss-resistant-context-compiler"
EXPECTED_VERSION = "0.1.1a2"
SCHEMA_GLOB = "*.schema.json"
MATERIALIZED_WITNESS_SCHEMA = "ctxc-materialized-context-witness-0.2"
_MAX_WITNESS_BYTES = 4 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
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
        "recent_message_ids",
        "retrieval_result_sha256",
    }
)
_MATERIALIZED_CONTEXT_PROBE = r"""
import hashlib
import importlib.util
import json
from pathlib import Path
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
    materialize_context,
    verify_materialized_context_result,
)
from context_compiler.connector import ExactTokenCounterAdapter
from context_compiler.context_window import (
    ContextWindowBudget as ModuleContextWindowBudget,
    ContextWindowPrototype,
    compose_context_window,
)
from context_compiler.materialized_window import (
    MaterializedContextWindow,
    compose_materialized_context_window,
)
from context_compiler.models import SourceRecord

assert ContextWindowBudget is ModuleContextWindowBudget
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
    package_root = Path(context_compiler.__file__).resolve(strict=True).parent
    for path in package_root.rglob("*"):
        assert path.name != "__pycache__"
        assert path.suffix != ".pyc"

witness = {
    "schema": "ctxc-materialized-context-witness-0.2",
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
    "prototype_sha256": first.prototype_sha256,
    "recent_message_ids": [value.id for value in first.recent_messages],
    "recent_messages_sha256": first.recent_messages_sha256,
    "receipt_sha256": receipt["receipt_sha256"],
    "retrieval_result_sha256": runtime["retrieval_result_sha256"],
    "runtime_sha256": hashlib.sha256(runtime_bytes).hexdigest(),
}
print(
    json.dumps(
        witness,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
)
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
    try:
        value = json.loads(raw)
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
    return value


def _materialized_context_witness(
    python: Path,
    *,
    module_root: Path | None = None,
    require_standalone: bool,
) -> dict[str, object]:
    verified_root = None if module_root is None else _verified_distribution_directory(module_root)
    completed = subprocess.run(
        _materialized_context_probe_command(
            python,
            module_root=verified_root,
            require_standalone=require_standalone,
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=_subprocess_environment(),
        timeout=120,
    )
    if completed.stderr:
        raise RuntimeError("materialized-context probe emitted unexpected stderr")
    return _decode_materialized_witness(completed.stdout)


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
) -> None:
    if len(installed_witnesses) != 2:
        raise ValueError("release smoke requires exactly two installed witnesses")
    for witness in installed_witnesses:
        if witness != source_witness:
            raise RuntimeError("installed materialized-context behavior differs from source")


def _assert_installed_package(
    python: Path,
    environment: Path,
    expected_schema_names: list[str],
) -> dict[str, object]:
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
    return _materialized_context_witness(
        python,
        require_standalone=True,
    )


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
) -> tuple[dict[str, str], dict[str, object]]:
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
        witness = _assert_installed_package(
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
    return parser


def _release_report(
    schema_count: int,
    artifacts: Sequence[dict[str, str]],
) -> dict[str, object]:
    return {
        "schema": "ctxc-release-install-smoke-0.1",
        "schema_count": schema_count,
        "artifacts": [dict(value) for value in artifacts],
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
    smoke_results = [
        _smoke_artifact(
            path,
            expected_schema_names,
            offline_build_inputs=offline_build_inputs,
        )
        for path in _release_artifacts(args.dist_dir)
    ]
    results = [result for result, _witness in smoke_results]
    installed_witnesses = [witness for _result, witness in smoke_results]
    _require_matching_materialized_witnesses(
        source_witness,
        installed_witnesses,
    )
    print(
        json.dumps(
            _release_report(len(expected_schema_names), results),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
