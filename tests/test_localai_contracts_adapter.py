from __future__ import annotations

import hashlib
import importlib.util
import io
import marshal
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from context_compiler import CompilationPolicy, ExactTokenCounterAdapter, LocalAIConnector
from context_compiler.localai_contracts_adapter import (
    CONTEXT_COMPILE_OPERATION,
    LOCALAI_CONTRACTS_PROTOCOL_VERSION,
    LOCALAI_CONTRACTS_SOURCE_COMMIT,
    LOCALAI_CONTRACTS_VERSION,
    LOCALAI_CONTRACTS_WHEEL_SHA256,
    AuthenticatedAuthority,
    LocalAIContractsAdapter,
)

contracts = pytest.importorskip(
    "localai_contracts",
    reason="exact optional localai-contracts wheel is not installed",
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_INSTALL_ROOT = Path(contracts.__file__).resolve().parent.parent
VALIDATION_ERROR = (
    "localai-contracts 0.2.0a2 failed optional-adapter validation"
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


def _assert_install_rejected_before_marker(
    install_root: Path,
    marker: Path,
) -> None:
    script = f"""
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from context_compiler.localai_contracts_adapter import (
    LocalAIContractsAdapter,
    LocalAIContractsUnavailableError,
)
try:
    LocalAIContractsAdapter()
except LocalAIContractsUnavailableError as exc:
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
