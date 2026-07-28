# CtxC OpenHands release checklist

This checklist is for a candidate `ctxc-openhands` artifact. It does not
authorize merging, publishing, a live OpenHands deployment, or an upstream RFC
submission. Record every command, environment identity, exit status, artifact
digest, and remaining red gate. Never overwrite failed evidence with a later
run.

The integration is alpha and separately packaged. The dependency-free core
must continue to install and run without OpenHands.

## 1. Scope and authority

- [ ] The candidate contains only reviewed integration changes.
- [ ] Commit author and committer attribution are `JamesNguyen42`.
- [ ] No Git LFS pointer or LFS-managed artifact is introduced.
- [ ] No production PyPI upload, release tag, merge, or upstream submission is
      included in the candidate operation.
- [ ] No superiority or semantic-completeness language was added.
- [ ] All failed test, replay, live-preflight, crash, and external-run evidence
      remains a failure record.

## 2. Exact compatibility identity

- [ ] OpenHands is exactly `1.8.0` at
      `bc26df351dd5d833a95131556dbe2da69af82253`.
- [ ] SDK/tools/agent-server are exactly `1.27.0` at
      `904279edf2df5fa12d7caecc7576f62659b2e2dd`.
- [ ] The 23-file reviewed API inventory and aggregate digest verify without
      regeneration.
- [ ] License, source, lock, artifact, and private-seam digests verify against
      the frozen compatibility manifest.
- [ ] Python is CPython 3.12 or 3.13.
- [ ] The compatibility upgrade procedure was result-blind; no pin was changed
      after observing a test result.

## 3. Import and dependency isolation

- [ ] A clean core-only wheel environment reports no core runtime
      dependencies.
- [ ] `import context_compiler` imports no `openhands` module.
- [ ] Ordinary `import ctxc_openhands` imports no `openhands` module.
- [ ] `openhands-ai`, `openhands-sdk`, `openhands-tools`, and
      `openhands-agent-server` are absent from every offline CI environment.
- [ ] The integration is installed with the exact core dependency and without
      the `live` extra.
- [ ] The live lock is still labeled direct-artifact evidence, not a complete
      dependency closure.

## 4. Event, authority, and atomicity gates

- [ ] The
      [event and authority map](EVENT_AUTHORITY_MAP.md) remains exactly aligned
      with `SUPPORTED_EVENT_KINDS`, and all 18 reviewed event kinds have
      explicit positive coverage.
- [ ] Unknown event kinds and unexpected fields fail closed.
- [ ] Host source/role claims cannot promote authority.
- [ ] Assistant, tool, retrieval, attachment, file/search, hook, and
      delegated-agent output remains untrusted without an independently
      verified receipt.
- [ ] Unknown tool names remain generic and cannot receive authority.
- [ ] Authority receipts bind the exact event, session, ID, kind, role, issuer,
      and optional tool name; mutation and replay tests pass.
- [ ] Action/result linkage checks call ID, tool name, and action ID where
      available.
- [ ] Incomplete or duplicate tool groups cannot become visible to compaction.
- [ ] Known transient/derived events are explicitly refused as durable source
      history rather than silently discarded.

## 5. Request accounting gates

- [ ] The immutable ledger covers prompts, verified memory, recent tail,
      current turn, retrieval, attachments, tool schemas, and provider
      framing.
- [ ] Model, route, source head, active generation, semantic digest, tokenizer
      identity/vectors, tool-schema digest, reserves, margin, and request
      digest replay exactly.
- [ ] Component counts sum to the total count.
- [ ] Payload plus reserved output plus margin cannot exceed the hard limit.
- [ ] The bundled exact tokenizer is labeled exact only for the offline
      canonical-UTF-8-byte protocol.
- [ ] `character-estimate-v1` and every provider estimate remain labeled
      estimated.
- [ ] Real `run()`/`arun()` remains refused until the exact final immutable
      provider request is supported.

## 6. Transaction, recovery, and source-retention gates

- [ ] SQLite runs on a local, non-cloud-synchronized, non-symbolic path with WAL,
      `synchronous=FULL`, foreign keys on, dirty reads off, trusted schema off,
      and bounded busy timeout.
- [ ] Network, distributed, cloud-synchronized, UNC, linked-parent, and
      in-memory database paths are outside the qualified storage boundary.
- [ ] Store schema 2 is required; schema 1, unknown schema, repair, journal
      conversion, and automatic migration remain fail-closed.
- [ ] New stores, report outputs, and backups are exclusively created and
      cannot overwrite an existing file, link, alias, or failed-evidence path.
- [ ] The `prepared` → `verified` → `committed` → `active` state machine passes
      transition and tamper tests.
- [ ] Only one verified `active` generation is visible.
- [ ] Source-head, parent-generation, and active-epoch compare-and-swap tests
      pass.
