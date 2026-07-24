# Benchmarking and the 50% claim boundary

> **The local LRCBench certificate is not proof against external systems.** It
> compares this implementation only with the bundled deterministic head, tail,
> and extractive baselines on generated histories. It does not establish that
> the compiler is 50% better than ACON, FoldAgent, AMA-Agent, MemIR, or most
> related technology in the world.

The benchmark is intentionally fail-closed: a run exits successfully only when
all absolute safety/quality gates and a paired relative-gain gate pass. A
certificate failure means “the evidence does not support the claim,” not
necessarily “the program crashed.”

## LRCBench 0.2

`benchmarks/lrcbench.py` is dependency-free, deterministic for a fixed config,
and provider-neutral. Gold atoms remain inside the evaluator and are never
given to a candidate system.

Default configuration:

| Setting | Default |
| --- | ---: |
| Histories | 32 |
| Messages per history | 72 |
| Noise lines per message | 8 |
| Active token budget | 900 |
| Minimum compression | 5x |
| Seed | 56,056 |
| Paired bootstrap samples | 2,000 |
| Bootstrap lower quantile | 0.025 |

Each generated history contains every required adversarial stratum:

- a correction separated from the original requirement and, in some cases,
  buried early in the history;
- mutually conflicting numeric requirements;
- two files with the same symbol name;
- an exact assertion with case-specific numbers;
- long terminal, source, search, schema, and narrative noise;
- an unresolved question that must not become a fact;
- an instruction-shaped quotation in a tool record that is a gold-negative
  authority test.

Cases rotate through six lexical families for goals, API and Python
constraints, replacement versus additive corrections, numeric/database/
polarity conflicts, facts, decisions, unresolved state, diagnostics, and
discarded attempts. This increases template breadth but does not make the
corpus representative of natural production traffic.

All output framing and provenance pointers count against the same deterministic
four-characters-per-token estimate.

## Compared systems

The compiler is evaluated against three matched-budget local baselines:

- `head`: keep content from the beginning until the budget is exhausted;
- `tail`: keep content from the end until the budget is exhausted;
- `extractive`: rank source lines with a query-free TF-IDF-style heuristic and
  retain the highest-scoring lines under budget.

For a bundled-only run, the local frontier result uses the strongest bundled
baseline by critical recall, breaking ties by exact recall, quality, then
system name. This prevents choosing a conveniently weak local comparison after
seeing results. External runs additionally compute a separate decision for
every explicitly registered system.

## Metrics

Per-history measurements include:

- critical-atom recall;
- exact-literal recall, requiring the literal in rendered output plus a
  matching claim and the exact gold-atom source offsets;
- provenance validity;
- semantic-support accuracy and unsupported-claim rate, including token order,
  numeric identifiers, negation, and uncertainty agreement with cited text;
- unresolved-to-fact promotion rate;
- unsupported critical-claim rate, general authority-violation rate, and
  authority accuracy;
- stale-claim rate for inactive or superseded atoms;
- budget compliance and source/active compression ratio.

Provenance credit is deliberately tight: the submitted source id, start, and
end must match the gold atom. A coarse enclosing span is valid source text but
does not earn recall credit for a smaller hidden literal.

The bounded quality score is the harmonic mean of critical recall, exact
recall, provenance validity, one minus unresolved-promotion rate, authority
accuracy, one minus general authority-violation rate, semantic-support
accuracy, and one minus stale-claim rate. A history is perfect only when every
one of those safety/recall requirements is perfect, no unsupported or stale
claim is emitted, the budget is respected, and the configured compression floor
is met.

Claim-bearing critical recall, exact recall, quality, and memory-quality
efficiency are history-weighted. In particular,
`memory_quality_efficiency` is the mean across histories of
`quality_score / active_tokens`. The paired bootstrap resamples the same
per-history quantities, so its estimand matches the corresponding point
estimate. Other diagnostic rates retain their explicitly reported
claim-weighted or corpus-weighted denominators.

## Certificate gates

A local certificate is issued only if all of the following hold:

1. At least 24 histories are evaluated and every required stratum appears in
   at least 24 histories.
2. The strongest baseline has at least 10% critical recall, avoiding a
   degenerate relative comparison.
3. Compiler critical recall is at least 98%.
4. Compiler exact-literal recall is at least 99%.
5. Compiler provenance validity is exactly 100%.
6. Unresolved-to-fact promotion is 0%.
7. Unsupported critical-claim and general authority-violation rates are 0%,
   and authority accuracy is 100%.
