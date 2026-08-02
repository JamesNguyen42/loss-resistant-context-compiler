# Agent progress

Last updated: 2026-08-01 (America/Los_Angeles)

## Resume point

- Branch: `codex/openhands-live-agent-beta`
- Upstream: `origin/codex/openhands-live-agent-beta`
- Base commit for this in-progress checkpoint: `fc966dd`
- Current checkpoint: fail-closed external-adapter deadline ordering. Launch
  and containment setup consume the run timeout, and completion first observed
  at or after the deadline cannot be accepted.
- Previous checkpoint: source-bound, gold-free SWE-bench Verified intake was
  committed and pushed as `fc966dd`.
- Next task: extract the generic bounded literal-argv process lifecycle before
  implementing the separate SWE-bench repository and execution/result
  boundaries. Do not encode a repository patch as an LRCBench rendered-memory
  candidate.

## Mission status

1. Linux `/proc/<pid>/exe` portability: complete and pushed in `6eae3c4`.
2. LocalAI 1.0 contract convergence: isolated optional adapter, exact-wheel
   validation, conversion audit, and contract request complete in `c0ee2f9`.
3. Public long-horizon suite evidence: SWE-bench Verified source selection and
   candidate-input projection are now pinned, but no repository has been
   prepared, no model or grader has run, and no external score exists. A second
   materially different public suite is still required.
4. Natural-history benchmark, maintainability, and performance work remain
   after the P0 external-evidence gap.

## Current checkpoint

- Reordered the external-runner monitor boundary so it samples elapsed
  monotonic time before asking whether the adapter has exited. The deadline
  begins before temporary-directory and process launch setup.
- Added a deterministic regression in which the first possible completion
  observation occurs one second after a five-second deadline. The process is
  retained as `timeout`; no exit probe can upgrade it.
- Kept existing fail-closed cleanup behavior unchanged. POSIX cleanup failure
  still becomes `process_group_cleanup_failed`; Windows Job, Darwin handoff,
  descriptor-retention, output-limit, and inference-service behavior remains
  covered by the full runner test file.

## Completed source-intake checkpoint (`fc966dd`)

- Added source/sdist-only `benchmarks.swebench` formats
  `ctxc-swebench-suite-0.1`, `ctxc-swebench-task-input-0.1`, and
  `ctxc-swebench-verification-0.1`. The core and wheel retain zero new runtime
  dependencies and 26 installed schemas.
- Pinned public, ungated `SWE-bench/SWE-bench_Verified` revision
  `91aa3ed51b709be6457e12d00300a6a596d4c6a3`, split `test`, and exact
  `data/test-00000-of-00001.parquet` bytes: 2,090,470 bytes, SHA-256
  `43ed5a3d1d98da36472c1ade65ddd2085d7b4ff694fcaf6a023a07c5c1f32f21`.
- Independently reproduced the 500-row, 13-string-column source twice. The
  deterministic no-newline canonical JSON snapshot is 8,097,924 bytes with
  SHA-256
  `e1b70254514c107a92a37514ee94faae646baed60f97bd535097bb910d042df5`.
  Its ordered instance-id digest is
  `33e18be7a9bd9f674790b63ed4d0b3fb17c176994802e3062b7d5a430a4e7d16`;
  its ordered full-record-hash digest is
  `497c33e6915ef684637fcf9a5417777ac5de034e94fecb48b1202df8b561e798`;
  and its ordered candidate-input-hash digest is
  `635f0b58b94a8ffa996cd5b03c6dd47714f0839680cbc58a59de0d4f4919523f`.
- Committed all 500 ordered `(ordinal, instance_id, source_sha256,
  candidate_input_sha256)` bindings without source text, patches, test
  patches, or test outcomes. The suite self-hash is
  `2f97bfbcb036553f9203db2a54bca3b553cf2ddac344b40ca5a7d4b9e2d4f34f`;
  the pretty descriptor file SHA-256 is
  `4ec7973d279838645ac1907e4a107276650412612c1f0571bc30780b1f852570`.
