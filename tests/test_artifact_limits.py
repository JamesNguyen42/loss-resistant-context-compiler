from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from context_compiler import (
    ArtifactLimitError,
    ArtifactLimits,
    ContextCompiler,
    SourceRecord,
)
from context_compiler.cli import main
from context_compiler.io import (
    load_artifact,
    load_artifact_path,
    verify_artifact_dict,
)
from context_compiler.limits import (
    DEFAULT_ARTIFACT_LIMITS,
    validate_artifact_value,
)


def source() -> SourceRecord:
    return SourceRecord.create(
        id="source-0",
        sequence=0,
        role="user",
        content="constraint: Preserve the café API 🧭",
    )


def artifact_payload() -> tuple[SourceRecord, dict[str, object], str]:
    record = source()
    artifact = ContextCompiler().compile([record]).to_dict()
    raw = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return record, artifact, raw


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("max_input_bytes", True, TypeError),
        ("max_line_chars", 0, ValueError),
        ("max_canonical_bytes", -1, ValueError),
        ("max_json_depth", 1.5, TypeError),
        ("max_items", "1", TypeError),
        ("max_selected_items", False, TypeError),
        ("max_provenance_spans", 0, ValueError),
        ("max_verification_issues", -2, ValueError),
    ],
)
def test_artifact_limits_require_positive_non_boolean_integers(
    field: str,
    value: object,
    exception: type[Exception],
) -> None:
    with pytest.raises(exception):
        ArtifactLimits(**{field: value})


def test_artifact_stream_enforces_exact_multibyte_utf8_boundary() -> None:
    _, artifact, raw = artifact_payload()
    byte_count = len(raw.encode("utf-8"))

    loaded = load_artifact(
        io.StringIO(raw),
        limits=replace(DEFAULT_ARTIFACT_LIMITS, max_input_bytes=byte_count),
    )

    assert loaded == artifact
    with pytest.raises(ArtifactLimitError, match="compiled artifact input exceeds"):
        load_artifact(
            io.StringIO(raw),
            limits=replace(
                DEFAULT_ARTIFACT_LIMITS,
                max_input_bytes=byte_count - 1,
            ),
        )


def test_artifact_path_counts_bom_and_file_bytes_before_decoding(tmp_path: Path) -> None:
    _, artifact, raw = artifact_payload()
    path = tmp_path / "artifact.json"
    path.write_text(raw, encoding="utf-8-sig", newline="")
    byte_count = path.stat().st_size

    loaded = load_artifact_path(
        path,
        limits=replace(DEFAULT_ARTIFACT_LIMITS, max_input_bytes=byte_count),
    )

    assert loaded == artifact
    with pytest.raises(ArtifactLimitError, match="compiled artifact input exceeds"):
        load_artifact_path(
            path,
            limits=replace(
                DEFAULT_ARTIFACT_LIMITS,
                max_input_bytes=byte_count - 1,
            ),
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('{"items":[],"items":[]}', "duplicate JSON object key"),
        ('{"value":NaN}', "non-standard JSON constant"),
        ('{"value":Infinity}', "non-standard JSON constant"),
        ('{"value":-Infinity}', "non-standard JSON constant"),
        ('{"value":1e309}', "JSON number must be finite"),
    ],
)
def test_artifact_json_rejects_duplicate_keys_and_nonfinite_numbers(
    payload: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        load_artifact(io.StringIO(payload))


def test_artifact_json_integer_length_is_bounded_explicitly() -> None:
    payload = '{"value":' + "9" * 641 + "}"

    with pytest.raises(
        ArtifactLimitError,
        match=(
            "compiled artifact JSON exceeds the supported JSON integer length "
            "of 640 digits"
        ),
    ):
        load_artifact(io.StringIO(payload))


def test_artifact_line_and_depth_limits_precede_json_decoding() -> None:
    with pytest.raises(ArtifactLimitError, match="line 2"):
        load_artifact(
            io.StringIO("a\r\nbb"),
            limits=replace(DEFAULT_ARTIFACT_LIMITS, max_line_chars=1),
        )

    nested = '{"literal":"[[[{{{","value":' + "[" * 5 + "0" + "]" * 5 + "}"
    with pytest.raises(ArtifactLimitError, match="nesting depth"):
        load_artifact(
            io.StringIO(nested),
            limits=replace(DEFAULT_ARTIFACT_LIMITS, max_json_depth=4),
        )


def test_artifact_canonical_size_boundary_applies_to_direct_values() -> None:
    value = {"text": "café 🧭", "values": [1, True, None]}
    expected = len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )

    assert (
        validate_artifact_value(
            value,
            limits=replace(
                DEFAULT_ARTIFACT_LIMITS,
                max_canonical_bytes=expected,
            ),
        )
        == expected
    )
    with pytest.raises(ArtifactLimitError, match="compiled artifact exceeds"):
        validate_artifact_value(
            value,
            limits=replace(
                DEFAULT_ARTIFACT_LIMITS,
                max_canonical_bytes=expected - 1,
            ),
        )


