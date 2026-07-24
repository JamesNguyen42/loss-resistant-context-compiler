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
5. any callable supplied to `ModelExtractor` or `LiteralModelExtractor` is
   trusted to receive the source text, even though its output remains
   untrusted data.

If an attacker controls both an artifact and the “trusted” source history used
to verify it, hashes cannot recover the original truth.

## Threats and current controls

| Threat | Current control | Residual risk |
| --- | --- | --- |
| Omission of an explicit protected commitment | Non-replaceable built-in clause atomization, full deterministic recovery, and protected-only certification; custom safety extraction is additive | Novel syntax may evade both built-in rule passes |
| Post-verification item mutation | The final snapshot is recursively sealed and prompt/artifact rendering rechecks a canonical digest | Full Python-process compromise can bypass application controls; reflective mutation is detected at the next checked access but cannot be prevented from corrupting process memory |
| Primary model-provider failure | Built-in passes run independently; exceptions or wholly unusable output produce deterministic fallback plus a warning by default, with an opt-in strict failure policy | Generic in-process callables must enforce their own deadline and cancellation; fallback can lose novel phrasing found only by the model |
| Custom domain extractor omission or failure | Bounded `DomainLabelExtractor`, strict fail-fast composition, result validation, source-digest recheck, core item verification, and independent built-in fallback | Artifact replay verifies returned custom items but cannot prove arbitrary custom-domain completeness; a failed pack can lose domain-only vocabulary |
| Tool-output prompt injection | Goals, constraints, and corrections require `user`, `system`, or `developer`; decisions and unresolved state reject tool provenance; facts reject tool provenance unless the host sets `metadata.trusted_for_state: true` | Spoofed upstream roles or host-supplied trust metadata defeat the gate; errors and references still preserve untrusted tool literals |
| Memory text closes or splits its prompt envelope | Selected items are JSON-encoded; `<`, `>`, `&`, NEL, and Unicode line/paragraph separators are escaped inside an explicitly untrusted JSONL envelope | Syntactic containment does not stop a model from semantically following quoted instructions |
| Artifact text injects terminal controls | Item inspection runs only after envelope validation; text output JSON-quotes strings and visibly escapes control/format and line-separator characters, detailed JSON uses ASCII escapes, and item/link/text display is bounded | Printable confusable Unicode is not normalized; downstream programs should consume JSON rather than parse terminal text |
| Fabricated model provenance | Strict JSON rejects duplicate keys and non-finite numbers. The coordinate mode rebuilds spans from known source ids and integer character offsets. The unique-literal mode forbids coordinates and derives a span only from one exact occurrence in every cited source. Ordinary text must equal a complete atomic cited span across every Python line-boundary form; forged fields, reserved tags, byte-offset substitutions, ambiguous literals, and paraphrases are rejected | A real literal can still be assigned an incorrect non-authority-gated type that passes lexical checks; unique occurrence does not prove semantic completeness |
| Hallucinated model claim | Exact atomic extraction, role checks, ordered token support, numeric/negation checks, and consistent evidence polarity | Literal support is not logical entailment or proof that a memory kind is pragmatically correct |
| Lexical cue used only as quoted or discussed text | Authority gates block tool and assistant commitments; a frozen phrase diagnostic explicitly measures benign mention traps | The current local diagnostic observed four negative cases with six false-positive atoms overall; lexical matching does not generally distinguish use from mention |
| Uncertainty promoted to fact | Rule ordering favors unresolved; verifier rejects facts citing uncertainty without confirmation markers | Novel uncertainty phrasing or mixed confirmed/uncertain spans can be misclassified |
| Exact literal corruption | Exact items must equal every cited source quote | Correctly copied source text can itself be false or malicious |
| Stale requirement treated as current | Explicit corrections supersede linked earlier items; recognized revocations retire them without inventing replacement state; selecting a superseded item fails verification | Novel correction wording can remain unlinked and appear as a separate active claim |
| Conflicting state silently resolved | Clear polarity/numeric conflicts mark both claims conflicting and add an unresolved item | Semantic contradictions outside the lexical heuristic can be missed |
| Source/artifact modification | Source metadata is canonical-JSON-only and recursively immutable; canonical record hashes bind timestamp and metadata as well as content; inspection and diffing reject stale or malformed artifact envelopes, and full artifact replay binds derived structures | Hashes provide integrity comparison, not authorship, freshness, signatures, or rollback protection; an attacker can recompute artifact and diff self-hashes |
| Forged compilation telemetry | New metrics use an exact versioned shape inside the artifact hash; replay recomputes source, recovery, selection, status, conflict, protected-budget, and verification counts | Primary-extractor volume and elapsed duration are compilation-time observations that replay cannot independently reconstruct; hashes do not prove who measured them |
| Hidden performance regression | CI runs a fixed-digest, self-hashed profile with median latency, input-doubling growth, and exclusive `tracemalloc` peak ceilings | Shared-runner timing is noisy; `tracemalloc` is not RSS and misses native allocations; the small profile does not characterize production or million-event scale |
| Custom-tokenizer mismatch | A named counter records `custom:<id>`; independent verification requires the same callback and stable id and recomputes all compression fields | The id is a caller-managed label, not code signing or proof that two implementations are identical |
| Partial or conflicting archive write through the API | Persistent OS advisory lock released on descriptor close/process death, id/sequence collision rejection, load-time hash validation, and bounded full-file atomic replacement; readers observe an old or new complete log | A filesystem administrator can rewrite/delete files; advisory locks and rename durability may not be reliable on every network filesystem |
| Truncated or raced transactional file output | CLI files, archives, reports, corpora, and manifests flush and `fsync` the complete payload in a same-directory temporary file before atomic install; failed pre-install writes preserve the old destination, and manifests use exclusive no-clobber installation | Stdout is non-transactional; Windows lacks portable directory `fsync`; rename, hard-link, and durability guarantees depend on filesystem semantics |
| Budget pressure removes requirements | Protected kinds bypass optional selection; overflow is explicit or strict-fail | Enough protected content can exceed the downstream model’s hard context window |
| Source resource exhaustion | Shared positive limits cap serialized source/archive bytes, physical line length, JSON depth, record count, per-record and total canonical size across loaders, compiler, verifier, and archive; strict JSON rejects ambiguous/non-finite input | Python objects may already be allocated before a direct API call; configured maxima are not process-RSS limits |
| Benchmark evidence resource exhaustion or parser ambiguity | Reports, corpora, candidates, and manifests share bounded regular-file hashing and strict UTF-8 JSON decoding with duplicate-key, non-finite, byte, line, and depth rejection | Direct already-decoded Python objects are caller allocations; configured file limits do not cap total verifier RSS |
| Artifact resource exhaustion | Strict bounded loaders cap raw/canonical bytes, physical lines, JSON depth, item/selection collections, provenance spans, and embedded issues before inspect or replay | Direct Python objects may already be allocated; limits do not make a self-hashed artifact trustworthy |
| Compile/provider resource exhaustion | Model responses/candidate counts are bounded; unique-literal extraction preflights aggregate cited-source search characters; the local Qwen adapter has a transport timeout; optional whole-compile isolation owns a POSIX process group or Windows Job Object and terminates its descendant tree at the deadline | Search characters are a work proxy, not a time/RSS cap; direct in-process compilation is uncapped; a deliberately daemonized POSIX child can escape its process group; deadline job configuration uses local pickle and therefore requires trusted serializable objects |
| Common content-secret disclosure | Optional preprocessing uses nine fixed lexical detectors, length/line-boundary-preserving masks, recomputed record hashes, explicit limits, deterministic replay, and a strict self-hashed coordinate report that omits original content-secret text and hashes | Detection is heuristic; false positives and false negatives remain. Original input enters process memory first. Metadata, ids, timestamps, PII, unknown formats, report coordinates, storage, logs, and previous artifacts are outside the redaction scope |
| Secret disclosure after redaction | Compiling the redacted source set prevents recognized content secrets from entering its items, prompt, or artifact; replay binds the exact redacted records | Source metadata and identity fields remain in record hashes and may themselves be sensitive; external providers, process arguments, archives, diffs, benchmark evidence, or host logs can still expose anything not redacted before those boundaries |
| External benchmark adapter escape | The standard runner avoids a shell, isolates cases, limits time/output/process-tree memory on POSIX and Windows, terminates descendants, validates candidates, hashes a manifest, binds retained dependency-lock, command-referenced adapter-entrypoint, bounded link-free source tree, resolved runtime executable, portable per-case command contract, and external network-policy artifacts, and separately samples a stable pre-existing service identity and memory | It does not establish a filesystem or network sandbox or prove the retained policy was enforced; reviewed code and an isolated host/container remain necessary. The source tree does not bind imports outside its root or prove which files were loaded. The runtime digest does not bind shared libraries or interpreter support files. Service sampling does not contain or terminate that process and can miss between-poll spikes |
| External comparison protocol drift or cherry-picking | A strict self-hashed manifest binds the dated document, screened candidates, registered set, immutable revisions, exact model/resources, frozen datasets/statistics, runner policy, and explicit blockers; external scoring requires a frozen manifest plus exact dataset and adapter matches. Per-case replay reconstructs the ordered one-case corpus inputs from the retained parent | The current artifact is still a draft. Self-hashes are not timestamps, signatures, or proof of result-blind decisions; an independently anchored freeze and reproduction remain necessary |

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

