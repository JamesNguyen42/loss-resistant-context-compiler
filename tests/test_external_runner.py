from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

import benchmarks.external_runner as external_runner_module
from benchmarks.external_runner import (
    AdapterEntrypointEvidence,
    AdapterRuntimeEvidence,
    DependencyLockEvidence,
    ExternalRunnerError,
    InferenceServiceAccounting,
    InferenceServiceContract,
    NetworkIsolationEvidence,
    RunnerIdentity,
    RunnerLimits,
    capture_adapter_entrypoint_evidence,
    capture_adapter_source_evidence,
    capture_dependency_lock_evidence,
    capture_inference_service_contract,
    capture_network_isolation_evidence,
    capture_process_environment_evidence,
    load_external_run_manifest,
    run_external_cases,
    run_external_command,
)
from benchmarks.external_runner import main as runner_main
from benchmarks.json_io import StrictJsonLimits
from benchmarks.lrcbench import (
    CANDIDATE_SCHEMA,
    CORPUS_PRODUCER_SCHEMA,
    BenchmarkConfig,
    CorpusProducerMetadata,
    ExternalBaselineError,
    _canonical_sha256,
    corpus_document,
    dataset_digest,
    decode_corpus_document,
    generate_histories,
    load_external_candidates,
    run_benchmark,
)
from context_compiler.local_qwen import QWEN_Q4_VARIANT
from tests.protocol_fixtures import (
    FIXTURE_DEPENDENCY_LOCK_CONTENT,
    FIXTURE_DEPENDENCY_LOCK_SHA256,
    FIXTURE_ENVIRONMENT_ID,
    FIXTURE_NETWORK_ISOLATION_CONTENT,
    FIXTURE_NETWORK_ISOLATION_MODE,
    FIXTURE_NETWORK_ISOLATION_SHA256,
    write_frozen_external_protocol,
)

FIXTURE_ADAPTER_REVISION = "a" * 40
# CPython on Darwin maps more than 256 MiB before adapter code runs. Keep the
# functional subprocess tests hard-bounded without confusing runtime startup
# address space with adapter behavior. The launcher contract test below still
# verifies exact propagation of a deliberately small 256 MiB ceiling.
_FUNCTIONAL_ADAPTER_MEMORY_MB = (
    32_768 if sys.platform == "darwin" else 256
)


@pytest.mark.parametrize(
    ("platform_name", "max_memory_mb", "process_succeeded", "expected"),
    (
        ("linux", None, False, False),
        ("darwin", None, True, False),
        ("linux", 256, False, True),
        ("win32", 256, False, True),
        ("darwin", 256, False, False),
        ("darwin", 256, True, True),
    ),
)
def test_memory_limit_attestation_is_conservative_on_darwin_failures(
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
    max_memory_mb: int | None,
    process_succeeded: bool,
    expected: bool,
) -> None:
    monkeypatch.setattr(external_runner_module.sys, "platform", platform_name)

    assert external_runner_module._memory_limit_attested(
        RunnerLimits(max_memory_mb=max_memory_mb),
        process_succeeded=process_succeeded,
    ) is expected


def corpus_producer(
    *,
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> CorpusProducerMetadata:
    return CorpusProducerMetadata(
        created_at=created_at,
        repository_commit=None,
        repository_dirty=None,
        package_version="0.1.0",
        python_version="test-python",
        platform="test-platform",
        command=("pytest", "external-runner"),
    )


def write_corpus(
    path: Path,
    *,
    histories: int = 1,
) -> tuple[BenchmarkConfig, dict]:
    config = BenchmarkConfig(
        histories=histories,
        messages_per_history=24,
        noise_lines_per_message=1,
        token_budget=900,
        bootstrap_samples=100,
    )
    cases = generate_histories(config)
    document = corpus_document(
        cases,
        config,
        dataset_digest(cases, config),
        producer=corpus_producer(),
    )
    path.write_text(json.dumps(document), encoding="utf-8")
    return config, document


def valid_adapter_command(directory: Path) -> list[str]:
    program = (
        "import json,sys;"
        "corpus=json.load(open(sys.argv[1],encoding='utf-8'));"
        "payload={'schema':'lrcbench-candidate-output-0.1',"
        "'dataset_sha256':corpus['dataset_sha256'],'system':sys.argv[3],"
        "'cases':[{'case_id':case['case_id'],'rendered_text':'','claims':[]}"
        " for case in corpus['cases']]};"
        "json.dump(payload,open(sys.argv[2],'w',encoding='utf-8'))"
    )
    adapter_directory = directory / "adapter-source"
    adapter_directory.mkdir(exist_ok=True)
    entrypoint = adapter_directory / "valid-adapter.py"
    entrypoint.write_text(program, encoding="utf-8")
    return [
        sys.executable,
        str(entrypoint),
        "{corpus}",
        "{candidate}",
        "{system}",
    ]


def claim_identity() -> RunnerIdentity:
    return RunnerIdentity(
        adapter_revision=FIXTURE_ADAPTER_REVISION,
        environment_id=FIXTURE_ENVIRONMENT_ID,
        model_id=QWEN_Q4_VARIANT,
        model_context_length=8192,
        tokenizer_id="character-estimate-v1",
        inference_concurrency=1,
        retry_count=0,
        model_service_cost_usd=0.0,
    )


def retained_network_isolation(tmp_path: Path):
    evidence_path = tmp_path / "network-isolation.txt"
    evidence_path.write_bytes(FIXTURE_NETWORK_ISOLATION_CONTENT)
    evidence = capture_network_isolation_evidence(
        FIXTURE_NETWORK_ISOLATION_MODE,
        evidence_path,
    )
    assert evidence.evidence_sha256 == FIXTURE_NETWORK_ISOLATION_SHA256
    return evidence


def retained_dependency_lock(tmp_path: Path):
    evidence_path = tmp_path / "requirements.lock"
    evidence_path.write_bytes(FIXTURE_DEPENDENCY_LOCK_CONTENT)
    evidence = capture_dependency_lock_evidence(evidence_path)
    assert evidence.evidence_sha256 == FIXTURE_DEPENDENCY_LOCK_SHA256
    return evidence


def retained_adapter_entrypoint(tmp_path: Path):
    command = valid_adapter_command(tmp_path)
    evidence = capture_adapter_entrypoint_evidence(command[1])
    assert evidence.entrypoint_path == str(Path(command[1]).resolve())
    return evidence


def retained_adapter_source(tmp_path: Path):
    command = valid_adapter_command(tmp_path)
    evidence = capture_adapter_source_evidence(Path(command[1]).parent)
    assert evidence.file_count == 1
    assert evidence.files[0].relative_path == "valid-adapter.py"
    return evidence


def retained_inference_service():
    return capture_inference_service_contract(
        os.getpid(),
        max_memory_mb=4_096,
    )


def test_claim_identity_requires_frozen_model_and_environment_contract() -> None:
    identity = claim_identity()
    safe_environment = capture_process_environment_evidence(
        {"LRCBENCH_MODE": "frozen"}
    )
    changed_environment = capture_process_environment_evidence(
        {"LRCBENCH_MODE": "diagnostic"}
    )
    sensitive_environment = capture_process_environment_evidence(
        {"OPENAI_API_KEY": "not-retained"}
    )

    assert identity.claim_metadata_complete
    assert safe_environment.claim_evidence_complete
    assert (
        safe_environment.environment_sha256
        != changed_environment.environment_sha256
    )
    assert safe_environment.variable_names == ("LRCBENCH_MODE",)
    assert not sensitive_environment.claim_evidence_complete
    with pytest.raises(
        ExternalRunnerError,
        match="portable ASCII identifiers",
    ):
        capture_process_environment_evidence({"NOT-PORTABLE": "value"})
    assert not replace(identity, model_id="another/model").claim_metadata_complete
    assert not replace(
        identity,
        environment_id="fixture-environment",
    ).claim_metadata_complete
    assert not replace(
        identity,
        adapter_revision="fixture-revision",
    ).claim_metadata_complete
    assert not replace(identity, model_context_length=4096).claim_metadata_complete
    assert not replace(identity, tokenizer_id="other-tokenizer").claim_metadata_complete
    assert not replace(identity, inference_concurrency=2).claim_metadata_complete
    assert not replace(identity, retry_count=1).claim_metadata_complete
    assert not replace(identity, model_service_cost_usd=0.01).claim_metadata_complete
    with pytest.raises(TypeError, match="model_service_cost_usd must be numeric"):
        replace(identity, model_service_cost_usd=None)  # type: ignore[arg-type]


def test_inference_service_contract_captures_stable_process_identity() -> None:
    contract = retained_inference_service()

    assert contract.enabled
    assert contract.process_id == os.getpid()
    assert Path(contract.executable_path or "").is_absolute()
    assert contract.executable_sha256 is not None
    assert len(contract.executable_sha256) == 64
    assert contract.executable_bytes > 0
    assert contract.memory_metric in {
        "resident-set-bytes",
        "working-set-bytes",
    }
    with pytest.raises(
        ExternalRunnerError,
        match="requires a positive process ID",
    ):
        capture_inference_service_contract(None, max_memory_mb=4_096)
    with pytest.raises(
        ExternalRunnerError,
        match="requires a positive memory ceiling",
    ):
        capture_inference_service_contract(os.getpid())


def test_darwin_inference_snapshot_binds_identity_path_and_rss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "darwin-service"
    executable.write_bytes(b"fixture executable")
    calls: list[int] = []

    class FakeLibproc:
        def proc_pidinfo(
            self,
            process_id: int,
            flavor: int,
            _argument: int,
            pointer: object,
            byte_count: int,
        ) -> int:
            calls.append(flavor)
            information = pointer._obj  # type: ignore[attr-defined]
            if flavor == external_runner_module._DARWIN_PROC_PIDTBSDINFO:
                information.pbi_pid = process_id
                information.pbi_start_tvsec = 1_700_000_000
                information.pbi_start_tvusec = 123_456
            elif flavor == external_runner_module._DARWIN_PROC_PIDTASKINFO:
                information.pti_resident_size = 64 * 1024 * 1024
            else:
                raise AssertionError(f"unexpected proc_pidinfo flavor: {flavor}")
            return byte_count

        def proc_pidpath(
            self,
            _process_id: int,
            buffer: object,
            _byte_count: int,
        ) -> int:
            encoded = os.fsencode(executable.resolve())
            buffer.value = encoded  # type: ignore[attr-defined]
            return len(encoded)

    monkeypatch.setattr(
        external_runner_module,
        "_darwin_inference_process_api",
        lambda: FakeLibproc(),
    )

    snapshot = external_runner_module._darwin_inference_process_snapshot(42)

    assert ctypes.sizeof(external_runner_module._DarwinProcBsdInfo) == 136
    assert ctypes.sizeof(external_runner_module._DarwinProcTaskInfo) == 96
    assert snapshot == external_runner_module._InferenceProcessSnapshot(
        process_id=42,
        process_start_token="darwin-proc-start:1700000000:123456",
        executable_path=str(executable.resolve()),
        memory_metric="resident-set-bytes",
        memory_bytes=64 * 1024 * 1024,
    )
    assert calls == [
        external_runner_module._DARWIN_PROC_PIDTBSDINFO,
        external_runner_module._DARWIN_PROC_PIDTASKINFO,
        external_runner_module._DARWIN_PROC_PIDTBSDINFO,
    ]


def test_darwin_inference_snapshot_rejects_identity_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "darwin-service"
    executable.write_bytes(b"fixture executable")
    identity_samples = iter((1_700_000_000, 1_700_000_001))

    class RestartedLibproc:
        def proc_pidinfo(
            self,
            process_id: int,
            flavor: int,
            _argument: int,
            pointer: object,
            byte_count: int,
        ) -> int:
            information = pointer._obj  # type: ignore[attr-defined]
            if flavor == external_runner_module._DARWIN_PROC_PIDTBSDINFO:
                information.pbi_pid = process_id
                information.pbi_start_tvsec = next(identity_samples)
                information.pbi_start_tvusec = 0
            else:
                information.pti_resident_size = 1
            return byte_count

        def proc_pidpath(
            self,
            _process_id: int,
            buffer: object,
            _byte_count: int,
        ) -> int:
            encoded = os.fsencode(executable.resolve())
            buffer.value = encoded  # type: ignore[attr-defined]
            return len(encoded) + 1

    monkeypatch.setattr(
        external_runner_module,
        "_darwin_inference_process_api",
        lambda: RestartedLibproc(),
    )

    with pytest.raises(
        ExternalRunnerError,
        match="identity changed during sampling",
    ):
        external_runner_module._darwin_inference_process_snapshot(42)


def test_darwin_inference_snapshot_dispatches_without_weak_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = external_runner_module._InferenceProcessSnapshot(
        process_id=42,
        process_start_token="darwin-proc-start:1:2",
        executable_path="/usr/bin/service",
        memory_metric="resident-set-bytes",
        memory_bytes=1,
    )
    monkeypatch.setattr(external_runner_module.os, "name", "posix")
    monkeypatch.setattr(external_runner_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        external_runner_module,
        "_darwin_inference_process_snapshot",
        lambda process_id: expected
        if process_id == 42
        else (_ for _ in ()).throw(AssertionError("unexpected process id")),
    )

    assert external_runner_module._inference_process_snapshot(42) == expected


def _path_evidence_builders(
    path: str,
    source_evidence: external_runner_module.AdapterSourceEvidence,
) -> tuple[Callable[[], object], ...]:
    digest = "a" * 64
    return (
        lambda: DependencyLockEvidence(
            evidence_path=path,
            evidence_sha256=digest,
            evidence_bytes=1,
        ),
        lambda: AdapterEntrypointEvidence(
            entrypoint_path=path,
            entrypoint_sha256=digest,
            entrypoint_bytes=1,
        ),
        lambda: replace(source_evidence, source_root=path),
        lambda: AdapterRuntimeEvidence(
            executable_path=path,
            executable_sha256=digest,
            executable_bytes=1,
        ),
        lambda: NetworkIsolationEvidence(
            mode="host-firewall",
            evidence_path=path,
            evidence_sha256=digest,
            evidence_bytes=1,
        ),
        lambda: InferenceServiceContract(
            process_id=1,
            process_start_token="start-token",
            executable_path=path,
            executable_sha256=digest,
            executable_bytes=1,
            memory_metric="working-set-bytes",
            max_memory_mb=1,
        ),
        lambda: InferenceServiceAccounting(
            process_id=1,
            process_start_token="start-token",
            executable_path=path,
            executable_sha256=digest,
            executable_bytes=1,
            memory_metric="working-set-bytes",
            max_memory_mb=1,
            sample_count=2,
            peak_memory_bytes=1,
        ),
    )


def _adapter_source_evidence(
    tmp_path: Path,
) -> external_runner_module.AdapterSourceEvidence:
    source_root = tmp_path / "adapter-source"
    source_root.mkdir()
    (source_root / "adapter.py").write_text("print('fixture')\n", encoding="utf-8")
    return capture_adapter_source_evidence(source_root)


@pytest.mark.parametrize(
    ("path", "flavor"),
    [
        ("/var/lib/ctxc/evidence.bin", "posix"),
        (r"C:\ctxc\evidence.bin", "windows"),
        ("C:/ctxc/evidence.bin", "windows"),
        (r"\\server\share\ctxc\evidence.bin", "windows"),
    ],
)
def test_retained_evidence_accepts_canonical_cross_platform_absolute_paths(
    tmp_path: Path,
    path: str,
    flavor: str,
) -> None:
    source_evidence = _adapter_source_evidence(tmp_path)

    assert external_runner_module._absolute_path_flavor(path) == flavor
    for builder in _path_evidence_builders(path, source_evidence):
        assert builder() is not None


@pytest.mark.parametrize(
    "path",
    [
        "relative/evidence.bin",
        r"..\evidence.bin",
        r"C:drive-relative\evidence.bin",
        r"\rooted-without-drive\evidence.bin",
        "/tmp/../evidence.bin",
        "/tmp/./evidence.bin",
        "/tmp//evidence.bin",
        "/tmp/evidence.bin/",
        r"C:\tmp\..\evidence.bin",
        r"C:\tmp\.\evidence.bin",
        r"C:\tmp\\evidence.bin",
        r"C:\tmp/evidence.bin",
        "//server/share/evidence.bin",
        "///tmp/evidence.bin",
        r"\\?\C:\evidence.bin",
        r"\\.\C:\evidence.bin",
        r"C:\tmp\CON.txt",
        r"C:\tmp\CONIN$.txt",
        r"C:\tmp\CONOUT$.txt",
        "C:\\tmp\\COM\u00b9.txt",
        "C:\\tmp\\COM\u00b2.txt",
        "C:\\tmp\\COM\u00b3.txt",
        "C:\\tmp\\LPT\u00b9.txt",
        "C:\\tmp\\LPT\u00b2.txt",
        "C:\\tmp\\LPT\u00b3.txt",
        "C:\\tmp\\trailing.",
        "C:\\tmp\\trailing ",
        "nul\x00evidence.bin",
        "control\x1fevidence.bin",
    ],
)
def test_retained_evidence_rejects_noncanonical_or_ambiguous_paths(
    tmp_path: Path,
    path: str,
) -> None:
    source_evidence = _adapter_source_evidence(tmp_path)

    assert external_runner_module._absolute_path_flavor(path) is None
    for builder in _path_evidence_builders(path, source_evidence):
        with pytest.raises(ValueError, match="absolute"):
            builder()


@pytest.mark.parametrize(
    "foreign_path",
    [
        None,
        r"\\server\share\ctxc\foreign-evidence.bin",
    ],
    ids=("foreign-flavor", "unc"),
)
def test_foreign_retained_evidence_is_never_reopened_on_this_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    foreign_path: str | None,
) -> None:
    if foreign_path is None:
        foreign_path = (
            "/var/lib/ctxc/foreign-evidence.bin"
            if os.name == "nt"
            else r"C:\ctxc\foreign-evidence.bin"
        )
    source_evidence = _adapter_source_evidence(tmp_path)
    (
        dependency,
        entrypoint,
        source,
        runtime,
        network,
        service,
        _accounting,
    ) = tuple(
        builder()
        for builder in _path_evidence_builders(
            foreign_path,
            source_evidence,
        )
    )

    def unexpected_host_access(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("foreign retained path reached a host access boundary")

    monkeypatch.setattr(
        external_runner_module,
        "_bounded_file_evidence",
        unexpected_host_access,
    )
    monkeypatch.setattr(
        external_runner_module,
        "capture_adapter_source_evidence",
        unexpected_host_access,
    )
    monkeypatch.setattr(
        external_runner_module,
        "_inference_process_snapshot",
        unexpected_host_access,
    )

    assert not external_runner_module._dependency_lock_evidence_matches(
        dependency
    )
    assert not external_runner_module._adapter_entrypoint_evidence_matches(
        entrypoint
    )
    assert not external_runner_module._adapter_source_evidence_matches(source)
    assert not external_runner_module._adapter_source_covers_entrypoint(
        source,
        entrypoint,
    )
    assert not external_runner_module._adapter_runtime_evidence_matches(runtime)
    assert not external_runner_module._command_uses_adapter_runtime(
        (sys.executable,),
        runtime,
    )
    assert not external_runner_module._network_isolation_evidence_matches(
        network
    )
    assert not external_runner_module._command_references_adapter_entrypoint(
        (str(tmp_path / "adapter.py"),),
        working_directory=tmp_path,
        evidence=entrypoint,
    )
    monitor = external_runner_module._InferenceServiceMonitor(service)
    assert not monitor._executable_matches()
    assert monitor.sample(check_executable=True) == "inference_service_unavailable"

def test_inference_service_memory_ceiling_fails_closed_during_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "service.bin"
    executable.write_bytes(b"fixture inference service executable\n")
    contract = InferenceServiceContract(
        process_id=42,
        process_start_token="fixture-start-token",
        executable_path=str(executable.resolve()),
        executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        executable_bytes=executable.stat().st_size,
        memory_metric="working-set-bytes",
        max_memory_mb=1,
    )
    observed_memory = iter(
        (512 * 1024, 512 * 1024, 2 * 1024 * 1024)
    )

    def sample(_process_id: int):
        return external_runner_module._InferenceProcessSnapshot(
            process_id=42,
            process_start_token="fixture-start-token",
            executable_path=str(executable.resolve()),
            memory_metric="working-set-bytes",
            memory_bytes=next(observed_memory, 2 * 1024 * 1024),
        )

    monkeypatch.setattr(
        external_runner_module,
        "_inference_process_snapshot",
        sample,
    )
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    manifest = run_external_command(
        [sys.executable, "-c", "import time;time.sleep(0.05)"],
        system="service-limit-fixture",
        corpus_path=corpus_path,
        candidate_path=tmp_path / "candidate.json",
        limits=RunnerLimits(timeout_seconds=5),
        inference_service=contract,
    )

    assert manifest.termination_reason == "inference_service_memory_limit"
    assert manifest.inference_service.sample_count >= 2
    assert manifest.inference_service.peak_memory_bytes == 2 * 1024 * 1024
    assert not manifest.inference_service.claim_evidence_complete
    assert not manifest.ready_for_scoring


def test_inference_service_identity_and_executable_changes_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "service.bin"
    executable.write_bytes(b"fixture inference service executable\n")
    executable_path = str(executable.resolve())
    contract = InferenceServiceContract(
        process_id=42,
        process_start_token="fixture-start-token",
        executable_path=executable_path,
        executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        executable_bytes=executable.stat().st_size,
        memory_metric="working-set-bytes",
        max_memory_mb=1,
    )
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    start_tokens = iter(
        (
            "fixture-start-token",
            "fixture-start-token",
            "restarted-token",
        )
    )

    def changed_identity(_process_id: int):
        return external_runner_module._InferenceProcessSnapshot(
            process_id=42,
            process_start_token=next(start_tokens, "restarted-token"),
            executable_path=executable_path,
            memory_metric="working-set-bytes",
            memory_bytes=512 * 1024,
        )

    monkeypatch.setattr(
        external_runner_module,
        "_inference_process_snapshot",
        changed_identity,
    )
    identity_manifest = run_external_command(
        [sys.executable, "-c", "import time;time.sleep(0.05)"],
        system="service-identity-fixture",
        corpus_path=corpus_path,
        candidate_path=tmp_path / "identity-candidate.json",
        limits=RunnerLimits(timeout_seconds=5),
        inference_service=contract,
    )

    assert (
        identity_manifest.termination_reason
        == "inference_service_identity_changed"
    )

    def stable_identity(_process_id: int):
        return external_runner_module._InferenceProcessSnapshot(
            process_id=42,
            process_start_token="fixture-start-token",
            executable_path=executable_path,
            memory_metric="working-set-bytes",
            memory_bytes=512 * 1024,
        )

    monkeypatch.setattr(
        external_runner_module,
        "_inference_process_snapshot",
        stable_identity,
    )
    mutation_program = (
        "from pathlib import Path;import sys;"
        "Path(sys.argv[1]).write_bytes(b'modified service executable')"
    )
    executable_manifest = run_external_command(
        [sys.executable, "-c", mutation_program, executable_path],
        system="service-executable-fixture",
        corpus_path=corpus_path,
        candidate_path=tmp_path / "executable-candidate.json",
        limits=RunnerLimits(timeout_seconds=5),
        inference_service=contract,
    )

    assert (
        executable_manifest.termination_reason
        == "inference_service_executable_modified"
    )
    assert not executable_manifest.ready_for_scoring


def test_network_isolation_evidence_is_required_and_revalidated(tmp_path) -> None:
    with pytest.raises(
        ExternalRunnerError,
        match="unverified network isolation cannot accept",
    ):
        capture_network_isolation_evidence(
            "unverified",
            tmp_path / "unexpected.txt",
        )
    with pytest.raises(
        ExternalRunnerError,
        match="requires a retained evidence file",
    ):
        capture_network_isolation_evidence("host-firewall")

    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    diagnostic_candidate = tmp_path / "diagnostic-candidate.json"
    diagnostic = run_external_cases(
        valid_adapter_command(tmp_path),
        system="network-fixture",
        corpus_path=corpus_path,
        candidate_path=diagnostic_candidate,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=claim_identity(),
    )
    assert diagnostic.ready_for_scoring
    assert not diagnostic.claim_metadata_complete

    evidence = retained_network_isolation(tmp_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = run_external_cases(
        valid_adapter_command(tmp_path),
        system="network-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=claim_identity(),
        dependency_lock=retained_dependency_lock(tmp_path),
        adapter_entrypoint=retained_adapter_entrypoint(tmp_path),
        adapter_source=retained_adapter_source(tmp_path),
        network_isolation=evidence,
        inference_service=retained_inference_service(),
    )
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    assert manifest.claim_metadata_complete

    Path(evidence.evidence_path or "").write_text(
        "tampered network policy\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalRunnerError,
        match="network-isolation evidence file does not match",
    ):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )

    execution_evidence = retained_network_isolation(tmp_path)
    mutating_candidate = tmp_path / "mutating-candidate.json"
    mutation_program = (
        "from pathlib import Path;import sys;"
        "Path(sys.argv[1]).write_text('changed during execution',encoding='utf-8')"
    )
    mutated = run_external_command(
        [
            sys.executable,
            "-c",
            mutation_program,
            execution_evidence.evidence_path,
        ],
        system="network-fixture",
        corpus_path=corpus_path,
        candidate_path=mutating_candidate,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=claim_identity(),
        dependency_lock=retained_dependency_lock(tmp_path),
        network_isolation=execution_evidence,
        inference_service=retained_inference_service(),
    )
    assert mutated.termination_reason == "network_isolation_evidence_modified"
    assert not mutated.ready_for_scoring


def test_dependency_lock_evidence_binds_environment_and_revalidates(
    tmp_path,
) -> None:
    empty_lock = tmp_path / "empty.lock"
    empty_lock.write_bytes(b"")
    with pytest.raises(
        ExternalRunnerError,
        match="dependency-lock evidence cannot be empty",
    ):
        capture_dependency_lock_evidence(empty_lock)

    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    dependency_lock = retained_dependency_lock(tmp_path)
    network_isolation = retained_network_isolation(tmp_path)
    assert claim_identity().environment_id == (
        f"sha256:{dependency_lock.evidence_sha256}"
    )

    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = run_external_cases(
        valid_adapter_command(tmp_path),
        system="lock-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=claim_identity(),
        dependency_lock=dependency_lock,
        adapter_entrypoint=retained_adapter_entrypoint(tmp_path),
        adapter_source=retained_adapter_source(tmp_path),
        network_isolation=network_isolation,
        inference_service=retained_inference_service(),
    )
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    assert manifest.claim_metadata_complete

    Path(dependency_lock.evidence_path or "").write_text(
        "tampered dependency lock\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalRunnerError,
        match="dependency-lock evidence file does not match",
    ):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )

    mismatch_lock = retained_dependency_lock(tmp_path)
    mismatch_candidate = tmp_path / "mismatch-candidate.json"
    mismatch = run_external_cases(
        valid_adapter_command(tmp_path),
        system="lock-fixture",
        corpus_path=corpus_path,
        candidate_path=mismatch_candidate,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=replace(
            claim_identity(),
            environment_id="sha256:" + "f" * 64,
        ),
        dependency_lock=mismatch_lock,
        adapter_entrypoint=retained_adapter_entrypoint(tmp_path),
        adapter_source=retained_adapter_source(tmp_path),
        network_isolation=retained_network_isolation(tmp_path),
        inference_service=retained_inference_service(),
    )
    assert mismatch.ready_for_scoring
    assert not mismatch.claim_metadata_complete

    execution_lock = retained_dependency_lock(tmp_path)
    mutating_candidate = tmp_path / "mutating-lock-candidate.json"
    mutation_program = (
        "from pathlib import Path;import sys;"
        "Path(sys.argv[1]).write_text('changed during execution',encoding='utf-8')"
    )
    mutated = run_external_command(
        [
            sys.executable,
            "-c",
            mutation_program,
            execution_lock.evidence_path,
        ],
        system="lock-fixture",
        corpus_path=corpus_path,
        candidate_path=mutating_candidate,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=claim_identity(),
        dependency_lock=execution_lock,
        network_isolation=retained_network_isolation(tmp_path),
        inference_service=retained_inference_service(),
    )
    assert mutated.termination_reason == "dependency_lock_evidence_modified"
    assert not mutated.ready_for_scoring


