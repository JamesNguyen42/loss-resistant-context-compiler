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
reviewed installed source/resource files. Each bounded validation reader for
an installed file or package-local bytecode cache rejects regular-mode Windows
reparse targets at its pre-open, opened-descriptor, and post-read observations;
a file rejected by these reader-level checks does not advance the tree-digest
state. Any package-local executable bytecode is parsed only by one
empty-environment, memory/deadline/process-tree
bounded no-site batch worker. It compiles the verified source associated with
every cache before unmarshalling any cache, fully consumes each marshal record,
and binds const-stripped format-2 serialized metadata, raw adaptive
instruction/cache images, and a bounded tagged constant graph with per-code
identity topology;
external cache prefixes fail closed. It repeats those checks after import and
validates the returned module and loaded module paths. This proves only observed
installed-tree and provenance-claim agreement. PEP 610 metadata does not
independently authenticate the archive, make the check/import sequence atomic
against a writable install, or undo `.pth`, `sitecustomize`, `meta_path`, or
same-origin module-object effects already inside the process. Release evidence
must independently re-hash the wheel before a direct offline
`--no-index --no-deps --no-compile` install, unset `PYTHONPYCACHEPREFIX`, and
keep the environment non-writable by untrusted actors. The current local result
is not a signature, independent source audit, vulnerability scan, or
publication authorization.
The comparison normalizes only sharing between separate nested code objects,
which differs across valid compiler processes. It requires CPython's private
raw adaptive-code image and rejects cacheful installs on implementations that
do not expose it. Worker resource containment is not a filesystem or network
sandbox against a native marshal vulnerability.
Windows and non-Darwin POSIX use a 256 MiB process/address-space ceiling.
macOS uses an exact 1 TiB virtual-address-space ceiling because hosted arm64
interpreters begin with much larger virtual mappings; bounded input, deadline,
and owned process-tree rules remain the tighter operational limits.

At adapter checkpoint `da664387`, the canonical LF `git archive` provider
wheel was 222,661 bytes with SHA-256
`4366b4da11f85643be8f1a639dce1df495165a0c6af70f297579345e0465572d`.
The Windows checkout-materialized counterpart was 222,718 bytes with SHA-256
`f356ab0280f07ab3ac60a472cc80614bf273754b7fb8092b71a3298514431d9b`.
Only the first is exact-commit/source-archive-byte evidence. These
checkpoint-specific artifacts are not tracked or published, their hashes are
not interchangeable, and later source or metadata edits require new builds.

At exact implementation head
`0f20b8a1c131fe3c0908f7d6738790529f42338c`, tree
`f04dcf9a0dbe58d91d87856b3a9d4fd6de0293ca`, two exact Git archives
produced the same 222,688-byte provider wheel at SHA-256
`bda1b1c50fea351eaf1241e62e8a1dde56de5dc4963d8d04a91e21e5eb7fa993`;
raw `RECORD` SHA-256 is
`485f77359cfc7f8def4c1b47f73e130784ca7beb17baff2ae270792a8726dfc1`.
The exact-a2 clean lane passed 22/22 with `inference_status: not_run`, and the
same clean interpreter ran the direct-`ContextBundle` component harness. The
ignored path-neutral witness is 1,058 bytes with SHA-256
`555495a9621798c962129beab1159aa4d573c31a173554738a8253c0b0ceca68`.
These local, unpublished bytes are not durable retention, signed provenance, or
release authorization, and the commit-epoch provider wheel is distinct from
the hosted fixed-epoch root wheel below. The first Windows build from the long
synchronized workspace path failed while creating a nested schema destination
and produced no wheel; it remains a failed attempt.

The general build and development extras still use lower bounds, and ordinary
development installation resolves compatible releases from the live package
index. There is no general developer-environment lock, cross-version resolved
dependency lock, or offline dependency mirror. The two narrow automatic
release-build exceptions below do not change that boundary.

