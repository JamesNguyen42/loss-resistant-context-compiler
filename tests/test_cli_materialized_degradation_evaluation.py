from __future__ import annotations

import json
from pathlib import Path

from context_compiler.cli import main


def _canonical_line(value: dict[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def test_degradation_evaluation_cli_create_new_and_verify_round_trip(
    tmp_path: Path,
    capsys,
) -> None:
    report_path = tmp_path / "degradation-report.json"
    assert (
        main(
            [
                "evaluate-materialization-degradation",
                "--output",
                str(report_path),
            ]
        )
        == 0
    )
    created = capsys.readouterr()
    assert created.out == ""
    assert created.err == ""
    raw = report_path.read_bytes()
    report = json.loads(raw)
    assert raw == _canonical_line(report)
    assert report["integrity_passed"] is True
    assert report["claim_boundaries"]["inference_status"] == "not_run"
    assert report["claim_boundaries"]["retrieval_status"] == "not_run"

    assert (
        main(
            [
                "evaluate-materialization-degradation",
                "--verify-report",
                str(report_path),
                "--expected-report-sha256",
                report["report_sha256"],
            ]
        )
        == 0
    )
    verified = capsys.readouterr()
    assert verified.err == ""
    assert verified.out.encode("utf-8") == raw


def test_degradation_evaluation_cli_requires_closed_verification_arguments(
    tmp_path: Path,
    capsys,
) -> None:
    assert (
        main(
            [
                "evaluate-materialization-degradation",
                "--expected-report-sha256",
                "0" * 64,
            ]
        )
        == 2
    )
    expected_without_report = capsys.readouterr()
    assert expected_without_report.out == ""
    assert "requires --verify-report" in expected_without_report.err

    report_path = tmp_path / "missing.json"
    assert (
        main(
            [
                "evaluate-materialization-degradation",
                "--verify-report",
                str(report_path),
            ]
        )
        == 2
    )
    report_without_expected = capsys.readouterr()
    assert report_without_expected.out == ""
    assert "requires --expected-report-sha256" in report_without_expected.err
