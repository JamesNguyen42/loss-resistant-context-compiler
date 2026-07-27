from __future__ import annotations

import os
from pathlib import Path

import pytest

import ctxc_openhands.evidence as evidence_module
from ctxc_openhands.evidence import (
    EvidenceValidationError,
    load_evidence_report,
    write_evidence_report,
)
from ctxc_openhands.scenario import run_offline_crash_scenario


def _scenario_report(tmp_path: Path) -> Path:
    report = run_offline_crash_scenario(tmp_path / "scenario.sqlite3")
    report_path = tmp_path / "scenario.json"
    write_evidence_report(report_path, report)
    return report_path


def test_evidence_loader_rejects_hardlink_alias(
    tmp_path: Path,
) -> None:
    report_path = _scenario_report(tmp_path)
    alias = tmp_path / "scenario-alias.json"
    try:
        os.link(report_path, alias)
    except OSError as exc:
        pytest.skip(f"hard-link creation is unavailable: {exc}")

    with pytest.raises(EvidenceValidationError, match="hard-link aliases"):
        load_evidence_report(report_path)


def test_evidence_loader_rejects_post_open_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path = _scenario_report(tmp_path)
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(report_path.read_bytes())
    real_identity = evidence_module._regular_file_identity
    calls = 0

    def replacing_identity(path: Path, *, label: str) -> tuple[int, int]:
        nonlocal calls
        calls += 1
        if calls == 2:
            os.replace(replacement, report_path)
        return real_identity(path, label=label)

    monkeypatch.setattr(
        evidence_module,
        "_regular_file_identity",
        replacing_identity,
    )

    with pytest.raises(
        EvidenceValidationError,
        match="identity or bytes changed while reading",
    ):
        load_evidence_report(report_path)
