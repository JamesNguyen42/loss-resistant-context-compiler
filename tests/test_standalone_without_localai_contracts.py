from __future__ import annotations

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


def test_core_metadata_has_no_required_sibling_or_forbidden_project_imports() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]

    assert project["dependencies"] == []
    assert project.get("optional-dependencies", {}).get("unified") == [
        "localai-contracts==0.2.0a1"
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