The OpenHands automatic package workflow has a narrower reviewed exception at
exact implementation head `0f20b8a`: its tracked 666-byte
`requirements-build.lock`, SHA-256
`243f3ab977d82c04968cf4ea6474b7ef79c060d485aa3a3d67a383d1ef6fbbfe`,
binds seven universal build wheels. Each lane acquires only those bytes with
`--require-hashes`, validates their metadata, `RECORD`, and contents, retains
them, and uses them without an index for a dedicated builder and both
clean-install modes. This does not create a general core/dev lock, authenticate
the configured index or publishers, or make temporary Actions retention
durable.

The current OpenHands build-input inspector also treats the archive namespace
as a separate bounded integrity boundary. It validates every ZIP name after
central-directory metadata, member-count, and member-name-length bounds but
before decompressing any member. It independently validates the complete parsed
`RECORD` namespace before testing ZIP membership, hashes, or sizes. Exact ZIP
and `RECORD` duplicate diagnostics remain separate and fail early.

Exact root release-input implementation head
`7915beb15f6a3429c24871779c7cdab280d1ee04`, tree
`5fb61a9f0e9e00016acfd00d04e85e8ef04638f8`, adds an independent root
`requirements-build.lock` with the same 666 bytes and SHA-256. The root
platform-smoke, repeated-build, and LRCBench jobs acquire only those seven wheel
bytes under `--require-hashes`, validate the exact distribution inventory,
install a dedicated builder with `--no-index --no-deps --only-binary=:all:`,
and use it for their release builds. Platform-smoke and LRCBench pass the same
wheelhouse and lock to the clean wheel/sdist smoke. Repeated-build instead
builds two exact-input candidates and requires the strict comparator to accept
both archives. Root CI push run `30429423660` and pull-request run
`30429426031` passed all seven jobs at that head.

By default, the standalone sdist install smoke still bootstraps the
lower-bounded `setuptools>=77` and `wheel>=0.41` requirements from the
configured package index. That mode is an explicitly reported online
clean-environment diagnostic. The automatic root release lanes at `7915beb`
instead supply both `--build-wheelhouse` and `--build-requirements`; this path
rejects linked inputs and invokes pip with `--no-index`,
`--only-binary=:all:`, and `--require-hashes` after acquisition. The artifact
installation itself always uses `--no-index`. This proves exact input-byte
binding and post-acquisition offline build/smoke for those runs, not offline
acquisition, index or publisher authentication, a general development lock, or
durable wheel retention. The automatic runs construct and temporarily retain
this hash-pinned build wheelhouse; the wheel files are not tracked in Git.

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
user/group names, gzip time, and member mtimes deterministically. Nonportable
names include the complete supported Windows device-alias set: `CON`, `PRN`,
`AUX`, `NUL`, `CONIN$`, and `CONOUT$`; COM/LPT 1--9 and superscript 1/2/3
forms; and aliases with ASCII spaces before an extension. These members are
rejected, never normalized. The root and OpenHands backends now additionally
validate namespace prefixes in both physical and parsed layers for tar and
wheel inputs. Physical tar/PAX and gzip expansion limits are enforced before
the standard tar parser receives the validated anonymous stream; the wheel
central-directory namespace is likewise checked before `ZipFile` parsing.
Parsed `TarInfo` and `ZipInfo` inventories repeat the namespace check. A file
cannot also be an ancestor, and raw spellings of an explicit or implicit
directory cannot collide under NFC plus Unicode casefolding. Accepted names
are not normalized, merged, or rewritten.

The namespace algorithm runs only after existing member, path-length, and
aggregate bounds. It sorts one bounded key per exact-deduplicated member,
replacing `/` with the already-forbidden NUL sentinel, then compares adjacent
component sequences. This preserves component order and catches `a` versus
`a/x` even when raw lexical order contains `a-foo` between them. Existing exact
duplicate diagnostics, type and size bounds, and required-member checks remain
separate. The backend then re-inventories the candidate and aborts the
deterministic build if member order, type, mode, size, content, or installed
bytes changed. A failed candidate pathname is retained rather than risking
deletion of a concurrently substituted file. Without the epoch the hook
preserves normal Setuptools behavior.

That first normalization boundary applied only to the core distribution. In
the 2026-07-27 local integration diagnostic, direct-Setuptools wheels were
byte-identical but sdists differed because gzip/member timestamps varied
across 17 generated members. That result remains a failed historical outcome;
no artifact was rewritten or relabeled.