- [ ] At least 1,024 seeded schedules cover append plus
      `generation.prepare`, `generation.verify`, `generation.commit`, and
      `generation.activate`; every exact fault point is represented before a
      complete domain cycle repeats.
- [ ] Every seeded schedule uses a real three-worker simultaneous-start race,
      and the report binds the seed, schedule digests, domain/fault totals,
      outcome totals, and self-hash.
- [ ] Spawned abrupt-process cases cover append and generation precommit,
      postcommit, and activation boundaries. The parent reopens the database
      and proves exactly-once source counts, no loss/duplication, old-or-new
      verified visibility, and integrity on Linux, Windows, and macOS.
- [ ] Cross-process writer contention and stale-candidate tests also pass on
      all required operating systems.
- [ ] A crash exposes exactly the old or new verified generation.
- [ ] `superseded` and `rolled_back` generations remain retained.
- [ ] No source-event update/delete path exists; recovery and rollback never
      rewind source history.
- [ ] Rehydrated exact spans remain labeled `untrusted-evidence`.

## 7. CLI and operational gates

- [ ] `doctor` exits 0 only for offline readiness and reports
      `ordinary_import_loaded_openhands: false`.
- [ ] `doctor --require-live` exits 2 in the recorded environment and retains
      blocker `hash-pinned-wheelhouse-absent`.
- [ ] `explain`, replay, recovery, rehydration, and verification open stores
      with `require_existing=True`; a typo never initializes, repairs, or
      migrates a database.
- [ ] `explain` reports source head/count, active generation, transitions, and
      integrity without widening certificate claims.
- [ ] Standalone and stored `replay` reproduce every bound count and digest;
      mismatch exits 2.
- [ ] `recover` defaults to inspection; `--apply` uses only verified
      transitions and retains rolled-back candidates.
- [ ] `rehydrate` verifies the requested quote digest and returns untrusted
      evidence.
- [ ] Callback failure poisons later dispatch and reconciles only from the
      complete persisted EventLog in exact order.
- [ ] `ask_agent()` and `execute_tool()` remain explicitly refused.
- [ ] `verify-evidence --report REPORT --database DATABASE` exits 0 only after
      combined JSON-and-database verification; JSON-only inspection reports
      `passed: false`, scope `json-only`, and exits 2.
- [ ] Scenario, soak, and crash-campaign verification binds the canonical
      self-hashed report to the exact checkpointed SQLite SHA-256/size, absent
      WAL/SHM sidecars, retained source/generation/integrity state, and request
      ledgers where applicable.
- [ ] Scenario and soak verification also binds the sole session and exact
      ordered generation count/ids, epoch/parent lineage, terminal states,
      passed-verification flags, activation transitions, source counts/heads,
      bundle and semantic digests, active pointer, and report-specific arrays.
- [ ] SQLite or filesystem failure during final-request replay returns one
      structured failed verification and never escapes as an unhandled error.
- [ ] Evidence report input/canonical output remains capped at 4 MiB, depth 64,
      250,000 structural items, and 1 MiB per string; scenario/soak SQLite is
      capped at 16 GiB and campaign SQLite at 512 MiB.
- [ ] Soak refuses more than 10,000 events or 100 compactions.
- [ ] Captured CLI argument vectors are reconciled with report parameters and
      are not described as executable, shell, environment, container, or
      operator attestation; library calls retain an empty argument vector.
- [ ] Scenario/soak reports retain
      `isolation.network_isolation_enforced: false`, and the campaign retains
      `network_isolation_enforced: false`; separate runtime evidence, not an
      edited report, records any `--network=none` enforcement.

## 8. Test and static-analysis matrix

- [ ] Linux, Windows, and macOS pass on Python 3.12.
- [ ] Linux, Windows, and macOS pass on Python 3.13.
- [ ] The ordinary isolated suite, including bounded crash primitives, passes
      in every automatic matrix lane.
- [ ] The exact mission-size test is present in the `retained_evidence`
      selection and absent from the ordinary selection in every matrix lane.
- [ ] Ruff passes for integration source, tests, and CI scripts.
- [ ] `compileall` passes for integration source, tests, and CI scripts.
- [ ] The root core CI remains green on Python 3.11–3.13.
- [ ] CodeQL and dependency review remain green.

The required isolated workflow is
`.github/workflows/openhands-integration.yml`. Its matrix must not install or
import a live OpenHands dependency. Automatic lanes must qualify an unlinked
descendant of `RUNNER_TEMP` before exporting Python temporary-directory
variables. The 10,000/100 soak and 1,024-schedule campaign are a separate
manual/default-off retained-evidence gate; they must never be selected by an
ordinary push or pull-request package lane.

## 9. Build and clean-install gates

- [ ] Build tooling versions and the Python runtime are retained with the
      evidence.
