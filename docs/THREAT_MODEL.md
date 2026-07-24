# Threat model

This document describes the security and integrity boundary of the current
alpha implementation. It is not a security certification.

> **Evidence boundary:** the bundled certificate is local-only and does not
> establish a security or state-of-the-art advantage over external systems.

## Assets

- explicit user/system/developer goals, constraints, and corrections;
- confirmed observations, decisions, unresolved questions, and failed paths;
- exact errors, file/test references, versions, counts, and hashes;
- the binding from every claim to its original source text;
- source ordering and temporal state;
- active-context budget and benchmark evidence;
- potentially sensitive raw history stored in source records or archives.

## Trust boundaries

The compiler assumes:

1. the caller authenticates message roles before creating `SourceRecord`s;
2. ids and sequence numbers reflect the intended history order;
3. the source set supplied to verification is trusted independently of the
   artifact being checked;
4. the local host and Python process are not fully compromised;
5. any callable supplied to `ModelExtractor` is trusted to receive the source
   text, even though its output remains untrusted data.

If an attacker controls both an artifact and the “trusted” source history used
to verify it, hashes cannot recover the original truth.

## Threats and current controls

| Threat | Current control | Residual risk |
| --- | --- | --- |
| Omission of an explicit protected commitment | Non-replaceable built-in clause atomization, full deterministic recovery, and protected-only certification; custom safety extraction is additive | Novel syntax may evade both built-in rule passes |
| Post-verification item mutation | The final snapshot is recursively sealed and prompt/artifact rendering rechecks a canonical digest | Full Python-process compromise can bypass application controls; reflective mutation is detected at the next checked access but cannot be prevented from corrupting process memory |
| Primary model-provider failure | Built-in passes run independently; exceptions or wholly unusable output produce deterministic fallback plus a warning by default, with an opt-in strict failure policy | Generic in-process callables must enforce their own deadline and cancellation; fallback can lose novel phrasing found only by the model |
| Tool-output prompt injection | Goals, constraints, and corrections require `user`, `system`, or `developer`; decisions and unresolved state reject tool provenance; facts reject tool provenance unless the host sets `metadata.trusted_for_state: true` | Spoofed upstream roles or host-supplied trust metadata defeat the gate; errors and references still preserve untrusted tool literals |
| Memory text closes its prompt envelope | Selected items are JSON-encoded and `<`, `>`, and `&` are escaped inside an explicitly untrusted envelope | Syntactic containment does not stop a model from semantically following quoted instructions |
| Fabricated model provenance | Strict JSON rejects duplicate keys and non-finite numbers; model spans are rebuilt from known source ids and offsets; ordinary text must equal a complete atomic cited span, and forged fields, reserved tags, and paraphrases are rejected | A real literal can still be assigned an incorrect non-authority-gated type that passes lexical checks |
| Hallucinated model claim | Exact atomic extraction, role checks, ordered token support, numeric/negation checks, and consistent evidence polarity | Literal support is not logical entailment or proof that a memory kind is pragmatically correct |
| Uncertainty promoted to fact | Rule ordering favors unresolved; verifier rejects facts citing uncertainty without confirmation markers | Novel uncertainty phrasing or mixed confirmed/uncertain spans can be misclassified |
| Exact literal corruption | Exact items must equal every cited source quote | Correctly copied source text can itself be false or malicious |
| Stale requirement treated as current | Explicit corrections supersede linked earlier items; recognized revocations retire them without inventing replacement state; selecting a superseded item fails verification | Novel correction wording can remain unlinked and appear as a separate active claim |
| Conflicting state silently resolved | Clear polarity/numeric conflicts mark both claims conflicting and add an unresolved item | Semantic contradictions outside the lexical heuristic can be missed |
| Source/artifact modification | Source metadata is canonical-JSON-only and recursively immutable; canonical record hashes bind timestamp and metadata as well as content; higher-level digests and artifact replay bind derived structures | Hashes provide integrity comparison, not authorship, freshness, signatures, or rollback protection; an attacker can recompute an artifact self-hash |
| Custom-tokenizer mismatch | A named counter records `custom:<id>`; independent verification requires the same callback and stable id and recomputes all compression fields | The id is a caller-managed label, not code signing or proof that two implementations are identical |
| Archive overwrite through the API | Append-only path, exclusive local lock, id/sequence collision rejection, `fsync`, load-time hash validation | A filesystem administrator can rewrite/delete files; locks may not be reliable on every network filesystem |
| Budget pressure removes requirements | Protected kinds bypass optional selection; overflow is explicit or strict-fail | Enough protected content can exceed the downstream model’s hard context window |
| Source resource exhaustion | Shared positive limits cap serialized source/archive bytes, physical line length, JSON depth, record count, per-record and total canonical size across loaders, compiler, verifier, and archive; strict JSON rejects ambiguous/non-finite input | Python objects may already be allocated before a direct API call; configured maxima are not process-RSS limits |
| Compile/provider resource exhaustion | Model responses/candidate counts are bounded and the local Qwen subprocess adapter has a hard timeout | Artifact input size, whole-compile duration, custom extractor/token-counter work, and generic completion-callable latency are not globally capped |
| Secret disclosure | None beyond caller-controlled storage and provider choice | Source quotes, metadata, artifacts, prompts, benchmark JSON, and model calls can expose secrets; the LM Studio CLI prompt is visible in process arguments on some hosts |
| External benchmark adapter escape | The standard runner avoids a shell, isolates cases, limits time/output/process-tree memory on POSIX and Windows, terminates descendants, validates candidates, and hashes a manifest | It is not a filesystem or network sandbox; reviewed code and an isolated host/container remain necessary, and a pre-existing inference service is outside the process-tree memory boundary |

