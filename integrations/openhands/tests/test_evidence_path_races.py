from __future__ import annotations

from pathlib import Path
from typing import Any, BinaryIO

import pytest

from ctxc_openhands.evidence import (
    EvidenceValidationError,
    load_evidence_report,
    write_evidence_report,
)
from ctxc_openhands.scenario import run_offline_crash_scenario


def test_report_mutation_during_open_descriptor_read_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "scenario.sqlite3"
    report_path = tmp_path / "scenario.json"
    report = run_offline_crash_scenario(database)
    write_evidence_report(report_path, report)
    real_open = Path.open

    class MutatingReader:
        def __init__(self, stream: BinaryIO) -> None:
            self.stream = stream
            self.mutated = False

        def __enter__(self) -> MutatingReader:
            return self

        def __exit__(self, *args: object) -> None:
            self.stream.close()

        def read(self, size: int = -1) -> bytes:
            value = self.stream.read(size)
            if not self.mutated:
                with real_open(report_path, "ab") as writer:
                    writer.write(b" ")
                    writer.flush()
                self.mutated = True
            return value

        def fileno(self) -> int:
            return self.stream.fileno()

    def patched_open(
        path: Path,
        mode: str = "r",
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        stream = real_open(path, mode, *args, **kwargs)
        if path == report_path and mode == "rb":
            return MutatingReader(stream)
        return stream

    monkeypatch.setattr(Path, "open", patched_open)

    with pytest.raises(
        EvidenceValidationError,
        match="identity or bytes changed while reading",
    ):
        load_evidence_report(report_path)


def test_linked_parent_report_paths_fail_closed_when_supported(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlink creation is unavailable")

    database = tmp_path / "scenario.sqlite3"
    report = run_offline_crash_scenario(database)
    direct = real_parent / "report.json"
    write_evidence_report(direct, report)

    with pytest.raises(EvidenceValidationError, match="unsafe evidence report path"):
        load_evidence_report(linked_parent / "report.json")
    with pytest.raises(EvidenceValidationError, match="unsafe evidence report path"):
        write_evidence_report(linked_parent / "new-report.json", report)
