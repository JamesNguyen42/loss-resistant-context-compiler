# Draft RFC: durable event callbacks and immutable final-request hooks

Status: local proposal; not submitted upstream.

Target reviewed baseline:

- OpenHands `1.8.0` at
  `bc26df351dd5d833a95131556dbe2da69af82253`; and
- OpenHands SDK/tools `1.27.0` at
  `904279edf2df5fa12d7caecc7576f62659b2e2dd`.

This proposal addresses integration gaps found during result-blind API review.
It does not claim that the current adapter is live-compatible or semantically
complete. The exact reviewed identity and blocker are in the
[compatibility policy](../compatibility/README.md).

## Summary

Add two stable, public host extension points:

1. a post-persistence event subscription with ordered replay and durable
   cursor semantics; and
2. a pre-dispatch immutable final-request hook over the exact request the
   provider transport will send.

Both hooks must cover synchronous and asynchronous execution, expose explicit
failure behavior, and prevent bypass APIs from silently escaping the same
event, authority, confirmation, security, and accounting path.

## Motivation

The reviewed host currently presents three integration risks.

First, user callbacks run before the default callback that appends to the host
`EventLog`. If an integration callback raises, it can prevent the host's own
persistence. A safety-conscious adapter must catch every exception, retain a
bounded failure, poison later dispatch, and reconcile the complete persisted
log. That preserves source history but makes failure handling indirect.

Second, the reviewed request-preparation seams are private methods. They expose
LiteLLM-bound keyword arguments, not a stable immutable envelope and not
necessarily the byte-identical provider HTTP request. The host may assemble or
transform prompts, memory, retrieval, attachments, tool schemas, framing, and
provider fields across multiple layers. An adapter cannot honestly claim exact
final-request accounting if any mutation occurs after its hook.

Third, `LocalConversation.ask_agent()` is unrecorded and stateless, while
`execute_tool()` bypasses the normal loop, event path, confirmation, and
security analysis. An integration that guarantees replay and authority
boundaries must refuse both today.

## Goals

- Let an integration observe every durable host event exactly once in
  persisted order, then replay from a durable cursor after failure.
- Let an integration inspect, hash, account, persist, approve, or refuse the
  exact immutable request immediately before provider dispatch.
- Bind retry attempts and provider routes without allowing the request to
  mutate after approval.
- Distinguish exact counts from estimates in the public type system.
- Preserve tool call/result atomicity and delegated-agent provenance.
- Make bypass behavior explicit and testable.
- Keep host secrets and private reasoning protected by least-privilege views.

## Non-goals

- Defining semantic completeness for condensed memory.
- Declaring one context compiler, tokenizer, or agent superior.
- Making arbitrary tool output trusted.
- Replacing provider-specific tokenizers with a universal tokenizer.
- Requiring a particular database or external integration.
- Exposing secrets or full request content to every callback.

## Proposal A: persisted-event subscription

Introduce a public API conceptually equivalent to:

```python
class PersistedEventEnvelope:
    conversation_id: str
    ordinal: int
    event_id: str
    event_kind: str
    event: Event
    previous_log_head_sha256: str
    log_head_sha256: str


class EventSubscription:
    def replay(self, *, after_ordinal: int | None = None) -> Sequence[PersistedEventEnvelope]:
        ...

    def acknowledge(self, *, ordinal: int, log_head_sha256: str) -> None:
        ...


def subscribe_persisted_events(
    callback: Callable[[PersistedEventEnvelope], None],
) -> EventSubscription:
    ...
```

Names are illustrative. The required semantics are:

1. The host commits the event to its durable `EventLog` before invoking the
   callback.
2. The callback receives a stable conversation ID, persisted ordinal, exact
   event ID/kind, and a log-head binding.
3. Callback failure never rolls back or deletes the host event.
4. Failure is observable. The host either pauses subsequent model dispatch or
   exposes a policy switch that a strict integration can use to pause it.
5. Replay returns the complete persisted order from a cursor and detects a
   stale or mismatched log head.
6. Acknowledgement is explicit and idempotent. It must not mean the event is
   trusted or semantically understood.
