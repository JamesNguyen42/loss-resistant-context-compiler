from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from context_compiler import (
    ARTIFACT_DIFF_SCHEMA,
    ContextCompiler,
    SourceRecord,
    diff_artifacts,
)
from context_compiler.cli import main


def artifact_pair() -> tuple[dict, dict]:
    original = SourceRecord.create(
        id="diff-source-0",
        sequence=0,
        role="user",
        content="constraint: The retry ceiling must be 3 attempts.",
    )
    correction = SourceRecord.create(
        id="diff-source-1",
        sequence=1,
        role="user",
        content=(
            "Actually, the retry ceiling must be 2 attempts rather than 3."
        ),
    )
    before = ContextCompiler().compile([original]).to_dict()
    after = ContextCompiler().compile([original, correction]).to_dict()
    return before, after


def report_digest(report: dict) -> str:
    unsigned = {
        key: value
        for key, value in report.items()
        if key != "diff_sha256"
    }
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_artifact(path: Path, artifact: dict) -> None:
    path.write_text(
        json.dumps(artifact, ensure_ascii=False),
        encoding="utf-8",
    )


def test_diff_classifies_temporal_and_selection_changes() -> None:
    before, after = artifact_pair()
    original_id = before["items"][0]["id"]

    report = diff_artifacts(before, after)

    assert report["schema"] == ARTIFACT_DIFF_SCHEMA
    assert report["diff_sha256"] == report_digest(report)
    assert report["summary"]["artifact_changed"] is True
    assert report["summary"]["source_history_changed"] is True
    assert report["summary"]["ledger_comparison_complete"] is True
    assert report["summary"]["payload_added_items"] == 2
    assert report["summary"]["payload_removed_items"] == 0
    assert report["summary"]["payload_modified_items"] == 1
    assert report["summary"]["unchanged_shared_items"] == 0
    assert report["selection"]["removed"] == [original_id]
    assert len(report["selection"]["added"]) == 2
    assert report["warnings"] == []

    changes = report["item_changes"]
    assert changes is not None
    modified = changes["payload_modified"]
    assert [entry["id"] for entry in modified] == [original_id]
    assert "status" in modified[0]["changed_fields"]
    assert "tags" in modified[0]["changed_fields"]
    assert modified[0]["before"]["status"] == "active"
    assert modified[0]["after"]["status"] == "superseded"
    assert len(modified[0]["before"]["metadata_sha256"]) == 64

    detached = json.loads(json.dumps(report))
    before["items"][0]["tags"].append("mutated-after-diff")
    after["compression"]["token_budget"] = 1
    assert report == detached
    assert report["diff_sha256"] == report_digest(report)


def test_identical_diff_is_deterministic_and_empty() -> None:
    artifact, _ = artifact_pair()

    first = diff_artifacts(artifact, artifact)
    second = diff_artifacts(artifact, artifact)

    assert first == second
    assert first["diff_sha256"] == report_digest(first)
    assert first["summary"] == {
        "artifact_changed": False,
        "source_history_changed": False,
        "ledger_comparison_complete": True,
        "payload_added_items": 0,
        "payload_removed_items": 0,
        "payload_modified_items": 0,
        "unchanged_shared_items": len(artifact["items"]),
        "selection_added_items": 0,
        "selection_removed_items": 0,
        "top_level_changed_fields": [],
    }
    assert first["selection"] == {"added": [], "removed": []}
    assert first["item_changes"] == {
        "payload_added": [],
        "payload_removed": [],
        "payload_modified": [],
    }
    assert all(
        change["changed"] is False
        for change in first["report_changes"].values()
    )


def test_summary_only_retains_counts_but_omits_item_details() -> None:
    before, after = artifact_pair()

    detailed = diff_artifacts(before, after)
    summary = diff_artifacts(
        before,
        after,
        include_item_details=False,
    )

    assert summary["details_included"] is False
    assert summary["item_changes"] is None
    assert summary["summary"] == detailed["summary"]
    assert summary["selection"] == detailed["selection"]
    assert summary["diff_sha256"] == report_digest(summary)
    assert summary["diff_sha256"] != detailed["diff_sha256"]


def test_active_only_diff_labels_payload_comparison_as_incomplete() -> None:
    before, after = artifact_pair()
    active_after = {
        **after,
        "items": [
            item
            for item in after["items"]
            if item["id"] in after["selected_item_ids"]
        ],
        "ledger_complete": False,
    }
    unsigned = {
        key: value
        for key, value in active_after.items()
        if key != "artifact_sha256"
    }
    active_after["artifact_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    report = diff_artifacts(before, active_after)

    assert report["summary"]["ledger_comparison_complete"] is False
    assert report["warnings"] == [
        "payload_changes_do_not_prove_complete_ledger_changes"
    ]


def test_diff_rejects_tampered_input_before_comparison() -> None:
    before, after = artifact_pair()
    before["items"][0]["text"] = "tampered"

    with pytest.raises(ValueError, match="artifact digest mismatch"):
        diff_artifacts(before, after)


def test_diff_requires_boolean_detail_option() -> None:
    before, after = artifact_pair()

    with pytest.raises(TypeError, match="include_item_details"):
        diff_artifacts(
            before,
            after,
            include_item_details=1,  # type: ignore[arg-type]
        )


def test_cli_diff_writes_summary_only_report(
    tmp_path: Path,
) -> None:
    before, after = artifact_pair()
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    output_path = tmp_path / "diff.json"
    write_artifact(before_path, before)
    write_artifact(after_path, after)

    assert (
        main(
            [
                "diff",
                str(before_path),
                str(after_path),
                "-o",
                str(output_path),
                "--summary-only",
            ]
        )
        == 0
    )
    report = json.loads(output_path.read_text(encoding="utf-8"))

    assert report["schema"] == ARTIFACT_DIFF_SCHEMA
    assert report["details_included"] is False
    assert report["item_changes"] is None
    assert report["summary"]["payload_added_items"] == 2


def test_cli_diff_returns_integrity_diagnostic_for_tampered_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before, after = artifact_pair()
    before["items"][0]["text"] = "tampered"
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    write_artifact(before_path, before)
    write_artifact(after_path, after)

    assert (
        main(
            [
                "diff",
                str(before_path),
                str(after_path),
                "--error-format",
                "json",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.err)

    assert captured.out == ""
    assert diagnostic["command"] == "diff"
    assert diagnostic["category"] == "integrity"
    assert diagnostic["code"] == "integrity_check_failed"


def test_cli_diff_applies_artifact_byte_limit_to_each_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact, _ = artifact_pair()
    compact = json.dumps(artifact, separators=(",", ":"))
    padded = json.dumps(artifact, indent=2)
    limit = len(compact.encode("utf-8"))
    assert len(padded.encode("utf-8")) > limit

    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    for oversized_first in (True, False):
        before_path.write_text(
            padded if oversized_first else compact,
            encoding="utf-8",
        )
        after_path.write_text(
            compact if oversized_first else padded,
            encoding="utf-8",
        )

        assert (
            main(
                [
                    "diff",
                    str(before_path),
                    str(after_path),
                    "--max-artifact-bytes",
                    str(limit),
                ]
            )
            == 2
        )
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "compiled artifact input exceeds" in captured.err
