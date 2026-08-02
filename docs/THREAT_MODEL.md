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
- OpenHands host EventLog/source parity, atomic tool-call/result linkage, and
  callback failure diagnostics;
- verified generation visibility and immutable final-request ledgers in the
  separate integration store.

## Trust boundaries

The compiler assumes:

1. the caller authenticates message roles before creating `SourceRecord`s;
2. ids and sequence numbers reflect the intended history order;
3. the source set supplied to verification is trusted independently of the
   artifact being checked;
4. the local host and Python process are not fully compromised;
5. any callable supplied to `ModelExtractor` or `LiteralModelExtractor` is
   trusted to receive the source text, even though its output remains
   untrusted data;
6. connector checkpoints, explicit source records, and archives carrying a
   previously admitted authority marker are protected host state and do not
   arrive from an untrusted bearer;
7. an OpenHands `source` or message `role` literal is not authentication;
   authority exists only after an independently issued event-bound receipt
   verifies under a reviewed issuer policy;
8. the separate SQLite-WAL store runs on a correct local non-symbolic
   filesystem, and neither SQLite nor the Python process is fully compromised;
9. the canonical byte tokenizer and fake runtime establish exactness only for
   their offline protocol, not for a real OpenHands provider request.

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
| Source/artifact modification | Source metadata is canonical-JSON-only and recursively immutable; canonical record hashes bind timestamp and metadata as well as content; inspection and diffing reject stale or malformed artifact envelopes; full artifact replay binds derived structures; a detached trust manifest can bind the complete artifact, exact source set, and optional archive head to a separately retained digest | The manifest is not a signature or trusted timestamp. Authenticity, freshness, and rollback resistance depend on protecting the latest expected digest outside the bundle rewrite boundary; an attacker controlling that anchor can replace the whole bundle |
| Forged compilation telemetry | New metrics use an exact versioned shape inside the artifact hash; replay recomputes source, recovery, selection, status, conflict, protected-budget, and verification counts | Primary-extractor volume and elapsed duration are compilation-time observations that replay cannot independently reconstruct; hashes do not prove who measured them |
| Hidden performance regression | CI runs a fixed-digest, self-hashed profile with median latency, input-doubling growth, and exclusive `tracemalloc` peak ceilings | Shared-runner timing is noisy; `tracemalloc` is not RSS and misses native allocations; the small profile does not characterize production or million-event scale |
| Custom-tokenizer mismatch | A named counter records `custom:<id>`; independent verification requires the same callback and stable id and recomputes all compression fields | The id is a caller-managed label, not code signing or proof that two implementations are identical |
| Partial, conflicting, redirected, or rolled-back archive state through the API | Persistent OS advisory lock released on descriptor close/process death, stable single-link regular-file reads and a single-link lock marker, full ancestor-chain checks, POSIX parent-relative opens, bounded replacement-race retries, id/sequence collision rejection, canonical entry hash chaining, optional externally retained head preconditions, load-time hash validation, and bounded full-file atomic replacement; readers observe an old or new complete log | Standalone verification accepts a valid older prefix; rollback detection requires a separately protected newer head. An administrator can recompute/rewrite/delete files; advisory locks and rename durability may not be reliable on every network filesystem |
| Truncated or raced transactional file output | CLI files, archives, reports, corpora, and manifests flush and `fsync` the complete payload in a same-directory temporary file before atomic install; linked/reparse ancestors and special destinations are refused, POSIX installs are relative to a pinned parent descriptor, failed pre-install writes preserve the old destination, failed cleanup rechecks the temporary-file identity, and external-run manifests use exclusive no-clobber installation | Stdout is non-transactional. Windows lacks portable parent-relative replacement and directory `fsync`; a privileged rename in the final syscall window, the remaining check/unlink cleanup race after the identity recheck, and durability guarantees remain host/filesystem trust boundaries |
| Budget pressure removes requirements | Protected kinds bypass optional selection; overflow is explicit or strict-fail | Enough protected content can exceed the downstream model’s hard context window |
| Source/artifact path redirection or resource exhaustion | Serialized source/artifact paths require stable regular files, reject linked/reparse ancestors, freeze and recheck every ancestor identity, use POSIX parent-relative access, use no-follow flags and pre/open identity checks, retry bounded atomic replacement races, compare post-read content metadata, and never decompress input; shared positive limits cap serialized source/archive bytes, physical line length, JSON depth, record count, per-record and total canonical size across loaders, compiler, verifier, and archive; fixed 1,024/128/256-character id/role/timestamp ceilings bound identity-field replication; strict JSON rejects ambiguous/non-finite input | Direct caller streams remain host trust boundaries. On hosts without parent-relative APIs, a privileged rename inside pathname resolution can cause an unintended descriptor to be opened before the post-open ancestor check rejects it. Python objects may already be allocated before a direct API call; accepted maxima and derived-item multiplicity are not process-RSS limits |
| Benchmark evidence resource exhaustion or parser ambiguity | Reports, corpora, candidates, and manifests share ancestor-guarded bounded regular-file hashing, pre/open/final file snapshots, and strict UTF-8 JSON decoding with duplicate-key, non-finite, byte, line, and depth rejection | Direct already-decoded Python objects are caller allocations; configured file limits do not cap total verifier RSS |
| SWE-bench mirror/path escape, transformed checkout, or repository-history leakage | The source-only preparer requires an offline absolute SHA-1 bare mirror and absolute hashed Git executable; disables inherited/global/worktree config, lazy fetch, replacement refs, prompting, and optional locks; rejects shallow/partial/promisor state, alternates, grafts, includes, extra remotes, worktrees, links/reparse points, and special mirror entries; starts a release-digest-bound worker with isolated Python startup and bounded/EOF-checked Git pipes; reads and recomputes bounded raw commit/tree/blob frames without checkout/archive/filter semantics; permits only portable `100644`/`100755` regular files in a fresh tree; reopens the live commit; and independently rejects unexpected paths, directories, links, attributes, streams, modes, or bytes before returning coordinator-only self-hashed evidence with no serialized output path | A configured origin URL, SHA-1 object id, SHA-256, or self-hash does not authenticate GitHub, authorship, freshness, or license. Git and its pack parser run on the trusted coordinator host and are not security-reviewed by this contract. Host-level path substitution outside the guarded observations, hostile filesystem behavior, SHA-1 collision attacks, and a fully compromised Python/Git process remain trust boundaries. The synthetic mirror tests do not prove that all public tasks prepare successfully, and the prepared directory is not a candidate mount or filesystem/network sandbox |
| Connector contract ambiguity, schema-only trust, or exception-data disclosure | Seventeen bounded Draft 2020-12 schemas, strict runtime decoding, six golden stateful transcripts, 66 negative vectors, in-process/stdio semantic-equivalence checks, and ten schema-closed error variants constrain the wire shape; fixed public messages and normalized public exception types prevent raw exception text from crossing the boundary | JSON Schema acceptance is structural. It does not prove source authority, provenance truth, replay validity, checkpoint freshness, protected completeness, or digest authenticity; trusted in-process diagnostics remain a host responsibility and runtime verification remains authoritative |
| Canonical adapter trust promotion, dependency shadowing, projection loss, or transport drift | Before the adapter initiates package import or exposes a preloaded root, one exact-version distribution, all 35 immutable wheel `RECORD` rows and only the closed installer-generated set, an unset external bytecode-cache prefix, exact built-in module/spec/source-loader state bound to its recorded package, an exact bounded link-free source/resource tree digest and sizes, absence of unexpected importable entries, and any package-local executable bytecode against freshly compiled verified source must agree; the complete `RECORD`/file/origin gate repeats after import. Exact-wheel PEP 610 metadata and one platform-canonical launcher are mandatory, so a transitive optional-extra install without direct archive binding fails closed. Typed request and NDJSON surfaces then accept actual canonical requests after a wheel-owned handshake, advertise one executed operation, apply identical bounded canonical parsing in-process and over NDJSON, namespace input metadata, require an independent host callback before preserving authority, return every complete source as untrusted evidence, project only exact source quotes with UTF-8 byte offsets, record protected omissions/overflow, and pass the fixed 22-case non-inference gate. Direct `handle` is only the wheel server's negotiated callback seam | The installed `RECORD` and PEP 610 claim do not independently authenticate the wheel archive, and check/import is not atomic against writable site-packages. Prior `.pth`, `sitecustomize`, custom `meta_path`, or preloaded same-origin object effects cannot be undone; a custom finder runs during spec resolution, and the local host/Python process remains trusted. A compromised verifier can authenticate the wrong event. The canonical projection does not prove authenticity, semantic completeness, or final-request accounting and cannot carry the private artifact, archive state, or detector-scoped certificate |
| Unknown or malformed OpenHands event enters durable history | The separate adapter requires one of 18 exact top-level classes, matching concrete class and serialized kind, exact reviewed fields, bounded canonical JSON, and explicit durable/transient handling; unknown classes, kinds, and fields fail closed | The map is intentionally revision-specific. A legitimate new host event is an availability failure until reviewed, pinned, documented, and regression-tested |
| Forged authenticated or trusted-state `SourceRecord` is passed directly to the public SQLite store | Append independently rederives the exact mapper-produced record from the canonical host event and compares role, content, receipt-bound authority, provenance, OpenHands metadata, and record identity before insertion; direct transient ingestion is refused | This protects the application API, not a database modified outside it. Receipt issuer policy and local database/filesystem integrity remain trusted boundaries |
| OpenHands source/role spoof or authority self-promotion | Host source, message role, hook identity, and tool payload remain claims; an independent receipt binds the exact canonical event, session, id, kind, role, issuer, and optional reviewed tool. Unknown tools cannot receive authority, and only allowlisted `ObservationEvent` tools can become trusted for state | Compromise or misconfiguration of the independent issuer secret/allowlists can grant incorrect authority; hashing records the decision but cannot justify it |
| Pre-persistence callback refusal loses parity with the host EventLog | The callback catches every failure instead of raising into the host, records the first bounded failure, and poisons later ingestion and dispatch until the complete persisted EventLog is replayed in exact order and source counts match | Correct host persistence and delivery of a complete, ordered EventLog remain host responsibilities. An incomplete or filtered reconciliation must stay blocked |
| Tool call/result split, duplication, reordering, or family substitution | Atomic groups are re-derived from canonical events, compared with declared metadata, and require ordered matching call id, tool name, and action id where present; incomplete or inconsistent groups block compaction and active-tail reads | A host that never emits a matching result causes safe availability loss. ACP progress telemetry deliberately cannot complete an ordinary action group |
| Crash, contention, or historical-row corruption exposes an unverified OpenHands generation | The local SQLite store uses WAL, `synchronous=FULL`, immutable source/transition/ledger triggers, independently replayed `prepared`, `verified`, `committed`, and `active` states, and source-head/parent/epoch compare-and-swap; only one verified active pointer is readable. One-transaction integrity review enumerates every retained generation/transition/ledger/operation, reconstructs source prefixes and activation epochs, and exactly replays supported request ledgers | SQLite and local filesystem correctness remain assumptions. A privileged local attacker can rewrite or replace the database and recompute self-hashes; these controls are not consensus, signatures, remote attestation, or hostile-storage containment |
| A re-self-hashed scenario or soak report makes false claims about the bound SQLite database | One bounded transaction reads the session/generation/activation inventory, followed by separately bounded source, active-generation, integrity, and ledger reads guarded by pre/post database hashes and WAL/SHM rejection. The standalone verifier cross-binds the sole session, ordered generation count/ids, parent and epoch lineage, terminal states, verification flags, activation transitions and operation ids, captured source counts/heads, bundle and semantic digests, active pointer, and report-specific generation arrays before `json-and-database` scope can pass | Report and database hashes remain self-consistency evidence, not authorship or freshness. The recovery operation proves the durable recovery path, not an independently observed OS crash. Historical artifacts accepted before these row bindings are not retroactively reverified |
| Estimated or partial accounting is mislabeled exact, or a final request exceeds its hard limit | The fake-runtime ledger requires all eight request categories, tokenizer vector replay, component/total equality, reserves, margin, tool-schema and final-request digests, and refuses overflow. Real host `run()`/`arun()` remains refused without an exact immutable request/tokenizer hook | Exactness currently covers only the canonical-UTF-8-byte fake protocol. It says nothing exact about LiteLLM/provider wire bytes or a real model tokenizer |
| Exact rehydration promotes malicious source text into authority | Rehydration verifies the retained source record, half-open character span, quote digest, and response self-hash, but fixes the trust label to `untrusted-evidence` and warns that fidelity is not truth or instruction priority | A downstream consumer can ignore the label and follow hostile evidence; access control and safe presentation remain integration responsibilities |
| Version-matching OpenHands packages are mistaken for reviewed live compatibility | A self-hashed compatibility manifest binds exact host/SDK/tools/agent-server revisions, source files, artifact digests, Python versions, and retained blocker state; imports happen only after the exact gate. Manual installation cannot clear the final-request accounting refusal | No complete hash-pinned offline dependency closure or successful live demonstration exists. Self-hashes are not signatures, and private host seams may change or differ from provider wire behavior |
| Artifact resource exhaustion | Strict bounded loaders cap raw/canonical bytes, physical lines, JSON depth, item/selection collections, provenance spans, and embedded issues before inspect or replay | Direct Python objects may already be allocated; limits do not make a self-hashed artifact trustworthy |
| Compile/provider resource exhaustion | Built-in extraction checks an incremental item ceiling; every extractor result is bounded by item/rejection/canonical/auxiliary size, all passes share candidate/resolved/provenance ceilings, and recovery, resolution, conflict search, selection, and independent verification consume one shared item-work budget. Model responses/candidate counts and unique-literal cited-source search are separately bounded; optional whole-compile isolation and the exact-Qwen CLI transport own a POSIX process group or Windows Job Object, retain the POSIX leader until group signaling finishes, and terminate the Windows Job at the deadline | A custom extractor executes before its returned result can be bounded. Item/search work are deterministic proxies, not wall-clock or RSS caps, and do not cover every regex, token, allocation, or custom-counter cost. A deliberately daemonized POSIX child can escape its process group. Linux group-signal success does not prove that every member accepted the signal; after final macOS group signaling, cleanup requires either group disappearance or stable all-zombie evidence. Deadline job configuration uses local pickle and therefore requires trusted serializable objects |
| Common content-secret disclosure | Optional preprocessing uses nine fixed lexical detectors, length/line-boundary-preserving masks, recomputed record hashes, explicit limits, deterministic replay, and a strict self-hashed coordinate report that omits original content-secret text and hashes | Detection is heuristic; false positives and false negatives remain. Original input enters process memory first. Metadata, ids, timestamps, PII, unknown formats, report coordinates, storage, logs, and previous artifacts are outside the redaction scope |
| Secret disclosure after redaction | Compiling the redacted source set prevents recognized content secrets from entering its items, prompt, or artifact; replay binds the exact redacted records | Source metadata and identity fields remain in record hashes and may themselves be sensitive; external providers, process arguments, archives, diffs, benchmark evidence, or host logs can still expose anything not redacted before those boundaries |
| Natural-history consent, license, privacy, annotation, or split failure | Versioned bounded contracts require explicit origin/license/consent/privacy states, exact spans, two independent annotators, complete adjudication, full attempted/included/excluded accounting, repository/task-group-disjoint splits, and label-free exports | The committed records are synthetic contract fixtures. Self-hashes do not authenticate consent or reviewers, detect every private field, prove annotation quality, or establish that a natural cohort exists; real data requires independent review before commit or evaluation |
| External benchmark adapter escape | The standard runner never shell-interprets adapter argv, isolates cases, limits time/output, applies inherited per-process memory limits on POSIX and per-process/aggregate Job limits on Windows, signals its owned POSIX process group or terminates the Windows Job, validates candidates, hashes a manifest, binds retained dependency-lock, command-referenced adapter-entrypoint, bounded link-free source tree, resolved runtime executable, portable per-case command contract, and external network-policy artifacts, and separately samples a stable pre-existing service identity and memory. macOS uses one fixed runner-owned `/bin/sh -p` script with an empty environment; `-p` grants no privilege. Limits and adapter argv remain quoted positional arguments, while a bounded canonical anonymous-FD payload carries the exact adapter environment to the verifier | It does not establish a filesystem or network sandbox or prove the retained policy was enforced; reviewed code and an isolated host/container remain necessary. A POSIX descendant can leave the owned group. Linux group-signal success does not prove that every member accepted the signal; macOS fails cleanup on a live, inaccessible, raced, or uninspectable remaining member. POSIX descendants each receive the individual address-space limit, not one aggregate tree ceiling. Exact-limit status exists only after verifier success. After exact inherited `RLIMIT_AS` verification, the environment FD is checked, read, scrubbed, truncated, and closed; in-memory length, SHA-256, and protocol validation precede exact `RLIMIT_FSIZE`, canonical decode, and `execve`. A pre-shell launch failure or shell, pre-verifier, or inexact-`RLIMIT_AS` exit closes the anonymous unlinked FD through process/context teardown without guaranteeing a scrub; completed scrubbing is not cryptographic erasure. The fixed macOS `/bin/sh` implementation and supervisory interpreter are runner-owned platform machinery and are not separately hashed in the manifest. The source tree does not bind imports outside its root or prove which files were loaded. The runtime digest does not bind shared libraries or interpreter support files. Service sampling does not contain or terminate that process and can miss between-poll spikes |
| External comparison protocol drift or cherry-picking | A strict self-hashed manifest binds the dated document, screened candidates, registered set, immutable revisions, exact model/resources, frozen datasets/statistics, runner policy, and explicit blockers; external scoring requires a frozen manifest plus exact dataset and adapter matches. Per-case replay reconstructs the ordered one-case corpus inputs from the retained parent | The current artifact is still a draft. Self-hashes are not timestamps, signatures, or proof of result-blind decisions; an independently anchored freeze and reproduction remain necessary |

