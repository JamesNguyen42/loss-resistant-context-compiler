from __future__ import annotations

import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from context_compiler import (
    ContextCompiler,
    SourceArchive,
    SourceLimitError,
    SourceLimits,
    SourceRecord,
)
from context_compiler.cli import main
from context_compiler.io import load_sources, load_sources_path, verify_artifact_dict
from context_compiler.limits import (
    DEFAULT_SOURCE_LIMITS,
    bounded_json_utf8_size,
    source_value_size,
)


def source(sequence: int, content: str = "goal: stay bounded") -> SourceRecord:
    return SourceRecord.create(
        id=f"source-{sequence}",
        sequence=sequence,
        role="user",
        content=content,
    )


def resign_artifact(artifact: dict[str, object]) -> None:
    unsigned = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    artifact["artifact_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("max_input_bytes", True, TypeError),
        ("max_records", 1.5, TypeError),
        ("max_line_chars", 0, ValueError),
        ("max_record_bytes", -1, ValueError),
        ("max_total_record_bytes", "64", TypeError),
        ("max_json_depth", False, TypeError),
    ],
)
def test_source_limits_require_positive_non_boolean_integers(
    field: str,
    value: object,
    exception: type[Exception],
) -> None:
    with pytest.raises(exception):
        SourceLimits(**{field: value})


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        123456,
        -0.25,
        'quote " slash \\ controls \b\f\n\r\t\x00',
        "Unicode 🧭 café",
        {"nested": [1, 2.5, None, {"ok": True}]},
    ],
)
def test_bounded_json_size_matches_compact_utf8_encoding(value: object) -> None:
    expected = len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )

    assert (
        bounded_json_utf8_size(
            value,
            max_bytes=expected,
            max_depth=16,
            label="fixture",
        )
        == expected
    )
    with pytest.raises(SourceLimitError, match="fixture exceeds"):
        bounded_json_utf8_size(
            value,
            max_bytes=expected - 1,
            max_depth=16,
            label="fixture",
        )