8. Semantic-support accuracy is 100% and unsupported-claim rate is 0%.
9. Stale-claim rate is 0%.
10. At least 90% of histories are perfect.
11. The compiler and every baseline respect the matched token budget.
12. Corpus compression meets the configured floor, normally 5x.
13. At least one paired 50% gain basis below passes both its point estimate and
    its configured lower-quantile paired bootstrap margin.

### Relative gain basis A: critical semantic loss

Let critical loss be:

```text
L(system) = 1 - critical_atom_recall(system)
```

The point estimate is:

```text
loss_reduction = (L(baseline) - L(compiler)) / L(baseline)
```

It qualifies only when baseline loss is at least `0.01`, loss reduction is at
least `0.50`, and the configured lower quantile of the paired bootstrap margin
`0.5 * L(baseline) - L(compiler)` is strictly positive.

### Relative gain basis B: memory-quality efficiency

Per-history efficiency is quality divided by active tokens. The aggregate point
estimate is the mean of that quantity:

```text
efficiency_gain = efficiency(compiler) / efficiency(baseline) - 1
```

It qualifies only when the gain is at least `0.50` and the configured lower
quantile of the paired per-history bootstrap margin
`efficiency(compiler) - 1.5 * efficiency(baseline)` is strictly positive.

Raw bounded quality is deliberately not used for a “50% better” ratio: a
perfect score cannot be 50% higher than any baseline above two thirds.

This is a component memory metric, not observed agent task-completion
efficiency. “Completion efficiency” is reserved for successful downstream
tasks per total token or cost.

## Run and audit

From an editable install:

```console
python -m pip install -e .
python -m benchmarks
```

The CI certificate uses exactly 24 histories:

```console
python -m benchmarks --histories 24 --json-out lrcbench-24.json --include-histories
```

Useful overrides:

```console
python -m benchmarks \
  --histories 32 \
  --messages 72 \
  --noise-lines 8 \
  --token-budget 900 \
  --minimum-compression 5 \
  --seed 56056 \
  --bootstrap-samples 2000 \
  --bootstrap-lower-quantile 0.025 \
  --json-out benchmarks/result.json \
  --include-histories
```

The current `lrcbench-report-0.2` JSON report includes the full config, deterministic
dataset SHA-256, aggregate system metrics, optional per-history evidence,
certificate margins/reasons, and two digests. `certificate.evidence_sha256`
binds deterministic comparison evidence. Top-level `report_sha256` also binds
the run-specific envelope: repository commit and dirty state, package and
schema versions, Python/platform, exact command, UTC start, duration, tokenizer
and model identity, baseline revisions, model-service cost, and failures.
Version `0.2` additionally binds frozen external-protocol identity, document
hash, synthetic dataset digest, and registered set into certificate evidence.
Generated reports are measurements, not source fixtures, unless intentionally
reviewed and committed.
Report and corpus-export files are installed through a flushed, `fsync`ed
same-directory atomic replacement, so a pre-replacement failure preserves an
older evidence file rather than truncating it.

Saved current-schema reports and the retained local-only `0.1` snapshot can be
checked without rerunning the scored systems:

```console
python -m benchmarks --verify-report benchmarks/result.json
```

This path performs bounded strict JSON decoding, regenerates the deterministic
dataset hash from the recorded configuration, recomputes both evidence
digests, validates schema/run/comparison accounting, and reconciles included
per-history counts, rates, and aggregates. It intentionally returns success
for an internally valid non-issued certificate. The two embedded hashes remain
self-hashes rather than signatures, so verification does not establish
authorship, preregistration, or fairness.

## External candidate interchange

LRCBench can export the exact generated corpus without gold atoms. A
claim-bearing external run must use the corpus digest frozen in its protocol:

```console
python -m benchmarks --histories 32 --export-corpus lrcbench-corpus.json
```

The export records schema `lrcbench-corpus-0.3`, full generation config,
`dataset_sha256`, versioned producer metadata, a canonical `corpus_sha256`,
case ids, and ordered source events. Producer metadata is bound by the corpus
digest but excluded from the frozen dataset identity. The corpus decoder
verifies the gold-free self-digest before an adapter run. Direct external
artifacts use `lrcbench-candidate-output-0.2`, embed the versioned adapter/model
producer record, and bind every other field with
`candidate_payload_sha256`. Each case supplies `rendered_text` and typed or
untyped claims with exact source id, character offsets, and quote. The standard
runner can ingest a legacy producerless `0.1` adapter payload only to normalize
it to `0.2`; direct imports cannot use that compatibility path. The full schema
and example are in [the benchmark README](../benchmarks/README.md).

