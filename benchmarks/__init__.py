"""Deterministic LRCBench evaluation and interchange harness."""

from .lrcbench import (
    CANDIDATE_SCHEMA,
    CORPUS_SCHEMA,
    BenchmarkConfig,
    BenchmarkReport,
    ExternalBaselineError,
    corpus_document,
    decode_external_candidate,
    run_benchmark,
    run_interchange_self_test,
)

__all__ = [
    "CANDIDATE_SCHEMA",
    "CORPUS_SCHEMA",
    "BenchmarkConfig",
    "BenchmarkReport",
    "ExternalBaselineError",
    "corpus_document",
    "decode_external_candidate",
    "run_benchmark",
    "run_interchange_self_test",
]
