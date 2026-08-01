# Loss-resistant Context Compiler

A small, model-agnostic Python library and CLI that turns long agent histories
into typed working memory with exact source provenance.

> **Evidence boundary:** this project includes a deterministic local benchmark
> that can issue a certificate against its bundled head, tail, and extractive
> baselines. That certificate is **not proof** that this project is 50% better
> than ACON, FoldAgent, AMA-Agent, MemIR, or any other external system. An
> external superiority claim requires matched, head-to-head, independently
> reproducible evaluation. See [Benchmarking](docs/BENCHMARKING.md).

The compiler keeps a ledger of goals, constraints, corrections, confirmed
facts, decisions, unresolved questions, exact errors and references, discarded
attempts, progress, and background context. Every item carries one or more
character spans into immutable source records. A separate safety extraction
pass and verifier protect the commitments most costly to lose.

This is an alpha research implementation. “Loss-resistant” means that explicit
invariants are checked and failures are surfaced; it does not mean arbitrary
meaning can be compressed without loss.

## Project status

| Area | Current state |
| --- | --- |
| Release | Alpha research implementation, package version `0.1.1a20` |
| Distribution | `loss-resistant-context-compiler`; import `context_compiler`; CLI `ctxc` |
| Runtime | Python 3.11+, standard-library-only core |
| Interfaces | Python API, `ctxc` CLI, JSON/JSONL input, JSON artifacts, optional LocalAI connector |
| Regression suite | Complete Ubuntu suite on Python 3.11–3.13; Windows/macOS Python 3.13 release/filesystem smoke |
| Content secret preprocessing | Opt-in, fixed-detector, length-preserving, and auditable |
| Local synthetic benchmark | 32.60x compression and 100% critical recall on the recorded run |
| Local bundled certificate | `ISSUED` against head, tail, and extractive controls |
| Novel English diagnostic | 64 local cases; 85.37% precision and 87.5% recall |
| Exact local Qwen phrase evaluation | 64 calls; 5% model-only recall, 96.92% candidate rejection, 87.5% final recall |
| Post-hoc Qwen offset ablation | 0 calls; 63.16% literal-only precision, 60% recall, and 2 final verification failures; non-claim-bearing |
| Held-out paired Qwen result | 128 sequential calls; literal mode raised model-only recall from 17.5% to 92.5%, but reduced precision from 100% to 74% and caused 4 final verification failures |
| External protocol | Strict self-hashed draft with 4 screened candidates and 9 explicit blockers; not claim-ready |
| Named external comparisons | No comparative candidate scored; result-blind ACON/AMA-Agent screening and one failed ACON diagnostic only |
| Natural-history evidence | Strict contract fixtures exist; no collected natural cohort |
| Materialization retention diagnostic | 30 project-authored synthetic-naturalistic structural cases; 20 held out; no model or natural-cohort claim |
| OpenHands integration | Separately packaged `ctxc-openhands` draft alpha; exact current version is owned by its [package metadata](integrations/openhands/pyproject.toml); reviewed offline fake-runtime foundation only; live execution blocked |
| Downstream agent task completion | Not measured |
| “50% better than most related technology” | **Not established** |
| Production readiness | Not production-ready |
| Confirmed fail-closed blockers | The four identified in-process P0 paths are closed |

The local benchmark result is meaningful evidence that the current design can
beat simple truncation and extraction policies on its own adversarial corpus.
It is not evidence that the same result generalizes to natural histories,
different languages, external context systems, or real agent task completion.

## Mission

Long-running agents accumulate terminal output, repeated files, superseded
plans, installation logs, duplicate search results, tool schemas, abandoned
approaches, and stale reasoning. Keeping all of it active wastes tokens and can
distract the model. Replacing it with an ordinary prose summary creates a
different risk: goals, prohibitions, corrections, exact failures, and open
questions can disappear or change meaning.

This project treats context reduction as compilation rather than
summarization:

1. parse source events into typed claims;
2. attach every claim to exact source spans;
3. resolve corrections, revocations, conflicts, and closed questions;
4. select the most useful active state within a token budget;
5. independently verify protected coverage and provenance;
6. retain the full audit ledger and optional cold source archive.

The intended result is a compact working-memory prompt for the next model call
plus a larger machine-verifiable artifact for audit and replay.

## Target and definition of success

The product target has six parts. All six are required before the original
goal should be considered complete.

| Target | Required evidence |
| --- | --- |
| 5–10x less active context | At least 5x source-to-active compression on synthetic and held-out natural histories |
| No lost explicit commitments | 100% recall for goals, constraints, user corrections, unresolved questions, exact errors, and exact references |
| Exact provenance | 100% valid source ids, character offsets, quotes, and hashes for retained claims |
| Safer temporal state | No stale superseded claims, authority violations, unsupported claims, or unresolved-to-fact promotions |
| Better agent outcomes | Higher completion on at least two public long-horizon task suites under matched models, tools, budgets, and retries |
| At least 50% better than most related systems | A preregistered, paired comparison must clear the statistical bar for a strict majority of the dated comparison set |

For a component-level memory claim, “50% better” can mean at least 50% less
critical semantic loss or at least 50% more memory quality per active token,
with a positive preregistered paired bootstrap lower bound. For the original
product claim, memory proxies are not enough: the compiler must also deliver at
least 50% task-failure reduction or 1.5x successful completions per total token
or cost against a strict majority of the dated comparison set. The exact
estimand and percentile must be frozen before external runs. The complete claim
protocol is in [Benchmarking](docs/BENCHMARKING.md).

The 100% retention target means zero observed protected misses on the frozen
synthetic and natural cohorts, with a confidence bound reported separately. It
is not a universal proof that unseen phrasing can never be missed. The current
LRCBench certificate thresholds of 98% critical and 99% exact recall are useful
alpha gates but are not sufficient for the final no-loss release target.

The target is deliberately harder than obtaining a high summary-similarity
score. A compact output fails if it drops one protected requirement, promotes
one open question to a fact, accepts one tool-output instruction as an
authoritative goal, or cannot trace a claim back to the source.

## What exists today

The repository currently includes:

- a typed memory model for goals, constraints, corrections, facts, decisions,
  unresolved questions, exact errors and references, discarded attempts,
  progress, and background context;
- immutable source records with content and canonical-record SHA-256 digests;
- exact character-offset provenance with quote hashes;
- fixed-seed generative coverage for arbitrary control/Unicode source ids,
  character-vs-byte offsets, all Python line boundaries, whitespace, and
  coordinated clause boundaries;
- a deterministic rule extractor and an optional provider-neutral
  `ModelExtractor`;
- an opt-in `LiteralModelExtractor` that derives character offsets only from a
  unique exact source literal and never trusts model-supplied coordinates;
- an independent deterministic recovery pass for rule-recognized content;
- conservative correction, revocation, conflict, and unresolved-state
  resolution;
- budget-aware selection that never silently drops protected items;
- sealed compiled snapshots with recursive immutability, an integrity digest,
  verification, and independent artifact replay;
- versioned compilation telemetry for item flow, protected-budget pressure,
  recovery, conflicts, verification outcomes, and elapsed compile time, with
  deterministic fields checked again during artifact replay;
- non-replaceable built-in deterministic recovery and protected-item
  certification passes;
- strict bounded model-output parsing with duplicate-key and non-finite-number
  rejection, deterministic provider-failure fallback, and an opt-in strict
  provider-failure policy;
- shared default-on source and compiled-artifact limits for UTF-8 input bytes,
  physical line length, JSON depth, canonical size, schema collections, and
  fixed source-id/role/timestamp ceilings that bound derived-field
  amplification;
- fail-closed compilation expansion limits for each extractor's item,
  rejection, canonical-byte, and auxiliary output; combined candidates,
  resolved items, provenance spans, and shared comparison/render work;
- shared strict regular-file loading for benchmark reports, corpora, external
  candidates, and run manifests, with byte/line/depth limits plus duplicate-key
  and non-finite-number rejection;
- versioned corpus and candidate producer records that are bound by envelope
  self-digests while remaining outside the frozen benchmark dataset identity;
- reusable same-directory atomic file replacement for CLI outputs, archive
  commits, benchmark reports, corpus exports, and runner manifests, plus opt-in
  versioned JSON error diagnostics with stable resource, I/O, input, integrity,
  timeout, and policy categories;
- opt-in `ctxc-event-0.1` JSONL completion events that bind the corresponding
  artifact envelope and expose extraction rejections/failures, recovery,
  verification, compression, and compilation telemetry without changing
  default stderr;
- an opt-in whole-compile deadline that runs materialized inputs in an isolated
  POSIX process group or Windows Job Object, signals the owned POSIX group or
  terminates the Windows Job on timeout, and reconstructs successful output
  from bounded strict JSON;
- a portable JSON artifact, compact prompt renderer, and 25 installed JSON Schemas;
- a stable `materialize_context()` consumer wrapper and `ctxc materialize`
  command that emit the existing materialized v1 plan, structured runtime
  payload, fixed untrusted-retrieval insertion marker, and independently
  verifiable receipt without constructing a provider request;
- an exact `ContextWindowDegradationPolicy` opt-in that keeps default
  materialization strict and is available to `ctxc materialize` only through
  `--degradation-policy lossless-compact-then-reallocate-v1`; it tries a
  self-describing lossless compact memory form and makes at most one bounded
  memory reallocation from the smaller exact pre-verification requirement
  observed for the original partition without displacing fixed input, the
  current turn, or the configured minimum recent tail;
- an optional standard-library LocalAI connector with six framework-neutral
  operations, a strict versioned JSONL process boundary, immutable
  `SourceEvent` mapping, self-hashed `ContextBundle` output, and deterministic
  checkpoints;
- a separately packaged `ctxc-openhands` draft alpha that leaves the core
  dependency-free, pins one exact OpenHands identity, maps a closed 18-class
  event inventory, retains immutable source history in SQLite WAL, and
  exercises compaction, recovery, rehydration, and exact fake-request replay
  without claiming a successful live OpenHands run;
- seventeen packaged connector schemas, six-operation golden JSONL, 66
  intentional negative vectors, and a standalone conformance runner that proves
  in-process and stdio semantic equivalence;
- a machine-readable artifact reader/writer registry with an explicit
  no-silent-migration policy;
- a fail-closed JSON inspector plus a bounded terminal item view with escaped
  control/format characters, provenance coordinates, status, conflicts, and
  active-selection state;
- a logically append-only local source archive with canonical entry hash
  chaining, optional externally retained head checks, exclusive locking,
  legacy migration, and bounded old-or-new atomic commits;
- the `ctxc compile`, `verify`, `inspect`, `diff`, `schema`, `redact`,
  `archive`, `trust`, and optional `connector --stdio` commands;
- LRCBench, external-candidate import/export, history-weighted paired bootstrap
  gates, per-system decisions, and self-hashed JSON reports with producer/run
  metadata;
- gold-free corpus self-digests plus a bounded, non-interpolating external
  runner with sequential per-case processes, inherited POSIX per-process
  memory limits, Windows per-process/aggregate Job limits, and valid or failed
  manifests that feed per-system certificate decisions;
- result-blind ACON and AMA-Agent compatibility records plus a pinned ACON
  diagnostic adapter whose first bounded run is retained as a failed,
  non-scoreable manifest;
- an API-free, single-inference adapter for the exact local
  `qwen/qwen3.6-35b-a3b@q4_k_m` LM Studio model;
- a fixed-digest, versioned CI compile-performance gate with latency-growth and
  traced-Python-memory ceilings;
- six strict natural-history evidence schemas and seven self-hashed fixtures for
  corpus, independent annotations, adjudication, grouped splits, gold-free
  export, and reports; these fixtures do not constitute a natural cohort;
- a frozen, self-hashed 64-case novel-English precision/recall diagnostic with
  exact deterministic replay;
- a sequential captured-output harness for the exact local Qwen Q4 model that
  binds every ModelExtractor prompt/output, separates strict model-only
  acceptance from deterministic recovery, records rejection/latency/zero-cost
  evidence, and replays saved outputs without model access;
- a model-free, self-hashed post-hoc ablation that discards only captured
  model offsets, retains candidate text and cited source ids, replays the
  result through `LiteralModelExtractor`, and labels the target prompt as
  unevaluated;