Serialized reports, corpora, candidates, and run manifests all enter through
the same strict regular-file JSON reader. It enforces UTF-8, byte, physical-line,
and nesting limits and rejects duplicate keys, NaN/infinity, special files,
and file growth past the limit during hashing. Candidate validation and
per-case aggregation compare the exact byte count and digest observed at each
step, so a concurrent replacement cannot silently change the scored payload.

Import one or more external candidates under the identical frozen generation
config:

```console
python -m benchmarks --histories 32 \
  --external-protocol benchmarks/protocols/external-comparison-v1.json \
  --expected-external-system acon \
  --expected-external-system foldagent \
  --external-run-manifest acon-manifest.json \
  --external-run-manifest foldagent-manifest.json
```

Every external run requires a strict, self-hashed protocol with status
`frozen`. Its registered set is authoritative; repeatable
`--expected-external-system` values are optional assertions, but when present
must equal that complete set. An unexpected file is rejected, while a
registered system with no output remains in the majority denominator as an
invalid non-win. The loader also rejects a protocol/corpus digest mismatch or
an adapter revision that differs from the frozen candidate record. It fails
closed on a schema mismatch, missing or extra cases, duplicate systems, unknown
fields, invalid spans, claims absent from rendered output, or an over-budget
case. External systems do not supply trusted token counts: the harness adds a
canonical claim/provenance sidecar and derives active tokens itself.

Direct `--external-baseline` imports remain useful for interchange diagnostics
inside a frozen protocol, but they cannot count as certificate wins without a
validated bounded-run manifest. A failed manifest contributes its retained
reason and hash to the per-system invalid decision and evidence digest.

The repository also provides a standard process boundary:

```console
python -m benchmarks.external_runner \
  --system SYSTEM \
  --corpus lrcbench-corpus.json \
  --candidate-out SYSTEM-candidate.json \
  --manifest-out SYSTEM-manifest.json \
  --isolation per-case \
  --timeout-seconds 300 \
  --max-stdout-bytes 1000000 \
  --max-stderr-bytes 1000000 \
  --max-candidate-bytes 20000000 \
  --max-memory-mb 32768 \
  --network-isolation-mode host-firewall \
  --network-isolation-evidence network-policy.txt \
  --adapter-revision REVISION \
  --environment-id sha256:DEPENDENCY_LOCK_SHA256 \
  --model-id qwen/qwen3.6-35b-a3b@q4_k_m \
  --model-context-length 8192 \
  --tokenizer-id character-estimate-v1 \
  --inference-concurrency 1 \
  --retry-count 0 \
  --model-service-cost-usd 0 \
  -- ADAPTER_COMMAND {corpus} {candidate} {system} {case_id}
```

It never invokes a shell or overwrites an existing output. The manifest uses an
exclusive atomic install, so another file created during the run wins rather
than being overwritten at commit time. The default `per-case` mode runs cases
sequentially in fresh processes, gives each process a one-case gold-free corpus,
applies per-case time/output limits, validates each candidate independently,
and merges only complete valid coverage. The self-hashed manifest binds the
exact invocation and outcome for every case.
`whole-corpus` mode is retained for diagnostics but is not claim-bearing.

The original corpus is retained as manifest evidence. On import, the loader
reopens its absolute path, verifies its canonical self-digest and exact file
digest, and checks the recorded case count before returning the candidate for
full benchmark-side decoding.

`--max-memory-mb` bounds the adapter process tree with `RLIMIT_AS` on POSIX and
a Job Object assigned before process resume on Windows. It does not account for
a pre-existing inference service outside that process tree. The wrapper is not
a filesystem sandbox and does not establish its own network sandbox, so
unreviewed adapter code still belongs in a separately isolated environment.
Claim controls require a bounded retained host firewall, container, or
network-namespace policy artifact. The runner hashes it before and after
execution and manifest reload rechecks it, but this does not independently
prove that the host enforced the named policy.