- Froze the result-blind selection as every row in physical source order, with
  no sampling. The current external protocol conservatively cannot freeze a
  seedless task slot, so a future protocol revision must represent an explicit
  full-population selection rather than inventing an unused seed.
- Added a safe local Parquet reader/materializer. It bounds and reads one
  regular file through one descriptor, verifies its exact bytes, lazily
  requires exact optional `pyarrow==25.0.0`, checks physical field order/type,
  nullable metadata, zero observed nulls, row count, all row bindings, and the
  final canonical snapshot, and refuses an existing output.
- Added a dependency-free snapshot loader and allowlist projection. Candidate
  payloads contain exactly `opaque_task_id` and `problem_statement`.
  Coordinator fields and evaluator-only `patch`, `test_patch`, `FAIL_TO_PASS`,
  and `PASS_TO_PASS` never enter a task payload.
- HMAC routing ids bind a separately retained raw 32-byte key, suite self-hash,
  and canonical instance id. Projection, exclusive writing, and CLI
  verification revalidate publicly constructible source objects and recompute
  every statement and opaque id from source plus key. Re-signed id or public
  text substitution fails.
- Marked all relevant non-claims explicitly: key entropy is not proven by
  length, opaque ids do not prevent public-corpus relinking, pretrained-data
  contamination is not excluded, and candidate mount isolation is not yet
  verified. The task-document envelope is coordinator evidence; only an
  individual `tasks[]` record is candidate-facing.
- Pinned the official harness source at tag `v4.1.0`, revision
  `726c5461e2ef52d83cf1ea2107870a8bb3328d57`, exact license and entrypoint
  hashes, but labeled it `pinned-not-security-reviewed`. Upstream hidden-test
  application issue 538 is an explicit fail-closed requirement for the next
  grader checkpoint.
- Kept dataset and harness license scopes separate. The pinned dataset card
  has no declared SPDX license; repository/dataset redistribution review
  remains false. The harness project's MIT license is not applied to dataset
  rows or third-party repository snapshots.
- Added typed `ExternalDatasetBinding` values and `dataset_by_kind()` to the
  verified external protocol while retaining the legacy
  `synthetic_dataset_sha256` property. The still-pending coding slot now binds
  the exact suite revision, self-hash, and 500-task count. All nine blockers
  remain.
- Added bounded binary regular-file reading to shared benchmark JSON I/O, with
  ancestor/path/substitution checks inherited from the existing safe opener.
- Added routine CI descriptor verification and explicit source-distribution
  membership checks. PyArrow and the public dataset are not installed or
  downloaded in routine CI.

## Verification evidence

All ordinary commands used the repository Python 3.12 virtual environment.
Optional-contract test invocations used `PYTHONDONTWRITEBYTECODE=1`.

- External-runner focused deadline tests: 2 passed. The complete
  172-test `tests/test_external_runner.py` file passed with 168 passes and four
  existing platform skips; Ruff passed for the changed module and tests.
- `python -m pytest -q`: exit 0 in 284.2 seconds; 2,217 collected, with 24
  existing skips and 2,193 passing tests inferred from the complete progress
  stream. A first attempt correctly failed only after that invocation created
  an optional-package bytecode cache inside the reviewed LocalAI install. The
  cache was moved intact to the system temporary directory, the cause was
  reproduced in one focused test, and the corrected no-bytecode full run
  passed.
- Focused SWE-bench, benchmark JSON I/O, and external-protocol set: 36 passed.
  It includes missing/extra/duplicate/reordered rows, per-record binding drift,
  hidden-vs-public digest separation, gold canaries, poisoned credential
  variables, forged dataclasses, re-signed opaque-id/text substitution,
  overwrite refusal, dataset/url/date binding, and fake-PyArrow import,
  version, schema, null, decode-failure, and success paths.
- `python -m ruff check .`: passed.
- `python -m compileall -q src benchmarks tests scripts conformance integrations _ctxc_build_backend.py`:
  passed with a temporary external bytecode prefix.
- `python conformance/run_connector_conformance.py`: 17 schemas, 6 golden
  steps, and 66 negative vectors passed.