`LiteralModelExtractor` keeps those policies but removes model-generated
coordinates from its schema. It uses exact, case-sensitive Python code-point
matching after edge trimming, rejects missing or repeated literals, constructs
the span from immutable source content, and requires atomic support even for
intrinsically exact errors and references. It does not normalize Unicode,
collapse whitespace, choose an occurrence, or accept a paraphrase. Unique
occurrence is provenance derivation, not evidence that the chosen text is the
best or only durable claim in the source.

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

The project provides opt-in common-secret redaction for source content. It does
not automatically enable it, and it provides no encryption, retention
scheduling, access control, general personal-data discovery, or secure
deletion. Exact provenance retains whichever redacted or unredacted source
quotations the caller chooses to compile.

`ctxc redact` and `redact_sources()` use only fixed, bounded lexical detectors.
They preserve character offsets and line boundaries, then recompute content
and record hashes. Their report includes source ids, coordinates, counts, and a
redacted-source digest, but no original content-secret text or per-secret
digest. Mask length and coordinates remain observable. The report self-hash is
not authentication.

Source ids, roles, sequence numbers, timestamps, and metadata are deliberately
unchanged in report version `0.1`. The redacted source-set digest commits to
those values through each record hash. A secret placed in metadata is therefore
neither removed nor outside all digest-based guessing risk. Minimize those
fields separately.

