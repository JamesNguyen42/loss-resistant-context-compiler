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
- A pinned CodeQL Python workflow runs `security-extended` queries on pushes,
  pull requests, and a weekly schedule. Repository contents remain read-only;
  only the CodeQL job receives `security-events: write` to upload its findings.
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

The build and development extras currently use lower bounds and CI resolves
compatible releases from the live package index. No cross-version, hash-pinned
dev/build lock or offline wheelhouse is retained. A credible replacement needs
resolver output for CPython 3.11-3.13 and every CI platform, hashes for each
allowed distribution, a documented update cadence, and an offline installation
check; a single host-generated lock would overstate portability.

By default, the standalone sdist install smoke bootstraps the lower-bounded
`setuptools>=77` and `wheel>=0.41` requirements from the configured package
index. That path is an explicitly reported online clean-environment diagnostic,
not proof of an offline or reproducible source install. An alternate mode
requires both `--build-wheelhouse` and `--build-requirements`, rejects linked
inputs, and invokes pip with `--no-index`, `--only-binary=:all:`, and
`--require-hashes`; the artifact installation itself always uses `--no-index`.
The repository does not yet retain a reviewed requirements file and matching
hash-pinned build wheelhouse for every supported platform. That offline smoke
therefore remains a red gate rather than an inferred pass.

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

`python -m scripts.release_artifact_manifest` creates bounded checksum evidence
for exactly the current-version `py3-none-any` wheel and source distribution in
a lexical real directory. It rejects symlink or junction distribution roots,
noncanonical archive names, extra archives, empty or non-regular files, and any
archive change observed across two complete validation passes. `create` writes
a no-overwrite `SHA256SUMS` first and a self-hashed manifest last, then
immediately verifies both archives and both evidence files against the freshly
computed manifest digest. The manifest is the completion marker: a checksum
file left by a failed second write is a retained failed attempt, not successful
evidence. CI retains the completed pair with the built archives.

The manifest's `revision` is a caller-supplied assertion. The tool validates and
binds the lowercase 40-character value, but it does not establish that the
working tree or archive bytes came from that commit and does not prove
publisher identity. A later `verify --expected-manifest-sha256 <trusted-digest>`
can bind the retained files to an independently stored digest. The two-pass
checks detect mutation during validation; they still assume the distribution
workspace remains trusted after the final pass. An actor that retains write
access could substitute files before upload, so signatures or attestations and
upload-side digest checks remain required.

`python -m scripts.release_reproducibility` compares exactly one wheel and one
source distribution from each of two build directories. It performs bounded,
link-free archive inspection, exact streaming byte comparison, and writes a
no-overwrite self-hashed report. A failure identifies the first member-content,
tar/ZIP metadata, or container-encoding difference; the report remains failed
and is not converted into a pass. The verifier deliberately does not normalize
or rewrite either candidate artifact.

The repeated-build gate is currently red. With fixed `SOURCE_DATE_EPOCH` and
`PYTHONHASHSEED=0`, the diagnostic wheel hashes matched byte for byte and the
sdist member contents matched, but Setuptools 83.0.0 did not apply
`SOURCE_DATE_EPOCH` to sdist tar member mtimes. The source distribution is not
yet byte-for-byte reproducible; no release should represent it as reproducible
until the backend or a separately reviewed deterministic builder fixes that
metadata and the two-build verifier passes independently.

The report establishes output equality only. It does not discover or attest
the source revision, build frontend/backend versions, dependency hashes,
platform image, or build environment; those inputs must be retained and
independently checked before any reproducible-build claim.

This is checksum and substitution-detection groundwork, not a signature,
authorship proof, reproducible-build proof, SBOM, vulnerability scan, or
provenance attestation. This repository does not yet publish signed artifacts,
use a production release environment, or claim SLSA conformance.

The retained ACON diagnostic demonstrates the fail-closed boundary rather than
satisfying it: the source tree, immutable upstream revision, license bytes,
runtime, value-redacted environment, command, model contract, and resource
limits are bound, but dependency-lock, enforced network-isolation, and
inference-service evidence are absent. Its manifest therefore remains a failed,
non-scoreable run. Optional-adapter vulnerability/license scanning and exact
resolved dependency locks remain release work.
