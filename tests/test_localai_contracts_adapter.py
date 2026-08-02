from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import marshal
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import context_compiler.compiler as compiler_module
import context_compiler.localai_contracts_adapter as adapter_module
from conformance.run_connector_conformance import (
    _read_bounded_text,
    _strict_json_loads,
)
from conformance.schema_validation import SchemaValidationError, validate_instance
from context_compiler import CompilationPolicy, ExactTokenCounterAdapter, LocalAIConnector
from context_compiler.localai_contracts_adapter import (
    CONTEXT_COMPILE_OPERATION,
    LOCALAI_CONTRACTS_PROTOCOL_VERSION,
    LOCALAI_CONTRACTS_SOURCE_COMMIT,
    LOCALAI_CONTRACTS_VERSION,
    LOCALAI_CONTRACTS_WHEEL_SHA256,
    LOCALAI_CONVERSION_AUDIT_SCHEMA,
    AuthenticatedAuthority,
    LocalAIContractsAdapter,
)

contracts = pytest.importorskip(
    "localai_contracts",
    reason="exact optional localai-contracts wheel is not installed",
)

ROOT = Path(__file__).resolve().parents[1]
CONVERSION_GOLDEN = (
    ROOT
    / "conformance"
    / "fixtures"
    / "golden-localai-contract-conversion-v1.json"
)
CONTRACTS_INSTALL_ROOT = Path(contracts.__file__).resolve().parent.parent
VALIDATION_ERROR = (
    "localai-contracts 0.2.0a2 failed optional-adapter validation"
)
CONTRACTS_DIST_INFO = "localai_contracts-0.2.0a2.dist-info"
EXPECTED_LAUNCHER_BODY = (
    b"# -*- coding: utf-8 -*-\n"
    b"import re\n"
    b"import sys\n"
    b"from localai_contracts.integration import main\n"
    b"if __name__ == '__main__':\n"
    b"    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
    b"    sys.exit(main())\n"
)
REMOVESUFFIX_LAUNCHER_BODY = (
    b"import sys\n"
    b"from localai_contracts.integration import main\n"
    b"if __name__ == '__main__':\n"
    b"    sys.argv[0] = sys.argv[0].removesuffix('.exe')\n"
    b"    sys.exit(main())\n"
)


def _record_path(install_root: Path) -> Path:
    return install_root / CONTRACTS_DIST_INFO / "RECORD"


def _record_lines(install_root: Path) -> list[str]:
    return _record_path(install_root).read_text(encoding="ascii").splitlines()


def _write_record_lines(install_root: Path, lines: list[str]) -> None:
    _record_path(install_root).write_text(
        "\n".join(lines) + "\n",
        encoding="ascii",
        newline="\n",
    )


def _replace_record_row(
    install_root: Path,
    path: str,
    replacement: str | None,
) -> None:
    lines = _record_lines(install_root)
    matches = [index for index, line in enumerate(lines) if line.split(",", 1)[0] == path]
    assert len(matches) == 1
    index = matches[0]
    if replacement is None:
        del lines[index]
    else:
        lines[index] = replacement
    _write_record_lines(install_root, lines)


def _record_digest(contents: bytes) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(contents).digest())
        .rstrip(b"=")
        .decode("ascii")
    )


