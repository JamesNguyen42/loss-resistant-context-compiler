# Held-out paired Qwen extractor evaluation

This document records the pre-result protocol and retained result for comparing
the coordinate-bearing `ModelExtractor` prompt with the unique-literal
`LiteralModelExtractor` prompt on a new frozen corpus. The same exact local
Qwen build, annotations, limits, and process were used for both modes, with
call order alternating by frozen case index.

The protocol and corpus were committed on clean revision
`b6d7095714e01c7e1d43a34ea86f8bee9235794b` before the first target-prompt
output was observed. A weak result, transport failure, false positive, or
verification failure is evidence to retain, not permission to edit the corpus,
validator, recovery pass, or scoring rules and rerun.

## Frozen corpus

The input is
[`heldout_literal_phrases_v1.json`](../benchmarks/data/heldout_literal_phrases_v1.json):

- schema: `ctxc-phrase-corpus-0.1`;
- scope: `local-heldout-paired-extractor-diagnostic`;
- corpus SHA-256:
  `ab5eff1220ad4fb663886dc7d237552c83e602cdee531e7d377d64423ca0e45d`;
- 64 isolated one-line English cases;
- 40 positive cases and 24 semantic negatives;
- 5 positive cases for each of `goal`, `constraint`, `unresolved`, `decision`,
  `confirmed_fact`, `discarded_attempt`, `exact_error`, and
  `exact_reference`;
- 8 untrusted-tool injections, 8 assistant-authority negatives, and 8 benign
  keyword mentions.

Case ids and source text are disjoint from the earlier 64-case corpus. The
authoring envelope records no draft model, no model API, and zero model-service
cost. Cases and exact-span labels were audited before any coordinate or
unique-literal target call on this corpus.

A deterministic perfect-oracle dry run is allowed before freeze because it
does not invoke a model or reveal target behavior. Both validators must be
capable of accepting all 40 annotated atoms when given structurally perfect
responses. The test suite enforces that precondition.

This remains a locally authored, English-only, single-message diagnostic. It
is not independent annotation, natural-history prevalence, or downstream task
evidence.

## Frozen paired procedure

`benchmarks.qwen_paired_eval` counterbalances order by frozen zero-based case
index:

- even index: coordinate, then literal;
- odd index: literal, then coordinate.

Consequently, a complete run records 128 model calls. Case concurrency is
exactly one, each mode receives one draw per case, and retries per call are
zero. Alternation prevents either prompt from receiving the same systematic
order position. A failed call is captured by exception type and replayed as a
provider failure; it is not repeated. The harness rejects a dirty repository,
a corpus other than the frozen hash, a changed normalized command, and a live
destination that already exists. Final report installation is exclusive, so a
destination created during the run is not overwritten.

The protocol object embedded in every report fixes:

- analysis type `pre-result-heldout-paired-extractor-evaluation`;
- coordinate schema `model-extraction.schema.json`;
- literal schema `model-extraction-literal.schema.json`;
- a 10,000,000-character aggregate literal-location work cap;
- two calls per case, one case at a time, with alternating order;
- one captured draw per case and mode;
- no retries;
- no quality threshold;
- retention of weak results;
- no post-result validator tuning.

There is deliberately no pass/fail model-quality threshold. The live command
succeeds when it produces complete, internally valid evidence, regardless of
which extractor performs better.

## Exact model and resource identity

The report is accepted only with:

- model `qwen/qwen3.6-35b-a3b@q4_k_m`;
- quantization `Q4_K_M`;
- loaded context length 8,192;
- exactly one inference slot;
- local LM Studio CLI transport;
- no network model API;
- one adapter preflight before every completion;
- 240-second timeout per completion;
- at most 200,000 output and validator-response characters per completion;
- at most 32 decoded candidates per case and mode;
- zero model-service cost.

The harness records the executable SHA-256, CLI version, Python/platform
identity, clean repository commit, UTC start, duration, and normalized command.
Those fields are self-reported local audit evidence, not remote attestation.
USD 0.00 describes model-service charges only; it excludes hardware,
electricity, depreciation, and operator time.

The installed `lms chat` CLI exposes neither a seed nor a temperature option.
This protocol therefore cannot pair identical random draws across prompts.
Alternating order reduces systematic order bias but does not remove sampling
noise; the result is one captured draw per case and mode, not an estimate over
repeated samples.

## Capture, scoring, and replay

Every mode-specific case record binds:

- the exact prompt SHA-256;
- cleaned raw output, character count, and SHA-256, or a retained exception
  type;
- decoded, accepted, and rejected candidates;
- rejection reasons and primary degradation;
- accepted model-only atoms before recovery;
- deterministic recovery additions;
- final compiler atoms and verification issues.

Each mode reports completion outcomes, candidates, model-only micro
precision/recall/F1, per-kind metrics, exact cases, negative accuracy,
deterministic-recovery contribution, final compiler quality, verification
failures, latency, and zero service cost.

The paired comparison reports literal-minus-coordinate deltas for accepted and
rejected candidates, degraded cases, model-only quality, final quality, and
verification failures. It also counts cases where both modes, only coordinate,
only literal, or neither produced an exact primary or final result. Positive
deltas are not automatically improvements: more accepted candidates can raise
recall while worsening precision or verification.