- [ ] The core wheel builds with no dependencies.
- [ ] The integration wheel and sdist build without the live extra.
- [ ] Two fresh source builds and one extracted-sdist rebuild use the same
      retained epoch/toolchain and produce one exact final sdist name, size,
      and SHA-256 without overwriting any candidate.
- [ ] Wheel contents include both self-hashed compatibility/tokenizer data
      resources and exclude OpenHands code.
- [ ] Sdist contents include the license, README, direct-artifact live lock,
      compatibility evidence, packaged data resources, configured
      `pyproject.toml`, manifest, and package-local PEP 517 backend. The
      backend must remain absent from the wheel.
- [ ] A fresh wheel environment installs core then integration with
      `--no-index --no-deps`.
- [ ] A separate fresh sdist environment installs with no build isolation,
      using only the retained exact build-tool wheelhouse.
- [ ] The build-tool wheelhouse itself comes from a reviewed, hash-pinned
      offline closure. Current CI downloads exact versions online without
      `--require-hashes`.
- [ ] Both clean environments disable user-site and inherited `PYTHONPATH`.
- [ ] Both clean environments pass import isolation, ordinary doctor, retained
      live-blocker, console-entry-point, and packaged-resource checks.
- [ ] Artifact and clean-install report SHA-256 digests are retained.

## 10. Offline evidence gates

- [ ] The recorded offline scenario performs three forced compactions, one
      injected activation crash/restart, and exact fake-request replay.
- [ ] The scenario retains the PostgreSQL and authentication constraints.
- [ ] Its report says `passed-offline-fake-runtime`,
      `live_openhands.status: blocked-not-run`, and
      `semantic_completeness_claimed: false`.
- [ ] The deterministic 10,000-event/100-compaction soak passes and retains its
      self-hashed report and database.
- [ ] The crash/concurrency campaign report is independently verified and
      retains its database, exact count, seed, all five transaction domains,
      schedule-manifest digest, outcome totals, six abrupt-process cases, and
      `semantic_completeness_claimed: false`.
- [ ] Each scenario, soak, and campaign report passes a fresh DB-backed
      `verify-evidence` invocation against the exact retained database.
- [ ] JSON and SQLite are preserved together at non-overwriting paths with the
      command, exit status, runtime/environment, resource limits, and external
      isolation evidence.
- [ ] No offline result is described as a live OpenHands demonstration.
- [ ] Every failed attempt remains a failed record and was not retried into,
      replaced at, or reclassified under the same evidence path.

The 2026-07-27 scenario and soak pairs are hash-intact historical artifacts that
passed the then-current verifier. Independent review later found that verifier
lacked ordered report-to-generation row binding. Those pairs were not modified
or relabeled and have not been reverified under the strengthened verifier. The
campaign remains a separately verified historical pair and was not affected by
that finding, but it was not rerun in this review cycle. Keep the release boxes
open until new non-overwriting scenario/soak pairs pass the strengthened
verifier, a campaign pair passes its applicable verifier, and the exact pairs
plus outer runtime evidence are durably retained.

## 11. Documentation and supply-chain gates

- [ ] Package quickstart, runbook, compatibility policy, container demo, and
      upstream RFC match the candidate behavior.
- [ ] The container demo uses no package-install step, no live dependency, no
      network, and an operator-supplied immutable CPython base-image digest.
- [ ] Scenario/soak container invocations use a read-only root plus a dedicated
      writable evidence bind mount; `--network=none` enforcement is retained
      outside the report because the report does not self-attest isolation.
- [ ] Security/authority, atomicity, source retention, crash states,
      exact-versus-estimated accounting, and claim boundaries are explicit.
- [ ] The live blocker remains visible in human- and machine-readable
      evidence.
- [ ] An SBOM/provenance/signing plan exists for a future approved release;
      absence is recorded as a red gate rather than fabricated.

## 12. Final decision

- [ ] Every required GitHub Actions lane is green.
- [ ] Every artifact digest matches the reviewed candidate.
- [ ] Every public API change has been reviewed for standalone core behavior,
      authority, provenance, replay, bounded decoding, and certificate
      boundaries.
- [ ] Remaining red gates are listed below with owners and exact blockers.
- [ ] The pull request remains draft unless explicit approval says otherwise.
- [ ] No merge, production publish, release, or upstream submission occurs
      without explicit approval.

### Remaining red gates

Record each unresolved gate; do not delete this section when it is non-empty.

