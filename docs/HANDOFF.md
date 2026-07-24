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
claim rules.

## Snapshot

| Field | Value |
| --- | --- |
| Repository | [`JamesNguyen42/loss-resistant-context-compiler`](https://github.com/JamesNguyen42/loss-resistant-context-compiler) |
| Default branch | `main` |
| Package version | `0.1.0` |
| Python | 3.11, 3.12, and 3.13 in CI |
| Core runtime dependencies | None outside the Python standard library |
| Tests at this snapshot | 899 collected: 893 passing, 6 skipped |
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
| External protocol | Valid self-hashed draft; 4 screened candidates, 9 explicit blockers, `claim_ready: false` |
| External systems evaluated | None |
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
adversarial storage.

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
    -> source-set digest integrity recheck
    -> canonicalization and deduplication
    -> correction, revocation, unresolved, and conflict resolution
    -> budget-aware active-item selection
    -> independent invariant verification
    -> recursive sealing and canonical snapshot digest
    -> complete JSON ledger and/or verified compact typed-memory prompt
    -> optional append-only cold source archive
```

The compiler is synchronous and provider-neutral by default. Passing
`timeout_seconds` runs a materialized, serializable job inside a dedicated
POSIX process group or Windows Job Object, terminates its owned descendant tree
on timeout, and returns only a bounded strict-JSON artifact reconstructed as a
sealed snapshot. It is deterministic when all supplied extractors and token
counters are deterministic, apart from timestamps and measured duration.

## Code map

| Path | Responsibility |
| --- | --- |
| `src/context_compiler/models.py` | Enums, immutable source records, sealable items, frozen reports, policy, rendering, hashes |
| `src/context_compiler/extractors.py` | Rule extraction, constraint atomization, coordinate and unique-literal model adapters, authority checks |
| `src/context_compiler/local_qwen.py` | Exact local Qwen Q4 LM Studio CLI preflight, timeout, and single-slot adapter |
| `src/context_compiler/resolver.py` | Deduplication, corrections, revocations, unresolved closure, conflicts |
| `src/context_compiler/compiler.py` | End-to-end orchestration, recovery, selection, compression accounting |
| `src/context_compiler/isolation.py` | Whole-compile subprocess deadline, strict result transfer, and sealed reconstruction |
| `src/context_compiler/process_tree.py` | POSIX process-group and Windows Job Object ownership/termination |
| `src/context_compiler/verifier.py` | Independent coverage, provenance, support, authority, and state checks |
| `src/context_compiler/io.py` | Input decoding, strict artifact shape validation, replay verification |
| `src/context_compiler/limits.py` | Shared source/artifact byte, line, depth, canonical-size, and collection limits |
| `src/context_compiler/atomic.py` | Shared same-directory replace-or-create UTF-8 transactions and durability helpers |
| `src/context_compiler/file_lock.py` | Cross-platform persistent advisory-file locking |
| `src/context_compiler/archive.py` | Logically append-only local archive, canonical entry chain, expected-head preconditions, legacy migration, advisory locking, atomic commits, loading, and verification |
| `src/context_compiler/artifact_diff.py` | Integrity-gated deterministic artifact comparison and self-hashed diff reports |
| `src/context_compiler/artifact_inspection.py` | Versioned bounded artifact summaries and control-character-safe terminal rendering |
| `src/context_compiler/schema_compatibility.py` | Machine-readable artifact reader/writer window and no-silent-migration policy |
| `src/context_compiler/redaction.py` | Fixed common-secret content detectors, masking policy, immutable result, audit report, and exact replay |
| `src/context_compiler/cli.py` | `ctxc` parsing, atomic output transactions, versioned error/completion diagnostics, and exit codes |
| `benchmarks/lrcbench.py` | Corpus generation, baselines, metrics, interchange, bootstrap certificate |
| `benchmarks/json_io.py` | Shared bounded regular-file hashing and strict JSON decoding for benchmark evidence |
| `benchmarks/report_verifier.py` | Bounded strict saved-report verification and deterministic replay |
| `benchmarks/external_protocol.py` | Strict self-hashed external-protocol validation and claim-readiness gate |
| `benchmarks/external_runner.py` | Shell-free adapter process limits, validation, and self-hashed run manifests |
| `benchmarks/performance_gate.py` | Fixed-digest CI compile latency/growth/traced-memory regression gate |
| `benchmarks/qwen_phrase_eval.py` | Sequential exact-Qwen prompt/output capture, model-only/recovery scoring, and offline replay |
| `benchmarks/qwen_literal_ablation.py` | Model-free frozen-output offset ablation, literal replay, self-hashed report, and strict regeneration |
| `benchmarks/qwen_paired_eval.py` | Clean-tree paired coordinate/literal capture, alternating order, comparison metrics, and offline replay |
| `benchmarks/protocols/` | Human-readable and machine-verifiable external comparison protocol; v1 is a valid non-claim-bearing draft with explicit blockers |
| `schemas/` | Source, coordinate/unique-literal model extraction, compiled artifact, redaction report, and source-archive entry/report contracts |
| `tests/` | Unit, adversarial, schema, benchmark, tokenizer, and held-out regressions |
| `.github/workflows/ci.yml` | Cross-version tests, lint, wheel checks, interchange, benchmark |

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
- `CompilationPolicy`;
- `ContextCompiler`;
- `CompiledMemory`;
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
- `VerificationReport`.

Custom token accounting requires both a callback and a stable
`token_counter_id`. Artifact verification must receive the identical callback
and id or fail with `unverifiable_token_counter`.
The CLI has no way to receive a Python callback, so custom-token artifacts must
use `verify_artifact_dict()` through the Python API. `ctxc verify` correctly
fails such artifacts as unverifiable.

`LmsQwenCompletion` is restricted to
`qwen/qwen3.6-35b-a3b@q4_k_m`, verifies Q4 quantization and one loaded
inference slot, uses the API-free LM Studio CLI with catalog fetching disabled,
and enforces a subprocess timeout. See [Local Qwen integration](LOCAL_QWEN.md).

## Naming and version map

| Identity | Current value |
| --- | --- |
| Project and repository name | Loss-resistant Context Compiler |
| GitHub repository slug | `loss-resistant-context-compiler` |
| Python distribution | `lossless-context-compiler` |
| Import package | `context_compiler` |
| CLI command | `ctxc` |
| Package version | `0.1.0` |
| Compiled artifact schema | `1.0` |
| Artifact schema compatibility registry | `ctxc-artifact-schema-compatibility-0.1` |
| Artifact diff schema | `ctxc-artifact-diff-0.1` |
| Artifact inspection schema | `ctxc-artifact-inspection-0.1` |
| Compile completion event schema | `ctxc-event-0.1` |
| CLI diagnostic schema | `ctxc-diagnostic-0.1` |
| Redaction report schema | `ctxc-redaction-report-0.1` |
| Redaction verification schema | `ctxc-redaction-verification-0.1` |
| Performance gate report | `ctxc-performance-gate-0.1` |
| LRCBench report version | `lrcbench-0.2` |
| LRCBench corpus schema | `lrcbench-corpus-0.3` |
| LRCBench candidate schema | `lrcbench-candidate-output-0.2` |
| Corpus producer schema | `lrcbench-corpus-producer-0.1` |
| Candidate producer schema | `lrcbench-candidate-producer-0.1` |
| External runner manifest | `lrcbench-external-run-manifest-0.13` |
| External comparison protocol | `lrcbench-external-protocol-0.9` |
| Adapter process-environment evidence | `lrcbench-process-environment-0.1` |
| Installed schema directory | `share/lossless-context-compiler/schemas` |

The resistant/lossless distribution-name difference is unresolved and is a
release TODO. Do not rename it casually: package, schema-install,
documentation, and migration compatibility must change together.

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
`compiler_metadata.metrics`; older schema-1.0 artifacts may omit it.
Independent replay reconciles source, recovery, certification, selection,
status, conflict, protected-budget, and verification counts. Primary-extractor
volume and elapsed time are measured but not independently reproducible. The
artifact does **not** embed the complete source history; verification and
provenance rehydration require the separately retained source events.

`--active-only` intentionally emits only selected items and marks
`ledger_complete: false`. Independent verification rejects the incomplete
ledger for a full certificate because omitted protected coverage cannot be
audited. Any selected superseded item independently fails verification as
`selected_superseded_item`.

## Current evidence

### Regression and packaging

- 899 tests are collected: 893 pass and 6 platform/optional checks are skipped.
- Ruff checks pass.
- CI covers Python 3.11, 3.12, and 3.13.
- CI builds a wheel and verifies that all seven schemas are included.
- CI runs the external-candidate interchange self-test.
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
  older schema-1.0 artifacts that omit metrics.
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
- CLI file outputs use flushed same-directory temporary files and atomic
  replacement. Failure-injection tests prove pre-replacement `fsync`/replace
  failures preserve the old file and remove temporary files; POSIX tests also
  preserve existing regular-file modes.
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
  and automatic lock release after forced process termination. Cleanup also
  preserves a primary archive error if descriptor close independently fails.
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
  strict regular-file JSON boundary. Tests cover duplicate/non-finite values,
  size/line/depth limits, special files, and candidate mutation between hash,
  validation, and per-case aggregation.
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
  corpus mutation, digest validation, overwrite refusal, and retried Windows
  cleanup for post-termination sharing violations.
- Claim-bearing runner manifests require per-case isolation, an enforced
  adapter process-tree memory limit, an immutable adapter revision, retained
  dependency-lock bytes defining the environment identity, a retained
  command-referenced adapter entrypoint covered by a bounded immutable source
  tree, a bounded name-audited process environment, the exact local Qwen Q4
  model, one inference slot, context and tokenizer ids, retries, zero
  model-service cost, bounded retained network-isolation evidence, and sampled
  inference-service identity/peak-memory evidence; incomplete controls are a
  per-system non-win.
- External scoring requires a strict frozen
  `lrcbench-external-protocol-0.9` manifest. The protocol's set is authoritative,
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

No named external system has been run through this repository. No public
downstream long-horizon task suite has been evaluated. The generated templates
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

### Scale and availability

- Batch compilation reparses the supplied history.
- Source, archive, and compiled-artifact input is bounded by default.
  Whole-compile duration has an opt-in isolated deadline; direct in-process
  compilation remains uncapped, and generic completion callables still need a
  shorter transport deadline to degrade into deterministic fallback rather
  than aborting the whole isolated run.
- Stdout cannot be transactional, and Windows has no portable parent-directory
  `fsync`; atomic file replacement still depends on destination filesystem
  semantics.
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

### Evaluation

- The recorded histories are generated templates, not natural production
  prevalence.
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
  not prove the host enforced it. POSIX `RLIMIT_AS` and Windows Job Objects
  bound the adapter process tree, but not a pre-existing inference service. The
  runner now identifies that service by PID creation token plus executable
  digest and samples Windows working set or Linux RSS; it cannot contain the
  service, prove the adapter used that PID, include separate helper processes,
  or observe a spike that begins and ends between 20 ms polls. The draft
  protocol still needs to freeze the service binary, metric, and ceiling.

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
and trust-anchor storage are host responsibilities. The package does not
currently implement this transaction manager.

## Exact next step

The known in-process safety paths, internal benchmark estimands, frozen model
diagnostic, and narrow LM Studio stdout framing are complete. The highest-value
next work is the external and natural-history evidence path:

1. preserve the paired report and its four verification failures without
   post-result tuning or rescoring;
2. resolve the nine explicit blockers in the machine-readable external
   protocol without looking at comparative results;
3. complete result-blind inclusion decisions and freeze the initial comparison
   set, dependency locks, adapter revisions, retained source roots and
   entrypoints, runtime executable digests, and portable command contracts;
4. freeze the implemented pre-existing inference-service executable, memory
   metric, and ceiling (or replace monitoring with stronger containment);
5. add the first reproducible, no-paid-service external adapter;
6. define the natural-history privacy, licensing, and annotation protocol.

The detailed ordered backlog is in [TODO.md](../TODO.md).

## Restart checklist

From a fresh clone:

```console
git status -sb
git log -1 --format=fuller
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests benchmarks
python -m compileall -q src benchmarks tests
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
    names = zipfile.ZipFile(wheels[0]).namelist()
    assert sum(name.endswith('.schema.json') for name in names) == 7
"
```

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
docs/BENCHMARKING.md, docs/THREAT_MODEL.md, docs/REDACTION.md, and
docs/LITERAL_MODEL_EXTRACTION.md. Inspect the current branch, diff, tests, and
recorded evidence before changing anything.
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