The preprocessor must first read the original input. It cannot erase that file,
Python memory, swap, shell history, backups, previous output, or upstream logs.
The CLI's redacted source and report files are each replaced atomically, but
the pair is not one cross-file filesystem transaction. Verify the report digest
against the retained redacted source set before use.

Before ingestion:

- authenticate roles and minimize metadata and source identifiers;
- run content-secret redaction and inspect its report when its fixed detector
  scope is appropriate;
- separately remove or tokenize sensitive personal data and unsupported secret
  formats;
- decide whether an external model provider may receive the history;
- remember that the included LM Studio CLI passes the prompt as a process
  argument that local process monitors or other users may observe;
- protect archives and generated artifacts with appropriate host controls;
- define deletion and backup policies outside this package.

Do not publish `--include-histories` benchmark-like evidence built from private
production histories without a separate review.

See [Content secret redaction](REDACTION.md) for the exact detector vocabulary,
resource limits, replay contract, and safe deployment sequence.

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

Archive locking has a finite timeout and uses a persistent `.append.lock`
marker. File existence is not ownership: the OS advisory lock is released when
the descriptor closes or the process dies, while the marker remains available
for the next writer. Do not delete or replace that marker while writers may be
running; use a local filesystem with documented advisory-lock semantics.

## Benchmark and claim threats

The LRCBench certificate can be gamed by overfitting to its generator, changing
baselines, tuning after observing gold cases, or reporting only favorable
seeds. `certificate.evidence_sha256` detects changes to deterministic comparison
evidence, while top-level `report_sha256` additionally binds producer/run
metadata and failures. Both are self-hashes: they detect inconsistency relative
to the recorded document but provide neither authorship nor experimental
fairness.

The bounded `--verify-report` path adds strict JSON/schema checks, deterministic
dataset regeneration, both digest recomputations, comparison/run-metadata
validation, and optional raw-metric reconciliation. It detects more internally
inconsistent reports, but an attacker who can fabricate a completely new
self-consistent report can also compute new self-hashes. Trusted publication
still requires an external signature or independently anchored digest.

External scoring additionally requires a strict frozen
`lrcbench-external-protocol-0.8` manifest. The manifest self-hash binds its
Markdown document digest, comparison decisions and immutable revisions,
adapter/dependency evidence, adapter entrypoint, source-tree, runtime, and
portable command-contract digests, exact local-Qwen constraints, dataset
manifests, statistics, every
execution-affecting runner limit, retained network-evidence digest,
inference-service metric/executable digest/ceiling, and blockers. The benchmark
imports the registered set from that manifest and refuses a different synthetic
dataset digest, adapter/environment identity,
entrypoint/source-tree/runtime/command digest, model contract, isolation mode,
network-evidence digest, service
accounting contract, or runner limit. Current `lrcbench-report-0.2` evidence records the
protocol and document hashes, dataset digest, and registered set. Report replay
checks those fields for internal consistency but does not by itself prove that
the referenced protocol was independently anchored or frozen before results;
retain and verify the protocol artifact separately.