def test_text_input_enforces_exact_utf8_byte_boundary() -> None:
    payload = json.dumps(
        {"role": "user", "content": "goal: preserve 🧭"},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    byte_count = len(payload.encode("utf-8"))

    loaded = load_sources(
        io.StringIO(payload),
        limits=replace(DEFAULT_SOURCE_LIMITS, max_input_bytes=byte_count),
    )

    assert loaded[0].content == "goal: preserve 🧭"
    with pytest.raises(SourceLimitError, match="source input exceeds"):
        load_sources(
            io.StringIO(payload),
            limits=replace(DEFAULT_SOURCE_LIMITS, max_input_bytes=byte_count - 1),
        )


def test_path_and_stream_reject_overlong_physical_lines(tmp_path: Path) -> None:
    payload = '{"role":"user","content":"goal: bounded"}\n'
    path = tmp_path / "history.jsonl"
    path.write_text(payload, encoding="utf-8", newline="")
    max_chars = len(payload.rstrip("\n")) - 1
    limits = replace(DEFAULT_SOURCE_LIMITS, max_line_chars=max_chars)

    with pytest.raises(SourceLimitError, match="line 1"):
        load_sources(io.StringIO(payload), json_lines=True, limits=limits)
    with pytest.raises(SourceLimitError, match="line 1"):
        load_sources_path(path, limits=limits)


def test_crlf_standalone_cr_and_unbalanced_json_fail_at_the_right_boundary() -> None:
    limits = replace(DEFAULT_SOURCE_LIMITS, max_line_chars=1)
    for payload in ("a\r\nbb", "a\rbb"):
        with pytest.raises(SourceLimitError, match="line 2"):
            load_sources(io.StringIO(payload), json_lines=True, limits=limits)

    with pytest.raises(ValueError, match="invalid JSON source record"):
        load_sources(io.StringIO("[{]"), json_lines=True)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            '{"role":"user","role":"tool","content":"goal: bounded"}',
            "duplicate JSON object key",
        ),
        (
            '{"role":"user","content":"goal: bounded","metadata":{"score":NaN}}',
            "non-standard JSON constant",
        ),
        (
            '{"role":"user","content":"goal: bounded","metadata":{"score":1e309}}',
            "JSON number must be finite",
        ),
    ],
)
def test_source_json_rejects_ambiguous_or_nonfinite_values(
    payload: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        load_sources(io.StringIO(payload), json_lines=False)


def test_source_json_nesting_failure_is_normalized_to_input_error() -> None:
    payload = (
        '{"role":"user","content":"goal: bounded","metadata":'
        + "[" * 2_000
        + "0"
        + "]" * 2_000
        + "}"
    )

    with pytest.raises(ValueError, match="supported nesting depth"):
        load_sources(io.StringIO(payload), json_lines=False)


def test_json_depth_scan_ignores_brackets_inside_strings() -> None:
    payload = json.dumps(
        {
            "role": "user",
            "content": "[[[{{{literal text}}}]]]",
        }
    )

    records = load_sources(
        io.StringIO(payload),
        json_lines=False,
        limits=replace(DEFAULT_SOURCE_LIMITS, max_json_depth=2),
    )

    assert records[0].content == "[[[{{{literal text}}}]]]"


@pytest.mark.parametrize("json_lines", [False, True])
def test_serialized_record_count_is_bounded_for_json_and_jsonl(
    json_lines: bool,
) -> None:
    records = [
        {"role": "user", "content": "goal: one"},
        {"role": "user", "content": "goal: two"},
    ]
    payload = (
        "\n".join(json.dumps(record) for record in records) if json_lines else json.dumps(records)
    )

    with pytest.raises(SourceLimitError, match="record count exceeds 1"):
        load_sources(
            io.StringIO(payload),
            json_lines=json_lines,
            limits=replace(DEFAULT_SOURCE_LIMITS, max_records=1),
        )


def test_direct_python_input_bounds_records_before_conversion() -> None:
    oversized = {
        "role": "tool",
        "content": "result",
        "tool_schema": {"blob": "x" * 1_000},
    }
    limits = replace(DEFAULT_SOURCE_LIMITS, max_record_bytes=300)

    with pytest.raises(SourceLimitError, match="source record 0"):
        ContextCompiler(source_limits=limits).compile([oversized])


def test_direct_python_input_bounds_total_canonical_record_bytes() -> None:
    first = source(0)
    second = source(1)
    first_size = source_value_size(
        first.to_dict(),
        limits=DEFAULT_SOURCE_LIMITS,
        index=0,
    )
    second_size = source_value_size(
        second.to_dict(),
        limits=DEFAULT_SOURCE_LIMITS,
        index=1,
    )
    limits = replace(
        DEFAULT_SOURCE_LIMITS,
        max_total_record_bytes=first_size + second_size - 1,
    )

    with pytest.raises(SourceLimitError, match="total UTF-8 JSON bytes"):
        ContextCompiler(source_limits=limits).compile([first, second])


def test_high_record_count_generator_stops_at_first_excess_record() -> None:
    yielded: list[int] = []

    def records():
        for sequence in range(10):
            yielded.append(sequence)
            yield {"role": "user", "content": f"goal: {sequence}"}

    with pytest.raises(SourceLimitError, match="record count exceeds 2"):
        ContextCompiler(source_limits=replace(DEFAULT_SOURCE_LIMITS, max_records=2)).compile(
            records()
        )

    assert yielded == [0, 1, 2]


def test_verifier_applies_limits_to_direct_source_lists() -> None:
    records = [source(0), source(1)]
    artifact = ContextCompiler().compile(records).to_dict()

    with pytest.raises(SourceLimitError, match="record count exceeds 1"):
        verify_artifact_dict(
            artifact,
            records,
            source_limits=replace(DEFAULT_SOURCE_LIMITS, max_records=1),
        )


def test_verifier_reports_effective_limits_and_rejects_forged_limit_shape() -> None:
    record = source(0)
    artifact = ContextCompiler().compile([record]).to_dict()
    limits = replace(DEFAULT_SOURCE_LIMITS, max_records=7)

    report = verify_artifact_dict(artifact, [record], source_limits=limits)
    assert report["verification_source_limits"] == limits.to_dict()

    artifact["compiler_metadata"]["source_limits"]["max_records"] = True
    resign_artifact(artifact)
    forged = verify_artifact_dict(artifact, [record], source_limits=limits)
    assert forged["passed"] is False
    assert "invalid_source_limits" in {issue["code"] for issue in forged["issues"]}


def test_archive_size_limit_refuses_append_without_partial_write(tmp_path: Path) -> None:
    first = source(0, "goal: first")
    second = source(1, "goal: second")
    first_payload = (
        json.dumps(first.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    limits = replace(
        DEFAULT_SOURCE_LIMITS,
        max_input_bytes=len(first_payload),
    )
    archive = SourceArchive(tmp_path / "archive", source_limits=limits)

    assert archive.append([first]) == 1
    before = archive.events_path.read_bytes()
    with pytest.raises(SourceLimitError, match="archive exceeds"):
        archive.append([second])

    assert archive.events_path.read_bytes() == before
    assert archive.load() == [first]


def test_archive_combined_record_limit_refuses_append_atomically(tmp_path: Path) -> None:
    archive = SourceArchive(
        tmp_path / "archive",
        source_limits=replace(DEFAULT_SOURCE_LIMITS, max_records=1),
    )
    first = source(0)

    assert archive.append([first]) == 1
    before = archive.events_path.read_bytes()
    with pytest.raises(SourceLimitError, match="record count exceeds 1"):
        archive.append([source(1)])

    assert archive.events_path.read_bytes() == before


def test_compiler_records_effective_source_limits_in_artifact() -> None:
    limits = replace(
        DEFAULT_SOURCE_LIMITS,
        max_input_bytes=123_456,
        max_records=123,
    )

    artifact = ContextCompiler(source_limits=limits).compile([source(0)])

    assert artifact.compiler_metadata["source_limits"] == limits.to_dict()


def test_cli_source_limit_override_fails_with_input_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "history.json"
    path.write_text(
        json.dumps([{"role": "user", "content": "goal: stay bounded"}]),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "compile",
            str(path),
            "--max-source-bytes",
            str(path.stat().st_size - 1),
        ]
    )

    assert exit_code == 2
    assert "source input exceeds" in capsys.readouterr().err


def test_binary_looking_tool_output_is_bounded_as_record_data() -> None:
    record = {
        "role": "tool",
        "content": "\x00\x01\x02" * 500,
    }

    with pytest.raises(SourceLimitError, match="source record 0"):
        ContextCompiler(source_limits=replace(DEFAULT_SOURCE_LIMITS, max_record_bytes=400)).compile(
            [record]
        )
