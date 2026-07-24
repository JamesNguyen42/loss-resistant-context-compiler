# LRCBench

> **Evidence boundary:** an `ISSUED` report without external candidates proves
> only a local frontier over the bundled deterministic controls. It is not
> evidence that this project is 50% better than ACON, FoldAgent, AMA-Agent,
> MemIR, or most related technology.

LRCBench is a deterministic, provider-neutral benchmark for long-history
context compilers. It generates histories containing buried corrections,
conflicting requirements, duplicate function/file names, exact numeric
failures, unresolved questions, and gold-negative prompt injections in
untrusted tool noise.

The cases rotate through six lexical families, including replacement and
additive corrections, numeric/database/polarity conflicts, varied facts and
decisions, unresolved phrasings, diagnostics, and discarded attempts. These
are still generated templates rather than natural production prevalence.

Run it from the repository root with the package on `PYTHONPATH`:

```powershell
$env:PYTHONPATH = "src"
python -m benchmarks
```

The harness compares the compiler with matched-budget head truncation, tail
truncation, and a query-free TF-IDF-style extractive baseline. Gold atoms are
not passed to any system. Output framing and provenance pointers count against
the shared token estimate.

The bundled `compiler` candidate uses the default deterministic
`RuleBasedExtractor`; this snapshot does not exercise `ModelExtractor`. That
optional adapter accepts only complete atomic source literals for ordinary
claims and exact source literals for exact claims, not model paraphrases.

## External baselines

The bundled baselines are deterministic controls, not claims about the current
state of the art. Export the exact gold-free corpus before running ACON,
FoldAgent, AMA, or another external implementation:

```powershell
$env:PYTHONPATH = "src"
python -m benchmarks --histories 24 --export-corpus lrcbench-corpus.json
```

The export has schema `lrcbench-corpus-0.3`, the full benchmark config,
`dataset_sha256`, a versioned `lrcbench-corpus-producer-0.1` record, a
`corpus_sha256` over the complete gold-free export, and ordered `source_events`
for every case. Producer metadata changes corpus evidence but is deliberately
excluded from `dataset_sha256`. The export never contains gold atoms. The
corpus decoder rejects any source, configuration, or producer-envelope change
that does not match the self-digest. A direct external candidate must use this
current envelope:

```json
{
  "schema": "lrcbench-candidate-output-0.2",
  "dataset_sha256": "<copy from corpus export>",
  "candidate_payload_sha256": "<canonical SHA-256 of every other field>",
  "system": "acon",
  "producer": {
    "schema": "lrcbench-candidate-producer-0.1",
    "adapter_revision": "<immutable adapter revision>",
    "environment_id": "<frozen environment identifier>",
    "model_id": "qwen/qwen3.6-35b-a3b@q4_k_m",
    "model_context_length": 8192,
    "tokenizer_id": "character-estimate-v1",
    "inference_concurrency": 1,
    "retry_count": 0,
    "model_service_cost_usd": 0.0
  },
  "cases": [
    {
      "case_id": "history-000",
      "rendered_text": "Do not change the public API refresh_token().",
      "claims": [
        {
          "text": "Do not change the public API refresh_token().",
          "kind": "constraint",
          "provenance": [
            {
              "source_id": "h000-s012",
              "start": 41,
              "end": 86,
              "quote": "Do not change the public API refresh_token()."
            }
          ]
        }
      ]
    }
  ]
}
```

The standard bounded runner remains compatible with a raw
`lrcbench-candidate-output-0.1` adapter payload. After validating it, the runner
adds its registered `RunnerIdentity`, computes `candidate_payload_sha256`, and
retains only the normalized `0.2` artifact. This legacy allowance does not
apply to direct `--external-baseline` imports.

Every exported case must occur exactly once. `kind` may be `null` for
untyped/extractive output or one of the typed-memory kinds. Every claim needs
one or more exact source spans, and its text must occur in `rendered_text`.
Recall credit requires the exact gold-atom offsets; a coarse enclosing span
does not count and cannot be used to game provenance coverage.
Do not submit an `active_tokens` field: the harness derives token usage after
adding a canonical claim/provenance ledger, so metadata cannot be free.
Unknown fields, wrong hashes, missing or extra cases, invalid spans, duplicate
system names, and any over-budget case fail closed before a report is emitted.
All report, corpus, candidate, and manifest file reads share a regular-file,
UTF-8, duplicate-key, non-finite-number, byte, line, and JSON-depth boundary.
Direct `--external-baseline` imports default to 20 MB per candidate; override
that explicit boundary with `--max-external-candidate-bytes` when a frozen
protocol requires a different limit.

