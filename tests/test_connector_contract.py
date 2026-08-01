from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from context_compiler import (
    CONNECTOR_REQUEST_SCHEMA,
    CONNECTOR_RESPONSE_SCHEMA,
    LocalAIConnector,
    decode_connector_request,
)
from context_compiler.cli import main

ROOT = Path(__file__).resolve().parents[1]


def request(
    request_id: str,
    operation: str,
    payload: dict | None = None,
) -> dict:
    return {
        "schema": CONNECTOR_REQUEST_SCHEMA,
        "request_id": request_id,
        "operation": operation,
        "payload": payload or {},
    }


def assert_response_envelope(
    response: dict,
    *,
    request_id: str | None,
    operation: str | None,
    ok: bool,
) -> None:
    assert set(response) == {
        "schema",
        "request_id",
        "operation",
        "ok",
        "result",
        "error",
    }
    assert response["schema"] == CONNECTOR_RESPONSE_SCHEMA
    assert response["request_id"] == request_id
    assert response["operation"] == operation
    assert response["ok"] is ok
    assert (response["result"] is None) is not (response["error"] is None)
    if not ok:
        assert set(response["error"]) == {
            "category",
            "code",
            "message",
            "retryable",
            "details",
        }


def test_shared_envelope_contract_covers_every_connector_operation() -> None:
    connector = LocalAIConnector()

    capabilities = connector.handle_request(request("1", "capabilities"))
    assert_response_envelope(
        capabilities,
        request_id="1",
        operation="capabilities",
        ok=True,
    )
    assert capabilities["result"]["operations"] == [
        "capabilities",
        "ingest_source_events",
        "compile_memory",
        "render_context",
        "verify_memory",
        "inspect_memory",
    ]

    ingest = connector.handle_request(
        request(
            "2",
            "ingest_source_events",
            {
                "session_id": "contract-session",
                "events": [
                    {
                        "schema": "localai-source-event-0.1",
                        "id": "event-0",
                        "sequence": 0,
                        "role": "user",
                        "content": "constraint: The database stays PostgreSQL.",
                    }
                ],
            },
        )
    )
    assert_response_envelope(
        ingest,
        request_id="2",
        operation="ingest_source_events",
        ok=True,
    )
    assert ingest["result"]["accepted_events"] == 1

    compile_response = connector.handle_request(
        request(
            "3",
            "compile_memory",
            {"session_id": "contract-session"},
        )
    )
    assert_response_envelope(
        compile_response,
        request_id="3",
        operation="compile_memory",
        ok=True,
    )
    bundle = compile_response["result"]["bundle"]
    checkpoint = compile_response["result"]["checkpoint"]
    assert bundle["schema"] == "localai-context-bundle-0.1"
    assert checkpoint["schema"] == "ctxc-incremental-checkpoint-0.1"

    render = connector.handle_request(
        request("4", "render_context", {"bundle": bundle})
    )
    assert_response_envelope(
        render,
        request_id="4",
        operation="render_context",
        ok=True,
    )
    assert "The database stays PostgreSQL." in render["result"]["context"]

    verify = connector.handle_request(
        request(
            "5",
            "verify_memory",
            {"bundle": bundle, "checkpoint": checkpoint},
        )
    )
    assert_response_envelope(
        verify,
        request_id="5",
        operation="verify_memory",
        ok=True,
    )
    assert verify["result"]["passed"]
    assert (
        verify["result"]["certificate"]["claim"]
        == "all detected protected commitments retained"
    )
    assert verify["result"]["certificate"]["semantic_completeness_claimed"] is False

    inspect = connector.handle_request(
        request("6", "inspect_memory", {"bundle": bundle})
    )
    assert_response_envelope(
        inspect,
        request_id="6",
        operation="inspect_memory",
        ok=True,
    )
    assert inspect["result"]["trusted_memory_counts"]["constraints"] == 1


