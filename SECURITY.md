# Security policy

## Supported development line

This repository is an alpha research implementation. Security fixes target only
the latest development line until a versioned support policy replaces this one.
A passing test suite or benchmark certificate is not a security certification.

## Reporting a vulnerability

Report undisclosed vulnerabilities through the repository's private
[GitHub security advisory form](https://github.com/JamesNguyen42/loss-resistant-context-compiler/security/advisories/new).
Do not include exploit details, private histories, credentials, or personal data
in a public issue. Include the affected revision, platform, smallest safe
reproduction, expected invariant, observed behavior, and whether the issue can
change or omit verified output.

The maintainer will preserve failing reproductions, assess the affected trust
boundary, and coordinate disclosure after a fix and regression are available.
No response-time or remediation-time SLA is currently offered.

## Security-sensitive invariants

Reports are especially useful when they involve:

- provenance, authority, protected retention, temporal state, replay, or claim
  boundary bypasses;
- unsafe parsing, unbounded decoding, archive or lock races, path substitution,
  or artifact-integrity failures;
- connector requests that change semantics between in-process and stdio use;
- evidence tooling that drops, rewrites, retries, or reclassifies a failed run;
- source, corpus, annotation, or report data escaping its declared privacy and
  license boundary.

Every security, parsing, integrity, or concurrency fix must include a regression
that fails before the fix and passes after it. A gate must not be weakened to
make a reproduction pass.

## Separate OpenHands alpha

`ctxc-openhands` is an isolated draft-alpha package, not a supported live
deployment. Ordinary core installation and import remain dependency-free. Live
OpenHands execution is blocked by `hash-pinned-wheelhouse-absent` and by the
absence of a supported immutable final-provider-request/exact-tokenizer hook.
Do not report the offline fake runtime as a live security test.

OpenHands source/role/tool labels do not authenticate authority. Unknown host
events fail closed, unverified assistant/tool/retrieval/file/search/delegated
output remains untrusted, tool calls/results stay atomic, and rehydrated exact
spans remain `untrusted-evidence`. Reports of authority promotion, incomplete
pair visibility, source-history mutation, generation-state bypass, callback
poison bypass, or request-ledger mismatch are security-sensitive.

The qualified SQLite boundary is a local, non-cloud-synchronized,
non-symbolic filesystem. Schema 2 has no repair or automatic migration.
Evidence databases, reports, and backups are exclusively created and never
overwrite an existing path. Network/distributed/cloud-sync storage, host
compromise, and arbitrary code execution remain outside this boundary.

A scenario, soak, or crash-campaign JSON self-hash is not attestation.
`verify-evidence` can pass only when it also reconciles the exact retained
SQLite database; JSON-only scope is deliberately a failure. Evidence reports
do not self-attest process isolation and retain false network-isolation fields.
Preserve external runtime controls, every failed run, and each bound
JSON/SQLite pair. See the
[OpenHands runbook](integrations/openhands/docs/RUNBOOK.md).

## Boundaries that are not vulnerabilities by themselves

The package does not authenticate source roles, prove semantic completeness,
provide signatures or remote attestation, sandbox arbitrary adapter code,
manage encryption or deletion, or remain secure after host/Python-process
compromise. Self-hashes provide integrity relative to a separately trusted
value, not authorship or freshness. See [the threat model](docs/THREAT_MODEL.md)
for the complete current boundary.
