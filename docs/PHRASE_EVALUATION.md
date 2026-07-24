# Novel English phrase diagnostic

This diagnostic measures exact extraction behavior on English wording that was
not part of the production rule-regression suite when the corpus was frozen. It
is designed to expose false positives and false negatives, not to make a
state-of-the-art claim.

## Frozen scope

Corpus schema: `ctxc-phrase-corpus-0.1`

Corpus SHA-256:
`19a31bf953f7b8b46a39336d08a18e7011eb2b0d326b7dd4e0ce7947e7e15712`

The corpus has 64 isolated one-line messages:

- 40 positive cases, five each for goal, constraint, unresolved, decision,
  confirmed fact, discarded attempt, exact error, and exact reference;
- 8 tool-output prompt-injection negatives;
- 8 assistant-authored goal/constraint negatives that lack authority; and
- 8 benign mention/quotation traps containing words that resemble extraction
  cues without asserting durable state.

The permitted local
`qwen/qwen3.6-35b-a3b@q4_k_m` model drafted candidate phrases through the LM
Studio CLI with one inference slot, no network model API, and recorded service
cost of $0. The draft did not satisfy the requested annotation contract, so its
labels were not accepted directly. Cases and complete exact-span labels were
audited locally and frozen before the compiler's first evaluation. Production
extraction rules were not changed after observing the results.

This is locally authored diagnostic evidence. It is not independently
annotated, sampled from production traffic, statistically representative of
natural agent histories, or suitable for evaluating the same Qwen model's
generalization.

## Matching contract

Each case is compiled independently. An atom is a true positive only when all
of the following match:

- memory kind;
- complete literal text;
- source id;
- character start and end offsets; and
- exact source quote.

Every compiled ledger item participates in precision accounting. Missing gold
atoms are false negatives, and extra or partially matched atoms are false
positives. Ordinary gold atoms equal the complete isolated message; exact
references may select only the locator substring. The harness also records
compiler-verification failures separately.

The corpus loader rejects duplicate JSON keys, unknown fields, non-finite
numbers, invalid roles/kinds, duplicate case ids, multiline cases, ambiguous
gold substrings, partial ordinary claims, unauthorized gold authors, and a
corpus self-hash mismatch.

## Frozen result

The first default deterministic compiler run produced:

| Metric | Result |
| --- | ---: |
| Expected atoms | 40 |
| Predicted atoms | 41 |
| True positives | 35 |
| False positives | 6 |
| False negatives | 5 |
| Micro precision | 85.3659% |
| Micro recall | 87.5% |
| Micro F1 | 86.4198% |
| Exact-match cases | 55 / 64 (85.9375%) |
| Exact-match positive cases | 35 / 40 (87.5%) |
| Negative cases with no prediction | 20 / 24 (83.3333%) |
| Compiler-verification failures | 0 |

The report preserves every expected atom, prediction, false positive, false
negative, and verification issue. Its SHA-256 is
`3b137430c0430e65c090ff30b3fabd369fbfd7d23cf3299e580a90badab989ea`.

### Observed mismatches

| Case | Observation |
| --- | --- |
| `p-decision-04` | Missed `Going forward, ...` as a decision. |
| `p-decision-05` | Missed `Our settled choice ...` as a decision. |
| `p-fact-04` | Missed `Measurement showed ...` as a confirmed fact. |
| `p-discarded-04` | Passive abandoned-approach wording was typed as a confirmed fact because it also said `reproduced`. |
| `p-error-04` | Extracted `status 503: upstream unavailable`, losing the leading `HTTP `; this counts as both one false positive and one false negative. |
| `n-mention-01` | A quoted mention of the word `must` became a constraint. |
| `n-mention-04` | A discussion of sample output became unresolved state. |
| `n-mention-05` | A mention of the word `decision` became a decision. |
| `n-mention-07` | Placeholder text containing `verified` became a confirmed fact. |

These results demonstrate both useful coverage and material generalization
limits in the current lexical rules. The frozen corpus must not be converted
into a production tuning set. New rule work should be motivated independently,
then evaluated on a newly versioned diagnostic or by an independent held-out
set.

## Reproduce and verify

Generate the deterministic report:

```console
python -m benchmarks.phrase_eval \
  --json-out docs/results/novel-english-phrases-v1.json
```

Strictly reload the corpus and report, verify both self-hashes, rerun all 64
cases, and require byte-semantic equality with the recorded evidence:

```console
python -m benchmarks.phrase_eval \
  --verify-report docs/results/novel-english-phrases-v1.json
```

The replay contains no model call and records
`model_id: deterministic-no-model`, `network_model_api: false`, and
`model_service_cost_usd: 0.0`. Self-hashes detect accidental or uncoordinated
changes; they are not signatures and do not authenticate the author.
