from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

import benchmarks.swebench as swebench
from benchmarks.swebench import (
    CANDIDATE_GENERATED_FIELDS,
    CANDIDATE_SOURCE_FIELDS,
    DEFAULT_SUITE,
    EVALUATOR_ONLY_FIELDS,
    SUITE_SCHEMA,
    TASK_INPUT_SCHEMA,
    SweBenchError,
    VerifiedSweBenchSource,
    decode_suite,
    decode_task_input,
    load_opaque_key,
    load_source_snapshot,
    load_suite,
    main,
    materialize_source_snapshot,
    task_input_document,
    verify_suite,
    verify_task_input_binding,
    write_task_input,
)


def _record(ordinal: int) -> dict[str, str]:
    suffix = str(ordinal)
    return {
        "repo": "example/project",
        "instance_id": f"example__project-{ordinal}",
        "base_commit": suffix * 40,
        "patch": f"GOLD_SOLUTION_{ordinal}",
        "test_patch": f"GOLD_TEST_PATCH_{ordinal}",
        "problem_statement": f"Fix public problem {ordinal} without gold.",
        "hints_text": "" if ordinal == 1 else "coordinator-only hint",
        "created_at": f"2024-01-0{ordinal}T00:00:00Z",
        "version": f"{ordinal}.0",
        "FAIL_TO_PASS": f'["GOLD_FAIL_{ordinal}"]',
        "PASS_TO_PASS": f'["GOLD_PASS_{ordinal}"]',
        "environment_setup_commit": chr(96 + ordinal) * 40,
        "difficulty": "<15 min fix",
    }


def _records() -> list[dict[str, str]]:
    return [_record(1), _record(2)]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _resign(document: dict, field: str) -> None:
    unsigned = copy.deepcopy(document)
    unsigned.pop(field, None)
    document[field] = _digest(unsigned)


def _fixture_suite_document(
    records: list[dict[str, str]],
    *,
    parquet_bytes: bytes = b"fixture parquet bytes",
) -> dict:
    document = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
    artifact = document["dataset"]["artifact"]
    artifact["byte_count"] = len(parquet_bytes)
    artifact["sha256"] = hashlib.sha256(parquet_bytes).hexdigest()
    snapshot_bytes = _canonical_bytes(records)
    source_hashes = [_digest(record) for record in records]
    candidate_hashes = [
        _digest({"problem_statement": record["problem_statement"]})
        for record in records
    ]
    snapshot = document["canonical_snapshot"]
    snapshot.update(
        {
            "byte_count": len(snapshot_bytes),
            "sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
            "row_count": len(records),
            "first_instance_id": records[0]["instance_id"],
            "last_instance_id": records[-1]["instance_id"],
            "ordered_instance_ids_sha256": _digest(
                [record["instance_id"] for record in records]
            ),
            "ordered_source_sha256s_sha256": _digest(source_hashes),
            "ordered_candidate_input_sha256s_sha256": _digest(
                candidate_hashes
            ),
            "record_binding_fields": [
                "ordinal",
                "instance_id",
                "source_sha256",
                "candidate_input_sha256",
            ],
            "record_bindings": [
                [
                    ordinal,
                    record["instance_id"],
                    source_hashes[ordinal],
                    candidate_hashes[ordinal],
                ]
                for ordinal, record in enumerate(records)
            ],
        }
    )
    document["selection"].update(
        {
            "population_count": len(records),
            "selected_count": len(records),
            "ordered_selected_instance_ids_sha256": snapshot[
                "ordered_instance_ids_sha256"
            ],
        }
    )
    _resign(document, "suite_sha256")
    return document


def _fixture_suite(
    records: list[dict[str, str]] | None = None,
    *,
    parquet_bytes: bytes = b"fixture parquet bytes",
):
    return decode_suite(
        _fixture_suite_document(records or _records(), parquet_bytes=parquet_bytes)
    )


