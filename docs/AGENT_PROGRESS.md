# Agent progress

Last updated: 2026-08-02 (America/Los_Angeles)

## Resume point

- Branch: `codex/openhands-live-agent-beta`
- Upstream: `origin/codex/openhands-live-agent-beta`
- Current committed HEAD and base for this in-progress checkpoint: `06e06c9`.
- Current checkpoint: every normalized POSIX-relative adapter-source inventory
  record now applies the shared Windows component-safety policy to every path
  part. This is an accepted-domain tightening; members are rejected rather than
  rewritten and no manifest, protocol, or tree-algorithm version changes.
- Previous checkpoint: complete Windows device-alias rejection in the root and
  OpenHands archive validators and pinned build-wheel inspector was tested,
  independently reviewed, committed, and pushed as `06e06c9`.
- Next public preparation target: scikit-learn ordinals 349--380 (32 selected
  rows). No local mirror or exact base-commit license evidence has yet been
  retained for that repository. Do not encode a repository patch as an
  LRCBench rendered-memory candidate.

## Mission status

1. Linux `/proc/<pid>/exe` portability: complete and pushed in `6eae3c4`.
2. LocalAI 1.0 contract convergence: isolated optional adapter, exact-wheel
   validation, conversion audit, and contract request complete in `c0ee2f9`.
3. Public long-horizon suite evidence: SWE-bench Verified source selection,
   candidate-input projection, raw-Git preparation, and a full-cohort
   prediction/official-JSONL boundary are pinned. A separately replayable
   synthetic controller-run ledger and a separate text-patch composition
   preflight now exist. Sixty-two public rows have been
   attempted: 56 prepared, 6 policy-refused, and 438 unattempted. No public
   candidate, model, agent, or grader has run and no resolution, external score,
   or usefulness result exists. Tau2-bench's materially different 278-row
   half-duplex text core is now locally source-bound after a separate internal
   result-blind review, but its candidate adapter, dependency environment,
   simulator/grader evidence, isolation, execution, reward, score, usefulness,
   and claim readiness remain absent or false.
4. Natural-history benchmark, maintainability, and performance work remain
   after the P0 external-evidence gap.

## Current adapter-source relative-member portability

- `AdapterSourceFileEvidence` now rejects a record when any `PurePosixPath`
  component fails the existing Windows lexical safety rule. This covers invalid
  characters and control ranges, trailing dots/spaces, `CON`, `PRN`, `AUX`,
  `NUL`, `CONIN$`, `CONOUT$`, COM/LPT 1--9, and COM/LPT superscript 1/2/3,
  including aliases with ASCII spaces before an extension.
- The immutable record is the common construction boundary for direct records,
  bounded source-tree capture, external-manifest replay, and compatibility-audit
  decoding. Adversarial loader and audit tests recompute both the nested source
  tree digest and the outer manifest digest before presenting an unsafe member,
  so rejection is not attributable to a stale ledger. `COM0`, `COM10`,
  `CON name`, and `CLOCK$` remain accepted controls.
- The focused 21-case selection completed on CPython 3.12 and clean CPython
  3.14.6 with 20 passed and one expected Windows capture skip on each runtime.
  The complete external-runner, external-compatibility, and compatibility-audit
  modules collected 221 tests on CPython 3.12 and completed with 216 passed,
  five expected skips, zero failures, and zero errors in 15.882 seconds.
  Ignored JUnit reports are `build/adapter-source-focused-py312-v2.xml`,
  `build/adapter-source-focused-py314-v2.xml`, and
  `build/adapter-source-full-py312-v2.xml`. The deterministic two-case benchmark
  corpus/candidate interchange self-test, focused Ruff, and diff checks pass.
- Runner manifest `0.13`, external protocol `0.10`, and adapter source-tree
  algorithm `0.1` remain unchanged. Accepted records retain their prior bytes
  and digests. This component-level rule does not claim case-folding,
  Unicode-normalization, or path-length collision freedom, make foreign source
  roots native-reopenable, bind imports outside the retained root, or prove
  which source files an adapter loaded.
- No live or external benchmark candidate, model, grader, GPU, or inference
  runtime is involved; the self-test uses only its two deterministic in-process
  fixture records.

## Previous archive-member device-alias hardening (`06e06c9`)

- The byte-identical root and OpenHands PEP 517 backends now compare each sdist
  and wheel component's pre-extension stem, after trimming only ASCII U+0020,
  against `CON`, `PRN`, `AUX`, `NUL`, `CONIN$`, `CONOUT$`, COM/LPT 1--9, and
  COM/LPT superscript 1/2/3 aliases. The pinned OpenHands build-wheel inspector
  applies the same rule to both ZIP entries and `RECORD` paths.
- Ambiguous members such as `CON .txt`, `COM1 .txt`, `CONIN$.txt`, `COM¹.txt`,
  and `LPT² .log` fail before archive normalization or approval. `COM0.txt`,
  `COM10.txt`, and `CON name.txt` remain accepted controls; `CLOCK$` is not added
  to this policy.
- Root regressions exercise real bounded sdist and wheel inputs. OpenHands
  regressions cover both parity backend helpers and approved build-wheel
  inspection, including independent ZIP-entry and `RECORD`-path failures. The
  complete root deterministic-archive module passed 81 tests; the two complete
  OpenHands backend/build-input modules passed 60 tests with one expected
  platform skip. Focused Ruff and diff checks pass. The backend source files
  remain byte-identical at Git blob
  `41c565e720c5f581496db054713078c9c67057ff`. Ignored JUnit reports are
  `build/archive-device-root.xml` and `build/archive-device-openhands-v2.xml`.
  The complete OpenHands packaging/parity module also passed all 22 tests in
  21.225 seconds; its ignored report is
  `build/archive-device-openhands-packaging.xml`.
