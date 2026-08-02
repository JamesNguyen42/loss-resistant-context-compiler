# Agent progress

Last updated: 2026-08-01 (America/Los_Angeles)

## Resume point

- Branch: `codex/openhands-live-agent-beta`
- Upstream: `origin/codex/openhands-live-agent-beta`
- Base commit for this in-progress checkpoint: `6eae3c4`
- Current checkpoint: LocalAI 1.0 SourceEvent/ContextBundle conversion auditing.
  Code, schema, tests, documentation, and release-shaped checks are complete
  and are being committed together.
- Previous checkpoint: Linux inference-service identity portability was
  committed and pushed as `6eae3c4`.
- Next task: begin a credential-free, source-bound SWE-bench evaluation
  adapter. Freeze the public suite revision and license evidence, ingest
  gold-free task inputs without inspecting test patches, and emit the existing
  external-runner manifest/result format before attempting any model-backed
  score.

## Mission status

1. Linux `/proc/<pid>/exe` portability: complete and pushed in `6eae3c4`.
2. LocalAI 1.0 contract convergence: the isolated optional adapter, exact-wheel
   validation, exhaustive conversion audit, golden corpus, and upstream
   contract request are complete in this checkpoint. The shared wire is
   unchanged.
3. Public long-horizon suite evidence: no successful external score exists.
   Credential-free, source-bound adapters for at least two public suites remain
   required before any external-usefulness claim.
4. Natural-history benchmark, maintainability, and performance work: continue
   after the P0 external-evidence gap.

## Completed in this checkpoint

- Added provider-local `ctxc-localai-conversion-audit-0.1` diagnostics without
  adding a shared manifest field, operation, request wrapper, or response
  member. Ordinary `context.compile` still returns the exact shared
  `ContextBundle` document.
- Classified all 16 shared-to-private SourceEvent paths and all 47
  private-to-shared ContextBundle paths as represented, normalized, derived,
  defaulted, omitted, or rejected. Inventory and represented-relation checks
  also run on the normal wire path without serializing the sidecar.
- Reconstructed every shared SourceEvent through the private record and
  compared bounded canonical bytes. Preserved SHA-256, SHA-512, and
  BLAKE2b-256 content hashes, all seven shared roles, metadata, provenance,
  trust, and exact authority normalization.
- Bound `verify_conversion_audit` to actual typed caller SourceEvents and the
  actual typed output bundle. It recomputes payload/event/output digests,
  deterministic bundle and source-store identities, exact UTF-8 spans,
  provenance, canonical list order, token accounting, source coverage, and
  adapter-fixed omission/overflow shapes.
- Added a strict machine-readable claim boundary. It distinguishes
  caller-evidence-bound values, provider assertions, assertion-dependent
  values, and output-bundle assertions; defines `{ordinal}` path-pattern and
  output RFC 6901 roots; and states that the self-hash is mutation detection,
  not authentication.
- Marked the sidecar privacy-sensitive. Raw ids, content, metadata, and
  authority issuers are excluded, including digest-shaped canaries, but stable
  unsalted digests remain linkable and dictionary-testable and are not safe
  telemetry.
- Added a 5,716-byte, LF-only, self-hashed seven-role golden fixture. Its final
  raw SHA-256 is
  `2e337dd2a20b246d827840639bde6d11e29b39ca2861a6fb986ccb16e98f8762`;
  the canonical fixture self-hash is
  `05292bab6287bedf22767b473eceda35332e9ca9de547f01fe2f0559d5827264`.
  The frozen audit is 17,374 canonical bytes with document SHA-256
  `525ec1cd2530450ecdeae29fa96724f5e1de7c4f32438af4b5ef8fed837eb7bc`.
- Added dependency-free routine-CI tests for strict bounded golden decoding,
  duplicate/non-finite rejection, frozen bytes, canonical self-hash, schema
  audit, claim-boundary constants, and the trusted-state authentication
  conditional. Optional exact-wheel tests remain fail-closed when the reviewed
  wheel is absent.
- Hardened the clean-install validator so the console and literal `-m`
  subprocesses remove `PYTHONHOME`, `PYTHONPATH`, and
  `PYTHONPYCACHEPREFIX`, enable safe-path/no-bytecode behavior, and run from
  the clean environment rather than the checkout.
- Added `docs/CONTRACT_REQUESTS.md` with the exact upstream request for
  operation-specific schema negotiation. It accounts for the closed 1.0
  manifest, requires an explicit old-peer selection/retry boundary, and uses
  an RFC 6901 `payload_array_path` plus explicit `each_item` semantics instead
  of an ambiguous wildcard pointer.
