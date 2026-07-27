from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

import ctxc_openhands.crash_campaign as crash_campaign
import ctxc_openhands.storage as storage
from ctxc_openhands.cli import main
from ctxc_openhands.crash_campaign import (
    CRASH_CAMPAIGN_REPORT_SCHEMA,
    CRASH_CAMPAIGN_VERIFICATION_SCHEMA,
    MAXIMUM_CAMPAIGN_SCHEDULES,
    MINIMUM_CAMPAIGN_SCHEDULES,
    run_crash_concurrency_campaign,
    verify_crash_campaign_report,
)
from ctxc_openhands.faults import (
    TRANSACTION_FAULT_DOMAINS,
    deterministic_transaction_schedules,
)
from ctxc_openhands.storage import SQLiteGenerationStore


def _report_digest(report: dict) -> str:
    unsigned = {key: value for key, value in report.items() if key != "report_sha256"}
    raw = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_transaction_schedules_are_seeded_bounded_and_cover_every_domain() -> None:
    count = MINIMUM_CAMPAIGN_SCHEDULES
    first = deterministic_transaction_schedules(count=count, seed=0xA11CE)
    second = deterministic_transaction_schedules(count=count, seed=0xA11CE)

    assert first == second
    assert len(first) == count
    assert len({schedule.schedule_sha256 for schedule in first}) == count
    complete_cycle = sum(len(points) for _domain, points in TRANSACTION_FAULT_DOMAINS)
    observed = {
        (schedule.domain, schedule.fault.point)
        for schedule in first[:complete_cycle]
    }
    expected = {
        (domain, point)
        for domain, points in TRANSACTION_FAULT_DOMAINS
        for point in points
    }
    assert observed == expected
    assert all(len(schedule.worker_order) == 3 for schedule in first)
    assert all(len(schedule.worker_yields) == 3 for schedule in first)


def test_report_verifier_bounds_and_detaches_hostile_inputs() -> None:
    invalid_values = (
        (None, "campaign report must be an object"),
        ({"value": object()}, "campaign report contains a non-JSON value"),
        ({"value": "\ud800"}, "campaign report contains invalid UTF-8 text"),
    )
    for value, expected_issue in invalid_values:
        verification = verify_crash_campaign_report(value)
        assert verification["passed"] is False
        assert verification["report_verified"] is False
        assert verification["issues"] == [expected_issue]

    class HostileMapping(dict):
        def items(self):
            raise RuntimeError("hostile mapping must not escape")

    hostile = verify_crash_campaign_report(HostileMapping())
    assert hostile["passed"] is False
    assert hostile["issues"] == [
        "campaign report mapping could not be inspected"
    ]

    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    cyclic = verify_crash_campaign_report(cycle)
    assert cyclic["passed"] is False
    assert cyclic["issues"] == [
        "campaign report contains a cycle or shared container"
    ]

    deep: dict[str, object] = {}
    cursor = deep
    for _ in range(65):
        child: dict[str, object] = {}
        cursor["child"] = child
        cursor = child
    too_deep = verify_crash_campaign_report(deep)
    assert too_deep["passed"] is False
    assert too_deep["issues"] == ["campaign report exceeds the depth limit"]

    oversized = {str(index): "x" * 1_000_000 for index in range(5)}
    too_large = verify_crash_campaign_report(oversized)
    assert too_large["passed"] is False
    assert too_large["issues"] == [
        "campaign report exceeds the canonical byte limit"
    ]


