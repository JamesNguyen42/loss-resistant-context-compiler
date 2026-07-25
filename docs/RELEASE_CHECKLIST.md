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
- [ ] Run Ruff and `compileall` across `src`, `tests`, `benchmarks`, and
  `scripts`.
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
- [ ] Inspect both archives for the expected package, CLI, documentation,
  connector conformance assets, six natural-history schemas, seven contract
  fixtures, and all 25 installed JSON Schemas.
- [ ] Install the wheel and sdist into separate clean environments and run
  import, metadata, schema parsing, `ctxc --help`, compile, trust-create, and
  trust-verify smoke tests.
- [ ] Confirm the installed core has no third-party runtime requirement.
- [ ] Generate SHA-256 checksums. Public artifacts additionally require the
  external signatures/attestations specified by the release policy; their
  absence remains a red gate.

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
