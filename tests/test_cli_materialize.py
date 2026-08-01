from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest

from context_compiler import verify_materialized_context_result
from context_compiler.cli import (
    _MAX_SERIALIZED_RESULT_BYTES,
    _write_exact_utf8_output,
    main,
)
from context_compiler.context_window import CONTEXT_WINDOW_DEGRADATION_MODE

ROOT = Path(__file__).parents[1]
HISTORY = ROOT / "examples" / "materialized_context.jsonl"
ALLOCATION_SHA256 = "a" * 64


def materialize_args(input_path: str) -> list[str]:
    return [
        "materialize",
        input_path,
        "--current-turn-id",
        "deploy-011",
        "--hard-limit-tokens",
        "3000",
        "--memory-budget-tokens",
        "2200",
        "--reserved-output-tokens",
        "128",
        "--safety-margin-tokens",
        "64",
        "--minimum-recent-messages",
        "2",
        "--maximum-recent-messages",
        "3",
        "--per-message-overhead-tokens",
        "2",
        "--allocation-plan-sha256",
        ALLOCATION_SHA256,
        "--tokenizer-profile",
        "unicode-codepoint-count-v1",
    ]


def verify_materialization_args(
    input_path: str,
    *,
    receipt_sha256: str,
    allocation_sha256: str = ALLOCATION_SHA256,
) -> list[str]:
    return [
        "verify-materialization",
        input_path,
        "--expected-receipt-sha256",
        receipt_sha256,
        "--expected-allocation-plan-sha256",
        allocation_sha256,
    ]


def materialized_result_bytes(capsys: pytest.CaptureFixture[str]) -> bytes:
    assert main(materialize_args(str(HISTORY))) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    return captured.out.encode("utf-8")


def test_materialize_cli_emits_one_canonical_verifiable_result(
    capsys,
) -> None:
    assert main(materialize_args(str(HISTORY))) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.endswith("\n")
    assert captured.out.count("\n") == 1

    raw = captured.out[:-1]
    value = json.loads(raw)
    assert raw == json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert value["runtime_payload"]["tokenizer_identity"] == ("unicode-codepoint-count-v1")
    assert value["runtime_payload"]["provider_execution_ready"] is False
    assert value["runtime_payload"]["final_provider_recount_required"] is True
    assert (
        verify_materialized_context_result(
            value,
            expected_receipt_sha256=value["receipt"]["receipt_sha256"],
            expected_allocation_plan_sha256=ALLOCATION_SHA256,
        )
        == value
    )


