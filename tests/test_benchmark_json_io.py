from __future__ import annotations

import hashlib

import pytest

from benchmarks.json_io import (
    StrictJsonError,
    StrictJsonLimits,
    hash_bounded_regular_file,
    load_strict_json_file,
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

    document = load_strict_json_file(
        path,
        limits=limits(),
        label="fixture",
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
    with pytest.raises(StrictJsonError, match="exceeds"):
        hash_bounded_regular_file(
            path,
            max_bytes=len(encoded) - 1,
            label="fixture",
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
