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

## LRCBench 0.1

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

The “strongest baseline” used by the certificate is the one with the highest
critical recall, breaking ties by exact recall, quality, then system name. This
prevents choosing a conveniently weak comparison after seeing results.

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
    its one-sided bootstrap margin.

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
least `0.50`, and the 2.5th percentile of the paired bootstrap margin
`0.5 * L(baseline) - L(compiler)` is strictly positive.

### Relative gain basis B: completion efficiency

Aggregate efficiency is mean quality divided by mean active tokens. The point
estimate is:

```text
efficiency_gain = efficiency(compiler) / efficiency(baseline) - 1
```

It qualifies only when the gain is at least `0.50` and the 2.5th percentile of
the paired per-history bootstrap margin
`efficiency(compiler) - 1.5 * efficiency(baseline)` is strictly positive.

Raw bounded quality is deliberately not used for a “50% better” ratio: a
perfect score cannot be 50% higher than any baseline above two thirds.

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
  --json-out benchmarks/result.json \
  --include-histories
```

The JSON report includes the full config, deterministic dataset SHA-256,
aggregate system metrics, optional per-history evidence, certificate margins,
reasons, and an evidence digest. Record the code revision, Python version,
platform, and command alongside any published result. Generated reports are
measurements, not source fixtures, unless intentionally reviewed and committed.

## External candidate interchange

LRCBench can export the exact generated corpus without gold atoms:

```console
python -m benchmarks --histories 24 --export-corpus lrcbench-corpus.json
```

The export records schema `lrcbench-corpus-0.1`, full generation config,
`dataset_sha256`, case ids, and ordered source events. Run an external system
on that fixed corpus, then adapt its output to
`lrcbench-candidate-output-0.1`. Each case supplies `rendered_text` and typed
or untyped claims with exact source id, character offsets, and quote. The full
schema and example are in [the benchmark README](../benchmarks/README.md).

Import one or more external candidates under the identical generation config:

```console
python -m benchmarks --histories 24 \
  --external-baseline acon-output.json \
  --external-baseline foldagent-output.json
```

The loader fails closed on a schema or dataset-hash mismatch, missing or extra
cases, duplicate systems, unknown fields, invalid spans, claims absent from
rendered output, or an over-budget case. External systems do not supply trusted
token counts: the harness adds a canonical claim/provenance sidecar and derives
active tokens itself.

Run the built-in round-trip and negative checks before preparing an adapter:

```console
python -m benchmarks --self-test
```

A report without external candidates is labeled `local-bundled-only`. A report
with at least one validated candidate is labeled `external-inclusive`, and
that candidate joins the baseline pool from which the strongest critical-recall
comparison is selected. **External-inclusive does not mean state of the art or
“better than most.”** It says only that the listed candidates participated.
The preregistered strict-majority bar below still applies.

## Current local snapshot

On 2026-07-23, the current implementation's default deterministic 32-history
run issued its `local-bundled-only` certificate. Its dataset SHA-256 was
`9dd650433b9d1a018951a7a4745ba31907f6314965e44aee01ea9ecca24389ae`.
All 102 tests also passed. The relevant observed metrics were:

| System | Critical | Exact | Provenance | Semantic support | Authority | Stale | Unresolved to fact | Perfect | Compression |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Compiler | 100% | 100% | 100% | 100% | 100% | 0% | 0% | 100% | 32.60x |
| Strongest bundled extractive baseline | 88.1% | 83.6% | 100% | 100% | 0% | 100% | 0% | 0% | 30.13x |

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
   one 50% relative basis with a positive paired 95% lower margin. Report all
   comparisons, including failures.
6. “Most” means a strict majority: the qualifying win set `W` must satisfy
   `|W| > |R| / 2`. For four preregistered related systems, at least three must
   independently clear the 50% bar.
7. Have an independent party reproduce the result from released configs and
   raw per-history evidence.

Until those conditions are met, the accurate statement is: **the repository
contains a local synthetic certificate against three bundled baselines, not an
external state-of-the-art certificate.**

## Known benchmark limitations

- Histories are generated from templates and all cases currently contain all
  five strata; this is broad adversarial coverage, not natural prevalence.
- The bundled compiler run uses the default deterministic rule extractor, and
  its English recognition vocabulary overlaps the generated templates. This
  can overestimate generalization to novel phrasing, other languages, or new
  task domains.
- The optional `ModelExtractor` is not exercised by this snapshot. Its accepted
  claims are exact complete atomic source spans, not paraphrases, so LRCBench
  does not measure free-form abstractive-summary quality for this project.
- The four-characters-per-token estimate is deterministic but not a provider
  tokenizer.
- Local baselines are intentionally simple and are not substitutes for current
  research systems.
- Atom recall is a proxy for downstream completion, not completion itself.
- The paired bootstrap quantifies sampling variation over generated histories;
  it does not cover benchmark-design bias or implementation mistakes.