def test_verify_materialization_cli_reemits_exact_file_and_stdin_bytes(
    tmp_path: Path,
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected = materialized_result_bytes(capsys)
    value = json.loads(expected)
    receipt_sha256 = value["receipt"]["receipt_sha256"]
    result_path = tmp_path / "materialized.json"
    verified_path = tmp_path / "verified.json"
    result_path.write_bytes(expected)

    file_args = verify_materialization_args(
        str(result_path),
        receipt_sha256=receipt_sha256,
    )
    assert main([*file_args, "--output", str(verified_path)]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert verified_path.read_bytes() == expected

    class BinaryOnlyStdin:
        def __init__(self) -> None:
            self.buffer = io.BytesIO(expected)

        def read(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("verification must not use locale text stdin")

    monkeypatch.setattr(sys, "stdin", BinaryOnlyStdin())
    assert main(verify_materialization_args("-", receipt_sha256=receipt_sha256)) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.encode("utf-8") == expected


@pytest.mark.parametrize(
    ("digest_name", "digest_value"),
    [
        ("receipt", "b" * 64),
        ("receipt", "A" * 64),
        ("receipt", "a" * 63),
        ("allocation", "b" * 64),
        ("allocation", "A" * 64),
        ("allocation", "a" * 63),
    ],
)
def test_verify_materialization_cli_requires_exact_independent_digests(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    digest_name: str,
    digest_value: str,
) -> None:
    expected = materialized_result_bytes(capsys)
    value = json.loads(expected)
    result_path = tmp_path / "materialized.json"
    output_path = tmp_path / "verified.json"
    result_path.write_bytes(expected)
    output_path.write_bytes(b"retained-output")
    receipt = value["receipt"]["receipt_sha256"]
    allocation = ALLOCATION_SHA256
    if digest_name == "receipt":
        receipt = digest_value
    else:
        allocation = digest_value

    args = verify_materialization_args(
        str(result_path),
        receipt_sha256=receipt,
        allocation_sha256=allocation,
    )
    assert main([*args, "--output", str(output_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err
    assert output_path.read_bytes() == b"retained-output"


@pytest.mark.parametrize(
    "transform",
    [
        pytest.param(lambda value: value[:-1], id="missing-final-lf"),
        pytest.param(lambda value: value[:-1] + b"\r\n", id="crlf"),
        pytest.param(lambda value: value + b"\n", id="extra-record"),
        pytest.param(lambda value: b"\xef\xbb\xbf" + value, id="bom"),
        pytest.param(lambda _value: b'{"schema":"x","schema":"x"}\n', id="duplicate-key"),
        pytest.param(lambda _value: b'{"schema":"\xff"}\n', id="invalid-utf8"),
    ],
)
def test_verify_materialization_cli_rejects_noncanonical_input_without_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    transform,
) -> None:
    expected = materialized_result_bytes(capsys)
    value = json.loads(expected)
    result_path = tmp_path / "materialized-invalid.json"
    output_path = tmp_path / "verified.json"
    result_path.write_bytes(transform(expected))
    output_path.write_bytes(b"retained-output")

    args = verify_materialization_args(
        str(result_path),
        receipt_sha256=value["receipt"]["receipt_sha256"],
    )
    assert main([*args, "--output", str(output_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err
    assert output_path.read_bytes() == b"retained-output"


def test_verify_materialization_cli_rejects_tamper_alias_hardlink_and_oversize(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected = materialized_result_bytes(capsys)
    value = json.loads(expected)
    receipt_sha256 = value["receipt"]["receipt_sha256"]

    def args_for(path: Path) -> list[str]:
        return verify_materialization_args(
            str(path),
            receipt_sha256=receipt_sha256,
        )

    tampered = json.loads(expected)
    tampered["runtime_payload"]["provider_execution_ready"] = True
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_bytes(
        json.dumps(
            tampered,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    assert main(args_for(tampered_path)) == 2
    assert capsys.readouterr().out == ""

    alias_path = tmp_path / "alias.json"
    alias_path.write_bytes(expected)
    alias_before = alias_path.read_bytes()
    assert main([*args_for(alias_path), "--output", str(alias_path)]) == 2
    assert capsys.readouterr().out == ""
    assert alias_path.read_bytes() == alias_before

    hardlink_source = tmp_path / "hardlink-source.json"
    hardlink_alias = tmp_path / "hardlink-alias.json"
    hardlink_source.write_bytes(expected)
    os.link(hardlink_source, hardlink_alias)
    assert main(args_for(hardlink_alias)) == 2
    assert capsys.readouterr().out == ""

    oversized_path = tmp_path / "oversized.json"
    with oversized_path.open("wb") as stream:
        stream.seek(_MAX_SERIALIZED_RESULT_BYTES)
        stream.write(b"xx")
    assert main(args_for(oversized_path)) == 2
    assert capsys.readouterr().out == ""


def test_verify_materialization_cli_parser_requires_both_external_digests() -> None:
    with pytest.raises(SystemExit):
        main(["verify-materialization", "missing.json"])
    with pytest.raises(SystemExit):
        main(
            [
                "verify-materialization",
                "missing.json",
                "--expected-receipt-sha256",
                "a" * 64,
            ]
        )


def test_materialize_cli_stdin_is_byte_identical(
    monkeypatch,
    capsys,
) -> None:
    expected_args = materialize_args(str(HISTORY))
    assert main(expected_args) == 0
    expected = capsys.readouterr().out

    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(HISTORY.read_text(encoding="utf-8")),
    )
    assert main(materialize_args("-")) == 0
    assert capsys.readouterr().out == expected


def test_materialize_cli_stdin_uses_strict_utf8_binary_input(
    monkeypatch,
    capsys,
) -> None:
    payload = HISTORY.read_bytes()

    class BinaryOnlyStdin:
        def __init__(self) -> None:
            self.buffer = io.BytesIO(payload)

        def read(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("materialize stdin must not use the locale text stream")

    monkeypatch.setattr(sys, "stdin", BinaryOnlyStdin())
    assert main(materialize_args("-")) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["runtime_payload"]["current_turn"]["id"] == "deploy-011"


def test_materialize_cli_stdin_rejects_invalid_utf8_before_materialization(
    monkeypatch,
    capsys,
) -> None:
    class InvalidUtf8Stdin:
        buffer = io.BytesIO(b'{"role":"user","content":"\xff"}\n')

        def read(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("invalid binary input must not reach the locale text stream")

    monkeypatch.setattr(sys, "stdin", InvalidUtf8Stdin())
    assert main(materialize_args("-")) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "source input must be valid UTF-8 text" in captured.err


def test_materialize_cli_stdin_reports_invalid_utf8_as_encoding_error(
    monkeypatch,
    capsys,
) -> None:
    class InvalidUtf8Stdin:
        buffer = io.BytesIO(b'{"role":"user","content":"\xff"}\n')

        def read(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("invalid binary input must not reach the locale text stream")

    monkeypatch.setattr(sys, "stdin", InvalidUtf8Stdin())
    args = materialize_args("-")
    args.extend(["--error-format", "json"])

    assert main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    diagnostic = json.loads(captured.err)
    assert diagnostic["category"] == "invalid_input"
    assert diagnostic["code"] == "invalid_encoding"
    assert diagnostic["exception_type"] == "UnicodeError"
    assert diagnostic["message"] == "source input must be valid UTF-8 text"


def test_materialize_cli_subprocess_reads_utf8_stdin_without_utf8_mode() -> None:
    current_content = "continue with caf\u00e9, \U0001f680, and e\u0301 exactly once"
    payload = b"".join(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        for value in (
            {
                "id": "subprocess-0",
                "sequence": 0,
                "role": "assistant",
                "content": "retain the exact UTF-8 stdin bytes",
            },
            {
                "id": "subprocess-1",
                "sequence": 1,
                "role": "user",
                "content": current_content,
            },
        )
    )
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "0"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.pop("PYTHONIOENCODING", None)
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(ROOT / "src"), environment.get("PYTHONPATH")) if value
    )
    args = [
        sys.executable,
        "-B",
        "-m",
        "context_compiler.cli",
        "materialize",
        "-",
        "--current-turn-id",
        "subprocess-1",
        "--hard-limit-tokens",
        "512",
        "--memory-budget-tokens",
        "128",
        "--reserved-output-tokens",
        "32",
        "--safety-margin-tokens",
        "16",
        "--minimum-recent-messages",
        "1",
        "--maximum-recent-messages",
        "1",
        "--allocation-plan-sha256",
        ALLOCATION_SHA256,
        "--tokenizer-profile",
        "unicode-codepoint-count-v1",
    ]

    completed = subprocess.run(
        args,
        input=payload,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    assert completed.stderr == b""
    assert completed.stdout.endswith(b"\n")
    assert completed.stdout.count(b"\n") == 1
    assert current_content.encode("utf-8") in completed.stdout
    value = json.loads(completed.stdout)
    assert value["runtime_payload"]["current_turn"]["id"] == "subprocess-1"


def test_materialize_cli_unicode_profile_counts_code_points_not_utf8_bytes(
    tmp_path: Path,
    capsys,
) -> None:
    recent_content = "assistant data: café 🧪 e\u0301"
    current_content = "继续 with 🚀 exactly once"
    history = tmp_path / "unicode.jsonl"
    history.write_text(
        "\n".join(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            for value in (
                {
                    "id": "unicode-0",
                    "sequence": 0,
                    "role": "assistant",
                    "content": recent_content,
                },
                {
                    "id": "unicode-1",
                    "sequence": 1,
                    "role": "user",
                    "content": current_content,
                },
            )
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    args = [
        "materialize",
        str(history),
        "--current-turn-id",
        "unicode-1",
        "--hard-limit-tokens",
        "512",
        "--memory-budget-tokens",
        "128",
        "--reserved-output-tokens",
        "32",
        "--safety-margin-tokens",
        "16",
        "--minimum-recent-messages",
        "1",
        "--maximum-recent-messages",
        "1",
        "--allocation-plan-sha256",
        ALLOCATION_SHA256,
        "--tokenizer-profile",
        "unicode-codepoint-count-v1",
    ]

    assert main(args) == 0
    captured = capsys.readouterr()
    value = json.loads(captured.out)
    accounting = value["runtime_payload"]["accounting"]
    assert accounting["recent_tail_tokens"] == len(recent_content)
    assert accounting["current_turn_tokens"] == len(current_content)
    assert len(recent_content) != len(recent_content.encode("utf-8"))
    assert len(current_content) != len(current_content.encode("utf-8"))
    assert value["runtime_payload"]["provider_execution_ready"] is False
    assert value["runtime_payload"]["final_provider_recount_required"] is True


def test_materialize_cli_stdout_is_exact_utf8_bytes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    current_content = "continue with caf\u00e9, \U0001f680, and e\u0301 exactly once"
    history = tmp_path / "unicode-stdout.jsonl"
    history.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
            for value in (
                {
                    "id": "stdout-0",
                    "sequence": 0,
                    "role": "assistant",
                    "content": "retain the exact UTF-8 output bytes",
                },
                {
                    "id": "stdout-1",
                    "sequence": 1,
                    "role": "user",
                    "content": current_content,
                },
            )
        ),
        encoding="utf-8",
        newline="\n",
    )

    class BinaryOnlyStdout:
        def __init__(self) -> None:
            self.buffer = io.BytesIO()

        def write(self, _value: str) -> int:
            raise AssertionError("materialize stdout must not use the text stream")

    output = BinaryOnlyStdout()
    monkeypatch.setattr(sys, "stdout", output)
    args = [
        "materialize",
        str(history),
        "--current-turn-id",
        "stdout-1",
        "--hard-limit-tokens",
        "512",
        "--memory-budget-tokens",
        "128",
        "--reserved-output-tokens",
        "32",
        "--safety-margin-tokens",
        "16",
        "--minimum-recent-messages",
        "1",
        "--maximum-recent-messages",
        "1",
        "--allocation-plan-sha256",
        ALLOCATION_SHA256,
        "--tokenizer-profile",
        "unicode-codepoint-count-v1",
    ]

    assert main(args) == 0
    raw = output.buffer.getvalue()
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1
    assert current_content.encode("utf-8") in raw
    decoded = json.loads(raw)
    assert raw == (
        json.dumps(
            decoded,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def test_exact_utf8_stdout_fails_closed_without_a_complete_binary_write(
    monkeypatch,
) -> None:
    class MissingBuffer:
        pass

    monkeypatch.setattr(sys, "stdout", MissingBuffer())
    with pytest.raises(OSError, match="binary buffer"):
        _write_exact_utf8_output("{}", None)

    class ShortBuffer:
        def write(self, value: bytes) -> int:
            return len(value) - 1

        def flush(self) -> None:
            raise AssertionError("a short write must fail before flush")

    class ShortStdout:
        buffer = ShortBuffer()

    monkeypatch.setattr(sys, "stdout", ShortStdout())
    with pytest.raises(OSError, match="complete canonical report"):
        _write_exact_utf8_output("{}", None)


def test_materialize_output_file_remains_atomic_utf8_and_overwritable(
    tmp_path: Path,
) -> None:
    output = tmp_path / "materialized.json"
    output.write_bytes(b"previous-result\n")
    args = [*materialize_args(str(HISTORY)), "--output", str(output)]

    assert main(args) == 0
    raw = output.read_bytes()
    assert raw.endswith(b"\n")
    value = json.loads(raw)
    assert raw == (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


@pytest.mark.parametrize("use_degradation_policy", [False, True])
def test_materialize_refusal_is_reason_coded_and_writes_no_partial_output(
    tmp_path: Path,
    capsys,
    use_degradation_policy: bool,
) -> None:
    output = tmp_path / "result.json"
    output.write_text("previous-complete-result\n", encoding="utf-8")
    args = materialize_args(str(HISTORY))
    args[args.index("3000")] = "400"
    args[args.index("2200")] = "200"
    if use_degradation_policy:
        args.extend(["--degradation-policy", CONTEXT_WINDOW_DEGRADATION_MODE])
    args.extend(["--error-format", "json", "--output", str(output)])

    assert main(args) == 2
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.err)
    assert captured.out == ""
    assert diagnostic["schema"] == "ctxc-diagnostic-0.1"
    assert diagnostic["command"] == "materialize"
    assert diagnostic["code"] == "mandatory_components_do_not_fit"
    assert diagnostic["message"] == (
        "mandatory_components_do_not_fit: "
        "cause=current_turn_and_minimum_recent_tail_exceed_tail_capacity; "
        "available_dynamic_planning_units=208; "
        "memory_allocation_planning_units=200; "
        "tail_capacity_planning_units=8; current_turn_planning_units=104; "
        "minimum_recent_message_count=2; "
        "minimum_recent_tail_planning_units=170; "
        "required_tail_planning_units=274; shortfall_planning_units=266"
    )
    assert "details" not in diagnostic
    assert "req-1700" not in diagnostic["message"]
    assert output.read_text(encoding="utf-8") == "previous-complete-result\n"


def test_materialize_cli_requires_matching_fixed_input_digest(
    capsys,
) -> None:
    args = materialize_args(str(HISTORY))
    args.extend(["--fixed-input-tokens", "1", "--error-format", "json"])

    assert main(args) == 2
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.err)
    assert captured.out == ""
    assert diagnostic["code"] == "invalid_context_window"
    assert "fixed_input_sha256" in diagnostic["message"]
    assert "details" not in diagnostic


def test_materialize_cli_emits_exact_content_free_overflow_diagnostic(
    tmp_path: Path,
    capsys,
) -> None:
    pack = json.loads(
        files("context_compiler")
        .joinpath("data/materialized_retention_pack_v1.json")
        .read_text(encoding="utf-8")
    )
    case = next(value for value in pack["cases"] if value["case_id"] == "case-021")
    history = tmp_path / "heldout.jsonl"
    history.write_text(
        "".join(
            json.dumps(source, ensure_ascii=False, separators=(",", ":")) + "\n"
            for source in case["sources"]
        ),
        encoding="utf-8",
        newline="\n",
    )
    output = tmp_path / "result.json"
    output.write_text("previous-complete-result\n", encoding="utf-8")
    args = [
        "materialize",
        str(history),
        "--current-turn-id",
        case["current_turn_id"],
        "--hard-limit-tokens",
        "2200",
        "--memory-budget-tokens",
        "1200",
        "--reserved-output-tokens",
        "128",
        "--safety-margin-tokens",
        "64",
        "--minimum-recent-messages",
        "2",
        "--maximum-recent-messages",
        "3",
        "--per-message-overhead-tokens",
        "2",
        "--allocation-plan-sha256",
        ALLOCATION_SHA256,
        "--tokenizer-profile",
        "unicode-codepoint-count-v1",
        "--error-format",
        "json",
        "--output",
        str(output),
    ]

    assert main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    raw = captured.err[:-1]
    diagnostic = json.loads(raw)
    assert raw == json.dumps(
        diagnostic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert diagnostic["schema"] == "ctxc-diagnostic-0.2"
    assert diagnostic["command"] == "materialize"
    assert diagnostic["category"] == "invalid_input"
    assert diagnostic["code"] == "compiled_memory_not_verified"
    assert diagnostic["exit_code"] == 2
    assert diagnostic["details"] == {
        "schema": "loss-resistant-materialization-refusal-diagnostic-v1",
        "reason": "compiled_memory_not_verified",
        "stage": "compile_memory",
        "cause": "memory_token_budget_overflow",
        "tokenizer_identity": "unicode-codepoint-count-v1",
        "memory_budget_tokens": 1_200,
        "required_memory_tokens": 1_772,
        "overflow_tokens": 572,
        "compiled_prefix_message_count": 14,
        "compiled_prefix_manifest_sha256": (
            "0d681e92657fdddffa8c37de63aa5428d68d126b0a2e8a930e1a87354feae6b2"
        ),
        "retrieval_result_sha256": None,
        "provider_execution_ready": False,
        "final_provider_recount_required": True,
    }
    for source in case["sources"]:
        assert source["id"] not in raw
        assert source["content"] not in raw
    assert str(tmp_path) not in raw
    assert output.read_text(encoding="utf-8") == "previous-complete-result\n"


def test_materialize_cli_exact_degradation_policy_accepts_bounded_overflow(
    tmp_path: Path,
    capsys,
) -> None:
    pack = json.loads(
        files("context_compiler")
        .joinpath("data/materialized_retention_pack_v1.json")
        .read_text(encoding="utf-8")
    )
    case = next(value for value in pack["cases"] if value["case_id"] == "case-021")
    history = tmp_path / "heldout.jsonl"
    history.write_text(
        "".join(
            json.dumps(source, ensure_ascii=False, separators=(",", ":")) + "\n"
            for source in case["sources"]
        ),
        encoding="utf-8",
        newline="\n",
    )
    input_before = history.read_bytes()
    args = [
        "materialize",
        str(history),
        "--current-turn-id",
        case["current_turn_id"],
        "--hard-limit-tokens",
        "2200",
        "--memory-budget-tokens",
        "1200",
        "--reserved-output-tokens",
        "128",
        "--safety-margin-tokens",
        "64",
        "--minimum-recent-messages",
        "2",
        "--maximum-recent-messages",
        "3",
        "--per-message-overhead-tokens",
        "2",
        "--allocation-plan-sha256",
        ALLOCATION_SHA256,
        "--tokenizer-profile",
        "unicode-codepoint-count-v1",
        "--degradation-policy",
        CONTEXT_WINDOW_DEGRADATION_MODE,
    ]

    assert main(args) == 0
    first = capsys.readouterr()
    assert first.err == ""
    assert first.out.count("\n") == 1
    assert main(args) == 0
    second = capsys.readouterr()
    assert second.err == ""
    assert second.out == first.out
    assert history.read_bytes() == input_before

    result = json.loads(first.out)
    assert (
        verify_materialized_context_result(
            first.out.encode("utf-8"),
            expected_receipt_sha256=result["receipt"]["receipt_sha256"],
            expected_allocation_plan_sha256=ALLOCATION_SHA256,
        )
        == result
    )
    materialized = result["materialized_context"]
    prototype = materialized["prototype"]
    runtime = result["runtime_payload"]
    degradation = prototype["context_bundle"]["artifact"]["compiler_metadata"][
        "context_window_degradation"
    ]
    assert degradation == {
        "effective_memory_budget_tokens": 1_486,
        "mode": CONTEXT_WINDOW_DEGRADATION_MODE,
        "requested_memory_budget_tokens": 1_200,
        "rung": "minimal_memory_reallocation_compact",
    }
    assert [message["id"] for message in runtime["recent_messages"]] == [
        "case-021-m15",
        "case-021-m16",
        "case-021-m17",
    ]
    assert runtime["current_turn"]["id"] == "case-021-m18"
    assert runtime["accounting"]["current_turn_message_count"] == 1
    assert len(runtime["recent_tail_omissions"]) == 14
    assert runtime["retrieval_result_sha256"] is None
    assert runtime["provider_execution_ready"] is False
    assert runtime["final_provider_recount_required"] is True
    assert prototype["context_bundle"]["certificate"]["semantic_completeness_claimed"] is False


@pytest.mark.parametrize(
    "value",
    [
        "LOSSLESS-COMPACT-THEN-REALLOCATE-V1",
        "lossless-compact-only-v1",
    ],
)
def test_materialize_cli_rejects_unknown_degradation_policy_before_reading_sources(
    value: str,
    monkeypatch,
) -> None:
    def fail_if_read(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid degradation policy must fail before source input")

    monkeypatch.setattr("context_compiler.cli._input_sources", fail_if_read)
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                *materialize_args("unread.jsonl"),
                "--degradation-policy",
                value,
            ]
        )
    assert exc_info.value.code == 2


def test_materialize_cli_rejects_non_exact_policy_before_reading_sources(
    monkeypatch,
    capsys,
) -> None:
    class StringSubclass(str):
        pass

    def fail_if_read(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("non-exact degradation policy must fail before source input")

    monkeypatch.setattr("context_compiler.cli._input_sources", fail_if_read)
    args = [
        *materialize_args("unread.jsonl"),
        "--degradation-policy",
        StringSubclass(CONTEXT_WINDOW_DEGRADATION_MODE),
        "--error-format",
        "json",
    ]
    assert main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    diagnostic = json.loads(captured.err)
    assert diagnostic["code"] == "invalid_type"
    assert "exact string" in diagnostic["message"]


def test_materialize_cli_strict_success_is_identical_with_policy_flag(capsys) -> None:
    args = materialize_args(str(HISTORY))
    assert main(args) == 0
    strict = capsys.readouterr()
    assert strict.err == ""

    assert main([*args, "--degradation-policy", CONTEXT_WINDOW_DEGRADATION_MODE]) == 0
    opted_in = capsys.readouterr()
    assert opted_in.err == ""
    assert opted_in.out == strict.out
