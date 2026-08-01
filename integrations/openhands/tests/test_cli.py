from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from context_compiler import LocalAIConnector
from store_helpers import append_message, compile_generation, store_path

from ctxc_openhands import cli as cli_module
from ctxc_openhands.cli import main
from ctxc_openhands.fake_runtime import OfflineFakeRuntime
from ctxc_openhands.session import OpenHandsSession
from ctxc_openhands.storage import SQLiteGenerationStore


def _strict_message() -> dict:
    return {
        "kind": "MessageEvent",
        "id": "message-0",
        "timestamp": "2026-07-27T12:00:00+00:00",
        "source": "agent",
        "llm_message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Keep authentication enabled."}],
        },
    }


def _json_stdout(capsys) -> dict:  # type: ignore[no-untyped-def]
    captured = capsys.readouterr()
    assert captured.err == ""
    value = json.loads(captured.out)
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("writer_name", ["report", "evidence"])
def test_structured_stdout_retains_builtin_stringio_contract(
    monkeypatch: pytest.MonkeyPatch,
    writer_name: str,
) -> None:
    output = io.StringIO()
    monkeypatch.setattr(cli_module.sys, "stdout", output)
    value = {"detail": "preserve caf\u00e9, \U0001f9ea, and e\u0301"}

    if writer_name == "report":
        cli_module._write_report(None, value)
    else:
        cli_module._write_evidence_output(None, value)

    assert json.loads(output.getvalue()) == value
    assert output.getvalue().endswith("\n")
    assert not output.getvalue().endswith("\n\n")


@pytest.mark.parametrize("writer_name", ["report", "evidence"])
def test_structured_stdout_bypasses_the_host_text_encoding(
    monkeypatch: pytest.MonkeyPatch,
    writer_name: str,
) -> None:
    class BinaryBuffer:
        def __init__(self) -> None:
            self.payloads: list[bytes] = []
            self.flushes = 0

        def write(self, payload: bytes) -> int:
            self.payloads.append(payload)
            return len(payload)

        def flush(self) -> None:
            self.flushes += 1

    class HostTextOutput:
        def __init__(self) -> None:
            self.buffer = BinaryBuffer()

        def write(self, _value: str) -> int:
            raise AssertionError("the locale text writer must not be used")

    output = HostTextOutput()
    monkeypatch.setattr(cli_module.sys, "stdout", output)
    value = {"detail": "preserve caf\u00e9, \U0001f9ea, and e\u0301"}

    if writer_name == "report":
        cli_module._write_report(None, value)
    else:
        cli_module._write_evidence_output(None, value)

    assert len(output.buffer.payloads) == 1
    assert output.buffer.flushes == 1
    payload = output.buffer.payloads[0]
    assert payload.endswith(b"\n")
    assert not payload.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in payload
    assert b"\x00" not in payload
    assert json.loads(payload.decode("utf-8", errors="strict")) == value


@pytest.mark.parametrize("write_result", [None, True, 0, "short", "oversized"])
def test_structured_stdout_rejects_incomplete_writes_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    write_result: object,
) -> None:
    class RefusingBuffer:
        def __init__(self) -> None:
            self.writes = 0
            self.flushes = 0

        def write(self, payload: bytes) -> object:
            self.writes += 1
            if write_result == "short":
                return len(payload) - 1
            if write_result == "oversized":
                return len(payload) + 1
            return write_result

        def flush(self) -> None:
            self.flushes += 1

    class BinaryOutput:
        def __init__(self) -> None:
            self.buffer = RefusingBuffer()

    output = BinaryOutput()
    monkeypatch.setattr(cli_module.sys, "stdout", output)

    with pytest.raises(
        OSError,
        match="standard output did not accept the complete structured output",
    ):
        cli_module._write_report(None, {"passed": True})

    assert output.buffer.writes == 1
    assert output.buffer.flushes == 0


def test_structured_stdout_flush_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FlushFailureBuffer:
        def __init__(self) -> None:
            self.writes = 0
            self.flushes = 0

        def write(self, payload: bytes) -> int:
            self.writes += 1
            return len(payload)

        def flush(self) -> None:
            self.flushes += 1
            raise OSError("injected structured-output flush failure")

    class BinaryOutput:
        def __init__(self) -> None:
            self.buffer = FlushFailureBuffer()

    output = BinaryOutput()
    monkeypatch.setattr(cli_module.sys, "stdout", output)

    with pytest.raises(OSError, match="injected structured-output flush failure"):
        cli_module._write_report(None, {"passed": True})

    assert output.buffer.writes == 1
    assert output.buffer.flushes == 1


