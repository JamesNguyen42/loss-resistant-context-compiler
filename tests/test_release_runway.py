from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
FULL_ACTION_REF = re.compile(r"^\s*uses:\s+[^@\s]+@([0-9a-f]{40})(?:\s+#.*)?$")
BUILD_LOCK_LINES = [
    "build==1.5.0 --hash=sha256:"
    "13f3eecb844759ab66efec90ca17639bbf14dc06cb2fdf37a9010322d9c50a6f",
    "colorama==0.4.6 --hash=sha256:"
    "4f1d9991f5acc0ca119f9d443620b77f9d6b33703e51011c16baf57afb285fc6",
    "packaging==26.2 --hash=sha256:"
    "5fc45236b9446107ff2415ce77c807cee2862cb6fac22b8a73826d0693b0980e",
    "pip==25.0.1 --hash=sha256:"
    "c46efd13b6aa8279f33f2864459c8ce587ea6a1a59ee20de055868d8f7688f7f",
    "pyproject-hooks==1.2.0 --hash=sha256:"
    "9e5c6bfa8dcc30091c74b0cf803c81fdd29d94f01992a7707bc97babb1141913",
    "setuptools==83.0.0 --hash=sha256:"
    "29b23c360f22f414dc7336bb39178cc7bcbf6021ed2733cde173f09dba19abb3",
    "wheel==0.47.0 --hash=sha256:"
    "212281cab4dff978f6cedd499cd893e1f620791ca6ff7107cf270781e587eced",
]


def _workflow_action_lines(path: Path) -> list[str]:
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith("uses:")
    ]


def _workflow_job(workflow: str, name: str) -> str:
    marker = f"  {name}:\n"
    assert workflow.count(marker) == 1
    remainder = workflow.split(marker, maxsplit=1)[1]
    next_job = re.search(r"(?m)^  [a-z][a-z0-9-]+:\n", remainder)
    return remainder if next_job is None else remainder[: next_job.start()]


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

    assert 'python-version: ["3.11", "3.12", "3.13", "3.14"]' in workflow
    assert "os: [windows-latest, macos-latest]" in workflow
    assert "python -m compileall -q" in workflow
    assert "conformance _ctxc_build_backend.py" in workflow
    assert "python -m ruff check" in workflow
    assert "scripts/release_install_smoke.py" in workflow
    assert "-m _ctxc_build_backend --sdist-dir" in workflow
    assert 'SOURCE_DATE_EPOCH: "1700000000"' in workflow
    assert "exact repeated release builds" in workflow
    assert "python -m scripts.release_reproducibility" in workflow
    assert "release-reproducibility.json" in workflow
    assert "python -m scripts.release_artifact_manifest create" in workflow
    assert "python -m scripts.release_artifact_manifest verify" in workflow
    assert "dist/SHA256SUMS" in workflow
    assert "dist/release-artifacts.json" in workflow
    assert "python conformance/run_connector_conformance.py" in workflow
    assert "python -m benchmarks.natural_history" in workflow
    assert "tests/test_archive_locking.py" in workflow
    assert "tests/test_compile_deadline.py" in workflow
    assert "tests/test_deterministic_sdist.py" in workflow
    assert "tests/test_external_runner.py" in workflow
    assert "tests/test_release_install_smoke.py" in workflow
    assert workflow.count(
        "python -m pip --isolated download --no-deps --only-binary=:all:"
    ) == 3
    assert workflow.count("Provision the hash-bound release builder") == 2
    assert "Provision and record the hash-bound same-job release builder" in workflow
    assert workflow.count("len(distributions) == 7 and actual == expected") == 3
    assert workflow.count(
        "& $env:CTXC_BUILD_PYTHON -m pip --isolated wheel ."
    ) == 5
    assert workflow.count("--build-wheelhouse ci-build-wheelhouse") == 2
    assert workflow.count("--build-requirements requirements-build.lock") == 2
    assert workflow.count("release-install-smoke.json") >= 4
    assert workflow.count("release-install-smoke.log") >= 4
    assert workflow.count("ctxc-release-install-smoke-0.3") == 2
    assert "ctxc-release-install-smoke-0.2" not in workflow
    assert "ctxc-release-install-smoke-0.1" not in workflow
    assert workflow.count("materialized_context_witness") == 4
    assert workflow.count("ctxc-materialized-context-witness-0.3") == 2
    assert workflow.count("source_wheel_sdist_witness_bytes_identical") == 4
    assert workflow.count(
        "evaluation['source_wheel_sdist_report_bytes_identical'] is True"
    ) == 2
    assert workflow.count("evaluation['integrity_passed'] is False") == 2
    assert workflow.count("evaluation['inference_status']=='not_run'") == 2
    assert workflow.count("evaluation['retrieval_status']=='not_run'") == 2
    assert workflow.count("hash-pinned-offline-wheelhouse") == 2
    assert workflow.count("ci-build-wheelhouse/*.whl") == 3
    assert "release-requirements-build.lock" in workflow
    assert "root-release-smoke-${{ runner.os }}-python-3.13" in workflow
    assert "Prepare the extracted-sdist candidate" in workflow
    assert "_sdist_inventory(first_sdist)" in workflow
    assert 'archive.extractall(extraction_parent, filter="data")' in workflow
    assert workflow.index("_sdist_inventory(first_sdist)") < workflow.index(
        'archive.extractall(extraction_parent, filter="data")'
    )
    assert "expected exactly one extracted source root" in workflow
    roundtrip_step = workflow.split(
        "- name: Build the extracted-sdist candidate",
        maxsplit=1,
    )[1].split("- name:", maxsplit=1)[0]
    assert "& $env:CTXC_BUILD_PYTHON -m pip --isolated wheel ." in roundtrip_step
    assert "--no-build-isolation" in roundtrip_step
    assert "--no-index" in roundtrip_step
    assert "--no-deps" in roundtrip_step
    assert "-m _ctxc_build_backend" in roundtrip_step
    assert "--sdist-dir ../../dist-roundtrip" in roundtrip_step
    assert "release-roundtrip-reproducibility.json" in workflow
    assert "dist-roundtrip/*.whl" in workflow
    assert "dist-roundtrip/*.tar.gz" in workflow
    assert "--no-build-isolation" in workflow
    assert "--no-index" in workflow
    assert "--only-binary=:all:" in workflow
    assert "--require-hashes" in workflow
    assert "--force-reinstall" in workflow
    assert "release-python.txt" in workflow
    assert "release-build-toolchain.txt" in workflow
    assert "if-no-files-found: error" in workflow
    assert "permissions:\n  contents: read" in workflow


