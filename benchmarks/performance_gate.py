"""Reproducible bounded performance regression gate for the compiler.

The profile is intentionally small enough for ordinary CI.  It is a regression
tripwire, not a claim about production-scale latency or resident memory.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import platform
import statistics
import sys
import time
import tracemalloc
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from context_compiler import CompilationPolicy, ContextCompiler, SourceRecord
from context_compiler.atomic import atomic_write_text

PERFORMANCE_REPORT_SCHEMA = "ctxc-performance-gate-0.1"
PERFORMANCE_PROFILE = "ci-compile-v1"
PERFORMANCE_SOURCE_COUNTS = (128, 256)
PERFORMANCE_TRIALS = 3
PERFORMANCE_WARMUP_SOURCES = 32
PERFORMANCE_WORKLOAD_SHA256 = {
    32: "4035370c53e88faf97e79069d511b147d820998b3ced8edfb82b73940c6f7cae",
    128: "0fe461c8138835be7c76bac8f3300ea1a188dce8459cffd25b5972859c84ed2c",
    256: "4bdbc94593cf936e00986ba2ec8ed7b1b799a15e9f03766cbac35ee53c0e169d",
}

MAX_MEDIAN_SECONDS = {
    128: 2.0,
    256: 8.0,
}
MAX_GROWTH_RATIO = 8.0
MIN_GROWTH_BASELINE_SECONDS = 0.001
MAX_PEAK_TRACED_BYTES = 64 * 1024 * 1024

_TOKEN_BUDGET = 200_000
_MINIMUM_COMPRESSION = 1.0


@dataclass(frozen=True, slots=True)
class _Measurement:
    duration_seconds: float
    item_count: int
    selected_item_count: int
    peak_traced_bytes: int | None = None


def build_performance_sources(count: int) -> list[SourceRecord]:
    """Build the fixed item-dense source prefix used by the CI profile."""

    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError("performance source count must be an integer")
    if count <= 0:
        raise ValueError("performance source count must be positive")
    templates = (
        "constraint: Preserve compatibility marker {i} exactly",
        "goal: Complete deterministic batch {i}",
        "unresolved: Whether worker {i} needs retry",
        "Observed failure: worker {i} returned timeout code {code}",
        "tool output line {i}: " + ("noise " * 30),
    )
    return [
        SourceRecord.create(
            id=f"perf-{index:06d}",
            sequence=index,
            role="user" if index % 5 < 4 else "tool",
            content=templates[index % len(templates)].format(
                i=index,
                code=500 + (index % 10),
            ),
        )
        for index in range(count)
    ]


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _workload_sha256(sources: Sequence[SourceRecord]) -> str:
    return _canonical_sha256([source.to_dict() for source in sources])


def _checked_workload_sha256(sources: list[SourceRecord]) -> str:
    source_count = len(sources)
    try:
        expected = PERFORMANCE_WORKLOAD_SHA256[source_count]
    except KeyError as exc:
        raise RuntimeError(
            f"performance workload has no frozen digest for {source_count} sources"
        ) from exc
    actual = _workload_sha256(sources)
    if actual != expected:
        raise RuntimeError(
            "performance workload digest changed; version the profile and "
            "thresholds together"
        )
    return actual


def _measure_compile(
    sources: list[SourceRecord],
    *,
    trace_peak: bool = False,
) -> _Measurement:
    if trace_peak and tracemalloc.is_tracing():
        raise RuntimeError(
            "performance peak measurement requires exclusive tracemalloc ownership"
        )
    gc.collect()
    policy = CompilationPolicy(
        token_budget=_TOKEN_BUDGET,
        minimum_compression_ratio=_MINIMUM_COMPRESSION,
    )
    if trace_peak:
        tracemalloc.start()
    try:
        started = time.perf_counter()
        memory = ContextCompiler(policy=policy).compile(sources)
        duration = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1] if trace_peak else None
    finally:
        if trace_peak:
            tracemalloc.stop()
    if not memory.verification.passed:
        raise RuntimeError("performance workload did not produce verified memory")
    if not math.isfinite(duration) or duration < 0:
        raise RuntimeError("performance clock returned an invalid duration")
    return _Measurement(
        duration_seconds=duration,
        item_count=len(memory.items),
        selected_item_count=len(memory.selected_item_ids),
        peak_traced_bytes=peak,
    )


def _threshold_violations(
    *,
    median_seconds: dict[int, float],
    growth_ratio: float,
    peak_traced_bytes: int,
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for source_count, limit in MAX_MEDIAN_SECONDS.items():
        observed = median_seconds[source_count]
        if observed > limit:
            violations.append(
                {
                    "code": "median_latency_exceeded",
                    "source_count": source_count,
                    "observed": observed,
                    "limit": limit,
                }
            )
    if growth_ratio > MAX_GROWTH_RATIO:
        violations.append(
            {
                "code": "latency_growth_exceeded",
                "from_source_count": PERFORMANCE_SOURCE_COUNTS[0],
                "to_source_count": PERFORMANCE_SOURCE_COUNTS[-1],
                "observed": growth_ratio,
                "limit": MAX_GROWTH_RATIO,
            }
        )
    if peak_traced_bytes > MAX_PEAK_TRACED_BYTES:
        violations.append(
            {
                "code": "peak_traced_memory_exceeded",
                "source_count": PERFORMANCE_SOURCE_COUNTS[-1],
                "observed": peak_traced_bytes,
                "limit": MAX_PEAK_TRACED_BYTES,
            }
        )
    return violations


def run_performance_gate() -> dict[str, Any]:
    """Measure the frozen CI profile and return a self-hashed report."""

    warmup_sources = build_performance_sources(PERFORMANCE_WARMUP_SOURCES)
    workload_sha256 = {
        str(PERFORMANCE_WARMUP_SOURCES): _checked_workload_sha256(
            warmup_sources
        )
    }
    _measure_compile(warmup_sources)
    results: list[dict[str, Any]] = []
    median_seconds: dict[int, float] = {}
    largest_sources: list[SourceRecord] | None = None

    for source_count in PERFORMANCE_SOURCE_COUNTS:
        sources = build_performance_sources(source_count)
        workload_sha256[str(source_count)] = _checked_workload_sha256(sources)
        measurements = [
            _measure_compile(sources)
            for _ in range(PERFORMANCE_TRIALS)
        ]
        item_counts = {measurement.item_count for measurement in measurements}
        selected_counts = {
            measurement.selected_item_count for measurement in measurements
        }
        if len(item_counts) != 1 or len(selected_counts) != 1:
            raise RuntimeError("performance workload output changed between trials")
        raw_durations = [
            measurement.duration_seconds for measurement in measurements
        ]
        durations = [round(duration, 6) for duration in raw_durations]
        median = round(statistics.median(raw_durations), 6)
        median_seconds[source_count] = median
        results.append(
            {
                "source_count": source_count,
                "trial_seconds": durations,
                "median_seconds": median,
                "item_count": measurements[0].item_count,
                "selected_item_count": measurements[0].selected_item_count,
            }
        )
        largest_sources = sources

    if largest_sources is None:
        raise RuntimeError("performance profile has no source counts")
    traced = _measure_compile(largest_sources, trace_peak=True)
    if traced.peak_traced_bytes is None:
        raise RuntimeError("traced performance measurement did not report a peak")
    if (
        traced.item_count != results[-1]["item_count"]
        or traced.selected_item_count != results[-1]["selected_item_count"]
    ):
        raise RuntimeError("traced performance workload output changed")

    first_median = median_seconds[PERFORMANCE_SOURCE_COUNTS[0]]
    last_median = median_seconds[PERFORMANCE_SOURCE_COUNTS[-1]]
    growth_ratio = round(
        last_median / max(first_median, MIN_GROWTH_BASELINE_SECONDS),
        6,
    )
    violations = _threshold_violations(
        median_seconds=median_seconds,
        growth_ratio=growth_ratio,
        peak_traced_bytes=traced.peak_traced_bytes,
    )
    report: dict[str, Any] = {
        "schema": PERFORMANCE_REPORT_SCHEMA,
        "profile": PERFORMANCE_PROFILE,
        "workload": {
            "source_counts": list(PERFORMANCE_SOURCE_COUNTS),
            "warmup_source_count": PERFORMANCE_WARMUP_SOURCES,
            "trials_per_source_count": PERFORMANCE_TRIALS,
            "workload_sha256_by_source_count": workload_sha256,
            "token_budget": _TOKEN_BUDGET,
            "minimum_compression_ratio": _MINIMUM_COMPRESSION,
        },
        "thresholds": {
            "max_median_seconds": {
                str(key): value for key, value in MAX_MEDIAN_SECONDS.items()
            },
            "max_growth_ratio": MAX_GROWTH_RATIO,
            "min_growth_baseline_seconds": MIN_GROWTH_BASELINE_SECONDS,
            "max_peak_traced_bytes": MAX_PEAK_TRACED_BYTES,
        },
        "environment": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "logical_cpu_count": os.cpu_count(),
        },
        "results": results,
        "latency_growth_ratio": growth_ratio,
        "peak_traced_memory": {
            "source_count": PERFORMANCE_SOURCE_COUNTS[-1],
            "bytes": traced.peak_traced_bytes,
            "scope": "Python allocations observed by tracemalloc during compile",
        },
        "passed": not violations,
        "violations": violations,
    }
    report["report_sha256"] = _canonical_sha256(report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="return nonzero when a committed performance threshold is exceeded",
    )
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    try:
        report = run_performance_gate()
        rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.json_out is not None:
            atomic_write_text(args.json_out, rendered)
        sys.stdout.write(rendered)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    return 1 if args.check and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