def test_adapter_entrypoint_is_referenced_revalidated_and_immutable(
    tmp_path: Path,
) -> None:
    empty_entrypoint = tmp_path / "empty-adapter.py"
    empty_entrypoint.write_bytes(b"")
    with pytest.raises(
        ExternalRunnerError,
        match="adapter entrypoint evidence cannot be empty",
    ):
        capture_adapter_entrypoint_evidence(empty_entrypoint)

    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    entrypoint = retained_adapter_entrypoint(tmp_path)
    source = retained_adapter_source(tmp_path)
    with pytest.raises(
        ExternalRunnerError,
        match="does not reference its retained entrypoint",
    ):
        run_external_command(
            [sys.executable, "-c", "raise SystemExit(0)"],
            system="entrypoint-fixture",
            corpus_path=corpus_path,
            candidate_path=tmp_path / "unreferenced-candidate.json",
            limits=RunnerLimits(timeout_seconds=5),
            adapter_entrypoint=entrypoint,
            adapter_source=source,
        )

    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    retained_manifest = run_external_command(
        valid_adapter_command(tmp_path),
        system="entrypoint-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
        adapter_entrypoint=entrypoint,
        adapter_source=source,
    )
    manifest_path.write_text(
        retained_manifest.to_json(),
        encoding="utf-8",
    )
    Path(entrypoint.entrypoint_path or "").write_text(
        "changed after execution",
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalRunnerError,
        match="adapter entrypoint evidence file does not match",
    ):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )

    mutating_source = tmp_path / "mutating-source"
    mutating_source.mkdir()
    mutating_entrypoint = mutating_source / "mutating-adapter.py"
    mutating_entrypoint.write_text(
        "from pathlib import Path;"
        "Path(__file__).write_text('changed',encoding='utf-8')",
        encoding="utf-8",
    )
    mutating_evidence = capture_adapter_entrypoint_evidence(
        mutating_entrypoint
    )
    manifest = run_external_command(
        [sys.executable, str(mutating_entrypoint)],
        system="entrypoint-fixture",
        corpus_path=corpus_path,
        candidate_path=tmp_path / "mutated-candidate.json",
        limits=RunnerLimits(timeout_seconds=5),
        adapter_entrypoint=mutating_evidence,
        adapter_source=capture_adapter_source_evidence(mutating_source),
    )

    assert (
        manifest.termination_reason
        == "adapter_entrypoint_evidence_modified"
    )
    assert not manifest.ready_for_scoring


def test_adapter_source_tree_is_bounded_complete_and_immutable(
    tmp_path: Path,
) -> None:
    empty_source = tmp_path / "empty-source"
    empty_source.mkdir()
    with pytest.raises(
        ExternalRunnerError,
        match="adapter source tree cannot be empty",
    ):
        capture_adapter_source_evidence(empty_source)

    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    command = valid_adapter_command(tmp_path)
    source_root = Path(command[1]).parent
    support_directory = source_root / "support"
    support_directory.mkdir()
    helper_path = support_directory / "helper.py"
    helper_path.write_text("VALUE = 1\n", encoding="utf-8")
    entrypoint = capture_adapter_entrypoint_evidence(command[1])
    source = capture_adapter_source_evidence(source_root)
    assert [item.relative_path for item in source.files] == [
        "support/helper.py",
        "valid-adapter.py",
    ]

    unrelated_source = tmp_path / "unrelated-source"
    unrelated_source.mkdir()
    (unrelated_source / "other.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalRunnerError,
        match="does not cover its retained entrypoint",
    ):
        run_external_command(
            command,
            system="source-fixture",
            corpus_path=corpus_path,
            candidate_path=tmp_path / "uncovered-candidate.json",
            limits=RunnerLimits(timeout_seconds=5),
            adapter_entrypoint=entrypoint,
            adapter_source=capture_adapter_source_evidence(
                unrelated_source
            ),
        )

    candidate_path = tmp_path / "source-candidate.json"
    manifest_path = tmp_path / "source-manifest.json"
    retained_manifest = run_external_command(
        command,
        system="source-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
        adapter_entrypoint=entrypoint,
        adapter_source=source,
    )
    manifest_path.write_text(
        retained_manifest.to_json(),
        encoding="utf-8",
    )
    helper_path.write_text("VALUE = 3\n", encoding="utf-8")
    with pytest.raises(
        ExternalRunnerError,
        match="adapter source evidence tree does not match",
    ):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )

    mutating_source = tmp_path / "runtime-mutating-source"
    mutating_source.mkdir()
    mutating_entrypoint_path = mutating_source / "adapter.py"
    mutating_entrypoint_path.write_text(
        "from pathlib import Path;"
        "Path(__file__).with_name('generated.py').write_text("
        "'VALUE = 4\\n',encoding='utf-8')",
        encoding="utf-8",
    )
    mutated = run_external_cases(
        [sys.executable, str(mutating_entrypoint_path)],
        system="source-fixture",
        corpus_path=corpus_path,
        candidate_path=tmp_path / "source-mutated-candidate.json",
        limits=RunnerLimits(timeout_seconds=5),
        adapter_entrypoint=capture_adapter_entrypoint_evidence(
            mutating_entrypoint_path
        ),
        adapter_source=capture_adapter_source_evidence(mutating_source),
    )
    assert mutated.termination_reason is not None
    assert mutated.termination_reason.endswith(
        "adapter_source_evidence_modified"
    )
    assert (
        mutated.case_runs[0].termination_reason
        == "adapter_source_evidence_modified"
    )
    assert not mutated.ready_for_scoring


def test_claim_command_contract_rejects_deep_embedded_template() -> None:
    deep_template = "template:" + "[" * 2_000 + "0" + "]" * 2_000
    assert not external_runner_module._claim_command_contract_complete(
        [deep_template]
    )


def test_command_contract_is_portable_and_binds_each_case_and_runtime(
    tmp_path: Path,
) -> None:
    manifests = []
    documents = []
    for label in ("first-host-path", "second-host-path"):
        directory = tmp_path / label
        directory.mkdir()
        corpus_path = directory / "corpus.json"
        _config, document = write_corpus(corpus_path, histories=2)
        command = valid_adapter_command(directory)
        manifests.append(
            run_external_cases(
                command,
                system="portable-fixture",
                corpus_path=corpus_path,
                candidate_path=directory / "candidate.json",
                limits=RunnerLimits(
                    timeout_seconds=5,
                    max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
                ),
                identity=claim_identity(),
                dependency_lock=retained_dependency_lock(directory),
                adapter_entrypoint=capture_adapter_entrypoint_evidence(
                    command[1]
                ),
                adapter_source=capture_adapter_source_evidence(
                    Path(command[1]).parent
                ),
                network_isolation=retained_network_isolation(
                    directory
                ),
                inference_service=retained_inference_service(),
            )
        )
        documents.append(document)

    first, second = manifests
    assert first.command != second.command
    assert first.command_contract == second.command_contract
    assert first.command_sha256 == second.command_sha256
    assert (
        first.adapter_runtime.executable_sha256
        == second.adapter_runtime.executable_sha256
    )
    assert first.adapter_source.tree_sha256 == second.adapter_source.tree_sha256
    assert first.claim_metadata_complete
    assert second.claim_metadata_complete
    assert first.working_directory == first.adapter_source.source_root
    assert second.working_directory == second.adapter_source.source_root

    embedded_directory = tmp_path / "embedded-template"
    embedded_directory.mkdir()
    embedded_corpus = embedded_directory / "corpus.json"
    _config, embedded_document = write_corpus(
        embedded_corpus,
        histories=2,
    )
    embedded_source = embedded_directory / "adapter-source"
    embedded_source.mkdir()
    embedded_entrypoint = embedded_source / "adapter.py"
    embedded_entrypoint.write_text(
        "import json,sys;"
        "args=dict(value[2:].split('=',1) for value in sys.argv[1:]);"
        "corpus=json.load(open(args['corpus'],encoding='utf-8'));"
        "payload={'schema':'lrcbench-candidate-output-0.1',"
        "'dataset_sha256':corpus['dataset_sha256'],"
        "'system':args['system'],"
        "'cases':[{'case_id':case['case_id'],"
        "'rendered_text':args['case'],'claims':[]}"
        " for case in corpus['cases']]};"
        "json.dump(payload,open(args['candidate'],'w',encoding='utf-8'))",
        encoding="utf-8",
    )
    embedded_manifest = run_external_cases(
        [
            sys.executable,
            str(embedded_entrypoint),
            "--corpus={corpus}",
            "--candidate={candidate}",
            "--system={system}",
            "--case={case_id}",
        ],
        system="embedded-fixture",
        corpus_path=embedded_corpus,
        candidate_path=embedded_directory / "candidate.json",
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=claim_identity(),
        dependency_lock=retained_dependency_lock(embedded_directory),
        adapter_entrypoint=capture_adapter_entrypoint_evidence(
            embedded_entrypoint
        ),
        adapter_source=capture_adapter_source_evidence(
            embedded_source
        ),
        network_isolation=retained_network_isolation(
            embedded_directory
        ),
        inference_service=retained_inference_service(),
    )
    assert embedded_manifest.ready_for_scoring
    assert embedded_manifest.claim_metadata_complete
    assert any(
        value.startswith("template:")
        for value in embedded_manifest.command_contract
    )
    embedded_manifest_path = embedded_directory / "manifest.json"
    embedded_manifest_path.write_text(
        embedded_manifest.to_json(),
        encoding="utf-8",
    )
    load_external_run_manifest(
        embedded_manifest_path,
        expected_dataset_sha256=embedded_document["dataset_sha256"],
    )

    original_payload = first.to_dict()
    tampered_case = json.loads(json.dumps(original_payload))
    tampered_case["case_runs"][0]["command"].append("--unexpected")
    tampered_case.pop("manifest_sha256")
    tampered_case["manifest_sha256"] = _canonical_sha256(tampered_case)
    tampered_case_path = tmp_path / "tampered-case-manifest.json"
    tampered_case_path.write_text(
        json.dumps(tampered_case),
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalRunnerError,
        match="command does not match the command contract",
    ):
        load_external_run_manifest(
            tampered_case_path,
            expected_dataset_sha256=documents[0]["dataset_sha256"],
        )

    tampered_runtime = json.loads(json.dumps(original_payload))
    tampered_runtime["adapter_runtime"] = {
        "executable_path": first.adapter_entrypoint.entrypoint_path,
        "executable_sha256": first.adapter_entrypoint.entrypoint_sha256,
        "executable_bytes": first.adapter_entrypoint.entrypoint_bytes,
    }
    tampered_runtime.pop("manifest_sha256")
    tampered_runtime["manifest_sha256"] = _canonical_sha256(
        tampered_runtime
    )
    tampered_runtime_path = tmp_path / "tampered-runtime-manifest.json"
    tampered_runtime_path.write_text(
        json.dumps(tampered_runtime),
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalRunnerError,
        match="command does not use its adapter runtime",
    ):
        load_external_run_manifest(
            tampered_runtime_path,
            expected_dataset_sha256=documents[0]["dataset_sha256"],
        )


