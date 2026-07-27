# Changelog

All notable project changes are recorded here. The project follows the
versioning and release rules in
[docs/RELEASE_POLICY.md](docs/RELEASE_POLICY.md).

## Unreleased

### Changed

- Closed the connector response error contract over ten stable variants and
  replaced raw exception messages and concrete Python types with fixed public
  messages and normalized connector error types.
- Standardized the Python distribution and installed schema directory on
  `loss-resistant-context-compiler`. The import package remains
  `context_compiler`, the command remains `ctxc`, and artifact schema versions
  are unchanged.
- Made deterministic recovery, protected certification, provider fallback,
  superseded-state handling, compilation expansion, and artifact replay fail
  closed.
- Reworked benchmark certificates around history-weighted estimands,
  per-system decisions, strict-majority gates, retained evidence, and offline
  replay.
- Made archive-lock acquisition retry a post-open descriptor that was replaced
  or unlinked, preserving bounded single-link ownership across platforms.
- Added an opt-in deterministic PEP 517 sdist boundary driven by
  `SOURCE_DATE_EPOCH`, with bounded archive validation and a separate strict
  repeated-build comparator.
- Kept compile, exact-Qwen CLI, and external-runner POSIX leaders waitable until
  owned process-group signaling completes, and required either group
  disappearance or bounded stable all-zombie proof after final macOS
  `SIGKILL` before the leader is reaped.
- Bounded the Darwin transition in which a successfully delivered `SIGTERM` is
  followed by an `EPERM` liveness probe: only the still-owned unreaped leader
  is reobserved through the existing grace deadline, and stable all-zombie
  proof remains mandatory.
- Added an isolated no-site macOS resource-limit launcher, stable `libproc`
  inference-service identity/RSS accounting, and retained non-scoreable
  manifests for post-start external process-group cleanup failures.
- Bound the exact Darwin adapter environment before accounting and handoff,
  including a canonical UID-qualified CoreFoundation text-encoding entry that
  rejects conflicting caller values instead of permitting post-`execve`
  host-derived mutation.
- Bound external stdout/stderr evidence to runner-retained descriptors and a
  finite `cap + 1` prefix witness, preventing path substitution and unbounded
  hashing when a failed cleanup leaves a writer alive.

### Added

- Recursively sealed compiled snapshots with render-time digest checks.
- Shared source, compilation, artifact, model-output, and benchmark-evidence
  resource limits.
- Hash-chained source archives, expected-head rollback checks, persistent
  advisory locks, and crash-safe atomic commits.
- Ancestor-directory guards with POSIX parent-relative access and adversarial
  substitution tests.
- Strict artifact inspection, artifact diffing, schema-compatibility reporting,
  JSON diagnostics, completion events, redaction, and compile deadlines.
- Detached `ctxc-trust-manifest-0.1` creation and verification for complete
  replay-verified artifacts, exact source sets, optional archive heads, and
  separately retained SHA-256 anchors.
- A bounded sequential external-adapter runner and frozen local Qwen diagnostic
  replay paths.
- A fixed-digest performance regression gate and offline wheel/schema checks.
- Seventeen packaged connector JSON Schemas, six-operation golden JSONL,
  66 intentional negative vectors, and a standalone dependency-free
  conformance runner proving in-process/stdio semantic equivalence.
- Six strict natural-history evidence schemas with bounded self-hashed corpus,
  annotation, adjudication, repository/task-group split, gold-free export, and
  report contract fixtures. These fixtures are not a collected natural cohort.
- Result-blind pinned ACON and AMA-Agent compatibility records plus one bounded
  ACON diagnostic adapter attempt retained as a failed, non-scoreable manifest.
- A cross-platform release runway with Windows/macOS smoke installs,
  `SECURITY.md`, `CONTRIBUTING.md`, release checklist, immutable GitHub Action
  pins, dependency review, Dependabot, and supply-chain guidance.

### Migration notes

- Code that mutated a returned `CompiledMemory`, `MemoryItem`, or nested value
  must instead construct new input and recompile. Verified snapshots are now
  recursively sealed, and serialized copies remain untrusted until validated
  and replayed.
- Primary provider failures still use explicit deterministic fallback by
  default. Applications that require provider output must opt into
  `CompilationPolicy(fail_on_primary_extractor_error=True)` and handle the
  exception without rendering partial or unverified state.
- Source, compilation, artifact, and model-output work now has finite defaults.
  Integrations may pass explicit positive limit objects when a documented
  workload needs different bounds; serialized strict-JSON integers longer than
  640 digits are rejected as a resource-limit error.
- Connector bundles must carry structurally valid trusted-memory records.
  `render_context` and `inspect_memory` reject a projection that disagrees with
  its embedded artifact, while `verify_memory` retains its independent
  `trusted_memory_artifact_mismatch` diagnostic for structurally valid input.
- Connector checkpoints, direct source records, and archives can preserve
  authority admitted by the authenticated host. Protect those serialized
  values as host state; their self-hashes do not make them safe bearer
  credentials.

### Security

- Rejected ungrounded or non-atomic model claims, unauthorized state, stale
  superseded execution state, mutable verified output, special-file inputs,
  hard-link aliases where identity matters, and unsafe ancestor paths.
- Prevented detached manifests from reporting trust without an external
  digest anchor, and refused incomplete ledgers, replay failures, malformed
  manifests, archive races, and output aliases.
- Bounded conformance evidence reads, rejected duplicate/non-finite JSON, and
  retained content-relevant file identity checks while tolerating only
  descriptor-local change-time differences on Windows cloud filesystems.
- Prevented post-reap POSIX group-ID reuse during compile and external-run
  cleanup. macOS signal permission ambiguity now fails closed unless stable
  process evidence proves that every remaining group member is a zombie.

## 0.1.0 - 2026-07-23

### Added

- Initial alpha implementation of typed, provenance-linked context compilation,
  deterministic extraction, verification, JSON artifacts, a local source
  archive, CLI commands, and the synthetic LRCBench harness.

This development baseline was not a production or external-superiority claim.
