# ACON result-blind diagnostic adapter

This adapter targets only ACON revision
`d63f9ae18959dc7215ff62899c94c5e8c56847ae`. It is diagnostic infrastructure,
not a reproduction, inclusion decision, or comparison result.

Run `preflight` first. It refuses a missing/dirty/wrong checkout, a Python
runtime other than 3.11, a missing retained dependency lock, absent external
network-policy evidence, absent service PID/ceiling, or an exact-Qwen LM Studio
preflight failure. These checks do not prove that the host enforced the
network-policy file; the existing external runner retains and hashes that
artifact separately.

If every precondition passes, invoke `run` only through
`python -m benchmarks.external_runner` in per-case mode. The adapter uses ACON's
official `HistoryOptimizer` and prompt, injects the repository's exact local
Qwen CLI transport, and emits a legacy runner-bound candidate with rendered
summary text and no claims. Empty claims intentionally prevent the wrapper from
inventing provenance that ACON does not expose.

The current host blocker is retained in
`benchmarks/compatibility/acon-diagnostic-blocker.json`.