def _run_isolated(script: str, *arguments: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            script,
            *(str(argument) for argument in arguments),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def _copy_contracts_install(destination: Path) -> None:
    shutil.copytree(
        CONTRACTS_INSTALL_ROOT / "localai_contracts",
        destination / "localai_contracts",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    dist_infos = tuple(
        CONTRACTS_INSTALL_ROOT.glob("localai_contracts-0.2.0a2.dist-info")
    )
    assert len(dist_infos) == 1
    shutil.copytree(dist_infos[0], destination / dist_infos[0].name)
    launcher_name = "localai-integration.exe" if sys.platform == "win32" else "localai-integration"
    launcher = Path(sys.executable).parent / launcher_name
    canonical_launcher_row = os.path.relpath(launcher, destination).replace(os.sep, "/")
    lines = _record_lines(destination)
    launcher_matches = [
        index
        for index, line in enumerate(lines)
        if line.split(",", 1)[0].replace("\\", "/").rsplit("/", 1)[-1]
        == launcher_name
    ]
    assert len(launcher_matches) == 1
    index = launcher_matches[0]
    _old_path, hash_field, size_field = lines[index].split(",")
    lines[index] = f"{canonical_launcher_row},{hash_field},{size_field}"
    _write_record_lines(destination, lines)


def _assert_install_rejected_before_marker(
    install_root: Path,
    marker: Path,
) -> None:
    script = f"""
from pathlib import Path
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import context_compiler.localai_contracts_adapter as adapter
real_import_module = adapter.importlib.import_module
def forbidden_import(name):
    Path(sys.argv[3]).write_text("import-attempted", encoding="utf-8")
    return real_import_module(name)
adapter.importlib.import_module = forbidden_import
try:
    adapter.LocalAIContractsAdapter()
except adapter.LocalAIContractsUnavailableError as exc:
    assert str(exc) == {VALIDATION_ERROR!r}
else:
    raise AssertionError("invalid contracts install was accepted")
assert "localai_contracts" not in sys.modules
"""
    completed = _run_isolated(
        script,
        ROOT / "src",
        install_root,
        marker,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not marker.exists()


def _assert_install_accepted(install_root: Path) -> None:
    script = """
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from context_compiler.localai_contracts_adapter import LocalAIContractsAdapter
manifest = LocalAIContractsAdapter().get_manifest()
assert manifest.supported_operations == ["context.compile"]
"""
    completed = _run_isolated(script, ROOT / "src", install_root)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_posix_launcher_record_path_is_platform_canonical(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = tmp_path / "ctxc-venv"
    monkeypatch.setattr(adapter_module.sys, "platform", "linux")
    monkeypatch.setattr(
        adapter_module.sys,
        "executable",
        str(environment / "bin" / "python"),
    )
    site_root = environment / "lib" / "python3.13" / "site-packages"

    assert adapter_module._launcher_record_paths(site_root) == {
        "../../../bin/localai-integration": environment / "bin" / "localai-integration"
    }


@pytest.mark.parametrize(
    "launcher_body",
    [EXPECTED_LAUNCHER_BODY, REMOVESUFFIX_LAUNCHER_BODY],
)
def test_posix_safe_shebang_launcher_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    launcher_body: bytes,
) -> None:
    executable = tmp_path / "ctxc venv" / "bin" / "python"
    encoded = os.fsencode(str(executable))
    monkeypatch.setattr(adapter_module.sys, "platform", "linux")
    monkeypatch.setattr(adapter_module.sys, "executable", str(executable))
    launcher = (
        b"#!/bin/sh\n'''exec' \""
        + encoded
        + b'" "$0" "$@"\n'
        + b"' '''\n"
        + launcher_body
    )

    adapter_module._validate_launcher_contents(launcher)


def test_pip_launcher_body_variants_remain_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module.sys, "platform", "linux")
    monkeypatch.setattr(adapter_module.sys, "executable", "/opt/ctxc/bin/python")

    for body in (EXPECTED_LAUNCHER_BODY, REMOVESUFFIX_LAUNCHER_BODY):
        adapter_module._validate_launcher_contents(b"#!/opt/ctxc/bin/python\n" + body)

    changed = REMOVESUFFIX_LAUNCHER_BODY.replace(b".exe", b".EXE")
    with pytest.raises(ValueError, match="entry point mismatch"):
        adapter_module._validate_launcher_contents(
            b"#!/opt/ctxc/bin/python\n" + changed
        )


def test_posix_launcher_uses_utf8_for_non_ascii_interpreter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "ctxc-é" / "bin" / "python"
    encoded = str(executable).encode("utf-8")
    monkeypatch.setattr(adapter_module.sys, "platform", "linux")
    monkeypatch.setattr(adapter_module.sys, "executable", str(executable))
    monkeypatch.setattr(
        adapter_module.os,
        "fsencode",
        lambda _value: pytest.fail("distlib launcher encoding used os.fsencode"),
    )

    adapter_module._validate_launcher_contents(
        b"#!" + encoded + b"\n" + EXPECTED_LAUNCHER_BODY
    )


def test_posix_launcher_must_be_owner_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module.sys, "platform", "linux")

    adapter_module._validate_launcher_mode(stat.S_IFREG | stat.S_IRUSR | stat.S_IXUSR)
    with pytest.raises(ValueError, match="owner-executable"):
        adapter_module._validate_launcher_mode(stat.S_IFREG | stat.S_IRUSR)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher regression")
def test_cotampered_windows_launcher_and_record_are_rejected(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "cotampered-launcher"
    _copy_contracts_install(install_root)
    fake_scripts = tmp_path / "fake-venv" / "Scripts"
    fake_scripts.mkdir(parents=True)
    fake_executable = fake_scripts / "python.exe"
    launcher = fake_scripts / "localai-integration.exe"
    real_launcher = Path(sys.executable).parent / "localai-integration.exe"
    malicious = b"MALICIOUS-PREFIX" + real_launcher.read_bytes()
    launcher.write_bytes(malicious)
    old_launcher_row = next(
        line
        for line in _record_lines(install_root)
        if line.split(",", 1)[0].rsplit("/", 1)[-1]
        == "localai-integration.exe"
    )
    old_path = old_launcher_row.split(",", 1)[0]
    new_path = os.path.relpath(launcher, install_root).replace(os.sep, "/")
    _replace_record_row(
        install_root,
        old_path,
        f"{new_path},sha256={_record_digest(malicious)},{len(malicious)}",
    )
    marker = tmp_path / "cotampered-launcher-imported"
    script = f"""
from pathlib import Path
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import context_compiler.localai_contracts_adapter as adapter
adapter.sys.executable = sys.argv[3]
real_import_module = adapter.importlib.import_module
def forbidden_import(name):
    Path(sys.argv[4]).write_text("import-attempted", encoding="utf-8")
    return real_import_module(name)
adapter.importlib.import_module = forbidden_import
try:
    adapter.LocalAIContractsAdapter()
except adapter.LocalAIContractsUnavailableError as exc:
    assert str(exc) == {VALIDATION_ERROR!r}
else:
    raise AssertionError("co-tampered native launcher was accepted")
assert "localai_contracts" not in sys.modules
"""
    completed = _run_isolated(
        script,
        ROOT / "src",
        install_root,
        fake_executable,
        marker,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not marker.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher regression")
def test_windows_launcher_rejects_other_architecture_stubs() -> None:
    launcher = (
        Path(sys.executable).parent / "localai-integration.exe"
    ).read_bytes()
    expected_size, _expected_digest = (
        adapter_module._expected_windows_launcher_stub()
    )
    remainder = launcher[expected_size:]
    distribution = importlib.metadata.distribution("pip")
    candidates = (
        distribution.locate_file("pip/_vendor/distlib/t32.exe"),
        distribution.locate_file("pip/_vendor/distlib/t64.exe"),
        distribution.locate_file("pip/_vendor/distlib/t64-arm.exe"),
    )
    wrong_stubs = [
        Path(candidate).read_bytes()
        for candidate in candidates
        if len(Path(candidate).read_bytes()) != expected_size
    ]
    assert len(wrong_stubs) == 2

    for stub in wrong_stubs:
        with pytest.raises(ValueError, match="launcher is truncated|native stub mismatch"):
            adapter_module._validate_launcher_contents(stub + remainder)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher regression")
@pytest.mark.parametrize(
    ("maximum", "version", "resource"),
    [
        (2**31 - 1, "32 bit (Intel)", "pip/_vendor/distlib/t32.exe"),
        (
            2**63 - 1,
            "64 bit (AMD64) on Windows ARM64",
            "pip/_vendor/distlib/t64.exe",
        ),
        (2**63 - 1, "64 bit (ARM64)", "pip/_vendor/distlib/t64-arm.exe"),
    ],
)
def test_windows_launcher_stub_constants_cover_supported_architectures(
    monkeypatch: pytest.MonkeyPatch,
    maximum: int,
    version: str,
    resource: str,
) -> None:
    distribution = importlib.metadata.distribution("pip")
    stub = Path(distribution.locate_file(resource)).read_bytes()
    monkeypatch.setattr(adapter_module.sys, "maxsize", maximum)
    monkeypatch.setattr(adapter_module.sys, "version", version)

    assert adapter_module._expected_windows_launcher_stub() == (
        len(stub),
        hashlib.sha256(stub).hexdigest(),
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher regression")
def test_windows_launcher_rejects_global_zip_comment() -> None:
    launcher = (
        Path(sys.executable).parent / "localai-integration.exe"
    ).read_bytes()
    archive_offset = launcher.index(b"PK\x03\x04")
    rebuilt = io.BytesIO()
    with zipfile.ZipFile(rebuilt, mode="w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("__main__.py", EXPECTED_LAUNCHER_BODY)
        archive.comment = b"unreviewed"

    with pytest.raises(ValueError, match="archive shape mismatch"):
        adapter_module._validate_launcher_contents(
            launcher[:archive_offset] + rebuilt.getvalue()
        )


def _event(
    content: str = "constraint: Keep the database stable.",
    *,
    source_event_id: str = "event-0",
    sequence: int = 0,
    role: str = "user",
    trust: str = "trusted",
    metadata: dict[str, Any] | None = None,
    digest: str | None = None,
) -> Any:
    value = contracts.SourceEvent(
        source_event_id=source_event_id,
        sequence=sequence,
        role=role,
        trust=trust,
        content=content,
        metadata={} if metadata is None else metadata,
        content_hash={
            "algorithm": "sha256",
            "value": digest or hashlib.sha256(content.encode("utf-8")).hexdigest(),
        },
    )
    if digest is None:
        value.validate()
    return value


def _load_conversion_golden() -> dict[str, Any]:
    text = _read_bounded_text(CONVERSION_GOLDEN, label="conversion golden")
    raw = text.encode("utf-8")
    assert len(raw) == 5_716
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert hashlib.sha256(raw).hexdigest() == (
        "2e337dd2a20b246d827840639bde6d11e29b39ca2861a6fb986ccb16e98f8762"
    )
    fixture = _strict_json_loads(text, label="conversion golden")
    assert fixture["schema"] == "ctxc-localai-conversion-golden-0.1"
    unsigned = {
        key: value for key, value in fixture.items() if key != "golden_sha256"
    }
    assert hashlib.sha256(contracts.canonical_bytes(unsigned)).hexdigest() == fixture[
        "golden_sha256"
    ]
    return fixture


def _golden_authority(event: Any) -> AuthenticatedAuthority | None:
    if event.source_event_id == "golden-user":
        return AuthenticatedAuthority("FIXTURE_AUTHORITY_SENTINEL")
    if event.source_event_id == "golden-tool":
        return AuthenticatedAuthority(
            "FIXTURE_AUTHORITY_SENTINEL",
            trusted_for_state=True,
        )
    return None


def _golden_connector_factory() -> LocalAIConnector:
    return LocalAIConnector(
        policy=CompilationPolicy(token_budget=100_000),
        token_counter=ExactTokenCounterAdapter(
            "golden-utf8-bytes-v1",
            lambda text: len(text.encode("utf-8")),
        ),
    )


def _compile_conversion_golden(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    dict[str, Any],
    LocalAIContractsAdapter,
    list[Any],
    Any,
    dict[str, Any],
]:
    fixture = _load_conversion_golden()
    monkeypatch.setattr(
        compiler_module,
        "utc_now",
        lambda: fixture["fixed_compiled_at"],
    )
    monkeypatch.setattr(
        compiler_module,
        "time",
        type("FixedClock", (), {"perf_counter": staticmethod(lambda: 1.0)}),
    )
    adapter = LocalAIContractsAdapter(
        connector_factory=_golden_connector_factory,
        authority_verifier=_golden_authority,
    )
    events = [
        contracts.SourceEvent.from_dict(document)
        for document in fixture["source_events"]
    ]
    bundle, audit = adapter.compile_with_conversion_audit(events)
    return fixture, adapter, events, bundle, audit


def _recompute_projected_bundle_id(
    adapter: LocalAIContractsAdapter,
    document: dict[str, Any],
) -> None:
    body = {
        key: value
        for key, value in document.items()
        if key not in {"schema_version", "bundle_id"}
    }
    document["bundle_id"] = adapter_module._projected_bundle_id(
        contracts,
        body,
        limits=adapter.limits,
    )


def _rebind_audit_output(
    adapter: LocalAIContractsAdapter,
    audit: dict[str, Any],
    output_bundle: Any,
) -> dict[str, Any]:
    rebound = json.loads(json.dumps(audit))
    output_sha256 = output_bundle.canonical_digest()
    rebound["context_bundle_conversion"]["output_document_sha256"] = (
        output_sha256
    )
    rebound["context_bundle_conversion"]["output_round_trip_sha256"] = (
        output_sha256
    )
    unsigned = {
        key: value for key, value in rebound.items() if key != "audit_sha256"
    }
    rebound["audit_sha256"] = hashlib.sha256(
        contracts.bounded_canonical_bytes(unsigned, limits=adapter.limits)
    ).hexdigest()
    return rebound


def _rehash_conversion_audit(
    adapter: LocalAIContractsAdapter,
    audit: dict[str, Any],
) -> None:
    unsigned = {
        key: value for key, value in audit.items() if key != "audit_sha256"
    }
    audit["audit_sha256"] = hashlib.sha256(
        contracts.bounded_canonical_bytes(unsigned, limits=adapter.limits)
    ).hexdigest()


def _string_leaves(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [
            leaf
            for child in value.values()
            for leaf in _string_leaves(child)
        ]
    if isinstance(value, list):
        return [leaf for child in value for leaf in _string_leaves(child)]
    return [value] if isinstance(value, str) else []


def _handshake_request(
    adapter: LocalAIContractsAdapter,
    *,
    request_id: str = "handshake-0",
) -> Any:
    return contracts.ConnectorRequest(
        protocol_version=contracts.PROTOCOL_VERSION,
        request_id=request_id,
        operation=contracts.HANDSHAKE_OPERATION,
        payload={
            "manifest": adapter.get_manifest().to_dict(),
            "required_operations": [CONTEXT_COMPILE_OPERATION],
            "allow_predicted": False,
            "targets": {},
        },
        expected_response_schema=None,
    )


def _compile_request(
    event: Any,
    *,
    request_id: str = "compile-0",
) -> Any:
    return contracts.ConnectorRequest(
        protocol_version=contracts.PROTOCOL_VERSION,
        request_id=request_id,
        operation=CONTEXT_COMPILE_OPERATION,
        payload={"source_events": [event.to_dict()]},
        expected_response_schema={
            "name": "ContextBundle",
            "version": "1.0.0",
        },
    )


def _error(response: Any) -> Any:
    assert response.error is not None
    return contracts.ErrorEnvelope.from_dict(response.error)


def test_shadow_package_is_rejected_before_its_initializer_executes(
    tmp_path: Path,
) -> None:
    shadow_root = tmp_path / "shadow"
    shadow_package = shadow_root / "localai_contracts"
    shadow_package.mkdir(parents=True)
    marker = tmp_path / "shadow-executed"
    (shadow_package / "__init__.py").write_text(
        """
from pathlib import Path
import sys
Path(sys.argv[-1]).write_text("executed", encoding="utf-8")
__version__ = "0.2.0a2"
PROTOCOL_VERSION = "1.0.0"
""".lstrip(),
        encoding="utf-8",
    )
    script = f"""
import sys
sys.path[:0] = [sys.argv[3], sys.argv[1], sys.argv[2]]
from context_compiler.localai_contracts_adapter import (
    LocalAIContractsAdapter,
    LocalAIContractsUnavailableError,
)
try:
    LocalAIContractsAdapter()
except LocalAIContractsUnavailableError as exc:
    assert str(exc) == {VALIDATION_ERROR!r}
else:
    raise AssertionError("shadow package was accepted")
assert "localai_contracts" not in sys.modules
"""

    completed = _run_isolated(
        script,
        ROOT / "src",
        CONTRACTS_INSTALL_ROOT,
        shadow_root,
        marker,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not marker.exists()


def test_tampered_distribution_tree_is_rejected_before_import(
    tmp_path: Path,
) -> None:
    tampered_root = tmp_path / "tampered"
    _copy_contracts_install(tampered_root)
    marker = tmp_path / "tampered-executed"
    initializer = tampered_root / "localai_contracts" / "__init__.py"
    original = initializer.read_bytes()
    line_ending = b"\r\n" if b"\r\n" in original[:128] else b"\n"
    original_header = (
        b'"""Public API for localai-contracts."""'
        + line_ending
        + line_ending
    )
    executable_expression = b"open(__import__('sys').argv[-1],'w')"
    padding_size = len(original_header) - len(executable_expression) - len(line_ending)
    assert padding_size >= 0
    executable_header = (
        executable_expression + (b" " * padding_size) + line_ending
    )
    assert len(original_header) == len(executable_header)
    assert original.count(original_header) == 1
    tampered = original.replace(original_header, executable_header, 1)
    assert len(tampered) == len(original) == 6_474
    initializer.write_bytes(tampered)

    _assert_install_rejected_before_marker(tampered_root, marker)


def test_same_size_resource_mutation_is_rejected_before_import(
    tmp_path: Path,
) -> None:
    tampered_root = tmp_path / "tampered-resource"
    _copy_contracts_install(tampered_root)
    marker = tmp_path / "tampered-resource-executed"
    fixture = (
        tampered_root
        / "localai_contracts"
        / "fixtures"
        / "phase0-conformance-v1.json"
    )
    contents = bytearray(fixture.read_bytes())
    contents[0] = ord("[") if contents[0] != ord("[") else ord("{")
    fixture.write_bytes(contents)

    _assert_install_rejected_before_marker(tampered_root, marker)


def test_record_hash_field_mutation_is_rejected_before_import(
    tmp_path: Path,
) -> None:
    tampered_root = tmp_path / "tampered-record"
    _copy_contracts_install(tampered_root)
    marker = tmp_path / "tampered-record-imported"
    initializer = tampered_root / "localai_contracts" / "__init__.py"
    package_sha256 = hashlib.sha256(initializer.read_bytes()).hexdigest()
    original = (
        "localai_contracts/__init__.py,"
        "sha256=ndvRkTKPdPZZ3kzNT2VBznQugtKHy19hdcAD5kUBYKk,6474"
    )
    mutated = original.replace("sha256=n", "sha256=A", 1)
    assert original in _record_lines(tampered_root)
    _replace_record_row(
        tampered_root,
        "localai_contracts/__init__.py",
        mutated,
    )

    assert hashlib.sha256(initializer.read_bytes()).hexdigest() == package_sha256
    assert initializer.stat().st_size == 6_474
    _assert_install_rejected_before_marker(tampered_root, marker)


def test_record_is_revalidated_after_package_import(tmp_path: Path) -> None:
    install_root = tmp_path / "post-import-record"
    _copy_contracts_install(install_root)
    script = f"""
from pathlib import Path
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import context_compiler.localai_contracts_adapter as adapter
record = Path(sys.argv[3])
real_import_module = adapter.importlib.import_module
def import_then_tamper(name):
    module = real_import_module(name)
    contents = record.read_bytes()
    old = b"localai_contracts/__init__.py,sha256=ndvR"
    new = b"localai_contracts/__init__.py,sha256=AdvR"
    assert contents.count(old) == 1
    record.write_bytes(contents.replace(old, new, 1))
    return module
adapter.importlib.import_module = import_then_tamper
try:
    adapter.LocalAIContractsAdapter()
except adapter.LocalAIContractsUnavailableError as exc:
    assert str(exc) == {VALIDATION_ERROR!r}
else:
    raise AssertionError("post-import RECORD mutation was accepted")
"""
    completed = _run_isolated(
        script,
        ROOT / "src",
        install_root,
        _record_path(install_root),
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "conflicting-duplicate",
        "malformed-base64",
        "missing-self-row",
        "hashed-self-row",
    ],
)
def test_invalid_immutable_record_inventory_is_rejected(
    tmp_path: Path,
    mutation: str,
) -> None:
    install_root = tmp_path / f"immutable-{mutation}"
    _copy_contracts_install(install_root)
    marker = tmp_path / f"immutable-{mutation}-imported"
    lines = _record_lines(install_root)
    package_path = "localai_contracts/__init__.py"
    package_line = next(
        line for line in lines if line.split(",", 1)[0] == package_path
    )
    self_path = f"{CONTRACTS_DIST_INFO}/RECORD"
    self_line = next(line for line in lines if line.split(",", 1)[0] == self_path)
    if mutation == "missing":
        lines.remove(package_line)
    elif mutation == "duplicate":
        lines.append(package_line)
    elif mutation == "conflicting-duplicate":
        lines.append(package_line.replace("sha256=n", "sha256=A", 1))
    elif mutation == "malformed-base64":
        index = lines.index(package_line)
        lines[index] = package_line.replace("sha256=n", "sha256=!", 1)
    elif mutation == "missing-self-row":
        lines.remove(self_line)
    else:
        index = lines.index(self_line)
        lines[index] = (
            f"{self_path},"
            "sha256=47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU,0"
        )
    _write_record_lines(install_root, lines)

    _assert_install_rejected_before_marker(install_root, marker)


def test_requested_marker_may_be_absent_when_archive_binding_remains(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "requested-absent"
    _copy_contracts_install(install_root)
    requested = f"{CONTRACTS_DIST_INFO}/REQUESTED"
    _replace_record_row(install_root, requested, None)
    (install_root / CONTRACTS_DIST_INFO / "REQUESTED").unlink()

    _assert_install_accepted(install_root)


def test_unrecorded_requested_marker_is_rejected(tmp_path: Path) -> None:
    install_root = tmp_path / "unrecorded-requested"
    _copy_contracts_install(install_root)
    marker = tmp_path / "unrecorded-requested-imported"
    requested = f"{CONTRACTS_DIST_INFO}/REQUESTED"
    _replace_record_row(install_root, requested, None)
    assert (install_root / CONTRACTS_DIST_INFO / "REQUESTED").is_file()

    _assert_install_rejected_before_marker(install_root, marker)


def test_empty_requested_marker_is_accepted_for_direct_install(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "direct-install"
    _copy_contracts_install(install_root)
    requested = install_root / CONTRACTS_DIST_INFO / "REQUESTED"
    assert requested.read_bytes() == b""

    _assert_install_accepted(install_root)


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "nonempty", "partial-hash"],
)
def test_invalid_requested_marker_is_rejected(
    tmp_path: Path,
    mutation: str,
) -> None:
    install_root = tmp_path / f"requested-{mutation}"
    _copy_contracts_install(install_root)
    marker = tmp_path / f"requested-{mutation}-imported"
    requested_path = f"{CONTRACTS_DIST_INFO}/REQUESTED"
    lines = _record_lines(install_root)
    requested_line = next(
        line for line in lines if line.split(",", 1)[0] == requested_path
    )
    if mutation == "duplicate":
        lines.append(requested_line)
        _write_record_lines(install_root, lines)
    elif mutation == "partial-hash":
        _replace_record_row(install_root, requested_path, f"{requested_path},,0")
    else:
        contents = b"x"
        (install_root / CONTRACTS_DIST_INFO / "REQUESTED").write_bytes(contents)
        _replace_record_row(
            install_root,
            requested_path,
            f"{requested_path},sha256={_record_digest(contents)},{len(contents)}",
        )

    _assert_install_rejected_before_marker(install_root, marker)


@pytest.mark.parametrize(
    "mutation",
    [
        "unexpected-extra",
        "launcher-path",
        "launcher-hash",
        "launcher-size",
        "installer-hash",
        "direct-url-size",
        "missing-installer",
        "missing-direct-url",
        "missing-launcher",
    ],
)
def test_invalid_generated_record_row_is_rejected(
    tmp_path: Path,
    mutation: str,
) -> None:
    install_root = tmp_path / f"generated-{mutation}"
    _copy_contracts_install(install_root)
    marker = tmp_path / f"generated-{mutation}-imported"
    lines = _record_lines(install_root)
    installer_path = f"{CONTRACTS_DIST_INFO}/INSTALLER"
    direct_url_path = f"{CONTRACTS_DIST_INFO}/direct_url.json"
    launcher_name = (
        "localai-integration.exe" if sys.platform == "win32" else "localai-integration"
    )
    if mutation == "unexpected-extra":
        lines.append(
            f"{CONTRACTS_DIST_INFO}/UNEXPECTED,"
            "sha256=47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU,0"
        )
    elif mutation == "launcher-path":
        index = next(
            index
            for index, line in enumerate(lines)
            if line.split(",", 1)[0].rsplit("/", 1)[-1] == launcher_name
        )
        _path, hash_field, size_field = lines[index].split(",")
        lines[index] = f"../alternate/{launcher_name},{hash_field},{size_field}"
    elif mutation in {"launcher-hash", "launcher-size"}:
        index = next(
            index
            for index, line in enumerate(lines)
            if line.split(",", 1)[0].rsplit("/", 1)[-1] == launcher_name
        )
        path, hash_field, size_field = lines[index].split(",")
        if mutation == "launcher-hash":
            prefix = "sha256="
            assert hash_field.startswith(prefix)
            first = hash_field[len(prefix)]
            replacement = "A" if first != "A" else "B"
            hash_field = prefix + replacement + hash_field[len(prefix) + 1 :]
        else:
            size_field = str(int(size_field) + 1)
        lines[index] = f"{path},{hash_field},{size_field}"
    elif mutation == "installer-hash":
        path = installer_path
        index = next(
            index
            for index, line in enumerate(lines)
            if line.split(",", 1)[0] == path
        )
        recorded = lines[index]
        prefix = f"{path},sha256="
        assert recorded.startswith(prefix)
        first = recorded[len(prefix)]
        replacement = "A" if first != "A" else "B"
        lines[index] = prefix + replacement + recorded[len(prefix) + 1 :]
    elif mutation == "direct-url-size":
        path = direct_url_path
        index = next(
            index
            for index, line in enumerate(lines)
            if line.split(",", 1)[0] == path
        )
        _path, hash_field, size_field = lines[index].split(",")
        lines[index] = f"{path},{hash_field},{int(size_field) + 1}"
    else:
        if mutation == "missing-installer":
            missing = installer_path
        elif mutation == "missing-direct-url":
            missing = direct_url_path
        else:
            missing = next(
                line.split(",", 1)[0]
                for line in lines
                if line.split(",", 1)[0].rsplit("/", 1)[-1] == launcher_name
            )
        lines = [line for line in lines if line.split(",", 1)[0] != missing]
    _write_record_lines(install_root, lines)

    _assert_install_rejected_before_marker(install_root, marker)


def test_installer_marker_must_be_exact_pip_lf(tmp_path: Path) -> None:
    install_root = tmp_path / "installer-crlf"
    _copy_contracts_install(install_root)
    marker = tmp_path / "installer-crlf-imported"
    relative = f"{CONTRACTS_DIST_INFO}/INSTALLER"
    installer = install_root / CONTRACTS_DIST_INFO / "INSTALLER"
    contents = b"pip\r\n"
    installer.write_bytes(contents)
    _replace_record_row(
        install_root,
        relative,
        f"{relative},sha256={_record_digest(contents)},{len(contents)}",
    )

    _assert_install_rejected_before_marker(install_root, marker)


def test_duplicate_generated_launcher_row_is_rejected(tmp_path: Path) -> None:
    install_root = tmp_path / "duplicate-launcher"
    _copy_contracts_install(install_root)
    marker = tmp_path / "duplicate-launcher-imported"
    lines = _record_lines(install_root)
    launcher_name = (
        "localai-integration.exe" if sys.platform == "win32" else "localai-integration"
    )
    launcher_line = next(
        line
        for line in lines
        if line.split(",", 1)[0].rsplit("/", 1)[-1] == launcher_name
    )
    lines.append(launcher_line)
    _write_record_lines(install_root, lines)

    _assert_install_rejected_before_marker(install_root, marker)


def test_noncanonical_quoted_record_path_is_rejected(tmp_path: Path) -> None:
    install_root = tmp_path / "quoted-record"
    _copy_contracts_install(install_root)
    marker = tmp_path / "quoted-record-imported"
    lines = _record_lines(install_root)
    expected = "localai_contracts/__init__.py"
    index = next(
        index for index, line in enumerate(lines) if line.split(",", 1)[0] == expected
    )
    lines[index] = f'"{expected}",' + lines[index].split(",", 1)[1]
    _write_record_lines(install_root, lines)

    _assert_install_rejected_before_marker(install_root, marker)


def test_mixed_record_line_endings_are_rejected(tmp_path: Path) -> None:
    install_root = tmp_path / "mixed-record-line-endings"
    _copy_contracts_install(install_root)
    marker = tmp_path / "mixed-record-line-endings-imported"
    record = _record_path(install_root)
    contents = record.read_bytes()
    assert b"\r\n" not in contents
    record.write_bytes(contents.replace(b"\n", b"\r\n", 1))

    _assert_install_rejected_before_marker(install_root, marker)


def test_oversized_record_is_rejected_before_import(tmp_path: Path) -> None:
    install_root = tmp_path / "oversized-record"
    _copy_contracts_install(install_root)
    marker = tmp_path / "oversized-record-imported"
    _record_path(install_root).write_bytes(b"x" * (64 * 1024 + 1))

    _assert_install_rejected_before_marker(install_root, marker)


def test_direct_url_duplicate_key_is_rejected(tmp_path: Path) -> None:
    install_root = tmp_path / "duplicate-direct-url-key"
    _copy_contracts_install(install_root)
    marker = tmp_path / "duplicate-direct-url-key-imported"
    relative = f"{CONTRACTS_DIST_INFO}/direct_url.json"
    direct_url = install_root / CONTRACTS_DIST_INFO / "direct_url.json"
    original = direct_url.read_bytes()
    assert original.endswith(b"}")
    duplicate = (
        original[:-1]
        + b',"url":"file:///localai_contracts-0.2.0a2-py3-none-any.whl"}'
    )
    direct_url.write_bytes(duplicate)
    _replace_record_row(
        install_root,
        relative,
        f"{relative},sha256={_record_digest(duplicate)},{len(duplicate)}",
    )

    _assert_install_rejected_before_marker(install_root, marker)


def test_direct_url_must_bind_the_reviewed_wheel_hash(tmp_path: Path) -> None:
    install_root = tmp_path / "wrong-direct-url-hash"
    _copy_contracts_install(install_root)
    marker = tmp_path / "wrong-direct-url-hash-imported"
    relative = f"{CONTRACTS_DIST_INFO}/direct_url.json"
    direct_url = install_root / CONTRACTS_DIST_INFO / "direct_url.json"
    original = direct_url.read_bytes()
    expected = LOCALAI_CONTRACTS_WHEEL_SHA256.encode("ascii")
    assert original.count(expected) >= 2
    mutated = original.replace(expected, b"0" * 64, 2)
    direct_url.write_bytes(mutated)
    _replace_record_row(
        install_root,
        relative,
        f"{relative},sha256={_record_digest(mutated)},{len(mutated)}",
    )

    _assert_install_rejected_before_marker(install_root, marker)


@pytest.mark.parametrize(
    "mutation",
    ["relative", "uppercase-scheme", "invalid-escape", "lowercase-escape"],
)
def test_direct_url_path_must_be_canonical_and_absolute(
    tmp_path: Path,
    mutation: str,
) -> None:
    install_root = tmp_path / f"direct-url-{mutation}"
    _copy_contracts_install(install_root)
    marker = tmp_path / f"direct-url-{mutation}-imported"
    relative = f"{CONTRACTS_DIST_INFO}/direct_url.json"
    direct_url = install_root / CONTRACTS_DIST_INFO / "direct_url.json"
    document = json.loads(direct_url.read_text(encoding="utf-8"))
    filename = "localai_contracts-0.2.0a2-py3-none-any.whl"
    if mutation == "relative":
        document["url"] = f"file:{filename}"
    elif mutation == "uppercase-scheme":
        document["url"] = f"FILE:///tmp/{filename}"
    elif mutation == "invalid-escape":
        document["url"] = f"file:///tmp/%ZZ/{filename}"
    else:
        document["url"] = f"file:///tmp/%6c{filename[1:]}"
    contents = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    direct_url.write_bytes(contents)
    _replace_record_row(
        install_root,
        relative,
        f"{relative},sha256={_record_digest(contents)},{len(contents)}",
    )

    _assert_install_rejected_before_marker(install_root, marker)


def test_posix_direct_url_accepts_canonical_encoded_colon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module.sys, "platform", "linux")

    assert adapter_module._decode_canonical_file_url_path(
        "/tmp/archive%3Aset/localai_contracts-0.2.0a2-py3-none-any.whl"
    ) == (
        b"/tmp/archive:set/localai_contracts-0.2.0a2-py3-none-any.whl"
    )


@pytest.mark.parametrize(
    "path",
    [
        "/tmp//localai_contracts-0.2.0a2-py3-none-any.whl",
        "/tmp\\localai_contracts-0.2.0a2-py3-none-any.whl",
        "/tmp/%2Flocalai_contracts-0.2.0a2-py3-none-any.whl",
    ],
)
def test_posix_direct_url_rejects_noncanonical_separator_forms(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setattr(adapter_module.sys, "platform", "linux")

    with pytest.raises(ValueError, match="direct URL"):
        adapter_module._decode_canonical_file_url_path(path)


def test_posix_direct_url_accepts_encoded_literal_backslash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module.sys, "platform", "linux")

    assert adapter_module._decode_canonical_file_url_path(
        "/tmp%5Carchive/localai_contracts-0.2.0a2-py3-none-any.whl"
    ) == (
        b"/tmp\\archive/localai_contracts-0.2.0a2-py3-none-any.whl"
    )


def test_linked_installer_metadata_is_rejected_before_import(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "linked-direct-url"
    _copy_contracts_install(install_root)
    marker = tmp_path / "linked-direct-url-imported"
    direct_url = install_root / CONTRACTS_DIST_INFO / "direct_url.json"
    target = tmp_path / "direct-url-target"
    target.write_bytes(direct_url.read_bytes())
    direct_url.unlink()
    try:
        direct_url.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {type(exc).__name__}")

    _assert_install_rejected_before_marker(install_root, marker)


def test_linked_record_is_rejected_before_import(tmp_path: Path) -> None:
    install_root = tmp_path / "linked-record"
    _copy_contracts_install(install_root)
    marker = tmp_path / "linked-record-imported"
    record = _record_path(install_root)
    target = tmp_path / "record-target"
    target.write_bytes(record.read_bytes())
    record.unlink()
    try:
        record.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {type(exc).__name__}")

    _assert_install_rejected_before_marker(install_root, marker)


def test_linked_resource_is_rejected_before_import(tmp_path: Path) -> None:
    tampered_root = tmp_path / "linked-resource"
    _copy_contracts_install(tampered_root)
    marker = tmp_path / "linked-resource-executed"
    fixture = (
        tampered_root
        / "localai_contracts"
        / "fixtures"
        / "phase0-conformance-v1.json"
    )
    target = (
        CONTRACTS_INSTALL_ROOT
        / "localai_contracts"
        / "fixtures"
        / "phase0-conformance-v1.json"
    )
    fixture.unlink()
    try:
        fixture.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {type(exc).__name__}")

    _assert_install_rejected_before_marker(tampered_root, marker)


def test_ambiguous_distributions_fail_before_import(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _copy_contracts_install(first_root)
    _copy_contracts_install(second_root)
    script = f"""
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2], sys.argv[3]]
from context_compiler.localai_contracts_adapter import (
    LocalAIContractsAdapter,
    LocalAIContractsUnavailableError,
)
try:
    LocalAIContractsAdapter()
except LocalAIContractsUnavailableError as exc:
    assert str(exc) == {VALIDATION_ERROR!r}
else:
    raise AssertionError("ambiguous contracts distributions were accepted")
assert "localai_contracts" not in sys.modules
"""

    completed = _run_isolated(
        script,
        ROOT / "src",
        first_root,
        second_root,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_unrecorded_importable_subpackage_is_rejected_before_import(
    tmp_path: Path,
) -> None:
    tampered_root = tmp_path / "extra-package"
    _copy_contracts_install(tampered_root)
    marker = tmp_path / "extra-package-executed"
    extra_package = tampered_root / "localai_contracts" / "models"
    extra_package.mkdir()
    (extra_package / "__init__.py").write_text(
        """
from pathlib import Path
import sys
Path(sys.argv[-1]).write_text("executed", encoding="utf-8")
""".lstrip(),
        encoding="utf-8",
    )

    _assert_install_rejected_before_marker(tampered_root, marker)


def test_forged_bytecode_cache_is_rejected_before_execution(
    tmp_path: Path,
) -> None:
    tampered_root = tmp_path / "forged-bytecode"
    _copy_contracts_install(tampered_root)
    marker = tmp_path / "forged-bytecode-executed"
    source = tampered_root / "localai_contracts" / "canonical.py"
    source_stat = source.stat()
    cache = Path(importlib.util.cache_from_source(str(source), optimization=""))
    cache.parent.mkdir()
    code = compile(
        "open(__import__('sys').argv[-1],'w').close()\n",
        str(source),
        "exec",
        dont_inherit=True,
        optimize=0,
)
    cache.write_bytes(
        importlib.util.MAGIC_NUMBER
        + (0).to_bytes(4, "little")
        + (int(source_stat.st_mtime) & 0xFFFF_FFFF).to_bytes(4, "little")
        + (source_stat.st_size & 0xFFFF_FFFF).to_bytes(4, "little")
        + marshal.dumps(code)
    )

    _assert_install_rejected_before_marker(tampered_root, marker)


def test_external_pycache_prefix_is_rejected_before_bytecode_executes(
    tmp_path: Path,
) -> None:
    cache_prefix = tmp_path / "external-cache"
    cache_prefix_argument = (
        Path("\\\\?\\" + str(cache_prefix))
        if sys.platform == "win32"
        else cache_prefix
    )
    marker = tmp_path / "external-cache-executed"
    script = f"""
import importlib.util
import marshal
from pathlib import Path
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
sys.pycache_prefix = sys.argv[3]
source = Path(sys.argv[2]) / "localai_contracts" / "__init__.py"
cache = Path(importlib.util.cache_from_source(str(source)))
cache.parent.mkdir(parents=True)
source_stat = source.stat()
code = compile(
    "from pathlib import Path\\n"
    "import sys\\n"
    "Path(sys.argv[4]).write_text('executed', encoding='utf-8')\\n",
    str(source),
    "exec",
    dont_inherit=True,
    optimize=0,
)
cache.write_bytes(
    importlib.util.MAGIC_NUMBER
    + (0).to_bytes(4, "little")
    + (int(source_stat.st_mtime) & 0xFFFF_FFFF).to_bytes(4, "little")
    + (source_stat.st_size & 0xFFFF_FFFF).to_bytes(4, "little")
    + marshal.dumps(code)
)
from context_compiler.localai_contracts_adapter import (
    LocalAIContractsAdapter,
    LocalAIContractsUnavailableError,
)
try:
    LocalAIContractsAdapter()
except LocalAIContractsUnavailableError as exc:
    assert str(exc) == {VALIDATION_ERROR!r}
else:
    raise AssertionError("external bytecode cache prefix was accepted")
assert "localai_contracts" not in sys.modules
cache.unlink()
"""

    completed = _run_isolated(
        script,
        ROOT / "src",
        CONTRACTS_INSTALL_ROOT,
        cache_prefix_argument,
        marker,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not marker.exists()


def test_bytecode_cache_compiled_from_verified_source_is_accepted(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "verified-bytecode"
    _copy_contracts_install(install_root)
    source = install_root / "localai_contracts" / "canonical.py"
    source_stat = source.stat()
    cache = Path(importlib.util.cache_from_source(str(source), optimization=""))
    cache.parent.mkdir()
    code = compile(
        source.read_bytes(),
        str(source),
        "exec",
        dont_inherit=True,
        optimize=0,
    )
    cache.write_bytes(
        importlib.util.MAGIC_NUMBER
        + (0).to_bytes(4, "little")
        + (int(source_stat.st_mtime) & 0xFFFF_FFFF).to_bytes(4, "little")
        + (source_stat.st_size & 0xFFFF_FFFF).to_bytes(4, "little")
        + marshal.dumps(code)
    )
    script = """
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from context_compiler.localai_contracts_adapter import LocalAIContractsAdapter
manifest = LocalAIContractsAdapter().get_manifest()
assert manifest.supported_operations == ["context.compile"]
"""

    completed = _run_isolated(
        script,
        ROOT / "src",
        install_root,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


@pytest.mark.parametrize(
    "poison_name",
    ["localai_contracts", "localai_contracts.models"],
)
def test_preloaded_module_poisoning_is_rejected(
    tmp_path: Path,
    poison_name: str,
) -> None:
    poison_file = tmp_path / "poison.py"
    poison_file.write_text("", encoding="utf-8")
    script = f"""
import importlib.machinery
import sys
import types
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from context_compiler.localai_contracts_adapter import (
    LocalAIContractsAdapter,
    LocalAIContractsUnavailableError,
)
name = sys.argv[4]
poison = types.ModuleType(name)
poison.__file__ = sys.argv[3]
poison.__package__ = name if name == "localai_contracts" else "localai_contracts"
poison.__spec__ = importlib.machinery.ModuleSpec(
    name,
    loader=None,
    origin=sys.argv[3],
)
if name == "localai_contracts":
    poison.__path__ = [str(__import__("pathlib").Path(sys.argv[3]).parent)]
sys.modules[name] = poison
try:
    LocalAIContractsAdapter()
except LocalAIContractsUnavailableError as exc:
    assert str(exc) == {VALIDATION_ERROR!r}
else:
    raise AssertionError("preloaded poison was accepted")
assert sys.modules[name] is poison
if name != "localai_contracts":
    assert "localai_contracts" not in sys.modules
"""

    completed = _run_isolated(
        script,
        ROOT / "src",
        CONTRACTS_INSTALL_ROOT,
        poison_file,
        Path(poison_name),
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_preloaded_module_getattr_is_not_invoked_during_rejection(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "module-getattr-executed"
    script = f"""
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import types
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from context_compiler.localai_contracts_adapter import (
    LocalAIContractsAdapter,
    LocalAIContractsUnavailableError,
)
name = "localai_contracts"
package = Path(sys.argv[2]) / name
initializer = package / "__init__.py"
loader = importlib.machinery.SourceFileLoader(name, str(initializer))
spec = importlib.util.spec_from_file_location(
    name,
    str(initializer),
    loader=loader,
    submodule_search_locations=[str(package)],
)
assert type(spec) is importlib.machinery.ModuleSpec
poison = types.ModuleType(name)
poison.__package__ = name
poison.__spec__ = spec
poison.__loader__ = loader
poison.__path__ = [str(package)]
def module_getattr(_attribute):
    Path(sys.argv[3]).write_text("executed", encoding="utf-8")
    raise AttributeError(_attribute)
poison.__getattr__ = module_getattr
sys.modules[name] = poison
try:
    LocalAIContractsAdapter()
except LocalAIContractsUnavailableError as exc:
    assert str(exc) == {VALIDATION_ERROR!r}
else:
    raise AssertionError("preloaded poison was accepted")
assert sys.modules[name] is poison
"""

    completed = _run_isolated(
        script,
        ROOT / "src",
        CONTRACTS_INSTALL_ROOT,
        marker,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not marker.exists()


def test_same_origin_preloaded_module_getattr_is_not_invoked_for_missing_api(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "same-origin-module-getattr-executed"
    script = f"""
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import types
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import context_compiler.localai_contracts_adapter as adapter
name = "localai_contracts"
package = Path(sys.argv[2]) / name
initializer = package / "__init__.py"
loader = importlib.machinery.SourceFileLoader(name, str(initializer))
spec = importlib.util.spec_from_file_location(
    name,
    str(initializer),
    loader=loader,
    submodule_search_locations=[str(package)],
)
assert type(spec) is importlib.machinery.ModuleSpec
poison = types.ModuleType(name)
poison.__file__ = str(initializer)
poison.__package__ = name
poison.__spec__ = spec
poison.__loader__ = loader
poison.__path__ = [str(package)]
def module_getattr(_attribute):
    Path(sys.argv[3]).write_text("executed", encoding="utf-8")
    raise AttributeError(_attribute)
poison.__getattr__ = module_getattr
sys.modules[name] = poison
try:
    adapter.LocalAIContractsAdapter()
except adapter.LocalAIContractsUnavailableError as exc:
    assert str(exc) == {VALIDATION_ERROR!r}
else:
    raise AssertionError("same-origin incomplete poison was accepted")
assert sys.modules[name] is poison
"""

    completed = _run_isolated(
        script,
        ROOT / "src",
        CONTRACTS_INSTALL_ROOT,
        marker,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not marker.exists()


def test_loaded_root_origin_is_revalidated_after_import(tmp_path: Path) -> None:
    poison_file = tmp_path / "wrong-origin.py"
    poison_file.write_text("", encoding="utf-8")
    script = f"""
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import context_compiler.localai_contracts_adapter as adapter
real_import = adapter.importlib.import_module
def import_then_mutate(name):
    module = real_import(name)
    module.__file__ = sys.argv[3]
    return module
adapter.importlib.import_module = import_then_mutate
try:
    adapter.LocalAIContractsAdapter()
except adapter.LocalAIContractsUnavailableError as exc:
    assert str(exc) == {VALIDATION_ERROR!r}
else:
    raise AssertionError("post-import origin mutation was accepted")
"""

    completed = _run_isolated(
        script,
        ROOT / "src",
        CONTRACTS_INSTALL_ROOT,
        poison_file,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_import_return_object_must_be_the_validated_sys_modules_root() -> None:
    script = f"""
import sys
import types
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import context_compiler.localai_contracts_adapter as adapter
real_import = adapter.importlib.import_module
fake = types.ModuleType("localai_contracts")
fake.__version__ = "0.2.0a2"
fake.PROTOCOL_VERSION = "1.0.0"
for name in (
    "ComponentCapabilityManifest",
    "ConnectorRequest",
    "ConnectorResponse",
    "ConnectorServer",
    "ContextBundle",
    "DEFAULT_PARSE_LIMITS",
    "NdjsonConnectorServer",
    "ParseLimits",
    "SourceEvent",
    "SubjectOperationProbe",
    "SubjectOperationPurpose",
    "UnsupportedOperationError",
    "bounded_canonical_bytes",
    "canonical_bytes",
    "parse_json",
):
    setattr(fake, name, object())
def import_then_substitute(name):
    real_import(name)
    return fake
adapter.importlib.import_module = import_then_substitute
try:
    adapter._load_contracts()
except adapter.LocalAIContractsUnavailableError as exc:
    assert str(exc) == {VALIDATION_ERROR!r}
else:
    raise AssertionError("substituted import return object was accepted")
"""

    completed = _run_isolated(
        script,
        ROOT / "src",
        CONTRACTS_INSTALL_ROOT,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_exact_wheel_identity_and_executed_manifest_are_closed() -> None:
    assert contracts.__version__ == LOCALAI_CONTRACTS_VERSION == "0.2.0a2"
    assert (
        contracts.PROTOCOL_VERSION
        == LOCALAI_CONTRACTS_PROTOCOL_VERSION
        == "1.0.0"
    )
    assert (
        LOCALAI_CONTRACTS_WHEEL_SHA256
        == "36a02dbc4267402949dddda1da180d800590cc579e0c1ecb022fc96f6a7c29ae"
    )
    assert (
        LOCALAI_CONTRACTS_SOURCE_COMMIT
        == "3858190e8b458847da94e9ed24be83f4928b7d1a"
    )

    manifest = LocalAIContractsAdapter().get_manifest()
    assert type(manifest) is contracts.ComponentCapabilityManifest
    manifest.validate()
    assert manifest.supported_operations == [CONTEXT_COMPILE_OPERATION]
    assert manifest.operation_capabilities == [
        {
            "operation": CONTEXT_COMPILE_OPERATION,
            "evidence": "executed",
            "limitations": [
                "Canonical trust is not authentication.",
                "Only independently authenticated exact source spans may "
                "enter trusted active memory.",
                "Accounting covers projected span-content components only.",
            ],
            "disabled_reason": None,
        }
    ]
    assert manifest.restrictions == {"models": [], "runtimes": [], "backends": []}
    assert manifest.supported_schema_versions == {
        name: ["1.0.0"]
        for name in (
            "ComponentCapabilityManifest",
            "ConnectorRequest",
            "ConnectorResponse",
            "ErrorEnvelope",
            "SourceEvent",
            "ContextBundle",
        )
    }


def test_actual_request_requires_handshake_and_returns_actual_response() -> None:
    adapter = LocalAIContractsAdapter()
    event = _event()

    premature = adapter.handle_request(_compile_request(event))
    assert type(premature) is contracts.ConnectorResponse
    assert not premature.ok
    assert _error(premature).code == "handshake_required"

    handshake = adapter.handle_request(_handshake_request(adapter))
    assert type(handshake) is contracts.ConnectorResponse
    assert handshake.ok
    assert handshake.payload is not None
    assert handshake.payload["negotiated"]["operations"] == [
        CONTEXT_COMPILE_OPERATION
    ]

    response = adapter.handle_request(_compile_request(event))
    assert type(response) is contracts.ConnectorResponse
    assert response.ok
    assert response.payload is not None
    bundle = contracts.ContextBundle.from_dict(response.payload)
    assert bundle.to_dict() == response.payload
    assert "context_bundle" not in response.payload

    with pytest.raises(TypeError, match="actual localai_contracts.ConnectorRequest"):
        adapter.handle_request(_compile_request(event).to_dict())


def test_unknown_operations_fail_closed_without_reaching_private_operations() -> None:
    adapter = LocalAIContractsAdapter()
    with pytest.raises(contracts.UnsupportedOperationError):
        adapter.handle("compile_memory", {})
    with pytest.raises(contracts.UnsupportedOperationError):
        adapter.handle("unknown.operation", {})

    assert adapter.handle_request(_handshake_request(adapter)).ok
    response = adapter.handle_request(
        contracts.ConnectorRequest(
            protocol_version="1.0.0",
            request_id="private-operation",
            operation="compile_memory",
            payload={},
            expected_response_schema=None,
        )
    )
    assert not response.ok
    assert _error(response).code == "operation_not_negotiated"


@pytest.mark.parametrize(
    "role",
    ["system", "developer", "user", "assistant", "tool", "connector", "other"],
)
def test_declared_trust_and_metadata_cannot_authenticate_authority(role: str) -> None:
    event = _event(
        role=role,
        metadata={
            "trusted_for_state": True,
            "ctxc_authenticated_authority": True,
            "localai_authority": {
                "authenticated": True,
                "trusted_for_state": True,
            },
        },
    )
    document = LocalAIContractsAdapter().handle(
        CONTEXT_COMPILE_OPERATION,
        {"source_events": [event.to_dict()]},
    )
    bundle = contracts.ContextBundle.from_dict(document)

    assert bundle.trusted_active_memory == []
    full_source = [
        span
        for span in bundle.untrusted_retrieved_spans
        if span["start_byte"] == 0
        and span["end_byte"] == len(event.content.encode("utf-8"))
        and span["content"] == event.content
    ]
    assert len(full_source) == 1


def test_independent_authority_enables_only_exact_unicode_source_spans() -> None:
    observed: list[Any] = []

    def verify(event: Any) -> AuthenticatedAuthority:
        observed.append(event)
        return AuthenticatedAuthority("test-host")

    event = _event("constraint: Café stays PostgreSQL.")
    bundle = contracts.ContextBundle.from_dict(
        LocalAIContractsAdapter(authority_verifier=verify).handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [event.to_dict()]},
        )
    )

    assert len(observed) == 1
    assert type(observed[0]) is contracts.SourceEvent
    assert bundle.trusted_active_memory
    assert any(
        span["content"] == "Café stays PostgreSQL."
        for span in bundle.trusted_active_memory
    )
    for span in bundle.trusted_active_memory + bundle.untrusted_retrieved_spans:
        assert len(span["content"].encode("utf-8")) == (
            span["end_byte"] - span["start_byte"]
        )
        contracts.verify_text_digest(span["content"], span["content_hash"])


def test_untrusted_or_derived_events_do_not_invoke_authority_verifier() -> None:
    calls: list[str] = []

    def verify(event: Any) -> AuthenticatedAuthority:
        calls.append(event.source_event_id)
        return AuthenticatedAuthority("test-host")

    adapter = LocalAIContractsAdapter(authority_verifier=verify)
    for trust in ("untrusted", "derived"):
        event = _event(
            source_event_id=f"event-{trust}",
            trust=trust,
        )
        bundle = contracts.ContextBundle.from_dict(
            adapter.handle(
                CONTEXT_COMPILE_OPERATION,
                {"source_events": [event.to_dict()]},
            )
        )
        assert bundle.trusted_active_memory == []
    assert calls == []


def test_tool_state_requires_an_independent_tool_specific_decision() -> None:
    event = _event(
        "confirmed_fact: The migration completed.",
        role="tool",
    )
    no_state = contracts.ContextBundle.from_dict(
        LocalAIContractsAdapter(
            authority_verifier=lambda event: AuthenticatedAuthority("test-host")
        ).handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [event.to_dict()]},
        )
    )
    assert no_state.trusted_active_memory == []

    with_state = contracts.ContextBundle.from_dict(
        LocalAIContractsAdapter(
            authority_verifier=lambda event: AuthenticatedAuthority(
                "test-host",
                trusted_for_state=True,
            )
        ).handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [event.to_dict()]},
        )
    )
    assert any(
        span["content"] == "The migration completed."
        for span in with_state.trusted_active_memory
    )


@pytest.mark.parametrize(
    "verifier",
    [
        lambda event: {"issuer": "not-an-actual-decision"},
        lambda event: AuthenticatedAuthority("test-host", trusted_for_state=True),
        lambda event: (_ for _ in ()).throw(RuntimeError("private detail")),
    ],
)
def test_bad_authority_verifiers_fail_closed_behind_generic_envelope(
    verifier: Any,
) -> None:
    adapter = LocalAIContractsAdapter(authority_verifier=verifier)
    server = contracts.ConnectorServer(adapter)
    assert server.handle(_handshake_request(adapter)).ok
    response = server.handle(_compile_request(_event(role="user")))
    assert not response.ok
    error = _error(response)
    assert error.message in {
        "connector request validation failed",
        "connector adapter failed",
    }
    assert "private detail" not in error.message
    assert error.details == {}


def test_content_digest_and_input_identity_fail_closed() -> None:
    adapter = LocalAIContractsAdapter()
    server = contracts.ConnectorServer(adapter)
    assert server.handle(_handshake_request(adapter)).ok
    response = server.handle(
        _compile_request(
            _event(digest="0" * 64),
        )
    )
    assert not response.ok
    assert _error(response).code == "request_failed"

    duplicate = _event(source_event_id="duplicate", sequence=0)
    with pytest.raises(ValueError, match="duplicate id"):
        adapter.handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [duplicate.to_dict(), duplicate.to_dict()]},
        )
    second_sequence = _event(source_event_id="other", sequence=0)
    with pytest.raises(ValueError, match="duplicate sequence"):
        adapter.handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [duplicate.to_dict(), second_sequence.to_dict()]},
        )


def test_fresh_in_process_and_ndjson_results_are_semantically_identical() -> None:
    event = _event()
    in_process = LocalAIContractsAdapter()
    assert in_process.handle_request(_handshake_request(in_process)).ok
    in_process_response = in_process.handle_request(_compile_request(event))
    assert in_process_response.ok

    serialized = LocalAIContractsAdapter()
    requests = (
        _handshake_request(serialized).canonical_bytes()
        + b"\n"
        + _compile_request(event).canonical_bytes()
        + b"\n"
    )
    reader = io.BytesIO(requests)
    writer = io.BytesIO()
    assert serialized.serve_ndjson(reader, writer) == 2
    lines = writer.getvalue().splitlines()
    assert len(lines) == 2
    handshake = contracts.ConnectorResponse.from_json(lines[0])
    response = contracts.ConnectorResponse.from_json(lines[1])
    assert handshake.ok
    assert response.ok
    assert contracts.canonical_bytes(response.payload) == contracts.canonical_bytes(
        in_process_response.payload
    )
    assert (
        contracts.ContextBundle.from_dict(response.payload).canonical_digest()
        == contracts.ContextBundle.from_dict(
            in_process_response.payload
        ).canonical_digest()
    )


def test_deterministic_projection_excludes_private_time_and_session_identity() -> None:
    event = _event()
    first = LocalAIContractsAdapter().handle(
        CONTEXT_COMPILE_OPERATION,
        {"source_events": [event.to_dict()]},
    )
    second = LocalAIContractsAdapter().handle(
        CONTEXT_COMPILE_OPERATION,
        {"source_events": [event.to_dict()]},
    )
    assert contracts.canonical_bytes(first) == contracts.canonical_bytes(second)
    assert first["bundle_id"] == second["bundle_id"]


def test_conversion_audit_matches_self_hashed_golden_and_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, adapter, events, bundle, audit = _compile_conversion_golden(
        monkeypatch
    )
    expected = fixture["expected"]
    parsed_events, private_events, _authenticated = adapter._map_source_events(
        fixture["source_events"]
    )
    private_hashes = [
        hashlib.sha256(
            contracts.bounded_canonical_bytes(
                event.to_dict(),
                limits=adapter.limits,
            )
        ).hexdigest()
        for event in private_events
    ]
    round_trips = [
        adapter._shared_source_event_from_private(event)
        for event in private_events
    ]

    assert [event.to_dict() for event in parsed_events] == fixture["source_events"]
    assert private_hashes == expected["private_source_event_sha256"]
    assert [event.to_dict() for event in round_trips] == fixture["source_events"]
    assert [event.canonical_digest() for event in round_trips] == expected[
        "round_trip_source_event_sha256"
    ]
    assert bundle.canonical_digest() == expected["context_bundle_sha256"]
    assert audit["context_bundle_conversion"]["private_document_sha256"] == (
        expected["private_context_bundle_document_sha256"]
    )
    assert audit["context_bundle_conversion"]["private_bundle_sha256"] == (
        expected["private_context_bundle_self_sha256"]
    )
    assert audit["context_bundle_conversion"]["output_document_sha256"] == (
        expected["context_bundle_sha256"]
    )
    assert audit["audit_sha256"] == expected["conversion_audit_sha256"]
    audit_bytes = contracts.bounded_canonical_bytes(
        audit,
        limits=adapter.limits,
    )
    assert len(audit_bytes) == expected["conversion_audit_bytes"]
    assert hashlib.sha256(audit_bytes).hexdigest() == expected[
        "conversion_audit_document_sha256"
    ]
    assert len(audit["source_event_conversion"]["records"]) == expected[
        "source_event_records"
    ]
    assert len(audit["source_event_conversion"]["field_dispositions"]) == (
        expected["source_field_dispositions"]
    )
    assert len(audit["context_bundle_conversion"]["field_dispositions"]) == (
        expected["context_field_dispositions"]
    )
    assert adapter.verify_conversion_audit(
        audit,
        source_events=events,
        output_bundle=bundle,
    ) == audit
    assert audit["schema"] == LOCALAI_CONVERSION_AUDIT_SCHEMA

    schema_path = ROOT / "schemas" / "localai-contract-conversion-audit.schema.json"
    schema_name = schema_path.name
    validate_instance(
        audit,
        schema_name,
        documents={
            schema_name: json.loads(schema_path.read_text(encoding="utf-8"))
        },
    )

    serialized_audit = json.dumps(audit, ensure_ascii=False, sort_keys=True)
    for document in fixture["source_events"]:
        assert document["source_event_id"] not in serialized_audit
        assert document["content"] not in serialized_audit
        sentinel = document["metadata"].get("sentinel")
        if sentinel is None:
            sentinel = document["metadata"]["nested"]["sentinel"]
        assert sentinel not in serialized_audit
    assert "FIXTURE_AUTHORITY_SENTINEL" not in serialized_audit

    wire_document = adapter.handle(
        CONTEXT_COMPILE_OPERATION,
        {"source_events": fixture["source_events"]},
    )
    assert wire_document == bundle.to_dict()
    assert "conversion_audit" not in wire_document
    assert set(wire_document) == set(contracts.ContextBundle.from_dict(wire_document).to_dict())
    assert all(type(event) is contracts.SourceEvent for event in events)


def test_conversion_audit_machine_labels_provider_assertions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture, adapter, events, bundle, audit = _compile_conversion_golden(
        monkeypatch
    )
    boundary = audit["claim_boundary"]
    assert boundary["self_hash"] == "mutation_detection_only_not_authentication"
    assert boundary["digest_privacy"] == (
        "sensitive_unsalted_linkable_dictionary_testable_not_safe_telemetry"
    )
    assert (
        "/source_event_conversion/records/{ordinal}/independently_authenticated"
        in boundary["provider_asserted_path_patterns"]
    )
    assert (
        "/context_bundle_conversion/private_document_sha256"
        in boundary["provider_asserted_path_patterns"]
    )
    assert (
        "/context_bundle_conversion/source_coverage"
        in boundary["provider_assertion_dependent_path_patterns"]
    )

    relabeled = json.loads(json.dumps(audit))
    relabeled["claim_boundary"]["self_hash"] = "authenticated"
    _rehash_conversion_audit(adapter, relabeled)
    with pytest.raises(ValueError, match="claim boundary is invalid"):
        adapter.verify_conversion_audit(
            relabeled,
            source_events=events,
            output_bundle=bundle,
        )


def test_conversion_audit_provider_asserted_private_digests_are_not_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture, adapter, events, bundle, audit = _compile_conversion_golden(
        monkeypatch
    )
    changed = json.loads(json.dumps(audit))
    changed["source_event_conversion"]["records"][0][
        "private_document_sha256"
    ] = "0" * 64
    changed["context_bundle_conversion"]["private_document_sha256"] = "1" * 64
    changed["context_bundle_conversion"]["private_bundle_sha256"] = "2" * 64
    _rehash_conversion_audit(adapter, changed)

    assert adapter.verify_conversion_audit(
        changed,
        source_events=events,
        output_bundle=bundle,
    ) == changed


def test_conversion_audit_excludes_digest_shaped_raw_values() -> None:
    source_id = "a" * 64
    content = "b" * 64
    metadata_value = "c" * 64
    authority_issuer = "d" * 64
    adapter = LocalAIContractsAdapter(
        authority_verifier=lambda _event: AuthenticatedAuthority(authority_issuer)
    )
    event = _event(
        source_event_id=source_id,
        content=content,
        metadata={"sentinel": metadata_value},
    )

    _bundle, audit = adapter.compile_with_conversion_audit([event])

    leaves = _string_leaves(audit)
    for raw_value in (source_id, content, metadata_value, authority_issuer):
        assert raw_value not in leaves


def test_conversion_audit_retains_non_sha256_source_hashes_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, adapter, _events, _bundle, _audit = _compile_conversion_golden(
        monkeypatch
    )
    _parsed, private_events, _authenticated = adapter._map_source_events(
        fixture["source_events"]
    )
    for document, private_event in zip(
        fixture["source_events"],
        private_events,
        strict=True,
    ):
        if document["content_hash"]["algorithm"] == "sha256":
            assert private_event.content_sha256 == document["content_hash"]["value"]
        else:
            assert private_event.content_sha256 == ""
        assert (
            private_event.metadata["localai_contracts_event"]["content_hash"]
            == document["content_hash"]
        )
        assert (
            adapter._shared_source_event_from_private(private_event).to_dict()
            == document
        )


def test_private_source_event_retention_mutation_fails_closed() -> None:
    fixture = _load_conversion_golden()
    adapter = LocalAIContractsAdapter()
    _events, private_events, _authenticated = adapter._map_source_events(
        [fixture["source_events"][0]]
    )
    private_event = private_events[0]
    metadata = private_event.to_dict()["metadata"]
    metadata["localai_contracts_event"]["original_role"] = "tool"
    mutated = replace(private_event, metadata=metadata)

    with pytest.raises(ValueError, match="provenance is inconsistent"):
        adapter._shared_source_event_from_private(mutated)


def test_private_source_event_authority_mutation_fails_closed() -> None:
    fixture = _load_conversion_golden()
    adapter = LocalAIContractsAdapter(authority_verifier=_golden_authority)
    user_document = next(
        document
        for document in fixture["source_events"]
        if document["source_event_id"] == "golden-user"
    )
    _events, private_events, _authenticated = adapter._map_source_events(
        [user_document]
    )
    mutated = replace(
        private_events[0],
        authority={"authenticated": False, "trusted_for_state": False},
    )

    with pytest.raises(ValueError, match="authenticated authority is inconsistent"):
        adapter._shared_source_event_from_private(mutated)


def test_conversion_audit_tampering_is_rejected_even_when_rehashed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture, adapter, events, bundle, audit = _compile_conversion_golden(
        monkeypatch
    )
    tampered = json.loads(json.dumps(audit))
    tampered["summary"]["semantic_completeness_claimed"] = True

    with pytest.raises(ValueError, match="digest mismatch"):
        adapter.verify_conversion_audit(
            tampered,
            source_events=events,
            output_bundle=bundle,
        )

    unsigned = {
        key: value for key, value in tampered.items() if key != "audit_sha256"
    }
    tampered["audit_sha256"] = hashlib.sha256(
        contracts.bounded_canonical_bytes(unsigned, limits=adapter.limits)
    ).hexdigest()
    with pytest.raises(ValueError, match="summary is invalid"):
        adapter.verify_conversion_audit(
            tampered,
            source_events=events,
            output_bundle=bundle,
        )


@pytest.mark.parametrize(
    ("section", "expected_error"),
    [
        ("source", "SourceEvent record is invalid"),
        ("bundle", "ContextBundle policy is invalid"),
    ],
)
def test_conversion_audit_rejects_rehashed_round_trip_contradictions(
    monkeypatch: pytest.MonkeyPatch,
    section: str,
    expected_error: str,
) -> None:
    _fixture, adapter, events, bundle, audit = _compile_conversion_golden(
        monkeypatch
    )
    tampered = json.loads(json.dumps(audit))
    if section == "source":
        tampered["source_event_conversion"]["records"][0][
            "round_trip_document_sha256"
        ] = "0" * 64
    else:
        tampered["context_bundle_conversion"][
            "output_round_trip_sha256"
        ] = "0" * 64
    unsigned = {
        key: value for key, value in tampered.items() if key != "audit_sha256"
    }
    tampered["audit_sha256"] = hashlib.sha256(
        contracts.bounded_canonical_bytes(unsigned, limits=adapter.limits)
    ).hexdigest()

    with pytest.raises(ValueError, match=expected_error):
        adapter.verify_conversion_audit(
            tampered,
            source_events=events,
            output_bundle=bundle,
        )


def test_conversion_audit_rejects_rehashed_evidence_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture, adapter, events, bundle, audit = _compile_conversion_golden(
        monkeypatch
    )
    tampered = json.loads(json.dumps(audit))
    tampered["input_payload_sha256"] = "0" * 64
    for record in tampered["source_event_conversion"]["records"]:
        record["input_document_sha256"] = "1" * 64
        record["round_trip_document_sha256"] = "1" * 64
    tampered["context_bundle_conversion"]["output_document_sha256"] = "2" * 64
    tampered["context_bundle_conversion"]["output_round_trip_sha256"] = "2" * 64
    unsigned = {
        key: value for key, value in tampered.items() if key != "audit_sha256"
    }
    tampered["audit_sha256"] = hashlib.sha256(
        contracts.bounded_canonical_bytes(unsigned, limits=adapter.limits)
    ).hexdigest()

    with pytest.raises(ValueError, match="SourceEvent evidence mismatch"):
        adapter.verify_conversion_audit(
            tampered,
            source_events=events,
            output_bundle=bundle,
        )


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("producer", "exact source provenance changed"),
        ("bundle-id", "ContextBundle identity mismatch"),
        ("trusted-span", "exact source span changed"),
        ("accounting", "span token count changed"),
        ("policy", "policy identity mismatch"),
        ("source-store", "source-store evidence mismatch"),
        ("span-order", "canonical span order changed"),
        ("provenance-order", "canonical provenance order changed"),
        ("omission", "omission shape changed"),
        ("overflow", "overflow shape changed"),
    ],
)
def test_conversion_audit_rejects_rebound_output_semantic_mutation(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    expected_error: str,
) -> None:
    _fixture, adapter, events, bundle, audit = _compile_conversion_golden(
        monkeypatch
    )
    output_document = bundle.to_dict()
    if mutation == "producer":
        source_provenance = next(
            item
            for item in output_document["provenance"]
            if item["transform"] == "exact_source_event_projection"
        )
        source_provenance["producer"] = {
            "name": "forged-producer",
            "version": "9.9",
        }
        _recompute_projected_bundle_id(adapter, output_document)
    elif mutation == "bundle-id":
        output_document["bundle_id"] = "ctxc-context-" + ("0" * 64)
    elif mutation == "trusted-span":
        span = output_document["trusted_active_memory"][0]
        original_span_id = span["span_id"]
        span["content"] = "X" + span["content"][1:]
        content_sha256 = hashlib.sha256(span["content"].encode("utf-8")).hexdigest()
        span["content_hash"] = {
            "algorithm": "sha256",
            "value": content_sha256,
        }
        span["span_id"] = adapter_module._stable_id(
            "active",
            span["source_event_id"],
            span["start_byte"],
            span["end_byte"],
            content_sha256,
        )
        provenance = next(
            item
            for item in output_document["provenance"]
            if item["item_id"] == original_span_id
        )
        provenance["item_id"] = span["span_id"]
        _recompute_projected_bundle_id(adapter, output_document)
    elif mutation == "accounting":
        output_document["trusted_active_memory"][0]["token_count"] += 1
        output_document["token_accounting"]["trusted_tokens"] += 1
        output_document["token_accounting"]["total_tokens"] += 1
        _recompute_projected_bundle_id(adapter, output_document)
    elif mutation == "policy":
        output_document["policy_identity"]["name"] = "forged-policy"
        output_document["policy_identity"]["version"] = "forged-version"
        _recompute_projected_bundle_id(adapter, output_document)
    elif mutation == "source-store":
        output_document["source_store_identity"]["digest"]["value"] = "0" * 64
        _recompute_projected_bundle_id(adapter, output_document)
    elif mutation == "span-order":
        output_document["untrusted_retrieved_spans"].reverse()
        _recompute_projected_bundle_id(adapter, output_document)
    elif mutation == "provenance-order":
        output_document["provenance"].reverse()
        _recompute_projected_bundle_id(adapter, output_document)
    elif mutation == "omission":
        output_document["omissions"].append(
            {
                "source_event_id": "ghost-event",
                "reason": "forged-omission",
                "estimated_tokens": None,
            }
        )
        _recompute_projected_bundle_id(adapter, output_document)
    else:
        output_document["overflow"] = {
            "occurred": True,
            "dropped_items": 17,
            "dropped_tokens": 9,
            "reason": "forged-overflow",
        }
        _recompute_projected_bundle_id(adapter, output_document)
    mutated_bundle = contracts.ContextBundle.from_dict(output_document)
    rebound_audit = _rebind_audit_output(adapter, audit, mutated_bundle)

    with pytest.raises(ValueError, match=expected_error):
        adapter.verify_conversion_audit(
            rebound_audit,
            source_events=events,
            output_bundle=mutated_bundle,
        )


def test_conversion_audit_schema_rejects_digest_line_terminator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture, _adapter, _events, _bundle, audit = _compile_conversion_golden(
        monkeypatch
    )
    schema_path = ROOT / "schemas" / "localai-contract-conversion-audit.schema.json"
    schema_name = schema_path.name
    documents = {
        schema_name: json.loads(schema_path.read_text(encoding="utf-8"))
    }
    tampered = json.loads(json.dumps(audit))
    tampered["audit_sha256"] += "\n"

    with pytest.raises(SchemaValidationError):
        validate_instance(
            tampered,
            schema_name,
            documents=documents,
        )

    invalid_disposition = json.loads(json.dumps(audit))
    disposition = invalid_disposition["source_event_conversion"][
        "field_dispositions"
    ][0]
    disposition["status"] = "omitted"
    disposition["lossy"] = False
    with pytest.raises(SchemaValidationError):
        validate_instance(
            invalid_disposition,
            schema_name,
            documents=documents,
        )

    invalid_authority = json.loads(json.dumps(audit))
    record = invalid_authority["source_event_conversion"]["records"][0]
    record["trusted_for_state"] = True
    record["independently_authenticated"] = False
    with pytest.raises(SchemaValidationError):
        validate_instance(
            invalid_authority,
            schema_name,
            documents=documents,
        )


def test_wire_compile_rejects_incomplete_context_field_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _load_conversion_golden()
    monkeypatch.setattr(
        compiler_module,
        "utc_now",
        lambda: fixture["fixed_compiled_at"],
    )
    adapter = LocalAIContractsAdapter(
        connector_factory=_golden_connector_factory,
        authority_verifier=_golden_authority,
    )
    complete_policy = adapter_module._context_bundle_projection_policy()
    monkeypatch.setattr(
        adapter_module,
        "_context_bundle_projection_policy",
        lambda: complete_policy[1:],
    )

    with pytest.raises(ValueError, match="field policy is incomplete"):
        adapter.handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": fixture["source_events"]},
        )


def test_wire_compile_checks_canonical_source_round_trip_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LocalAIContractsAdapter()
    event = _event(metadata={"numeric_sentinel": 1.0})
    real_map = adapter._map_source_events

    def mutate_private(documents: list[Any]) -> Any:
        events, private_events, authenticated = real_map(documents)
        metadata = private_events[0].to_dict()["metadata"]
        metadata["localai_contracts_event"]["metadata"]["numeric_sentinel"] = 1
        private_events[0] = replace(private_events[0], metadata=metadata)
        return events, private_events, authenticated

    monkeypatch.setattr(adapter, "_map_source_events", mutate_private)
    with pytest.raises(ValueError, match="did not round trip exactly"):
        adapter.handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [event.to_dict()]},
        )


def test_wire_compile_rejects_false_projection_disposition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_project = LocalAIContractsAdapter._project_bundle

    def mutate_policy_version(self: Any, **kwargs: Any) -> Any:
        projected = real_project(self, **kwargs)
        document = projected.to_dict()
        document["policy_identity"]["version"] = "mutated-private-version"
        return contracts.ContextBundle.from_dict(document)

    monkeypatch.setattr(
        LocalAIContractsAdapter,
        "_project_bundle",
        mutate_policy_version,
    )
    with pytest.raises(ValueError, match="projection disposition is inconsistent"):
        LocalAIContractsAdapter().handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [_event().to_dict()]},
        )


def test_wire_compile_does_not_serialize_discarded_conversion_audit() -> None:
    limits = contracts.ParseLimits(max_bytes=4_096)
    adapter = LocalAIContractsAdapter(limits=limits)
    event = _event("constraint: Keep the database stable.")
    payload = {"source_events": [event.to_dict()]}

    result = adapter.handle(CONTEXT_COMPILE_OPERATION, payload)

    assert len(contracts.canonical_bytes(payload)) < limits.max_bytes
    assert len(contracts.canonical_bytes(result)) < limits.max_bytes
    assert "conversion_audit" not in result


def test_conversion_audit_rejects_missing_full_source_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_project = LocalAIContractsAdapter._project_bundle

    def omit_full_source(self: Any, **kwargs: Any) -> Any:
        projected = real_project(self, **kwargs)
        document = projected.to_dict()
        source_provenance = next(
            item
            for item in document["provenance"]
            if item["transform"] == "exact_source_event_projection"
        )
        span_id = source_provenance["item_id"]
        document["untrusted_retrieved_spans"] = [
            span
            for span in document["untrusted_retrieved_spans"]
            if span["span_id"] != span_id
        ]
        document["provenance"] = [
            item for item in document["provenance"] if item["item_id"] != span_id
        ]
        return contracts.ContextBundle.from_dict(document)

    monkeypatch.setattr(LocalAIContractsAdapter, "_project_bundle", omit_full_source)
    adapter = LocalAIContractsAdapter()
    with pytest.raises(ValueError, match="one exact full source span"):
        adapter.compile_with_conversion_audit([_event()])


def test_token_accounting_covers_actual_projected_components_only() -> None:
    event = _event("constraint: Café stays PostgreSQL.")

    def connector_factory() -> LocalAIConnector:
        return LocalAIConnector(
            token_counter=ExactTokenCounterAdapter(
                "test-tokenizer@1",
                lambda text: len(text.encode("utf-8")),
            )
        )

    bundle = contracts.ContextBundle.from_dict(
        LocalAIContractsAdapter(
            connector_factory=connector_factory,
            authority_verifier=lambda event: AuthenticatedAuthority("test-host"),
        ).handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [event.to_dict()]},
        )
    )
    accounting = bundle.token_accounting
    assert accounting["kind"] == "exact"
    assert "tokenizer_identity_sha256=" in accounting["method"]
    assert "test-tokenizer@1" not in accounting["method"]
    assert accounting["trusted_tokens"] == sum(
        len(span["content"].encode("utf-8"))
        for span in bundle.trusted_active_memory
    )
    assert accounting["retrieved_tokens"] == sum(
        len(span["content"].encode("utf-8"))
        for span in bundle.untrusted_retrieved_spans
    )
    assert accounting["total_tokens"] == (
        accounting["trusted_tokens"] + accounting["retrieved_tokens"]
    )
    assert all(
        span["token_count"] == len(span["content"].encode("utf-8"))
        for span in bundle.trusted_active_memory + bundle.untrusted_retrieved_spans
    )