def test_corpus_export_digest_detects_gold_free_source_tampering(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)

    decoded_config, cases, digest = decode_corpus_document(document)

    assert decoded_config.histories == 1
    assert len(cases) == 1
    assert digest == document["dataset_sha256"]

    tampered = json.loads(json.dumps(document))
    tampered["cases"][0]["source_events"][0]["content"] += "tampered"
    with pytest.raises(ExternalBaselineError, match="corpus_sha256 mismatch"):
        decode_corpus_document(tampered)


def test_corpus_producer_changes_evidence_without_changing_dataset_identity() -> None:
    config = BenchmarkConfig(
        histories=1,
        messages_per_history=24,
        noise_lines_per_message=1,
        token_budget=900,
        bootstrap_samples=100,
    )
    cases = generate_histories(config)
    digest = dataset_digest(cases, config)
    first = corpus_document(
        cases,
        config,
        digest,
        producer=corpus_producer(),
    )
    second = corpus_document(
        cases,
        config,
        digest,
        producer=corpus_producer(created_at="2026-01-02T00:00:00+00:00"),
    )

    assert first["producer"]["schema"] == CORPUS_PRODUCER_SCHEMA
    assert first["dataset_sha256"] == second["dataset_sha256"] == digest
    assert first["corpus_sha256"] != second["corpus_sha256"]

    tampered = json.loads(json.dumps(first))
    tampered["producer"]["tokenizer_id"] = "wrong-tokenizer"
    tampered.pop("corpus_sha256")
    tampered["corpus_sha256"] = _canonical_sha256(tampered)
    with pytest.raises(ExternalBaselineError, match="tokenizer_id must be"):
        decode_corpus_document(tampered)