def test_shared_envelope_returns_bounded_errors_and_keeps_request_identity() -> None:
    connector = LocalAIConnector()
    unknown = connector.handle_request(request("bad-1", "delete_memory"))

    assert_response_envelope(
        unknown,
        request_id="bad-1",
        operation="delete_memory",
        ok=False,
    )
    assert unknown["error"] == {
        "category": "invalid_request",
        "code": "unsupported_operation",
        "message": "connector operation is unsupported",
        "retryable": False,
        "details": {"exception_type": "ConnectorRequestError"},
    }

    extra = request("bad-2", "capabilities")
    extra["unexpected"] = True
    invalid = connector.handle_request(extra)
    assert_response_envelope(
        invalid,
        request_id=None,
        operation=None,
        ok=False,
    )
    assert invalid["error"]["code"] == "invalid_value"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ValueError(
                "unsupported connector operation: "
                "'delete_memory?token=phase-0-secret'"
            ),
            {
                "category": "invalid_request",
                "code": "unsupported_operation",
                "message": "connector operation is unsupported",
                "retryable": False,
                "details": {"exception_type": "ConnectorRequestError"},
            },
        ),
        (
            RuntimeError(
                "provider failed at C:\\private\\tenant\\memory.json "
                "with bearer phase-0-secret"
            ),
            {
                "category": "runtime",
                "code": "connector_failure",
                "message": "connector operation failed",
                "retryable": False,
                "details": {"exception_type": "ConnectorRuntimeError"},
            },
        ),
    ],
)
def test_shared_envelope_redacts_internal_error_details(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: dict,
) -> None:
    connector = LocalAIConnector()

    def fail_dispatch(_operation: str, _payload: dict) -> dict:
        raise error

    monkeypatch.setattr(connector, "_dispatch_operation", fail_dispatch)
    response = connector.handle_request(request("redacted", "capabilities"))

    assert response["error"] == expected
    assert "phase-0-secret" not in json.dumps(response)
    assert response["error"]["details"]["exception_type"] != type(error).__name__


def test_shared_envelope_survives_unrenderable_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnrenderableValueError(ValueError):
        def __str__(self) -> str:
            raise RuntimeError("phase-0-secret")

    connector = LocalAIConnector()

    def fail_dispatch(_operation: str, _payload: dict) -> dict:
        raise UnrenderableValueError()

    monkeypatch.setattr(connector, "_dispatch_operation", fail_dispatch)
    response = connector.handle_request(request("unrenderable", "capabilities"))

    assert response["error"] == {
        "category": "invalid_request",
        "code": "invalid_value",
        "message": "connector request has an invalid value",
        "retryable": False,
        "details": {"exception_type": "ConnectorRequestError"},
    }


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (
            '{"schema":"ctxc-connector-request-0.1",'
            '"request_id":"1","request_id":"2",'
            '"operation":"capabilities","payload":{}}',
            "duplicate JSON object key",
        ),
        (
            '{"schema":"ctxc-connector-request-0.1",'
            '"request_id":"1","operation":"capabilities","payload":{"x":NaN}}',
            "non-standard JSON constant",
        ),
    ],
)
def test_request_decoder_rejects_ambiguous_json(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        decode_connector_request(raw)


def test_request_decoder_enforces_byte_and_depth_limits() -> None:
    raw = json.dumps(request("1", "capabilities"))
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        decode_connector_request(raw, max_request_bytes=8)

    nested: dict = {}
    cursor = nested
    for _ in range(10):
        cursor["next"] = {}
        cursor = cursor["next"]
    deep = request("2", "capabilities", nested)
    with pytest.raises(ValueError, match="JSON levels"):
        decode_connector_request(json.dumps(deep), max_json_depth=5)


def test_ctxc_connector_stdio_round_trips_and_survives_protocol_error() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "context_compiler",
            "connector",
            "--stdio",
        ],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    def exchange(value: dict | str) -> dict:
        line = value if isinstance(value, str) else json.dumps(value)
        process.stdin.write(line + "\n")
        process.stdin.flush()
        response_line = process.stdout.readline()
        assert response_line
        return json.loads(response_line)

    capabilities = exchange(request("cli-1", "capabilities"))
    assert_response_envelope(
        capabilities,
        request_id="cli-1",
        operation="capabilities",
        ok=True,
    )

    malformed = exchange('{"request_id":"duplicate","request_id":"again"}')
    assert_response_envelope(
        malformed,
        request_id=None,
        operation=None,
        ok=False,
    )

    compiled = exchange(
        request(
            "cli-2",
            "compile_memory",
            {
                "events": [
                    {
                        "schema": "localai-source-event-0.1",
                        "id": "cli-event",
                        "sequence": 0,
                        "role": "user",
                        "content": "constraint: Leave the authentication flow alone.",
                    }
                ]
            },
        )
    )
    assert_response_envelope(
        compiled,
        request_id="cli-2",
        operation="compile_memory",
        ok=True,
    )
    bundle = compiled["result"]["bundle"]
    checkpoint = compiled["result"]["checkpoint"]

    rendered = exchange(request("cli-3", "render_context", {"bundle": bundle}))
    assert_response_envelope(
        rendered,
        request_id="cli-3",
        operation="render_context",
        ok=True,
    )
    assert "Leave the authentication flow alone." in rendered["result"]["context"]

    verified = exchange(
        request(
            "cli-4",
            "verify_memory",
            {"bundle": bundle, "checkpoint": checkpoint},
        )
    )
    assert_response_envelope(
        verified,
        request_id="cli-4",
        operation="verify_memory",
        ok=True,
    )
    assert verified["result"]["passed"]

    inspected = exchange(request("cli-5", "inspect_memory", {"bundle": bundle}))
    assert_response_envelope(
        inspected,
        request_id="cli-5",
        operation="inspect_memory",
        ok=True,
    )
    assert inspected["result"]["trusted_memory_counts"]["constraints"] == 1

    process.stdin.close()
    process.stdin = None
    remaining_stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0
    assert remaining_stdout == ""
    assert stderr == ""


