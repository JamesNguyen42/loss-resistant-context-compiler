from __future__ import annotations

import json
from pathlib import Path

import pytest

from context_compiler import (
    ARTIFACT_SCHEMA_COMPATIBILITY_SCHEMA,
    artifact_schema_registry,
    artifact_schema_support,
)
from context_compiler.cli import main


def test_registry_freezes_the_exact_reader_writer_window() -> None:
    assert artifact_schema_registry() == {
        "schema": ARTIFACT_SCHEMA_COMPATIBILITY_SCHEMA,
        "artifact_family": "compiled-memory",
        "current_version": "1.0",
        "readable_versions": ["1.0"],
        "writable_versions": ["1.0"],
        "versions": [
            {
                "version": "1.0",
                "status": "current",
                "readable": True,
                "writable": True,
                "compatible_omissions": [
                    "compiler_metadata.metrics",
                ],
            }
        ],
        "migration_policy": {
            "automatic": False,
            "silent": False,
            "available_migrations": [],
            "future_migration_requirements": [
                "preserve the original artifact",
                "require trusted source-bound replay",
                "record the origin artifact digest and migration identifier",
                "emit a new artifact digest",
            ],
        },
    }


def test_current_artifact_schema_is_readable_and_writable() -> None:
    assert artifact_schema_support("1.0") == {
        "schema": ARTIFACT_SCHEMA_COMPATIBILITY_SCHEMA,
        "artifact_schema_version": "1.0",
        "current_version": "1.0",
        "status": "current",
        "readable": True,
        "writable": True,
        "migration": {
            "available": False,
            "automatic": False,
            "target_version": None,
        },
        "reason": "current reader and writer support this artifact schema",
    }


def test_unknown_well_formed_schema_is_a_stable_unsupported_query() -> None:
    support = artifact_schema_support("2.0")

    assert support["status"] == "unsupported"
    assert support["readable"] is False
    assert support["writable"] is False
    assert support["migration"] == {
        "available": False,
        "automatic": False,
        "target_version": None,
    }
    assert support["reason"] == (
        "no reader, writer, or explicit migration is registered for this "
        "artifact schema"
    )


@pytest.mark.parametrize("version", [None, True, 1, 1.0, b"1.0"])
def test_non_string_schema_queries_are_rejected(version: object) -> None:
    with pytest.raises(TypeError, match="must be a string"):
        artifact_schema_support(version)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "version",
    ["", "1", "1.0 ", " 1.0", "01.0", "1.00", "v1.0", "1.0.0"],
)
def test_noncanonical_schema_queries_are_rejected(version: str) -> None:
    with pytest.raises(ValueError, match="canonical MAJOR.MINOR"):
        artifact_schema_support(version)


def test_registry_returns_fresh_mutable_containers() -> None:
    first = artifact_schema_registry()
    first["readable_versions"].append("forged")
    first["versions"][0]["compatible_omissions"].clear()

    second = artifact_schema_registry()
    assert second["readable_versions"] == ["1.0"]
    assert second["versions"][0]["compatible_omissions"] == [
        "compiler_metadata.metrics"
    ]


def test_cli_schema_registry_and_unsupported_query(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["schema"]) == 0
    registry = json.loads(capsys.readouterr().out)
    assert registry == artifact_schema_registry()

    assert main(["schema", "--artifact-version", "9.4"]) == 0
    support = json.loads(capsys.readouterr().out)
    assert support == artifact_schema_support("9.4")


def test_cli_schema_writes_atomically_to_an_output_path(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "schema-support.json"

    assert (
        main(
            [
                "schema",
                "--artifact-version",
                "1.0",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert json.loads(output.read_text(encoding="utf-8")) == artifact_schema_support(
        "1.0"
    )


def test_cli_schema_rejects_invalid_version_with_json_diagnostic(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "schema",
                "--artifact-version",
                "latest",
                "--error-format",
                "json",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.err)

    assert captured.out == ""
    assert diagnostic["command"] == "schema"
    assert diagnostic["category"] == "invalid_input"
    assert diagnostic["code"] == "invalid_value"
