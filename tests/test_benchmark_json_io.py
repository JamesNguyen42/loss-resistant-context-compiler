from __future__ import annotations

import hashlib

import pytest

import benchmarks.json_io as json_io
from benchmarks.json_io import (
    StrictJsonError,
    StrictJsonLimits,
    hash_bounded_regular_file,
    load_strict_json_file,
    read_bounded_regular_file,
)


def limits(
    *,
    max_bytes: int = 1_024,
    max_line_chars: int = 1_024,
    max_depth: int = 8,
) -> StrictJsonLimits:
    return StrictJsonLimits(
        max_bytes=max_bytes,
        max_line_chars=max_line_chars,
        max_depth=max_depth,
    )


def test_strict_json_file_records_exact_bytes_and_accepts_utf8_bom(tmp_path) -> None:
    path = tmp_path / "evidence.json"
    encoded = b'\xef\xbb\xbf{"nested":{"value":1}}\r\n'
    path.write_bytes(encoded)
    value = path.stat()
    identity = (value.st_dev, value.st_ino)

    document = load_strict_json_file(
        path,
        limits=limits(),
        label="fixture",
        expected_identity=identity,
    )

    assert document.value == {"nested": {"value": 1}}
    assert document.byte_count == len(encoded)
    assert document.file_sha256 == hashlib.sha256(encoded).hexdigest()
    file_evidence = hash_bounded_regular_file(
        path,
        max_bytes=len(encoded),
        label="fixture",
    )
    assert file_evidence.byte_count == document.byte_count
    assert file_evidence.file_sha256 == document.file_sha256
    binary = read_bounded_regular_file(
        path,
        max_bytes=len(encoded),
        label="fixture",
        expected_identity=identity,
    )
    assert binary.value == encoded
    assert binary.byte_count == document.byte_count
    assert binary.file_sha256 == document.file_sha256
    with pytest.raises(StrictJsonError, match="exceeds"):
        hash_bounded_regular_file(
            path,
            max_bytes=len(encoded) - 1,
            label="fixture",
        )
    with pytest.raises(StrictJsonError, match="identity does not match"):
        read_bounded_regular_file(
            path,
            max_bytes=len(encoded),
            label="fixture",
            expected_identity=(identity[0], identity[1] + 1),
        )
    with pytest.raises(TypeError, match="expected_identity"):
        load_strict_json_file(
            path,
            limits=limits(),
            label="fixture",
            expected_identity=(identity[0], True),  # type: ignore[arg-type]
        )
    with pytest.raises(StrictJsonError, match="exceeds"):
        read_bounded_regular_file(
            path,
            max_bytes=len(encoded) - 1,
            label="fixture",
        )


@pytest.mark.parametrize("reader", ["json", "binary", "hash"])
@pytest.mark.parametrize("reparse_check", [1, 2])
def test_benchmark_readers_reject_regular_mode_reparse_targets(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    reader: str,
    reparse_check: int,
) -> None:
    path = tmp_path / "reparse-evidence.json"
    encoded = b'{"value":1}\n'
    path.write_bytes(encoded)
    checks = 0

    def simulated_reparse(_value) -> bool:
        nonlocal checks
        checks += 1
        return checks == reparse_check

    monkeypatch.setattr(
        json_io,
        "_is_link_or_reparse",
        simulated_reparse,
        raising=False,
    )
    with pytest.raises(StrictJsonError, match="path must be a regular file"):
        if reader == "json":
            load_strict_json_file(path, limits=limits(), label="fixture")
        elif reader == "binary":
            read_bounded_regular_file(
                path,
                max_bytes=len(encoded),
                label="fixture",
            )
        else:
            hash_bounded_regular_file(
                path,
                max_bytes=len(encoded),
                label="fixture",
            )
    assert checks == reparse_check


def test_strict_json_file_can_reject_utf8_bom_without_a_second_read(tmp_path) -> None:
    path = tmp_path / "bom-forbidden.json"
    path.write_bytes(b'\xef\xbb\xbf{"value":1}\n')

    with pytest.raises(StrictJsonError, match="must not contain a BOM"):
        load_strict_json_file(
            path,
            limits=limits(),
            label="fixture",
            allow_bom=False,
        )
    with pytest.raises(TypeError, match="allow_bom must be a boolean"):
        load_strict_json_file(
            path,
            limits=limits(),
            label="fixture",
            allow_bom=1,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("raw", "configured", "message"),
    [
        ('{"value":1,"value":2}', limits(), "duplicate JSON object key"),
        ('{"value":NaN}', limits(), "non-standard JSON constant"),
        ("[[[0]]]", limits(max_depth=2), "exceeds JSON depth"),
        ('{"value":"too long"}', limits(max_line_chars=8), "characters on one line"),
        ('{"value":1}', limits(max_bytes=4), "exceeds 4 bytes"),
        ('{"value":' + "9" * 641 + "}", limits(), "exceeds 640 JSON integer digits"),
    ],
)
def test_strict_json_file_rejects_ambiguous_or_oversized_input(
    tmp_path,
    raw: str,
    configured: StrictJsonLimits,
    message: str,
) -> None:
    path = tmp_path / "unsafe.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(StrictJsonError, match=message):
        load_strict_json_file(path, limits=configured, label="fixture")

    with pytest.raises(StrictJsonError, match="regular file"):
        load_strict_json_file(tmp_path, limits=limits(), label="fixture")
