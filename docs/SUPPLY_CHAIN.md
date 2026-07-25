# Supply-chain groundwork

The core runtime intentionally has no third-party dependency. Build, test, and
optional external-adapter dependencies remain part of the release trust
boundary and must be reviewable rather than implicitly inherited from a host.

## Repository controls

- GitHub Actions are referenced by immutable full commit SHA with the release
  tag retained in a comment for reviewability.
- Dependabot is configured for both GitHub Actions and Python development/build
  manifests.
- Pull requests run GitHub dependency review and fail when a newly introduced
  dependency has a known high-or-critical vulnerability.
- Workflows use read-only repository contents permissions unless a future,
  separately reviewed publishing job requires narrower additional authority.
- CI does not publish packages. Release builds and smoke installs are evidence,
  not deployment authorization.

These controls follow GitHub's guidance that a full commit SHA is the immutable
way to reference an action and that dependency review can block vulnerable
changes. They reduce drift but do not prove that an action, runner image, build
backend, or package index is uncompromised.

## Python and adapter dependencies

`pyproject.toml` is the authoritative core/build/development manifest. Runtime
`dependencies` must remain empty. A new core dependency requires explicit
architecture, security, license, and release review. Build and development
requirements must stay bounded and visible.

Every claim-bearing external adapter has a stricter boundary: retain and hash
its dependency lock, source tree, entrypoint, runtime executable, portable
command contract, environment-name digest, resource limits, and externally
established network-isolation evidence. A revision string or local environment
name alone is insufficient.

## Artifact publication boundary

Before any public package release:

1. build wheel and sdist from one clean reviewed commit;
2. run the cross-platform clean-install and frozen-evidence gates;
3. inspect the complete archive inventory and generate SHA-256 checksums;
4. create externally verifiable signatures or provenance attestations for both
   archives;
5. retain the checksums, attestations, workflow/revision identity, and failed
   attempts with the release record;
6. require explicit approval before any test-index or production-index upload.

This repository currently implements build, inventory, clean-install,
dependency-review, and checksum groundwork only. It does not yet publish signed
artifacts, use a production release environment, or claim SLSA conformance.
