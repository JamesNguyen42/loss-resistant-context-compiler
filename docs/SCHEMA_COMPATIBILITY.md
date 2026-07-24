# Compiled-artifact schema compatibility

This document defines the reader, writer, and migration policy for
compiled-memory artifacts. It applies to the top-level `schema_version` field;
the benchmark, diagnostic, inspection, diff, event, and metrics formats have
their own independently versioned contracts.

## Current support window

| Artifact version | Read | Write | Status |
| --- | --- | --- | --- |
| `1.0` | yes | yes | current |

The current package writes only `1.0` and accepts only `1.0`. An unknown
well-formed version is not treated as approximately compatible. Artifact
inspection, diffing, and independent replay reject it before trusting its
contents.

Within `1.0`, `compiler_metadata.metrics` is an optional additive field. Older
`1.0` artifacts that omit it remain readable and replayable. When metrics are
present, their schema and replayable fields are validated. No other omitted,
renamed, or reinterpreted field is implied by this exception.

The support window is available without reading an artifact:

```console
ctxc schema
ctxc schema --artifact-version 1.0
ctxc schema --artifact-version 2.0
```

The last command succeeds as a query and reports `status: "unsupported"`,
`readable: false`, `writable: false`, and no migration. Malformed or
non-canonical versions fail with exit code `2`. Version queries use canonical
`MAJOR.MINOR` decimal syntax without whitespace, prefixes, leading zeroes, or
extra components.

Python callers can use `artifact_schema_registry()` and
`artifact_schema_support(version)`. Both return
`ctxc-artifact-schema-compatibility-0.1` records.

## No silent migration

This release has no artifact migration function and performs no automatic or
silent migration. Changing `schema_version`, reshaping a payload, and
recomputing `artifact_sha256` is not a trusted migration: the self-hash proves
only internal consistency and does not establish semantic equivalence.

A future migration must be an explicit, separately identified operation. It
must:

1. preserve the original artifact;
2. require replay against separately trusted source events;
3. record the origin artifact digest and a stable migration identifier;
4. emit a new artifact with its own digest;
5. fail closed when source replay, field interpretation, or protected-state
   coverage cannot be established.

Introducing a migration also requires changing the machine-readable registry,
tests, JSON Schema, architecture and handoff documentation, and release notes
together. Supporting a newer writer does not by itself authorize an older
reader to ignore unknown fields or reinterpret old ones.

## Operational guidance

Retain the original full-ledger artifact and immutable source history together.
Before upgrading software, record the artifact digest and run
`ctxc schema --artifact-version VERSION` with the target package. After an
upgrade, use `ctxc verify ARTIFACT SOURCES`; a successful envelope check or
self-hash alone is not semantic verification.

Active-only artifacts are deliberately incomplete and cannot receive a full
replay certificate. They are not suitable as the sole input to any future
migration.