## Authority model

The deterministic extractor treats `user`, `system`, and `developer` as
authoritative for goals, constraints, and corrections. This is a parsing
policy, not identity authentication. An integration must map its own trusted
actors to these roles and prevent tools, retrieved documents, web pages, and
assistant-generated quotations from forging them.

Facts, decisions, and unresolved state have different policies. User, system,
developer, and assistant records may carry those kinds; tool records may carry
errors, references, and failed-attempt evidence but not decisions or unresolved
state. A tool can become a confirmed-fact source only when the host sets
`metadata.trusted_for_state` to the JSON boolean `true`. Do not copy that flag
from untrusted tool-controlled input. Canonical source-record hashing binds the
flag once accepted, but hashing does not justify the trust decision.

`SourceRecord` accepts only finite canonical JSON metadata and recursively
copies and freezes nested objects and arrays. This prevents later mutation from
silently changing an accepted trust flag. It does not make the original trust
decision correct.

`ModelExtractor` applies the same role policies before accepting candidates.
It also requires ordinary candidate text to equal a complete atomic cited span
and rejects duplicate JSON keys, non-standard or overflowed non-finite numbers,
paraphrases, forged object fields, reserved internal tags, uncertainty promoted
to fact, and invalid or unauthorized spans. The verifier independently rechecks
role metadata, atomic support, ordering, exactness, negation, and evidence
polarity. These controls prevent several representation attacks; they do not
prove that a correctly copied source statement is true or that every plausible
memory kind is semantically appropriate. Source roles are rendered in the
prompt so a downstream agent can retain that distinction.

## Integrity is not authenticity

SHA-256 answers “does this value match the value previously committed to?” It
does not answer:

- who authored the source;
- whether its role is genuine;
- whether the source was already malicious when hashed;
- whether a newer history has been rolled back;
- whether two colluding files were rewritten together.

Each source has a content digest and a canonical record digest covering id,
sequence, role, content, timestamp, and metadata. The source-set digest commits
to those record digests. Artifact verification additionally reconstructs the
canonical selected prompt and compression statistics and compares the embedded
verification report with an independent replay. Those checks expose internally
inconsistent rewrites; they do not provide an external trust anchor.

