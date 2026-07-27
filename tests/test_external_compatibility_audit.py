from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import benchmarks.compatibility_audit as compatibility_audit
from benchmarks.compatibility_audit import (
    CompatibilityAuditError,
    audit_repository,
)

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BLOCKERS = {
    "acon-dependency-lock-absent",
    "acon-external-run-claim-controls-incomplete",
    "acon-external-run-command-missing-preflight-arguments",
    "acon-external-run-dependency-lock-absent",
    "acon-external-run-failed",
    "acon-external-run-network-unverified",
    "acon-external-run-runtime-mismatch",
    "acon-external-run-service-unmeasured",
    "acon-license-hash-mismatch",
    "ama-agent-dependency-lock-absent",
    "ama-agent-license-hash-mismatch",
    "external-protocol-draft",
}
EVIDENCE_FILES = (
    "benchmarks/protocols/external-comparison-v1.json",
    "benchmarks/protocols/external-comparison-v1.md",
    "benchmarks/compatibility/acon-v1.json",
    "benchmarks/compatibility/ama-agent-v1.json",
    "benchmarks/compatibility/acon-diagnostic-blocker.json",
    "benchmarks/compatibility/acon-diagnostic-failed-manifest.json",
)


def _copy_evidence(tmp_path: Path) -> Path:
    for relative in EVIDENCE_FILES:
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return tmp_path


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _resign_failed_manifest(payload: dict) -> None:
    unsigned = dict(payload)
    unsigned.pop("manifest_sha256")
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    payload["manifest_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_external_compatibility_audit_is_result_blind_and_non_scoreable() -> None:
    report = audit_repository(ROOT)

    assert report["status"] == "blocked"
    assert report["result_blind"] is True
    assert report["comparative_output_generated"] is False
    assert report["comparative_output_inspected"] is False
    assert report["ready_for_scoring"] is False
    assert report["external_execution_performed_by_audit"] is False
    assert {blocker["id"] for blocker in report["blockers"]} == EXPECTED_BLOCKERS
    assert [attempt["kind"] for attempt in report["attempts"]] == [
        "adapter-preflight",
        "external-runner",
    ]
    assert report["attempts"][0]["runner_invoked"] is False
    assert report["attempts"][1]["status"] == "failed"
    assert report["attempts"][1]["candidate_valid"] is False


def test_external_compatibility_audit_cli_is_an_executable_blocker() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "benchmarks.compatibility_audit", "--compact"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 2
    assert completed.stderr == ""
    report = json.loads(completed.stdout)
    assert report["status"] == "blocked"
    assert report["ready_for_scoring"] is False
    assert {blocker["id"] for blocker in report["blockers"]} == EXPECTED_BLOCKERS


def test_external_compatibility_audit_rejects_manifest_hash_tampering(
    tmp_path: Path,
) -> None:
    _copy_evidence(tmp_path)

    manifest_path = tmp_path / "benchmarks/compatibility/acon-diagnostic-failed-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["ready_for_scoring"] = True
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        CompatibilityAuditError,
        match="failed manifest SHA-256 mismatch",
    ):
        audit_repository(tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("comparative_quality_delta", {"acon": 1.0, "compiler": 0.0}),
        ("scores", {"acon": 1.0}),
    ],
)
def test_external_compatibility_audit_rejects_unknown_comparative_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    _copy_evidence(tmp_path)
    path = tmp_path / "benchmarks/compatibility/acon-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    _write_json(path, payload)

    with pytest.raises(CompatibilityAuditError, match="fields do not match"):
        audit_repository(tmp_path)


def test_external_compatibility_audit_rejects_scoring_permission_widening(
    tmp_path: Path,
) -> None:
    _copy_evidence(tmp_path)
    path = tmp_path / "benchmarks/compatibility/acon-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["diagnostic_adapter"]["scoring_permitted"] = True
    _write_json(path, payload)

    with pytest.raises(CompatibilityAuditError, match="scoring_permitted"):
        audit_repository(tmp_path)