def test_estimated_accounting_and_protected_overflow_remain_explicit() -> None:
    event = _event("constraint: " + "retain this requirement " * 20)

    def connector_factory() -> LocalAIConnector:
        return LocalAIConnector(policy=CompilationPolicy(token_budget=1))

    bundle = contracts.ContextBundle.from_dict(
        LocalAIContractsAdapter(
            connector_factory=connector_factory,
            authority_verifier=lambda event: AuthenticatedAuthority("test-host"),
        ).handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [event.to_dict()]},
        )
    )
    assert bundle.token_accounting["kind"] == "estimated"
    assert "estimated" in bundle.token_accounting["method"]
    assert bundle.overflow == {
        "occurred": True,
        "dropped_items": 0,
        "dropped_tokens": None,
        "reason": "private_protected_budget_overflow_retained",
    }


def test_private_json_seam_uses_a2_bounded_encoder_with_explicit_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LocalAIContractsAdapter()
    real_bounded = contracts.bounded_canonical_bytes
    observed_limits: list[Any] = []

    def bounded(value: Any, *, limits: Any) -> bytes:
        observed_limits.append(limits)
        return real_bounded(value, limits=limits)

    def unbounded(_value: Any) -> bytes:
        raise AssertionError("unbounded canonical_bytes was used")

    monkeypatch.setattr(contracts, "bounded_canonical_bytes", bounded)
    monkeypatch.setattr(contracts, "canonical_bytes", unbounded)

    assert adapter._bounded_json({"value": "bounded"}, label="test") == {
        "value": "bounded"
    }
    assert observed_limits == [adapter.limits]


