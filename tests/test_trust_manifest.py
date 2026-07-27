from __future__ import annotations

import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from context_compiler import (
    TRUST_MANIFEST_SCHEMA,
    TRUST_VERIFICATION_SCHEMA,
    ContextCompiler,
    SourceArchive,
    SourceRecord,
    TrustManifestError,
    create_trust_manifest,
    load_trust_manifest,
    load_trust_manifest_path,
    trust_manifest_sha256,
    validate_trust_manifest,
    verify_trust_manifest,
)
from context_compiler.cli import main

ROOT = Path(__file__).parents[1]
CREATED_AT = "2026-07-24T12:34:56+00:00"


def compiled_bundle(
    *,
    source_id: str = "trust-source-0",
    content: str = "constraint: preserve the externally anchored requirement",
) -> tuple[list[SourceRecord], dict[str, Any]]:
    source = SourceRecord.create(
        id=source_id,
        sequence=0,
        role="user",
        content=content,
    )
    sources = [source]
    return sources, ContextCompiler().compile(sources).to_dict()


def resign_artifact(artifact: dict[str, Any]) -> None:
    unsigned = {
        key: value
        for key, value in artifact.items()
        if key != "artifact_sha256"
    }
    artifact["artifact_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def write_bundle(
    directory: Path,
) -> tuple[list[SourceRecord], dict[str, Any], Path, Path]:
    sources, artifact = compiled_bundle()
    sources_path = directory / "sources.json"
    artifact_path = directory / "artifact.json"
    sources_path.write_text(
        json.dumps([source.to_dict() for source in sources]),
        encoding="utf-8",
    )
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    return sources, artifact, sources_path, artifact_path


def issue_codes(report: dict[str, Any]) -> set[str]:
    return {issue["code"] for issue in report["issues"]}


def test_manifest_creation_binds_a_complete_independently_verified_bundle() -> None:
    sources, artifact = compiled_bundle()

    first = create_trust_manifest(
        artifact,
        sources,
        created_at=CREATED_AT,
    )
    second = create_trust_manifest(
        artifact,
        sources,
        created_at=CREATED_AT,
    )

    assert first == second
    assert validate_trust_manifest(first) is first
    assert first == {
        "schema": TRUST_MANIFEST_SCHEMA,
        "created_at": CREATED_AT,
        "artifact_schema_version": "1.0",
        "artifact_sha256": artifact["artifact_sha256"],
        "source_digest": artifact["source_digest"],
        "source_count": 1,
        "ledger_complete": True,
        "archive_chain_head_sha256": None,
        "manifest_sha256": trust_manifest_sha256(first),
    }

    report = verify_trust_manifest(
        first,
        artifact,
        sources,
        expected_manifest_sha256=first["manifest_sha256"],
    )

    assert report["schema"] == TRUST_VERIFICATION_SCHEMA
    assert report["passed"] is True
    assert report["anchored"] is True
    assert all(report["checks"].values())
    assert report["issues"] == []


def test_verification_never_claims_success_without_an_external_anchor() -> None:
    sources, artifact = compiled_bundle()
    manifest = create_trust_manifest(
        artifact,
        sources,
        created_at=CREATED_AT,
    )

    report = verify_trust_manifest(
        manifest,
        artifact,
        sources,
        expected_manifest_sha256=None,
    )

    assert report["passed"] is False
    assert report["anchored"] is False
    assert report["checks"]["manifest_self_hash"] is True
    assert report["checks"]["external_anchor"] is False
    assert issue_codes(report) == {"missing_external_anchor"}