def test_external_compatibility_audit_binds_verified_protocol_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_load = compatibility_audit._load_object_evidence

    def split_snapshot(root: Path, relative: str):
        payload, digest = real_load(root, relative)
        if relative == "benchmarks/protocols/external-comparison-v1.json":
            digest = "0" * 64
        return payload, digest

    monkeypatch.setattr(
        compatibility_audit,
        "_load_object_evidence",
        split_snapshot,
    )

    with pytest.raises(CompatibilityAuditError, match="changed between verified reads"):
        audit_repository(ROOT)


def test_external_compatibility_audit_binds_preflight_source_identity(
    tmp_path: Path,
) -> None:
    _copy_evidence(tmp_path)
    path = tmp_path / "benchmarks/compatibility/acon-diagnostic-blocker.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["repository"] = "https://github.com/attacker/other"
    payload["revision"] = "0" * 40
    _write_json(path, payload)

    with pytest.raises(CompatibilityAuditError, match="repository"):
        audit_repository(tmp_path)


def test_external_compatibility_audit_pins_source_license_and_blockers(
    tmp_path: Path,
) -> None:
    _copy_evidence(tmp_path)
    path = tmp_path / "benchmarks/compatibility/acon-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source"]["revision"] = "0" * 40
    _write_json(path, payload)

    with pytest.raises(CompatibilityAuditError, match="source revision"):
        audit_repository(tmp_path)

    _copy_evidence(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["license"]["file_sha256"] = "0" * 64
    _write_json(path, payload)

    with pytest.raises(CompatibilityAuditError, match="license file_sha256"):
        audit_repository(tmp_path)

    _copy_evidence(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["blockers"].pop()
    _write_json(path, payload)

    with pytest.raises(CompatibilityAuditError, match="blockers do not match"):
        audit_repository(tmp_path)


def test_external_compatibility_audit_rejects_unbound_present_lock(
    tmp_path: Path,
) -> None:
    _copy_evidence(tmp_path)
    path = tmp_path / "benchmarks/compatibility/ama-agent-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["dependencies"]["resolved_lock_state"] = "present"
    _write_json(path, payload)

    with pytest.raises(CompatibilityAuditError, match="resolved_lock_state"):
        audit_repository(tmp_path)


def test_external_compatibility_audit_rejects_boolean_numeric_aliases(
    tmp_path: Path,
) -> None:
    _copy_evidence(tmp_path)
    path = tmp_path / "benchmarks/compatibility/acon-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["required_diagnostic_contract"]["inference_concurrency"] = True
    payload["required_diagnostic_contract"]["retry_count"] = False
    _write_json(path, payload)

    with pytest.raises(CompatibilityAuditError, match="inference_concurrency"):
        audit_repository(tmp_path)

    _copy_evidence(tmp_path)
    manifest_path = tmp_path / "benchmarks/compatibility/acon-diagnostic-failed-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["identity"]["inference_concurrency"] = True
    _resign_failed_manifest(manifest)
    _write_json(manifest_path, manifest)

    with pytest.raises(CompatibilityAuditError, match="inference_concurrency"):
        audit_repository(tmp_path)

    _copy_evidence(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["limits"]["timeout_seconds"] = 300
    _resign_failed_manifest(manifest)
    _write_json(manifest_path, manifest)

    with pytest.raises(CompatibilityAuditError, match="limits timeout_seconds"):
        audit_repository(tmp_path)


def test_external_compatibility_audit_rejects_bare_required_options(
    tmp_path: Path,
) -> None:
    _copy_evidence(tmp_path)
    manifest_path = tmp_path / "benchmarks/compatibility/acon-diagnostic-failed-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["command"].extend(
        [
            "--acon-source-root",
            "--dependency-lock",
            "--network-isolation-evidence",
            "--inference-service-pid",
            "--max-inference-service-memory-mb",
            "--lms-executable",
        ]
    )
    _resign_failed_manifest(manifest)
    _write_json(manifest_path, manifest)

    report = audit_repository(tmp_path)
    blocker_ids = {blocker["id"] for blocker in report["blockers"]}
    assert "acon-external-run-command-missing-preflight-arguments" in blocker_ids
