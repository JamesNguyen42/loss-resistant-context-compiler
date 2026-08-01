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

## Fixed degradation comparison

`ctxc evaluate-materialization-degradation` reuses the exact immutable 20-case
held-out split, budget, tokenizer profile, source bytes, and gold atom spans.
It has no pack, case, budget, threshold, or arm override. The fixed arm order
is:

1. `strict`, the ordinary no-policy public materialization path;
2. `lossless_compact_only`, an evaluator-private stop after the exact compact
   attempt, with no reallocation or new public policy;
3. `lossless_compact_then_single_reallocation`, the existing public opt-in
   policy.

The compact-only arm first preserves any strict success. After the exact
compiled-memory overflow only, it invokes the existing compact compiler path.
If compact succeeds, the evaluator requires the public policy to produce the
same prototype bytes and uses that verified public result. Its receipt digest
must match a second deterministic execution; that replay binding is not an
externally retained receipt anchor. If compact still overflows, its exact
refusal is retained; it never
crosses the reallocation boundary.

At the fixed held-out budget, strict and compact-only each accept zero cases:
18 refuse with `compiled_memory_not_verified`, and two refuse with
`mandatory_components_do_not_fit`. The public ladder accepts those 18
memory-overflow cases and retains the same two mandatory refusals. All 18
accepted results preserve every required active or protected exact atom,
correction precedence, protected retention, the authority boundary, exact
source partition, one final current turn, omission inventory, deterministic
canonical bytes, and the second-execution receipt binding. Required and
protected atoms match only through their frozen source id and exact character
span or through that same exact raw source; equal text in another source cannot
satisfy retention.

The first evaluation-spec preflight failed. Spec digest
`60bc8d14985c8264bbb3430d40320d83fa1c9ec5839f5df77d5552b28d8a0e2d`
treated the older pack's strict expected outcomes as a current integrity gate;
its failed report digest was
`370df110ad65914443fa681bed2cd795b1f3921ac72ff5e1eec0694ac774ba75`.
Those historical outcomes and all fixture/oracle bytes remain unchanged. The
failed report bytes were not retained, so its spec and report digests form a
digest-only failed-preflight record. The replacement spec records historical
accepted-outcome matches as measurements while the two predeclared mandatory
refusals remain integrity gates. Other integrity checks cover deterministic
bytes, exact source-span retention, authority, partition, current-turn, and the
second-execution receipt binding.

The corrected fixed spec has SHA-256
`6473ddd7b9b941a644032564ebc235040439291693df8d62e31eb07faed89525`.
Generated reports are not tracked as frozen evidence. Retain the exact
canonical report bytes outside the source tree and supply the expected
embedded report digest independently when replaying them. The digest binds a
structural report; it is not a signature or provider evidence.

The held-out full-ladder outcomes had already been exercised by regression
tests, and compact-only was observed in the failed preflight before this report
was formalized. The report says so and does not call the comparison newly
unseen, independently blinded, or a natural cohort.

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

Run and verify the fixed degradation comparison with separate output files:

```console
ctxc evaluate-materialization-degradation -o degradation-report.json
ctxc evaluate-materialization-degradation \
  --verify-report degradation-report.json \
  --expected-report-sha256 EXPECTED_SHA256 \
  -o degradation-report-verified.json
```

The degradation command returns 0 only when its integrity invariants pass. A
modeled refusal remains a report row rather than a process failure. Exit 2 is
reserved for invalid invocation, malformed/unverifiable input, I/O failure, or
an unexpected evaluator failure.

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