- a pre-result paired evaluator and disjoint self-hashed 64-case corpus that
  counterbalance coordinate/unique-literal prompt order, require a clean
  revision, retain failures without retry, reserve an exclusive report
  destination, and now bind the complete 128-call exact-Qwen result;
- public, bounded domain-label and strict extractor-composition hooks that do
  not accept caller-supplied regexes or replace built-in recovery;
- optional fixed-detector content secret redaction with preserved offsets,
  recomputed source hashes, bounded scans, strict replay, and a self-hashed
  audit report that contains neither original content secrets nor their hashes;
- cross-version CI, linting, wheel/schema checks, and regression tests.

## In development

The next phase is mainly evidence, generalization, and integration rather than
adding more claims to the README:

- resolve the retained external adapter blockers, freeze the comparison set, and
  run matched systems through the existing candidate interchange;
- collect and independently annotate held-out natural coding-agent histories
  under the implemented consent, license, privacy, adjudication, split, and
  no-label-leakage contracts;
- measure end-to-end task completion on public long-horizon suites;
- test the optional model extractor across providers and novel phrasing;
- add named provider tokenizers and production framework-specific adapters;
- optimize incremental connector compilation beyond its current
  correctness-first full-prefix recompilation;
- continue hardening generic-provider transport deadlines, metadata/PII
  handling, encryption guidance, signed publication/key handling, and
  observability;
- obtain independent reproduction before making a state-of-the-art claim.

The ordered engineering backlog is in [TODO.md](TODO.md). The current design
state, base revision, non-negotiable decisions, code map, and restart procedure
for a new chat are in [docs/HANDOFF.md](docs/HANDOFF.md).

### Closed P0 safety paths

The four identified in-process fail-closed defects now have regression-tested
controls:

1. verified `CompiledMemory` snapshots and their items are recursively sealed;
   prompt and artifact rendering recheck a canonical snapshot digest;
2. built-in deterministic recovery and protected-item certification execute
   independently of caller-supplied extractors and cannot be replaced;
3. primary extractor failures and wholly unusable outputs fall back to
   deterministic recovery with explicit verification warnings, while
   `fail_on_primary_extractor_error=True` provides strict behavior;
4. selecting superseded state is audit-only and makes verification fail, so it
   cannot enter a prompt labeled verified.

LRCBench also now uses one history-weighted memory estimand for point estimates
and paired bootstrap bounds, records per-system decisions, and applies a strict
majority rule to a frozen external comparison set. Any external scoring now
requires a verified self-hashed protocol whose system set, adapter revisions,
and synthetic dataset digest match the run. No external candidate has been
scored and no collected natural-history task outcome exists, so the
external-superiority claim remains unestablished.

## Intended use

This project is aimed at long-running coding, research, operations, and tool
agents where a small amount of durable state must survive a much larger noisy
history. It is especially useful when histories contain:

- requirements changed halfway through a task;
- a correction buried thousands of tokens after the original instruction;
- two files or functions with the same name;
- an exact error code, test count, path, line range, or node id;
- untrusted tool output that resembles an instruction;
- unresolved questions that must remain questions;
- hard context limits where protected overflow must be explicit.

It is not a general semantic theorem prover, a secure identity system, a
tamper-proof database, a secrets scanner, or proof that arbitrary meaning can
be compressed without loss. Those boundaries are detailed in the
[threat model](docs/THREAT_MODEL.md).

## Why typed memory

Ordinary summaries blur important distinctions: a question can become a fact,
an old requirement can look current, and a paraphrased error can lose the one
number needed to debug it. This compiler represents those states explicitly:

```yaml
goal:
  - Repair authentication timeout
constraint:
  - Do not change the public API
confirmed_fact:
  - Failure occurs only after token refresh
unresolved:
  - Whether clock skew causes expiration
exact_reference:
  - tests/test_token_refresh.py::test_clock_skew
```

The compact prompt includes pointers such as
`source-7:118-164#9f24e1c77a`; the JSON artifact retains the full source quote
and SHA-256 digest.

## Current guarantees

At compilation time, for inputs the extractors recognize, the current
implementation enforces these structural properties:

- every memory item has source provenance;
- goals, constraints, user corrections, unresolved questions, exact errors,
  and exact references are protected from budget-driven removal;
- under the default configuration, a full deterministic rule pass can recover
  every rule-recognized item missed by a primary extractor, while the
  independent coverage certificate is scoped to protected kinds;
- independent constraint commitments in labeled sections, bullets, sentences,
  conjunctions, and semicolon-separated clauses are atomized before temporal
  resolution, so correcting one does not retire its neighboring constraints;
- the conservative grammar recognizes tested indirect preservation
  requirements including “the database stays PostgreSQL” and “leave the
  authentication flow alone” as constraints;
- model-produced ordinary claims must equal a complete atomic source span;
  model paraphrases and truncated clauses are rejected;
- exact items must equal every cited source literal;
- recognized diagnostics retain their complete literal line, and recognized
  references retain full POSIX/Windows paths plus line ranges, GitHub line
  anchors, or pytest node ids;
- a deterministic 222-case extraction grammar corpus crosses section labels,
  bullet forms, conjunctions, negated limits, numbers, units, path locators,
  diagnostics, and correction forms while checking exact source spans;
- uncertain source text cannot silently validate as a confirmed fact;
- tool output cannot assert goals, constraints, corrections, decisions,
  unresolved state, or confirmed facts; a tool fact is accepted only when the
  host sets `metadata.trusted_for_state` to the JSON boolean `true`;
- at the optional connector boundary, assistant, tool, and function events are
  historical-only unless the host supplies authenticated authority metadata;
  event metadata cannot self-promote them, and authenticated tool state remains
  restricted to the existing confirmed-fact path;
- explicit corrections retain the old item as superseded state;
- explicit revocations retire the matched old commitment without inventing
  replacement state;
- clear numeric or polarity conflicts remain visible and generate an
  unresolved item;
- each source-record hash binds id, sequence, role, content, timestamp, and
  metadata; quote, source-set, and artifact digests make changes visible when
  checked against an independently trusted source set;
- source metadata is deep-copied, restricted to finite canonical JSON values,
  and recursively immutable after `SourceRecord` construction;
- opaque source ids, roles, and timestamps retain arbitrary Unicode and
  control characters but are capped at 1,024, 128, and 256 characters
  respectively across source, provenance, model, archive, artifact, and
  redaction contracts;
- the final compiled ledger, verification report, statistics, selection, and
  compiler metadata are recursively sealed, and prompt/artifact rendering
  rechecks a canonical snapshot digest.

These are implementation invariants, not a proof of semantic completeness.
The rule extractor is deliberately conservative and heuristic. Upstream roles
must be authenticated and archives must be protected by the host. The optional
preprocessor covers only recognized content secrets; metadata, ids, timestamps,
personal data, and unknown formats remain the caller's responsibility. See
[Content secret redaction](docs/REDACTION.md) and
[Threat model](docs/THREAT_MODEL.md).

## Install

Python 3.11 or newer is required. The runtime uses only the standard library.
The stable distribution name is `loss-resistant-context-compiler`; no package
index release is currently claimed.

The optional LocalAI connector is included in that standard-library core. It
does not require or import `localai-contracts` or any sibling LocalAI project.
If a host already has contract objects, the in-process adapter accepts them
structurally through a mapping, dataclass, `model_dump()`, `to_dict()`, or
`dict()`; the plain versioned JSON protocol is the portable process-boundary
contract.

```console
python -m pip install -e .
```

For development tools:

```console
python -m pip install -e ".[dev]"
```

Early editable installs may still carry the old development distribution
metadata `lossless-context-compiler`. It is not an alias or upgrade target;
remove it before reinstalling the current project. The Python import and
`ctxc` command have not changed. See [Support](SUPPORT.md).

## CLI quick start

Optionally create length-preserving redacted source records before any model
extractor sees the history:

```console
ctxc redact history.jsonl -o redacted-history.jsonl \
  --report redaction-report.json
```

The command never overwrites its input. It masks common credential forms in
content only, writes fresh source hashes, and emits a strict self-hashed audit
map without copying or hashing the original content secrets. Source ids,
roles, timestamps, and metadata remain unchanged. Retain the redacted history
for artifact verification. See [Content secret redaction](docs/REDACTION.md)
for detector coverage, limits, false-positive/false-negative risk, and the
non-transactional two-file boundary.

Compile the included small JSONL history to a typed prompt:

```console
ctxc compile examples/auth_timeout.jsonl --format prompt
```

Write a portable JSON artifact, verify it independently against the sources,
and inspect its headline metrics:

```console
ctxc compile examples/auth_timeout.jsonl -o compiled-memory.json
ctxc verify compiled-memory.json examples/auth_timeout.jsonl
ctxc inspect compiled-memory.json
ctxc inspect compiled-memory.json --format text --show-items
```

Materialize a bounded consumer result from the included correction-bearing
history:

```console
ctxc materialize examples/materialized_context.jsonl \
  --current-turn-id deploy-011 \
  --hard-limit-tokens 3000 --memory-budget-tokens 2200 \
  --reserved-output-tokens 128 --safety-margin-tokens 64 \
  --minimum-recent-messages 2 --maximum-recent-messages 3 \
  --per-message-overhead-tokens 2 \
  --allocation-plan-sha256 <independently-calculated-sha256> \
  --tokenizer-profile unicode-codepoint-count-v1 \
  -o materialized-context.json \
  --emit-receipt-sha256 > materialized-receipt.sha256

ctxc verify-materialization materialized-context.json \
  --expected-receipt-sha256 <independently-retained-receipt-sha256> \
  --expected-allocation-plan-sha256 <independently-calculated-sha256> \
  -o verified-materialized-context.json
```

The CLI profile counts its named Unicode code-point units exactly; it is not a
provider tokenizer. The result keeps verified memory, recent raw messages, one
empty untrusted external-retrieval slot, and the current user turn in a fixed
order. It remains provider-not-ready and requires a final recount. Python hosts
with an exact tokenizer should use `materialize_context()` and retain the
receipt digest independently for `verify_materialized_context_result()` or
`serialize_materialized_context_result()`. The serializer verifies both
independent anchors and emits exactly one canonical UTF-8 JSON line. CLI hosts
can pair `--output` with `--emit-receipt-sha256` to retain the verified receipt
digest separately. The result file is atomically completed first; stdout is
then exactly the lowercase digest plus one LF. A nonzero exit leaves that file
unanchored and unusable. Without the flag, established stdout and output-file
bytes are unchanged. `verify-materialization` accepts both independent digests
and boundedly re-emits the original canonical result bytes without creating a
second receipt or readiness claim.

When mandatory fixed inputs, protected memory, the current turn, and the
minimum recent tail cannot fit, the existing
`mandatory_components_do_not_fit` reason remains a generic
`ctxc-diagnostic-0.1` error. Its deterministic content-free message names the
failed capacity boundary and reports the exact integer planning-unit inputs,
required total, and shortfall. It contains no source IDs, text, paths, or
partial memory, and it does not add a `details` object. The counts are for the
named planning counter, not provider tokens. Strict and degradation-enabled
calls refuse identically; neither path clamps or silently changes a budget.

Python callers facing an exact compiled-memory overflow may explicitly supply
`ContextWindowDegradationPolicy()`. The ladder compares the exact
pre-verification strict and lossless-compact requirements observed for the
original partition and, if necessary, reallocates once to the smaller observed
count. A shifted boundary can change the final compiled rendering, so this is
not a global-minimum claim over every possible repartition. Compiler metadata
and the receipt transitively bind the mode, rung, requested/effective memory
budgets, rendering profile, and all shifted-prefix omissions. Default calls and
`ctxc materialize` without the exact opt-in remain strict. Every result still has
`semantic_completeness_claimed: false`,
`provider_execution_ready: false`, `retrieval_result_sha256: null`, and
`final_provider_recount_required: true`.

Run the installed structural retention diagnostic over its 20-case held-out
split:

```console
ctxc evaluate-materialization --split heldout -o retention-report.json
```

