# Support matrix

This project is an alpha research and engineering artifact. “Supported” below
means exercised by the stated checks; it does not imply a production SLA.

## Package identities

| Surface | Stable identity |
| --- | --- |
| Python distribution | `loss-resistant-context-compiler` |
| Import package | `context_compiler` |
| CLI command | `ctxc` |
| Installed schemas | `share/loss-resistant-context-compiler/schemas` |
| Artifact schema | `1.0` reader and writer |

The old development distribution name `lossless-context-compiler` is not an
alias. Imports and the `ctxc` command are unchanged by the metadata rename.

## Runtime and platform support

| Environment | Status | Evidence |
| --- | --- | --- |
| CPython 3.11 | Supported | Complete Ubuntu CI suite |
| CPython 3.12 | Supported | Complete Ubuntu CI suite and current Windows local validation |
| CPython 3.13 | Supported | Complete Ubuntu CI suite |
| Ubuntu local filesystem | Supported | CI tests, wheel build, benchmark replay, performance gate |
| Windows 11 local NTFS/OneDrive workspace | Provisional | Complete local suite, offline evidence replay, source/wheel builds, clean no-index wheel install; not a CI matrix target |
| macOS | Unverified | No current CI or retained filesystem-semantics report |
| Network/distributed filesystems | Unverified | Advisory locks, hard links, directory identity, rename, and durability semantics vary |
| PyPy or other Python implementations | Unverified | No current test matrix |

Core runtime code has no third-party dependency. Development checks use the
optional `dev` dependencies. The exact LM Studio Qwen adapter is optional and
supports only the documented local Qwen Q4 identity for the retained
diagnostic; deterministic compilation does not require a model.

## Format support

| Format | Current support |
| --- | --- |
| Compiled artifacts | Read/write `1.0`; unknown versions fail closed |
| Source archive entries | Current chained `ctxc-source-archive-entry-0.1`; legacy raw JSONL reads and upgrades only on explicit append |
| Redaction reports | `ctxc-redaction-report-0.1` |
| Artifact inspection | `ctxc-artifact-inspection-0.1` |
| Artifact diff | `ctxc-artifact-diff-0.1` |
| Detached trust manifests | `ctxc-trust-manifest-0.1`; verification reports use `ctxc-trust-verification-0.1` |
| CLI diagnostics/events | `ctxc-diagnostic-0.1` / `ctxc-event-0.1` |
| LRCBench reports | Current `lrcbench-0.2` plus the explicit retained local-only `0.1` replay path |

No format is silently migrated. See
[docs/SCHEMA_COMPATIBILITY.md](docs/SCHEMA_COMPATIBILITY.md) and
[docs/RELEASE_POLICY.md](docs/RELEASE_POLICY.md).
The seven schemas that predate the distribution rename retain their historical
`lossless-context-compiler` `$id` URI values for compatibility. The new trust
manifest schema uses the current `loss-resistant-context-compiler` namespace;
all eight files install under the current distribution's schema directory.

## Installation support

Source-tree editable installs and locally built wheels are tested. No package
index release is currently claimed. Before a public release, the repository
must add signed source/wheel artifacts, test-index installation evidence, and
clean installation results for each supported platform.

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