def _snapshot(
    tmp_path: Path,
    records: list[dict[str, str]] | None = None,
    *,
    suite=None,
):
    selected_records = records or _records()
    selected_suite = suite or _fixture_suite(selected_records)
    path = tmp_path / "source.json"
    path.write_bytes(_canonical_bytes(selected_records))
    return selected_suite, path, load_source_snapshot(path, selected_suite)


def test_committed_suite_pins_real_verified_source_without_claiming_a_score() -> None:
    suite = load_suite()
    summary = verify_suite()

    assert suite.document["schema"] == SUITE_SCHEMA
    assert suite.document["dataset"]["id"] == "SWE-bench/SWE-bench_Verified"
    assert suite.document["dataset"]["revision"] == (
        "91aa3ed51b709be6457e12d00300a6a596d4c6a3"
    )
    assert suite.source_artifact_sha256 == (
        "43ed5a3d1d98da36472c1ade65ddd2085d7b4ff694fcaf6a023a07c5c1f32f21"
    )
    assert suite.snapshot_sha256 == (
        "e1b70254514c107a92a37514ee94faae646baed60f97bd535097bb910d042df5"
    )
    assert suite.row_count == 500
    bindings = suite.document["canonical_snapshot"]["record_bindings"]
    assert len(bindings) == 500
    assert bindings[0] == (
        0,
        "astropy__astropy-12907",
        "bcadb9e2ee9c01d1951516eeb31a5864abb90adc8e3bedc5419ce4eb414517db",
        "3dfdf9e2e46ecfb17d17ee175a0e43cdafe6670cced76aaaa94a360914f21f12",
    )
    assert bindings[-1][0:3] == (
        499,
        "sympy__sympy-24661",
        "40d08558d55ddbc6fdfc2b8469f876f0410ad8d8b8fdb886a375f6a2eba69e75",
    )
    assert suite.document["dataset"]["card"]["declared_license_spdx"] is None
    assert summary["candidate_gold_fields_excluded"] is True
    assert summary["model_evaluation_executed"] is False
    assert summary["external_score_generated"] is False
    assert summary["claim_ready"] is False


def test_suite_requires_exhaustive_allowlist_partition_and_self_hash() -> None:
    document = _fixture_suite_document(_records())
    document["projection"]["candidate_source_fields"] = [
        "problem_statement",
        "patch",
    ]
    _resign(document, "suite_sha256")
    with pytest.raises(SweBenchError, match="field policy"):
        decode_suite(document)

    document = _fixture_suite_document(_records())
    document["suite_sha256"] = "0" * 64
    with pytest.raises(SweBenchError, match="suite_sha256 mismatch"):
        decode_suite(document)

    document = _fixture_suite_document(_records())
    document["dataset"]["id"] = "different/identity"
    _resign(document, "suite_sha256")
    with pytest.raises(SweBenchError, match="does not match dataset.id"):
        decode_suite(document)

    document = _fixture_suite_document(_records())
    document["dataset"]["revision_date"] = "2026-02-31"
    _resign(document, "suite_sha256")
    with pytest.raises(SweBenchError, match="real YYYY-MM-DD"):
        decode_suite(document)

    document = _fixture_suite_document(_records())
    document["canonical_snapshot"]["record_bindings"][0][2] = "0" * 64
    _resign(document, "suite_sha256")
    with pytest.raises(SweBenchError, match="record_bindings.*source_sha256"):
        decode_suite(document)


def test_source_snapshot_loads_exact_order_and_rejects_mutation(tmp_path: Path) -> None:
    records = _records()
    suite, path, source = _snapshot(tmp_path, records)

    assert [record["instance_id"] for record in source.records] == [
        "example__project-1",
        "example__project-2",
    ]

    path.write_bytes(_canonical_bytes(list(reversed(records))))
    with pytest.raises(SweBenchError, match="SHA-256 mismatch"):
        load_source_snapshot(path, suite)


