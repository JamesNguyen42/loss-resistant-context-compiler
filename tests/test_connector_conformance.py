from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conformance import run_connector_conformance as conformance_runner

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIRECTORY = ROOT / "schemas"
CONFORMANCE_DIRECTORY = ROOT / "conformance"

CONNECTOR_SCHEMA_FILENAMES = {
    "connector-request.schema.json",
    "connector-response.schema.json",
    "localai-source-event.schema.json",
    "context-bundle.schema.json",
    "incremental-checkpoint.schema.json",
    *{
        f"connector-{operation}-{side}.schema.json"
        for operation in (
            "capabilities",
            "ingest-source-events",
            "compile-memory",
            "render-context",
            "verify-memory",
            "inspect-memory",
        )
        for side in ("payload", "result")
    },
}

_STAT_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_nlink",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


def _stat_copy(observed: object, **changes: int) -> SimpleNamespace:
    values = {name: getattr(observed, name) for name in _STAT_FIELDS}
    values.update(changes)
    return SimpleNamespace(**values)


def _schema_nodes(value: object):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _schema_nodes(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _schema_nodes(nested)


def test_connector_schema_collections_are_explicitly_bounded() -> None:
    for filename in CONNECTOR_SCHEMA_FILENAMES:
        schema = json.loads(
            (SCHEMA_DIRECTORY / filename).read_text(encoding="utf-8")
        )
        for node in _schema_nodes(schema):
            if node.get("type") == "array":
                assert isinstance(node.get("maxItems"), int), (
                    filename,
                    node,
                )
                assert node["maxItems"] > 0


def test_connector_schema_graph_materializes_every_wire_contract() -> None:
    assert len(CONNECTOR_SCHEMA_FILENAMES) == 17
    for filename in CONNECTOR_SCHEMA_FILENAMES:
        schema = json.loads(
            (SCHEMA_DIRECTORY / filename).read_text(encoding="utf-8")
        )
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == (
            "https://example.invalid/loss-resistant-context-compiler/"
            f"{filename}"
        )
        assert schema["type"] == "object"

    request = json.loads(
        (SCHEMA_DIRECTORY / "connector-request.schema.json").read_text(
            encoding="utf-8"
        )
    )
    response = json.loads(
        (SCHEMA_DIRECTORY / "connector-response.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(request["oneOf"]) == 6
    assert len(response["oneOf"]) == 7
    assert request["additionalProperties"] is False
    assert response["additionalProperties"] is False


def test_connector_transcripts_cover_all_operations_and_65_negative_vectors() -> None:
    success = [
        json.loads(line)
        for line in (
            CONFORMANCE_DIRECTORY / "fixtures" / "golden-success.jsonl"
        )
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    negative = [
        json.loads(line)
        for line in (
            CONFORMANCE_DIRECTORY / "fixtures" / "golden-negative.jsonl"
        )
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert [row["request"]["operation"] for row in success] == [
        "capabilities",
        "ingest_source_events",
        "compile_memory",
        "render_context",
        "verify_memory",
        "inspect_memory",
    ]
    assert len(negative) == 65
    assert len({row["id"] for row in negative}) == 65


def test_conformance_fixture_reader_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "oversized.jsonl"
    fixture.write_text('{}\n', encoding="utf-8")
    monkeypatch.setattr(conformance_runner, "MAX_CONFORMANCE_BYTES", 2)

    with pytest.raises(ValueError, match="exceeds 2 bytes"):
        conformance_runner._load_jsonl(fixture)


def test_conformance_fixture_reader_rejects_nonfinite_numbers(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "nonfinite.jsonl"
    fixture.write_text('{"value":1e9999}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="must be finite"):
        conformance_runner._load_jsonl(fixture)


def test_bounded_reader_allows_descriptor_only_change_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tmp_path / "document.json"
    document.write_text('{"safe":true}', encoding="utf-8")
    real_fstat = conformance_runner.os.fstat
    calls = 0

    def descriptor_stat(descriptor: int) -> object:
        nonlocal calls
        observed = real_fstat(descriptor)
        calls += 1
        if calls == 2:
            return _stat_copy(
                observed, st_ctime_ns=observed.st_ctime_ns + 1
            )
        return observed

    monkeypatch.setattr(conformance_runner.os, "fstat", descriptor_stat)

    assert conformance_runner._read_bounded_text(document, label="document") == (
        '{"safe":true}'
    )


def test_bounded_reader_rejects_modification_time_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tmp_path / "document.json"
    document.write_text('{"safe":true}', encoding="utf-8")
    real_fstat = conformance_runner.os.fstat
    calls = 0

    def descriptor_stat(descriptor: int) -> object:
        nonlocal calls
        observed = real_fstat(descriptor)
        calls += 1
        if calls == 2:
            return _stat_copy(
                observed, st_mtime_ns=observed.st_mtime_ns + 1
            )
        return observed

    monkeypatch.setattr(conformance_runner.os, "fstat", descriptor_stat)

    with pytest.raises(ValueError, match="changed while reading"):
        conformance_runner._read_bounded_text(document, label="document")


def test_standalone_conformance_runner_proves_in_process_stdio_equivalence() -> None:
    completed = subprocess.run(
        [sys.executable, str(CONFORMANCE_DIRECTORY / "run_connector_conformance.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "golden_steps": 6,
        "negative_vectors": 65,
        "passed": True,
        "schemas": 17,
    }
