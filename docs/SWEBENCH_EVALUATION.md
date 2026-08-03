# SWE-bench Verified source intake

This repository now has reproducible **source intake, candidate-input
projection, raw-Git repository preparation, full-cohort prediction-ledger, and
synthetic controller-run and text-patch composition boundaries** for SWE-bench
Verified. The preparation
boundary has passed local synthetic-mirror tests and retained public
preparation replay; the prediction and controller-run boundaries have passed
synthetic contract tests, and the patch boundary has passed synthetic
mechanical tests. The retained public cohort has attempted 94 selected rows
across Flask, Seaborn, Requests, Xarray, Pylint, Pytest, and scikit-learn: 88
exact base commits passed live preparation replay, six Pylint commits were
refused because their trees contain forbidden symlinks, and 406 rows remain
unattempted. No
public candidate, model, or agent ran; no candidate mount or verified isolation
exists; and no grader, resolution, external score, or usefulness result exists.
The external comparison protocol therefore keeps its coding-task slot `pending`
and retains blocker `coding-task-suite`.

## Frozen source identity

The source descriptor is
[`benchmarks/suites/swebench_verified_v1.json`](../benchmarks/suites/swebench_verified_v1.json),
schema `ctxc-swebench-suite-0.1`. It binds:

- dataset `SWE-bench/SWE-bench_Verified` at immutable Hugging Face revision
  `91aa3ed51b709be6457e12d00300a6a596d4c6a3`;
- split `test`, all 500 rows in physical Parquet order, without sampling;
- `data/test-00000-of-00001.parquet`, 2,090,470 bytes, SHA-256
  `43ed5a3d1d98da36472c1ade65ddd2085d7b4ff694fcaf6a023a07c5c1f32f21`;
- a canonical 8,097,924-byte local JSON snapshot, SHA-256
  `e1b70254514c107a92a37514ee94faae646baed60f97bd535097bb910d042df5`;
- all 500 ordered `(ordinal, instance_id, source_sha256,
  candidate_input_sha256)` bindings plus their aggregate digests, without raw
  patch or test content;
- official harness tag `v4.1.0`, revision
  `726c5461e2ef52d83cf1ea2107870a8bb3328d57`, and exact license and
  entrypoint byte hashes; and
- suite self-hash
  `2f97bfbcb036553f9203db2a54bca3b553cf2ddac344b40ca5a7d4b9e2d4f34f`.

The immutable dataset card has no declared SPDX license. The harness source is
MIT-licensed, but that does not establish a license for dataset rows, issue
text, patches, tests, or repository snapshots. Raw/derived source rows and
generated task documents remain local and ignored until redistribution review
is complete. The current artifact-by-artifact inventory and public preparation
cohort are recorded in
[SWE-bench license and acquisition review](SWEBENCH_LICENSE_REVIEW.md).

## Offline workflow

The default operation is dependency-free and only verifies the committed
descriptor:

```console
python -m benchmarks.swebench verify-suite
```

Download the exact immutable Parquet URL recorded in the descriptor without a
token, then materialize it locally. Only this conversion operation needs the
exact optional decoder `pyarrow==25.0.0`; PyArrow is intentionally not a core
or development dependency.

```console
python -m benchmarks.swebench materialize-source \
  --parquet build/swebench/source.parquet \
  --output build/swebench/source-canonical.json
```

`materialize-source` opens and bounds the local regular file, verifies its
exact bytes before decoding in memory, checks the physical schema, null counts,
row order, all semantic digest anchors, and the final canonical snapshot bytes,
then refuses an existing output. It performs no network access and reads no
credentials. The canonical snapshot contains evaluator gold and must never be
mounted into an agent environment.

Create and retain a cryptographically random raw 32-byte key outside the
candidate sandbox. Length and the all-zero check do not prove entropy; key
generation and custody remain coordinator responsibilities. Project the exact
snapshot through the frozen allowlist:

```console
python -m benchmarks.swebench project \
  --snapshot build/swebench/source-canonical.json \
  --opaque-key-file build/swebench/opaque-id.key \
  --output build/swebench/task-input.json

python -m benchmarks.swebench verify-task-input \
  --input build/swebench/task-input.json \
  --snapshot build/swebench/source-canonical.json \
  --opaque-key-file build/swebench/opaque-id.key
```

The second command recomputes every candidate payload and HMAC routing ID from
the separately retained source and key. A self-hash alone is mutation
detection, not authentication.

## Candidate boundary

Only one `tasks[]` entry is a candidate payload. Its exact fields are:

```json
{
  "opaque_task_id": "task-<64 lowercase hex characters>",
  "problem_statement": "<public issue text>"
}
```

The containing `ctxc-swebench-task-input-0.1` document is coordinator evidence
and must not be mounted wholesale. It binds the suite, source snapshot,
full-record aggregate, candidate-input aggregate, key fingerprint, order, and
non-claim state. Projection constructs the two-field payload from an allowlist;
it never subtracts known gold fields from a source record.

The coordinator retains `repo`, canonical `instance_id`, `base_commit`,
`hints_text`, `created_at`, `version`, `environment_setup_commit`, and
`difficulty`. The evaluator alone retains `patch`, `test_patch`,
`FAIL_TO_PASS`, and `PASS_TO_PASS`.

Opaque IDs are pseudonymous routing identifiers, not privacy or contamination
protection. The public problem statement, suite identity, and fixed row order
can permit relinking by a party with the public corpus. Network isolation also
cannot show that a pretrained model never encountered public benchmark gold.
Neither unlinkability nor unseen-holdout status is claimed.

## Repository-preparation boundary

`benchmarks.swebench_repository` and its standalone worker are available only
from source and the sdist. They add no core or wheel dependency. The public
source-bound entry point is `prepare_task_repository()`, which revalidates the
canonical source snapshot and derives the repository, base commit, instance
id, ordinal, and source-record SHA-256 from the selected row. The lower-level
`prepare_repository_from_commit()` exists for contract tests and retains a
null suite binding; it must not be substituted for source-bound preparation.

Mirror verification is offline. It accepts absolute paths for an existing
bare mirror and Git executable, requires SHA-1 object format plus one canonical
`https://github.com/<owner>/<repo>[.git]` origin and mirror refspec, disables
system/global config, prompting, lazy fetch, replacements, and optional locks,
and rejects shallow or partial/promisor state, alternates, grafts, replace
refs, includes, extra remotes, linked worktrees, worktree-specific config or
files, links/reparse points, and special entries. The configured origin
URL and SHA-1 commit name do not authenticate GitHub or the repository author.
Raw objects also receive independent SHA-256 evidence.
POSIX mirror regular files must have a single link; locally derived mirrors
must be acquired with Git hardlinking disabled. This conservative availability
constraint does not authenticate or strengthen repository provenance.

Preparation stages a release-digest-bound standalone worker beneath the system
temporary directory and launches it with isolated `python -B -I -S` through
the generic literal-argv lifecycle. The worker's `git cat-file` descendants
remain in the same owned Windows Job or POSIX process group. Bounded pipe
drainers retain no more than their configured caps, and each batch protocol
must end at clean stdout EOF. The worker reads and recomputes exact commit,
tree, and blob objects; preflights bounded object counts, per-object bytes,
aggregate tree and blob bytes, depth, paths, and worker output; and writes each
blob independently into an unpredictable fresh directory. It never invokes
checkout, restore, archive, filters, hooks, or patch application.

Only `100644` and `100755` blobs are accepted. Symlinks, gitlinks/submodules,
special entries, invalid UTF-8, non-NFC paths, controls and format characters,
Windows reserved names, `.git`/`git~1` aliases, slash/backslash ambiguity,
case-fold collisions, oversized paths, and unsafe output ancestors fail
closed. The prepared tree contains no `.git`, history, remotes, manifest,
raw source snapshot, or evaluator material. After the worker exits, the parent
independently walks the output, rejects unexpected/link/hard-link/special
entries or extra empty directories, and recomputes every size, blob id,
SHA-256, and executable-mode observation available on the platform. It also
rejects user-visible extended attributes, unexpected Windows file attributes,
and NTFS alternate data streams. Verification reopens the exact live mirror
commit and compares its complete raw export rather than trusting retained tree
evidence alone.

The coordinator-only manifest schemas are
`ctxc-swebench-bare-mirror-0.1` and
`ctxc-swebench-repository-preparation-0.1`. They bind exact runtime limits,
worker and Git bytes, mirror/config observations, source identity, raw-object
evidence, every materialized entry, export policy, and self-hash. Neither
self-hash authenticates an author. The preparation manifest permanently keeps
repository origin/redistribution review, candidate mount, mount/network
isolation, Git security review, execution, grading, external score,
usefulness, and claim readiness false.

The synthetic tests use small locally created SHA-1 mirrors and prove contract
behavior, not repository redistribution permission, hostile-pack parser
safety, or candidate isolation. Separately retained public evidence currently
covers ordinals 287--380: 94 rows. Its six symlink-policy refusals stay in the
500-task denominator alongside 88 verified preparations and 406 explicitly
unattempted rows; none is a candidate result.