@pytest.mark.parametrize("mutation", ["missing", "unknown", "duplicate"])
def test_source_snapshot_rejects_invalid_record_sets(
    tmp_path: Path,
    mutation: str,
) -> None:
    records = _records()
    if mutation == "missing":
        records[0].pop("patch")
    elif mutation == "unknown":
        records[0]["surprise"] = "value"
    else:
        records[1]["instance_id"] = records[0]["instance_id"]
        with pytest.raises(SweBenchError, match="duplicate IDs"):
            _fixture_suite(records)
        return
    suite = _fixture_suite(records)
    path = tmp_path / "source.json"
    path.write_bytes(_canonical_bytes(records))

    with pytest.raises(SweBenchError, match="fields are invalid"):
        load_source_snapshot(path, suite)


def test_hidden_mutation_changes_source_binding_but_not_candidate_projection() -> None:
    original = _records()
    hidden_changed = copy.deepcopy(original)
    hidden_changed[0]["patch"] = "DIFFERENT_GOLD_SOLUTION"
    public_changed = copy.deepcopy(original)
    public_changed[0]["problem_statement"] += " Public correction."

    original_suite = _fixture_suite(original)
    hidden_suite = _fixture_suite(hidden_changed)
    public_suite = _fixture_suite(public_changed)
    original_snapshot = original_suite.document["canonical_snapshot"]
    hidden_snapshot = hidden_suite.document["canonical_snapshot"]
    public_snapshot = public_suite.document["canonical_snapshot"]

    assert (
        original_snapshot["ordered_source_sha256s_sha256"]
        != hidden_snapshot["ordered_source_sha256s_sha256"]
    )
    assert (
        original_snapshot["ordered_candidate_input_sha256s_sha256"]
        == hidden_snapshot["ordered_candidate_input_sha256s_sha256"]
    )
    assert (
        original_snapshot["ordered_candidate_input_sha256s_sha256"]
        != public_snapshot["ordered_candidate_input_sha256s_sha256"]
    )


