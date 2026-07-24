# External comparison protocol v1

Status: **DRAFT — NOT PREREGISTERED OR CLAIM-BEARING**

Created: 2026-07-24

This file defines the decisions that must be frozen before any external
comparison result is inspected. Empty fields are deliberate blockers. A run
under this draft may be used only to debug adapters and infrastructure.

## Claim and unit of comparison

The component claim is that the loss-resistant context compiler achieves, for
each counted win, either:

1. at least 50% lower history-weighted critical semantic loss; or
2. at least 50% higher history-weighted memory quality per active token;

with a strictly positive paired bootstrap margin at the frozen lower quantile.

The product claim additionally requires at least 50% lower downstream task
failure or 1.5 times as many successful task completions per total token or
cost. Memory recall alone cannot establish the product claim.

“Most” means a strict majority of a dated registered comparison set `R`:
`|W| > |R| / 2`. Missing, invalid, over-budget, timed-out, crashed, or
unreproducible registered systems are non-wins unless an exclusion condition
was written here before any result was observed.

## Evaluation constraints

All systems must use:

- the exact underlying model
  `qwen/qwen3.6-35b-a3b@q4_k_m`;
- local `Q4_K_M` inference only;
- exactly one inference slot and one adapter process at a time;
- no hosted model, model API, paid service, or paid dataset;
- the same model context length, task prompt, tools, source history, active
  token budget, retry count, and stopping rule;
- offline/cached dependencies after setup, with setup provenance recorded;
- the same evaluator-owned tokenizer or deterministic token estimator.

If a related system cannot run with that exact local model under these
constraints, record it as excluded with the technical reason. Do not replace it
after observing another system’s score.

## Inclusion rule

A system is materially comparable when, at the freeze date, it:

1. reduces, retrieves, compresses, or manages active context for long-horizon
   language-model agents;
2. has a publicly inspectable implementation or sufficiently complete
   reproduction artifact;
3. can produce a rendered active context under a matched budget;
4. can expose enough output/provenance information to be adapted without using
   evaluator gold;
5. has a license permitting the intended local evaluation; and
6. can be installed and run without a paid service.

Exclude a system only for a criterion above, an unresolved security risk, or a
documented inability to execute on the available hardware. Record failed setup
attempts rather than silently removing the system.

## Candidate comparison set

The local related-work review identifies these candidates for inclusion
screening:

| Candidate | Inclusion status | Pinned revision | License check | Adapter |
| --- | --- | --- | --- | --- |
| ACON | pending | **TBD** | **TBD** | **TBD** |
| FoldAgent | pending | **TBD** | **TBD** | **TBD** |
| AMA-Agent | pending | **TBD** | **TBD** | **TBD** |
| MemIR | pending | **TBD** | **TBD** | **TBD** |

This is not yet a registered set. Before freezing:

- resolve each pending decision using the inclusion rule;
- pin an immutable source revision and dependency lock;
- record the exact local setup and command;
- keep at least four included systems when materially comparable runnable
  systems exist;
- state the final set `R` explicitly below.

Frozen comparison set `R`: **TBD**

Freeze timestamp: **TBD**

Protocol file SHA-256 at freeze: **TBD**

## Datasets and tasks

The claim-bearing study must include all of:

1. frozen LRCBench synthetic histories, with its dataset and gold-free corpus
   digests;
2. a sealed natural coding-agent history cohort with independent annotations;
3. one public long-horizon coding task suite; and
4. one materially different public tool-using or research task suite.

Natural cohort manifest: **TBD**

Coding task suite and revision: **TBD**

Second task suite and revision: **TBD**

No claim-bearing run may begin while any field above is `TBD`.

## Frozen metrics and estimands

Component primary metrics:

- history-weighted critical-atom recall;
- history-weighted exact-literal recall;
- claim-weighted provenance, semantic-support, and authority accuracy;
- history-weighted memory-quality efficiency:
  `mean_history(quality_score / active_tokens)`;
- stale-claim and unresolved-to-fact rates;
- per-history budget compliance and corpus compression.

Product primary metrics:

- paired task success/failure;
- successful task completions per total token;
- successful task completions per measured cost, which is expected to be zero
  for local model service fees but must still be recorded;
- latency and tool-error rate as secondary operational metrics.

The point estimate and paired bootstrap must use the same per-history or
per-task unit. Do not switch to atom weighting after seeing results.

## Statistical plan

- LRCBench seed: `56056`
- minimum synthetic histories: `24` per registered stratum
- default claim-bearing synthetic histories: `32`
- paired bootstrap samples: `2000`
- bootstrap lower quantile: `0.025`
- component gain threshold: `0.50`
- perfect compiler provenance, semantic-support, and authority accuracy
- zero stale claims and unresolved-to-fact promotions
- at least 98% synthetic critical recall and 99% exact recall for the alpha
  certificate; the final no-loss release gate remains zero observed protected
  and exact misses on every frozen cohort
- at least 5x compression and 100% matched-budget compliance

Sample sizes and task seeds for the two downstream suites: **TBD**

Changing any primary metric, quantile, seed, sample size, comparison system, or
exclusion rule after results requires a new protocol version and complete
rerun.

## Runner and failure policy

Export the gold-free corpus:

```console
python -m benchmarks --histories 32 --export-corpus lrcbench-corpus.json
```

Run each adapter through the shell-free bounded runner:

```console
python -m benchmarks.external_runner \
  --system SYSTEM \
  --corpus lrcbench-corpus.json \
  --candidate-out SYSTEM-candidate.json \
  --manifest-out SYSTEM-manifest.json \
  --timeout-seconds 300 \
  --max-stdout-bytes 1000000 \
  --max-stderr-bytes 1000000 \
  --max-candidate-bytes 20000000 \
  --adapter-revision REVISION \
  --environment-id ENVIRONMENT_LOCK_OR_IMAGE_DIGEST \
  --model-id qwen/qwen3.6-35b-a3b@q4_k_m \
  --model-context-length 8192 \
  --tokenizer-id character-estimate-v1 \
  --inference-concurrency 1 \
  --retry-count 0 \
  --model-service-cost-usd 0 \
  -- ADAPTER_COMMAND {corpus} {candidate} {system}
```

The runner refuses existing output paths, does not invoke a shell, monitors
time and output sizes, rejects corpus modification during execution, hashes
stdout/stderr/candidate evidence, validates the candidate interchange, and
emits a self-hashed manifest. On POSIX, a
`--max-memory-mb` limit is also available. Windows claim-bearing runs require a
separately reviewed memory-limiting sandbox because the standard-library runner
refuses to claim memory enforcement there.

Incomplete identity metadata, any model other than the exact Qwen Q4 variant,
inference concurrency other than one, or nonzero model-service cost makes the
system a certificate non-win. The candidate may still be retained for
interchange diagnostics.

Score all intended systems in one explicitly registered invocation:

```console
python -m benchmarks \
  --expected-external-system SYSTEM_A \
  --expected-external-system SYSTEM_B \
  --external-run-manifest SYSTEM_A-manifest.json \
  --external-run-manifest SYSTEM_B-manifest.json
```

Every name in `R` must appear as `--expected-external-system`, including a
system whose output is missing. Runner manifests, failed setup logs, and invalid
outputs remain in the evidence bundle.

## Blinding and change control

- Adapter inputs contain source events, strata, config, dataset digest, and a
  gold-free corpus digest; they never contain evaluator gold atoms.
- Develop adapters only against a diagnostic corpus before the claim cohort is
  frozen.
- Freeze this protocol, system revisions, adapter revisions, environments, and
  corpus manifests before scoring the claim cohort.
- Do not inspect per-system claim results while deciding inclusion or tuning
  another adapter.
- Report every registered system and every failed run.
- Obtain an independent clean-environment reproduction before publishing an
  external-superiority statement.

## Current boundary

No external system or downstream task suite has been evaluated under this
protocol. The repository’s recorded certificate remains
`local-bundled-only`. This draft and the bounded runner are infrastructure, not
evidence that the compiler is better than any named external system.
