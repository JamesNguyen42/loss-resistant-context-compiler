from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

import benchmarks.swebench_run as run_module
from benchmarks.literal_process import (
    LiteralProcessError,
    LiteralProcessLimits,
    LiteralProcessResult,
    StreamEvidence,
)
from benchmarks.swebench import (
    DEFAULT_SUITE,
    VerifiedSweBenchSource,
    decode_suite,
    load_source_snapshot,
    task_input_document,
)
from benchmarks.swebench_prediction import (
    CandidatePatchCapture,
    RepositoryPreparationOutcome,
    RetainedCaptureEvidence,
    SystemIdentity,
    build_prediction_ledger,
)
from benchmarks.swebench_repository import (
    PreparedSweBenchRepository,
    prepare_task_repository,
    verify_local_bare_mirror,
)
from benchmarks.swebench_run import (
    CONTROLLER_PROTOCOL,
    CONTROLLER_RESULT_SCHEMA,
    RUN_LEDGER_SCHEMA,
    CandidateWorkspace,
    ControllerSpec,
    RunLedgerLimits,
    SweBenchRunError,
    VerifiedSweBenchRunLedger,
    build_run_ledger,
    decode_run_ledger,
    load_run_ledger,
    verify_run_ledger,
    write_run_ledger,
)

