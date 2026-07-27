from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import pickle
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from context_compiler import (
    REDACTION_REPORT_SCHEMA,
    REDACTION_VERIFICATION_SCHEMA,
    SECRET_DETECTOR_NAMES,
    ContextCompiler,
    RedactionError,
    RedactionFinding,
    RedactionLimitError,
    RedactionPolicy,
    RedactionResult,
    SourceRecord,
    redact_sources,
    verify_redaction_report_hash,
    verify_redaction_result,
)
from context_compiler.cli import main
from context_compiler.io import load_sources_path

_LINE_BOUNDARY_CHARACTERS = frozenset(
    "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
)
_PYTHON_LINE_BOUNDARIES = (
    "\n",
    "\r\n",
    "\r",
    "\v",
    "\f",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x85",
    "\u2028",
    "\u2029",
)


def make_source(
    content: str,
    *,
    sequence: int = 0,
    source_id: str = "redaction-source",
    metadata: dict[str, Any] | None = None,
) -> SourceRecord:
    return SourceRecord.create(
        id=source_id,
        sequence=sequence,
        role="user",
        content=content,
        timestamp="2026-07-24T12:00:00+00:00",
        metadata=metadata,
    )


def detector_examples() -> list[tuple[str, str, str]]:
    private_label = "PRIVATE KEY"
    private_key = (
        "-----BEGIN "
        + private_label
        + "-----\n"
        + ("ZmFrZQ" * 5)
        + "\n-----END "
        + private_label
        + "-----"
    )
    openai_style = "sk-" + ("A1_" * 9)
    github_token = "gh" + "p_" + ("Ab2" * 8)
    aws_key = "AK" + "IA" + ("A1" * 8)
    jwt = "eyJ" + ("a" * 8) + "." + ("b" * 9) + "." + ("c" * 10)
    bearer = "Bearer " + ("BearerToken9" * 2)
    basic_value = ("QWxhZGRpbjpvcGVu" * 2) + "=="
    basic = "Basic " + basic_value
    url_secret = "alice:" + ("urlSecret7" * 2)
    url = "https://" + url_secret + "@example.invalid/private"
    assignment_value = "quoted-" + ("value8" * 2)
    assignment = 'password="' + assignment_value + '"'
    return [
        ("pem_private_key", private_key, private_key),
        ("openai_style_key", openai_style, openai_style),
        ("github_token", github_token, github_token),
        ("aws_access_key_id", aws_key, aws_key),
        ("jwt", jwt, jwt),
        ("bearer_token", bearer, bearer.removeprefix("Bearer ")),
        ("basic_authorization", basic, basic_value),
        ("url_userinfo", url, url_secret),
        ("credential_assignment", assignment, assignment_value),
    ]


