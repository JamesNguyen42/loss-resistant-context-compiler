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

## Novel English phrase diagnostic

`python -m benchmarks.phrase_eval` is a smaller, separate generalization
diagnostic. Its frozen 64-case corpus has 40 exact-span positive cases and 24
semantic negatives spanning tool prompt injection, assistant authority, and
benign keyword mentions. The first deterministic run recorded 35 true
positives, 6 false positives, 5 false negatives, 85.3659% micro precision,
87.5% micro recall, and no compiler-verification failures.

The corpus and report have independent self-hashes. `--verify-report` strictly
loads both artifacts and requires exact deterministic replay:

```console
python -m benchmarks.phrase_eval \
  --verify-report docs/results/novel-english-phrases-v1.json
```

This locally authored diagnostic is not independently annotated, representative
of production traffic, multilingual, or an evaluation of `ModelExtractor`.
See [the complete method and mismatch audit](../docs/PHRASE_EVALUATION.md).

## Exact local Qwen captured-output diagnostic

`python -m benchmarks.qwen_phrase_eval` evaluates the exact local Qwen Q4
adapter against the already frozen 64-case phrase corpus. It invokes one case
at a time, captures one prompt-bound raw output per case, and scores strict
model-only acceptance separately from deterministic recovery and final
compiler output. It also records candidate rejection, per-call latency, and
zero model-service cost.

The saved report can be verified without LM Studio because every captured
output is replayed through `ModelExtractor` and the full compiler:

```console
python -m benchmarks.qwen_phrase_eval \
  --verify-report docs/results/qwen-novel-english-phrases-v1.json
```

The pre-registered 2026-07-24 run completed 64 calls without transport errors.
Qwen emitted 65 candidates across all 64 cases; strict validation accepted 2
and rejected 63 (96.9231%). Model-only recall was 5%. Deterministic recovery
added 33 expected atoms after model misses, producing 85.3659% final precision,
87.5% final recall, and zero verification failures. Median call latency was
1.968 seconds and model-service cost was USD 0.00. All 24 negatives elicited a
candidate, so accepted-layer negative accuracy is principally a validation
result. Raw outputs make replay possible but can expose source text, so this
path is intended for the public corpus. See
[the exact protocol and result audit](../docs/QWEN_PHRASE_EVALUATION.md).

The later `LiteralModelExtractor` unique-literal prompt is not evaluated by
that frozen report. Reuse of the observed corpus for it is post-hoc; a new
claim-bearing run needs a separately frozen corpus and report protocol.

## Model-free unique-literal offset ablation

`python -m benchmarks.qwen_literal_ablation` strictly verifies the frozen Qwen
report, discards only captured integer coordinates, retains candidate text and
cited source ids, and deterministically replays the transformed outputs through
`LiteralModelExtractor`. It makes no model call and marks both
`claim_bearing: false` and `target_prompt_evaluated: false`.

```console
python -m benchmarks.qwen_literal_ablation \
  --verify-report docs/results/qwen-literal-offset-ablation-v1.json
```