- `python -m benchmarks --self-test`: passed. The LRCBench, phrase, exact-Qwen
  phrase, literal-ablation, paired-extractor, and natural-history frozen report
  verifiers all passed offline.
- `python -m benchmarks.swebench verify-suite`: 500 tasks, source artifact
  SHA-256 `43ed5a...2f21`, descriptor SHA-256 `4ec797...2570`, suite self-hash
  `2f97bf...f34f`, `claim_ready: false`.
- Real immutable Parquet replay with `pyarrow==25.0.0` reproduced all 500 row
  bindings and snapshot SHA-256 `e1b702...df5`. A random-key 500-task export
  then passed full source/key reconciliation with task-input self-hash
  `1900c54c5a88f5197f49f40dc1a8c372ec3799ab750908cb7ed399fd21919345`.
  The local Windows CPython 3.12 PyArrow wheel was 27,945,954 bytes with
  SHA-256
  `3f356afe61186395c861d5cd63dc21ff7d5fa335012a4668d979257df7fea0f5`;
  it is local decoder evidence, not a portable environment lock.
- External protocol verification passed with protocol self-hash
  `48e0c336878a292819ebd1015f7a9dbf9c5065f91c65411512aec458745fcd46`
  and `claim_ready: false`.
- A deterministic sdist at `SOURCE_DATE_EPOCH=1760000000` contained the new
  module, suite descriptor, documentation, and tests: 1,246,851 bytes,
  SHA-256
  `45dc501674febb529d70bf90a99ac3d8360f9b34efc7cd9d7468934ecf03a722`.
  The companion diagnostic wheel was 319,073 bytes, retained all 26 installed
  schemas, and had SHA-256
  `c15056ba4a23cc6feab9b7c3bf99d2f9f510a3a84ca201fc49e72a646aeee8b6`.
- `git diff --check` passed before the final progress update.

## Honest blockers and claim boundary

- No public long-horizon task has been executed or scored. Do not claim
  external usefulness, task-completion improvement, production readiness, or
  superiority from this source-intake checkpoint.
- The canonical source snapshot contains public evaluator gold and must never
  enter a candidate mount. It and the raw Parquet, opaque key, derived task
  document, evaluator cache, grader output, and repository snapshots remain
  ignored local material.
- Dataset redistribution permission and each underlying repository snapshot's
  obligations are unresolved. The missing dataset-card license remains a
  blocker rather than an inferred MIT grant.
- Network isolation controls live lookup but not training contamination.
  Public problem statements and fixed suite order can relink opaque task ids.
- The official harness source is pinned, not security-approved. Claim-bearing
  grading must prove exact hidden-test-patch application, reject candidate
  overlap/precreation of hidden test paths, retain raw application evidence,
  and close the documented upstream false-positive path.
- The existing `lrcbench-external-run-manifest-0.13` runner accepts bounded
  rendered memory, not repository patches. Reusing that wire would be a false
  equivalence.

## Next exact actions

1. Extract only the generic bounded literal-argv subprocess/resource boundary
   from `benchmarks.external_runner`, with characterization tests preserving
   timeout, output, process-tree, environment, network, and executable/source
   evidence. Keep all LRC candidate validation in the current runner.
2. Add a distinct SWE-bench repository-preparation contract: export only the
   exact base-commit tree, exclude `.git` history/remotes/later refs and all
   evaluator/Hugging Face caches, bind the resulting tree, and require retained
   network-isolation evidence before candidate launch.
3. Add SWE-bench prediction/run/result evidence around the official
   `instance_id`, `model_name_or_path`, and `model_patch` fields. Retain every
   selected task and failure in the denominator, plus prompt/model tokens,
   wall time, trajectory, workspace diff, and raw grader evidence.
4. Review and harden the pinned official grader before any score. Require
   hidden-test application success and candidate/hidden-test path
   non-overlap; mark correction recovery and unresolved-question preservation
   `not_measured` until trajectory evidence supports them.
5. Select and source-bind a materially different second public long-horizon
   suite without inspecting comparison results.
