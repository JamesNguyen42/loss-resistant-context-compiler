# TODO and development roadmap

This is the authoritative engineering backlog for the loss-resistant context
compiler. It separates implemented foundations from work that is still needed
to support the original product claim.

The goal is not merely to generate smaller prompts. The release must preserve
explicit commitments and exact provenance, improve real agent outcomes, and
demonstrate a statistically supported advantage over a strict majority of a
preregistered related-system comparison set.

## Status legend

- `[x]` implemented and covered by current repository evidence;
- `[ ]` not complete;
- **P0** blocks the external 50%-better claim;
- **P1** blocks a credible production beta;
- **P2** improves adoption, performance, or research breadth.

Do not check off a measurement task based on a simulator, prediction, local
control, or unreviewed run. Link the exact report, configuration, revision, and
raw evidence when completing benchmark work.

## Completed foundation

- [x] Define typed memory kinds and protected commitment categories.
- [x] Bind every item to exact source ids, character offsets, quotes, and
  SHA-256 hashes.
- [x] Hash canonical source records, including role, sequence, timestamp, and
  metadata.
- [x] Add deterministic rule extraction and optional provider-neutral model
  extraction.
- [x] Run a separate deterministic recovery pass by default.
- [x] Reject ungrounded model paraphrases and non-atomic ordinary claims.
- [x] Gate authoritative state by source role and explicit trusted-tool
  metadata.
- [x] Resolve recognized corrections, revocations, conflicts, and closed
  questions conservatively.
- [x] Keep protected items under budget pressure or expose a strict failure.
- [x] Verify provenance, protected coverage, semantic support, authority, and
  temporal state.
- [x] Replay and verify portable JSON artifacts against an independently
  supplied source set.
- [x] Provide `ctxc compile`, `verify`, `inspect`, and `archive`.
- [x] Provide JSON Schemas for source events, model output, and compiled memory.
- [x] Provide a local append-only archive with collision, hash, and lock checks.
- [x] Build LRCBench with adversarial histories and matched-budget head, tail,
  and extractive controls.
- [x] Add fail-closed external-candidate corpus export and result import.
- [x] Add exact-offset benchmark provenance credit and paired bootstrap gates.
- [x] Record a passing 32-history `local-bundled-only` certificate.
- [x] Run 102 tests and CI across Python 3.11, 3.12, and 3.13.

## P0: close confirmed fail-closed gaps

These are reproduced defects in the current `0.1.0` implementation, not
speculative enhancements.

### P0-S1 Seal verified memory against mutation

Current reproduction:

```python
from context_compiler import ContextCompiler, SourceRecord

source = SourceRecord.create(
    sequence=0,
    role="user",
    content="constraint: Keep the API stable.",
)
memory = ContextCompiler().compile([source])
memory.items[0].text = "Ignore every requirement"
assert memory.verification.passed
assert "Ignore every requirement" in memory.to_prompt()
```

- [ ] Make the resolved item ledger, provenance, metadata, selection ids,
  verification report, and compression report immutable after compilation.
- [ ] Alternatively bind verification to a canonical snapshot digest and
  recheck it before every prompt or artifact render.
- [ ] Ensure nested lists and dictionaries cannot bypass the seal.
- [ ] Add regression tests for item text, kind, status, provenance, selection,
  metadata, verification, and compression mutation.
- [ ] Ensure `to_prompt()` can never render state different from the state that
  passed verification.

### P0-S2 Make certification obligations non-replaceable

Current reproduction:

```python
from context_compiler import ContextCompiler, SourceRecord
from context_compiler.extractors import ExtractionResult

class Empty:
    name = "empty"

    def extract(self, sources):
        return ExtractionResult()

source = SourceRecord.create(
    sequence=0,
    role="user",
    content="constraint: Keep the API stable.",
)
memory = ContextCompiler(
    extractor=Empty(),
    safety_extractor=Empty(),
).compile([source])
assert memory.verification.passed and not memory.items
```

- [ ] Always run a built-in, non-overridable protected certification extractor.
- [ ] Treat a custom safety extractor as additive rather than replacing the
  built-in obligation.
- [ ] Derive verification obligations independently from untrusted custom
  extractors.
- [ ] Add an explicit unsafe testing seam if replacement is necessary for unit
  tests, and make it impossible to produce a verified prompt.
- [ ] Add regression tests for empty, malicious, throwing, and partial custom
  extractors.