def test_rehashing_tampered_manifest_cannot_bypass_the_retained_anchor() -> None:
    sources, artifact = compiled_bundle()
    manifest = create_trust_manifest(
        artifact,
        sources,
        created_at=CREATED_AT,
    )
    retained_anchor = manifest["manifest_sha256"]
    tampered = deepcopy(manifest)
    tampered["source_count"] = 2
    tampered["manifest_sha256"] = trust_manifest_sha256(tampered)

    anchored = verify_trust_manifest(
        tampered,
        artifact,
        sources,
        expected_manifest_sha256=retained_anchor,
    )
    attacker_anchor = verify_trust_manifest(
        tampered,
        artifact,
        sources,
        expected_manifest_sha256=tampered["manifest_sha256"],
    )

    assert anchored["passed"] is False
    assert anchored["checks"]["manifest_self_hash"] is True
    assert anchored["checks"]["external_anchor"] is False
    assert "external_anchor_mismatch" in issue_codes(anchored)
    assert attacker_anchor["passed"] is False
    assert attacker_anchor["checks"]["external_anchor"] is True
    assert attacker_anchor["checks"]["source_binding"] is False
    assert "source_binding_mismatch" in issue_codes(attacker_anchor)


def test_manifest_rejects_a_different_valid_artifact_and_source_set() -> None:
    sources, artifact = compiled_bundle()
    manifest = create_trust_manifest(
        artifact,
        sources,
        created_at=CREATED_AT,
    )
    other_sources, other_artifact = compiled_bundle(
        source_id="other-source",
        content="constraint: retain a different valid bundle",
    )

    report = verify_trust_manifest(
        manifest,
        other_artifact,
        other_sources,
        expected_manifest_sha256=manifest["manifest_sha256"],
    )

    assert report["checks"]["artifact_replay"] is True
    assert report["checks"]["artifact_binding"] is False
    assert report["checks"]["source_binding"] is False
    assert {
        "artifact_binding_mismatch",
        "source_binding_mismatch",
    } <= issue_codes(report)
    assert report["passed"] is False


def test_creation_refuses_incomplete_or_replay_failing_artifacts() -> None:
    sources, artifact = compiled_bundle()
    incomplete = ContextCompiler().compile(sources).to_dict(
        include_all_items=False
    )
    replay_failing = deepcopy(artifact)
    replay_failing["items"][0]["text"] = "forged retained statement"
    resign_artifact(replay_failing)

    with pytest.raises(
        TrustManifestError,
        match="complete artifact ledger",
    ):
        create_trust_manifest(incomplete, sources, created_at=CREATED_AT)
    with pytest.raises(
        TrustManifestError,
        match="fails independent replay",
    ):
        create_trust_manifest(
            replay_failing,
            sources,
            created_at=CREATED_AT,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("created_at", "2026-07-24T12:34:56-07:00", "explicit UTC"),
        ("created_at", "2026-07-24T12:34:56-00:00", "explicit UTC"),
        ("created_at", "2026-07-24 12:34:56+00:00", "RFC 3339"),
        ("artifact_sha256", "F" * 64, "lowercase hexadecimal"),
        ("source_count", True, "non-negative integer"),
        ("ledger_complete", False, "must be true"),
    ],
)
def test_manifest_shape_is_strict(
    field: str,
    value: Any,
    message: str,
) -> None:
    sources, artifact = compiled_bundle()
    manifest = create_trust_manifest(
        artifact,
        sources,
        created_at=CREATED_AT,
    )
    manifest[field] = value
    manifest["manifest_sha256"] = trust_manifest_sha256(manifest)

    with pytest.raises(TrustManifestError, match=message):
        validate_trust_manifest(manifest)

    manifest["unexpected"] = True
    manifest["manifest_sha256"] = trust_manifest_sha256(manifest)
    with pytest.raises(TrustManifestError, match="unknown fields"):
        validate_trust_manifest(manifest)


