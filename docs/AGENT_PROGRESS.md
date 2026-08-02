# Agent progress

Last updated: 2026-08-01 (America/Los_Angeles)

## Resume point

- Branch: `codex/openhands-live-agent-beta`
- Upstream: `origin/codex/openhands-live-agent-beta`
- Base commit for this in-progress checkpoint: `6aba02e`
- Current checkpoint: Linux inference-service identity portability. The code,
  tests, documentation, and release-shaped verification are complete and are
  being committed together.
- Next task: add machine-readable loss auditing and checked golden round trips
  for the optional LocalAI 1.0 `SourceEvent` / `ContextBundle` adapter. Then
  document any operation-level schema-negotiation request that cannot be solved
  safely inside this repository.

## Mission status

1. Linux `/proc/<pid>/exe` portability: complete in the current checkpoint.
2. LocalAI 1.0 contract convergence: adapter and exact-wheel validation already
   exist; conversion loss auditing and shared/private/shared golden round trips
   remain.
3. Public long-horizon suite evidence: no successful external score exists yet.
   Credential-free, source-bound adapters for at least two public suites remain
   required before any external-usefulness claim.
4. Natural-history benchmark, maintainability, and performance work: continue
   after the P0 contract and external-evidence gaps.

## Completed in this checkpoint

- Linux claim-bearing inference-service inspection now classifies missing
  procfs, protected procfs, invisible/out-of-namespace PIDs, unavailable
  executables, identity races, and unsupported platforms.
- Linux executable evidence is hashed from the opened `/proc/<pid>/exe`
  descriptor. The display symlink is not resolved and reopened through the
  runner mount namespace.
- Start-token bracketing plus link-target and inode rechecks reject process
  restart and concurrent `execve` races observed during capture.
- `--expected-inference-service-executable-sha256` can reject a visible numeric
  PID collision when the wrong process uses different executable bytes before
  adapter launch.
- Monitoring reuses process-bound executable evidence and preserves the
  classified inspection reason in preflight and runtime diagnostics while
  retaining stable public failure codes.
- Ordinary external-runner tests use deterministic process fixtures; a marked
  native smoke test alone depends on the host inspection capability.
- Documentation explicitly retains the limits: a PID is namespace-local, two
  processes using identical executable bytes are not distinguished by the new
  digest guard, sampling is not containment, and service accounting may be
  omitted only for non-claim diagnostic runs.

## Verification evidence

All successful commands below used the repository Python 3.12 virtual
environment. Optional-contract checks were run with
`PYTHONDONTWRITEBYTECODE=1` so the independently verified installed package was
not mutated by interpreter cache files.

- `python -m pytest -q`: 2,130 passed, 24 skipped; 2,154 collected; exit 0 in
  273.8 seconds.
- `python -m pytest -q tests/test_external_runner.py`: passed (native test is
  narrowly skipped only when the host reports a classified inspection
  unavailability).
- `python -m ruff check .`: passed.
- `python -m compileall -q src benchmarks tests scripts conformance integrations _ctxc_build_backend.py`:
  passed.
- `python conformance/run_connector_conformance.py`: 17 schemas, 6 golden
  steps, and 66 negative vectors passed.
- `python -m benchmarks --self-test`: passed.
- Checked-in LRCBench, phrase, Qwen phrase, Qwen literal-ablation, and Qwen
  paired-extractor reports all passed their strict `--verify-report` paths.
- Fresh canonical wheel and deterministic sdist builds passed
  `scripts/release_install_smoke.py`; both installed artifacts produced
  byte-identical materialized-context witnesses. The final report contained
  `schema_count: 25` and two passed artifacts.
- A real Linux container successfully captured `/proc` executable evidence and
  accepted the matching expected digest. A hardened container with readable
  `/proc/1/stat` but denied `/proc/1/exe` failed closed as
  `permission_denied` before execution.

## Environment observations and retained evidence

- An initial full-suite attempt exposed `__pycache__` mutation in the optional
  exact-wheel `localai_contracts` installation. The cache was moved, without
  deletion, to ignored
  `.artifacts/localai_contracts-pycache-portability-validation`; the corrected
  no-bytecode full run passed.
- A convenience `python -m build` attempt from this deep OneDrive checkout hit
  the Windows legacy path limit while its frontend rebuilt a wheel from the
  sdist. The repository's documented direct-wheel plus deterministic-sdist CI
  path succeeded, and both resulting artifacts passed isolated install smoke.
  The failed attempt remains under ignored
  `.artifacts/portability-release-smoke-20260801-1`; successful artifacts are
  under ignored `.artifacts/p1`.

## Honest blockers and claim boundary

- No public external long-horizon suite has successfully scored this compiler;
  do not claim external usefulness yet.
- No machine-readable LocalAI conversion-loss audit or checked
  shared/private/shared golden corpus exists yet.
- The optional LocalAI dependency's shared request/response negotiation does
  not currently prove operation-specific `SourceEvent` or `ContextBundle`
  schema negotiation. Do not invent a private protocol extension to hide that
  upstream contract gap.
- Exact model checkpoints, public corpus revisions, inference-service resource
  ceilings, and retained raw outputs must be frozen before a claim-bearing
  external comparison can run.

## Next exact actions

1. Inspect the installed LocalAI 1.0 models and existing adapter field mappings.
2. Define a bounded, deterministic conversion-audit record that reports every
   represented, normalized, defaulted, or rejected field without silently
   widening the shared contract.
3. Add checked SourceEvent and ContextBundle golden round trips plus negative
   vectors against the exact installed wheel.
4. Run the focused adapter tests, full suite, Ruff, compileall, conformance,
   package smoke, and benchmark replay; update this file and commit the next
   coherent checkpoint.
