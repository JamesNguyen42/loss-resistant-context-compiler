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

The `unified` extra is pinned to `localai-contracts==0.2.0a2`. The reviewed
`py3-none-any` wheel has SHA-256
`36a02dbc4267402949dddda1da180d800590cc579e0c1ecb022fc96f6a7c29ae`.
The immutable handoff records source commit
`3858190e8b458847da94e9ed24be83f4928b7d1a`, 35 `RECORD` rows with raw
SHA-256 `a20ae81b7cc5dd9e80fc2757d5fea6331f2c232818049026caecf63d48d14076`,
29 package members equal to that commit's Git blobs, packaged MIT license
bytes, `Requires-Python >=3.11`, and no `Requires-Dist` entries. The installed
package tree is independently bound to framed SHA-256
`296f49a2d7b48158d2d3a33e36b77d5b5c495362cbe3aceaaf8975fb256e538c`.
The workspace handoff copy is ignored and must not be committed or published.
The optional `unified` extra does not by itself make the provider
`contract_ready`: a transitive `--find-links` resolution can install identical
package bytes without the PEP 610 direct-archive metadata needed to bind that
installation to the reviewed wheel file. That lane is intentionally
fail-closed. The supported conformance lane directly installs both the provider
wheel and the independently hash-checked reviewed contracts wheel.

The optional adapter does not rely on runtime version strings alone. Before it
initiates package import or exposes a preloaded root, it requires one
exact-version distribution, an unset `sys.pycache_prefix`, and exact built-in
module/spec/source-loader state bound to the distribution's recorded package
and initializer. It rejects loader instance overrides, non-string
registry/namespace keys, links/reparse points, and unexpected tree entries. It
requires all 35 immutable wheel `RECORD` rows exactly once: 34 hashed rows with
reviewed path, URL-safe SHA-256, size, and installed bytes, plus the `RECORD`
self-row with canonical empty hash/size fields. Required installer-generated
rows are the exact pip marker, exact-wheel PEP 610 direct-archive metadata, and
one platform-canonical launcher bound to the reviewed entry point. An exact
empty `REQUESTED` marker is optional; every other generated row is rejected.
Windows additionally
binds the native prefix to the architecture-matched reviewed distlib 0.3.9
console stub without consulting an ambient pip installation at runtime. An
installer that emits another native stub is unsupported and fails closed. The
adapter then verifies exact sizes plus a canonically framed SHA-256 over the
reviewed installed source/resource files. Any package-local executable
bytecode must equal fresh compilation of verified source; external cache
prefixes fail closed. It repeats those checks after import and validates the
returned module and loaded module paths. This proves only observed
installed-tree and provenance-claim agreement. PEP 610 metadata does not
independently authenticate the archive, make the check/import sequence atomic
against a writable install, or undo `.pth`, `sitecustomize`, `meta_path`, or
same-origin module-object effects already inside the process. Release evidence
must independently re-hash the wheel before a direct offline
`--no-index --no-deps --no-compile` install, unset `PYTHONPYCACHEPREFIX`, and
keep the environment non-writable by untrusted actors. The current local result
is not a signature, independent source audit, vulnerability scan, or
publication authorization.

At adapter checkpoint `da664387`, the canonical LF `git archive` provider
wheel was 222,661 bytes with SHA-256
`4366b4da11f85643be8f1a639dce1df495165a0c6af70f297579345e0465572d`.
The Windows checkout-materialized counterpart was 222,718 bytes with SHA-256
`f356ab0280f07ab3ac60a472cc80614bf273754b7fb8092b71a3298514431d9b`.
Only the first is exact-commit/source-archive-byte evidence. These
checkpoint-specific artifacts are not tracked or published, their hashes are
not interchangeable, and later source or metadata edits require new builds.

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

Setuptools 83.0.0 does not apply `SOURCE_DATE_EPOCH` to sdist tar members or the
gzip header. The project therefore uses `_ctxc_build_backend`, a thin PEP 517
wrapper that delegates every other hook to `setuptools.build_meta`. When and
only when a valid `SOURCE_DATE_EPOCH` is supplied, it validates the backend
archive under explicit member and byte limits, rejects links, special files,
duplicate, nonportable, or unsafe names, unexpected PAX fields, multiple gzip
members, trailing data, and input replacement, then rewrites uid/gid,
user/group names, gzip time, and member mtimes deterministically. Physical
tar/PAX and gzip expansion limits are enforced before the standard tar parser
receives the validated anonymous stream. It re-inventories the candidate and
aborts the deterministic sdist build if member order, type, mode, size,
content, or installed bytes changed. A failed candidate pathname is retained
rather than risking deletion of a concurrently substituted file. Without the
epoch the hook preserves normal Setuptools behavior.

That normalization applies only to the core distribution.
`integrations/openhands` uses `setuptools.build_meta` directly. In the
2026-07-27 local candidate diagnostic, repeated integration wheels were
byte-identical but repeated sdists differed because gzip/member timestamps
varied across 17 generated members. That result remains a failed
integration-sdist reproducibility gate; no artifact was rewritten or relabeled
to manufacture equality.

CI fixes `SOURCE_DATE_EPOCH` and `PYTHONHASHSEED`, builds wheel and sdist from
two clean checkouts in one job with pip 25.0.1, Setuptools 83.0.0, and wheel
0.47.0, records Python and the installed tool inventory, and requires the
separate exact comparator to report both archives byte-identical. That closes
the same-revision, same-job-toolchain repeated-build defect. It does not prove
equality across Python, Setuptools, operating-system, or compression-library
versions, and it does not establish offline or hash-pinned inputs or independent
reproduction.

The report establishes output equality only. The adjacent CI evidence records
the selected Python and installed tool versions, but does not attest dependency
hashes, the hosted platform image, or the complete build environment. Those
inputs must be retained and independently checked before any broader
reproducible-build claim.

Portable stdlib checks cannot atomically prevent a hostile same-user process
from replacing a pathname after the final verification syscall. The wrapper
narrows and detects tested replacement windows, binds the installed inode,
inventory, digest, and canonical metadata, and fails closed on a detected
substitution. The release workspace must still be write-restricted from build
through upload.

This is checksum, substitution-detection, and bounded same-toolchain
repeatability groundwork, not a signature, authorship proof, universal
reproducible-build proof, SBOM, vulnerability scan, or provenance attestation.
This repository does not yet publish signed artifacts, use a production release
environment, or claim SLSA conformance.

The retained ACON diagnostic demonstrates the fail-closed boundary rather than
satisfying it: the source tree, immutable upstream revision, license bytes,
runtime, value-redacted environment, command, model contract, and resource
limits are bound, but dependency-lock, enforced network-isolation, and
inference-service evidence are absent. Its manifest therefore remains a failed,
non-scoreable run. Optional-adapter vulnerability/license scanning and exact
resolved dependency locks remain release work.