### P0-S3 Fall back safely after provider failure

Current behavior:

- `ModelExtractor` provider exceptions propagate;
- primary extraction runs before deterministic safety extraction;
- a timeout prevents any verified deterministic fallback result.

Current reproduction:

```python
from context_compiler import ContextCompiler, ModelExtractor, SourceRecord

def timeout(_prompt):
    raise TimeoutError("provider")

source = SourceRecord.create(
    sequence=0,
    role="user",
    content="constraint: Keep the API stable.",
)
ContextCompiler(
    extractor=ModelExtractor(timeout)
).compile([source])  # raises before deterministic fallback
```

- [ ] Validate and hash sources before invoking any provider.
- [ ] Run the non-overridable deterministic safety pass before or independently
  of the provider call.
- [ ] Add provider deadlines, cancellation, and bounded response handling.
- [ ] On timeout, malformed output, or outage, return verified deterministic
  memory plus an explicit degradation issue when policy permits.
- [ ] Provide a strict policy that fails without rendering when provider
  extraction is mandatory.
- [ ] Add injected timeout, connection, malformed JSON, and cancellation tests.

### P0-S4 Keep superseded state out of verified execution prompts

Current reproduction:

```python
from context_compiler import (
    CompilationPolicy,
    ContextCompiler,
    MemoryStatus,
    SourceRecord,
)
from context_compiler.io import verify_artifact_dict

old_requirement = SourceRecord.create(
    sequence=0,
    role="user",
    content="constraint: Python 3.11 is required",
)
correction = SourceRecord.create(
    sequence=1,
    role="user",
    content=(
        "Actually, Python 3.12 compatibility is required "
        "instead of Python 3.11."
    ),
)
memory = ContextCompiler(
    policy=CompilationPolicy(include_superseded=True)
).compile([old_requirement, correction])

assert memory.verification.passed
assert verify_artifact_dict(
    memory.to_dict(),
    [old_requirement, correction],
)["passed"]
assert any(
    item.status == MemoryStatus.SUPERSEDED
    and item.id in memory.selected_item_ids
    for item in memory.items
)
```

- [ ] Decide whether `include_superseded` is an audit-only view or an execution
  prompt feature.
- [ ] If audit-only, prevent normal `to_prompt()` rendering and verification
  certification for artifacts that select superseded state.
- [ ] If retained in a prompt, add a separately reviewed non-executable
  historical envelope and demonstrate that downstream models do not treat it
  as current state.
- [ ] Add compile-time and independent-replay regression tests.
- [ ] Keep the default exclusion behavior unchanged.

### P0-B1 Make benchmark estimands internally consistent

The current point estimates and paired bootstrap do not always weight the same
quantity:

- aggregate critical recall is atom-weighted, while the bootstrap averages
  per-history recall;
- aggregate efficiency is mean quality divided by mean active tokens, while
  the bootstrap averages per-history quality divided by tokens;
- the name `completion_efficiency` describes memory-quality efficiency, not
  observed downstream task completion.

- [ ] Choose history-weighted, atom-weighted, or cluster-weighted estimands in
  the preregistered protocol.
- [ ] Use the same estimand for point estimates, bootstrap samples, thresholds,
  and report labels.
- [ ] Align the percentile and confidence label. The implementation uses the
  2.5th percentile, which is the lower endpoint of a two-sided 95% interval or
  a conservative one-sided 97.5% bound, not a one-sided 95% bound.
- [ ] Rename the current metric to `memory_quality_efficiency`.
- [ ] Reserve “completion efficiency” for actual successful downstream tasks
  per token or cost.
- [ ] Add unequal-history-size tests that fail when point and bootstrap
  estimands diverge.

### P0-B2 Implement per-system majority decisions

- [ ] Compute a separate absolute-gate and relative-gain result for every
  preregistered external system.
- [ ] Record a win, tie, loss, invalid run, or excluded result for each system.
- [ ] Count preregistered invalid, timed-out, or failed systems as non-wins in
  the majority denominator unless the exclusion rule was frozen in advance.
- [ ] Enforce `|W| > |R| / 2` rather than issuing one strongest-baseline
  certificate as proof of “most.”
- [ ] Keep the strongest-baseline result as a useful frontier metric with a
  different name.
- [ ] Bind the comparison set and all per-system results into the evidence
  digest.

## P0: establish credible external evidence

### P0-E1 Freeze the related-system comparison protocol