`ctxc-openhands` now has its own PEP 517 module with exact source-byte parity
to the root backend. The module is packaged in the sdist, excluded from the
wheel, and selected through the integration's `backend-path`. With a valid
`SOURCE_DATE_EPOCH`, it applies the same bounded final-archive normalization;
without an epoch, and for non-sdist hooks, it delegates normal Setuptools
behavior. The integration manifest reserves generated `setup.cfg` from the
source file list so an extracted sdist rebuild reaches the same
`SOURCES.txt` fixed point instead of changing one member payload.

At the namespace-prefix checkpoint, the root and OpenHands backend files remain
byte-identical with SHA-256
`e7b0271647fc4ec1206b591d6bf82ea17fba9a9d8f40c6f185f072166e41f1e8`.
The combined CPython 3.12 and CPython 3.14 JUnit reports each contain 177
tests: 176 passed and one expected Windows symlink skip. The 28,236-byte 3.12
report has SHA-256
`b12074a93e4ff2200ccad12c342a2e08c3b65a0208a293418d0f26bad8a93d22`
and records 1.775 seconds; the 28,236-byte 3.14 report has SHA-256
`a8304fb33611a2d6630141d97de058414a87ffc9b778ccea3d86aff816e75d00`
and records 1.993 seconds. Focused Ruff is clean. An independent CPU-only
comparison, seed `0xC7C`, matched 25,000 random bounded backend inventories and
25,000 all-file CI inventories exactly against an O(n²) component oracle. That
50,000-inventory comparison is non-retained review evidence, not a published
artifact.

The first wrapper checkpoint, `cf8b8d3911ef776ca015856f8df19ac47dae0628`,
proved two fresh source copies but did not rebuild its own generated sdist.
Independent review found that recursive build changed only
`src/ctxc_openhands.egg-info/SOURCES.txt`: generated `setup.cfg` became a new
file-list input. The local first/rebuilt pair remains failed at
`b6f4ed61459b5ad9ffb1eb49428ca69be139ce865f7a01f9278ef7df4bffc004`
and
`873c1e5959f924a71fbaafb8d7a1a8133bc7c64f90210e9bc1675045eb37196e`.
Commit `2f692484272aa36bf267703cad2bb4d6926676ff` adds the manifest fixed
point and the exact extracted-sdist regression; it does not relabel the first
checkpoint.

At root implementation head `7915beb`, Core CI acquires the seven exact wheels
authorized by the root `requirements-build.lock`, validates their distribution
inventory, and installs a dedicated no-index builder. It fixes
`SOURCE_DATE_EPOCH` and `PYTHONHASHSEED`.
It builds wheel and sdist from two clean checkouts in one job with pip 25.0.1,
Setuptools 83.0.0, and wheel 0.47.0. It records the exact input wheels, Python,
and installed tool inventory, and requires the separate comparator to report
both archives byte-identical.
OpenHands ordinary package lanes independently build two fresh source copies
and rebuild the first generated sdist from its extracted source, requiring all
three final sdist byte strings to match. At exact packaging head
`2f692484272aa36bf267703cad2bb4d6926676ff`, the corresponding local
Git-archive qualification also matched all three 232,006-byte artifacts at
SHA-256
`9a8f5035d8cbe904dc03142b3be54e3e15fae699153fb7630b4948b7415ac6be`.
At `2f692484`, these checks closed only the same-revision, same-platform,
same-job-toolchain repeated-build defect under an explicit epoch. That result
did not prove equality across Python, Setuptools, operating-system, or
compression-library versions, and it did not establish offline inputs,
hash-pinned inputs, or independent reproduction.

For exact packaging head `2f692484`, all six automatic package jobs passed in
push run `30341548763` and all six passed in pull-request run `30341552866`,
covering Linux, Windows, and macOS on Python 3.12/3.13. Each lane proved its
own three-build fixed point.

