from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from context_compiler import (
    ARCHIVE_ENTRY_SCHEMA,
    ARCHIVE_GENESIS_SHA256,
    ARCHIVE_REPORT_SCHEMA,
    LEGACY_ARCHIVE_SCHEMA,
    SourceArchive,
    SourceRecord,
)
from context_compiler.cli import main


def source(sequence: int) -> SourceRecord:
    return SourceRecord.create(
        id=f"chain-source-{sequence}",
        sequence=sequence,
        role="user",
        content=f"constraint: retain chained source {sequence}",
    )


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_entries(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def write_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(
                entry,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
            for entry in entries
        ),
        encoding="utf-8",
    )


def test_archive_entries_form_a_canonical_chain_and_report_the_head(
    tmp_path: Path,
) -> None:
    archive = SourceArchive(tmp_path / "archive")

    assert archive.append([source(2), source(0), source(1)]) == 3

    previous = ARCHIVE_GENESIS_SHA256
    entries = read_entries(archive.events_path)
    assert [entry["source"]["sequence"] for entry in entries] == [0, 1, 2]
    for position, entry in enumerate(entries):
        assert set(entry) == {
            "schema",
            "position",
            "previous_entry_sha256",
            "source",
            "entry_sha256",
        }
        assert entry["schema"] == ARCHIVE_ENTRY_SCHEMA
        assert entry["position"] == position
        assert entry["previous_entry_sha256"] == previous
        payload = {key: value for key, value in entry.items() if key != "entry_sha256"}
        assert entry["entry_sha256"] == canonical_sha256(payload)
        previous = entry["entry_sha256"]

    report = archive.verify()
    assert report.passed is True
    assert report.schema == ARCHIVE_REPORT_SCHEMA
    assert report.archive_schema == ARCHIVE_ENTRY_SCHEMA
    assert report.chain_head_sha256 == previous


def test_archive_chain_rejects_field_hash_link_and_order_tampering(
    tmp_path: Path,
) -> None:
    archive = SourceArchive(tmp_path / "archive")
    assert archive.append([source(0), source(1), source(2)]) == 3
    committed = archive.events_path.read_bytes()

    mutations = [
        (
            lambda entries: entries[1]["source"].__setitem__("content", "tampered"),
            "hash mismatch",
        ),
        (
            lambda entries: entries[1].__setitem__(
                "previous_entry_sha256",
                "f" * 64,
            ),
            "does not link",
        ),
        (
            lambda entries: entries[1].__setitem__("entry_sha256", "f" * 64),
            "hash mismatch",
        ),
        (
            lambda entries: entries[1].__setitem__("unexpected", True),
            "fields do not match",
        ),
    ]
    for mutate, expected_issue in mutations:
        entries = read_entries(archive.events_path)
        mutate(entries)
        write_entries(archive.events_path, entries)
        report = archive.verify()
        assert report.passed is False
        assert expected_issue in report.issues[0]
        archive.events_path.write_bytes(committed)

    entries = read_entries(archive.events_path)
    write_entries(archive.events_path, [entries[0], entries[2], entries[1]])
    assert archive.verify().passed is False
    archive.events_path.write_bytes(committed)

    entries = read_entries(archive.events_path)
    write_entries(archive.events_path, [entries[0], entries[2]])
    assert archive.verify().passed is False


def test_retained_head_detects_valid_prefix_rollback_and_blocks_append(
    tmp_path: Path,
) -> None:
    archive = SourceArchive(tmp_path / "archive")
    assert archive.append([source(0)]) == 1
    earlier_bytes = archive.events_path.read_bytes()
    earlier_head = archive.verify().chain_head_sha256
    assert earlier_head is not None

    assert archive.append([source(1)], expected_chain_head=earlier_head) == 1
    latest_head = archive.verify().chain_head_sha256
    assert latest_head is not None and latest_head != earlier_head

    archive.events_path.write_bytes(earlier_bytes)
    assert archive.verify().passed is True
    anchored = archive.verify(expected_chain_head=latest_head)
    assert anchored.passed is False
    assert "chain head mismatch" in anchored.issues[0]

    rolled_back_bytes = archive.events_path.read_bytes()
    with pytest.raises(ValueError, match="chain head mismatch"):
        archive.load(expected_chain_head=latest_head)
    with pytest.raises(ValueError, match="chain head mismatch"):
        archive.append([source(2)], expected_chain_head=latest_head)
    assert archive.events_path.read_bytes() == rolled_back_bytes


def test_legacy_archive_loads_and_first_new_record_upgrades_it_atomically(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "archive"
    directory.mkdir()
    legacy = [source(2), source(0)]
    events_path = directory / "events.jsonl"
    events_path.write_text(
        "".join(
            json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":"))
            + "\n"
            for record in legacy
        ),
        encoding="utf-8",
    )
    archive = SourceArchive(directory)

    assert [record.sequence for record in archive.load()] == [0, 2]
    legacy_report = archive.verify()
    assert legacy_report.passed is True
    assert legacy_report.archive_schema == LEGACY_ARCHIVE_SCHEMA
    assert legacy_report.chain_head_sha256 is None
    assert archive.verify(expected_chain_head=ARCHIVE_GENESIS_SHA256).passed is False
    with pytest.raises(ValueError, match="has no chain head"):
        archive.append([source(1)], expected_chain_head=ARCHIVE_GENESIS_SHA256)

    assert archive.append([source(1)]) == 1
    upgraded = archive.verify()
    assert upgraded.passed is True
    assert upgraded.archive_schema == ARCHIVE_ENTRY_SCHEMA
    assert upgraded.chain_head_sha256 is not None
    assert [entry["source"]["sequence"] for entry in read_entries(events_path)] == [
        0,
        1,
        2,
    ]