- The combined root/OpenHands archive-validator set also passed on clean
  CPython 3.14.6 with 141 passed and one expected platform skip in 2.023
  seconds; its ignored report is `build/archive-device-py314-v2.xml`.
- An offline, no-build-isolation root packaging smoke under
  `SOURCE_DATE_EPOCH=1700000000` built both the wheel and deterministic sdist
  from the dirty checkpoint. The ignored outputs are retained under
  `build/archive-device-smoke-fb30956-v2`.
- No model, candidate, grader, GPU, or inference runtime is involved.

## Previous root CPython 3.14 support closure (`fb30956`)

- Root `pyproject.toml` now advertises CPython 3.14 and the complete Ubuntu
  `unit-tests` matrix covers 3.11, 3.12, 3.13, and 3.14. The Windows/macOS
  filesystem/release-smoke jobs remain on 3.13, and LRCBench remains on 3.11;
  those are intentionally role-specific evidence lanes.
- Root support, status, handoff, release-checklist, and workflow/packaging
  contract tests track the four-version matrix. The OpenHands package bound,
  classifiers, compatibility constant, manifest, six automatic lanes, and
  historical evidence remain unchanged at CPython 3.12/3.13.
- The committed Ubuntu CPython 3.14 job passed its complete suite, Ruff, and
  compileall steps in push run `30776191309`, job `91572042993`, from
  2026-08-03T01:09:19Z through 2026-08-03T01:14:36Z. Root CPython 3.14 support
  is therefore no longer provisional.
- A clean ignored non-editable CPython 3.14.6 environment, with no
  `localai-contracts` distribution, collected 2,405 core tests and completed
  with 2,380 passed, 25 expected skips, zero failures, and zero errors in
  823.078 seconds (824.4 seconds wall time). This is not exact-wheel LocalAI or
  OpenHands evidence. Its ignored JUnit report is
  `build/py314-core-full-suite.xml`.
- The strengthened workflow/packaging contract set passed 14 tests on both the
  repository CPython 3.12 environment and the clean CPython 3.14 environment.
  It freezes the root full-suite matrix at 3.11--3.14, platform/reproducibility
  jobs at 3.13, benchmark at 3.11, and all OpenHands roles at their existing
  3.12/3.13 assignments. Focused Ruff and diff checks pass.
- The non-editable wheel built and installed under CPython 3.14 reports only
  `loss-resistant-context-compiler==0.1.1a21` and includes the exact Python 3,
  3.11, 3.12, 3.13, and 3.14 classifiers. The ignored legacy source-tree
  egg-info was not deleted or used as installed evidence.
- No candidate, model, grader, GPU, or inference runtime is involved.

## Previous retained-path device-alias correction (`fff99b5`)

- `_windows_path_component_is_safe` trims only ASCII U+0020 from the
  pre-extension stem before its existing case-insensitive Windows reserved-name
  comparison. Paths such as `CON .txt`, `COM1 .txt`, `NUL .txt`, `COM¹ .txt`,
  and `LPT² .log` are rejected rather than rewritten. `COM0.txt`, `COM10.txt`,
  and `CON name.txt` remain valid controls; `CLOCK$` is not added to the policy.
- The one shared gate covers drive-letter and UNC paths used by all retained
  dependency-lock, adapter-entrypoint/source-root, runtime, network-policy,
  inference-service contract, and inference-service accounting evidence.
  POSIX absolute paths and adapter-source relative member names are unchanged.
- The 42-case focused canonical/ambiguous retained-path set passes, including
  drive-backslash, drive-forward-slash, UNC, superscript alias, explicit ADS,
  trailing-dot/space, and valid-control coverage. Focused Ruff and diff checks
  pass. The complete external-runner module collected 181 tests and completed
  with 177 passed, four expected platform skips, zero failures, and zero errors
  in 25.554 seconds; its ignored JUnit report is
  `build/external-runner-device-alias.xml`.
- The model-free benchmark self-test passed with its two-case corpus/candidate
  interchange checks. Two independent read-only reviews found no remaining
  P0--P2 portability, policy, test, or documentation issue after the docs were
  narrowed to the exact claim-control evidence fields; a separate
  write-disabled selection covering the focused cases plus foreign-path replay
  passed 44 tests.

## Previous LocalAI validation-reader hardening (`c14fd16`)

- `_hash_expected_regular_file`, `_read_bounded_regular_file`, and
  `_read_exact_regular_file` reject regular-mode reparse semantics on the
  pathname before open, the opened descriptor, the descriptor after reading,
  and the final pathname. Existing regular-file, identity, size, bounded-read,
  and final-path checks remain fail closed.
- The installed-tree hash reader buffers its already bounded contents and does
  not update the caller's digest until its post-read identity/reparse checks
  pass. This is a reader-level guarantee; later `RECORD`, tree-digest, and
  import checks remain separate and still discard a failed construction.
- A 12-case regression matrix covers all three readers at all four reparse
  observations, including pre-open/read ordering and tree-digest non-mutation.
  The two affected modules collected 184 tests and completed with 180 passed
  and four expected platform skips. The corrected standalone module also
  passed all 18 tests on CPython 3.14.6 under `-B` with bytecode writes
  disabled.
- Eleven CPython 3.14 caches accidentally generated by an earlier write-enabled
  audit were preserved in the ignored local
  `build/py314-cache-quarantine-20260802-1720` directory instead of deleted.
  The installed optional package has zero 3.14 caches, and the write-disabled
  3.14 rerun created none.
- Focused Ruff and diff checks pass. Independent read-only code/test review and
  a separate documentation re-review found no remaining P0--P2 issue. The
  definitive full suite collected 2,561 tests and completed with 2,533 passed,
  28 expected platform skips, zero failures, and zero errors in 820.129 seconds
  (821.5 seconds wall time); its ignored JUnit report is
  `build/localai-reader-full-suite.xml`.