def test_interchange_file_loaders_reject_duplicate_keys_and_size_overflow(
    tmp_path,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    config, document = write_corpus(corpus_path)
    original_corpus = corpus_path.read_text(encoding="utf-8")
    corpus_path.write_text(
        original_corpus.replace(
            "{",
            '{"schema":"lrcbench-corpus-0.3",',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ExternalRunnerError, match="duplicate JSON object key"):
        external_runner_module._load_corpus(corpus_path)
    corpus_path.write_text(original_corpus, encoding="utf-8")

    candidate_payload = {
        "schema": CANDIDATE_SCHEMA,
        "dataset_sha256": document["dataset_sha256"],
        "system": "strict-fixture",
        "cases": [
            {
                "case_id": case["case_id"],
                "rendered_text": "",
                "claims": [],
            }
            for case in document["cases"]
        ],
    }
    candidate_path = tmp_path / "candidate.json"
    candidate_text = json.dumps(candidate_payload)
    systems = (
        "strict-fixture",
        "strict-fixture-b",
        "strict-fixture-c",
        "strict-fixture-d",
    )
    protocol_path = write_frozen_external_protocol(
        tmp_path,
        systems,
        synthetic_dataset_sha256=document["dataset_sha256"],
    )
    candidate_path.write_text(
        candidate_text.replace(
            "{",
            f'{{"schema":"{CANDIDATE_SCHEMA}",',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ExternalBaselineError, match="duplicate JSON object key"):
        run_benchmark(
            config,
            external_baseline_paths=(candidate_path,),
            expected_external_systems=systems,
            external_protocol_path=protocol_path,
        )

    candidate_path.write_text(candidate_text, encoding="utf-8")
    with pytest.raises(ExternalBaselineError, match="external candidate exceeds"):
        load_external_candidates(
            (candidate_path,),
            cases=generate_histories(config),
            dataset_sha256=document["dataset_sha256"],
            token_budget=config.token_budget,
            max_candidate_bytes=len(candidate_text.encode("utf-8")) - 1,
        )
    with pytest.raises(
        ExternalBaselineError,
        match="candidate byte limit does not match",
    ):
        run_benchmark(
            config,
            external_baseline_paths=(candidate_path,),
            expected_external_systems=systems,
            external_protocol_path=protocol_path,
            max_external_candidate_bytes=len(candidate_text.encode("utf-8")) - 1,
        )
    with pytest.raises(ExternalBaselineError, match="max_bytes must be positive"):
        run_benchmark(
            config,
            max_external_candidate_bytes=0,
        )

    runner_candidate = tmp_path / "runner-candidate.json"
    manifest = run_external_command(
        valid_adapter_command(tmp_path),
        system="strict-fixture",
        corpus_path=corpus_path,
        candidate_path=runner_candidate,
        limits=RunnerLimits(timeout_seconds=5),
    )
    manifest_text = manifest.to_json()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        manifest_text.replace(
            "{",
            f'{{"schema":"{external_runner_module.RUNNER_MANIFEST_SCHEMA}",',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ExternalRunnerError, match="duplicate JSON object key"):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )


def test_corpus_loader_enforces_serialized_size_before_decoding(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    byte_count = corpus_path.stat().st_size
    monkeypatch.setattr(
        external_runner_module,
        "_CORPUS_JSON_LIMITS",
        StrictJsonLimits(
            max_bytes=byte_count - 1,
            max_line_chars=byte_count,
            max_depth=128,
        ),
    )

    with pytest.raises(ExternalRunnerError, match="benchmark corpus exceeds"):
        external_runner_module._load_corpus(corpus_path)


def test_candidate_mutation_during_validation_fails_closed(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    original_loader = external_runner_module.load_strict_json_file
    mutated = False

    def mutate_before_candidate_read(path, *, limits, label):
        nonlocal mutated
        if label == "external candidate" and not mutated:
            mutated = True
            candidate = Path(path)
            candidate.write_text(
                candidate.read_text(encoding="utf-8") + " ",
                encoding="utf-8",
            )
        return original_loader(path, limits=limits, label=label)

    monkeypatch.setattr(
        external_runner_module,
        "load_strict_json_file",
        mutate_before_candidate_read,
    )

    manifest = run_external_command(
        valid_adapter_command(tmp_path),
        system="strict-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    )

    assert mutated
    assert manifest.process_succeeded
    assert not manifest.candidate_valid
    assert not manifest.ready_for_scoring
    assert manifest.validation_error == "candidate changed while it was being validated"


def test_per_case_candidate_mutation_before_aggregation_is_rejected(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    original_loader = external_runner_module.load_strict_json_file

    def mutate_before_aggregate_read(path, *, limits, label):
        if label == "per-case external candidate":
            candidate = Path(path)
            candidate.write_text(
                candidate.read_text(encoding="utf-8") + " ",
                encoding="utf-8",
            )
        return original_loader(path, limits=limits, label=label)

    monkeypatch.setattr(
        external_runner_module,
        "load_strict_json_file",
        mutate_before_aggregate_read,
    )

    with pytest.raises(ExternalRunnerError, match="changed before aggregation"):
        run_external_cases(
            valid_adapter_command(tmp_path),
            system="strict-fixture",
            corpus_path=corpus_path,
            candidate_path=candidate_path,
            limits=RunnerLimits(
                timeout_seconds=5,
                max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
            ),
            identity=claim_identity(),
            dependency_lock=retained_dependency_lock(tmp_path),
            adapter_entrypoint=retained_adapter_entrypoint(tmp_path),
            adapter_source=retained_adapter_source(tmp_path),
            network_isolation=retained_network_isolation(tmp_path),
            inference_service=retained_inference_service(),
        )


def test_per_case_runner_avoids_adapter_shell_interpretation_and_validates_candidate(
    tmp_path,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    command = valid_adapter_command(tmp_path)

    manifest = run_external_cases(
        command,
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=claim_identity(),
        dependency_lock=retained_dependency_lock(tmp_path),
        adapter_entrypoint=retained_adapter_entrypoint(tmp_path),
        adapter_source=retained_adapter_source(tmp_path),
        network_isolation=retained_network_isolation(tmp_path),
        inference_service=retained_inference_service(),
    )

    assert manifest.process_succeeded
    assert manifest.candidate_valid
    assert manifest.ready_for_scoring
    assert manifest.claim_metadata_complete
    assert manifest.isolation_mode == "per_case"
    assert manifest.command == (
        str(Path(command[0]).resolve()),
        *command[1:],
    )
    assert manifest.command[0] == manifest.adapter_runtime.executable_path
    assert external_runner_module._DARWIN_LIMIT_LAUNCHER_PROTOCOL not in (
        json.dumps(manifest.command_contract)
    )
    assert manifest.case_count == 1
    assert len(manifest.case_runs) == 1
    assert manifest.exit_code == 0
    assert manifest.termination_reason is None
    assert manifest.corpus_sha256 == document["corpus_sha256"]
    assert manifest.candidate_sha256 == hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    candidate_payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate_payload_sha256 = candidate_payload.pop("candidate_payload_sha256")
    assert candidate_payload["schema"] == CANDIDATE_SCHEMA
    assert candidate_payload["producer"] == claim_identity().to_candidate_producer().to_dict()
    assert candidate_payload_sha256 == _canonical_sha256(candidate_payload)
    payload = manifest.to_dict()
    manifest_sha = payload.pop("manifest_sha256")
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    assert manifest_sha == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_external_command_environment_evidence_matches_adapter_observation(
    tmp_path: Path,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    observed_environment_path = tmp_path / "observed-environment.json"
    source_root = tmp_path / "environment-adapter-source"
    source_root.mkdir()
    entrypoint = source_root / "environment-adapter.py"
    program = (
        "import json,os,sys;"
        "corpus=json.load(open(sys.argv[1],encoding='utf-8'));"
        "json.dump(dict(os.environ),"
        "open(sys.argv[4],'w',encoding='utf-8'),sort_keys=True);"
        "payload={'schema':'lrcbench-candidate-output-0.1',"
        "'dataset_sha256':corpus['dataset_sha256'],'system':sys.argv[3],"
        "'cases':[{'case_id':case['case_id'],'rendered_text':'','claims':[]}"
        " for case in corpus['cases']]};"
        "json.dump(payload,open(sys.argv[2],'w',encoding='utf-8'))"
    )
    entrypoint.write_text(program, encoding="utf-8")
    command = [
        sys.executable,
        str(entrypoint),
        "{corpus}",
        "{candidate}",
        "{system}",
        str(observed_environment_path),
    ]
    environment = external_runner_module._default_process_environment()
    environment["CTXC_ENVIRONMENT_PROBE"] = "literal=one\nSnowman: \u2603"
    environment["LC_CTYPE"] = "UTF-8"
    expected_environment, _expected_evidence = (
        external_runner_module._prepare_process_environment(environment)
    )

    manifest = run_external_command(
        command,
        system="environment-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        adapter_entrypoint=capture_adapter_entrypoint_evidence(entrypoint),
        adapter_source=capture_adapter_source_evidence(source_root),
        environment=environment,
    )

    observed_environment = json.loads(
        observed_environment_path.read_text(encoding="utf-8")
    )
    assert manifest.process_succeeded
    assert manifest.candidate_valid
    assert observed_environment == expected_environment
    assert manifest.process_environment == capture_process_environment_evidence(
        observed_environment
    )
    assert manifest.process_environment == capture_process_environment_evidence(
        expected_environment
    )
    assert "SHLVL" not in observed_environment
    assert "_" not in observed_environment


def test_cli_defaults_to_claim_eligible_per_case_mode(
    tmp_path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LRCBENCH_ADAPTER_MODE", "frozen-value")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-inherited")
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    dependency_lock = retained_dependency_lock(tmp_path)
    adapter_entrypoint = retained_adapter_entrypoint(tmp_path)
    adapter_source = retained_adapter_source(tmp_path)
    network_isolation = retained_network_isolation(tmp_path)

    exit_code = runner_main(
        [
            "--system",
            "cli-fixture",
            "--corpus",
            str(corpus_path),
            "--candidate-out",
            str(candidate_path),
            "--manifest-out",
            str(manifest_path),
            "--pass-environment",
            "LRCBENCH_ADAPTER_MODE",
            "--timeout-seconds",
            "5",
            "--max-memory-mb",
            str(_FUNCTIONAL_ADAPTER_MEMORY_MB),
            "--dependency-lock-evidence",
            dependency_lock.evidence_path,
            "--adapter-entrypoint-evidence",
            adapter_entrypoint.entrypoint_path,
            "--adapter-source-root",
            adapter_source.source_root,
            "--network-isolation-mode",
            network_isolation.mode,
            "--network-isolation-evidence",
            network_isolation.evidence_path,
            "--inference-service-pid",
            str(os.getpid()),
            "--max-inference-service-memory-mb",
            "4096",
            "--adapter-revision",
            FIXTURE_ADAPTER_REVISION,
            "--environment-id",
            FIXTURE_ENVIRONMENT_ID,
            "--model-id",
            QWEN_Q4_VARIANT,
            "--model-context-length",
            "8192",
            "--tokenizer-id",
            "character-estimate-v1",
            "--inference-concurrency",
            "1",
            "--retry-count",
            "0",
            "--model-service-cost-usd",
            "0",
            "--",
            *valid_adapter_command(tmp_path),
        ]
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["isolation_mode"] == "per_case"
    assert payload["claim_metadata_complete"]
    assert "LRCBENCH_ADAPTER_MODE" in payload[
        "process_environment"
    ]["variable_names"]
    assert "OPENAI_API_KEY" not in payload[
        "process_environment"
    ]["variable_names"]
    serialized_manifest = manifest_path.read_text(encoding="utf-8")
    assert "frozen-value" not in serialized_manifest
    assert "must-not-be-inherited" not in serialized_manifest
    assert "ready for registered scoring" in capsys.readouterr().out


def test_per_case_runner_uses_one_validated_corpus_case_per_process(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, _document = write_corpus(corpus_path, histories=3)
    candidate_path = tmp_path / "candidate.json"
    program = (
        "import json,sys;"
        "corpus=json.load(open(sys.argv[1],encoding='utf-8'));"
        "assert len(corpus['cases'])==1;"
        "case=corpus['cases'][0];"
        "assert case['case_id']==sys.argv[4];"
        "payload={'schema':'lrcbench-candidate-output-0.1',"
        "'dataset_sha256':corpus['dataset_sha256'],'system':sys.argv[3],"
        "'cases':[{'case_id':case['case_id'],'rendered_text':sys.argv[4],"
        "'claims':[]}]};"
        "json.dump(payload,open(sys.argv[2],'w',encoding='utf-8'))"
    )
    source_root = tmp_path / "isolated-source"
    source_root.mkdir()
    entrypoint_path = source_root / "isolated-adapter.py"
    entrypoint_path.write_text(program, encoding="utf-8")
    command = [
        sys.executable,
        str(entrypoint_path),
        "{corpus}",
        "{candidate}",
        "{system}",
        "{case_id}",
    ]

    manifest = run_external_cases(
        command,
        system="isolated-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=claim_identity(),
        dependency_lock=retained_dependency_lock(tmp_path),
        adapter_entrypoint=capture_adapter_entrypoint_evidence(
            entrypoint_path
        ),
        adapter_source=capture_adapter_source_evidence(source_root),
        network_isolation=retained_network_isolation(tmp_path),
        inference_service=retained_inference_service(),
    )

    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert manifest.ready_for_scoring
    assert manifest.claim_metadata_complete
    assert manifest.case_count == 3
    assert len(manifest.case_runs) == 3
    assert [record.case_id for record in manifest.case_runs] == [
        value["case_id"] for value in payload["cases"]
    ]
    assert all(record.command[-1] == record.case_id for record in manifest.case_runs)
    assert len({record.corpus_sha256 for record in manifest.case_runs}) == 3
    assert [
        record.candidate_payload_sha256
        for record in manifest.case_runs
    ] == [
        _canonical_sha256(
            {
                "schema": CANDIDATE_SCHEMA,
                "dataset_sha256": payload["dataset_sha256"],
                "system": payload["system"],
                "producer": payload["producer"],
                "cases": [raw_case],
            }
        )
        for raw_case in payload["cases"]
    ]


def test_per_case_runner_retains_failure_and_continues_later_cases(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path, histories=2)
    candidate_path = tmp_path / "candidate.json"
    program = (
        "import json,sys;"
        "corpus=json.load(open(sys.argv[1],encoding='utf-8'));"
        "case=corpus['cases'][0];"
        "sys.exit(7) if case['case_id'].endswith('000') else None;"
        "payload={'schema':'lrcbench-candidate-output-0.1',"
        "'dataset_sha256':corpus['dataset_sha256'],'system':sys.argv[3],"
        "'cases':[{'case_id':case['case_id'],'rendered_text':'','claims':[]}]};"
        "json.dump(payload,open(sys.argv[2],'w',encoding='utf-8'))"
    )

    manifest = run_external_cases(
        [
            sys.executable,
            "-c",
            program,
            "{corpus}",
            "{candidate}",
            "{system}",
        ],
        system="failure-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
    )

    assert len(manifest.case_runs) == 2
    assert manifest.case_runs[0].exit_code == 7
    assert not manifest.case_runs[0].process_succeeded
    assert manifest.case_runs[0].candidate_payload_sha256 is None
    assert manifest.case_runs[1].candidate_valid
    assert manifest.case_runs[1].candidate_payload_sha256 is not None
    assert manifest.termination_reason == ("case_failure:history-000:nonzero_exit")
    assert not manifest.ready_for_scoring
    assert not candidate_path.exists()


def test_whole_corpus_mode_is_diagnostic_even_with_complete_identity(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = run_external_command(
        valid_adapter_command(tmp_path),
        system="whole-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=claim_identity(),
        dependency_lock=retained_dependency_lock(tmp_path),
        adapter_entrypoint=retained_adapter_entrypoint(tmp_path),
        adapter_source=retained_adapter_source(tmp_path),
        network_isolation=retained_network_isolation(tmp_path),
        inference_service=retained_inference_service(),
    )
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")

    reference = load_external_run_manifest(
        manifest_path,
        expected_dataset_sha256=document["dataset_sha256"],
    )

    assert manifest.ready_for_scoring
    assert manifest.isolation_mode == "whole_corpus"
    assert not manifest.claim_metadata_complete
    assert "per-case isolation" in reference.failure_reason


def test_ready_manifest_reloads_candidate_and_binds_benchmark_evidence(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = run_external_cases(
        valid_adapter_command(tmp_path),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=300,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=claim_identity(),
        dependency_lock=retained_dependency_lock(tmp_path),
        adapter_entrypoint=retained_adapter_entrypoint(tmp_path),
        adapter_source=retained_adapter_source(tmp_path),
        network_isolation=retained_network_isolation(tmp_path),
        inference_service=retained_inference_service(),
    )
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    systems = (
        "fixture-adapter",
        "fixture-adapter-b",
        "fixture-adapter-c",
        "fixture-adapter-d",
    )
    adapter_entrypoint_sha256s = {
        "fixture-adapter": manifest.adapter_entrypoint.entrypoint_sha256,
    }
    adapter_source_tree_sha256s = {
        "fixture-adapter": manifest.adapter_source.tree_sha256,
    }
    adapter_runtime_executable_sha256s = {
        "fixture-adapter": manifest.adapter_runtime.executable_sha256,
    }
    adapter_environment_sha256s = {
        "fixture-adapter": (
            manifest.process_environment.environment_sha256
        ),
    }
    adapter_command_sha256s = {
        "fixture-adapter": manifest.command_sha256,
    }
    inference_service_executable_sha256 = (
        manifest.inference_service.executable_sha256
    )
    protocol_path = write_frozen_external_protocol(
        tmp_path,
        systems,
        adapter_revisions={
            "fixture-adapter": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "fixture-adapter": FIXTURE_ENVIRONMENT_ID,
        },
        adapter_entrypoint_sha256s=adapter_entrypoint_sha256s,
        adapter_source_tree_sha256s=adapter_source_tree_sha256s,
        adapter_runtime_executable_sha256s=(
            adapter_runtime_executable_sha256s
        ),
        adapter_environment_sha256s=adapter_environment_sha256s,
        adapter_command_sha256s=adapter_command_sha256s,
        synthetic_dataset_sha256=document["dataset_sha256"],
        max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        inference_service_executable_sha256=(
            inference_service_executable_sha256
        ),
    )

    reference = load_external_run_manifest(
        manifest_path,
        expected_dataset_sha256=document["dataset_sha256"],
    )
    reordered_environment_payload = json.loads(
        json.dumps(manifest.to_dict())
    )
    reordered_environment_payload["process_environment"][
        "variable_names"
    ].reverse()
    reordered_environment_payload.pop("manifest_sha256")
    reordered_environment_payload["manifest_sha256"] = (
        _canonical_sha256(reordered_environment_payload)
    )
    reordered_environment_path = (
        tmp_path / "reordered-environment-manifest.json"
    )
    reordered_environment_path.write_text(
        json.dumps(reordered_environment_payload),
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalRunnerError,
        match="process environment evidence is invalid.*not canonical",
    ):
        load_external_run_manifest(
            reordered_environment_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )
    report = run_benchmark(
        config,
        external_manifest_paths=(manifest_path,),
        expected_external_systems=systems,
        external_protocol_path=protocol_path,
    )

    assert reference.system == "fixture-adapter"
    assert reference.candidate_path == candidate_path.resolve()
    assert reference.candidate_bytes == candidate_path.stat().st_size
    assert reference.candidate_sha256 == hashlib.sha256(
        candidate_path.read_bytes()
    ).hexdigest()
    assert reference.failure_reason is None
    assert reference.adapter_revision == FIXTURE_ADAPTER_REVISION
    assert reference.environment_id == FIXTURE_ENVIRONMENT_ID
    assert reference.model_id == QWEN_Q4_VARIANT
    assert reference.model_service_cost_usd == 0.0
    assert report.run_metadata.model_id == "unrecorded"
    assert report.run_metadata.model_service_cost_usd is None
    assert {
        revision.name: revision.revision
        for revision in report.run_metadata.baseline_revisions
    }["fixture-adapter"] == FIXTURE_ADAPTER_REVISION
    assert report.certificate.external_manifests[0].system == "fixture-adapter"
    assert (
        report.certificate.external_manifests[0].manifest_sha256
        == manifest.to_dict()["manifest_sha256"]
    )
    comparison = next(
        value for value in report.certificate.comparisons if value.system == "fixture-adapter"
    )
    assert "no validated external run manifest" not in comparison.reasons
    mismatched_protocol_directory = tmp_path / "mismatched-environment"
    mismatched_protocol_directory.mkdir()
    mismatched_protocol_path = write_frozen_external_protocol(
        mismatched_protocol_directory,
        systems,
        adapter_revisions={
            "fixture-adapter": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "fixture-adapter": "sha256:" + "c" * 64,
        },
        adapter_entrypoint_sha256s=adapter_entrypoint_sha256s,
        adapter_source_tree_sha256s=adapter_source_tree_sha256s,
        adapter_runtime_executable_sha256s=(
            adapter_runtime_executable_sha256s
        ),
        adapter_environment_sha256s=adapter_environment_sha256s,
        adapter_command_sha256s=adapter_command_sha256s,
        synthetic_dataset_sha256=document["dataset_sha256"],
        max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        inference_service_executable_sha256=(
            inference_service_executable_sha256
        ),
    )
    with pytest.raises(
        ExternalBaselineError,
        match="environment identity does not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=mismatched_protocol_path,
        )

    mismatched_entrypoint_directory = tmp_path / "mismatched-entrypoint"
    mismatched_entrypoint_directory.mkdir()
    mismatched_entrypoint_protocol = write_frozen_external_protocol(
        mismatched_entrypoint_directory,
        systems,
        adapter_revisions={
            "fixture-adapter": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "fixture-adapter": FIXTURE_ENVIRONMENT_ID,
        },
        adapter_entrypoint_sha256s={
            "fixture-adapter": "f" * 64,
        },
        adapter_source_tree_sha256s=adapter_source_tree_sha256s,
        adapter_runtime_executable_sha256s=(
            adapter_runtime_executable_sha256s
        ),
        adapter_environment_sha256s=adapter_environment_sha256s,
        adapter_command_sha256s=adapter_command_sha256s,
        synthetic_dataset_sha256=document["dataset_sha256"],
        max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        inference_service_executable_sha256=(
            inference_service_executable_sha256
        ),
    )
    with pytest.raises(
        ExternalBaselineError,
        match="adapter entrypoint does not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=mismatched_entrypoint_protocol,
        )

    mismatched_source_directory = tmp_path / "mismatched-source"
    mismatched_source_directory.mkdir()
    mismatched_source_protocol = write_frozen_external_protocol(
        mismatched_source_directory,
        systems,
        adapter_revisions={
            "fixture-adapter": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "fixture-adapter": FIXTURE_ENVIRONMENT_ID,
        },
        adapter_entrypoint_sha256s=adapter_entrypoint_sha256s,
        adapter_source_tree_sha256s={
            "fixture-adapter": "f" * 64,
        },
        adapter_runtime_executable_sha256s=(
            adapter_runtime_executable_sha256s
        ),
        adapter_environment_sha256s=adapter_environment_sha256s,
        adapter_command_sha256s=adapter_command_sha256s,
        synthetic_dataset_sha256=document["dataset_sha256"],
        max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        inference_service_executable_sha256=(
            inference_service_executable_sha256
        ),
    )
    with pytest.raises(
        ExternalBaselineError,
        match="adapter source tree does not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=mismatched_source_protocol,
        )

    mismatched_runtime_directory = tmp_path / "mismatched-runtime"
    mismatched_runtime_directory.mkdir()
    mismatched_runtime_protocol = write_frozen_external_protocol(
        mismatched_runtime_directory,
        systems,
        adapter_revisions={
            "fixture-adapter": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "fixture-adapter": FIXTURE_ENVIRONMENT_ID,
        },
        adapter_entrypoint_sha256s=adapter_entrypoint_sha256s,
        adapter_source_tree_sha256s=adapter_source_tree_sha256s,
        adapter_runtime_executable_sha256s={
            "fixture-adapter": "f" * 64,
        },
        adapter_environment_sha256s=adapter_environment_sha256s,
        adapter_command_sha256s=adapter_command_sha256s,
        synthetic_dataset_sha256=document["dataset_sha256"],
        max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        inference_service_executable_sha256=(
            inference_service_executable_sha256
        ),
    )
    with pytest.raises(
        ExternalBaselineError,
        match="adapter runtime does not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=mismatched_runtime_protocol,
        )

    mismatched_process_environment_directory = (
        tmp_path / "mismatched-process-environment"
    )
    mismatched_process_environment_directory.mkdir()
    mismatched_process_environment_protocol = (
        write_frozen_external_protocol(
            mismatched_process_environment_directory,
            systems,
            adapter_revisions={
                "fixture-adapter": FIXTURE_ADAPTER_REVISION,
            },
            environment_ids={
                "fixture-adapter": FIXTURE_ENVIRONMENT_ID,
            },
            adapter_entrypoint_sha256s=adapter_entrypoint_sha256s,
            adapter_source_tree_sha256s=adapter_source_tree_sha256s,
            adapter_runtime_executable_sha256s=(
                adapter_runtime_executable_sha256s
            ),
            adapter_environment_sha256s={
                "fixture-adapter": "f" * 64,
            },
            adapter_command_sha256s=adapter_command_sha256s,
            synthetic_dataset_sha256=document["dataset_sha256"],
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
            inference_service_executable_sha256=(
                inference_service_executable_sha256
            ),
        )
    )
    with pytest.raises(
        ExternalBaselineError,
        match="adapter environment does not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=(
                mismatched_process_environment_protocol
            ),
        )

    mismatched_command_directory = tmp_path / "mismatched-command"
    mismatched_command_directory.mkdir()
    mismatched_command_protocol = write_frozen_external_protocol(
        mismatched_command_directory,
        systems,
        adapter_revisions={
            "fixture-adapter": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "fixture-adapter": FIXTURE_ENVIRONMENT_ID,
        },
        adapter_entrypoint_sha256s=adapter_entrypoint_sha256s,
        adapter_source_tree_sha256s=adapter_source_tree_sha256s,
        adapter_runtime_executable_sha256s=(
            adapter_runtime_executable_sha256s
        ),
        adapter_environment_sha256s=adapter_environment_sha256s,
        adapter_command_sha256s={
            "fixture-adapter": "f" * 64,
        },
        synthetic_dataset_sha256=document["dataset_sha256"],
        max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        inference_service_executable_sha256=(
            inference_service_executable_sha256
        ),
    )
    with pytest.raises(
        ExternalBaselineError,
        match="adapter command does not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=mismatched_command_protocol,
        )

    mismatched_limits_directory = tmp_path / "mismatched-limits"
    mismatched_limits_directory.mkdir()
    mismatched_limits_protocol = write_frozen_external_protocol(
        mismatched_limits_directory,
        systems,
        adapter_revisions={
            "fixture-adapter": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "fixture-adapter": FIXTURE_ENVIRONMENT_ID,
        },
        adapter_entrypoint_sha256s=adapter_entrypoint_sha256s,
        adapter_source_tree_sha256s=adapter_source_tree_sha256s,
        adapter_runtime_executable_sha256s=(
            adapter_runtime_executable_sha256s
        ),
        adapter_environment_sha256s=adapter_environment_sha256s,
        adapter_command_sha256s=adapter_command_sha256s,
        synthetic_dataset_sha256=document["dataset_sha256"],
        max_memory_mb=512,
        inference_service_executable_sha256=(
            inference_service_executable_sha256
        ),
    )
    with pytest.raises(
        ExternalBaselineError,
        match="run manifest limits do not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=mismatched_limits_protocol,
        )

    mismatched_network_directory = tmp_path / "mismatched-network"
    mismatched_network_directory.mkdir()
    mismatched_network_protocol = write_frozen_external_protocol(
        mismatched_network_directory,
        systems,
        adapter_revisions={
            "fixture-adapter": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "fixture-adapter": FIXTURE_ENVIRONMENT_ID,
        },
        adapter_entrypoint_sha256s=adapter_entrypoint_sha256s,
        adapter_source_tree_sha256s=adapter_source_tree_sha256s,
        adapter_runtime_executable_sha256s=(
            adapter_runtime_executable_sha256s
        ),
        adapter_environment_sha256s=adapter_environment_sha256s,
        adapter_command_sha256s=adapter_command_sha256s,
        synthetic_dataset_sha256=document["dataset_sha256"],
        max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        network_isolation_evidence_sha256="f" * 64,
        inference_service_executable_sha256=(
            inference_service_executable_sha256
        ),
    )
    with pytest.raises(
        ExternalBaselineError,
        match="network-isolation evidence does not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=mismatched_network_protocol,
        )

    mismatched_service_directory = tmp_path / "mismatched-service"
    mismatched_service_directory.mkdir()
    mismatched_service_protocol = write_frozen_external_protocol(
        mismatched_service_directory,
        systems,
        adapter_revisions={
            "fixture-adapter": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "fixture-adapter": FIXTURE_ENVIRONMENT_ID,
        },
        adapter_entrypoint_sha256s=adapter_entrypoint_sha256s,
        adapter_source_tree_sha256s=adapter_source_tree_sha256s,
        adapter_runtime_executable_sha256s=(
            adapter_runtime_executable_sha256s
        ),
        adapter_environment_sha256s=adapter_environment_sha256s,
        adapter_command_sha256s=adapter_command_sha256s,
        synthetic_dataset_sha256=document["dataset_sha256"],
        max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        inference_service_executable_sha256="f" * 64,
    )
    with pytest.raises(
        ExternalBaselineError,
        match="inference-service accounting does not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=mismatched_service_protocol,
        )

    different_lock_path = tmp_path / "different-requirements.lock"
    different_lock_path.write_text(
        "different-package==2.0.0\n",
        encoding="utf-8",
    )
    different_lock = capture_dependency_lock_evidence(different_lock_path)
    mismatched_lock_candidate = tmp_path / "mismatched-lock-candidate.json"
    mismatched_lock_manifest_path = tmp_path / "mismatched-lock-manifest.json"
    mismatched_lock_manifest = run_external_cases(
        valid_adapter_command(tmp_path),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=mismatched_lock_candidate,
        limits=RunnerLimits(
            timeout_seconds=300,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=claim_identity(),
        dependency_lock=different_lock,
        adapter_entrypoint=retained_adapter_entrypoint(tmp_path),
        adapter_source=retained_adapter_source(tmp_path),
        network_isolation=retained_network_isolation(tmp_path),
        inference_service=retained_inference_service(),
    )
    mismatched_lock_manifest_path.write_text(
        mismatched_lock_manifest.to_json(),
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalBaselineError,
        match="dependency-lock evidence does not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(mismatched_lock_manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=protocol_path,
        )

    mismatched_poll_candidate = tmp_path / "mismatched-poll-candidate.json"
    mismatched_poll_manifest_path = tmp_path / "mismatched-poll-manifest.json"
    mismatched_poll_manifest = run_external_cases(
        valid_adapter_command(tmp_path),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=mismatched_poll_candidate,
        limits=RunnerLimits(
            timeout_seconds=300,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
            poll_interval_seconds=0.01,
        ),
        identity=claim_identity(),
        dependency_lock=retained_dependency_lock(tmp_path),
        adapter_entrypoint=retained_adapter_entrypoint(tmp_path),
        adapter_source=retained_adapter_source(tmp_path),
        network_isolation=retained_network_isolation(tmp_path),
        inference_service=retained_inference_service(),
    )
    mismatched_poll_manifest_path.write_text(
        mismatched_poll_manifest.to_json(),
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalBaselineError,
        match="run manifest limits do not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(mismatched_poll_manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=protocol_path,
        )

    mismatched_model_candidate = tmp_path / "mismatched-model-candidate.json"
    mismatched_model_manifest_path = tmp_path / "mismatched-model-manifest.json"
    mismatched_model_manifest = run_external_cases(
        valid_adapter_command(tmp_path),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=mismatched_model_candidate,
        limits=RunnerLimits(
            timeout_seconds=300,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=replace(claim_identity(), model_context_length=4096),
        dependency_lock=retained_dependency_lock(tmp_path),
        adapter_entrypoint=retained_adapter_entrypoint(tmp_path),
        adapter_source=retained_adapter_source(tmp_path),
        network_isolation=retained_network_isolation(tmp_path),
        inference_service=retained_inference_service(),
    )
    mismatched_model_manifest_path.write_text(
        mismatched_model_manifest.to_json(),
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalBaselineError,
        match="run manifest model contract does not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(mismatched_model_manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=protocol_path,
        )

    original_manifest_loader = external_runner_module.load_external_run_manifest

    def load_then_mutate_candidate(*args, **kwargs):
        loaded = original_manifest_loader(*args, **kwargs)
        candidate_path.write_text(
            candidate_path.read_text(encoding="utf-8") + " ",
            encoding="utf-8",
        )
        return loaded

    monkeypatch.setattr(
        external_runner_module,
        "load_external_run_manifest",
        load_then_mutate_candidate,
    )
    with pytest.raises(ExternalBaselineError, match="changed after manifest validation"):
        run_benchmark(
            config,
            external_manifest_paths=(manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=protocol_path,
        )

    corpus_path.write_text(
        corpus_path.read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )
    with pytest.raises(ExternalRunnerError, match="corpus file SHA-256"):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )


def test_ready_manifest_wraps_candidate_disappearance_during_validation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = run_external_cases(
        valid_adapter_command(tmp_path),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=claim_identity(),
        dependency_lock=retained_dependency_lock(tmp_path),
        adapter_entrypoint=retained_adapter_entrypoint(tmp_path),
        adapter_source=retained_adapter_source(tmp_path),
        network_isolation=retained_network_isolation(tmp_path),
        inference_service=retained_inference_service(),
    )
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    original_loader = external_runner_module.load_strict_json_file

    def remove_before_candidate_load(path, *, limits, label):
        if label == "manifest candidate":
            candidate_path.unlink()
        return original_loader(path, limits=limits, label=label)

    monkeypatch.setattr(
        external_runner_module,
        "load_strict_json_file",
        remove_before_candidate_load,
    )

    with pytest.raises(
        ExternalRunnerError,
        match="manifest candidate could not be validated",
    ):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )


def test_ready_manifest_rejects_rehashed_candidate_producer_mismatch(
    tmp_path,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = run_external_cases(
        valid_adapter_command(tmp_path),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=claim_identity(),
        dependency_lock=retained_dependency_lock(tmp_path),
        adapter_entrypoint=retained_adapter_entrypoint(tmp_path),
        adapter_source=retained_adapter_source(tmp_path),
        network_isolation=retained_network_isolation(tmp_path),
        inference_service=retained_inference_service(),
    )
    candidate_payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate_payload["producer"]["adapter_revision"] = "forged-revision"
    candidate_payload.pop("candidate_payload_sha256")
    candidate_payload["candidate_payload_sha256"] = _canonical_sha256(
        candidate_payload
    )
    candidate_text = (
        json.dumps(
            candidate_payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )
    candidate_path.write_text(candidate_text, encoding="utf-8")
    candidate_bytes = candidate_path.read_bytes()

    manifest_payload = manifest.to_dict()
    manifest_payload["candidate_bytes"] = len(candidate_bytes)
    manifest_payload["candidate_sha256"] = hashlib.sha256(candidate_bytes).hexdigest()
    manifest_payload.pop("manifest_sha256")
    manifest_payload["manifest_sha256"] = _canonical_sha256(manifest_payload)
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

    with pytest.raises(ExternalRunnerError, match="registered runner identity"):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )


def test_ready_candidate_with_unrecorded_identity_cannot_cross_frozen_protocol(
    tmp_path,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    manifest = run_external_cases(
        valid_adapter_command(tmp_path),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
    )
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    systems = (
        "fixture-adapter",
        "fixture-adapter-b",
        "fixture-adapter-c",
        "fixture-adapter-d",
    )
    protocol_path = write_frozen_external_protocol(
        tmp_path,
        systems,
        adapter_revisions={
            "fixture-adapter": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "fixture-adapter": FIXTURE_ENVIRONMENT_ID,
        },
        synthetic_dataset_sha256=document["dataset_sha256"],
        max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
    )

    with pytest.raises(
        ExternalBaselineError,
        match="adapter revision does not match the frozen protocol",
    ):
        run_benchmark(
            config,
            external_manifest_paths=(manifest_path,),
            expected_external_systems=systems,
            external_protocol_path=protocol_path,
        )

    assert manifest.ready_for_scoring
    assert not manifest.claim_metadata_complete


def test_manifest_tampering_is_rejected_before_candidate_scoring(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest = run_external_command(
        valid_adapter_command(tmp_path),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    ).to_dict()
    manifest["system"] = "forged-system"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ExternalRunnerError, match="SHA-256 mismatch"):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )


def test_manifest_memory_limit_attestation_relations_fail_closed(tmp_path: Path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    runtime = getattr(sys, "_base_executable", sys.executable)
    failed = run_external_command(
        [runtime, "-c", "raise SystemExit(7)"],
        system="failed-memory-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
    ).to_dict()
    assert not failed["process_succeeded"]

    def write_rehashed(name: str, payload: dict) -> Path:
        path = tmp_path / name
        payload.pop("manifest_sha256", None)
        payload["manifest_sha256"] = _canonical_sha256(payload)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    conservative_failure = json.loads(json.dumps(failed))
    conservative_failure["memory_limit_enforced"] = False
    conservative_path = write_rehashed(
        "conservative-failure-manifest.json",
        conservative_failure,
    )

    reference = load_external_run_manifest(
        conservative_path,
        expected_dataset_sha256=document["dataset_sha256"],
    )
    assert reference.failure_reason == "nonzero_exit"

    unattested_success = json.loads(json.dumps(conservative_failure))
    unattested_success.update(
        {
            "exit_code": 0,
            "process_succeeded": True,
            "termination_reason": None,
        }
    )
    unattested_success_path = write_rehashed(
        "unattested-success-manifest.json",
        unattested_success,
    )
    with pytest.raises(
        ExternalRunnerError,
        match="memory-limit flag is inconsistent",
    ):
        load_external_run_manifest(
            unattested_success_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )

    unconfigured_claim = json.loads(json.dumps(conservative_failure))
    unconfigured_claim["limits"]["max_memory_mb"] = None
    unconfigured_claim["memory_limit_enforced"] = True
    unconfigured_claim_path = write_rehashed(
        "unconfigured-memory-claim-manifest.json",
        unconfigured_claim,
    )
    with pytest.raises(
        ExternalRunnerError,
        match="memory-limit flag is inconsistent",
    ):
        load_external_run_manifest(
            unconfigured_claim_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )


def test_rehashed_inconsistent_case_audit_record_is_rejected(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path, histories=2)
    candidate_path = tmp_path / "candidate.json"
    original_payload = run_external_cases(
        valid_adapter_command(tmp_path),
        system="fixture-adapter",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=claim_identity(),
        dependency_lock=retained_dependency_lock(tmp_path),
        adapter_entrypoint=retained_adapter_entrypoint(tmp_path),
        adapter_source=retained_adapter_source(tmp_path),
        network_isolation=retained_network_isolation(tmp_path),
        inference_service=retained_inference_service(),
    ).to_dict()
    payload = json.loads(json.dumps(original_payload))
    payload["case_runs"][0]["stdout_bytes"] += 1
    unsigned = dict(payload)
    unsigned.pop("manifest_sha256")
    canonical = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    payload["manifest_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ExternalRunnerError, match="stdout byte count"):
        load_external_run_manifest(
            manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )

    count_payload = json.loads(json.dumps(original_payload))
    count_payload["case_count"] = 3
    count_unsigned = dict(count_payload)
    count_unsigned.pop("manifest_sha256")
    count_canonical = json.dumps(
        count_unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    count_payload["manifest_sha256"] = hashlib.sha256(count_canonical.encode("utf-8")).hexdigest()
    count_manifest_path = tmp_path / "count-manifest.json"
    count_manifest_path.write_text(json.dumps(count_payload), encoding="utf-8")

    with pytest.raises(ExternalRunnerError, match="case_count.*retained corpus"):
        load_external_run_manifest(
            count_manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )

    corpus_payload = json.loads(json.dumps(original_payload))
    corpus_payload["case_runs"][0]["corpus_sha256"] = "f" * 64
    corpus_payload.pop("manifest_sha256")
    corpus_payload["manifest_sha256"] = _canonical_sha256(
        corpus_payload
    )
    corpus_manifest_path = tmp_path / "corpus-evidence-manifest.json"
    corpus_manifest_path.write_text(
        json.dumps(corpus_payload),
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalRunnerError,
        match="case audit corpus evidence is inconsistent",
    ):
        load_external_run_manifest(
            corpus_manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )

    candidate_payload = json.loads(json.dumps(original_payload))
    candidate_payload["case_runs"][0][
        "candidate_payload_sha256"
    ] = "f" * 64
    candidate_payload.pop("manifest_sha256")
    candidate_payload["manifest_sha256"] = _canonical_sha256(
        candidate_payload
    )
    candidate_manifest_path = (
        tmp_path / "candidate-evidence-manifest.json"
    )
    candidate_manifest_path.write_text(
        json.dumps(candidate_payload),
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalRunnerError,
        match="case_runs\\[0\\] candidate payload evidence is inconsistent",
    ):
        load_external_run_manifest(
            candidate_manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )

    order_payload = json.loads(json.dumps(original_payload))
    order_payload["case_runs"].reverse()
    order_payload.pop("manifest_sha256")
    order_payload["manifest_sha256"] = _canonical_sha256(order_payload)
    order_manifest_path = tmp_path / "case-order-manifest.json"
    order_manifest_path.write_text(
        json.dumps(order_payload),
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalRunnerError,
        match="case audit is not the ordered corpus prefix",
    ):
        load_external_run_manifest(
            order_manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )

    path_payload = json.loads(json.dumps(original_payload))
    first_case = path_payload["case_runs"][0]
    original_case_corpus = first_case["corpus_path"]
    forged_case_corpus = str(
        (tmp_path / "forged-case" / "corpus.json").resolve()
    )
    first_case["corpus_path"] = forged_case_corpus
    first_case["command"] = [
        part.replace(original_case_corpus, forged_case_corpus)
        for part in first_case["command"]
    ]
    path_payload.pop("manifest_sha256")
    path_payload["manifest_sha256"] = _canonical_sha256(path_payload)
    path_manifest_path = tmp_path / "case-path-manifest.json"
    path_manifest_path.write_text(
        json.dumps(path_payload),
        encoding="utf-8",
    )
    with pytest.raises(
        ExternalRunnerError,
        match="case audit paths are inconsistent",
    ):
        load_external_run_manifest(
            path_manifest_path,
            expected_dataset_sha256=document["dataset_sha256"],
        )


def test_runner_timeout_is_a_retained_nonwin_and_releases_process(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"

    manifest = run_external_command(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        system="timeout-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=0.05, poll_interval_seconds=0.01),
    )

    assert not manifest.process_succeeded
    assert not manifest.candidate_valid
    assert not manifest.ready_for_scoring
    assert manifest.termination_reason == "timeout"


class _FakeDarwinEnvironmentFile:
    def __init__(self, payload: bytes) -> None:
        self.data = bytearray(payload)
        self.position = 0
        self.closed = False

    def __enter__(self) -> _FakeDarwinEnvironmentFile:
        return self

    def __exit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        self.closed = True

    def seek(self, offset: int) -> int:
        self.position = offset
        return offset

    def read(self, size: int = -1) -> bytes:
        end = (
            len(self.data)
            if size < 0
            else min(
                len(self.data),
                self.position + size,
            )
        )
        result = bytes(self.data[self.position : end])
        self.position = end
        return result

    def write(self, value: bytes) -> int:
        end = self.position + len(value)
        if end > len(self.data):
            self.data.extend(b"\x00" * (end - len(self.data)))
        self.data[self.position : end] = value
        self.position = end
        return len(value)

    def truncate(self, size: int = 0) -> int:
        del self.data[size:]
        if self.position > size:
            self.position = size
        return size

    def flush(self) -> None:
        return None


def _install_fake_darwin_environment_handoff(
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    *,
    payload: bytes | None = None,
    expected_sha256: str | None = None,
    link_count: int = 0,
    reported_size: int | None = None,
) -> tuple[tuple[int, int, str], _FakeDarwinEnvironmentFile]:
    handoff_payload = (
        external_runner_module._encode_darwin_process_environment(environment)
        if payload is None
        else payload
    )
    descriptor = 17
    stream = _FakeDarwinEnvironmentFile(handoff_payload)
    handoff_link_count = link_count
    handoff_reported_size = (
        len(handoff_payload) if reported_size is None else reported_size
    )

    class FileInformation:
        st_mode = 0o100600
        st_nlink = handoff_link_count
        st_size = handoff_reported_size

    def fstat(requested_descriptor: int) -> object:
        if requested_descriptor != descriptor or stream.closed:
            raise OSError(errno.EBADF, "closed test descriptor")
        return FileInformation()

    def fdopen(
        requested_descriptor: int,
        mode: str,
        *,
        closefd: bool,
    ) -> _FakeDarwinEnvironmentFile:
        assert requested_descriptor == descriptor
        assert mode == "r+b"
        assert closefd is True
        return stream

    monkeypatch.setattr(external_runner_module.os, "fstat", fstat)
    monkeypatch.setattr(external_runner_module.os, "fdopen", fdopen)
    return (
        (
            descriptor,
            len(handoff_payload),
            expected_sha256 or hashlib.sha256(handoff_payload).hexdigest(),
        ),
        stream,
    )


def test_darwin_memory_limit_uses_privileged_prelimit_without_preexec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(external_runner_module.os, "name", "posix")
    monkeypatch.setattr(external_runner_module.sys, "platform", "darwin")
    limits = RunnerLimits(
        max_stdout_bytes=101,
        max_stderr_bytes=202,
        max_candidate_bytes=303,
        max_memory_mb=256,
    )
    command = [
        "/usr/bin/runtime",
        "268435456",
        "--",
        external_runner_module._DARWIN_LIMIT_LAUNCHER_PROTOCOL,
    ]
    original_command = list(command)
    handoff = (
        17,
        len(external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL),
        "a" * 64,
    )

    launch_command, preexec_fn = external_runner_module._adapter_process_launch(
        command,
        limits,
        darwin_environment_handoff=handoff,
    )

    assert preexec_fn is None
    assert launch_command[:7] == [
        "/bin/sh",
        "-p",
        "-c",
        external_runner_module._DARWIN_PRELIMIT_LAUNCHER,
        external_runner_module._DARWIN_PRELIMIT_LAUNCHER_PROTOCOL,
        str(256 * 1024),
        "--",
    ]
    assert launch_command[7:13] == [
        sys.executable,
        "-I",
        "-S",
        "-c",
        external_runner_module._DARWIN_LIMIT_LAUNCHER,
        external_runner_module._DARWIN_LIMIT_LAUNCHER_PROTOCOL,
    ]
    assert launch_command[13:19] == [
        str(256 * 1024 * 1024),
        "303",
        "17",
        str(len(external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL)),
        "a" * 64,
        "--",
    ]
    assert launch_command[19:] == original_command
    assert command == original_command
    assert 'ulimit -S -H -v "$1"' in external_runner_module._DARWIN_PRELIMIT_LAUNCHER
    assert 'exec "$@"' in external_runner_module._DARWIN_PRELIMIT_LAUNCHER
    assert (
        f'"{external_runner_module._DARWIN_PRELIMIT_LAUNCHER_PROTOCOL}"'
        in external_runner_module._DARWIN_PRELIMIT_LAUNCHER
    )
    assert (
        'ENVIRONMENT_PROTOCOL = b"ctxc-darwin-environment-v1\\x00"'
        in external_runner_module._DARWIN_LIMIT_LAUNCHER
    )
    assert (
        "ENVIRONMENT_MAX_BYTES = "
        f"{external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_MAX_BYTES:_}"
        in external_runner_module._DARWIN_LIMIT_LAUNCHER
    )
    assert (
        f'"{external_runner_module._DARWIN_LIMIT_LAUNCHER_PROTOCOL}"'
        in external_runner_module._DARWIN_LIMIT_LAUNCHER
    )
    assert external_runner_module._posix_limit_setup(limits) is None


def test_darwin_launch_context_uses_empty_supervisor_environment_and_closes_handoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(external_runner_module.sys, "platform", "darwin")
    environment = {
        "CTXC_EMPTY": "",
        "CTXC_ENV": "literal=value",
    }
    handoff = (
        17,
        len(external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL),
        "a" * 64,
    )
    lifecycle: list[str] = []

    class FakeHandoffContext:
        def __enter__(self) -> tuple[int, int, str]:
            lifecycle.append("entered")
            return handoff

        def __exit__(
            self,
            _exception_type: object,
            _exception: object,
            _traceback: object,
        ) -> None:
            lifecycle.append("closed")

    def handoff_factory(
        requested_environment: dict[str, str],
        *,
        directory: Path,
    ) -> FakeHandoffContext:
        assert requested_environment == environment
        assert directory == tmp_path
        return FakeHandoffContext()

    monkeypatch.setattr(
        external_runner_module,
        "_darwin_environment_handoff",
        handoff_factory,
    )

    with external_runner_module._adapter_process_launch_context(
        ["/usr/bin/runtime", "adapter.py"],
        RunnerLimits(max_memory_mb=256),
        environment=environment,
        directory=tmp_path,
    ) as (launch_command, preexec_fn, launch_environment, pass_fds):
        assert lifecycle == ["entered"]
        assert preexec_fn is None
        assert launch_environment == {}
        assert pass_fds == (17,)
        assert launch_command[-2:] == ["/usr/bin/runtime", "adapter.py"]

    assert lifecycle == ["entered", "closed"]
    assert environment == {"CTXC_EMPTY": "", "CTXC_ENV": "literal=value"}


def test_darwin_limit_launcher_rechecks_memory_and_sets_exact_file_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_bytes = 32_768 * 1024 * 1024
    file_bytes = 303
    fake_resource = ModuleType("resource")
    fake_resource.RLIMIT_AS = 1  # type: ignore[attr-defined]
    fake_resource.RLIMIT_FSIZE = 2  # type: ignore[attr-defined]
    fake_resource.RLIM_INFINITY = -1  # type: ignore[attr-defined]
    limits = {
        fake_resource.RLIMIT_AS: (memory_bytes, memory_bytes),
        fake_resource.RLIMIT_FSIZE: (101, 1_024),
    }
    applied: list[tuple[int, tuple[int, int]]] = []
    executed: list[tuple[str, list[str], dict[str, str]]] = []
    expected_environment = {
        "CTXC_EMPTY": "",
        "CTXC_ENV": "exact=\nSnowman: \u2603\tvalue",
    }
    handoff, handoff_stream = _install_fake_darwin_environment_handoff(
        monkeypatch,
        expected_environment,
    )

    def getrlimit(resource_name: int) -> tuple[int, int]:
        return limits[resource_name]

    def setrlimit(
        resource_name: int,
        requested: tuple[int, int],
    ) -> None:
        applied.append((resource_name, requested))
        if resource_name == fake_resource.RLIMIT_FSIZE:
            assert handoff_stream.closed
            assert handoff_stream.data == b""

    def execve(
        executable: str,
        argv: list[str],
        environment: dict[str, str],
    ) -> None:
        assert handoff_stream.closed
        executed.append((executable, argv, environment))

    fake_resource.getrlimit = getrlimit  # type: ignore[attr-defined]
    fake_resource.setrlimit = setrlimit  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "resource", fake_resource)
    monkeypatch.setattr(external_runner_module.os, "execve", execve)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "darwin-limit-launcher",
            external_runner_module._DARWIN_LIMIT_LAUNCHER_PROTOCOL,
            str(memory_bytes),
            str(file_bytes),
            str(handoff[0]),
            str(handoff[1]),
            handoff[2],
            "--",
            "/usr/bin/runtime",
            "adapter.py",
        ],
    )

    exec(external_runner_module._DARWIN_LIMIT_LAUNCHER, {})

    assert applied == [
        (fake_resource.RLIMIT_FSIZE, (file_bytes, file_bytes)),
    ]
    assert executed == [
        (
            "/usr/bin/runtime",
            ["/usr/bin/runtime", "adapter.py"],
            expected_environment,
        )
    ]
    assert handoff_stream.data == b""


@pytest.mark.parametrize(
    (
        "payload",
        "digest_override",
        "link_count",
        "reported_size",
        "message",
        "consumed",
    ),
    [
        pytest.param(
            external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL
            + b"CTXC_B=2\x00CTXC_A=1\x00",
            None,
            0,
            None,
            "Darwin environment handoff names are not canonical",
            True,
            id="out-of-order",
        ),
        pytest.param(
            external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL
            + b"CTXC_A=1\x00CTXC_A=2\x00",
            None,
            0,
            None,
            "Darwin environment handoff names are not canonical",
            True,
            id="duplicate",
        ),
        pytest.param(
            external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL + b"CTXC_A=1\x00",
            "0" * 64,
            0,
            None,
            "Darwin environment handoff digest mismatch",
            True,
            id="wrong-digest",
        ),
        pytest.param(
            external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL + b"CTXC_A=1\x00",
            None,
            1,
            None,
            "invalid Darwin environment handoff file",
            False,
            id="linked-file",
        ),
        pytest.param(
            b"ctxc-darwin-environment-v0\x00CTXC_A=1\x00",
            None,
            0,
            None,
            "Darwin environment handoff protocol mismatch",
            True,
            id="wrong-protocol",
        ),
        pytest.param(
            external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL
            + b"CTXC_A=1",
            None,
            0,
            None,
            "Darwin environment handoff is not canonical",
            True,
            id="missing-terminal-nul",
        ),
        pytest.param(
            external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL
            + b"CTXC_A=\xff\x00",
            None,
            0,
            None,
            "Darwin environment handoff encoding is invalid",
            True,
            id="invalid-utf8",
        ),
        pytest.param(
            external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL
            + b"1CTXC=value\x00",
            None,
            0,
            None,
            "Darwin environment handoff names are not canonical",
            True,
            id="invalid-name",
        ),
        pytest.param(
            external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL
            + b"CTXC_A\x00",
            None,
            0,
            None,
            "Darwin environment handoff record is invalid",
            True,
            id="missing-equals",
        ),
        pytest.param(
            external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL
            + b"".join(
                f"A{index:04d}=x\x00".encode("ascii")
                for index in range(1_025)
            ),
            None,
            0,
            None,
            "Darwin environment handoff has invalid record count",
            True,
            id="too-many-records",
        ),
        pytest.param(
            external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL
            + (b"A" * 4_097)
            + b"=x\x00",
            None,
            0,
            None,
            "Darwin environment handoff record exceeds its limit",
            True,
            id="oversized-name",
        ),
        pytest.param(
            external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL
            + b"CTXC_A="
            + (b"x" * 1_000_001)
            + b"\x00",
            None,
            0,
            None,
            "Darwin environment handoff record exceeds its limit",
            True,
            id="oversized-value",
        ),
        pytest.param(
            external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL
            + b"".join(
                name + b"=" + (b"x" * 1_000_000) + b"\x00"
                for name in (b"A", b"B", b"C", b"D")
            ),
            None,
            0,
            None,
            "Darwin environment handoff exceeds its aggregate limit",
            True,
            id="oversized-aggregate",
        ),
        pytest.param(
            external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL
            + b"CTXC_A=1\x00",
            None,
            0,
            len(external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL)
            + len(b"CTXC_A=1\x00")
            + 1,
            "invalid Darwin environment handoff file",
            False,
            id="fstat-size-mismatch",
        ),
    ],
)
def test_darwin_limit_launcher_rejects_untrusted_environment_payload(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    digest_override: str | None,
    link_count: int,
    reported_size: int | None,
    message: str,
    consumed: bool,
) -> None:
    memory_bytes = 32_768 * 1024 * 1024
    file_bytes = 303
    fake_resource = ModuleType("resource")
    fake_resource.RLIMIT_AS = 1  # type: ignore[attr-defined]
    fake_resource.RLIMIT_FSIZE = 2  # type: ignore[attr-defined]
    fake_resource.RLIM_INFINITY = -1  # type: ignore[attr-defined]
    limits = {
        fake_resource.RLIMIT_AS: (memory_bytes, memory_bytes),
        fake_resource.RLIMIT_FSIZE: (1, 1_024),
    }
    executed: list[str] = []
    handoff, handoff_stream = _install_fake_darwin_environment_handoff(
        monkeypatch,
        {},
        payload=payload,
        expected_sha256=digest_override,
        link_count=link_count,
        reported_size=reported_size,
    )

    def getrlimit(resource_name: int) -> tuple[int, int]:
        return limits[resource_name]

    def setrlimit(
        _resource_name: int,
        _requested: tuple[int, int],
    ) -> None:
        return None

    def execve(
        executable: str,
        _argv: list[str],
        _environment: dict[str, str],
    ) -> None:
        executed.append(executable)

    fake_resource.getrlimit = getrlimit  # type: ignore[attr-defined]
    fake_resource.setrlimit = setrlimit  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "resource", fake_resource)
    monkeypatch.setattr(external_runner_module.os, "execve", execve)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "darwin-limit-launcher",
            external_runner_module._DARWIN_LIMIT_LAUNCHER_PROTOCOL,
            str(memory_bytes),
            str(file_bytes),
            str(handoff[0]),
            str(handoff[1]),
            handoff[2],
            "--",
            "/usr/bin/runtime",
        ],
    )

    with pytest.raises(SystemExit, match=message):
        exec(external_runner_module._DARWIN_LIMIT_LAUNCHER, {})

    assert not executed
    assert handoff_stream.closed is consumed
    if consumed:
        assert handoff_stream.data == b""
    else:
        assert handoff_stream.data == payload


@pytest.mark.parametrize(
    "raw_size",
    [
        str(len(external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL) - 1),
        str(external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_MAX_BYTES + 1),
    ],
    ids=["below-protocol", "above-maximum"],
)
def test_darwin_limit_launcher_rejects_environment_size_bounds(
    monkeypatch: pytest.MonkeyPatch,
    raw_size: str,
) -> None:
    memory_bytes = 1
    fake_resource = ModuleType("resource")
    fake_resource.RLIMIT_AS = 1  # type: ignore[attr-defined]
    fake_resource.RLIMIT_FSIZE = 2  # type: ignore[attr-defined]
    fake_resource.RLIM_INFINITY = -1  # type: ignore[attr-defined]

    def getrlimit(_resource_name: int) -> tuple[int, int]:
        return (memory_bytes, memory_bytes)

    def fail_after_size_validation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid size must fail before file access or limit changes")

    fake_resource.getrlimit = getrlimit  # type: ignore[attr-defined]
    fake_resource.setrlimit = fail_after_size_validation  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "resource", fake_resource)
    monkeypatch.setattr(external_runner_module.os, "fstat", fail_after_size_validation)
    monkeypatch.setattr(external_runner_module.os, "execve", fail_after_size_validation)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "darwin-limit-launcher",
            external_runner_module._DARWIN_LIMIT_LAUNCHER_PROTOCOL,
            str(memory_bytes),
            "1",
            "17",
            raw_size,
            "0" * 64,
            "--",
            "/usr/bin/runtime",
        ],
    )

    with pytest.raises(
        SystemExit,
        match="invalid Darwin environment handoff size",
    ):
        exec(external_runner_module._DARWIN_LIMIT_LAUNCHER, {})


def test_darwin_limit_launcher_retains_environment_consume_io_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_bytes = 1
    fake_resource = ModuleType("resource")
    fake_resource.RLIMIT_AS = 1  # type: ignore[attr-defined]
    fake_resource.RLIMIT_FSIZE = 2  # type: ignore[attr-defined]
    fake_resource.RLIM_INFINITY = -1  # type: ignore[attr-defined]

    def getrlimit(_resource_name: int) -> tuple[int, int]:
        return (memory_bytes, memory_bytes)

    def fail_fstat(_descriptor: int) -> object:
        raise OSError(errno.EIO, "injected handoff read failure")

    def fail_after_consume(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("consume failure must precede file limit and exec")

    fake_resource.getrlimit = getrlimit  # type: ignore[attr-defined]
    fake_resource.setrlimit = fail_after_consume  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "resource", fake_resource)
    monkeypatch.setattr(external_runner_module.os, "fstat", fail_fstat)
    monkeypatch.setattr(external_runner_module.os, "execve", fail_after_consume)
    payload = external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "darwin-limit-launcher",
            external_runner_module._DARWIN_LIMIT_LAUNCHER_PROTOCOL,
            str(memory_bytes),
            "1",
            "17",
            str(len(payload)),
            hashlib.sha256(payload).hexdigest(),
            "--",
            "/usr/bin/runtime",
        ],
    )

    with pytest.raises(
        SystemExit,
        match="could not consume Darwin environment handoff: OSError",
    ):
        exec(external_runner_module._DARWIN_LIMIT_LAUNCHER, {})


@pytest.mark.parametrize(
    "raw_descriptor",
    ["9" * 20, str(2_147_483_648)],
    ids=["oversized-decimal", "outside-descriptor-range"],
)
def test_darwin_limit_launcher_rejects_invalid_descriptor_bounds(
    monkeypatch: pytest.MonkeyPatch,
    raw_descriptor: str,
) -> None:
    memory_bytes = 1
    fake_resource = ModuleType("resource")
    fake_resource.RLIMIT_AS = 1  # type: ignore[attr-defined]
    fake_resource.RLIMIT_FSIZE = 2  # type: ignore[attr-defined]
    fake_resource.RLIM_INFINITY = -1  # type: ignore[attr-defined]

    def getrlimit(_resource_name: int) -> tuple[int, int]:
        return (memory_bytes, memory_bytes)

    def setrlimit(
        _resource_name: int,
        _requested: tuple[int, int],
    ) -> None:
        raise AssertionError("invalid descriptor must fail before setrlimit")

    fake_resource.getrlimit = getrlimit  # type: ignore[attr-defined]
    fake_resource.setrlimit = setrlimit  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "resource", fake_resource)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "darwin-limit-launcher",
            external_runner_module._DARWIN_LIMIT_LAUNCHER_PROTOCOL,
            str(memory_bytes),
            "1",
            raw_descriptor,
            str(len(external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL)),
            "0" * 64,
            "--",
            "/usr/bin/runtime",
        ],
    )

    with pytest.raises(
        SystemExit,
        match="invalid Darwin environment handoff descriptor",
    ):
        exec(external_runner_module._DARWIN_LIMIT_LAUNCHER, {})


@pytest.mark.parametrize(
    "observed_memory_limit",
    [
        (32_768 * 1024 * 1024 - 1, 32_768 * 1024 * 1024),
        (32_768 * 1024 * 1024, 32_768 * 1024 * 1024 + 1),
        (1, -1),
    ],
)
def test_darwin_limit_launcher_rejects_inexact_prelimited_memory(
    monkeypatch: pytest.MonkeyPatch,
    observed_memory_limit: tuple[int, int],
) -> None:
    memory_bytes = 32_768 * 1024 * 1024
    file_bytes = 303
    fake_resource = ModuleType("resource")
    fake_resource.RLIMIT_AS = 1  # type: ignore[attr-defined]
    fake_resource.RLIMIT_FSIZE = 2  # type: ignore[attr-defined]
    fake_resource.RLIM_INFINITY = -1  # type: ignore[attr-defined]
    limits = {
        fake_resource.RLIMIT_AS: observed_memory_limit,
        fake_resource.RLIMIT_FSIZE: (1, -1),
    }
    executed: list[str] = []

    def getrlimit(resource_name: int) -> tuple[int, int]:
        return limits[resource_name]

    def setrlimit(
        _resource_name: int,
        _requested: tuple[int, int],
    ) -> None:
        return None

    def execve(
        executable: str,
        _argv: list[str],
        _environment: dict[str, str],
    ) -> None:
        executed.append(executable)

    fake_resource.getrlimit = getrlimit  # type: ignore[attr-defined]
    fake_resource.setrlimit = setrlimit  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "resource", fake_resource)
    monkeypatch.setattr(external_runner_module.os, "execve", execve)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "darwin-limit-launcher",
            external_runner_module._DARWIN_LIMIT_LAUNCHER_PROTOCOL,
            str(memory_bytes),
            str(file_bytes),
            "17",
            str(len(external_runner_module._DARWIN_ENVIRONMENT_HANDOFF_PROTOCOL)),
            "a" * 64,
            "--",
            "/usr/bin/runtime",
        ],
    )

    with pytest.raises(
        SystemExit,
        match=(
            r"inherited RLIMIT_AS limit is not exact: "
            rf"{observed_memory_limit[0]}/{observed_memory_limit[1]} != "
            rf"{memory_bytes}/{memory_bytes}"
        ),
    ):
        exec(external_runner_module._DARWIN_LIMIT_LAUNCHER, {})

    assert not executed


