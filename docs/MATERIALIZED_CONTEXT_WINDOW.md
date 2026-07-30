# Materialized context-window prototype

CtxC can produce a deterministic, hard-budgeted planning artifact from an
authoritative LRCC memory bundle, a bounded recent history, and one current user
turn. The artifact stops before provider execution. It is experimental,
module-qualified, and intentionally not exported from `context_compiler`.

Use the provisional modules directly:

```python
from context_compiler.connector import ExactTokenCounterAdapter
from context_compiler.context_window import (
    ContextWindowBudget,
    ContextWindowPrototype,
    compose_context_window,
)
from context_compiler.materialized_window import (
    MaterializedContextWindow,
)
```

`ContextWindowPrototype` is deliberately distinct from the external
`localai_contracts.ContextWindowPlan`. The external contract describes a
desired allocation. This prototype records the LRCC result of applying a local
allocation policy. No compatibility or stable public-API promise is attached to
these module-qualified names.

## Source partition and authority

The input must be an exact built-in `list` or `tuple` of exact `SourceRecord`
or plain `dict` values, already ordered by strictly increasing sequence. One
explicit `current_turn_id` must identify the final message, and that message
must have the canonical `user` role.

Every accepted input is represented in exactly one ordered partition:

1. an older prefix compiled into a verified `ContextBundle`;
2. bounded recent history retained verbatim;
3. the current user turn retained verbatim and separately accounted.

Prefix entries remain visible in `recent_tail_omissions` through their source
ID, sequence, role, content digest, original record digest, compiled record
digest, and the reason `compiled_into_verified_memory`. This is explicit
accounting for removal from the live tail, not a claim that the compiler
preserved every possible meaning.

The prototype accepts only `user`, `assistant`, `tool`, and `function` history
roles. Assistant, tool, and function history enters the connector without
authenticated authority. Caller-authored connector authority, provenance,
redaction, and trusted-state metadata is rejected because this boundary has no
independent authenticator. Host `system` and `developer` framing belongs in the
host-owned fixed-input component rather than source history.

Role-shaped text inside content remains ordinary data. Tabs and line endings
are preserved; other control characters, malformed Unicode, duplicate IDs or
sequences, mapping/scalar subclasses, bool-as-int values, unknown fields, and
noncanonical roles are rejected.

## Protected retention

When an older prefix is compiled, the prototype requires all of the following:

- exact tokenizer accounting;
- verification and overflow-refusal compiler policy;
- an issued detector-scoped retention certificate;
- detected protected commitments equal retained protected commitments;
- `semantic_completeness_claimed` equal to `false`;
- no omitted or overflowed protected item;
- every active goal, constraint, user correction, decision, and every
  core-protected item selected in the verified bundle;
- an independent replay over the exact source events.

The dedicated protected-state digest covers active goals, constraints, user
corrections, decisions, unresolved questions, exact errors, exact references,
and the protected-omission inventory. The complete nested bundle digest remains
the broader binding.

Optional recent history cannot consume the protected-memory allocation or the
current-turn allocation. If mandatory memory, the current turn, the requested
minimum tail, output reserve, or margin does not fit, construction raises a
reason-coded `ContextWindowError`. Counts are never clamped.

The compiler records wall-clock time and elapsed duration. For this
planning-only artifact, those two operational observations are normalized to
fixed neutral values immediately after compilation. The artifact and bundle
digests are then recomputed and ordinary connector replay is repeated. No
semantic field, source identity, retention result, provenance span, or token
count is normalized.

## Exact planning accounting

The accounting equation is:

```text
input =
  fixed host input
  + verified memory
  + recent history
  + current user turn

occupied =
  input
  + reserved provider output
  + safety margin
```

Equality with `hard_limit_tokens` is accepted. One token over is refused.
`fixed_input_tokens` may cover host-owned provider framing, tool schemas, and
attachments. A nonzero fixed count requires a digest of the exact fixed-input
manifest. The count is caller supplied, so the artifact does not label the
final provider request exact.

The materialization binds the tokenizer identity, allocation digest, prototype
digest, complete nested `ContextBundle` and bundle digest, rendered-memory
digest, protected-state digest, ordered recent-history digest, current-turn
record digest, omission digest, all counts, and the null local refusal reason
into `materialization_sha256`.

`to_bytes()` and
`runtime_bytes(expected_allocation_plan_sha256=...)` use canonical compact
UTF-8 JSON. Returned JSON values are detached copies, accounting is read-only,
and every export revalidates nested and outer digests. Both runtime projection
methods require the caller's independently expected allocation digest and
refuse a different, missing, or malformed value. Projection remains
path-neutral.

## Final provider recount

`runtime_payload(expected_allocation_plan_sha256=...)` is a planning payload,
not a provider request. It always contains:

```json
{
  "provider_execution_ready": false,
  "final_provider_recount_required": true,
  "retrieval_result_sha256": null
}
```

The trusted compositor must assemble the final immutable provider request and
recount all prompts, verified memory, recent history, current turn, tool
schemas, attachments, framing, reserved output, and margin with the bound
tokenizer. It must refuse a hard-limit overflow. This prototype supplies no
mechanism for claiming that recount passed.

Retrieval is outside v1. A separate, data-only `retrieval_result_sha256`
boundary is a follow-on; `fixed_input_tokens` does not stand in for that
binding, and this artifact makes no combined-mode or ZoomCache claim.

## Limits

This prototype does not claim semantic completeness, provider execution,
provider-specific serialization, retrieval integration, superiority, or a
stable public API. It does not destructively remove source history; callers
remain responsible for retaining the authoritative event log and for checking
independently expected artifact digests at trust boundaries.