It compares complete raw history, a bounded recent tail, and the verified
materialized result under one fixed Unicode code-point planning-unit profile.
The canonical self-hashed report retains every refusal and measures exact
correction, identifier, path, number, detail, current-turn, omission, and
authority-boundary outcomes. The bundled histories are visible,
project-authored synthetic-naturalistic fixtures rather than a collected or
operationally blinded natural cohort. The command makes no provider-token,
model-answer, task-completion, semantic-completeness, retrieval, or comparative
claim. See [Materialization retention evaluation](docs/MATERIALIZATION_RETENTION_EVALUATION.md).

The frozen pack's current deterministic all-split outcome is red: all 28
predeclared accepted cases refuse with `compiled_memory_not_verified`, while
both predeclared hard-limit refusals match. The command still writes the
complete canonical report and returns 3; neither the fixtures nor their
expectations were changed after observing that result.

Run the fixed degradation comparison over the unchanged 20-case held-out split:

```console
ctxc evaluate-materialization-degradation -o degradation-report.json
```

The three arms use the default strict path, a compact-only stop, and the public
compact-plus-single-reallocation policy. Strict and compact-only each refused
all 20 cases (18 `compiled_memory_not_verified`, two mandatory-component
refusals). The full ladder accepted 18 and retained the same two mandatory
refusals. Every accepted result preserved all required/protected exact atoms,
correction precedence, authority boundaries, source partition, current turn,
and receipt bindings. Required/protected atoms are bound to their exact frozen
source spans, and receipt verification is bound to a second deterministic
execution rather than described as an external anchor. The report explicitly
records that both the full ladder and the failed compact-only preflight were
previously observed; it is structural synthetic-naturalistic evidence, not
newly unseen natural-history or provider-token evidence.

JSON remains the default inspection format. The terminal view bounds displayed
items, per-item provenance/state links, and raw string length through
`--max-display-items`, `--max-display-links`, and `--max-text-chars`. It
JSON-quotes untrusted strings and visibly escapes terminal controls, Unicode
format controls, and line/paragraph separators. Printable confusable Unicode
is not normalized, so the original JSON artifact remains the authoritative
audit input. JSON item details serialize non-ASCII characters as JSON escapes
without changing their decoded values; the same safe serialization applies to
summary strings. Python callers can use
`summarize_artifact()` or `render_artifact_text()` directly; JSON summaries
identify their contract as `ctxc-artifact-inspection-0.1`.

CLI reports written to stdout use strict UTF-8 with one LF terminator,
independently of the host text encoding. File reports retain their existing
atomic UTF-8 behavior.

Compare two integrity-checked artifact envelopes without requiring their source
histories:

```console
ctxc diff before.json after.json -o artifact-diff.json
ctxc diff before.json after.json --summary-only
```

The deterministic `ctxc-artifact-diff-0.1` report identifies payload additions,
removals, same-id field changes, active-selection changes, verification,
compression, and metric changes. It binds itself with `diff_sha256`.
Per-item details preserve item text and compact provenance coordinates while
hashing rather than copying arbitrary item metadata. If either input is
active-only, the report marks `ledger_comparison_complete: false` and warns
that payload changes do not prove complete-ledger changes; selection changes
remain exact.

Query the exact artifact reader/writer support window before an upgrade or
deployment:

```console
ctxc schema
ctxc schema --artifact-version 1.0
ctxc schema --artifact-version 2.0
```

The versioned `ctxc-artifact-schema-compatibility-0.1` response reports only
artifact schema `1.0` as readable and writable. A query for an unknown
well-formed version succeeds with `status: "unsupported"`; malformed version
syntax is an input error. The package never silently migrates artifacts.
See [Schema compatibility](docs/SCHEMA_COMPATIBILITY.md) for the preservation
and trusted-source-replay requirements imposed on any future migration.

To place the complete compile pipeline inside an isolated, cancellable worker
boundary:

```console
ctxc compile examples/auth_timeout.jsonl -o compiled-memory.json \
  --compile-timeout-seconds 60
```

Successful and completed nonzero outcomes can emit one compact JSONL event on
stderr while the normal artifact or prompt stays on stdout or at `-o`:

```console
ctxc compile examples/auth_timeout.jsonl -o compiled-memory.json \
  --event-format jsonl
```

For JSON output, the opt-in `ctxc-event-0.1` record includes the exact emitted
artifact digest and ledger mode. For prompt output it binds the corresponding
complete audit envelope. It also carries the exit outcome, extraction
rejection/failure counts, recovery contributions, a verification summary, the
compression report, and versioned compile metrics. Default
`--event-format none` preserves silent success stderr. A verification-failed
prompt request may emit a completion event followed by its ordinary error
diagnostic; each remains one JSON line when JSON error format is also enabled.

Use an append-only local cold archive while keeping only compiled state active:

```console
ctxc archive append .context-archive examples/auth_timeout.jsonl
ctxc archive verify .context-archive
ctxc compile examples/auth_timeout.jsonl --archive .context-archive --format prompt
```

Archive writes are logical appends but physical transactions: while holding an
exclusive OS advisory lock, `SourceArchive` validates and serializes the
complete bounded history into a same-directory temporary file, flushes and
`fsync`s it, then atomically replaces `events.jsonl`. Readers therefore observe
either the previous complete archive or the next complete archive, never a
partially appended JSONL tail. The `.append.lock` marker intentionally persists;
ownership is the kernel lock, not file existence, and descriptor close or
process death releases it. The marker itself must remain one regular hard link;
aliases are refused before locking. The tradeoff is O(archive size) work and
temporary disk space per append, and the filesystem must implement local
advisory locks and atomic replacement correctly.

Archive reads refuse a symlink, hard-linked file, directory, FIFO, device, or
other non-regular `events.jsonl`. They open without following links where the
OS supports it, compare the pre-open and open file identities, and retry a
bounded number of times if an atomic replacement lands between those checks.
This keeps a hostile special file from turning verification into an unbounded
read or redirecting it to an aliased file. The shared ancestor guard also
rejects linked/reparse parents, pins the exact parent on POSIX, and rechecks the
full chain before accepting the read. It does not protect against a filesystem
administrator who can replace state outside those checks.

Each new-format line is a canonical `ctxc-source-archive-entry-0.1` envelope
whose SHA-256 binds its position, preceding entry hash, and complete canonical
source record. `archive append` and `archive verify` return the current
`chain_head_sha256`. Retain that head outside the archive, then supply it as a
precondition on the next operation:

```console
ctxc archive verify .context-archive --expected-chain-head HEAD
ctxc archive append .context-archive next.jsonl --expected-chain-head HEAD
ctxc compile next.jsonl --archive .context-archive \
  --archive-expected-chain-head HEAD
ctxc verify compiled-memory.json --archive .context-archive \
  --archive-expected-chain-head HEAD
```

The append response contains the replacement head to retain for the following
operation. A valid older prefix passes standalone structural verification, but
fails when checked against a later externally retained head. Raw legacy source
JSONL remains readable; the first non-idempotent append upgrades the complete
file atomically. Artifact replay can consume the archive directory directly
through `ctxc verify ARTIFACT --archive ARCHIVE`; the expected-head option
checks the anchor before any source record enters replay. The chain is an
integrity and stale-state check, not a signature, trusted timestamp, or
filesystem access control. Anyone able to rewrite the archive can recompute it,
so rollback detection depends on keeping the expected head in a separately
protected location.

Create a detached manifest when the artifact, source digest, and optional
archive head need one externally anchored bundle identity:

```console
ctxc trust create compiled-memory.json examples/auth_timeout.jsonl \
  -o trust-manifest.json
ctxc trust verify trust-manifest.json compiled-memory.json \
  examples/auth_timeout.jsonl \
  --expected-manifest-sha256 MANIFEST_SHA256
```

`trust create` accepts only a complete artifact that passes independent replay.
It binds the artifact digest, exact source-set digest/count, complete-ledger
status, and either the verified archive chain head or `null`. Retain the
manifest's `manifest_sha256` outside the storage boundary holding the manifest,
artifact, and sources; verification requires that separately retained value.
For archive-backed sources, use `--archive ARCHIVE` and optionally require
`--archive-expected-chain-head HEAD` on both trust commands. A self-hash copied
beside the bundle is not an external anchor, and this feature is not a digital
signature or key-management system. See
[Detached trust manifests](docs/TRUST_MANIFESTS.md).

`ctxc compile` also accepts `-` for stdin. Inputs may be a JSON list, an object
containing `sources`, `events`, or `messages`, or JSONL. Each record accepts
`id`, `sequence`, `role`, `content`, `timestamp`, and `metadata`.
Source JSON uses strict decoding: duplicate object keys, non-standard
NaN/infinity constants, overflowed non-finite floats, and excessive nesting
are rejected.

Source ingestion is bounded by default across `compile`, `verify`, `redact`,
and archive commands:

| Boundary | Default | CLI override |
| --- | ---: | --- |
| Serialized source or archive input | 64 MiB | `--max-source-bytes` |
| Source records | 100,000 | `--max-source-records` |
| One physical JSON/JSONL line | 1,048,576 characters | `--max-source-line-chars` |
| One canonical source record | 4 MiB | `--max-source-record-bytes` |
| All canonical source records | 64 MiB | `--max-total-source-bytes` |
| JSON container nesting | 128 levels | `--max-source-json-depth` |
| Source id | 1,024 characters | fixed structural invariant |
| Source role | 128 characters | fixed structural invariant |
| Source timestamp | 256 characters | fixed structural invariant |

The first six configurable limits are available through `SourceLimits` for the
Python API. The final three are exported as `MAX_SOURCE_ID_CHARS`,
`MAX_SOURCE_ROLE_CHARS`, and `MAX_SOURCE_TIMESTAMP_CHARS` and apply to every
`SourceRecord`; increasing a byte limit does not bypass them. These are hard
ingestion boundaries, not a promise that total process memory equals the byte
caps; Python objects, compiler state, and caller-controlled extractors have
additional overhead.

Serialized source and compiled-artifact paths must resolve directly to stable
regular files. Path loaders reject symlinks and special files, reject linked
or reparse-point ancestors, freeze every ancestor's file identity, use
descriptor-relative access inside the exact parent on POSIX, compare
pre-open/open identity, retry a bounded atomic replacement race, and recheck
both content metadata and the ancestor chain after the bounded read. They use
no-follow and nonblocking-open flags where available.
Gzip bytes are rejected as invalid UTF-8 and are never decompressed. Direct
text streams remain the caller's trust boundary.

Compilation itself has a separate default-on expansion contract. Crossing one
of these limits aborts; protected items are never truncated to make the job
fit:

| Boundary | Default | CLI override |
| --- | ---: | --- |
| Items from one extractor | 10,000 | `--max-extractor-items` |
| Rejections from one extractor | 10,000 | `--max-extractor-rejections` |
| Canonical item bytes from one extractor | 64 MiB | `--max-extractor-bytes` |
| Rejection/metadata bytes from one extractor | 16 MiB | `--max-extractor-auxiliary-bytes` |
| Candidates retained across all passes | 30,000 | `--max-total-candidate-items` |
| Items after recovery/resolution | 20,000 | `--max-resolved-items` |
| Provenance spans during compilation | 100,000 | `--max-compilation-provenance-spans` |
| Item comparison/render work units | 5,000,000 | `--max-compilation-item-work` |

`CompilationLimits` exposes the same values to `ContextCompiler`. Built-in
rule extraction checks its item ceiling incrementally. Caller-supplied
extractors are trusted code and can consume resources before returning, but
their returned shape is bounded before recovery, resolution, selection, or
verification. Item-work accounting covers protected recovery, correction and
unresolved resolution, conflict-pair search, selection rendering, and the
quadratic independent-verification paths.

`ctxc verify`, `ctxc inspect`, and `ctxc diff` also decode compiled artifacts
strictly and bound each input independently:

| Boundary | Default | CLI override |
| --- | ---: | --- |
| Serialized artifact input | 128 MiB | `--max-artifact-bytes` |
| One physical artifact line | 8,388,608 characters | `--max-artifact-line-chars` |
| Decoded compact artifact JSON | 128 MiB | `--max-artifact-canonical-bytes` |
| JSON container nesting | 128 levels | `--max-artifact-json-depth` |
| Memory items / selected ids | 200,000 each | `--max-artifact-items`, `--max-artifact-selected-items` |
| Provenance spans | 1,000,000 | `--max-artifact-provenance-spans` |
| Embedded verification issues | 100,000 | `--max-artifact-verification-issues` |

