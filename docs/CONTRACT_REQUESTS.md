# Upstream contract requests

This file records changes that must be owned by `localai-contracts`. CtxC does
not add private fields or operations to the shared protocol while these
requests are unresolved.

## LRCC-001: bind negotiated schemas to operations

Status: proposed after `localai-contracts==0.2.0a2` / protocol `1.0.0`.
Whether this is a protocol-minor or major boundary depends on the explicit
old-peer fallback described below.

### Current gap

The current handshake intersects `supported_schema_versions` globally and
negotiates `context.compile` independently. An operation capability does not
declare which request and response schemas it requires. Consequently, two
peers can negotiate `context.compile` even when one peer omitted
`SourceEvent`, `ContextBundle`, or a common version for either schema.

`expected_response_schema` is optional, so it cannot be the operation's schema
contract. The adapter callback also receives only `(operation, payload)` and
cannot recover an immutable operation-specific negotiation decision. The
stateful `ConnectorServer` and `NdjsonConnectorServer` own the handshake, so a
provider-local request field would neither fix nor faithfully represent this
gap.

### Minimal shared change requested

The pinned `1.0.0` manifest schema closes operation-capability objects with
`additionalProperties: false`. Adding the member below to a `1.0.0` document
is therefore not backwards-compatible: an old peer rejects the manifest before
negotiation. The owner must introduce a new manifest/handshake schema version.
It may call the change protocol-minor only if the transport has an explicit,
tested way to select or retry with the old `1.0.0` manifest serialization for
old peers. Without that mechanism, this is a deliberate breaking protocol
boundary and must be versioned accordingly. CtxC must never send the new field
while claiming the old schema.

Add an optional `schema_requirements` member to each operation capability:

```json
{
  "operation": "context.compile",
  "schema_requirements": {
    "request": [
      {
        "name": "SourceEvent",
        "payload_array_path": "/source_events",
        "applies_to": "each_item"
      }
    ],
    "response": {
      "name": "ContextBundle"
    }
  }
}
```

`payload_array_path` is an RFC 6901 JSON Pointer resolved against the operation
payload, not against the outer connector request. `applies_to: "each_item"`
requires the selected value to be an array and applies the negotiated schema
to every element in array order. A missing path or non-array value is a
request-validation error. An empty array selects no documents; whether an
empty array is allowed remains the operation payload schema's responsibility.
There is no wildcard-token extension, so RFC 6901 escaping remains unchanged
and a property literally named `*` has no special meaning.

Negotiation should include an operation only when every listed requirement has
a common schema version. `NegotiatedCapabilities` should retain the selected
version per operation and schema. `ConnectorServer` should validate the
declared request locations and response against those selected versions, even
when `expected_response_schema` is absent, and make the immutable negotiated
operation context available to the adapter callback or an equivalent typed
operation handler. The in-process and NDJSON servers must apply the same rule.

Within the new manifest version, an operation capability without
`schema_requirements` retains the current behavior. This prevents
reinterpretation of upgraded manifests; it does not by itself make a new-field
manifest acceptable to a `1.0.0` peer.

### Required conformance vectors

1. The peer advertises `context.compile` but omits `SourceEvent`.
2. Both peers advertise `SourceEvent` but have no common version.
3. The peer omits `ContextBundle` or has no common response version.
4. `expected_response_schema` is absent, but the server still validates the
   negotiated response schema.
5. An element selected from payload array `/source_events` is malformed or uses an unselected
   version.
6. Both schemas negotiate exact version `1.0.0`; typed in-process and NDJSON
   execution produce the same successful result.

### Current CtxC behavior

CtxC advertises the exact shared versions that it accepts and validates every
actual input and output model. Its provider-local conversion-audit sidecar
records `operation_schema_binding_available: false` and
`operation_schema_binding_enforced: false`. It does not alter the shared
manifest, handshake, `context.compile` payload, or `ContextBundle` response to
simulate the missing upstream guarantee.
