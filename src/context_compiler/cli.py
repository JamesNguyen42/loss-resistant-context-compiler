"""Command-line interface for the context compiler."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .archive import SourceArchive
from .atomic import atomic_write_text
from .compiler import ContextCompiler
from .io import (
    load_artifact_path,
    load_sources,
    load_sources_path,
    verify_artifact_dict,
)
from .isolation import CompilationIsolationError
from .limits import (
    DEFAULT_ARTIFACT_LIMITS,
    DEFAULT_SOURCE_LIMITS,
    ArtifactLimitError,
    ArtifactLimits,
    SourceLimitError,
    SourceLimits,
)
from .models import CompilationPolicy

_DIAGNOSTIC_SCHEMA = "ctxc-diagnostic-0.1"


def _source_limits(args: argparse.Namespace) -> SourceLimits:
    return SourceLimits(
        max_input_bytes=args.max_source_bytes,
        max_records=args.max_source_records,
        max_line_chars=args.max_source_line_chars,
        max_record_bytes=args.max_source_record_bytes,
        max_total_record_bytes=args.max_total_source_bytes,
        max_json_depth=args.max_source_json_depth,
    )


def _artifact_limits(args: argparse.Namespace) -> ArtifactLimits:
    return ArtifactLimits(
        max_input_bytes=args.max_artifact_bytes,
        max_line_chars=args.max_artifact_line_chars,
        max_canonical_bytes=args.max_artifact_canonical_bytes,
        max_json_depth=args.max_artifact_json_depth,
        max_items=args.max_artifact_items,
        max_selected_items=args.max_artifact_selected_items,
        max_provenance_spans=args.max_artifact_provenance_spans,
        max_verification_issues=args.max_artifact_verification_issues,
    )


def _input_sources(path: str, limits: SourceLimits) -> list:
    if path == "-":
        return load_sources(sys.stdin, limits=limits)
    return load_sources_path(path, limits=limits)


def _write_output(value: str, path: str | None) -> None:
    rendered = value + ("" if value.endswith("\n") else "\n")
    if path:
        atomic_write_text(Path(path), rendered)
    else:
        sys.stdout.write(rendered)


def _command_name(args: argparse.Namespace) -> str:
    if getattr(args, "command", None) == "archive":
        return f"archive {getattr(args, 'archive_command', '')}".strip()
    return str(getattr(args, "command", "unknown"))


def _error_identity(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, (ArtifactLimitError, SourceLimitError)):
        return "resource_limit", "resource_limit_exceeded"
    if isinstance(exc, TimeoutError):
        return "timeout", "operation_timed_out"
    if isinstance(exc, CompilationIsolationError):
        return "runtime", "compilation_isolation_failed"
    if isinstance(exc, FileNotFoundError):
        return "io", "path_not_found"
    if isinstance(exc, PermissionError):
        return "io", "permission_denied"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_input", "invalid_json"
    if isinstance(exc, UnicodeError):
        return "invalid_input", "invalid_encoding"
    if isinstance(exc, OSError):
        return "io", "io_error"
    if isinstance(exc, TypeError):
        return "invalid_input", "invalid_type"
    message = str(exc).casefold()
    if any(
        marker in message
        for marker in (
            "duplicate json object key",
            "invalid json",
            "json number must be finite",
            "non-standard json constant",
        )
    ):
        return "invalid_input", "invalid_json"
    if any(marker in message for marker in ("hash mismatch", "digest mismatch")):
        return "integrity", "integrity_check_failed"
    if any(
        marker in message
        for marker in ("token budget", "compression ratio", "minimum_compression_ratio")
    ):
        return "policy", "policy_rejected"
    return "invalid_input", "invalid_value"


def _write_error(
    args: argparse.Namespace,
    exc: BaseException,
    *,
    exit_code: int = 2,
    category: str | None = None,
    code: str | None = None,
) -> None:
    if getattr(args, "error_format", "text") == "json":
        inferred_category, inferred_code = _error_identity(exc)
        diagnostic = {
            "schema": _DIAGNOSTIC_SCHEMA,
            "command": _command_name(args),
            "category": category or inferred_category,
            "code": code or inferred_code,
            "exit_code": exit_code,
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }
        sys.stderr.write(
            json.dumps(
                diagnostic,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        return
    sys.stderr.write(f"ctxc: {exc}\n")


def _compile(args: argparse.Namespace) -> int:
    try:
        source_limits = _source_limits(args)
        sources = _input_sources(args.input, source_limits)
        if args.archive:
            archive = SourceArchive(args.archive, source_limits=source_limits)
            archive.append(sources)
            sources = archive.load()
        policy = CompilationPolicy(
            token_budget=args.token_budget,
            minimum_compression_ratio=args.minimum_compression,
            fail_on_budget_overflow=args.strict_budget,
            include_superseded=args.include_superseded,
            recover_missed_protected=not args.no_recovery,
        )
        result = ContextCompiler(
            policy=policy,
            source_limits=source_limits,
        ).compile(
            sources,
            timeout_seconds=args.compile_timeout_seconds,
        )
    except (
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        TimeoutError,
    ) as exc:
        _write_error(args, exc)
        return 2
    if args.format == "prompt" and not result.verification.passed:
        _write_error(
            args,
            ValueError("refusing to render prompt from unverified memory"),
            exit_code=3,
            category="verification",
            code="unverified_prompt_refused",
        )
        return 3
    try:
        if args.format == "prompt":
            rendered = result.to_prompt()
        else:
            rendered = result.to_json(include_all_items=not args.active_only)
        _write_output(rendered, args.output)
    except (OSError, TypeError, ValueError) as exc:
        _write_error(args, exc)
        return 2
    if not result.verification.passed:
        return 3
    if args.require_target and not result.compression.target_met:
        return 4
    return 0


def _archive_append(args: argparse.Namespace) -> int:
    try:
        source_limits = _source_limits(args)
        sources = _input_sources(args.input, source_limits)
        archive = SourceArchive(args.archive, source_limits=source_limits)
        appended = archive.append(sources)
        report = archive.verify()
    except (OSError, TypeError, ValueError, json.JSONDecodeError, TimeoutError) as exc:
        _write_error(args, exc)
        return 2
    value = {
        "passed": report.passed,
        "appended": appended,
        "records": report.records,
        "source_digest": report.digest,
        "issues": list(report.issues),
    }
    _write_output(json.dumps(value, indent=2), args.output)
    return 0 if report.passed else 3


def _archive_verify(args: argparse.Namespace) -> int:
    try:
        report = SourceArchive(
            args.archive,
            source_limits=_source_limits(args),
        ).verify()
    except OSError as exc:
        _write_error(args, exc)
        return 2
    value = {
        "passed": report.passed,
        "records": report.records,
        "source_digest": report.digest,
        "issues": list(report.issues),
    }
    _write_output(json.dumps(value, indent=2), args.output)
    return 0 if report.passed else 3


def _verify(args: argparse.Namespace) -> int:
    try:
        source_limits = _source_limits(args)
        artifact_limits = _artifact_limits(args)
        artifact = load_artifact_path(args.artifact, limits=artifact_limits)
        sources = _input_sources(args.sources, source_limits)
        report = verify_artifact_dict(
            artifact,
            sources,
            source_limits=source_limits,
            artifact_limits=artifact_limits,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        _write_error(args, exc)
        return 2
    _write_output(json.dumps(report, indent=2, ensure_ascii=False), args.output)
    return 0 if report["passed"] else 3


def _inspect(args: argparse.Namespace) -> int:
    try:
        artifact_limits = _artifact_limits(args)
        artifact = load_artifact_path(args.artifact, limits=artifact_limits)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        _write_error(args, exc)
        return 2
    if not isinstance(artifact, dict):
        raise TypeError("compiled artifact must be a JSON object")
    items = artifact.get("items")
    selected = artifact.get("selected_item_ids")
    if not isinstance(items, list) or not isinstance(selected, list):
        raise TypeError("compiled artifact items and selected_item_ids must be arrays")
    compiler_metadata = artifact.get("compiler_metadata")
    metrics = (
        compiler_metadata.get("metrics", {})
        if isinstance(compiler_metadata, dict)
        else {}
    )
    summary = {
        "schema_version": artifact.get("schema_version"),
        "source_count": artifact.get("source_count"),
        "total_items": len(items),
        "selected_items": len(selected),
        "verification": artifact.get("verification", {}),
        "compression": artifact.get("compression", {}),
        "metrics": metrics,
    }
    _write_output(json.dumps(summary, indent=2, ensure_ascii=False), args.output)
    return 0


def _add_source_limit_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-source-bytes",
        type=int,
        default=DEFAULT_SOURCE_LIMITS.max_input_bytes,
        help="maximum UTF-8 bytes read from a source input or archive",
    )
    parser.add_argument(
        "--max-source-records",
        type=int,
        default=DEFAULT_SOURCE_LIMITS.max_records,
        help="maximum source records",
    )
    parser.add_argument(
        "--max-source-line-chars",
        type=int,
        default=DEFAULT_SOURCE_LIMITS.max_line_chars,
        help="maximum characters in one physical JSON/JSONL line",
    )
    parser.add_argument(
        "--max-source-record-bytes",
        type=int,
        default=DEFAULT_SOURCE_LIMITS.max_record_bytes,
        help="maximum canonical UTF-8 JSON bytes in one source record",
    )
    parser.add_argument(
        "--max-total-source-bytes",
        type=int,
        default=DEFAULT_SOURCE_LIMITS.max_total_record_bytes,
        help="maximum canonical UTF-8 JSON bytes across source records",
    )
    parser.add_argument(
        "--max-source-json-depth",
        type=int,
        default=DEFAULT_SOURCE_LIMITS.max_json_depth,
        help="maximum JSON container nesting depth in source records",
    )


def _add_artifact_limit_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-artifact-bytes",
        type=int,
        default=DEFAULT_ARTIFACT_LIMITS.max_input_bytes,
        help="maximum UTF-8 bytes read from a compiled artifact",
    )
    parser.add_argument(
        "--max-artifact-line-chars",
        type=int,
        default=DEFAULT_ARTIFACT_LIMITS.max_line_chars,
        help="maximum characters in one physical artifact JSON line",
    )
    parser.add_argument(
        "--max-artifact-canonical-bytes",
        type=int,
        default=DEFAULT_ARTIFACT_LIMITS.max_canonical_bytes,
        help="maximum compact UTF-8 JSON bytes in a decoded artifact",
    )
    parser.add_argument(
        "--max-artifact-json-depth",
        type=int,
        default=DEFAULT_ARTIFACT_LIMITS.max_json_depth,
        help="maximum JSON container nesting depth in a compiled artifact",
    )
    parser.add_argument(
        "--max-artifact-items",
        type=int,
        default=DEFAULT_ARTIFACT_LIMITS.max_items,
        help="maximum memory items in a compiled artifact",
    )
    parser.add_argument(
        "--max-artifact-selected-items",
        type=int,
        default=DEFAULT_ARTIFACT_LIMITS.max_selected_items,
        help="maximum selected item ids in a compiled artifact",
    )
    parser.add_argument(
        "--max-artifact-provenance-spans",
        type=int,
        default=DEFAULT_ARTIFACT_LIMITS.max_provenance_spans,
        help="maximum provenance spans across a compiled artifact",
    )
    parser.add_argument(
        "--max-artifact-verification-issues",
        type=int,
        default=DEFAULT_ARTIFACT_LIMITS.max_verification_issues,
        help="maximum embedded verification issues in a compiled artifact",
    )


def _add_error_format_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--error-format",
        choices=("text", "json"),
        default="text",
        help="render runtime errors as human-readable text or versioned JSON",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctxc",
        description="Compile verbose agent histories into typed, provenance-linked memory.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser("compile", help="compile JSON or JSONL history")
    compile_parser.add_argument("input", help="history path or - for stdin")
    compile_parser.add_argument("-o", "--output")
    compile_parser.add_argument("--format", choices=("json", "prompt"), default="json")
    compile_parser.add_argument("--token-budget", type=int, default=4_000)
    compile_parser.add_argument("--minimum-compression", type=float, default=5.0)
    compile_parser.add_argument(
        "--compile-timeout-seconds",
        type=float,
        help=(
            "run compilation in an isolated process tree and terminate it "
            "after this whole-run deadline"
        ),
    )
    compile_parser.add_argument("--strict-budget", action="store_true")
    compile_parser.add_argument("--require-target", action="store_true")
    compile_parser.add_argument("--include-superseded", action="store_true")
    compile_parser.add_argument("--active-only", action="store_true")
    compile_parser.add_argument("--no-recovery", action="store_true")
    compile_parser.add_argument(
        "--archive",
        help="append input to this immutable source archive, then compile the full archive",
    )
    _add_source_limit_arguments(compile_parser)
    _add_error_format_argument(compile_parser)
    compile_parser.set_defaults(handler=_compile)

    verify_parser = subparsers.add_parser(
        "verify", help="re-verify an artifact against immutable source history"
    )
    verify_parser.add_argument("artifact")
    verify_parser.add_argument("sources")
    verify_parser.add_argument("-o", "--output")
    _add_source_limit_arguments(verify_parser)
    _add_artifact_limit_arguments(verify_parser)
    _add_error_format_argument(verify_parser)
    verify_parser.set_defaults(handler=_verify)

    inspect_parser = subparsers.add_parser("inspect", help="show artifact health and compression")
    inspect_parser.add_argument("artifact")
    inspect_parser.add_argument("-o", "--output")
    _add_artifact_limit_arguments(inspect_parser)
    _add_error_format_argument(inspect_parser)
    inspect_parser.set_defaults(handler=_inspect)

    archive_parser = subparsers.add_parser("archive", help="manage immutable cold source events")
    archive_subparsers = archive_parser.add_subparsers(dest="archive_command", required=True)
    archive_append = archive_subparsers.add_parser("append", help="append JSON/JSONL sources")
    archive_append.add_argument("archive")
    archive_append.add_argument("input", help="history path or - for stdin")
    archive_append.add_argument("-o", "--output")
    _add_source_limit_arguments(archive_append)
    _add_error_format_argument(archive_append)
    archive_append.set_defaults(handler=_archive_append)
    archive_verify = archive_subparsers.add_parser("verify", help="verify archive hashes and shape")
    archive_verify.add_argument("archive")
    archive_verify.add_argument("-o", "--output")
    _add_source_limit_arguments(archive_verify)
    _add_error_format_argument(archive_verify)
    archive_verify.set_defaults(handler=_archive_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, TypeError, ValueError, TimeoutError) as exc:
        _write_error(args, exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