`ArtifactLimits` exposes the same boundaries to `load_artifact()`,
`load_artifact_path()`, and `verify_artifact_dict()`. Duplicate keys,
non-standard or overflowed non-finite numbers, and excessive depth fail before
artifact shape or cryptographic replay work begins.

`ctxc inspect` additionally requires a supported, structurally valid envelope,
valid item/selection references, and a matching canonical `artifact_sha256`
before reporting health. The exported `validate_artifact_envelope()` function
provides the same source-independent check to Python callers. It detects stale
or malformed envelopes; it is not authenticity or semantic verification, which
still requires trusted sources and `verify_artifact_dict()`.

`diff_artifacts()` and `ctxc diff` apply that envelope check to both inputs
before comparing them. A self-consistent diff remains an artifact-derived view,
not proof that either input is authentic or true.

`artifact_schema_registry()` and `artifact_schema_support(version)` expose the
same compatibility policy as `ctxc schema`. The only current reader/writer
version is `1.0`; `compiler_metadata.metrics` and
`compiler_metadata.compilation_limits` are the two documented optional
additive fields within that version. Unknown artifact versions are never
inferred or silently migrated.

The default JSON output contains the complete typed ledger and is the format to
retain for audit. It carries `artifact_sha256`, which `ctxc verify`
recomputes over the canonical artifact payload before checking its contents.
Independent verification also rebuilds the canonical prompt and compression
report, reruns the invariant verifier, and compares the embedded verification
report with that replay. New artifacts include optional
`compilation-metrics-0.1` telemetry under `compiler_metadata.metrics`;
`ctxc inspect` surfaces it, and replay reconciles every field derivable from
the sources and typed ledger. Schema-1.0 artifacts created before this addition
remain valid without metrics or recorded compilation limits. New artifacts
record the exact `CompilationLimits` used; replay validates their shape and
item/provenance ceilings, but the self-reported values are not proof of host
resource enforcement. Primary-extractor volume and elapsed time are measured
evidence and can only be shape-checked during replay. A self-hash is an
integrity check, not a signature; an attacker who can rewrite both an artifact
and its expected trust anchors is outside this guarantee.
`ctxc trust create` standardizes a detached bundle binding, and
`ctxc trust verify` refuses to pass without the externally supplied manifest
digest. It does not remove that external-anchor trust boundary.
`--active-only` intentionally omits unselected ledger entries for compact
transport. Its artifact is marked `ledger_complete: false`;
independent `ctxc verify` rejects it with `incomplete_ledger` because omitted
protected coverage cannot support the detector-scoped protected-retention
claim.

Important compile options:

- `--token-budget N`: requested active-context budget, default `4000`;
- `--minimum-compression R`: target source/active ratio, default `5.0`;
- `--strict-budget`: fail instead of reporting a protected-item overflow;
- `--require-target`: return a failure status if the compression target misses;
- `--include-superseded`: include superseded items for diagnosis; verification
  deliberately fails and verified prompt rendering is refused;
- `--no-recovery`: disable the independent full deterministic recovery pass;
- `--format json|prompt`: choose the output representation;
- `--active-only`: emit a compact, intentionally incomplete non-audit ledger;
- `--error-format text|json`: keep human-readable runtime errors (default) or
  emit one compact JSON object to stderr. Generic errors use
  `ctxc-diagnostic-0.1`; materialization overflow details use
  `ctxc-diagnostic-0.2` with a nested
  `loss-resistant-materialization-refusal-diagnostic-v1` object.

Exit status is `0` on success, `2` for invalid input or policy errors, `3` for
a failed verification, and `4` when `--require-target` is set and the requested
compression ratio is not achieved.

Every `-o/--output` file and archive commit uses the same restrictive
same-directory atomic writer: the complete payload is flushed and `fsync`ed,
then installed with `os.replace()`. Existing regular-file permissions are
preserved. A failure before replacement leaves the prior destination unchanged
and removes the temporary file; linked/reparse ancestors and non-regular
destinations are refused. POSIX creation, replacement, cleanup, and directory
`fsync` are relative to a pinned parent descriptor. Other hosts revalidate the
complete ancestor chain before and after installation. Stdout behavior is
unchanged.

With `--error-format json`, runtime failures have stable top-level fields:
`schema`, `command`, `category`, `code`, `exit_code`, `exception_type`, and
`message`. Argument-parser usage errors remain argparse text, while failed
verification reports and compiled artifacts continue to carry their detailed
issue/rejection data in normal command output. An unsafe or changed ancestor
chain is reported as `io` / `unsafe_path_boundary`.

## Optional LocalAI connector

### Canonical `localai-contracts` 1.0.0 boundary

`context_compiler.localai_contracts_adapter` is a separately imported optional
boundary over the private CtxC connector. The ordinary `context_compiler`
import, Python API, and `ctxc` command do not import or require
`localai-contracts`; required core dependencies remain empty. The `unified`
extra pins the optional distribution version, but installing that extra alone
does not establish `contract_ready`. In particular, a transitive
`provider.whl[unified] --find-links <directory>` install lacks the direct
archive provenance required by this boundary and fails closed. To use the
reviewed identity, independently verify the exact wheel hash below, then
directly install that reviewed contracts wheel together with the provider
wheel using `--no-index --no-deps --no-compile`.

Adapter construction does not execute the first matching package and then
inspect version strings. Before the adapter initiates an optional-package
import, and before it exposes any preloaded package through this boundary, it
requires exactly one reviewed-version distribution, an unset
`sys.pycache_prefix`, and exact built-in module/spec/source-loader state bound to
the recorded package. It rejects loader instance overrides, non-string
namespace/module-registry keys, links/reparse points, unexpected tree entries,
and file-set drift. It requires the exact 35 immutable wheel `RECORD` rows:
34 hashed rows with their URL-safe SHA-256 values, sizes, and installed bytes,
plus the `RECORD` self-row with canonical empty hash/size fields. The required
pip-generated rows are an exact installer marker, exact-wheel PEP 610
direct-archive metadata, and one platform-canonical launcher whose bytes
resolve the reviewed entry point.
On Windows, that launcher must use the architecture-matched reviewed distlib
0.3.9 console stub; another installer stub fails closed without making pip a
runtime dependency. The empty `REQUESTED` marker is optional but exact when
present. Exact sizes plus a canonically framed SHA-256 also bind the reviewed
installed source/resource tree. Any package-local executable bytecode cache
must match fresh compilation of those verified source bytes; external
bytecode-cache prefixes fail closed. The complete `RECORD`, file, and origin
gate is repeated after import, and the returned module must be the validated
`sys.modules` root.

These checks establish agreement for the installed tree and its direct-archive
claim at the observed checks. The PEP 610 claim does not independently
authenticate the wheel bytes: they must still be hashed before installation.
The checks also do not make check-and-import atomic against writable
site-packages or undo code that already ran through `.pth`, `sitecustomize`, a
custom `meta_path`, or a preloaded module. A custom finder can run while Python
resolves the preflight spec. A same-origin module object forged inside a
compromised process remains outside this boundary. Independently hash the
reviewed wheel before installation, unset `PYTHONPYCACHEPREFIX`, and use a clean
environment that is not writable by untrusted actors while the connector runs.

The reviewed compatibility identity is:

| Identity | Value |
| --- | --- |
| Distribution/import | `localai-contracts` / `localai_contracts` |
| Distribution version | `0.2.0a2` |
| Protocol and contract schemas | `1.0.0` |
| Reviewed source commit | `3858190e8b458847da94e9ed24be83f4928b7d1a` |
| Reviewed wheel SHA-256 | `36a02dbc4267402949dddda1da180d800590cc579e0c1ecb022fc96f6a7c29ae` |
| Wheel members / package members | `35` / `29` |
| Wheel `RECORD` SHA-256 | `a20ae81b7cc5dd9e80fc2757d5fea6331f2c232818049026caecf63d48d14076` |
| Installed package-tree SHA-256 | `296f49a2d7b48158d2d3a33e36b77d5b5c495362cbe3aceaaf8975fb256e538c` |
| Phase-0 fixture SHA-256 | `458bdd75449c70277d212761f84e6e04a79ddc010b314a2d0a4799801ef1706d` |

It advertises exactly one executed subject operation,
`context.compile`. The payload is exactly
`{"source_events": [SourceEvent, ...]}` with one to eight actual
`localai_contracts.SourceEvent` documents. The result is the canonical
`ContextBundle` document directly, not a private bundle and not a wrapper.
The wheel's `ConnectorServer` owns request and NDJSON negotiation:
`connector.handshake` must succeed first, unknown or private six-operation
names fail closed, and an expected `ContextBundle` schema is semantically
validated before success. Direct `handle` is only the server's
already-negotiated callback seam.

Use the wheel-owned typed in-process client/server classes, or run the bounded
canonical NDJSON console alias:

```console
ctxc-localai-contracts
```

The accepted module entry point is
`python -m context_compiler.localai_contracts_connector`.

Every physical record must end with a newline. The wheel's default limits cap
a record at 1 MiB, depth at 32, individual strings at 262,144 characters,
arrays and objects at 10,000 entries, and the complete parse at 100,000 nodes.
Duplicate keys, non-finite numbers, malformed UTF-8, and missing newlines
produce canonical closed errors. A bounded oversized physical line is drained
through its newline and produces exactly one error before the next record; an
oversized unterminated EOF produces one error, while a line beyond the bounded
drain ceiling closes fatally without a fabricated response. Reader I/O errors
propagate and poison the transport. The adapter applies the wheel's public
`bounded_canonical_bytes` with the same explicit `ParseLimits` at every private
serialization seam, so the Python path cannot bypass structural or byte
bounds.

`SourceEvent.trust` is a serialized claim, not authentication. Without a
host-owned `authority_verifier`, every event is compiled through the private
untrusted-history path and every projected span remains untrusted. A verifier
may return `AuthenticatedAuthority` only after independently authenticating
the exact event; caller metadata, role names, hashes, or `trust: trusted`
cannot construct that decision. Authenticated tool state additionally requires
the verifier's explicit `trusted_for_state=True` decision and remains limited
to the private confirmed-fact path.

The projection preserves every complete source event as an exact UTF-8
`untrusted_retrieved_spans` entry. Selected private provenance quotes are
projected from exact source text with character offsets converted to UTF-8
byte offsets; only independently authenticated selected spans can enter
`trusted_active_memory`. Rehydrated full sources stay untrusted even when the
actor was authenticated. Private protected omissions and budget overflow
remain explicit. Estimated counts remain estimated; exact counts require an
injected exact counter and bind its identity digest. Counts cover the actual
emitted span-content components, including deliberate duplicate components,
not host framing or a final request.

The canonical projection cannot carry the private compiled artifact,
checkpoint, archive-verification state, or detector-scoped retention
certificate. Those richer structures remain private and independently
replay-verified. A canonical `ContextBundle` is not a replacement certificate,
does not authenticate its producer, and makes no semantic-completeness claim.

The clean-installed exact-wheel conformance gate requires all 22 fresh-session in-process
and NDJSON cases and retains the non-inference scope:

```python
report = localai_contracts.assert_phase0_conformant(
    LocalAIContractsAdapter,
    operation_probe=adapter.build_phase0_probe([source_event]),
).to_dict()
assert report["passed_count"] == 22
assert report["inference_status"] == "not_run"
```

`scripts/validate_localai_contracts_install.py` runs three isolated offline
`--no-index --no-compile` lanes. Provider-only and direct lanes also use
`--no-deps`; the transitive lane resolves only from its local `--find-links`
directory. Repository-owned Python probes use `-B`; the shared-compatible
connector argv does not. Instead, that module child receives
`PYTHONDONTWRITEBYTECODE=1`, and the validator proves no provider or contracts
package `.pyc` appeared before or after the round trip. The provider-only lane
verifies ordinary core use and the installed console alias's fixed exit-2
failure while `localai_contracts` is absent. A transitive `provider[unified]`
lane without direct PEP 610 archive metadata must remain fail-closed. The
supported direct provider-plus-exact-wheel lane launches
`[clean-environment sys.executable, "-m",
"context_compiler.localai_contracts_connector"]`; its literal argv tail is
`-m context_compiler.localai_contracts_connector`. It sends a canonical
handshake followed by `context.compile`, requires the direct `ContextBundle`
payload to match the in-process digest, and runs the same 22-case gate with
`failed_count: 0` and `inference_status: not_run`. No concrete temporary
interpreter path is retained in the repository.

