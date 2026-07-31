from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

from context_compiler import __version__

ROOT = Path(__file__).parents[1]
STABLE_DISTRIBUTION = "loss-resistant-context-compiler"


def project_configuration() -> dict:
    return tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )


def test_distribution_identity_matches_the_public_release_contract() -> None:
    configuration = project_configuration()
    project = configuration["project"]

    assert __version__ == "0.1.1a1"
    assert project["name"] == STABLE_DISTRIBUTION
    assert project["version"] == __version__
    assert project["requires-python"] == ">=3.11"
    assert project["dependencies"] == []
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["scripts"] == {
        "ctxc": "context_compiler.cli:main",
        "ctxc-localai-contracts": (
            "context_compiler.localai_contracts_connector:main"
        ),
    }
    assert project["urls"] == {
        "Repository": (
            "https://github.com/JamesNguyen42/"
            "loss-resistant-context-compiler"
        ),
        "Issues": (
            "https://github.com/JamesNguyen42/"
            "loss-resistant-context-compiler/issues"
        ),
    }
    assert configuration["build-system"]["requires"] == [
        "setuptools>=77",
        "wheel>=0.41",
    ]
    assert configuration["build-system"]["build-backend"] == (
        "_ctxc_build_backend"
    )
    assert configuration["build-system"]["backend-path"] == ["."]


def test_schema_install_path_matches_the_distribution_and_all_schemas_parse() -> None:
    configuration = project_configuration()
    data_files = configuration["tool"]["setuptools"]["data-files"]

    assert data_files == {
        "share/loss-resistant-context-compiler/schemas": [
            "schemas/*.json"
        ]
    }
    schemas = sorted((ROOT / "schemas").glob("*.json"))
    assert len(schemas) == 25
    schema_ids: dict[str, str] = {}
    for schema in schemas:
        decoded = json.loads(schema.read_text(encoding="utf-8"))
        assert isinstance(decoded, dict)
        assert decoded.get("type") == "object"
        schema_ids[schema.name] = decoded["$id"]

    assert schema_ids["trust-manifest.schema.json"].startswith(
        "https://example.invalid/loss-resistant-context-compiler/"
    )
    historical_schema_names = {
        "compiled-memory.schema.json",
        "model-extraction.schema.json",
        "model-extraction-literal.schema.json",
        "redaction-report.schema.json",
        "source-archive-entry.schema.json",
        "source-archive-report.schema.json",
        "source-event.schema.json",
    }
    assert all(
        schema_ids[name].startswith(
            "https://example.invalid/lossless-context-compiler/"
        )
        for name in historical_schema_names
    )
    assert all(
        schema_id.startswith(
            "https://example.invalid/loss-resistant-context-compiler/"
        )
        for name, schema_id in schema_ids.items()
        if name not in historical_schema_names
    )


def test_release_documents_freeze_name_versioning_and_support_boundaries() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    release_policy = (
        ROOT / "docs" / "RELEASE_POLICY.md"
    ).read_text(encoding="utf-8")
    support = (ROOT / "SUPPORT.md").read_text(encoding="utf-8")
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "## Unreleased" in changelog
    assert f"## {__version__} - " in changelog
    assert STABLE_DISTRIBUTION in changelog
    assert "Semantic Versioning" in release_policy
    assert "No artifact, source-archive, or benchmark schema was renamed" in (
        " ".join(release_policy.split())
    )
    assert "| CPython 3.11 | Supported |" in support
    assert "| CPython 3.13 | Supported |" in support
    assert "No package index release is currently claimed." in " ".join(
        support.split()
    )
    assert {
        "include CHANGELOG.md",
        "include requirements-build.lock",
        "include SUPPORT.md",
        "recursive-include benchmarks *.json *.md *.py",
        "recursive-include conformance *.jsonl *.py",
        "recursive-include docs *.json *.md",
    } <= set(manifest.splitlines())
    assert manifest.splitlines().count("exclude setup.cfg") == 1
    assert not (ROOT / "setup.cfg").exists()


def test_root_build_lock_is_the_reviewed_canonical_input_set() -> None:
    payload = (ROOT / "requirements-build.lock").read_bytes()

    assert len(payload) == 666
    assert payload.endswith(b"\n")
    assert b"\r" not in payload
    assert len(payload.splitlines()) == 7
    assert hashlib.sha256(payload).hexdigest() == (
        "243f3ab977d82c04968cf4ea6474b7ef79c060d485aa3a3d67a383d1ef6fbbfe"
    )
