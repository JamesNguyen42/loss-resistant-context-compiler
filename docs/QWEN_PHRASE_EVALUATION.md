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

## Current status

The captured-output harness and offline verifier are implemented and
regression-tested. The first live 64-case report has not yet been recorded in
this checkpoint.
