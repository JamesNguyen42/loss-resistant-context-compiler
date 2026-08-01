"""Command-line interface for the context compiler."""

from __future__ import annotations

import argparse
import codecs
import io
import json
import os
import sys
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from .archive import SourceArchive
from .artifact_diff import diff_artifacts
from .artifact_inspection import render_artifact_text, summarize_artifact
from .atomic import AtomicDestinationExistsError, atomic_write_text
from .compiler import ContextCompiler
from .connector import ExactTokenCounterAdapter
from .context_window import ContextWindowBudget, ContextWindowError
from .io import (
    load_artifact_path,
    load_sources,
    load_sources_path,
    verify_artifact_dict,
)
from .isolation import CompilationIsolationError
from .limits import (
    DEFAULT_ARTIFACT_LIMITS,
    DEFAULT_COMPILATION_LIMITS,
    DEFAULT_SOURCE_LIMITS,
    ArtifactLimitError,
    ArtifactLimits,
    CompilationLimitError,
    CompilationLimits,
    SourceLimitError,
    SourceLimits,
)
from .materialized_degradation_evaluation import (
    evaluate_materialization_degradation,
    load_materialization_degradation_report,
)
from .materialized_evaluation import (
    evaluate_materialization_retention,
    load_materialization_retention_report,
)
from .materialized_window import materialize_context
from .models import CompilationPolicy, CompiledMemory
from .path_safety import PathBoundaryError
from .redaction import (
    SECRET_DETECTOR_NAMES,
    RedactionLimitError,
    RedactionPolicy,
    redact_sources,
)
from .schema_compatibility import artifact_schema_registry, artifact_schema_support
from .trust import (
    create_trust_manifest,
    load_trust_manifest_path,
    verify_trust_manifest,
)

_DIAGNOSTIC_SCHEMA = "ctxc-diagnostic-0.1"
_DETAIL_DIAGNOSTIC_SCHEMA = "ctxc-diagnostic-0.2"
_EVENT_SCHEMA = "ctxc-event-0.1"
_MATERIALIZE_TOKENIZER_PROFILE = "unicode-codepoint-count-v1"
_CLI_MASK_CHARACTERS = frozenset({"*", "#", "█", "■"})


class _StderrEmissionError(RuntimeError):
    """Raised after one failed binary diagnostic/event emission attempt."""


class _Utf8BinaryTextWriter:
    """Expose a strict text-writer surface over an unowned binary stream."""

    def __init__(self, output: object) -> None:
        self._output = output

    def write(self, value: str) -> int:
        if type(value) is not str:
            raise TypeError("connector output must be an exact string")
        payload = value.encode("utf-8", errors="strict")
        written = self._output.write(payload)
        if type(written) is not int or written != len(payload):
            raise OSError("standard output did not accept the complete connector record")
        return len(value)

    def flush(self) -> None:
        self._output.flush()


def _mask_character(value: str) -> str:
    if value not in _CLI_MASK_CHARACTERS:
        raise argparse.ArgumentTypeError(
            "mask must be '*', '#', U+2588, or U+25A0"
        )
    return value


def _source_limits(args: argparse.Namespace) -> SourceLimits:
    return SourceLimits(
        max_input_bytes=args.max_source_bytes,
        max_records=args.max_source_records,
        max_line_chars=args.max_source_line_chars,
        max_record_bytes=args.max_source_record_bytes,
        max_total_record_bytes=args.max_total_source_bytes,
        max_json_depth=args.max_source_json_depth,
    )