## Previous portable-file-boundary checkpoint (`7495ce1`)

- Core source/artifact/archive reads and shared benchmark JSON/binary/hash reads
  now reject a target whose pre-open or opened-descriptor stat carries Windows
  reparse semantics even when its mode looks regular. Parent guards, identity
  checks, bounded retries, post-read snapshots, and error types are unchanged.
- Files On-Demand placeholders that still carry a reparse tag are deliberately
  refused until materialized as ordinary files; this preserves the existing
  fail-closed path policy.
- SWE-bench repository-worker and patch-tree component checks now trim ASCII
  spaces from the pre-extension stem before matching Windows device aliases.
  `CON .txt`, `COM1 .txt`, and superscript-digit aliases are refused, while
  `COM0.txt`, `COM10.txt`, and `CON name.txt` remain accepted controls.
- The repository-worker source release anchor was updated to the exact reviewed
  worker bytes, SHA-256
  `cb9c8680d74022b7db8aea8b376790a49131f5fd1a1c0b93ba74341302c8a44f`.
- The integrated path/archive/source/repository/patch set passed 112 tests and
  skipped four expected platform-specific cases in 169.94 seconds on Windows.
  Ruff, compile validation, and diff checks are clean.
- The first full supported-suite run collected 2,408 tests and finished with
  2,314 passed, 67 failed, and 27 skipped in 819.71 seconds. All 67 failures
  were in `tests/test_localai_contracts_adapter.py`: a package-local
  `smoke.pyc` created by another interpreter process represented the same code
  as fresh verified-source compilation but had different raw marshal bytes.
  The run is retained as diagnostic evidence, not relabeled green. After the
  portability correction and added regressions, the definitive rerun collected
  2,437 tests and completed with 2,409 passed, zero failed, and 28 skipped in
  832.94 seconds.
- Independent read-only review found no P0--P2 issue in the shared reparse fix;
  a separate independent review approved the SWE-bench path correction, worker
  anchor, tests, and their interaction with the shared reader change with no
  P0--P2 finding.

## Previous LocalAI bytecode-portability correction (`7495ce1`)

- Package-local caches are still bounded regular files and external cache
  prefixes remain unsupported. One at-most-16 MiB, 64-record batch is passed
  through an empty-environment `-I -S -B` worker. Windows assigns the suspended
  process to a 256 MiB Job Object before resume; non-Darwin POSIX clamps the
  inherited address-space and core limits. macOS uses the fixed `/bin/sh -p`
  pre-limit pattern with an exact 1 TiB virtual-address-space ceiling so hosted
  arm64 interpreter mappings fit before startup. One absolute ten-second
  latest-acceptance deadline covers batch construction through comparison;
  mandatory process-tree cleanup has separately bounded grace.
- The worker compiles the verified source associated with every cache before
  unmarshalling any cache, fully consumes each marshal payload, and compares
  const-stripped format-2 serialized metadata, private raw adaptive
  instruction/cache images, and a depth/node/byte-bounded tagged constant graph
  with per-code identity topology. This supports CPython 3.14 slice constants
  without relying on format 5's process-dependent reference layout.
  Cross-process sharing between
  separate nested code objects is normalized because valid compiler processes
  differ there; identity reachable within each code object remains bound.
- The worker propagates the current interpreter's no-debug-range compile mode.
  Cacheful validation requires CPython's private `_co_code_adaptive` bytes and
  therefore fails closed on an implementation that does not expose them.
  Memory/deadline/process-tree containment is not a filesystem or network
  sandbox against a native marshal vulnerability.
- Focused regressions now accept the real independently produced cache, a
  deliberately byte-different independent compiler-process cache, current
  no-debug-range mode, runtime-normalized cross-mode caches, CPython 3.14 slice
  constants, and all 39 supported optimization-cache names in one batch. They
  reject forged code, changed metadata or slice values, malformed or trailing
  marshal data, a raw specialized instruction, changed within-code constant
  sharing, over-limit batches/files, and a hung, setup-delayed, or
  batch-construction-delayed worker. Direct independent-process probes accepted
  all four previously failing reviewed sources on CPython 3.13.14 and all 39
  optimization/source combinations in one batch on CPython 3.14.6. The two
  LocalAI modules passed in reverse order with 166 passed and four skipped
  before two additional invalid-graph regressions passed separately; the final
  full suite includes all of them and is green.
- Three independent read-only re-reviews found no P0--P2 issue in the final
  bytecode semantics, Darwin/deadline containment, tests, or documentation.
  Ruff, diff/compile checks, connector conformance, benchmark self-test, and an
  offline wheel/source-distribution build also passed. No model, candidate,
  grader, GPU, or inference runtime was executed.

## Previous SWE-bench patch-composition checkpoint (`ba49a6e`)

- Added coordinator-only, source/sdist-only `benchmarks.swebench_patch` and
  strict self-hashed schema `ctxc-swebench-patch-composition-0.1`. It adds no
  installed wheel schema and does not change the public 56 prepared / 6
  policy-refused / 438 unattempted denominator.
- Revalidate the canonical `VerifiedSweBenchSource` and source-bound
  `PreparedSweBenchRepository`, rescan the immutable prepared base, and apply
  candidate and hidden text patches to independent temporary copies. The
  bounded near-linear overlap check treats equal and ancestor/descendant
  effective paths as conflicts.
- Retain exactly two statuses. `overlap-detected-not-composable` records the
  conflicts without composition evidence.
  `disjoint-composition-preflight-verified-not-a-grader` applies both deltas to
  a third clean copy and requires exact candidate and hidden replay.
