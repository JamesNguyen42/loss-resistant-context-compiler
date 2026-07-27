from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ctxc_openhands.cli import (
    MAX_CLI_JSON_ITEMS,
    _write_evidence_output,
    _write_report,
    main,
)
from ctxc_openhands.evidence import load_evidence_report


def test_cli_replay_rejects_deep_and_flat_json_shapes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    deep = tmp_path / "deep.json"
    deep.write_bytes(b'{"x":' + b"[" * 70 + b"0" + b"]" * 70 + b"}")
    flat = tmp_path / "flat.json"
    flat.write_bytes(
        b'{"x":['
        + b",".join(b"0" for _ in range(MAX_CLI_JSON_ITEMS + 1))
        + b"]}"
    )

    deep_status = main(["replay", "--ledger", str(deep)])
    deep_output = capsys.readouterr()
    flat_status = main(["replay", "--ledger", str(flat)])
    flat_output = capsys.readouterr()

    assert deep_status == 2
    assert deep_output.out == ""
    assert "depth" in json.loads(deep_output.err)["error"]
    assert flat_status == 2
    assert flat_output.out == ""
    assert "item count" in json.loads(flat_output.err)["error"]


def test_cli_scenario_canonical_round_trip_and_exact_producer_argv(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "scenario.sqlite3"
    report_path = tmp_path / "scenario.json"
    argv = [
        "offline-scenario",
        "--database",
        str(database),
        "--output",
        str(report_path),
    ]

    status = main(argv)
    receipt = json.loads(capsys.readouterr().out)
    report = load_evidence_report(report_path)

    assert status == 0
    assert receipt == {"written": str(report_path.absolute())}
    assert report["command"]["producer"] == "cli"
    assert report["command"]["name"] == "ctxc-openhands"
    assert report["command"]["argv"] == argv

    json_only_status = main(
        ["verify-evidence", "--report", str(report_path)]
    )
    json_only = json.loads(capsys.readouterr().out)
    assert json_only_status == 2
    assert json_only["passed"] is False
    assert json_only["report_verified"] is True
    assert json_only["database_verified"] is False

    bound_status = main(
        [
            "verify-evidence",
            "--report",
            str(report_path),
            "--database",
            str(database),
        ]
    )
    bound = json.loads(capsys.readouterr().out)
    assert bound_status == 0
    assert bound["passed"] is True
    assert bound["database_verified"] is True
    assert bound["attestation_claimed"] is False
    assert bound["semantic_completeness_claimed"] is False


def test_cli_report_outputs_are_exclusive_under_preexisting_and_race(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    retained = tmp_path / "retained.json"
    retained.write_bytes(b"retained-failed-run")

    with pytest.raises(FileExistsError, match="already exists"):
        _write_report(str(retained), {"passed": True})
    with pytest.raises(FileExistsError, match="already exists"):
        _write_evidence_output(str(retained), {"passed": True})
    assert retained.read_bytes() == b"retained-failed-run"

    raced = tmp_path / "raced.json"

    def write_once() -> str:
        try:
            _write_report(str(raced), {"passed": True})
        except FileExistsError:
            return "exists"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _index: write_once(), range(2)))

    capsys.readouterr()
    assert outcomes == ["created", "exists"]
    assert json.loads(raced.read_text(encoding="utf-8")) == {"passed": True}
