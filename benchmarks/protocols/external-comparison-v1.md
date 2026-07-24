# External comparison protocol v1

Status: **DRAFT — NOT PREREGISTERED OR CLAIM-BEARING**

Created: 2026-07-24

This file defines the decisions that must be frozen before any external
comparison result is inspected. Its self-hashed machine-readable companion is
[`external-comparison-v1.json`](external-comparison-v1.json). The JSON records
every unresolved item as an explicit blocker and can be verified with:

```console
python -m benchmarks.external_protocol \
  --verify benchmarks/protocols/external-comparison-v1.json
```

Adding `--require-frozen` intentionally fails while this document is a draft.
A run under this draft may be used only to debug adapters and infrastructure.

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
| ACON | screening | `d63f9ae18959dc7215ff62899c94c5e8c56847ae` | MIT file verified | required |
| FoldAgent | screening | `58a2d6964ecebe99940529eace50a0558901b8a5` | Apache-2.0 file verified | required |
| AMA-Agent | screening | `ddfd319e0be33424288c13806f1eafc63e625b59` | MIT file verified | required |
| MemIR | screening | No public implementation revision identified | No code license identified | blocked on artifact |

The first three revisions above were observed directly with `git ls-remote` on
2026-07-24. Their repositories provide dependency declarations, but not a
complete resolved environment lock suitable for a claim-bearing run. ACON's
documented quick start uses an OpenAI key; FoldAgent documents remote grading
and a local vLLM path; AMA-Agent documents both hosted APIs and local vLLM.
None yet has an adapter proven against the exact one-slot LM Studio Qwen
transport. The MemIR v1 paper describes prompts and experiments but does not
identify a public implementation artifact.

This is not yet a registered set. Before freezing:

- resolve each pending decision using the inclusion rule;
- pin an immutable source revision and dependency lock;
- identify the claim environment as
  `sha256:<dependency-lock-sha256>` so retained run evidence can be matched
  exactly to that lock;
- retain the exact lock file at the path recorded by every runner manifest;
- record the exact local setup and command;
- keep at least four included systems when materially comparable runnable
  systems exist;
- state the final set `R` explicitly below.

Frozen comparison set `R`: **not frozen; no systems registered**

Freeze timestamp: **not assigned while status is draft**

Protocol file SHA-256 at freeze: **not assigned while status is draft**

Adapter process-tree memory limit (MB): **unresolved blocker
`adapter-memory-limit`**

Pre-existing inference-service executable digest, memory metric, and ceiling:
**unresolved blocker `inference-service-accounting`**. The runner can enforce
the measured contract, but these values must be selected from the evaluation
host without looking at comparison results.

Network-isolation mode and retained host/container policy evidence:
**unresolved blocker `network-isolation-evidence`**

## Datasets and tasks

The claim-bearing study must include all of:

1. frozen LRCBench synthetic histories, with its dataset and gold-free corpus
   digests;
2. a sealed natural coding-agent history cohort with independent annotations;
3. one public long-horizon coding task suite; and
4. one materially different public tool-using or research task suite.

Natural cohort manifest: **unresolved blocker `natural-history-cohort`**

Coding task suite and revision: **unresolved blocker `coding-task-suite`**

Second task suite and revision: **unresolved blocker `second-task-suite`**

No claim-bearing run may begin while any blocker exists.

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

Sample sizes and task seeds for the two downstream suites: **unresolved
blocker `downstream-samples`**

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
  --isolation per-case \
  --timeout-seconds 300 \
  --max-stdout-bytes 1000000 \
  --max-stderr-bytes 1000000 \
  --max-candidate-bytes 20000000 \
  --max-memory-mb MEMORY_LIMIT_MB \
  --dependency-lock-evidence DEPENDENCY_LOCK \
  --network-isolation-mode NETWORK_MODE \
  --network-isolation-evidence NETWORK_POLICY_EXPORT \
  --inference-service-pid INFERENCE_SERVICE_PID \
  --max-inference-service-memory-mb SERVICE_MEMORY_LIMIT_MB \
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

The runner refuses existing output paths, does not invoke a shell, monitors
time and output sizes, rejects corpus modification during execution, hashes
stdout/stderr/candidate evidence, validates the candidate interchange, and
emits a self-hashed manifest. It hashes the dependency lock before execution,
detects mutation, and requires those bytes to define `environment_id`. Every
case runs sequentially in a fresh process against a one-case gold-free corpus;
a failure is retained without allowing partial merged output. POSIX enforces
`--max-memory-mb` with `RLIMIT_AS`. Windows creates the process suspended,
assigns and verifies a Job Object with per-process and aggregate limits, and
only then resumes adapter code.

The adapter memory limit covers only the adapter process tree. The separate
service options capture a pre-existing inference process's PID creation token
and executable digest and sample Windows working set or Linux RSS at the same
20 ms cadence. Disappearance, restart, executable change, or ceiling breach
invalidates the adapter without terminating the service. This is monitoring,
not containment; a shorter between-poll spike can be missed, and the runner
cannot prove the adapter used that PID or automatically include separate helper
processes. Configured service accounting is supported on Windows and Linux and
fails preflight elsewhere. The final protocol must freeze the service
executable digest, memory metric, and ceiling before a claim-bearing run. The
wrapper does not create a filesystem or network sandbox. A claim-bearing
manifest must instead retain and hash the host
firewall, container, or network-namespace policy artifact established outside
the runner; reload verifies that exact file but cannot independently prove the
host enforced it. Retain the corpus and network evidence at the absolute paths
recorded in each manifest; import revalidates their byte counts and digests.

Whole-corpus isolation, no enforced adapter memory limit, incomplete identity
metadata, any model other than the exact Qwen Q4 variant, inference concurrency
other than one, or nonzero model-service cost makes the system a certificate
non-win. Claim-eligible runner schema
`lrcbench-external-run-manifest-0.7` also requires an immutable adapter
revision, retained dependency-lock bytes matching the canonical `sha256:`
environment identity, context length 8192, the evaluator tokenizer, one slot,
zero retries, and zero service cost, plus bounded retained network-isolation
evidence and at least two stable service-memory samples within the ceiling. The
scorer rejects any identity, lock/network-evidence digest, service
metric/executable digest/ceiling, isolation mode, timeout, output, candidate,
adapter memory, or 20 ms enforcement-polling limit that differs from the frozen
protocol. The candidate may still be retained for interchange diagnostics.

Score all intended systems in one explicitly registered invocation:

```console
python -m benchmarks \
  --external-protocol benchmarks/protocols/external-comparison-v1.json \
  --expected-external-system SYSTEM_A \
  --expected-external-system SYSTEM_B \
  --external-run-manifest SYSTEM_A-manifest.json \
  --external-run-manifest SYSTEM_B-manifest.json
```

The verified frozen protocol is authoritative for `R`; every registered system
remains in the denominator even when its output is missing. If
`--expected-external-system` assertions are supplied, they must enumerate the
complete frozen set exactly. The harness refuses a draft protocol, a different
synthetic dataset digest, an unregistered output, or a run/candidate adapter
revision that differs from the frozen revision. Runner manifests, failed setup
logs, and invalid outputs remain in the evidence bundle.

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
