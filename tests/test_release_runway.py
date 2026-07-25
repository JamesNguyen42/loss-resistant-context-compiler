from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
FULL_ACTION_REF = re.compile(r"^\s*uses:\s+[^@\s]+@([0-9a-f]{40})(?:\s+#.*)?$")


def _workflow_action_lines(path: Path) -> list[str]:
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith("uses:")
    ]


def test_workflows_pin_every_action_to_an_immutable_full_sha() -> None:
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows
    action_lines = [
        line
        for workflow in workflows
        for line in _workflow_action_lines(workflow)
    ]
    assert action_lines
    assert all(FULL_ACTION_REF.match(line) for line in action_lines), action_lines


def test_ci_covers_supported_python_and_platform_release_smokes() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert 'python-version: ["3.11", "3.12", "3.13"]' in workflow
    assert "os: [windows-latest, macos-latest]" in workflow
    assert (
        "python -m compileall -q src benchmarks tests scripts conformance"
        in workflow
    )
    assert "scripts/release_install_smoke.py" in workflow
    assert "python -m scripts.release_artifact_manifest create" in workflow
    assert "python -m scripts.release_artifact_manifest verify" in workflow
    assert "dist/SHA256SUMS" in workflow
    assert "dist/release-artifacts.json" in workflow
    assert "python conformance/run_connector_conformance.py" in workflow
    assert "python -m benchmarks.natural_history" in workflow
    assert "tests/test_archive_locking.py" in workflow
    assert "tests/test_release_install_smoke.py" in workflow
    assert "permissions:\n  contents: read" in workflow


def test_codeql_uses_a_pinned_python_analysis_with_narrow_permissions() -> None:
    workflow = (ROOT / ".github" / "workflows" / "codeql.yml").read_text(
        encoding="utf-8"
    )

    assert "languages: python" in workflow
    assert "queries: security-extended" in workflow
    assert "contents: read" in workflow
    assert "security-events: write" in workflow
    assert "contents: write" not in workflow
    assert "github/codeql-action/init@e4fba868fa4b1b91e1fdab776edc8cfbe6e9fb81" in workflow
    assert "github/codeql-action/analyze@e4fba868fa4b1b91e1fdab776edc8cfbe6e9fb81" in workflow


def test_supply_chain_configuration_tracks_actions_and_python_manifests() -> None:
    dependabot = (ROOT / ".github" / "dependabot.yml").read_text(
        encoding="utf-8"
    )
    dependency_review = (
        ROOT / ".github" / "workflows" / "dependency-review.yml"
    ).read_text(encoding="utf-8")
    codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(
        encoding="utf-8"
    )

    assert "package-ecosystem: github-actions" in dependabot
    assert "package-ecosystem: pip" in dependabot
    assert "fail-on-severity: high" in dependency_review
    assert "@JamesNguyen42" in codeowners
    assert "/docs/results/ @JamesNguyen42" in codeowners


def test_release_and_security_documents_preserve_claim_boundaries() -> None:
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "RELEASE_CHECKLIST.md").read_text(
        encoding="utf-8"
    )
    supply_chain = (ROOT / "docs" / "SUPPLY_CHAIN.md").read_text(
        encoding="utf-8"
    )

    assert "not a security certification" in security
    assert "must include a regression" in security
    assert "Never imply semantic completeness" in contributing
    assert "Do not overwrite files in" in contributing
    assert "does not authorize a merge" in checklist
    assert "Keep every failing external adapter" in checklist
    assert "python -m scripts.release_artifact_manifest create" in checklist
    assert "hash-pinned offline wheelhouse" in checklist
    assert "does not yet publish signed artifacts" in " ".join(supply_chain.split())
    assert "source distribution is not yet byte-for-byte reproducible" in " ".join(
        supply_chain.split()
    )
    assert "hash-pinned build wheelhouse" in supply_chain


def test_source_distribution_manifest_includes_release_runway_assets() -> None:
    manifest = set((ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines())

    assert {
        "include CONTRIBUTING.md",
        "include SECURITY.md",
        "recursive-include scripts *.py",
    } <= manifest


def test_release_install_smoke_runner_has_a_standalone_help_contract() -> None:
    runner = ROOT / "scripts" / "release_install_smoke.py"
    source = runner.read_text(encoding="utf-8")
    assert "Path(tempfile.gettempdir()).resolve(strict=True)" in source


    completed = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--help",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "separate clean environments" in completed.stdout
