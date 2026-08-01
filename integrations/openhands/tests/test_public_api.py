from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import ctxc_openhands


def test_public_api_is_explicit() -> None:
    assert set(ctxc_openhands.__all__) == {
        "AuthorityError",
        "AuthorityPolicy",
        "AuthorityVerificationError",
        "CanonicalUtf8ByteTokenizer",
        "CompatibilityManifestError",
        "DurableEventCallback",
        "EventMappingError",
        "FinalRequestLedger",
        "GuardedLocalConversation",
        "HostBypassRefusedError",
        "IntegrationPoisonedError",
        "LiveCompatibilityError",
        "LiveRequestAccountingUnavailableError",
        "OPENHANDS_API_MANIFEST_SHA256",
        "OPENHANDS_HOST_COMMIT",
        "OPENHANDS_HOST_VERSION",
        "OPENHANDS_SDK_COMMIT",
        "OPENHANDS_SDK_VERSION",
        "OpenHandsSession",
        "REQUIRED_COMPONENT_CATEGORIES",
        "RequestLedgerError",
        "RequestLedgerLimits",
        "SEMANTIC_RESULT_SCHEMA",
        "SQLiteGenerationStore",
        "SUPPORTED_EVENT_KINDS",
        "build_final_request_ledger",
        "deterministic_bundle_copy",
        "inspect_live_compatibility",
        "issue_authority_receipt",
        "load_pinned_host_api",
        "map_host_event",
        "pinned_callback",
        "semantic_result_digest",
        "semantic_result_projection",
        "validate_atomic_event_pair",
        "validate_final_request_ledger",
        "verify_pin_manifest",
    }
    assert ctxc_openhands.__version__ == "0.1.0a15"


def test_core_and_integration_imports_do_not_load_openhands() -> None:
    package_root = Path(__file__).resolve().parents[1]
    repository_root = package_root.parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (
            str(package_root / "src"),
            str(repository_root / "src"),
        )
    )
    code = """
import json
import sys
import context_compiler
import ctxc_openhands
print(json.dumps({
    "core": context_compiler.__version__,
    "integration": ctxc_openhands.__version__,
    "openhands": sorted(
        name for name in sys.modules
        if name == "openhands" or name.startswith("openhands.")
    ),
}))
"""

    completed = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=repository_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    report = json.loads(completed.stdout)

    assert report == {
        "core": "0.1.1a14",
        "integration": "0.1.0a15",
        "openhands": [],
    }
