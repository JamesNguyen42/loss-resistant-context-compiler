# Release and versioning policy

## Stable identities

The stable Python distribution name is
`loss-resistant-context-compiler`. Its normalized wheel prefix is
`loss_resistant_context_compiler`. The import package remains
`context_compiler`, the command-line entry point remains `ctxc`, and installed
JSON Schemas live under
`share/loss-resistant-context-compiler/schemas`.

Early development metadata used `lossless-context-compiler`. That name is not a
supported alias or compatibility package. A local editable installation with
the old metadata should be uninstalled before installing the renamed
distribution. No artifact, source-archive, or benchmark schema was renamed
with the distribution; the import package and CLI identity are also unchanged.
The seven JSON Schemas that existed before the rename retain their historical
`https://example.invalid/lossless-context-compiler/...` `$id` values because
those URIs are stored-format identifiers, not distribution branding or live
network endpoints. New schemas, beginning with the detached trust manifest,
use the `loss-resistant-context-compiler` namespace. All schema files install
under the current distribution path.

## Semantic versions

The Python distribution uses Semantic Versioning:

- `MAJOR` changes when a stable public Python or CLI contract breaks;
- `MINOR` adds backward-compatible public behavior;
- `PATCH` fixes backward-compatible defects.

While the version is `0.y.z`, a minor release may deliberately change an
unstable public contract, but the change must be called out under
`CHANGELOG.md`, include migration instructions, and must not silently reinterpret
stored evidence.

The version in `pyproject.toml` and `context_compiler.__version__` must match.
Distribution versions do not replace embedded format versions. Artifact,
archive, redaction, inspection, diff, event, diagnostic, benchmark, corpus,
candidate, protocol, and manifest schemas each evolve under their own explicit
version field.

## Stored-format compatibility

The artifact reader/writer window is published by
`ctxc schema` and documented in
[SCHEMA_COMPATIBILITY.md](SCHEMA_COMPATIBILITY.md). Unknown versions fail
closed. There is no automatic migration.

A stored-format change requires:

1. a new explicit schema version when interpretation changes;
2. strict old/new fixtures and negative tests;
3. an updated reader/writer support window;
4. trusted-source replay for any explicit migration;
5. origin and output digests in the migration record;
6. changelog and handoff updates.

Benchmark and evaluation formats follow the same no-silent-reinterpretation
rule. A code release must continue to replay committed frozen reports or
document and implement an explicit compatibility reader.

## Release gates

A release candidate is not ready until all applicable checks pass from a clean
commit:

1. full tests on every supported Python version;
2. lint and bytecode compilation;
3. offline source-distribution and wheel builds;
4. exact distribution name/version/entry-point metadata checks;
5. clean-environment wheel installation and import;
6. inclusion and parseability of all eight installed JSON Schemas;
7. frozen benchmark, protocol, and model-diagnostic replay without new model
   calls;
8. the fixed performance gate;
9. changelog, support matrix, threat model, and handoff reconciliation;
10. retained SHA-256 checksums and external signatures for public artifacts.

Passing these gates establishes packaging and internal-evidence consistency,
not production readiness or superiority over external systems. The P0 evidence
gates in `TODO.md` remain separately binding.

## Deprecation and support

Before `1.0.0`, deprecated Python or CLI behavior should remain available for
one minor release when doing so does not weaken fail-closed security or stored
evidence. At and after `1.0.0`, backward-incompatible public changes require a
major version. Security fixes may remove unsafe behavior immediately and must
be documented.

Only the latest development line receives fixes during the alpha phase. The
current platform and format matrix is in [../SUPPORT.md](../SUPPORT.md).
