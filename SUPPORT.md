# Support matrix

This project is an alpha research and engineering artifact. “Supported” below
means exercised by the stated checks; it does not imply a production SLA.

## Package identities

| Surface | Stable identity |
| --- | --- |
| Python distribution | `loss-resistant-context-compiler` |
| Import package | `context_compiler` |
| CLI command | `ctxc` |
| Optional canonical connector | `ctxc-localai-contracts` |
| Installed schemas | `share/loss-resistant-context-compiler/schemas` |
| Artifact schema | `1.0` reader and writer |

The old development distribution name `lossless-context-compiler` is not an
alias. Imports and the `ctxc` command are unchanged by the metadata rename.

## Runtime and platform support

| Environment | Status | Evidence |
| --- | --- | --- |
| CPython 3.11 | Supported | Complete Ubuntu CI suite, frozen replay, LRCBench, and performance gate |
| CPython 3.12 | Supported | Complete Ubuntu CI suite and current Windows local validation |
| CPython 3.13 | Supported | Complete Ubuntu CI suite; Windows and macOS release/filesystem smoke |
| CPython 3.14 | Supported | Complete Ubuntu CI suite and complete local Windows core-suite validation |
| Ubuntu local filesystem | Supported | Complete CI tests, release builds and clean installs, benchmark replay, LRCBench, and performance gate |
| Windows 11 local NTFS/OneDrive workspace | Provisional | Hosted Python 3.13 filesystem and clean wheel/sdist smoke plus complete local Python 3.12 and core Python 3.14 validation |
| macOS local filesystem | Provisional | Hosted Python 3.13 filesystem and clean wheel/sdist smoke; not a complete test-suite target |
| Network/distributed filesystems | Unverified | Advisory locks, hard links, directory identity, rename, and durability semantics vary |
| PyPy or other Python implementations | Unverified | No current test matrix |

Core runtime code has no third-party dependency. Development checks use the
optional `dev` dependencies. The exact LM Studio Qwen adapter is optional and
supports only the documented local Qwen Q4 identity for the retained
diagnostic; deterministic compilation does not require a model.

The `unified` extra supports exactly `localai-contracts==0.2.0a2`, protocol and
schema version `1.0.0`, through the separately imported canonical adapter.
Other versions are refused. Provider-only installation and import remain
supported; invoking the optional entry point without its wheel exits closed.
The optional adapter also requires one unambiguous installed distribution, an
unset `sys.pycache_prefix`, exact built-in module/spec/source-loader state bound
to its recorded package, the reviewed source/resource tree digest and sizes, no
linked/reparse or unexpected importable entries, and package-local bytecode
that matches compilation of verified source. Loader instance overrides and
non-string registry/namespace keys fail closed without invoking their hooks.
Failures use one path-free validation error. This is a strict clean-install
support contract, not support for writable or hook-modified site-packages.
Independently hash the wheel archive and unset `PYTHONPYCACHEPREFIX` before
installation; prior startup/finder/preload execution and same-origin
in-process module forgery are not reversible by the adapter.

## Format support

| Format | Current support |
| --- | --- |
| Compiled artifacts | Read/write `1.0`; unknown versions fail closed |
| Source archive entries | Current chained `ctxc-source-archive-entry-0.1`; legacy raw JSONL reads and upgrades only on explicit append |
| Redaction reports | `ctxc-redaction-report-0.1` |
| Artifact inspection | `ctxc-artifact-inspection-0.1` |
| Artifact diff | `ctxc-artifact-diff-0.1` |
| Detached trust manifests | `ctxc-trust-manifest-0.1`; verification reports use `ctxc-trust-verification-0.1` |
| CLI diagnostics/events | Generic errors use `ctxc-diagnostic-0.1`; materialization overflow details use `ctxc-diagnostic-0.2` with nested `loss-resistant-materialization-refusal-diagnostic-v1`; completion events use `ctxc-event-0.1` |
| Connector wire contracts | Seventeen Draft 2020-12 schemas for request/response, source event, bundle, checkpoint, and six payload/result pairs; runtime semantic verification remains authoritative |
| Canonical optional connector | `localai-contracts` protocol, SourceEvent, ContextBundle, request/response, error, and manifest `1.0.0`; only `context.compile` is executed |
| Provider-local conversion audit | `ctxc-localai-conversion-audit-0.1`; diagnostic sidecar only, never a shared connector payload |
| LRCBench reports | Current `lrcbench-0.2` plus the explicit retained local-only `0.1` replay path |
| Natural-history evidence | Six strict source-distribution schemas and seven self-hashed contract fixtures; no collected natural cohort exists |
| External compatibility evidence | Result-blind ACON and AMA-Agent screening records plus one retained failed ACON diagnostic; no scoreable candidate exists |

No format is silently migrated. See
[docs/SCHEMA_COMPATIBILITY.md](docs/SCHEMA_COMPATIBILITY.md) and
[docs/RELEASE_POLICY.md](docs/RELEASE_POLICY.md).
The seven schemas that predate the distribution rename retain their historical
`lossless-context-compiler` `$id` URI values for compatibility. Nineteen newer
schemas use the current `loss-resistant-context-compiler` namespace. All 26
files install under the current distribution's schema directory. The separate
natural-history schemas and connector transcript fixtures are source-
distribution conformance assets rather than installed runtime schemas.

## Installation support

Source-tree editable installs and locally built wheels are tested. CI builds
one wheel and one source distribution, inspects their inventories, installs
each in a separate clean environment, and runs metadata, schema, CLI, compile,
and detached-trust round trips on Ubuntu, Windows, and macOS. Windows and macOS
remain provisional because their hosted jobs are targeted smoke coverage, not
the complete suite.

The optional boundary additionally has two offline clean-wheel lanes: provider
only, and provider plus the exact contracts wheel. The latter launches
`[clean-environment sys.executable, "-m",
"context_compiler.localai_contracts_connector"]` (literal argv tail
`-m context_compiler.localai_contracts_connector`) for a real subprocess
handshake and canonical `context.compile` NDJSON round trip, then runs the
22-case non-inference conformance gate. Both installs use
`--no-index --no-deps --no-compile`. The module child uses the harness
interpreter exactly, without an argv-level `-B`, receives
`PYTHONDONTWRITEBYTECODE=1`, and is followed by a provider/contracts package
no-`.pyc` check.

No package index release is currently claimed. Before a public release, the
repository must add signed or otherwise externally attestable source/wheel
artifacts and explicit test-index approval. Production PyPI publication is not
authorized by the current checks.

If an old editable install still has `lossless-context-compiler` metadata,
remove that distribution and reinstall from the current source or wheel. Do
not assume package managers will treat the old and new distribution names as
an upgrade relationship.

## Security and maintenance

During alpha development, fixes target the latest branch only. Hashes in this
repository detect inconsistency but are not signatures. Detached trust
manifests can bind an artifact and its exact source set only when the expected
manifest digest is protected separately. Filesystem administrators, upstream
role authentication, key management, encryption, retention, and the external
anchor channel remain host responsibilities as detailed in
[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).