Run an adapter through the standard shell-free bounded process wrapper:

```powershell
python -m benchmarks.external_runner `
  --system acon `
  --corpus lrcbench-corpus.json `
  --candidate-out acon-output.json `
  --manifest-out acon-manifest.json `
  --isolation per-case `
  --timeout-seconds 300 `
  --max-stdout-bytes 1000000 `
  --max-stderr-bytes 1000000 `
  --max-candidate-bytes 20000000 `
  --max-memory-mb 32768 `
  --adapter-revision REVISION `
  --environment-id ENVIRONMENT_LOCK_OR_IMAGE_DIGEST `
  --model-id qwen/qwen3.6-35b-a3b@q4_k_m `
  --model-context-length 8192 `
  --tokenizer-id character-estimate-v1 `
  --inference-concurrency 1 `
  --retry-count 0 `
  --model-service-cost-usd 0 `
  -- ADAPTER_COMMAND {corpus} {candidate} {system} {case_id}
```

Placeholders are replaced as individual arguments without invoking a shell.
Per-case mode is the default: it creates a one-case gold-free corpus, starts a
fresh bounded adapter process for that case, validates the one-case candidate,
and repeats sequentially before merging the complete output. The manifest
records every exact invocation and its hashes, limits, process status, platform,
and validation outcome. `--timeout-seconds` applies to each case; stdout and
stderr limits apply both per case and to the aggregate.

Keep the original corpus beside the manifest. Manifest loading reopens that
absolute path, revalidates both the corpus self-digest and exact file digest,
and requires the recorded case count to match before any candidate is scored.

`--max-memory-mb` uses `RLIMIT_AS` on POSIX and a race-free Windows Job Object
boundary created before adapter code is resumed. It limits the adapter process
tree, not an already-running inference service outside that tree. The wrapper
is not a filesystem or network sandbox, so execute only reviewed adapter code
in an appropriately isolated environment.

Revision, environment, model, context, tokenizer, inference concurrency,
retries, and service cost are also recorded. Claim-bearing manifests require
per-case isolation, an enforced process-tree memory limit, complete identity
fields, the exact Qwen Q4 model, one inference slot, and zero model-service
cost. `--isolation whole-corpus` remains useful for diagnostics but is a
registered certificate non-win.

Evaluation also recomputes active tokens from the final rendered string for
every bundled or programmatic candidate. A valid character span alone is not
semantic support: ordered content tokens, numeric identifiers, negation, and
uncertainty must agree with the cited text. Unsupported or hallucinated claims,
inactive/superseded claims, and authority-gated goals, constraints, or user
corrections sourced from tool/assistant messages make a history imperfect.
Gold-negative authority violations are detected by provenance overlap, so
paraphrasing a prompt injection cannot evade the check.

Import one or more candidates with repeatable options. The generation options
must match those used for the corpus export:

```powershell
python -m benchmarks --histories 24 `
  --expected-external-system acon `
  --expected-external-system foldagent `
  --external-run-manifest acon-manifest.json `
  --external-run-manifest foldagent-manifest.json
```

Every intended participant must be registered with
`--expected-external-system`. A missing registered output is retained as an
invalid non-win; an unexpected supplied system is rejected. The certificate
computes a separate win, tie, loss, or invalid decision for each registered
system and requires wins against a strict majority. Run
`python -m benchmarks --self-test` for a deterministic export/import
round-trip plus negative checks for hash mismatch, missing cases, and budget
overflow.

## CI performance regression gate

Run the separate bounded compiler profile from the repository root:

```console
python -m benchmarks.performance_gate --check \
  --json-out ctxc-performance.json
