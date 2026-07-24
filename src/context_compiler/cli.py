"""Command-line interface for the context compiler."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .archive import SourceArchive
from .compiler import ContextCompiler
from .io import load_sources, load_sources_path, verify_artifact_dict
from .limits import DEFAULT_SOURCE_LIMITS, SourceLimits
from .models import CompilationPolicy


def _source_limits(args: argparse.Namespace) -> SourceLimits:
    return SourceLimits(
        max_input_bytes=args.max_source_bytes,
        max_records=args.max_source_records,
        max_line_chars=args.max_source_line_chars,
        max_record_bytes=args.max_source_record_bytes,
        max_total_record_bytes=args.max_total_source_bytes,
        max_json_depth=args.max_source_json_depth,
    )


def _input_sources(path: str, limits: SourceLimits) -> list:
    if path == "-":
        return load_sources(sys.stdin, limits=limits)
    return load_sources_path(path, limits=limits)


def _write_output(value: str, path: str | None) -> None:
    if path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            value + ("" if value.endswith("\n") else "\n"),
            encoding="utf-8",
        )
    else:
        sys.stdout.write(value + ("" if value.endswith("\n") else "\n"))


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
        ).compile(sources)
    except (OSError, TypeError, ValueError, json.JSONDecodeError, TimeoutError) as exc:
        sys.stderr.write(f"ctxc: {exc}\n")
        return 2
    if args.format == "prompt" and not result.verification.passed:
        sys.stderr.write("ctxc: refusing to render prompt from unverified memory\n")
        return 3
    try:
        if args.format == "prompt":
            rendered = result.to_prompt()
        else:
            rendered = result.to_json(include_all_items=not args.active_only)
        _write_output(rendered, args.output)
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write(f"ctxc: {exc}\n")
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
        sys.stderr.write(f"ctxc: {exc}\n")
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
        sys.stderr.write(f"ctxc: {exc}\n")
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
        artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
        sources = _input_sources(args.sources, source_limits)
        report = verify_artifact_dict(
            artifact,
            sources,
            source_limits=source_limits,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"ctxc: {exc}\n")
        return 2
    _write_output(json.dumps(report, indent=2, ensure_ascii=False), args.output)
    return 0 if report["passed"] else 3


def _inspect(args: argparse.Namespace) -> int:
    try:
        artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"ctxc: {exc}\n")
        return 2
    if not isinstance(artifact, dict):
        raise TypeError("compiled artifact must be a JSON object")
    items = artifact.get("items")
    selected = artifact.get("selected_item_ids")
    if not isinstance(items, list) or not isinstance(selected, list):
        raise TypeError("compiled artifact items and selected_item_ids must be arrays")
    summary = {
        "schema_version": artifact.get("schema_version"),
        "source_count": artifact.get("source_count"),
        "total_items": len(items),
        "selected_items": len(selected),
        "verification": artifact.get("verification", {}),
        "compression": artifact.get("compression", {}),
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
    compile_parser.set_defaults(handler=_compile)

    verify_parser = subparsers.add_parser(
        "verify", help="re-verify an artifact against immutable source history"
    )
    verify_parser.add_argument("artifact")
    verify_parser.add_argument("sources")
    verify_parser.add_argument("-o", "--output")
    _add_source_limit_arguments(verify_parser)
    verify_parser.set_defaults(handler=_verify)

    inspect_parser = subparsers.add_parser("inspect", help="show artifact health and compression")
    inspect_parser.add_argument("artifact")
    inspect_parser.add_argument("-o", "--output")
    inspect_parser.set_defaults(handler=_inspect)

    archive_parser = subparsers.add_parser("archive", help="manage immutable cold source events")
    archive_subparsers = archive_parser.add_subparsers(dest="archive_command", required=True)
    archive_append = archive_subparsers.add_parser("append", help="append JSON/JSONL sources")
    archive_append.add_argument("archive")
    archive_append.add_argument("input", help="history path or - for stdin")
    archive_append.add_argument("-o", "--output")
    _add_source_limit_arguments(archive_append)
    archive_append.set_defaults(handler=_archive_append)
    archive_verify = archive_subparsers.add_parser("verify", help="verify archive hashes and shape")
    archive_verify.add_argument("archive")
    archive_verify.add_argument("-o", "--output")
    _add_source_limit_arguments(archive_verify)
    archive_verify.set_defaults(handler=_archive_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, TypeError, ValueError, TimeoutError) as exc:
        sys.stderr.write(f"ctxc: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