def test_darwin_limit_launcher_rejects_file_hard_limit_clamping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_bytes = 32_768 * 1024 * 1024
    file_bytes = 303
    fake_resource = ModuleType("resource")
    fake_resource.RLIMIT_AS = 1  # type: ignore[attr-defined]
    fake_resource.RLIMIT_FSIZE = 2  # type: ignore[attr-defined]
    fake_resource.RLIM_INFINITY = -1  # type: ignore[attr-defined]
    limits = {
        fake_resource.RLIMIT_AS: (memory_bytes, memory_bytes),
        fake_resource.RLIMIT_FSIZE: (1, file_bytes - 1),
    }
    executed: list[str] = []
    handoff, _handoff_stream = _install_fake_darwin_environment_handoff(monkeypatch, {})

    def getrlimit(resource_name: int) -> tuple[int, int]:
        return limits[resource_name]

    def setrlimit(
        _resource_name: int,
        _requested: tuple[int, int],
    ) -> None:
        return None

    def execve(
        executable: str,
        _argv: list[str],
        _environment: dict[str, str],
    ) -> None:
        executed.append(executable)

    fake_resource.getrlimit = getrlimit  # type: ignore[attr-defined]
    fake_resource.setrlimit = setrlimit  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "resource", fake_resource)
    monkeypatch.setattr(external_runner_module.os, "execve", execve)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "darwin-limit-launcher",
            external_runner_module._DARWIN_LIMIT_LAUNCHER_PROTOCOL,
            str(memory_bytes),
            str(file_bytes),
            str(handoff[0]),
            str(handoff[1]),
            handoff[2],
            "--",
            "/usr/bin/runtime",
        ],
    )

    with pytest.raises(
        SystemExit,
        match=(
            rf"inherited hard RLIMIT_FSIZE limit {file_bytes - 1} "
            rf"is below requested {file_bytes}"
        ),
    ):
        exec(external_runner_module._DARWIN_LIMIT_LAUNCHER, {})

    assert not executed