Canonical request parsing shares exact `ParseLimits` in-process and over NDJSON,
but physical stream recovery is intentionally transport-specific. Only bounded
oversized records can drain to one error and resume; over-ceiling frames,
non-binary streams, short writes, and real I/O failures close fatally.

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

The connector downgrades assistant, tool, and function records whose
connector-owned authentication marker is missing. It intentionally preserves
an explicit marker across checkpoint, direct-record, and archive restart paths
so authenticated host state keeps batch/restart equivalence. A self-hash is not
proof that the host authored that marker: accepting attacker-controlled,
rehashable connector state would let the attacker assert the same authority as
an authenticated `SourceEvent`. Protect these serialized inputs as part of the
host boundary; they are not safe bearer credentials.

The canonical `localai-contracts` boundary differs from the private
structural connector: `SourceEvent.trust`, role, metadata, and content hashes
are all claims. Without a host-owned verifier callback, every event is mapped
through the connector's untrusted assistant-history path. A callback decision
is considered only when the event also declares `trust: trusted`; it must
return the adapter's exact `AuthenticatedAuthority` value, and
`trusted_for_state` is restricted to authenticated tools. Complete source
rehydration always returns in `untrusted_retrieved_spans`, even when selected
subspans from the same source are independently authenticated.

The separate OpenHands adapter does not reuse a host `source` or nested role as
an authority marker. A receipt is eligible only for reviewed model-facing,
non-transient events and is verified against an independently configured issuer
secret and allowlists. It binds the complete canonical event and session, so
mutation or cross-session replay fails. Even a valid receipt cannot promote
most event kinds to state authority: only an exact reviewed
`ObservationEvent` tool may become `trusted_for_state=true`, and only when that
issuer independently allowlists the tool. File, search, retrieval, attachment,
hook, assistant, and delegated-agent text therefore remains untrusted unless a
narrower reviewed receipt path explicitly applies.

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

