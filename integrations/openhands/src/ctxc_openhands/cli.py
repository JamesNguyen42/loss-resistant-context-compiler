"""Command-line diagnostics and recovery for the isolated OpenHands package."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import context_compiler

from . import __version__
from .compatibility import verify_pin_manifest
from .crash_campaign import run_crash_concurrency_campaign
from .evidence import (
    _verify_evidence_mapping,
    verify_evidence,
    write_evidence_report,
)
from .host_guard import inspect_live_compatibility
from .recovery import recover_session
from .replay import replay_final_request
from .scenario import run_offline_crash_scenario
from .soak import run_deterministic_soak
from .storage import (
    SQLiteGenerationStore,
    _regular_file_identity,
    _reject_link_or_reparse_chain,
)
from .tokenizer import CanonicalUtf8ByteTokenizer, validate_tokenizer_vectors

MAX_CLI_JSON_BYTES = 16 * 1024 * 1024
MAX_CLI_JSON_DEPTH = 64
MAX_CLI_JSON_ITEMS = 500_000


def _json(value: Any, *, pretty: bool = True) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        allow_nan=False,
    )


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _validate_json_structure(value: object) -> None:
    pending: list[tuple[object, int]] = [(value, 1)]
    item_count = 0
    while pending:
        item, depth = pending.pop()
        if depth > MAX_CLI_JSON_DEPTH:
            raise ValueError(f"JSON input exceeds structural depth {MAX_CLI_JSON_DEPTH}")
        item_count += 1
        if item_count > MAX_CLI_JSON_ITEMS:
            raise ValueError(f"JSON input exceeds structural item count {MAX_CLI_JSON_ITEMS}")
        if isinstance(item, dict):
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def _load_json(path: str | Path) -> Any:
    source = Path(path).expanduser().absolute()
    _reject_link_or_reparse_chain(source, label="JSON input")
    expected_identity = _regular_file_identity(source, label="JSON input")
    before = source.lstat()
    with source.open("rb") as stream:
        raw = stream.read(MAX_CLI_JSON_BYTES + 1)
        opened = os.fstat(stream.fileno())
    actual_identity = _regular_file_identity(source, label="JSON input")
    after = source.lstat()
    if len(raw) > MAX_CLI_JSON_BYTES:
        raise ValueError(f"JSON input exceeds {MAX_CLI_JSON_BYTES} bytes")
    if (
        len(raw) != before.st_size
        or len(raw) != opened.st_size
        or len(raw) != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
        or expected_identity != (opened.st_dev, opened.st_ino)
        or expected_identity != actual_identity
        or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        or getattr(opened, "st_nlink", 1) not in (0, 1)
        or getattr(after, "st_nlink", 1) not in (0, 1)
        or not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(after.st_mode)
    ):
        raise ValueError("JSON input identity or bytes changed while reading")
    text = raw.decode("utf-8", errors="strict")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except RecursionError as exc:
        raise ValueError("JSON input exceeds the decoder recursion limit") from exc
    _validate_json_structure(value)
    return value


def _safe_error_text(exc: BaseException) -> str:
    try:
        return str(exc)
    except BaseException:
        return "<exception text unavailable>"


def _write_report(path: str | None, value: Any) -> None:
    rendered = _json(value) + "\n"
    if path is None:
        sys.stdout.write(rendered)
        return
    destination = Path(path).expanduser().absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise FileExistsError(
            f"report output already exists: {destination}"
        ) from None
    sys.stdout.write(_json({"written": str(destination)}) + "\n")


def _write_evidence_output(path: str | None, value: dict[str, Any]) -> None:
    if path is None:
        sys.stdout.write(_json(value, pretty=False) + "\n")
        return
    destination = Path(path).expanduser().absolute()
    write_evidence_report(destination, value)
    sys.stdout.write(_json({"written": str(destination)}) + "\n")


def _doctor(*, require_live: bool) -> tuple[dict[str, Any], int]:
    imported_before = {
        name for name in sys.modules if name == "openhands" or name.startswith("openhands.")
    }
    manifest = verify_pin_manifest()
    tokenizer = validate_tokenizer_vectors(CanonicalUtf8ByteTokenizer())
    live = inspect_live_compatibility()
    imported_after = {
        name for name in sys.modules if name == "openhands" or name.startswith("openhands.")
    }
    sqlite_supports_strict = sqlite3.sqlite_version_info >= (3, 37, 0)
    offline_ready = (
        context_compiler.__version__ == "0.1.1a8"
        and tokenizer.passed
        and sqlite_supports_strict
        and imported_after == imported_before
    )
    report = {
        "schema": "ctxc-openhands-doctor-0.1",
        "package_version": __version__,
        "core_version": context_compiler.__version__,
        "python": sys.version.split()[0],
        "sqlite": sqlite3.sqlite_version,
        "sqlite_strict_supported": sqlite_supports_strict,
        "manifest": {
            "path": str(manifest.path),
            "file_sha256": manifest.file_sha256,
            "payload_sha256": manifest.payload_sha256,
            "reviewed_api_sha256": manifest.reviewed_api_sha256,
            "reviewed_api_file_count": manifest.reviewed_api_file_count,
            "retained_blocker_code": manifest.blocker_code,
        },
        "tokenizer_vectors": {
            "passed": tokenizer.passed,
            "identity": tokenizer.tokenizer_identity,
            "vector_count": tokenizer.vector_count,
            "vector_sha256": tokenizer.actual_vector_sha256,
            "issues": list(tokenizer.issues),
        },
        "ordinary_import_loaded_openhands": imported_after != imported_before,
        "offline_ready": offline_ready,
        "live_ready": live.passed,
        "live": live.to_dict(),
        "semantic_completeness_claimed": False,
    }
    passed = offline_ready and (live.passed if require_live else True)
    report["passed"] = passed
    report["require_live"] = require_live
    return report, 0 if passed else 2


def _explain(database: str, session_id: str) -> dict[str, Any]:
    store = SQLiteGenerationStore(database, require_existing=True)
    integrity = store.integrity_report()
    snapshot = store.snapshot(session_id)
    active = store.read_active(session_id)
    return {
        "schema": "ctxc-openhands-explain-0.1",
        "session_id": session_id,
        "source_count": snapshot.source_count,
        "source_head_sha256": snapshot.source_head_sha256,
        "source_digest_sha256": snapshot.source_digest_sha256,
        "active_epoch": snapshot.active_epoch,
        "active_generation": (
            None
            if active is None
            else {
                "generation_id": active.generation_id,
                "covered_source_count": active.covered_source_count,
                "tail_count": len(active.tail),
                "bundle_sha256": active.bundle.bundle_sha256,
                "semantic_result_digest": active.semantic_result_digest,
                "certificate": active.bundle.certificate,
            }
        ),
        "generations": list(store.list_generations(session_id)),
        "integrity": integrity,
        "semantic_completeness_claimed": False,
    }


def _replay(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    tokenizer = CanonicalUtf8ByteTokenizer()
    if args.ledger is not None:
        ledger = _load_json(args.ledger)
    else:
        missing = [
            name for name in ("database", "session_id", "request_id") if getattr(args, name) is None
        ]
        if missing:
            raise ValueError("stored replay requires --database, --session-id, and --request-id")
        store = SQLiteGenerationStore(args.database, require_existing=True)
        ledger = store.load_request_ledger(
            session_id=args.session_id,
            request_id=args.request_id,
            tokenizer=tokenizer,
        )
    report = replay_final_request(ledger, tokenizer=tokenizer).to_dict()
    return report, 0 if report["passed"] else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctxc-openhands",
        description=(
            "Fail-closed diagnostics for the separately packaged CtxC OpenHands integration"
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    doctor = subcommands.add_parser("doctor")
    doctor.add_argument("--require-live", action="store_true")

    explain = subcommands.add_parser("explain")
    explain.add_argument("--database", required=True)
    explain.add_argument("--session-id", required=True)

    replay = subcommands.add_parser("replay")
    replay.add_argument("--ledger")
    replay.add_argument("--database")
    replay.add_argument("--session-id")
    replay.add_argument("--request-id")

    recover = subcommands.add_parser("recover")
    recover.add_argument("--database", required=True)
    recover.add_argument("--session-id", required=True)
    recover.add_argument("--apply", action="store_true")

    rehydrate = subcommands.add_parser("rehydrate")
    rehydrate.add_argument("--database", required=True)
    rehydrate.add_argument("--session-id", required=True)
    rehydrate.add_argument("--source-id", required=True)
    rehydrate.add_argument("--start", required=True, type=int)
    rehydrate.add_argument("--end", required=True, type=int)
    rehydrate.add_argument("--quote-sha256", required=True)

    scenario = subcommands.add_parser("offline-scenario")
    scenario.add_argument("--database", required=True)
    scenario.add_argument("--output")

    soak = subcommands.add_parser("soak")
    soak.add_argument("--database", required=True)
    soak.add_argument("--events", type=int, default=10_000)
    soak.add_argument("--compactions", type=int, default=100)
    soak.add_argument("--restart-every", type=int)
    soak.add_argument("--output")

    campaign = subcommands.add_parser("fault-campaign")
    campaign.add_argument("--database", required=True)
    campaign.add_argument("--schedules", type=int, default=1_024)
    campaign.add_argument("--seed", type=lambda value: int(value, 0), default=0xC7C0_5A17)
    campaign.add_argument("--output")

    evidence = subcommands.add_parser("verify-evidence")
    evidence.add_argument("--report", required=True)
    evidence.add_argument("--database")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = tuple(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(raw_argv)
    try:
        if args.command == "doctor":
            value, status = _doctor(require_live=args.require_live)
            _write_report(None, value)
            return status
        if args.command == "explain":
            value = _explain(args.database, args.session_id)
            _write_report(None, value)
            integrity = value.get("integrity")
            return (
                0
                if isinstance(integrity, dict) and integrity.get("passed") is True
                else 2
            )
        if args.command == "replay":
            value, status = _replay(args)
            _write_report(None, value)
            return status
        if args.command == "recover":
            store = SQLiteGenerationStore(args.database, require_existing=True)
            report = recover_session(
                store,
                session_id=args.session_id,
                apply=args.apply,
            )
            _write_report(None, report.to_dict())
            return 0 if report.passed else 2
        if args.command == "rehydrate":
            store = SQLiteGenerationStore(args.database, require_existing=True)
            value = store.rehydrate(
                session_id=args.session_id,
                source_id=args.source_id,
                start=args.start,
                end=args.end,
                quote_sha256=args.quote_sha256,
            )
            _write_report(None, value)
            return 0
        if args.command == "offline-scenario":
            value = run_offline_crash_scenario(
                args.database,
                producer_argv=raw_argv,
            )
            _write_evidence_output(args.output, value)
            verification = _verify_evidence_mapping(value, database=args.database)
            return 0 if verification.get("passed") is True else 2
        if args.command == "soak":
            value = run_deterministic_soak(
                args.database,
                event_count=args.events,
                compaction_count=args.compactions,
                restart_every_compactions=args.restart_every,
                producer_argv=raw_argv,
            )
            _write_evidence_output(args.output, value)
            verification = _verify_evidence_mapping(value, database=args.database)
            return 0 if verification.get("passed") is True else 2
        if args.command == "fault-campaign":
            value = run_crash_concurrency_campaign(
                args.database,
                schedule_count=args.schedules,
                seed=args.seed,
                producer_argv=raw_argv,
            )
            _write_evidence_output(args.output, value)
            verification = _verify_evidence_mapping(value, database=args.database)
            return 0 if verification.get("passed") is True else 2
        if args.command == "verify-evidence":
            value = verify_evidence(
                args.report,
                database=args.database,
            )
            _write_report(None, value)
            return 0 if value.get("passed") is True else 2
        raise RuntimeError(f"unhandled command: {args.command}")
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError, RuntimeError) as exc:
        sys.stderr.write(
            _json(
                {
                    "passed": False,
                    "error_type": type(exc).__name__,
                    "error": _safe_error_text(exc),
                },
                pretty=False,
            )
            + "\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