A fresh `--verify-only` replay at clean code revision
`d512b04156728cb9a4088f250ead395192572b34` revalidated every retained mirror,
manifest, and prepared output tree against the current 42,849-byte worker,
SHA-256
`cb9c8680d74022b7db8aea8b376790a49131f5fd1a1c0b93ba74341302c8a44f`.
The ignored 72,728-byte preparation summary has file SHA-256
`21ded8d8bff3038e1064abaff01d952e06fe60d165617f3e3e213d55ba99b781`
and self-hash
`f11f8bd4235c1469fbe8dd8865d92519af82f8c831ae5901a82b182bd221b96d`.
Its 88 manifests total 16,333,450 bytes and bind 64,056 regular files,
801,780,869 blob bytes, 7,603 tree objects, and 3,208,763 raw tree bytes.
Repository-origin authentication, redistribution review, candidate mount,
mount/network isolation, Git security review, execution, grading, external
score, usefulness, and claim readiness all remain false.

The next bounded repository slice is Sphinx ordinals 381--424, 44 distinct
exact commits from
`sphinx-doc__sphinx-10323@31eba1a76dd485dc633cae48227b46879eda5df4` through
`sphinx-doc__sphinx-9711@81a4fd973d4cfcb25d01a7b0be62cdb28f82406d`.
Ordinal 425, `sympy__sympy-11618`, is the first following SymPy row. The
through-380 checkpoint makes no Sphinx mirror, preparation, license-inventory,
execution, or grading claim.

## Full-cohort prediction boundary

`benchmarks.swebench_prediction` is source/sdist-only and keeps candidate
capture separate from repository preparation, execution, and grading. Its
`ctxc-swebench-prediction-ledger-0.1` document revalidates the exact source,
source/key-bound task input, and every claimed successful preparation. It then
reconciles candidate outputs by opaque task id and maps to canonical
`instance_id` only in the coordinator-owned artifact.

Every selected row remains present in physical source order with one closed
disposition: repository not attempted, repository preparation refused,
prepared with no prediction, or prediction recorded. Missing, duplicate,
unexpected, malformed, invalid-text, oversized, and unprepared-task outputs
become per-task nonpredictions or bounded protocol violations. They do not
abort after partial work, disappear from the denominator, or gain a patch by
best-effort parsing. Unknown identifiers and rejected output content are not
serialized verbatim.

Accepted patch text is preserved as exact strict UTF-8 without newline or
Unicode normalization and appears only in the deterministic official
interchange. This includes NUL and U+FEFF inside the JSON string; Unicode
surrogate code points fail closed rather than being normalized or repaired.
That JSONL contains exactly one compact line for every selected task, with
exact fields `instance_id`, `model_name_or_path`, and `model_patch`.
Nonpredictions use JSON `null`. The ledger retains the exact byte count and
SHA-256 of each recorded patch and canonical line plus the complete JSONL byte
count, line count, and SHA-256; it does not serialize runtime paths, the opaque
key, problem statements, source gold, or rejected raw output.

Finite limits separately cap patch bytes, candidate-capture count, aggregate
retained-capture bytes, protocol violations, ledger bytes, and JSONL bytes.
Contiguous zero-based capture ordinals let replay count each retained capture
once even when the same evidence appears in both a task row and its protocol
violation. Guarded file replay binds the identity of the descriptor actually
read to the initial or installed file identity, so an A-B-A pathname swap
cannot substitute same-byte evidence while mutating the intended output.
Every live verification also requires an independently supplied expected
identity for the code revision/clean-state claim plus model, agent, prompt,
tool, and controller digests. Reconciliation prevents a ledger-only identity
rewrite, but the supplied labels and hashes are not signatures, attestation,
or proof that those components produced a capture.