The report schema is
`ctxc-qwen-paired-extractor-eval-report-0.1`. Its top-level self-hash binds the
corpus, protocol, system, run, metrics, and every raw capture. Offline
verification rechecks the self-hash, reconstructs both prompts, replays every
capture through its original extractor and the full compiler, regenerates all
case and aggregate evidence, and requires the recorded run duration to cover
the summed call latency.

Self-hashes detect inconsistent modification relative to checked-in values.
They do not prove that Qwen produced the bytes. An author able to replace and
rehash the complete evidence can fabricate an internally consistent report.

## Recorded held-out result

The 2026-07-24 run completed all 128 strictly sequential calls and produced
[the self-hashed captured-output report](results/qwen-heldout-paired-extractors-v1.json).
Offline replay verifies report SHA-256
`eb76a5accefdb50b906bb5c4658432d70958be112ef2a1febb1d71e957c9e6d2`
against the frozen corpus and both extractor implementations.

| Metric | Coordinate | Unique literal | Literal minus coordinate |
| --- | ---: | ---: | ---: |
| Reported / accepted / rejected candidates | 66 / 7 / 59 | 65 / 50 / 15 | -1 / +43 / -44 |
| Degraded cases | 57 | 15 | -42 |
| Model-only TP / FP / FN | 7 / 0 / 33 | 37 / 13 / 3 | +30 / +13 / -30 |
| Model-only precision | 100% | 74% | -26 points |
| Model-only recall | 17.5% | 92.5% | +75 points |
| Model-only F1 | 29.7872% | 82.2222% | +52.435 points |
| Model-only exact cases | 31 / 64 | 52 / 64 | +21 |
| Negative cases without a primary prediction | 24 / 24 | 15 / 24 | -9 |
| Recovery TP after a primary miss | 19 | 1 | -18 |
| Final TP / FP / FN | 26 / 8 / 14 | 38 / 21 / 2 | +12 / +13 / -12 |
| Final precision | 76.4706% | 64.4068% | -12.0638 points |
| Final recall | 65% | 95% | +30 points |
| Final F1 | 70.2703% | 76.7677% | +6.4974 points |
| Final exact cases | 43 / 64 | 44 / 64 | +1 |
| Final verification failures | 0 | 4 | +4 |

The primary paired exactness counts were 22 both exact, 9 coordinate-only,
30 literal-only, and 3 neither. After deterministic recovery they were 37
both exact, 6 coordinate-only, 7 literal-only, and 14 neither.

The result confirms that generated coordinates were a large admission
bottleneck for this model and corpus: unique-literal output greatly increased
model-only recall and F1. It did not produce an unqualified improvement.
Literal mode admitted 13 model-only false positives, reduced primary negative
accuracy from 100% to 62.5%, reduced final precision, and produced four
`fact_without_confirmation_evidence` verification failures on
`h-fact-02` through `h-fact-05`. Its three primary false negatives were the
complete `discarded_attempt` atoms in `h-discarded-02`, `h-discarded-04`, and
`h-discarded-05`; one response also split a failed attempt into truncated
`discarded_attempt` and `progress` claims.

Coordinate mode remained precise after strict admission but recalled only
7/40 expected atoms without recovery. Its rejection evidence includes 19
out-of-bounds spans, 18 ordinary literal mismatches, 14 exact-literal
mismatches, and eight authority, tool-role, or correction violations. One
otherwise successful `h-n-authority-08` CLI invocation was degraded as
`invalid_json` because LM Studio model-loading spinner lines preceded the JSON
on stdout. The report retains those bytes; the harness did not silently strip
or retry them. A later adapter hardening accepts only a bounded exact-model
loading prefix followed by one unambiguous JSON object. It is covered with
synthetic transport fixtures and does not alter or rescore this frozen result.

Both modes completed 64 CLI invocations without a caught transport exception.
Coordinate calls took 131.374205 seconds in aggregate and literal calls
113.825091 seconds; the full run lasted 245.380297 seconds. The report records
one inference slot, no retries, no network model API, and USD 0.00 in model
service charges. Because the CLI exposed no sampling controls, all differences
remain one-draw point estimates on a small, locally authored corpus.

## Commands

The live evaluation was run once from the clean frozen revision with:

```console
python -m benchmarks.qwen_paired_eval \
  --lms C:\path\to\lms.exe \
  --json-out docs/results/qwen-heldout-paired-extractors-v1.json
```

The serialized command replaces local paths with placeholders. Verify the
saved report later without LM Studio:

```console
python -m benchmarks.qwen_paired_eval \
  --verify-report docs/results/qwen-heldout-paired-extractors-v1.json
```

Raw outputs can reproduce source text. This capture path is approved only for
the bundled public corpus unless a separate privacy review and preprocessing
protocol authorizes another input.

## Claim boundary

This paired diagnostic can compare two prompts for one exact dated local model
build on one locally authored corpus. It cannot establish:

- provider or model-family generalization;
- natural-history or multilingual quality;
- downstream task completion;
- improvement over an external memory system;
- independence from repository authoring;
- a 50%-better, state-of-the-art, or production-readiness claim.

No external system is part of the protocol, there is no preregistered
statistical superiority threshold, and 64 isolated cases are too narrow for a
product claim. The result must be published with all false positives,
transport errors, recovery dependence, and verification failures visible.
