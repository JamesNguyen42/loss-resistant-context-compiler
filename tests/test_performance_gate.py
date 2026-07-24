from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import benchmarks.performance_gate as performance_gate


def test_performance_workload_is_frozen_and_prefix_stable() -> None:
    small = performance_gate.build_performance_sources(5)
    full = performance_gate.build_performance_sources(
        performance_gate.PERFORMANCE_SOURCE_COUNTS[-1]
    )

    assert [source.id for source in small] == [
        "perf-000000",
        "perf-000001",
        "perf-000002",
        "perf-000003",
        "perf-000004",
    ]
    assert small == full[:5]
    assert performance_gate._workload_sha256(full) == (
        performance_gate.PERFORMANCE_WORKLOAD_SHA256[256]
    )
    for count, expected in performance_gate.PERFORMANCE_WORKLOAD_SHA256.items():
        assert performance_gate._checked_workload_sha256(
            performance_gate.build_performance_sources(count)
        ) == expected


@pytest.mark.parametrize("count", [None, True, 1.0, "1"])
def test_performance_workload_rejects_noninteger_counts(count: object) -> None:
    with pytest.raises(TypeError, match="must be an integer"):
        performance_gate.build_performance_sources(count)  # type: ignore[arg-type]


@pytest.mark.parametrize("count", [-1, 0])
def test_performance_workload_rejects_nonpositive_counts(count: int) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        performance_gate.build_performance_sources(count)


def test_small_real_performance_workload_compiles_verified_memory() -> None:
    measurement = performance_gate._measure_compile(
        performance_gate.build_performance_sources(8)
    )

    assert measurement.duration_seconds >= 0
    assert measurement.item_count > 0
    assert measurement.selected_item_count > 0
    assert measurement.peak_traced_bytes is None


def test_peak_measurement_refuses_contaminated_tracemalloc_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_gate.tracemalloc, "is_tracing", lambda: True)

    with pytest.raises(RuntimeError, match="exclusive tracemalloc ownership"):
        performance_gate._measure_compile(
            performance_gate.build_performance_sources(8),
            trace_peak=True,
        )


def test_threshold_evaluator_accepts_values_at_the_limits() -> None:
    violations = performance_gate._threshold_violations(
        median_seconds=dict(performance_gate.MAX_MEDIAN_SECONDS),
        growth_ratio=performance_gate.MAX_GROWTH_RATIO,
        peak_traced_bytes=performance_gate.MAX_PEAK_TRACED_BYTES,
    )

    assert violations == []


def test_threshold_evaluator_reports_each_limit_independently() -> None:
    medians = {
        source_count: limit + 0.1
        for source_count, limit in performance_gate.MAX_MEDIAN_SECONDS.items()
    }

    violations = performance_gate._threshold_violations(
        median_seconds=medians,
        growth_ratio=performance_gate.MAX_GROWTH_RATIO + 0.1,
        peak_traced_bytes=performance_gate.MAX_PEAK_TRACED_BYTES + 1,
    )

    assert [violation["code"] for violation in violations] == [
        "median_latency_exceeded",
        "median_latency_exceeded",
        "latency_growth_exceeded",
        "peak_traced_memory_exceeded",
    ]


def test_growth_baseline_floor_is_recorded_in_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_gate, "_measure_compile", _fake_measurement)

    report = performance_gate.run_performance_gate()

    assert report["thresholds"]["min_growth_baseline_seconds"] == 0.001


def _fake_measurement(
    sources: list,
    *,
    trace_peak: bool = False,
) -> performance_gate._Measurement:
    source_count = len(sources)
    duration = {
        performance_gate.PERFORMANCE_WARMUP_SOURCES: 0.05,
        128: 0.25,
        256: 1.0,
    }[source_count]
    return performance_gate._Measurement(
        duration_seconds=duration,
        item_count=source_count * 2,
        selected_item_count=source_count,
        peak_traced_bytes=(
            performance_gate.MAX_PEAK_TRACED_BYTES // 2
            if trace_peak
            else None
        ),
    )


def test_report_is_deterministically_shaped_and_self_hashed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(performance_gate, "_measure_compile", _fake_measurement)

    report = performance_gate.run_performance_gate()
    claimed_digest = report.pop("report_sha256")
    canonical = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

    assert report["schema"] == performance_gate.PERFORMANCE_REPORT_SCHEMA
    assert report["profile"] == performance_gate.PERFORMANCE_PROFILE
    assert report["passed"] is True
    assert report["violations"] == []
    assert report["latency_growth_ratio"] == 4.0
    assert claimed_digest == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_cli_writes_report_and_check_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(performance_gate, "_measure_compile", _fake_measurement)
    output = tmp_path / "nested" / "performance.json"

    assert performance_gate.main(["--check", "--json-out", str(output)]) == 0
    stdout_report = json.loads(capsys.readouterr().out)
    file_report = json.loads(output.read_text(encoding="utf-8"))

    assert stdout_report == file_report
    assert file_report["passed"] is True


def test_cli_check_fails_on_threshold_violation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def slow_measurement(
        sources: list,
        *,
        trace_peak: bool = False,
    ) -> performance_gate._Measurement:
        measurement = _fake_measurement(sources, trace_peak=trace_peak)
        if len(sources) == 256 and not trace_peak:
            return performance_gate._Measurement(
                duration_seconds=performance_gate.MAX_MEDIAN_SECONDS[256] + 1,
                item_count=measurement.item_count,
                selected_item_count=measurement.selected_item_count,
            )
        return measurement

    monkeypatch.setattr(performance_gate, "_measure_compile", slow_measurement)

    assert performance_gate.main(["--check"]) == 1
    report = json.loads(capsys.readouterr().out)

    assert report["passed"] is False
    assert {violation["code"] for violation in report["violations"]} == {
        "median_latency_exceeded",
        "latency_growth_exceeded",
    }


def test_cli_internal_error_exits_with_usage_error_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_gate() -> dict:
        raise RuntimeError("injected performance gate failure")

    monkeypatch.setattr(performance_gate, "run_performance_gate", fail_gate)

    with pytest.raises(SystemExit) as raised:
        performance_gate.main(["--check"])

    assert raised.value.code == 2
    assert "injected performance gate failure" in capsys.readouterr().err