- Strict decode is structural only. Public verify, exclusive write, load, and
  replay canonically snapshot source and preparation evidence, bind the live
  prepared-tree summary, and require exact semantic replay. The structural
  `retained_disjoint` property fails closed on malformed public construction.
- Inline and payload-free binary diffs, extended copy/rename headers,
  file-mode changes, and non-`100644` new/deleted-file modes are rejected
  before native Git parsing.
- Accepted text patches still reach an unsandboxed native Git parser without a
  native memory or filesystem quota. `PatchCompositionError` exposes a
  machine-readable stage and fixed
  `preflight-exception-not-a-cohort-result` disposition. An outer
  complete-denominator ledger remains responsible for retaining and mapping
  that exception.
- Candidate-code and hidden-test execution, official grading, official result,
  external score, usefulness, candidate mount/network/filesystem isolation,
  native quotas, Git-parser sandboxing, and claim readiness remain false.
- The finalized focused suite passed 22 synthetic/adversarial tests in 62.6
  seconds. An independent read-only re-audit passed the same 22 cases and found
  no remaining P0--P2 issue. The integrated source/repository/patch matrix
  passed 39 tests and skipped one expected platform-specific case. Ruff check,
  Ruff formatting, compile validation, and deterministic Python quality lint
  were clean.

## Previous tau2-bench source-binding checkpoint (`43e8489`)

- Added source/sdist-only `benchmarks.tau2` and
  `benchmarks/suites/tau2_text_v1.json`. The descriptor pins annotated tag
  object `b711c1ead46f55111bf765cf44d5da8bacc2d28c`, peeled commit
  `fc0055dc4e0a316c3f83133267fbd6faaa770992`, root tree
  `4837da1c2b310152f63d3d7987f4325183ca6f7c`, exact license/lock/task/split
  bytes, and all three loader implementations. Its self-hash is
  `6c9c6042c380fc82eb26a0f13d9bbd47aae9d8ef7aa07f2f6a49110b947c3163`;
  the 64,244-byte descriptor file has SHA-256
  `e2ebe9deca75c6420b48094f5ec7479afbdcd627c2fae4de8eae46bbee85b807`.
- Reproduced the upstream within-domain loader rule: physical `tasks.json`
  order filtered by `split_tasks.json["base"]`, under the explicitly frozen
  cross-domain order airline, retail, telecom. The selected counts are 50,
  114, and 114. The 15,948-byte `domain<TAB>task_id<LF>` manifest has SHA-256
  `61336d42294a9265ea4b70748e7be0988a98b6064e20e366dbf5d4088b0426a5`.
- Replayed the verifier against ignored `build/tau2-bench-mirror.git` with the
  absolute Git executable. The non-authenticating mirror self-hash was
  `28a70e4a24ca0932288be78359683a7361d9ad67ebc9f6e44991de1d0a6ab83e`;
  the source-verification self-hash was
  `8997eb7e6ed12d40b39395560ce3216d0b82757dbdb7db798c60383f76b7a3fe`.
  A matching configured origin is not provenance authentication, and neither
  Git SHA-1 nor a document self-hash authenticates an author.
- Added `docs/TAU2_EVALUATION.md` after a result-blind source/security review.
  It prohibits the upstream in-process candidate factory because the pinned
  builder passes arbitrary factories live environment-backed tools and the
  full hidden Task. It also records the retail NL-grader empty-results defect,
  external user-simulator nondeterminism, committed-result exclusions, a
  recursive candidate-visible allowlist, and complete failure dispositions.
- Bound the still-pending external-protocol second-task slot to the exact commit,
  suite self-hash, and 278-row full selection. All nine blockers remain; the
  `second-task-suite` blocker now covers the adapter, simulator/grader,
  isolation, execution, reward, and result controls rather than source
  selection.
- The focused tau2 source suite passed 16 tests, including a
  synthetic annotated-tag mirror, re-signed identity/selection mutations,
  loader-order divergence, strict task/split failures, bounded Git lifecycle,
  CLI output, and proof that the upstream `tau2` package is not imported. The
  eight external-protocol tests also passed with the pending second-slot binding,
  and Ruff reported no tau2 source/test findings.
- The source-task candidate allowlist is empty, and all adapter, dependency,
  runtime-boundary, network/filesystem/process-isolation, candidate/simulator/
  grader execution, reward, score, usefulness, legal/redistribution, and
  claim-readiness fields remain false.

## Current Xarray preparation expansion

- Verified the configured `https://github.com/pydata/xarray.git` local bare
  mirror with Git `2.55.0.windows.3`. Its non-authenticating mirror self-hash is
  `35b8f2ac2aba3d3860fc1da39c6211f4d1c953f7d7a22d7493551eadab413cf9`.
- Prepared every selected Xarray row, contiguous ordinals 298--319: 22 prepared
  and zero refused. The manifests total 1,558,923 bytes and bind 6,160 regular
  files, 138,899,190 blob bytes, 681 tree objects, and 285,569 tree-object
  bytes.
- Inspected license/notice filenames and root packaging declarations from every
  exact selected base commit without checkout or candidate execution. All 22
  commits contain the same 10,273-byte Apache License 2.0 root `LICENSE`,
  SHA-256
  `73ba74dfaa520b49a401b5d21459a8523a146f3b7518a833eea5efa85130bf68`.
  Packaging metadata uses `Apache` through ordinal 315 and `Apache-2.0` at
  316--319. This is discovery evidence, not legal approval.