def test_private_json_seam_rejects_custom_containers_before_methods() -> None:
    calls: list[str] = []

    class HostileDict(dict[Any, Any]):
        def __len__(self) -> int:
            calls.append("len")
            return super().__len__()

        def __iter__(self) -> Any:
            calls.append("iter")
            return super().__iter__()

        def items(self) -> Any:
            calls.append("items")
            return super().items()

    payload = HostileDict(source_events=[_event().to_dict()])
    with pytest.raises(contracts.CanonicalizationError):
        LocalAIContractsAdapter().handle(CONTEXT_COMPILE_OPERATION, payload)
    assert calls == []


def test_private_json_seam_rejects_huge_integer_before_decimal_conversion() -> None:
    adapter = LocalAIContractsAdapter(
        limits=contracts.ParseLimits(max_bytes=512),
    )
    with pytest.raises(contracts.ParseLimitError):
        adapter._bounded_json(
            {"value": 1 << 1_000_000},
            label="huge integer",
        )


def test_projected_bundle_is_bounded_before_full_serialization() -> None:
    adapter = LocalAIContractsAdapter(
        limits=contracts.ParseLimits(max_bytes=1_024),
    )
    event = _event("constraint: Keep the database stable.")
    payload = {"source_events": [event.to_dict()]}
    assert len(contracts.canonical_bytes(payload)) < adapter.limits.max_bytes
    with pytest.raises(contracts.ParseLimitError):
        adapter.handle(CONTEXT_COMPILE_OPERATION, payload)


