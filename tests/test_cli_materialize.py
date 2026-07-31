from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from context_compiler import verify_materialized_context_result
from context_compiler.cli import main

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


def test_materialize_refusal_is_reason_coded_and_writes_no_partial_output(
    tmp_path: Path,
    capsys,
) -> None:
    output = tmp_path / "result.json"
    output.write_text("previous-complete-result\n", encoding="utf-8")
    args = materialize_args(str(HISTORY))
    args[args.index("3000")] = "400"
    args[args.index("2200")] = "200"
    args.extend(["--error-format", "json", "--output", str(output)])

    assert main(args) == 2
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.err)
    assert captured.out == ""
    assert diagnostic["schema"] == "ctxc-diagnostic-0.1"
    assert diagnostic["command"] == "materialize"
    assert diagnostic["code"] == "mandatory_components_do_not_fit"
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
