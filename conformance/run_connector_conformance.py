#!/usr/bin/env python3
"""Dependency-free connector schema/transcript conformance runner."""

from __future__ import annotations

import copy
import io
import json
import math
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCHEMAS = ROOT / "schemas"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SRC))

from context_compiler import (  # noqa: E402
    LocalAIConnector,
    decode_connector_request,
    serve_stdio,
)

MAX_CONFORMANCE_BYTES = 32 * 1024 * 1024
MAX_CONFORMANCE_LINE_CHARS = 8 * 1024 * 1024

CONNECTOR_SCHEMAS = {
    "connector-request.schema.json",
    "connector-response.schema.json",
    "localai-source-event.schema.json",
    "context-bundle.schema.json",
    "incremental-checkpoint.schema.json",
    *{
        f"connector-{operation}-{side}.schema.json"
        for operation in (
            "capabilities",
            "ingest-source-events",
            "compile-memory",
            "render-context",
            "verify-memory",
            "inspect-memory",
        )
        for side in ("payload", "result")
    },
}


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    decoded = float(value)
    if not math.isfinite(decoded):
        raise ValueError("conformance JSON numbers must be finite")
    return decoded


def _read_bounded_text(path: Path, *, label: str) -> str:
    try:
        candidate = path.lstat()
    except OSError as exc:
        raise ValueError(f"could not inspect {label}: {path}") from exc
    if not stat.S_ISREG(candidate.st_mode) or candidate.st_nlink != 1:
        raise ValueError(f"{label} must be a single-link regular file")
    if candidate.st_size > MAX_CONFORMANCE_BYTES:
        raise ValueError(f"{label} exceeds {MAX_CONFORMANCE_BYTES} bytes")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"could not open {label}: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            (candidate.st_dev, candidate.st_ino)
            != (opened.st_dev, opened.st_ino)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
        ):
            raise ValueError(f"{label} changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(64 * 1024, MAX_CONFORMANCE_BYTES - total + 1),
            )
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_CONFORMANCE_BYTES:
                raise ValueError(
                    f"{label} exceeds {MAX_CONFORMANCE_BYTES} bytes"
                )
            chunks.append(chunk)
        final = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        current = path.lstat()
    except OSError as exc:
        raise ValueError(f"could not recheck {label}: {path}") from exc
    # Windows cloud-file providers can expose a descriptor-local change time
    # that differs from the path view without changing the file or its bytes.
    # Change time is metadata-only, so bind identity plus content-relevant
    # metadata and deliberately exclude st_ctime_ns from this comparison.
    opened_snapshot = (
        opened.st_dev,
        opened.st_ino,
        opened.st_mode,
        opened.st_nlink,
        opened.st_size,
        opened.st_mtime_ns,
    )
    final_snapshot = (
        final.st_dev,
        final.st_ino,
        final.st_mode,
        final.st_nlink,
        final.st_size,
        final.st_mtime_ns,
    )
    current_snapshot = (
        current.st_dev,
        current.st_ino,
        current.st_mode,
        current.st_nlink,
        current.st_size,
        current.st_mtime_ns,
    )
    if opened_snapshot != final_snapshot or current_snapshot != final_snapshot:
        raise ValueError(f"{label} changed while reading")
    try:
        decoded = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be UTF-8") from exc
    if any(len(line) > MAX_CONFORMANCE_LINE_CHARS for line in decoded.splitlines()):
        raise ValueError(
            f"{label} exceeds {MAX_CONFORMANCE_LINE_CHARS} characters on one line"
        )
    return decoded


def _load_json(path: Path) -> Any:
    return json.loads(
        _read_bounded_text(path, label=f"schema {path.name}"),
        object_pairs_hook=_strict_object,
        parse_float=_finite_float,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON constant {value}")
        ),
    )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_document = _read_bounded_text(path, label=f"fixture {path.name}")
    for line_number, raw in enumerate(raw_document.splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_float=_finite_float,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant {constant}")
            ),
        )
        if not isinstance(value, dict):
            raise TypeError(f"{path}:{line_number} must be an object")
        rows.append(value)
    return rows


def _follow_fragment(document: Any, fragment: str, *, label: str) -> None:
    current = document
    if not fragment:
        return
    if not fragment.startswith("/"):
        raise ValueError(f"{label} has unsupported non-pointer fragment")
    for component in fragment[1:].split("/"):
        key = component.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            raise ValueError(f"{label} points to missing component {key!r}")
        current = current[key]


def _validate_schema_graph() -> None:
    missing = CONNECTOR_SCHEMAS - {path.name for path in SCHEMAS.glob("*.json")}
    if missing:
        raise ValueError(f"missing connector schemas: {sorted(missing)}")
    documents = {path.name: _load_json(path) for path in SCHEMAS.glob("*.json")}
    for filename in sorted(CONNECTOR_SCHEMAS):
        document = documents[filename]
        if document.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise ValueError(f"{filename} is not Draft 2020-12")
        if document.get("type") != "object":
            raise ValueError(f"{filename} root must describe an object")
        stack = [document]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                reference = value.get("$ref")
                if isinstance(reference, str):
                    target_name, _, fragment = reference.partition("#")
                    target_name = target_name or filename
                    if "://" in target_name or target_name not in documents:
                        raise ValueError(f"{filename} has unresolved local ref {reference!r}")
                    _follow_fragment(
                        documents[target_name],
                        fragment,
                        label=f"{filename}:{reference}",
                    )
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)