The ledger is the authoritative denominator and disposition evidence. The
JSONL is only a harness interchange artifact: upstream loaders may collapse
duplicate ids or filter empty patches, so replay verifies the exact canonical
line sequence instead of treating a successful load as completeness proof.
Self-hashes do not authenticate a model or coordinator. Fixed-false state
records that no candidate mount, execution, hidden-test application, grading,
score, usefulness, or claim readiness follows from this checkpoint. The
ignored, preparation-only 490,862-byte through-380 ledger reconciles 88
prepared, 6 refused, and 406 unattempted outcomes over all 500 source rows. Its
file SHA-256 is
`dc8d7c10b7ab4ad4cf24123296abf502f614a1740337e356ce281e7bffcdf8af`
and its self-hash is
`3fa05e48739ca1fdc1e8295724b68f45f42728f9229cf908dfeaa6fd29192e5d`.
The companion official-format JSONL remains byte-for-byte unchanged from the
earlier preparation-only checkpoint: 53,734 bytes, exactly 500 rows in physical
source order, 500 `null` patches, and SHA-256
`546a3e42b9cf5bfcf7165eb244b5da909ff069971519e0ac7f083b413f950e60`.
Neither artifact is committed or a candidate run. Candidate-capture origin,
system identity, and producer authentication also remain false.
Because rejected candidate bytes are discarded rather than duplicated beside
the official JSONL, invalid,
duplicate, and unexpected-capture dispositions are coordinator assertions and
are explicitly marked as not independently replayable unless the capture came
through the controller-run boundary below. That boundary binds raw bytes and
workspace observations, not their producer's authenticity or isolated
execution.

## Controller-run boundary

`benchmarks.swebench_run` is available only from source and the sdist. It adds
`ctxc-swebench-run-ledger-0.1` and strict controller result schema
`ctxc-swebench-controller-result-0.1`; neither is an installed core-package JSON
Schema. `build_run_ledger()` and its alias `run_swebench_controller()` reconcile
the full selected cohort. Load, write, decode, and live verification replay the
same source, task-input, preparation, system, controller, artifact, and
workspace bindings.

The caller supplies one ordered `CandidateWorkspace` binding per selected task.
An unprepared row must have no workspace; a prepared row with no workspace is
retained as unavailable. Before a launch, the coordinator requires the mutable
workspace to match the verified preparation and independently reverifies the
prepared repository. It creates a fresh non-overlapping artifact root, writes a
canonical request containing only `opaque_task_id` and `problem_statement`, and
appends the request path followed by the workspace path to the configured
literal argv under protocol `append-request-json-and-workspace-path-v1`. The
workspace is the process working directory. This is a caller-provided directory,
not a mount created or isolated by the coordinator.

The process lifecycle retains bounded exact stdout/stderr prefixes and their
observed/captured counts. Successful complete stdout must be exactly one strict
controller-result object with the matching opaque id, patch text, bounded token
document, and bounded ordered trajectory. Patch captures are derived only by
replaying those retained stdout bytes. The ledger stores exact patch evidence,
a trajectory count/byte/hash summary, token totals, initial/final workspace
summaries, and a deterministic delta. Repository-not-attempted,
preparation-refused, workspace, launch, timeout, stream, process, cleanup,
nonzero-exit, malformed/oversized output, final-observation, and captured-run
dispositions remain exhaustive over the source-selected denominator.

Live replay detects artifact drift and current-workspace content drift only
where an initial or final summary was retained; a final-scan-failure row replays
the structural failure code, not the missing contents. Replay also requires the
expected controller executable bytes, argv, environment, source, task input,
preparations, and system labels to match. Those checks and the ledger self-hash
are consistency evidence, not signatures or attestation. They do not prove
which script or loaded code executed, authenticate process exit, trigger,
timing, cleanup, the controller, system, agent, model, token counter,
trajectory, or raw-output producer, or prevent an uncontained controller from
accessing host paths. Workspace traversal and launch remain pathname-based,
not descriptor-pinned against concurrent nested-directory or executable
substitution. `provider_reported` and `tokenizer:*` counts remain controller
assertions; `not_measured` remains explicit.

The underlying lifecycle owns a Windows Job or an escapable POSIX process
group. It provides no mount, filesystem, network, user, PID, or immutable-image
isolation. Every candidate-execution-authentication, isolation, hidden-test,
grading, resolution, score, usefulness, producer-authentication, and
claim-readiness field is fixed false. The focused suite passed 42 synthetic
cases and skipped one in 158.55 seconds on Windows; the skipped
directory-symlink-root fixture was unavailable because this Windows token lacks
directory-symlink privilege. No public candidate, model, agent, or grader was
used.

## Text-patch composition preflight

`benchmarks.swebench_patch` is a coordinator-only, source/sdist-only mechanical
preflight. It is not installed in the dependency-free wheel. Its self-hashed
artifact schema is `ctxc-swebench-patch-composition-0.1`, and its accepted input
is candidate and hidden **text** patch bytes for one source-bound prepared
repository. It is neither a public-suite run nor a grader.