def test_ctxc_connector_stdio_does_not_close_host_binary_stdin(
    monkeypatch,
    capsys,
) -> None:
    value = request("binary-host", "capabilities")
    binary_input = io.BytesIO((json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8"))

    class BinaryStdin:
        buffer = binary_input

        def readline(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("connector must not use the locale text stream")

    monkeypatch.setattr(sys, "stdin", BinaryStdin())

    assert main(["connector", "--stdio"]) == 0
    assert binary_input.closed is False
    response = json.loads(capsys.readouterr().out)
    assert_response_envelope(
        response,
        request_id="binary-host",
        operation="capabilities",
        ok=True,
    )


def test_ctxc_connector_stdio_uses_host_binary_stdout_without_closing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = request("binary-output", "capabilities")
    binary_output = io.BytesIO()

    class BinaryStdout:
        buffer = binary_output

        def write(self, _value: str) -> int:
            raise AssertionError("connector must not use the locale text stream")

    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps(value, separators=(",", ":")) + "\n"),
    )
    monkeypatch.setattr(sys, "stdout", BinaryStdout())

    assert main(["connector", "--stdio"]) == 0
    assert binary_output.closed is False
    payload = binary_output.getvalue()
    assert payload.endswith(b"\n")
    assert b"\r" not in payload
    response = json.loads(payload.decode("utf-8", errors="strict"))
    assert_response_envelope(
        response,
        request_id="binary-output",
        operation="capabilities",
        ok=True,
    )


def test_ctxc_connector_stdio_retains_exact_stringio_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = request("string-output", "capabilities")
    output = io.StringIO()
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps(value, separators=(",", ":")) + "\n"),
    )
    monkeypatch.setattr(sys, "stdout", output)

    assert main(["connector", "--stdio"]) == 0
    response = json.loads(output.getvalue())
    assert_response_envelope(
        response,
        request_id="string-output",
        operation="capabilities",
        ok=True,
    )


def test_ctxc_connector_stdio_rejects_custom_text_only_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary_input = io.BytesIO(b"")

    class BinaryStdin:
        buffer = binary_input

    class TextOnlyStdout:
        writes = 0

        def write(self, _value: str) -> int:
            self.writes += 1
            return 0

    output = TextOnlyStdout()
    monkeypatch.setattr(sys, "stdin", BinaryStdin())
    monkeypatch.setattr(sys, "stdout", output)

    assert main(["connector", "--stdio"]) == 2
    assert binary_input.closed is False
    assert output.writes == 0


