# Natural-history evidence contract

This repository contains a strict, dependency-free contract kit for a future
held-out natural coding-agent history cohort. It does **not** contain a
collected natural cohort and does not complete the natural-history P0 evidence
gate. The bundled three-history data is explicitly marked
`contract-fixture`, every fixture result is retained as
`not-evaluated-contract-fixture`, `natural_history_claimed` is false,
`claim_ready` is false, and semantic completeness is never claimed.

The implementation is isolated in
`benchmarks/natural_history.py`. Its six Draft 2020-12 JSON Schemas are under
`benchmarks/schemas/natural-history/`, and the internally consistent golden
documents are under `benchmarks/data/natural_history/`. Runtime validation is
performed by standard-library Python rather than a JSON Schema dependency.
The schemas document the interchange surface; the Python validator additionally
enforces cross-document, exact-span, grouping, and replay invariants.

## Standalone conformance

From the repository root:

```console
python -m benchmarks.natural_history
```

The default command strictly loads and cross-checks the corpus, both
annotations, adjudication, split, gold-free export, and report. A successful
fixture verification says only that the contract examples are internally
consistent. It does not authenticate reviewers, prove that a privacy or license
review occurred, establish preregistration time, or turn the fixture into
natural evidence.

Pass explicit files when reviewing a candidate cohort:

```console
python -m benchmarks.natural_history \
  --corpus candidate-corpus.json \
  --annotation annotator-a.json \
  --annotation annotator-b.json \
  --adjudication adjudication.json \
  --split split.json \
  --gold-free adapter-input.json \
  --report diagnostic-report.json
```

Exactly two annotation files are required. A canonical adapter input can be
generated with an atomic same-directory replacement:

```console
python -m benchmarks.natural_history \
  --corpus candidate-corpus.json \
  --split split.json \
  --export-gold-free adapter-input.json
```

The export contains every history and source plus repository/task grouping and
split assignment. It contains no annotation, adjudication, label kind, temporal
status, exactness, protected-state, or provenance-label fields.

## Contract chain

Every stored document has an independent explicit schema version and a
canonical SHA-256 over all fields except its own digest field. Unknown and
missing fields fail closed. JSON files enter through the shared benchmark
regular-file boundary: bounded UTF-8 bytes, physical line length, and nesting;
duplicate object keys, non-finite values, links, special files, and file
mutation during the read are rejected. Direct in-memory decoders apply the same
JSON depth and integer-digit limits plus bounded canonical bytes, scalar size,
and node count before self-hashing; cycles, shared containers, non-JSON values,
and invalid UTF-8 fail as `NaturalHistoryError`. Validated envelopes and
verified gold-free/report mappings expose read-only nested metadata, so later
in-process mutation cannot silently invalidate their checked state. UTC fields
must also name real calendar instants rather than merely matching a timestamp
shape.

The chain is:

1. `ctxc-natural-history-corpus-0.1`
2. two `ctxc-natural-history-annotation-0.1` documents
3. `ctxc-natural-history-adjudication-0.1`
4. `ctxc-natural-history-split-0.1`
5. `ctxc-natural-history-gold-free-0.1`
6. `ctxc-natural-history-report-0.1`

The annotation documents bind the corpus digest. Adjudication binds the corpus
and the sorted pair of annotation digests. The split binds the corpus.
Gold-free export binds the corpus and split. A report binds the corpus,
adjudication, and split. These are integrity links, not signatures or trusted
timestamps. A party able to replace and rehash the complete set can fabricate a
new internally consistent set, so a claim-bearing freeze still needs an
independently retained digest or signature and a review record outside the
bundle.

## Corpus admission

The corpus also binds an intake-manifest digest and explicit collection
accounting. `attempted_history_count` must equal included histories plus
documented exclusions. Included ids must equal the corpus histories in
canonical order; every exclusion needs a unique id, stable reason code,
documented review state, and evidence digest. This prevents a validator from
silently discarding an attempted history inside the evidence chain. As with
the other self-hashed assertions, a real collection still needs the retained
intake manifest as an external audit input.

Every history has one explicit origin:

- `public` requires a syntactically canonical, non-empty SPDX license
  identifier, state `verified-compatible`, and a license-evidence SHA-256;
- `explicit-consent` requires state `authorized-by-consent`, consent state
  `documented`, and a consent-evidence SHA-256;
- `synthetic` requires both license and consent to be explicitly
  `not-applicable-synthetic` with no evidence digest.

There is no implicit or unknown state. Every history must also carry an
`approved` repository-publication privacy review with a reviewer id, UTC
review time, completed content-secret review, completed personal-data review,
and evidence digest. Pending, skipped, denied, or absent review state is
rejected. For every dated source, privacy review completion must be at or after
the source timestamp.

Those fields record reviewed assertions; a digest does not prove that the
license, consent, or privacy decision was correct. Before a real corpus is
accepted, retain the underlying license and consent evidence under appropriate
access controls, review source ids and timestamps as well as content, and
separately define retention, deletion, backup, and breach workflows. The
existing fixed content-secret detector is not comprehensive PII or
domain-sensitive-data detection.

