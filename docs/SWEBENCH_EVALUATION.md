# SWE-bench Verified source intake

This repository now has reproducible **source intake, candidate-input
projection, raw-Git repository preparation, and full-cohort prediction-ledger
boundaries** for SWE-bench Verified. The preparation boundary has passed local
synthetic-mirror tests and the prediction boundary has passed synthetic
contract tests. In addition, one selected Flask base commit has passed local
public-tree preparation. That pilot did not create a candidate mount, capture a
prediction, run an agent, invoke a grader, or produce an external score. The
other 499 selected tasks remain unprepared. The external comparison
protocol therefore keeps its coding-task slot `pending` and retains blocker
`coding-task-suite`.

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
is complete. The current artifact-by-artifact inventory and first public
preparation pilot are recorded in
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

The current tests use small locally created SHA-1 mirrors. They prove contract
behavior, not public-cohort coverage, repository redistribution permission,
hostile-pack parser safety, or candidate isolation. A rejected public
repository must later become a retained per-task preparation failure rather
than disappearing from the 500-task denominator.

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
score, usefulness, or claim readiness follows from this checkpoint. No real
500-row prediction ledger or JSONL is committed. Because rejected candidate
bytes are discarded rather than duplicated beside the official JSONL, invalid,
duplicate, and unexpected-capture dispositions are coordinator assertions and
are explicitly marked as not independently replayable. The later run contract
must bind controller-owned raw execution evidence before any claim.

## Requirements before execution or scoring

A later executor/grader checkpoint must fail closed unless it can retain and
verify all of the following:

1. Run all selected source rows through the raw-Git preparer and retain every
   success or refusal. Independently create a candidate mount from a verified
   tree with no upstream `.git` history, later refs, remotes, evaluator cache,
   raw dataset, hidden tests, or grader evidence.
2. Externally evidenced outbound-network isolation for every candidate run.
3. Separate candidate and evaluator lifetimes and filesystems. Apply hidden
   test material only after candidate termination and never return grader
   feedback to later matched runs.
4. Exact successful application of the hidden `test_patch`; reject candidate
   diffs that overlap or pre-create hidden test paths. Retain raw application
   and before/after workspace evidence. This is required because upstream
   [issue 538](https://github.com/SWE-bench/SWE-bench/issues/538) documents a
   false-positive path when hidden-test application fails.
5. A reviewed, immutable harness environment and container/image identities,
   not merely a tag and source-file hash.
6. An execution/result contract binding code revision, suite and selection,
   model, prompts, tools, tokens, wall time, trajectory, patch, every failure,
   and raw official grader evidence. Timeouts, missing output, invalid patches,
   setup failures, grader failures, and test failures all remain in the
   denominator.
7. Metrics for task completion, prompt/model tokens, wall time, and failure
   rate. Correction recovery and unresolved-question preservation remain
   explicitly `not_measured` unless the retained trajectory supports them.
8. Dataset and repository redistribution review. The current descriptor's
   missing dataset-card license is a blocker, not an inferred MIT grant.

The official source links are the pinned
[dataset tree](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified/tree/91aa3ed51b709be6457e12d00300a6a596d4c6a3)
and [harness tree](https://github.com/SWE-bench/SWE-bench/tree/726c5461e2ef52d83cf1ea2107870a8bb3328d57).