def test_verifier_returns_a_bounded_failure_report_for_malformed_manifest() -> None:
    sources, artifact = compiled_bundle()

    report = verify_trust_manifest(
        ["not", "a", "manifest"],
        artifact,
        sources,
        expected_manifest_sha256="0" * 64,
    )

    assert report["passed"] is False
    assert report["declared_manifest_sha256"] is None
    assert report["checks"]["artifact_envelope"] is True
    assert report["checks"]["artifact_replay"] is True
    assert {
        "invalid_manifest_json",
        "invalid_manifest_shape",
        "external_anchor_mismatch",
    } <= issue_codes(report)


def test_strict_loader_rejects_duplicate_keys_empty_and_oversized_input(
    tmp_path: Path,
) -> None:
    with pytest.raises(TrustManifestError, match="duplicate JSON object key"):
        load_trust_manifest(io.StringIO('{"schema":"one","schema":"two"}'))
    with pytest.raises(TrustManifestError, match="cannot be empty"):
        load_trust_manifest(io.StringIO(" \n"))
    with pytest.raises(TrustManifestError, match="exceeds"):
        load_trust_manifest(io.StringIO("x" * (64 * 1024 + 1)))

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"schema":"one","schema":"two"}', encoding="utf-8")
    with pytest.raises(TrustManifestError, match="duplicate JSON object key"):
        load_trust_manifest_path(manifest_path)


def test_trust_manifest_json_integer_length_is_bounded_and_sanitized() -> None:
    payload = '{"source_count":' + "9" * 641 + "}"

    with pytest.raises(
        TrustManifestError,
        match=(
            "trust manifest JSON exceeds the supported JSON integer length "
            "of 640 digits"
        ),
    ) as caught:
        load_trust_manifest(io.StringIO(payload))
    assert "sys.set_int_max_str_digits" not in str(caught.value)


def test_archive_chain_head_is_part_of_the_manifest_binding(
    tmp_path: Path,
) -> None:
    sources, artifact = compiled_bundle()
    archive = SourceArchive(tmp_path / "archive")
    assert archive.append(sources) == 1
    chain_head = archive.verify().chain_head_sha256
    assert chain_head is not None
    manifest = create_trust_manifest(
        artifact,
        sources,
        archive_chain_head_sha256=chain_head,
        created_at=CREATED_AT,
    )

    passed = verify_trust_manifest(
        manifest,
        artifact,
        sources,
        expected_manifest_sha256=manifest["manifest_sha256"],
        archive_chain_head_sha256=chain_head,
    )
    failed = verify_trust_manifest(
        manifest,
        artifact,
        sources,
        expected_manifest_sha256=manifest["manifest_sha256"],
        archive_chain_head_sha256="f" * 64,
    )

    assert passed["passed"] is True
    assert failed["passed"] is False
    assert failed["checks"]["archive_binding"] is False
    assert "archive_binding_mismatch" in issue_codes(failed)