Exact provider reconciliation for implementation head
`0f20b8a1c131fe3c0908f7d6738790529f42338c`, tree
`f04dcf9a0dbe58d91d87856b3a9d4fd6de0293ca`, used two Git-archive
materializations and produced the same 222,688-byte provider wheel at SHA-256
`bda1b1c50fea351eaf1241e62e8a1dde56de5dc4963d8d04a91e21e5eb7fa993`;
its raw `RECORD` SHA-256 is
`485f77359cfc7f8def4c1b47f73e130784ca7beb17baff2ae270792a8726dfc1`.
Provider-only and exact-a2 install lanes passed, Phase 0 passed 22/22 with
`inference_status: not_run`, and the clean same-interpreter component harness
returned the direct `ContextBundle`. Its component-manifest digest is
`d88ba3e2b98ca0f077b02ffb6696f3728ee24ad77972bedd16a229309052b42c`
and its canonical payload digest is
`f83cc1d120e174189332f9f6131b3ac4878d95a740d938976cc20846142bdd45`.
The exact-key local witness is ignored, not published, and not durable release
evidence; it is 1,058 bytes with SHA-256
`555495a9621798c962129beab1159aa4d573c31a173554738a8253c0b0ceca68`.
Do not conflate that commit-epoch provider wheel with the hosted fixed-epoch
root wheel recorded below. The first Windows build from the long synchronized
workspace path failed while creating a nested schema destination and produced
no wheel; the later short-root builds do not relabel that attempt.

### Dependency-free six-operation connector

`LocalAIConnector` exposes six operations over the existing compiler:
`capabilities`, `ingest_source_events`, `compile_memory`, `render_context`,
`verify_memory`, and `inspect_memory`. It is framework-neutral and uses only
the standard library plus this package. No sibling repository is imported, and
`localai-contracts` is not required. Hosts may pass compatible Python objects
in-process, but plain versioned JSON is the stable cross-process
contract.

Run the sequential JSON Lines service with:

```console
ctxc connector --stdio
```

Each nonblank input line must be exactly one
`ctxc-connector-request-0.1` object with the four fields `schema`,
`request_id`, `operation`, and `payload`. Each output line is exactly one
`ctxc-connector-response-0.1` object with `schema`, `request_id`, `operation`,
`ok`, `result`, and `error`; exactly one of `result` and `error` is non-null.
The CLI reads strict UTF-8 bytes and emits strict UTF-8 with one LF terminator,
independently of the host text encoding.
Operation payloads also reject unknown fields. Duplicate JSON keys,
NaN/infinity, excessive depth, and request lines above the configured byte
limit fail closed. A protocol error produces an error response and the service
continues with the next line.

Connector failures expose only a closed category/code pair, a fixed public
message, retryability, and a normalized public exception type. Raw exception
text, concrete Python exception classes, paths, provider payloads, source
content, and credentials are never serialized into the response envelope.
Clients must branch on `error.code`, not parse `error.message`.

For example, these two physical input lines query capabilities and compile one
event:

```jsonl
{"schema":"ctxc-connector-request-0.1","request_id":"cap-1","operation":"capabilities","payload":{}}
{"schema":"ctxc-connector-request-0.1","request_id":"compile-1","operation":"compile_memory","payload":{"events":[{"schema":"localai-source-event-0.1","id":"event-0","sequence":0,"role":"user","content":"constraint: The database stays PostgreSQL."}]}}
```

The compile response returns a self-hashed
`localai-context-bundle-0.1` plus a self-hashed
`ctxc-incremental-checkpoint-0.1`. Send the bundle to `render_context`,
`inspect_memory`, or `verify_memory`; supply the checkpoint or another
independently trusted source set when verifying after a process restart.
Sessions otherwise last only for the lifetime of the stdio process.
`inspect_memory` is a source-independent integrity/summary view, not a
substitute for `verify_memory` against trusted sources.

The 17 connector schemas under `schemas/` cover request/response, the connector
`SourceEvent`, `ContextBundle`, checkpoint, and payload/result pairs for all six
operations. Run `python conformance/run_connector_conformance.py` to validate
the schema graph, six golden state transitions, 66 negative vectors, and exact
in-process/stdio equivalence. JSON Schema validation is structural; the runtime
remains authoritative for provenance, artifact replay, authority, and digest
semantics.

The same flow is available directly in Python:

```python
from context_compiler import LocalAIConnector, SourceEvent

connector = LocalAIConnector()
ingested = connector.ingest_source_events(
    [
        SourceEvent(
            id="event-0",
            sequence=0,
            role="user",
            content="constraint: Leave the authentication flow alone.",
        )
    ]
)
bundle = connector.compile_memory(session_id=ingested["session_id"])
context = connector.render_context(bundle)
report = connector.verify_memory(bundle, checkpoint=ingested["checkpoint"])
assert report["passed"]
```

`SourceEvent` mapping validates any supplied content and record hashes before
adding connector-owned metadata, then creates a fresh immutable
`SourceRecord`. Ids and sequences remain collision-checked; role, redaction,
source provenance, and original-record-hash evidence are preserved rather than
reinterpreted. Redaction and host-provenance descriptors are carried as
metadata, not accepted as proof or allowed to bypass content hashes and exact
compiler provenance. Assistant, tool, and function events are untrusted
historical data by default. Only host-supplied
`authority.authenticated: true` can enable the core assistant authority paths,
and tool state additionally requires `authority.trusted_for_state: true`; even
then it is limited to confirmed facts. A `trusted_for_state` key inside event
metadata cannot authenticate itself. Regular `ContextCompiler` callers that do
not use the connector keep their existing role behavior. The process
supervising stdin is the stdio trust boundary: it must restrict who can submit
`authority.authenticated: true`. Neither JSON, a bundle self-hash, nor a
checkpoint self-hash authenticates that authority assertion.

The same connector authority normalization applies when sources arrive through
events, checkpoints, direct source records, or a configured archive; changing
the entry path cannot promote unauthenticated history. For retry-safe ingestion,
hosts should reuse an explicit event id and sequence. If both are omitted, the
next default sequence advances and the retried content is a new source record.
A checkpoint, explicit record set, or archive can also preserve an authority
marker previously admitted by the host. Treat those bytes as protected
host-boundary state, not as untrusted bearer credentials: their self-hashes do
not authenticate who asserted the marker.

`ContextBundle.trusted_memory` contains:

- active goals, constraints, user corrections, decisions, confirmed facts,
  unresolved questions, exact errors, and exact references;
- the exact source spans and source content/record hashes supporting those
  categories;
- every detected protected item omitted from selection or retained beyond the
  requested budget as explicit
  `omitted_or_overflowed_protected_items`.

The bundle binds the source digest and count, optional source-archive chain
head and its verification status, compiler policy and policy digest, tokenizer
identity and accounting mode, rendered-memory digest, and compiled-artifact
digest. Its own SHA-256 detects modification but is not a signature. An archive
head is rollback evidence only when the host verifies or independently retains
it.

Without a counter adapter, connector accounting is explicitly
`mode: "estimated"`, `exact: false`, with tokenizer identity
`character-estimate-v1`. It cannot be relabeled as exact during verification.
An embedding host can supply the exact model tokenizer in-process:

```python
from context_compiler import ExactTokenCounterAdapter, LocalAIConnector

# `model_tokenizer` is the host's exact tokenizer instance.
exact_counter = ExactTokenCounterAdapter(
    identity="vendor/model-tokenizer@revision",
    count_tokens=lambda text: len(model_tokenizer.encode(text)),
)
connector = LocalAIConnector(token_counter=exact_counter)
```

Exact replay requires the same adapter and identity. Artifact schema `1.0`
retains its historical `*_tokens_estimate` field names even when those values
came from the exact adapter; the bundle's `mode` and `exact` fields are the
truthful accounting claim. The standalone `ctxc connector --stdio` command has
no callback injection option and therefore reports estimated accounting. An
embedded `serve_stdio(connector=...)` process can use an in-process adapter.
Connector counts cover the compiler source text and rendered typed-memory
context, not host-added chat framing, tool schemas, or later prompt material.
Rendering, inspection, and replay of a bundle that claims exact accounting
require the matching in-process adapter; the claim cannot be consumed as exact
through an unconfigured stdio process.

`IncrementalCompiler` appends immutable events, reuses the same sealed result
for an unchanged source digest on ordinary no-deadline calls, and emits a
checkpoint that binds the complete source prefix, source digest/count, session
id, and archive-head state. Resume reconstructs that exact prefix and delegates
to ordinary batch compilation, so the resulting ledger, selection,
verification, and prompt keep batch semantics. This is correctness-first
checkpointing, not yet an incremental performance engine: after any accepted
change it recompiles and reparses the complete source prefix. Checkpoint
self-hashes detect changes; they do not authenticate the checkpoint or attest
that its archive head was verified.

Stdio sessions are sequential and in-memory. When a `SourceArchive` is
configured, one connector session owns that archive; do not multiplex the same
connector/archive instance across sessions. The connector verifies and binds
the retained head for that owner. On resume,
`archive_head_verified` is re-established from the actual configured archive,
never trusted merely because checkpoint JSON says it was verified.

An issued connector certificate says exactly
`all detected protected commitments retained`. It is scoped to commitments
recognized by the current detectors. It does not claim semantic completeness,
that every natural-language requirement was detected, or that a self-hashed
bundle authenticates its producer.

## Separately packaged OpenHands alpha

[`ctxc-openhands`](integrations/openhands/README.md) is an isolated package
under `integrations/openhands/`; its exact current version is owned by the
[integration metadata](integrations/openhands/pyproject.toml). Ordinary
`loss-resistant-context-compiler` installation and every standalone `ctxc`
Python/CLI path remain dependency-free and do not import OpenHands. Importing
`ctxc_openhands` also defers all host imports until its exact compatibility
gate passes.

The reviewed identity is OpenHands `1.8.0` at
`bc26df351dd5d833a95131556dbe2da69af82253` with
`openhands-sdk`, `openhands-tools`, and `openhands-agent-server` `1.27.0` at
`904279edf2df5fa12d7caecc7576f62659b2e2dd`, on CPython 3.12 or 3.13. The
[machine-readable pin](integrations/openhands/compatibility/openhands-1.8.0.json)
and [closed event map](integrations/openhands/docs/EVENT_AUTHORITY_MAP.md) bind
that review. Unknown top-level event classes, kinds, or fields fail closed.
Host `source` and message `role` values are claims, not authentication;
assistant, tool, retrieval, attachment, file/search, hook, and delegated-agent
output remains untrusted unless an independently issued event-bound authority
receipt verifies it. An otherwise valid event with an unknown explicit tool
name and no serialized nested kind remains generic and cannot be
authority-promoted. An unknown serialized nested action/observation kind fails
closed, and every known nested kind must match the explicit tool category.

The callback runs before the host persistence callback, never raises into the
host, and poisons later ingestion and dispatch after its first refusal. Poison
clears only after exact-order reconciliation against the complete persisted
host EventLog. Action/result and assistant tool-call/result pairs remain
atomic: call id, tool name, and action id where available must match, and an
incomplete, duplicate, reversed, or cross-family group blocks compaction.

The integration store keeps source events append-only and moves a candidate
through `prepared` -> `verified` -> `committed` -> `active`. Only the verified
`active` generation is visible. Source-head, parent-generation, and
active-epoch compare-and-swap checks make a crash expose the old or new
verified generation, while `superseded` and `rolled_back` generations remain
retained. Exact span rehydration always returns `untrusted-evidence`; byte or
span fidelity does not establish truth, authority, or instruction priority.
The qualified store boundary is a local, non-cloud-synchronized,
non-symbolic filesystem. Schema 2 is required and has no automatic repair or
migration. Evidence producers exclusively create new databases/reports;
diagnostic commands use `require_existing=True`, so a typo cannot initialize a
store, and backups never overwrite an existing destination.

