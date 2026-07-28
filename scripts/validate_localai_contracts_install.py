"""Run isolated provider-only and exact-contracts-wheel install lanes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

EXPECTED_CONTRACTS_SHA256 = (
    "3f1cbc1c1079a552304541caa6b7bfbaae926494b67956e3107767ffc980ee41"
)
EXPECTED_CONTRACTS_FILENAME = "localai_contracts-0.2.0a1-py3-none-any.whl"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _venv_python(root: Path) -> Path:
    return (
        root / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else root / "bin" / "python"
    )


def _venv_connector(root: Path) -> Path:
    return (
        root / "Scripts" / "ctxc-localai-contracts.exe"
        if sys.platform == "win32"
        else root / "bin" / "ctxc-localai-contracts"
    )


def _run(
    command: list[str],
    *,
    input_bytes: bytes | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        command,
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed with exit {completed.returncode}: "
            f"{completed.stderr.decode('utf-8', errors='replace')}"
        )
    return completed


def _create_environment(root: Path, wheels: list[Path]) -> Path:
    venv.EnvBuilder(with_pip=True, clear=False).create(root)
    python = _venv_python(root)
    _run(
        [
            str(python),
            "-I",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            *[str(path) for path in wheels],
        ]
    )
    return python


def _provider_only_lane(root: Path, provider_wheel: Path) -> None:
    python = _create_environment(root, [provider_wheel])
    script = r"""
import importlib.util
from context_compiler import ContextCompiler, SourceRecord
from context_compiler.localai_contracts_adapter import (
    LocalAIContractsAdapter,
    LocalAIContractsUnavailableError,
)

assert importlib.util.find_spec("localai_contracts") is None
source = SourceRecord.create(
    id="standalone",
    sequence=0,
    role="user",
    content="constraint: Keep standalone behavior stable.",
)
assert ContextCompiler().compile([source]).verification.passed
try:
    LocalAIContractsAdapter()
except LocalAIContractsUnavailableError:
    pass
else:
    raise AssertionError("optional adapter did not fail closed without its wheel")
"""
    _run([str(python), "-I", "-c", script])
    connector = _venv_connector(root)
    completed = subprocess.run(
        [str(connector)],
        input=b"",
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 2:
        raise RuntimeError("provider-only optional entry point did not exit 2")
    if completed.stdout != b"":
        raise RuntimeError("provider-only optional entry point wrote protocol output")
    expected_stderr = (
        "localai-contracts 0.2.0a1 is required for this optional connector"
        + os.linesep
    ).encode("utf-8")
    if completed.stderr != expected_stderr:
        raise RuntimeError("provider-only optional entry point changed its error")


def _provider_and_contracts_lane(
    root: Path,
    provider_wheel: Path,
    contracts_wheel: Path,
) -> tuple[list[str], dict[str, object]]:
    python = _create_environment(root, [provider_wheel, contracts_wheel])
    script = r"""
import hashlib
import json
import subprocess
import sys

import localai_contracts as contracts
from context_compiler.localai_contracts_adapter import (
    CONTEXT_COMPILE_OPERATION,
    LocalAIContractsAdapter,
)

