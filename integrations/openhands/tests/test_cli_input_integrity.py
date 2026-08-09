from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import ctxc_openhands.cli as cli_module
from ctxc_openhands.cli import _load_json, main


def test_cli_json_loader_rejects_hardlink_and_symlink_aliases(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"value":1}', encoding="utf-8")
    hardlink = tmp_path / "hardlink.json"
    try:
        os.link(source, hardlink)
    except OSError as exc:
        pytest.skip(f"hard-link creation is unavailable: {exc}")

    with pytest.raises(ValueError, match="hard-link aliases"):
        _load_json(source)

    hardlink.unlink()
    symlink = tmp_path / "symlink.json"
    try:
        symlink.symlink_to(source)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(ValueError, match="symbolic link|reparse point"):
        _load_json(symlink)


def test_cli_json_loader_rejects_post_open_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.json"
    replacement = tmp_path / "replacement.json"
    source.write_text('{"value":1}', encoding="utf-8")
    replacement.write_text('{"value":2}', encoding="utf-8")
    real_identity = cli_module._regular_file_identity
    calls = 0

    def replacing_identity(path: Path, *, label: str) -> tuple[int, int]:
        nonlocal calls
        calls += 1
        if calls == 2:
            os.replace(replacement, source)
        return real_identity(path, label=label)

    monkeypatch.setattr(cli_module, "_regular_file_identity", replacing_identity)

    with pytest.raises(ValueError, match="identity or bytes changed"):
        _load_json(source)


def test_cli_json_loader_rejects_in_place_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"value":1}', encoding="utf-8")
    original_mtime = source.stat().st_mtime_ns
    real_identity = cli_module._regular_file_identity
    calls = 0

    def mutating_identity(path: Path, *, label: str) -> tuple[int, int]:
        nonlocal calls
        calls += 1
        if calls == 2:
            source.write_text('{"value":2}', encoding="utf-8")
            os.utime(
                source,
                ns=(original_mtime + 2_000_000_000, original_mtime + 2_000_000_000),
            )
        return real_identity(path, label=label)

    monkeypatch.setattr(cli_module, "_regular_file_identity", mutating_identity)

    with pytest.raises(ValueError, match="identity or bytes changed"):
        _load_json(source)


def test_explain_preserves_red_integrity_report_and_exits_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = {
        "schema": "ctxc-openhands-explain-0.1",
        "integrity": {"passed": False, "issues": ["forced-red-integrity"]},
    }
    monkeypatch.setattr(cli_module, "_explain", lambda _database, _session_id: report)

    status = main(
        [
            "explain",
            "--database",
            "unused.sqlite3",
            "--session-id",
            "session",
        ]
    )
    captured = capsys.readouterr()

    assert status == 2
    assert captured.err == ""
    assert json.loads(captured.out) == report


@pytest.mark.parametrize(
    ("command", "producer_name"),
    [
        ("offline-scenario", "run_offline_crash_scenario"),
        ("soak", "run_deterministic_soak"),
        ("fault-campaign", "run_crash_concurrency_campaign"),
    ],
)
def test_generated_evidence_commands_exit_two_when_database_gate_is_red(
    command: str,
    producer_name: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = {"schema": f"forced-{command}", "retained": True}

    def producer(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return report

    monkeypatch.setattr(cli_module, producer_name, producer)
    monkeypatch.setattr(
        cli_module,
        "_verify_evidence_mapping",
        lambda _report, *, database: {
            "passed": False,
            "database_verified": False,
            "issues": [f"forced-red:{database}"],
        },
    )

    status = main([command, "--database", "unused.sqlite3"])
    captured = capsys.readouterr()

    assert status == 2
    assert captured.err == ""
    assert json.loads(captured.out) == report


def test_malformed_existing_database_is_structured_exit_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "malformed.sqlite3"
    original = b"not-a-sqlite-database"
    database.write_bytes(original)

    status = main(
        [
            "explain",
            "--database",
            str(database),
            "--session-id",
            "session",
        ]
    )
    captured = capsys.readouterr()
    error = json.loads(captured.err)

    assert status == 2
    assert captured.out == ""
    assert error["passed"] is False
    assert error["error_type"]
    assert error["error"]
    assert database.read_bytes() == original


def test_exception_with_broken_string_rendering_is_still_structured(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class BrokenTextError(ValueError):
        def __str__(self) -> str:
            raise RuntimeError("broken __str__")

    def fail_to_load(_path: str | Path) -> Any:
        raise BrokenTextError()

    monkeypatch.setattr(cli_module, "_load_json", fail_to_load)

    status = main(["replay", "--ledger", "unused.json"])
    captured = capsys.readouterr()
    error = json.loads(captured.err)

    assert status == 2
    assert captured.out == ""
    assert error == {
        "error": "<exception text unavailable>",
        "error_type": "BrokenTextError",
        "passed": False,
    }
