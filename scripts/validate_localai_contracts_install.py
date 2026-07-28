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
    "36a02dbc4267402949dddda1da180d800590cc579e0c1ecb022fc96f6a7c29ae"
)
EXPECTED_CONTRACTS_FILENAME = "localai_contracts-0.2.0a2-py3-none-any.whl"


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


def _create_empty_environment(root: Path) -> Path:
    venv.EnvBuilder(with_pip=True, clear=False).create(root)
    return _venv_python(root)


def _create_environment(root: Path, wheels: list[Path]) -> Path:
    python = _create_empty_environment(root)
    _run(
        [
            str(python),
            "-I",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--no-compile",
            *[str(path) for path in wheels],
        ]
    )
    return python


def _transitive_without_archive_provenance_lane(
    root: Path,
    provider_wheel: Path,
    contracts_wheel: Path,
) -> None:
    python = _create_empty_environment(root)
    _run(
        [
            str(python),
            "-I",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-compile",
            "--find-links",
            str(contracts_wheel.parent),
            f"{provider_wheel}[unified]",
        ]
    )
    script = r"""
import importlib.metadata
from pathlib import Path
import sys

from context_compiler.localai_contracts_adapter import (
    LocalAIContractsAdapter,
    LocalAIContractsUnavailableError,
)

distribution = importlib.metadata.distribution("localai-contracts")
site_root = Path(distribution.locate_file(""))
dist_info = site_root / "localai_contracts-0.2.0a2.dist-info"
assert not (dist_info / "direct_url.json").exists()
assert not (dist_info / "REQUESTED").exists()
try:
    LocalAIContractsAdapter()
except LocalAIContractsUnavailableError as exc:
    assert str(exc) == (
        "localai-contracts 0.2.0a2 failed optional-adapter validation"
    )
else:
    raise AssertionError("transitive install without archive provenance was accepted")
assert "localai_contracts" not in sys.modules
"""
    _run([str(python), "-I", "-B", "-c", script])


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
    _run([str(python), "-I", "-B", "-c", script])
    connector = _venv_connector(root)
    connector_environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    completed = subprocess.run(
        [str(connector)],
        input=b"",
        env=connector_environment,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 2:
        raise RuntimeError("provider-only optional entry point did not exit 2")
    if completed.stdout != b"":
        raise RuntimeError("provider-only optional entry point wrote protocol output")
    expected_stderr = (
        "localai-contracts 0.2.0a2 is unavailable or failed optional-adapter validation"
        + os.linesep
    ).encode("utf-8")
    if completed.stderr != expected_stderr:
        raise RuntimeError("provider-only optional entry point changed its error")


def _tampered_record_lane(root: Path, python: Path) -> None:
    mutate = r"""
import importlib.metadata
from pathlib import Path

distribution = importlib.metadata.distribution("localai-contracts")
record = Path(distribution.locate_file(
    "localai_contracts-0.2.0a2.dist-info/RECORD"
))
original = (
    "localai_contracts/__init__.py,"
    "sha256=ndvRkTKPdPZZ3kzNT2VBznQugtKHy19hdcAD5kUBYKk,6474"
)
replacement = (
    "localai_contracts/__init__.py,"
    "sha256=AdvRkTKPdPZZ3kzNT2VBznQugtKHy19hdcAD5kUBYKk,6474"
)
contents = record.read_text(encoding="ascii")
assert contents.count(original) == 1
record.write_text(
    contents.replace(original, replacement),
    encoding="ascii",
    newline="",
)
"""
    _run([str(python), "-I", "-B", "-c", mutate])
    marker = root / "tampered-record-import-attempted"
    reject = r"""
from pathlib import Path
import sys

from context_compiler import localai_contracts_adapter as adapter

real_import_module = adapter.importlib.import_module
def forbidden_import(name):
    Path(sys.argv[1]).write_text("import-attempted", encoding="utf-8")
    return real_import_module(name)
adapter.importlib.import_module = forbidden_import
try:
    adapter.LocalAIContractsAdapter()
except adapter.LocalAIContractsUnavailableError as exc:
    assert str(exc) == (
        "localai-contracts 0.2.0a2 failed optional-adapter validation"
    )
else:
    raise AssertionError("tampered installed RECORD was accepted")
assert "localai_contracts" not in sys.modules
"""
    _run([str(python), "-I", "-B", "-c", reject, str(marker)])
    if marker.exists():
        raise RuntimeError("tampered installed RECORD reached package import")


def _provider_and_contracts_lane(
    root: Path,
    provider_wheel: Path,
    contracts_wheel: Path,
) -> tuple[list[str], dict[str, object]]:
    python = _create_environment(root, [provider_wheel, contracts_wheel])
    script = r"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import context_compiler
import localai_contracts as contracts
from context_compiler.localai_contracts_adapter import (
    CONTEXT_COMPILE_OPERATION,
    LocalAIContractsAdapter,
)

adapter = LocalAIContractsAdapter()
limits = contracts.DEFAULT_PARSE_LIMITS
assert adapter.limits is limits
package_roots = (
    Path(context_compiler.__file__).parent,
    Path(contracts.__file__).parent,
)
def assert_no_package_bytecode():
    assert not any(path for root in package_roots for path in root.rglob("*.pyc"))

assert_no_package_bytecode()
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
wire_input = (
    contracts.bounded_canonical_bytes(handshake.to_dict(), limits=limits)
    + b"\n"
    + contracts.bounded_canonical_bytes(request.to_dict(), limits=limits)
    + b"\n"
)
connector_environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
completed = subprocess.run(
    [
        sys.executable,
        "-m",
        "context_compiler.localai_contracts_connector",
    ],
    input=wire_input,
    env=connector_environment,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    timeout=30,
    check=False,
)
assert completed.returncode == 0, completed.stderr
assert completed.stderr == b""
assert_no_package_bytecode()
lines = completed.stdout.splitlines()
assert len(lines) == 2
handshake_response = contracts.ConnectorResponse.from_json(lines[0], limits=limits)
compile_response = contracts.ConnectorResponse.from_json(lines[1], limits=limits)
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
    completed = _run([str(python), "-I", "-B", "-c", script])
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
    _tampered_record_lane(root, python)
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
        _transitive_without_archive_provenance_lane(
            temporary / "transitive-without-archive-provenance",
            provider_wheel,
            contracts_wheel,
        )
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
        "transitive_without_archive_provenance": "rejected-as-expected",
        "tampered_record_hash_field": "rejected-before-import",
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
