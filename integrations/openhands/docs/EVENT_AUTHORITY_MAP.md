# OpenHands event and authority map

This document is the closed event inventory implemented by
`ctxc_openhands.event_map` for the only reviewed host identity:

- OpenHands `1.8.0` at
  `bc26df351dd5d833a95131556dbe2da69af82253`; and
- OpenHands SDK `1.27.0` at
  `904279edf2df5fa12d7caecc7576f62659b2e2dd`.

It describes the 18 exact class names in `SUPPORTED_EVENT_KINDS`. It is not a
compatibility claim for another revision, a semantic-completeness claim, or a
claim that a live OpenHands run has succeeded.

## Fail-closed rules

Host `source` and nested message `role` values are serialized claims, not
authentication. Without a separately issued and successfully verified
event-bound authority receipt, every event is recorded with
`authenticated=false` and `trusted_for_state=false`. A source or role literal
never promotes its own authority.

The mapper accepts only the exact classes and fields reviewed here:

- an object event's concrete class name must equal its serialized `kind`;
- unknown event classes, unknown `kind` values, missing required event-schema
  fields, and unexpected event-schema fields fail closed;
- the same exact-field rule applies to the closed nested message, content-item,
  and tool-call shapes; and
- expressly opaque payload slots such as `observation`, `value`, `hook_input`,
  `raw_input`, and `raw_output` may contain application JSON, but that JSON is
  bounded, canonicalized, and never interpreted as extra event-schema fields.

Every accepted event is canonical JSON no larger than 1,048,576 bytes, with a
maximum depth of 32, 50,000 total JSON items, 10,000 members in any one
collection, and 262,144 characters in any one string. Common fields require an
exact `kind`, a non-empty `id` of at most 512 characters, an ISO-8601
`timestamp` of at most 128 characters, and one reviewed `source`. Strict text
fields reject surrounding whitespace and control characters. The table calls
out tighter or structural bounds.

`Durable` means `OpenHandsSession.ingest()` appends the canonical event to
immutable source history. `Refused transient` means the mapper validates the
known derived event, then ingestion raises `TransientEventError`; it is not
silently dropped. `Model-facing` and `auxiliary` below are the mapper's
`model_visibility` classifications. That classification does not grant
authority.

## Authority and state promotion

A receipt is eligible only for a model-facing, non-transient event. It must
verify against the configured issuer secret and allowlists and bind the exact
canonical event digest, event ID, event kind, session, claimed role, issuer,
and optional tool name. A rejected or mismatched receipt fails the event.

Known tool names may receive a compatible tool receipt. Unknown tool names stay
in the `generic` category and cannot receive authority. `ObservationEvent` is
the only event kind eligible for `trusted_for_state=true`, and then only when
the verified receipt:

- claims the `tool` role;
- binds the event's exact reviewed tool name; and
- names a tool independently allowlisted for trusted state by that issuer.

All other event kinds are ineligible for trusted-state promotion, even if they
can carry an ordinary authenticated receipt.

## Closed 18-class inventory