def test_in_process_request_server_uses_the_adapter_limits() -> None:
    limits = contracts.ParseLimits(max_bytes=4_096)
    adapter = LocalAIContractsAdapter(limits=limits)
    assert adapter._request_server.limits is limits
    assert adapter.handle_request(_handshake_request(adapter)).ok
    oversized = _compile_request(_event("x" * 5_000))
    response = adapter.handle_request(oversized)
    assert not response.ok
    assert _error(response).code == "request_failed"


def test_ndjson_reader_oserror_propagates_without_protocol_output() -> None:
    class FailingReader:
        def readline(self, _size: int) -> bytes:
            raise OSError("injected reader failure")

    writer = io.BytesIO()
    with pytest.raises(OSError, match="injected reader failure"):
        LocalAIContractsAdapter().serve_ndjson(FailingReader(), writer)
    assert writer.getvalue() == b""


def test_ndjson_text_reader_is_fatal_without_protocol_output() -> None:
    class TextReader:
        def readline(self, _size: int) -> str:
            return "{}\n"

    writer = io.BytesIO()
    with pytest.raises(contracts.ProtocolError):
        LocalAIContractsAdapter().serve_ndjson(TextReader(), writer)
    assert writer.getvalue() == b""


@pytest.mark.parametrize("write_result", [None, 0])
def test_ndjson_incomplete_or_noninteger_write_is_fatal(
    write_result: int | None,
) -> None:
    class RejectingWriter:
        def __init__(self) -> None:
            self.write_calls = 0
            self.flush_calls = 0

        def write(self, data: bytes) -> int | None:
            assert type(data) is bytes
            self.write_calls += 1
            return write_result

        def flush(self) -> None:
            self.flush_calls += 1

    writer = RejectingWriter()
    handshake = _handshake_request(LocalAIContractsAdapter()).canonical_bytes()
    with pytest.raises(
        OSError,
        match="NDJSON writer did not accept the complete record",
    ):
        LocalAIContractsAdapter().serve_ndjson(
            io.BytesIO(handshake + b"\n"),
            writer,
        )
    assert writer.write_calls == 1
    assert writer.flush_calls == 0