def test_ctxc_connector_stdio_rejects_short_binary_stdout_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = request("short-output", "capabilities")

    class ShortBuffer:
        writes = 0
        flushes = 0

        def write(self, payload: bytes) -> int:
            self.writes += 1
            return len(payload) - 1

        def flush(self) -> None:
            self.flushes += 1

    buffer = ShortBuffer()

    class BinaryStdout:
        pass

    output = BinaryStdout()
    output.buffer = buffer
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps(value, separators=(",", ":")) + "\n"),
    )
    monkeypatch.setattr(sys, "stdout", output)

    assert main(["connector", "--stdio"]) == 2
    assert buffer.writes == 1
    assert buffer.flushes == 0


def test_ctxc_connector_stdio_does_not_retry_failed_binary_stdout_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = request("failed-flush", "capabilities")

    class FlushFailureBuffer:
        writes = 0
        flushes = 0

        def write(self, payload: bytes) -> int:
            self.writes += 1
            return len(payload)

        def flush(self) -> None:
            self.flushes += 1
            raise OSError("injected connector output flush failure")

    buffer = FlushFailureBuffer()

    class BinaryStdout:
        pass

    output = BinaryStdout()
    output.buffer = buffer
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps(value, separators=(",", ":")) + "\n"),
    )
    monkeypatch.setattr(sys, "stdout", output)

    assert main(["connector", "--stdio"]) == 2
    assert buffer.writes == 1
    assert buffer.flushes == 1


@pytest.mark.parametrize("python_io_encoding", ["cp1252:strict", "utf-16:strict"])
def test_ctxc_connector_stdio_uses_strict_utf8_bytes_outside_utf8_mode(
    python_io_encoding: str,
) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONUTF8"] = "0"
    environment["PYTHONIOENCODING"] = python_io_encoding
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    content = "constraint: preserve caf\u00e9, \U0001f680, and e\u0301 exactly"
    value = request(
        "unicode-stdio",
        "compile_memory",
        {
            "events": [
                {
                    "schema": "localai-source-event-0.1",
                    "id": "unicode-event",
                    "sequence": 0,
                    "role": "user",
                    "content": content,
                }
            ]
        },
    )
    input_bytes = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "context_compiler",
            "connector",
            "--stdio",
        ],
        cwd=ROOT,
        env=environment,
        input=input_bytes,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0
    assert completed.stderr == b""
    assert completed.stdout.endswith(b"\n")
    assert b"\x00" not in completed.stdout
    assert b"\r" not in completed.stdout
    assert len(completed.stdout.splitlines()) == 1
    decoded = completed.stdout.decode("utf-8", errors="strict")
    response = json.loads(decoded)
    serialized = json.dumps(
        response,
        ensure_ascii=False,
        sort_keys=True,
    )
    assert response["ok"] is True
    assert content in serialized
    assert "caf\u00c3\u00a9" not in serialized
    verification = LocalAIConnector().verify_memory(
        response["result"]["bundle"],
        events=value["payload"]["events"],
    )
    assert verification["passed"] is True


def test_ctxc_connector_stdio_rejects_invalid_utf8_before_dispatch() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONUTF8"] = "0"
    environment["PYTHONIOENCODING"] = "cp1252:strict"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "context_compiler",
            "connector",
            "--stdio",
            "--error-format",
            "json",
        ],
        cwd=ROOT,
        env=environment,
        input=b'{"schema":"ctxc-connector-request-0.1","payload":"\xff"}\n',
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 2
    assert completed.stdout == b""
    assert completed.stderr.endswith(b"\n")
    assert len(completed.stderr.splitlines()) == 1
    diagnostic = json.loads(completed.stderr.decode("ascii"))
    assert diagnostic["category"] == "invalid_input"
    assert diagnostic["code"] == "invalid_encoding"
    assert diagnostic["exception_type"] == "UnicodeError"
    assert diagnostic["message"] == "connector input must be valid UTF-8 text"