_OPAQUE_KEY = bytes(range(1, 33))
_PATCH = (
    "diff --git a/module.py b/module.py\n"
    "--- a/module.py\n"
    "+++ b/module.py\n"
    "@@ -1 +1 @@\n"
    "-VALUE = 1\n"
    "+VALUE = 2\n"
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", errors="strict")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_digest(path: Path) -> tuple[int, str]:
    encoded = path.read_bytes()
    return len(encoded), hashlib.sha256(encoded).hexdigest()


def _resign(document: dict[str, Any]) -> None:
    unsigned = copy.deepcopy(document)
    unsigned.pop("run_ledger_sha256", None)
    document["run_ledger_sha256"] = _digest(unsigned)


def _record(ordinal: int, commit: str) -> dict[str, str]:
    return {
        "repo": "example/project",
        "instance_id": f"example__project-{ordinal + 1}",
        "base_commit": commit,
        "patch": f"GOLD_SOLUTION_{ordinal}",
        "test_patch": f"GOLD_TEST_PATCH_{ordinal}",
        "problem_statement": f"Fix public problem {ordinal} without gold.",
        "hints_text": "coordinator-only hint",
        "created_at": f"2024-01-{ordinal + 1:02d}T00:00:00Z",
        "version": f"{ordinal + 1}.0",
        "FAIL_TO_PASS": f'["GOLD_FAIL_{ordinal}"]',
        "PASS_TO_PASS": f'["GOLD_PASS_{ordinal}"]',
        "environment_setup_commit": chr(97 + ordinal) * 40,
        "difficulty": "<15 min fix",
    }


def _suite_document(records: list[dict[str, str]]) -> dict[str, Any]:
    document = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
    snapshot_bytes = _canonical_bytes(records)
    source_hashes = [_digest(record) for record in records]
    candidate_hashes = [
        _digest({"problem_statement": record["problem_statement"]})
        for record in records
    ]
    snapshot = document["canonical_snapshot"]
    snapshot.update(
        {
            "byte_count": len(snapshot_bytes),
            "sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
            "row_count": len(records),
            "first_instance_id": records[0]["instance_id"],
            "last_instance_id": records[-1]["instance_id"],
            "ordered_instance_ids_sha256": _digest(
                [record["instance_id"] for record in records]
            ),
            "ordered_source_sha256s_sha256": _digest(source_hashes),
            "ordered_candidate_input_sha256s_sha256": _digest(candidate_hashes),
            "record_binding_fields": [
                "ordinal",
                "instance_id",
                "source_sha256",
                "candidate_input_sha256",
            ],
            "record_bindings": [
                [
                    ordinal,
                    record["instance_id"],
                    source_hashes[ordinal],
                    candidate_hashes[ordinal],
                ]
                for ordinal, record in enumerate(records)
            ],
        }
    )
    document["selection"].update(
        {
            "population_count": len(records),
            "selected_count": len(records),
            "ordered_selected_instance_ids_sha256": snapshot[
                "ordered_instance_ids_sha256"
            ],
        }
    )
    unsigned = copy.deepcopy(document)
    unsigned.pop("suite_sha256", None)
    document["suite_sha256"] = _digest(unsigned)
    return document


def _git(cwd: Path, executable: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        [str(executable), *arguments],
        cwd=cwd,
        env={
            **os.environ,
            "GIT_AUTHOR_DATE": "2001-02-03T04:05:06Z",
            "GIT_COMMITTER_DATE": "2001-02-03T04:05:06Z",
        },
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(
            "fixture Git command failed: "
            f"{arguments!r}: {completed.stderr.decode('utf-8', errors='replace')}"
        )
    return completed.stdout


_CONTROLLER_SOURCE = r'''from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

request_path = Path(sys.argv[-2])
workspace_path = Path(sys.argv[-1])
request_bytes = request_path.read_bytes()
request = json.loads(request_bytes)
if set(request) != {"opaque_task_id", "problem_statement"}:
    raise SystemExit(91)
if request_bytes != json.dumps(
    request,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8"):
    raise SystemExit(92)

mode = os.environ["FIXTURE_MODE"]
if mode == "timeout":
    time.sleep(30)
    raise SystemExit(93)
if mode == "stdout-overflow":
    os.write(sys.stdout.fileno(), b"x" * 8192)
    time.sleep(1)
    raise SystemExit(94)
if mode == "stderr-overflow":
    os.write(sys.stderr.fileno(), b"y" * 8192)
    time.sleep(1)
    raise SystemExit(95)
if mode == "malformed":
    os.write(sys.stdout.fileno(), b'{"schema":')
    raise SystemExit(0)

patch = os.environ.get("FIXTURE_PATCH", "")
if mode == "oversized":
    patch = "X" * int(os.environ["FIXTURE_PATCH_SIZE"])
if mode == "mutate":
    (workspace_path / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    (workspace_path / "added").mkdir()
    (workspace_path / "added" / "trace.txt").write_text(
        "controller\n", encoding="utf-8"
    )

payload = {
    "schema": "ctxc-swebench-controller-result-0.1",
    "opaque_task_id": request["opaque_task_id"],
    "model_patch": patch,
    "tokens": (
        {"method": "not_measured", "prompt": None, "model": None}
        if mode == "unmeasured"
        else {"method": "provider_reported", "prompt": 11, "model": 7}
    ),
    "trajectory": [{"kind": "fixture", "mode": mode}],
}
encoded = json.dumps(
    payload,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
os.write(sys.stdout.fileno(), encoded)
if mode == "nonzero":
    raise SystemExit(9)
'''


@dataclass(frozen=True, slots=True)
class _RunContext:
    source: VerifiedSweBenchSource
    task_input: dict[str, Any]
    prepared: PreparedSweBenchRepository
    opaque_ids: tuple[str, ...]
    controller_script: Path
    python: Path
    python_byte_count: int
    python_sha256: str


@pytest.fixture(scope="module")
def run_context(tmp_path_factory: pytest.TempPathFactory) -> _RunContext:
    git_name = shutil.which("git")
    if git_name is None:
        pytest.skip("Git is required for SWE-bench run-ledger tests")
    git = Path(git_name).resolve()
    root = tmp_path_factory.mktemp("swebench-run")
    repository = root / "source"
    mirror_path = root / "mirror.git"
    _git(
        root,
        git,
        "init",
        "--object-format=sha1",
        "--initial-branch=main",
        str(repository),
    )
    for name, value in (
        ("user.name", "Run Ledger Fixture"),
        ("user.email", "fixture@example.invalid"),
        ("core.autocrlf", "false"),
        ("core.filemode", "true"),
        ("commit.gpgsign", "false"),
    ):
        _git(repository, git, "config", name, value)
    (repository / "module.py").write_bytes(b"VALUE = 1\n")
    (repository / "data.bin").write_bytes(b"\x00fixture\xff\n")
    _git(repository, git, "add", "--all")
    _git(repository, git, "commit", "-m", "fixture")
    commit = _git(repository, git, "rev-parse", "HEAD").decode("ascii").strip()
    _git(root, git, "clone", "--mirror", "--no-local", str(repository), str(mirror_path))
    _git(
        mirror_path,
        git,
        "remote",
        "set-url",
        "origin",
        "https://github.com/example/project.git",
    )

    records = [_record(ordinal, commit) for ordinal in range(3)]
    suite = decode_suite(_suite_document(records))
    snapshot = root / "source.json"
    snapshot.write_bytes(_canonical_bytes(records))
    source = load_source_snapshot(snapshot, suite=suite)
    task_input = task_input_document(source, opaque_key=_OPAQUE_KEY)
    mirror = verify_local_bare_mirror(mirror_path, "example/project", git)
    prepared_parent = root / "prepared"
    prepared_parent.mkdir()
    prepared = prepare_task_repository(source, 0, mirror, prepared_parent)
    controller_script = root / "controller.py"
    controller_script.write_text(_CONTROLLER_SOURCE, encoding="utf-8", newline="\n")
    # The controller uses only the standard library. Prefer the stable base
    # runtime over an editable-venv launcher inside a synced worktree, whose
    # metadata can be touched independently while fail-closed hashing runs.
    python = Path(getattr(sys, "_base_executable", sys.executable)).resolve(
        strict=True
    )
    python_byte_count, python_sha256 = _file_digest(python)
    return _RunContext(
        source=source,
        task_input=task_input,
        prepared=prepared,
        opaque_ids=tuple(
            str(task["opaque_task_id"])
            for task in task_input["tasks"]
        ),
        controller_script=controller_script,
        python=python,
        python_byte_count=python_byte_count,
        python_sha256=python_sha256,
    )


def _system() -> SystemIdentity:
    return SystemIdentity(
        code_revision="1" * 40,
        repository_clean=True,
        model_name_or_path="fixture-agent",
        model_sha256="2" * 64,
        agent_sha256="3" * 64,
        prompt_sha256="4" * 64,
        tool_sha256="5" * 64,
        controller_sha256="6" * 64,
    )


def _preparations(
    context: _RunContext,
) -> tuple[RepositoryPreparationOutcome, ...]:
    return (
        RepositoryPreparationOutcome(
            opaque_task_id=context.opaque_ids[0],
            status="prepared",
            prepared=context.prepared,
        ),
        RepositoryPreparationOutcome(
            opaque_task_id=context.opaque_ids[1],
            status="refused",
            failure_code="fixture-refusal",
        ),
        RepositoryPreparationOutcome(
            opaque_task_id=context.opaque_ids[2],
            status="not-attempted",
        ),
    )


def _controller(
    context: _RunContext,
    *,
    mode: str = "success",
    patch: str = _PATCH,
    patch_size: int = 128,
    process_limits: LiteralProcessLimits | None = None,
) -> ControllerSpec:
    environment = {
        "FIXTURE_MODE": mode,
        "FIXTURE_PATCH": patch,
        "FIXTURE_PATCH_SIZE": str(patch_size),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if os.name == "nt" and "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return ControllerSpec(
        argv=[str(context.python), str(context.controller_script)],
        environment=environment,
        limits=process_limits or LiteralProcessLimits(
            timeout_seconds=5,
            poll_interval_seconds=0.005,
            max_stdout_bytes=64 * 1024,
            max_stderr_bytes=64 * 1024,
        ),
        executable_sha256=context.python_sha256,
        executable_byte_count=context.python_byte_count,
    )


def _workspace(context: _RunContext, parent: Path, name: str = "workspace") -> Path:
    destination = parent / name
    shutil.copytree(context.prepared.path, destination, copy_function=shutil.copy2)
    return destination


def _workspaces(
    context: _RunContext,
    workspace: Path | None,
) -> tuple[CandidateWorkspace, ...]:
    return tuple(
        CandidateWorkspace(opaque_id, workspace if ordinal == 0 else None)
        for ordinal, opaque_id in enumerate(context.opaque_ids)
    )


def _build(
    context: _RunContext,
    tmp_path: Path,
    *,
    controller: ControllerSpec | None = None,
    workspace: Path | None | object = ...,
    limits: RunLedgerLimits | None = None,
) -> VerifiedSweBenchRunLedger:
    selected_workspace = (
        _workspace(context, tmp_path)
        if workspace is ...
        else workspace
    )
    assert selected_workspace is None or isinstance(selected_workspace, Path)
    artifact_parent = tmp_path / "artifacts"
    artifact_parent.mkdir()
    return build_run_ledger(
        context.source,
        context.task_input,
        opaque_key=_OPAQUE_KEY,
        preparations=_preparations(context),
        workspaces=_workspaces(context, selected_workspace),
        system=_system(),
        controller=controller or _controller(context),
        artifact_parent=artifact_parent,
        limits=limits,
    )


def _verify(
    context: _RunContext,
    ledger: VerifiedSweBenchRunLedger,
) -> VerifiedSweBenchRunLedger:
    return verify_run_ledger(
        ledger,
        context.source,
        context.task_input,
        opaque_key=_OPAQUE_KEY,
        system=_system(),
        controller=ledger.controller,
    )


def _stream(payload: bytes, *, complete: bool = True) -> StreamEvidence:
    return StreamEvidence(
        observed_bytes=len(payload),
        captured_bytes=len(payload),
        captured_sha256=hashlib.sha256(payload).hexdigest(),
        prefix=payload,
        complete=complete,
    )


def _fake_result(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    environment: dict[str, str],
    limits: LiteralProcessLimits,
    stdout: bytes = b"",
    stdout_complete: bool = True,
    stderr: bytes = b"",
    stderr_complete: bool = True,
    exit_code: int | None = 0,
    termination_trigger: str | None = None,
    execution_error: str | None = None,
    cleanup_error: str | None = None,
    cleanup_detail: str | None = None,
) -> LiteralProcessResult:
    memory_scope = (
        "windows-job-process-and-aggregate"
        if limits.max_memory_mb is not None
        else "none"
    )
    containment_scope = (
        "windows-job-process-and-descendants"
        if os.name == "nt"
        else "posix-session-process-group-escapable"
    )
    return LiteralProcessResult(
        argv=argv,
        cwd=str(cwd.resolve(strict=True)),
        limits=limits,
        exit_code=exit_code,
        duration_seconds=0.01,
        setup_duration_seconds=0.001,
        process_duration_seconds=0.008,
        cleanup_duration_seconds=0.001,
        termination_trigger=termination_trigger,
        execution_error=execution_error,
        cleanup_error=cleanup_error,
        cleanup_detail=cleanup_detail,
        stdout=_stream(stdout, complete=stdout_complete),
        stderr=_stream(stderr, complete=stderr_complete),
        memory_limit_scope=memory_scope,
        containment_scope=containment_scope,
        environment_names=tuple(sorted(environment)),
        environment_sha256=_digest(environment),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_patch_bytes", 0),
        ("max_request_bytes", True),
        ("max_workspace_path_bytes", 16 * 1024 + 1),
        ("max_token_count", 10**12 + 1),
    ],
)
def test_run_limits_reject_invalid_scalar_bounds(field: str, value: object) -> None:
    with pytest.raises(SweBenchRunError):
        RunLedgerLimits(**{field: value})


def test_run_limits_reject_inconsistent_aggregate_bounds() -> None:
    with pytest.raises(SweBenchRunError, match="file_bytes"):
        RunLedgerLimits(
            max_workspace_file_bytes=2,
            max_workspace_total_bytes=1,
        )
    with pytest.raises(SweBenchRunError, match="per-event"):
        RunLedgerLimits(max_ledger_bytes=63, max_trajectory_events=1)


def test_constructor_protocol_and_shape_validation(run_context: _RunContext) -> None:
    with pytest.raises(SweBenchRunError, match="opaque_task_id"):
        CandidateWorkspace("not-opaque", None)
    with pytest.raises(SweBenchRunError, match="absolute"):
        CandidateWorkspace(run_context.opaque_ids[0], Path("relative"))
    base = {
        "argv": [str(run_context.python), str(run_context.controller_script)],
        "environment": {},
        "limits": LiteralProcessLimits(timeout_seconds=1),
        "executable_sha256": run_context.python_sha256,
        "executable_byte_count": run_context.python_byte_count,
    }
    with pytest.raises(SweBenchRunError, match="absolute"):
        ControllerSpec(**{**base, "argv": ["python"]})
    with pytest.raises(SweBenchRunError, match="plain mapping"):
        ControllerSpec(**{**base, "environment": ()})
    with pytest.raises(SweBenchRunError, match="protocol"):
        ControllerSpec(**{**base, "protocol": "not-the-protocol"})
    assert ControllerSpec(**base).protocol == CONTROLLER_PROTOCOL


def test_success_records_complete_denominator_literal_call_and_two_field_request(
    run_context: _RunContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_controller = _controller(run_context, mode="mutate")
    observed: dict[str, object] = {}
    real_run = run_module.run_literal_argv

    def observe(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        limits: LiteralProcessLimits,
    ) -> LiteralProcessResult:
        observed.update(
            argv=tuple(argv),
            cwd=cwd,
            environment=dict(environment),
            limits=limits,
        )
        return real_run(argv, cwd=cwd, environment=environment, limits=limits)

    monkeypatch.setattr(run_module, "run_literal_argv", observe)
    ledger = _build(run_context, tmp_path, controller=selected_controller)
    document = ledger.to_dict()
    assert document["schema"] == RUN_LEDGER_SCHEMA
    assert document["cohort"]["selected_count"] == 3
    assert document["cohort"]["launch_attempt_count"] == 1
    assert document["cohort"]["lifecycle_result_count"] == 1
    assert document["cohort"]["capture_count"] == 1
    assert [row["disposition"] for row in document["tasks"]] == [
        "run-captured",
        "repository-preparation-refused",
        "repository-not-attempted",
    ]
    assert document["cohort"]["tokens"] == {
        "measured_run_count": 1,
        "not_measured_run_count": 0,
        "prompt_tokens_total": 11,
        "model_tokens_total": 7,
    }

    row = document["tasks"][0]
    request_path = ledger.artifact_root / row["request"]["label"]
    request_bytes = request_path.read_bytes()
    request = json.loads(request_bytes)
    assert request_bytes == _canonical_bytes(request)
    assert set(request) == {"opaque_task_id", "problem_statement"}
    assert request == {
        "opaque_task_id": run_context.opaque_ids[0],
        "problem_statement": "Fix public problem 0 without gold.",
    }
    assert b"GOLD_" not in request_bytes
    workspace = Path(ledger.workspaces[0].path or "")
    assert observed["argv"] == (
        *selected_controller.argv,
        str(request_path),
        str(workspace.resolve(strict=True)),
    )
    assert observed["cwd"] == workspace
    assert observed["environment"] == dict(selected_controller.environment)
    assert observed["limits"] == selected_controller.limits
    assert row["workspace"]["delta"]["added_file_count"] == 1
    assert row["workspace"]["delta"]["added_directory_count"] == 1
    assert row["workspace"]["delta"]["modified_file_count"] == 1
    assert ledger.captures == ledger.prediction_captures()
    assert ledger.captures[0].model_patch == _PATCH


def test_captures_are_reconstructed_only_from_retained_raw_stdout(
    run_context: _RunContext,
    tmp_path: Path,
) -> None:
    ledger = _build(run_context, tmp_path)
    fabricated_patch = "FABRICATED"
    fabricated = CandidatePatchCapture(
        opaque_task_id=run_context.opaque_ids[0],
        status="recorded",
        model_patch=fabricated_patch,
        capture=RetainedCaptureEvidence(
            observed_bytes=len(fabricated_patch),
            captured_bytes=len(fabricated_patch),
            captured_sha256=hashlib.sha256(
                fabricated_patch.encode("utf-8")
            ).hexdigest(),
            complete=True,
        ),
    )
    forged_runtime = replace(ledger, captures=(fabricated,))
    replayed = _verify(run_context, forged_runtime)
    assert replayed.captures[0].model_patch == _PATCH
    assert replayed.captures != forged_runtime.captures


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("timeout", "execution-timeout"),
        ("nonzero", "nonzero-exit"),
        ("malformed", "malformed-controller-output"),
        ("stdout-overflow", "stdout-limit-exceeded"),
        ("stderr-overflow", "stderr-limit-exceeded"),
    ],
)
def test_controller_failures_are_totalized_with_raw_stream_evidence(
    run_context: _RunContext,
    tmp_path: Path,
    mode: str,
    expected: str,
) -> None:
    process_limits = LiteralProcessLimits(
        timeout_seconds=0.08 if mode == "timeout" else 5,
        poll_interval_seconds=0.002,
        max_stdout_bytes=64 if mode == "stdout-overflow" else 64 * 1024,
        max_stderr_bytes=64 if mode == "stderr-overflow" else 64 * 1024,
    )
    ledger = _build(
        run_context,
        tmp_path,
        controller=_controller(
            run_context,
            mode=mode,
            process_limits=process_limits,
        ),
    )
    row = ledger.to_dict()["tasks"][0]
    assert row["disposition"] == expected
    assert row["request"] is not None
    assert row["process"] is not None
    assert row["stdout"] is not None
    assert row["stderr"] is not None
    assert row["observation"]["status"] == "not-recorded"
    assert ledger.captures == ()
    if mode.endswith("overflow"):
        stream = row["stdout" if mode.startswith("stdout") else "stderr"]
        maximum = (
            process_limits.max_stdout_bytes
            if mode.startswith("stdout")
            else process_limits.max_stderr_bytes
        )
        assert stream["observed_bytes"] > maximum
        assert stream["captured_bytes"] == maximum + 1
        assert stream["complete"] is True


