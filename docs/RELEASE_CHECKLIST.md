# Release candidate checklist

This checklist prepares a reviewable candidate. It does not authorize a merge,
a production PyPI upload, or a broader product/evidence claim.

## Freeze the candidate

- [ ] Start from a clean commit and record the full revision, branch, UTC time,
  Python versions, platform images, and exact commands.
- [ ] Confirm every commit and package author field uses only the configured
  `JamesNguyen42` identity, with no co-author or tool attribution.
- [ ] Confirm `pyproject.toml` and `context_compiler.__version__` match.
- [ ] Review the complete public Python exports, CLI help, schemas, and stored
  format versions; document every intentional change and migration boundary.
- [ ] Confirm no frozen report, corpus, protocol, gold label, threshold, or
  decision was changed after observing its result.

## Code and platform gates

- [ ] Run the complete test suite on CPython 3.11, 3.12, and 3.13.
- [ ] Run Ruff and `compileall` across `src`, `tests`, `benchmarks`, `scripts`,
  `conformance`, and `_ctxc_build_backend.py`.
- [ ] Run the cross-platform lock/path/package smoke coverage on Ubuntu,
  Windows, and macOS; record skips and filesystem limitations explicitly.
- [ ] Run every new conformance and natural-history negative vector; verify
  malformed, oversized, incomplete, raced, and duplicate inputs fail closed.
- [ ] Keep every failing external adapter or unavailable platform run red; do
  not turn infrastructure failure into a pass.

## Evidence gates

- [ ] Replay every committed frozen report without model or paid-service calls.
- [ ] Verify the external protocol document and machine manifest. A draft or
  blocked protocol must remain non-claim-ready.
- [ ] Verify result-blind compatibility records, immutable source/license pins,
  and every retained external failure. Confirm no failed run produced or was
  converted into a scoreable candidate.
- [ ] Run LRCBench to a new temporary output path and retain the actual issued
  or failed certificate unchanged.
- [ ] Run the fixed performance gate to a new temporary output path.
- [ ] Verify natural-history corpus, annotation, adjudication, grouped split,
  gold-free export, and report contracts independently. Do not claim a natural
  cohort exists until reviewed licensed/consented data is actually frozen.

## Distribution gates

- [ ] Build exactly one wheel and one source distribution from the candidate.
- [ ] Inspect the wheel for the expected package, CLI entry point, and all 25
  installed core/connector JSON Schemas.
- [ ] Inspect the source distribution separately for release documentation,
  connector conformance assets, six natural-history schemas, seven contract
  fixtures, and the source copies of the 25 installed schemas.
- [ ] Install the wheel and sdist into separate clean environments and run
  import, metadata, schema parsing, `ctxc --help`, compile, trust-create, and
  trust-verify smoke tests. Require lexical real distribution paths and
  regular single-link archives; retain any link or replacement rejection.
- [ ] Confirm the installed core has no third-party runtime requirement.
- [ ] If the `unified` extra is in the candidate, verify the reviewed
  `localai-contracts==0.2.0a2` wheel SHA-256, source commit, exact 35-row
  `RECORD`, packaged MIT license bytes, zero-dependency metadata, and
  protocol/schema `1.0.0`.
- [ ] Require all 35 immutable wheel `RECORD` rows exactly once: 34 hashed rows
  with reviewed path, URL-safe SHA-256, size, and installed bytes, plus the
  `RECORD` self-row with canonical empty hash/size fields. Require exact
  `INSTALLER`, exact-wheel PEP 610 `direct_url.json`, and one
  platform-canonical launcher. Permit an optional exact empty `REQUESTED`
  marker and reject every other installer-generated row.
- [ ] Before the adapter initiates optional-package import or exposes a
  preloaded root, require one unambiguous distribution, an unset
  `sys.pycache_prefix`, exact built-in module/spec/source-loader state bound to
  the recorded package/initializer, the exact bounded link-free installed
  source/resource tree digest and sizes, no unexpected importable entries, and
  package-local bytecode matching fresh compilation of verified source. Repeat
  the gate after import. Run the shadow, ambiguous-distribution, exact-size
  source/resource mutation, extra-subpackage, external/forged-bytecode,
  loader/module hook, wrong-origin preload, and post-import mutation
  regressions on every supported CPython/platform lane. Independently hash the
  wheel archive, unset `PYTHONPYCACHEPREFIX`, and use non-writable clean
  environments.
