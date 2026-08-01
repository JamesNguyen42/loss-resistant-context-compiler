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
- Added an opt-in deterministic PEP 517 core-sdist boundary driven by
  `SOURCE_DATE_EPOCH`, with bounded archive validation and a separate strict
  repeated-build comparator.
- Added an integration-local, parity-guarded PEP 517 boundary for
  `ctxc-openhands`. Under one explicit epoch and recorded toolchain, it
  requires two fresh source builds and an extracted-sdist rebuild to produce
  the same final sdist bytes while other hooks continue to delegate to
  Setuptools.
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
- Rebound the optional canonical adapter from revoked
  `localai-contracts==0.2.0a1` transport evidence to the immutable
  `0.2.0a2` wheel. The a1 read-error and oversized-line outcomes remain failed,
  superseded evidence rather than being relabeled.
- Required the optional adapter to validate one exact distribution,
  built-in module/spec/source-loader origins, the bounded link-free 29-member
  package tree, and any executable bytecode before import, repeat the complete
  gate afterward, and reject shadowed, preloaded, substituted, or mutated
  package state.
- Applied a2 `bounded_canonical_bytes` with the adapter's explicit
  `ParseLimits` at every private serialization seam, retained binary-only
  handshake-first NDJSON, and distinguished recoverable bounded oversize
  draining from fatal over-ceiling and real I/O failures.
- Bound the optional a2 install to all 35 immutable wheel `RECORD` rows and a
  closed installer-generated set before and after import. A transitive
  optional-extra install without exact-wheel PEP 610 archive metadata now
  remains intentionally unavailable.
- Cross-bound scenario and soak reports to the bounded ordered SQLite
  generation inventory, lineage, activation transitions, source counts/heads,
  bundle and semantic digests, active pointer, and report-specific arrays.
- Converted SQLite and filesystem failures during evidence final-request ledger
  replay into one structured failed verification, with focused warning-strict
  regressions.
- Closed connector subprocess pipes with `communicate()`. At `94c35cda`, the
  complete ordinary suite passed under warning-strict execution.

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
- A separately imported optional `localai-contracts==0.2.0a2` adapter for
  protocol/schema `1.0.0` that exposes only executed `context.compile`, uses
  the wheel's handshake-first typed server and bounded canonical NDJSON
  transport, and leaves the dependency-free core and private six-operation
  protocol unchanged.
- A deterministic canonical ContextBundle projection with exact UTF-8 source
  spans, explicit trust separation, provenance, omissions/overflow, truthful
  component-grain accounting, a 22-case non-inference conformance gate, and
  provider-only, expected-fail-closed transitive-extra, and direct
  provider-plus-contracts clean-install validation.
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
- Prevented canonical SourceEvent role, trust, or nested metadata from
  authenticating authority. Unverified content remains private untrusted
  history; only an independent host callback may admit exact selected spans to
  trusted memory, and complete rehydrated sources always remain untrusted
  evidence.
- Rejected optional-contract shadow packages, ambiguous distributions,
  distribution/spec/loader path mismatches, loader instance overrides,
  non-string module or namespace keys, external bytecode-cache prefixes,
  linked/reparse tree entries, unexpected installed-tree entries,
  source/resource digest or size mismatches, forged package-local bytecode, and
  post-import origin changes before exposing the canonical adapter. Attribute
  hooks on rejected preloaded modules are not invoked. The gate remains
  installed-environment validation rather than wheel-archive authentication,
  atomic import, or containment of a compromised Python process.

## 0.1.1a6 - 2026-07-31

### Added

- Added the exact `ContextWindowDegradationPolicy` opt-in for Python
  materialization callers. The bounded ladder first preserves the strict
  attempt, then tries a lossless self-describing compact memory rendering, and
  only then makes one bounded memory reallocation while preserving fixed input,
  the current turn, and the configured minimum recent tail.
- Added structural regressions over the unchanged 20-case held-out
  synthetic-naturalistic split plus focused conflict, correction, fixed-input,
  tamper, exact-fit, retry-boundary, and concurrency cases. No model, retrieval,
  or provider is called.

### Changed

- Bound the chosen degradation mode, rung, requested memory budget, effective
  memory budget, and rendering profile inside compiler metadata and every
  downstream artifact digest. Reallocation chooses the smaller exact
  pre-verification strict or compact requirement observed for the original
  partition and increases the budget only to that count. A boundary message can
  move into the compiled prefix during that one final attempt, so the rung name
  does not claim a globally minimal budget over every possible repartition.
- Advanced the separately packaged OpenHands integration to `0.1.0a7` solely
  to preserve its exact dependency on core `0.1.1a6`.

Default materialization and the CLI remain strict and byte-compatible when no
policy is supplied. The frozen evaluation pack, its predeclared expectations,
and its retained red strict report were not changed. Provider execution remains
not ready, a final immutable provider-request recount is still required, and
this change makes no semantic-completeness, retrieval-authority, natural-cohort,
or comparative claim.

## 0.1.1a5 - 2026-07-31

### Added

- Added exact, content-free planning-unit details when strict materialization
  computes an over-budget memory candidate before independent verification.
  The public reason
  remains `compiled_memory_not_verified`; unrelated compile and replay errors
  do not fabricate numeric accounting or partial output.
- Extended the clean-install materialization witness with the verified-memory,
  recent-history, null-retrieval, and current-turn prompt-assembly inputs plus
  the fixed held-out overflow diagnostic. Source, wheel, and sdist probes must
  emit identical canonical witness bytes, and the release report retains their
  independently calculated canonical-line digest.

### Changed