def test_oversized_patch_retains_only_bounded_capture_witness(
    run_context: _RunContext,
    tmp_path: Path,
) -> None:
    ledger = _build(
        run_context,
        tmp_path,
        controller=_controller(run_context, mode="oversized", patch_size=64),
        limits=RunLedgerLimits(max_patch_bytes=8),
    )
    row = ledger.to_dict()["tasks"][0]
    assert row["disposition"] == "candidate-output-oversized"
    assert row["observation"]["patch"] == {
        "observed_bytes": 64,
        "retained_prefix_bytes": 9,
        "retained_prefix_sha256": hashlib.sha256(b"X" * 9).hexdigest(),
    }
    assert len(ledger.captures) == 1
    capture = ledger.captures[0]
    assert capture.status == "oversized"
    assert capture.model_patch is None
    assert capture.capture is not None
    assert capture.capture.captured_bytes == 9
    assert capture.capture.complete is False


def test_incomplete_stream_is_not_treated_as_complete_controller_output(
    run_context: _RunContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller(run_context)
    valid = _canonical_bytes(
        {
            "schema": CONTROLLER_RESULT_SCHEMA,
            "opaque_task_id": run_context.opaque_ids[0],
            "model_patch": _PATCH,
            "tokens": {"method": "not_measured", "prompt": None, "model": None},
            "trajectory": [],
        }
    )

    def incomplete(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        limits: LiteralProcessLimits,
    ) -> LiteralProcessResult:
        return _fake_result(
            tuple(argv),
            cwd=cwd,
            environment=dict(environment),
            limits=limits,
            stdout=valid,
            stdout_complete=False,
            termination_trigger="stream_observation_failed",
            execution_error="fixture incomplete stream capture",
        )

    monkeypatch.setattr(run_module, "run_literal_argv", incomplete)
    ledger = _build(run_context, tmp_path, controller=controller)
    row = ledger.to_dict()["tasks"][0]
    assert row["stdout"]["complete"] is False
    assert row["disposition"] == "stream-observation-failed"
    assert row["observation"]["status"] == "not-recorded"
    assert ledger.captures == ()


def test_invalid_unicode_patch_is_totalized_as_malformed_output(
    run_context: _RunContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = json.dumps(
        {
            "schema": CONTROLLER_RESULT_SCHEMA,
            "opaque_task_id": run_context.opaque_ids[0],
            "model_patch": "\ud800",
            "tokens": {"method": "not_measured", "prompt": None, "model": None},
            "trajectory": [],
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")

    def invalid_patch(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        limits: LiteralProcessLimits,
    ) -> LiteralProcessResult:
        return _fake_result(
            tuple(argv),
            cwd=cwd,
            environment=dict(environment),
            limits=limits,
            stdout=encoded,
        )

    monkeypatch.setattr(run_module, "run_literal_argv", invalid_patch)
    ledger = _build(run_context, tmp_path)
    row = ledger.to_dict()["tasks"][0]
    assert row["disposition"] == "malformed-controller-output"
    assert row["observation"]["failure_code"] == "malformed-controller-output"
    assert ledger.captures == ()


def test_launch_error_is_totalized(
    run_context: _RunContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_launch(*args: object, **kwargs: object) -> LiteralProcessResult:
        raise LiteralProcessError("fixture launch failure")

    monkeypatch.setattr(run_module, "run_literal_argv", fail_launch)
    ledger = _build(run_context, tmp_path)
    row = ledger.to_dict()["tasks"][0]
    assert row["disposition"] == "launch-failed"
    assert row["process"] is None
    assert row["stdout"]["captured_bytes"] == 0
    assert row["stderr"]["captured_bytes"] == 0
    assert row["stdout"]["complete"] is False
    assert row["stderr"]["complete"] is False
    assert row["workspace"]["final"] is not None
    assert row["workspace"]["final_failure_code"] is None


@pytest.mark.parametrize(
    ("process_kind", "expected"),
    [
        ("launch", "launch-failed"),
        ("timeout", "execution-timeout"),
        ("nonzero", "nonzero-exit"),
        ("success", "workspace-observation-failed"),
    ],
)
def test_process_failure_precedence_survives_final_workspace_scan_failure(
    run_context: _RunContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_kind: str,
    expected: str,
) -> None:
    real_snapshot = run_module._snapshot_workspace
    snapshot_calls = 0

    def fail_after_first(path: Path, *, limits: RunLedgerLimits):
        nonlocal snapshot_calls
        snapshot_calls += 1
        if snapshot_calls == 2:
            raise SweBenchRunError("fixture final scan failure")
        return real_snapshot(path, limits=limits)

    def outcome(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        limits: LiteralProcessLimits,
    ) -> LiteralProcessResult:
        if process_kind == "launch":
            raise LiteralProcessError("fixture launch failure")
        return _fake_result(
            tuple(argv),
            cwd=cwd,
            environment=dict(environment),
            limits=limits,
            exit_code=(
                None
                if process_kind == "timeout"
                else 0 if process_kind == "success" else 7
            ),
            termination_trigger="timeout" if process_kind == "timeout" else None,
        )

    monkeypatch.setattr(run_module, "_snapshot_workspace", fail_after_first)
    monkeypatch.setattr(run_module, "run_literal_argv", outcome)
    ledger = _build(run_context, tmp_path)
    row = ledger.to_dict()["tasks"][0]
    assert row["disposition"] == expected
    assert row["workspace"]["final"] is None
    assert row["workspace"]["delta"] is None
    assert row["workspace"]["final_failure_code"] == (
        "workspace-observation-failed"
    )
    if process_kind == "launch":
        assert row["process"] is None
        assert row["stdout"]["complete"] is False
        assert row["stderr"]["complete"] is False
    else:
        assert row["process"] is not None


@pytest.mark.parametrize(
    ("termination_trigger", "cleanup_error", "expected"),
    [
        ("process_observation_failed", None, "process-observation-failed"),
        (None, "fixture-cleanup-failure", "cleanup-failed"),
    ],
)
def test_additional_literal_process_failures_are_totalized(
    run_context: _RunContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    termination_trigger: str | None,
    cleanup_error: str | None,
    expected: str,
) -> None:
    def outcome(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        limits: LiteralProcessLimits,
    ) -> LiteralProcessResult:
        return _fake_result(
            tuple(argv),
            cwd=cwd,
            environment=dict(environment),
            limits=limits,
            exit_code=None if termination_trigger is not None else 0,
            termination_trigger=termination_trigger,
            execution_error=(
                "fixture process observation failure"
                if termination_trigger is not None
                else None
            ),
            cleanup_error=cleanup_error,
            cleanup_detail=(
                "fixture cleanup failure detail"
                if cleanup_error is not None
                else None
            ),
        )

    monkeypatch.setattr(run_module, "run_literal_argv", outcome)
    ledger = _build(run_context, tmp_path)
    row = ledger.to_dict()["tasks"][0]
    assert row["disposition"] == expected
    assert row["workspace"]["final_failure_code"] is None
    assert row["process"] is not None
    assert row["process"]["termination_trigger"] == termination_trigger
    assert row["process"]["cleanup_error"] == cleanup_error


def test_workspace_unavailable_mismatch_and_hardlinks_are_totalized(
    run_context: _RunContext,
    tmp_path: Path,
) -> None:
    unavailable_root = tmp_path / "unavailable"
    unavailable_root.mkdir()
    unavailable = _build(run_context, unavailable_root, workspace=None)
    assert unavailable.to_dict()["tasks"][0]["disposition"] == "workspace-unavailable"

    mismatch_root = tmp_path / "mismatch"
    mismatch_root.mkdir()
    mismatch_workspace = _workspace(run_context, mismatch_root)
    (mismatch_workspace / "module.py").write_text("VALUE = 999\n", encoding="utf-8")
    mismatch = _build(
        run_context,
        mismatch_root,
        workspace=mismatch_workspace,
    )
    assert mismatch.to_dict()["tasks"][0]["disposition"] == (
        "workspace-initial-mismatch"
    )
    forged_mismatch = mismatch.to_dict()
    forged_mismatch["tasks"][0]["observation"]["failure_code"] = (
        "workspace-inspection-failed"
    )
    _resign(forged_mismatch)
    with pytest.raises(SweBenchRunError, match="evidence/failure code"):
        decode_run_ledger(forged_mismatch)

    hardlink_root = tmp_path / "hardlink"
    hardlink_root.mkdir()
    hardlink_workspace = _workspace(run_context, hardlink_root)
    os.link(
        hardlink_workspace / "module.py",
        hardlink_workspace / "module-alias.py",
    )
    hardlink = _build(
        run_context,
        hardlink_root,
        workspace=hardlink_workspace,
    )
    row = hardlink.to_dict()["tasks"][0]
    assert row["disposition"] == "workspace-initial-mismatch"
    assert row["observation"]["failure_code"] == "workspace-inspection-failed"
    forged_inspection = hardlink.to_dict()
    forged_inspection["tasks"][0]["observation"]["failure_code"] = (
        "workspace-initial-mismatch"
    )
    _resign(forged_inspection)
    with pytest.raises(SweBenchRunError, match="evidence/failure code"):
        decode_run_ledger(forged_inspection)


def test_workspace_and_artifact_boundaries_cannot_overlap(
    run_context: _RunContext,
    tmp_path: Path,
) -> None:
    workspace = _workspace(run_context, tmp_path)
    with pytest.raises(SweBenchRunError, match="overlaps"):
        build_run_ledger(
            run_context.source,
            run_context.task_input,
            opaque_key=_OPAQUE_KEY,
            preparations=_preparations(run_context),
            workspaces=_workspaces(run_context, workspace),
            system=_system(),
            controller=_controller(run_context),
            artifact_parent=workspace,
        )

    with pytest.raises(SweBenchRunError, match="prepared tree"):
        build_run_ledger(
            run_context.source,
            run_context.task_input,
            opaque_key=_OPAQUE_KEY,
            preparations=_preparations(run_context),
            workspaces=_workspaces(run_context, None),
            system=_system(),
            controller=_controller(run_context),
            artifact_parent=run_context.prepared.path,
        )


def test_lexical_workspace_root_link_is_rejected(
    run_context: _RunContext,
    tmp_path: Path,
) -> None:
    workspace = _workspace(run_context, tmp_path, "real-workspace")
    linked_workspace = tmp_path / "linked-workspace"
    try:
        linked_workspace.symlink_to(workspace, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks are unavailable: {type(exc).__name__}")
    artifact_parent = tmp_path / "artifacts"
    artifact_parent.mkdir()
    with pytest.raises(SweBenchRunError, match="could not be inspected"):
        build_run_ledger(
            run_context.source,
            run_context.task_input,
            opaque_key=_OPAQUE_KEY,
            preparations=_preparations(run_context),
            workspaces=_workspaces(run_context, linked_workspace),
            system=_system(),
            controller=_controller(run_context),
            artifact_parent=artifact_parent,
        )

def test_controller_digest_and_request_limits_fail_closed_before_launch(
    run_context: _RunContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = _controller(run_context)
    wrong_controller = ControllerSpec(
        argv=list(selected.argv),
        environment=dict(selected.environment),
        limits=selected.limits,
        executable_sha256="0" * 64,
        executable_byte_count=selected.executable_byte_count,
    )
    with pytest.raises(SweBenchRunError, match="evidence mismatch"):
        _build(run_context, tmp_path / "digest", controller=wrong_controller)

    def must_not_launch(*args: object, **kwargs: object) -> LiteralProcessResult:
        pytest.fail("controller launched after request limit failure")

    monkeypatch.setattr(run_module, "run_literal_argv", must_not_launch)
    with pytest.raises(SweBenchRunError, match="request .* byte limit"):
        _build(
            run_context,
            tmp_path / "request",
            limits=RunLedgerLimits(max_request_bytes=1),
        )


def test_schema_self_hash_and_fixed_false_claims_reject_forgery(
    run_context: _RunContext,
    tmp_path: Path,
) -> None:
    ledger = _build(run_context, tmp_path)
    document = ledger.to_dict()
    claimed = document["run_ledger_sha256"]
    unsigned = copy.deepcopy(document)
    unsigned.pop("run_ledger_sha256")
    assert claimed == _digest(unsigned)
    assert ledger.claim_ready is False

    extra = copy.deepcopy(document)
    extra["unexpected"] = True
    with pytest.raises(SweBenchRunError, match="fields"):
        decode_run_ledger(extra)

    wrong_schema = copy.deepcopy(document)
    wrong_schema["schema"] = "ctxc-swebench-run-ledger-999"
    _resign(wrong_schema)
    with pytest.raises(SweBenchRunError, match="schema"):
        decode_run_ledger(wrong_schema)

    stale_hash = copy.deepcopy(document)
    stale_hash["cohort"]["selected_count"] = 2
    with pytest.raises(SweBenchRunError):
        decode_run_ledger(stale_hash)

    for claim in (
        "candidate_execution_authenticated",
        "candidate_mount_created",
        "filesystem_isolation_verified",
        "network_isolation_verified",
        "user_isolation_verified",
        "pid_isolation_verified",
        "immutable_image_verified",
        "controller_producer_authenticated",
        "system_identity_authenticated",
        "model_identity_authenticated",
        "trajectory_authenticated",
        "token_counts_authenticated",
        "hidden_tests_applied",
        "grading_performed",
        "resolution_computed",
        "external_score_computed",
        "usefulness_measured",
        "claim_ready",
        "self_hash_authenticates_producer",
    ):
        forged = copy.deepcopy(document)
        forged["evidence_state"][claim] = True
        _resign(forged)
        with pytest.raises(SweBenchRunError, match=claim):
            decode_run_ledger(forged)


@pytest.mark.parametrize(
    ("process_updates", "message"),
    [
        (
            {
                "termination_trigger": "future_trigger",
                "execution_error": "fixture future failure",
            },
            "termination_trigger is invalid",
        ),
        ({"execution_error": "fixture stray failure"}, "trigger/error evidence"),
        (
            {
                "termination_trigger": "process_observation_failed",
                "execution_error": None,
            },
            "trigger/error evidence",
        ),
        (
            {
                "termination_trigger": "stream_observation_failed",
                "execution_error": "fixture stream observation failure",
            },
            "requires an incomplete stream",
        ),
        (
            {
                "cleanup_error": "fixture_cleanup_failed",
                "cleanup_detail": None,
            },
            "cleanup error/detail evidence",
        ),
        ({"exit_code": None}, "lacks an exit code"),
    ],
)
def test_resigned_impossible_process_states_are_rejected(
    run_context: _RunContext,
    tmp_path: Path,
    process_updates: dict[str, object],
    message: str,
) -> None:
    ledger = _build(run_context, tmp_path)
    document = ledger.to_dict()
    document["tasks"][0]["process"].update(process_updates)
    _resign(document)
    with pytest.raises(SweBenchRunError, match=message):
        decode_run_ledger(document)


def test_resigned_prepared_row_cannot_be_relabelled_as_not_attempted(
    run_context: _RunContext,
    tmp_path: Path,
) -> None:
    ledger = _build(run_context, tmp_path)
    document = ledger.to_dict()
    row = document["tasks"][0]
    row["disposition"] = "repository-not-attempted"
    row["request"] = None
    row["stdout"] = None
    row["stderr"] = None
    row["process"] = None
    row["workspace"]["final"] = None
    row["workspace"]["delta"] = None
    row["observation"] = copy.deepcopy(document["tasks"][2]["observation"])
    cohort = document["cohort"]
    cohort["launch_attempt_count"] = 0
    cohort["lifecycle_result_count"] = 0
    cohort["capture_count"] = 0
    cohort["disposition_counts"]["run-captured"] = 0
    cohort["disposition_counts"]["repository-not-attempted"] = 2
    cohort["tokens"] = {
        "measured_run_count": 0,
        "not_measured_run_count": 0,
        "prompt_tokens_total": 0,
        "model_tokens_total": 0,
    }
    document["evidence_state"]["controller_launch_attempted"] = False
    document["evidence_state"]["controller_lifecycle_result_observed"] = False
    _resign(document)
    with pytest.raises(SweBenchRunError, match="exact prelaunch outcome"):
        decode_run_ledger(document)


def test_resigned_unlaunched_rows_cannot_gain_workspace_evidence(
    run_context: _RunContext,
    tmp_path: Path,
) -> None:
    successful_root = tmp_path / "successful"
    successful_root.mkdir()
    successful = _build(run_context, successful_root)
    successful_document = successful.to_dict()
    final = successful_document["tasks"][0]["workspace"]["final"]
    delta = successful_document["tasks"][0]["workspace"]["delta"]
    assert final is not None
    assert delta is not None

    for ordinal in (1, 2):
        forged = copy.deepcopy(successful_document)
        forged["tasks"][ordinal]["workspace"]["final"] = copy.deepcopy(final)
        forged["tasks"][ordinal]["workspace"]["delta"] = copy.deepcopy(delta)
        _resign(forged)
        with pytest.raises(SweBenchRunError, match="candidate state"):
            decode_run_ledger(forged)

    unavailable_root = tmp_path / "unavailable"
    unavailable_root.mkdir()
    unavailable = _build(run_context, unavailable_root, workspace=None)
    forged = unavailable.to_dict()
    forged["tasks"][0]["workspace"]["final"] = copy.deepcopy(final)
    forged["tasks"][0]["workspace"]["delta"] = copy.deepcopy(delta)
    _resign(forged)
    with pytest.raises(SweBenchRunError, match="retains workspace evidence"):
        decode_run_ledger(forged)


def test_resigned_launch_failure_cannot_claim_complete_streams(
    run_context: _RunContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_launch(*args: object, **kwargs: object) -> LiteralProcessResult:
        raise LiteralProcessError("fixture launch failure")

    monkeypatch.setattr(run_module, "run_literal_argv", fail_launch)
    ledger = _build(run_context, tmp_path)
    document = ledger.to_dict()
    document["tasks"][0]["stdout"]["complete"] = True
    _resign(document)
    with pytest.raises(SweBenchRunError, match="unobserved streams"):
        decode_run_ledger(document)


def test_resigned_observation_forgery_is_rejected_by_raw_stdout_replay(
    run_context: _RunContext,
    tmp_path: Path,
) -> None:
    ledger = _build(run_context, tmp_path)
    document = ledger.to_dict()
    document["tasks"][0]["observation"]["patch"]["sha256"] = "0" * 64
    _resign(document)
    forged = replace(ledger, document=document)
    with pytest.raises(SweBenchRunError, match="output evidence mismatch"):
        _verify(run_context, forged)


def test_resigned_success_cannot_hide_stdout_beyond_the_process_limit(
    run_context: _RunContext,
    tmp_path: Path,
) -> None:
    process_limits = LiteralProcessLimits(
        timeout_seconds=5,
        poll_interval_seconds=0.005,
        max_stdout_bytes=1024,
        max_stderr_bytes=1024,
    )
    ledger = _build(
        run_context,
        tmp_path,
        controller=_controller(run_context, process_limits=process_limits),
    )
    document = ledger.to_dict()
    row = document["tasks"][0]
    stdout_path = ledger.artifact_root / row["stdout"]["artifact"]["label"]
    original = stdout_path.read_bytes()
    assert len(original) < process_limits.max_stdout_bytes
    forged_stdout = original + b" " * (
        process_limits.max_stdout_bytes + 1 - len(original)
    )
    stdout_path.write_bytes(forged_stdout)
    row["stdout"].update(
        {
            "observed_bytes": len(forged_stdout),
            "captured_bytes": len(forged_stdout),
            "complete": True,
        }
    )
    row["stdout"]["artifact"].update(
        {
            "byte_count": len(forged_stdout),
            "sha256": hashlib.sha256(forged_stdout).hexdigest(),
        }
    )
    _resign(document)
    forged = replace(ledger, document=document)
    with pytest.raises(SweBenchRunError, match="impossible stream"):
        _verify(run_context, forged)


def test_write_load_live_replay_and_artifact_substitution_safety(
    run_context: _RunContext,
    tmp_path: Path,
) -> None:
    ledger = _build(run_context, tmp_path / "build")
    output = tmp_path / "ledger.json"
    write_run_ledger(
        output,
        ledger,
        run_context.source,
        run_context.task_input,
        opaque_key=_OPAQUE_KEY,
        system=_system(),
        controller=ledger.controller,
    )
    loaded = load_run_ledger(
        output,
        ledger.artifact_root,
        run_context.source,
        run_context.task_input,
        opaque_key=_OPAQUE_KEY,
        preparations=ledger.preparations,
        workspaces=ledger.workspaces,
        system=_system(),
        controller=ledger.controller,
    )
    assert loaded.to_dict() == ledger.to_dict()
    assert loaded.captures == ledger.captures

    linked_output = tmp_path / "ledger-hardlink.json"
    os.link(output, linked_output)
    with pytest.raises(SweBenchRunError, match="single-link"):
        load_run_ledger(
            linked_output,
            ledger.artifact_root,
            run_context.source,
            run_context.task_input,
            opaque_key=_OPAQUE_KEY,
            preparations=ledger.preparations,
            workspaces=ledger.workspaces,
            system=_system(),
            controller=ledger.controller,
        )


def test_live_replay_rejects_raw_artifact_tampering_and_extra_paths(
    run_context: _RunContext,
    tmp_path: Path,
) -> None:
    tampered = _build(run_context, tmp_path / "tampered")
    row = tampered.to_dict()["tasks"][0]
    stdout_path = tampered.artifact_root / row["stdout"]["artifact"]["label"]
    stdout_path.write_bytes(stdout_path.read_bytes() + b" ")
    with pytest.raises(SweBenchRunError):
        _verify(run_context, tampered)

    extra = _build(run_context, tmp_path / "extra")
    (extra.artifact_root / "rogue.bin").write_bytes(b"rogue")
    with pytest.raises(SweBenchRunError, match="entry limit|path set"):
        _verify(run_context, extra)


def test_run_ledger_output_cannot_enter_candidate_or_artifact_boundaries(
    run_context: _RunContext,
    tmp_path: Path,
) -> None:
    ledger = _build(run_context, tmp_path)
    workspace = Path(ledger.workspaces[0].path or "")
    for output in (
        ledger.artifact_root / "ledger.json",
        workspace / "ledger.json",
        run_context.prepared.path / "ledger.json",
        run_context.prepared.mirror.path / "ledger.json",
    ):
        with pytest.raises(SweBenchRunError, match="outside"):
            write_run_ledger(
                output,
                ledger,
                run_context.source,
                run_context.task_input,
                opaque_key=_OPAQUE_KEY,
                system=_system(),
                controller=ledger.controller,
            )


def test_prediction_ledger_accepts_only_replayed_run_captures(
    run_context: _RunContext,
    tmp_path: Path,
) -> None:
    run_ledger = _build(run_context, tmp_path / "run")
    replayed = _verify(run_context, run_ledger)
    prediction = build_prediction_ledger(
        run_context.source,
        run_context.task_input,
        opaque_key=_OPAQUE_KEY,
        preparations=_preparations(run_context),
        captures=replayed.prediction_captures(),
        system=_system(),
        jsonl_path=tmp_path / "predictions.jsonl",
    )
    rows = [
        json.loads(line)
        for line in prediction.jsonl_path.read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0] == {
        "instance_id": "example__project-1",
        "model_name_or_path": "fixture-agent",
        "model_patch": _PATCH,
    }
    assert [row["model_patch"] for row in rows[1:]] == [None, None]
    assert prediction.document["cohort"]["prediction_count"] == 1
    assert prediction.document["cohort"]["nonprediction_count"] == 2

    empty = build_prediction_ledger(
        run_context.source,
        run_context.task_input,
        opaque_key=_OPAQUE_KEY,
        preparations=_preparations(run_context),
        captures=(),
        system=_system(),
        jsonl_path=tmp_path / "missing.jsonl",
    )
    assert empty.document["cohort"]["prediction_count"] == 0
    assert len(empty.jsonl_path.read_text(encoding="utf-8").splitlines()) == 3