def _compilation_limits(args: argparse.Namespace) -> CompilationLimits:
    return CompilationLimits(
        max_extractor_items=args.max_extractor_items,
        max_extractor_rejections=args.max_extractor_rejections,
        max_extractor_bytes=args.max_extractor_bytes,
        max_extractor_auxiliary_bytes=(
            args.max_extractor_auxiliary_bytes
        ),
        max_total_candidate_items=args.max_total_candidate_items,
        max_resolved_items=args.max_resolved_items,
        max_provenance_spans=args.max_compilation_provenance_spans,
        max_item_work=args.max_compilation_item_work,
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
        binary_input = getattr(sys.stdin, "buffer", None)
        if binary_input is None:
            return load_sources(sys.stdin, limits=limits)
        try:
            return load_sources(
                codecs.getreader("utf-8")(binary_input, errors="strict"),
                limits=limits,
            )
        except UnicodeDecodeError as exc:
            raise UnicodeError("source input must be valid UTF-8 text") from exc
    return load_sources_path(path, limits=limits)


def _write_output(value: str, path: str | None) -> None:
    rendered = value + ("" if value.endswith("\n") else "\n")
    if path:
        atomic_write_text(Path(path), rendered)
    else:
        sys.stdout.write(rendered)


def _write_binary_stdout(rendered: str) -> None:
    payload = rendered.encode("utf-8", errors="strict")
    output = getattr(sys.stdout, "buffer", None)
    if output is None:
        raise RuntimeError("standard output does not expose a binary buffer")
    written = output.write(payload)
    if type(written) is not int or written != len(payload):
        raise OSError("standard output did not accept the complete canonical report")
    output.flush()


def _write_binary_stderr(rendered: str) -> None:
    payload = rendered.encode("utf-8", errors="strict")
    output = getattr(sys.stderr, "buffer", None)
    if output is None:
        raise _StderrEmissionError("standard error does not expose a binary buffer")
    try:
        written = output.write(payload)
    except (OSError, TypeError, ValueError) as exc:
        raise _StderrEmissionError("standard error write failed") from exc
    if type(written) is not int or written != len(payload):
        raise _StderrEmissionError("standard error did not accept the complete diagnostic")
    try:
        output.flush()
    except (OSError, TypeError, ValueError) as exc:
        raise _StderrEmissionError("standard error flush failed") from exc


def _write_exact_utf8_output(value: str, path: str | None) -> None:
    """Write canonical UTF-8 bytes to stdout while preserving atomic file output."""

    rendered = value + ("" if value.endswith("\n") else "\n")
    if path:
        atomic_write_text(Path(path), rendered)
    elif type(sys.stdout) is io.StringIO:
        written = sys.stdout.write(rendered)
        if type(written) is not int or written != len(rendered):
            raise OSError("in-memory standard output did not accept the complete report")
        sys.stdout.flush()
    else:
        _write_binary_stdout(rendered)


def _write_new_output(value: str, path: str | None) -> None:
    rendered = value + ("" if value.endswith("\n") else "\n")
    if path:
        try:
            atomic_write_text(Path(path), rendered, overwrite=False)
        except AtomicDestinationExistsError as exc:
            raise FileExistsError("output already exists") from exc
    else:
        _write_binary_stdout(rendered)


def _command_name(args: argparse.Namespace) -> str:
    command = getattr(args, "command", None)
    if command in {"archive", "trust"}:
        nested = getattr(args, f"{command}_command", "")
        return f"{command} {nested}".strip()
    return str(getattr(args, "command", "unknown"))


def _error_identity(exc: BaseException) -> tuple[str, str]:
    if isinstance(
        exc,
        (
            ArtifactLimitError,
            CompilationLimitError,
            RedactionLimitError,
            SourceLimitError,
        ),
    ):
        return "resource_limit", "resource_limit_exceeded"
    if isinstance(exc, TimeoutError):
        return "timeout", "operation_timed_out"
    if isinstance(exc, CompilationIsolationError):
        return "runtime", "compilation_isolation_failed"
    if isinstance(exc, FileNotFoundError):
        return "io", "path_not_found"
    if isinstance(exc, PermissionError):
        return "io", "permission_denied"
    if isinstance(exc, PathBoundaryError):
        return "io", "unsafe_path_boundary"
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
    if any(
        marker in message
        for marker in ("hash mismatch", "digest mismatch", "chain head mismatch")
    ):
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
        details = exc.diagnostic if type(exc) is ContextWindowError else None
        diagnostic = {
            "schema": (
                _DETAIL_DIAGNOSTIC_SCHEMA if details is not None else _DIAGNOSTIC_SCHEMA
            ),
            "command": _command_name(args),
            "category": category or inferred_category,
            "code": code or inferred_code,
            "exit_code": exit_code,
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }
        if details is not None:
            diagnostic["details"] = details
        _write_binary_stderr(
            json.dumps(
                diagnostic,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        return
    _write_binary_stderr(f"ctxc: {exc}\n")


def _compile_event(
    result: CompiledMemory,
    artifact: dict,
    *,
    exit_code: int,
    output_format: str,
) -> dict:
    metadata = result.compiler_metadata
    primary_rejections = metadata.get("primary_rejections")
    additive_rejections = metadata.get("additive_safety_rejections")
    verification_report = result.verification.to_dict()
    verification_issues = verification_report["issues"]
    verification = {
        key: value
        for key, value in verification_report.items()
        if key != "issues"
    }
    verification["issue_count"] = len(verification_issues)
    verification["error_count"] = sum(
        issue["severity"] == "error" for issue in verification_issues
    )
    verification["warning_count"] = sum(
        issue["severity"] == "warning" for issue in verification_issues
    )
    verification["info_count"] = sum(
        issue["severity"] == "info" for issue in verification_issues
    )
    verification["issue_codes"] = sorted(
        {issue["code"] for issue in verification_issues}
    )
    outcomes = {
        0: "accepted",
        3: "verification_failed",
        4: "compression_target_not_met",
    }
    return {
        "schema": _EVENT_SCHEMA,
        "command": "compile",
        "event": "compilation_completed",
        "outcome": outcomes[exit_code],
        "exit_code": exit_code,
        "output_format": output_format,
        "compiled_at": result.compiled_at,
        "source_count": result.source_count,
        "source_digest": result.source_digest,
        "artifact_sha256": artifact["artifact_sha256"],
        "ledger_complete": artifact["ledger_complete"],
        "extraction": {
            "primary_extractor": metadata.get("extractor"),
            "primary_rejections": (
                len(primary_rejections)
                if isinstance(primary_rejections, list)
                else 0
            ),
            "additive_safety_rejections": (
                len(additive_rejections)
                if isinstance(additive_rejections, list)
                else 0
            ),
            "recovered_items": metadata.get("recovered_items", 0),
            "primary_failure": metadata.get("primary_failure"),
            "primary_degradation": metadata.get("primary_degradation"),
            "additive_safety_failure": metadata.get(
                "additive_safety_failure"
            ),
        },
        "verification": verification,
        "compression": result.compression.to_dict(),
        "metrics": metadata.get("metrics", {}),
    }


def _emit_compile_event(
    args: argparse.Namespace,
    result: CompiledMemory,
    artifact: dict | None,
    *,
    exit_code: int,
) -> None:
    if args.event_format != "jsonl":
        return
    if artifact is None:
        raise ValueError("compile event requires an artifact envelope")
    event = _compile_event(
        result,
        artifact,
        exit_code=exit_code,
        output_format=args.format,
    )
    _write_binary_stderr(
        json.dumps(
            event,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def _compile(args: argparse.Namespace) -> int:
    try:
        source_limits = _source_limits(args)
        if args.archive_expected_chain_head is not None and not args.archive:
            raise ValueError("--archive-expected-chain-head requires --archive")
        sources = _input_sources(args.input, source_limits)
        if args.archive:
            archive = SourceArchive(args.archive, source_limits=source_limits)
            archive.append(
                sources,
                expected_chain_head=args.archive_expected_chain_head,
            )
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
            compilation_limits=_compilation_limits(args),
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
    exit_code = (
        3
        if not result.verification.passed
        else (
            4
            if args.require_target and not result.compression.target_met
            else 0
        )
    )
    artifact: dict | None = None
    try:
        if args.format == "json" or args.event_format == "jsonl":
            artifact = result.to_dict(
                include_all_items=(
                    not args.active_only if args.format == "json" else True
                )
            )
        if args.format == "prompt" and not result.verification.passed:
            _emit_compile_event(
                args,
                result,
                artifact,
                exit_code=exit_code,
            )
            _write_error(
                args,
                ValueError("refusing to render prompt from unverified memory"),
                exit_code=3,
                category="verification",
                code="unverified_prompt_refused",
            )
            return 3
        if args.format == "prompt":
            rendered = result.to_prompt()
        else:
            if artifact is None:
                raise ValueError("JSON output requires an artifact envelope")
            rendered = json.dumps(
                artifact,
                indent=2,
                ensure_ascii=False,
            )
        _write_exact_utf8_output(rendered, args.output)
        _emit_compile_event(
            args,
            result,
            artifact,
            exit_code=exit_code,
        )
    except _StderrEmissionError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        _write_error(args, exc)
        return 2
    return exit_code


def _materialize(args: argparse.Namespace) -> int:
    try:
        source_limits = _source_limits(args)
        sources = _input_sources(args.input, source_limits)
        budget = ContextWindowBudget(
            hard_limit_tokens=args.hard_limit_tokens,
            memory_budget_tokens=args.memory_budget_tokens,
            reserved_output_tokens=args.reserved_output_tokens,
            safety_margin_tokens=args.safety_margin_tokens,
            fixed_input_tokens=args.fixed_input_tokens,
            minimum_recent_messages=args.minimum_recent_messages,
            maximum_recent_messages=args.maximum_recent_messages,
            per_message_overhead_tokens=args.per_message_overhead_tokens,
        )
        result = materialize_context(
            sources,
            current_turn_id=args.current_turn_id,
            budget=budget,
            token_counter=ExactTokenCounterAdapter(
                args.tokenizer_profile,
                len,
            ),
            allocation_plan_sha256=args.allocation_plan_sha256,
            fixed_input_sha256=args.fixed_input_sha256,
            source_limits=source_limits,
            compilation_limits=_compilation_limits(args),
        )
        rendered = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        _write_exact_utf8_output(rendered, args.output)
    except (
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        TimeoutError,
    ) as exc:
        code = exc.reason if isinstance(exc, ContextWindowError) else None
        _write_error(args, exc, code=code)
        return 2
    return 0


def _evaluate_materialization(args: argparse.Namespace) -> int:
    try:
        if args.verify_report is None:
            if args.expected_report_sha256 is not None:
                raise ValueError(
                    "--expected-report-sha256 requires --verify-report"
                )
            report = evaluate_materialization_retention(
                selected_split=args.split or "heldout",
            )
        else:
            if args.split is not None:
                raise ValueError("--split cannot be combined with --verify-report")
            if args.expected_report_sha256 is None:
                raise ValueError(
                    "--verify-report requires --expected-report-sha256"
                )
            report = load_materialization_retention_report(
                args.verify_report,
                expected_report_sha256=args.expected_report_sha256,
            )
        rendered = json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        _write_new_output(rendered, args.output)
        return 0 if report["integrity_passed"] is True else 3
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
    return 0


def _evaluate_materialization_degradation(args: argparse.Namespace) -> int:
    try:
        if args.verify_report is None:
            if args.expected_report_sha256 is not None:
                raise ValueError("--expected-report-sha256 requires --verify-report")
            report = evaluate_materialization_degradation()
        else:
            if args.expected_report_sha256 is None:
                raise ValueError("--verify-report requires --expected-report-sha256")
            report = load_materialization_degradation_report(
                args.verify_report,
                expected_report_sha256=args.expected_report_sha256,
            )
        rendered = json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        _write_new_output(rendered, args.output)
        return 0 if report["integrity_passed"] is True else 3
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
    return 0


def _archive_append(args: argparse.Namespace) -> int:
    try:
        source_limits = _source_limits(args)
        sources = _input_sources(args.input, source_limits)
        archive = SourceArchive(args.archive, source_limits=source_limits)
        appended = archive.append(
            sources,
            expected_chain_head=args.expected_chain_head,
        )
        report = archive.verify()
    except (OSError, TypeError, ValueError, json.JSONDecodeError, TimeoutError) as exc:
        _write_error(args, exc)
        return 2
    value = {
        "schema": report.schema,
        "passed": report.passed,
        "appended": appended,
        "records": report.records,
        "source_digest": report.digest,
        "archive_schema": report.archive_schema,
        "chain_head_sha256": report.chain_head_sha256,
        "issues": list(report.issues),
    }
    _write_output(json.dumps(value, indent=2), args.output)
    return 0 if report.passed else 3


def _archive_verify(args: argparse.Namespace) -> int:
    try:
        report = SourceArchive(
            args.archive,
            source_limits=_source_limits(args),
        ).verify(expected_chain_head=args.expected_chain_head)
    except (OSError, TypeError, ValueError) as exc:
        _write_error(args, exc)
        return 2
    value = {
        "schema": report.schema,
        "passed": report.passed,
        "records": report.records,
        "source_digest": report.digest,
        "archive_schema": report.archive_schema,
        "chain_head_sha256": report.chain_head_sha256,
        "issues": list(report.issues),
    }
    _write_output(json.dumps(value, indent=2), args.output)
    return 0 if report.passed else 3


def _verify(args: argparse.Namespace) -> int:
    try:
        if (args.sources is None) == (args.source_archive is None):
            raise ValueError("verify requires exactly one of SOURCES or --archive")
        if (
            args.archive_expected_chain_head is not None
            and args.source_archive is None
        ):
            raise ValueError("--archive-expected-chain-head requires --archive")
        source_limits = _source_limits(args)
        artifact_limits = _artifact_limits(args)
        artifact = load_artifact_path(args.artifact, limits=artifact_limits)
        sources = (
            SourceArchive(
                args.source_archive,
                source_limits=source_limits,
            ).load(expected_chain_head=args.archive_expected_chain_head)
            if args.source_archive is not None
            else _input_sources(args.sources, source_limits)
        )
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


def _trust_sources(
    args: argparse.Namespace,
    source_limits: SourceLimits,
) -> tuple[list, str | None]:
    if (args.sources is None) == (args.source_archive is None):
        raise ValueError(
            "trust commands require exactly one of SOURCES or --archive"
        )
    if (
        args.archive_expected_chain_head is not None
        and args.source_archive is None
    ):
        raise ValueError("--archive-expected-chain-head requires --archive")
    if args.source_archive is None:
        return _input_sources(args.sources, source_limits), None

    archive = SourceArchive(
        args.source_archive,
        source_limits=source_limits,
    )
    report = archive.verify(
        expected_chain_head=args.archive_expected_chain_head,
    )
    if not report.passed:
        detail = "; ".join(report.issues) or "unknown archive failure"
        raise ValueError(f"source archive verification failed: {detail}")
    if report.chain_head_sha256 is None:
        raise ValueError(
            "trust commands require a hash-chained source archive; append "
            "through ctxc archive append to upgrade this legacy archive"
        )
    sources = archive.load(
        expected_chain_head=report.chain_head_sha256,
    )
    return sources, report.chain_head_sha256


def _trust_output_aliases_input(
    args: argparse.Namespace,
    *,
    include_manifest: bool,
) -> bool:
    if args.output is None:
        return False
    protected = [args.artifact]
    if include_manifest:
        protected.append(args.manifest)
    if args.sources is not None and args.sources != "-":
        protected.append(args.sources)
    if args.source_archive is not None:
        archive_path = Path(args.source_archive)
        protected.extend(
            (
                str(archive_path / "events.jsonl"),
                str(archive_path / ".append.lock"),
            )
        )
    return any(_paths_alias(args.output, path) for path in protected)


def _trust_create(args: argparse.Namespace) -> int:
    try:
        if _trust_output_aliases_input(args, include_manifest=False):
            raise ValueError(
                "trust manifest output must not overwrite an input"
            )
        source_limits = _source_limits(args)
        artifact_limits = _artifact_limits(args)
        artifact = load_artifact_path(
            args.artifact,
            limits=artifact_limits,
        )
        sources, archive_head = _trust_sources(args, source_limits)
        manifest = create_trust_manifest(
            artifact,
            sources,
            archive_chain_head_sha256=archive_head,
            source_limits=source_limits,
            artifact_limits=artifact_limits,
        )
        _write_output(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            args.output,
        )
    except (
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        _write_error(args, exc)
        return 2
    return 0


def _trust_verify(args: argparse.Namespace) -> int:
    try:
        if _trust_output_aliases_input(args, include_manifest=True):
            raise ValueError(
                "trust verification output must not overwrite an input"
            )
        source_limits = _source_limits(args)
        artifact_limits = _artifact_limits(args)
        manifest = load_trust_manifest_path(args.manifest)
        artifact = load_artifact_path(
            args.artifact,
            limits=artifact_limits,
        )
        sources, archive_head = _trust_sources(args, source_limits)
        report = verify_trust_manifest(
            manifest,
            artifact,
            sources,
            expected_manifest_sha256=(
                args.expected_manifest_sha256
            ),
            archive_chain_head_sha256=archive_head,
            source_limits=source_limits,
            artifact_limits=artifact_limits,
        )
        _write_output(
            json.dumps(report, indent=2, ensure_ascii=False),
            args.output,
        )
    except (
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        _write_error(args, exc)
        return 2
    return 0 if report["passed"] else 3


def _inspect(args: argparse.Namespace) -> int:
    try:
        if args.max_text_chars <= 0:
            raise ValueError("max_text_chars must be a positive integer")
        artifact_limits = _artifact_limits(args)
        artifact = load_artifact_path(args.artifact, limits=artifact_limits)
        rendered = (
            render_artifact_text(
                artifact,
                include_items=args.show_items,
                max_display_items=args.max_display_items,
                max_display_links=args.max_display_links,
                max_text_chars=args.max_text_chars,
                limits=artifact_limits,
            )
            if args.inspect_format == "text"
            else json.dumps(
                summarize_artifact(
                    artifact,
                    include_items=args.show_items,
                    max_display_items=args.max_display_items,
                    max_display_links=args.max_display_links,
                    limits=artifact_limits,
                ),
                indent=2,
                ensure_ascii=True,
            )
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        _write_error(args, exc)
        return 2
    _write_output(rendered, args.output)
    return 0


def _diff(args: argparse.Namespace) -> int:
    try:
        artifact_limits = _artifact_limits(args)
        before = load_artifact_path(args.before, limits=artifact_limits)
        after = load_artifact_path(args.after, limits=artifact_limits)
        report = diff_artifacts(
            before,
            after,
            include_item_details=not args.summary_only,
            limits=artifact_limits,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        _write_error(args, exc)
        return 2
    _write_output(
        json.dumps(report, indent=2, ensure_ascii=False),
        args.output,
    )
    return 0


def _schema(args: argparse.Namespace) -> int:
    try:
        report = (
            artifact_schema_support(args.artifact_version)
            if args.artifact_version is not None
            else artifact_schema_registry()
        )
    except (TypeError, ValueError) as exc:
        _write_error(args, exc)
        return 2
    _write_output(
        json.dumps(report, indent=2, ensure_ascii=True),
        args.output,
    )
    return 0


def _connector(args: argparse.Namespace) -> int:
    """Run the optional LocalAI connector over versioned JSON Lines."""

    if not args.stdio:
        raise ValueError("connector requires --stdio")
    # Keep connector startup out of every ordinary command path.
    from .connector import serve_stdio

    binary_input = getattr(sys.stdin, "buffer", None)
    binary_output = getattr(sys.stdout, "buffer", None)
    if binary_output is None:
        if type(sys.stdout) is not io.StringIO:
            raise OSError("standard output does not expose a binary buffer")
        output_stream = sys.stdout
    else:
        output_stream = _Utf8BinaryTextWriter(binary_output)
    decoder = (
        None
        if binary_input is None
        else io.TextIOWrapper(
            binary_input,
            encoding="utf-8",
            errors="strict",
            newline="",
        )
    )
    try:
        return serve_stdio(
            input_stream=sys.stdin if decoder is None else decoder,
            output_stream=output_stream,
            max_request_bytes=args.max_request_bytes,
            max_json_depth=args.max_request_json_depth,
        )
    except UnicodeDecodeError as exc:
        raise UnicodeError("connector input must be valid UTF-8 text") from exc
    finally:
        if decoder is not None:
            decoder.detach()


def _paths_alias(first: str, second: str) -> bool:
    first_path = Path(first)
    second_path = Path(second)
    if os.path.normcase(str(first_path.resolve(strict=False))) == os.path.normcase(
        str(second_path.resolve(strict=False))
    ):
        return True
    try:
        return first_path.samefile(second_path)
    except (FileNotFoundError, OSError):
        return False


def _redact(args: argparse.Namespace) -> int:
    try:
        if args.output == "-" or args.report == "-":
            raise ValueError(
                "redacted source output and report must be file paths; "
                "'-' is supported only for input"
            )
        if _paths_alias(args.output, args.report):
            raise ValueError(
                "redacted source output and report must use different paths"
            )
        if args.input != "-" and (
            _paths_alias(args.input, args.output)
            or _paths_alias(args.input, args.report)
        ):
            raise ValueError(
                "redaction refuses to overwrite its source input"
            )
        source_limits = _source_limits(args)
        sources = _input_sources(args.input, source_limits)
        policy = RedactionPolicy(
            enabled_detectors=(
                tuple(args.detector)
                if args.detector is not None
                else SECRET_DETECTOR_NAMES
            ),
            mask_character=args.mask_character,
            max_sources=args.max_source_records,
            max_findings_per_source=args.max_redactions_per_source,
            max_total_findings=args.max_total_redactions,
            max_source_chars=args.max_redaction_source_chars,
            max_total_chars=args.max_redaction_total_chars,
        )
        result = redact_sources(sources, policy=policy)
        rendered_sources = "".join(
            json.dumps(
                source.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
            for source in result.sources
        )
        rendered_report = json.dumps(
            result.to_report(),
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        atomic_write_text(Path(args.output), rendered_sources)
        _write_output(rendered_report, args.report)
    except (
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        _write_error(args, exc)
        return 2
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


def _add_compilation_limit_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument(
        "--max-extractor-items",
        type=int,
        default=DEFAULT_COMPILATION_LIMITS.max_extractor_items,
        help="maximum memory items returned by one extractor",
    )
    parser.add_argument(
        "--max-extractor-rejections",
        type=int,
        default=DEFAULT_COMPILATION_LIMITS.max_extractor_rejections,
        help="maximum rejection records returned by one extractor",
    )
    parser.add_argument(
        "--max-extractor-bytes",
        type=int,
        default=DEFAULT_COMPILATION_LIMITS.max_extractor_bytes,
        help="maximum canonical UTF-8 JSON bytes across one extractor's items",
    )
    parser.add_argument(
        "--max-extractor-auxiliary-bytes",
        type=int,
        default=(
            DEFAULT_COMPILATION_LIMITS.max_extractor_auxiliary_bytes
        ),
        help="maximum canonical bytes in one extractor's rejections and metadata",
    )
    parser.add_argument(
        "--max-total-candidate-items",
        type=int,
        default=DEFAULT_COMPILATION_LIMITS.max_total_candidate_items,
        help="maximum candidates retained across all extractor passes",
    )
    parser.add_argument(
        "--max-resolved-items",
        type=int,
        default=DEFAULT_COMPILATION_LIMITS.max_resolved_items,
        help="maximum memory items after recovery and temporal resolution",
    )
    parser.add_argument(
        "--max-compilation-provenance-spans",
        type=int,
        default=DEFAULT_COMPILATION_LIMITS.max_provenance_spans,
        help="maximum provenance spans during compilation",
    )
    parser.add_argument(
        "--max-compilation-item-work",
        type=int,
        default=DEFAULT_COMPILATION_LIMITS.max_item_work,
        help="maximum item-comparison/render work units during compilation",
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
    compile_parser.add_argument(
        "--event-format",
        choices=("none", "jsonl"),
        default="none",
        help=(
            "emit a versioned compile completion event to stderr as JSON Lines"
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
    compile_parser.add_argument(
        "--archive-expected-chain-head",
        help=(
            "require this externally retained archive chain head before appending; "
            "must be 64 lowercase hexadecimal characters"
        ),
    )
    _add_source_limit_arguments(compile_parser)
    _add_compilation_limit_arguments(compile_parser)
    _add_error_format_argument(compile_parser)
    compile_parser.set_defaults(handler=_compile)

    materialize_parser = subparsers.add_parser(
        "materialize",
        help="emit a bounded materialized context, runtime payload, and receipt",
    )
    materialize_parser.add_argument("input", help="history path or - for stdin")
    materialize_parser.add_argument("-o", "--output")
    materialize_parser.add_argument("--current-turn-id", required=True)
    materialize_parser.add_argument("--hard-limit-tokens", type=int, required=True)
    materialize_parser.add_argument("--memory-budget-tokens", type=int, required=True)
    materialize_parser.add_argument("--reserved-output-tokens", type=int, default=1_024)
    materialize_parser.add_argument("--safety-margin-tokens", type=int, default=256)
    materialize_parser.add_argument("--fixed-input-tokens", type=int, default=0)
    materialize_parser.add_argument(
        "--fixed-input-sha256",
        help="digest of fixed host input; required exactly when its count is nonzero",
    )
    materialize_parser.add_argument("--minimum-recent-messages", type=int, default=0)
    materialize_parser.add_argument("--maximum-recent-messages", type=int, default=4_096)
    materialize_parser.add_argument("--per-message-overhead-tokens", type=int, default=0)
    materialize_parser.add_argument("--allocation-plan-sha256", required=True)
    materialize_parser.add_argument(
        "--tokenizer-profile",
        choices=(_MATERIALIZE_TOKENIZER_PROFILE,),
        required=True,
        help=(
            "exact Unicode code-point planning units only; this is not a "
            "provider tokenizer and final provider recount remains required"
        ),
    )
    _add_source_limit_arguments(materialize_parser)
    _add_compilation_limit_arguments(materialize_parser)
    _add_error_format_argument(materialize_parser)
    materialize_parser.set_defaults(handler=_materialize)

    evaluation_parser = subparsers.add_parser(
        "evaluate-materialization",
        help=(
            "run or verify the bundled offline structural-retention diagnostic"
        ),
    )
    evaluation_parser.add_argument(
        "--split",
        choices=("heldout", "development", "train", "all"),
        help="bundled grouped split to evaluate; defaults to heldout",
    )
    evaluation_parser.add_argument(
        "--verify-report",
        help="verify one previously emitted report instead of running the diagnostic",
    )
    evaluation_parser.add_argument(
        "--expected-report-sha256",
        help="independently retained report digest required for verification",
    )
    evaluation_parser.add_argument("-o", "--output")
    _add_error_format_argument(evaluation_parser)
    evaluation_parser.set_defaults(handler=_evaluate_materialization)

    degradation_evaluation_parser = subparsers.add_parser(
        "evaluate-materialization-degradation",
        help=(
            "run or verify the fixed offline strict/compact/reallocation diagnostic"
        ),
    )
    degradation_evaluation_parser.add_argument(
        "--verify-report",
        help="verify one previously emitted report instead of running the diagnostic",
    )
    degradation_evaluation_parser.add_argument(
        "--expected-report-sha256",
        help="independently retained report digest required for verification",
    )
    degradation_evaluation_parser.add_argument("-o", "--output")
    _add_error_format_argument(degradation_evaluation_parser)
    degradation_evaluation_parser.set_defaults(
        handler=_evaluate_materialization_degradation
    )

    verify_parser = subparsers.add_parser(
        "verify", help="re-verify an artifact against immutable source history"
    )
    verify_parser.add_argument("artifact")
    verify_parser.add_argument("sources", nargs="?")
    verify_parser.add_argument(
        "--archive",
        dest="source_archive",
        help="load immutable source history from this SourceArchive directory",
    )
    verify_parser.add_argument(
        "--archive-expected-chain-head",
        help=(
            "require this externally retained archive chain head; "
            "must be 64 lowercase hexadecimal characters"
        ),
    )
    verify_parser.add_argument("-o", "--output")
    _add_source_limit_arguments(verify_parser)
    _add_artifact_limit_arguments(verify_parser)
    _add_error_format_argument(verify_parser)
    verify_parser.set_defaults(handler=_verify)

    inspect_parser = subparsers.add_parser("inspect", help="show artifact health and compression")
    inspect_parser.add_argument("artifact")
    inspect_parser.add_argument("-o", "--output")
    inspect_parser.add_argument(
        "--format",
        dest="inspect_format",
        choices=("json", "text"),
        default="json",
        help="render machine-readable JSON or control-character-safe text",
    )
    inspect_parser.add_argument(
        "--show-items",
        action="store_true",
        help="include bounded item, provenance, status, and selection details",
    )
    inspect_parser.add_argument(
        "--max-display-items",
        type=int,
        default=100,
        help="maximum item details to display",
    )
    inspect_parser.add_argument(
        "--max-text-chars",
        type=int,
        default=240,
        help="maximum raw characters per string in text output",
    )
    inspect_parser.add_argument(
        "--max-display-links",
        type=int,
        default=16,
        help="maximum tags, state links, or provenance spans per item",
    )
    _add_artifact_limit_arguments(inspect_parser)
    _add_error_format_argument(inspect_parser)
    inspect_parser.set_defaults(handler=_inspect)

    diff_parser = subparsers.add_parser(
        "diff",
        help="compare two integrity-checked compiled artifacts",
    )
    diff_parser.add_argument("before", help="earlier compiled artifact")
    diff_parser.add_argument("after", help="later compiled artifact")
    diff_parser.add_argument("-o", "--output")
    diff_parser.add_argument(
        "--summary-only",
        action="store_true",
        help="omit per-item change details",
    )
    _add_artifact_limit_arguments(diff_parser)
    _add_error_format_argument(diff_parser)
    diff_parser.set_defaults(handler=_diff)

    schema_parser = subparsers.add_parser(
        "schema",
        help="report compiled-artifact schema compatibility",
    )
    schema_parser.add_argument(
        "--artifact-version",
        help="query one canonical MAJOR.MINOR artifact schema version",
    )
    schema_parser.add_argument("-o", "--output")
    _add_error_format_argument(schema_parser)
    schema_parser.set_defaults(handler=_schema)

    connector_parser = subparsers.add_parser(
        "connector",
        help="serve the optional LocalAI connector protocol",
    )
    connector_transport = connector_parser.add_mutually_exclusive_group(
        required=True,
    )
    connector_transport.add_argument(
        "--stdio",
        action="store_true",
        help="read one versioned JSON request per line and write JSON responses",
    )
    connector_parser.add_argument(
        "--max-request-bytes",
        type=int,
        default=8 * 1024 * 1024,
        help="maximum UTF-8 bytes in one connector request line",
    )
    connector_parser.add_argument(
        "--max-request-json-depth",
        type=int,
        default=128,
        help="maximum decoded JSON container depth in one connector request",
    )
    _add_error_format_argument(connector_parser)
    connector_parser.set_defaults(handler=_connector)

    redact_parser = subparsers.add_parser(
        "redact",
        help="mask common content secrets before compilation",
    )
    redact_parser.add_argument("input", help="history path or - for stdin")
    redact_parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="redacted JSONL source path (must differ from input)",
    )
    redact_parser.add_argument(
        "--report",
        required=True,
        help="self-hashed redaction report path (must differ from input/output)",
    )
    redact_parser.add_argument(
        "--detector",
        action="append",
        choices=SECRET_DETECTOR_NAMES,
        help="enable one fixed detector; repeat to select a subset",
    )
    redact_parser.add_argument(
        "--mask-character",
        type=_mask_character,
        metavar="MASK",
        default="*",
        help=(
            "length-preserving mask: *, #, U+2588, or U+25A0"
        ),
    )
    redact_parser.add_argument(
        "--max-redactions-per-source",
        type=int,
        default=1_000,
        help="maximum selected secret spans in one source",
    )
    redact_parser.add_argument(
        "--max-total-redactions",
        type=int,
        default=10_000,
        help="maximum selected secret spans across all sources",
    )
    redact_parser.add_argument(
        "--max-redaction-source-chars",
        type=int,
        default=2_000_000,
        help="maximum Unicode characters scanned in one source",
    )
    redact_parser.add_argument(
        "--max-redaction-total-chars",
        type=int,
        default=16_000_000,
        help="maximum Unicode characters scanned across all sources",
    )
    _add_source_limit_arguments(redact_parser)
    _add_error_format_argument(redact_parser)
    redact_parser.set_defaults(handler=_redact)

    archive_parser = subparsers.add_parser("archive", help="manage immutable cold source events")
    archive_subparsers = archive_parser.add_subparsers(dest="archive_command", required=True)
    archive_append = archive_subparsers.add_parser("append", help="append JSON/JSONL sources")
    archive_append.add_argument("archive")
    archive_append.add_argument("input", help="history path or - for stdin")
    archive_append.add_argument("-o", "--output")
    archive_append.add_argument(
        "--expected-chain-head",
        help=(
            "require this externally retained chain head before appending; "
            "must be 64 lowercase hexadecimal characters"
        ),
    )
    _add_source_limit_arguments(archive_append)
    _add_error_format_argument(archive_append)
    archive_append.set_defaults(handler=_archive_append)
    archive_verify = archive_subparsers.add_parser("verify", help="verify archive hashes and shape")
    archive_verify.add_argument("archive")
    archive_verify.add_argument("-o", "--output")
    archive_verify.add_argument(
        "--expected-chain-head",
        help=(
            "compare against this externally retained chain head; "
            "must be 64 lowercase hexadecimal characters"
        ),
    )
    _add_source_limit_arguments(archive_verify)
    _add_error_format_argument(archive_verify)
    archive_verify.set_defaults(handler=_archive_verify)

    trust_parser = subparsers.add_parser(
        "trust",
        help="create or verify externally anchored artifact/source manifests",
    )
    trust_subparsers = trust_parser.add_subparsers(
        dest="trust_command",
        required=True,
    )
    trust_create = trust_subparsers.add_parser(
        "create",
        help="create a detached manifest for a replay-verified artifact",
    )
    trust_create.add_argument("artifact")
    trust_create.add_argument("sources", nargs="?")
    trust_create.add_argument(
        "--archive",
        dest="source_archive",
        help="bind immutable sources from this SourceArchive directory",
    )
    trust_create.add_argument(
        "--archive-expected-chain-head",
        help=(
            "require this externally retained archive chain head before "
            "creating the manifest"
        ),
    )
    trust_create.add_argument("-o", "--output")
    _add_source_limit_arguments(trust_create)
    _add_artifact_limit_arguments(trust_create)
    _add_error_format_argument(trust_create)
    trust_create.set_defaults(handler=_trust_create)

    trust_verify = trust_subparsers.add_parser(
        "verify",
        help="verify a detached manifest against an external SHA-256 anchor",
    )
    trust_verify.add_argument("manifest")
    trust_verify.add_argument("artifact")
    trust_verify.add_argument("sources", nargs="?")
    trust_verify.add_argument(
        "--archive",
        dest="source_archive",
        help="verify immutable sources from this SourceArchive directory",
    )
    trust_verify.add_argument(
        "--archive-expected-chain-head",
        help=(
            "require this externally retained archive chain head before "
            "verifying the manifest"
        ),
    )
    trust_verify.add_argument(
        "--expected-manifest-sha256",
        required=True,
        help=(
            "externally retained 64-character lowercase SHA-256 of the "
            "manifest payload"
        ),
    )
    trust_verify.add_argument("-o", "--output")
    _add_source_limit_arguments(trust_verify)
    _add_artifact_limit_arguments(trust_verify)
    _add_error_format_argument(trust_verify)
    trust_verify.set_defaults(handler=_trust_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except _StderrEmissionError:
        return 2
    except (OSError, TypeError, ValueError, TimeoutError) as exc:
        with suppress(_StderrEmissionError):
            _write_error(args, exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