- Rebuilt the ignored 500-row preparation-only ledger after two complete live
  preparation replays. Its 488,750-byte JSON file has SHA-256
  `2219e2f1526c51f4c965a7af41364c393075151fecc2e2b4553eb037e359b772`
  and self-hash
  `b5668f76a4949d42310ce644de007696a6d9ee3725d3d0514edabf6887082f34`.
  The unchanged 53,734-byte null-prediction JSONL has SHA-256
  `546a3e42b9cf5bfcf7165eb244b5da909ff069971519e0ac7f083b413f950e60`.
  A fresh interpreter reloaded and replayed all 500 rows plus the live JSONL in
  229.25 seconds with the same cohort, file hashes, and self-hash.
- The current full public-preparation evidence therefore binds 56 trees / 23,467
  files / 253,569,052 blob bytes, plus the six exact Pylint symlink-policy
  refusals. Origin authentication, redistribution approval, candidate mount,
  execution, isolation, hidden-test application, grading, score, usefulness,
  and claim readiness remain absent or false.
- The focused SWE-bench source, repository, and prediction regressions passed in
  191.8 seconds; the one expected POSIX directory-mode case skipped on Windows.
  The deterministic documentation gate reported no warning or error. Its only
  signals were the longstanding document-level over-sectioning advisories for
  TODO, changelog, and handoff.

## Completed controller-run checkpoint (`6109de1`)

- Added source/sdist-only `benchmarks.swebench_run` with
  `ctxc-swebench-run-ledger-0.1`, strict controller result schema
  `ctxc-swebench-controller-result-0.1`, and protocol
  `append-request-json-and-workspace-path-v1`. The dependency-free wheel and its
  26 installed schemas remain unchanged by design.
- Reconcile every source-selected row in physical order over repository,
  workspace, launch, process, stream, output, and capture dispositions. Missing
  workspace and every failure remain in the denominator; a final workspace
  observation failure cannot replace an already reported launch or process
  disposition within one internally consistent ledger.
- For a prepared row, require a caller-provided mutable workspace to equal the
  prepared tree before launch. Write only the exact two-field task request,
  append request/workspace paths as literal arguments, retain bounded raw
  stdout/stderr prefixes, and record initial/final workspace summaries plus a
  deterministic delta.
- Reconstruct patch captures only from replayed complete stdout. Bound patch,
  trajectory, token, request, workspace, executable, artifact, and ledger work;
  reject overlapping coordinator/workspace/repository paths, unsafe ancestors,
  links, reparse points, hard-linked workspace files, special entries, and
  unexpected run artifacts.
- Keep candidate mount creation, candidate execution authentication,
  mount/filesystem/network/user/PID/image isolation, controller/system/model
  producer authentication, token and trajectory authentication, hidden-test
  application, grading, resolution, score, usefulness, and claim readiness
  false. The controller executable, argv, environment, raw outputs, system
  labels, and ledger self-hash are consistency evidence, not attestation.
- The current 42-pass focused suite uses only synthetic local Git repositories,
  workspaces, and controller processes. Its one skip is the
  directory-symlink-root regression, unavailable because this Windows token
  lacks directory-symlink privilege. No public candidate, model, agent, or
  grader was launched.

## Completed public-preparation checkpoint (`3009500`)

- Attempted selected ordinals 287--297, 320--348 across local HTTPS bare
  mirrors for Seaborn, Flask, Requests, Pylint, and Pytest. Every configured
  origin URL remains unauthenticated provenance.
- Retained 34 successful source-bound manifests: 2 Seaborn, 1 Flask,
  8 Requests, 4 Pylint, and 19 Pytest. Every handle passed a fresh live mirror
  and output-tree replay. In aggregate they bind 17,307 portable regular files,
  114,669,862 blob bytes, 2,063 tree objects, and 867,348 tree-object bytes.
- Retained exact `tree-symlink-forbidden` refusals for Pylint ordinals
  324--329. Each commit contains the same two mode-`120000` paths. These are
  preparation-policy refusals, not candidate or test failures.
- The full selected state is therefore 34 prepared, 6 refused, and 460 not
  attempted. The 34 ignored manifests total 4,460,409 bytes. No row disappeared
  from the 500-task denominator.
- Expanded the discovered license-file inventory without flattening
  commit-specific variants: Seaborn includes five third-party license files;
  Requests varies root `LICENSE` and `NOTICE` bytes and has theme/extension
  licenses; all ten inspected Pylint commits share one root license; Pytest has
  root, documentation, and theme variants. The pinned dataset card still has
  no declared license, and per-path/legal/redistribution review is incomplete.
- Kept repository-origin authentication, redistribution approval, candidate
  mount, filesystem/network isolation, execution, grading, score, usefulness,
  and claim readiness false. This is preparation coverage, not a SWE-bench
  result.

## Completed prediction checkpoint (`3c79856`)

- Added source/sdist-only `benchmarks.swebench_prediction` and
  `ctxc-swebench-prediction-ledger-0.1` without changing the dependency-free
  wheel or its 26 installed schemas.
- Revalidate the exact source/key-bound task projection and every claimed
  successful repository preparation, then reconcile the complete selected
  cohort in physical source order. Every task closes over repository not
  attempted, preparation refused, prepared without prediction, or prediction
  recorded; malformed, missing, duplicate, unexpected, oversized, and
  unprepared-task captures cannot shrink the denominator.
- Emit a canonical UTF-8/LF JSONL with exactly `instance_id`,
  `model_name_or_path`, and `model_patch` once per selected task. Strict UTF-8
  patch text, including NUL, U+FEFF, line endings, and final-newline state, is
  preserved without normalization. The ledger retains only counts and SHA-256
  bindings for accepted patch bytes, canonical lines, and the complete JSONL.
- Require a separately supplied expected code revision/clean-state plus
  model/agent/prompt/tool/controller identity for construction and every live
  replay. Keep system-authentication and candidate-capture-origin claims false;
  rejected raw captures are not retained, so protocol violations are explicitly
  not independently replayable at this boundary.