`build_patch_composition()` revalidates the canonical
`VerifiedSweBenchSource` and the corresponding
`PreparedSweBenchRepository` before taking the hidden `test_patch`; it does not
trust a caller-supplied record mapping. The prepared base is rescanned and
copied into independent candidate and hidden temporary trees. Each patch is
applied through the bounded literal-argv lifecycle, and bounded tree summaries
plus complete changed-path pre/post states and deltas are retained. The
effective-path comparison treats the same path and ancestor/descendant
relationships as conflicts. Its work is bounded and near-linear in changed
paths and permitted path depth.

The artifact has exactly two statuses:

- `overlap-detected-not-composable` retains the conflict set and no composition
  object; and
- `disjoint-composition-preflight-verified-not-a-grader` creates a third clean
  base copy, applies candidate then hidden patches, and requires exact replay of
  both deltas without changing a candidate-owned final path.

The source preparation is reverified after temporary work and remains
unchanged. `decode_patch_composition()` strictly checks fields, types, bounds,
canonical paths, derivable summaries, and the self-hash, but it does not
authenticate the producer or prove patch semantics. Public verify, load,
exclusive write, and replay canonically snapshot source/preparation evidence,
bind the live prepared-tree summary, and require exact semantic patch replay.
The fail-closed `retained_disjoint` property reports only structurally retained
evidence; it is not a safety, isolation, authenticity, or claim decision.

Inline and payload-free binary diffs, extended copy/rename headers, file-mode
changes, and new/deleted-file modes other than exact regular-file `100644` are
rejected before Git starts. Accepted ordinary text content/add/delete patches
still run through an unsandboxed native Git parser. The process has a deadline
and bounded retained streams, and trees are checked against finite limits, but
no native memory or filesystem quota is installed; post-operation verification
is not native resource containment. A `PatchCompositionError` carries a
machine-readable `stage` and fixed disposition
`preflight-exception-not-a-cohort-result`. A future outer
complete-denominator ledger must retain and map the exception. This one-task
helper must not silently drop the row or manufacture a cohort result.

Candidate-code execution, hidden-test execution, official-grader execution,
official results, external scores, usefulness, candidate-mount/network/
filesystem isolation, native quotas, Git-parser sandboxing, and claim readiness
remain false. Self-hashing does not authenticate an author. The synthetic
preflight changes no public denominator. At the independently verified current
preparation checkpoint, 88 rows are prepared, 6 are policy-refused, and 406 are
unattempted, with no public candidate or grader run.

## Requirements before public execution or scoring

A claim-bearing public executor/grader checkpoint must fail closed unless it can
retain and verify all of the following:

1. Run the remaining 406 selected source rows through the raw-Git preparer and
   retain every success or refusal. Independently create a candidate mount from
   a verified tree with no upstream `.git` history, later refs, remotes,
   evaluator cache, raw dataset, hidden tests, or grader evidence.
2. Externally evidenced outbound-network isolation for every candidate run.
3. Separate candidate and evaluator lifetimes and filesystems. Apply hidden
   test material only after candidate termination and never return grader
   feedback to later matched runs.
4. Run the mechanical text-patch preflight and retain any
   `preflight-exception-not-a-cohort-result` disposition in the outer ledger.
   Then require exact successful application of the hidden `test_patch` in the
   separately isolated evaluator; reject candidate diffs that overlap or
   pre-create hidden test paths, and retain raw application plus before/after
   workspace evidence. The synthetic preflight is not proof of that public
   evaluator action. These controls are required because upstream
   [issue 538](https://github.com/SWE-bench/SWE-bench/issues/538) documents a
   false-positive path when hidden-test application fails.
5. A reviewed, immutable harness environment and container/image identities,
   not merely a tag and source-file hash.
6. Use the controller-run ledger for exact requests, raw streams, workspace
   observations, patches, wall time, and every launch/process/output failure,
   then add independently authenticated code/controller/model, prompt, tool,
   token, and trajectory evidence plus raw official grader evidence. Timeouts,
   missing output, invalid patches, setup failures, grader failures, and test
   failures all remain in the denominator.
7. Metrics for task completion, prompt/model tokens, wall time, and failure
   rate. Correction recovery and unresolved-question preservation remain
   explicitly `not_measured` unless the retained trajectory supports them.
8. Dataset and repository redistribution review. The current descriptor's
   missing dataset-card license is a blocker, not an inferred MIT grant.

The official source links are the pinned
[dataset tree](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified/tree/91aa3ed51b709be6457e12d00300a6a596d4c6a3)
and [harness tree](https://github.com/SWE-bench/SWE-bench/tree/726c5461e2ef52d83cf1ea2107870a8bb3328d57).