- Advanced the separately packaged OpenHands integration to `0.1.0a6` solely
  to preserve its exact dependency on core `0.1.1a5`.

The compact-projection experiment was not adopted and successful compiled and
materialized payload bytes are unchanged: projection occurs after the strict
compiler budget decision and cannot repair a pre-materialization refusal.
Provider execution remains not ready, a final immutable provider-request
recount is still required, and this change makes no semantic-completeness,
retrieval-authority, or comparative claim.

## 0.1.1a4 - 2026-07-31

### Changed

- Extended `verify_materialized_context_result(...)` to accept the exact
  canonical, bounded UTF-8 JSON-line bytes written by `ctxc materialize -o`,
  while retaining the independent receipt and allocation-plan digest checks.
  Duplicate keys, noncanonical framing or encoding, excessive nesting, and
  oversized serialized results are rejected before result verification.
- Advanced the separately packaged OpenHands integration to `0.1.0a5` solely
  to retain its exact dependency on the new core package identity. No host,
  authority, retrieval, or live-execution behavior changed.
- Recognized the two exact pip-generated `localai-integration` entry-point
  bodies observed across supported Python installers while retaining the
  existing launcher path, interpreter, native stub, archive-shape, `RECORD`,
  and installed-byte checks.
- Required OpenHands clean-install validation to disable pip bytecode
  compilation and to reject core or integration package bytecode both after
  installation and after the import and doctor probes.

The materialized schemas remain v1, provider execution remains not ready, and
a final immutable provider-request recount is still required. This change
makes no semantic-completeness, retrieval-authority, or comparative claim.

## 0.1.1a3 - 2026-07-31

### Added

- Added a dependency-free `ctxc evaluate-materialization` structural
  diagnostic that compares complete raw history, a bounded recent tail, and
  the existing `materialize_context()` result under one fixed Unicode
  code-point planning-unit profile.
- Added one immutable, package-resident set of 30 project-authored
  synthetic-naturalistic coding histories. The grouped split contains 20
  held-out, six development, and four train cases with explicit corrections,
  superseded values, exact identifiers, paths, numbers, recent details, and
  untrusted role-shaped content.
- Added canonical self-hashed reports with exact integer retention,
  accounting, omission, refusal, current-turn, and authority-boundary
  measurements. Report verification requires an independently supplied digest.

### Changed

- Advanced the separately packaged OpenHands integration to `0.1.0a4` solely
  to retain its exact dependency on the new core package identity. No host,
  authority, retrieval, or live-execution behavior changed.

The diagnostic uses no model or retrieval system and does not measure model
answers or task completion. Its packaged gold is visible rather than sealed,
and the histories are project-authored fixtures rather than a collected,
licensed, consented, privacy-reviewed natural cohort. It makes no semantic
completeness, comparative, provider-readiness, or retrieval-authority claim.
Its current deterministic all-split outcome is red: 28 predeclared accepted
cases refuse with `compiled_memory_not_verified`; the two intentional
hard-limit refusals match, and the CLI writes the report before returning 3.

## 0.1.1a2 - 2026-07-31

### Added

- Added the stable, standard-library-only `materialize_context` consumer entry
  point and `ctxc materialize` command. One bounded call now returns the
  existing materialized-context artifact, its existing runtime projection, an
  explicit four-part component manifest, and a receipt that can be checked
  against independently supplied allocation-plan and receipt digests.
- Added a naturalistic offline fixture covering corrected retry policy, exact
  request identifiers, role-shaped untrusted text, recent-message retention,
  current-turn uniqueness, and digest-only omissions.

### Changed

- Exported only the consumer budget, wrapper, verifier, and result schema
  identifiers from the package root. The lower-level prototype and
  materialization types remain module-level provisional APIs, and their v1
  serialized schemas are unchanged.
- Kept external retrieval as an empty, explicitly untrusted host insertion
  point outside LRCC memory. The result remains planning-only, requires a
  final provider recount, and makes no provider-readiness, completeness, or
  comparative claim.
- Advanced the separately packaged OpenHands integration to `0.1.0a3` only to
  retain its exact dependency on this core package identity. Host behavior and
  the live-execution blocker are unchanged.

The CLI's built-in `unicode-codepoint-count-v1` profile reports exact Unicode
code-point planning units, not provider tokens. Provider execution still
requires the host's immutable final-request recount with its exact tokenizer.

This is an archive-qualified alpha package candidate, not a package-index
release or a production-readiness claim.

## 0.1.1a1 - 2026-07-31

### Changed

- Added a distinct prerelease package identity for the accepted huge-history
  compilation optimization. Exact built-in source records now avoid parallel
  ordering lists, canonical source-digest rows are streamed, and bounded
  printable-ASCII JSON accounting uses one bounded update when the complete
  string fits the remaining limit.
- Preserved byte-identical compiler artifacts, prompts, source digests,
  ordering, omission and refusal behavior, replay, public APIs, and all stored
  schema versions. This package identity does not relabel the earlier `0.1.0`
  wheel or widen any completeness or comparative claim.
- Advanced the separately packaged OpenHands integration to `0.1.0a2` solely
  to retain its exact dependency on this core package identity. Its host pin,
  authority mapping, live blocker, and claim boundaries are unchanged.

This is an archive-qualified alpha package candidate, not a package-index
release or a production-readiness claim.

## 0.1.0 - 2026-07-23

### Added

- Initial alpha implementation of typed, provenance-linked context compilation,
  deterministic extraction, verification, JSON artifacts, a local source
  archive, CLI commands, and the synthetic LRCBench harness.

This development baseline was not a production or external-superiority claim.