- Hosted retained evidence: the first failed package matrices remain retained.
  Exact implementation head `1f684d975005a7e552f62f36dfb7309a58b11799`
  passed all six automatic Linux, Windows, and macOS Python 3.12/3.13 package
  jobs in push run `30331509718` and all six in pull-request run `30331512148`.
  Exact packaging head `2f692484272aa36bf267703cad2bb4d6926676ff`
  later passed all six automatic package jobs in push run `30341548763` and
  all six in pull-request run `30341552866`, including the recursive-sdist
  regression. Root push CI `30341549065` and pull-request CI `30341552713`
  each passed 7/7 jobs; CodeQL `30341552714` and dependency review
  `30341553236` passed.
  Exact package-gate implementation head
  `f9ba3de6f0ab2ac7e861bd7da907e6949df1339e` then passed six package jobs plus
  the separate aggregate in OpenHands push run `30355357305` and pull-request
  run `30355359105`. Root push CI `30355357152` and pull-request CI
  `30355359094` each passed 7/7 jobs; CodeQL `30355359110` and dependency
  review `30355359096` passed. The push report raw SHA-256/self-hash are
  `e9203177989fab536e70febcf5316ba6ea21d2a39ea2d3f8dc2c46b146a6f743`
  and `9931d25167831dea6698e0794a93e1cc46a1fc23ed29126c94708aefe7efb35c`.
  The pull-request report is bound to synthetic merge revision
  `a585ddbea770e49ff23f49b091536a86fa9db295` with the implementation tree; its
  raw SHA-256/self-hash are
  `9c8ef8530ffa37c6596d94070f75aaed95ab532af3fbf9737870bfa53b08c942`
  and `8ccf05ab81647db1d5030f79ee5e9f367318e923b539b9376cf3e015b04ff2c5`.
  The retained-evidence jobs were skipped/default-off, so the manual durable
  retained-evidence gate remains pending. Both aggregate artifacts expire on
  2026-08-11; build-input closure, live execution, SBOMs, signatures,
  provenance attestations, and release authorization also remain open.
- Evidence verification and retention: the hash-intact 2026-07-27 scenario and
  soak pairs were not reverified under the strengthened report-to-database
  verifier. The separately verified campaign pair was not affected by that
  finding. None of the three is durably hosted or current-head release evidence.
- Live OpenHands execution: blocked by `hash-pinned-wheelhouse-absent`. The
  exact dependency closure is not available in a local hash-pinned wheelhouse,
  and no supported immutable final-provider-request/exact-tokenizer accounting
  hook exists.
- Historical integration sdist failures: repeated wheels were byte-identical at
  `8df8d4a0890daf149461205293d308329212a5c107c25ee4d1f0d068a2d88db1`,
  but repeated sdists differed:
  `40e6916ad15899a2a76549ce6b23967b39a242bb7357d3ee8386c012b823f91b`
  versus
  `09d4b9263fb2c7cc32930cbb7f02ae20f94265ac8e3e800583a9d31683e7a12c`.
  Setuptools varied gzip/member timestamps across 17 generated members; the
  failed comparison remains a failure.
- The later `1bb4ee2d` exact-archive pair remains retained separately:
  wheel
  `9b8782e0355e49e5412b3163fc4f69c287afa12294bc3e48034f0fd0111a699d`;
  sdists
  `09ae8db3ddc9805166b46881409add621bec02984a3ce3f6740ddef80853e3c3`
  and
  `145da6a021441e23dc5be6192e1a8b11afdff18901ece30d5f89a758b21a10d1`.
  The two sdist payload inventories matched while 17 generated timestamps
  differed.
- Initial wrapper head `cf8b8d3` remains a failed recursive result:
  231,581 bytes /
  `b6f4ed61459b5ad9ffb1eb49428ca69be139ce865f7a01f9278ef7df4bffc004`
  rebuilt to 231,588 bytes /
  `873c1e5959f924a71fbaafb8d7a1a8133bc7c64f90210e9bc1675045eb37196e`
  because only `SOURCES.txt` changed. Its hosted automatic jobs passed but did
  not exercise that missing regression, so the checkpoint is historical.
- Same-toolchain final-sdist repeatability is closed at packaging
  implementation head `2f692484272aa36bf267703cad2bb4d6926676ff`:
  two fresh exact Git archives and an extracted-sdist rebuild all produced
  232,006 bytes at
  `9a8f5035d8cbe904dc03142b3be54e3e15fae699153fb7630b4948b7415ac6be`
  under epoch `1785225894` and the recorded toolchain. At that checkpoint this
  did not close build-input, cross-platform equality, or release gates. The
  later `f9ba3de` aggregate closes only equality across its six recorded hosted
  package lanes.
- Build-input closure: CI installs exact build-tool versions from the configured
  index without reviewed hashes. A later `--no-index` artifact install does not
  authenticate the online-acquired wheelhouse.
- Supply-chain attestations: no candidate SBOM, artifact signature, or
  provenance attestation has been generated. Their absence is retained as a
  release red gate; checksums and self-hashes are substitution-detection
  groundwork, not signatures or attestations.
- Production release: not authorized.