Each history belongs to one repository, task group, and task and contains a
non-empty ordered source list. Source ids are unique inside the history.
Sequences must be contiguous from zero and match array order, making a missing
or reordered event explicit. Source content, total content, history, source,
label, and provenance collections are bounded.

## Independent annotation and adjudication

Each annotation pass:

- covers every corpus history exactly once, including histories with zero
  labels;
- identifies a distinct annotator and distinct attestation digest;
- explicitly attests independent work and blindness to peer labels;
- uses the fixed typed-memory vocabulary and temporal statuses;
- derives `protected` and `exact` flags from the label kind rather than trusting
  arbitrary flags;
- cites one or more exact Python-character `[start, end)` spans;
- requires each quote to equal the immutable source slice and match its full
  UTF-8 SHA-256;
- requires label text to equal each cited quote;
- requires label ids to be unique across the full annotation envelope,
  rejects duplicate provenance spans, and requires spans in source-sequence
  then offset order.

Annotation completion cannot predate any corpus privacy review. The kit can
validate those assertions and distinct identities, but it cannot observe how
annotators worked. Operational collection must prevent annotators
from seeing one another's labels before both documents are durably closed.

Adjudication requires a third identifier, distinct attestation evidence, a
completion time no earlier than either annotation, a completeness attestation,
canonical coverage of every history, and a decision for every label in the symmetric
difference between the two semantic label sets. Consensus labels must survive.
Each disagreement must be accepted, rejected, or replaced. Final labels must
equal exactly the consensus plus the decisions; unreviewed disagreements,
silent label additions, and silent removals fail.

## Split and leakage controls

The split policy is fixed as
`repository-and-task-group-disjoint-v1`. Every history appears exactly once and
is assigned to `train`, `development`, or `test`; all three must be present.
No repository id and no task-group id may occur in more than one split. The
manifest must attest that assignments were frozen before tuning, that test gold
is sealed, and must bind a seed commitment plus creation time.

These fields make violations detectable in the document; their timestamps and
attestations are self-reported. A real held-out run must anchor the split
manifest after all corpus privacy reviews and before rule, prompt, threshold,
or adapter tuning, restrict test-gold
access outside the repository, and record any access as a protocol violation
requiring a new cohort.

The gold-free export is rebuilt deterministically from the corpus and split.
Verification requires exact equality with that rebuild. All history ids and
source events remain present, while all label-bearing structure is forbidden.
This prevents structural label leakage; it does not prevent the original
history text itself from containing naturally occurring words such as
“constraint” or “error.”

## Report failure policy

The report contract retains every corpus history in canonical order. A history
is either:

- `scored`, with a complete non-negative metric record; or
- `failed`, with a stable failure code and all scored metrics set to `null`.

A failed history cannot be partially scored. Run timestamps must be real UTC
instants, cannot finish before they start, and cannot start before the bound
adjudication completed or split was created. Expected, protected, exact, split,
and source-character counts are recomputed from the adjudication, split, and
corpus. Summary counts are recomputed across all histories. Failed histories
remain in the denominator with zero matched labels; they cannot be dropped,
converted to successes, or hidden by a rehashed summary. Reports remain
`diagnostic-only`, `claim_ready: false`, and
`semantic_completeness_claimed: false`.

## Freeze workflow for real evidence

The tooling is ready for contract diagnostics, not a freeze by itself. A real
candidate cohort should follow this order:

1. define collection, license, consent, privacy, retention, and access policies;
2. collect complete histories without silently filtering parser failures;
3. review and minimize content and structural metadata before repository
   publication;
4. create the self-hashed corpus and anchor its digest externally;
5. freeze repository/task-group splits before any tuning and seal test gold;
6. complete two operationally independent full annotation passes;
7. adjudicate every disagreement without changing the frozen source corpus;
8. export only the verified gold-free document to systems under evaluation;
9. retain every scored and failed history in the report;
10. independently reproduce the validation and retain external anchors.

Only after collection, review, sealed evaluation, zero observed protected and
exact misses, separately reported uncertainty, downstream task evidence, and
the other release gates pass may project status documents be updated. Even then,
the result is evidence for the bounded frozen cohort, not a universal proof of
semantic completeness.

## Remaining evidence blockers

The current repository still lacks:

- a collected, licensed or consented multi-repository natural cohort;
- externally retained license, consent, privacy, and preregistration evidence;
- operational proof of independent annotation and sealed-test access control;
- measured agreement statistics and a reviewed adjudication handbook;
- a completed compiler or external-system run over a sealed natural test split;
- confidence bounds, real-token compression, downstream completion, and
  independent reproduction on that cohort.

The golden fixtures therefore must not be cited as completing TODO P0-E3 or as
support for a natural-history, no-loss, downstream-completion, or external
superiority claim.
