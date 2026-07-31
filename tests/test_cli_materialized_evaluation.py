from __future__ import annotations

import json
from pathlib import Path

import pytest

import context_compiler.cli as cli_module
from context_compiler.cli import main


def _assert_canonical_line(raw: str) -> dict[str, object]:
    assert raw.endswith("\n")
    assert raw.count("\n") == 1
    value = json.loads(raw)
    assert raw[:-1] == json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return value


class _ExactBinaryBuffer:
    def __init__(self, *, short_write: bool = False) -> None:
        self.payloads: list[bytes] = []
        self.flushed = False
        self.short_write = short_write

    def write(self, payload: bytes) -> int:
        assert type(payload) is bytes
        self.payloads.append(payload)
        return len(payload) - 1 if self.short_write else len(payload)

    def flush(self) -> None:
        self.flushed = True


class _CorruptingTextStdout:
    encoding = "ascii"

    def __init__(self, buffer: _ExactBinaryBuffer) -> None:
        self.buffer = buffer

    def write(self, _value: str) -> int:
        raise AssertionError("canonical output used the text stream")


def test_evaluate_materialization_stdout_is_exact_utf8_binary(
    monkeypatch,
) -> None:
    output = _ExactBinaryBuffer()
    monkeypatch.setattr(cli_module.sys, "stdout", _CorruptingTextStdout(output))

    cli_module._write_new_output('{"word":"café"}', None)

    assert output.payloads == [b'{"word":"caf\xc3\xa9"}\n']
    assert output.flushed is True


def test_evaluate_materialization_stdout_rejects_short_binary_write(
    monkeypatch,
) -> None:
    output = _ExactBinaryBuffer(short_write=True)
    monkeypatch.setattr(cli_module.sys, "stdout", _CorruptingTextStdout(output))

    with pytest.raises(OSError, match="complete canonical report"):
        cli_module._write_new_output('{"complete":true}', None)
    assert output.flushed is False


def test_evaluate_materialization_cli_is_byte_identical_and_defaults_heldout(
    capsys,
) -> None:
    assert main(["evaluate-materialization"]) == 3
    first = capsys.readouterr()
    assert first.err == ""
    report = _assert_canonical_line(first.out)
    assert report["selection"]["split"] == "heldout"
    assert report["integrity_passed"] is False
    assert report["claim_boundaries"]["inference_status"] == "not_run"
    assert report["claim_boundaries"]["retrieval_status"] == "not_run"

    assert main(["evaluate-materialization", "--split", "heldout"]) == 3
    second = capsys.readouterr()
    assert second.err == ""
    assert second.out == first.out


def test_evaluate_materialization_cli_create_new_and_verify_round_trip(
    tmp_path: Path,
    capsys,
) -> None:
    report_path = tmp_path / "retention-report.json"
    args = [
        "evaluate-materialization",
        "--split",
        "development",
        "--output",
        str(report_path),
    ]

    assert main(args) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    retained = report_path.read_bytes()
    report = _assert_canonical_line(retained.decode("utf-8"))

    assert main(args) == 2
    refused = capsys.readouterr()
    assert refused.out == ""
    assert refused.err == "ctxc: output already exists\n"
    assert report_path.read_bytes() == retained

    assert (
        main(
            [
                "evaluate-materialization",
                "--verify-report",
                str(report_path),
                "--expected-report-sha256",
                str(report["report_sha256"]),
            ]
        )
        == 3
    )
    verified = capsys.readouterr()
    assert verified.err == ""
    assert verified.out.encode("utf-8") == retained


def test_evaluate_materialization_does_not_relabel_temporary_allocation_failure(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_temporary_allocation(*_args: object, **_kwargs: object) -> None:
        raise FileExistsError("injected temporary allocation exhaustion")

    monkeypatch.setattr(cli_module, "atomic_write_text", fail_temporary_allocation)

    assert (
        main(
            [
                "evaluate-materialization",
                "--split",
                "development",
                "--output",
                str(tmp_path / "report.json"),
            ]
        )
        == 2
    )
    refused = capsys.readouterr()
    assert refused.out == ""
    assert refused.err == "ctxc: injected temporary allocation exhaustion\n"


def test_evaluate_materialization_cli_verification_fails_closed(
    tmp_path: Path,
    capsys,
) -> None:
    report_path = tmp_path / "report.json"
    assert (
        main(
            [
                "evaluate-materialization",
                "--split",
                "train",
                "--output",
                str(report_path),
            ]
        )
        == 3
    )
    capsys.readouterr()

    assert (
        main(
            [
                "evaluate-materialization",
                "--verify-report",
                str(report_path),
            ]
        )
        == 2
    )
    missing = capsys.readouterr()
    assert missing.out == ""
    assert "requires --expected-report-sha256" in missing.err

    assert (
        main(
            [
                "evaluate-materialization",
                "--verify-report",
                str(report_path),
                "--expected-report-sha256",
                "0" * 64,
            ]
        )
        == 2
    )
    wrong = capsys.readouterr()
    assert wrong.out == ""
    assert "expected digest" in wrong.err

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert (
        main(
            [
                "evaluate-materialization",
                "--split",
                "heldout",
                "--verify-report",
                str(report_path),
                "--expected-report-sha256",
                str(report["report_sha256"]),
            ]
        )
        == 2
    )
    mixed = capsys.readouterr()
    assert mixed.out == ""
    assert "--split cannot be combined" in mixed.err


@pytest.mark.parametrize(
    "unsupported",
    (
        "--pack",
        "--case-id",
        "--task-filter",
        "--hard-limit-tokens",
        "--retrieval-result",
        "--model",
    ),
)
def test_evaluate_materialization_cli_has_no_fixture_tuning_or_runtime_flags(
    unsupported: str,
    capsys,
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["evaluate-materialization", unsupported, "value"])
    assert raised.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