- [ ] Write a dated inclusion and exclusion protocol before running external
  experiments.
- [ ] Define “materially comparable” in terms of active-context reduction,
  long-horizon memory, runnable artifacts, and support for matched evaluation.
- [ ] Select at least four serious candidates when possible so “most” is not a
  comparison against one convenient baseline.
- [ ] Evaluate ACON, FoldAgent, AMA-Agent, and MemIR for inclusion using the
  protocol; record a technical reason for every exclusion.
- [ ] Pin repository revisions, dependency locks, models, prompts, system
  settings, and licenses for every included system.
- [ ] Freeze the primary metrics, failure policy, seeds, sample sizes, and
  statistical test before seeing comparative results.
- [ ] Publish the protocol in a versioned file under `benchmarks/protocols/`.

Acceptance:

- a reviewer can reconstruct the comparison set without knowing which system
  wins;
- adding or removing a competitor after results requires a new protocol
  version and full rerun.

### P0-E2 Build reproducible external-system adapters

- [ ] Add a subprocess or container runner with per-case time, memory, and
  output limits.
- [ ] Keep gold atoms completely outside candidate inputs.
- [ ] Normalize every candidate through
  `lrcbench-candidate-output-0.1`.
- [ ] Record system revision, environment image, model id, tokenizer, context
  limit, Python/platform, exact command, timestamp, latency, failures, retries,
  and cost.
- [ ] Count the final rendered output and provenance ledger under the same
  tokenizer and token budget for every system.
- [ ] Fail closed on missing cases, duplicate cases, malformed spans, unknown
  fields, budget overruns, timeouts, or partial output.
- [ ] Add one adapter directory per included system with setup and reproduction
  instructions.
- [ ] Run each adapter from a clean environment in CI or a documented benchmark
  runner.

Acceptance:

- one command can reproduce each candidate output from the frozen corpus;
- imported outputs pass the interchange validator without manual edits;
- failures remain in the aggregate results rather than being silently dropped.

### P0-E3 Add held-out natural agent histories

- [ ] Define a privacy and licensing policy for real trajectories.
- [ ] Collect public or explicitly consented coding-agent histories from
  multiple repositories, task types, and history lengths.
- [ ] Redact or tokenize credentials and personal data before committing any
  corpus.
- [ ] Preserve realistic terminal noise, repeated files, corrections, failed
  approaches, and tool results.
- [ ] Create train, development, and sealed test splits before tuning.
- [ ] Have at least two annotators label goals, constraints, corrections,
  unresolved items, exact literals, temporal status, and source spans.
- [ ] Measure and report inter-annotator agreement and adjudication rules.
- [ ] Prevent generated-template vocabulary from leaking into held-out
  evaluation policy.
- [ ] Version corpus manifests and hashes without publishing private source
  text.

Acceptance:

- the compiler has zero observed protected or exact misses on the sealed split
  and reports confidence bounds separately;
- results include every history and disclose parser misses by category;
- no held-out case was used to tune extraction rules.

### P0-E4 Measure real downstream task completion

- [ ] Preregister at least two public long-horizon agent suites.
- [ ] Include a coding benchmark where repository state and exact failures
  matter.
- [ ] Include a second tool-using or research benchmark with materially
  different trajectories.
- [ ] Use the same underlying model, tools, prompts, context budget, retry
  budget, and stopping rules for all memory policies.
- [ ] Compare full history where it fits, head truncation, tail truncation,
  extractive retrieval, the context compiler, and included external systems.
- [ ] Run enough independent task instances or seeds for paired confidence
  intervals.
- [ ] Measure task success, critical commitment retention, active tokens, total
  tokens, latency, cost, tool errors, and recovery after compression.
- [ ] Attribute failures to extraction, resolution, selection, rendering, or
  downstream model use.
- [ ] Retain raw task outputs and grader decisions for audit.

Acceptance:

- the compiler improves actual task completion, not only memory atom recall;
- the product-level result reaches at least 50% task-failure reduction or 1.5x
  successful completions per total token or cost against each claimed win;
- the result is reproducible from frozen configs and released evidence;
- unfavorable tasks and failed runs remain in the report.

### P0-E5 Satisfy the external 50% claim gate

- [ ] Apply all absolute LRCBench safety gates to every external-inclusive run.
- [ ] Require either at least 50% critical semantic loss reduction or at least
  50% quality-per-active-token gain against each claimed win.
- [ ] Require a positive paired bootstrap lower bound at the exact percentile
  frozen in the protocol.