@pytest.mark.parametrize(
    "exception_type",
    [OSError, OverflowError, ValueError],
)
def test_darwin_limit_launcher_rejects_limit_application_failure_before_exec(
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[Exception],
) -> None:
    memory_bytes = 32_768 * 1024 * 1024
    file_bytes = 303
    fake_resource = ModuleType("resource")
    fake_resource.RLIMIT_AS = 1  # type: ignore[attr-defined]
    fake_resource.RLIMIT_FSIZE = 2  # type: ignore[attr-defined]
    fake_resource.RLIM_INFINITY = -1  # type: ignore[attr-defined]
    limits = {
        fake_resource.RLIMIT_AS: (memory_bytes, memory_bytes),
        fake_resource.RLIMIT_FSIZE: (1, -1),
    }
    executed: list[str] = []
    handoff, _handoff_stream = _install_fake_darwin_environment_handoff(monkeypatch, {})

    def getrlimit(resource_name: int) -> tuple[int, int]:
        return limits[resource_name]

    def setrlimit(
        resource_name: int,
        _requested: tuple[int, int],
    ) -> None:
        if resource_name == fake_resource.RLIMIT_FSIZE:
            raise exception_type("injected limit-application failure")

    def execve(
        executable: str,
        _argv: list[str],
        _environment: dict[str, str],
    ) -> None:
        executed.append(executable)

    fake_resource.getrlimit = getrlimit  # type: ignore[attr-defined]
    fake_resource.setrlimit = setrlimit  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "resource", fake_resource)
    monkeypatch.setattr(external_runner_module.os, "execve", execve)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "darwin-limit-launcher",
            external_runner_module._DARWIN_LIMIT_LAUNCHER_PROTOCOL,
            str(memory_bytes),
            str(file_bytes),
            str(handoff[0]),
            str(handoff[1]),
            handoff[2],
            "--",
            "/usr/bin/runtime",
        ],
    )

    with pytest.raises(
        SystemExit,
        match=(
            r"could not apply exact RLIMIT_FSIZE limit: "
            rf"{exception_type.__name__}"
        ),
    ):
        exec(external_runner_module._DARWIN_LIMIT_LAUNCHER, {})

    assert not executed


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="requires the hosted macOS /bin/sh and RLIMIT_AS implementation",
)
def test_darwin_prelimit_is_exact_and_preserves_literal_adapter_argv(
    tmp_path: Path,
) -> None:
    startup_file = tmp_path / "hostile-startup.sh"
    startup_file.write_text("exit 91\n", encoding="utf-8")
    literal_arguments = (
        "; printf injected >&2; exit 92",
        "$(printf injected >&2)",
        "*?[literal]",
    )
    environment = {
        "BASHOPTS": "extdebug",
        "BASH_ENV": str(startup_file),
        "ENV": str(startup_file),
        "IFS": "/",
        "LC_CTYPE": "UTF-8",
        "SHELLOPTS": "xtrace",
    }
    program = (
        "import json,os,resource,sys;"
        "print(json.dumps({'limit':resource.getrlimit(resource.RLIMIT_AS),"
        "'argv':sys.argv[1:],'environment':dict(os.environ)},sort_keys=True))"
    )
    limits = RunnerLimits(
        max_stdout_bytes=1_048_576,
        max_stderr_bytes=1_048_576,
        max_candidate_bytes=1_048_576,
        max_memory_mb=32_768,
    )
    with external_runner_module._adapter_process_launch_context(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            program,
            *literal_arguments,
        ],
        limits,
        environment=environment,
        directory=tmp_path,
    ) as (
        launch_command,
        preexec_fn,
        launch_environment,
        pass_fds,
    ):
        completed = subprocess.run(
            launch_command,
            cwd=tmp_path,
            env=launch_environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=10,
            pass_fds=pass_fds,
        )
        assert launch_environment == {}
        assert len(pass_fds) == 1

    assert preexec_fn is None
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8",
        errors="replace",
    )
    assert completed.stderr == b""
    payload = json.loads(completed.stdout)
    memory_bytes = 32_768 * 1024 * 1024
    assert payload == {
        "limit": [memory_bytes, memory_bytes],
        "argv": list(literal_arguments),
        "environment": environment,
    }
    assert "SHLVL" not in payload["environment"]
    assert "_" not in payload["environment"]


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="requires Darwin to reject a limit below the current VM map",
)
def test_darwin_prelimit_failure_cannot_exec_adapter(tmp_path: Path) -> None:
    marker = tmp_path / "adapter-executed"
    with external_runner_module._adapter_process_launch_context(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            "from pathlib import Path; Path(__import__('sys').argv[1]).touch()",
            str(marker),
        ],
        RunnerLimits(
            max_stdout_bytes=1_024,
            max_stderr_bytes=1_024,
            max_candidate_bytes=1_024,
            max_memory_mb=1,
        ),
        environment={},
        directory=tmp_path,
    ) as (
        launch_command,
        preexec_fn,
        launch_environment,
        pass_fds,
    ):
        completed = subprocess.run(
            launch_command,
            cwd=tmp_path,
            env=launch_environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=10,
            pass_fds=pass_fds,
        )
        assert launch_environment == {}
        assert len(pass_fds) == 1

    assert preexec_fn is None
    assert completed.returncode == 125
    assert b"could not apply exact Darwin RLIMIT_AS limit" in completed.stderr
    assert not marker.exists()


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="requires Darwin to reject a limit below the current VM map",
)
def test_darwin_failed_prelimit_manifest_does_not_attest_memory_limit(
    tmp_path: Path,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    marker = tmp_path / "adapter-executed"
    manifest = run_external_command(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            "from pathlib import Path; Path(__import__('sys').argv[1]).touch()",
            str(marker),
        ],
        system="darwin-prelimit-failure",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=10,
            max_stdout_bytes=1_024,
            max_stderr_bytes=1_024,
            max_candidate_bytes=1_024,
            max_memory_mb=1,
        ),
    )

    assert manifest.exit_code == 125
    assert manifest.termination_reason == "nonzero_exit"
    assert not manifest.process_succeeded
    assert not manifest.memory_limit_enforced
    assert not manifest.ready_for_scoring
    assert not candidate_path.exists()
    assert not marker.exists()


def test_posix_group_eperm_liveness_probe_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def killpg(_process_group_id: int, requested_signal: int) -> None:
        calls.append(requested_signal)
        if requested_signal == 0:
            raise PermissionError(
                errno.EPERM,
                "injected macOS liveness denial",
            )

    monkeypatch.setattr(
        external_runner_module.os,
        "killpg",
        killpg,
        raising=False,
    )

    with pytest.raises(
        ExternalRunnerError,
        match="could not verify the adapter process group after SIGTERM",
    ):
        external_runner_module._terminate_posix_process_group(42)

    assert calls == [
        signal.SIGTERM,
        0,
    ]


def test_darwin_group_eperm_after_term_requires_proof_without_reaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int | str]] = []

    class ExitedProcess:
        pid = 42
        returncode: int | None = None

        def wait(self, *, timeout: int) -> int:
            raise AssertionError(
                "group helper must not reap before zombie proof"
            )

    process = ExitedProcess()
    waitid_results: list[object | None] = [None, object()]

    def killpg(_process_group_id: int, requested_signal: int) -> None:
        events.append(("killpg", requested_signal))
        if requested_signal == 0:
            raise PermissionError(
                errno.EPERM,
                "injected zombie-leader denial",
            )

    def waitid(id_type: int, pid: int, options: int) -> object:
        assert id_type == 1
        assert pid == process.pid
        assert options == 0x0100000D
        events.append(("waitid", pid))
        return waitid_results.pop(0)

    def prove(
        process_group_id: int,
        *,
        expected_leader_pid: int,
        error_type: type[RuntimeError],
        termination_signal_delivered: bool,
    ) -> None:
        assert process_group_id == expected_leader_pid == process.pid
        assert error_type is ExternalRunnerError
        assert termination_signal_delivered is True
        events.append(("proof", "all-zombie"))

    monkeypatch.setattr(
        external_runner_module.os,
        "killpg",
        killpg,
        raising=False,
    )
    monkeypatch.setattr(external_runner_module.os, "P_PID", 1, raising=False)
    monkeypatch.setattr(external_runner_module.os, "WEXITED", 0x00000004, raising=False)
    monkeypatch.setattr(external_runner_module.os, "WNOHANG", 0x00000001, raising=False)
    monkeypatch.setattr(external_runner_module.os, "WNOWAIT", 0x01000008, raising=False)
    monkeypatch.setattr(external_runner_module.os, "waitid", waitid, raising=False)
    monkeypatch.setattr(external_runner_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        external_runner_module,
        "prove_darwin_process_group_all_zombies",
        prove,
    )

    external_runner_module._terminate_posix_process_group(
        process.pid,
        process=process,  # type: ignore[arg-type]
    )

    assert events == [
        ("waitid", process.pid),
        ("killpg", signal.SIGTERM),
        ("waitid", process.pid),
        ("killpg", 0),
        ("proof", "all-zombie"),
    ]


