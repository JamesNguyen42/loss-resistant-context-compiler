# Content secret redaction

The compiler provides optional, deterministic preprocessing for several common
credential forms. It is an explicit step before compilation, not a hidden
change to source ingestion.

```bash
ctxc redact history.jsonl \
  --output redacted-history.jsonl \
  --report redaction-report.json

ctxc compile redacted-history.jsonl --output compiled-memory.json
ctxc verify compiled-memory.json redacted-history.jsonl
```

Keep the redacted history beside the compiled artifact. Artifact provenance and
the artifact source digest intentionally bind the redacted records, not the
original records.

## Exact scope

Version `ctxc-redaction-report-0.1` has a deliberately narrow boundary:

- only `SourceRecord.content` is scanned and masked;
- source ids, sequence numbers, roles, timestamps, and metadata are preserved;
- every Unicode character offset is preserved;
- every physical line-boundary character is preserved;
- changed source content and record hashes are recomputed;
- findings record detector names, source coordinates, and counts, but never the
  original content secret or a digest of that secret;
- fixed resource ceilings bound source count, characters, candidates, and
  findings before an arbitrary iterable can be materialized without limit.

This first version does **not** redact metadata, source ids, timestamps,
arbitrary personal data, proprietary text, or unknown credential formats. It
does not accept caller-supplied regular expressions. It is a common-secret
preprocessor, not a complete DLP, PII-discovery, or compliance system.
The machine-readable interchange shape is
[redaction-report.schema.json](../schemas/redaction-report.schema.json);
runtime verification additionally enforces relationships JSON Schema cannot
express, such as exact counts, ordering, non-overlap, and self-hash equality.

The original input must enter the local Python process before it can be
redacted. Redaction does not erase the input file, process arguments, terminal
history, memory, swap, backups, logs, or earlier artifacts. Apply appropriate
host storage and access controls independently.

## Fixed detectors

The detector vocabulary and priority are public and versioned in code:

| Detector | Recognized content |
| --- | --- |
| `pem_private_key` | PEM RSA, EC, OpenSSH, or generic private-key blocks |
| `openai_style_key` | Long `sk-` credential forms |
| `github_token` | Long `ghp_`, `gho_`, `ghu_`, `ghs_`, or `ghr_` forms |
| `aws_access_key_id` | `AKIA` or `ASIA` access-key identifiers |
| `jwt` | Three sufficiently long base64url-like JWT segments |
| `bearer_token` | Long tokens following an HTTP `Bearer` scheme |
| `basic_authorization` | Long base64-like values following HTTP `Basic` |
| `url_userinfo` | `user:password` material between `://` and `@` |
| `credential_assignment` | Long quoted or bare values assigned to a fixed set of credential labels |

These are bounded lexical heuristics. A match can be a false positive, and an
unrecognized or unusually formatted secret can be a false negative. Review
both the selected policy and the report before relying on the result.

Overlapping matches are resolved deterministically by source start, longest
span, built-in detector priority, and detector name. A span is emitted at most
once. Every non-line-boundary character in that span is replaced by one
allowlisted mask character: `*`, `#`, `█`, or `■`.

Length preservation keeps existing character provenance meaningful within the
redacted record. It also reveals the secret's approximate length and line
shape; masking is not encryption.

## CLI

`ctxc redact` requires distinct source-output and report paths:

```bash
ctxc redact HISTORY \
  --output REDACTED.jsonl \
  --report REPORT.json
```

The command refuses to overwrite its input and detects resolved-path,
symlink, and existing hard-link aliases where the host exposes them. Each
destination is installed through a flushed same-directory temporary file and
atomic replacement. The two destination replacements are individually
crash-safe but are not one cross-file transaction. The report's redacted
source digest detects a mismatched pair.

Input can be `-` for stdin. Output is always source-record JSON Lines with
fresh content and record hashes. Both output paths are required so redacted
records are not accidentally mixed with human-readable stdout.

All detectors run by default. A repeatable option selects an explicit subset:

```bash
ctxc redact history.jsonl \
  -o redacted.jsonl \
  --report report.json \
  --detector github_token \
  --detector jwt
```

Anything covered only by an omitted detector remains unchanged. The report
records the exact canonical detector subset.

The redaction-specific resource options are:

| Option | Default |
| --- | ---: |
| `--max-source-records` | 100,000 records |
| `--max-redactions-per-source` | 1,000 findings |
| `--max-total-redactions` | 10,000 findings |
| `--max-redaction-source-chars` | 2,000,000 characters |
| `--max-redaction-total-chars` | 16,000,000 characters |

The ordinary source byte, record, line, canonical-size, and JSON-depth limits
also apply before scanning. The effective record cap is recorded as
`policy.max_sources`. A limit failure writes neither destination.

## Python API

```python
from context_compiler import (
    RedactionPolicy,
    SourceRecord,
    redact_sources,
    verify_redaction_result,
)

original = SourceRecord.create(
    sequence=0,
    role="user",
    content="Authorization: Bearer example-value-replaced-at-runtime",
)
result = redact_sources(
    [original],
    policy=RedactionPolicy(
        enabled_detectors=("bearer_token",),
        mask_character="*",
    ),
)

redacted_sources = result.sources
report = result.to_report()
verification = verify_redaction_result([original], result)
```

`RedactionResult` contains immutable source and finding tuples plus a
canonical, privately stored report. `to_report()` returns a detached copy.
Direct iterables are integrity-checked and character-counted one record at a
time and stop after the first record beyond `max_sources`; they are not first
converted to an unbounded list.
`verify_redaction_result()` reruns the selected fixed detectors over an
independently supplied original source set and requires exact source, finding,
policy, and report equality.

`verify_redaction_report_hash()` validates the strict nested report shape,
policy, counts, ordering, non-overlap, detector totals, and canonical
self-hash. It cannot prove that a report came from a trusted author or that the
named source set was the true original.

## Audit evidence and privacy

The report contains:

- the exact scope declaration and resource policy;
- source, changed-source, and finding counts;
- nonzero counts by detector;
- the digest of the redacted source set;
- ordered finding coordinates and masking counts;
- a canonical `report_sha256`.

No original content secret or per-secret digest is retained. Omitting original
secret digests avoids turning the report into a direct offline guessing oracle
for low-entropy content credentials.

The redacted source digest still commits to the unmodified source ids, roles,
timestamps, and metadata through each redacted record hash. If those fields
contain sensitive or low-entropy values, protect the report and redacted
records accordingly. Finding coordinates and source ids can also be sensitive.

The report hash detects accidental or internally inconsistent modification. It
is self-computable, not a signature, freshness proof, trusted timestamp, or
rollback defense.

## Safe deployment sequence

1. authenticate and normalize source roles outside this package;
2. minimize source ids and metadata before creating records;
3. run redaction before any model extractor or remote provider receives data;
4. inspect the redaction report and sample redacted output;
5. compile only the redacted records;
6. retain the exact redacted records needed for artifact replay;
7. apply encryption, access control, retention, and deletion controls at the
   storage layer;
8. treat false-negative testing and policy review as an ongoing operational
   responsibility.
