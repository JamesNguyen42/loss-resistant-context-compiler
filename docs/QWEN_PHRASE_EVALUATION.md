# Exact local Qwen phrase evaluation

This protocol evaluates the optional `ModelExtractor` with exactly the local
Qwen build already allowed by the repository. It is intentionally separate
from the deterministic phrase report and from LRCBench.

The harness was frozen before its live 64-case result was observed. A weak
result is evidence about the current exact-span model path, not a reason to
tune the production validator or deterministic recovery against this corpus.

## Frozen scope

The input is the existing
[`novel_english_phrases_v1.json`](../benchmarks/data/novel_english_phrases_v1.json)
corpus:

- schema: `ctxc-phrase-corpus-0.1`;
- corpus SHA-256:
  `19a31bf953f7b8b46a39336d08a18e7011eb2b0d326b7dd4e0ce7947e7e15712`;
- 64 isolated English cases;
- 40 exact-span positive cases and 24 semantic negatives;
- fixed roles, source text, kinds, and annotations.

The corpus was locally curated from an earlier local-Qwen draft before the
deterministic compiler evaluation. It is therefore neither independent of the
model family nor representative of production traffic. It is English-only,
small, isolated to one physical line per case, and contains no downstream task
outcomes.

## Exact model and resource identity

A live report is accepted only with:

- model `qwen/qwen3.6-35b-a3b@q4_k_m`;
- quantization `Q4_K_M`;
- loaded context length 8,192;
- exactly one inference slot;
- local LM Studio CLI transport;
- no network model API;
- 240-second timeout per completion;
- 200,000 output characters per completion;
- 200,000 response characters at the validator boundary;
- at most 32 decoded candidates per isolated case;
- zero model-service cost.

The adapter preflights the on-disk and loaded identity before every call. The
harness processes cases in frozen corpus order with no parallel case workers.
It records the LM Studio executable SHA-256 and CLI version, but those fields
are self-reported local evidence rather than remote attestation.

LM Studio receives the prompt as a process argument. The bundled corpus is
public; do not reuse this raw-output capture path for private histories without
a separate privacy review and content preprocessing.

## Capture once, replay without a model

For each case, the harness:

1. builds the normal `ModelExtractor` prompt from one immutable source record;
2. hashes that exact prompt;
3. invokes the exact local completion once and measures only that completion
   boundary, including its per-call adapter preflight;
4. stores the cleaned raw output or only the exception type on transport
   failure;
5. binds successful raw output with character count and SHA-256;
6. replays the capture through the strict `ModelExtractor`;
7. compiles it again with normal independent deterministic recovery and
   verification;
8. scores model-only accepted atoms, recovery additions, and final compiler
   atoms separately.

Offline verification does not load or call Qwen. It reconstructs every prompt,
requires the recorded prompt digest, replays every stored raw output or
recorded failure, and recomputes every deterministic case analysis and
aggregate. The bounded loader rejects duplicate keys, non-finite values,
excessive bytes, long lines, and deep JSON.

The report deliberately stores raw model outputs so validation behavior can be
reproduced exactly. Its self-hash detects internal modification but is not a
signature or proof that Qwen produced those bytes. An author able to fabricate
a complete report can recompute its hashes. The repository revision, dirty
state, executable digest, and capture hashes are audit evidence, not a trusted
execution attestation.

## Metric definitions

`model_only` scores only candidates accepted by `ModelExtractor` before
compiler recovery or temporal resolution.

`candidates` records:

- decoded candidate count when a valid envelope exposes one;
- validator-accepted candidates;
- candidate-level rejections and their rate;
- all rejection events, including output-level failures separately from the
  candidate denominator;
- cases whose primary extraction degraded.

`deterministic_recovery` records:

- final ledger atoms tagged `verifier-recovered`;
- recovered atoms matching the frozen expected ledger;
- expected atoms added after a model-only miss;
- cases receiving any or true-positive recovery;
- final recall minus model-only recall.

`final_compiler` scores the complete resolved ledger and counts independent
verification failures. `latency` reports per-call count, total, mean, median,
nearest-rank p95, minimum, and maximum over sequential calls. Timing is local
wall-clock evidence, not a portable service-level objective. `cost` is model
service cost only and is fixed at USD 0.00; it does not estimate electricity,
hardware depreciation, or operator time.

## Commands

Run the live evaluation only from a clean revision:

```console
python -m benchmarks.qwen_phrase_eval \
  --lms C:\path\to\lms.exe \
  --json-out docs/results/qwen-novel-english-phrases-v1.json
```

The serialized command replaces the Python executable, corpus, LM Studio
executable, and destination paths with descriptive placeholders. Offline
verification requires that exact normalized command. Corpus and LM Studio
executable digests bind their relevant identities without publishing local
usernames or directory layouts.

Verify a saved report without LM Studio:

```console
python -m benchmarks.qwen_phrase_eval \
  --verify-report docs/results/qwen-novel-english-phrases-v1.json
```

The live command returns success when valid evidence is captured, regardless
of model quality. Low recall, high rejection, recovery dependence, or a final
verification failure is an evaluation result rather than a harness error.

## Claim boundary

This diagnostic can support a narrow statement about one dated local model
build, validator revision, corpus, and machine. It cannot establish:

- provider or model-family generalization;
- natural-history or multilingual quality;
- independence from corpus authoring;
- downstream agent task completion;
- comparison with another memory system;
- a “50% better” or production-readiness claim.