def test_darwin_denied_final_sigkill_retains_successful_sigterm_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int | str]] = []
    observations = iter((False, True))
    liveness_probes = 0

    class ExitingProcess:
        pid = 44
        returncode: int | None = None

        def wait(self, *, timeout: int) -> int:
            raise AssertionError("failed proof must not reap the leader")

    process = ExitingProcess()

    def exited_without_reaping(_process: object) -> bool:
        events.append(("waitid", process.pid))
        return next(observations)

    def killpg(_process_group_id: int, requested_signal: int) -> None:
        nonlocal liveness_probes
        events.append(("killpg", requested_signal))
        if requested_signal == 0:
            liveness_probes += 1
            if liveness_probes == 2:
                raise PermissionError(
                    errno.EPERM,
                    "injected Darwin zombie-only group",
                )
            return
        if requested_signal == external_runner_module._POSIX_SIGKILL:
            raise PermissionError(
                errno.EPERM,
                "injected Darwin zombie-only group",
            )
        assert requested_signal == signal.SIGTERM

    def reject_live_member(
        process_group_id: int,
        *,
        expected_leader_pid: int,
        error_type: type[RuntimeError],
        termination_signal_delivered: bool,
    ) -> None:
        assert process_group_id == expected_leader_pid == process.pid
        assert error_type is ExternalRunnerError
        assert termination_signal_delivered is True
        events.append(("proof", "live-survivor"))
        raise error_type("anchored Darwin process group still contains live PID 45")

    monkeypatch.setattr(external_runner_module.sys, "platform", "darwin")
    monkeypatch.setattr(external_runner_module.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(
        external_runner_module,
        "_posix_process_exited_without_reaping",
        exited_without_reaping,
    )
    monkeypatch.setattr(
        external_runner_module,
        "prove_darwin_process_group_all_zombies",
        reject_live_member,
    )

    with pytest.raises(ExternalRunnerError, match="live PID 45"):
        external_runner_module._terminate_posix_process_group(
            process.pid,
            process=process,  # type: ignore[arg-type]
        )

    assert events == [
        ("waitid", process.pid),
        ("killpg", signal.SIGTERM),
        ("waitid", process.pid),
        ("killpg", 0),
        ("killpg", external_runner_module._POSIX_SIGKILL),
        ("killpg", 0),
        ("proof", "live-survivor"),
    ]


def test_darwin_exited_leader_kill_eperm_requires_proof_without_reaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int | str]] = []

    class ExitedProcess:
        pid = 42
        returncode: int | None = None

        def wait(self, *, timeout: int) -> int:
            raise AssertionError(
                "group helper must not reap before zombie proof"
            )

    process = ExitedProcess()

    def killpg(_process_group_id: int, requested_signal: int) -> None:
        events.append(("killpg", requested_signal))
        raise PermissionError(
            errno.EPERM,
            "injected Darwin zombie-only group",
        )

    def exited_without_reaping(_process: object) -> bool:
        events.append(("waitid", process.pid))
        return True

    def prove(
        process_group_id: int,
        *,
        expected_leader_pid: int,
        error_type: type[RuntimeError],
        termination_signal_delivered: bool,
    ) -> None:
        assert process_group_id == expected_leader_pid == process.pid
        assert error_type is ExternalRunnerError
        assert termination_signal_delivered is False
        events.append(("proof", "all-zombie"))

    monkeypatch.setattr(
        external_runner_module.os,
        "killpg",
        killpg,
        raising=False,
    )
    monkeypatch.setattr(
        external_runner_module,
        "_posix_process_exited_without_reaping",
        exited_without_reaping,
    )
    monkeypatch.setattr(external_runner_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        external_runner_module,
        "prove_darwin_process_group_all_zombies",
        prove,
    )

    external_runner_module._terminate_posix_process_group(
        process.pid,
        process=process,  # type: ignore[arg-type]
    )

    assert events == [
        ("waitid", process.pid),
        ("killpg", external_runner_module._POSIX_SIGKILL),
        ("killpg", 0),
        ("proof", "all-zombie"),
    ]


def test_darwin_successful_sigkill_rejects_live_unsignalable_survivor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int | str]] = []

    class ExitedProcess:
        pid = 43
        returncode = None

        def wait(self, *, timeout: int) -> int:
            raise AssertionError("failed proof must not reap the leader")

    def killpg(_process_group_id: int, requested_signal: int) -> None:
        events.append(("killpg", requested_signal))
        if requested_signal == 0:
            raise PermissionError(
                errno.EPERM,
                "injected unsignalable survivor",
            )

    def reject_live_member(
        process_group_id: int,
        *,
        expected_leader_pid: int,
        error_type: type[RuntimeError],
        termination_signal_delivered: bool,
    ) -> None:
        assert process_group_id == expected_leader_pid == 43
        assert termination_signal_delivered is True
        events.append(("proof", "live-survivor"))
        raise error_type(
            "anchored Darwin process group still contains live PID 44"
        )

    monkeypatch.setattr(
        external_runner_module.os,
        "killpg",
        killpg,
        raising=False,
    )
    monkeypatch.setattr(
        external_runner_module,
        "_posix_process_exited_without_reaping",
        lambda _process: True,
    )
    monkeypatch.setattr(external_runner_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        external_runner_module,
        "prove_darwin_process_group_all_zombies",
        reject_live_member,
    )

    with pytest.raises(ExternalRunnerError, match="live PID 44"):
        external_runner_module._terminate_posix_process_group(
            43,
            process=ExitedProcess(),  # type: ignore[arg-type]
        )

    assert events == [
        ("killpg", external_runner_module._POSIX_SIGKILL),
        ("killpg", 0),
        ("proof", "live-survivor"),
    ]


def test_posix_group_initial_term_eperm_fails_without_reap_or_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []

    class ExitedProcess:
        pid = 42
        returncode = None

    process = ExitedProcess()

    def killpg(_process_group_id: int, requested_signal: int) -> None:
        events.append(("killpg", requested_signal))
        raise PermissionError(
            errno.EPERM,
            "injected ambiguous TERM denial",
        )

    def exited_without_reaping(_process: object) -> bool:
        events.append(("waitid", process.pid))
        return False

    monkeypatch.setattr(external_runner_module.sys, "platform", "linux")
    monkeypatch.setattr(
        external_runner_module.os,
        "killpg",
        killpg,
        raising=False,
    )
    monkeypatch.setattr(
        external_runner_module,
        "_posix_process_exited_without_reaping",
        exited_without_reaping,
    )

    with pytest.raises(
        ExternalRunnerError,
        match="could not terminate the adapter process group",
    ):
        external_runner_module._terminate_posix_process_group(
            process.pid,
            process=process,  # type: ignore[arg-type]
        )

    assert events == [
        ("waitid", process.pid),
        ("killpg", signal.SIGTERM),
    ]


def test_darwin_initial_term_eperm_accepts_stable_all_zombie_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int | str]] = []
    observations = iter((False, True))

    class ExitedProcess:
        pid = 42
        returncode = None

    process = ExitedProcess()

    def killpg(_process_group_id: int, requested_signal: int) -> None:
        events.append(("killpg", requested_signal))
        raise PermissionError(
            errno.EPERM,
            "injected post-exit Darwin TERM denial",
        )

    def exited_without_reaping(_process: object) -> bool:
        events.append(("waitid", process.pid))
        return next(observations)

    def prove(
        process_group_id: int,
        *,
        expected_leader_pid: int,
        error_type: type[RuntimeError],
    ) -> None:
        assert process_group_id == expected_leader_pid == process.pid
        assert error_type is ExternalRunnerError
        events.append(("proof", "all-zombie"))

    monkeypatch.setattr(
        external_runner_module.os,
        "killpg",
        killpg,
        raising=False,
    )
    monkeypatch.setattr(
        external_runner_module,
        "_posix_process_exited_without_reaping",
        exited_without_reaping,
    )
    monkeypatch.setattr(external_runner_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        external_runner_module,
        "prove_darwin_process_group_all_zombies",
        prove,
    )

    external_runner_module._terminate_posix_process_group(
        process.pid,
        process=process,  # type: ignore[arg-type]
    )

    assert events == [
        ("waitid", process.pid),
        ("killpg", signal.SIGTERM),
        ("waitid", process.pid),
        ("proof", "all-zombie"),
    ]


def test_darwin_initial_term_eperm_rejects_live_member_after_leader_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int | str]] = []
    observations = iter((False, True))

    class ExitedProcess:
        pid = 42
        returncode = None

    process = ExitedProcess()

    def killpg(_process_group_id: int, requested_signal: int) -> None:
        events.append(("killpg", requested_signal))
        raise PermissionError(
            errno.EPERM,
            "injected post-exit Darwin TERM denial",
        )

    def exited_without_reaping(_process: object) -> bool:
        events.append(("waitid", process.pid))
        return next(observations)

    def reject_live_member(
        process_group_id: int,
        *,
        expected_leader_pid: int,
        error_type: type[RuntimeError],
    ) -> None:
        assert process_group_id == expected_leader_pid == process.pid
        events.append(("proof", "live-member"))
        raise error_type(
            "anchored Darwin process group still contains live PID 43"
        )

    monkeypatch.setattr(
        external_runner_module.os,
        "killpg",
        killpg,
        raising=False,
    )
    monkeypatch.setattr(
        external_runner_module,
        "_posix_process_exited_without_reaping",
        exited_without_reaping,
    )
    monkeypatch.setattr(external_runner_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        external_runner_module,
        "prove_darwin_process_group_all_zombies",
        reject_live_member,
    )

    with pytest.raises(ExternalRunnerError, match="live PID 43"):
        external_runner_module._terminate_posix_process_group(
            process.pid,
            process=process,  # type: ignore[arg-type]
        )

    assert events == [
        ("waitid", process.pid),
        ("killpg", signal.SIGTERM),
        ("waitid", process.pid),
        ("proof", "live-member"),
    ]


def test_posix_failed_group_cleanup_retains_original_error_during_direct_cleanup() -> None:
    class UnstoppableProcess:
        def kill(self) -> None:
            raise PermissionError("injected direct-child signal denial")

        def wait(self, *, timeout: int) -> int:
            raise subprocess.TimeoutExpired("adapter", timeout)

    with pytest.raises(
        ExternalRunnerError,
        match="original process-group cleanup failure",
    ):
        try:
            raise ExternalRunnerError(
                "original process-group cleanup failure"
            )
        finally:
            external_runner_module._best_effort_stop_and_reap_direct_process(
                UnstoppableProcess(),  # type: ignore[arg-type]
            )


def test_posix_group_exited_leader_is_sigkilled_before_caller_reaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []

    class ExitedProcess:
        pid = 42
        returncode = None

        def wait(self, *, timeout: int) -> int:
            events.append(("wait", timeout))
            return 0

    def killpg(_process_group_id: int, requested_signal: int) -> None:
        events.append(("killpg", requested_signal))

    def exited_without_reaping(_process: object) -> bool:
        events.append(("waitid", 42))
        return True

    monkeypatch.setattr(
        external_runner_module.os,
        "killpg",
        killpg,
        raising=False,
    )
    monkeypatch.setattr(
        external_runner_module,
        "_posix_process_exited_without_reaping",
        exited_without_reaping,
    )
    monkeypatch.setattr(external_runner_module.sys, "platform", "linux")

    external_runner_module._terminate_posix_process_group(
        42,
        process=ExitedProcess(),  # type: ignore[arg-type]
    )

    assert events == [
        ("waitid", 42),
        ("killpg", external_runner_module._POSIX_SIGKILL),
    ]


def test_posix_normal_success_observes_cleans_then_reaps_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []

    class ExitedProcess:
        pid = 42
        returncode: int | None = None

        def poll(self) -> int:
            raise AssertionError("POSIX normal completion must not poll/reap")

        def wait(self, *, timeout: int) -> int:
            events.append(("wait", timeout))
            self.returncode = 0
            return 0

        def kill(self) -> None:
            raise AssertionError("successful cleanup must not kill after reap")

    process = ExitedProcess()

    def exited_without_reaping(_process: object) -> bool:
        events.append(("waitid", process.pid))
        return True

    def killpg(_process_group_id: int, requested_signal: int) -> None:
        assert process.returncode is None
        events.append(("killpg", requested_signal))

    monkeypatch.setattr(
        external_runner_module,
        "_posix_process_exited_without_reaping",
        exited_without_reaping,
    )
    monkeypatch.setattr(
        external_runner_module.os,
        "killpg",
        killpg,
        raising=False,
    )
    monkeypatch.setattr(external_runner_module.sys, "platform", "linux")

    exit_code = external_runner_module._cleanup_and_reap_posix_process(
        process,  # type: ignore[arg-type]
    )

    assert exit_code == 0
    assert events == [
        ("waitid", process.pid),
        ("killpg", external_runner_module._POSIX_SIGKILL),
        ("wait", 1),
    ]


def test_posix_group_reaped_leader_never_signals_reusable_pgid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReapedProcess:
        pid = 42
        returncode = 0

    def killpg(_process_group_id: int, _requested_signal: int) -> None:
        raise AssertionError("a reaped leader's PGID must never be signaled")

    monkeypatch.setattr(
        external_runner_module.os,
        "killpg",
        killpg,
        raising=False,
    )

    with pytest.raises(
        ExternalRunnerError,
        match="adapter process was reaped before process-group cleanup",
    ):
        external_runner_module._terminate_posix_process_group(
            42,
            process=ReapedProcess(),  # type: ignore[arg-type]
        )


def test_posix_group_eperm_sigkill_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExitedProcess:
        pid = 42
        returncode = None

    def killpg(_process_group_id: int, requested_signal: int) -> None:
        if requested_signal == external_runner_module._POSIX_SIGKILL:
            raise PermissionError(
                errno.EPERM,
                "injected macOS signal denial",
            )

    monkeypatch.setattr(
        external_runner_module.os,
        "killpg",
        killpg,
        raising=False,
    )
    monkeypatch.setattr(
        external_runner_module,
        "_posix_process_exited_without_reaping",
        lambda _process: True,
    )
    monkeypatch.setattr(external_runner_module.sys, "platform", "linux")

    with pytest.raises(
        ExternalRunnerError,
        match="could not kill the adapter process group",
    ):
        external_runner_module._terminate_posix_process_group(
            42,
            process=ExitedProcess(),  # type: ignore[arg-type]
        )


def _install_post_start_group_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    message: str,
) -> tuple[list[tuple[str, ...]], list[str]]:
    launches: list[tuple[str, ...]] = []
    direct_cleanup: list[str] = []

    class StartedProcess:
        pid = 4_242

        def __init__(
            self,
            command: list[str],
            *,
            stdout: object,
            stderr: object,
            **_kwargs: object,
        ) -> None:
            self.args = tuple(command)
            self.returncode: int | None = None
            launches.append(self.args)
            stdout.write(b"retained stdout")  # type: ignore[attr-defined]
            stderr.write(b"retained stderr")  # type: ignore[attr-defined]

        def poll(self) -> int:
            raise AssertionError("POSIX monitoring must not reap with poll")

        def kill(self) -> None:
            direct_cleanup.append("kill")
            self.returncode = -9

        def wait(self, *, timeout: int) -> int:
            assert timeout == 1
            direct_cleanup.append("wait")
            if self.returncode is None:
                self.returncode = 0
            return self.returncode

    def reject_group_cleanup(_process: object) -> int:
        raise ExternalRunnerError(message)

    monkeypatch.setattr(
        external_runner_module.subprocess,
        "Popen",
        StartedProcess,
    )
    monkeypatch.setattr(
        external_runner_module.platform,
        "platform",
        lambda: "retained-failure-test-platform",
    )
    monkeypatch.setattr(
        external_runner_module,
        "_uses_windows_process_control",
        lambda: False,
    )
    monkeypatch.setattr(
        external_runner_module,
        "_posix_process_exited_without_reaping",
        lambda _process: True,
    )
    monkeypatch.setattr(
        external_runner_module,
        "_cleanup_and_reap_posix_process",
        reject_group_cleanup,
    )
    return launches, direct_cleanup


def test_whole_corpus_retains_post_start_process_group_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    failure = "anchored Darwin process group still contains live PID 4243"
    launches, direct_cleanup = _install_post_start_group_cleanup_failure(
        monkeypatch,
        message=failure,
    )

    manifest = run_external_command(
        valid_adapter_command(tmp_path),
        system="cleanup-failure-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    )

    assert len(launches) == 1
    assert direct_cleanup == ["kill", "wait"]
    assert manifest.termination_reason == "process_group_cleanup_failed"
    assert manifest.validation_error == failure
    assert manifest.exit_code == -9
    assert not manifest.process_succeeded
    assert not manifest.candidate_valid
    assert not manifest.ready_for_scoring
    assert manifest.stdout_bytes == len(b"retained stdout")
    assert manifest.stdout_sha256 == hashlib.sha256(
        b"retained stdout"
    ).hexdigest()
    assert manifest.stderr_bytes == len(b"retained stderr")
    assert manifest.stderr_sha256 == hashlib.sha256(
        b"retained stderr"
    ).hexdigest()
    assert not candidate_path.exists()

    manifest_path = tmp_path / "retained-whole-manifest.json"
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    reference = load_external_run_manifest(
        manifest_path,
        expected_dataset_sha256=document["dataset_sha256"],
    )
    assert reference.failure_reason == "process_group_cleanup_failed"


def test_per_case_retains_cleanup_failure_and_does_not_launch_next_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path, histories=2)
    candidate_path = tmp_path / "candidate.json"
    failure = "anchored Darwin process group still contains live PID 4243"
    launches, direct_cleanup = _install_post_start_group_cleanup_failure(
        monkeypatch,
        message=failure,
    )

    manifest = run_external_cases(
        valid_adapter_command(tmp_path),
        system="cleanup-failure-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    )

    assert len(launches) == 1
    assert direct_cleanup == ["kill", "wait"]
    assert manifest.case_count == 2
    assert len(manifest.case_runs) == 1
    case_run = manifest.case_runs[0]
    assert case_run.case_id == "history-000"
    assert case_run.termination_reason == "process_group_cleanup_failed"
    assert case_run.validation_error == failure
    assert not case_run.process_succeeded
    assert not case_run.candidate_valid
    assert manifest.termination_reason == (
        "case_failure:history-000:process_group_cleanup_failed"
    )
    assert manifest.validation_error == failure
    assert not manifest.process_succeeded
    assert not manifest.candidate_valid
    assert not manifest.ready_for_scoring
    assert not candidate_path.exists()

    manifest_path = tmp_path / "retained-per-case-manifest.json"
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    reference = load_external_run_manifest(
        manifest_path,
        expected_dataset_sha256=document["dataset_sha256"],
    )
    assert reference.failure_reason == (
        "case_failure:history-000:process_group_cleanup_failed"
    )


def _install_concrete_post_start_posix_failure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    group_signal_error: OSError | None = None,
    wait_timeouts: int = 0,
) -> tuple[list[tuple[str, ...]], list[tuple[str, int]]]:
    launches: list[tuple[str, ...]] = []
    events: list[tuple[str, int]] = []

    class StartedProcess:
        pid = 4_242

        def __init__(
            self,
            command: list[str],
            *,
            stdout: object,
            stderr: object,
            **_kwargs: object,
        ) -> None:
            self.args = tuple(command)
            self.returncode: int | None = None
            self.wait_calls = 0
            launches.append(self.args)
            stdout.write(b"retained stdout")  # type: ignore[attr-defined]
            stderr.write(b"retained stderr")  # type: ignore[attr-defined]

        def poll(self) -> int:
            raise AssertionError("POSIX monitoring must not reap with poll")

        def kill(self) -> None:
            events.append(("kill", self.pid))

        def wait(self, *, timeout: int) -> int:
            assert timeout == 1
            self.wait_calls += 1
            events.append(("wait", self.wait_calls))
            if self.wait_calls <= wait_timeouts:
                raise subprocess.TimeoutExpired(
                    "sensitive-adapter-command",
                    timeout,
                )
            self.returncode = -9
            return self.returncode

    def killpg(_process_group_id: int, requested_signal: int) -> None:
        events.append(("killpg", requested_signal))
        if group_signal_error is not None:
            raise group_signal_error

    monkeypatch.setattr(
        external_runner_module.subprocess,
        "Popen",
        StartedProcess,
    )
    monkeypatch.setattr(
        external_runner_module.platform,
        "platform",
        lambda: "retained-concrete-failure-test-platform",
    )
    monkeypatch.setattr(
        external_runner_module,
        "_uses_windows_process_control",
        lambda: False,
    )
    monkeypatch.setattr(
        external_runner_module,
        "_posix_process_exited_without_reaping",
        lambda _process: True,
    )
    monkeypatch.setattr(
        external_runner_module.os,
        "killpg",
        killpg,
        raising=False,
    )
    monkeypatch.setattr(external_runner_module.sys, "platform", "linux")
    return launches, events