The manifest also binds adapter revision, environment id, model identity,
context length, tokenizer, inference concurrency, retries, and model-service
cost. Missing per-case isolation, no enforced process-tree memory limit,
unrecorded identity, a model other than the exact local Qwen Q4 build,
concurrency other than one, or nonzero model service cost is a
certificate-invalid non-win even when the candidate interchange itself is
valid. In current runner schema `lrcbench-external-run-manifest-0.5`, a
claim-eligible identity requires an immutable adapter revision, environment id
`sha256:<dependency-lock-sha256>`, the exact 8192-context Qwen model, evaluator
tokenizer, one slot, zero retries, zero service cost, and retained
network-isolation evidence. Scoring also matches the manifest's isolation,
network-evidence digest, and time/polling/output/candidate/memory limits to the
frozen protocol.

Run the built-in round-trip and negative checks before preparing an adapter:

```console
python -m benchmarks --self-test
```

A report without external candidates is labeled `local-bundled-only`. A report
with a frozen registered external set is labeled `external-inclusive`. Every
registered system receives a win, tie, loss, or invalid decision. The
certificate records the protocol hashes and dataset binding, comparison set,
number of wins, required strict majority, every result, and all margins in its
evidence digest. **External-inclusive does not mean state of the art or
“better than most.”** The dated inclusion protocol and downstream evidence
requirements below still apply.

## Bounded CI performance gate

LRCBench measures memory quality and compression; its run duration is recorded
but is not a performance threshold. CI therefore runs a separate fixed profile:

```console
python -m benchmarks.performance_gate --check \
  --json-out ctxc-performance.json
```

Profile `ci-compile-v1` uses SHA-256-bound, item-dense deterministic source
prefixes. After a 32-event warmup, it measures three compiles at 128 and 256
events and uses each median. The committed limits are 2 seconds, 8 seconds,
8x growth across the doubling, and 64 MiB of peak Python allocations observed
by `tracemalloc` during a separate largest-input compile. The
`ctxc-performance-gate-0.1` report records raw trials, environment, limits,
violations, and its own canonical digest. The growth denominator has a recorded
1 ms floor so sub-resolution first samples do not create meaningless ratios.

The gate times compilation after source records are materialized.
`tracemalloc` is deliberately run separately because tracing changes timing;
it does not measure full process RSS or native allocations. Thresholds are
broad shared-runner regression tripwires, not a portable service-level
objective or evidence about 10,000-to-1,000,000-event behavior. Changing the
workload requires a new profile version, workload digest, thresholds, tests,
and documentation together.

## Current local snapshot

On 2026-07-24, the current implementation's default deterministic 32-history
run issued its `local-bundled-only` certificate. Its dataset SHA-256 was
`421d49585ef9ac96fe2a378f79c18da1791e508789ac0290d3cc5018cda07761`.
The suite collected 878 tests: 873 passed and 5 platform/optional checks were
skipped. The relevant observed metrics were:

| System | Critical | Exact | Provenance | Semantic support | Authority | Stale | Unresolved to fact | Perfect | Compression |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Compiler | 100% | 100% | 100% | 100% | 100% | 0% | 0% | 100% | 32.60x |
| Bundled extractive baseline | 88.2% | 83.6% | 100% | 100% | 0% | 100% | 0% | 0% | 30.13x |

The reviewed report is
[the recorded local evidence](results/lrcbench-local.json). It is a dated
working-tree snapshot, not a permanent guarantee. Most importantly, `ISSUED`
means only that the compiler cleared LRCBench’s local gates against bundled
baselines. It does not mean that the compiler beat any named external system.

## The exact external “50% better than most” bar

No external comparison has been performed by this repository. A defensible
claim that the compiler is “at least 50% better than most related technology
today” requires a separate, preregistered study:

1. Define a dated, independently justified comparison set `R` before running
   experiments. Include all materially comparable, runnable systems meeting
   stated inclusion rules; do not select only weak baselines.
2. Freeze source revisions, models, prompts, tools, retrieval corpora, hardware,
   token accounting, latency/cost accounting, and failure handling.
3. Evaluate on at least two public long-horizon agent suites with held-out real
   trajectories, plus the synthetic adversarial suite. LRCBench alone is not
   enough.
4. Measure downstream task completion as well as memory recall, exactness,
   provenance, authority, cost, and compression. Use blinded or deterministic
   grading where possible.
5. For each external competitor, require all absolute gates above and at least
   one 50% relative basis with a positive preregistered paired bootstrap lower
   bound. Report all comparisons, including failures.