The saved self-hashed report transformed all 65 candidates, accepted 38, and
rejected 27. Literal-only output recorded 24 TP, 14 FP, and 16 FN: 63.1579%
precision, 60% recall, and 61.5385% F1. Recovery produced a final 40 TP,
20 FP, and 0 FN, but two cases failed independent verification. This isolates
the benefit of deterministic coordinate derivation while showing that exact
copying does not solve semantic selection or kind assignment. See
[the evidence and claim boundary](../docs/QWEN_PHRASE_EVALUATION.md#post-hoc-unique-literal-offset-ablation).

## Held-out paired extractor protocol and result

`python -m benchmarks.qwen_paired_eval` is frozen before any target output on
the new
[`heldout_literal_phrases_v1.json`](data/heldout_literal_phrases_v1.json)
corpus. The corpus has 64 cases (40 positives and 24 semantic negatives), is
self-hashed, and has no ids or source strings in common with the first phrase
corpus.

Each case receives one coordinate prompt and one unique-literal prompt. Order
alternates by frozen case index, all 128 calls execute sequentially through the
one-slot exact local Qwen adapter, and there are no retries. The installed LM
Studio CLI exposes no seed or temperature flag, so the protocol records one
uncontrolled draw per case and mode and treats sampling noise as a limitation.
The live path requires a clean revision and an exclusive new report
destination.

The completed run retained all 128 calls. Coordinate mode recorded 7 TP, 0 FP,
and 33 FN before recovery (100% precision, 17.5% recall, 29.7872% F1); literal
mode recorded 37 TP, 13 FP, and 3 FN (74% precision, 92.5% recall, 82.2222%
F1). Literal final F1 was only 6.4974 points higher, final precision was
12.0638 points lower, and four literal compiles failed verification. One
coordinate capture retained LM Studio loading-status stdout as an
`invalid_json` failure. The exact corpus hash, report schema, metrics, replay
rules, command, resource identity, result audit, and claim boundary are in the
[paired protocol](../docs/QWEN_PAIRED_EVALUATION.md) and
[self-hashed report](../docs/results/qwen-heldout-paired-extractors-v1.json).

## SWE-bench Verified source intake

[`suites/swebench_verified_v1.json`](suites/swebench_verified_v1.json) pins the
official 500-row `test` split at dataset revision
`91aa3ed51b709be6457e12d00300a6a596d4c6a3`, selects the full physical row
order without sampling, and records aggregate hashes for every full source row
and allowlisted candidate input plus all 500 ordered per-instance bindings. It
separately pins the official harness at
tag `v4.1.0`, revision `726c5461e2ef52d83cf1ea2107870a8bb3328d57`.

Verify the source-only descriptor without optional dependencies:

```console
python -m benchmarks.swebench verify-suite
```

The local `materialize-source` operation requires exact optional
`pyarrow==25.0.0`; projection and verification remain dependency-free. Keep
the downloaded Parquet, canonical source snapshot, raw 32-byte opaque-ID key,
and generated task document under ignored `build/`. The canonical snapshot
contains evaluator gold and must not enter a candidate mount. Only each
allowlisted task payload, with exact fields `opaque_task_id` and
`problem_statement`, is candidate-facing.

The HMAC ID is pseudonymous, not unlinkable: public problem text and fixed
suite order can still identify a task. A raw-Git preparation boundary now has
synthetic-mirror coverage plus 62 retained public outcomes across six
repositories: 56 verified preparations, six exact
`tree-symlink-forbidden` policy refusals, and 438 unattempted rows. These are
not candidate mounts or public executions; no public candidate, model, agent,
or grader has run, no score exists, and the pinned dataset card declares no
license. See the complete
[source, projection, and execution boundary](../docs/SWEBENCH_EVALUATION.md).

### SWE-bench repository preparation

`benchmarks.swebench_repository` is a source/sdist-only coordinator boundary;
it is not part of the dependency-free wheel. `verify_local_bare_mirror()`
accepts only an absolute local bare mirror and absolute Git executable. It
requires SHA-1 object format, a single canonical GitHub origin URL and mirror
refspec, and rejects shallow/partial/promisor repositories, alternates,
grafts, replace refs, config includes, extra remotes, linked worktrees,
worktree-specific config, links/reparse points, and special mirror entries.
The configured origin URL is evidence, not authentication.
On POSIX, mirror regular files must also have link count one; a mirror derived
from another local object store must therefore be copied with hardlinking
disabled (for example Git's `--no-hardlinks`) before verification.

`prepare_task_repository()` derives repository, exact base commit, instance
id, ordinal, and source-record hash from a revalidated canonical source row.
An outer literal-process lifecycle owns a release-digest-bound staged
standalone worker and all Git descendants. Isolated `python -B -I -S` startup
precedes the worker, Git command pipes retain no more than their configured
caps, and batch readers require clean protocol EOF. The worker reads raw
`commit`, `tree`, and `blob` frames through `git cat-file`, recomputes their
Git object ids, preflights bounded counts and bytes, and never calls checkout,
restore, archive, or a filter. Only modes
`100644` and `100755` are materialized. Symlinks, gitlinks, invalid UTF-8,
non-NFC/control/device/`.git`-alias paths, case-fold collisions, oversized
objects, and unsafe output types fail closed.

The output is an unpredictable fresh directory containing only independent
regular files; `.git`, history, remotes, the source manifest, and evaluator
gold are absent. Empty directories and unexpected metadata are rejected. The
parent reopens the exact mirror commit, compares its complete live raw export,
and independently rescans and rehashes every output path through guarded
regular-file descriptors, including checks for extended attributes and NTFS
alternate streams. Its self-hashed
`ctxc-swebench-repository-preparation-0.1` manifest remains coordinator-only
and serializes exact limits while asserting no author authentication. All
origin, redistribution, mount, network, execution, grader, score, usefulness,
and claim-readiness fields remain false. Synthetic Git tests prove the
mechanism. The ignored public evidence currently proves that 56 exact selected
trees prepared and replayed locally and that six other selected commits were
refused by the link-free export policy; it does not prove safe mounting,
candidate execution, or task success.

### SWE-bench prediction ledger

`benchmarks.swebench_prediction` is another source/sdist-only coordinator
boundary. Its `ctxc-swebench-prediction-ledger-0.1` document is authoritative
for the complete selected denominator in physical source order. Construction
replays the source/key-bound task projection and every claimed successful
repository preparation; candidate-facing captures remain keyed only by the
opaque task id until the coordinator performs the canonical-instance mapping.

The reconciler closes every selected row over one of four dispositions:
repository not attempted, repository preparation refused, prepared with no
prediction, or prediction recorded. Missing, duplicate, unexpected, malformed,
and oversized candidate outputs become retained nonpredictions or bounded
protocol violations instead of aborting after partial work or silently
shrinking the cohort. Patch text is never normalized: strict UTF-8, including
NUL, U+FEFF, line endings, and final-newline state, is preserved exactly once
in the emitted JSONL. Unicode surrogate code points are rejected rather than
normalized or repaired. The ledger retains only byte counts and SHA-256
bindings for patches, canonical JSONL lines, and the complete artifact.
Contiguous capture ordinals let replay de-duplicate task/protocol evidence and
re-enforce candidate count and aggregate retained-capture bytes separately from
per-patch and artifact bytes. Construction, load, write, and replay require the
separately retained expected code/model/agent/prompt/tool/controller identity;
matching those caller-supplied digests does not authenticate their producer.

The deterministic interchange has exactly one line per selected task and the
official fields `instance_id`, `model_name_or_path`, and `model_patch`.
Nonpredictions use `model_patch: null`; the ledger, not an upstream loader, is
the denominator and disposition authority. The JSONL and its self-hashed
ledger are coordinator evidence only: they do not prove that a model produced
the bytes or that a repository was mounted, executed, graded, resolved, or
useful. All execution, grading, score, and claim-readiness flags remain false,
and no public-suite prediction artifact is checked in. Rejected raw candidate
content is deliberately not retained, so its status and the duplicate/unknown
protocol rows remain coordinator assertions rather than independently
replayable capture evidence. The separate controller-run ledger below can bind
future raw capture bytes and workspace observations, but it still does not
authenticate their producer or establish isolated candidate execution.

### SWE-bench controller-run ledger

`benchmarks.swebench_run` is the source/sdist-only controller/run boundary; it
is not part of the dependency-free wheel. `build_run_ledger()` (also exported as
`run_swebench_controller()`) accepts the revalidated source/task projection,
complete preparation outcomes, one ordered `CandidateWorkspace` binding per
selected task, an independently expected `SystemIdentity`, and a fixed
`ControllerSpec`. `decode_run_ledger()`, `verify_run_ledger()`,
`load_run_ledger()`, and `write_run_ledger()` preserve the same strict replay
boundary. The artifact schemas are `ctxc-swebench-run-ledger-0.1` and
`ctxc-swebench-controller-result-0.1`.

For each prepared row with a supplied workspace, the coordinator first requires
the caller-provided mutable tree to equal the verified preparation, byte and
path for byte and path. It writes a canonical request containing only
`opaque_task_id` and `problem_statement`, then appends that request path and the
workspace path to the fixed literal argv under protocol
`append-request-json-and-workspace-path-v1`; the controller's working directory
is the workspace. Workspaces, preparation trees, bare mirrors, and the fresh run
artifact root may not overlap. Links, reparse points, hard-linked files, special
entries, unsafe ancestors, and unexpected artifact labels fail closed. The
coordinator does not create a mount or copy, contain, or clean the workspace.
Traversal and launch remain pathname-based rather than descriptor-pinned, so a
concurrent nested-directory or executable substitution is an accepted host
trust boundary, not an adversarial-filesystem guarantee.

Every source-selected row remains in physical order. Repository-not-attempted,
preparation-refused, missing/mismatched workspace, launch, timeout, stream,
process, cleanup, nonzero-exit, malformed/oversized output, final-workspace
observation, and captured-run outcomes are all explicit dispositions. Raw
stdout and stderr are retained as bounded exact prefixes with observed/captured
counts and hashes. A patch capture is constructed only by replaying complete
successful stdout as one strict five-field controller envelope. Initial and
final workspace summaries plus a deterministic file delta remain separate from
the patch capture. Within one internally consistent ledger, a final scan failure
cannot replace an already reported launch or process disposition. Exit status,
termination, timing, and cleanup fields remain unauthenticated coordinator
observations and can be rewritten by a producer that recomputes the self-hash.

Token methods are limited to `not_measured`, `provider_reported`, or a named
`tokenizer:*` value. The ledger records bounded prompt/model totals, and it
records only a count/byte/hash summary of the ordered trajectory while the
trajectory itself remains inside raw stdout. These values, the controller
executable/argv/environment digests, the expected system identity, and the
self-hash provide consistency bindings, not signatures, producer attestation,
loaded-code proof, or model/token/trajectory authentication. The underlying
literal lifecycle owns a Windows Job or an escapable POSIX process group; it
does not establish mount, filesystem, network, user, PID, or image isolation.

The focused synthetic suite currently records 42 passes and one skip in
158.55 seconds. The skip is the directory-symlink-root regression, unavailable
because this Windows token lacks directory-symlink privilege. No public
candidate, model, or agent ran. Candidate execution authentication, candidate
mount creation, every isolation field, hidden-test application, grading,
resolution, external score, usefulness, producer authentication, and claim
readiness remain false. `prediction_captures()` is only an interoperability
bridge into the separate full-cohort prediction ledger after live replay; it is
not result or score evidence.

### SWE-bench text-patch composition preflight

`benchmarks.swebench_patch` is a coordinator-only, source/sdist-only mechanical
preflight for candidate and hidden **text** patches. It is absent from the
dependency-free wheel and adds no installed schema. Its strict self-hashed
evidence schema is `ctxc-swebench-patch-composition-0.1`.

`build_patch_composition()` first revalidates the canonical
`VerifiedSweBenchSource` and its source-bound `PreparedSweBenchRepository`; a
caller-supplied mapping cannot replace the hidden patch. It rescans the
prepared base, copies that immutable input into separate candidate and hidden
temporary trees, and invokes the exact native Git text-patch parser through
the literal-argv lifecycle. It records bounded tree summaries plus complete
changed-path pre/post states and deltas. Equal, ancestor, and descendant
effective paths count as conflicts; overlap work is bounded and near-linear in
changed paths and path depth.

The evidence has exactly two statuses. `overlap-detected-not-composable`
retains the conflicting paths and no composition evidence.
`disjoint-composition-preflight-verified-not-a-grader` creates a third clean
copy, replays candidate then hidden deltas, and requires both deltas and every
candidate-owned final state to match. The prepared input is reverified after
temporary work and is never mutated. `decode_patch_composition()` is a strict
structural and self-hash check only. Public verify, load, write, and replay
operations canonically snapshot their source and preparation inputs, bind the
live prepared-tree summary, and require exact semantic patch replay. The
structural `retained_disjoint` property fails closed on malformed evidence; it
is not a safety, authenticity, isolation, or claim-readiness decision.

Inline and payload-free binary diffs, extended copy/rename headers, file-mode
changes, and new/deleted file modes other than exact regular-file `100644` are
rejected before the native parser. Accepted ordinary text content/add/delete
patches still pass through an unsandboxed native Git process with no native
memory or filesystem quota; tree and artifact limits are verified, not
enforced by the operating system. A `PatchCompositionError` exposes a
machine-readable `stage` and fixed disposition
`preflight-exception-not-a-cohort-result`. The outer complete-denominator
ledger must retain and map that exception; this single-task preflight cannot
turn it into a cohort result or silently remove a row.

Candidate-code execution, hidden-test execution, official grading, official
results, external scores, usefulness, candidate-mount/network/filesystem
isolation, native quotas, Git-parser sandboxing, and claim readiness remain
false. The 56 prepared, 6 policy-refused, and 438 unattempted public-row counts
are unchanged, and no public candidate or grader was run.

## tau2-bench v1.0.1 text-core source intake

[`suites/tau2_text_v1.json`](suites/tau2_text_v1.json) and
`benchmarks.tau2` form a source/sdist-only, dependency-free intake boundary for
the separately result-blind-reviewed tau2-bench v1.0.1 half-duplex text
core. The descriptor binds the annotated tag and peeled commit, exact raw Git
objects and required blobs, the `base` split for airline, retail, and
manual-policy telecom, and an exact 278-row manifest. Within each domain the
verifier reproduces the upstream loader's physical `tasks.json` order filtered
by base-split membership; it deliberately does not use the literal split-array
order. Per-row bindings retain only SHA-256 task-key and full-source hashes, not
raw upstream task ids. The descriptor remains coordinator-only and must not be
mounted into a future candidate process.

Verify the self-hashed descriptor without tau2-bench installed:

```console
python -m benchmarks.tau2 verify-suite
```

Given an absolute, already populated local bare mirror and an absolute Git
executable, `verify-source` revalidates the mirror, reads bounded raw tag,
commit, tree, and blob objects with literal argv, recomputes their Git and
SHA-256 identities, strictly parses the task/split JSON only after byte
verification, and reconstructs the complete cohort. It neither clones nor
fetches, imports tau2-bench, opens committed result payloads, launches a
candidate, constructs a user simulator, or invokes a grader.

The source-task candidate allowlist is empty. Future candidate requests may
contain only a separately specified normal-agent prompt/policy, serialized tool
schemas, observed agent-view messages and coordinator-produced tool results;
the full Task and every scenario, initial state, assertion, golden action,
grader field, DB, result, and upstream checkout remain trusted-side only. The
upstream in-process agent factory is prohibited because it passes live tools
and the full Task to registered factories. See the complete
[evaluation and secrecy contract](../docs/TAU2_EVALUATION.md).

This checkpoint fixes source and selection facts only. Repository-origin and
signature authentication, legal/redistribution review, dependency
reproduction, candidate adapter, runtime boundary, filesystem/network/process
isolation, simulator and grader evidence, execution, reward, score,
usefulness, and claim readiness remain false.

## External baselines

The bundled baselines are deterministic controls, not claims about the current
state of the art. The self-hashed
[machine protocol](protocols/external-comparison-v1.json) is currently a
verified draft, not a claim-bearing registration:

```powershell
python -m benchmarks.external_protocol `
  --verify benchmarks/protocols/external-comparison-v1.json
```

`--require-frozen` intentionally rejects that draft until every recorded
blocker is resolved. After a new protocol version is frozen, export its exact
gold-free corpus before running ACON, FoldAgent, AMA, or another included
implementation:

```powershell
$env:PYTHONPATH = "src"
python -m benchmarks --histories 32 --export-corpus lrcbench-corpus.json
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
    "environment_id": "sha256:<dependency_lock_sha256>",
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

### Generic literal process lifecycle

`benchmarks.literal_process.run_literal_argv()` is a source/sdist-only building
block for a future isolated benchmark controller. It accepts only a bounded
non-string argument sequence with an absolute executable, an absolute working
directory, a bounded explicit environment, and immutable finite limits. It
does no placeholder expansion and always passes `shell=False`.

Stdout and stderr are drained concurrently. Each stream records its total
drained byte count but retains at most `limit + 1` bytes and that prefix's
SHA-256, so a fast writer cannot create an unbounded spool file or unbounded
in-memory evidence. Overflow terminates the owned lifecycle. The monotonic
deadline begins before containment setup, is checked before launch and before
Windows resume, and wins when completion cannot be proven before it. Results
retain the exact limits and separate setup, process, cleanup, and total wall
durations.

Windows children start suspended, enter a per-process and aggregate Job Object,
and resume only after membership verification. POSIX leaders stay waitable
until anchored process-group cleanup completes, but a child can deliberately
leave that group. The generic lifecycle does not use unsafe `preexec_fn` and
therefore rejects POSIX memory-limit requests; a separately verified
container/VM controller must provide POSIX memory, filesystem, mount, PID,
user, and network isolation. `swebench_containment_claim_ready` is always
false. This helper has not executed or scored SWE-bench and does not replace
the LRCBench-specific candidate/manifest validator below.

Run an adapter through the standard bounded, non-interpolating process wrapper:

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
  --dependency-lock-evidence requirements.lock `
  --adapter-entrypoint-evidence adapter.py `
  --adapter-source-root adapter-source `
  --pass-environment HF_HOME `
  --network-isolation-mode host-firewall `
  --network-isolation-evidence network-policy.txt `
  --inference-service-pid INFERENCE_SERVICE_PID `
  --max-inference-service-memory-mb SERVICE_MEMORY_LIMIT_MB `
  --expected-inference-service-executable-sha256 SERVICE_EXECUTABLE_SHA256 `
  --adapter-revision REVISION `
  --environment-id sha256:DEPENDENCY_LOCK_SHA256 `
  --model-id qwen/qwen3.6-35b-a3b@q4_k_m `
  --model-context-length 8192 `
  --tokenizer-id character-estimate-v1 `
  --inference-concurrency 1 `
  --retry-count 0 `
  --model-service-cost-usd 0 `
  -- ADAPTER_COMMAND {corpus} {candidate} {system} {case_id}
```

Placeholders are replaced as individual arguments without shell interpretation.
On macOS only, the resulting literal argv passes through the fixed runner-owned
shell pre-limiter described below.
Per-case mode is the default: it creates a one-case gold-free corpus, starts a
fresh bounded adapter process for that case, validates the one-case candidate,
and repeats sequentially before merging the complete output. The manifest
records every exact invocation and its hashes, limits, process status, platform,
and validation outcome. `--timeout-seconds` applies to each case; stdout and
stderr limits apply both per case and to the aggregate.
Stream byte/hash evidence is read from runner-retained descriptors rather than
reopened paths. At or below the cap it covers the full observed stream; above
the cap it records a `cap + 1` prefix witness while the one-time descriptor-size
observation still forces the corresponding limit failure. Growth after that
observation is not chased.

Keep the original corpus beside the manifest. Manifest loading reopens that
absolute path, revalidates both the corpus self-digest and exact file digest,
requires the recorded case count to match, reconstructs each deterministic
one-case corpus, and verifies the ordered executed prefix, exact self/file
digests, and runner-owned temporary path layout before any candidate is scored.
Retained claim-control evidence paths use canonical POSIX or Windows spelling.
Their Windows components reject invalid characters, trailing dots/spaces,
alternate data streams, and reserved device aliases, including aliases with
ASCII spaces immediately before an extension; ambiguous evidence paths are
rejected, never normalized.
For a complete run, it also reconstructs every normalized one-case candidate
envelope from the registered producer and corresponding raw merged case, then
matches its self-digest to the case audit. It also rehashes the retained
dependency lock and requires its digest to define
the recorded `environment_id`. The retained adapter entrypoint must be one of
the exact command arguments and an exact record in the bounded recursive
inventory of `--adapter-source-root`. Loader replay rehashes every regular file
in that link-free tree. Its tree digest, the entrypoint bytes, and the canonical
command-contract digest are later matched to the frozen per-system protocol
fields. The contract replaces bound runtime, entrypoint, source-root, corpus,
candidate, system, and case paths with typed tokens, so clean-host absolute
paths can differ. The exact resolved runtime executable is hashed separately,
and each per-case invocation must normalize to the same contract. Claim runs
use the source root as their working directory. The fixed tree policy accepts
at most 10,000 files, 256 MB per file, and 512 MB total; use a dedicated
adapter directory rather than a repository root with generated artifacts.
Per-case execution revalidates the tree and runtime after every case and stops
before launching another case if either changed.

Adapter processes do not inherit the complete host environment. The runner
starts with a bounded platform-specific set of path, locale, temporary, and
process-startup variables plus deterministic Python guards that disable
user-site imports and bytecode writes. Home and cache variables are opt-in.
Use repeatable `--pass-environment NAME` only for
additional variables the reviewed adapter needs; a requested missing variable
fails before execution. The manifest retains the platform, sorted names,
aggregate encoded size, and a canonical digest over each name and value hash,
never plaintext values. Sensitive-looking credential names make claim metadata
incomplete, and external scoring matches the environment digest frozen for
that system. Value hashes can still be guessed when values have low entropy, so
this mechanism is reproducibility evidence, not secret storage.

`--max-memory-mb` applies an inherited per-process `RLIMIT_AS` ceiling on POSIX.
It limits virtual address space, not physical RSS/footprint, and is not an
aggregate tree limit; a usable finite value is host/runtime-map sensitive.
Windows uses a Job Object, assigned before adapter code resumes, with
per-process and aggregate limits. On macOS a fixed runner-owned `/bin/sh -p`
script starts with an empty environment and sets the requested `RLIMIT_AS`
soft and hard values in 1024-byte units. Here `-p` selects privileged shell
mode and grants no privilege. The script is runner-owned and its control fields
are runner-generated. It passes the isolated no-site (`-I -S`) verifier plus
adapter argv only as quoted positional arguments; adapter text is never
shell-interpreted. A bounded
canonical encoding of the exact adapter environment travels through an
anonymous, unlinked regular-file descriptor with its byte count and SHA-256
digest. Before bounds and hashing on Darwin, the runner reserves
`__CF_USER_TEXT_ENCODING` as `0x{uid:X}:0:0`; a conflicting caller value fails
closed. The counted, hashed entry prevents CoreFoundation's default-text-encoding
initializer from replacing that environment entry with a host/home-derived
value after `execve`. Evidence covers the exact mapping passed
to `execve`, not later mutations by arbitrary runtime code. The verifier first
confirms exact inherited `RLIMIT_AS`; validates the
descriptor, file, and expected size; reads, scrubs, truncates, and closes the
handoff; validates the retained in-memory length, SHA-256, and protocol; applies
byte-exact `RLIMIT_FSIZE`; canonically decodes the environment; and calls
`execve` with literal adapter argv. A pre-shell launch failure or shell,
pre-verifier, or inexact-`RLIMIT_AS` exit closes the anonymous unlinked descriptor
through process/context teardown without guaranteeing a scrub. Completed
scrubbing is not a cryptographic-erasure guarantee. Exact-limit status requires
all verifier checks to succeed; failure or mismatch is retained instead of
substituting a different ceiling. Preflight and `Popen` failures are blocking
runner errors. A post-`Popen` launcher failure is retained in a failed,
non-scoreable case manifest. On Darwin, a configured limit with
`process_succeeded: false` conservatively records
`memory_limit_enforced: false` because the parent has no authenticated
verifier-completion signal; this may underreport enforcement but cannot upgrade
the retained failure. A configured limit with `process_succeeded: true`
requires `memory_limit_enforced: true`.
POSIX cleanup
keeps the leader waitable while the group ID is in use. After the final macOS
group signal, a bounded stable `libproc` snapshot must prove every remaining
member is a zombie; this is also the only condition under which `EPERM` is
accepted. A process that deliberately leaves the POSIX group is outside this
boundary.

The separate service options capture an already-running inference service's
PID creation token and executable digest, then sample Windows working set or
Linux/macOS RSS before, during, and after each case. macOS rechecks process
creation identity around each `libproc` path/RSS observation. A restart,
disappearance, executable change, or ceiling breach invalidates the adapter
without terminating the service. Sampling is not containment and can miss a
spike between polls; it does not prove the adapter used that PID or
automatically include separate helper processes, and it is not a verified
jetsam or physical-footprint provider. Configured service accounting
is supported on Windows, Linux, and macOS and fails preflight elsewhere. Linux
requires a mounted procfs and access to the target process. It hashes an opened
`/proc/<pid>/exe` descriptor and rechecks the proc link target and inode, rather
than resolving and reopening the display pathname through the runner's mount
namespace. Missing procfs, a protected executable link, and a
PID outside the runner's PID namespace are distinct fail-closed preflight
errors; no command-line or pathname fallback is accepted. The PID must be the
service PID as visible inside the runner namespace. Supplying
`--expected-inference-service-executable-sha256` rejects a visible PID collision
when its executable bytes differ before the adapter starts; two processes using
the same executable remain indistinguishable. Frozen scoring still requires the
same digest. Omitting all service
options disables only this claim-bearing evidence and leaves diagnostic runner
features available. The wrapper does not establish a filesystem or network
sandbox, so execute only
reviewed adapter code in an appropriately isolated environment. Claim-bearing
runs must provide the retained host firewall,
container, or network-namespace policy file; the runner hashes it before
execution, detects changes, and manifest reload rehashes it. That artifact is
auditable evidence, not proof that the host enforced the named policy.

Revision, environment, model, context, tokenizer, inference concurrency,
retries, and service cost are also recorded. Claim-bearing manifests require
per-case isolation, an enforced platform-appropriate adapter memory limit,
complete identity fields, the exact Qwen Q4 model, one inference slot, and zero
model-service cost. Current `lrcbench-external-run-manifest-0.13` claim metadata
requires an
immutable adapter revision, `environment_id` equal to
`sha256:<dependency-lock-sha256>`, the exact retained lock bytes, context length
8192, the evaluator tokenizer, zero retries, the exact
Qwen/one-slot/zero-service-cost identity, and retained network-isolation
evidence, the retained command-referenced adapter entrypoint, its bounded
immutable source tree, the hashed resolved runtime, a portable complete command
contract, a bounded name-audited process-environment digest, plus at least
two stable inference-service samples within the configured ceiling. Scoring
also requires every dependency/process-environment/network-evidence digest,
adapter entrypoint/source-tree/runtime/command digest, the service
memory metric/executable digest/ceiling, isolation, timeout, polling, output,
candidate, and adapter memory limit to match the frozen protocol.
`--isolation whole-corpus` remains useful for diagnostics but is a registered
certificate non-win.

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
python -m benchmarks --histories 32 `
  --external-protocol benchmarks/protocols/external-comparison-v1.json `
  --expected-external-system acon `
  --expected-external-system foldagent `
  --external-run-manifest acon-manifest.json `
  --external-run-manifest foldagent-manifest.json
```

The verified frozen protocol is authoritative for the registered set. Optional
`--expected-external-system` assertions must match that complete set exactly.
A missing registered output is retained as an invalid non-win; an unexpected
supplied system is rejected. The harness also rejects a draft protocol, a
different synthetic dataset digest, or a candidate/run-manifest adapter
revision that differs from the frozen revision. The current report binds the
protocol id, protocol/document/dataset hashes, and registered set into
certificate evidence. The certificate computes a separate win, tie, loss, or
invalid decision for each registered system and requires wins against a strict
majority. Run
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

`--external-baseline` remains available for direct interchange diagnostics
inside a frozen protocol, but a registered system without a validated run
manifest is an invalid certificate non-win. A failed manifest contributes its
exact bounded-run failure reason to the per-system decision and evidence
digest.

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
The suite collected 885 tests alongside it: 880 passed and 5
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
New JSON reports use schema id `lrcbench-report-0.2`. Their `run_metadata` records
the repository revision/dirty state, package and interchange versions, exact
command, timestamp, duration, environment, tokenizer/model, baseline revisions,
cost, and failures. `report_sha256` binds that whole envelope while the
certificate's `evidence_sha256` remains deterministic across equivalent runs.
Version `0.2` adds external-protocol evidence. The verifier continues to replay
the committed local-only `lrcbench-report-0.1` snapshot, but refuses a legacy
external-inclusive claim.

Verify a newly generated report, or replay the retained local `0.1` report,
offline:

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
[draft external comparison protocol](protocols/external-comparison-v1.md) and
its [strict machine-readable companion](protocols/external-comparison-v1.json).
The draft records four screened candidates and nine explicit blockers. It
remains non-claim-bearing until every blocker is resolved and the strict
verifier accepts it with `--require-frozen`; it currently supports no
production claim.
