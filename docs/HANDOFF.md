# Project handoff for the next chat

This file is the durable restart context for a new chat or contributor. Read it
with the root [README](../README.md), [TODO](../TODO.md),
[architecture](ARCHITECTURE.md), [benchmark protocol](BENCHMARKING.md), and
[threat model](THREAT_MODEL.md) before changing behavior or making comparative
claims.

## Snapshot

| Field | Value |
| --- | --- |
| Repository | [`JamesNguyen42/loss-resistant-context-compiler`](https://github.com/JamesNguyen42/loss-resistant-context-compiler) |
| Default branch | `main` |
| Package version | `0.1.0` |
| Python | 3.11, 3.12, and 3.13 in CI |
| Core runtime dependencies | None outside the Python standard library |
| Tests at this snapshot | 102 passing |
| Recorded benchmark | 32 generated histories, 72 messages each |
| Recorded compiler compression | 32.60x |
| Recorded compiler critical recall | 100% |
| Recorded local certificate | `ISSUED`, scope `local-bundled-only` |
| External systems evaluated | None |
| External 50%-better claim | Not established |
| Downstream task completion evidence | None yet |
| Confirmed fail-closed blockers | Four reproduced P0 paths |

The implementation baseline before this documentation handoff was commit
`32ea078e60d039e94750e9172a548ddbe923dc26`. Use `git log -1` and
`git status -sb` to establish the newer exact state after pulling.

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

This is the intended contract, but current `0.1.0` objects remain mutable after
verification. The confirmed defect is documented below and in `TODO.md`.

### Integrity is not authenticity

Hashes detect changes relative to trusted values. They do not prove authorship,
role authenticity, freshness, or freedom from malicious source content.
Artifacts and their source set need an independent trust anchor in adversarial
storage.

## Confirmed open safety defects

The following defects were reproduced against the implementation baseline.
They are the first work items for the next engineering session.

### Verified state can be mutated

`MemoryItem` and `CompiledMemory` are mutable. `to_prompt()` checks the old
`verification.passed` value and then renders current mutable items. A caller can
change an item after compilation and render changed text under the stale
passing report.

Required direction:

- freeze the complete verified snapshot; or
- bind verification to a canonical snapshot digest and recheck it on render;
- cover nested mutation and every rendered field with regression tests.

### The safety obligation can be replaced

`ContextCompiler` accepts a caller-supplied `safety_extractor`. If both primary
and safety extractors return no items, the verifier receives no protected
candidates and can certify an empty prompt despite an explicit source
constraint.

Required direction:

- always run a built-in non-overridable protected scanner;
- let custom safety extractors add obligations, never subtract them;
- prevent test-only unsafe seams from producing a verified prompt.

### A provider failure prevents deterministic fallback

Primary extraction runs before safety extraction, and provider exceptions are
not converted into a degraded result. A `TimeoutError` therefore exits before
the rule pass can return verified fallback memory.

Required direction:

- validate sources and run deterministic safety independently;
- bound provider calls with deadlines and response limits;
- return verified rule memory plus an explicit degradation issue when policy
  permits;
- retain a strict mode that fails without rendering when model extraction is
  mandatory.

### Superseded state can enter a verified prompt

With `CompilationPolicy(include_superseded=True)`, obsolete items can be
selected into the prompt while compile-time and independent artifact
verification both pass. The prompt labels the item `status: superseded`, but
this still reintroduces stale state into active model context.

Required direction:

- make the option audit-only and refuse verified execution rendering; or
- isolate history in a separately reviewed non-executable envelope;
- add compile and artifact-replay regressions.

These defects do not change the stored deterministic LRCBench output, but they
block a general fail-closed or production-safety claim.

## Processing pipeline

The following is the current order, not the desired post-P0 design:

```text
source construction validates initial hashes
    -> source ordering and id/sequence uniqueness checks
    -> primary extraction
    -> configured safety extraction
    -> canonicalization and deduplication
    -> correction, revocation, unresolved, and conflict resolution
    -> budget-aware active-item selection
    -> source-digest integrity recheck
    -> independent invariant verification
    -> complete JSON ledger and/or compact typed-memory prompt
    -> optional append-only cold source archive
```

The intended fix moves a non-overridable protected scan and integrity recheck
before any provider call, then treats provider output as optional additive
coverage.

The compiler is synchronous and provider-neutral. It is deterministic when all
supplied extractors and token counters are deterministic.

## Code map

| Path | Responsibility |
| --- | --- |
| `src/context_compiler/models.py` | Enums, immutable source records, mutable items, policy, reports, rendering, hashes |
| `src/context_compiler/extractors.py` | Rule extraction, constraint atomization, model adapter, authority checks |
| `src/context_compiler/resolver.py` | Deduplication, corrections, revocations, unresolved closure, conflicts |
| `src/context_compiler/compiler.py` | End-to-end orchestration, recovery, selection, compression accounting |
| `src/context_compiler/verifier.py` | Independent coverage, provenance, support, authority, and state checks |
| `src/context_compiler/io.py` | Input decoding, strict artifact shape validation, replay verification |
| `src/context_compiler/archive.py` | Append-only local archive, locking, loading, and verification |
| `src/context_compiler/cli.py` | `ctxc` command-line interface and exit codes |
| `benchmarks/lrcbench.py` | Corpus generation, baselines, metrics, interchange, bootstrap certificate |
| `schemas/` | Source, model extraction, and compiled artifact contracts |
| `tests/` | Unit, adversarial, schema, benchmark, tokenizer, and held-out regressions |
| `.github/workflows/ci.yml` | Cross-version tests, lint, wheel checks, interchange, benchmark |

## Public interfaces

### CLI

```console
ctxc compile HISTORY [--format json|prompt]
ctxc verify ARTIFACT HISTORY
ctxc inspect ARTIFACT
ctxc archive append ARCHIVE HISTORY
ctxc archive verify ARCHIVE
```

Important compile options:

- `--token-budget`;
- `--minimum-compression`;
- `--strict-budget`;
- `--require-target`;
- `--include-superseded`, diagnostic-only until P0-S4 is fixed;
- `--active-only`;
- `--no-recovery`;
- `--archive`.

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
- `RuleBasedExtractor`;
- `ModelExtractor`;
- `SourceArchive`;
- `VerificationReport`.

Custom token accounting requires both a callback and a stable
`token_counter_id`. Artifact verification must receive the identical callback
and id or fail with `unverifiable_token_counter`.
The CLI has no way to receive a Python callback, so custom-token artifacts must
use `verify_artifact_dict()` through the Python API. `ctxc verify` correctly
fails such artifacts as unverifiable.

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
| LRCBench/candidate schema family | `0.1` |
| Installed schema directory | `share/lossless-context-compiler/schemas` |

The resistant/lossless distribution-name difference is unresolved and is a
release TODO. Do not rename it casually: package, schema-install,
documentation, and migration compatibility must change together.

## Input and output contracts

Input accepts:

- a JSON list;
- a JSON object containing `sources`, `events`, or `messages`;
- JSONL;
- stdin through `ctxc compile -`.

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
verification report, and artifact self-hash. It does **not** embed the complete
source history; verification and provenance rehydration require the separately
retained source events.

`--active-only` intentionally emits only selected items and marks
`ledger_complete: false`. A selected superseded item is still emitted if the
diagnostic `include_superseded` option is enabled. Independent verification
rejects the incomplete ledger for a full certificate because omitted protected
coverage cannot be audited.

## Current evidence

### Regression and packaging

- 102 tests pass.
- Ruff checks pass.
- CI covers Python 3.11, 3.12, and 3.13.
- CI builds a wheel and verifies that all three schemas are included.
- CI runs the external-candidate interchange self-test.
- CI runs a 24-history fail-closed benchmark certificate.

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
- dataset SHA-256
  `9dd650433b9d1a018951a7a4745ba31907f6314965e44aee01ea9ecca24389ae`.

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

Strongest bundled extractive control:

- 88.1% critical recall;
- 83.6% exact recall;
- 100% provenance validity;
- 100% semantic-support accuracy;
- 0% authority accuracy;
- 100% stale-claim rate;
- 0% perfect histories;
- 30.13x compression.

The local certificate was issued using critical-semantic-loss reduction. Its
scope is `local-bundled-only`.

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

The committed result file also does not embed the producing commit SHA, Python
version, platform, exact command, or run timestamp. The implementation baseline
is known from repository history, but that is weaker than self-contained
evidence metadata. Adding those fields and binding them into the report digest
is a P1 evidence task.

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
- a 50% paired relative-gain point estimate and positive 2.5th-percentile
  paired bootstrap margin.

With external candidate outputs, the strongest imported or bundled competitor
becomes the comparison baseline. “Most” still requires separately winning
against a strict majority of a preregistered dated set.

Those are current alpha component-certificate gates. The final product gate is
stricter: zero observed protected and exact misses on frozen synthetic and
natural cohorts, real-token compression on every cohort, and at least 50%
task-failure reduction or 1.5x successful completions per total token or cost
against the strict majority. The current 2.5th percentile is the lower endpoint
of a two-sided 95% interval, not a one-sided 95% bound; the external protocol
must freeze and label the intended quantile correctly.

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
- There is no automatic redaction, encryption, access control, or retention
  manager.
- Exact provenance can retain secrets or personal data.
- Local archive append-only behavior is not filesystem-enforced.
- Hashes are not signatures and do not prevent rollback.

### Scale and availability

- Batch compilation reparses the supplied history.
- Input bytes, line length, record count, and archive size are not globally
  capped.
- Protected items can exceed a downstream hard context limit.
- Archive locking may require operator review after an abnormal process death.

### Evaluation

- The recorded histories are generated templates, not natural production
  prevalence.
- The optional model extractor is not exercised in the recorded benchmark.
- Local controls are simple and are not state-of-the-art substitutes.
- Atom recall is a proxy for agent success, not task completion.
- Bootstrap intervals do not cover benchmark design bias.
- Aggregate critical loss and memory-quality efficiency currently use different
  weighting from their paired bootstrap calculations.
- The current certificate selects one strongest baseline; it does not implement
  the strict-majority per-external-system decision required for “most.”

## Safe host-side compaction transaction

The library compiles and verifies memory, but the host decides when old active
context is removed. A safe integration should perform this transaction:

1. authenticate roles and redact secrets before ingestion;
2. durably persist the retrievable trusted source events and separately anchor
   their expected digest;
3. compile a candidate artifact without deleting active history;
4. independently verify the complete artifact against the trusted source set;
5. reject or escalate verification failure, strict overflow, missing tokenizer,
   provider failure, or unsupported schema;
6. atomically install only the verified prompt and artifact;
7. retain the source archive, trust anchor, and a rollback pointer;
8. optionally compose a recent uncompressed tail under a separate tested host
   policy;
9. remove old active context only after the verified replacement is durable.

Recent-tail composition, atomic installation, rollback, retention, redaction,
and trust-anchor storage are host responsibilities. The package does not
currently implement this transaction manager.

## Exact next step

The highest-value next work is not another extractor regex. Close the four
confirmed safety defects, align the benchmark estimands, and then freeze the
external evaluation path:

1. write failing regression tests for mutation, safety replacement, provider
   timeout, and selected superseded state;
2. seal verified state;
3. make the built-in protected scanner non-replaceable;
4. add bounded deterministic provider fallback;
5. isolate or reject superseded state in verified execution prompts;
6. align and rename the certificate estimands;
7. add per-system strict-majority decisions;
8. write the dated comparison inclusion protocol;
9. implement the external runner and first adapter;
10. run a diagnostic cohort before freezing claim-bearing evidence.

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
    assert sum(name.endswith('.schema.json') for name in names) == 3
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
docs/BENCHMARKING.md, and docs/THREAT_MODEL.md. Inspect the current branch,
diff, tests, and recorded evidence before changing anything. Preserve the
fail-closed provenance, authority, protected-retention, and claim-boundary
rules. Start with the highest-priority incomplete P0 work in TODO.md, validate
it empirically, and do not claim external superiority without the required
matched evidence. Keep all GitHub authorship and commits solely under
JamesNguyen42.
```

## Decision log

- Use typed memory rather than ordinary summaries.
- Keep exact source spans and immutable hashes for every item.
- Treat goals, constraints, corrections, unresolved questions, exact errors,
  and exact references as protected.
- Run deterministic recovery even when a model extractor is enabled.
- Reject model paraphrases from the trusted typed ledger.
- Treat tool output as untrusted for durable state by default.
- Retain superseded and conflicting state for audit.
- Surface protected overflow instead of silently dropping commitments.
- Refuse normal prompt rendering when verification fails.
- Keep the runtime provider-neutral and standard-library-only.
- Use LRCBench certificates as scoped evidence, never universal product claims.
- Require real downstream task completion and external comparisons for the
  original 50%-better target.