- [ ] Run the canonical adapter's 22-case Phase 0 gate and require exactly
  `passed_count: 22` and `inference_status: not_run`; do not substitute model
  execution or a synthetic response.
- [ ] Run `scripts/validate_localai_contracts_install.py` against the candidate
  provider wheel and exact contracts wheel. Require three distinct
  environments: provider-only; transitive `provider[unified]` without direct
  PEP 610 archive metadata, which must fail closed; and direct
  provider-plus-contracts. The supported direct lane must launch
  `[clean-environment sys.executable, "-m",
  "context_compiler.localai_contracts_connector"]` (literal argv tail
  `-m context_compiler.localai_contracts_connector`) for a real
  handshake/compile NDJSON round trip and repeat the 22-case non-inference gate.
  All lanes use `--no-index --no-compile`. Provider-only and direct lanes also
  use `--no-deps`; the transitive lane resolves only from its local
  `--find-links` directory. Keep argv-level `-B` out of the shared connector
  command, set `PYTHONDONTWRITEBYTECODE=1` in its environment, and require the
  post-round-trip provider/contracts package no-`.pyc` verifier.
- [ ] Provide a reviewed hash-pinned offline wheelhouse and exact requirements
  file for sdist build tools. Run `scripts/release_install_smoke.py` with both
  `--build-wheelhouse <directory>` and
  `--build-requirements <requirements.txt>`. Require the reported
  `hash-pinned-offline-wheelhouse` bootstrap; that path enforces no index,
  binary-only build tools, and pip hash checking. This gate is red because the
  repository does not yet retain the reviewed cross-platform wheelhouse and
  requirements inputs. The default `online-lower-bounds` result remains only an
  online diagnostic.
- [ ] Independently verify that the clean checkout, candidate commit, and
  archive inputs match the revision supplied to the evidence tool; the tool
  binds that value but does not discover or attest source provenance.
- [ ] From an empty evidence-output path, run the module-form
  `python -m scripts.release_artifact_manifest create` command with
  `--dist-dir dist`, the exact `--revision`,
  `--manifest-out dist/release-artifacts.json`, and
  `--checksums-out dist/SHA256SUMS`. Require exit zero and retain the printed
  manifest SHA-256 outside the artifact bundle. `create` self-verifies the
  completed pair. A nonzero exit, missing manifest, or lone checksum file is a
  retained failed attempt, not release evidence.
- [ ] Re-run `python -m scripts.release_artifact_manifest verify` with the exact
  archives, manifest, checksum file, and
  `--expected-manifest-sha256 <trusted-digest>` immediately before upload.
  Keep the distribution workspace write-restricted between verification and
  upload, and compare upload-side digests where the index exposes them.
- [ ] Rebuild twice in clean environments with fixed timestamp and hash-seed
  inputs. Build the sdist through `_ctxc_build_backend` with an explicit
  `SOURCE_DATE_EPOCH`; a direct `setuptools.build_meta.build_sdist` call bypasses
  the normalization boundary. The repository command is
  `python -m _ctxc_build_backend --sdist-dir dist --source-date-epoch
  1700000000`. Run
  `python -m scripts.release_reproducibility --first-dist
  <first-dist> --second-dist <second-dist> --json-out
  <new-reproducibility-report.json>` and retain the report even when the command
  exits 1. Require status `passed` and byte-identical wheel and sdist results.
  CI exercises this from two clean checkouts in one job with explicit
  pip/Setuptools/wheel versions and retains the Python/tool inventory. Do not
  infer cross-platform, cross-toolchain, offline, hash-pinned-input, or
  independent reproducibility from that result.
- [ ] Public artifacts additionally require the external
  signatures/attestations specified by the release policy; their absence
  remains a red gate. The checksum manifest is not an SBOM or signature.

## Documentation and handoff

- [ ] Reconcile README, TODO, changelog, support matrix, architecture,
  benchmarking, threat model, release policy, security policy, and handoff.
- [ ] State every unsupported platform, privacy/licensing limitation, external
  adapter blocker, failed run, and unverified claim.
- [ ] Verify the draft PR describes root causes, user impact, public API and
  stored-format changes, evidence boundaries, validations, and remaining red
  gates.
- [ ] Obtain explicit approval before merge, production publication, protocol
  freeze, or claim widening.
