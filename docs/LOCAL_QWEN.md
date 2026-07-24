# Exact local Qwen integration

This repository includes an API-free completion adapter for exactly one model
build:

- model key: `qwen/qwen3.6-35b-a3b`
- required variant: `qwen/qwen3.6-35b-a3b@q4_k_m`
- required quantization: `Q4_K_M`
- transport: the local LM Studio `lms` command-line program
- inference concurrency: exactly one loaded slot

The adapter does not use an HTTP model API, does not fetch the LM Studio
catalog, and does not require a paid service. It refuses a different model,
variant, quantization, unloaded model, or multi-slot configuration.

## Preflight

Install or load the model in LM Studio outside this library, with one inference
slot. These local commands show the state that the adapter validates:

```console
lms ls --llm --json
lms ps --json
```

The first response must contain the exact model key, selected Q4 variant, and
`Q4_K_M` quantization. The second must show that model as `idle` or `loaded`
with `"parallel": 1`.

## Python use

```python
from context_compiler import (
    CompilationPolicy,
    ContextCompiler,
    LmsQwenCompletion,
    ModelExtractor,
)

complete = LmsQwenCompletion(
    r"C:\path\to\.lmstudio\bin\lms.exe",
    timeout_seconds=120,
    max_output_chars=1_000_000,
)
extractor = ModelExtractor(
    complete,
    model_id=complete.model_id,
    max_response_chars=1_000_000,
    max_candidates=10_000,
)
compiler = ContextCompiler(
    extractor=extractor,
    policy=CompilationPolicy(
        fail_on_primary_extractor_error=False,
    ),
)
memory = compiler.compile(sources)
```

Before each completion, `LmsQwenCompletion` checks both the on-disk and loaded
model state. A process-wide nonblocking lock serializes inference. The command
has a hard subprocess timeout, disables reasoning output, passes
`--dont-fetch-catalog`, and strips terminal escape sequences.

The default compiler policy is loss-resistant degradation: a timeout, process
failure, invalid model envelope, oversized response, or wholly unusable
candidate set produces deterministic recovered memory and an explicit
verification warning. Use
`CompilationPolicy(fail_on_primary_extractor_error=True)` only when the caller
must abort on provider failure. Independent built-in recovery and protected-item
certification remain active in either mode.

An optional outer `ContextCompiler.compile(..., timeout_seconds=N)` deadline
can isolate and terminate the complete compile process tree. Keep the Qwen
adapter's transport timeout shorter than that outer deadline; otherwise the
outer timeout aborts the run before deterministic provider-failure fallback can
be returned.

## Privacy boundary

LM Studio's CLI receives the extraction prompt as a command-line argument.
Other local users or process-monitoring software may be able to inspect process
arguments. Remove secrets before ingestion and do not treat this transport as a
confidentiality boundary.

## Scoped diagnostic

On 2026-07-24, the exact local Q4 model was exercised once against the public
`examples/auth_timeout.jsonl` history through `ModelExtractor`. The compile
passed verification; deterministic recovery supplied 11 candidates and the
validator rejected 7 model candidates. This is an integration diagnostic, not
a model-quality, latency, cost, or external-system performance claim.

Automated tests mock the CLI boundary and cover exact identity, quantization,
loaded-state and single-slot checks, ANSI removal, timeouts, and refusal of a
different configuration. They do not require the model in CI.

## Unique-literal response mode

`LiteralModelExtractor` can be used with the same completion adapter when the
model can copy a source literal more reliably than it can calculate Python
character offsets. Its separate response schema asks for exact text and
`source_ids` only. The validator rejects absent or repeated text and derives
coordinates from a single exact occurrence before applying the ordinary
authority, uncertainty, exactness, and atomicity checks.

```python
from context_compiler import LiteralModelExtractor

compiler = ContextCompiler(
    extractor=LiteralModelExtractor(
        complete,
        model_id=complete.model_id,
        max_response_chars=200_000,
        max_candidates=32,
    )
)
```

This is not a fuzzy repair mode and does not accept paraphrases. It was designed
after the first 64-case Qwen report, so that already observed corpus cannot
serve as preregistered evidence for the new prompt. See
[Unique-literal model extraction](LITERAL_MODEL_EXTRACTION.md).

In one explicitly post-hoc smoke run against the same public
`examples/auth_timeout.jsonl` history, the compile passed verification with 10
model-extracted items, 1 rejected model candidate, and 4 deterministic
recoveries in a 14-item ledger. The raw response was not retained, so this is
only an integration check; it is not replayable model-quality, latency, cost,
or comparative evidence.

## Frozen corpus-scale evaluator

`benchmarks.qwen_phrase_eval` is frozen before its first live result. It uses
the existing self-hashed 64-case English phrase corpus and requires this exact
model/quantization, an 8,192-token loaded context, one slot, the local CLI, a
240-second per-call timeout, bounded output/candidates, and zero model-service
cost.

Each case receives one sequential live completion. The report binds the exact
`ModelExtractor` prompt and cleaned raw output, then deterministically replays
that capture to score:

- validator-accepted model-only atoms;
- decoded and rejected candidates;
- deterministic recovery contributions;
- final compiler quality and verification;
- per-call latency and service cost.

Offline report verification never calls a model. The pre-registered
2026-07-24 run completed all 64 calls: strict validation accepted 2 of 65
decoded candidates, for 5% model-only recall and a 96.9231% candidate
rejection rate. Deterministic recovery raised final recall to 87.5%, with
85.3659% precision and zero verification failures. Median call latency was
1.968 seconds and model-service cost was USD 0.00. Every negative case
elicited a model candidate, so its accepted-layer negative accuracy reflects
validator rejection rather than model restraint. See
[Exact local Qwen phrase evaluation](QWEN_PHRASE_EVALUATION.md) for commands,
the recorded evidence, exact mismatch audit, raw-output privacy, replay, and
claim limits.

The same saved outputs also support a model-free, explicitly post-hoc offset
ablation. It discards captured coordinates, retains candidate text and source
ids, and replays them through `LiteralModelExtractor`; it does not send that
extractor's prompt to Qwen. The replay measured 63.1579% literal-only
precision, 60% recall, and two final verification failures. See
[the post-hoc method and result](QWEN_PHRASE_EVALUATION.md#post-hoc-unique-literal-offset-ablation).

A new disjoint corpus and
[paired pre-result protocol](QWEN_PAIRED_EVALUATION.md) were frozen before the
first live coordinate-versus-unique-literal comparison. The completed run made
128 sequential calls with alternating order and no retries. Literal mode raised
model-only recall from 17.5% to 92.5% but reduced precision from 100% to 74%
and caused four final confirmation-evidence verification failures. One
coordinate response was also rejected as invalid JSON because LM Studio
loading progress preceded the JSON on stdout. The result is replayable evidence
of a recall/safety tradeoff, not an unconditional recommendation.