def _lookup(responses: dict[str, dict[str, Any]], reference: str) -> Any:
    step, _, path = reference.partition(".")
    current: Any = responses[step]
    for component in path.split(".") if path else ():
        current = current[component]
    return copy.deepcopy(current)


def _resolve(value: Any, responses: dict[str, dict[str, Any]]) -> Any:
    if isinstance(value, dict) and set(value) == {"$ctxc_ref"}:
        return _lookup(responses, value["$ctxc_ref"])
    if isinstance(value, dict):
        return {key: _resolve(entry, responses) for key, entry in value.items()}
    if isinstance(value, list):
        return [_resolve(entry, responses) for entry in value]
    return value


def _normalize(response: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(response)
    operation = normalized.get("operation")
    result = normalized.get("result")
    if not isinstance(result, dict):
        return normalized
    if operation == "compile_memory":
        bundle = result["bundle"]
        bundle["artifact"]["compiled_at"] = "<dynamic>"
        metrics = bundle["artifact"]["compiler_metadata"].get("metrics")
        if isinstance(metrics, dict):
            metrics["compile_duration_seconds"] = 0
        bundle["artifact"]["artifact_sha256"] = "<derived>"
        bundle["bindings"]["artifact_sha256"] = "<derived>"
        bundle["bundle_sha256"] = "<derived>"
    elif operation == "inspect_memory":
        result["bundle_sha256"] = "<derived>"
        result["bindings"]["artifact_sha256"] = "<derived>"
        result["artifact"]["artifact_sha256"] = "<derived>"
        result["artifact"]["compiled_at"] = "<dynamic>"
        metrics = result["artifact"].get("metrics")
        if isinstance(metrics, dict):
            metrics["compile_duration_seconds"] = 0
    return normalized


class StdioClient:
    def __init__(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SRC)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "context_compiler", "connector", "--stdio"],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

    def exchange(self, raw: str) -> dict[str, Any]:
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.process.stdin.write(raw + "\n")
        self.process.stdin.flush()
        response = self.process.stdout.readline()
        if not response:
            raise RuntimeError("connector stdio process ended without a response")
        return json.loads(response, object_pairs_hook=_strict_object)

    def close(self) -> None:
        assert self.process.stdin is not None
        assert self.process.stderr is not None
        self.process.stdin.close()
        code = self.process.wait(timeout=30)
        stderr = self.process.stderr.read()
        if code != 0 or stderr:
            raise RuntimeError(f"connector stdio failed ({code}): {stderr}")


def _assert_expectations(response: dict[str, Any], expectations: dict[str, Any]) -> None:
    for path, expected in expectations.items():
        current: Any = response
        if path.endswith(" contains"):
            path = path.removesuffix(" contains")
            for component in path.split("."):
                current = current[component]
            if expected not in current:
                raise AssertionError(f"{path} does not contain {expected!r}")
            continue
        for component in path.split("."):
            current = current[component]
        if current != expected:
            raise AssertionError(f"{path}: expected {expected!r}, got {current!r}")


def _embedded_stdio(raw: str) -> dict[str, Any]:
    output = io.StringIO()
    serve_stdio(
        connector=LocalAIConnector(),
        input_stream=io.StringIO(raw + "\n"),
        output_stream=output,
    )
    return json.loads(output.getvalue(), object_pairs_hook=_strict_object)


def run() -> dict[str, int]:
    _validate_schema_graph()
    steps = _load_jsonl(FIXTURES / "golden-success.jsonl")
    vectors = _load_jsonl(FIXTURES / "golden-negative.jsonl")
    if len(vectors) < 50:
        raise AssertionError("connector conformance requires at least 50 negative vectors")

    direct_connector = LocalAIConnector()
    direct_responses: dict[str, dict[str, Any]] = {}
    stdio_responses: dict[str, dict[str, Any]] = {}
    stdio = StdioClient()
    try:
        for step in steps:
            request_direct = _resolve(step["request"], direct_responses)
            request_stdio = _resolve(step["request"], stdio_responses)
            direct = direct_connector.handle_request(
                decode_connector_request(json.dumps(request_direct, separators=(",", ":")))
            )
            wire = stdio.exchange(json.dumps(request_stdio, separators=(",", ":")))
            if _normalize(direct) != _normalize(wire):
                raise AssertionError(f"{step['id']} in-process/stdio semantic mismatch")
            _assert_expectations(direct, step["expect"])
            _assert_expectations(wire, step["expect"])
            direct_responses[step["id"]] = direct
            stdio_responses[step["id"]] = wire

        for vector in vectors:
            raw = vector["raw"]
            embedded = _embedded_stdio(raw)
            wire = stdio.exchange(raw)
            if embedded != wire:
                raise AssertionError(f"{vector['id']} embedded/process stdio mismatch")
            _assert_expectations(wire, vector["expect"])
    finally:
        stdio.close()

    return {
        "schemas": len(CONNECTOR_SCHEMAS),
        "golden_steps": len(steps),
        "negative_vectors": len(vectors),
    }


def main() -> int:
    result = run()
    print(json.dumps({"passed": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
