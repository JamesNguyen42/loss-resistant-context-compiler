"""Result-blind ACON adapter for the bounded LRCBench external runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from benchmarks.json_io import (  # noqa: E402
    StrictJsonLimits,
    load_strict_json_file,
)

ACON_REVISION = "d63f9ae18959dc7215ff62899c94c5e8c56847ae"
ACON_REPOSITORY = "https://github.com/microsoft/acon"
MAX_CORPUS_BYTES = 128 * 1024 * 1024
MAX_SUMMARY_CHARS = 2_000_000

_CORPUS_LIMITS = StrictJsonLimits(
    max_bytes=MAX_CORPUS_BYTES,
    max_line_chars=8 * 1024 * 1024,
    max_depth=128,
)


class PreflightError(RuntimeError):
    """The pinned diagnostic environment is incomplete or inconsistent."""


def _strict_object(path: Path) -> dict[str, Any]:
    try:
        loaded = load_strict_json_file(
            path,
            limits=_CORPUS_LIMITS,
            label="ACON diagnostic corpus",
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise PreflightError(str(exc)) from exc
    value = loaded.value
    if not isinstance(value, dict):
        raise PreflightError("corpus must decode to an object")
    return value


def _run_text(command: list[str], *, timeout: float = 10.0) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PreflightError(f"could not execute {Path(command[0]).name}") from exc
    if completed.returncode != 0:
        raise PreflightError(
            f"{Path(command[0]).name} exited with status {completed.returncode}"
        )
    return completed.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def preflight(args: argparse.Namespace, *, check_model: bool = True) -> dict[str, Any]:
    blockers: list[str] = []
    source_root = args.acon_source_root
    if source_root is None:
        blockers.append("missing --acon-source-root")
    elif not source_root.is_dir():
        blockers.append("--acon-source-root is not a directory")
    else:
        try:
            revision = _run_text(
                ["git", "-C", str(source_root), "rev-parse", "HEAD"]
            )
            dirty = _run_text(
                [
                    "git",
                    "-C",
                    str(source_root),
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                ]
            )
            origin = _run_text(
                ["git", "-C", str(source_root), "remote", "get-url", "origin"]
            )
        except PreflightError as exc:
            blockers.append(str(exc))
        else:
            if revision != ACON_REVISION:
                blockers.append("ACON checkout revision does not match the pin")
            if dirty:
                blockers.append("ACON checkout is not clean")
            if origin.rstrip("/").removesuffix(".git") != ACON_REPOSITORY:
                blockers.append("ACON checkout origin does not match the official repository")
            required = (
                source_root / "src/productive_agents/ctxopt/history_optimizer.py",
                source_root / "experiments/appworld/prompts/context_opt/system_prompt.jinja",
                source_root / "experiments/appworld/prompts/context_opt/prompt_history_v2.jinja",
            )
            if not all(path.is_file() for path in required):
                blockers.append("ACON checkout lacks the required optimizer prompt assets")

    if sys.version_info[:2] != (3, 11):
        blockers.append("adapter runtime must be exactly Python 3.11")
    if args.dependency_lock is None:
        blockers.append("missing --dependency-lock")
    elif not args.dependency_lock.is_file():
        blockers.append("--dependency-lock is not a regular file")
    if args.network_isolation_evidence is None:
        blockers.append("missing --network-isolation-evidence")
    elif not args.network_isolation_evidence.is_file():
        blockers.append("--network-isolation-evidence is not a regular file")
    if args.inference_service_pid is None or args.inference_service_pid <= 0:
        blockers.append("missing positive --inference-service-pid")
    if args.max_inference_service_memory_mb is None:
        blockers.append("missing --max-inference-service-memory-mb")
    elif args.max_inference_service_memory_mb <= 0:
        blockers.append("--max-inference-service-memory-mb must be positive")
    if args.lms_executable is None:
        blockers.append("missing --lms-executable")
    elif not args.lms_executable.is_file():
        blockers.append("--lms-executable is not a regular file")
    elif check_model and not blockers:
        try:
            from context_compiler import LmsQwenCompletion

            LmsQwenCompletion(args.lms_executable).preflight()
        except Exception as exc:
            blockers.append(f"exact local Qwen preflight failed: {type(exc).__name__}")

    return {
        "schema": "lrcbench-acon-adapter-preflight-0.1",
        "system": "acon",
        "repository": ACON_REPOSITORY,
        "revision": ACON_REVISION,
        "result_blind": True,
        "comparative_output_generated": False,
        "ready": not blockers,
        "blockers": blockers,
        "dependency_lock_sha256": (
            _sha256(args.dependency_lock)
            if args.dependency_lock is not None and args.dependency_lock.is_file()
            else None
        ),
        "network_isolation_evidence_sha256": (
            _sha256(args.network_isolation_evidence)
            if args.network_isolation_evidence is not None
            and args.network_isolation_evidence.is_file()
            else None
        ),
    }


class _AconQwenBackend:
    def __init__(self, executable: Path, system_message: str = "") -> None:
        from context_compiler import LmsQwenCompletion

        self.transport = LmsQwenCompletion(executable)
        self.system_message = system_message

    def generate(self, prompt: str, temperature: float = 0.0) -> str:
        if temperature != 0.0:
            raise PreflightError("diagnostic ACON temperature must be zero")
        self.transport.preflight()
        output = self.transport._run(  # narrow reuse of the approved CLI boundary
            [
                "chat",
                self.transport.model_key,
                "--prompt",
                prompt,
                "--system-prompt",
                self.system_message,
                "--reasoning",
                "off",
                "--dont-fetch-catalog",
                "--yes",
            ],
            timeout=float(self.transport.timeout_seconds),
        )
        if len(output) > MAX_SUMMARY_CHARS:
            raise PreflightError("ACON summary exceeded the configured limit")
        return output


def run_adapter(args: argparse.Namespace) -> int:
    report = preflight(args)
    if not report["ready"]:
        sys.stderr.write(json.dumps(report, sort_keys=True) + "\n")
        return 2
    corpus = _strict_object(args.corpus)
    cases = corpus.get("cases")
    if not isinstance(cases, list) or len(cases) != 1:
        raise PreflightError("per-case adapter requires exactly one corpus case")
    case = cases[0]
    if not isinstance(case, dict):
        raise PreflightError("corpus case must be an object")
    events = case.get("source_events")
    if not isinstance(events, list):
        raise PreflightError("corpus case source_events must be an array")
    history: list[tuple[dict[str, Any], str]] = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("content"), str):
            raise PreflightError("source events must be objects with string content")
        history.append(
            (
                {
                    "source_id": event.get("id"),
                    "sequence": event.get("sequence"),
                    "role": event.get("role"),
                },
                event["content"],
            )
        )

    source_root = args.acon_source_root
    assert source_root is not None
    sys.path.insert(0, str(source_root / "src"))
    from productive_agents.ctxopt.history_optimizer import HistoryOptimizer

    prompt_dir = source_root / "experiments/appworld/prompts/context_opt"
    backend = _AconQwenBackend(args.lms_executable)
    optimizer = HistoryOptimizer(
        {
            "model": "qwen/qwen3.6-35b-a3b@q4_k_m",
            "temperature": 0.0,
            "history_prompt_dir": str(prompt_dir),
            "prompts": {
                "prompt_system": "system_prompt",
                "prompt_history_user": "prompt_history_v2",
            },
            "history_summarization_threshold": -1,
        },
        debug_mode=False,
        llm=backend,
    )
    backend.system_message = optimizer.system_message
    rendered = optimizer.process(
        task="Compress the ordered source-event history for continuation.",
        history=history,
    )
    if not isinstance(rendered, str) or not rendered:
        raise PreflightError("ACON returned an empty summary")
    payload = {
        "schema": "lrcbench-candidate-output-0.1",
        "dataset_sha256": corpus.get("dataset_sha256"),
        "system": args.system,
        "cases": [
            {
                "case_id": case.get("case_id"),
                "rendered_text": rendered,
                "claims": [],
            }
        ],
    }
    with args.candidate.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for name in ("preflight", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("--acon-source-root", type=Path)
        command.add_argument("--dependency-lock", type=Path)
        command.add_argument("--network-isolation-evidence", type=Path)
        command.add_argument("--inference-service-pid", type=int)
        command.add_argument("--max-inference-service-memory-mb", type=int)
        command.add_argument("--lms-executable", type=Path)
        if name == "run":
            command.add_argument("--corpus", type=Path, required=True)
            command.add_argument("--candidate", type=Path, required=True)
            command.add_argument("--system", required=True)
            command.add_argument("--case-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode == "preflight":
        report = preflight(args, check_model=False)
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return 0 if report["ready"] else 2
    try:
        return run_adapter(args)
    except (OSError, PreflightError, TypeError, ValueError) as exc:
        sys.stderr.write(f"acon diagnostic adapter: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