The immutable request ledger accounts for prompts, verified memory, recent
tail, current turn, retrieval, attachments, tool schemas, provider framing,
reserved output, and safety margin, and refuses hard-limit overflow. Its
bundled tokenizer is exact only for the offline canonical-UTF-8-byte fake
protocol. `character-estimate-v1` remains estimated. Real OpenHands `run()` and
`arun()` are intentionally refused because no stable public hook exposes both
the final immutable provider request and its exact tokenizer for replay.

From a source checkout, the offline-only preflight and crash scenario are:

```console
PYTHONPATH=src:integrations/openhands/src python -m ctxc_openhands.cli doctor
PYTHONPATH=src:integrations/openhands/src python -m ctxc_openhands.cli offline-scenario --database .artifacts/openhands-offline.sqlite --output .artifacts/openhands-offline.json
```

The scenario forces three compactions and one activation crash/restart and
checks exact retention of its PostgreSQL and authentication constraints. It
uses the offline fake runtime, imports no OpenHands dependency, makes no
network request, and uses no paid service. Process-level network access is not
disabled by the Python runner, so the report honestly retains
`isolation.network_isolation_enforced: false`; only separately retained
runtime controls such as the container demo's `--network=none` can evidence
process isolation. It is not a recorded live demonstration.
`doctor --require-live` is expected to exit 2 with
`hash-pinned-wheelhouse-absent` while the complete reviewed hash-pinned offline
dependency closure and supported immutable final-request/exact-tokenizer hook
are absent. Retain that failure rather than relabeling ordinary `doctor`
success as live readiness.

Operational commands include `doctor`, `explain`, `replay`, `recover`,
`rehydrate`, `offline-scenario`, `soak`, `fault-campaign`, and
`verify-evidence`. Evidence JSON is bounded and self-hashed, but only:

```console
ctxc-openhands verify-evidence --report REPORT --database DATABASE
```

can exit 0 after matching the frozen SQLite SHA-256/size, WAL-checkpoint
binding, source/generation/integrity state, and retained request ledgers. For
scenario and soak reports, it additionally binds the exact one-session
inventory, ordered generation lineage and activation transitions, and
report-specific counts and digests.
Omitting the database is an intentional JSON-only diagnostic that reports
`passed: false`, has scope `json-only`, and exits 2. Preserve each report and
its exact SQLite database together at non-overwriting paths. The captured CLI
argument vector is reconciled with report parameters; it does not attest the
shell, executable, environment, container, or operator.

The 2026-07-27 scenario and soak pairs are hash-intact historical artifacts,
but they were accepted by an earlier verifier that did not bind report claims
to the ordered database generations. They have not been rerun, rewritten, or
reverified under the strengthened scenario/soak verifier; their current proof
is incomplete. The crash-campaign pair remains separately verified historical
temporary evidence and was not rerun in this review cycle; the
ordered-generation finding did not apply to its separate verifier. None of
these pairs is durably hosted or current-head release evidence.

Local offline validation does not establish release readiness. Exact
implementation head `1f684d975005a7e552f62f36dfb7309a58b11799` passed all
six automatic Linux, Windows, and macOS Python 3.12/3.13 package jobs in push run
`30331509718` and all six in pull-request run `30331512148`. The
retained-evidence jobs were skipped/default-off, and durable retained-evidence
hosting remains pending. The 2026-07-27 raw Setuptools integration sdists
remain a retained failed pair because 17 generated timestamps differed.
Initial wrapper head `cf8b8d3911ef776ca015856f8df19ac47dae0628`
made fresh source copies repeat but missed the extracted-sdist fixed point:
its local 231,581-byte sdist at
`b6f4ed61459b5ad9ffb1eb49428ca69be139ce865f7a01f9278ef7df4bffc004`
rebuilt to 231,588 bytes at
`873c1e5959f924a71fbaafb8d7a1a8133bc7c64f90210e9bc1675045eb37196e`
because only `SOURCES.txt` changed. That result also remains failed.
Packaging implementation head
`2f692484272aa36bf267703cad2bb4d6926676ff` adds a package-local,
parity-guarded backend and closes only same-platform, same-toolchain,
same-epoch final-sdist repeatability. Two fresh exact Git archives and one
extracted-sdist rebuild produced the same 232,006-byte sdist with SHA-256
`9a8f5035d8cbe904dc03142b3be54e3e15fae699153fb7630b4948b7415ac6be`;
clean wheel and recursive-sdist installs passed. Hash-pinned build-input
closure, a candidate SBOM, signatures, and provenance attestations remain
separate red gates. Exact packaging head `2f692484` also passed all six
automatic Linux, Windows, and macOS Python 3.12/3.13 package jobs in push run
`30341548763` and all six in pull-request run `30341552866`; both retained
evidence jobs were skipped/default-off. Root push CI `30341549065` and
pull-request CI `30341552713` each passed 7/7 jobs; CodeQL `30341552714` and
dependency review `30341553236` passed.

Exact package-gate implementation head
`f9ba3de6f0ab2ac7e861bd7da907e6949df1339e` then added a separate automatic
comparison of the uploaded package files from all six package lanes. OpenHands
push run `30355357305` and pull-request run `30355359105` each passed the six
Linux, Windows, and macOS Python 3.12/3.13 package jobs plus the aggregate job;
both retained-evidence jobs were skipped/default-off. Root push CI
`30355357152` and pull-request CI `30355359094` each passed 7/7 jobs; CodeQL
`30355359110` and dependency review `30355359096` passed. The push aggregate
report is 16,681 bytes with raw SHA-256
`e9203177989fab536e70febcf5316ba6ea21d2a39ea2d3f8dc2c46b146a6f743` and
self-hash
`9931d25167831dea6698e0794a93e1cc46a1fc23ed29126c94708aefe7efb35c`.
It binds the exact implementation revision and proves six-lane byte identity
for the 221,771-byte root wheel
(`ffc60ecf166cc28563c2a5bc6597e1c4cb0a4c2be6c965d31bafbe3877d1c17c`),
136,343-byte integration wheel
(`ed846517d05b9a734754a9d893de84f23d807f9236c626f1791f9f6d215fa3a9`),
and 240,187-byte integration sdist
(`b8ee2f1f13c06cfe7de1343f9d765eb25aaf6bc1f7d03da7c6443b46eec0594a`).
The pull-request report is separately bound to GitHub's synthetic merge
revision `a585ddbea770e49ff23f49b091536a86fa9db295`, whose tree
`812b43400e7028a4b89ab6ad7aa30eb73aafb536` equals the implementation tree; its
raw SHA-256 is
`9c8ef8530ffa37c6596d94070f75aaed95ab532af3fbf9737870bfa53b08c942`
and its self-hash is
`8ccf05ab81647db1d5030f79ee5e9f367318e923b539b9376cf3e015b04ff2c5`.
These temporary Actions artifacts expire on 2026-08-11. They close only the
automatic six-lane package-byte comparison at that checkpoint; durable retained
evidence, hash-pinned build inputs, live OpenHands execution, SBOMs,
signatures, provenance attestations, and release authorization remained open.

Exact package-input implementation head
`0f20b8a1c131fe3c0908f7d6738790529f42338c` binds the automatic package
builds to the tracked 666-byte `requirements-build.lock`, SHA-256
`243f3ab977d82c04968cf4ea6474b7ef79c060d485aa3a3d67a383d1ef6fbbfe`,
and seven exact universal wheels. Acquisition uses `--require-hashes`; the
dedicated builder, builds, and clean installs then use and revalidate the
retained no-index wheelhouse. OpenHands push run `30366252700` and
pull-request run `30366258656` each passed six Linux, Windows, and macOS
Python 3.12/3.13 package jobs plus the aggregate; retained evidence stayed
skipped/default-off. Root CI push `30366251022` and pull-request CI
`30366255412` each passed 7/7 jobs; CodeQL `30366255532` and dependency
review `30366255341` passed. All six lanes agreed on the 222,688-byte hosted
root wheel
(`ec46f710169a95c21c54a28b941d2f5205104113c3e594fe6945ef68f633642f`),
136,967-byte integration wheel
(`16976fa84cebb2b35f1cc15db89a41f016a3a8385498fd2335adbc28c85aacf0`),
and 255,770-byte integration sdist
(`bf51799df63019c3138368a2d6f9e8bc3ddd398163838669340764be36130957`).
The 44,203-byte push report has raw SHA-256
`46e47ee4b6f3d1b7ce0fdcc5e41e5f812f2ee0d17005d0c77acd986198213acd`
and self-hash
`62cd0a5a4ee351dc9fc2c3bd892a79db5394058e96a611c2a47946e0e42ec876`.
The same-size pull-request report is bound to synthetic merge revision
`37f7796a1fa74d2637a5ad85e70a9c988ffc5836`, whose tree equals the
implementation tree; its raw SHA-256/self-hash are
`4fd96cb025bd557644e670a79c2ae6eeabb244099886d649ab755f7ffb6e8bda`
and `aba29437bf2ee4ba7d5876eea978bc1bf74cba321ebe047e5ff92acf80e69626`.
This closes exact package-build input byte identity for those temporary
automatic lanes, not signed origin, durable retention, the complete live
OpenHands dependency closure, live execution, SBOMs, signatures, provenance
attestations, or release authorization.

Exact root release-input implementation head
`7915beb15f6a3429c24871779c7cdab280d1ee04`, tree
`5fb61a9f0e9e00016acfd00d04e85e8ef04638f8`, adds the independent root
666-byte `requirements-build.lock` with the same reviewed SHA-256
`243f3ab977d82c04968cf4ea6474b7ef79c060d485aa3a3d67a383d1ef6fbbfe`
and seven universal build wheels. The root platform-smoke, repeated-build, and
LRCBench jobs acquire only those exact wheel bytes with `--require-hashes`,
validate the seven-distribution inventory, create a dedicated no-index builder,
and use it for their release builds. Platform-smoke and LRCBench pass the same
wheelhouse and lock to the wheel/sdist clean-install smoke, which reports
`hash-pinned-offline-wheelhouse` for the sdist and `not-applicable` for the
already-built wheel. The repeated-build job instead builds two candidates with
the exact builder and requires the strict comparator to accept both archives.

For that exact implementation head, root CI push run `30429423660` and
pull-request run `30429426031` each passed all seven jobs, including Python
3.11/3.12/3.13, Windows and macOS release smoke, exact repeated release builds,
LRCBench, packaged installs, and the bounded performance gate. OpenHands push
run `30429423659` and pull-request run `30429426030` also passed; their retained
evidence remained manual/default-off. CodeQL `30429426038` and dependency
review `30429426044` passed. This closes only the exact root build-input and
post-acquisition offline build/smoke path at `7915beb`. The configured index and
publishers are not authenticated by the hashes, Actions artifacts are
temporary rather than durably retained, the default online smoke remains a
diagnostic, and live execution, inference, SBOMs, signatures, provenance
attestations, publication, and release authorization remain open.

See the
[operator runbook](integrations/openhands/docs/RUNBOOK.md),
[compatibility policy](integrations/openhands/compatibility/README.md), and
[release checklist](integrations/openhands/docs/RELEASE_CHECKLIST.md), the
[offline container demo](integrations/openhands/demo/README.md), and the
[draft upstream hook RFC](integrations/openhands/docs/UPSTREAM_RFC.md). No
offline test, replay, scenario, soak, certificate, or self-hash establishes
semantic completeness, live compatibility, or superiority.

## Python API

```python
from context_compiler import (
    CompilationLimits,
    CompilationPolicy,
    ContextCompiler,
    SourceLimits,
    SourceRecord,
    artifact_schema_support,
    create_trust_manifest,
    diff_artifacts,
    validate_artifact_envelope,
    verify_trust_manifest,
)

sources = [
    SourceRecord.create(
        sequence=0,
        role="user",
        content="constraint: Do not change the public API",
    ),
    SourceRecord.create(
        sequence=1,
        role="assistant",
        content="unresolved: Whether clock skew causes expiration",
    ),
]

compiler = ContextCompiler(
    policy=CompilationPolicy(token_budget=800, minimum_compression_ratio=5.0),
    source_limits=SourceLimits(max_records=10_000, max_input_bytes=16 * 1024 * 1024),
    compilation_limits=CompilationLimits(max_extractor_items=5_000),
)
memory = compiler.compile(sources)
artifact = memory.to_dict()
validate_artifact_envelope(artifact)
assert artifact_schema_support(memory.schema_version)["readable"]

if not memory.verification.passed:
    raise RuntimeError(memory.verification.to_dict())

print(memory.to_prompt())

manifest = create_trust_manifest(artifact, sources)
anchor = manifest["manifest_sha256"]  # retain outside the bundle's storage boundary
assert verify_trust_manifest(
    manifest,
    artifact,
    sources,
    expected_manifest_sha256=anchor,
)["passed"]
```