def test_invalid_expected_head_is_rejected_before_archive_creation(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "archive"
    archive = SourceArchive(directory)

    with pytest.raises(ValueError, match="64 lowercase hexadecimal"):
        archive.append([source(0)], expected_chain_head="F" * 64)
    with pytest.raises(TypeError, match="string or null"):
        archive.append([source(0)], expected_chain_head=7)  # type: ignore[arg-type]

    assert not directory.exists()


def test_archive_cli_emits_chain_state_and_enforces_retained_heads(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "source.json"
    input_path.write_text(json.dumps([source(0).to_dict()]), encoding="utf-8")
    directory = tmp_path / "archive"

    assert main(["archive", "append", str(directory), str(input_path)]) == 0
    appended = json.loads(capsys.readouterr().out)
    head = appended["chain_head_sha256"]
    assert appended["schema"] == ARCHIVE_REPORT_SCHEMA
    assert appended["archive_schema"] == ARCHIVE_ENTRY_SCHEMA
    assert isinstance(head, str) and len(head) == 64

    assert (
        main(
            [
                "archive",
                "verify",
                str(directory),
                "--expected-chain-head",
                head,
            ]
        )
        == 0
    )
    verified = json.loads(capsys.readouterr().out)
    assert verified["passed"] is True
    assert verified["chain_head_sha256"] == head

    assert (
        main(
            [
                "archive",
                "verify",
                str(directory),
                "--expected-chain-head",
                "f" * 64,
            ]
        )
        == 3
    )
    mismatch = json.loads(capsys.readouterr().out)
    assert mismatch["passed"] is False
    assert "chain head mismatch" in mismatch["issues"][0]


def test_compile_archive_head_precondition_prevents_stale_reuse(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    directory = tmp_path / "archive"
    archive = SourceArchive(directory)
    assert archive.append([source(0)]) == 1
    retained_head = archive.verify().chain_head_sha256
    assert retained_head is not None
    input_path = tmp_path / "next.json"
    input_path.write_text(json.dumps([source(1).to_dict()]), encoding="utf-8")

    assert (
        main(
            [
                "compile",
                str(input_path),
                "--archive",
                str(directory),
                "--archive-expected-chain-head",
                retained_head,
            ]
        )
        == 0
    )
    capsys.readouterr()
    committed = archive.events_path.read_bytes()

    input_path.write_text(json.dumps([source(2).to_dict()]), encoding="utf-8")
    assert (
        main(
            [
                "compile",
                str(input_path),
                "--archive",
                str(directory),
                "--archive-expected-chain-head",
                retained_head,
                "--error-format",
                "json",
            ]
        )
        == 2
    )
    diagnostic = json.loads(capsys.readouterr().err)
    assert diagnostic["category"] == "integrity"
    assert diagnostic["code"] == "integrity_check_failed"
    assert "chain head mismatch" in diagnostic["message"]
    assert archive.events_path.read_bytes() == committed


def test_compile_rejects_archive_head_without_an_archive(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "source.json"
    input_path.write_text(json.dumps([source(0).to_dict()]), encoding="utf-8")

    assert (
        main(
            [
                "compile",
                str(input_path),
                "--archive-expected-chain-head",
                ARCHIVE_GENESIS_SHA256,
            ]
        )
        == 2
    )
    assert "--archive-expected-chain-head requires --archive" in capsys.readouterr().err


def test_artifact_verify_can_load_an_anchored_source_archive(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = source(0)
    source_path = tmp_path / "source.json"
    artifact_path = tmp_path / "artifact.json"
    archive_directory = tmp_path / "archive"
    source_path.write_text(json.dumps([record.to_dict()]), encoding="utf-8")

    assert main(["compile", str(source_path), "--output", str(artifact_path)]) == 0
    capsys.readouterr()
    archive = SourceArchive(archive_directory)
    assert archive.append([record]) == 1
    head = archive.verify().chain_head_sha256
    assert head is not None

    assert (
        main(
            [
                "verify",
                str(artifact_path),
                "--archive",
                str(archive_directory),
                "--archive-expected-chain-head",
                head,
            ]
        )
        == 0
    )
    replay = json.loads(capsys.readouterr().out)
    assert replay["passed"] is True

    assert (
        main(
            [
                "verify",
                str(artifact_path),
                "--archive",
                str(archive_directory),
                "--archive-expected-chain-head",
                "f" * 64,
                "--error-format",
                "json",
            ]
        )
        == 2
    )
    diagnostic = json.loads(capsys.readouterr().err)
    assert diagnostic["category"] == "integrity"
    assert diagnostic["code"] == "integrity_check_failed"

    assert (
        main(
            [
                "verify",
                str(artifact_path),
                str(source_path),
                "--archive-expected-chain-head",
                head,
            ]
        )
        == 2
    )
    assert "--archive-expected-chain-head requires --archive" in capsys.readouterr().err

    assert (
        main(
            [
                "verify",
                str(artifact_path),
                str(source_path),
                "--archive",
                str(archive_directory),
            ]
        )
        == 2
    )
    assert "requires exactly one of SOURCES or --archive" in capsys.readouterr().err
