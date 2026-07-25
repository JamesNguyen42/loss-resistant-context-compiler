from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.adapters.acon_diagnostic import adapter

ROOT = Path(__file__).resolve().parents[1]
COMPATIBILITY = ROOT / "benchmarks" / "compatibility"
LICENSE_SHA256 = {
    "acon": "c2cfccb812fe482101a8f04597dfc5a9991a6b2748266c47ac91b6a5aae15383",
    "ama-agent": "3c8bff7214b9a3f79e2b1e76413104fff6df8b315d6598b2e8d16a9cadd2e244",
}
ACON_DIAGNOSTIC_DATASET_SHA256 = (
    "0267866a43e7f122e167000f22fc55919eeb456602c4ce1570a8650be6ad1f62"
)
RETAINED_ACON_BLOCKERS = [
    "missing --acon-source-root",
    "adapter runtime must be exactly Python 3.11",
    "missing --dependency-lock",
    "missing --network-isolation-evidence",
    "missing positive --inference-service-pid",
    "missing --max-inference-service-memory-mb",
    "missing --lms-executable",
]


@pytest.mark.parametrize("name", ("acon-v1.json", "ama-agent-v1.json"))
def test_result_blind_preflight_records_keep_claim_boundary(name: str) -> None:
    payload = json.loads((COMPATIBILITY / name).read_text(encoding="utf-8"))

    assert payload["schema"] == "lrcbench-compatibility-preflight-0.1"
    assert payload["status"] == "screening"
    assert payload["result_blind"] is True
    assert payload["comparative_output_generated"] is False
    assert payload["comparative_output_inspected"] is False
    assert payload["claim_ready"] is False
    assert payload["blockers"]
    assert "metrics" not in payload
    assert "results" not in payload
    assert len(payload["source"]["revision"]) == 40
    assert payload["source"]["tree_url"].endswith(payload["source"]["revision"])
    assert len(payload["license"]["file_sha256"]) == 64
    assert payload["license"]["file_sha256"] == LICENSE_SHA256[payload["system"]]
    assert payload["dependencies"]["resolved_lock_state"] == "absent"
    contract = payload["required_diagnostic_contract"]
    assert contract["model_id"] == "qwen/qwen3.6-35b-a3b@q4_k_m"
    assert contract["model_context_length"] == 8192
    assert contract["inference_concurrency"] == 1
    assert contract["retry_count"] == 0
    assert contract["model_service_cost_usd"] == 0
    assert contract["network_access"] is False


def test_acon_preflight_retains_exact_missing_prerequisites() -> None:
    args = adapter._parser().parse_args(["preflight"])
    report = adapter.preflight(args, check_model=False)

    expected = ["missing --acon-source-root"]
    if sys.version_info[:2] != (3, 11):
        expected.append("adapter runtime must be exactly Python 3.11")
    expected.extend(
        [
            "missing --dependency-lock",
            "missing --network-isolation-evidence",
            "missing positive --inference-service-pid",
            "missing --max-inference-service-memory-mb",
            "missing --lms-executable",
        ]
    )
    assert report["result_blind"] is True
    assert report["comparative_output_generated"] is False
    assert report["ready"] is False
    assert report["blockers"] == expected


def test_retained_blocker_matches_the_attempted_host_preflight() -> None:
    payload = json.loads(
        (COMPATIBILITY / "acon-diagnostic-blocker.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload["status"] == "blocked-before-external-runner"
    assert payload["runner_invoked"] is False
    assert payload["candidate_created"] is False
    assert payload["manifest_created"] is False
    assert payload["result_blind"] is True
    assert payload["comparative_output_generated"] is False
    assert payload["attempted_command"] == [
        ".venv/Scripts/python.exe",
        "benchmarks/adapters/acon_diagnostic/adapter.py",
        "preflight",
    ]
    assert payload["attempted_at_host_runtime"] == "Python 3.12.13"
    assert payload["blockers"] == RETAINED_ACON_BLOCKERS


def test_retained_external_runner_manifest_remains_a_verified_failure() -> None:
    manifest_path = COMPATIBILITY / "acon-diagnostic-failed-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["process_succeeded"] is False
    assert payload["candidate_valid"] is False
    assert payload["ready_for_scoring"] is False
    assert payload["claim_metadata_complete"] is False
    assert payload["exit_code"] == 2
    assert payload["termination_reason"] == (
        "case_failure:history-000:nonzero_exit"
    )
    unsigned = dict(payload)
    claimed = unsigned.pop("manifest_sha256")
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == claimed
    assert claimed == (
        "4688dcedd1091dc82c3d60769fe0d72b5772ec170f4e79245026dfdc5e3402b2"
    )
    assert payload["dataset_sha256"] == ACON_DIAGNOSTIC_DATASET_SHA256
    assert payload["identity"]["adapter_revision"] == adapter.ACON_REVISION
    assert payload["identity"]["model_id"] == "qwen/qwen3.6-35b-a3b@q4_k_m"
    assert payload["limits"]["max_memory_mb"] == 32_768
    assert payload["adapter_source"]["tree_sha256"]
    assert payload["adapter_runtime"]["executable_sha256"]
    assert payload["process_environment"]["environment_sha256"]
    assert payload["command_sha256"]
    assert payload["dependency_lock"]["evidence_sha256"] is None
    assert payload["network_isolation"]["mode"] == "unverified"
    assert payload["network_isolation"]["evidence_sha256"] is None


def test_acon_preflight_rejects_nonpositive_inference_memory_limit() -> None:
    args = adapter._parser().parse_args(
        ["preflight", "--max-inference-service-memory-mb", "-1"]
    )

    report = adapter.preflight(args, check_model=False)

    assert "--max-inference-service-memory-mb must be positive" in report[
        "blockers"
    ]
    assert report["ready"] is False


def test_acon_corpus_loader_rejects_nonfinite_exponent(tmp_path: Path) -> None:
    corpus = tmp_path / "nonfinite.json"
    corpus.write_text('{"value":1e9999}', encoding="utf-8")

    with pytest.raises(adapter.PreflightError, match="finite"):
        adapter._strict_object(corpus)


def test_acon_preflight_cli_is_an_executable_blocker() -> None:
    completed = subprocess.run(
        [sys.executable, str(adapter.__file__), "preflight"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["ready"] is False
    assert "missing --acon-source-root" in payload["blockers"]
    assert completed.stderr == ""
