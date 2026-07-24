# LRCBench

> **Evidence boundary:** an `ISSUED` report without external candidates proves
> only a local frontier over the bundled deterministic controls. It is not
> evidence that this project is 50% better than ACON, FoldAgent, AMA-Agent,
> MemIR, or most related technology.

LRCBench is a deterministic, provider-neutral benchmark for long-history
context compilers. It generates histories containing buried corrections,
conflicting requirements, duplicate function/file names, exact numeric
failures, unresolved questions, and gold-negative prompt injections in
untrusted tool noise.

The cases rotate through six lexical families, including replacement and
additive corrections, numeric/database/polarity conflicts, varied facts and
decisions, unresolved phrasings, diagnostics, and discarded attempts. These
are still generated templates rather than natural production prevalence.

Run it from the repository root with the package on `PYTHONPATH`:

```powershell
$env:PYTHONPATH = "src"
python -m benchmarks
```

The harness compares the compiler with matched-budget head truncation, tail
truncation, and a query-free TF-IDF-style extractive baseline. Gold atoms are
not passed to any system. Output framing and provenance pointers count against
the shared token estimate.

The bundled `compiler` candidate uses the default deterministic
`RuleBasedExtractor`; this snapshot does not exercise `ModelExtractor`. That
optional adapter accepts only complete atomic source literals for ordinary
claims and exact source literals for exact claims, not model paraphrases.

## External baselines

The bundled baselines are deterministic controls, not claims about the current
state of the art. Export the exact gold-free corpus before running ACON,
FoldAgent, AMA, or another external implementation:

```powershell
$env:PYTHONPATH = "src"
python -m benchmarks --histories 24 --export-corpus lrcbench-corpus.json
```

The export has schema `lrcbench-corpus-0.1`, the full benchmark config,
`dataset_sha256`, and ordered `source_events` for every case. It never
contains gold atoms. An external adapter must return a document with this
shape:

```json
{
  "schema": "lrcbench-candidate-output-0.1",
  "dataset_sha256": "<copy from corpus export>",
  "system": "acon",
  "cases": [
    {
      "case_id": "history-000",
      "rendered_text": "Do not change the public API refresh_token().",
      "claims": [
        {
          "text": "Do not change the public API refresh_token().",
          "kind": "constraint",
          "provenance": [
            {
              "source_id": "h000-s012",
              "start": 41,
              "end": 86,
              "quote": "Do not change the public API refresh_token()."
            }
          ]
        }
      ]
    }
  ]
}
```

Every exported case must occur exactly once. `kind` may be `null` for
untyped/extractive output or one of the typed-memory kinds. Every claim needs
one or more exact source spans, and its text must occur in `rendered_text`.
Recall credit requires the exact gold-atom offsets; a coarse enclosing span
does not count and cannot be used to game provenance coverage.
Do not submit an `active_tokens` field: the harness derives token usage after
adding a canonical claim/provenance ledger, so metadata cannot be free.
Unknown fields, wrong hashes, missing or extra cases, invalid spans, duplicate
system names, and any over-budget case fail closed before a report is emitted.

Evaluation also recomputes active tokens from the final rendered string for
every bundled or programmatic candidate. A valid byte span alone is not
semantic support: ordered content tokens, numeric identifiers, negation, and
uncertainty must agree with the cited text. Unsupported or hallucinated claims,
inactive/superseded claims, and authority-gated goals, constraints, or user
corrections sourced from tool/assistant messages make a history imperfect.
Gold-negative authority violations are detected by provenance overlap, so
paraphrasing a prompt injection cannot evade the check.

Import one or more candidates with repeatable options. The generation options
must match those used for the corpus export:

```powershell
python -m benchmarks --histories 24 `
  --external-baseline acon-output.json `
  --external-baseline foldagent-output.json
```

All supplied external candidates join the bundled controls, and the
certificate faces the baseline with the strongest critical-atom recall. Run
`python -m benchmarks --self-test` for a deterministic export/import
round-trip plus negative checks for hash mismatch, missing cases, and budget
overflow.

The certificate fails closed. It requires at least 24 histories in every
registered adversarial stratum, near-perfect critical and exact recall,
perfect provenance, semantic support, and authority accuracy, no stale claims,
unresolved-to-fact promotions, unsupported claims, or unsupported critical
claims, at least 5x compression,
no budget overrun, a 90% history-perfect rate, and both an aggregate and paired
bootstrap result establishing either at least 50% less critical semantic loss
or at least 50% more quality per active token than the strongest baseline. The
bounded 0-to-1 quality score is deliberately not used for relative gain: doing
so would make a perfect candidate unable to beat a baseline above 0.667 by 50%.
A failed run exits with status 2; this means the evidence does not support the
claim, not that the harness crashed.

Without `--external-baseline`, reports and claims are explicitly labeled
`local-bundled-only`; that certificate compares only the included controls
and must not be presented as a state-of-the-art comparison. With at least one
validated external candidate, the scope is `external-inclusive`.

## Recorded local snapshot

The reviewed 2026-07-23 default run covers 32 histories and dataset SHA-256
`9dd650433b9d1a018951a7a4745ba31907f6314965e44aee01ea9ecca24389ae`.
All 102 tests passed alongside it.

| System | Critical | Exact | Provenance | Support | Authority | Stale | Promotion | Perfect | Compression |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Compiler | 100% | 100% | 100% | 100% | 100% | 0% | 0% | 100% | 32.60x |
| Extractive | 88.1% | 83.6% | 100% | 100% | 0% | 100% | 0% | 0% | 30.13x |

See the [recorded JSON report](../docs/results/lrcbench-local.json). Its
certificate scope is `local-bundled-only`; these numbers contain no external
system result.

Use `--json-out benchmarks/result.json --include-histories` to retain auditable
per-history measurements. Generated result files should not be treated as
source fixtures unless intentionally reviewed and committed.