- Bound per-patch bytes, aggregate retained-capture bytes, capture count, JSONL,
  ledger, and violation work before expensive conversion. Refuse links,
  reparse/special outputs, writes within a prepared tree or mirror, and
  substitution-unsafe cleanup after late failures.
- Keep candidate mount, candidate execution, hidden-test application, grading,
  external score, usefulness, and claim-readiness flags permanently false.
  Local tests are mechanism evidence only, not public SWE-bench results.

## Completed repository-preparation checkpoint (`fbb045b`)

- Added source/sdist-only `benchmarks.swebench_repository` and its standalone
  staged worker with `ctxc-swebench-bare-mirror-0.1` and
  `ctxc-swebench-repository-preparation-0.1` evidence. The dependency-free
  wheel and its installed schema count remain unchanged.
- Require an absolute local SHA-1 bare mirror and absolute regular Git
  executable. Local/global/system config inheritance, lazy fetch, prompting,
  replacement refs, optional locks, shallow/partial/promisor repositories,
  alternates, grafts, includes, linked-worktree config, extra remotes,
  links/reparse points, and special mirror entries fail closed. A canonical
  GitHub origin URL is checked but explicitly not authenticated.
- Stage one release-digest-bound standalone worker in system temporary storage
  and launch it as literal argv with `-B -I -S`. Bounded pipe readers prevent
  Git config/ref output from being retained beyond its cap, and raw
  `git cat-file --batch` readers require exact object frames and clean EOF.
- Recompute SHA-1 object ids and SHA-256 evidence for the exact commit, every
  tree, and every blob without checkout, archive, smudge/clean filters, or a
  serialized output path. Reject symlinks, gitlinks, special files, empty tree
  directories, invalid/non-NFC/nonportable names, case collisions, `.git`
  aliases, Windows device names, and every configured byte/count/depth limit.
- Materialize only fresh independent regular files. Parent verification
  reopens the exact mirror commit, compares the complete live raw export with
  retained evidence, and independently scans paths, directory structure,
  hashes, Git object ids, hard links, modes, user-visible extended attributes,
  Windows integrity-changing attributes, and NTFS alternate data streams.
- Bind source-aware preparation to the exact suite row, ordinal, instance id,
  repository, base commit, and source-record digest. Any late source or mirror
  mismatch cleans the fresh output before failing.
- Keep candidate mount, filesystem/network isolation, repository-origin and
  license authentication, execution, grading, score, usefulness, and
  claim-readiness flags false. The 26 focused tests use only synthetic local
  Git mirrors; they are not public-suite execution evidence.

## Completed literal-process checkpoint (`e3f2d62`)

- Added source/sdist-only `benchmarks.literal_process` without changing the
  dependency-free wheel. Bounded literal arguments, an absolute executable and
  working directory, a portable explicit environment, and immutable numeric
  limits are validated before `Popen`; placeholder and shell expansion do not
  exist.
- Replaced named output spools with two concurrently drained pipes. Each
  stream counts observed bytes but retains only `limit + 1` bytes and that
  prefix's digest. A fast 1 MiB writer triggered the limit with only 17 bytes
  retained and no temporary output path.
- Made the deadline cover containment setup as well as execution, check it
  before launch and before Windows resume, and reject values beyond a seven-day
  operational maximum. Results retain exact limits and separate setup,
  process, cleanup, and total durations.
- Reused shared Windows Job and anchored POSIX process-tree primitives. Windows
  starts suspended and verifies Job membership before resume. POSIX leaders
  remain waitable until group cleanup, while the result explicitly labels that
  group escapable.
- Added an emergency ownership guard for every post-launch exception and
  granular disarming after normal cleanup. Injected unexpected and expected
  post-launch failures prove the process is removed and the original error is
  not replaced by a false cleanup failure.
- Removed unsafe POSIX `preexec_fn` resource limits. Windows may retain
  per-process and aggregate Job memory limits; POSIX memory requests fail
  before launch pending an independently verified external controller.
- Kept `swebench_containment_claim_ready` permanently false. No repository was
  prepared, no candidate or grader ran, and no external score was generated.

## Completed deadline checkpoint (`622822d`)

- Reordered the LRCBench external-runner monitor boundary so it samples elapsed
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

- Clean tau2 checkpoint `43e8489`: the full repository suite passed 2,337
  tests, skipped 27, and passed 112 subtests in 681.1 seconds on Windows.
- A clean build from that committed tau2 checkpoint passed packaging
  inspection: the sdist contains the tau2 module, descriptor, test, and
  evaluation contract, while the wheel excludes the source-only tau2 artifacts
  and retains the unchanged installed schema inventory.
- Patch-composition focused validation after the security repair passed all 22
  synthetic/adversarial cases in 62.6 seconds. Independent review repeated the
  22-case pass and approved module SHA-256
  `ea18be63a4c613acc6dc0f43cb8bc80b740f7b0ebb3a7d29684c699561f86bf3`
  and test SHA-256
  `9cc770cfbbe7631f16fd96577cf664edafdc4cd2af9222a42fed7751d17276ea`
  with no P0--P2 finding. The integrated source/repository/patch matrix passed
  39 tests and skipped one platform-specific case in 141.5 seconds.
- Full integrated repository suite: 2,359 passed, 27 skipped, and 112 subtests
  passed in 675.54 seconds on Windows. No model, GPU, or inference runtime was
  started.
- Precommit package inspection at `SOURCE_DATE_EPOCH=1700000000` confirmed that
  the sdist contains the patch module, test, and evaluation contract, while the
  wheel excludes patch source artifacts and retains exactly 26 installed
  schemas. Exact local archive digests are not embedded into an archive member.