def test_projection_is_deterministic_allowlisted_and_gold_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite, _path, source = _snapshot(tmp_path)
    key = bytes(range(1, 33))
    first = task_input_document(source, opaque_key=key)
    for name in ("HF_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, f"poison-{name}")
    second = task_input_document(source, opaque_key=key)

    assert first == second
    assert first["schema"] == TASK_INPUT_SCHEMA
    assert first["candidate_payload_fields"] == list(
        CANDIDATE_GENERATED_FIELDS + CANDIDATE_SOURCE_FIELDS
    )
    assert all(set(task) == {"opaque_task_id", "problem_statement"} for task in first["tasks"])
    assert all(task["opaque_task_id"].startswith("task-") for task in first["tasks"])
    serialized = _canonical_bytes(first)
    for canary in (
        b"GOLD_SOLUTION_1",
        b"GOLD_TEST_PATCH_1",
        b"GOLD_FAIL_1",
        b"GOLD_PASS_1",
        b"example__project-1",
        b"coordinator-only hint",
    ):
        assert canary not in serialized
    for field in EVALUATOR_ONLY_FIELDS:
        assert f'"{field}"'.encode() not in serialized
    assert first["evidence_state"]["claim_ready"] is False
    decode_task_input(first, suite=suite)
    verify_task_input_binding(first, source, opaque_key=key)


def test_task_input_rejects_public_mutation_even_when_rehashed(tmp_path: Path) -> None:
    suite, _path, source = _snapshot(tmp_path)
    document = task_input_document(source, opaque_key=bytes(range(1, 33)))
    document["tasks"][0]["problem_statement"] = "substituted"
    _resign(document, "task_input_sha256")

    with pytest.raises(SweBenchError, match="problem statements"):
        decode_task_input(document, suite=suite)


def test_task_input_binding_rejects_opaque_id_substitution(tmp_path: Path) -> None:
    _suite, _path, source = _snapshot(tmp_path)
    key = bytes(range(1, 33))
    document = task_input_document(source, opaque_key=key)
    document["tasks"][0]["opaque_task_id"] = "task-" + "f" * 64
    _resign(document, "task_input_sha256")

    with pytest.raises(SweBenchError, match="bound source and opaque key"):
        verify_task_input_binding(document, source, opaque_key=key)


def test_projection_revalidates_publicly_constructible_source(tmp_path: Path) -> None:
    _suite, _path, source = _snapshot(tmp_path)
    forged_records = [dict(record) for record in source.records]
    forged_records[0]["instance_id"] = "example__project-999"
    forged = VerifiedSweBenchSource(
        suite=source.suite,
        records=tuple(forged_records),
        file_sha256=source.file_sha256,
        byte_count=source.byte_count,
    )

    with pytest.raises(SweBenchError, match="endpoint IDs"):
        task_input_document(forged, opaque_key=bytes(range(1, 33)))


def test_recursive_forbidden_key_scan_rejects_nested_gold() -> None:
    with pytest.raises(SweBenchError, match="forbidden evaluator field patch"):
        swebench._scan_forbidden_keys(
            [{"safe": {"nested": [{"patch": "gold"}]}}]
        )


def test_key_loader_requires_exact_nonzero_binary_key(tmp_path: Path) -> None:
    path = tmp_path / "key.bin"
    path.write_bytes(bytes(range(1, 33)))
    assert load_opaque_key(path) == bytes(range(1, 33))

    path.write_bytes(b"short")
    with pytest.raises(SweBenchError, match="exactly 32 bytes"):
        load_opaque_key(path)
    path.write_bytes(b"\0" * 32)
    with pytest.raises(SweBenchError, match="all zero"):
        load_opaque_key(path)


def test_materialize_verifies_source_bytes_and_refuses_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _records()
    parquet_bytes = b"fixture parquet bytes"
    suite = _fixture_suite(records, parquet_bytes=parquet_bytes)
    parquet_path = tmp_path / "source.parquet"
    parquet_path.write_bytes(parquet_bytes)
    output = tmp_path / "snapshot.json"
    monkeypatch.setattr(
        swebench,
        "_decode_parquet_bytes",
        lambda encoded, selected_suite: copy.deepcopy(records),
    )

    summary = materialize_source_snapshot(
        parquet_path,
        output,
        suite=suite,
    )
    assert summary["verified"] is True
    assert summary["claim_ready"] is False
    assert output.read_bytes() == _canonical_bytes(records)
    with pytest.raises(SweBenchError, match="already exists"):
        materialize_source_snapshot(parquet_path, output, suite=suite)

    parquet_path.write_bytes(b"changed source bytes")
    with pytest.raises(SweBenchError, match="byte count mismatch"):
        materialize_source_snapshot(
            parquet_path,
            tmp_path / "other.json",
            suite=suite,
        )


class _FakeField:
    def __init__(self, name: str, *, field_type: str = "string", nullable: bool = True):
        self.name = name
        self.type = field_type
        self.nullable = nullable


class _FakeColumn:
    def __init__(self, null_count: int = 0):
        self.null_count = null_count


class _FakeTable:
    def __init__(self, records: list[dict[str, str]]):
        self._records = records
        self.num_rows = len(records)
        self.column_names = list(swebench.SOURCE_FIELDS)
        self.schema = [_FakeField(name) for name in self.column_names]
        self._null_counts = {name: 0 for name in self.column_names}

    def column(self, name: str) -> _FakeColumn:
        return _FakeColumn(self._null_counts[name])

    def to_pylist(self) -> list[dict[str, str]]:
        return copy.deepcopy(self._records)


class _FakePyArrow:
    __version__ = "25.0.0"

    @staticmethod
    def BufferReader(encoded: bytes) -> bytes:
        return encoded


class _FakeParquet:
    def __init__(self, table: _FakeTable):
        self.table = table

    def read_table(self, reader: bytes) -> _FakeTable:
        assert reader == b"parquet"
        return self.table


def _install_fake_pyarrow(
    monkeypatch: pytest.MonkeyPatch,
    table: _FakeTable,
    *,
    version: str = "25.0.0",
) -> None:
    arrow = _FakePyArrow()
    arrow.__version__ = version
    parquet = _FakeParquet(table)

    def fake_import(name: str):
        return arrow if name == "pyarrow" else parquet

    monkeypatch.setattr(swebench.importlib, "import_module", fake_import)


def test_parquet_decoder_checks_optional_dependency_and_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite = _fixture_suite()

    def missing_import(name: str):
        raise ModuleNotFoundError("missing pyarrow", name="pyarrow")

    monkeypatch.setattr(swebench.importlib, "import_module", missing_import)
    with pytest.raises(SweBenchError, match="requires the optional pyarrow"):
        swebench._decode_parquet_bytes(b"parquet", suite)

    _install_fake_pyarrow(monkeypatch, _FakeTable(_records()), version="24.0.0")
    with pytest.raises(SweBenchError, match="must equal the pinned decoder"):
        swebench._decode_parquet_bytes(b"parquet", suite)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("row_count", "row count"),
        ("field_order", "field order"),
        ("field_type", "field schema"),
        ("nullable", "field schema"),
        ("null", "null values"),
    ],
)
def test_parquet_decoder_checks_physical_contract(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    message: str,
) -> None:
    suite = _fixture_suite()
    table = _FakeTable(_records())
    if mutation == "row_count":
        table.num_rows += 1
    elif mutation == "field_order":
        table.column_names = list(reversed(table.column_names))
    elif mutation == "field_type":
        table.schema[0].type = "large_string"
    elif mutation == "nullable":
        table.schema[0].nullable = False
    else:
        table._null_counts[table.column_names[0]] = 1
    _install_fake_pyarrow(monkeypatch, table)

    with pytest.raises(SweBenchError, match=message):
        swebench._decode_parquet_bytes(b"parquet", suite)