def test_strict_campaign_producer_argv_parser_and_preflight(
    tmp_path: Path,
) -> None:
    database = tmp_path / "campaign.sqlite3"
    parsed = crash_campaign._parse_campaign_cli_argv(
        [
            "fault-campaign",
            f"--database={database}",
            "--schedules=1024",
            "--seed=0x5A17C7C0",
            "--output=evidence.json",
        ]
    )
    assert parsed == {
        "database": str(database.absolute()),
        "schedule_count": 1_024,
        "seed": 0x5A17_C7C0,
        "output": "evidence.json",
    }

    invalid_vectors = (
        (
            ["fault-campaign", "--database", str(tmp_path / "other.sqlite3")],
            "do not match the campaign invocation",
        ),
        (
            [
                "fault-campaign",
                "--database",
                str(database),
                "--schedules",
                "1025",
            ],
            "do not match the campaign invocation",
        ),
        (
            [
                "fault-campaign",
                "--database",
                str(database),
                "--database",
                str(database),
            ],
            "repeats flag --database",
        ),
        (
            ["fault-campaign", "--database", str(database), "--unknown=value"],
            "unknown flag --unknown",
        ),
        (
            ["fault-campaign", "--database", str(database), "--seed"],
            "flag --seed is missing its value",
        ),
        (
            ["fault-campaign", "positional", "--database", str(database)],
            "unexpected positional argument",
        ),
        (
            ["fault-campaign", "--schedules", "1024"],
            "missing --database",
        ),
    )
    for argv, message in invalid_vectors:
        with pytest.raises(ValueError, match=message):
            run_crash_concurrency_campaign(database, producer_argv=argv)
        assert not database.exists()

    with pytest.raises(ValueError, match="valid UTF-8"):
        run_crash_concurrency_campaign(
            database,
            producer_argv=[
                "fault-campaign",
                "--database",
                str(database),
                "\ud800",
            ],
        )
    assert not database.exists()