- Ruff check, Ruff formatting, compile validation, and deterministic Python
  quality lint were clean. Documentation quality lint had no error or warning;
  only the longstanding whole-file `AS-TXT-003` structural information on
  `TODO.md`, `CHANGELOG.md`, and `docs/HANDOFF.md` remained.
- Repository-wide Ruff and compile checks passed. Connector conformance passed
  6 golden steps and 66 negative vectors across 17 schemas. The external draft
  protocol, 500-row SWE-bench source descriptor, 278-row tau2 source
  descriptor, natural-history fixture kit, and benchmark self-test all verified
  offline with claim readiness still false where applicable.
- All five committed benchmark reports replayed offline without model or
  inference execution. OneDrive cold-file hydration first triggered the
  fail-closed changed-during-read guard on several inputs; isolated retries
  succeeded with the committed report/corpus hashes and no guard weakening.
- Controller-run focused suite: 42 passed and one skipped in 158.55 seconds on
  Windows. The skip is explicitly the directory-symlink-root case, which could
  not create its fixture because the current Windows token lacks
  directory-symlink privilege. The passing cases use synthetic repositories,
  workspaces, and controllers; they are mechanism evidence, not a public
  SWE-bench candidate run or result.
- Literal-process, repository-preparation, prediction-ledger, and controller-run
  integration matrix: 126 passed and two skipped in 352.05 seconds on Windows.
  The skips are the POSIX-directory-mode case and the same unavailable
  directory-symlink fixture.
- Full repository suite: 2,320 passed, 27 skipped, and 112 subtests passed in
  610.10 seconds on Windows.
- Two independent deterministic sdist builds at `SOURCE_DATE_EPOCH=1700000000`
  were byte-identical. The sdist's run module and test bytes exactly match the
  worktree; the wheel excludes both files and retains exactly the expected 26
  schemas. Exact local archive digests are intentionally not embedded into an
  archive member that would make the recorded digest self-referential.
- Ruff passed repository-wide. The offline deterministic quality gate reported
  no Python findings. Its only documentation signals were advisory whole-file
  over-sectioning notices for the longstanding TODO, changelog, and handoff
  structures; no warning or error fired.
- Public preparation cohort expansion: fresh source-bound replay passed for
  all 34 prepared handles. Seaborn contributed 2 trees / 633 files /
  10,778,538 blob bytes; Requests 8 / 928 / 18,896,934; Pylint 4 / 6,166 /
  12,823,506; Pytest 19 / 9,329 / 70,592,351. Six independent Pylint attempts
  reproduced the exact `tree-symlink-forbidden` refusal for the same two
  symlink entries. With the earlier Flask tree, the cohort is 34 prepared,
  6 refused, and 460 unattempted.
- Preparation-only prediction replay: one ignored 487,299-byte ledger closes
  the full 500-row denominator with 34 prepared/no-prediction, 6 refused,
  460 not attempted, 0 predictions, and 0 protocol violations. Its file
  SHA-256 is
  `8397d853d7d15bbacb20e56d784d8a270d7b506e39231015b934207670ae63cb`
  and self-hash is
  `cfedda6985ca5c088bf123db678793fc9031461c92ebc755c8a0af80a14e24ff`.
  The 53,734-byte, 500-line null-prediction JSONL has SHA-256
  `546a3e42b9cf5bfcf7165eb244b5da909ff069971519e0ac7f083b413f950e60`.
- Public Flask preparation pilot: exact source ordinal 289 and commit
  `7ee9ceb71e868944a46e1ff00b506772a53a4f1d` passed bare-mirror verification,
  raw-object export, live mirror replay, and independent output-tree replay in
  11.7 seconds. The result contained 251 files / 1,578,533 blob bytes and kept
  `claim_ready: false`. The root and artwork license files plus project
  metadata were read directly from that commit; no repository code was
  imported or executed.
- Post-pilot source, repository, and prediction regression run: 94 passed and
  the one POSIX-only directory-mode case skipped on Windows in 189.29 seconds.
  A fresh live reconstruction of the ignored Flask preparation handle also
  passed source-bound mirror and tree replay before the test run.
- Prediction-ledger focused suite: all 47 cases passed on Windows.
  It covers exact UTF-8 patch preservation, empty predictions, complete-cohort
  closure, source/key/preparation/system replay, missing/duplicate/unexpected/
  malformed/oversized captures, hostile subclasses and lying sequences,
  aggregate work replay, hard links, output-tree/mirror guards, descriptor and
  A-B-A substitution, identity-safe late cleanup, forged/resigned ledgers, and
  every fixed-false execution/grading/claim state. The final combined
  prediction, strict-JSON, atomic, and parent-safety run passed 67 with three
  expected platform skips in 101.8 seconds.
- Repository-preparation focused suite: 26 collected on Windows in 88.3
  seconds; 25 passed and the POSIX-only directory-mode regression skipped.
  It creates real synthetic local Git mirrors and covers exact commit/tree/blob
  export, worker isolation and release digest, bounded Git output, clean batch
  EOF, worktree/partial/promisor/alternate/include rejection, forged evidence,
  source/limit binding, hard links, symlinks, FIFO/special files, extra empty
  directories, and readable NTFS alternate data streams. Ruff and `py_compile`
  passed after the final production repair. The staged worker's release-bound
  source SHA-256 is
  `3d40d21af1bdfd8ce7af0b4d445fd62748aa20162b4ff674e40c08c916fb0239`.
- Literal-process focused suite: 12 passed on Windows, including preflight
  rejection, literal metacharacters, exact environment hashing, exact/cap+1
  stream boundaries, a fast 1 MiB burst, prelaunch and post-launch deadlines,
  sanitized `Popen` failure, unexpected-exception cleanup, expected-fatal
  ownership disarming, Windows Job memory scope, and permanent SWE-bench
  non-readiness. Focused module coverage is 84%; Ruff and compileall passed.