`ctxc-trust-manifest-0.1` standardizes that missing link without pretending to
be a signature. Creation is allowed only for a complete artifact that passes
independent replay. Verification binds the artifact digest, exact source-set
digest/count, and optional archive head, but it reports success only when the
caller also supplies the manifest SHA-256 retained outside the bundle. The
declared creation time is descriptive, not authoritative. Keeping the expected
digest beside attacker-writable bundle files does not add authenticity; an
attacker who can replace both can recompute every hash.

The archive's rolling entry chain binds physical order and exposes a current
head. Supplying a separately retained head to verify or the next append detects
an older valid prefix and stale state, but the self-hashed chain does not
authenticate its author or prevent a privileged rewriter from recomputing the
entire file. For adversarial storage, protect the head outside the archive or
use a signed, monotonically versioned, write-once store. The prompt uses only a
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
fields separately. Fixed source-id, role, and timestamp length ceilings bound
structural amplification; they do not detect or remove sensitive values.

The preprocessor must first read the original input. It cannot erase that file,
Python memory, swap, shell history, backups, previous output, or upstream logs.
The CLI's redacted source and report files are each replaced atomically, but
the pair is not one cross-file filesystem transaction. Verify the report digest
against the retained redacted source set before use.

The OpenHands SQLite store deliberately retains complete canonical host events,
superseded and rolled-back generations, request ledgers, and bounded callback
failure evidence. Rehydration can reproduce exact source substrings. Apply
redaction and access/retention policy before this boundary; the store does not
provide encryption, secure deletion, or PII discovery. Preserve databases and
reports for audit only in approved storage, and use SQLite-aware backup rather
than copying a live WAL database as ordinary files.

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
production histories without a separate review. The natural-history evidence
schemas make license, consent, privacy-review, exclusion, annotation,
adjudication, and split state explicit, but satisfying a schema is not that
review and cannot make private source text publishable.

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

