from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from ctxc_openhands import compatibility
from ctxc_openhands.compatibility import (
    OPENHANDS_API_MANIFEST_SHA256,
    OPENHANDS_PIN_MANIFEST_FILE_SHA256,
    OPENHANDS_PIN_MANIFEST_FILENAME,
    OPENHANDS_PIN_MANIFEST_MAX_BYTES,
    OPENHANDS_PIN_MANIFEST_PAYLOAD_SHA256,
    OPENHANDS_REVIEWED_API_FILE_COUNT,
    CompatibilityManifestError,
    default_pin_manifest_path,
    verify_pin_manifest,
)


def _manifest_value() -> dict:
    return json.loads(default_pin_manifest_path().read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict, *, indent: int = 2) -> None:
    path.write_text(
        json.dumps(value, indent=indent, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _refresh_payload_digest(value: dict) -> None:
    payload = dict(value)
    payload.pop("integrity")
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    value["integrity"]["payload_sha256"] = hashlib.sha256(canonical).hexdigest()


def test_exact_manifest_verifies_and_retains_blocked_live_state() -> None:
    result = verify_pin_manifest()

    assert result.path == default_pin_manifest_path().resolve()
    assert result.file_sha256 == OPENHANDS_PIN_MANIFEST_FILE_SHA256
    assert result.payload_sha256 == OPENHANDS_PIN_MANIFEST_PAYLOAD_SHA256
    assert result.reviewed_api_sha256 == OPENHANDS_API_MANIFEST_SHA256
    assert result.reviewed_api_file_count == OPENHANDS_REVIEWED_API_FILE_COUNT == 23
    assert result.blocker_code == "hash-pinned-wheelhouse-absent"
    assert result.real_offline_import == "blocked"
    assert result.live_scenario == "blocked"

def test_packaged_manifest_is_byte_identical_to_audit_copy() -> None:
    project_root = Path(__file__).resolve().parents[1]
    packaged = default_pin_manifest_path()
    audit_copy = project_root / "compatibility" / OPENHANDS_PIN_MANIFEST_FILENAME

    assert packaged.parent.name == "data"
    assert packaged.read_bytes() == audit_copy.read_bytes()
    assert hashlib.sha256(audit_copy.read_bytes()).hexdigest() == (
        OPENHANDS_PIN_MANIFEST_FILE_SHA256
    )

def test_exact_byte_copy_verifies_offline(tmp_path: Path) -> None:
    copied = tmp_path / "pin.json"
    copied.write_bytes(default_pin_manifest_path().read_bytes())

    result = verify_pin_manifest(copied)

    assert result.path == copied.resolve()
    assert result.file_sha256 == OPENHANDS_PIN_MANIFEST_FILE_SHA256


def test_import_and_verification_do_not_import_openhands() -> None:
    imported_before = {
        name for name in sys.modules if name == "openhands" or name.startswith("openhands.")
    }
    compatibility.verify_pin_manifest()
    imported_after = {
        name for name in sys.modules if name == "openhands" or name.startswith("openhands.")
    }
    assert imported_after == imported_before


def test_duplicate_key_fails_before_identity_acceptance(tmp_path: Path) -> None:
    target = tmp_path / "duplicate.json"
    raw = default_pin_manifest_path().read_bytes().replace(
        b'  "schema_version": 1,\n',
        b'  "schema_version": 1,\n  "schema_version": 1,\n',
        1,
    )
    target.write_bytes(raw)

    with pytest.raises(CompatibilityManifestError, match="duplicate JSON key"):
        verify_pin_manifest(target)


def test_payload_self_hash_tampering_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "self-hash.json"
    raw = default_pin_manifest_path().read_bytes().replace(
        OPENHANDS_PIN_MANIFEST_PAYLOAD_SHA256.encode("ascii"),
        ("0" * 64).encode("ascii"),
        1,
    )
    target.write_bytes(raw)

    with pytest.raises(CompatibilityManifestError, match="integrity.payload_sha256"):
        verify_pin_manifest(target)


def test_reformatting_cannot_bypass_exact_file_anchor(tmp_path: Path) -> None:
    target = tmp_path / "reformatted.json"
    value = _manifest_value()
    _write_json(target, value, indent=4)

    with pytest.raises(CompatibilityManifestError, match="manifest file digest"):
        verify_pin_manifest(target)


def test_identity_and_claim_widening_fail_before_file_anchor(tmp_path: Path) -> None:
    cases = (
        (
            "host-version",
            lambda value: value["host"].__setitem__("version", "1.8.1"),
            "host.version",
        ),
        (
            "semantic-claim",
            lambda value: value["claim_boundaries"].__setitem__(
                "semantic_completeness", True
            ),
            "claim_boundaries.semantic_completeness",
        ),
        (
            "live-success",
            lambda value: value["status"].__setitem__("live_scenario", "passed"),
            "status.live_scenario",
        ),
    )
    for name, mutate, match in cases:
        target = tmp_path / f"{name}.json"
        value = _manifest_value()
        mutate(value)
        _refresh_payload_digest(value)
        _write_json(target, value)
        with pytest.raises(CompatibilityManifestError, match=match):
            verify_pin_manifest(target)


def test_reviewed_api_reordering_and_private_seam_drift_fail(tmp_path: Path) -> None:
    reordered = tmp_path / "reordered.json"
    value = _manifest_value()
    files = value["reviewed_api"]["files"]
    files[0], files[1] = files[1], files[0]
    _refresh_payload_digest(value)
    _write_json(reordered, value)
    with pytest.raises(CompatibilityManifestError, match="sorted by path"):
        verify_pin_manifest(reordered)

    seam = tmp_path / "seam.json"
    value = _manifest_value()
    value["private_llm_transport"]["chat_seam"] = "public_transport"
    _refresh_payload_digest(value)
    _write_json(seam, value)
    with pytest.raises(CompatibilityManifestError, match="private_llm_transport.chat_seam"):
        verify_pin_manifest(seam)


def test_malformed_nonfinite_oversized_and_missing_files_fail_closed(tmp_path: Path) -> None:
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_bytes(
        default_pin_manifest_path()
        .read_bytes()
        .replace(b'"schema_version": 1', b'"schema_version": NaN', 1)
    )
    with pytest.raises(CompatibilityManifestError, match="non-finite JSON number"):
        verify_pin_manifest(nonfinite)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (OPENHANDS_PIN_MANIFEST_MAX_BYTES + 1))
    with pytest.raises(CompatibilityManifestError, match="exceeds"):
        verify_pin_manifest(oversized)

    with pytest.raises(CompatibilityManifestError, match="cannot read"):
        verify_pin_manifest(tmp_path / "missing.json")