6. “Most” means a strict majority: the qualifying win set `W` must satisfy
   `|W| > |R| / 2`. For four preregistered related systems, at least three must
   independently clear the 50% bar.
7. Have an independent party reproduce the result from released configs and
   raw per-history evidence.

Until those conditions are met, the accurate statement is: **the repository
contains a local synthetic certificate against three bundled baselines, not an
external state-of-the-art certificate.**

The current versioned
[external comparison protocol](../benchmarks/protocols/external-comparison-v1.md)
and [self-hashed JSON manifest](../benchmarks/protocols/external-comparison-v1.json)
form a verified draft. The result-blind screen records observed revisions and
license evidence for ACON, FoldAgent, and AMA-Agent, plus the unresolved MemIR
artifact. Nine explicit blockers cover final candidate decisions/adapters,
dependency locks, adapter memory, inference-service accounting, the natural
cohort, two downstream suites, and frozen downstream samples. The draft
therefore cannot serve as a preregistration yet. Verify its internal state
without claiming readiness:

```console
python -m benchmarks.external_protocol \
  --verify benchmarks/protocols/external-comparison-v1.json
```

Adding `--require-frozen` must continue to fail until those blockers are
resolved.

For the original product-level claim, component memory metrics are not enough.
The strict-majority result must additionally show at least 50% task-failure
reduction or 1.5x successful completions per total token or cost on matched
downstream runs. It must also show zero observed protected and exact misses on
the frozen claim cohorts and at least 5x real-token compression per cohort.

## Known benchmark limitations

- Histories are generated from templates and all cases currently contain all
  five strata; this is broad adversarial coverage, not natural prevalence.
- The bundled compiler run uses the default deterministic rule extractor, and
  its English recognition vocabulary overlaps the generated templates. This
  can overestimate generalization to novel phrasing, other languages, or new
  task domains.
- A separate frozen 64-case
  [novel-English diagnostic](PHRASE_EVALUATION.md) measured 85.3659% precision
  and 87.5% recall while preserving six false positives and five false
  negatives. It reduces template-overlap uncertainty but remains locally
  authored, small, single-message, and non-representative of production
  prevalence.
- The optional `ModelExtractor` is not exercised by this snapshot. Its accepted
  claims are exact complete atomic source spans, not paraphrases, so LRCBench
  does not measure free-form abstractive-summary quality for this project.
- A separate pre-registered
  [exact-Qwen captured-output diagnostic](QWEN_PHRASE_EVALUATION.md) exercised
  `ModelExtractor` on 64 isolated cases. It recorded 5% accepted model-only
  recall and a 96.9231% candidate rejection rate; deterministic recovery raised
  final recall to 87.5%. That result exposes an exact-span reliability gap but
  remains locally authored, small, English-only, and outside LRCBench's
  downstream comparison protocol.
- The later unique-literal response mode removes model offset arithmetic. A
  model-free
  [post-hoc ablation](QWEN_PHRASE_EVALUATION.md#post-hoc-unique-literal-offset-ablation)
  transformed the 65 saved outputs and measured 63.1579% literal-only
  precision, 60% recall, and 2 final verification failures. It did not call
  the model or evaluate the new prompt and may not be presented as
  claim-bearing evidence.
- A new disjoint 64-case corpus and
  [paired pre-result protocol and result](QWEN_PAIRED_EVALUATION.md) record the
  128-call coordinate-versus-unique-literal comparison. Literal mode improved
  model-only recall from 17.5% to 92.5% and F1 from 29.7872% to 82.2222%, but
  reduced precision from 100% to 74%, admitted 13 false positives, and produced
  four final verification failures. After deterministic recovery, its F1
  advantage was only 6.4974 points and its precision was 12.0638 points lower.
  The local CLI offers no seed or temperature control, so the
  one-draw-per-mode comparison remains sensitive to sampling noise even with
  alternating prompt order.
- The four-characters-per-token estimate is deterministic but not a provider
  tokenizer.
- Local baselines are intentionally simple and are not substitutes for current
  research systems.
- Atom recall is a proxy for downstream completion, not completion itself.
- The paired bootstrap quantifies sampling variation over generated histories;
  it does not cover benchmark-design bias or implementation mistakes.
- No registered external set has been run, so the implemented per-system
  strict-majority logic has no external evidence behind it yet.
- A malformed candidate passed directly with `--external-baseline` aborts the
  diagnostic import. Claim-bearing runs should use the bounded manifest path,
  which preserves malformed output as a per-system invalid decision.