def test_parquet_decoder_success_and_decode_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite = _fixture_suite()
    table = _FakeTable(_records())
    _install_fake_pyarrow(monkeypatch, table)
    assert swebench._decode_parquet_bytes(b"parquet", suite) == _records()

    def fail_decode(_reader: bytes):
        raise RuntimeError("gold-bearing decoder detail")

    parquet = _FakeParquet(table)
    parquet.read_table = fail_decode
    arrow = _FakePyArrow()

    def fake_import(name: str):
        return arrow if name == "pyarrow" else parquet

    monkeypatch.setattr(swebench.importlib, "import_module", fake_import)
    with pytest.raises(SweBenchError, match="could not decode") as caught:
        swebench._decode_parquet_bytes(b"parquet", suite)
    assert "gold-bearing decoder detail" not in str(caught.value)


def test_write_reload_and_cli_are_deterministic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    records = _records()
    suite_document = _fixture_suite_document(records)
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(suite_document), encoding="utf-8")
    suite, source_path, source = _snapshot(
        tmp_path,
        records,
        suite=load_suite(suite_path),
    )
    document = task_input_document(source, opaque_key=bytes(range(1, 33)))
    output = tmp_path / "tasks.json"
    write_task_input(
        output,
        document,
        source=source,
        opaque_key=bytes(range(1, 33)),
    )
    decoded = swebench.load_task_input(output, suite=suite)
    assert decoded == document
    with pytest.raises(SweBenchError, match="already exists"):
        write_task_input(
            output,
            document,
            source=source,
            opaque_key=bytes(range(1, 33)),
        )

    assert main(["--suite", str(suite_path), "verify-suite"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["verified"] is True
    assert summary["claim_ready"] is False

    key_path = tmp_path / "key.bin"
    key_path.write_bytes(bytes(range(1, 33)))
    assert main(
        [
            "--suite",
            str(suite_path),
            "verify-task-input",
            "--input",
            str(output),
            "--snapshot",
            str(source_path),
            "--opaque-key-file",
            str(key_path),
        ]
    ) == 0
    bound_summary = json.loads(capsys.readouterr().out)
    assert bound_summary["source_binding_verified"] is True
