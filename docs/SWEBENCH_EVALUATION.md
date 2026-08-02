# SWE-bench Verified source intake

This repository now has a reproducible **source intake and candidate-input
projection** for SWE-bench Verified. It has not prepared repositories, run an
agent, invoked the grader, or produced an external score. The external
comparison protocol therefore keeps its coding-task slot `pending` and retains
blocker `coding-task-suite`.

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
is complete.

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

## Requirements before execution or scoring

A later executor/grader checkpoint must fail closed unless it can retain and
verify all of the following:

1. A pristine base-commit tree with no upstream `.git` history, later refs,
   remotes, evaluator cache, raw dataset, hidden tests, or grader evidence in
   the candidate mount.
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