def test_ndjson_text_writer_rejects_binary_protocol_record() -> None:
    writer = io.StringIO()
    handshake = _handshake_request(LocalAIContractsAdapter()).canonical_bytes()
    with pytest.raises(TypeError):
        LocalAIContractsAdapter().serve_ndjson(
            io.BytesIO(handshake + b"\n"),
            writer,
        )
    assert writer.getvalue() == ""


def test_oversized_ndjson_record_is_drained_once_then_resynchronizes() -> None:
    limits = contracts.ParseLimits(max_bytes=4_096)
    adapter = LocalAIContractsAdapter(limits=limits)
    oversized = b"x" * (limits.max_bytes + 1) + b"\n"
    handshake = _handshake_request(adapter).canonical_bytes() + b"\n"
    writer = io.BytesIO()

    assert adapter.serve_ndjson(io.BytesIO(oversized + handshake), writer) == 2
    lines = writer.getvalue().splitlines()
    assert len(lines) == 2
    first = contracts.ConnectorResponse.from_json(lines[0])
    second = contracts.ConnectorResponse.from_json(lines[1])
    assert not first.ok
    assert _error(first).code == "invalid_ndjson_request"
    assert second.ok
    assert second.operation == "connector.handshake"


def test_oversized_unterminated_ndjson_record_produces_one_response() -> None:
    limits = contracts.ParseLimits(max_bytes=4_096)
    writer = io.BytesIO()
    record = b"x" * (limits.max_bytes + 1)

    assert (
        LocalAIContractsAdapter(limits=limits).serve_ndjson(
            io.BytesIO(record),
            writer,
        )
        == 1
    )
    lines = writer.getvalue().splitlines()
    assert len(lines) == 1
    response = contracts.ConnectorResponse.from_json(lines[0])
    assert not response.ok
    assert _error(response).code == "invalid_ndjson_request"