def test_python_support_lanes_remain_scoped_by_role() -> None:
    core_workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    unit_tests = _workflow_job(core_workflow, "unit-tests")
    platform_smoke = _workflow_job(core_workflow, "platform-smoke")
    reproducibility = _workflow_job(core_workflow, "reproducibility")
    benchmark = _workflow_job(core_workflow, "benchmark")

    assert 'python-version: ["3.11", "3.12", "3.13", "3.14"]' in unit_tests
    assert "python-version: ${{ matrix.python-version }}" in unit_tests
    assert platform_smoke.count('python-version: "3.13"') == 1
    assert reproducibility.count('python-version: "3.13"') == 1
    assert benchmark.count('python-version: "3.11"') == 1

    openhands_workflow = (
        ROOT / ".github" / "workflows" / "openhands-integration.yml"
    ).read_text(encoding="utf-8")
    offline_package = _workflow_job(openhands_workflow, "offline-package")
    package_reproducibility = _workflow_job(
        openhands_workflow,
        "package-byte-reproducibility",
    )
    retained_evidence = _workflow_job(
        openhands_workflow,
        "retained-offline-evidence",
    )

    assert 'python-version: ["3.12", "3.13"]' in offline_package
    assert "python-version: ${{ matrix.python-version }}" in offline_package
    assert package_reproducibility.count('python-version: "3.13"') == 1
    assert retained_evidence.count('python-version: "3.12"') == 1


def test_root_build_lock_is_the_reviewed_exact_universal_wheel_set() -> None:
    lock = ROOT / "requirements-build.lock"
    payload = lock.read_bytes()

    assert len(payload) == 666
    assert hashlib.sha256(payload).hexdigest() == (
        "243f3ab977d82c04968cf4ea6474b7ef79c060d485aa3a3d67a383d1ef6fbbfe"
    )
    assert b"\r" not in payload
    assert payload.endswith(b"\n")
    assert payload.decode("ascii").splitlines() == BUILD_LOCK_LINES


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
    assert "two clean checkouts" in supply_chain
    assert "same-job-toolchain repeated-build defect" in " ".join(
        supply_chain.split()
    )
    assert "hash-pinned inputs" in supply_chain
    assert "hash-pinned build wheelhouse" in supply_chain


def test_source_distribution_manifest_includes_release_runway_assets() -> None:
    manifest = set((ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines())

    assert {
        "include CONTRIBUTING.md",
        "include SECURITY.md",
        "include _ctxc_build_backend.py",
        "include requirements-build.lock",
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
