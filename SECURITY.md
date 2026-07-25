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

## Boundaries that are not vulnerabilities by themselves

The package does not authenticate source roles, prove semantic completeness,
provide signatures or remote attestation, sandbox arbitrary adapter code,
manage encryption or deletion, or remain secure after host/Python-process
compromise. Self-hashes provide integrity relative to a separately trusted
value, not authorship or freshness. See [the threat model](docs/THREAT_MODEL.md)
for the complete current boundary.