def test_whole_corpus_retains_raw_posix_group_signal_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    launches, events = _install_concrete_post_start_posix_failure(
        monkeypatch,
        group_signal_error=OSError(
            errno.EIO,
            "sensitive injected operating-system detail",
        ),
    )

    manifest = run_external_command(
        valid_adapter_command(tmp_path),
        system="raw-cleanup-oserror-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    )

    assert len(launches) == 1
    assert events == [
        ("killpg", external_runner_module._POSIX_SIGKILL),
        ("kill", 4_242),
        ("wait", 1),
    ]
    assert manifest.termination_reason == "process_group_cleanup_failed"
    assert (
        manifest.validation_error
        == "could not kill the adapter process group"
    )
    assert "sensitive injected" not in manifest.to_json()
    assert manifest.exit_code == -9
    assert not manifest.process_succeeded
    assert not manifest.candidate_valid
    assert not manifest.ready_for_scoring
    assert manifest.stdout_bytes == len(b"retained stdout")
    assert manifest.stdout_sha256 == hashlib.sha256(
        b"retained stdout"
    ).hexdigest()
    assert manifest.stderr_bytes == len(b"retained stderr")
    assert manifest.stderr_sha256 == hashlib.sha256(
        b"retained stderr"
    ).hexdigest()
    assert not candidate_path.exists()

    manifest_path = tmp_path / "raw-cleanup-oserror-manifest.json"
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    reference = load_external_run_manifest(
        manifest_path,
        expected_dataset_sha256=document["dataset_sha256"],
    )
    assert reference.failure_reason == "process_group_cleanup_failed"


def test_per_case_retains_second_posix_reap_timeout_without_next_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    _config, document = write_corpus(corpus_path, histories=2)
    candidate_path = tmp_path / "candidate.json"
    launches, events = _install_concrete_post_start_posix_failure(
        monkeypatch,
        wait_timeouts=2,
    )

    manifest = run_external_cases(
        valid_adapter_command(tmp_path),
        system="second-reap-timeout-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    )

    assert len(launches) == 1
    assert events == [
        ("killpg", external_runner_module._POSIX_SIGKILL),
        ("wait", 1),
        ("kill", 4_242),
        ("wait", 2),
        ("kill", 4_242),
        ("wait", 3),
    ]
    assert manifest.case_count == 2
    assert len(manifest.case_runs) == 1
    case_run = manifest.case_runs[0]
    assert case_run.case_id == "history-000"
    assert case_run.termination_reason == "process_group_cleanup_failed"
    assert case_run.validation_error == (
        "could not reap the adapter process after process-group cleanup"
    )
    assert not case_run.process_succeeded
    assert not case_run.candidate_valid
    assert case_run.stdout_bytes == len(b"retained stdout")
    assert case_run.stdout_sha256 == hashlib.sha256(
        b"retained stdout"
    ).hexdigest()
    assert case_run.stderr_bytes == len(b"retained stderr")
    assert case_run.stderr_sha256 == hashlib.sha256(
        b"retained stderr"
    ).hexdigest()
    assert manifest.termination_reason == (
        "case_failure:history-000:process_group_cleanup_failed"
    )
    assert manifest.validation_error == case_run.validation_error
    assert not manifest.process_succeeded
    assert not manifest.candidate_valid
    assert not manifest.ready_for_scoring
    assert manifest.stdout_bytes == len(b"retained stdout")
    assert manifest.stderr_bytes == len(b"retained stderr")
    assert not candidate_path.exists()

    manifest_path = tmp_path / "second-reap-timeout-manifest.json"
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    reference = load_external_run_manifest(
        manifest_path,
        expected_dataset_sha256=document["dataset_sha256"],
    )
    assert reference.failure_reason == (
        "case_failure:history-000:process_group_cleanup_failed"
    )


def test_stream_snapshot_fixes_one_bounded_descriptor_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_reads: list[int] = []
    fstat_descriptors: list[int] = []

    class GrowingReader:
        def fileno(self) -> int:
            return 4_242

        def seek(self, offset: int) -> int:
            assert offset == 0
            return 0

        def read(self, requested_bytes: int) -> bytes:
            requested_reads.append(requested_bytes)
            return b"x" * requested_bytes

    class LargeStat:
        st_size = 10_000_000

    def fstat(descriptor: int) -> LargeStat:
        fstat_descriptors.append(descriptor)
        return LargeStat()

    monkeypatch.setattr(external_runner_module.os, "fstat", fstat)

    observed_bytes, byte_count, digest = (
        external_runner_module._bounded_stream_snapshot(
            GrowingReader(),  # type: ignore[arg-type]
            max_bytes=7,
            label="stdout",
        )
    )

    assert fstat_descriptors == [4_242]
    assert requested_reads == [8]
    assert observed_bytes == 10_000_000
    assert byte_count == 8
    assert digest == hashlib.sha256(b"x" * 8).hexdigest()


def test_stream_snapshot_retains_observed_size_after_short_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads = iter((b"x", b""))

    class TruncatedReader:
        def fileno(self) -> int:
            return 4_242

        def seek(self, offset: int) -> int:
            assert offset == 0
            return 0

        def read(self, _requested_bytes: int) -> bytes:
            return next(reads)

    class InitiallyLargeStat:
        st_size = 10_000_000

    monkeypatch.setattr(
        external_runner_module.os,
        "fstat",
        lambda _descriptor: InitiallyLargeStat(),
    )

    observed_bytes, byte_count, digest = (
        external_runner_module._bounded_stream_snapshot(
            TruncatedReader(),  # type: ignore[arg-type]
            max_bytes=7,
            label="stdout",
        )
    )

    assert observed_bytes == 10_000_000
    assert byte_count == 1
    assert digest == hashlib.sha256(b"x").hexdigest()


def test_observed_stream_limit_survives_a_short_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    empty_digest = hashlib.sha256(b"").hexdigest()
    short_digest = hashlib.sha256(b"x").hexdigest()

    def short_snapshot(
        _stream: object,
        *,
        max_bytes: int,
        label: str,
    ) -> tuple[int, int, str]:
        if label == "stdout":
            return max_bytes + 1, 1, short_digest
        return 0, 0, empty_digest

    monkeypatch.setattr(
        external_runner_module,
        "_bounded_stream_snapshot",
        short_snapshot,
    )

    manifest = run_external_command(
        valid_adapter_command(tmp_path),
        system="short-stream-snapshot-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5, max_stdout_bytes=7),
    )

    assert manifest.termination_reason == "stdout_limit"
    assert manifest.stdout_bytes == 1
    assert manifest.stdout_sha256 == short_digest
    assert not manifest.process_succeeded
    assert not manifest.candidate_valid
    assert not manifest.ready_for_scoring


def test_runner_never_stats_stream_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    original_size = external_runner_module._size

    def reject_stream_path(path: Path) -> int:
        if path.name in {"stdout.bin", "stderr.bin"}:
            raise AssertionError("stream limits must inspect retained handles")
        return original_size(path)

    monkeypatch.setattr(
        external_runner_module,
        "_size",
        reject_stream_path,
    )

    manifest = run_external_command(
        valid_adapter_command(tmp_path),
        system="stream-handle-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    )

    assert manifest.ready_for_scoring
    assert manifest.stdout_bytes == 0
    assert manifest.stderr_bytes == 0


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows does not permit replacing these open output paths",
)
def test_stream_evidence_uses_retained_descriptors_after_path_replacement(
    tmp_path: Path,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    original_stdout = b"original stdout descriptor bytes"
    original_stderr = b"original stderr descriptor bytes"
    fabricated_stdout = b"fabricated stdout path bytes"
    fabricated_stderr = b"fabricated stderr path bytes"
    adapter_directory = tmp_path / "replacing-adapter-source"
    adapter_directory.mkdir()
    entrypoint = adapter_directory / "replace-stream-paths.py"
    entrypoint.write_text(
        "\n".join(
            (
                "import json",
                "import os",
                "import sys",
                "from pathlib import Path",
                "corpus = json.load(open(sys.argv[1], encoding='utf-8'))",
                "candidate = Path(sys.argv[2])",
                "run_directory = next(",
                "    candidate.parent.glob('.lrcbench-run-*')",
                ")",
                "stdout_path = run_directory / 'stdout.bin'",
                "stderr_path = run_directory / 'stderr.bin'",
                f"os.write(1, {original_stdout!r})",
                f"os.write(2, {original_stderr!r})",
                "os.fsync(1)",
                "os.fsync(2)",
                "stdout_path.unlink()",
                "stderr_path.unlink()",
                f"stdout_path.write_bytes({fabricated_stdout!r})",
                f"stderr_path.write_bytes({fabricated_stderr!r})",
                "payload = {",
                "    'schema': 'lrcbench-candidate-output-0.1',",
                "    'dataset_sha256': corpus['dataset_sha256'],",
                "    'system': sys.argv[3],",
                "    'cases': [",
                "        {",
                "            'case_id': case['case_id'],",
                "            'rendered_text': '',",
                "            'claims': [],",
                "        }",
                "        for case in corpus['cases']",
                "    ],",
                "}",
                "json.dump(payload, candidate.open('w', encoding='utf-8'))",
            )
        ),
        encoding="utf-8",
    )

    manifest = run_external_command(
        [
            sys.executable,
            str(entrypoint),
            "{corpus}",
            "{candidate}",
            "{system}",
        ],
        system="stream-descriptor-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    )

    assert manifest.ready_for_scoring
    assert manifest.stdout_bytes == len(original_stdout)
    assert manifest.stdout_sha256 == hashlib.sha256(
        original_stdout
    ).hexdigest()
    assert manifest.stderr_bytes == len(original_stderr)
    assert manifest.stderr_sha256 == hashlib.sha256(
        original_stderr
    ).hexdigest()
    assert manifest.stdout_sha256 != hashlib.sha256(
        fabricated_stdout
    ).hexdigest()
    assert manifest.stderr_sha256 != hashlib.sha256(
        fabricated_stderr
    ).hexdigest()


def test_popen_start_failure_remains_an_external_runner_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)

    def reject_start(*_args: object, **_kwargs: object) -> object:
        raise OSError("injected pre-start failure")

    monkeypatch.setattr(
        external_runner_module.subprocess,
        "Popen",
        reject_start,
    )
    monkeypatch.setattr(
        external_runner_module,
        "_uses_windows_process_control",
        lambda: False,
    )

    with pytest.raises(
        ExternalRunnerError,
        match="could not start adapter process: OSError",
    ):
        run_external_command(
            valid_adapter_command(tmp_path),
            system="start-failure-fixture",
            corpus_path=corpus_path,
            candidate_path=tmp_path / "candidate.json",
            limits=RunnerLimits(timeout_seconds=5),
        )


def test_runner_removes_descendants_after_successful_adapter_exit(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    marker_path = tmp_path / "orphan-marker.txt"
    child_program = (
        "from pathlib import Path;import sys,time;"
        "time.sleep(0.4);"
        "Path(sys.argv[1]).write_text('orphan',encoding='utf-8')"
    )
    adapter_program = (
        "import json,subprocess,sys;"
        f"child_program={child_program!r};"
        "subprocess.Popen([sys.executable,'-c',child_program,sys.argv[4]]);"
        "corpus=json.load(open(sys.argv[1],encoding='utf-8'));"
        "payload={'schema':'lrcbench-candidate-output-0.1',"
        "'dataset_sha256':corpus['dataset_sha256'],'system':sys.argv[3],"
        "'cases':[{'case_id':case['case_id'],'rendered_text':'','claims':[]}"
        " for case in corpus['cases']]};"
        "json.dump(payload,open(sys.argv[2],'w',encoding='utf-8'))"
    )

    manifest = run_external_command(
        [
            sys.executable,
            "-c",
            adapter_program,
            "{corpus}",
            "{candidate}",
            "{system}",
            str(marker_path),
        ],
        system="descendant-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    )
    time.sleep(0.6)

    assert manifest.ready_for_scoring
    assert not marker_path.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing violation regression")
def test_windows_temp_cleanup_retries_sharing_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "runner-temp"
    directory.mkdir()
    (directory / "stderr.bin").write_bytes(b"fixture")
    real_rmtree = external_runner_module.shutil.rmtree
    calls = 0

    def fail_once(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            error = PermissionError(13, "injected sharing violation")
            error.winerror = 32
            raise error
        real_rmtree(path)

    monkeypatch.setattr(external_runner_module.shutil, "rmtree", fail_once)

    external_runner_module._remove_runner_directory(directory)

    assert calls == 2
    assert not directory.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object regression")
def test_windows_job_memory_limit_allows_bounded_adapter(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"

    manifest = run_external_command(
        valid_adapter_command(tmp_path),
        system="bounded-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=5,
            max_memory_mb=256,
        ),
    )

    assert manifest.memory_limit_enforced
    assert manifest.ready_for_scoring


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object regression")
def test_windows_job_memory_limit_blocks_descendant_allocation(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    program = (
        "import subprocess,sys;"
        "result=subprocess.run([sys.executable,'-c',"
        "'data=bytearray(256*1024*1024)']);"
        "raise SystemExit(result.returncode)"
    )

    manifest = run_external_command(
        [sys.executable, "-c", program],
        system="memory-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5, max_memory_mb=64),
    )

    assert manifest.memory_limit_enforced
    assert manifest.exit_code != 0
    assert manifest.termination_reason == "nonzero_exit"
    assert not manifest.ready_for_scoring
    assert not candidate_path.exists()


def test_failed_exact_contract_manifest_becomes_a_registered_invalid_nonwin(
    tmp_path,
) -> None:
    corpus_path = tmp_path / "corpus.json"
    config, document = write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "manifest.json"
    source_root = tmp_path / "failing-source"
    source_root.mkdir()
    entrypoint_path = source_root / "failing-adapter.py"
    entrypoint_path.write_text("raise SystemExit(7)", encoding="utf-8")
    manifest = run_external_cases(
        [sys.executable, str(entrypoint_path)],
        system="timeout-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(
            timeout_seconds=300,
            max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        ),
        identity=claim_identity(),
        dependency_lock=retained_dependency_lock(tmp_path),
        adapter_entrypoint=capture_adapter_entrypoint_evidence(
            entrypoint_path
        ),
        adapter_source=capture_adapter_source_evidence(source_root),
        network_isolation=retained_network_isolation(tmp_path),
        inference_service=retained_inference_service(),
    )
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    systems = (
        "timeout-fixture",
        "timeout-fixture-b",
        "timeout-fixture-c",
        "timeout-fixture-d",
    )
    protocol_path = write_frozen_external_protocol(
        tmp_path,
        systems,
        adapter_revisions={
            "timeout-fixture": FIXTURE_ADAPTER_REVISION,
        },
        environment_ids={
            "timeout-fixture": FIXTURE_ENVIRONMENT_ID,
        },
        adapter_entrypoint_sha256s={
            "timeout-fixture": (
                manifest.adapter_entrypoint.entrypoint_sha256
            ),
        },
        adapter_source_tree_sha256s={
            "timeout-fixture": manifest.adapter_source.tree_sha256,
        },
        adapter_runtime_executable_sha256s={
            "timeout-fixture": (
                manifest.adapter_runtime.executable_sha256
            ),
        },
        adapter_environment_sha256s={
            "timeout-fixture": (
                manifest.process_environment.environment_sha256
            ),
        },
        adapter_command_sha256s={
            "timeout-fixture": manifest.command_sha256,
        },
        synthetic_dataset_sha256=document["dataset_sha256"],
        max_memory_mb=_FUNCTIONAL_ADAPTER_MEMORY_MB,
        inference_service_executable_sha256=(
            manifest.inference_service.executable_sha256
        ),
    )

    report = run_benchmark(
        config,
        external_manifest_paths=(manifest_path,),
        expected_external_systems=systems,
        external_protocol_path=protocol_path,
    )

    comparison = next(
        value for value in report.certificate.comparisons if value.system == "timeout-fixture"
    )
    assert comparison.decision == "invalid"
    assert any(reason.endswith("nonzero_exit") for reason in comparison.reasons)
    assert report.certificate.external_manifests[
        0
    ].failure_reason.endswith("nonzero_exit")


def test_runner_enforces_final_stdout_limit_even_for_fast_process(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"

    manifest = run_external_command(
        [sys.executable, "-c", "print('x' * 10000)"],
        system="output-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(max_stdout_bytes=128, timeout_seconds=5),
    )

    assert manifest.stdout_bytes > 128
    assert manifest.termination_reason == "stdout_limit"
    assert not manifest.ready_for_scoring


def test_runner_rejects_invalid_candidate_after_successful_process(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    program = (
        "from pathlib import Path; import sys;Path(sys.argv[1]).write_text('{}',encoding='utf-8')"
    )

    manifest = run_external_command(
        [sys.executable, "-c", program, "{candidate}"],
        system="invalid-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    )

    assert manifest.process_succeeded
    assert not manifest.candidate_valid
    assert not manifest.ready_for_scoring
    assert manifest.termination_reason is None
    assert "missing fields" in manifest.validation_error


def test_runner_detects_corpus_modification_during_adapter_execution(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    program = (
        "from pathlib import Path; import sys;"
        "path=Path(sys.argv[1]);"
        "path.write_text(path.read_text(encoding='utf-8')+' ',encoding='utf-8')"
    )

    manifest = run_external_command(
        [sys.executable, "-c", program, "{corpus}"],
        system="mutation-fixture",
        corpus_path=corpus_path,
        candidate_path=candidate_path,
        limits=RunnerLimits(timeout_seconds=5),
    )

    assert manifest.termination_reason == "corpus_modified"
    assert not manifest.process_succeeded
    assert not manifest.ready_for_scoring


def test_runner_refuses_to_overwrite_candidate_output(tmp_path) -> None:
    corpus_path = tmp_path / "corpus.json"
    write_corpus(corpus_path)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text("owned by caller", encoding="utf-8")

    with pytest.raises(ExternalRunnerError, match="refusing to overwrite"):
        run_external_command(
            valid_adapter_command(tmp_path),
            system="fixture-adapter",
            corpus_path=corpus_path,
            candidate_path=candidate_path,
        )