@pytest.mark.parametrize(
    ("value", "override", "message"),
    [
        (
            {"items": [{}, {}]},
            {"max_items": 1},
            "memory items",
        ),
        (
            {"selected_item_ids": ["one", "two"]},
            {"max_selected_items": 1},
            "selected item ids",
        ),
        (
            {"items": [{"provenance": [{}, {}]}]},
            {"max_provenance_spans": 1},
            "provenance spans",
        ),
        (
            {"verification": {"issues": [{}, {}]}},
            {"max_verification_issues": 1},
            "verification issues",
        ),
    ],
)
def test_artifact_collection_limits_fail_before_schema_walk(
    value: dict[str, object],
    override: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(ArtifactLimitError, match=message):
        validate_artifact_value(
            value,
            limits=replace(DEFAULT_ARTIFACT_LIMITS, **override),
        )


def test_direct_cyclic_artifact_is_rejected_without_recursing_forever() -> None:
    artifact: dict[str, object] = {}
    artifact["self"] = artifact

    with pytest.raises(TypeError, match="cyclic JSON"):
        validate_artifact_value(artifact, limits=DEFAULT_ARTIFACT_LIMITS)

    report = verify_artifact_dict(artifact, [source()])
    assert report["passed"] is False
    assert "invalid_artifact_json" in {issue["code"] for issue in report["issues"]}


def test_verifier_reports_effective_artifact_limits() -> None:
    record, artifact, _ = artifact_payload()
    limits = replace(DEFAULT_ARTIFACT_LIMITS, max_items=17)

    report = verify_artifact_dict(
        artifact,
        [record],
        artifact_limits=limits,
    )

    assert report["passed"] is True
    assert report["verification_artifact_limits"] == limits.to_dict()


@pytest.mark.parametrize("command", ["verify", "inspect"])
def test_cli_artifact_byte_limit_returns_input_error(
    command: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record, _, raw = artifact_payload()
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text(raw, encoding="utf-8", newline="")
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(
        json.dumps([record.to_dict()], ensure_ascii=False),
        encoding="utf-8",
    )
    arguments = [command, str(artifact_path)]
    if command == "verify":
        arguments.append(str(sources_path))
    arguments.extend(
        [
            "--max-artifact-bytes",
            str(artifact_path.stat().st_size - 1),
        ]
    )

    exit_code = main(arguments)

    assert exit_code == 2
    assert "compiled artifact input exceeds" in capsys.readouterr().err


def test_cli_inspect_rejects_ambiguous_artifact_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "artifact.json"
    path.write_text('{"items":[],"items":[]}', encoding="utf-8")

    assert main(["inspect", str(path)]) == 2
    assert "duplicate JSON object key" in capsys.readouterr().err