adapter = LocalAIContractsAdapter()
manifest = adapter.get_manifest()
handshake = contracts.ConnectorRequest(
    protocol_version="1.0.0",
    request_id="clean-handshake",
    operation="connector.handshake",
    payload={
        "manifest": manifest.to_dict(),
        "required_operations": [CONTEXT_COMPILE_OPERATION],
        "allow_predicted": False,
        "targets": {},
    },
    expected_response_schema=None,
)
content = "constraint: Keep the database stable."
event = contracts.SourceEvent(
    source_event_id="clean-event",
    sequence=0,
    role="user",
    trust="trusted",
    content=content,
    metadata={},
    content_hash={
        "algorithm": "sha256",
        "value": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    },
)
event.validate()
request = contracts.ConnectorRequest(
    protocol_version="1.0.0",
    request_id="clean-compile",
    operation=CONTEXT_COMPILE_OPERATION,
    payload={"source_events": [event.to_dict()]},
    expected_response_schema={"name": "ContextBundle", "version": "1.0.0"},
)
completed = subprocess.run(
    [
        sys.executable,
        "-m",
        "context_compiler.localai_contracts_connector",
    ],
    input=handshake.canonical_bytes() + b"\n" + request.canonical_bytes() + b"\n",
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    timeout=30,
    check=False,
)
assert completed.returncode == 0, completed.stderr
assert completed.stderr == b""
lines = completed.stdout.splitlines()
assert len(lines) == 2
handshake_response = contracts.ConnectorResponse.from_json(lines[0])
compile_response = contracts.ConnectorResponse.from_json(lines[1])
assert handshake_response.ok
assert compile_response.ok
assert compile_response.payload is not None
assert "context_bundle" not in compile_response.payload
serialized = contracts.ContextBundle.from_dict(compile_response.payload)
direct = contracts.ContextBundle.from_dict(
    adapter.handle(
        CONTEXT_COMPILE_OPERATION,
        {"source_events": [event.to_dict()]},
    )
)
assert serialized.canonical_digest() == direct.canonical_digest()
probe = adapter.build_phase0_probe([event])
report = contracts.assert_phase0_conformant(
    LocalAIContractsAdapter,
    operation_probe=probe,
    subject_name="loss-resistant-context-compiler",
).to_dict()
assert report["passed_count"] == 22
assert report["failed_count"] == 0
assert report["inference_status"] == "not_run"
assert report["observation_scope"] == "connector_transport_conformance"
encoded_report = json.dumps(
    report,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
sys.stdout.buffer.write(encoded_report + b"\n")
"""
    completed = _run([str(python), "-I", "-c", script])
    try:
        report = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "clean-installed Phase 0 report was not canonical JSON"
        ) from exc
    canonical_report = (
        json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if completed.stdout != canonical_report:
        raise RuntimeError("clean-installed Phase 0 report was not canonical JSON")
    if (
        not isinstance(report, dict)
        or report.get("passed_count") != 22
        or report.get("failed_count") != 0
        or report.get("inference_status") != "not_run"
        or report.get("observation_scope")
        != "connector_transport_conformance"
    ):
        raise RuntimeError("clean-installed Phase 0 conformance did not pass")
    return (
        [
            str(python),
            "-m",
            "context_compiler.localai_contracts_connector",
        ],
        report,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-wheel", required=True, type=Path)
    parser.add_argument("--contracts-wheel", required=True, type=Path)
    arguments = parser.parse_args(argv)
    provider_wheel = arguments.provider_wheel.resolve(strict=True)
    contracts_wheel = arguments.contracts_wheel.resolve(strict=True)
    if contracts_wheel.name != EXPECTED_CONTRACTS_FILENAME:
        raise SystemExit("unexpected localai-contracts wheel filename")
    contracts_sha256 = _sha256(contracts_wheel)
    if contracts_sha256 != EXPECTED_CONTRACTS_SHA256:
        raise SystemExit("localai-contracts wheel SHA-256 mismatch")

    with tempfile.TemporaryDirectory(prefix="ctxc-localai-contracts-install-") as raw:
        temporary = Path(raw)
        _provider_only_lane(temporary / "provider-only", provider_wheel)
        connector_argv, phase0_report = _provider_and_contracts_lane(
            temporary / "provider-and-contracts",
            provider_wheel,
            contracts_wheel,
        )

    result = {
        "schema": "ctxc-localai-contracts-install-smoke-0.1",
        "provider_wheel_sha256": _sha256(provider_wheel),
        "contracts_wheel_sha256": contracts_sha256,
        "provider_only": "passed",
        "phase0_passed_count": phase0_report["passed_count"],
        "phase0_failed_count": phase0_report["failed_count"],
        "phase0_observation_scope": phase0_report["observation_scope"],
        "provider_and_exact_contracts": "passed",
        "subprocess_ndjson_roundtrip": "passed",
        "inference_status": "not_run",
        "connector_argv": connector_argv,
    }
    sys.stdout.write(
        json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