@pytest.mark.retained_evidence
def test_1024_schedule_real_wal_campaign_and_abrupt_process_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "crash-campaign.sqlite3"
    report = run_crash_concurrency_campaign(
        database,
        schedule_count=MINIMUM_CAMPAIGN_SCHEDULES,
        seed=0x5A17_C7C0,
    )

    assert report["schema"] == CRASH_CAMPAIGN_REPORT_SCHEMA
    assert report["status"] == "passed"
    assert report["execution_path_network_capability"] == "none"
    assert report["network_isolation_enforced"] is False
    assert report["paid_service_use"] == "none"
    assert report["command"]["invocation_kind"] == "library-api"
    assert report["command"]["producer_argv"] == []
    assert report["command"]["actual_process_argv_recorded"] is False
    assert report["command"]["parameters"]["database"] == str(database.absolute())
    assert report["command"]["parameters"]["schedule_count"] == 1_024
    assert report["command"]["parameters"]["seed"] == 0x5A17_C7C0
    assert report["schedule_count"] == 1_024
    assert set(report["domains"]) == {
        domain for domain, _points in TRANSACTION_FAULT_DOMAINS
    }
    assert all(
        domain["schedule_count"] > 0 for domain in report["domains"].values()
    )
    assert report["interleaving"]["worker_count"] == 3
    assert report["interleaving"]["multi_worker_schedule_count"] == 1_024
    assert report["outcome_totals"]["passed"] == 1_024
    assert report["outcome_totals"]["failed"] == 0
    assert report["append_outcome"]["source_count"] > 0
    assert report["append_outcome"]["contiguous_sequences"] is True
    assert report["abrupt_subprocess"]["case_count"] == 6
    assert report["abrupt_subprocess"]["passed"] == 6
    assert report["abrupt_subprocess"]["failed"] == 0
    assert report["no_event_loss"] is True
    assert report["no_event_duplication"] is True
    assert report["old_or_new_verified_visibility_only"] is True
    assert report["semantic_completeness_claimed"] is False
    assert report["report_sha256"] == _report_digest(report)
    database_bytes = database.read_bytes()
    assert report["database"]["byte_length"] == len(database_bytes)
    assert report["database"]["sha256"] == hashlib.sha256(database_bytes).hexdigest()
    assert report["database"]["wal_checkpoint"]["wal_byte_length"] == 0

    verification = verify_crash_campaign_report(report, database=database)
    assert verification["schema"] == CRASH_CAMPAIGN_VERIFICATION_SCHEMA
    assert verification["passed"] is True
    assert verification["issues"] == []
    assert verification["scope"] == "report-and-database"
    assert verification["report_verified"] is True
    assert verification["database_supplied"] is True
    assert verification["database_verified"] is True
    assert verification["attestation_claimed"] is False
    assert verification["database_sha256"] == report["database"]["sha256"]
    assert SQLiteGenerationStore(database).integrity_report()["passed"] is True

    report_only = verify_crash_campaign_report(report)
    assert report_only["scope"] == "report-only"
    assert report_only["passed"] is False
    assert report_only["report_verified"] is True
    assert report_only["database_supplied"] is False
    assert report_only["database_verified"] is False
    assert (
        "database not supplied; JSON-only verification is incomplete"
        in report_only["issues"]
    )

    exact_cli = copy.deepcopy(report)
    exact_cli["command"]["invocation_kind"] = "cli"
    exact_cli["command"]["producer_argv"] = [
        "fault-campaign",
        "--database",
        str(database.absolute()),
        "--schedules=1024",
        f"--seed={0x5A17_C7C0}",
        "--output",
        "campaign-report.json",
    ]
    exact_cli["report_sha256"] = _report_digest(exact_cli)
    exact_cli_verification = verify_crash_campaign_report(
        exact_cli,
        database=database,
    )
    assert exact_cli_verification["passed"] is True

    resigned_argv_vectors = (
        (
            [
                "fault-campaign",
                "--database",
                str(database.absolute()),
                "--schedules=1025",
                f"--seed={0x5A17_C7C0}",
            ],
            "producer argv parameters do not match exact command parameters",
        ),
        (
            [
                "fault-campaign",
                "--database",
                str(database.absolute()),
                "--database",
                str(database.absolute()),
            ],
            "producer argv repeats flag --database",
        ),
        (
            [
                "fault-campaign",
                "--database",
                str(database.absolute()),
                "--unknown=value",
            ],
            "producer argv contains unknown flag --unknown",
        ),
    )
    for producer_argv, expected_issue in resigned_argv_vectors:
        resigned = copy.deepcopy(report)
        resigned["command"]["invocation_kind"] = "cli"
        resigned["command"]["producer_argv"] = producer_argv
        resigned["report_sha256"] = _report_digest(resigned)
        resigned_verification = verify_crash_campaign_report(resigned)
        assert resigned_verification["passed"] is False
        assert expected_issue in resigned_verification["issues"]

    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{database}{suffix}")
        sidecar.write_bytes(b"")
        sidecar_verification = verify_crash_campaign_report(
            report,
            database=database,
        )
        assert sidecar_verification["passed"] is False
        assert (
            "database WAL/SHM sidecars must be absent"
            in sidecar_verification["issues"]
        )
        sidecar.unlink()

    real_copyfile = shutil.copyfile
    mutation_database = tmp_path / "campaign-mutated.sqlite3"
    real_copyfile(database, mutation_database)

    def copy_then_mutate(source, destination, *args, **kwargs):
        result = real_copyfile(source, destination, *args, **kwargs)
        if Path(source) == mutation_database:
            with mutation_database.open("r+b") as stream:
                stream.seek(-1, os.SEEK_END)
                final = stream.read(1)
                stream.seek(-1, os.SEEK_END)
                stream.write(bytes([final[0] ^ 1]))
        return result

    monkeypatch.setattr(
        "ctxc_openhands.crash_campaign.shutil.copyfile",
        copy_then_mutate,
    )
    mutation_verification = verify_crash_campaign_report(
        report,
        database=mutation_database,
    )
    assert mutation_verification["passed"] is False
    assert (
        "database changed while campaign evidence was verified"
        in mutation_verification["issues"]
    )

    replacement_database = tmp_path / "campaign-replaced.sqlite3"
    real_copyfile(database, replacement_database)

    def copy_then_replace(source, destination, *args, **kwargs):
        result = real_copyfile(source, destination, *args, **kwargs)
        if Path(source) == replacement_database:
            replacement = tmp_path / "same-size-replacement.sqlite3"
            payload = bytearray(replacement_database.read_bytes())
            payload[-1] ^= 1
            replacement.write_bytes(payload)
            os.replace(replacement, replacement_database)
        return result

    monkeypatch.setattr(
        "ctxc_openhands.crash_campaign.shutil.copyfile",
        copy_then_replace,
    )
    replacement_verification = verify_crash_campaign_report(
        report,
        database=replacement_database,
    )
    assert replacement_verification["passed"] is False
    assert (
        "database changed while campaign evidence was verified"
        in replacement_verification["issues"]
    )

    unknown = copy.deepcopy(report)
    unknown["unexpected_summary"] = {}
    unknown["report_sha256"] = _report_digest(unknown)
    verification = verify_crash_campaign_report(unknown)
    assert verification["passed"] is False
    assert "report has unknown fields: unexpected_summary" in verification["issues"]

    missing = copy.deepcopy(report)
    del missing["interleaving"]["winner_identity_is_not_evidence"]
    missing["report_sha256"] = _report_digest(missing)
    verification = verify_crash_campaign_report(missing)
    assert verification["passed"] is False
    assert (
        "interleaving is missing fields: winner_identity_is_not_evidence"
        in verification["issues"]
    )

    digest_tamper = copy.deepcopy(report)
    digest_tamper["database"]["sha256"] = "0" * 64
    digest_tamper["report_sha256"] = _report_digest(digest_tamper)
    verification = verify_crash_campaign_report(
        digest_tamper,
        database=database,
    )
    assert verification["passed"] is False
    assert "database digest or byte length mismatch" in verification["issues"]