7. Transient streaming deltas and derived condensation-summary views are
   marked non-durable and use a separate API. A durable event must never be
   silently reclassified as transient.
8. Unknown future event classes remain visible to the subscription, allowing
   strict consumers to stop rather than silently discard them.

### Authority metadata

The envelope should distinguish host provenance from authenticated authority.
`source="user"` and `role="system"` are not authentication. If the host has an
authenticated principal, expose a separately signed or otherwise verifiable
authority assertion with issuer, conversation, event ID, exact event digest,
role, and optional tool binding. Do not infer authority from event literals.

### Tool atomicity

Tool-related events should carry a stable group identity and explicit role:

- call;
- result;
- rejection; or
- agent/scaffold error.

Every result must bind the tool call ID and tool name. Observations and
rejections should also bind the action event ID. Agent errors that cannot bind
an action after restart must still bind the exact tool call ID and tool name.
The public view should let consumers request only complete groups while the
durable log continues to retain every individual event.

Delegated-agent events need a stable parent conversation/event binding. Nested
histories must not become unauthenticated authority merely because the parent
agent invoked them.

## Proposal B: immutable final-request hook

Introduce a stable request lifecycle with an immutable envelope:

```python
class FinalRequestPart:
    category: str
    bytes: bytes


class FinalRequestEnvelope:
    request_id: str
    attempt: int
    conversation_id: str
    model: str
    provider: str
    route: str
    content_type: str
    parts: Sequence[FinalRequestPart]
    body: bytes
    body_sha256: str
    tool_schema_sha256: str
    tokenizer: TokenizerAccounting
    reserved_output_tokens: int
    hard_limit_tokens: int


class RequestDecision:
    approved_body_sha256: str
    evidence_id: str


def before_provider_dispatch(
    envelope: FinalRequestEnvelope,
) -> RequestDecision:
    ...
```

Again, names are illustrative. Required behavior:

1. The envelope is constructed after prompts, verified memory, recent tail,
   the current turn, retrieval, attachments, tool schemas, provider framing,
   output reservation, and every provider-specific transformation are final.
2. `body` is the exact byte sequence passed to the transport. If the transport
   applies later encoding or mutation, the hook must move below that layer or
   expose the later exact bytes.
3. The body and parts are immutable. The host verifies that the digest
   approved by the callback is the digest dispatched.
4. Refusal prevents network dispatch and produces a durable, typed failure.
5. Every retry receives a new attempt identity and envelope. A previous
   approval cannot authorize changed bytes.
6. Sync and async paths share identical envelope and refusal semantics.
7. Streaming requests bind the immutable request bytes before response
   streaming starts.
8. The hook exposes the final tool-schema digest and model/route identity.
9. Provider redaction may hide selected content from low-privilege observers,
   but an accounting authority needs a capability-scoped exact-byte view or a
   provider-signed digest/count record. A redacted view cannot claim an
   independently recomputed exact digest.

### Component attribution

The public API should define stable categories at least as expressive as:

- prompts/system instructions;
- verified memory;
- recent uncompiled tail;
- current turn;
- retrieval;
- attachments;
- tool schemas; and
- provider framing.

If provider framing cannot be separated without changing bytes, it may be one
or more explicitly labeled parts. Concatenating parts must reproduce `body`
exactly. No byte can be omitted or counted twice.

### Exact and estimated token accounting

Use a tagged union rather than a boolean:

```python
class ExactAccounting:
    mode: Literal["exact"]
    tokenizer_identity: str
    tokenizer_revision: str
    replay_vector_sha256: str
    component_counts: Mapping[str, int]
    total_tokens: int


class EstimatedAccounting:
    mode: Literal["estimated"]
    estimator_identity: str
    estimate: int
    uncertainty: str | None
```

An exact record must replay against the immutable body and exact tokenizer
identity. Estimated accounting cannot be promoted to exact by configuration
or naming. Before dispatch, the host must refuse when:

```text
exact total + reserved output + configured safety margin > hard model limit
```

When only an estimate exists, the public policy must say whether dispatch is
allowed and must retain the estimated label. A strict integration can refuse.

### Relationship to private SDK seams

