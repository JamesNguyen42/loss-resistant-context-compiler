from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

import benchmarks.swebench_prediction as prediction_module
from benchmarks.swebench import (
    DEFAULT_SUITE,
    VerifiedSweBenchSource,
    decode_suite,
    load_source_snapshot,
    task_input_document,
)
from benchmarks.swebench_prediction import (
    CandidatePatchCapture,
    PredictionLedgerError,
    PredictionLedgerLimits,
    RepositoryPreparationOutcome,
    RetainedCaptureEvidence,
    SystemIdentity,
    VerifiedPredictionLedger,
    build_prediction_ledger,
    decode_prediction_ledger,
    load_prediction_ledger,
    verify_prediction_ledger,
    write_prediction_ledger,
)
from benchmarks.swebench_repository import (
    PreparedSweBenchRepository,
    prepare_repository_from_commit,
    prepare_task_repository,
    verify_local_bare_mirror,
)

_OPAQUE_KEY = bytes(range(1, 33))


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", errors="strict")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _resign(document: dict[str, object], field: str) -> None:
    unsigned = copy.deepcopy(document)
    unsigned.pop(field, None)
    document[field] = _digest(unsigned)


def _thaw(value: object) -> object:
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(key): _thaw(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(nested) for nested in value]
    return value


def _record(ordinal: int, *, base_commit: str | None = None) -> dict[str, str]:
    suffix = str(ordinal + 1)
    return {
        "repo": "example/project",
        "instance_id": f"example__project-{ordinal + 1}",
        "base_commit": base_commit or suffix * 40,
        "patch": f"GOLD_SOLUTION_{ordinal}",
        "test_patch": f"GOLD_TEST_PATCH_{ordinal}",
        "problem_statement": f"Fix public problem {ordinal} without gold.",
        "hints_text": "coordinator-only hint",
        "created_at": f"2024-01-{ordinal + 1:02d}T00:00:00Z",
        "version": f"{ordinal + 1}.0",
        "FAIL_TO_PASS": f'["GOLD_FAIL_{ordinal}"]',
        "PASS_TO_PASS": f'["GOLD_PASS_{ordinal}"]',
        "environment_setup_commit": chr(97 + ordinal) * 40,
        "difficulty": "<15 min fix",
    }


def _suite_document(records: list[dict[str, str]]) -> dict[str, object]:
    document = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
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
            "ordered_candidate_input_sha256s_sha256": _digest(candidate_hashes),
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


def _source_fixture(
    tmp_path: Path,
    *,
    records: list[dict[str, str]] | None = None,
) -> tuple[VerifiedSweBenchSource, dict[str, object]]:
    selected_records = records or [_record(index) for index in range(3)]
    suite = decode_suite(_suite_document(selected_records))
    snapshot = tmp_path / "source.json"
    snapshot.write_bytes(_canonical_bytes(selected_records))
    source = load_source_snapshot(snapshot, suite=suite)
    task_input = task_input_document(source, opaque_key=_OPAQUE_KEY)
    return source, task_input


def _assert_self_hash(document: dict[str, object], field: str) -> None:
    claimed = document[field]
    assert isinstance(claimed, str)
    unsigned = copy.deepcopy(document)
    unsigned.pop(field)
    assert claimed == _digest(unsigned)