For adversarial storage, keep trusted digests outside the archive or use a
signed, monotonically versioned, write-once store. The prompt uses only a
ten-hex-character digest prefix for compact lookup; use the JSON artifact’s
full digest for integrity comparison.

## Privacy

The project performs no redaction, encryption, retention scheduling, or access
control. Exact provenance deliberately retains source quotations, which can
include credentials, personal data, proprietary code, or terminal secrets.

Before ingestion:

- remove or tokenize secrets and sensitive personal data;
- minimize metadata;
- decide whether an external model provider may receive the history;
- remember that the included LM Studio CLI passes the prompt as a process
  argument that local process monitors or other users may observe;
- protect archives and generated artifacts with appropriate host controls;
- define deletion and backup policies outside this package.

Do not publish `--include-histories` benchmark-like evidence built from private
production histories without a separate review.

## Availability and fail behavior

The compiler favors retention over size. Protected overflow produces a warning
by default because silently dropping a requirement is worse than missing a
soft budget. Use strict budget mode when the downstream interface has a hard
limit, then handle the exception without falling back to unverified
truncation.

`CompilationPolicy(verify=False)` is an unsafe diagnostic switch: it returns a
failed report with `verification_not_performed`. Normal Python and CLI prompt
rendering refuse that result. The Python-only `allow_unverified=True` override
must not be used in an agent execution path.

The four known in-process fail-closed gaps are closed: compiled snapshots are
sealed and digest-checked, built-in safety passes cannot be replaced, provider
failure degrades explicitly to deterministic memory by default, and selected
superseded state fails verification. Keep `include_superseded` out of execution
paths because it is intentionally audit-only. For applications where model
extraction is mandatory, set `fail_on_primary_extractor_error=True` and handle
the exception without rendering or silently truncating.

Custom token accounting is portable only when compilation uses both
`token_counter` and a stable `token_counter_id`, and artifact verification is
given the matching pair. Unnamed counters and missing or mismatched verifier
callbacks fail with `unverifiable_token_counter`; do not bypass that failure by
trusting the artifact's embedded compression report.

Archive locking has a finite timeout. A crashed process normally removes its
lock in `finally`, but process termination can leave a stale lock file that
requires operator review. Never delete a lock without first establishing that
no writer is active.

## Benchmark and claim threats

The LRCBench certificate can be gamed by overfitting to its generator, changing
baselines, tuning after observing gold cases, or reporting only favorable
seeds. The report digest detects changes only relative to recorded inputs; it
does not guarantee experimental fairness.

Gold-free corpus exports now carry their own canonical `corpus_sha256`, and the
external runner verifies it before execution. That detects accidental or
unanchored export changes; it is still a self-hash, not a signature or proof
that the evaluator supplied the intended corpus.

The default external runner gives every case a fresh sequential process and
records its exact command and outcome. POSIX `RLIMIT_AS` and Windows Job Objects
bound the adapter process tree; Windows processes are assigned while suspended
and verified in the job before adapter code resumes. These controls limit
cross-case contamination and resource exhaustion, but they are not a
filesystem or network sandbox. They also do not contain a model server that was
already running outside the adapter process tree. Claim protocols must freeze
separate containment or accounting for that service.

Manifest replay requires the retained corpus at its recorded absolute path and
checks its canonical digest, file digest, dataset id, and case count. The
manifest is still only a self-hash: anyone able to replace the corpus,
candidate, and manifest together can create a new internally consistent bundle.

Most importantly, a local certificate is **not proof against external
technology**. External claims require preregistered systems, matched resources,
public downstream tasks, complete result disclosure, and independent
reproduction as specified in [Benchmarking](BENCHMARKING.md).

## Out of scope

The current package does not provide:

- cryptographic signatures, key management, or remote attestation;
- sandboxing of source content or downstream actions;
- distributed consensus or a tamper-proof event log;
- semantic entailment proofs;
- automatic secret detection;
- protection after host or Python-process compromise;
- guaranteed bounded process memory or runtime for adversarial extractors,
  token counters, artifacts, or caller-allocated Python objects;
- a production incident-response or migration system.