- The combined literal-process, external-runner, and compile-deadline set
  collected 252 tests: 245 passed and seven existing platform skips remained
  in 37.8 seconds.
- External-runner focused deadline tests: 2 passed. The complete
  172-test `tests/test_external_runner.py` file passed with 168 passes and four
  existing platform skips; Ruff passed for the changed module and tests.
- `python -m pytest -o addopts='' -q -ra`: 2,278 passed, 26 expected
  platform/optional skips, and 112 subtests passed in 445.52 seconds.
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
- Two `SOURCE_DATE_EPOCH=1700000000` wheel/sdist pairs were byte identical.
  Each sdist was 1,323,604 bytes with SHA-256
  `7c1d5089c729f3d28257ffda83960776c671f4cf4a74f8ade0fc0a765a41320c`;
  each wheel was 316,638 bytes with SHA-256
  `726ca9ef97f6943c33ba7277dba10180268c00486aaf68fd70a53053d60a4499`.
  The reproducibility report passed with self-hash
  `d4af42633fbc07ed105f04ff7fc3f73a8a122ecab41f9b7b6f162b19b0ccafd8`.
  Both new prediction files were present in the sdist; the source-only module
  was absent from the wheel, which retained exactly 26 schemas. The clean
  wheel/sdist install smoke passed for both artifacts with schema count 26 and
  byte-identical source/wheel/sdist materialization evidence.
- Two fixed-epoch sdists built before recording this evidence were byte
  identical at 1,296,166 bytes with SHA-256
  `c75d30376ea49412e9b2c9b45a69c94db2dbf9978e73731334ea05328c2d2e91`.
  Both repository-preparation modules and their test file were present. The
  companion 316,367-byte wheel had SHA-256
  `9eef277595dc917519997ee3aa1cde36158bbd02ee1d2e11d2fc9c1e9f5b616b`
  and excluded both source-only repository modules.
- A deterministic verification sdist built before recording this evidence at
  `SOURCE_DATE_EPOCH=1760000000` contained
  `benchmarks/literal_process.py` and `tests/test_literal_process.py`:
  1,260,225 bytes, SHA-256
  `5c46179f35c338dc6fe301532c48b8752f65e10447ee815a2c98b3e632600659`.
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
  superiority from this source/preparation checkpoint.
- Repository preparation has now been exercised against 62 selected public
  rows across six repositories in addition to synthetic local mirrors. The
  56 preparations and six policy refusals do not authenticate GitHub,
  authorship, freshness, or license; 438 rows remain unattempted.
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
- The patch-composition checkpoint is a synthetic mechanical preflight. Its
  native Git text parser is unsandboxed and lacks native memory and filesystem
  quotas. Its overlap or disjoint status is not a hidden-test execution,
  grader, result, score, usefulness, isolation, or claim-ready outcome, and an
  exception is not a cohort disposition until the outer ledger retains it.
- The existing `lrcbench-external-run-manifest-0.13` runner accepts bounded
  rendered memory, not repository patches. Reusing that wire would be a false
  equivalence.
- The controller-run ledger retains raw candidate stdout and reconstructs
  accepted captures from replay, closing the prior byte-consistency gap for
  controller-owned runs. It still does not authenticate the controller, model,
  agent, expected system digests, token counts, trajectory, or raw-output
  producer. Rejected prediction-ledger captures that did not pass through this
  run boundary remain coordinator assertions rather than independently
  replayable evidence.
- The preparer trusts the coordinator host, Python runtime, and hashed Git
  executable/pack parser. Host-level path substitution outside its guarded
  observations remains a documented trust boundary; the output is not a
  candidate filesystem, mount, user, PID, or network sandbox.
- Controller workspace traversal and executable launch remain pathname-based,
  not descriptor-pinned against concurrent nested-directory or executable
  substitution. Process exit, trigger, timing, and cleanup evidence is also an
  unauthenticated coordinator observation that a producer can relabel while
  recomputing an internally consistent self-hash. Rows with a failed final scan
  have no retained final contents to replay.

## Next exact actions

1. Acquire the scikit-learn repository through a reviewed channel, inspect the
   exact base-commit license evidence, and apply the raw-Git preparer to
   ordinals 349--380 (32 selected rows). No local mirror or license evidence is
   currently retained. Continue through the other unattempted repositories and
   preserve every success or refusal without silently narrowing the 500-row
   cohort.
2. Do not treat the controller-run ledger or the literal lifecycle's Windows
   Job/POSIX process group as filesystem, network, PID, user, mount, or image
   isolation. Provision and retain independently verifiable containment before
   launching any public candidate, while preserving the ledger's fixed-false
   authentication and grading fields.
3. Use the implemented complete-cohort prediction ledger for the official
   `instance_id`, `model_name_or_path`, and `model_patch` interchange. Add
   separately authenticated model/token/trajectory evidence and raw grader
   output without weakening either ledger's fixed-false claims.
4. Use the source/sdist-only synthetic patch-composition preflight before any
   grader, retain each `preflight-exception-not-a-cohort-result` disposition in
   an outer complete-denominator ledger, and separately prove exact hidden-patch
   application inside a hardened evaluator. Independent copies and exact
   disjoint replay do not turn the unsandboxed native Git parser into isolation
   or grading evidence; the pinned v4.1 harness remains vulnerable on 18 rows
   with hidden additions or renames.
5. Build a separately launched candidate adapter for the now source-bound,
   separately result-blind-reviewed tau2-bench v1.0.1 half-duplex text core.
   Keep the complete upstream task, simulator, golden-action, assertion, grader,
   result, and checkout material outside the candidate boundary.