def test_ndjson_record_beyond_drain_ceiling_is_fatal_without_output() -> None:
    limits = contracts.ParseLimits(max_bytes=4_096)
    writer = io.BytesIO()
    record = b"x" * (limits.max_bytes * 8 + 1) + b"\n"

    with pytest.raises(contracts.ProtocolError):
        LocalAIContractsAdapter(limits=limits).serve_ndjson(
            io.BytesIO(record),
            writer,
        )
    assert writer.getvalue() == b""


def test_in_process_payload_uses_the_same_strict_parse_limits() -> None:
    adapter = LocalAIContractsAdapter(
        limits=contracts.ParseLimits(max_bytes=512),
    )
    event = _event("x" * 600)
    with pytest.raises(contracts.ParseLimitError):
        adapter.handle(
            CONTEXT_COMPILE_OPERATION,
            {"source_events": [event.to_dict()]},
        )


@pytest.mark.parametrize(
    "record",
    [
        b'{"protocol_version":"1.0.0","protocol_version":"1.0.0"}\n',
        b'{"payload":{"value":NaN}}\n',
        b'{"payload":[]}',
        b"\xff\n",
    ],
)
def test_malformed_ndjson_is_one_bounded_canonical_failure(record: bytes) -> None:
    writer = io.BytesIO()
    assert LocalAIContractsAdapter().serve_ndjson(io.BytesIO(record), writer) == 1
    lines = writer.getvalue().splitlines()
    assert len(lines) == 1
    response = contracts.ConnectorResponse.from_json(lines[0])
    assert not response.ok
    assert response.request_id == "invalid-request"
    assert response.operation == "connector.invalid"
    assert _error(response).code == "invalid_ndjson_request"
    assert writer.getvalue() == response.canonical_bytes() + b"\n"


def test_phase0_probe_is_typed_and_all_22_non_inference_cases_pass() -> None:
    event = _event()
    adapter = LocalAIContractsAdapter()
    probe = adapter.build_phase0_probe([event])
    assert type(probe) is contracts.SubjectOperationProbe
    assert probe.purpose is contracts.SubjectOperationPurpose.CONTEXT_BUNDLE_COMPILE
    assert probe.operation == CONTEXT_COMPILE_OPERATION
    assert type(probe.expected_response) is contracts.ContextBundle

    report = contracts.assert_phase0_conformant(
        LocalAIContractsAdapter,
        operation_probe=probe,
        subject_name="loss-resistant-context-compiler",
    ).to_dict()
    assert report["passed_count"] == 22
    assert report["failed_count"] == 0
    assert report["inference_status"] == "not_run"
    assert report["observation_scope"] == "connector_transport_conformance"
