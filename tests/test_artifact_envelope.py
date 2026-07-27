from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from context_compiler import (
    ContextCompiler,
    SourceRecord,
    validate_artifact_envelope,
)
from context_compiler.cli import main


def compiled_artifact(*, include_all_items: bool = True) -> dict:
    source = SourceRecord.create(
        id="artifact-envelope-source",
        sequence=0,
        role="user",
        content="constraint: Inspect only an integrity-checked artifact",
    )
    return ContextCompiler().compile([source]).to_dict(
        include_all_items=include_all_items
    )


def rehash(artifact: dict) -> None:
    unsigned = {
        key: value
        for key, value in artifact.items()
        if key != "artifact_sha256"
    }
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    artifact["artifact_sha256"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def test_artifact_envelope_accepts_complete_and_active_only_artifacts() -> None:
    complete = compiled_artifact()
    active_only = compiled_artifact(include_all_items=False)

    assert validate_artifact_envelope(complete) is complete
    assert validate_artifact_envelope(active_only) is active_only
    assert complete["ledger_complete"] is True
    assert active_only["ledger_complete"] is False


def test_artifact_envelope_rejects_stale_self_hash() -> None:
    artifact = compiled_artifact()
    artifact["items"][0]["text"] = "tampered after compilation"

    with pytest.raises(ValueError, match="artifact digest mismatch"):
        validate_artifact_envelope(artifact)


def test_missing_self_hash_is_a_caught_shape_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact = compiled_artifact()
    del artifact["artifact_sha256"]

    with pytest.raises(ValueError, match="missing required fields"):
        validate_artifact_envelope(artifact)

    path = tmp_path / "missing-digest.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    assert main(["inspect", str(path)]) == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "missing required fields: artifact_sha256" in captured.err


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda artifact: artifact.__setitem__("items", {}),
            "items must be a JSON array",
        ),
        (
            lambda artifact: artifact.__setitem__("schema_version", "99.0"),
            "unsupported artifact schema version",
        ),
        (
            lambda artifact: artifact["selected_item_ids"].append(
                artifact["selected_item_ids"][0]
            ),
            "selected_item_ids must be unique",
        ),
        (
            lambda artifact: artifact["selected_item_ids"].append("missing-item"),
            "must reference retained items",
        ),
        (
            lambda artifact: artifact["items"].append(
                dict(artifact["items"][0])
            ),
            "items must have unique ids",
        ),
    ],
)
def test_artifact_envelope_rejects_rehashed_invalid_contracts(
    mutate: Callable[[dict], None],
    message: str,
) -> None:
    artifact = compiled_artifact()
    mutate(artifact)
    rehash(artifact)

    with pytest.raises(ValueError, match=message):
        validate_artifact_envelope(artifact)


def test_cli_inspect_refuses_digest_mismatch_with_integrity_diagnostic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact = compiled_artifact()
    artifact["items"][0]["text"] = "tampered after compilation"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")

    assert (
        main(
            [
                "inspect",
                str(path),
                "--error-format",
                "json",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.err)

    assert captured.out == ""
    assert diagnostic["command"] == "inspect"
    assert diagnostic["category"] == "integrity"
    assert diagnostic["code"] == "integrity_check_failed"


def test_cli_inspect_reports_validated_envelope_identity(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact = compiled_artifact(include_all_items=False)
    path = tmp_path / "active-only.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")

    assert main(["inspect", str(path)]) == 0
    summary = json.loads(capsys.readouterr().out)

    assert summary["schema_version"] == "1.0"
    assert summary["artifact_sha256"] == artifact["artifact_sha256"]
    assert summary["source_digest"] == artifact["source_digest"]
    assert summary["ledger_complete"] is False
    assert summary["integrity"] == {
        "schema_supported": True,
        "shape_valid": True,
        "self_hash_valid": True,
    }
