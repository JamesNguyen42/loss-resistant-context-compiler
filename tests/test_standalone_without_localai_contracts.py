from __future__ import annotations

import dis
import importlib.util
import marshal
import os
import subprocess
import sys
import tomllib
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_normal_api_and_cli_work_when_localai_contracts_import_is_blocked() -> None:
    script = r'''
import builtins
import io
import json
import sys

real_import = builtins.__import__

def blocked_import(name, *args, **kwargs):
    if name == "localai_contracts" or name.startswith("localai_contracts."):
        raise AssertionError("standalone ctxc attempted to import localai_contracts")
    return real_import(name, *args, **kwargs)

builtins.__import__ = blocked_import

from context_compiler import ContextCompiler, LocalAIConnector, SourceRecord
from context_compiler.cli import main
from context_compiler.localai_contracts_adapter import LocalAIContractsAdapter

assert LocalAIContractsAdapter.__name__ == "LocalAIContractsAdapter"

source = SourceRecord.create(
    id="standalone",
    sequence=0,
    role="user",
    content="constraint: Leave the authentication flow alone.",
)
memory = ContextCompiler().compile([source])
assert memory.verification.passed
assert "Leave the authentication flow alone." in memory.to_prompt()
assert LocalAIConnector().capabilities()["optional_dependencies"] == {
    "localai_contracts_required": False
}

old_stdin = sys.stdin
old_stdout = sys.stdout
try:
    sys.stdin = io.StringIO(json.dumps({
        "id": "cli-source",
        "sequence": 0,
        "role": "user",
        "content": "goal: Preserve standalone behavior."
    }) + "\n")
    captured = io.StringIO()
    sys.stdout = captured
    status = main(["compile", "-", "--format", "prompt"])
finally:
    sys.stdin = old_stdin
    sys.stdout = old_stdout

assert status == 0
assert "Preserve standalone behavior." in captured.getvalue()
'''
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_optional_adapter_rejects_shadow_before_import_without_dependency(
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
    script = r'''
import sys
sys.path[:0] = [sys.argv[2], sys.argv[1]]
from context_compiler.localai_contracts_adapter import (
    LocalAIContractsAdapter,
    LocalAIContractsUnavailableError,
)
try:
    LocalAIContractsAdapter()
except LocalAIContractsUnavailableError as exc:
    assert str(exc) == (
        "localai-contracts 0.2.0a2 failed optional-adapter validation"
    )
else:
    raise AssertionError("shadow package was accepted")
assert "localai_contracts" not in sys.modules
'''
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            script,
            str(ROOT / "src"),
            str(shadow_root),
            str(marker),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not marker.exists()


def test_optional_adapter_bytecode_cache_matches_verified_source(
    tmp_path: Path,
) -> None:
    from context_compiler.localai_contracts_adapter import _validate_bytecode_cache

    source = tmp_path / "checked.py"
    source_bytes = b"value = 1\n"
    source.write_bytes(source_bytes)
    cache = tmp_path / "checked.pyc"
    code = compile(
        source_bytes,
        str(source),
        "exec",
        dont_inherit=True,
        optimize=0,
    )
    header = (
        importlib.util.MAGIC_NUMBER
        + (0).to_bytes(4, "little")
        + (0).to_bytes(8, "little")
    )
    cache.write_bytes(header + marshal.dumps(code))

    _validate_bytecode_cache(
        cache,
        source=source_bytes,
        source_path=source,
        optimize=0,
    )

    forged = compile(
        b"value = 2\n",
        str(source),
        "exec",
        dont_inherit=True,
        optimize=0,
    )
    cache.write_bytes(header + marshal.dumps(forged))
    try:
        _validate_bytecode_cache(
            cache,
            source=source_bytes,
            source_path=source,
            optimize=0,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("forged bytecode cache was accepted")


def test_optional_adapter_accepts_current_slice_constant_cache(
    tmp_path: Path,
) -> None:
    from context_compiler.localai_contracts_adapter import _validate_bytecode_cache

    source = tmp_path / "slice-constant.py"
    source_bytes = b"value = subject[1:2]\n"
    source.write_bytes(source_bytes)
    code = compile(
        source_bytes,
        str(source),
        "exec",
        dont_inherit=True,
        optimize=0,
    )
    if sys.version_info >= (3, 14):
        assert any(type(constant) is slice for constant in code.co_consts)
    cache = tmp_path / "slice-constant.pyc"
    cache.write_bytes(
        importlib.util.MAGIC_NUMBER
        + (0).to_bytes(4, "little")
        + (0).to_bytes(8, "little")
        + marshal.dumps(code)
    )

    _validate_bytecode_cache(
        cache,
        source=source_bytes,
        source_path=source,
        optimize=0,
    )


@pytest.mark.skipif(
    sys.version_info < (3, 14),
    reason="slice constants require CPython 3.14+ marshal format 5",
)
def test_optional_adapter_rejects_changed_slice_constant(tmp_path: Path) -> None:
    from context_compiler.localai_contracts_adapter import _validate_bytecode_cache

    source = tmp_path / "changed-slice.py"
    source_bytes = b"value = subject[1:4:2]\n"
    source.write_bytes(source_bytes)
    expected = compile(
        source_bytes,
        str(source),
        "exec",
        dont_inherit=True,
        optimize=0,
    )
    slice_indexes = [
        index
        for index, constant in enumerate(expected.co_consts)
        if type(constant) is slice
    ]
    assert len(slice_indexes) == 1
    constants = list(expected.co_consts)
    constants[slice_indexes[0]] = slice(1, 4, 3)
    forged = expected.replace(co_consts=tuple(constants))
    cache = tmp_path / "changed-slice.pyc"
    cache.write_bytes(
        importlib.util.MAGIC_NUMBER
        + (0).to_bytes(4, "little")
        + (0).to_bytes(8, "little")
        + marshal.dumps(forged)
    )

    with pytest.raises(ValueError):
        _validate_bytecode_cache(
            cache,
            source=source_bytes,
            source_path=source,
            optimize=0,
        )


def test_optional_adapter_rejects_specialized_bytecode_cache(
    tmp_path: Path,
) -> None:
    from context_compiler.localai_contracts_adapter import _validate_bytecode_cache

    source = tmp_path / "specialized.py"
    source_bytes = b"value = subject.attribute\n"
    source.write_bytes(source_bytes)
    code = compile(
        source_bytes,
        str(source),
        "exec",
        dont_inherit=True,
        optimize=0,
    )
    specialized_opcode = dis._all_opmap["LOAD_ATTR_INSTANCE_VALUE"]
    baseline = bytearray(code.co_code)
    load_attr_offsets = [
        offset
        for offset in range(0, len(baseline), 2)
        if baseline[offset] == dis.opmap["LOAD_ATTR"]
    ]
    assert len(load_attr_offsets) == 1
    baseline[load_attr_offsets[0]] = specialized_opcode
    marshalled = marshal.dumps(code)
    assert marshalled.count(code.co_code) == 1
    forged = marshalled.replace(code.co_code, bytes(baseline), 1)
    cache = tmp_path / "specialized.pyc"
    cache.write_bytes(
        importlib.util.MAGIC_NUMBER
        + (0).to_bytes(4, "little")
        + (0).to_bytes(8, "little")
        + forged
    )

    try:
        _validate_bytecode_cache(
            cache,
            source=source_bytes,
            source_path=source,
            optimize=0,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("specialized bytecode cache was accepted")


def test_optional_adapter_rejects_changed_constant_sharing(
    tmp_path: Path,
) -> None:
    from context_compiler.localai_contracts_adapter import _validate_bytecode_cache

    source = tmp_path / "constant-sharing.py"
    source_bytes = b"value = ((1000, 2000), (1000, 2000))\n"
    source.write_bytes(source_bytes)
    expected = compile(
        source_bytes,
        str(source),
        "exec",
        dont_inherit=True,
        optimize=0,
    )
    matching_constants = [
        (index, constant)
        for index, constant in enumerate(expected.co_consts)
        if type(constant) is tuple
        and len(constant) == 2
        and constant[0] == (1000, 2000)
        and constant[1] == (1000, 2000)
    ]
    assert len(matching_constants) == 1
    constant_index, outer = matching_constants[0]
    assert outer[0] is outer[1]
    first = tuple(list(outer[0]))
    second = tuple(list(outer[1]))
    assert first == second
    assert first is not second
    forged_constants = list(expected.co_consts)
    forged_constants[constant_index] = (first, second)
    forged = expected.replace(co_consts=tuple(forged_constants))
    assert marshal.dumps(forged, 2) == marshal.dumps(expected, 2)
    cache = tmp_path / "constant-sharing.pyc"
    cache.write_bytes(
        importlib.util.MAGIC_NUMBER
        + (0).to_bytes(4, "little")
        + (0).to_bytes(8, "little")
        + marshal.dumps(forged)
    )

    try:
        _validate_bytecode_cache(
            cache,
            source=source_bytes,
            source_path=source,
            optimize=0,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("changed constant-sharing topology was accepted")


@pytest.mark.parametrize("forgery", ["unsupported", "over-depth"])
def test_optional_adapter_rejects_invalid_constant_graph(
    tmp_path: Path,
    forgery: str,
) -> None:
    from context_compiler.localai_contracts_adapter import _validate_bytecode_cache

    source = tmp_path / f"{forgery}-constant.py"
    source_bytes = b"value = 1\n"
    source.write_bytes(source_bytes)
    expected = compile(
        source_bytes,
        str(source),
        "exec",
        dont_inherit=True,
        optimize=0,
    )
    if forgery == "unsupported":
        forged_constant: object = [1]
    else:
        forged_constant = 1
        for _depth in range(130):
            forged_constant = (forged_constant,)
    forged = expected.replace(co_consts=(forged_constant, *expected.co_consts[1:]))
    cache = tmp_path / f"{forgery}-constant.pyc"
    cache.write_bytes(
        importlib.util.MAGIC_NUMBER
        + (0).to_bytes(4, "little")
        + (0).to_bytes(8, "little")
        + marshal.dumps(forged)
    )

    with pytest.raises(ValueError):
        _validate_bytecode_cache(
            cache,
            source=source_bytes,
            source_path=source,
            optimize=0,
        )


def test_bytecode_worker_uses_darwin_prelimit_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import context_compiler.localai_contracts_adapter as adapter

    monkeypatch.setattr(adapter.sys, "platform", "darwin")

    command = adapter._bytecode_worker_command()

    assert command[:7] == (
        "/bin/sh",
        "-p",
        "-c",
        adapter._CONTRACTS_BYTECODE_DARWIN_PRELIMIT,
        adapter._CONTRACTS_BYTECODE_DARWIN_PRELIMIT_PROTOCOL,
        str(adapter._CONTRACTS_BYTECODE_DARWIN_ADDRESS_SPACE_MB * 1024),
        "--",
    )
    assert command[7] == sys.executable
    assert command[-2:] == ("-c", adapter._CONTRACTS_BYTECODE_WORKER)
    assert 'ulimit -S -H -v "$1"' in adapter._CONTRACTS_BYTECODE_DARWIN_PRELIMIT
    assert 'exec "$@"' in adapter._CONTRACTS_BYTECODE_DARWIN_PRELIMIT


@pytest.mark.parametrize(
    ("inherited_limit", "accepted"),
    [
        ((1024**4, 1024**4), True),
        ((1024**4 - 1, 1024**4), False),
        ((1024**4, 1024**4 - 1), False),
    ],
)
def test_bytecode_worker_requires_exact_darwin_address_space_limit(
    monkeypatch: pytest.MonkeyPatch,
    inherited_limit: tuple[int, int],
    accepted: bool,
) -> None:
    import context_compiler.localai_contracts_adapter as adapter

    observed_limits: list[tuple[int, tuple[int, int]]] = []
    fake_resource = types.ModuleType("resource")
    fake_resource.RLIMIT_AS = 1  # type: ignore[attr-defined]
    fake_resource.RLIMIT_CORE = 2  # type: ignore[attr-defined]
    fake_resource.RLIM_INFINITY = -1  # type: ignore[attr-defined]
    fake_resource.getrlimit = lambda _resource: inherited_limit  # type: ignore[attr-defined]
    fake_resource.setrlimit = (  # type: ignore[attr-defined]
        lambda resource, limit: observed_limits.append((resource, limit))
    )
    setup = adapter._CONTRACTS_BYTECODE_WORKER.split("\ndef frame", 1)[0]
    monkeypatch.setitem(sys.modules, "resource", fake_resource)
    monkeypatch.setattr(sys, "platform", "darwin")

    if accepted:
        exec(setup, {})
        assert observed_limits == [
            (fake_resource.RLIMIT_AS, (1024**4, 1024**4)),
            (fake_resource.RLIMIT_CORE, (0, 0)),
        ]
    else:
        with pytest.raises(SystemExit):
            exec(setup, {})
        assert observed_limits == []


def test_external_pycache_prefix_rejects_before_distribution_discovery(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "distribution-discovery-executed"
    script = r'''
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
import context_compiler.localai_contracts_adapter as adapter
def unexpected_distribution_discovery(*, name):
    assert name == "localai-contracts"
    Path(sys.argv[3]).write_text("executed", encoding="utf-8")
    return []
adapter.importlib.metadata.distributions = unexpected_distribution_discovery
sys.pycache_prefix = sys.argv[2]
try:
    adapter.LocalAIContractsAdapter()
except adapter.LocalAIContractsUnavailableError as exc:
    assert str(exc) == (
        "localai-contracts 0.2.0a2 failed optional-adapter validation"
    )
else:
    raise AssertionError("external bytecode cache prefix was accepted")
'''
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            script,
            str(ROOT / "src"),
            str(tmp_path / "external-cache"),
            str(marker),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not marker.exists()


def test_hostile_sys_modules_key_rejects_without_equality_hook(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "module-key-equality-executed"
    script = r'''
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from context_compiler.localai_contracts_adapter import (
    LocalAIContractsAdapter,
    LocalAIContractsUnavailableError,
)
class HostileKey:
    def __hash__(self):
        return hash("localai_contracts")
    def __eq__(self, _other):
        Path(sys.argv[2]).write_text("executed", encoding="utf-8")
        return False
key = HostileKey()
sys.modules[key] = object()
try:
    LocalAIContractsAdapter()
except LocalAIContractsUnavailableError as exc:
    assert str(exc) == (
        "localai-contracts 0.2.0a2 failed optional-adapter validation"
    )
else:
    raise AssertionError("hostile sys.modules key was accepted")
finally:
    del sys.modules[key]
'''
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            script,
            str(ROOT / "src"),
            str(marker),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not marker.exists()


def test_loaded_module_rejection_does_not_invoke_module_getattr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.machinery
    import importlib.util
    import types

    from context_compiler.localai_contracts_adapter import (
        _ContractsOrigin,
        _validate_loaded_modules,
    )

    package = tmp_path / "localai_contracts"
    package.mkdir()
    initializer = package / "__init__.py"
    initializer.write_text("", encoding="utf-8")
    loader = importlib.machinery.SourceFileLoader(
        "localai_contracts",
        str(initializer),
    )
    spec = importlib.util.spec_from_file_location(
        "localai_contracts",
        str(initializer),
        loader=loader,
        submodule_search_locations=[str(package)],
    )
    assert type(spec) is importlib.machinery.ModuleSpec
    marker = tmp_path / "module-getattr-executed"
    poison = types.ModuleType("localai_contracts")
    poison.__package__ = "localai_contracts"
    poison.__spec__ = spec
    poison.__loader__ = loader
    poison.__path__ = [str(package)]

    def module_getattr(_attribute: str) -> None:
        marker.write_text("executed", encoding="utf-8")
        raise AttributeError(_attribute)

    poison.__getattr__ = module_getattr
    with monkeypatch.context() as scoped_monkeypatch:
        scoped_monkeypatch.setitem(sys.modules, "localai_contracts", poison)
        try:
            _validate_loaded_modules(
                _ContractsOrigin(
                    package_directory=package,
                    initializer=initializer,
                )
            )
        except ValueError:
            pass
        else:
            raise AssertionError("preloaded poison was accepted")
    assert not marker.exists()


def test_source_loader_instance_override_is_rejected_without_execution(
    tmp_path: Path,
) -> None:
    import importlib.machinery

    from context_compiler.localai_contracts_adapter import _validate_source_loader

    source = tmp_path / "__init__.py"
    source.write_text("", encoding="utf-8")
    marker = tmp_path / "loader-override-executed"
    loader = importlib.machinery.SourceFileLoader(
        "localai_contracts",
        str(source),
    )

    def replaced_get_code(_fullname: str) -> None:
        marker.write_text("executed", encoding="utf-8")

    loader.get_code = replaced_get_code
    try:
        _validate_source_loader(
            loader,
            module_name="localai_contracts",
            expected_path=source,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("source loader instance override was accepted")
    assert not marker.exists()
    del loader.get_code

    class HostileKey:
        armed = False

        def __hash__(self) -> int:
            return hash("name")

        def __eq__(self, _other: object) -> bool:
            if self.armed:
                marker.write_text("executed", encoding="utf-8")
            return False

    key = HostileKey()
    vars(loader)[key] = object()
    key.armed = True
    try:
        _validate_source_loader(
            loader,
            module_name="localai_contracts",
            expected_path=source,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("non-string source loader state key was accepted")
    finally:
        key.armed = False
        del vars(loader)[key]
    assert not marker.exists()


def test_core_metadata_has_no_required_sibling_or_forbidden_project_imports() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]

    assert project["dependencies"] == []
    assert project.get("optional-dependencies", {}).get("unified") == [
        "localai-contracts==0.2.0a2"
    ]
    assert project["scripts"] == {
        "ctxc": "context_compiler.cli:main",
        "ctxc-localai-contracts": "context_compiler.localai_contracts_connector:main",
    }

    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "context_compiler").glob("*.py")
    ).casefold()
    for forbidden in (
        "zoomcache",
        "tokconductor",
        "vram_compiler",
        "expertpack",
    ):
        assert f"import {forbidden}" not in source_text
        assert f"from {forbidden}" not in source_text
