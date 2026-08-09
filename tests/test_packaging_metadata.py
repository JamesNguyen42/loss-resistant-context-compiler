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

    assert __version__ == "0.1.1a21"
    assert project["name"] == STABLE_DISTRIBUTION
    assert project["version"] == __version__
    assert project["requires-python"] == ">=3.11"
    assert project["classifiers"] == [
        "Development Status :: 3 - Alpha",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
    ]
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
    assert len(schemas) == 26
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


def test_materialized_retention_pack_is_explicit_package_data() -> None:
    configuration = project_configuration()

    assert configuration["tool"]["setuptools"]["package-data"] == {
        "context_compiler": ["data/materialized_retention_pack_v1.json"]
    }
    fixture = (
        ROOT
        / "src"
        / "context_compiler"
        / "data"
        / "materialized_retention_pack_v1.json"
    )
    assert fixture.is_file()
    payload = fixture.read_bytes()
    assert len(payload) == 192_498
    assert payload.endswith(b"\n")
    assert b"\r" not in payload
    assert hashlib.sha256(payload).hexdigest() == (
        "a17dc61a05ddb0d20811e8ec64c7a2da5f0262e98f24a734189550abb6f7f466"
    )
    value = json.loads(payload)
    assert value["schema"] == "ctxc-materialized-retention-pack-0.1"
    assert value["pack_id"] == "ctxc-materialized-retention-naturalistic-v1"
    assert len(value["cases"]) == 30
    assert value["split_policy"] == {
        "development_case_count": 6,
        "fixture_change_requires_new_pack_id": True,
        "group_disjoint": True,
        "heldout_case_count": 20,
        "name": "task-group-disjoint-4-6-20-v1",
        "result_tuning_prohibited": True,
        "train_case_count": 4,
    }


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
    assert "| CPython 3.14 | Supported |" in support
    assert "No package index release is currently claimed." in " ".join(
        support.split()
    )
    assert {
        "include CHANGELOG.md",
        "include requirements-build.lock",
        "include SUPPORT.md",
        "recursive-include benchmarks *.json *.md *.py",
        "recursive-include conformance *.json *.jsonl *.py",
        "recursive-include docs *.json *.md",
        "include src/context_compiler/data/materialized_retention_pack_v1.json",
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
