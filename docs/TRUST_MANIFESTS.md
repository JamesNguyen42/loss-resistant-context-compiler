# Detached trust manifests

`ctxc-trust-manifest-0.1` is a small detached record that binds one complete
compiled artifact to the exact source records used to verify it. It can also
bind the current hash-chained source-archive head.

The manifest becomes a trust control only when its `manifest_sha256` is retained
outside the storage boundary that holds the manifest, artifact, and sources.
The digest is not secret. It must be protected against unauthorized
replacement, for example in authenticated release metadata, protected
configuration, a transparency system, a signed record, or separately
controlled write-once storage. Copying the digest into another file beside the
bundle does not create an independent anchor.

## Direct-source workflow

Create a complete artifact, replay it, and emit its detached manifest:

```console
ctxc compile history.jsonl -o compiled-memory.json
ctxc verify compiled-memory.json history.jsonl
ctxc trust create compiled-memory.json history.jsonl -o trust-manifest.json
```

Read `manifest_sha256` from `trust-manifest.json` and retain that exact
64-character lowercase digest through the independent channel. Later, require
the retained value:

```console
ctxc trust verify trust-manifest.json compiled-memory.json history.jsonl \
  --expected-manifest-sha256 MANIFEST_SHA256
```

Verification exits `0` only when every check passes. A well-formed but
untrusted or mismatched bundle produces a versioned
`ctxc-trust-verification-0.1` report and exits `3`. Invalid inputs, malformed
digests, unsafe paths, and resource-limit failures exit `2`.

## Hash-chained archive workflow

The archive form verifies the archive first, freezes its observed chain head,
and reloads against that exact head before creating or verifying the manifest:

```console
ctxc trust create compiled-memory.json \
  --archive .context-archive \
  --archive-expected-chain-head ARCHIVE_HEAD \
  -o trust-manifest.json

ctxc trust verify trust-manifest.json compiled-memory.json \
  --archive .context-archive \
  --archive-expected-chain-head ARCHIVE_HEAD \
  --expected-manifest-sha256 MANIFEST_SHA256
```

The optional `--archive-expected-chain-head` is a second external precondition.
The manifest always records the archive head observed by the command. Legacy
raw-JSONL archives have no chain head and are refused by trust commands; a
non-idempotent `ctxc archive append` upgrades them before use.

## What creation requires

`create_trust_manifest()` and `ctxc trust create` require:

- a supported artifact envelope with a matching `artifact_sha256`;
- `ledger_complete: true`;
- an independent replay that passes against the supplied source records;
- source records whose content and canonical-record hashes are valid; and
- a canonical optional archive chain head.

The manifest binds:

- artifact schema version and full artifact SHA-256;
- canonical source-set digest and source count;
- complete-ledger status;
- archive chain head or an explicit `null` for direct sources;
- a declared UTC creation time; and
- a canonical self-hash over every field except `manifest_sha256`.

The timestamp is descriptive. It is not a trusted timestamp and does not
establish freshness by itself.

## What verification checks

`verify_trust_manifest()` reports eight independent booleans:

1. strict manifest shape;
2. manifest self-hash;
3. externally supplied manifest anchor;
4. artifact envelope integrity;
5. full artifact replay;
6. artifact binding;
7. source-set binding; and
8. archive-head binding.

All eight must be true. The CLI requires
`--expected-manifest-sha256`. The Python API permits `None` so callers can
inspect a bundle, but it deliberately returns `passed: false` with
`missing_external_anchor`; an unanchored self-hash can never be reported as
trusted.

Manifest files use strict JSON with duplicate-key and non-finite-number
rejection. Reads are limited to 64 KiB, 64 KiB per physical line, and eight
container levels, and use the same stable regular-file and ancestor-boundary
checks as other serialized project inputs. CLI output refuses to alias an
artifact, source file, manifest input, or archive event log.

## Python API

```python
from context_compiler import (
    create_trust_manifest,
    verify_trust_manifest,
)

manifest = create_trust_manifest(artifact, trusted_sources)
anchor = manifest["manifest_sha256"]  # retain outside this storage boundary

report = verify_trust_manifest(
    manifest,
    artifact,
    trusted_sources,
    expected_manifest_sha256=anchor,
)
assert report["passed"]
```

Python callers that supply `archive_chain_head_sha256` are responsible for
obtaining that value from a separately verified stable archive state. The CLI
performs that state check for its `--archive` workflow.

## Non-goals and residual risk

The manifest is not a digital signature, certificate, timestamp authority,
key-management system, access-control mechanism, or tamper-proof log. It does
not authenticate source roles, prove that source content was truthful, or
protect data after host compromise. An attacker who can replace the bundle and
the supposedly external expected digest can create a new internally valid
bundle. Freshness and rollback resistance likewise depend on retaining the
latest manifest digest and, for archives, the latest chain head outside the
attacker's rewrite boundary.

The machine-readable file contract is
[trust-manifest.schema.json](../schemas/trust-manifest.schema.json). Existing
pre-rename schema `$id` values remain stable compatibility identifiers; this
new schema uses the current `loss-resistant-context-compiler` namespace.