def test_packaged_schema_matches_the_runtime_manifest_contract() -> None:
    sources, artifact = compiled_bundle()
    manifest = create_trust_manifest(
        artifact,
        sources,
        created_at=CREATED_AT,
    )
    schema = json.loads(
        (ROOT / "schemas" / "trust-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert schema["$id"].startswith(
        "https://example.invalid/loss-resistant-context-compiler/"
    )
    assert set(schema["required"]) == set(manifest)
    assert set(schema["properties"]) == set(manifest)
    assert schema["properties"]["schema"]["const"] == TRUST_MANIFEST_SCHEMA
    assert schema["properties"]["artifact_schema_version"]["const"] == "1.0"
    assert schema["properties"]["ledger_complete"]["const"] is True
    assert schema["additionalProperties"] is False


def test_cli_direct_source_round_trip_and_anchor_mismatch_exit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, _, sources_path, artifact_path = write_bundle(tmp_path)
    manifest_path = tmp_path / "trust.json"

    assert (
        main(
            [
                "trust",
                "create",
                str(artifact_path),
                str(sources_path),
                "--output",
                str(manifest_path),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == ""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert (
        main(
            [
                "trust",
                "verify",
                str(manifest_path),
                str(artifact_path),
                str(sources_path),
                "--expected-manifest-sha256",
                manifest["manifest_sha256"],
            ]
        )
        == 0
    )
    passed = json.loads(capsys.readouterr().out)
    assert passed["passed"] is True
    assert passed["anchored"] is True

    assert (
        main(
            [
                "trust",
                "verify",
                str(manifest_path),
                str(artifact_path),
                str(sources_path),
                "--expected-manifest-sha256",
                "f" * 64,
            ]
        )
        == 3
    )
    failed = json.loads(capsys.readouterr().out)
    assert failed["passed"] is False
    assert "external_anchor_mismatch" in issue_codes(failed)


def test_cli_archive_round_trip_and_output_alias_protection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sources, _, sources_path, artifact_path = write_bundle(tmp_path)
    archive = SourceArchive(tmp_path / "archive")
    assert archive.append(sources) == 1
    head = archive.verify().chain_head_sha256
    assert head is not None
    manifest_path = tmp_path / "trust.json"

    assert (
        main(
            [
                "trust",
                "create",
                str(artifact_path),
                "--archive",
                str(archive.directory),
                "--archive-expected-chain-head",
                head,
                "--output",
                str(manifest_path),
            ]
        )
        == 0
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["archive_chain_head_sha256"] == head

    assert (
        main(
            [
                "trust",
                "verify",
                str(manifest_path),
                str(artifact_path),
                "--archive",
                str(archive.directory),
                "--archive-expected-chain-head",
                head,
                "--expected-manifest-sha256",
                manifest["manifest_sha256"],
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["passed"] is True

    original_lock = archive.lock_path.read_bytes()
    assert (
        main(
            [
                "trust",
                "create",
                str(artifact_path),
                "--archive",
                str(archive.directory),
                "--output",
                str(archive.lock_path),
            ]
        )
        == 2
    )
    assert "must not overwrite an input" in capsys.readouterr().err
    assert archive.lock_path.read_bytes() == original_lock

    original_sources = sources_path.read_bytes()
    assert (
        main(
            [
                "trust",
                "create",
                str(artifact_path),
                str(sources_path),
                "--output",
                str(sources_path),
            ]
        )
        == 2
    )
    assert "must not overwrite an input" in capsys.readouterr().err
    assert sources_path.read_bytes() == original_sources


def test_cli_archive_reload_fails_if_state_changes_after_verification(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, _, _, artifact_path = write_bundle(tmp_path)
    archive = SourceArchive(tmp_path / "archive")
    assert archive.append(sources) == 1
    manifest_path = tmp_path / "trust.json"
    original_verify = SourceArchive.verify
    changed = False

    def verify_then_change(
        self: SourceArchive,
        *,
        expected_chain_head: str | None = None,
    ) -> Any:
        nonlocal changed
        report = original_verify(
            self,
            expected_chain_head=expected_chain_head,
        )
        if not changed:
            changed = True
            self.append(
                [
                    SourceRecord.create(
                        id="trust-source-1",
                        sequence=1,
                        role="user",
                        content="constraint: retain the concurrently added source",
                    )
                ]
            )
        return report

    monkeypatch.setattr(SourceArchive, "verify", verify_then_change)

    assert (
        main(
            [
                "trust",
                "create",
                str(artifact_path),
                "--archive",
                str(archive.directory),
                "--output",
                str(manifest_path),
            ]
        )
        == 2
    )
    assert "archive chain head mismatch" in capsys.readouterr().err
    assert not manifest_path.exists()


def test_trust_json_diagnostic_names_the_nested_command(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing.json"

    assert (
        main(
            [
                "trust",
                "create",
                str(missing),
                str(missing),
                "--error-format",
                "json",
            ]
        )
        == 2
    )
    diagnostic = json.loads(capsys.readouterr().err)
    assert diagnostic["command"] == "trust create"
    assert diagnostic["category"] == "io"
