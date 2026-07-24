# Unique-literal model extraction

`LiteralModelExtractor` is an opt-in provider-neutral model boundary that
derives provenance offsets deterministically. It retains the project's
exact-source-literal contract; it is not an abstractive or paraphrase
acceptance path.

The original `ModelExtractor` remains available and unchanged. Its response
schema requires model-supplied `start` and `end` offsets. The first frozen
exact-Qwen corpus run showed why a separate mode is useful: 28 candidates cited
past the source boundary and another 26 did not equal their cited literal.
Those results remain immutable evidence in
[the Qwen phrase audit](QWEN_PHRASE_EVALUATION.md); this new interface was
designed after that run and does not retroactively improve its metrics.

## Response contract

The model returns exact text and source identities, but no coordinates:

```json
{
  "items": [
    {
      "kind": "constraint",
      "text": "Do not change the public API.",
      "exact": false,
      "priority": 90,
      "confidence": 0.95,
      "source_ids": ["source-0"]
    }
  ]
}
```

The strict schema is
[`model-extraction-literal.schema.json`](../schemas/model-extraction-literal.schema.json).
`source_ids` must be a nonempty list of unique, nonempty strings. A candidate
cannot also supply `provenance`, `source_id`, `start`, `end`, explanations, or
another undeclared field.

For each cited immutable `SourceRecord`, the validator:

1. removes only leading and trailing whitespace from candidate text with
   Python `str.strip()`;
2. performs exact, case-sensitive code-point matching with no Unicode
   normalization, case folding, or internal whitespace collapse;
3. requires exactly one possibly-overlapping occurrence in that source;
4. derives Python character offsets from that occurrence;
5. reconstructs and hashes a `ProvenanceSpan` from the source;
6. applies the existing kind, exactness, authority, uncertainty, reserved-tag,
   priority, confidence, and semantic-support checks;
7. requires the derived literal to be a complete atomic clause, including for
   `exact_error` and `exact_reference` in this stricter mode.

When multiple sources are cited, the same text must occur uniquely in every
one. All citations remain subject to role-authority checks. The candidate id
commits to the derived source ids and offsets, not to model-supplied
coordinates.

Repeated text is deliberately ambiguous even when every occurrence has the
same spelling. The extractor does not guess an occurrence, choose the first
one, use fuzzy matching, or ask the model for a tie-breaking number. An
integration that needs repeated literals should retain the original
coordinate schema or split input into smaller immutable source records.

## Resource and failure boundaries

The inherited limits bound response characters, conservative decoded JSON
size, and candidate count. `max_locator_work_chars` additionally bounds the
aggregate cited-source characters that could be scanned across one response;
its default is 10,000,000. The bound is checked before candidate decoding or
substring search. It is a deterministic work proxy, not a wall-clock or
process-RSS guarantee.

Use the extractor through `ContextCompiler`, which applies the shared source
count and canonical-size limits before calling it. Direct callers that bypass
the compiler remain responsible for bounding the already allocated source
list.

Absent, repeated, forged, ambiguous, non-atomic, or unauthorized literals are
candidate rejections. If every candidate is unusable, the result is marked
degraded. Normal compiler policy then retains verified deterministic recovery
and emits `primary_extractor_degraded`; strict primary-extractor policy aborts
instead. The extractor never turns a paraphrase into provenance.

## Usage with the allowed local Qwen adapter

```python
from context_compiler import (
    ContextCompiler,
    LiteralModelExtractor,
    LmsQwenCompletion,
)

complete = LmsQwenCompletion(
    r"C:\path\to\.lmstudio\bin\lms.exe",
    timeout_seconds=120,
)
compiler = ContextCompiler(
    extractor=LiteralModelExtractor(
        complete,
        model_id=complete.model_id,
        max_response_chars=200_000,
        max_candidates=32,
        max_locator_work_chars=10_000_000,
    )
)
memory = compiler.compile(sources)
```

