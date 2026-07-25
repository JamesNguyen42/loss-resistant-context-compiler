# Changelog

All notable project changes are recorded here. The project follows the
versioning and release rules in
[docs/RELEASE_POLICY.md](docs/RELEASE_POLICY.md).

## Unreleased

### Changed

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
  65 intentional negative vectors, and a standalone dependency-free
  conformance runner proving in-process/stdio semantic equivalence.
- Six strict natural-history evidence schemas with bounded self-hashed corpus,
  annotation, adjudication, repository/task-group split, gold-free export, and
  report contract fixtures. These fixtures are not a collected natural cohort.
- Result-blind pinned ACON and AMA-Agent compatibility records plus one bounded
  ACON diagnostic adapter attempt retained as a failed, non-scoreable manifest.
- A cross-platform release runway with Windows/macOS smoke installs,
  `SECURITY.md`, `CONTRIBUTING.md`, release checklist, immutable GitHub Action
  pins, dependency review, Dependabot, and supply-chain guidance.

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

## 0.1.0 - 2026-07-23

### Added

- Initial alpha implementation of typed, provenance-linked context compilation,
  deterministic extraction, verification, JSON artifacts, a local source
  archive, CLI commands, and the synthetic LRCBench harness.

This development baseline was not a production or external-superiority claim.