def _git(cwd: Path, executable: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        [str(executable), *arguments],
        cwd=cwd,
        env={
            **os.environ,
            "GIT_AUTHOR_DATE": "2001-02-03T04:05:06Z",
            "GIT_COMMITTER_DATE": "2001-02-03T04:05:06Z",
        },
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(
            "fixture Git command failed: "
            f"{arguments!r}: {completed.stderr.decode('utf-8', errors='replace')}"
        )
    return completed.stdout


@dataclass(frozen=True, slots=True)
class _PredictionContext:
    source: VerifiedSweBenchSource
    task_input: dict[str, object]
    prepared: PreparedSweBenchRepository
    opaque_ids: tuple[str, ...]


@pytest.fixture(scope="module")
def prediction_context(tmp_path_factory: pytest.TempPathFactory) -> _PredictionContext:
    git_name = shutil.which("git")
    if git_name is None:
        pytest.skip("Git is required for the source-bound prediction fixture")
    git = Path(git_name).resolve()
    root = tmp_path_factory.mktemp("swebench-prediction")
    repository = root / "source"
    mirror_path = root / "mirror.git"
    _git(
        root,
        git,
        "init",
        "--object-format=sha1",
        "--initial-branch=main",
        str(repository),
    )
    for name, value in (
        ("user.name", "Prediction Fixture"),
        ("user.email", "fixture@example.invalid"),
        ("core.autocrlf", "false"),
        ("core.filemode", "true"),
        ("commit.gpgsign", "false"),
    ):
        _git(repository, git, "config", name, value)
    (repository / "module.py").write_bytes(b"VALUE = 1\n")
    _git(repository, git, "add", "--all")
    _git(repository, git, "commit", "-m", "fixture")
    commit = _git(repository, git, "rev-parse", "HEAD").decode("ascii").strip()
    _git(root, git, "clone", "--mirror", "--no-local", str(repository), str(mirror_path))
    _git(
        mirror_path,
        git,
        "remote",
        "set-url",
        "origin",
        "https://github.com/example/project.git",
    )

    records = [_record(index, base_commit=commit) for index in range(3)]
    source, task_input = _source_fixture(root, records=records)
    mirror = verify_local_bare_mirror(mirror_path, "example/project", git)
    output_parent = root / "prepared"
    output_parent.mkdir()
    prepared = prepare_task_repository(source, 0, mirror, output_parent)
    opaque_ids = tuple(
        str(task["opaque_task_id"])
        for task in task_input["tasks"]
    )
    return _PredictionContext(
        source=source,
        task_input=task_input,
        prepared=prepared,
        opaque_ids=opaque_ids,
    )


def _system() -> SystemIdentity:
    return SystemIdentity(
        code_revision="1" * 40,
        repository_clean=True,
        model_name_or_path="fixture-agent",
        model_sha256="2" * 64,
        agent_sha256="3" * 64,
        prompt_sha256="4" * 64,
        tool_sha256="5" * 64,
        controller_sha256="6" * 64,
    )


def _preparations(
    context: _PredictionContext,
) -> tuple[RepositoryPreparationOutcome, ...]:
    return (
        RepositoryPreparationOutcome(
            opaque_task_id=context.opaque_ids[0],
            status="prepared",
            prepared=context.prepared,
        ),
        RepositoryPreparationOutcome(
            opaque_task_id=context.opaque_ids[1],
            status="refused",
            failure_code="fixture-refusal",
        ),
        RepositoryPreparationOutcome(
            opaque_task_id=context.opaque_ids[2],
            status="not-attempted",
        ),
    )


def _recorded_capture(
    opaque_task_id: str,
    model_patch: str,
) -> CandidatePatchCapture:
    encoded = model_patch.encode("utf-8", errors="strict")
    return CandidatePatchCapture(
        opaque_task_id=opaque_task_id,
        status="recorded",
        model_patch=model_patch,
        capture=RetainedCaptureEvidence(
            observed_bytes=len(encoded),
            captured_bytes=len(encoded),
            captured_sha256=hashlib.sha256(encoded).hexdigest(),
            complete=True,
        ),
    )


def _retained_bytes(payload: bytes, *, complete: bool = True) -> RetainedCaptureEvidence:
    return RetainedCaptureEvidence(
        observed_bytes=len(payload),
        captured_bytes=len(payload),
        captured_sha256=hashlib.sha256(payload).hexdigest(),
        complete=complete,
    )


def _build(
    context: _PredictionContext,
    output: Path,
    *,
    preparations: tuple[RepositoryPreparationOutcome, ...] | None = None,
    captures: tuple[CandidatePatchCapture, ...] | None = None,
    limits: PredictionLedgerLimits | None = None,
) -> VerifiedPredictionLedger:
    return build_prediction_ledger(
        context.source,
        context.task_input,
        opaque_key=_OPAQUE_KEY,
        preparations=preparations or _preparations(context),
        captures=(
            captures
            if captures is not None
            else (
                _recorded_capture(
                    context.opaque_ids[0],
                    "diff --git a/module.py b/module.py\n"
                    "--- a/module.py\n"
                    "+++ b/module.py\n"
                    "@@ -1 +1 @@\n"
                    "-VALUE = 1\n"
                    "+VALUE = 2\n",
                ),
            )
        ),
        system=_system(),
        jsonl_path=output,
        limits=limits,
    )


def _official_rows(ledger: VerifiedPredictionLedger) -> list[dict[str, object]]:
    encoded_lines = ledger.jsonl_path.read_bytes().splitlines(keepends=True)
    assert all(line.endswith(b"\n") for line in encoded_lines)
    rows = [json.loads(line) for line in encoded_lines]
    assert all(
        line == _canonical_bytes(row) + b"\n"
        for line, row in zip(encoded_lines, rows, strict=True)
    )
    return rows


def test_full_cohort_ledger_and_official_jsonl_are_deterministic(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    first = _build(prediction_context, tmp_path / "first.jsonl")
    second = _build(prediction_context, tmp_path / "second.jsonl")

    first_document = _thaw(first.document)
    second_document = _thaw(second.document)
    assert first_document == second_document
    assert first.jsonl_path.read_bytes() == second.jsonl_path.read_bytes()
    verified = verify_prediction_ledger(
        first,
        prediction_context.source,
        prediction_context.task_input,
        opaque_key=_OPAQUE_KEY,
        system=_system(),
    )
    assert _thaw(verified.document) == first_document

    serialized_ledger = _canonical_bytes(first_document)
    for opaque_id in prediction_context.opaque_ids:
        assert opaque_id.encode("ascii") in serialized_ledger
    assert isinstance(first_document, dict)
    assert [task["ordinal"] for task in first_document["tasks"]] == [0, 1, 2]

    official_rows = _official_rows(first)
    assert len(official_rows) == 3
    assert all(
        set(row) == {"instance_id", "model_name_or_path", "model_patch"}
        for row in official_rows
    )
    assert official_rows[0] == {
        "instance_id": "example__project-1",
        "model_name_or_path": "fixture-agent",
        "model_patch": (
            "diff --git a/module.py b/module.py\n"
            "--- a/module.py\n"
            "+++ b/module.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 1\n"
            "+VALUE = 2\n"
        ),
    }
    assert official_rows[1:] == [
        {
            "instance_id": "example__project-2",
            "model_name_or_path": "fixture-agent",
            "model_patch": None,
        },
        {
            "instance_id": "example__project-3",
            "model_name_or_path": "fixture-agent",
            "model_patch": None,
        },
    ]


def test_sparse_predictions_totalize_missing_and_unprepared_tasks(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    ledger = _build(prediction_context, tmp_path / "sparse.jsonl", captures=())
    document = _thaw(ledger.document)
    serialized = _canonical_bytes(document)

    assert [row["model_patch"] for row in _official_rows(ledger)] == [None, None, None]
    for opaque_id in prediction_context.opaque_ids:
        assert opaque_id.encode("ascii") in serialized
    assert isinstance(document, dict)
    assert len(document["tasks"]) == 3
    assert b"repository-prepared-no-prediction" in serialized
    assert b"repository-preparation-refused" in serialized
    assert b"repository-not-attempted" in serialized


def test_empty_patch_is_an_explicit_official_prediction(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    ledger = _build(
        prediction_context,
        tmp_path / "empty.jsonl",
        captures=(_recorded_capture(prediction_context.opaque_ids[0], ""),),
    )

    official = _official_rows(ledger)
    assert [row["model_patch"] for row in official] == ["", None, None]
    verify_prediction_ledger(
        ledger,
        prediction_context.source,
        prediction_context.task_input,
        opaque_key=_OPAQUE_KEY,
        system=_system(),
    )


@pytest.mark.parametrize(
    "patch",
    [
        "line one\r\nline two",
        "line one\nline two",
        "caf\N{LATIN SMALL LETTER E WITH ACUTE}",
        "cafe\N{COMBINING ACUTE ACCENT}",
        "\ufeffdiff",
        "diff\x00tail",
        "no-final-newline",
    ],
)
def test_patch_text_roundtrips_exact_utf8_without_normalization(
    prediction_context: _PredictionContext,
    tmp_path: Path,
    patch: str,
) -> None:
    suffix = hashlib.sha256(patch.encode("utf-8")).hexdigest()[:12]
    ledger = _build(
        prediction_context,
        tmp_path / f"utf8-{suffix}.jsonl",
        captures=(_recorded_capture(prediction_context.opaque_ids[0], patch),),
    )

    official = _official_rows(ledger)[0]
    assert official["model_patch"] == patch
    assert str(official["model_patch"]).encode("utf-8", errors="strict") == patch.encode(
        "utf-8", errors="strict"
    )


def test_no_runtime_paths_gold_or_opaque_key_are_serialized(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    ledger = _build(prediction_context, tmp_path / "privacy.jsonl")
    encoded = _canonical_bytes(_thaw(ledger.document))

    for forbidden in (
        str(ledger.jsonl_path).encode(),
        str(prediction_context.prepared.path).encode(),
        b"GOLD_SOLUTION",
        b"GOLD_TEST_PATCH",
        b"GOLD_FAIL",
        b"GOLD_PASS",
        _OPAQUE_KEY,
        _OPAQUE_KEY.hex().encode(),
        b"diff --git a/module.py b/module.py",
    ):
        assert forbidden not in encoded


@pytest.mark.parametrize(
    ("patch", "payload", "failure_code"),
    [
        (None, b"\xff", "invalid-utf8"),
        ("\ud800", "\ud800".encode("utf-8", errors="surrogatepass"), "unicode-surrogate"),
    ],
)
def test_invalid_patch_captures_are_retained_without_cohort_shrink(
    prediction_context: _PredictionContext,
    tmp_path: Path,
    patch: str | None,
    payload: bytes,
    failure_code: str,
) -> None:
    capture = CandidatePatchCapture(
        opaque_task_id=prediction_context.opaque_ids[0],
        status="invalid" if patch is None else "recorded",
        model_patch=patch,
        capture=_retained_bytes(payload),
        failure_code=failure_code if patch is None else None,
    )
    ledger = _build(
        prediction_context,
        tmp_path / f"{failure_code}.jsonl",
        captures=(capture,),
    )
    serialized = _canonical_bytes(_thaw(ledger.document))

    assert [row["model_patch"] for row in _official_rows(ledger)] == [None, None, None]
    assert hashlib.sha256(payload).hexdigest().encode("ascii") in serialized
    assert (
        failure_code.encode("ascii") in serialized
        or b"invalid-patch-text" in serialized
    )
    for opaque_id in prediction_context.opaque_ids:
        assert opaque_id.encode("ascii") in serialized


def test_oversized_patch_prefix_is_retained_without_an_official_line(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    retained = b"123456789"
    limits = PredictionLedgerLimits(max_patch_bytes=len(retained) - 1)
    capture = CandidatePatchCapture(
        opaque_task_id=prediction_context.opaque_ids[0],
        status="oversized",
        capture=RetainedCaptureEvidence(
            observed_bytes=len(retained),
            captured_bytes=len(retained),
            captured_sha256=hashlib.sha256(retained).hexdigest(),
            complete=True,
        ),
        failure_code="patch-byte-limit",
    )
    ledger = _build(
        prediction_context,
        tmp_path / "oversized.jsonl",
        captures=(capture,),
        limits=limits,
    )
    serialized = _canonical_bytes(_thaw(ledger.document))

    assert [row["model_patch"] for row in _official_rows(ledger)] == [None, None, None]
    assert hashlib.sha256(retained).hexdigest().encode("ascii") in serialized
    assert b"oversized-candidate-output" in serialized


def test_unprepared_recorded_capture_is_retained_but_not_published(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    patch = "diff --git a/a b/a\n"
    ledger = _build(
        prediction_context,
        tmp_path / "unprepared.jsonl",
        captures=(_recorded_capture(prediction_context.opaque_ids[1], patch),),
    )
    serialized = _canonical_bytes(_thaw(ledger.document))

    assert [row["model_patch"] for row in _official_rows(ledger)] == [None, None, None]
    assert hashlib.sha256(patch.encode()).hexdigest().encode("ascii") in serialized
    assert b"repository-preparation-refused" in serialized


def test_duplicate_and_unexpected_captures_are_protocol_evidence_not_rows(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    expected = prediction_context.opaque_ids[0]
    unexpected = "task-" + "f" * 64
    captures = (
        _recorded_capture(expected, "first"),
        _recorded_capture(expected, "duplicate"),
        _recorded_capture(unexpected, "unexpected"),
    )
    ledger = _build(
        prediction_context,
        tmp_path / "protocol-violations.jsonl",
        captures=captures,
    )
    serialized = _canonical_bytes(_thaw(ledger.document))

    assert [row["model_patch"] for row in _official_rows(ledger)] == [None, None, None]
    for opaque_id in prediction_context.opaque_ids:
        assert opaque_id.encode("ascii") in serialized
    token_digest = hashlib.sha256(
        b"bounded-opaque-id\0"
        + len(unexpected).to_bytes(8, "big", signed=False)
        + unexpected.encode("utf-8")
    ).hexdigest()
    assert unexpected.encode("ascii") not in serialized
    assert token_digest.encode("ascii") in serialized
    assert b"duplicate" in serialized
    assert b"unexpected" in serialized


def test_source_task_input_and_opaque_key_are_revalidated(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    wrong_key = bytes(range(2, 34))
    with pytest.raises(PredictionLedgerError):
        build_prediction_ledger(
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=wrong_key,
            preparations=_preparations(prediction_context),
            captures=(),
            system=_system(),
            jsonl_path=tmp_path / "wrong-key.jsonl",
        )

    ledger = _build(prediction_context, tmp_path / "bound.jsonl")
    wrong_task_input = task_input_document(
        prediction_context.source,
        opaque_key=wrong_key,
    )
    with pytest.raises(PredictionLedgerError):
        verify_prediction_ledger(
            ledger,
            prediction_context.source,
            wrong_task_input,
            opaque_key=wrong_key,
            system=_system(),
        )


def test_cross_source_preparation_is_rejected(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    records = [dict(record) for record in prediction_context.source.records]
    records[0]["problem_statement"] += " Changed source."
    other_source, other_task_input = _source_fixture(tmp_path, records=records)
    other_ids = tuple(
        str(task["opaque_task_id"])
        for task in other_task_input["tasks"]
    )
    other_context = _PredictionContext(
        source=other_source,
        task_input=other_task_input,
        prepared=prediction_context.prepared,
        opaque_ids=other_ids,
    )

    with pytest.raises(PredictionLedgerError):
        _build(other_context, tmp_path / "cross-source.jsonl")


def test_preparation_without_suite_binding_is_rejected(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    record = dict(prediction_context.source.records[0])
    output_parent = tmp_path / "null-suite-prepared"
    output_parent.mkdir()
    unbound = prepare_repository_from_commit(
        prediction_context.prepared.mirror,
        record["base_commit"],
        record["repo"],
        record["instance_id"],
        0,
        _digest(record),
        output_parent,
    )
    preparations = (
        RepositoryPreparationOutcome(
            opaque_task_id=prediction_context.opaque_ids[0],
            status="prepared",
            prepared=unbound,
        ),
        *_preparations(prediction_context)[1:],
    )

    with pytest.raises(PredictionLedgerError):
        _build(
            prediction_context,
            tmp_path / "null-suite.jsonl",
            preparations=preparations,
        )


def test_write_load_roundtrip_is_strict_and_refuses_overwrite(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    ledger = _build(prediction_context, tmp_path / "roundtrip.jsonl")
    manifest = tmp_path / "ledger.json"
    write_prediction_ledger(
        manifest,
        ledger,
        prediction_context.source,
        prediction_context.task_input,
        opaque_key=_OPAQUE_KEY,
        system=_system(),
    )
    encoded = manifest.read_bytes()
    loaded = load_prediction_ledger(
        manifest,
        ledger.jsonl_path,
        prediction_context.source,
        prediction_context.task_input,
        opaque_key=_OPAQUE_KEY,
        preparations=_preparations(prediction_context),
        system=_system(),
    )

    assert _thaw(loaded.document) == _thaw(ledger.document)
    assert loaded.jsonl_path == ledger.jsonl_path
    assert encoded.endswith(b"\n")
    with pytest.raises(PredictionLedgerError):
        write_prediction_ledger(
            manifest,
            ledger,
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            system=_system(),
        )


@pytest.mark.parametrize("mutation", ["patch", "instance", "whitespace"])
def test_live_official_jsonl_mutation_and_cross_task_swap_are_rejected(
    prediction_context: _PredictionContext,
    tmp_path: Path,
    mutation: str,
) -> None:
    ledger = _build(prediction_context, tmp_path / f"mutated-{mutation}.jsonl")
    official_rows = _official_rows(ledger)
    if mutation == "patch":
        official_rows[0]["model_patch"] = str(official_rows[0]["model_patch"]) + "# changed\n"
        ledger.jsonl_path.write_bytes(
            b"".join(_canonical_bytes(row) + b"\n" for row in official_rows)
        )
    elif mutation == "instance":
        official_rows[0]["instance_id"] = "example__project-2"
        ledger.jsonl_path.write_bytes(
            b"".join(_canonical_bytes(row) + b"\n" for row in official_rows)
        )
    else:
        ledger.jsonl_path.write_bytes(
            json.dumps(official_rows[0], ensure_ascii=False).encode("utf-8")
            + b"\n"
            + b"".join(
                _canonical_bytes(row) + b"\n" for row in official_rows[1:]
            )
        )

    with pytest.raises(PredictionLedgerError):
        verify_prediction_ledger(
            ledger,
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            system=_system(),
        )


def test_mutated_and_duplicate_key_ledger_files_are_rejected(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    ledger = _build(prediction_context, tmp_path / "strict.jsonl")
    manifest = tmp_path / "strict-ledger.json"
    write_prediction_ledger(
        manifest,
        ledger,
        prediction_context.source,
        prediction_context.task_input,
        opaque_key=_OPAQUE_KEY,
        system=_system(),
    )
    original = manifest.read_bytes()

    mutated = json.loads(original)
    mutated["schema"] = "forged-schema"
    manifest.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(PredictionLedgerError):
        load_prediction_ledger(
            manifest,
            ledger.jsonl_path,
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            preparations=_preparations(prediction_context),
            system=_system(),
        )

    duplicate = tmp_path / "duplicate-key-ledger.json"
    duplicate.write_bytes(
        original.replace(
            b'"schema":',
            b'"schema":"duplicate","schema":',
            1,
        )
    )
    with pytest.raises(PredictionLedgerError):
        load_prediction_ledger(
            duplicate,
            ledger.jsonl_path,
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            preparations=_preparations(prediction_context),
            system=_system(),
        )

    bom = tmp_path / "bom-ledger.json"
    bom.write_bytes(b"\xef\xbb\xbf" + original)
    with pytest.raises(PredictionLedgerError):
        load_prediction_ledger(
            bom,
            ledger.jsonl_path,
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            preparations=_preparations(prediction_context),
            system=_system(),
        )


@pytest.mark.parametrize("mutation", ["whitespace", "duplicate-key", "bom"])
def test_resigned_jsonl_evidence_still_rejects_noncanonical_lines(
    prediction_context: _PredictionContext,
    tmp_path: Path,
    mutation: str,
) -> None:
    ledger = _build(prediction_context, tmp_path / f"noncanonical-{mutation}.jsonl")
    lines = ledger.jsonl_path.read_bytes().splitlines(keepends=True)
    first = json.loads(lines[0])
    if mutation == "whitespace":
        lines[0] = json.dumps(first, ensure_ascii=False).encode("utf-8") + b"\n"
    elif mutation == "duplicate-key":
        lines[0] = lines[0].replace(
            b'"instance_id":',
            b'"instance_id":"duplicate","instance_id":',
            1,
        )
    else:
        lines[0] = b"\xef\xbb\xbf" + lines[0]
    mutated_jsonl = b"".join(lines)
    ledger.jsonl_path.write_bytes(mutated_jsonl)

    document = ledger.to_dict()
    document["tasks"][0]["prediction"]["jsonl_line"] = {
        "byte_count": len(lines[0]),
        "sha256": hashlib.sha256(lines[0]).hexdigest(),
    }
    document["official_jsonl"]["byte_count"] = len(mutated_jsonl)
    document["official_jsonl"]["sha256"] = hashlib.sha256(mutated_jsonl).hexdigest()
    _resign(document, "prediction_ledger_sha256")
    forged = VerifiedPredictionLedger(
        jsonl_path=ledger.jsonl_path,
        preparations=ledger.preparations,
        limits=ledger.limits,
        document=document,
    )

    with pytest.raises(PredictionLedgerError):
        verify_prediction_ledger(
            forged,
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            system=_system(),
        )


def test_public_ledger_and_limits_forgeries_are_revalidated(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    ledger = _build(prediction_context, tmp_path / "forged.jsonl")
    document = _thaw(ledger.document)
    assert isinstance(document, dict)
    document["schema"] = "forged-schema"
    forged_document = VerifiedPredictionLedger(
        jsonl_path=ledger.jsonl_path,
        preparations=ledger.preparations,
        limits=ledger.limits,
        document=document,
    )
    with pytest.raises(PredictionLedgerError):
        verify_prediction_ledger(
            forged_document,
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            system=_system(),
        )

    forged_limits = PredictionLedgerLimits()
    object.__setattr__(forged_limits, "max_patch_bytes", True)
    forged_runtime = VerifiedPredictionLedger(
        jsonl_path=ledger.jsonl_path,
        preparations=ledger.preparations,
        limits=forged_limits,
        document=ledger.document,
    )
    with pytest.raises(PredictionLedgerError):
        verify_prediction_ledger(
            forged_runtime,
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            system=_system(),
        )


def test_resigned_ledger_replays_aggregate_capture_limit(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    ledger = _build(prediction_context, tmp_path / "aggregate-replay.jsonl")
    document = _thaw(ledger.document)
    assert isinstance(document, dict)
    document["limits"]["max_total_capture_bytes"] = 1
    _resign(document, "prediction_ledger_sha256")

    with pytest.raises(PredictionLedgerError, match="aggregate byte limit"):
        decode_prediction_ledger(document)


def test_materialized_sequence_subclasses_cannot_lie_about_capture_count(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    class LyingCaptureList(list[CandidatePatchCapture]):
        def __len__(self) -> int:
            return 0

    captures = LyingCaptureList(
        [
            _recorded_capture(prediction_context.opaque_ids[0], "one"),
            _recorded_capture(prediction_context.opaque_ids[0], "two"),
        ]
    )
    with pytest.raises(PredictionLedgerError, match="materialized sequence"):
        build_prediction_ledger(
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            preparations=_preparations(prediction_context),
            captures=captures,
            system=_system(),
            jsonl_path=tmp_path / "lying-sequence.jsonl",
            limits=PredictionLedgerLimits(max_candidate_captures=1),
        )


@pytest.mark.parametrize("invalid_limits", [False, 0])
def test_explicit_falsy_limits_do_not_select_defaults(
    prediction_context: _PredictionContext,
    tmp_path: Path,
    invalid_limits: object,
) -> None:
    destination = tmp_path / f"falsy-limits-{type(invalid_limits).__name__}.jsonl"
    with pytest.raises(PredictionLedgerError, match="limits must be"):
        build_prediction_ledger(
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            preparations=_preparations(prediction_context),
            captures=(),
            system=_system(),
            jsonl_path=destination,
            limits=invalid_limits,  # type: ignore[arg-type]
        )
    assert not destination.exists()


def test_resigned_ledger_count_order_and_cross_task_forgeries_are_rejected(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    ledger = _build(prediction_context, tmp_path / "resigned.jsonl")
    document = ledger.to_dict()
    _assert_self_hash(document, "prediction_ledger_sha256")
    assert decode_prediction_ledger(copy.deepcopy(document)) == document

    count_forgery = copy.deepcopy(document)
    count_forgery["cohort"]["prediction_count"] = 2
    count_forgery["cohort"]["nonprediction_count"] = 1
    _resign(count_forgery, "prediction_ledger_sha256")
    with pytest.raises(PredictionLedgerError):
        decode_prediction_ledger(count_forgery)

    cross_task = copy.deepcopy(document)
    first_id = cross_task["tasks"][0]["opaque_task_id"]
    cross_task["tasks"][0]["opaque_task_id"] = cross_task["tasks"][1][
        "opaque_task_id"
    ]
    cross_task["tasks"][1]["opaque_task_id"] = first_id
    _resign(cross_task, "prediction_ledger_sha256")
    forged = VerifiedPredictionLedger(
        jsonl_path=ledger.jsonl_path,
        preparations=ledger.preparations,
        limits=ledger.limits,
        document=cross_task,
    )
    with pytest.raises(PredictionLedgerError):
        verify_prediction_ledger(
            forged,
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            system=_system(),
        )


def test_authoritative_preparation_and_system_dataclass_forgeries_are_rejected(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    preparations = list(_preparations(prediction_context))
    object.__setattr__(preparations[0], "prepared", None)
    with pytest.raises(PredictionLedgerError):
        _build(
            prediction_context,
            tmp_path / "forged-preparation.jsonl",
            preparations=tuple(preparations),
        )

    system = _system()
    object.__setattr__(system, "repository_clean", 1)
    with pytest.raises(PredictionLedgerError):
        build_prediction_ledger(
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            preparations=_preparations(prediction_context),
            captures=(),
            system=system,
            jsonl_path=tmp_path / "forged-system.jsonl",
        )

    ledger = _build(prediction_context, tmp_path / "system-bound.jsonl")
    other_system = SystemIdentity(
        code_revision="1" * 40,
        repository_clean=True,
        model_name_or_path="different-model",
        model_sha256="7" * 64,
        agent_sha256="3" * 64,
        prompt_sha256="4" * 64,
        tool_sha256="5" * 64,
        controller_sha256="6" * 64,
    )
    with pytest.raises(PredictionLedgerError, match="system identity mismatch"):
        verify_prediction_ledger(
            ledger,
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            system=other_system,
        )

    class HostileSystemIdentity(SystemIdentity):
        def to_dict(self) -> dict[str, object]:
            raise AssertionError("hostile system serializer must not run")

    hostile_system = HostileSystemIdentity(**_system().to_dict())
    with pytest.raises(PredictionLedgerError, match="must be SystemIdentity"):
        build_prediction_ledger(
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            preparations=_preparations(prediction_context),
            captures=(),
            system=hostile_system,
            jsonl_path=tmp_path / "hostile-system.jsonl",
        )


def test_execution_grading_score_and_claim_flags_remain_false(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    ledger = _build(prediction_context, tmp_path / "claims.jsonl")
    document = _thaw(ledger.document)
    assert isinstance(document, dict)
    evidence = document["evidence_state"]
    assert isinstance(evidence, dict)
    fixed_false = {
        "candidate_mount_created",
        "candidate_execution_performed",
        "hidden_tests_applied",
        "grading_performed",
        "external_score_computed",
        "usefulness_measured",
        "claim_ready",
        "self_hash_authenticates_producer",
        "system_identity_authenticated",
        "candidate_capture_origin_authenticated",
        "protocol_violations_independently_replayable",
    }
    assert {name for name, value in evidence.items() if value is False} == fixed_false
    assert ledger.claim_ready is False

    for name in fixed_false:
        forged = copy.deepcopy(document)
        forged["evidence_state"][name] = True
        _resign(forged, "prediction_ledger_sha256")
        with pytest.raises(PredictionLedgerError):
            decode_prediction_ledger(forged)


def test_hardlinked_live_artifacts_are_rejected(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    ledger = _build(prediction_context, tmp_path / "single-link.jsonl")
    jsonl_link = tmp_path / "jsonl-hardlink"
    try:
        os.link(ledger.jsonl_path, jsonl_link)
    except OSError:
        pytest.skip("the test filesystem does not support hard links")
    try:
        with pytest.raises(PredictionLedgerError, match="single-link regular file"):
            verify_prediction_ledger(
                ledger,
                prediction_context.source,
                prediction_context.task_input,
                opaque_key=_OPAQUE_KEY,
                system=_system(),
            )
    finally:
        jsonl_link.unlink()

    manifest = tmp_path / "single-link-ledger.json"
    write_prediction_ledger(
        manifest,
        ledger,
        prediction_context.source,
        prediction_context.task_input,
        opaque_key=_OPAQUE_KEY,
        system=_system(),
    )
    manifest_link = tmp_path / "ledger-hardlink"
    os.link(manifest, manifest_link)
    try:
        with pytest.raises(PredictionLedgerError, match="single-link regular file"):
            load_prediction_ledger(
                manifest,
                ledger.jsonl_path,
                prediction_context.source,
                prediction_context.task_input,
                opaque_key=_OPAQUE_KEY,
                preparations=_preparations(prediction_context),
                system=_system(),
            )
    finally:
        manifest_link.unlink()


def test_preflight_ledger_limit_failure_leaves_jsonl_absent(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "must-remain-absent.jsonl"
    with pytest.raises(PredictionLedgerError, match="max_ledger_bytes|ledger exceeds"):
        _build(
            prediction_context,
            destination,
            limits=PredictionLedgerLimits(max_ledger_bytes=1),
        )
    assert not destination.exists()


def test_capture_work_limits_and_objective_oversize_status_fail_closed(
    prediction_context: _PredictionContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "aggregate-limit.jsonl"
    with monkeypatch.context() as patch_context:
        def reject_encoding(value: str, *, max_bytes: int) -> bytes:
            raise AssertionError("aggregate limit must precede patch encoding")

        patch_context.setattr(prediction_module, "_patch_bytes", reject_encoding)
        with pytest.raises(PredictionLedgerError, match="aggregate byte limit"):
            _build(
                prediction_context,
                destination,
                captures=(
                    _recorded_capture(prediction_context.opaque_ids[0], "ab"),
                ),
                limits=PredictionLedgerLimits(max_total_capture_bytes=1),
            )
    assert not destination.exists()

    fabricated = CandidatePatchCapture(
        opaque_task_id=prediction_context.opaque_ids[0],
        status="oversized",
        capture=_retained_bytes(b"small"),
        failure_code="patch-byte-limit",
    )
    ledger = _build(
        prediction_context,
        tmp_path / "fabricated-oversize.jsonl",
        captures=(fabricated,),
    )
    document = ledger.to_dict()
    assert document["tasks"][0]["prediction"]["absence_code"] == (
        "invalid-candidate-output"
    )
    assert b"invalid-oversized-evidence" in _canonical_bytes(document)


def test_multibyte_patch_limit_is_totalized_before_full_encoding(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    patch = "é" * 3
    capture = CandidatePatchCapture(
        opaque_task_id=prediction_context.opaque_ids[0],
        status="recorded",
        model_patch=patch,
        capture=RetainedCaptureEvidence(
            observed_bytes=3,
            captured_bytes=3,
            captured_sha256=hashlib.sha256(b"abc").hexdigest(),
            complete=True,
        ),
    )
    ledger = _build(
        prediction_context,
        tmp_path / "multibyte-limit.jsonl",
        captures=(capture,),
        limits=PredictionLedgerLimits(max_patch_bytes=4),
    )

    assert [row["model_patch"] for row in _official_rows(ledger)] == [
        None,
        None,
        None,
    ]
    assert ledger.to_dict()["tasks"][0]["prediction"]["absence_code"] == (
        "oversized-candidate-output"
    )


def test_dishonest_zero_byte_evidence_is_rejected_before_patch_encoding(
    prediction_context: _PredictionContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = CandidatePatchCapture(
        opaque_task_id=prediction_context.opaque_ids[0],
        status="recorded",
        model_patch="bounded-but-not-observed",
        capture=_retained_bytes(b""),
    )

    def reject_encoding(value: str, *, max_bytes: int) -> bytes:
        raise AssertionError("evidence mismatch must precede patch encoding")

    monkeypatch.setattr(prediction_module, "_patch_bytes", reject_encoding)
    ledger = _build(
        prediction_context,
        tmp_path / "dishonest-zero-byte.jsonl",
        captures=(capture,),
        limits=PredictionLedgerLimits(max_total_capture_bytes=1),
    )

    assert [row["model_patch"] for row in _official_rows(ledger)] == [
        None,
        None,
        None,
    ]
    assert b"capture-evidence-mismatch" in _canonical_bytes(ledger.to_dict())


def test_arbitrary_malformed_capture_is_totalized_without_attribute_access(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    class ExplosiveCapture:
        @property
        def opaque_task_id(self) -> str:
            raise AssertionError("malformed candidate property must not run")

    ledger = _build(
        prediction_context,
        tmp_path / "malformed-object.jsonl",
        captures=(ExplosiveCapture(),),  # type: ignore[arg-type]
    )
    assert [row["model_patch"] for row in _official_rows(ledger)] == [
        None,
        None,
        None,
    ]
    assert b"malformed-candidate-capture" in _canonical_bytes(ledger.to_dict())


def test_hostile_capture_subclasses_are_totalized_without_callbacks(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    class HostileEvidence(RetainedCaptureEvidence):
        def to_dict(self) -> dict[str, object]:
            raise AssertionError("hostile evidence serializer must not run")

    class HostilePatch(str):
        def __len__(self) -> int:
            raise AssertionError("hostile patch length must not run")

    class HostileId(str):
        def __getitem__(self, key: object) -> str:
            raise AssertionError("hostile id slicing must not run")

    class HostileType(type):
        @property
        def __name__(self) -> str:
            raise AssertionError("hostile type name must not run")

    class HostileObject(metaclass=HostileType):
        pass

    hostile_evidence = _recorded_capture(
        prediction_context.opaque_ids[0],
        "safe",
    )
    object.__setattr__(
        hostile_evidence,
        "capture",
        HostileEvidence(**_retained_bytes(b"safe").to_dict()),
    )
    hostile_patch = _recorded_capture(
        prediction_context.opaque_ids[0],
        "safe",
    )
    object.__setattr__(hostile_patch, "model_patch", HostilePatch("safe"))
    hostile_id = _recorded_capture(
        prediction_context.opaque_ids[0],
        "safe",
    )
    object.__setattr__(
        hostile_id,
        "opaque_task_id",
        HostileId(prediction_context.opaque_ids[0]),
    )
    hostile_object_id = CandidatePatchCapture(
        opaque_task_id=HostileObject(),
        status="invalid",
        capture=_retained_bytes(b""),
        failure_code="invalid-candidate-id",
    )

    for ordinal, capture in enumerate(
        (hostile_evidence, hostile_patch, hostile_id, hostile_object_id),
    ):
        ledger = _build(
            prediction_context,
            tmp_path / f"hostile-capture-{ordinal}.jsonl",
            captures=(capture,),
        )
        assert [row["model_patch"] for row in _official_rows(ledger)] == [
            None,
            None,
            None,
        ]


def test_prediction_outputs_cannot_mutate_prepared_mirror_or_tree(
    prediction_context: _PredictionContext,
    tmp_path: Path,
) -> None:
    mirror_output = prediction_context.prepared.mirror.path / "forbidden.jsonl"
    with pytest.raises(PredictionLedgerError, match="bare mirror"):
        _build(prediction_context, mirror_output)
    assert not mirror_output.exists()

    ledger = _build(prediction_context, tmp_path / "guarded.jsonl")
    manifest = prediction_context.prepared.mirror.path / "forbidden-ledger.json"
    with pytest.raises(PredictionLedgerError, match="bare mirror"):
        write_prediction_ledger(
            manifest,
            ledger,
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            system=_system(),
        )
    assert not manifest.exists()


def test_late_jsonl_verification_failure_removes_owned_output(
    prediction_context: _PredictionContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "late-failure.jsonl"
    original = prediction_module._single_link_regular
    calls = 0

    def fail_first_prediction_check(path: Path, *, label: str) -> None:
        nonlocal calls
        calls += 1
        if path == destination and calls == 1:
            raise PredictionLedgerError("injected post-write failure")
        original(path, label=label)

    monkeypatch.setattr(
        prediction_module,
        "_single_link_regular",
        fail_first_prediction_check,
    )
    with pytest.raises(PredictionLedgerError, match="injected post-write failure"):
        _build(prediction_context, destination)
    assert not destination.exists()


def test_late_cleanup_preserves_a_substituted_output(
    prediction_context: _PredictionContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "substituted.jsonl"
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"concurrent replacement")
    original = prediction_module._single_link_regular
    replaced = False

    def substitute_then_fail(path: Path, *, label: str) -> None:
        nonlocal replaced
        if path == destination and not replaced:
            replaced = True
            os.replace(replacement, destination)
            raise PredictionLedgerError("injected substitution")
        original(path, label=label)

    monkeypatch.setattr(
        prediction_module,
        "_single_link_regular",
        substitute_then_fail,
    )
    with pytest.raises(PredictionLedgerError, match="cleanup was incomplete"):
        _build(prediction_context, destination)
    assert destination.read_bytes() == b"concurrent replacement"


def test_live_replay_rejects_jsonl_path_replacement_after_descriptor_read(
    prediction_context: _PredictionContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _build(prediction_context, tmp_path / "read-race.jsonl")
    replacement = tmp_path / "read-race-replacement"
    replacement.write_bytes(b"MALICIOUS")
    original = prediction_module.read_bounded_regular_file
    replaced = False

    def replace_after_read(
        path: str | Path,
        *,
        max_bytes: int,
        label: str = "input file",
        expected_identity: tuple[int, int] | None = None,
    ) -> object:
        nonlocal replaced
        document = original(
            path,
            max_bytes=max_bytes,
            label=label,
            expected_identity=expected_identity,
        )
        if Path(path) == ledger.jsonl_path and not replaced:
            replaced = True
            os.replace(replacement, ledger.jsonl_path)
        return document

    monkeypatch.setattr(
        prediction_module,
        "read_bounded_regular_file",
        replace_after_read,
    )
    with pytest.raises(PredictionLedgerError, match="path identity changed"):
        verify_prediction_ledger(
            ledger,
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            system=_system(),
        )
    assert ledger.jsonl_path.read_bytes() == b"MALICIOUS"


def test_live_replay_binds_the_descriptor_across_an_aba_path_swap(
    prediction_context: _PredictionContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _build(prediction_context, tmp_path / "aba-race.jsonl")
    original_bytes = ledger.jsonl_path.read_bytes()
    parked = tmp_path / "aba-original-parked"
    replacement = tmp_path / "aba-same-bytes-replacement"
    replacement.write_bytes(original_bytes)
    original = prediction_module.read_bounded_regular_file
    swapped = False

    def swap_aba_during_read(
        path: str | Path,
        *,
        max_bytes: int,
        label: str = "input file",
        expected_identity: tuple[int, int] | None = None,
    ) -> object:
        nonlocal swapped
        selected = Path(path)
        if selected != ledger.jsonl_path or swapped:
            return original(
                path,
                max_bytes=max_bytes,
                label=label,
                expected_identity=expected_identity,
            )
        swapped = True
        os.replace(ledger.jsonl_path, parked)
        parked.write_bytes(b"MALICIOUS")
        os.replace(replacement, ledger.jsonl_path)
        try:
            return original(
                path,
                max_bytes=max_bytes,
                label=label,
                expected_identity=expected_identity,
            )
        finally:
            os.replace(ledger.jsonl_path, replacement)
            os.replace(parked, ledger.jsonl_path)

    monkeypatch.setattr(
        prediction_module,
        "read_bounded_regular_file",
        swap_aba_during_read,
    )
    with pytest.raises(PredictionLedgerError, match="identity does not match"):
        verify_prediction_ledger(
            ledger,
            prediction_context.source,
            prediction_context.task_input,
            opaque_key=_OPAQUE_KEY,
            system=_system(),
        )
    assert ledger.jsonl_path.read_bytes() == b"MALICIOUS"