def test_packaged_report_schema_tracks_runtime_identity_and_detectors() -> None:
    schema_path = (
        Path(__file__).parents[1]
        / "schemas"
        / "redaction-report.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["properties"]["schema"]["const"] == REDACTION_REPORT_SCHEMA
    assert tuple(
        schema["properties"]["policy"]["properties"]["enabled_detectors"][
            "items"
        ]["enum"]
    ) == SECRET_DETECTOR_NAMES
    assert tuple(
        schema["properties"]["findings"]["items"]["properties"]["detector"][
            "enum"
        ]
    ) == SECRET_DETECTOR_NAMES
    assert set(schema["required"]) == set(
        redact_sources([]).to_report()
    )


@pytest.mark.parametrize(
    ("detector", "fragment", "secret"),
    detector_examples(),
    ids=[case[0] for case in detector_examples()],
)
def test_each_fixed_detector_masks_only_its_secret_span(
    detector: str,
    fragment: str,
    secret: str,
) -> None:
    content = "prefix " + fragment + " suffix"
    source = make_source(content)

    result = redact_sources(
        [source],
        policy=RedactionPolicy(enabled_detectors=(detector,)),
    )

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.detector == detector
    assert (finding.start, finding.end) == (
        content.index(secret),
        content.index(secret) + len(secret),
    )
    expected_mask = "".join(
        (
            character
            if character in _LINE_BOUNDARY_CHARACTERS
            else "*"
        )
        for character in secret
    )
    assert (
        result.sources[0].content[finding.start:finding.end]
        == expected_mask
    )
    assert len(result.sources[0].content) == len(source.content)
    assert finding.redacted_characters == sum(
        character not in _LINE_BOUNDARY_CHARACTERS
        for character in secret
    )
    assert finding.line_boundaries_preserved == sum(
        character in _LINE_BOUNDARY_CHARACTERS
        for character in secret
    )


@pytest.mark.parametrize(
    "boundary",
    _PYTHON_LINE_BOUNDARIES,
    ids=[
        "lf",
        "crlf",
        "cr",
        "vertical-tab",
        "form-feed",
        "file-separator",
        "group-separator",
        "record-separator",
        "next-line",
        "line-separator",
        "paragraph-separator",
    ],
)
def test_private_key_redaction_preserves_every_python_line_boundary(
    boundary: str,
) -> None:
    label = "EC PRIVATE KEY"
    secret = (
        "-----BEGIN "
        + label
        + "-----"
        + boundary
        + ("YWJj" * 8)
        + boundary
        + "-----END "
        + label
        + "-----"
    )
    source = make_source("before" + boundary + secret + boundary + "after")

    result = redact_sources(
        [source],
        policy=RedactionPolicy(enabled_detectors=("pem_private_key",)),
    )

    finding = result.findings[0]
    assert result.sources[0].content.count(boundary) == source.content.count(
        boundary
    )
    assert finding.line_boundaries_preserved == 2 * len(boundary)
    for index, character in enumerate(source.content):
        if character in _LINE_BOUNDARY_CHARACTERS:
            assert result.sources[0].content[index] == character


def test_specific_detector_wins_an_exact_overlap_deterministically() -> None:
    secret = "sk-" + ("Overlap9_" * 3)
    source = make_source("api_key=" + secret)

    result = redact_sources(
        [source],
        policy=RedactionPolicy(
            enabled_detectors=(
                "credential_assignment",
                "openai_style_key",
            )
        ),
    )

    assert result.policy.enabled_detectors == (
        "openai_style_key",
        "credential_assignment",
    )
    assert [finding.detector for finding in result.findings] == [
        "openai_style_key"
    ]


def test_common_lookalikes_are_not_redacted() -> None:
    content = " ".join(
        (
            "sk-short",
            "gh" + "p_short",
            "AK" + "IA" + ("A" * 15),
            "eyJshort.segment.segment",
            "Bearer tiny",
            "Basic short",
            "https://alice@example.invalid",
            "password=no",
        )
    )
    source = make_source(content)

    result = redact_sources([source])

    assert result.sources == (source,)
    assert result.findings == ()
    assert result.to_report()["finding_count"] == 0


def test_many_unmatched_private_key_boundaries_do_not_create_findings() -> None:
    unmatched = (
        "-----BEGIN " + "PRIVATE KEY" + "-----x"
    ) * 5_000
    source = make_source(unmatched)

    result = redact_sources(
        [source],
        policy=RedactionPolicy(enabled_detectors=("pem_private_key",)),
    )

    assert result.sources == (source,)
    assert result.findings == ()


def test_nested_private_key_begin_marker_keeps_the_earliest_boundary() -> None:
    begin = "-----BEGIN " + "PRIVATE KEY" + "-----"
    end = "-----END " + "PRIVATE KEY" + "-----"
    content = begin + "\nouter-material\n" + begin + "\nbody\n" + end

    result = redact_sources(
        [make_source(content)],
        policy=RedactionPolicy(enabled_detectors=("pem_private_key",)),
    )

    assert len(result.findings) == 1
    assert result.findings[0].start == 0
    assert result.findings[0].end == len(content)
    assert all(
        character == "*" or character in _LINE_BOUNDARY_CHARACTERS
        for character in result.sources[0].content
    )


def test_source_identity_metadata_and_original_are_outside_content_scope() -> None:
    content_secret = "sk-" + ("Content9_" * 3)
    metadata_secret = "metadata-" + ("private7" * 2)
    source = make_source(
        "token " + content_secret,
        source_id="source-id-is-not-redacted",
        metadata={"credential": metadata_secret, "nested": ["unchanged"]},
    )
    original = source.to_dict()

    result = redact_sources([source])
    redacted = result.sources[0]
    report = result.to_report()
    serialized_report = json.dumps(report, ensure_ascii=False)

    assert source.to_dict() == original
    assert redacted.id == source.id
    assert redacted.sequence == source.sequence
    assert redacted.role == source.role
    assert redacted.timestamp == source.timestamp
    assert redacted.metadata == source.metadata
    assert redacted.content_sha256 != source.content_sha256
    assert redacted.record_sha256 != source.record_sha256
    assert metadata_secret in json.dumps(redacted.to_dict())
    assert content_secret not in serialized_report
    assert (
        hashlib.sha256(content_secret.encode()).hexdigest()
        not in serialized_report
    )
    assert report["scope"] == {
        "content_redacted": True,
        "metadata_redacted": False,
        "source_ids_redacted": False,
        "timestamps_redacted": False,
        "length_preserving": True,
        "line_boundaries_preserved": True,
        "original_content_secret_text_included": False,
        "original_content_secret_hashes_included": False,
    }


@pytest.mark.parametrize("mask", ("*", "#", "█", "■"))
def test_allowed_mask_characters_preserve_unicode_character_offsets(
    mask: str,
) -> None:
    secret = "sk-" + ("Unicode8_" * 3)
    source = make_source("é🧭 " + secret + " 東京")

    result = redact_sources(
        [source],
        policy=RedactionPolicy(
            enabled_detectors=("openai_style_key",),
            mask_character=mask,
        ),
    )
    finding = result.findings[0]

    assert finding.start == source.content.index(secret)
    assert result.sources[0].content[finding.start:finding.end] == (
        mask * len(secret)
    )
    assert len(result.sources[0].content) == len(source.content)


def test_result_report_is_detached_and_pickle_round_trips() -> None:
    source = make_source("password=" + ("pickled8" * 2))
    result = redact_sources([source])
    detached = result.to_report()
    detached["findings"][0]["start"] = 0

    assert result.to_report()["findings"][0]["start"] != 0
    restored = pickle.loads(pickle.dumps(result))
    assert restored == result
    assert restored.to_report() == result.to_report()


def _resign(report: dict[str, Any]) -> None:
    unsigned = copy.deepcopy(report)
    unsigned.pop("report_sha256", None)
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    report["report_sha256"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def test_report_validator_rejects_self_consistent_structural_forgery() -> None:
    source = make_source(
        "password=" + ("first8" * 2) + " password=" + ("second9" * 2)
    )
    report = redact_sources([source]).to_report()
    forged_reports: list[dict[str, Any]] = []

    invalid_scope = copy.deepcopy(report)
    invalid_scope["scope"]["metadata_redacted"] = True
    forged_reports.append(invalid_scope)

    impossible_source_count = copy.deepcopy(report)
    impossible_source_count["source_count"] = 0
    forged_reports.append(impossible_source_count)

    wrong_finding_count = copy.deepcopy(report)
    wrong_finding_count["finding_count"] = 1
    forged_reports.append(wrong_finding_count)

    wrong_span_count = copy.deepcopy(report)
    wrong_span_count["findings"][0]["redacted_characters"] -= 1
    forged_reports.append(wrong_span_count)

    wrong_detector_count = copy.deepcopy(report)
    wrong_detector_count["detector_counts"]["credential_assignment"] += 1
    forged_reports.append(wrong_detector_count)

    unknown_detector = copy.deepcopy(report)
    unknown_detector["policy"]["enabled_detectors"].append("caller_regex")
    forged_reports.append(unknown_detector)

    unsorted_findings = copy.deepcopy(report)
    unsorted_findings["findings"].reverse()
    forged_reports.append(unsorted_findings)

    for forged in forged_reports:
        _resign(forged)
        with pytest.raises(RedactionError):
            verify_redaction_report_hash(forged)


def test_report_hash_and_exact_replay_detect_tampering() -> None:
    secret = "gh" + "p_" + ("Replay8" * 4)
    source = make_source(secret)
    result = redact_sources([source])
    report = result.to_report()
    report["report_sha256"] = "0" * 64

    with pytest.raises(RedactionError, match="SHA-256 mismatch"):
        verify_redaction_report_hash(report)

    verification = verify_redaction_result([source], result)
    assert verification == {
        "schema": REDACTION_VERIFICATION_SCHEMA,
        "verified": True,
        "source_count": 1,
        "finding_count": 1,
        "redacted_source_digest": result.to_report()[
            "redacted_source_digest"
        ],
        "report_sha256": result.to_report()["report_sha256"],
    }

    changed = make_source(
        "ordinary replacement",
        source_id=source.id,
        sequence=source.sequence,
    )
    with pytest.raises(RedactionError, match="do not match replay"):
        verify_redaction_result([changed], result)


@pytest.mark.parametrize(
    ("kwargs", "exception"),
    (
        ({"enabled_detectors": "jwt"}, TypeError),
        ({"enabled_detectors": ()}, ValueError),
        ({"enabled_detectors": ("jwt", "jwt")}, ValueError),
        ({"enabled_detectors": ("caller_regex",)}, ValueError),
        ({"mask_character": "x"}, ValueError),
        ({"mask_character": "**"}, ValueError),
        ({"max_sources": True}, TypeError),
        ({"max_sources": 0}, ValueError),
        ({"max_findings_per_source": True}, TypeError),
        ({"max_total_findings": 0}, ValueError),
        (
            {
                "max_findings_per_source": 2,
                "max_total_findings": 1,
            },
            ValueError,
        ),
        (
            {
                "max_source_chars": 2,
                "max_total_chars": 1,
            },
            ValueError,
        ),
    ),
)
def test_policy_rejects_unsafe_or_ambiguous_configuration(
    kwargs: dict[str, Any],
    exception: type[BaseException],
) -> None:
    with pytest.raises(exception):
        RedactionPolicy(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    (
        {"source_sequence": True},
        {"source_sequence": -1},
        {"source_id": ""},
        {"start": -1},
        {"end": 0},
        {"detector": "caller_regex"},
        {"redacted_characters": 0},
        {"line_boundaries_preserved": -1},
        {"redacted_characters": 5},
    ),
)
def test_finding_rejects_invalid_coordinates(kwargs: dict[str, Any]) -> None:
    values: dict[str, Any] = {
        "source_sequence": 0,
        "source_id": "source",
        "start": 0,
        "end": 4,
        "detector": "jwt",
        "redacted_characters": 4,
        "line_boundaries_preserved": 0,
    }
    values.update(kwargs)

    with pytest.raises((TypeError, ValueError)):
        RedactionFinding(**values)


def test_source_and_total_character_limits_fail_closed() -> None:
    with pytest.raises(RedactionLimitError, match="max_source_chars"):
        redact_sources(
            [make_source("12345")],
            policy=RedactionPolicy(
                max_source_chars=4,
                max_total_chars=8,
            ),
        )

    with pytest.raises(RedactionLimitError, match="max_total_chars"):
        redact_sources(
            [
                make_source("1234", source_id="first", sequence=0),
                make_source("5678", source_id="second", sequence=1),
            ],
            policy=RedactionPolicy(
                max_source_chars=4,
                max_total_chars=6,
            ),
        )


def test_source_count_limit_stops_an_iterable_before_materializing_it() -> None:
    observed: list[int] = []

    def records() -> Iterator[SourceRecord]:
        for index in range(10):
            observed.append(index)
            yield make_source(
                "ordinary",
                source_id=f"source-{index}",
                sequence=index,
            )

    with pytest.raises(RedactionLimitError, match="max_sources"):
        redact_sources(
            records(),
            policy=RedactionPolicy(max_sources=2),
        )

    assert observed == [0, 1, 2]


def test_candidate_per_source_and_total_finding_limits_fail_closed() -> None:
    two_secrets = (
        "password="
        + ("first8" * 2)
        + " password="
        + ("second9" * 2)
    )
    with pytest.raises(RedactionLimitError, match="candidates"):
        redact_sources(
            [make_source(two_secrets)],
            policy=RedactionPolicy(
                enabled_detectors=("credential_assignment",),
                max_findings_per_source=1,
                max_total_findings=1,
            ),
        )

    with pytest.raises(RedactionLimitError, match="max_total_findings"):
        redact_sources(
            [
                make_source(two_secrets, source_id="first", sequence=0),
                make_source(two_secrets, source_id="second", sequence=1),
            ],
            policy=RedactionPolicy(
                enabled_detectors=("credential_assignment",),
                max_findings_per_source=2,
                max_total_findings=2,
            ),
        )


def test_source_identity_and_integrity_fail_closed() -> None:
    first = make_source("ordinary", source_id="same", sequence=0)
    duplicate_id = make_source("other", source_id="same", sequence=1)
    duplicate_sequence = make_source("other", source_id="other", sequence=0)

    with pytest.raises(RedactionError, match="ids must be unique"):
        redact_sources([first, duplicate_id])
    with pytest.raises(RedactionError, match="sequences must be unique"):
        redact_sources([first, duplicate_sequence])

    object.__setattr__(first, "content", "changed after hashing")
    with pytest.raises(ValueError, match="content hash mismatch"):
        redact_sources([first])


def test_redacted_sources_compile_with_exact_redacted_provenance() -> None:
    secret = "sk-" + ("Compile8_" * 3)
    original = make_source(
        "constraint: Connect using credential " + secret + " during testing"
    )
    redaction = redact_sources([original])

    memory = ContextCompiler().compile(redaction.sources)
    artifact_text = json.dumps(memory.to_dict(), ensure_ascii=False)

    assert memory.verification.passed
    assert secret not in artifact_text
    for item in memory.items:
        for span in item.provenance:
            source = redaction.sources[0]
            assert span.quote == source.content[span.start:span.end]


def test_public_result_constructor_rejects_mismatched_report() -> None:
    source = make_source("password=" + ("construct8" * 2))
    result = redact_sources([source])
    report = result.to_report()
    report["source_count"] = 2
    _resign(report)

    with pytest.raises(RedactionError, match="source count"):
        RedactionResult(
            sources=result.sources,
            findings=result.findings,
            policy=result.policy,
            _report_json=json.dumps(report),
        )


def test_cli_writes_redacted_jsonl_and_self_hashed_report_atomically(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "sk-" + ("CliSecret8_" * 3)
    source_path = tmp_path / "source.json"
    output_path = tmp_path / "redacted" / "sources.jsonl"
    report_path = tmp_path / "reports" / "redaction.json"
    source_path.write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "content": "credential " + secret,
                    "metadata": {"retained": True},
                }
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "redact",
            str(source_path),
            "-o",
            str(output_path),
            "--report",
            str(report_path),
        ]
    )
    captured = capsys.readouterr()
    output_text = output_path.read_text(encoding="utf-8")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert captured.out == ""
    assert captured.err == ""
    assert secret in source_path.read_text(encoding="utf-8")
    assert secret not in output_text
    assert secret not in report_path.read_text(encoding="utf-8")
    assert len(load_sources_path(output_path)) == 1
    assert verify_redaction_report_hash(report)["schema"] == (
        REDACTION_REPORT_SCHEMA
    )
    assert list(tmp_path.rglob(".ctxc-*.tmp")) == []