The completion callable remains part of the confidentiality boundary. The
local LM Studio adapter passes prompts as process arguments. Run approved
content-secret preprocessing before this boundary, minimize metadata and
source ids separately, and do not treat unique-literal derivation as
redaction.

## Post-hoc offset ablation

The saved outputs from the first exact-Qwen coordinate run can be transformed
without calling a model:

```console
python -m benchmarks.qwen_literal_ablation \
  --verify-report docs/results/qwen-literal-offset-ablation-v1.json
```

The analyzer first verifies the frozen source report with SHA-256
`db2e054537e366056a8fd482f1a8e17b8fa0d02163a08ea79ef3c682af79c171`.
For every strict captured candidate, it retains kind, text, optional candidate
fields, and cited source ids; discards only integer coordinate values; then
replays the transformed response through `LiteralModelExtractor` and the full
compiler. Repeated source ids are stable-order deduplicated and counted. No
captured candidate, kind, text, or source identity is invented.

The reviewed
[`qwen-literal-offset-ablation-v1.json`](results/qwen-literal-offset-ablation-v1.json)
report has SHA-256
`a321f20c4a7c13f76a99ab85e7af699f02f9498b11a59f56278991c9cf0de97c`.
It records `claim_bearing: false`, `target_prompt_evaluated: false`, zero live
model calls, no network model API, and USD 0.00 model-service cost.

| Boundary | Result |
| --- | ---: |
| Captured / transformed candidates | 65 / 65 |
| Coordinate pairs ignored | 65 |
| Accepted / rejected literal candidates | 38 / 27 |
| Literal-only atoms | 24 TP, 14 FP, 16 FN |
| Literal-only precision / recall / F1 | 63.1579% / 60% / 61.5385% |
| Deterministic recovery additions / true positives | 22 / 16 |
| Final atoms | 40 TP, 20 FP, 0 FN |
| Final precision / recall / F1 | 66.6667% / 100% / 80% |
| Final exact matches | 46 / 64 |
| Final verification failures | 2 |

Compared with the coordinate validator, literal validation admitted 36 more
candidates and gained 22 true positives, but also admitted 14 false positives
and lost 36.8421 percentage points of model-only precision. The two final
verification failures were single-source model-labeled `confirmed_fact`
items without confirmation evidence. This result supports the narrow
engineering diagnosis that coordinate arithmetic caused many original
rejections; it simultaneously shows that semantic choice and type assignment
remain unresolved.

## Claim boundary

Unique literal location removes an unnecessary coordinate-generation task from
the model. It does not establish that the model chose the right kind, found
every durable item, understood temporal state, or copied the intended complete
literal. The atomicity and role checks are conservative syntactic policies,
not a semantic proof.

The completed 64-case report was observed before this interface existed, so it
must not be reused as preregistered evidence for the new prompt. A new
claim-bearing comparison requires a separately frozen corpus, report schema,
prompt digest, model identity, and analysis plan. Post-hoc replay or smoke
testing against the old corpus must be labeled exploratory.

One post-hoc public-example smoke run completed with 10 admitted model items, 1
rejection, 4 deterministic recoveries, and a verified 14-item ledger. Its raw
response was not retained, so it demonstrates only that the live local adapter
and new schema interoperate. It is not replayable quality or performance
evidence.

The replayable offset ablation above is stronger diagnostic evidence than that
smoke test, but it is still post-hoc and never sent the unique-literal prompt
to Qwen. Current evidence therefore supports only the engineering statement
that unique, exact source literals can be converted into hashed
Python-character spans without trusting model arithmetic. It does not support
provider generalization, production readiness, downstream task improvement,
or a comparison with another memory system.

A disjoint corpus and paired coordinate-versus-literal live procedure are now
frozen in the
[held-out paired Qwen protocol](QWEN_PAIRED_EVALUATION.md). No target result
has been observed at this checkpoint, so it does not yet change the evidence
claims above.