`diff_artifacts(before, after, include_item_details=False)` returns the same
self-hashed summary used by `ctxc diff --summary-only`.
Passing `expected_manifest_sha256=None` to `verify_trust_manifest()` always
produces a failed, explicitly unanchored report.

Pass `timeout_seconds` to execute the entire compiler pipeline in a dedicated
process tree:

```python
memory = compiler.compile(sources, timeout_seconds=60)
```

Deadline mode requires `sources` to be a materialized list or tuple and the
compiler configuration to be serializable. It copies that job into the worker,
so extractor mutation cannot change the caller's source objects. On timeout it
signals the worker's owned POSIX process group or terminates its Windows Job; on
success it accepts only a bounded strict-JSON artifact, validates its shape and
self-digest, and rebuilds a sealed snapshot. This is a cancellation and
state-isolation boundary, not a filesystem, network, or hostile-code sandbox.
Compiler configuration crosses the local boundary with pickle and must
therefore already be trusted. An extractor that deliberately escapes its POSIX
process group is outside the guarantee, and Linux group-signal success does not
prove that every member accepted the signal.

For exact provider token accounting, give the compiler a stable counter name
and give independent artifact verification the same callback and name:

```python
from context_compiler import ArtifactLimits
from context_compiler.io import verify_artifact_dict

def count_words(text: str) -> int:
    return len(text.split())

artifact_limits = ArtifactLimits(max_items=50_000)
compiler = ContextCompiler(
    token_counter=count_words,
    token_counter_id="words-v1",
)
memory = compiler.compile(sources)
checked = verify_artifact_dict(
    memory.to_dict(),
    sources,
    token_counter=count_words,
    token_counter_id="words-v1",
    artifact_limits=artifact_limits,
)
assert checked["passed"]
```

The id must identify the exact tokenizer and configuration. A custom counter
without `token_counter_id`, or verification without the matching callback and
id, fails compression replay with `unverifiable_token_counter`.
The CLI does not accept a custom counter callback, so custom-token artifacts
must be verified through `verify_artifact_dict()` in Python rather than
`ctxc verify`.

Pass a `ModelExtractor(complete)` as the primary extractor to use any provider
that can return the documented JSON envelope. This adapter is deliberately
extractive: each ordinary candidate must equal a complete atomic cited span,
and exact candidates must equal every cited span. It does not accept model
paraphrases. Invalid kinds, roles, tags, and spans are rejected before they
enter memory. The built-in deterministic recovery and protected-item
certification passes run independently of the provider. Provider exceptions,
invalid envelopes, oversized responses, and wholly unusable candidate sets
produce deterministic fallback memory plus an explicit warning by default.
Set `CompilationPolicy(fail_on_primary_extractor_error=True)` when provider
failure must abort instead. `ModelExtractor` bounds response size and candidate
count, rejects duplicate JSON keys and non-standard or overflowed non-finite
numbers, and validates exact object fields. An outer compile deadline can
terminate the owned Windows Job or members that remain in the owned POSIX
process group, but custom completion adapters should still enforce a shorter
transport-level deadline so provider failure can return deterministic fallback
memory instead of aborting the complete run.
The callable and any data it sends remain the integrator’s security and privacy
responsibility.

`LiteralModelExtractor(complete)` is a separate, stricter response mode for
models that copy source text more reliably than they calculate Python
character offsets. Its schema accepts exact `text` plus unique `source_ids`,
forbids model-supplied provenance coordinates, and derives a span only when the
literal occurs exactly once in every cited immutable source. It performs no
normalization, fuzzy matching, or paraphrase recovery; it retains all ordinary
authority, uncertainty, atomicity, and exactness checks and additionally
requires atomic literals for exact errors and references. An aggregate
locator-work limit bounds substring scanning. The original `ModelExtractor`
contract and frozen prompt replay remain unchanged. See
[Unique-literal model extraction](docs/LITERAL_MODEL_EXTRACTION.md).

For domain vocabulary, use `DomainLabelExtractor` rather than widening the
global phrase regexes. It maps up to 256 exact ASCII labels to `MemoryKind`
values, preserves exact spans, enforces the same role-authority policy, and
supports inline or section/bullet forms. `CompositeExtractor` combines multiple
uniquely named packs and fails loudly on an invalid component result; built-in
recovery still runs independently. See
[Extending extraction](docs/EXTENDING_EXTRACTION.md) for the contract, replay
boundary, and deployment checklist.

For programmatic preprocessing, `redact_sources()` accepts immutable source
records plus a `RedactionPolicy`. `verify_redaction_result()` deterministically
reruns the same fixed detectors against independently supplied originals, and
`verify_redaction_report_hash()` validates the report's nested shape, counts,
ordering, policy, and self-hash. Masking preserves character offsets and
physical line boundaries, so compiled provenance binds the redacted source
text exactly. It is not encryption, general PII detection, or deletion of the
original input.

For the exact locally installed Qwen build approved for this repository, the
API-free LM Studio CLI adapter verifies the model identity, Q4 quantization,
loaded state, and a single inference slot before every call:

```python
from context_compiler import ContextCompiler, LmsQwenCompletion, ModelExtractor

complete = LmsQwenCompletion(
    r"C:\path\to\.lmstudio\bin\lms.exe",
    timeout_seconds=120,
)
compiler = ContextCompiler(
    extractor=ModelExtractor(
        complete,
        model_id=complete.model_id,
        max_response_chars=1_000_000,
        max_candidates=10_000,
    )
)
```

The adapter disables catalog fetching and does not use an HTTP model API.
It accepts only one JSON object after an optional bounded exact-model loading
prefix and rejects ambiguous or trailing stdout. Its POSIX leader remains
waitable while the owned process group is signaled, and is reaped only after
that cleanup attempt; Windows uses a Job Object. LM Studio passes the prompt as
a process argument, so run content redaction before using it and separately
minimize metadata. See
[Local Qwen integration](docs/LOCAL_QWEN.md).

`CompilationPolicy(verify=False)` is an explicitly unsafe diagnostic mode. It
returns a failed report with `verification_not_performed`; normal
`CompiledMemory.to_prompt()` and CLI prompt output refuse to render that
memory. The Python-only `allow_unverified=True` override exists for deliberate
diagnostics and must not be used to feed an agent.

## Validate

Run the complete regression and static checks:

```console
python -m pytest -q
python -m ruff check src tests benchmarks scripts conformance _ctxc_build_backend.py
python -m compileall -q src benchmarks tests scripts conformance _ctxc_build_backend.py
```

Build the wheel and verify the 25 packaged schemas:

```console
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

Run the same deterministic LRCBench cohort used by CI:

```console
python -m benchmarks --histories 24 --json-out lrcbench-24.json --include-histories
```

Validate the fail-closed external-candidate interchange, or export the exact
gold-free corpus for a separately run system:

```console
python -m benchmarks --self-test
python -m benchmarks --histories 24 --export-corpus lrcbench-corpus.json
python -m benchmarks --verify-report lrcbench-24.json
python -m benchmarks.external_protocol --verify benchmarks/protocols/external-comparison-v1.json
python conformance/run_connector_conformance.py
python -m benchmarks.natural_history
python -m pytest -q tests/test_external_compatibility.py
python -m benchmarks.performance_gate --check --json-out ctxc-performance.json
python -m benchmarks.phrase_eval --verify-report docs/results/novel-english-phrases-v1.json
```

Benchmark JSON reports and corpus exports use the same flushed, `fsync`ed
same-directory replacement as CLI artifacts. External-run manifests use an
exclusive atomic install: a competing file created after the initial check is
preserved and the manifest commit fails instead of overwriting it.

New JSON reports carry `report_schema: lrcbench-report-0.2`,
`report_sha256`, and `run_metadata` containing the producer commit and dirty
state, package and interchange versions, exact command, UTC start time,
duration, Python/platform, tokenizer/model identity, baseline revisions,
model-service cost, and failures. The certificate `evidence_sha256` remains the
deterministic metric digest; `report_sha256` additionally binds the
run-specific envelope. Schema `0.2` adds frozen external-protocol evidence.
The verifier retains local-only `0.1` support so the dated committed report
continues to replay; legacy external-inclusive reports are rejected.

The `ctxc-performance-gate-0.1` CI profile compiles fixed 128- and 256-event
item-dense histories three times after a warmup. It fails on a median above
2 or 8 seconds respectively, growth above 8x when the source count doubles,
or more than 64 MiB of Python allocations observed by `tracemalloc` during
the largest compile. A 1 ms denominator floor keeps sub-resolution first
samples from producing meaningless growth ratios. The warmup and measured
prefixes are bound by committed per-size SHA-256 values, and the JSON result is
self-hashed. These deliberately generous ceilings are regression tripwires for
the GitHub Python 3.11 job, not production latency, RSS, or million-event
scalability claims.

`--verify-report` strictly decodes a bounded regular UTF-8 file, rejects
duplicate keys, non-finite numbers, excessive depth/size, and unknown fields,
regenerates the deterministic dataset id, recomputes both digests, validates
run metadata and comparison accounting, and reconciles raw history counts with
their recorded rates when `--include-histories` evidence is present. Its
success means a current document, or the retained local-only `0.1` document, is
internally consistent; self-hashes are not signatures and do not authenticate
who produced it. A structurally valid report with a non-issued certificate
still verifies successfully.

External outputs can be imported directly with repeatable
`--external-baseline` for diagnostics, but every external run requires
`--external-protocol` and the protocol must be frozen. A counted registered
comparison also requires a validated `--external-run-manifest`; otherwise it
is an invalid non-win. The protocol's registered set is authoritative, and
repeatable `--expected-external-system` assertions, when supplied, must match
that complete set. Missing or failed systems remain non-wins. See
[Benchmarking](docs/BENCHMARKING.md) for the strict schema and claim scope.
`python -m benchmarks.external_runner` supplies a non-interpolating adapter
wrapper with timeout/output limits, inherited per-process virtual-address-space
limits on POSIX, and per-process plus aggregate Job limits on Windows. Adapter
argv is always retained as a literal argument vector and is never
shell-interpreted. Subprocess creation uses `shell=False`; on macOS the launch
argv names the fixed runner-owned shell supervisor described below. POSIX
`RLIMIT_AS` is neither physical RSS/footprint accounting nor one aggregate
process-tree ceiling, and a usable finite value depends on the host and
runtime's existing mappings. On macOS, a fixed runner-owned `/bin/sh -p`
script starts with an empty environment and sets the requested `RLIMIT_AS`
soft and hard values in 1024-byte units before Python starts. Here `-p` selects
the shell's privileged mode; it grants no privilege. The script is runner-owned
and its control fields are runner-generated. It forwards the isolated no-site
(`-I -S`) verifier and adapter command only as quoted positional arguments
without evaluating adapter
text. The runner passes a bounded canonical encoding of the exact adapter
environment through an anonymous, unlinked regular-file descriptor, together
with its byte count and SHA-256 digest. Before bounds and hashing on Darwin, the
runner reserves `__CF_USER_TEXT_ENCODING` as `0x{uid:X}:0:0`; a conflicting
caller value fails closed. The counted, hashed entry prevents CoreFoundation's
default-text-encoding initializer from replacing that environment entry with a
host/home-derived value after `execve`. Evidence covers the
exact mapping passed to `execve`, not later mutations by arbitrary runtime code.
The verifier first requires the exact inherited `RLIMIT_AS`. It then validates
the descriptor, file, and expected
size; reads, scrubs, truncates, and closes the handoff; validates the retained
in-memory length, SHA-256, and protocol; applies byte-exact `RLIMIT_FSIZE`;
canonically decodes the environment; and uses `execve` with the literal adapter
argv. A pre-shell launch failure or shell, pre-verifier, or inexact-`RLIMIT_AS`
exit closes the anonymous unlinked descriptor through process/context teardown
but does not guarantee a scrub. Completed scrubbing reduces residual retention
but is not a cryptographic-erasure claim. An exact-limit claim is made only
after all verifier checks succeed; any mismatch or setup failure is retained
rather than substituting another ceiling. Preflight and `Popen`
failures are blocking runner errors. A post-`Popen` launcher failure is
retained in a failed, non-scoreable manifest. On macOS, a configured limit with
`process_succeeded: false` conservatively records
`memory_limit_enforced: false` because the parent has no authenticated signal
that the verifier completed; this may underreport enforcement but cannot
upgrade the retained failure. A configured limit with
`process_succeeded: true` requires `memory_limit_enforced: true`. The current
external protocol is still draft and blocked; these controls establish no production or
superiority claim. Stream byte counts and hashes come from runner-retained
descriptors, not
reopened paths. At or below a stream cap they cover the full observed stream;
above it they retain a `cap + 1` prefix witness while the descriptor-size
observation still forces a failed limit outcome. The snapshot does not chase
later growth. The default mode executes every case sequentially in a fresh
process, records a hashed per-case audit trail, and merges only fully validated
outputs.
Manifest replay reconstructs every
one-case corpus from the retained parent, verifies its exact canonical and file
digests, and requires executed cases to be the ordered parent-corpus prefix
with runner-owned temporary paths. For a complete run, it also rebuilds each
one-case candidate envelope from the matching raw merged case and registered
producer, then checks that semantic payload digest against the per-case audit.
Whole-corpus mode is diagnostic-only.
Current claim-eligible runner manifests retain the dependency lock, adapter
entrypoint, and a bounded recursive inventory of its immutable source root, and
identify the environment as `sha256:<dependency-lock-sha256>`. Reload rehashes
the lock and every inventoried regular file, requires the entrypoint to be an
exact tree record and appear in the recorded command, hashes the resolved
runtime executable, and verifies a path-independent command contract. Each
per-case invocation must normalize to that same contract, while claim runs use
the source root as their working directory. Adapter children receive a bounded
platform-startup allowlist instead of the full host environment. Extra
variables require repeatable `--pass-environment NAME`; values are represented
only by a canonical environment digest, while names remain auditable and
sensitive-looking names invalidate claim metadata. On macOS that exact mapping,
not the shell supervisor's empty environment, is the canonical anonymous-FD
payload verified immediately before `execve`. Scoring requires those
bytes, the immutable adapter revision, the
entrypoint/source-tree/runtime/environment/command digests, exact
model/context/tokenizer/retry contract, and all retained runner
limits—including the 20 ms enforcement polling cadence—to match the frozen
protocol. Claim controls also require a bounded retained host/container
network-isolation artifact whose digest matches the protocol. A pre-existing
local inference service is accounted separately: the runner captures its PID
creation identity and executable digest, samples Windows working set or
Linux/macOS RSS at the same 20 ms cadence, records sample count and peak bytes,
and invalidates the adapter run if the service disappears, restarts, changes
executable, or crosses its ceiling. macOS uses `libproc` and rechecks creation
identity around each executable/RSS observation. Scoring requires the memory
metric, executable digest, and service ceiling to match the frozen protocol.
The wrapper does not itself create a filesystem or network sandbox, does not
contain or terminate the inference service, and can miss a memory spike between
samples. The source inventory does not bind imports outside its root or prove
which files were loaded. The runtime digest covers argument zero, not every
shared library or interpreter support file. The runner also cannot prove that
the adapter used the designated PID or automatically include separate helper
processes. Service sampling is supported on Windows, Linux, and macOS; a
configured service contract fails preflight elsewhere.
It accepts the legacy producerless adapter payload only at that bounded runner
boundary, then emits the current self-hashed candidate envelope with the
registered adapter/model identity. Direct candidate imports require the current
producer-bearing schema.

The committed compatibility records pin ACON and AMA-Agent source revisions and
license bytes without generating or inspecting comparative output. The ACON
diagnostic was routed once through the existing runner against a one-case
gold-free corpus. It exited before external execution because the exact source
checkout, Python 3.11 environment, dependency lock, enforced network evidence,
inference-service accounting, and LM Studio executable were absent. The
self-hashed manifest retains that failure with no candidate and is not benchmark
evidence.

LRCBench requires exact gold-atom offsets for credited provenance. A candidate
cannot cite a broad enclosing source span to obtain recall credit for a smaller
literal inside it.

For a valid certificate-producing run, the benchmark exits `0` only when every
absolute gate and the paired 50% local frontier check passes; an unsupported
certificate exits `2` and explains why. `--self-test` has its own success
contract, and malformed CLI/configuration failures can use a different nonzero
status. A failed certificate is a valid evaluation result, not necessarily a
harness error.

Current local benchmark snapshot (2026-07-24): the recorded default 32-history
LRCBench certificate is `ISSUED` with scope
`local-bundled-only`. Dataset SHA-256
`421d49585ef9ac96fe2a378f79c18da1791e508789ac0290d3cc5018cda07761`
produced 100% compiler critical recall, exact recall, provenance validity,
semantic-support accuracy, authority accuracy, and history-perfect rate; 0%
stale-claim and unresolved-to-fact rates; and 32.60x corpus compression. The
bundled extractive baseline recorded 88.2% history-weighted critical recall,
83.6% exact recall, 100% provenance and semantic-support validity, 0%
authority accuracy, a 100% stale-claim rate, 0% perfect histories, and 30.13x
compression. The reviewed JSON evidence is
[docs/results/lrcbench-local.json](docs/results/lrcbench-local.json).

The separate frozen 64-case novel-English diagnostic recorded 35 true
positives, 6 false positives, and 5 false negatives: 85.3659% micro precision,
87.5% micro recall, 86.4198% F1, and 55/64 exact-match cases, with no compiler
verification failures. It is locally authored diagnostic evidence, not an
independent or production-representative corpus. See
[the method and mismatch audit](docs/PHRASE_EVALUATION.md) and the
[self-hashed report](docs/results/novel-english-phrases-v1.json).

The pre-registered exact-local-Qwen run then made 64 sequential calls against
that corpus. Qwen emitted 65 decoded candidates across all 64 cases; the
strict exact-span validator accepted 2 and rejected 63 (96.9231%). Accepted
model-only output had 100% precision but only 5% recall. Deterministic recovery
added 33 expected atoms missed by the model, raising final recall by 82.5
percentage points to 87.5%; final precision was 85.3659%, and all compiler
verifications passed. Median call latency was 1.968 seconds and model-service
cost was USD 0.00. Because every negative case elicited a candidate, the
accepted-layer 100% negative accuracy reflects validator rejection rather than
model restraint. See the [frozen protocol and result audit](docs/QWEN_PHRASE_EVALUATION.md)
and [replayable captured-output report](docs/results/qwen-novel-english-phrases-v1.json).

An explicitly post-hoc, model-free ablation then replayed those same 65 saved
candidates after discarding only their integer `start`/`end` values and
retaining candidate text plus cited source ids. Unique-literal validation
accepted 38 candidates and rejected 27. Literal-only output recorded 24 TP,
14 FP, and 16 FN: 63.1579% precision, 60% recall, and 61.5385% F1. Recovery
raised final recall to 100%, but final precision was only 66.6667% and two
compiles failed verification because model-labeled confirmed facts lacked
confirmation evidence. The run made zero model calls and did not evaluate the
new prompt, so it isolates offset arithmetic but is not claim-bearing evidence
for `LiteralModelExtractor`. See the
[method boundary](docs/QWEN_PHRASE_EVALUATION.md#post-hoc-unique-literal-offset-ablation)
and [self-hashed replay](docs/results/qwen-literal-offset-ablation-v1.json).

A separate pre-result protocol froze a disjoint 64-case corpus before a paired
comparison of coordinate and unique-literal prompts. The retained run made all
128 strictly sequential calls with counterbalanced order, one draw per mode,
and no retries. Coordinate output recorded 7 TP, 0 FP, and 33 FN before
recovery (100% precision, 17.5% recall, 29.7872% F1); literal output recorded
37 TP, 13 FP, and 3 FN (74% precision, 92.5% recall, 82.2222% F1). After
deterministic recovery, literal mode improved F1 over coordinate mode by only
6.4974 points, reduced precision by 12.0638 points, and caused four independent
confirmation-evidence verification failures. One coordinate response also
retains LM Studio loading-spinner stdout contamination as an `invalid_json`
failure. This is evidence of a recall/safety tradeoff, not a blanket model or
product win. See the
[held-out paired protocol and result](docs/QWEN_PAIRED_EVALUATION.md) and
[self-hashed report](docs/results/qwen-heldout-paired-extractors-v1.json).

These local generated results are **not an external-system comparison** and do
not establish the requested 50% advantage over most related technology. Rerun
the commands above for the current revision and environment.

## Documentation

- [TODO and development roadmap](TODO.md)
- [Changelog and release notes](CHANGELOG.md)
- [Runtime, platform, format, and installation support](SUPPORT.md)
- [Release and semantic-versioning policy](docs/RELEASE_POLICY.md)
- [Release candidate checklist](docs/RELEASE_CHECKLIST.md)
- [Supply-chain groundwork](docs/SUPPLY_CHAIN.md)
- [Next-chat handoff and current project state](docs/HANDOFF.md)
- [Architecture and invariants](docs/ARCHITECTURE.md)
- [Extending extraction with domain packs](docs/EXTENDING_EXTRACTION.md)
- [Benchmark design and the exact 50% bar](docs/BENCHMARKING.md)
- [Natural-history evidence contracts](docs/NATURAL_HISTORY_EVIDENCE.md)
- [Result-blind external compatibility records](benchmarks/compatibility/README.md)
- [Novel English phrase diagnostic](docs/PHRASE_EVALUATION.md)
- [Exact local Qwen phrase-evaluation protocol](docs/QWEN_PHRASE_EVALUATION.md)
- [Post-hoc Qwen literal-offset ablation evidence](docs/results/qwen-literal-offset-ablation-v1.json)
- [Held-out paired Qwen extractor-evaluation protocol](docs/QWEN_PAIRED_EVALUATION.md)
- [Held-out paired Qwen captured-output evidence](docs/results/qwen-heldout-paired-extractors-v1.json)
- [Unique-literal model extraction](docs/LITERAL_MODEL_EXTRACTION.md)
- [Threat model](docs/THREAT_MODEL.md)
- [Detached externally anchored trust manifests](docs/TRUST_MANIFESTS.md)
- [Compiled-artifact schema compatibility](docs/SCHEMA_COMPATIBILITY.md)
- [Related work](docs/RELATED_WORK.md)
- [Exact local Qwen integration](docs/LOCAL_QWEN.md)
- [Separately packaged OpenHands alpha](integrations/openhands/README.md)
- [OpenHands event and authority map](integrations/openhands/docs/EVENT_AUTHORITY_MAP.md)
- [OpenHands operator runbook](integrations/openhands/docs/RUNBOOK.md)
- [OpenHands release checklist](integrations/openhands/docs/RELEASE_CHECKLIST.md)
- [LRCBench harness notes](benchmarks/README.md)
- [Draft external comparison protocol](benchmarks/protocols/external-comparison-v1.md)
- [Machine-verifiable external protocol](benchmarks/protocols/external-comparison-v1.json)
- JSON Schemas:
  [source event](schemas/source-event.schema.json),
  [model extraction](schemas/model-extraction.schema.json),
  [unique-literal model extraction](schemas/model-extraction-literal.schema.json),
  [compiled memory](schemas/compiled-memory.schema.json),
  [redaction report](schemas/redaction-report.schema.json),
  [source archive entry](schemas/source-archive-entry.schema.json),
  [source archive command report](schemas/source-archive-report.schema.json),
  and [detached trust manifest](schemas/trust-manifest.schema.json);
- [Connector request schema](schemas/connector-request.schema.json),
  [golden transcripts](conformance/fixtures/golden-success.jsonl), and
  [standalone conformance runner](conformance/run_connector_conformance.py)

## License

[MIT](LICENSE)