Private preparation methods may remain implementation details. The public hook
must be versioned independently and tested below any mutable preparation
layer. A source hash over a private method is a temporary compatibility guard,
not a substitute for the public contract.

LiteLLM-bound keyword arguments should not be described as wire-exact unless
the same bytes are passed unchanged to the provider transport.

## Proposal C: remove silent bypasses

Every public request-driving API should declare one of:

- `guarded=True`: it emits durable events, passes confirmation/security
  analysis, invokes the immutable final-request hook, and is replayable; or
- `guarded=False`: strict integrations can disable it at conversation
  construction, and invocation produces a typed refusal.

For the reviewed APIs:

- `ask_agent()` should either enter the normal guarded request lifecycle or be
  explicitly disableable. A stateless, unrecorded call cannot satisfy durable
  replay.
- `execute_tool()` should either emit the ordinary action/result events and
  pass confirmation/security hooks or be explicitly disableable. Direct
  execution cannot silently bypass those controls.

The same rule applies to future convenience methods and delegated-agent
helpers.

## Crash and retry semantics

The request hook and event subscription should support this sequence:

1. durable input event committed;
2. immutable request built;
3. request hook approves and retains an evidence ID;
4. dispatch intent durably records request digest and attempt;
5. provider call begins;
6. response/tool events are durably appended; and
7. attempt is marked complete.

After a crash, the host must expose enough state to distinguish:

- never dispatched;
- dispatch started, outcome unknown;
- response partially streamed but not durably completed; and
- durably completed.

The API should not claim exactly-once provider side effects where the provider
does not support idempotency. It should provide exact local evidence and an
idempotency key when the provider supports one.

## Security and privacy

- Callbacks receive capabilities, not ambient access to all secrets.
- Request evidence should support encrypted-at-rest content with plaintext
  digests/counts verified inside the trusted boundary.
- Tool and retrieval content remains untrusted unless separate authority is
  verified.
- Reasoning/private chain-of-thought fields should be omitted or redacted
  according to upstream policy without making false exact-content claims.
- Errors are bounded and must not echo credentials.
- Subscribers cannot mutate host events, request bytes, authority records, or
  durable cursors.

## Compatibility and versioning

Both public contracts need explicit schema versions. Additions that alter
request bytes, event ordering, durable/transient classification, authority
semantics, tokenizer identity, or bypass behavior require a compatibility
review and regression vectors.

Upstream should publish:

- an exhaustive event-class inventory;
- serialized golden vectors and intentional negative vectors;
- sync/async final-request vectors;
- retry and streaming vectors;
- callback ordering guarantees;
- crash/replay tests; and
- a deprecation window for contract changes.

## Acceptance tests

An implementation of this RFC should prove:

1. a callback exception cannot prevent host event persistence;
2. replay from every event boundary returns the same ordered event IDs and log
   heads without loss or duplication;
3. unknown event kinds reach strict subscribers and can pause dispatch;
4. tool results cannot complete the wrong call/tool/action group;
5. every sent request digest equals the approved immutable-body digest;
6. every retry with changed bytes requires a new approval;
7. sync, async, streaming, and resumed sessions produce the same request
   envelope for the same final inputs;
8. exact component counts sum to the exact total and replay under the bound
   tokenizer identity;
9. hard-limit overflow prevents network dispatch;
10. estimated accounting never serializes as exact;
11. `ask_agent()`, `execute_tool()`, and future convenience paths cannot bypass
    strict mode; and
12. failures retain evidence without asserting semantic completeness.

## Adoption path

1. Land the persisted-event envelope and replay cursor behind an experimental
   public namespace.
2. Land the immutable request envelope below the final provider transformation
   and add digest-equality assertions.
3. Route or disable bypass APIs under a strict conversation option.
4. Publish golden vectors and crash/retry tests.
5. Let integrations remove private source-hash hooks only after the public
   contract is stable and the exact pinned release passes compatibility
   review.

Until those hooks exist and are reviewed, `ctxc-openhands` will continue to
retain the live blocker, refuse request-driving APIs that escape the guarded
path, and limit exact accounting to its offline fake protocol.