def test_structured_stdout_encoding_failure_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UntouchedBuffer:
        def __init__(self) -> None:
            self.writes = 0

        def write(self, _payload: bytes) -> int:
            self.writes += 1
            raise AssertionError("invalid text must fail before output")

    class BinaryOutput:
        def __init__(self) -> None:
            self.buffer = UntouchedBuffer()

    output = BinaryOutput()
    monkeypatch.setattr(cli_module.sys, "stdout", output)

    with pytest.raises(UnicodeEncodeError):
        cli_module._write_report(None, {"detail": "invalid surrogate: \ud800"})

    assert output.buffer.writes == 0


def test_missing_binary_stdout_returns_a_stable_cli_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TextOnlyOutput:
        def __init__(self) -> None:
            self.writes = 0

        def write(self, _value: str) -> int:
            self.writes += 1
            raise AssertionError("the locale text writer must not be used")

    stdout = TextOnlyOutput()
    stderr = io.StringIO()
    monkeypatch.setattr(cli_module.sys, "stdout", stdout)
    monkeypatch.setattr(cli_module.sys, "stderr", stderr)
    monkeypatch.setattr(
        cli_module,
        "_doctor",
        lambda *, require_live: ({"passed": not require_live}, 0),
    )

    assert main(["doctor"]) == 2

    assert stdout.writes == 0
    diagnostic = json.loads(stderr.getvalue())
    assert diagnostic["passed"] is False
    assert diagnostic["error_type"] == "OSError"
    assert diagnostic["error"] == "standard output does not expose a binary buffer"


def test_durable_report_is_retained_when_its_stdout_receipt_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TextOnlyOutput:
        pass

    report_path = tmp_path / "report-\U0001f9ea.json"
    value = {"detail": "preserve caf\u00e9 and e\u0301"}
    monkeypatch.setattr(cli_module.sys, "stdout", TextOnlyOutput())

    with pytest.raises(
        OSError,
        match="standard output does not expose a binary buffer",
    ):
        cli_module._write_report(str(report_path), value)

    assert json.loads(report_path.read_text(encoding="utf-8")) == value


def test_doctor_reports_offline_ready_and_retains_live_blocker(capsys) -> None:  # type: ignore[no-untyped-def]
    status = main(["doctor"])
    report = _json_stdout(capsys)

    assert status == 0
    assert report["passed"] is True
    assert report["offline_ready"] is True
    assert report["manifest"]["reviewed_api_file_count"] == 23
    assert report["ordinary_import_loaded_openhands"] is False
    assert report["semantic_completeness_claimed"] is False
    assert report["live_ready"] is False
    assert any(
        issue.startswith("retained compatibility blocker: hash-pinned-wheelhouse-absent")
        for issue in report["live"]["issues"]
    )


def test_doctor_require_live_retains_missing_dependency_failure(capsys) -> None:  # type: ignore[no-untyped-def]
    status = main(["doctor", "--require-live"])
    report = _json_stdout(capsys)

    assert report["live_ready"] is False
    assert status == 2
    assert report["passed"] is False


