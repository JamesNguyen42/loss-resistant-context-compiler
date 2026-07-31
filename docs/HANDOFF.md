# Project handoff for the next chat

This file is the durable restart context for a new chat or contributor. Read it
with the root [README](../README.md), [TODO](../TODO.md),
[architecture](ARCHITECTURE.md), [benchmark protocol](BENCHMARKING.md), and
[threat model](THREAT_MODEL.md). Read the
[extraction extension contract](EXTENDING_EXTRACTION.md) before adding domain
vocabulary or custom extractors, and [content secret redaction](REDACTION.md)
before changing preprocessing or privacy claims. Read the
[exact local-Qwen phrase protocol](QWEN_PHRASE_EVALUATION.md) before running or
changing the captured-output evaluation. Read
[unique-literal model extraction](LITERAL_MODEL_EXTRACTION.md) before changing
the model response or provenance-derivation contract. Read the
[held-out paired Qwen protocol and result](QWEN_PAIRED_EVALUATION.md) before
changing or interpreting the recorded comparison. Read the
[external comparison protocol](../benchmarks/protocols/external-comparison-v1.md)
and its [strict JSON manifest](../benchmarks/protocols/external-comparison-v1.json)
before changing candidate selection, adapter identity, resources, datasets, or
claim rules. Read [natural-history evidence contracts](NATURAL_HISTORY_EVIDENCE.md)
and [result-blind compatibility records](../benchmarks/compatibility/README.md)
before collecting histories or rerunning an external diagnostic.
Read the separate [OpenHands package README](../integrations/openhands/README.md),
[event and authority map](../integrations/openhands/docs/EVENT_AUTHORITY_MAP.md),
[operator runbook](../integrations/openhands/docs/RUNBOOK.md), and
[release checklist](../integrations/openhands/docs/RELEASE_CHECKLIST.md) before
changing its pin, host mapping, authority, storage transaction, accounting, or
live-readiness status.

## Snapshot