def test_campaign_report_verifier_rejects_tampering(tmp_path: Path) -> None:
    report = {
        "schema": CRASH_CAMPAIGN_REPORT_SCHEMA,
        "status": "passed",
        "schedule_count": 1,
        "seed": 1,
        "semantic_completeness_claimed": False,
    }
    report["report_sha256"] = _report_digest(report)
    verification = verify_crash_campaign_report(report)
    assert verification["passed"] is False
    assert "schedule_count is below the required bound" in verification["issues"]

    tampered = copy.deepcopy(report)
    tampered["semantic_completeness_claimed"] = True
    verification = verify_crash_campaign_report(tampered)
    assert verification["passed"] is False
    assert "report self-hash mismatch" in verification["issues"]
    assert "semantic completeness claim boundary widened" in verification["issues"]

    oversized_report = copy.deepcopy(report)
    oversized_report["schedule_count"] = MAXIMUM_CAMPAIGN_SCHEDULES + 1
    oversized_report["report_sha256"] = _report_digest(oversized_report)
    verification = verify_crash_campaign_report(oversized_report)
    assert verification["passed"] is False
    assert "schedule_count exceeds the evidence-size bound" in verification["issues"]

    with pytest.raises(ValueError, match="at least 1024"):
        run_crash_concurrency_campaign(
            tmp_path / "too-small.sqlite3",
            schedule_count=1_023,
        )

    over_bound = tmp_path / "too-large.sqlite3"
    with pytest.raises(ValueError, match="at most 20000"):
        run_crash_concurrency_campaign(
            over_bound,
            schedule_count=MAXIMUM_CAMPAIGN_SCHEDULES + 1,
        )
    assert not over_bound.exists()


def test_campaign_refuses_to_overwrite_database(tmp_path: Path) -> None:
    database = tmp_path / "existing.sqlite3"
    original = b"pre-existing evidence must remain unchanged"
    database.write_bytes(original)
    with pytest.raises(FileExistsError, match="already exists"):
        run_crash_concurrency_campaign(database)
    assert database.read_bytes() == original


def test_campaign_refuses_a_preexisting_symlink_database(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite3"
    target.write_bytes(b"target must remain unchanged")
    database = tmp_path / "linked.sqlite3"
    try:
        database.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(
        ValueError,
        match=r"^SQLite store path must not be a symbolic link or reparse point$",
    ):
        run_crash_concurrency_campaign(database)
    assert database.is_symlink()
    assert target.read_bytes() == b"target must remain unchanged"
    for suffix in ("-wal", "-shm", "-journal"):
        assert not Path(f"{database}{suffix}").exists()


def test_campaign_rejects_simulated_reparse_database_cross_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "simulated-reparse.sqlite3"
    original = b"simulated reparse evidence must remain unchanged"
    database.write_bytes(original)
    real_is_link_or_reparse = storage._is_link_or_reparse

    def simulated_link_or_reparse(path: Path) -> bool:
        if path == database:
            return True
        return real_is_link_or_reparse(path)

    monkeypatch.setattr(
        storage,
        "_is_link_or_reparse",
        simulated_link_or_reparse,
    )
    with pytest.raises(
        ValueError,
        match=r"^SQLite store path must not be a symbolic link or reparse point$",
    ):
        run_crash_concurrency_campaign(database)
    assert database.read_bytes() == original
    for suffix in ("-wal", "-shm", "-journal"):
        assert not Path(f"{database}{suffix}").exists()


def test_campaign_cli_parses_hex_seed_and_refuses_a_short_run(
    tmp_path: Path,
    capsys,
) -> None:
    status = main(
        [
            "fault-campaign",
            "--database",
            str(tmp_path / "too-short.sqlite3"),
            "--schedules",
            "1023",
            "--seed",
            "0x5A17C7C0",
        ]
    )
    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert "schedule_count must be at least 1024" in captured.err
    assert not (tmp_path / "too-short.sqlite3").exists()