| Exact class | Retention / visibility | Canonical source and role handling | Authority and state eligibility | Atomic behavior | Reviewed fields and useful bounds |
| --- | --- | --- | --- | --- | --- |
| `MessageEvent` | Durable; model-facing | `source` may claim `agent`, `user`, `environment`, or `hook`. Without a receipt, nested `role=tool` maps to core role `tool`; `assistant`, `system`, and `user` map to core role `assistant`. A verified receipt may supply only the compatible nested role; `system` accepts a verified `system` or `developer` role. | Untrusted by default. A compatible receipt may authenticate it. Tool-role messages require a reviewed tool name to receive authority. Never trusted for state. | Assistant messages with `tool_calls` declare one incomplete `llm-call` group per unique call ID. A tool-role message declares the matching `llm-result`. Incomplete, duplicate, reversed, or mismatched LLM groups block compaction. Other messages have no atomic group. | Requires exact `llm_message`. Nested role is one of `assistant`, `system`, `tool`, `user`; content has at most 10,000 exact text/image items; text is at most 262,144 characters; an image item has at most 256 URLs of at most 16,384 characters each. Assistant-only `tool_calls` has 1 to 256 unique calls; each call has an ID, name of at most 256 characters, JSON-object argument string of at most 262,144 characters, and reviewed origin. Tool-role messages require call ID and name. |
| `SystemPromptEvent` | Durable; model-facing | Requires claimed source `agent`; unauthenticated core role is `assistant`. A compatible receipt may set verified role `system` or `developer`. | Untrusted by default; compatible receipt allowed. Never trusted for state. | None. | Requires one exact text/image `system_prompt` item and a `tools` array of at most 10,000 items; optional `dynamic_context` remains within global JSON bounds. |
| `ActionEvent` | Durable; model-facing | Requires claimed source `agent`; core role is `tool`, independent of that claim. | Untrusted by default. A tool receipt is allowed only for an exact reviewed tool name. Never trusted for state. | Ordinary atomic `call`. It remains incomplete until one later `ObservationEvent`, `UserRejectObservation`, or `AgentErrorEvent` matches call ID and tool name, plus action ID where the result shape carries one. An incomplete group blocks compaction. | Requires `thought`, `tool_name`, `tool_call_id`, exact `tool_call`, and `llm_response_id`. Tool name is at most 256 characters; call object ID/name must match the outer fields; arguments encode a bounded JSON object. Thought has at most 10,000 exact content items. A reviewed nested action kind must agree with the explicit tool category. |
| `ObservationEvent` | Durable; model-facing | Requires claimed source `environment`; core role is `tool`. | Untrusted by default. A reviewed exact tool receipt may authenticate it. This is the only class eligible for independently allowlisted `trusted_for_state=true`. | Ordinary atomic `result`. Ingestion requires exactly one retained matching `ActionEvent`; call ID, tool name, and action ID must match. | Requires `tool_name`, `tool_call_id`, `action_id`, and an object `observation`. Tool name is at most 256 characters. A reviewed nested observation kind must agree with the explicit tool category. |
| `UserRejectObservation` | Durable; model-facing | Requires claimed source `environment`; core role is `tool`. `rejection_source` is separately restricted to `user` or `hook`, but is still only event data. | Untrusted by default. A reviewed exact tool receipt may authenticate it. Never trusted for state. | Ordinary atomic `result`; requires one retained `ActionEvent` with matching call ID, tool name, and action ID. | Requires call/tool/action IDs, bounded rejection reason of at most 262,144 characters, and rejection source `user` or `hook`. |
| `AgentErrorEvent` | Durable; model-facing | Requires claimed source `agent`; core role is `tool`. | Untrusted by default. A reviewed exact tool receipt may authenticate it. Never trusted for state. | Ordinary atomic `result`; requires one retained `ActionEvent` with matching call ID and tool name. The pinned error shape has no action ID to compare. | Requires tool name of at most 256 characters, call ID, and error text of at most 262,144 characters. |
| `Condensation` | Durable; model-facing | Requires claimed source `environment`; core role is `assistant`. | Untrusted by default; a compatible `assistant` receipt is allowed. Never trusted for state. | None. It records the host condenser event but does not replace or delete source history. | Requires `llm_response_id`. Optional forgotten IDs form a duplicate-free array of at most 10,000 IDs; summary is at most 262,144 characters; summary offset is a non-negative integer. |
| `CondensationRequest` | Durable; auxiliary | Requires claimed source `environment`; fixed core role is `assistant`. | Cannot carry authority; never trusted for state. | Classified as control `atomic_role=request`, but emits no tool or LLM call/result group. | Common fields only; no additional fields are accepted. |
| `CondensationSummaryEvent` | Refused transient; model-facing | Requires claimed source `environment`; mapping role is `assistant`, but no durable record is appended. | Cannot carry authority because it is transient; never trusted for state. | None. | Requires summary text of at most 262,144 characters. |
| `ConversationStateUpdateEvent` | Durable; auxiliary | Requires claimed source `environment`; fixed core role is `assistant`. Its state-like name does not establish trusted state. | Cannot carry authority; never trusted for state. | None. | Requires key of at most 512 characters and a canonical bounded JSON value. |
| `ConversationErrorEvent` | Durable; auxiliary | Requires claimed source `environment`; fixed core role is `assistant`. | Cannot carry authority; never trusted for state. | None. | Requires strict error code of at most 128 characters and detail of at most 262,144 characters. |
| `HookExecutionEvent` | Durable; auxiliary | Requires claimed source `hook`; fixed core role is `assistant`. Hook output and the source claim remain untrusted. | Cannot carry authority; never trusted for state. | None. Optional action/message IDs do not create an atomic group. | Requires one of six reviewed hook types, non-empty command of at most 16,384 characters, boolean success/blocked values, signed 32-bit exit code, and stdout/stderr of at most 262,144 characters each. Optional reason/context/error have the same string bound; IDs are at most 512 characters; `hook_input` is a bounded JSON object. |
| `LLMCompletionLogEvent` | Durable; auxiliary telemetry | Requires claimed source `environment`; fixed core role is `assistant`. | Cannot carry authority; never trusted for state. | None. | Requires filename of at most 4,096 characters, log data of at most 262,144 characters, and model/usage IDs of at most 512 characters each. |
| `ACPToolCallEvent` | Durable; auxiliary start/progress visualizer trajectory telemetry | Requires claimed source `agent`; fixed core role is `tool`. The pinned class is an `Event`, not an `LLMConvertibleEvent`; its source, title, status, content, and tool-shaped data remain untrusted telemetry. | Cannot carry authority; never trusted for state. | None. It is not an ordinary call or result and emits no atomic group. It cannot complete an `ActionEvent` group or make an `ObservationEvent` with the same call ID complete; that ordinary result still requires a retained matching `ActionEvent`. | Requires call ID, title of at most 16,384 characters, and boolean `is_error`. Optional status/tool kind are at most 256 characters; raw input/output are bounded JSON; content is a bounded JSON array of at most 10,000 items. |
| `StreamingDeltaEvent` | Refused transient; auxiliary stream data | Requires claimed source `agent`; mapping role is `assistant`, but no durable record is appended. | Cannot carry authority; never trusted for state. | None. | Optional content and reasoning content are each at most 262,144 characters; no other variant fields are accepted. |
| `TokenEvent` | Refused transient; auxiliary telemetry | `source` may claim `agent`, `user`, `environment`, or `hook`; mapping role is `assistant`, but no durable record is appended. | Cannot carry authority; never trusted for state. | None. | Requires prompt and response token-ID arrays, each with at most 10,000 non-negative signed-32-bit-range integers. |
| `PauseEvent` | Durable; auxiliary control | Requires claimed source `user`; fixed core role is `assistant`. The source claim is not user authentication. | Cannot carry authority; never trusted for state. | None. | Common fields only; no additional fields are accepted. |
| `InterruptEvent` | Durable; auxiliary control | Requires claimed source `user`; fixed core role is `assistant`. The source claim is not user authentication. | Cannot carry authority; never trusted for state. | None. | Common fields only; no additional fields are accepted. |

## Review synchronization

The inventory must remain exactly equal to `SUPPORTED_EVENT_KINDS`. Any pinned
host upgrade or event-policy change requires a result-blind compatibility
review, matching positive and negative tests, and an update to this map. Do not
add a permissive fallback row.
