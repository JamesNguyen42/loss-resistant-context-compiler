"""Importable hostile-callable fixtures for isolated compilation tests."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from context_compiler.extractors import ExtractionResult
from context_compiler.models import SourceRecord


class BlockingExtractor:
    name = "blocking-test-extractor"

    def __init__(self, marker_path: str) -> None:
        self.marker_path = marker_path

    def extract(self, _sources: list[SourceRecord]) -> ExtractionResult:
        time.sleep(2)
        Path(self.marker_path).write_text("not-terminated", encoding="utf-8")
        return ExtractionResult()


class DescendantExtractor:
    name = "descendant-test-extractor"

    def __init__(self, started_path: str, marker_path: str) -> None:
        self.started_path = started_path
        self.marker_path = marker_path

    def extract(self, _sources: list[SourceRecord]) -> ExtractionResult:
        Path(self.started_path).write_text("started", encoding="utf-8")
        child_program = (
            "from pathlib import Path;import sys,time;"
            "time.sleep(3);"
            "Path(sys.argv[1]).write_text('orphan',encoding='utf-8')"
        )
        creation_flags = (
            subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            if os.name == "nt"
            else 0
        )
        subprocess.Popen(
            [sys.executable, "-c", child_program, self.marker_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creation_flags,
        )
        time.sleep(10)
        return ExtractionResult()


class MutatingExtractor:
    name = "mutating-test-extractor"

    def extract(self, sources: list[SourceRecord]) -> ExtractionResult:
        object.__setattr__(sources[0], "content", "corrupted in worker")
        return ExtractionResult()