Exact package-gate implementation head
`f9ba3de6f0ab2ac7e861bd7da907e6949df1339e` then added a separate aggregate
that compared the root wheel, integration wheel, and integration sdist from all
six lanes. OpenHands push run `30355357305` and pull-request run `30355359105`
each passed the six package jobs plus the aggregate; retained evidence was
skipped/default-off. The push report is 16,681 bytes with raw SHA-256
`e9203177989fab536e70febcf5316ba6ea21d2a39ea2d3f8dc2c46b146a6f743` and
self-hash
`9931d25167831dea6698e0794a93e1cc46a1fc23ed29126c94708aefe7efb35c`.
It reports byte-identical 221,771-byte root wheels
(`ffc60ecf166cc28563c2a5bc6597e1c4cb0a4c2be6c965d31bafbe3877d1c17c`),
136,343-byte integration wheels
(`ed846517d05b9a734754a9d893de84f23d807f9236c626f1791f9f6d215fa3a9`),
and 240,187-byte integration sdists
(`b8ee2f1f13c06cfe7de1343f9d765eb25aaf6bc1f7d03da7c6443b46eec0594a`)
across Linux, macOS, and Windows on Python 3.12/3.13.

Documentation checkpoint `f7b417a767abdd833dfeda9f4aa6518fe94b5cdd`
also passed six package jobs plus the aggregate in OpenHands push run
`30357985272` and pull-request run `30357993368`; retained evidence remained
skipped/default-off. Its six lanes agreed on the 222,688-byte root wheel
(`ec46f710169a95c21c54a28b941d2f5205104113c3e594fe6945ef68f633642f`),
136,967-byte integration wheel
(`16976fa84cebb2b35f1cc15db89a41f016a3a8385498fd2335adbc28c85aacf0`),
and 241,931-byte integration sdist
(`a061cfab2643404547c4d3375adaf0bd2e62d8c01cccd2ac14ac134ea8059629`).

Exact package-input implementation head
`0f20b8a1c131fe3c0908f7d6738790529f42338c` then passed OpenHands push run
`30366252700` and pull-request run `30366258656`, each with six package jobs
plus the aggregate; retained evidence stayed skipped/default-off. The lanes
agreed on the 222,688-byte root wheel
(`ec46f710169a95c21c54a28b941d2f5205104113c3e594fe6945ef68f633642f`),
136,967-byte integration wheel
(`16976fa84cebb2b35f1cc15db89a41f016a3a8385498fd2335adbc28c85aacf0`),
255,770-byte integration sdist
(`bf51799df63019c3138368a2d6f9e8bc3ddd398163838669340764be36130957`),
the tracked lock, and all seven build-input wheels. The 44,203-byte push report
raw SHA-256/self-hash are
`46e47ee4b6f3d1b7ce0fdcc5e41e5f812f2ee0d17005d0c77acd986198213acd`
and `62cd0a5a4ee351dc9fc2c3bd892a79db5394058e96a611c2a47946e0e42ec876`;
the same-size pull-request report raw SHA-256/self-hash are
`4fd96cb025bd557644e670a79c2ae6eeabb244099886d649ab755f7ffb6e8bda`
and `aba29437bf2ee4ba7d5876eea978bc1bf74cba321ebe047e5ff92acf80e69626`.

The earlier aggregate reports established equality only for three outputs and
did not bind dependency hashes. The `0f20b8a` reports additionally bind the
tracked lock and seven exact input wheels across their six recorded hosted
lanes. They still do not attest the hosted platform image, publisher identity,
or complete build environment. Temporary artifacts must be retained durably
and independently checked before any cross-toolchain, independently
reproduced, or broader reproducible-build claim.

The separate root release-input implementation head
`7915beb15f6a3429c24871779c7cdab280d1ee04` passed root CI push run
`30429423660` and pull-request run `30429426031`, each with all seven jobs.
Those jobs bind the same seven input wheels to root platform smoke, repeated
release builds, and LRCBench package builds; their clean sdist smoke reports
`hash-pinned-offline-wheelhouse`. OpenHands push `30429423659` and pull-request
`30429426030`, CodeQL `30429426038`, and dependency review `30429426044` also
passed at that exact head. The retained-evidence job remained
manual/default-off, no model or runtime inference was run, and the successful
automatic lanes do not authorize release or publication.

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
