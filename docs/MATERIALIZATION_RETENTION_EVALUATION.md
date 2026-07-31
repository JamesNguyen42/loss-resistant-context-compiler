# Materialization retention evaluation

`ctxc evaluate-materialization` is a deterministic, dependency-free structural
diagnostic for the installed `materialize_context()` consumer path. It compares
three representations of the same ordered coding history:

1. `full_history` keeps every raw source in order;
2. `tail_only` keeps one largest bounded contiguous recent suffix plus the
   current turn;
3. `lrcc_materialized` uses the existing verified memory, recent raw messages,
   and current turn returned by `materialize_context()`.

All arms use the fixed `unicode-codepoint-count-v1` profile and the same fixed
input, reserve, margin, and hard-limit configuration. The resulting counts are
exact Unicode code-point planning units, not provider-token counts. No request
is sent and a host must still recount the final immutable provider request with
its exact tokenizer.

## Pack and split boundary

The installed pack contains 30 project-authored synthetic-naturalistic coding
histories in 15 task groups. The grouped split was fixed before the first
evaluation result:

- four train cases in two groups;
- six development cases in three groups;
- twenty held-out cases in ten groups.

Each history has an ordered final user turn, a distant superseded value and
explicit correction, exact identifiers, paths, numbers or errors, a recent
detail, and assistant/tool instruction-shaped content that must not become
authoritative semantic state. Two held-out cases intentionally exceed the
mandatory current-turn allocation and must remain recorded as refusals.

The pack is bundled as immutable package data, is validated against one
reviewed raw SHA-256 and a canonical self-digest, and is never written by the
evaluator. There is no external-pack or per-case-filter option. Changing a
fixture after observing a report is prohibited; a genuine fixture correction
requires a new pack id, digest, and review rather than replacement of this
pack.

These are visible, project-authored fixtures. They are not a collected natural
cohort, are not operationally blinded or independently adjudicated, and do not
close the natural-history evidence work in `TODO.md`.

## Measurements

Every case and arm retains integer-only measurements for:

- content, fixed-input, output-reserve, safety-margin, occupied, and hard-limit
  planning units;
- corrected, superseded, identifier, path, number, and detail atom ids that
  were retained or missing;
- exact current-turn occurrence count and last-position status;
- included and omitted source ids plus ordered-set digests;
- refusal or failure outcome and stable reason;
- exact omission accounting.

`planning_units` always describes the content actually represented by that
arm. A refused LRCC arm emits no context, so its content count is zero.
`attempted_planning_units` is otherwise null and, for a refusal, separately
records the attempted protected-memory reservation plus the minimum recent tail
and current turn. Aggregate attempted totals are kept separate from emitted
content totals.

The LRCC arm additionally binds its receipt, materialization, bundle, protected
state, and omission digests. Authority checks use selected live semantic-state
provenance, where both active and unresolved-conflict items remain live under
the compiler's state model. Assistant or tool text may remain visible in a raw
tail or exact-evidence item, but it cannot provenance-anchor an authoritative
goal, constraint, correction, decision, confirmed fact, or unresolved state.
This fixture role mapping does not authenticate actor identity. Full-history
and tail-only raw text are not mislabeled as authoritative memory, so the
presence of a superseded value in those raw arms is recorded rather than called
an authority violation.

Retention counts are measurements, not pass thresholds or winner rules.
Integrity fails only for malformed or changed pack/report bytes, case loss or
reordering, accounting or partition disagreement, current-turn violations,
authority promotion, or an expected-outcome mismatch. Every modeled
`ContextWindowError` refusal remains in the report. An invalid invocation,
malformed input, I/O failure, or unexpected post-materialization verification
failure aborts with exit 2 instead of being converted into a structural result.

The current deterministic all-split outcome is red. All 28 cases whose
predeclared outcome was `accepted` instead refuse with
`compiled_memory_not_verified`; the two predeclared
`mandatory_components_do_not_fit` cases refuse for that exact reason. An
all-split report therefore has `integrity_passed=false`, and the command returns
exit code 3 after writing the complete report. This observed product limitation
is not converted into a passing expectation or used to alter the pack.

## CLI

Run the held-out split and reserve a new output path:

```console
ctxc evaluate-materialization --split heldout -o retention-report.json
```

The current frozen pack writes a report and returns 3 because its structural
expectations are not all met. Exit 2 is reserved for an invalid invocation or
an unreadable, malformed, or unverifiable input/output condition.

Verify a retained report against an independently recorded digest:

```console
ctxc evaluate-materialization \
  --verify-report retention-report.json \
  --expected-report-sha256 EXPECTED_SHA256
```

Reports are compact canonical JSON with a self-digest and contain no raw
history text, gold literals, timestamps, host paths, platform identity, timing,
or floating ratios. They explicitly retain `inference_status=not_run`,
`retrieval_status=not_run`, `provider_execution_ready=false`, and
`final_provider_recount_required=true`.

## Claim boundary

This diagnostic provides structural retention and planning-unit evidence only.
It does not evaluate model answers, downstream task completion, semantic
completeness, external retrieval, provider admission, or comparative
superiority. Zoom or other retrieval output remains outside LRCC authoritative
memory and is not simulated by this pack.
