from __future__ import annotations

import importlib.util
import marshal
import os
import subprocess
import sys
import tomllib
from pathlib import Path

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
    sys.modules["localai_contracts"] = poison
    try:
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
    finally:
        del sys.modules["localai_contracts"]
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