- Installed schema inventory is now 26 files: 17 shared connector schemas,
  eight historical/core artifact schemas, and one provider-local conversion
  audit schema. Wheel/sdist routing and explicit CI archive checks include the
  new schema, fixture, contract request, validator, and dependency-free tests.

## Verification evidence

All successful commands used the repository Python 3.12 virtual environment.
Optional-contract checks used `PYTHONDONTWRITEBYTECODE=1` so the independently
verified installed contracts package was not mutated by cache files.

- `python -m pytest -q`: 2,170 passed, 24 skipped; 2,194 collected; exit 0 in
  247.5 seconds.
- Focused adapter, dependency-free audit conformance, packaging, and connector
  conformance tests: 147 passed, 3 skipped.
- `python -m ruff check .`: passed.
- `python -m compileall -q src benchmarks tests scripts conformance integrations _ctxc_build_backend.py`:
  passed with an external temporary bytecode prefix.
- `python conformance/run_connector_conformance.py`: 17 schemas, 6 golden
  steps, and 66 negative vectors passed.
- `python -m benchmarks --self-test`: passed. Checked-in LRCBench, phrase,
  Qwen phrase, Qwen literal-ablation, and Qwen paired-extractor reports all
  passed their strict offline `--verify-report` paths.
- A short-path staged source snapshot produced a 315,731-byte wheel with
  SHA-256
  `e1f720fe5047a29cbf219b4b0348b7fc715b66dfd1eff9b3319261a363f59898`
  and a deterministic sdist. `scripts/release_install_smoke.py` passed both
  isolated artifacts with `schema_count: 26` and byte-identical materialized
  context witnesses.
- `scripts/validate_localai_contracts_install.py` passed provider-only,
  expected-fail-closed transitive-extra, and direct exact-wheel lanes. The
  reviewed contracts wheel SHA-256 was
  `36a02dbc4267402949dddda1da180d800590cc579e0c1ecb022fc96f6a7c29ae`;
  clean installed Phase 0 reported 22 passed, 0 failed, and
  `inference_status: not_run`; NDJSON and in-process results matched.
- Adversarial review and tests covered rehashed evidence substitution,
  authority/private-digest claim boundaries, output rebinding, deterministic
  ordering, false span/provenance relations, omission/overflow forgery,
  incomplete field policy, raw digest-shaped canaries, strict JSON loading,
  and inherited import-path shadowing. Ruff, AST/JSON parsing, Draft 2020-12
  metaschema validation, and `git diff --check` passed.

## Environment observations and retained evidence

- The reviewed exact `localai-contracts==0.2.0a2` wheel is a local handoff
  artifact, not a tracked repository input or configured immutable hosted-CI
  download. Routine CI therefore runs the new dependency-free schema/golden
  coverage; exact-wheel adapter and clean-install coverage remains an explicit
  local/release gate until the contract owner publishes an immutable artifact
  source.
- The deep OneDrive checkout can still exceed the legacy Windows path limit
  when a frontend rebuilds a wheel from the sdist. The staged candidate was
  exported without mutation to `C:\ctxc-audit-candidate-20260801-01`; the same
  release build and clean-install checks passed there.
- An earlier optional-package cache artifact was moved to the system temporary
  directory rather than deleted. All final optional checks disabled bytecode
  writes.

## Honest blockers and claim boundary

- No public external long-horizon suite has successfully scored this compiler;
  do not claim external usefulness, production readiness, or superiority.
- The shared `localai-contracts` handshake still negotiates schema names
  globally rather than binding SourceEvent/ContextBundle versions to
  `context.compile`. LRCC-001 records the owner-controlled change; CtxC does
  not claim that its provider-local sidecar fixes the shared protocol.
- Audit self-hashes do not authenticate an author. Private conversion digests,
  authority decisions, omission/overflow existence and amount, and private
  policy identity remain labeled provider assertions. Stable unsalted audit
  digests are sensitive metadata.
- Exact model checkpoints, public corpus revisions, inference-service resource
  ceilings, and retained raw outputs must be frozen before a claim-bearing
  external comparison can run.

## Next exact actions

1. Inspect the existing external-runner/interchange seams and the retained ACON
   and AMA-Agent screening records so the new adapter reuses current evidence
   envelopes instead of creating another result format.
2. Add a credential-free SWE-bench suite descriptor/importer that pins upstream
   revision, dataset identity, license bytes, task split, and per-instance
   source hashes while excluding gold patches and test outcomes from candidate
   inputs.
3. Emit deterministic runner manifests and metric-ready result records for task
   completion, prompt/model tokens, wall time, correction recovery,
   unresolved-question preservation, and failure rate. Add offline fixtures and
   negative vectors before any credentialed model execution.
4. Select and freeze a second public long-horizon suite using the same evidence
   boundary; do not infer external usefulness from synthetic or local replay.
