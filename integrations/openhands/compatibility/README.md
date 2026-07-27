# OpenHands compatibility policy

`ctxc-openhands` supports exactly one reviewed host/API identity at a time:

- OpenHands `1.8.0` at
  `bc26df351dd5d833a95131556dbe2da69af82253`;
- `openhands-sdk`, `openhands-tools`, and `openhands-agent-server` `1.27.0` at
  `904279edf2df5fa12d7caecc7576f62659b2e2dd`; and
- Python 3.12 or 3.13.

The runtime machine-readable authority is packaged at
`ctxc_openhands/data/openhands-1.8.0.json`; `openhands-1.8.0.json` here is
a required byte-identical audit copy. Its reviewed API inventory is a closed, 23-file
manifest. Both the per-file records and their aggregate digest are checked
offline before compatibility is accepted.

## Fail-closed runtime boundary

Ordinary `ctxc` and `ctxc-openhands` imports do not import OpenHands. A live
bridge must first verify the exact distribution versions and the reviewed
source identity, then lazily import the known classes. An unrecognized
version, source digest, event class, serialized nested action/observation kind,
or source/role combination is unsupported and must be rejected. An unknown
explicit tool name in an otherwise valid event whose nested payload asserts no
`kind` is retained only in the generic untrusted category and is ineligible for
authority. If a nested action or observation does serialize `kind`, an unknown
kind fails closed; every known nested kind must match the explicit tool
category.

OpenHands event `source` values are provenance labels, not authentication.
Assistant, tool, retrieval, file, search, hook, and delegated-agent content
remains untrusted unless a separate authenticated-authority receipt verifies
it. This policy does not claim semantic completeness.

The reviewed final-request seams are private OpenHands SDK methods. They are
usable only while the exact `llm.py` source digest remains bound by the pin
manifest. They expose LiteLLM-bound arguments, not necessarily byte-identical
provider HTTP payloads. A wire-exact claim requires an independently verified
transport capture.

## Current blocker

A real offline import and live scenario have not run. The reviewed packages
are absent and there is no local hash-pinned wheelhouse containing their
complete dependency closure. This is retained as a blocking failure; the
offline fake runtime is not evidence of live compatibility.
`requirements-live.lock` binds the four reviewed direct wheels only and is
explicitly not evidence of a complete dependency closure.

## Upgrade procedure

Upgrades are result-blind:

1. choose one exact upstream host revision and its exact SDK/tools revisions;
2. review the complete event, conversation, persistence, View, condenser,
   tokenizer, and final-request surfaces before running compatibility results;
3. regenerate every source, license, lock, and distribution-artifact binding;
4. add explicit mappings and negative tests for every new class or field;
5. retain unknown or removed behavior as a red gate; and
6. open review without widening authority, completeness, or performance
   claims.

Changing only a version string, recomputing a digest after observing a test
result, accepting a compatible-version range, or weakening the validator is
not an upgrade.