In the separate OpenHands alpha, callback rejection is an availability stop,
not permission to skip the event. The callback lets host persistence continue,
then blocks subsequent dispatch until exact EventLog reconciliation succeeds.
Do not clear poison manually, filter a refused event, or synthesize a matching
tool half merely to resume.

A failed compile, replay, compare-and-swap, crash recovery, or hard-limit check
leaves the candidate invisible or retained as a non-active failure; it does not
authorize truncation or deletion of source history. Lowering reserved output or
safety margin merely to fit is a gate weakening.

Ordinary OpenHands `doctor` success means offline readiness only. The
live-required preflight remains a retained failure, and guarded `run()`/`arun()`
remains refused, until both the complete hash-pinned offline dependency closure
and a supported exact final-request/tokenizer hook exist.

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
`lrcbench-external-protocol-0.10` manifest. The manifest self-hash binds its
Markdown document digest, comparison decisions and immutable revisions,
adapter/dependency evidence, adapter entrypoint, source-tree, runtime, bounded
process-environment, and portable command-contract digests, exact local-Qwen
constraints, dataset
manifests, statistics, every
execution-affecting runner limit, retained network-evidence digest,
inference-service metric/executable digest/ceiling, and blockers. The benchmark
imports the registered set from that manifest and refuses a different synthetic
dataset digest, adapter/dependency identity,
entrypoint/source-tree/runtime/process-environment/command digest, model
contract, isolation mode, network-evidence digest, service
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