```

`ci-compile-v1` warms up on 32 events, then records three compile trials for
fixed 128- and 256-event item-dense prefixes. The warmup and both measured
inputs have committed workload SHA-256 values. `--check` returns nonzero if
either median exceeds its committed ceiling, if latency grows by more than 8x
while the input doubles, or if `tracemalloc` observes more than 64 MiB of
Python allocations during the largest compile. The versioned
`ctxc-performance-gate-0.1` output includes every trial, environment,
thresholds, violations, and a canonical `report_sha256`. Growth uses a
recorded 1 ms denominator floor to avoid amplifying sub-resolution baseline
noise.

The profile excludes source construction from the timer. `tracemalloc` is not
process RSS and does not see every native allocation. Shared-runner timing,
virtualization, and load remain sources of noise, so the ceilings are
deliberately broad. This gate catches major regressions; it does not satisfy
the separate roadmap item to characterize latency and peak RSS from 10,000 to
1,000,000 events.

`--external-baseline` remains available for direct interchange diagnostics,
but a registered system without a validated run manifest is an invalid
certificate non-win. A failed manifest contributes its exact bounded-run
failure reason to the per-system decision and evidence digest.

The certificate fails closed. It requires at least 24 histories in every
registered adversarial stratum, near-perfect critical and exact recall,
perfect provenance, semantic support, and authority accuracy, no stale claims,
unresolved-to-fact promotions, unsupported claims, or unsupported critical
claims, at least 5x compression,
no budget overrun, a 90% history-perfect rate, and both an aggregate and paired
bootstrap result establishing either at least 50% less critical semantic loss
or at least 50% more quality per active token than the compared system. Critical
recall, exact recall, quality, and memory-quality efficiency use history-weighted
point estimates, matching the paired history bootstrap. The lower quantile is
configurable and defaults to `0.025`. The
bounded 0-to-1 quality score is deliberately not used for relative gain: doing
so would make a perfect candidate unable to beat a baseline above 0.667 by 50%.
A failed run exits with status 2; this means the evidence does not support the
claim, not that the harness crashed.

Without a registered external set, reports and claims are explicitly labeled
`local-bundled-only`; that certificate compares only the included controls
and must not be presented as a state-of-the-art comparison. With a registered
external set, the scope is `external-inclusive`.

## Recorded local snapshot

The reviewed 2026-07-24 default run covers 32 histories and dataset SHA-256
`421d49585ef9ac96fe2a378f79c18da1791e508789ac0290d3cc5018cda07761`.
The suite collected 669 tests alongside it: 664 passed and 5
platform/optional checks were skipped.

| System | Critical | Exact | Provenance | Support | Authority | Stale | Promotion | Perfect | Compression |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Compiler | 100% | 100% | 100% | 100% | 100% | 0% | 0% | 100% | 32.60x |
| Extractive | 88.2% | 83.6% | 100% | 100% | 0% | 100% | 0% | 0% | 30.13x |

See the [recorded JSON report](../docs/results/lrcbench-local.json). Its
certificate scope is `local-bundled-only`; these numbers contain no external
system result.

Use `--json-out benchmarks/result.json --include-histories` to retain auditable
per-history measurements. Generated result files should not be treated as
source fixtures unless intentionally reviewed and committed.
Reports and corpus exports are installed by same-directory atomic replacement.
Runner manifests use exclusive atomic installation, so a competing destination
created during the run is preserved rather than overwritten.
JSON reports use schema id `lrcbench-report-0.1`. Their `run_metadata` records
the repository revision/dirty state, package and interchange versions, exact
command, timestamp, duration, environment, tokenizer/model, baseline revisions,
cost, and failures. `report_sha256` binds that whole envelope while the
certificate's `evidence_sha256` remains deterministic across equivalent runs.

Verify a newly generated current-schema report offline:

```console
python -m benchmarks --verify-report benchmarks/result.json
```

The verifier reads a bounded regular UTF-8 file through duplicate-key,
non-finite-number, depth, and size checks. It regenerates `dataset_sha256`,
recomputes both report digests, validates comparison and run metadata, and,
when present, reconciles every raw per-history count with its recorded rate and
aggregate. Verification confirms internal consistency, not authorship or
experimental fairness. A valid `NOT ISSUED` report passes verification because
certificate outcome and document integrity are separate questions.

The dated inclusion, failure, estimand, and freeze rules are in the
[draft external comparison protocol](protocols/external-comparison-v1.md).
It remains explicitly non-claim-bearing while required fields are `TBD`.