def test_read_and_recovery_commands_never_create_a_missing_store(
    tmp_path: Path,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    database = tmp_path / "missing.sqlite3"
    commands = (
        [
            "explain",
            "--database",
            str(database),
            "--session-id",
            "session-1",
        ],
        [
            "replay",
            "--database",
            str(database),
            "--session-id",
            "session-1",
            "--request-id",
            "request-1",
        ],
        [
            "recover",
            "--database",
            str(database),
            "--session-id",
            "session-1",
        ],
        [
            "rehydrate",
            "--database",
            str(database),
            "--session-id",
            "session-1",
            "--source-id",
            "source-1",
            "--start",
            "0",
            "--end",
            "1",
            "--quote-sha256",
            "0" * 64,
        ],
    )

    for command in commands:
        assert main(command) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        error = json.loads(captured.err)
        assert error["error_type"] == "FileNotFoundError"
        assert "does not exist" in error["error"]
        assert not database.exists()


def test_explain_and_recover_commands(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    database = store_path(tmp_path)
    store = SQLiteGenerationStore(database)
    append_message(store, 0)
    generation_id, _bundle, _replay = compile_generation(
        store,
        1,
        activate=False,
    )

    assert main(
        [
            "explain",
            "--database",
            str(database),
            "--session-id",
            "session-1",
        ]
    ) == 0
    explained = _json_stdout(capsys)
    assert explained["source_count"] == 1
    assert explained["active_generation"] is None
    assert explained["generations"][0]["generation_id"] == generation_id

    assert main(
        [
            "recover",
            "--database",
            str(database),
            "--session-id",
            "session-1",
            "--apply",
        ]
    ) == 0
    recovered = _json_stdout(capsys)
    assert recovered["passed"] is True
    assert recovered["active_generation_id"] == generation_id


def test_stored_request_replay_command(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    database = tmp_path / "store.sqlite3"
    session = OpenHandsSession(
        session_id="cli-session",
        store=SQLiteGenerationStore(database),
        connector_factory=LocalAIConnector,
    )
    session.ingest(_strict_message(), request_id="append")
    session.compact()
    runtime = OfflineFakeRuntime(session)
    request = runtime.prepare(request_id="request-1", current_turn="Continue.")
    runtime.dispatch(request)

    status = main(
        [
            "replay",
            "--database",
            str(database),
            "--session-id",
            "cli-session",
            "--request-id",
            "request-1",
        ]
    )
    report = _json_stdout(capsys)

    assert status == 0
    assert report["passed"] is True
    assert report["final_request_sha256"] == request.final_request_sha256


def test_standalone_exact_request_replay_command(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    session = OpenHandsSession(
        session_id="standalone-cli-session",
        store=SQLiteGenerationStore(tmp_path / "store.sqlite3"),
        connector_factory=LocalAIConnector,
    )
    session.ingest(_strict_message(), request_id="append")
    session.compact()
    request = OfflineFakeRuntime(session).prepare(
        request_id="request-1",
        current_turn="Continue.",
    )
    ledger = tmp_path / "ledger.json"
    ledger.write_text(request.ledger.to_json(), encoding="utf-8")

    status = main(["replay", "--ledger", str(ledger)])
    report = _json_stdout(capsys)

    assert status == 0
    assert report["passed"] is True
    assert report["total_tokens"] == request.total_tokens
    assert report["final_request_sha256"] == request.final_request_sha256
    assert report["ledger_sha256"] == request.ledger.ledger_sha256


def test_rehydrate_command_labels_exact_quote_untrusted(
    tmp_path: Path,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    database = store_path(tmp_path)
    store = SQLiteGenerationStore(database)
    record = append_message(store, 0)
    start = 0
    end = min(20, len(record.content))
    quote = record.content[start:end]

    status = main(
        [
            "rehydrate",
            "--database",
            str(database),
            "--session-id",
            "session-1",
            "--source-id",
            record.id,
            "--start",
            str(start),
            "--end",
            str(end),
            "--quote-sha256",
            hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        ]
    )
    report = _json_stdout(capsys)

    assert status == 0
    assert report["quote"] == quote
    assert report["trust"] == "untrusted-evidence"


def test_replay_rejects_duplicate_json_keys(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    ledger = tmp_path / "ledger.json"
    ledger.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")

    status = main(["replay", "--ledger", str(ledger)])
    captured = capsys.readouterr()
    error = json.loads(captured.err)

    assert status == 2
    assert captured.out == ""
    assert error["passed"] is False
    assert "duplicate JSON key" in error["error"]


def test_small_soak_command_writes_new_report_only(
    tmp_path: Path,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    database = tmp_path / "soak.sqlite3"
    report_path = tmp_path / "soak.json"

    status = main(
        [
            "soak",
            "--database",
            str(database),
            "--events",
            "4",
            "--compactions",
            "2",
            "--output",
            str(report_path),
        ]
    )
    written = _json_stdout(capsys)

    assert status == 0
    assert Path(written["written"]) == report_path
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "passed"

    status = main(
        [
            "soak",
            "--database",
            str(tmp_path / "second.sqlite3"),
            "--events",
            "1",
            "--compactions",
            "1",
            "--output",
            str(report_path),
        ]
    )
    captured = capsys.readouterr()
    assert status == 2
    assert "already exists" in captured.err