Those claims still require the frozen natural-history, downstream-task,
external-system, and independent-reproduction work in
[TODO](../TODO.md) and [Benchmarking](BENCHMARKING.md).

## Recorded result

The pre-registered harness was committed as
`f79fe2bdd39e046d79fc41491f8ada87e8b42438` before the live result was
observed. The clean-tree run started at `2026-07-24T14:46:16.094054+00:00`
and completed all 64 sequential calls without a transport error.

The reviewed evidence is
[`qwen-novel-english-phrases-v1.json`](results/qwen-novel-english-phrases-v1.json).
Its report SHA-256 is
`db2e054537e366056a8fd482f1a8e17b8fa0d02163a08ea79ef3c682af79c171`.
Offline verification reconstructs and replays all 64 prompts and outputs.

| Boundary | Result |
| --- | ---: |
| Model-only atoms | 2 TP, 0 FP, 38 FN |
| Model-only precision / recall / F1 | 100% / 5% / 9.5238% |
| Model-only positive exact matches | 2 / 40 |
| Decoded / accepted / rejected candidates | 65 / 2 / 63 |
| Candidate rejection rate | 96.9231% |
| Cases with degraded primary extraction | 62 / 64 |
| Expected atoms recovered after a model miss | 33 |
| Recall gain from deterministic recovery | 82.5 percentage points |
| Final atoms | 35 TP, 6 FP, 5 FN |
| Final precision / recall / F1 | 85.3659% / 87.5% / 86.4198% |
| Final exact matches | 55 / 64 |
| Final verification failures | 0 |
| Mean / median / nearest-rank p95 call latency | 2.003 / 1.968 / 2.182 seconds |
| Total measured model-call time | 128.164 seconds |
| Model-service cost | USD 0.00 |

Qwen emitted at least one candidate for every case, including every one of the
24 semantic negatives. Consequently, its 100% accepted-layer negative
accuracy does not mean the model withheld false claims: all 24 negative
candidates were rejected before admission.

Of the 63 rejection events, 28 cited a span beyond the source, 26 supplied text
that did not exactly equal the cited literal, 8 violated source-role authority,
and 1 cited a non-atomic clause. Thus 54/63 rejections (85.7143%) directly
involved the exact-text/offset contract. This is evidence that the current
prompted model path is not a reliable exact-span extractor on this corpus. It
is also evidence that strict admission prevented those candidates from
silently entering memory.

The final compiler metrics exactly match the separately recorded deterministic
phrase diagnostic: deterministic recovery, not the model path, supplied most
of the useful coverage, and the model produced no aggregate improvement on
this corpus. This comparison remains local, small, English-only, and
non-independent. No production extractor, validator, recovery rule, or corpus
annotation was changed after observing the run.

The later opt-in
[`LiteralModelExtractor`](LITERAL_MODEL_EXTRACTION.md) removes model-supplied
coordinate arithmetic while retaining exact literal admission. It was designed
in response to these observed failures. Results from replaying or rerunning
this already seen corpus with that interface are necessarily post-hoc and
cannot replace a separately frozen evaluation.

## Post-hoc unique-literal offset ablation

The repository retains one explicitly non-claim-bearing analysis of those
already observed outputs. `benchmarks.qwen_literal_ablation` first verifies the
complete frozen Qwen report, then transforms each strict captured candidate by
retaining its kind, text, optional fields, and cited source ids while
discarding only the model-supplied integer `start` and `end` values. It replays
the transformed response through `LiteralModelExtractor` and the full
compiler. It does not call Qwen, evaluate the unique-literal prompt, alter the
frozen corpus, or repair candidate semantics.

Verify the saved analysis offline:

```console
python -m benchmarks.qwen_literal_ablation \
  --verify-report docs/results/qwen-literal-offset-ablation-v1.json
```

The reviewed report is
[`qwen-literal-offset-ablation-v1.json`](results/qwen-literal-offset-ablation-v1.json).
Its SHA-256 is
`a321f20c4a7c13f76a99ab85e7af699f02f9498b11a59f56278991c9cf0de97c`;
it binds the frozen source-report hash, every raw-output hash, transformed
response and hypothetical target-prompt hash, per-case result, aggregate, and
the statements `claim_bearing: false`, `target_prompt_evaluated: false`, and
`live_model_calls: 0`.

| Boundary | Result |
| --- | ---: |
| Source / transformed candidates | 65 / 65 |
| Coordinate pairs ignored | 65 |
| Accepted / rejected literal candidates | 38 / 27 |
| Literal-only atoms | 24 TP, 14 FP, 16 FN |
| Literal-only precision / recall / F1 | 63.1579% / 60% / 61.5385% |
| Change from coordinate model-only | +22 TP, +14 FP, -22 FN |
| Deterministic recovery additions | 22, including 16 expected atoms after a primary miss |
| Final atoms | 40 TP, 20 FP, 0 FN |
| Final precision / recall / F1 | 66.6667% / 100% / 80% |
| Final exact matches | 46 / 64 |
| Final verification failures | 2 |
| Model calls / network model API / service cost | 0 / no / USD 0.00 |

Removing coordinate arithmetic admitted far more useful candidates, but it
also admitted semantic false positives and two single-source
`confirmed_fact` labels that failed independent confirmation-evidence
verification. The result therefore diagnoses two distinct problems: generated
offsets were a major failure source, and exact copying alone does not make
model selection or typing trustworthy. A new held-out corpus and prompt must
be frozen before any claim-bearing live comparison.
