from __future__ import annotations

import json
from pathlib import Path

import pytest

from context_compiler import (
    ARTIFACT_INSPECTION_SCHEMA,
    ContextCompiler,
    SourceRecord,
    render_artifact_text,
    summarize_artifact,
)
from context_compiler.cli import main


def write_artifact(
    path: Path,
    *,
    source_id: str = "inspector-source",
    content: str,
) -> dict:
    source = SourceRecord.create(
        id=source_id,
        sequence=0,
        role="user",
        content=content,
    )
    artifact = ContextCompiler().compile([source]).to_dict()
    path.write_text(
        json.dumps(artifact, ensure_ascii=False),
        encoding="utf-8",
    )
    return artifact


def test_text_inspector_escapes_control_characters_and_shows_provenance(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "artifact.json"
    artifact = write_artifact(
        path,
        source_id="source\n\u001b[2J\u202e\u2028",
        content="constraint: Keep \u001b[31mred\u001b[0m literal safe",
    )

    assert (
        main(
            [
                "inspect",
                str(path),
                "--format",
                "text",
                "--show-items",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out

    assert output.rstrip("\n") == render_artifact_text(
        artifact,
        include_items=True,
    )
    assert "\u001b" not in output
    assert "\u202e" not in output
    assert "\u2028" not in output
    assert "\\u001b[31m" in output
    assert "\\u202e" in output
    assert "\\u2028" in output
    assert "source\\\\u000a\\\\u001b[2J" in output
    assert "[SELECTED]" in output
    assert "provenance=[" in output
    assert "self_hash_valid=true" in output

    assert main(["inspect", str(path), "--show-items"]) == 0
    json_output = capsys.readouterr().out
    decoded = json.loads(json_output)

    assert "\u001b" not in json_output
    assert "\u202e" not in json_output
    assert "\u2028" not in json_output
    assert "\\u202e" in json_output
    assert decoded["item_details"][0]["provenance"][0]["source_id"].endswith(
        "\u202e\u2028"
    )


def test_text_inspector_limits_items_and_truncates_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "artifact.json"
    write_artifact(
        path,
        content=(
            "constraints:\n"
            "- Alpha requirement has a deliberately long description\n"
            "- Beta requirement remains active\n"
            "- Gamma requirement remains active"
        ),
    )

    assert (
        main(
            [
                "inspect",
                str(path),
                "--format",
                "text",
                "--show-items",
                "--max-display-items",
                "1",
                "--max-text-chars",
                "12",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out

    assert "item_details: displaying=1 omitted=2" in output
    assert "…" in output
    assert "deliberately long description" not in output
    assert output.count("- [SELECTED]") == 1


def test_json_item_view_exposes_conflicts_and_bounds_links(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "artifact.json"
    write_artifact(
        path,
        content=(
            "constraint: The DB cache must be enabled.\n"
            "constraint: The DB cache must be disabled."
        ),
    )

    assert (
        main(
            [
                "inspect",
                str(path),
                "--show-items",
                "--max-display-items",
                "10",
                "--max-display-links",
                "1",
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    details = summary["item_details"]
    unresolved = next(
        item
        for item in details
        if "detected-conflict" in item["tags"]
    )

    assert summary["max_display_links"] == 1
    assert summary["displayed_items"] == 3
    assert summary["omitted_items"] == 0
    assert all(
        item["selection_state"]
        == ("selected" if item["selected"] else "not_selected")
        for item in details
    )
    assert unresolved["status"] == "active"
    assert len(unresolved["conflicts_with"]) == 1
    assert unresolved["conflicts_with_omitted"] == 1
    assert len(unresolved["provenance"]) == 1
    assert unresolved["provenance_count"] == 2
    assert unresolved["provenance_omitted"] == 1


def test_default_json_inspection_remains_summary_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "artifact.json"
    artifact = write_artifact(
        path,
        content="constraint: Keep the default inspection compact",
    )

    assert main(["inspect", str(path)]) == 0
    summary = json.loads(capsys.readouterr().out)
    direct = summarize_artifact(artifact)

    assert summary == direct
    assert summary["schema"] == ARTIFACT_INSPECTION_SCHEMA
    assert summary["compiled_at"] == artifact["compiled_at"]
    assert "item_details" not in summary
    assert "displayed_items" not in summary
    assert "omitted_items" not in summary

    detached = json.loads(json.dumps(direct))
    artifact["verification"]["passed"] = False
    artifact["compression"]["token_budget"] = 1
    assert direct == detached


def test_inspector_rejects_nonpositive_display_bounds(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "artifact.json"
    write_artifact(
        path,
        content="constraint: Keep display limits positive",
    )

    for option in (
        "--max-display-items",
        "--max-display-links",
        "--max-text-chars",
    ):
        assert (
            main(
                [
                    "inspect",
                    str(path),
                    option,
                    "0",
                    "--error-format",
                    "json",
                ]
            )
            == 2
        )
        captured = capsys.readouterr()
        diagnostic = json.loads(captured.err)
        assert captured.out == ""
        assert diagnostic["command"] == "inspect"
        assert diagnostic["category"] == "invalid_input"
        assert diagnostic["code"] == "invalid_value"