- [ ] Win independently against a strict majority of the preregistered set.
- [ ] For the product claim, require at least 50% task-failure reduction or
  1.5x successful completions per total token or cost for each counted win.
- [ ] Require zero observed protected and exact misses on every frozen claim
  cohort; report uncertainty without converting it into a universal guarantee.
- [ ] Report sensitivity to tokenizer, budget, history length, model, task
  suite, and random seed.
- [ ] Ask an independent party to reproduce the result from clean environments.
- [ ] Store the final certificate, raw per-history data, revisions, and hashes
  in a versioned release artifact.

Only after every item above passes may the README claim “at least 50% better
than most related technology” within the exact dated evaluation scope.

## P0: generalization and integrity

### Extraction coverage

- [ ] Add property-based tests for arbitrary source ids, offsets, Unicode,
  whitespace, and clause boundaries.
- [ ] Add grammar-based fuzzing for labels, bullets, conjunctions, negation,
  numbers, units, paths, diagnostics, and corrections.
- [ ] Measure false positives and false negatives on novel English phrasing.
- [ ] Evaluate non-English and mixed-language histories before claiming
  multilingual support.
- [ ] Add domain packs or documented extension hooks rather than continually
  widening one global regular-expression vocabulary.
- [ ] Test extremely long lines, huge tool schemas, binary-looking output, and
  high record counts under explicit resource limits.

### Model extractor evaluation

- [ ] Build provider adapters outside the standard-library core.
- [ ] Evaluate at least one small local model and multiple hosted model
  families.
- [ ] Record extraction recall, rejection rate, latency, cost, and recovery-pass
  contribution separately.
- [ ] Test malicious model output, unknown keys, malformed JSON, NaN/infinity,
  invalid offsets, role forgery, reserved tags, and paraphrases.
- [ ] Decide whether exact-span-only model extraction remains the permanent
  contract or whether a separately verified abstractive path is justified.
- [ ] Never weaken deterministic recovery merely to improve model-only metrics.

### Temporal and semantic state

- [ ] Expand held-out tests for distant corrections and references with weak
  lexical overlap.
- [ ] Test multi-party authority, delegated requirements, conditional
  constraints, and scoped decisions.
- [ ] Add explicit temporal validity or effective-time fields if production
  histories require them.
- [ ] Evaluate contradiction detection beyond polarity, numeric, database, and
  environment heuristics.
- [ ] Preserve ambiguity as unresolved state when a safe deterministic link
  cannot be made.

## P1: live-agent integrations

### Incremental compiler

- [ ] Add an incremental session API that accepts one event at a time.
- [ ] Avoid reparsing the full history after every event while preserving the
  same final artifact as batch compilation.
- [ ] Define invalidation rules for corrections, conflicts, and archive reloads.
- [ ] Add deterministic checkpoint and resume support.
- [ ] Benchmark compile latency and peak memory from 10,000 to 1,000,000 events.
- [ ] Add bounded caches without allowing cached state to bypass verification.

### Agent framework adapters

- [ ] Define a small framework-neutral integration protocol.
- [ ] Add at least one production-quality coding-agent integration.
- [ ] Add adapters for selected popular agent runtimes after verifying their
  role and event semantics.
- [ ] Map tool, developer, system, assistant, and user authority explicitly.
- [ ] Prevent retrieved documents and tool text from being mislabeled as
  authoritative actors.
- [ ] Add examples showing compilation before context-window overflow and
  rehydration of exact source spans on demand.

### Token accounting and selection

- [ ] Add named tokenizer adapters for the models used in evaluation.
- [ ] Include chat framing, tool schemas, and provenance pointers in budget
  accounting.
- [ ] Test hard model context limits with strict overflow behavior.
- [ ] Compare the current priority-per-token selector with constrained
  optimization and learned policies.
- [ ] Preserve protected-item retention as a hard constraint in every selector.
- [ ] Evaluate adaptive budgets based on task phase and downstream model size.

## P1: production hardening

### Security and privacy

- [ ] Add optional secret and sensitive-data preprocessing with an auditable
  redaction map.
- [ ] Define encryption-at-rest and access-control guidance for source archives.
- [ ] Add external signatures or trusted manifests for artifacts and source
  digests.
- [ ] Add monotonic versions or hash chaining for rollback detection.
- [ ] Define safe retention, deletion, and backup workflows.
- [ ] Add security tests for path traversal, archive races, oversized JSON,
  decompression bombs, and hostile metadata.
