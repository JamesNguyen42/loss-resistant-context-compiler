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
