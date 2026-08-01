# Materialized context-window consumer path

CtxC can produce a deterministic, hard-budgeted planning artifact from an
authoritative LRCC memory bundle, a bounded recent history, and one current user
turn. The artifact stops before provider execution. The stable consumer wrapper
is exported from `context_compiler`; the lower-level prototype and materialized
window types remain experimental and module-qualified.

## Stable consumer API and CLI

Python hosts call `materialize_context()` with their exact
`ExactTokenCounterAdapter`, one independently calculated allocation digest, and
a `ContextWindowBudget`. The returned exact dictionary contains the unchanged
materialized v1 context, its unchanged runtime planning payload, a fixed
component manifest, and a compact receipt. Retain the receipt SHA-256 outside
the result and require it again when calling
`verify_materialized_context_result()`.

The manifest fixes this assembly order without concatenating untrusted text:

1. verified LRCC semantic memory;
2. recent raw source messages;
3. one empty, host-owned external-retrieval slot;
4. the exact current user turn.

The retrieval slot contains no text or digest. It is classified as untrusted,
cannot mutate LRCC memory, and cannot supply system or developer instructions.
A host that later inserts retrieval must bind and label it independently. The
LRCC result continues to carry `retrieval_result_sha256: null`.

The dependency-free CLI performs the same operation over existing JSON or
JSONL history:

```console
ctxc materialize examples/materialized_context.jsonl \
  --current-turn-id deploy-011 \
  --hard-limit-tokens 3000 \
  --memory-budget-tokens 2200 \
  --reserved-output-tokens 128 \
  --safety-margin-tokens 64 \
  --minimum-recent-messages 2 \
  --maximum-recent-messages 3 \
  --per-message-overhead-tokens 2 \
  --allocation-plan-sha256 <independently-calculated-sha256> \
  --tokenizer-profile unicode-codepoint-count-v1 \
  -o materialized-context.json
```

The CLI profile counts Unicode code points exactly for that named diagnostic
profile. It is not a provider tokenizer and must not be presented as exact
provider accounting. Hosts that have a real tokenizer use the Python API. Both
paths remain planning-only and require a recount over the final immutable
provider request.

Construction failures emit no partial result. With `--error-format json`, the
CLI reports the existing bounded diagnostic plus the stable
`ContextWindowError.reason`. When strict memory compilation computes an
over-budget candidate before independent verification, the CLI emits the
versioned `ctxc-diagnostic-0.2` envelope with a closed
`loss-resistant-materialization-refusal-diagnostic-v1` details object. It binds
the tokenizer identity, memory budget, required and overflow planning units,
compiled-prefix count, and a content-free ordered prefix-manifest digest. It
does not contain source IDs, source text, paths, timestamps, partial compiled
memory, or exception internals. Other compile or replay failures retain the
generic reason without invented numeric details.

These counts describe the named materialization counter, not provider tokens.
Default Python calls and the CLI do not retry with a larger budget or silently
clamp the refusal. A Python host may explicitly provide
`ContextWindowDegradationPolicy()`. That bounded path preserves the strict
attempt, retries the same partition with a self-describing lossless compact
memory representation, compares the two exact pre-verification requirements
observed for that original partition, and performs at most one bounded
reallocation using the smaller observed form. A shifted boundary can change the
final compiled rendering, so the `minimal_memory_reallocation_*` rung names do
not claim a globally minimal budget over every possible repartition. It never
reduces fixed input, the current turn, or the configured minimum recent tail.
Older raw recent messages move into the compiled prefix only when that exact
reallocation makes it unavoidable; each such source remains represented by a
digest-bound omission and the complete `ContextBundle` source inventory.

The compiler metadata binds the degradation mode and rung, the requested and
effective memory budgets, and any compact rendering profile before artifact,
bundle, prototype, materialization, runtime, manifest, and receipt digests are
calculated. The allocation-plan digest remains independently supplied by the
host, and the final provider request still requires a separate exact recount.
A self-hash alone is not an external anchor: an installed-package
witness binds its domain-separated refusal wrapper inside an independently
retained canonical witness-line digest, while an accepted result verifier
still requires both the independently retained receipt digest and allocation
digest.

The digest embedded beside a release-smoke witness is descriptive, not its own
authority. A consumer must pin the canonical-line SHA-256 outside that report
and supply it through
`scripts/release_install_smoke.py --expected-materialized-witness-sha256`
when it revalidates the exact wheel and sdist pair.

## Installed structural retention diagnostic

`ctxc evaluate-materialization` exercises this same public consumer path over
an immutable package-resident pack of 30 project-authored synthetic-naturalistic
coding histories. Grouped splits contain 4 train, 6 development, and 20
held-out cases. The command compares full raw history, a bounded recent tail,
and `materialize_context()` under `unicode-codepoint-count-v1`, and writes a
canonical report with exact integer planning-unit, correction, identifier,
path, number, detail, current-turn, omission/refusal, and authority-boundary
measurements.

```console
ctxc evaluate-materialization --split heldout -o retention-report.json
```

The separate fixed comparison command measures strict, compact-only, and the
public compact-plus-single-reallocation policy over the unchanged held-out
split:

```console
ctxc evaluate-materialization-degradation -o degradation-report.json
```

Its canonical report binds exact source-span structural retention, correction
precedence, current-turn, omission, deterministic-byte, and a receipt digest
from the second deterministic execution. It does not
enable degradation for `ctxc materialize`, add a retrieval binding, or make the
result provider-ready.

No arm calls or simulates a model, retrieval system, or provider. Zoom or other
retrieval remains outside authoritative LRCC memory. The fixtures are visible,
project-authored test material rather than blind gold or a collected, licensed,
consented, privacy-reviewed natural cohort. This structural diagnostic does not
establish semantic completeness, downstream task completion, provider-token
accounting/readiness, or comparative superiority, and it does not close P0-E3.
See [Materialization retention evaluation](MATERIALIZATION_RETENTION_EVALUATION.md)
for the exact pack, split, report, and claim boundaries.

Lower-level callers may still use the provisional modules directly:

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
these module-qualified names. The stable wrapper deliberately does not export
those types or reinterpret their v1 bytes.

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

`verify_materialized_context_result(...)` accepts either an exact decoded
object or the exact output-file bytes emitted by `ctxc materialize -o ...`.
Serialized input must be one bounded canonical UTF-8 JSON line; duplicate keys,
noncanonical whitespace or key order, alternate line endings, trailing data,
invalid Unicode, excessive nesting, and oversized input are rejected before
the existing receipt and allocation checks. The caller must still retain and
supply both expected digests independently of the serialized result.

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

The lower-level prototype and materialization types do not claim semantic
completeness, provider execution, provider-specific serialization, retrieval
integration, superiority, or a stable public API. Only the wrapper and CLI
surface described above are public. Stored roles on recent messages are
provenance data, not permission to replay assistant, tool, or function text as
native provider-role messages; a host must wrap the component as untrusted
data. LRCC does not destructively remove source history, and callers remain
responsible for retaining the authoritative event log and for checking
independently expected artifact digests at trust boundaries.
