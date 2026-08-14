# Loss-resistant Context Compiler project handoff

Last updated: 2026-08-14

This document is the transfer entry point for the current LRCC performance
branch and its stacked pull request. It focuses on the two final performance
commits, the invariants they preserve, their evidence, the present CI state,
and the work a new maintainer must complete. The repository already has
substantially deeper project documentation:

- [README](../README.md) explains the product, interfaces, evidence, and limits.
- [Full project handoff](HANDOFF.md) records the broader implementation and
  research state.
- [Architecture and invariants](ARCHITECTURE.md) defines the complete pipeline.
- [TODO and roadmap](../TODO.md) is the ordered backlog.
- [Benchmarking](BENCHMARKING.md) defines claim eligibility.
- [Threat model](THREAT_MODEL.md) and [redaction](REDACTION.md) define safety
  boundaries.
- [OpenHands integration](../integrations/openhands/README.md) documents the
  separately packaged draft adapter.

## Executive state

The Loss-resistant Context Compiler turns immutable agent-history events into
typed working memory with exact source provenance. Its core is
standard-library-only and model-agnostic. It extracts candidate state, resolves
temporal relationships, selects active memory under a token budget, and
independently verifies that protected commitments and exact evidence survived.
It emits a compact prompt plus a complete, replayable audit artifact.

The current branch speeds up two large-input operations:

1. independent protected-retention verification for exact built-in memory
   atoms; and
2. containment deduplication for large reference-match sets.

Both optimizations are guarded fast paths. When their exact assumptions cannot
be proven, they execute the legacy behavior. This is important because LRCC's
verification limits, custom-object behavior, monkeypatch seams, source offsets,
and deterministic error order are observable safety behavior, not incidental
implementation details.

The recorded synthetic A/B showed substantial improvements with matching
outputs, but this is not a production, natural-history, downstream-agent, or
external-system result. The project's original 50% superiority target remains
unestablished.

## Git and pull-request topology