| Field | Value |
| --- | --- |
| Repository | [`JamesNguyen42/loss-resistant-context-compiler`](https://github.com/JamesNguyen42/loss-resistant-context-compiler) |
| Default branch | `main` |
| Package version | `0.1.1a1` |
| Python | 3.11, 3.12, and 3.13 in CI |
| Core runtime dependencies | None outside the Python standard library |
| Validation checkpoints | `94c35cda`: root warning-strict 1,598 passed, 23 skipped, plus 105 passing subtests; `da664387`: exact optional adapter 103 passed, 3 Windows symlink skips; `cded7e96`: evidence verifier 29 passed warning-strict; `2f692484`: exact-archive root 1,598 passed/23 skipped/105 subtests and OpenHands 483 passed/5 skipped/1 retained deselected; `f9ba3de`: all exact-head hosted workflows passed and three package outputs were byte-identical across six automatic lanes; `0f20b8a`: exact seven-wheel OpenHands build inputs, clean provider/a2 harness, and all automatic hosted gates passed; `7915beb`: exact seven-wheel root release inputs and post-acquisition offline build/smoke passed every automatic hosted gate; no local inference |
| Canonical optional boundary | `localai-contracts==0.2.0a2`, protocol/schema `1.0.0`; `context.compile` only; non-inference |
| Recorded benchmark | 32 generated histories, 72 messages each |
| Recorded compiler compression | 32.60x |
| Recorded compiler critical recall | 100% |
| Recorded local certificate | `ISSUED`, scope `local-bundled-only` |
| Novel English diagnostic | 64 cases: 85.3659% precision, 87.5% recall |
| Exact local Qwen phrase evaluation | 64 calls: 5% model-only recall, 96.9231% candidate rejection, 87.5% final recall |
| Unique-literal model extraction | Opt-in; derives only unique exact spans and keeps the coordinate contract unchanged |
| Post-hoc Qwen offset ablation | 0 calls: 63.1579% literal-only precision, 60% recall, 2 final verification failures; not claim-bearing |
| Held-out paired Qwen result | 128 sequential calls; literal model-only P/R/F1 74%/92.5%/82.2222%, coordinate 100%/17.5%/29.7872%; literal final verification failures 4 |
| Common content-secret preprocessing | Opt-in, fixed-detector, offset-preserving, replayable |
| Detached trust manifest | Complete artifact + exact source set + optional archive head; success requires a separately retained digest |
| External protocol | Valid self-hashed draft; 4 screened candidates, 9 explicit blockers, `claim_ready: false` |
| External systems evaluated | No comparative candidate scored; result-blind ACON/AMA-Agent screens and one retained failed ACON diagnostic only |
| Natural-history evidence | Strict synthetic contract fixtures; no collected cohort |
| OpenHands integration | Separate `ctxc-openhands` `0.1.0a2` draft alpha; offline fake-runtime foundation only; live execution and recorded live scenario blocked |
| Installed JSON Schemas | 25 (7 historical ids, 18 current-namespace ids) |
| External 50%-better claim | Not established |
| Downstream task completion evidence | None yet |
| Confirmed fail-closed blockers | Four identified in-process paths closed |

The clean starting baseline for this work was commit
`7a0d4545be839d05161e993e8eecd5b0b03ee311`. The first validated safety,
benchmark, and local-Qwen checkpoint is commit `3ed2f79` on
`codex/harden-verified-memory-evaluation`; later runner and documentation work
may be newer. Always use `git log -1` and `git status -sb` to establish the
exact state.

## Original objective

Build a loss-resistant context compiler for agents that replaces large noisy
histories with typed, provenance-linked working memory.

The requested success criteria are:

1. five to ten times less active context;
2. no loss of explicit constraints or user corrections;
3. higher completion on long coding tasks;
4. exact provenance for every preserved claim;
5. compatibility with any underlying LLM;
6. performance at least 50% better than most related technology under a
   defensible, reproducible definition of “better” and “most.”

The user expects implementation and measured evidence, not a predicted or
simulated product claim. Continue means continue toward those criteria while
keeping the evidence boundary honest.

For the product-level 50% claim, require at least 50% task-failure reduction or
1.5x successful completions per total token or cost against a strict majority
of a preregistered dated comparison set. Memory-fidelity gains remain useful
component evidence but cannot substitute for downstream completion.

## User and repository requirements

- The GitHub repository belongs solely to `JamesNguyen42`.
- Do not add an assistant, model, tool, or service as an author, co-author,
  contributor, or collaborator.
- Use the user’s configured Git identity for commits.
- Preserve unrelated working-tree changes if any appear in future sessions.
- Do not claim external superiority without matched external and downstream
  evidence.
- For current project-side model work, use only the exact local
  `qwen/qwen3.6-35b-a3b@q4_k_m` Q4 build, with one inference slot, no model API,
  no other AI model, and no paid service.
- A failed certificate is a valid result and must not be hidden or reframed as
  success.
- Protected commitments may overflow a budget, but they must never disappear
  silently.
- These identity rules are not standing authorization to commit or push;
  obtain that authority from the current user request.

## Core idea

Ordinary summaries are free-form prose. They can collapse distinctions that
matter to an agent:

- a question can become a fact;
- an obsolete requirement can appear current;
- one corrected clause can accidentally retire a neighboring constraint;
- an exact error can lose a number or path;
- a tool-output prompt injection can become an apparent user instruction;
- a plausible claim can lose the evidence needed to audit it.

This project emits a typed ledger instead. Each `MemoryItem` has a kind, text,
status, priority, confidence, provenance spans, temporal links, and metadata.
The active prompt includes selected state. The JSON artifact retains the full
ledger and integrity data.

## Non-negotiable design decisions

### Typed state, not a prose summary

The supported kinds are:

- `goal`;
- `constraint`;
- `user_correction`;
- `confirmed_fact`;
- `decision`;
- `unresolved`;
- `exact_error`;
- `exact_reference`;
- `discarded_attempt`;
- `progress`;
- `context`.

Goals, constraints, user corrections, unresolved questions, exact errors, and
exact references are protected. An internal item that resolves a protected
question is protected as well.

### Atomic exact provenance

Every item must cite one or more `ProvenanceSpan` values:

- source id;
- Python character offsets `[start, end)`;
- exact quote;
- full quote SHA-256.

Span construction rejects boolean, floating-point, textual, or otherwise
non-integer offsets instead of relying on Python slice coercion. Extraction and
atomic replay share all line boundaries recognized by `str.splitlines()`, not
only LF. Prompt JSON Lines escape raw NEL and Unicode line/paragraph separators
from arbitrary source ids so one item remains one physical line.

Ordinary model-produced claims must equal a complete atomic cited span. Exact
items must equal every cited literal. Broad enclosing spans do not earn
benchmark recall credit for smaller gold atoms.

### Independent deterministic recovery

The optional model extractor is not trusted to find every protected item. The
compiler runs a separate full deterministic `RuleBasedExtractor` by default,
and a protected-only pass defines the independent coverage obligation.

Do not replace this with “the model probably captured it.” Any learned
extractor must remain behind deterministic validation and recovery unless new
evidence justifies a formally reviewed change.

### Fail-closed authority

Only authenticated `user`, `system`, and `developer` records may establish
goals, constraints, or corrections. Tool output may preserve exact failures,
references, and discarded attempts, but it cannot create durable authoritative
state. A host may opt a tool record into confirmed-fact extraction only by
setting the JSON boolean `metadata.trusted_for_state` to `true`.

The compiler does not authenticate roles. Integrations must do that before
constructing `SourceRecord` values.

### Conservative temporal resolution

Recognized corrections supersede linked old items while keeping old state in
the audit ledger. Recognized revocations retire the matched commitment without
inventing replacement state. Clear conflicts remain visible and create an
unresolved item. Ambiguous state should remain unresolved rather than being
resolved optimistically.

### Protected retention beats a soft budget

The selector may drop unprotected state, but protected state bypasses optional
budget selection. If protected content exceeds the requested budget, the
default result exposes overflow. Strict mode fails. Never “fix” overflow by
silently truncating a requirement.

### Verification before prompt rendering

Normal prompt rendering requires a passing verification report.
`CompilationPolicy(verify=False)` is diagnostic-only and produces an explicitly
failed report. The Python `allow_unverified=True` escape hatch must not be used
in an agent execution path.

The completed snapshot is recursively sealed after verification. A canonical
snapshot digest is rechecked before active-item access, prompt rendering, and
artifact serialization, so state different from the verified state is refused.

### Integrity is not authenticity

Hashes detect changes relative to trusted values. They do not prove authorship,
role authenticity, freshness, or freedom from malicious source content. The
archive chain supplies a current head and optional expected-head precondition,
but valid-prefix rollback detection depends on retaining that head outside the
archive. Artifacts and source state need an independent trust anchor in
adversarial storage. `ctxc-trust-manifest-0.1` now standardizes that binding:
creation requires a complete independently replayed artifact, and verification
requires the expected manifest digest supplied outside the file. It remains a
digest-anchor workflow, not a signature, timestamp authority, or key manager.

Source ids, roles, and timestamps are opaque but have fixed structural ceilings
of 1,024, 128, and 256 characters. These bounds apply across direct
`SourceRecord` construction, provenance, model interchange, archives,
artifacts, and redaction reports; they limit derived-field amplification but do
not constitute metadata or PII redaction.

## Closed fail-closed defects

Four defects reproduced against the clean starting baseline are now closed:

- verified memory, nested metadata, provenance, selection, reports, and
  statistics are sealed; a canonical digest detects reflective bypasses before
  rendering;
- built-in full deterministic recovery and protected-only certification are
  created inside every compile and cannot be replaced by custom extractors;
- custom safety extraction is additive, and its failure becomes an explicit
  warning without disabling built-in coverage;
- primary exceptions and wholly unusable model outputs fall back to
  deterministic memory with a warning by default, while
  `fail_on_primary_extractor_error=True` preserves strict abort behavior;
- selecting any superseded item produces `selected_superseded_item` and fails
  both compile-time and independent replay verification.

The corresponding regressions are in `tests/test_p0_safety.py`. These controls
close the known in-process paths; they do not make the package
production-ready or prove semantic completeness.

## Processing pipeline

The current order is:

```text
source construction validates initial hashes
    -> source ordering, uniqueness, and source-set digest preflight
    -> built-in full deterministic recovery extraction
    -> built-in protected-only certification extraction
    -> optional additive custom safety extraction
    -> primary extraction with validation, bounds, and fallback policy
    -> combined candidate/item/provenance ceiling checks
    -> source-set digest integrity recheck
    -> item/provenance-bounded canonicalization and deduplication
    -> work-budgeted correction, revocation, unresolved, and conflict resolution
    -> work-budgeted active-item selection
    -> work-budgeted independent invariant verification
    -> recursive sealing and canonical snapshot digest
    -> complete JSON ledger and/or verified compact typed-memory prompt
    -> optional append-only cold source archive
```

The compiler is synchronous and provider-neutral by default. Passing
`timeout_seconds` runs a materialized, serializable job inside a dedicated
POSIX process group or Windows Job Object, signals the owned POSIX group or
terminates the Windows Job on timeout, and returns only a bounded strict-JSON
artifact reconstructed as a sealed snapshot. A POSIX child that deliberately
leaves the group is outside that boundary, and Linux group-signal success does
not prove that every member accepted the signal. It is deterministic when all
supplied extractors and token counters are deterministic, apart from timestamps
and measured duration.

## Code map

| Path | Responsibility |
| --- | --- |
| `src/context_compiler/models.py` | Enums, immutable and identity-bounded source records, sealable items, frozen reports, policy, rendering, hashes |
| `src/context_compiler/extractors.py` | Rule extraction, constraint atomization, coordinate and unique-literal model adapters, authority checks |
| `src/context_compiler/local_qwen.py` | Exact local Qwen Q4 LM Studio CLI preflight, timeout, and single-slot adapter |
| `src/context_compiler/resolver.py` | Bounded deduplication, corrections, revocations, unresolved closure, conflicts |
| `src/context_compiler/compiler.py` | End-to-end orchestration, expansion limits, recovery, selection, compression accounting |
| `src/context_compiler/isolation.py` | Whole-compile subprocess deadline, strict result transfer, and sealed reconstruction |
| `src/context_compiler/process_tree.py` | POSIX process-group and Windows Job Object ownership/termination |
| `src/context_compiler/verifier.py` | Independent work-bounded coverage, provenance, support, authority, and state checks |
| `src/context_compiler/io.py` | Input decoding, strict artifact shape validation, replay verification |
| `src/context_compiler/limits.py` | Shared source/compilation/artifact byte, work, depth, canonical-size, and collection limits |
| `src/context_compiler/path_safety.py` | Full ancestor-chain validation, safe missing-parent creation, and POSIX parent-descriptor pinning |
| `src/context_compiler/atomic.py` | Ancestor-guarded same-directory replace-or-create UTF-8 transactions and durability helpers |
| `src/context_compiler/file_lock.py` | Ancestor-guarded, single-link persistent advisory-file locking |
| `src/context_compiler/archive.py` | Logically append-only local archive, canonical entry chain, expected-head preconditions, legacy migration, advisory locking, atomic commits, loading, and verification |
| `src/context_compiler/trust.py` | Strict detached artifact/source/archive bindings, bounded loading, external-anchor enforcement, and verification reports |
| `src/context_compiler/artifact_diff.py` | Integrity-gated deterministic artifact comparison and self-hashed diff reports |
| `src/context_compiler/artifact_inspection.py` | Versioned bounded artifact summaries and control-character-safe terminal rendering |
| `src/context_compiler/schema_compatibility.py` | Machine-readable artifact reader/writer window and no-silent-migration policy |
| `src/context_compiler/redaction.py` | Fixed common-secret content detectors, masking policy, immutable result, audit report, and exact replay |
| `src/context_compiler/cli.py` | `ctxc` parsing, atomic output transactions, versioned error/completion diagnostics, and exit codes |
| `src/context_compiler/localai_contracts_adapter.py` | Lazy exact-version canonical adapter, pre/post import origin and installed-tree gate, closed authority projection, direct ContextBundle result, typed request server, and 22-case probe |
| `src/context_compiler/localai_contracts_connector.py` | Optional bounded NDJSON module/console entry point; exits closed when the contracts wheel is absent |
| `scripts/validate_localai_contracts_install.py` | Offline provider-only and exact-contracts clean-wheel lanes, subprocess equivalence, and clean-installed Phase 0 conformance |
| `benchmarks/lrcbench.py` | Corpus generation, baselines, metrics, interchange, bootstrap certificate |
| `benchmarks/json_io.py` | Shared bounded regular-file hashing and strict JSON decoding for benchmark evidence |
| `benchmarks/report_verifier.py` | Bounded strict saved-report verification and deterministic replay |
| `benchmarks/external_protocol.py` | Strict self-hashed external-protocol validation and claim-readiness gate |
| `benchmarks/external_runner.py` | Non-interpreting adapter launch, process limits, validation, and self-hashed run manifests |
| `benchmarks/natural_history.py` | Bounded corpus/annotation/adjudication/split/gold-free/report contract validation |
| `benchmarks/compatibility/` | Result-blind pinned system screens and retained ACON blocker/failure evidence |
| `conformance/` | Dependency-free connector schema/golden/negative validation and in-process/stdio equivalence |
| `integrations/openhands/src/ctxc_openhands/` | Separate exact-pin host guard, closed event/authority mapping, callback poison, atomic binding, SQLite-WAL generations, immutable request ledger, fake runtime, replay/recovery/rehydration, scenario, soak, and CLI |
| `integrations/openhands/compatibility/` | Reviewed OpenHands/SDK/tools/agent-server source, artifact, license, lock, API, and live-blocker identity |
| `integrations/openhands/docs/` | Event-authority map, operator runbook, release checklist, and draft upstream hook RFC |
| `integrations/openhands/demo/` | Offline-only no-network container demonstration; not a live OpenHands demo |
| `benchmarks/performance_gate.py` | Fixed-digest CI compile latency/growth/traced-memory regression gate |
| `benchmarks/qwen_phrase_eval.py` | Sequential exact-Qwen prompt/output capture, model-only/recovery scoring, and offline replay |
| `benchmarks/qwen_literal_ablation.py` | Model-free frozen-output offset ablation, literal replay, self-hashed report, and strict regeneration |
| `benchmarks/qwen_paired_eval.py` | Clean-tree paired coordinate/literal capture, alternating order, comparison metrics, and offline replay |
| `benchmarks/protocols/` | Human-readable and machine-verifiable external comparison protocol; v1 is a valid non-claim-bearing draft with explicit blockers |
| `schemas/` | Eight historical/core artifact contracts plus 17 connector request/response/source/bundle/checkpoint/operation contracts |
| `tests/` | Unit, adversarial, schema, benchmark, tokenizer, and held-out regressions |
| `CHANGELOG.md` | Versioned release notes and the unreleased change ledger |
| `SUPPORT.md` | Runtime, platform, format, installation, and maintenance matrix |
| `docs/RELEASE_POLICY.md` | Stable identities, Semantic Versioning, compatibility, and release gates |
| `MANIFEST.in` | Complete source-distribution inclusion policy |
| `.github/workflows/ci.yml` | Cross-version tests, lint, wheel checks, interchange, benchmark |
| `.github/workflows/openhands-integration.yml` | Isolated Python 3.12/3.13 Linux, Windows, and macOS tests, lint, compile, doctor, build, and clean-install checks without live OpenHands dependencies |

## Public interfaces

### CLI

```console
ctxc compile HISTORY [--format json|prompt]
ctxc verify ARTIFACT HISTORY
ctxc verify ARTIFACT --archive ARCHIVE [--archive-expected-chain-head HEAD]
ctxc inspect ARTIFACT
ctxc inspect ARTIFACT --format text --show-items
ctxc diff BEFORE_ARTIFACT AFTER_ARTIFACT [--summary-only]
ctxc schema [--artifact-version VERSION]
ctxc redact HISTORY -o REDACTED.jsonl --report REPORT.json
ctxc archive append ARCHIVE HISTORY [--expected-chain-head HEAD]
ctxc archive verify ARCHIVE [--expected-chain-head HEAD]
ctxc trust create ARTIFACT HISTORY [-o MANIFEST]
ctxc trust create ARTIFACT --archive ARCHIVE [-o MANIFEST]
ctxc trust verify MANIFEST ARTIFACT HISTORY --expected-manifest-sha256 HASH
ctxc trust verify MANIFEST ARTIFACT --archive ARCHIVE --expected-manifest-sha256 HASH
```

Important compile options:

- `--token-budget`;
- `--minimum-compression`;
- `--strict-budget`;
- `--require-target`;
- `--include-superseded`, audit-only; selecting stale state fails verification
  and verified prompt rendering;
- `--active-only`;
- `--no-recovery`;
- `--archive` and its optional externally retained
  `--archive-expected-chain-head` precondition;
- `verify --archive` consumes the chained archive directly instead of the
  positional JSON/JSONL source path and accepts the same head precondition;
- `trust create` refuses incomplete or replay-failing artifacts, while
  `trust verify` requires the externally retained manifest SHA-256; both
  support direct sources or a hash-chained archive, never both;
- `--max-source-bytes`, `--max-source-records`,
  `--max-source-line-chars`, `--max-source-record-bytes`,
  `--max-total-source-bytes`, and `--max-source-json-depth`;
- `verify`, `inspect`, and `diff` also expose `--max-artifact-bytes`,
  `--max-artifact-line-chars`, `--max-artifact-canonical-bytes`,
  `--max-artifact-json-depth`, `--max-artifact-items`,
  `--max-artifact-selected-items`, `--max-artifact-provenance-spans`, and
  `--max-artifact-verification-issues`;
- `inspect --format text --show-items` exposes bounded provenance, status,
  conflict, and selection details; `--max-display-items`,
  `--max-display-links`, and `--max-text-chars` cap the terminal view;
- every operation accepts `--error-format text|json`; JSON runtime errors use
  the `ctxc-diagnostic-0.1` schema.

`ctxc redact` requires distinct output/report paths, refuses input aliases,
accepts stdin, and exposes a repeatable `--detector` subset plus mask,
per-source, total-finding, per-source-character, and total-character limits.
It changes source content only. The two output files are each atomically
replaced but are not installed as one cross-file transaction.

Exit codes:

- `0`: operation passed;
- `2`: invalid input or policy error;
- `3`: verification failed;
- `4`: `--require-target` was set and the compression target missed.

`python -m benchmarks` has a separate contract and returns `2` when its
certificate is not supported or its candidate input is invalid.

### Python

Primary exported objects:

- `SourceRecord`;
- `ProvenanceSpan`;
- `MemoryItem`;
- `MemoryKind` and `MemoryStatus`;
- `CompilationPolicy`;
- `CompilationMetrics` and `COMPILATION_METRICS_SCHEMA`;
- `CompilationLimits`;
- `CompilationLimitError`;
- `CompilationIsolationError`;
- `ContextCompiler`;
- `CompiledMemory`;
- `MAX_SOURCE_ID_CHARS`, `MAX_SOURCE_ROLE_CHARS`, and
  `MAX_SOURCE_TIMESTAMP_CHARS`;
- `Extractor`;
- `ExtractionResult`;
- `DomainLabelExtractor`;
- `CompositeExtractor`;
- `RuleBasedExtractor`;
- `ModelExtractor`;
- `LiteralModelExtractor`;
- `LmsQwenCompletion`;
- `LocalQwenError`;
- `SourceArchive`;
- `ArchiveReport`, `ARCHIVE_ENTRY_SCHEMA`, `ARCHIVE_REPORT_SCHEMA`,
  `ARCHIVE_GENESIS_SHA256`, and `LEGACY_ARCHIVE_SCHEMA`;
- `SourceLimits`;
- `SourceLimitError`;
- `ArtifactLimits`;
- `ArtifactLimitError`;
- `PathBoundaryError`;
- `SourceEvent`, `ContextBundle`, `IncrementalCompiler`,
  `ExactTokenCounterAdapter`, and `LocalAIConnector`;
- `source_event_to_record`, `decode_connector_request`, and `serve_stdio`;
- `CONNECTOR_PROTOCOL_VERSION`, `CONNECTOR_OPERATIONS`,
  `CONNECTOR_REQUEST_SCHEMA`, `CONNECTOR_RESPONSE_SCHEMA`,
  `CONNECTOR_INSPECTION_SCHEMA`, `SOURCE_EVENT_SCHEMA`,
  `CONTEXT_BUNDLE_SCHEMA`, `INCREMENTAL_CHECKPOINT_SCHEMA`, and
  `RETENTION_CERTIFICATE_WORDING`;
- `RedactionPolicy`;
- `RedactionFinding`;
- `RedactionResult`;
- `RedactionError`;
- `RedactionLimitError`;
- `redact_sources`;
- `verify_redaction_report_hash`;
- `verify_redaction_result`;
- `SECRET_DETECTOR_NAMES`;
- `REDACTION_REPORT_SCHEMA`;
- `REDACTION_VERIFICATION_SCHEMA`;
- `ARTIFACT_DIFF_SCHEMA`;
- `ARTIFACT_INSPECTION_SCHEMA`;
- `ARTIFACT_SCHEMA_COMPATIBILITY_SCHEMA`;
- `artifact_schema_registry`;
- `artifact_schema_support`;
- `diff_artifacts`;
- `summarize_artifact`;
- `render_artifact_text`;
- `validate_artifact_envelope`;
- `TrustManifestError`;
- `TRUST_MANIFEST_SCHEMA` and `TRUST_VERIFICATION_SCHEMA`;
- `create_trust_manifest`;
- `load_trust_manifest` and `load_trust_manifest_path`;
- `trust_manifest_sha256`;
- `validate_trust_manifest`;
- `verify_trust_manifest`;
- `VerificationReport`.


The canonical adapter is intentionally submodule-only:
`context_compiler.__init__` and the exported list above are unchanged.
Importing `context_compiler.localai_contracts_adapter` does not import the
optional dependency; constructing `LocalAIContractsAdapter` requires the exact
reviewed version and protocol. Before initiating optional-package import or
exposing a preloaded root, the adapter requires one unambiguous distribution,
an unset `sys.pycache_prefix`, exact built-in module/spec/source-loader state
bound to the recorded package/initializer, an exact bounded link-free installed
file set, and the reviewed source/resource tree digest. All 35 immutable wheel
`RECORD` rows must appear exactly once: 34 hashed rows with exact URL-safe
hashes, sizes, and installed bytes, plus the `RECORD` self-row with canonical
empty hash/size fields. Required installer rows are the exact pip marker,
exact-wheel PEP 610 archive metadata, and one platform-canonical launcher. An
exact empty `REQUESTED` marker is optional; every other generated row fails
closed. Loader instance overrides and
non-string registry/namespace keys fail closed without invoking their hooks.
Package-local executable bytecode must match compilation of verified source;
external cache prefixes are refused. The complete `RECORD`, file, and origin
gate repeats after import and binds the returned module object and every loaded
contract-module path. Its fixed path-free failure does not imply independent
wheel-archive authentication, an atomic import transaction, or containment of
writable site-packages, code already run by startup/custom-finder/preload
hooks, or arbitrary same-origin module forgery in a compromised process. The
root API and ordinary `ctxc` behavior remain standalone.
Custom token accounting requires both a callback and a stable
`token_counter_id`. Artifact verification must receive the identical callback
and id or fail with `unverifiable_token_counter`.
The CLI has no way to receive a Python callback, so custom-token artifacts must
use `verify_artifact_dict()` through the Python API. `ctxc verify` correctly
fails such artifacts as unverifiable.

`LmsQwenCompletion` is restricted to
`qwen/qwen3.6-35b-a3b@q4_k_m`, verifies Q4 quantization and one loaded
inference slot, uses the API-free LM Studio CLI with catalog fetching disabled,
and enforces a subprocess timeout. On POSIX it retains the waitable leader until
owned process-group signaling finishes, then reaps it without retrying a
reusable numeric group ID; Windows uses a Job Object. See
[Local Qwen integration](LOCAL_QWEN.md).

### Separate `ctxc-openhands` package

The draft-alpha OpenHands integration is its own distribution under
`integrations/openhands/`, with import package `ctxc_openhands` and CLI
`ctxc-openhands`. It requires the core but does not add OpenHands to the core's
dependency graph. Ordinary import of either package loads no OpenHands module;
the host import gate first requires the exact reviewed versions and 23 source
file digests.

Primary integration interfaces include `OpenHandsSession`,
`SQLiteGenerationStore`, the closed event mapper and atomic-pair validator,
the immutable final-request ledger, deterministic semantic projection, and the
offline exact byte tokenizer. The host-specific guard module validates exact
class identity, installs the non-throwing durable callback, refuses unrecorded
or security-bypassing host APIs, and refuses real `run()`/`arun()` because the
final immutable provider request cannot yet be captured, counted, and replayed
exactly.

The CLI surface is:

```console
ctxc-openhands doctor [--require-live]
ctxc-openhands explain --database DATABASE --session-id SESSION
ctxc-openhands replay --ledger LEDGER
ctxc-openhands replay --database DATABASE --session-id SESSION --request-id REQUEST
ctxc-openhands recover --database DATABASE --session-id SESSION [--apply]
ctxc-openhands rehydrate --database DATABASE --session-id SESSION --source-id ID --start START --end END --quote-sha256 SHA256
ctxc-openhands offline-scenario --database NEW_DATABASE --output NEW_REPORT
ctxc-openhands soak --database NEW_DATABASE --events 10000 --compactions 100 --restart-every 10 --output NEW_REPORT
ctxc-openhands fault-campaign --database NEW_DATABASE --schedules 1024 --seed 0x5A17C7C0 --output NEW_REPORT
ctxc-openhands verify-evidence --report REPORT [--database DATABASE]
```

`doctor` exit 0 means offline readiness only. In the recorded environment,
`doctor --require-live` must remain exit 2 with
`hash-pinned-wheelhouse-absent`. The complete reviewed transitive dependency
closure is not present in a local hash-pinned wheelhouse, and no stable public
OpenHands hook exposes both the final immutable request and an exact tokenizer.
Manually installing matching version labels does not clear that blocker.

The exact host map contains 18 top-level event classes. Unknown classes,
kinds, and fields fail closed. Host source and role claims do not authenticate
themselves. An unknown explicit tool with no serialized nested kind stays
generic and cannot receive authority; an unknown serialized nested
action/observation kind fails closed, and a known kind must match the explicit
tool category. Only independently verified event-bound receipts can enable
narrow allowlisted authority paths. Tool calls/results remain atomic. The
callback never blocks host persistence when
it refuses an event, but poisons later ingestion/dispatch until complete exact
EventLog reconciliation.

The local SQLite-WAL store retains immutable source events and advances
invisible candidates through `prepared`, `verified`, and `committed` before one
verified `active` pointer becomes visible. Source-head, parent, and epoch
compare-and-swap checks make activation old-or-new across a crash; rollback
changes visibility without deleting source. Rehydrated exact spans are always
`untrusted-evidence`.

The qualified SQLite boundary is a local, non-cloud-synchronized,
non-symbolic filesystem; network/distributed/cloud-sync semantics are not
qualified. Store schema 2 has no repair or automatic migration. Evidence
producers use exclusive `require_new=True` paths; `explain`, replay, recovery,
rehydration, and evidence verification use `require_existing=True`, so a path
typo cannot initialize a database. Report outputs and backups are likewise
non-overwriting.

The bundled tokenizer and final-request ledger are exact only for the offline
canonical-UTF-8-byte fake protocol. The offline scenario records three forced
compactions, one activation crash/restart, and exact retention of PostgreSQL and
authentication constraints, but its status is
`passed-offline-fake-runtime`; its nested `live_openhands.status` is
`blocked-not-run`. It is not
a live demonstration and does not establish semantic completeness or
superiority. Use the package [runbook](../integrations/openhands/docs/RUNBOOK.md)
and [release checklist](../integrations/openhands/docs/RELEASE_CHECKLIST.md) for
operations and validation.

Scenario, soak, and crash-campaign reports are bounded, canonical,
self-hashed, and bound to a checkpointed SQLite SHA-256 and byte length.
`verify-evidence` can exit 0 only with the exact database and
`json-and-database` scope; omitting `--database` intentionally reports
`passed: false`, scope `json-only`, and exits 2. Producers require absent
WAL/SHM sidecars after the truncating checkpoint, and verification reconciles
retained source/generation/integrity state and request ledgers before rehashing
the original database. Scenario and soak verification additionally requires
the exact one-session inventory, ordered generation lineage and activation
transitions, and report-specific counts and digests. Preserve the JSON and
SQLite file together.

The report records only the producing subcommand argument vector (or an honest
empty vector for a library call) and its parameters; it does not attest the
shell, executable, environment, container, or operator. Scenario/soak retain
`isolation.network_isolation_enforced: false`, and the campaign retains the
equivalent top-level false field. The producers make no network request and use
no paid service, but the Python process retains ordinary network capability
unless externally isolated. External enforcement such as
`docker run --network=none` must be retained separately. Never edit a report
to claim isolation.

One post-freeze local candidate set completed at exclusive paths on 2026-07-27.
Every producer and its then-current database-backed verifier exited 0, stderr
was empty, no WAL/SHM sidecar remained, and every report retained both
`network_isolation_enforced: false` and
`semantic_completeness_claimed: false`:

- scenario: report self-hash
  `accea44b269d364e76549095907351bcf17a29b1d44a917e26fc26ba7c5e0ea0`,
  report-file SHA-256
  `5d6a11f520b835b557ec009e48f2e6234496050383766b2f0ccebf0f51f046dc`,
  SQLite SHA-256
  `81183fa9d47caf656ff44ccb5231148a700182823e4de9ff30c90abbcae67fd2`
  over 159,744 bytes, and verifier self-hash
  `9aeaca00114089ddd66e46584009392934f3d8a00dcfd7bffa244b9e853f0315`;
- 10,000-event/100-compaction soak: report self-hash
  `e080e1a9926c4b844b0edb3324491cfb24533e7589d8a3e46b9ffe72291ac348`,
  report-file SHA-256
  `e48d4754990c2f654508788515c31c9a29d7bfa60fcc04e6da8b4cbc895decbc`,
  SQLite SHA-256
  `ac7344e3ca04d64a7f7cdbf599f398463ef070f04f965cf818caf4cd34700067`
  over 927,645,696 bytes, and verifier self-hash
  `b693b97a29f298c1ebc49ca342c6f50db7ddc482aa8a22dac9eecd61e6d9b0bb`;
- 1,024-schedule campaign: 1,024 injected faults, 1,024 passes, zero
  failures, and six passing abrupt-process cases; report self-hash
  `24c1ad5929b686785aba949c3aa378493e75793c09d8158dd705f658cd4e220a`,
  report-file SHA-256
  `fc612202590c509b3ae037ff2f380d2a6bd05b522e9b63c89185c90afdb0ab0b`,
  SQLite SHA-256
  `083fa6a34beb828b69576e0a745fa2cb94df2ca36589211f9a7a500881ed61ca`
  over 7,163,904 bytes, and verifier self-hash
  `485f555fefe9d53f133bea23695c1ccce2a60ddacfbe25169722d49ee67b60bd`.

Independent review subsequently proved that the historical scenario/soak
verifier did not bind those report claims to the ordered generation rows in
SQLite. Those two listed pairs remain hash-intact historical evidence, but
they have not been rerun, rewritten, or reverified under the strengthened
verifier; their current proof is incomplete. The campaign remains a separately
verified historical pair and was not affected by the ordered-generation
finding, but it was not rerun in this review cycle and is not durably hosted.
New non-overwriting scenario/soak evidence under the strengthened verifier,
campaign evidence under its applicable verifier, and durable hosted retention
remain red gates. Three earlier failed campaign roots and the first failed
clean-install root remain preserved under ignored
`.artifacts` paths and were not deleted, overwritten, or relabeled. Also
consult the
[offline container demo](../integrations/openhands/demo/README.md),
[compatibility policy](../integrations/openhands/compatibility/README.md), and
[draft upstream hook RFC](../integrations/openhands/docs/UPSTREAM_RFC.md) for
operations, validation, and unresolved host requirements.

## Naming and version map

| Identity | Current value |
| --- | --- |
| Project and repository name | Loss-resistant Context Compiler |
| GitHub repository slug | `loss-resistant-context-compiler` |
| Optional canonical connector | `ctxc-localai-contracts` / `python -m context_compiler.localai_contracts_connector` |
| Optional contracts identity | `localai-contracts==0.2.0a2`; protocol/schema `1.0.0`; reviewed wheel SHA-256 `36a02dbc4267402949dddda1da180d800590cc579e0c1ecb022fc96f6a7c29ae` |
| Python distribution | `loss-resistant-context-compiler` |
| Import package | `context_compiler` |
| CLI command | `ctxc` |
| Package version | `0.1.1a1` |
| OpenHands distribution | `ctxc-openhands` (separate package) |
| OpenHands import / CLI | `ctxc_openhands` / `ctxc-openhands` |
| OpenHands integration version | `0.1.0a2` draft alpha |
| Reviewed OpenHands host | `1.8.0` at `bc26df351dd5d833a95131556dbe2da69af82253` |
| Reviewed OpenHands SDK/tools/agent-server | `1.27.0` at `904279edf2df5fa12d7caecc7576f62659b2e2dd` |
| Compiled artifact schema | `1.0` |
| Artifact schema compatibility registry | `ctxc-artifact-schema-compatibility-0.1` |
| Artifact diff schema | `ctxc-artifact-diff-0.1` |
| Artifact inspection schema | `ctxc-artifact-inspection-0.1` |
| Compile completion event schema | `ctxc-event-0.1` |
| CLI diagnostic schema | `ctxc-diagnostic-0.1` |
| Redaction report schema | `ctxc-redaction-report-0.1` |
| Redaction verification schema | `ctxc-redaction-verification-0.1` |
| Detached trust manifest schema | `ctxc-trust-manifest-0.1` |
| Trust verification report schema | `ctxc-trust-verification-0.1` |
| Performance gate report | `ctxc-performance-gate-0.1` |
| LRCBench report version | `lrcbench-0.2` |
| LRCBench corpus schema | `lrcbench-corpus-0.3` |
| LRCBench candidate schema | `lrcbench-candidate-output-0.2` |
| Corpus producer schema | `lrcbench-corpus-producer-0.1` |
| Candidate producer schema | `lrcbench-candidate-producer-0.1` |
| External runner manifest | `lrcbench-external-run-manifest-0.13` |
| External comparison protocol | `lrcbench-external-protocol-0.10` |
| Connector request/response | `ctxc-connector-request-0.1` / `ctxc-connector-response-0.1` |
| Connector source/bundle/checkpoint | `localai-source-event-0.1` / `localai-context-bundle-0.1` / `ctxc-incremental-checkpoint-0.1` |
| Natural-history evidence family | `ctxc-natural-history-*-0.1` contracts; synthetic fixtures only |
| Adapter process-environment evidence | `lrcbench-process-environment-0.1` |
| Installed schema directory | `share/loss-resistant-context-compiler/schemas` |

The previous `lossless-context-compiler` value was unresolved pre-beta
metadata. The stable distribution now matches the truthful repository name.
It is not a compatibility alias: an old editable distribution must be removed
before reinstalling. The import package, CLI, package version, and every stored
schema identity remain unchanged. See [release policy](RELEASE_POLICY.md) and
[support matrix](../SUPPORT.md).

The seven pre-rename JSON Schema `$id` URI values deliberately retain their
historical `lossless-context-compiler` namespace as stored compatibility
identifiers. The new trust schema uses the current
`loss-resistant-context-compiler` namespace. All eight files install under the
current schema directory.

## Input and output contracts

Input accepts:

- a JSON list;
- a JSON object containing `sources`, `events`, or `messages`;
- JSONL;
- stdin through `ctxc compile -` or `ctxc redact -`.

Each record requires:

- `role`;
- `content`;

Each record may additionally contain:

- `id`;
- `sequence` (otherwise the loader uses input order);
- `timestamp`;
- `metadata`.

The complete JSON artifact is the audit format. It contains all items,
selection ids, source count and source-set digest, policy, compression report,
verification report, optional versioned compilation metrics, and artifact
self-hash. New compiles emit `compilation-metrics-0.1` under
`compiler_metadata.metrics` plus the effective `CompilationLimits` under
`compiler_metadata.compilation_limits`; older schema-1.0 artifacts may omit
either additive field.
Independent replay reconciles source, recovery, certification, selection,
status, conflict, protected-budget, and verification counts. Primary-extractor
volume and elapsed time are measured but not independently reproducible. The
artifact does **not** embed the complete source history; verification and
provenance rehydration require the separately retained source events.

The detached trust manifest is a separate, strict 64 KiB document that binds
the complete artifact digest, source-set digest/count, and optional archive
head. Its self-hash becomes meaningful only when the expected digest is
retained outside the bundle's rewrite boundary. See
[TRUST_MANIFESTS.md](TRUST_MANIFESTS.md).

`--active-only` intentionally emits only selected items and marks
`ledger_complete: false`. Independent verification rejects the incomplete
ledger for a full certificate because omitted protected coverage cannot be
audited. Any selected superseded item independently fails verification as
`selected_superseded_item`.

## Current evidence

### Regression and packaging

- At pipe-cleanup checkpoint `94c35cda`, the complete ordinary root suite passed
  under `-W error`: 1,598 passed, 23 platform/optional checks skipped on
  Windows, and 105 subtests passed. Root `testpaths` is `tests`, so this run did
  not select the manual `retained_evidence` OpenHands campaign.
- At ledger-I/O checkpoint `cded7e96`, all 29 warning-strict evidence tests
  passed. Injected `sqlite3.OperationalError` and `OSError` failures during
  final-request ledger replay return one structured red verification instead
  of escaping.
- At adapter checkpoint `da664387`, the separate immutable-a2 adapter lane
  collected 106 tests: 103 passed and three local Windows symlink-privilege
  regressions skipped. It covers exact installed `RECORD`, origin/tree binding,
  shadow/preload/substitution rejection, exact bytecode validation, canonical
  roles, bounded serialization, authority separation, direct ContextBundle
  shape, deterministic projection, binary I/O failures, oversize drain/fatal
  behavior, and in-process/NDJSON equivalence. Eleven distinct
  provider-only/packaging tests also passed.
- `assert_phase0_conformant` reported `passed_count: 22`, `failed_count: 0`,
  `inference_status: not_run`, and
  `observation_scope: connector_transport_conformance`.
- The immutable `localai-contracts==0.2.0a2` wheel is 114,553 bytes with
  SHA-256
  `36a02dbc4267402949dddda1da180d800590cc579e0c1ecb022fc96f6a7c29ae`.
  Its handoff records source commit
  `3858190e8b458847da94e9ed24be83f4928b7d1a`, 35 `RECORD` rows with
  raw SHA-256
  `a20ae81b7cc5dd9e80fc2757d5fea6331f2c232818049026caecf63d48d14076`,
  29 package members equal to Git blobs, and Phase-0 fixture SHA-256
  `458bdd75449c70277d212761f84e6e04a79ddc010b314a2d0a4799801ef1706d`.
  The independently derived installed package-tree digest is
  `296f49a2d7b48158d2d3a33e36b77d5b5c495362cbe3aceaaf8975fb256e538c`.
- The prior a1 wheel
  `3f1cbc1c1079a552304541caa6b7bfbaae926494b67956e3107767ffc980ee41`
  passed its then-current conformance gate but is revoked as final evidence
  after central transport framing and I/O defects. No current acceptance claim
  relies on it.
- The canonical LF `git archive` provider wheel for adapter checkpoint
  `da664387` is 222,661 bytes with SHA-256
  `4366b4da11f85643be8f1a639dce1df495165a0c6af70f297579345e0465572d`.
  The Windows checkout-materialized counterpart is 222,718 bytes with SHA-256
  `f356ab0280f07ab3ac60a472cc80614bf273754b7fb8092b71a3298514431d9b`.
  The latter is not exact-commit/source-archive-byte evidence; neither artifact
  is tracked or published.
- Exact provider reconciliation at implementation head
  `0f20b8a1c131fe3c0908f7d6738790529f42338c`, tree
  `f04dcf9a0dbe58d91d87856b3a9d4fd6de0293ca`, used two independent
  Git-archive materializations. Their 222,688-byte provider wheels matched at
  SHA-256
  `bda1b1c50fea351eaf1241e62e8a1dde56de5dc4963d8d04a91e21e5eb7fa993`;
  raw `RECORD` SHA-256 is
  `485f77359cfc7f8def4c1b47f73e130784ca7beb17baff2ae270792a8726dfc1`.
  Provider code/schema members did not drift from the reviewed optional
  connector. The direct a2 clean install passed Phase 0 22/22 with
  `inference_status: not_run`, and the same clean interpreter ran the component
  harness with direct `ContextBundle` output. Component-manifest and canonical
  payload digests are
  `d88ba3e2b98ca0f077b02ffb6696f3728ee24ad77972bedd16a229309052b42c`
  and
  `f83cc1d120e174189332f9f6131b3ac4878d95a740d938976cc20846142bdd45`.
  The path-neutral fixed provider witness v2 is ignored/local only: 1,058 bytes,
  SHA-256
  `555495a9621798c962129beab1159aa4d573c31a173554738a8253c0b0ceca68`.
  It is not tracked, published, durably retained, or release evidence. Its
  commit-epoch wheel differs from the hosted fixed-epoch root wheel below. The
  first Windows build from the long synchronized workspace path failed while
  creating a nested schema destination and produced no wheel; the short-root
  success does not replace or relabel it.
- An earlier pre-RECORD-gate local checkpoint produced an 885,292-byte sdist
  with SHA-256
  `08140acb63e31083efdc41eb1b4274e423c12d8e9c1ea55ebe737d478d636ab4`.
  An offline `--no-index --no-deps --no-build-isolation` wheel build from that
  sdist reproduced the direct wheel byte-for-byte at
  `ee36885fa45c6ba763732fdc222634b38bad4352257ab4d66040344766d1a62b`;
  that historical identical wheel passed its then-current clean-install lane.
  It is not current a2 RECORD-gate evidence. The build used the already
  reviewed local environment and is not a hash-pinned clean build-tool closure.
  The later root `7915beb` automatic lane closes its separate seven-wheel
  builder and post-acquisition offline smoke path, but does not retroactively
  qualify this historical artifact or close OpenHands live dependencies.
- Historical f590 provider provenance remains distinct: a Windows
  checkout-materialized wheel was 209,860 bytes with SHA-256
  `0d46899e8cf4c8eddf051137a9cae0a6036aa73da24b228d5ee52cc67a42e80b`,
  while two independent fresh `git archive` builds of
  `f59082d82053eb5a25fdfdd6303aa6b527bf70e5` matched at 209,806 bytes
  with SHA-256
  `9965d16b888f17fd1624da7606db975fb7d74b82fe0f730bee1a18837b154db7`.
  Neither historical hash is presented as current a2 provider evidence.
- The final offline a2 validator produced the required outcomes in three
  `--no-compile` lanes: provider-only passed; a transitive provider-extra-only
  install lacked exact-wheel `direct_url.json` provenance and failed closed;
  and direct provider-plus-exact-contracts passed. The supported direct lane
  launched
  `[clean-environment sys.executable, "-m",
  "context_compiler.localai_contracts_connector"]`; literal argv tail:
  `-m context_compiler.localai_contracts_connector`. It used
  `PYTHONDONTWRITEBYTECODE=1`, produced no provider/contracts package `.pyc`,
  matched the in-process bundle digest, and passed the clean-installed
  22-case gate.
- Earlier clean-install failures remain failures: the original provider-only
  stderr check assumed LF on Windows; the strengthened report check observed
  CRLF; a hand-applied fix omitted the child report-write line; and the first
  a2 inspection install generated path-dependent bytecode that failed exact
  source/bytecode validation. The accepted a2 recipe uses no-compile rather
  than weakening bytecode verification.
- The first current full-suite run failed with one Windows sharing violation
  while removing a reaped worker's `job.pickle` (1,492 passed, 21 skipped).
  The same node failed in isolation. Commit `258066c` added a two-second retry
  only for Windows sharing errors while retaining persistent failures; three
  focused tests and the complete deadline module then passed 65 with three
  platform skips.
- A second full run remains failed because strict executable hashing observed
  the active `.venv` launcher change (1,494 passed, 21 skipped). Its exact node
  passed in isolation. A bundled-runtime run from the synchronized workspace
  remains failed with one failure and 12 dependent errors because corpus ctime
  changed during strict reads. The exact tracked-file copy outside that
  metadata-changing boundary produced the complete green result above.
- No local model, inference endpoint, or user-owned runtime was loaded, called,
  reconfigured, stopped, or otherwise touched. The global inference lease was
  not granted.
- The current parent-archive bounded ordinary integration selector selected 480
  ordinary node IDs: 475 passed and five Windows symlink-privilege checks
  skipped. The one manual retained-evidence node for the real 1,024-schedule
  campaign was explicitly deselected, not reported as a pass.
- Ruff and `compileall` pass across `src`, `tests`, `benchmarks`, `scripts`,
  `conformance`, and `_ctxc_build_backend.py`.
- Complete Ubuntu CI covers Python 3.11, 3.12, and 3.13; Windows and macOS run
  Python 3.13 filesystem, external-runner/process-deadline/exact-Qwen transport
  regressions, and clean wheel/sdist release smoke.
- Distribution metadata, `ctxc`, package version, and the installed schema path
  are regression-tested. CI verifies all 25 wheel schemas, source-distribution
  conformance/natural/compatibility assets, and separate clean installs with
  `ctxc --help`, compile, trust-create, and trust-verify round trips.
- CI runs connector golden/negative conformance, natural-history contract
  validation, external-candidate interchange, and external protocol checks.
- At root implementation head `7915beb`, Core CI acquires the seven exact
  universal wheels authorized by the root `requirements-build.lock`, validates
  their inventory, and installs a dedicated no-index builder. It builds wheel
  and deterministic sdist outputs from two clean checkouts with a fixed
  timestamp/hash seed, retains the exact inputs and Python/tool inventory, and
  requires the separate comparator to accept both archives byte-for-byte. This
  is post-acquisition offline, hash-bound, same-job-toolchain repeatability, not
  cross-toolchain or independent reproduction, index-origin authentication, or
  durable retention.
- The historical direct-Setuptools `ctxc-openhands` candidate wheels were
  byte-identical at
  `8df8d4a0890daf149461205293d308329212a5c107c25ee4d1f0d068a2d88db1`,
  but its repeated sdists differed because Setuptools varied gzip/member
  timestamps across 17 generated members. Integration-sdist reproducibility
  remained failed at that checkpoint; the comparison is still retained as a
  failure.
- Initial wrapper head `cf8b8d3911ef776ca015856f8df19ac47dae0628`
  passed its two-source-copy comparison and all automatic hosted jobs, but
  independent review found the missing extracted-sdist fixed point. The local
  first build was 231,581 bytes at
  `b6f4ed61459b5ad9ffb1eb49428ca69be139ce865f7a01f9278ef7df4bffc004`;
  its rebuild was 231,588 bytes at
  `873c1e5959f924a71fbaafb8d7a1a8133bc7c64f90210e9bc1675045eb37196e`.
  Only `src/ctxc_openhands.egg-info/SOURCES.txt` differed because generated
  `setup.cfg` became a rebuild input. The hosted success and recursive local
  failure are both retained, and `cf8b8d3` is not final evidence. Its six
  automatic package jobs passed in push run `30340051113` and pull-request run
  `30340054635`.
- Packaging implementation head
  `2f692484272aa36bf267703cad2bb4d6926676ff` uses the integration-local
  backend, byte-parity guarded against the root backend, only when an explicit
  epoch is present. Under `SOURCE_DATE_EPOCH=1785225894`, CPython 3.12.13,
  pip 25.0.1, build 1.5.0, Setuptools 83.0.0, and wheel 0.47.0, two fresh
  exact Git archives and an extracted-sdist rebuild produced the same
  232,006-byte sdist at
  `9a8f5035d8cbe904dc03142b3be54e3e15fae699153fb7630b4948b7415ac6be`.
  The package-local backend is included in the sdist and excluded from the
  wheel; clean installation from the recursive sdist passed. This closes only
  the recorded same-platform, same-toolchain, same-epoch fixed point.
  Exact head `2f692484` passed all six automatic Linux, Windows, and macOS
  Python 3.12/3.13 package jobs in push run `30341548763` and all six in
  pull-request run `30341552866`; the retained jobs were skipped/default-off.
  Root push CI `30341549065` and pull-request CI `30341552713` each passed
  7/7 jobs; CodeQL `30341552714` and dependency review `30341553236` passed.
- CI enforces `ci-compile-v1` through a self-hashed
  `ctxc-performance-gate-0.1` report: three-trial medians at 128/256 events,
  doubling growth, and a separate exclusive `tracemalloc` peak.
- Gold-free corpus exports carry a canonical `corpus_sha256`.
- Model-output JSON rejects duplicate object keys, non-standard NaN/infinity,
  overflowed non-finite floats, forged fields/roles, invalid spans, reserved
  internal tags, and paraphrases before candidates enter memory.
- Fixed-seed generative regressions cover arbitrary control/Unicode source ids,
  character-vs-byte and invalid direct offsets, all 11 Python line boundaries,
  whitespace, coordinated clause boundaries, prompt JSON-line safety, and
  independent artifact replay.
- A deterministic 222-case extraction grammar corpus crosses labels, five
  bullet forms, four conjunction forms, negated numeric-unit limits, six path
  locator families, seven diagnostic families, and four correction grammars;
  every case checks verified end-to-end output and exact provenance.
- The self-hashed 64-case novel-English diagnostic records 35 true positives,
  6 false positives, and 5 false negatives (85.3659% precision, 87.5% recall)
  with zero compiler-verification failures. Strict report verification replays
  every case. The corpus is local diagnostic evidence, not independent or
  production-representative; see `docs/PHRASE_EVALUATION.md`.
- The exact local-Qwen corpus evaluator was frozen in commit `f79fe2b` before
  its first live result. The clean-tree run completed all 64 sequential calls
  with no transport error. Qwen emitted 65 candidates across all cases; strict
  validation accepted 2 and rejected 63 (96.9231%), yielding 5% model-only
  recall. Recovery added 33 expected atoms missed by the model, raising final
  recall to 87.5% with 85.3659% precision and no verification failures. Median
  call latency was 1.968 seconds; model-service cost was USD 0.00. The
  self-hashed raw-output report replays offline; see
  `docs/QWEN_PHRASE_EVALUATION.md`.
- The local LM Studio chat transport now tolerates only a bounded sequence of
  exact-model loading-status lines before one JSON object. It rejects arbitrary
  prefixes, malformed/non-object JSON, multiple objects, and trailing data
  without echoing captured output. Synthetic fixtures cover the failure shape;
  no completed Qwen report was rerun or rescored.
- Opt-in `LiteralModelExtractor` removes model-supplied offset arithmetic from
  the trust boundary without accepting paraphrases. Its separate strict schema
  accepts exact text and unique source ids, derives Python-character spans only
  for a single exact occurrence in every cited immutable source, applies all
  role/uncertainty/atomicity checks, and caps aggregate locator work. The
  original `ModelExtractor` prompt and frozen report replay remain unchanged;
  one post-hoc public-example smoke compile verified with 10 admitted model
  items, 1 rejection, and 4 recoveries, but retained no replayable raw output.
  See `docs/LITERAL_MODEL_EXTRACTION.md`.
- A deterministic post-hoc ablation strictly verifies the frozen exact-Qwen
  report, discards only the 65 captured coordinate pairs, and replays retained
  candidate text/source ids through `LiteralModelExtractor` with zero model
  calls. It accepted 38/65 candidates and recorded 24 TP, 14 FP, and 16 FN
  before recovery (63.1579% precision, 60% recall). Final recall reached 100%,
  but precision was 66.6667% and two cases failed confirmation-evidence
  verification. The self-hashed report explicitly says the target prompt was
  not evaluated and the analysis is not claim-bearing; see
  `docs/results/qwen-literal-offset-ablation-v1.json`.
- A second, disjoint 64-case corpus and paired evaluator were frozen on
  `b6d7095` before any target result. The retained exact-Qwen run completed all
  128 sequential calls with alternating order, one draw per mode, no retries,
  one slot, no model API, and USD 0.00 model-service cost. Coordinate mode
  recorded 7 TP / 0 FP / 33 FN (100% precision, 17.5% recall, 29.7872% F1);
  literal mode recorded 37 TP / 13 FP / 3 FN (74% precision, 92.5% recall,
  82.2222% F1). Final F1 was 70.2703% versus 76.7677%, but literal mode had
  lower final precision (64.4068% versus 76.4706%) and four
  confirmation-evidence verification failures. One coordinate capture retained
  LM Studio loading-spinner stdout contamination as `invalid_json`. The
  self-hashed report replays offline and CI now checks it; see
  `docs/QWEN_PAIRED_EVALUATION.md`.
- Public `DomainLabelExtractor`, `CompositeExtractor`, `Extractor`, and
  `ExtractionResult` APIs provide bounded exact-label domain packs and strict
  composition without changing the global regex vocabulary. Configuration is
  copied, read-only, capped, authority-gated, exact-span, pickle-safe, and
  artifact-replay tested. Custom-domain completeness remains outside the
  built-in certificate; see `docs/EXTENDING_EXTRACTION.md`.
- Optional `redact_sources()` and `ctxc redact` preprocessing covers nine fixed
  common-secret content forms without caller regexes. It preserves Unicode
  character offsets and every Python line-boundary form, recomputes source
  hashes, incrementally caps direct source iterables, enforces
  character/candidate/finding limits, and emits a strict self-hashed report
  without original content-secret text or hashes. Exact
  rerun verification, detector overlaps, immutable evidence, CLI path aliases,
  and redacted-provenance compilation are tested. Metadata, ids, timestamps,
  arbitrary PII, and unknown formats remain outside scope; see
  `docs/REDACTION.md`.
- Source loaders, direct compilation, independent verification, and archives
  share default-on byte, line, JSON-depth, count, per-record, and aggregate
  canonical-size limits. Adversarial tests cover UTF-8 boundaries, oversized
  paths and lines, deep/ambiguous/non-finite JSON, high-count generators, huge
  tool schemas, binary-looking output, and atomic archive refusal.
- Compilation expansion is separately fail-closed: each extractor has
  item/rejection/canonical-item/auxiliary-byte ceilings; all passes share
  candidate, resolved-item, provenance, and item-work ceilings. Built-in rule
  extraction checks incrementally, and recovery, temporal matching, conflict
  pairs, selection rendering, and verifier pair searches debit one budget.
  Resource violations abort rather than truncate protected state and are
  covered by direct, artifact-shape, verifier, and CLI regressions.
- Serialized source/artifact path loaders additionally require a stable
  regular file, reject symlinks/directories/FIFOs, compare pre-open/open and
  post-read identity/content metadata, request nonblocking/no-follow opens,
  retry bounded atomic replacement races, and reject gzip bytes without
  invoking a decompressor. The shared parent guard rejects linked/reparse
  ancestors, snapshots the complete lexical chain, and pins the exact parent
  descriptor on POSIX; adversarial swaps fail before a result is accepted.
- Artifact loaders, direct replay, `ctxc verify`, and `ctxc inspect` share
  strict raw/canonical byte, line, depth, item/selection, provenance, and issue
  limits. Tests cover BOM/multibyte boundaries, duplicate keys, non-finite
  values, excessive collections, cyclic direct dictionaries, and CLI refusal.
- `ctxc verify --archive` loads the bounded chained source archive directly;
  an optional expected head is enforced before artifact replay, and stale
  anchors produce the standard integrity diagnostic.
- `ctxc inspect` additionally rejects malformed or unsupported envelopes,
  duplicate/missing item-selection references, and stale `artifact_sha256`
  values before reporting explicit shape/schema/self-hash health. Python
  callers can use `validate_artifact_envelope()` for the same bounded check.
- The optional terminal inspector caps items, links, and text length and
  visibly escapes ANSI/control bytes, Unicode format controls, and Unicode
  line/paragraph separators. Tests cover escape injection, bidi controls,
  truncation, conflicts, provenance, selection state, and invalid bounds.
- `ctxc diff` validates both envelopes before emitting a deterministic,
  self-hashed `ctxc-artifact-diff-0.1` report. It separates payload and
  selection changes, summarizes report/metric changes, supports
  `--summary-only`, and explicitly marks incomplete-ledger comparisons.
- `ctxc schema` and the exported compatibility helpers report artifact `1.0`
  as the exact reader/writer window. Unknown well-formed versions receive a
  stable unsupported result, malformed versions fail, and no automatic or
  silent migration path exists. A future explicit migration must preserve the
  origin, require trusted-source replay, record its origin digest/identifier,
  and emit a new digest; see `docs/SCHEMA_COMPATIBILITY.md`.
- New artifacts carry strict `compilation-metrics-0.1` telemetry for item flow,
  post-resolution recovery, conflicts, protected-budget pressure, verification
  outcomes, and compile duration. `ctxc inspect` exposes it; replay rejects
  rehashed deterministic-count forgeries while preserving compatibility with
  older schema-1.0 artifacts that omit metrics or compilation limits.
- `ContextCompiler.compile(..., timeout_seconds=N)` and
  `ctxc compile --compile-timeout-seconds N` isolate a materialized,
  serializable compile in a POSIX process group or Windows Job Object. Tests
  prove ordinary timeout, worker-side source mutation containment, sealed
  success reconstruction, JSON timeout diagnostics, and removal of a spawned
  descendant before its delayed side effect.
- `ctxc compile --event-format jsonl` emits one opt-in `ctxc-event-0.1`
  completion record on stderr. JSON output events bind the emitted full or
  active-only artifact; prompt events bind the corresponding complete audit
  envelope. Events expose exit outcome, rejection/failure counts, recovery,
  verification, compression/budget state, and compile metrics. Default success
  stderr remains empty.
- CLI file outputs use ancestor-guarded, flushed same-directory temporary files
  and atomic replacement. Missing parents are created component by component;
  linked/reparse ancestors and special destinations are refused. POSIX install
  and cleanup are parent-descriptor-relative. Failure-injection tests prove
  pre-replacement `fsync`/replace failures preserve the old file and remove
  temporary files; POSIX tests also preserve existing regular-file modes.
- Archive appends use the same atomic writer under the exclusive lock and
  install a fully validated, bounded, sequence-sorted event log. Canonical
  entry envelopes bind position, prior-entry hash, and the complete source;
  append/verify expose an optional externally retained expected-head check.
  Tests prove field/link/order tampering is rejected, a retained newer head
  detects valid-prefix rollback and blocks stale append, legacy raw-source
  JSONL upgrades on a new append, readers observe the old or complete new
  archive, pre-replacement failures preserve committed history, and temporary
  files are cleaned.
- Archive reads require a stable single-link regular `events.jsonl`, use
  no-follow flags where available, compare pre-open/open identity, and retry a
  bounded atomic-replacement race. Tests reject directories, hard links,
  symlinks, and FIFOs without opening the special target.
- Archive writers contend on a persistent advisory-lock marker rather than its
  existence. Same-process and real subprocess tests prove live-writer timeout
  and automatic lock release after forced process termination. The marker must
  remain a single-link regular file and its parent chain must remain stable.
  Cleanup also preserves a primary archive error if descriptor close
  independently fails.
- Benchmark reports, exported corpora, per-case corpus inputs, and runner
  manifests use atomic commits. Failure injection proves old evidence survives
  failed replacement, temporary files are cleaned, and a manifest creation race
  cannot clobber the competing file.
- New benchmark reports use `lrcbench-report-0.2`, embed producer/run metadata,
  and carry a canonical `report_sha256`. Tests prove metadata changes affect
  the report digest without destabilizing deterministic certificate evidence.
  Version `0.2` binds frozen external protocol/document/dataset identity and
  registered systems; the verifier retains local-only `0.1` replay.
- `python -m benchmarks --verify-report PATH` strictly loads a bounded regular
  file, rejects duplicate/non-finite or structurally unknown JSON, regenerates
  the dataset id, recomputes both report digests, validates run/comparison
  metadata and current external-protocol evidence, and reconciles included
  per-history metrics. Passing verifies internal consistency, not authorship or
  fairness.
- Benchmark report, corpus, candidate, and manifest inputs now share the same
  strict regular-file JSON boundary. It now uses the same ancestor guard and
  rejects pre/open identity changes or in-place content mutation. Tests cover
  duplicate/non-finite values, size/line/depth limits, special files, parent
  substitution, and candidate mutation between hash, validation, and per-case
  aggregation.
- Corpus and candidate envelopes carry separate versioned producer records.
  Corpus producer changes alter `corpus_sha256` but not `dataset_sha256`;
  candidate producer and cases are bound by `candidate_payload_sha256`.
  The bounded runner normalizes legacy producerless adapter output to the
  current envelope and cross-checks retained producer identity against the
  manifest before scoring.
- Opt-in JSON runtime diagnostics have stable resource, timeout, I/O,
  invalid-input, integrity, and policy categories. Default text output and
  stdout behavior remain unchanged.
- The external runner has deterministic fixture coverage for sequential
  per-case execution, retained case failure, Windows Job Object memory
  enforcement, valid output, timeout, output overflow, invalid candidates,
  corpus mutation, digest validation, overwrite refusal, runner-retained
  descriptor evidence despite stream-path replacement, bounded `cap + 1`
  overflow witnesses, and retried Windows cleanup for post-termination sharing
  violations.
- Claim-bearing runner manifests require per-case isolation, an enforced
  platform-appropriate adapter memory limit, an immutable adapter revision, retained
  dependency-lock bytes defining the environment identity, a retained
  command-referenced adapter entrypoint covered by a bounded immutable source
  tree, a bounded name-audited process environment, the exact local Qwen Q4
  model, one inference slot, context and tokenizer ids, retries, zero
  model-service cost, bounded retained network-isolation evidence, and sampled
  inference-service identity/peak-memory evidence; incomplete controls are a
  per-system non-win.
- External scoring requires a strict frozen
  `lrcbench-external-protocol-0.10` manifest. The protocol's set is authoritative,
  and the benchmark rejects a draft, dataset mismatch, unregistered system, or
  adapter, dependency-environment, model, tokenizer, retry, network-evidence,
  or runner-limit mismatch before scoring. Current runner schema
  `lrcbench-external-run-manifest-0.13` freezes immutable adapter/environment
  identity, revalidated dependency-lock, adapter-entrypoint, and bounded
  adapter-source-tree bytes, the resolved runtime executable, the bounded
  name-audited process-environment digest, and the portable per-case-validated
  command contract, exact 8192-context Qwen, one slot, zero retries/service
  cost, retained network-isolation evidence, sampled service
  process/executable/peak-memory accounting, and bounded runner controls
  including enforcement polling cadence. It also reconstructs every one-case
  corpus on reload and verifies ordered-prefix, exact-digest, and
  runner-temporary-path derivation, then reconciles every successful one-case
  candidate self-digest with its raw case in the retained merged candidate.
  The committed v1 protocol is valid but intentionally blocked and not
  claim-ready.
- CI runs a 24-history fail-closed benchmark certificate.
- The performance profile is a broad shared-runner tripwire, not an SLO: it
  excludes source construction, `tracemalloc` is not RSS, and the separate
  10,000-to-1,000,000-event characterization remains open.

### Recorded local benchmark

The reviewed report is
[`docs/results/lrcbench-local.json`](results/lrcbench-local.json).

Configuration:

- 32 histories;
- 72 messages per history;
- 8 noise lines per message;
- 900 active-token budget;
- 5x minimum compression;
- seed `56056`;
- 2,000 paired bootstrap samples;
- bootstrap lower quantile `0.025`;
- dataset SHA-256
  `421d49585ef9ac96fe2a378f79c18da1791e508789ac0290d3cc5018cda07761`.

Compiler result:

- 100% critical-atom recall;
- 100% exact-literal recall;
- 100% provenance validity;
- 100% semantic-support accuracy;
- 100% authority accuracy;
- 0% stale claims;
- 0% unresolved-to-fact promotions;
- 100% perfect histories;
- 32.60x corpus compression.

Bundled extractive control:

- 88.2% history-weighted critical recall;
- 83.6% exact recall;
- 100% provenance validity;
- 100% semantic-support accuracy;
- 0% authority accuracy;
- 100% stale-claim rate;
- 0% perfect histories;
- 30.13x compression.

The local frontier certificate was issued using critical-semantic-loss
reduction. Its scope is `local-bundled-only`. Per-system decisions are recorded
for every bundled baseline; the strict-majority external decision remains
inapplicable because no external comparison set was run.

### What this evidence does not prove

No named external system has produced a scoreable candidate in this repository.
The ACON preflight and bounded runner attempt failed before external execution.
No public downstream long-horizon task suite has been evaluated. The generated templates
overlap the deterministic extractor’s English vocabulary. The default token
estimate is four characters per token rather than a provider tokenizer.

Therefore:

- do not say the project is 50% better than ACON, FoldAgent, AMA-Agent, MemIR,
  or current related technology;
- do not say it improves coding-task completion yet;
- do not generalize the local 32.60x result to natural histories;
- do not call the package production-ready.

Newly generated reports embed the producing commit plus dirty state, package and
schema versions, Python/platform, exact command, UTC timestamp, duration,
tokenizer/model identity, baseline revisions, cost, and failures. The committed
`docs/results/lrcbench-local.json` is the retained local-only
`lrcbench-report-0.1` snapshot and still replays exactly. It does not carry the
new external-protocol field and cannot be repurposed as external-inclusive
evidence.

## LRCBench claim protocol

Terminology:

- a **protected item** is a runtime memory kind that cannot be removed solely
  to meet a budget;
- a **critical atom** is a benchmark gold obligation used to measure retention;
- the sets overlap by design, but benchmark atoms come from case annotations
  and are not runtime `MemoryItem` objects;
- all spans are Unicode/Python character offsets, never UTF-8 byte offsets.

The benchmark contains buried corrections, conflicts, duplicate symbols and
paths, exact numeric failures, unresolved questions, long noise, and
instruction-shaped tool output.

Its certificate requires:

- at least 24 histories in every registered stratum;
- at least 98% compiler critical recall;
- at least 99% exact recall;
- exactly 100% provenance, semantic-support, and authority accuracy;
- zero stale claims, authority violations, unsupported claims, and
  unresolved-to-fact promotions;
- at least 90% perfect histories;
- matched budget compliance;
- at least 5x compression;
- a 50% paired relative-gain point estimate and positive configured lower-
  quantile paired bootstrap margin; the current default quantile is `0.025`.

Critical recall, exact recall, and memory-quality efficiency use the same
history-weighted estimand for point estimates and paired bootstrap samples.
External runs must load a frozen protocol; its complete registered set is
authoritative. Every system gets a separate win, tie, loss, or invalid
decision; a missing registered output is an invalid non-win, and “most”
requires `|W| > |R| / 2`.

Those are current alpha component-certificate gates. The final product gate is
stricter: zero observed protected and exact misses on frozen synthetic and
natural cohorts, real-token compression on every cohort, and at least 50%
task-failure reduction or 1.5x successful completions per total token or cost
against the strict majority. The external protocol must freeze the configured
lower quantile before results are observed.

## Known limitations

### Extraction

- The rule extractor is bounded, heuristic, English-oriented, and not
  semantically complete.
- Novel labels, correction wording, diagnostics, references, or conflict forms
  can be missed.
- The model adapter accepts exact complete source spans, not free-form
  paraphrases.

### Security and privacy

- Roles must be authenticated upstream.
- Common content-secret redaction is explicit, heuristic, and fixed-vocabulary;
  false positives and false negatives remain.
- There is no automatically enabled redaction, metadata/PII redaction,
  encryption, access control, secure deletion, or retention manager.
- Exact provenance can retain anything left in redacted content and always
  retains unmodified ids, roles, timestamps, and metadata.
- Redaction first reads the original source; it does not erase files, memory,
  process arguments, logs, backups, or prior artifacts.
- The redaction report reveals source ids, coordinates, and masked lengths; its
  self-hash is not a signature.
- Local archive append-only behavior is not filesystem-enforced.
- Archive hashes are not signatures; standalone verification accepts a valid
  older prefix, while rollback detection requires a separately protected
  expected head.
- Trust manifests are not signatures or trusted timestamps. If the expected
  manifest digest is kept beside attacker-writable bundle files, the attacker
  can replace and rehash the complete bundle.

### Scale and availability

- Batch compilation reparses the supplied history.
- Source, archive, and compiled-artifact input is bounded by default.
  In-process ledger expansion and major item-pair work are also bounded, but
  the deterministic work unit is not a wall-clock or RSS cap. Whole-compile
  duration has an opt-in isolated deadline, and generic completion callables
  still need a shorter transport deadline to degrade into deterministic
  fallback rather than aborting the whole isolated run.
- Stdout cannot be transactional. Windows has no portable parent-relative
  replacement or parent-directory `fsync`; a privileged rename in the final
  path-syscall window and durability still depend on destination filesystem
  semantics. Full-chain postchecks reject the operation but cannot guarantee
  cleanup of a temporary file stranded in a renamed directory.
- Each archive append rewrites the complete bounded event log, so append time
  and temporary storage are O(archive size).
- Exclusive manifest installation requires same-directory hard-link support;
  unsupported filesystems fail safely without publishing a partial manifest.
- Direct Python callers can allocate oversized objects before the compiler
  gets an opportunity to reject them; configured byte limits do not equal a
  process-RSS guarantee.
- Protected items can exceed a downstream hard context limit.
- Archive locking depends on correct local OS/filesystem advisory-lock
  semantics. The `.append.lock` marker intentionally persists and must not be
  interpreted as evidence that a writer is active.

### OpenHands draft alpha

- The first hosted package-matrix copies for the push and pull-request events
  remain failed results. Every platform exposed a stale symlink-error
  expectation; macOS also exposed its `/var` temporary-root alias when the
  ordinary matrix mistakenly selected the mission-size campaign. The focused
  follow-up marks that exact test `retained_evidence`, proves it is excluded
  from ordinary lanes, qualifies an unlinked `RUNNER_TEMP` descendant, and
  leaves the fail-closed path guard unchanged. Frozen checkpoint
  `4213410efb5c4e857819de3e831260ed2cd9f59a` then passed all 12 ordinary
  Linux, Windows, and macOS Python 3.12/3.13 package jobs in push run
  `30323957135` and pull-request run `30323958753`.
- Exact implementation head `1f684d975005a7e552f62f36dfb7309a58b11799`, including
  the later RECORD, report-to-database, pipe, ledger-I/O, and documentation
  review commits, passed all six automatic Linux, Windows, and macOS Python
  3.12/3.13 package jobs in push run `30331509718` and all six in pull-request
  run `30331512148`.
- Exact packaging head `2f692484272aa36bf267703cad2bb4d6926676ff`
  passed all six automatic package jobs in push run `30341548763` and all six
  in pull-request run `30341552866`. These lanes include two fresh source
  builds, one extracted-sdist rebuild, clean wheel/sdist installation, Ruff,
  compileall, and ordinary test selection on Linux, Windows, and macOS
  Python 3.12/3.13.
- Exact package-gate implementation head
  `f9ba3de6f0ab2ac7e861bd7da907e6949df1339e` added an automatic aggregate over
  the uploaded package files from those six package lanes. OpenHands push run
  `30355357305` and pull-request run `30355359105` each passed six package jobs
  plus the aggregate; retained evidence was skipped/default-off. Root push CI
  `30355357152` and pull-request CI `30355359094` each passed 7/7 jobs; CodeQL
  `30355359110` and dependency review `30355359096` passed. The 16,681-byte
  push report has raw SHA-256
  `e9203177989fab536e70febcf5316ba6ea21d2a39ea2d3f8dc2c46b146a6f743`
  and self-hash
  `9931d25167831dea6698e0794a93e1cc46a1fc23ed29126c94708aefe7efb35c`.
  It binds the exact implementation revision and reports byte-identical root
  wheels
  (`ffc60ecf166cc28563c2a5bc6597e1c4cb0a4c2be6c965d31bafbe3877d1c17c`),
  integration wheels
  (`ed846517d05b9a734754a9d893de84f23d807f9236c626f1791f9f6d215fa3a9`),
  and integration sdists
  (`b8ee2f1f13c06cfe7de1343f9d765eb25aaf6bc1f7d03da7c6443b46eec0594a`)
  across all six lanes. The pull-request report is bound to synthetic merge
  revision `a585ddbea770e49ff23f49b091536a86fa9db295`, whose tree
  `812b43400e7028a4b89ab6ad7aa30eb73aafb536` equals the implementation tree;
  its raw SHA-256/self-hash are
  `9c8ef8530ffa37c6596d94070f75aaed95ab532af3fbf9737870bfa53b08c942`
  and `8ccf05ab81647db1d5030f79ee5e9f367318e923b539b9376cf3e015b04ff2c5`.
  Both report artifacts are temporary and expire on 2026-08-11.
- Exact package-input implementation head
  `0f20b8a1c131fe3c0908f7d6738790529f42338c` tracks a 666-byte
  `requirements-build.lock`, SHA-256
  `243f3ab977d82c04968cf4ea6474b7ef79c060d485aa3a3d67a383d1ef6fbbfe`,
  for seven exact universal build wheels. Every lane acquires only those bytes
  with `--require-hashes`, validates and retains the wheel inventory, and uses
  it without an index for a dedicated builder and both clean-install modes.
  OpenHands push run `30366252700` and pull-request run `30366258656` each
  passed six Linux, Windows, and macOS Python 3.12/3.13 package jobs plus the
  aggregate; retained evidence stayed skipped/default-off. Root CI push
  `30366251022` and pull-request CI `30366255412` each passed 7/7 jobs; CodeQL
  `30366255532` and dependency review `30366255341` passed.
- The six hosted lanes at `0f20b8a` agreed on the 222,688-byte root wheel
  (`ec46f710169a95c21c54a28b941d2f5205104113c3e594fe6945ef68f633642f`),
  136,967-byte integration wheel
  (`16976fa84cebb2b35f1cc15db89a41f016a3a8385498fd2335adbc28c85aacf0`),
  255,770-byte integration sdist
  (`bf51799df63019c3138368a2d6f9e8bc3ddd398163838669340764be36130957`),
  the tracked lock, and all seven input wheels. The 44,203-byte push report raw
  SHA-256/self-hash are
  `46e47ee4b6f3d1b7ce0fdcc5e41e5f812f2ee0d17005d0c77acd986198213acd`
  and `62cd0a5a4ee351dc9fc2c3bd892a79db5394058e96a611c2a47946e0e42ec876`.
  The same-size pull-request report is bound to synthetic merge revision
  `37f7796a1fa74d2637a5ad85e70a9c988ffc5836`, whose tree equals the
  implementation tree; its raw SHA-256/self-hash are
  `4fd96cb025bd557644e670a79c2ae6eeabb244099886d649ab755f7ffb6e8bda`
  and `aba29437bf2ee4ba7d5876eea978bc1bf74cba321ebe047e5ff92acf80e69626`.
  This closes exact build-input byte identity for those automatic temporary
  lanes, not signed origin, durable retention, or a release authorization.
- Exact root release-input implementation head
  `7915beb15f6a3429c24871779c7cdab280d1ee04`, tree
  `5fb61a9f0e9e00016acfd00d04e85e8ef04638f8`, adds the independent root
  666-byte `requirements-build.lock` with the same SHA-256
  `243f3ab977d82c04968cf4ea6474b7ef79c060d485aa3a3d67a383d1ef6fbbfe`.
  The root platform-smoke, repeated-build, and LRCBench jobs acquire the seven
  authorized universal wheels with `--require-hashes`, validate the exact
  distribution/version inventory, create a dedicated no-index builder, and
  use it for their release builds. Platform-smoke and LRCBench pass the
  wheelhouse and lock through wheel/sdist clean-install smoke; the sdist result
  must report `hash-pinned-offline-wheelhouse` and the wheel result reports
  `not-applicable`. Repeated-build instead builds two exact-input candidates
  and requires the strict byte comparator to accept both archives.
- For exact implementation head `7915beb`, root CI push run `30429423660` and
  pull-request run `30429426031` each passed all seven jobs, including Python
  3.11/3.12/3.13, Windows and macOS release smoke, repeated release builds,
  LRCBench, package installs, and the bounded performance gate. OpenHands push
  run `30429423659` and pull-request run `30429426030` also passed; their
  retained-evidence jobs remained manual/default-off. CodeQL `30429426038` and
  dependency review `30429426044` passed. This is exact-head automatic hosted
  evidence only; it does not authenticate the configured index or publishers,
  make temporary Actions retention durable, authorize a release, or establish
  live execution or inference.
- The hosted retained-evidence job is manual/default-off and has not run for
  this candidate; both automatic runs skipped it. Local validation does not
  replace that durable retained-evidence gate.
- The post-freeze scenario and soak are hash-intact historical local artifacts,
  but their earlier verifier did not bind report claims to ordered database
  generations. They are not reverified under the strengthened verifier. The
  campaign remains a separately verified historical pair and was not affected
  by that finding. None of the three is durably retained or current-head
  release evidence.
- The reviewed OpenHands distributions are not available as a complete local
  hash-pinned offline dependency closure. Real offline import and live
  execution remain blocked.
- The historical raw Setuptools sdist failures remain retained. The later
  package-local backend at `2f692484` produced byte-identical final sdists
  across two exact source archives and an extracted-sdist rebuild under one
  recorded Windows toolchain and epoch. At that checkpoint, build tools were
  acquired online without a reviewed hash-pinned input closure. That
  `2f692484` result by itself did not establish cross-platform artifact
  equality; the later `f9ba3de` aggregate closed only equality for its six
  recorded hosted package lanes. The later `0f20b8a` gate closes the exact
  seven-wheel OpenHands build-input bytes for its six recorded lanes. The root
  `7915beb` gate closes the same exact seven-wheel set for its automatic root
  build and smoke paths. Index-origin attestation and durable retention remain
  separate work.
- No candidate SBOM, artifact signature, or provenance attestation exists.
  Checksums and self-hashes are substitution-detection groundwork, not
  authentication or release provenance.
- The pinned host exposes no stable public hook for the final immutable
  provider request plus exact tokenizer. Integrated `run()` and `arun()` always
  refuse; private seams are not treated as wire-exact.
- The crash scenario implementation and deterministic soak use only the offline fake
  runtime. They cannot satisfy the required recorded live scenario.
- The exact tokenizer and final-request digest apply only to the fake canonical
  UTF-8 transport, not a real OpenHands model or provider wire request.
- The closed 18-class event map is valid only for the pinned revisions. An
  unreviewed event or field fails availability closed until a result-blind
  compatibility review updates the pin, map, tests, and documentation.
- Callback recovery depends on the host retaining and supplying the complete
  persisted EventLog in exact order. No filtered or partial reconciliation is
  accepted.
- The SQLite-WAL store assumes a correct local non-symbolic filesystem. It is
  not encrypted, distributed, tamper-proof, or a substitute for host access,
  backup, privacy, and retention controls.

### Evaluation

- The recorded histories are generated templates, not natural production
  prevalence. The separate natural-history records are linked synthetic contract
  fixtures, not collected trajectories or performance evidence.
- The optional model extractor is not exercised in the recorded benchmark.
- The exact local Qwen adapter has one public-example integration diagnostic
  and one replayable 64-case captured-output diagnostic. The model-only path
  had 5% recall and a 96.9231% candidate rejection rate; all 24 semantic
  negatives elicited a candidate. The corpus is locally authored,
  non-independent, English-only, single-message, and not representative of
  production traffic.
- Replaying those already observed outputs through unique-literal validation
  is explicitly post-hoc. The model-free ablation improved literal-only recall
  to 60% but reduced precision to 63.1579%, admitted 14 false positives, and
  left 2 final verification failures. It did not evaluate the new prompt and
  cannot substitute for a newly frozen live evaluation.
- The paired corpus is still locally authored, English-only, and isolated to
  one message per case. The local CLI exposes no sampling seed or temperature,
  so alternating prompt order cannot eliminate one-draw sampling noise. The
  observed literal recall gain came with 13 model-only false positives, a
  37.5% primary negative-case error rate, and four final verification failures;
  it is a tradeoff diagnostic, not evidence of production superiority.
- The transport hardening added after the paired run prevents the observed
  loading-prefix shape from becoming an availability failure, but it cannot
  retroactively change the frozen metrics and does not improve semantic
  selection or typing.
- Local controls are simple and are not state-of-the-art substitutes.
- Atom recall is a proxy for agent success, not task completion.
- Bootstrap intervals do not cover benchmark design bias.
- A malformed provided candidate file currently aborts the run rather than
  producing a per-system invalid aggregate when passed directly. The
  claim-bearing manifest path converts bounded runner failures into retained
  invalid decisions with the manifest hash and reason bound into evidence.
- The bounded runner is not a filesystem sandbox and does not establish network
  isolation. It now binds and revalidates an externally created host firewall,
  container, or network-namespace policy artifact, but that retained file does
  not prove the host enforced it. POSIX `RLIMIT_AS` applies the same inherited
  per-process virtual-address-space ceiling to the adapter and each descendant;
  it is neither physical RSS/footprint accounting nor one aggregate process-tree
  limit, and a usable finite value is host/runtime-map sensitive. Windows Job
  Objects enforce per-process and aggregate job ceilings. Neither contains a
  pre-existing inference service. The runner now
  identifies that service by PID creation token plus executable digest and
  samples Windows working set or Linux/macOS RSS. macOS uses a fixed
  runner-owned `/bin/sh -p` pre-limiter, started with an empty environment, to
  set requested `RLIMIT_AS` soft/hard values before Python starts. The `-p`
  flag selects privileged shell mode and grants no privilege. Limit fields and
  adapter argv remain quoted positional parameters and are never
  shell-interpreted. A bounded canonical anonymous-FD payload carries the exact
  adapter environment with byte-count and SHA-256 verification. The isolated
  Darwin normalizer reserves `__CF_USER_TEXT_ENCODING` as `0x{uid:X}:0:0`
  before bounds and hashing; a conflicting caller value fails closed. This
  counted, hashed entry prevents CoreFoundation's default-text-encoding initializer
  from replacing that environment entry with a host/home-derived value after
  `execve`. Evidence covers the exact mapping passed to `execve`, not
  later mutations by arbitrary runtime code. The no-site (`-I -S`) verifier first
  requires exact inherited `RLIMIT_AS`;
  validates the descriptor, file, and expected size; reads, scrubs, truncates,
  and closes the handoff; validates the retained in-memory length, SHA-256, and
  protocol; applies byte-exact `RLIMIT_FSIZE`; canonically decodes the
  environment; and uses `execve` with literal adapter argv. A pre-shell launch
  failure or shell, pre-verifier, or inexact-`RLIMIT_AS` exit closes the anonymous
  unlinked descriptor through process/context teardown without guaranteeing a
  scrub. Completed scrubbing makes no cryptographic-erasure claim. `preexec_fn`
  is not used, and exact-limit status requires verifier success. Any mismatch
  or setup/`execve` failure remains fail-closed. Preflight and `Popen` failures
  remain blocking runner errors; a post-`Popen` launcher failure is retained in
  a failed, non-scoreable case manifest. On Darwin, a configured limit with
  `process_succeeded: false` conservatively records
  `memory_limit_enforced: false` because the parent has no authenticated
  verifier-completion signal; this may underreport enforcement but cannot
  upgrade the retained failure. A configured limit with
  `process_succeeded: true` requires `memory_limit_enforced: true`. Its unreaped
  leader anchors process-group cleanup. After a successful `SIGTERM`, a Darwin
  permission-denied liveness probe triggers bounded reobservation only of that
  leader through the existing grace deadline; cleanup is accepted only after
  stable bounded `libproc` snapshots prove all members are zombies.
  Cleanup/proof failure after process start is retained as a failed manifest,
  and per-case execution stops before the next case. `libproc` service sampling
  separately rechecks process creation
  identity; this is not a verified jetsam or physical-footprint provider. The
  runner still cannot contain the service, prove the adapter used
  that PID, include separate helper processes, or observe a spike that begins
  and ends between 20 ms polls. The draft protocol still needs to freeze the
  service binary, metric, and ceiling and supports no production claim.

## Safe host-side compaction transaction

The library compiles and verifies memory, but the host decides when old active
context is removed. A safe integration should perform this transaction:

1. authenticate roles, minimize metadata/source ids, and normalize events;
2. run the optional content-secret preprocessor before any model or compiled
   artifact receives the history, then inspect its bounded report;
3. separately remove unsupported sensitive data and apply storage controls;
4. durably persist the retrievable trusted redacted source events and
   separately anchor
   their expected digest;
5. compile a candidate artifact without deleting active history;
6. independently verify the complete artifact against the trusted redacted
   source set;
7. reject or escalate verification failure, strict overflow, missing tokenizer,
   provider failure, or unsupported schema;
8. render the prompt and artifact from the same sealed, digest-checked snapshot;
9. atomically install only that verified prompt and artifact;
10. retain the redacted source archive, redaction report, trust anchor, and a
    rollback pointer;
11. optionally compose a recent uncompressed tail under a separate tested host
   policy;
12. remove old active context only after the verified replacement is durable.

Recent-tail composition, atomic multi-artifact installation, rollback,
retention, redaction-policy selection, unsupported sensitive-data handling,
and trust-anchor storage are host responsibilities. The core package does not
implement this transaction manager. The separate OpenHands alpha implements a
local SQLite-WAL source/generation transaction with verified old-or-new active
visibility and explicit rollback, but it does not supply host authentication,
redaction policy, provider-request capture, or production storage controls.

## Exact next step

The known in-process safety paths, internal benchmark estimands, frozen model
diagnostic, narrow LM Studio stdout framing, and separately packaged OpenHands
offline foundation are complete.

OpenHands live execution still has two hard prerequisites: a complete reviewed
hash-pinned offline dependency closure and a stable public hook for the final
immutable request plus exact tokenizer. Until both exist, keep `run()`/`arun()`
and the recorded live scenario blocked; do not substitute the fake scenario or
private host seams. While that gate remains red, the highest-value broader
project work is the external and natural-history evidence path:

1. preserve every frozen report and the paired report's four verification
   failures without post-result tuning or rescoring;
2. resolve the nine explicit blockers in the machine-readable external
   protocol without looking at comparative results;
3. complete result-blind inclusion decisions and freeze the initial comparison
   set, dependency locks, adapter revisions, source/runtime/command evidence,
   and externally enforced network controls;
4. provide an exact Python 3.11 ACON checkout and lock plus single-slot local
   Qwen/inference-service evidence, then rerun the retained diagnostic without
   hiding a failure;
5. add clean adapters only for included systems and retain every failed run;
6. collect licensed/consented natural histories under the implemented privacy,
   independent-annotation, adjudication, grouped-split, and no-leakage contracts.

The detailed ordered backlog is in [TODO.md](../TODO.md).

## Restart checklist

From a fresh clone:

```console
git status -sb
git log -1 --format=fuller
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests benchmarks scripts conformance _ctxc_build_backend.py
python -m compileall -q src benchmarks tests scripts conformance _ctxc_build_backend.py
python -m pip install -e integrations/openhands --no-deps
python -m pytest -q integrations/openhands/tests
python -m ruff check integrations/openhands/src integrations/openhands/tests integrations/openhands/scripts
python -m compileall -q integrations/openhands/src integrations/openhands/tests integrations/openhands/scripts
python -m ctxc_openhands.cli doctor
python conformance/run_connector_conformance.py
python -m benchmarks.natural_history
python -m pytest -q tests/test_external_compatibility.py
python -m benchmarks --self-test
python -m benchmarks.external_protocol --verify benchmarks/protocols/external-comparison-v1.json
python -m benchmarks --verify-report docs/results/lrcbench-local.json
python -m benchmarks.performance_gate --check --json-out ctxc-performance.json
python -m benchmarks.phrase_eval --verify-report docs/results/novel-english-phrases-v1.json
python -m benchmarks.qwen_phrase_eval --verify-report docs/results/qwen-novel-english-phrases-v1.json
python -m benchmarks.qwen_literal_ablation --verify-report docs/results/qwen-literal-offset-ablation-v1.json
python -m benchmarks.qwen_paired_eval --verify-report docs/results/qwen-heldout-paired-extractors-v1.json
python -m benchmarks --histories 24 --json-out lrcbench-24.json --include-histories
python -c "
import pathlib, subprocess, sys, tempfile, zipfile
with tempfile.TemporaryDirectory() as directory:
    subprocess.run(
        [sys.executable, '-m', 'pip', 'wheel', '.', '--no-deps',
         '--wheel-dir', directory],
        check=True,
    )
    wheels = list(pathlib.Path(directory).glob('*.whl'))
    assert len(wheels) == 1, wheels
    assert wheels[0].name.startswith('loss_resistant_context_compiler-')
    names = zipfile.ZipFile(wheels[0]).namelist()
    assert sum(name.endswith('.schema.json') for name in names) == 25
"
```

Run `python -m ctxc_openhands.cli doctor --require-live` separately and retain
its expected exit 2 and `hash-pinned-wheelhouse-absent` report. Do not make the
restart checklist green by omitting that red live gate, and do not overwrite a
failed scenario, replay, recovery, or doctor report with a later run under the
same evidence path.

To regenerate the reviewed local snapshot intentionally:

```powershell
python -m benchmarks `
  --histories 32 `
  --messages 72 `
  --noise-lines 8 `
  --token-budget 900 `
  --minimum-compression 5 `
  --seed 56056 `
  --bootstrap-samples 2000 `
  --bootstrap-lower-quantile 0.025 `
  --json-out docs/results/lrcbench-local.json `
  --include-histories
```

Do not overwrite the committed snapshot during an exploratory run. Generate a
temporary path first, compare configuration, metrics, and hashes, then update
all duplicated snapshot references in one reviewed change.

Then:

1. read `README.md`, `TODO.md`, and this file;
2. inspect `git diff` before editing;
3. verify the recorded benchmark is still tied to the current code before
   updating its metrics;
4. preserve all fail-closed gates;
5. add a regression test for every integrity or parsing change;
6. distinguish local, external-inclusive, and downstream evidence;
7. commit and push only with the user’s Git identity and attribution.

## Suggested prompt for the next chat

```text
Continue the loss-resistant context compiler from this repository.
First read README.md, TODO.md, docs/HANDOFF.md, docs/ARCHITECTURE.md,
docs/BENCHMARKING.md, docs/THREAT_MODEL.md, docs/REDACTION.md,
docs/LITERAL_MODEL_EXTRACTION.md, integrations/openhands/README.md,
integrations/openhands/docs/EVENT_AUTHORITY_MAP.md, and
integrations/openhands/docs/RUNBOOK.md. Inspect the current branch, diff, tests,
and recorded evidence before changing anything. Keep live OpenHands execution
blocked until the complete hash-pinned offline dependency closure and supported
exact final-request/tokenizer hook both exist; do not call the fake scenario a
live demonstration.
Preserve the fail-closed provenance, authority, protected-retention, privacy-
scope, and claim-boundary rules. Start with the highest-priority incomplete P0
evidence work in TODO.md, validate it empirically, and do not claim external
superiority without the required matched evidence. Keep all GitHub authorship
and commits solely under JamesNguyen42.
```

## Decision log

- Use typed memory rather than ordinary summaries.
- Keep exact source spans and immutable hashes for every item.
- Treat goals, constraints, corrections, unresolved questions, exact errors,
  and exact references as protected.
- Run deterministic recovery even when a model extractor is enabled.
- Keep built-in recovery and protected certification non-replaceable; custom
  safety extraction is additive only.
- Reject model paraphrases from the trusted typed ledger.
- Keep exact-source-literal admission as the model contract; offer
  unique-literal offset derivation as a separate strict schema rather than an
  abstractive exception or silent coordinate repair.
- Treat tool output as untrusted for durable state by default.
- Retain superseded and conflicting state for audit.
- Surface protected overflow instead of silently dropping commitments.
- Refuse normal prompt rendering when verification fails.
- Seal verified snapshots and recheck their canonical digest before rendering.
- Treat provider output as optional by default with explicit deterministic
  fallback warnings; retain an opt-in strict failure policy.
- Keep source ids, roles, and timestamps opaque while enforcing their shared
  fixed character ceilings before they can amplify into derived state.
- Treat superseded selection as audit-only and fail execution verification.
- Keep the runtime provider-neutral and standard-library-only.
- Restrict the included LM Studio adapter to the exact local Qwen Q4 model and
  one inference slot; do not use a model API.
- Use history-weighted memory estimands and explicit per-system majority
  decisions in LRCBench.
- Require a strict frozen external protocol before any external scoring; make
  its registered set authoritative and bind its adapter and dataset identities
  into the current report.
- Use LRCBench certificates as scoped evidence, never universal product claims.
- Require real downstream task completion and external comparisons for the
  original 50%-better target.
- Keep OpenHands integration code and dependencies in the separate
  `ctxc-openhands` package; ordinary core installation and import stay
  dependency-free.
- Pin exact reviewed host bytes and fail closed on unknown top-level events or
  fields; never derive authority from host source/role claims.
- Preserve tool calls/results atomically and poison dispatch after a callback
  refusal until the complete persisted EventLog reconciles exactly.
- Retain source history and make only independently verified SQLite-WAL
  generations active through source-head, parent, and epoch compare-and-swap.
- Label rehydrated exact source as untrusted evidence.
- Claim exact final-request accounting only for the offline fake protocol;
  refuse live OpenHands request execution until the complete pinned dependency
  closure and a stable exact final-request/tokenizer hook both exist.