def test_cli_supports_stdin_and_a_fixed_detector_subset(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_secret = "gh" + "p_" + ("Selected8" * 3)
    unselected_secret = "sk-" + ("Unselected9_" * 3)
    payload = json.dumps(
        [
            {
                "role": "user",
                "content": selected_secret + " " + unselected_secret,
            }
        ]
    )
    output_path = tmp_path / "sources.jsonl"
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))

    assert (
        main(
            [
                "redact",
                "-",
                "-o",
                str(output_path),
                "--report",
                str(report_path),
                "--detector",
                "github_token",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    output = output_path.read_text(encoding="utf-8")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert captured.out == captured.err == ""
    assert selected_secret not in output
    assert unselected_secret in output
    assert report["policy"]["enabled_detectors"] == ["github_token"]
    assert report["policy"]["max_sources"] == 100_000


def test_cli_refuses_path_aliases_before_overwriting(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_path = tmp_path / "source.json"
    report_path = tmp_path / "report.json"
    source_path.write_text(
        json.dumps([{"role": "user", "content": "ordinary"}]),
        encoding="utf-8",
    )
    original = source_path.read_text(encoding="utf-8")

    assert (
        main(
            [
                "redact",
                str(source_path),
                "-o",
                str(source_path),
                "--report",
                str(report_path),
            ]
        )
        == 2
    )
    assert source_path.read_text(encoding="utf-8") == original
    assert not report_path.exists()
    assert "refuses to overwrite" in capsys.readouterr().err

    output_path = tmp_path / "same.jsonl"
    assert (
        main(
            [
                "redact",
                str(source_path),
                "-o",
                str(output_path),
                "--report",
                str(output_path),
            ]
        )
        == 2
    )
    assert not output_path.exists()
    assert "different paths" in capsys.readouterr().err


def test_cli_refuses_an_existing_hardlink_to_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_path = tmp_path / "source.json"
    output_path = tmp_path / "hardlink.json"
    report_path = tmp_path / "report.json"
    source_path.write_text(
        json.dumps([{"role": "user", "content": "ordinary"}]),
        encoding="utf-8",
    )
    try:
        os.link(source_path, output_path)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    assert (
        main(
            [
                "redact",
                str(source_path),
                "-o",
                str(output_path),
                "--report",
                str(report_path),
            ]
        )
        == 2
    )
    assert output_path.read_text(encoding="utf-8") == (
        source_path.read_text(encoding="utf-8")
    )
    assert not report_path.exists()
    assert "refuses to overwrite" in capsys.readouterr().err


def test_cli_reserves_dash_for_input_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_path = tmp_path / "source.json"
    report_path = tmp_path / "report.json"
    source_path.write_text("[]", encoding="utf-8")

    assert (
        main(
            [
                "redact",
                str(source_path),
                "-o",
                "-",
                "--report",
                str(report_path),
            ]
        )
        == 2
    )
    assert not report_path.exists()
    assert "supported only for input" in capsys.readouterr().err


def test_cli_help_and_invalid_mask_are_ascii_console_safe(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).parents[1]
    environment = dict(os.environ)
    existing_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(repository / "src")
        + (
            os.pathsep + existing_python_path
            if existing_python_path
            else ""
        )
    )
    environment["PYTHONIOENCODING"] = "cp1252"

    help_run = subprocess.run(
        [sys.executable, "-m", "context_compiler", "redact", "--help"],
        cwd=repository,
        env=environment,
        capture_output=True,
        check=False,
    )
    assert help_run.returncode == 0
    assert help_run.stderr == b""
    assert b"U+2588" in help_run.stdout

    invalid_run = subprocess.run(
        [
            sys.executable,
            "-m",
            "context_compiler",
            "redact",
            str(tmp_path / "input.json"),
            "-o",
            str(tmp_path / "output.jsonl"),
            "--report",
            str(tmp_path / "report.json"),
            "--mask-character",
            "x",
        ],
        cwd=repository,
        env=environment,
        capture_output=True,
        check=False,
    )
    assert invalid_run.returncode == 2
    assert b"mask must be" in invalid_run.stderr
    assert b"UnicodeEncodeError" not in invalid_run.stderr


def test_cli_redaction_limit_has_structured_resource_diagnostic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_path = tmp_path / "source.json"
    output_path = tmp_path / "output.jsonl"
    report_path = tmp_path / "report.json"
    source_path.write_text(
        json.dumps([{"role": "user", "content": "too long"}]),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "redact",
            str(source_path),
            "-o",
            str(output_path),
            "--report",
            str(report_path),
            "--max-redaction-source-chars",
            "3",
            "--max-redaction-total-chars",
            "3",
            "--error-format",
            "json",
        ]
    )
    diagnostic = json.loads(capsys.readouterr().err)

    assert exit_code == 2
    assert diagnostic["command"] == "redact"
    assert diagnostic["category"] == "resource_limit"
    assert diagnostic["code"] == "resource_limit_exceeded"
    assert diagnostic["exception_type"] == "RedactionLimitError"
    assert not output_path.exists()
    assert not report_path.exists()


def test_empty_input_produces_empty_jsonl_and_valid_report(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "empty.json"
    output_path = tmp_path / "empty.jsonl"
    report_path = tmp_path / "report.json"
    source_path.write_text("[]", encoding="utf-8")

    assert (
        main(
            [
                "redact",
                str(source_path),
                "-o",
                str(output_path),
                "--report",
                str(report_path),
            ]
        )
        == 0
    )
    report = verify_redaction_report_hash(
        json.loads(report_path.read_text(encoding="utf-8"))
    )

    assert output_path.read_text(encoding="utf-8") == ""
    assert report["source_count"] == 0
    assert report["finding_count"] == 0