| Item | Value |
| --- | --- |
| Repository | [JamesNguyen42/loss-resistant-context-compiler](https://github.com/JamesNguyen42/loss-resistant-context-compiler) |
| Working branch | `codex/lrcc-five50-final` |
| Draft pull request | [PR #7, Speed up LRCC verification and reference extraction](https://github.com/JamesNguyen42/loss-resistant-context-compiler/pull/7) |
| Stacked base branch | `codex/lrcc-cycle2-40` |
| Base reference commit | `f2dbc3fd5d46dfc44f8d329b1d72979ff8ecdbd7` |
| Performance reference head before handoff documentation | `80915db5bfa807fad65552f53ef5d5639b300fc4` |
| Reference tree | `21d191afbd0ab5480b150f258fd07941459d2a05` |

The performance diff from the stacked base contains exactly two commits:

| Commit | Purpose | Files |
| --- | --- | --- |
| `0bd5c184052828d3bbc21d0ee9719f87257bb957` | Index the normal exact protected-retention check while preserving the legacy verifier fallback | `src/context_compiler/verifier.py`, `tests/test_verifier_exact_retention.py` |
| `80915db5bfa807fad65552f53ef5d5639b300fc4` | Replace quadratic containment scans with a guarded linear pass for ordinary large reference sets | `src/context_compiler/extractors.py`, `tests/test_reference_match_scaling.py` |

PR #7 is stacked on `codex/lrcc-cycle2-40`, not `main`. The base includes
earlier compiler work that is outside these two commits. Merge the base stack
first or deliberately transplant the two commits and rerun all validation.
Reviewing only the final documentation commit is insufficient.

## Architecture orientation

The ordinary core pipeline is:

1. **Source ingestion.** `SourceRecord` values retain immutable content,
   role, ordering, metadata, and source identity under explicit size/depth
   limits.
2. **Optional preprocessing.** Length-preserving content-secret redaction can
   run before extraction while retaining a bounded report.
3. **Primary extraction.** A rule, model, literal-model, or domain extractor
   proposes typed `MemoryItem` values with exact `ProvenanceSpan` objects.
4. **Independent recovery and certification.** A non-replaceable built-in
   `RuleBasedExtractor` restores missed recognized atoms and separately
   defines protected obligations.
5. **Canonicalization and temporal resolution.** Equivalent atoms are merged;
   corrections, revocations, conflicts, and resolved questions retain explicit
   graph edges and audit history.
6. **Budgeted selection.** Protected active state is retained before optional
   context. Overflow is reported instead of silently losing commitments.
7. **Independent verification.** Provenance, literal exactness, authority,
   semantic support, temporal edges, protected retention, selection, and
   accounting are checked.
8. **Sealing and rendering.** Only a verified, digest-matching snapshot can
   render a normal prompt.
9. **Artifact/archive output.** The complete ledger can be replayed against
   trusted sources and separately anchored source archives.

Key code:

| Area | Primary files |
| --- | --- |
| Orchestration | `src/context_compiler/compiler.py` |
| Data model and coverage relation | `models.py` |
| Rule and model extraction | `extractors.py`, `model_extractor.py`, `literal_model_extractor.py` |
| Temporal state | `resolver.py`, `canonical.py` |
| Budgeting | `selector.py`, `tokenizer.py`, `limits.py` |
| Verification | `verifier.py`, `artifact.py`, `trust.py` |
| Prompt and context bundle | `prompt.py`, `context_bundle.py` |
| CLI and file transactions | `cli.py`, `io.py`, `archive.py` |
| Optional integration | `connector.py`, `localai_contracts_adapter.py`, `integrations/openhands/` |
| Evaluation | `benchmarks/`, `docs/results/` |

The optimized verifier function sits inside step 7. The optimized reference
matcher sits inside step 3. Neither changes the public artifact schema, typed
memory categories, authority rules, temporal resolution, selection priority,
prompt rendering, or connector protocol.

## Commit 1: exact protected-retention verification

### Previous behavior

`verify_memory()` checks that every protected candidate is covered by at
least one non-discarded retained item. The legacy
`_candidate_retained(candidate, retained)` scans retained items in order and
uses `memory_item_covers_candidate()`.

For `P` protected candidates and `R` retained items, the normal scan can
perform approximately `P * R` coverage checks. This became expensive for the
controlled fixture containing 2,000 retained exact items and 400 protected
candidates.

The coverage relation is more nuanced than a dictionary lookup:

- kinds must match;
- ordinary equal text is compared after edge trimming;
- the candidate's provenance-span set must be a subset of the retained set;
- non-exact ordinary atoms can use one carefully bounded label/bullet wrapper
  equivalence; and
- a supplied compilation work budget must be consumed in the same order and
  fail at the same point.

### Current fast path

`_exact_protected_retention()` builds an index only when the public verifier
can prove the simple exact relation is sufficient.

Eligibility requires:

- exact built-in `list` containers;
- exact built-in `MemoryItem`, `MemoryKind`, `ProvenanceSpan`, string,
  integer, and status values;
- every protected candidate to be intrinsically exact;
- the original coverage helper, candidate scan helper, relevant descriptors,
  `__getattribute__` methods, equality methods, and hash method;
- the original fast-path function itself; and
- no compilation work budget.

Eligible candidates are indexed by `(kind, text.strip())`. Each entry retains
the candidate index and its frozen provenance-span triples. The retained list
is scanned once:

- discarded items are skipped;
- matching kind/text candidates are found from the index;
- coverage succeeds when the candidate span set is a subset of the retained
  span set; and
- the result is recorded once per candidate.

The fast path snapshots the fields it reads and checks them again after the
scan. It also rechecks the helper and descriptor identities. If a value or
relevant behavior changed during inspection, it returns `None` and
`verify_memory()` performs the legacy scan.

### Why the fallback is mandatory

The legacy relation must still run when:

- a candidate is not exact and wrapper equivalence may apply;
- a custom/subclassed item, enum, status, text, integer, provenance, or list is
  supplied;
- a helper, descriptor, equality/hash method, or attribute access path is
  replaced;
- state changes while the fast path is observing it; or
- a work budget is present.

In particular, the compiler's internal verification path supplies a work
budget. It intentionally keeps the legacy operation-by-operation accounting.
The optimization targets the ordinary independent public verifier where no
budget is supplied.

Do not broaden eligibility merely because a new object compares equal. The
fast path is safe because it proves exact built-in semantics, not because it
assumes user objects behave conventionally.

### Regression coverage

`tests/test_verifier_exact_retention.py` contains five focused tests:

1. fast output equals a forced legacy run;
2. a work budget always selects the legacy scan and preserves its failure
   point;
3. a replaced coverage helper is observed through fallback;
4. a replaced enum inequality operation is observed through fallback; and
5. a missing exact commitment produces the same verification error.

## Commit 2: large reference extraction

### Previous behavior

`RuleBasedExtractor._reference_matches()` runs five ordered reference regexes
covering line-word references, GitHub anchors, Windows paths containing spaces,
pytest node IDs, and general references. It sorts all matches by full-match
start and descending full-match length.

The legacy deduplicator then considers each named `ref` span and calls:

`any(existing_start <= start and existing_end >= end for existing in unique)`

That preserves the first widest containing match, but repeatedly scanning all
previous accepted spans is quadratic for many non-overlapping references.

### Current fast path

The sorted order is unchanged. For ordinary exact match spans, the code tracks:

- the previous named-span start; and
- the furthest end of an accepted containing span.

When named-span starts are monotonic, an incoming span is contained if its end
does not extend beyond the furthest accepted end. This reduces the containment
pass to linear time after the existing sort.

The fast path is enabled only while:

- the active `any` is the exact built-in function;
- named-span starts and ends are exact built-in integers;
- spans are non-negative and non-inverted; and
- starts remain monotonic.

If any condition fails, the current and remaining matches use the original
`any`-over-accepted-spans behavior. The accepted `unique` mapping is shared,
so fallback continues from the exact state already produced.

The unqualified `sorted` call remains observable exactly once. The active
`any` is resolved during iteration so a replacement receives the same legacy
calls. Pre-import regex replacement, custom/stateful match methods, unmatched
named groups, and string subclasses are explicitly exercised.

### Invariants that must not be weakened

- Keep all five regex passes and their order.
- Keep the sort key based on full-match start and descending full-match length.
- Deduplicate based on the named `ref` span, not the full match.
- Preserve the insertion order and exact `re.Match` objects returned.
- Prefer the earlier/wider containing match exactly as before.
- Fall back if named spans are nonmonotonic even when full matches are sorted.
- Preserve dynamic built-in and regex-hook behavior covered by tests.
- Do not normalize, case-fold, rewrite, or guess reference text or offsets.

### Regression coverage

`tests/test_reference_match_scaling.py` contains ten focused tests:

1. 4,000 non-overlapping references match the legacy output;
2. repeated overlapping reference forms match the legacy output;
3. 2,000 seeded randomized differential cases match;
4. a replaced `any` sees the legacy calls and result;
5. a replaced `sorted` is dispatched exactly once;
6. custom stateful match methods retain access order;
7. a string subclass selects legacy deduplication;
8. nonmonotonic named spans from pre-import regex replacement fall back;
9. an unmatched named group falls back; and
10. a missing `any` is not resolved when there are no matches.

## Performance and functional evidence

The retained comparison is the same controlled five-operation synthetic A/B
used by ExpertPack and TokConductor. It exported exact baseline and final Git
trees, ran an isolated model-free parity preflight, then issued 20
counterbalanced serial Qwen requests, 10 per arm.

Key external records:

| Record | SHA-256 |
| --- | --- |
| `ab-pack-v1.json` | `b34fd5cfa29146e707c9b545c3216844872e1b65799b6e9b2944225ad453c8b2` |
| `ab-live-report-v1.json` | `159b06f0f7533f63659ed018a24001024b716597a0f74e1180698334988300df` |
| Reviewed runner bytes | `bc4a92905d6dc3a30bec0da65c311048414b9201680b860fc14bbb8d1d33eb5d` |

The LRCC baseline was
`f2dbc3fd5d46dfc44f8d329b1d72979ff8ecdbd7`; the final was
`80915db5bfa807fad65552f53ef5d5639b300fc4`.

Fixtures and exact output digests:

| Operation | Fixture | Result SHA-256 |
| --- | --- | --- |
| Exact verification | 2,000 retained items, including 400 protected candidates; verification passed and retained all 400 | `cac84ecbf16e39063c31a35343fdabfd3361c51b2b6eeff1714a180447265119` |
| Reference extraction | 4,000 non-overlapping exact references | `a7b3ea7a429f6c026f1bd21b7e86d3b7f162075f793bc197607b01f7c3cf637a` |

Observed medians:

| Operation | Baseline | Final | Reported improvement |
| --- | ---: | ---: | ---: |
| Exact memory verification | 128.3956 ms | 21.8716 ms | 82.965460% |
| Large reference extraction | 591.52195 ms | 155.7146 ms | 73.675601% |

Both arms passed 10/10 exact response checks. The model request policy used
`reasoning_effort=none`, temperature 0, seed 7, and a 256-token maximum.
Model timing was effectively unchanged.

### Claim boundary

These numbers support a narrow claim about the recorded machine, exact commits,
and fixed synthetic fixtures. They do not prove:

- production latency, memory, or scalability;
- performance for non-exact candidates, custom values, hook-modified behavior,
  work-budgeted compilation, or heavily overlapping/pathological matches;
- improved extraction quality or model generation quality;
- a contiguous end-to-end request improvement;
- direct LM Studio integration;
- an external-system comparison;
- natural-history generalization;
- downstream agent task completion; or
- the original 50% superiority target.

The combined host-plus-model figure in the external report is explicitly
non-contiguous and non-claim-bearing. Do not cite it as end-to-end latency.

The evidence bundle is not committed here and contains machine-specific
attestations. Transfer it privately with its hashes or construct a reviewed
sanitized package; do not paste the raw report into the repository without
reviewing host-local fields.

## Reproducing validation

### Focused tests

~~~powershell
python -m pytest -q tests/test_verifier_exact_retention.py tests/test_reference_match_scaling.py
~~~

A fresh local run on 2026-08-14 passed all 15 focused tests.

### Full repository checks

~~~powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests benchmarks scripts conformance _ctxc_build_backend.py
python -m compileall -q src benchmarks tests scripts conformance _ctxc_build_backend.py
git diff --check
~~~

The README and [full handoff](HANDOFF.md#restart-checklist) list the additional
benchmark, conformance, packaging, OpenHands, and report-replay checks.

### CI state at the performance reference head

As of 2026-08-14, [CI run 31787404066](https://github.com/JamesNguyen42/loss-resistant-context-compiler/actions/runs/31787404066)
for head `80915db5...` was red, but the test results need to be read precisely:

- the complete pytest suite passed on Python 3.11, 3.12, 3.13, and 3.14;
- the following Ruff step failed on all four jobs with `SIM102` at
  `src/context_compiler/extractors.py:953`;
- compileall was skipped in those jobs because Ruff failed;
- macOS and Windows Python 3.13 release/filesystem smoke jobs passed;
- the OpenHands offline package lanes passed on Ubuntu, Windows, and macOS for
  their configured Python 3.12/3.13 matrix;
- the six-lane package byte-reproducibility job passed; and
- retained-evidence and downstream benchmark jobs that depend on a fully green
  upstream CI state were skipped.

The Ruff issue is a style-only nested conditional in the legacy fallback:

~~~python
if not use_linear_deduplication:
    if current_any(...):
        continue
~~~

The likely minimal repair is the equivalent combined conditional. Preserve
short-circuiting so `current_any` is called only when the fast path is not in
use. Then rerun Ruff, focused tests, the complete test matrix, compileall,
release/filesystem smoke, and dependent jobs. Do not mark PR #7 ready while its
latest required CI run is red.

The handoff documentation commit changes the head and may trigger newer checks.
Always inspect the latest PR run rather than treating this dated snapshot as
current.

### Performance reproduction

The A/B builder and live runner are part of the separately retained evidence
bundle. A valid reproduction requires:

1. verified builder, runner, and pack hashes;
2. complete Git object graphs for the exact baseline/final commits;
3. exact Git-archive exports into isolated workers;
4. model-free parity of facts and public-operation digests;
5. the same fixed fixtures and request ordering;
6. a clean exclusive inference lease and reviewed runtime identity;
7. serial frozen requests and complete cleanup; and
8. publication to a new immutable report instead of overwriting evidence.

Repository unit tests prove behavioral equivalence on designed cases. They are
not substitutes for the timed A/B.

## Past bugs and repair patterns

### Optimizing a richer relation as if it were equality

**Risk:** a dictionary keyed only by kind/text can miss wrapper equivalence,
custom comparison behavior, or required work accounting.

**Repair:** optimize only intrinsically exact built-in atoms under unchanged
helpers and descriptors. Fall back for every richer case.

**Lesson:** in fail-closed code, eligibility proofs are part of the
optimization. A faster branch with broader assumptions is a semantic change.

### Losing work-budget behavior

**Risk:** a bulk index changes how many operations are charged and where a
bounded compile fails.

**Repair:** never select the verifier fast path when a work budget is present.

**Lesson:** resource accounting and failure coordinates are public behavior
when callers use them to bound untrusted work.

### Mutable or stateful objects changing during inspection

**Risk:** reading fields once into an index can accept a result even if custom
attribute access or concurrent mutation changes the source objects.

**Repair:** require exact built-in model objects and descriptors, snapshot
relevant fields, recheck them after the pass, and fall back if anything
changed.

### Assuming full-match sort implies named-span sort

**Risk:** the regex list is sorted by full-match coordinates while containment
uses the named `ref` group. Custom or pre-import-replaced patterns can produce
nonmonotonic named spans, invalidating the linear interval rule.

**Repair:** verify exact integer ranges and monotonic named starts during the
scan; switch to the legacy containment scan at the first mismatch.

### Treating built-ins and hooks as invisible

**Risk:** caching `any`, bypassing `sorted`, or changing match method call
order breaks existing observable behavior and diagnostic tests.

**Repair:** keep the single dynamic sort dispatch, validate the active
`any`, and preserve the legacy fallback for hook-modified cases.

### Running tests without the full quality gate

**Symptom:** every Python-version test suite passed, but CI was still red.

**Cause:** Ruff's `SIM102` check ran after pytest and compileall was skipped.

**Repair:** run formatting/static checks before or alongside expensive suites,
and inspect step-level conclusions instead of equating “tests passed” with
“CI passed.”

## Current risks and unfinished work

### Immediate PR work

1. Fix the Ruff `SIM102` finding without changing fallback call semantics.
2. Run Ruff and compileall locally before pushing.
3. Require a completely green latest CI run across Python 3.11-3.14.
4. Confirm the stacked base is still the intended review base.
5. Preserve or sanitize the external A/B evidence bundle for transfer.
6. Consider adding a repository-owned deterministic microbenchmark so future
   regressions can be detected without the external live harness. Keep timing
   thresholds generous and separate from correctness tests.

### Broader LRCC work

The broader project is feature-rich but remains an alpha research system. The
highest-value remaining work is evidence and integration, not relaxing current
invariants:

1. close the nine blockers in the frozen external-comparison protocol without
   inspecting comparative outcomes;
2. finish result-blind external adapters and run matched comparisons;
3. collect licensed or consented natural histories under the documented
   privacy, annotation, adjudication, and grouped-split contracts;
4. measure real downstream task completion;
5. keep OpenHands live execution blocked until the complete hash-pinned offline
   dependency closure and a stable exact final-request/tokenizer hook both
   exist;
6. retain all failed, refused, and verification-error outcomes instead of
   tuning them away; and
7. complete release and production hardening only after claim-bearing evidence
   exists.

Use [TODO](../TODO.md#recommended-next-work-package) and the
[full handoff](HANDOFF.md#exact-next-step) for the latest ordered backlog and
hard stop conditions.

## Transfer checklist

Before the new maintainer edits code:

- [ ] Read this file, README, full handoff, architecture, TODO, benchmarking,
      threat-model, and OpenHands documents.
- [ ] Record `git status -sb`, `git log -1 --format=fuller`, and the exact
      PR base/head.
- [ ] Inspect `git diff codex/lrcc-cycle2-40...HEAD`.
- [ ] Confirm whether the external A/B pack/report/runner were transferred.
- [ ] Verify their SHA-256 values before using any timing.
- [ ] Inspect the latest PR checks; do not rely on the dated CI snapshot above.

Before changing the verifier fast path:

- [ ] Identify whether exactness, built-in type identity, helper identity,
      descriptors, equality/hash behavior, mutation, or work budgeting changes.
- [ ] Keep the legacy scan available.
- [ ] Compare complete `VerificationReport.to_dict()` output against a forced
      legacy run.
- [ ] Test missing, discarded, wrapped, subclassed, patched, and budget-limited
      cases.

Before changing reference deduplication:

- [ ] Preserve regex order, sort dispatch, match-object order, named offsets,
      and containment precedence.
- [ ] Differential-test random and overlapping inputs against the legacy
      implementation.
- [ ] Exercise replaced built-ins and pre-import regex hooks.
- [ ] Retain the monotonicity/range checks and legacy fallback.

Before merging:

- [ ] Fix and verify Ruff `SIM102`.
- [ ] Pass all 15 focused regression tests.
- [ ] Pass full pytest on Python 3.11-3.14.
- [ ] Pass Ruff, compileall, release/filesystem smoke, package
      reproducibility, and `git diff --check`.
- [ ] Review the complete stacked diff.
- [ ] Keep performance wording tied to the exact synthetic fixtures.
- [ ] Do not claim external superiority, production readiness, natural-history
      generalization, downstream benefit, or end-to-end 50% improvement.