Adapter children receive a bounded platform-startup allowlist rather than the
complete host environment. Additional variables must be selected by name; the
manifest retains names and a digest over value hashes, not plaintext values.
Credential-like names make claim metadata incomplete, and scoring freezes the
digest per system. This reduces accidental credential propagation and binds
environment-dependent behavior, but a low-entropy value may still be guessed
from its digest, and neither a digest nor an allowlist proves what an adapter
read through files or another process.

The default external runner gives every case a fresh sequential process and
records its exact adapter command and outcome. It retains each valid one-case
candidate's semantic self-digest and, on complete-run reload, reconstructs
that envelope from the matching raw merged case and registered producer. POSIX
`RLIMIT_AS` applies one inherited virtual-address-space ceiling to each adapter
process and descendant; it is not physical RSS/footprint accounting or an
aggregate tree bound. A usable finite ceiling depends on the host and runtime's
existing virtual mappings. Windows processes are
assigned while suspended to a Job Object with per-process and aggregate memory
limits and are verified in the job before adapter code resumes. macOS first
uses a fixed runner-owned `/bin/sh -p` script with an empty environment to set
the requested `RLIMIT_AS` soft and hard values in 1024-byte units before Python
starts. The `-p` flag selects privileged shell mode and grants no privilege.
The script forwards only quoted positional arguments to an isolated no-site
(`-I -S`) verifier; it never shell-interprets adapter text.
A bounded canonical encoding of the exact adapter environment is passed through
an anonymous, unlinked regular-file descriptor with its expected byte count and
SHA-256 digest. Before bounds and hashing on Darwin, the runner reserves
`__CF_USER_TEXT_ENCODING` as `0x{uid:X}:0:0`; a conflicting caller value fails
closed. The counted, hashed entry prevents CoreFoundation's default-text-encoding
initializer from replacing that environment entry with a host/home-derived
value after `execve`. Evidence covers the exact mapping passed
to `execve`, not later mutations by arbitrary runtime code. The verifier first
requires exact inherited `RLIMIT_AS`;
validates the descriptor, file, and expected size; reads, scrubs, truncates,
and closes the handoff; validates the retained in-memory length, SHA-256, and
protocol; applies byte-exact `RLIMIT_FSIZE`; canonically decodes the
environment; and calls `execve` with literal adapter argv. A pre-shell launch
failure or shell, pre-verifier, or inexact-`RLIMIT_AS` exit closes the anonymous
unlinked descriptor through process/context teardown without guaranteeing a
scrub. Completed scrubbing reduces residual retention but does not establish
cryptographic erasure. Exact-limit status requires verifier success. If setup
or `execve` fails, the launcher fails instead of substituting a different,
evidence-inexact ceiling. This avoids `preexec_fn` while preserving inherited
limits and process-group ownership. Protocol
`lrcbench-external-protocol-0.10` binds the fixed shell prefix, launcher
protocol identifier, and exact inline-launcher
script digest. Individual run manifests do not independently bind those
details; the system-shell binary and implementation, supervisory interpreter,
and supporting runtime files remain unhashed.
POSIX exit observation retains the waitable leader as the group-ID
identity anchor until owned process-group cleanup completes. After a successful
`SIGTERM`, a Darwin permission-denied liveness probe permits only bounded
reobservation of that still-owned unreaped leader through the existing grace
deadline. Permission denial is accepted only after stable bounded process-group
snapshots prove all
anchored members are zombies; otherwise the started run is retained as
non-scoreable and per-case mode launches no later case. Preflight and `Popen`
failures remain blocking runner errors. A post-`Popen` launcher failure is
retained in a failed, non-scoreable case manifest. On Darwin, a configured
limit with `process_succeeded: false` conservatively records
`memory_limit_enforced: false` because the parent has no authenticated
verifier-completion signal; this may underreport enforcement but cannot upgrade
the retained failure. A configured limit with `process_succeeded: true`
requires `memory_limit_enforced: true`. These controls limit cross-case contamination and resource
exhaustion, but they are not a filesystem sandbox and do not establish network
isolation.
The stdout, stderr, and candidate-file byte caps are polling-enforced at an
approximately 20 ms cadence. A process can therefore transiently overshoot a
cap on disk before detection and termination. Stream evidence is read from
runner-retained descriptors rather than attacker-replaceable paths. At or below
the cap its byte count and hash cover the full one-time observed stream; above
the cap they cover a `cap + 1` prefix witness while the descriptor-size
observation still forces failure. The snapshot does not chase later growth.
These checks are not filesystem quotas, do not prevent writes elsewhere, and
do not turn the runner into a filesystem sandbox.
Claim-bearing manifests must retain a bounded host firewall, container, or
network-namespace
policy artifact; the runner hashes it before and after execution and reload
checks the same file. A self-consistent file still does not prove that the host
enforced the policy. These controls also do not contain a model server that was
already running outside the adapter process tree. The runner instead captures
that process's PID creation token and executable digest and samples Windows
working set or Linux/macOS RSS at the fixed polling cadence, failing the
adapter if identity changes, sampling becomes unavailable, or the ceiling is
exceeded. macOS `libproc` sampling rechecks PID creation time around the
executable and RSS observations. This measured peak is a sampled upper
observation, not proof that no shorter memory spike occurred, that the adapter
used the designated PID, or that separate helper processes were included. It is
not a verified jetsam or physical-footprint provider.
Linux hashes the opened `/proc/<pid>/exe` descriptor between start-token
observations and rechecks the proc link target and inode so a different
mount-namespace pathname or concurrent `execve` cannot substitute other bytes.
Protected or absent procfs and a PID not visible in the runner namespace
fail closed with distinct inspection categories; no `cmdline`, `ps`, or
caller-supplied-path fallback is trusted. A numeric PID can still collide with
an unrelated process in another PID namespace. Operators must supply the PID
visible to the runner, and claim scoring must match the captured executable
digest to the frozen protocol. Re-execution of the same path, inode, and bytes
does not change these observations and remains outside this identity proof.
Claim protocols must freeze the service executable digest, metric, and ceiling
or establish stronger external containment.

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
- a successful live OpenHands demonstration or production compatibility claim;
- a complete reviewed hash-pinned offline OpenHands dependency closure;
- exact provider-wire or real-model token accounting without a stable public
  final-immutable-request/tokenizer hook;
- hostile or distributed SQLite storage, encrypted retention, or consensus over
  the active generation;
- a production incident-response or migration system.