Gold-free corpus exports carry versioned producer metadata and a canonical
`corpus_sha256`; candidates carry versioned adapter/model metadata and
`candidate_payload_sha256`. Producer fields are deliberately outside the
frozen dataset identity but inside their envelope digests. The external runner
verifies these records, normalizes legacy adapter output, and cross-checks
candidate identity against the retained manifest. These controls detect
accidental or unanchored changes; they remain self-hashes, not signatures or
proof that the named producer supplied the artifact.

Claim-bearing runner manifests also retain the exact dependency lock behind
`environment_id`. The runner hashes it before and after execution, loader
revalidates the same absolute path, and scoring requires the digest frozen for
that system. This binds environment identity to bytes but does not prove those
dependencies were the ones imported by the adapter process.

Claim-bearing manifests also retain one command-referenced adapter entrypoint
and a bounded inventory of every regular file below its dedicated source root.
Links and junctions are rejected; the runner rehashes the complete set before
and after execution, reload reconstructs it, and scoring matches the
entrypoint, tree, runtime, and portable command-contract digests to the
per-system protocol. Each exact per-case command must normalize to the same
contract, and the claim working directory must equal the retained source root.
This prevents a free-form revision string from substituting an unregistered
source tree or command. It does not bind imports outside that root, prove which
files were loaded, bind the runtime's shared libraries, or eliminate
change-and-restore races between observations; reviewed isolated execution
remains necessary.

The default external runner gives every case a fresh sequential process and
records its exact command and outcome. It retains each valid one-case
candidate's semantic self-digest and, on complete-run reload, reconstructs
that envelope from the matching raw merged case and registered producer. POSIX
`RLIMIT_AS` and Windows Job Objects bound the adapter process tree; Windows
processes are assigned while suspended and verified in the job before adapter
code resumes. These controls limit cross-case contamination and resource
exhaustion, but they are not a filesystem sandbox and do not establish network
isolation. Claim-bearing
manifests must retain a bounded host firewall, container, or network-namespace
policy artifact; the runner hashes it before and after execution and reload
checks the same file. A self-consistent file still does not prove that the host
enforced the policy. These controls also do not contain a model server that was
already running outside the adapter process tree. The runner instead captures
that process's PID creation token and executable digest and samples Windows
working set or Linux RSS at the fixed polling cadence, failing the adapter if
identity changes, sampling becomes unavailable, or the ceiling is exceeded.
This measured peak is a sampled upper observation, not proof that no shorter
memory spike occurred, that the adapter used the designated PID, or that
separate helper processes were included. Claim protocols must freeze the
service executable digest, metric, and ceiling or establish stronger external
containment.

The exact-Qwen phrase evaluator stores cleaned raw model outputs so strict
candidate validation and deterministic recovery can be replayed without model
access. Prompt and output digests, executable digest, repository state, and the
top-level self-hash detect inconsistent changes, but do not prove that Qwen
produced the bytes. A complete fabricated report can be rehashed. Raw outputs
can reproduce source text; the capture path is approved only for the public
frozen corpus unless a separate privacy review authorizes other inputs.
Recorded wall-clock latency is shape- and aggregate-checked but cannot be
independently reproduced offline.

The unique-literal offset ablation verifies that exact frozen source report,
retains the captured candidate text and cited source ids, discards only
coordinate values, and deterministically regenerates its own self-hashed
report. It makes no model call and labels the target prompt unevaluated.
Regeneration proves consistency with the checked-in captures and current
validator code, not that the captures came from Qwen or that post-hoc metrics
generalize.

The held-out paired evaluator additionally requires the frozen corpus hash, a
clean repository, alternating sequential call order, no retries, and exclusive
creation of the first report. These controls reduce result-selection and
overwrite risk but do not provide execution attestation. The local CLI exposes
no seed or temperature setting, so one captured draw per mode remains subject
to sampling noise. The retained run also demonstrates that LM Studio can emit
loading-status text on stdout before an otherwise structured response. Strict
whole-output JSON parsing safely rejected that capture instead of guessing at
framing, but reduced availability and recall. The current adapter, added after
that frozen run, accepts only a bounded exact-model loading prefix followed by
one JSON object and rejects ambiguous or trailing payloads. The historical
report is not rescored.

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
- automatically enabled redaction or comprehensive DLP/PII detection;
- metadata, source-id, or timestamp redaction;
- protection after host or Python-process compromise;
- guaranteed bounded process memory or runtime for adversarial extractors,
  token counters, or caller-allocated Python objects;
- a production incident-response or migration system.