- [ ] Complete an external security review before a production claim.

### Reliability and operations

- [ ] Add configurable limits for source bytes, record count, line length,
  model response size, archive size, and compile duration.
- [ ] Add structured diagnostics that distinguish invalid input, extraction
  rejection, integrity failure, budget overflow, and policy failure.
- [ ] Embed commit SHA, package and schema versions, Python/platform, command,
  tokenizer id, run timestamp, baseline revisions, latency, cost, and failures
  in every benchmark evidence artifact.
- [ ] Add metrics for item counts, protected overflow, recovery additions,
  conflicts, verification failures, and compile latency.
- [ ] Add crash-safe archive recovery and document stale-lock handling.
- [ ] Test filesystem semantics on Windows, Linux, macOS, and network storage.
- [ ] Define schema migrations and backward-compatibility guarantees.
- [ ] Add reproducible performance regression thresholds to CI.

### Packaging and release

- [ ] Choose the stable distribution name and resolve the current distinction
  between repository name and `lossless-context-compiler`.
- [ ] Publish signed source and wheel artifacts to a test package index.
- [ ] Add release notes, a changelog, semantic-versioning policy, and support
  matrix.
- [ ] Verify installation from a clean Python environment on all supported
  platforms.
- [ ] Add supply-chain scanning and dependency review for optional adapters.
- [ ] Publish `0.2.0` only after the external runner and natural-history corpus
  format are stable.

## P2: developer and research experience

- [ ] Add an HTML or terminal inspector for item provenance, status, conflicts,
  and selection decisions.
- [ ] Add a diff view between two compiled artifacts.
- [ ] Add a benchmark dashboard generated only from signed or hashed reports.
- [ ] Add compact examples for coding, research, operations, and customer
  support histories.
- [ ] Add a plugin guide for custom extractors, verifiers, tokenizers, and
  storage backends.
- [ ] Evaluate hierarchical memory, retrieval from cold archives, and
  task-specific memory views.
- [ ] Explore training a smaller extractor from adversarial histories without
  changing the verifier’s fail-closed contract.
- [ ] Measure whether typed memory improves smaller local models more than
  larger hosted models.

## Proposed milestones

### `0.2.0` — external evaluation infrastructure

- verified-memory mutation, safety-override, provider-fallback, and
  superseded-rendering defects closed;
- benchmark estimands made consistent and renamed accurately;
- frozen comparison protocol;
- reproducible external runner;
- at least two working external adapters;
- exact tokenizer accounting;
- versioned benchmark manifests.

### `0.3.0` — held-out and downstream evidence

- sealed natural-history corpus;
- two public downstream task suites;
- complete paired reports;
- categorized failure analysis.

### `0.4.0` — live integration beta

- incremental compiler;
- at least two agent integrations;
- operational limits and metrics;
- schema migration policy.

### `1.0.0` — evidence-backed production release

- strict majority external 50% gate passes;
- at least 50% task-failure reduction or 1.5x successful completions per total
  token or cost is reproduced against the strict majority;
- zero observed protected and exact misses on frozen synthetic and natural
  cohorts, with confidence bounds reported;
- at least 5x real-token compression on every evaluation cohort and a reported
  combined median at or above the product target;
- results hold across at least three model families, including a documented
  local model in the 7B–8B class;
- security and privacy review is complete;
- clean installation, migration, and operations documentation exists;
- no known path silently drops a protected commitment.

## Recommended next work package

The next chat should start here unless new evidence changes the priority:

1. add failing regression tests for the four confirmed in-process safety
   defects;
2. seal compiled state and make the built-in certification pass
   non-replaceable;
3. implement deterministic fallback for bounded provider failures;
4. isolate or reject superseded state in verified execution prompts;
5. align and rename the benchmark estimands;
6. add per-system strict-majority certificate decisions;
7. create `benchmarks/protocols/external-comparison-v1.md`;
8. define inclusion rules and freeze the initial related-system set;
9. implement a resource-bounded external subprocess/container runner;
10. add one end-to-end external adapter;
11. run the interchange self-test and the adapter on a small
   diagnostic corpus;
12. add tests for every adapter failure mode;
13. freeze a full corpus only after the diagnostic path is reliable;
14. record results without changing the claim boundary.

See [docs/HANDOFF.md](docs/HANDOFF.md) before changing code or benchmark rules.
