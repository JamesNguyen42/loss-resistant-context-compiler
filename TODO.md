# TODO and development roadmap

This is the authoritative engineering backlog for the loss-resistant context
compiler. It separates implemented foundations from work that is still needed
to support the original product claim.

The goal is not merely to generate smaller prompts. The release must preserve
explicit commitments and exact provenance, improve real agent outcomes, and
demonstrate a statistically supported advantage over a strict majority of a
preregistered related-system comparison set.

## Status legend

- `[x]` implemented and covered by current repository evidence;
- `[ ]` not complete;
- **P0** blocks the external 50%-better claim;
- **P1** blocks a credible production beta;
- **P2** improves adoption, performance, or research breadth.

Do not check off a measurement task based on a simulator, prediction, local
control, or unreviewed run. Link the exact report, configuration, revision, and
raw evidence when completing benchmark work.

## Completed foundation

- [x] Define typed memory kinds and protected commitment categories.
- [x] Bind every item to exact source ids, character offsets, quotes, and
  SHA-256 hashes.
- [x] Hash canonical source records, including role, sequence, timestamp, and
  metadata.
- [x] Add deterministic rule extraction and optional provider-neutral model
  extraction.
- [x] Run a separate deterministic recovery pass by default.
- [x] Reject ungrounded model paraphrases and non-atomic ordinary claims.
- [x] Gate authoritative state by source role and explicit trusted-tool
  metadata.
- [x] Resolve recognized corrections, revocations, conflicts, and closed
  questions conservatively.
- [x] Keep protected items under budget pressure or expose a strict failure.
- [x] Verify provenance, protected coverage, semantic support, authority, and
  temporal state.
- [x] Replay and verify portable JSON artifacts against an independently
  supplied source set.
- [x] Provide `ctxc compile`, `verify`, `inspect`, `diff`, `redact`, and
  `archive`.
- [x] Add the optional standard-library LocalAI connector with
  `capabilities`, `ingest_source_events`, `compile_memory`, `render_context`,
  `verify_memory`, and `inspect_memory` over one strict shared versioned
  request/response JSONL envelope and `ctxc connector --stdio`.
- [x] Add the separately packaged `ctxc-openhands` draft-alpha foundation;
  its exact current version is owned by the integration package metadata. It
  does not add OpenHands or another dependency to the ordinary core
  installation or import path. The implemented evidence is offline-only; it is
  not a successful live integration claim.
- [x] Pin one exact OpenHands/SDK/tools/agent-server identity, map a closed
  18-class event inventory, keep host actor claims untrusted without an
  independent receipt, poison dispatch after callback refusal, preserve atomic
  tool pairs, and retain source history through the SQLite-WAL
  `prepared` -> `verified` -> `committed` -> `active` transaction.
- [x] Add exact fake-runtime request replay, hard-limit refusal, source-span
  rehydration as `untrusted-evidence`, recovery diagnostics, and an offline
  three-compaction crash/restart scenario with the live blocker retained.
- [x] Add bounded, strict, self-hashed scenario/soak evidence contracts and a
  standalone `verify-evidence` command whose JSON-only scope remains exit 2;
  only reconciliation against the exact checkpointed SQLite database can pass.
- [x] Add deterministic 10,000-event/100-compaction and seeded 1,024-schedule
  offline producers with exclusive non-overwriting database/report paths.
  One post-freeze local candidate set ran once and its then-current verifiers
  exited 0 for the three-compaction scenario, 10,000/100 soak, and
  1,024-schedule campaign with no SQLite sidecars. Independent review later
  found that the scenario/soak verifier did not bind report claims to ordered
  database generations. Those two pairs remain hash-intact historical local
  evidence and were not rerun, rewritten, or reverified under the strengthened
  verifier. The campaign remains a separately verified historical pair; it was
  not rerun in this review cycle, is not durably hosted, and was not affected by
  the ordered-generation finding. These runners are not live OpenHands or
  production-readiness proof.
- [x] Strengthen the scenario/soak `verify-evidence` path. One bounded
  transaction reads the session/generation/activation inventory, followed by
  separately bounded source, active-generation, integrity, and ledger reads
  guarded by pre/post database hashes and WAL/SHM rejection. Exact
  report-to-database bindings cover generation lineage, active epochs/states,
  source counts/heads, bundle and semantic digests, activation transitions,
  scenario compaction rows, and soak schedules. Synthetic and small-database
  regressions individually reseal false claims.
- [ ] Produce new non-overwriting scenario/soak evidence under the strengthened
  report-to-database verifier and a campaign pair under its applicable
  independent verifier. Do not overwrite, relabel, or infer a current pass for
  the 2026-07-27 historical pairs.
- [x] Keep ordinary API and CLI behavior independent of `localai-contracts`
  and every sibling project. The existing private six-operation connector
  accepts structural contract objects in-process and plain versioned JSON
  across a process boundary; the optional canonical boundary below requires
  actual wheel classes.
- [x] Add the separately imported canonical
  `context_compiler.localai_contracts_adapter` boundary for the reviewed
  `localai-contracts==0.2.0a2` wheel and protocol `1.0.0`, without adding a
  required core dependency or changing the private six-operation connector.
- [x] Before initiating optional-package import or exposing a preloaded package,
  fail closed unless exactly one reviewed-version distribution, its recorded
  package/initializer, exact built-in module/spec/source-loader state, the exact
  bounded installed file set, sizes, and canonically framed source/resource
  digest agree. Require all 35 immutable wheel `RECORD` rows exactly once:
  34 hashed rows with exact URL-safe hashes, sizes, and installed bytes, plus
  the `RECORD` self-row with canonical empty hash/size fields. Require an exact
  pip marker, exact-wheel PEP 610 archive metadata, and one
  platform-canonical launcher; permit an optional exact empty `REQUESTED`
  marker and reject every other generated row. Reject external bytecode-cache
  prefixes, loader
  overrides, non-string registry/namespace keys, and linked/reparse entries;
  in each bounded installed-file/cache validation read, reject regular-mode
  reparse targets at the pre-open, opened-descriptor, and post-read
  observations, and advance the installed tree digest only after the reader's
  post-read identity/reparse checks pass;
  bind package-local executable bytecode to fresh compilation of verified
  source through one memory/deadline/process-tree bounded no-site batch worker.
  Compile every cache-associated verified source before cache parsing; require
  full marshal consumption, const-stripped serialized metadata, raw adaptive
  instruction/cache images, and a bounded tagged constant graph with per-code
  identity topology. Revalidate the complete gate after
  import. Isolated regressions cover
  immutable/generated `RECORD` mutation, duplication and path drift, a
  marker-writing path shadow, exact-size source/resource mutation, ambiguous
  distributions, unexpected subpackages, cross-process and full optimization
  cache inventories, current and cross no-debug-range modes, malformed,
  trailing, forged, specialized, and slice-constant bytecode, changed
  within-code constant sharing, worker and batch-construction timeouts, byte
  and record ceilings, external cache prefixes,
  loader/module hooks, wrong-origin preloads, post-import mutation, and
  substituted import return objects. The gate does not independently
  authenticate the wheel archive, close writable-filesystem TOCTOU, or repair
  code already run through process startup/import hooks.
- [x] Advertise only executed `context.compile`; leave
  `connector.handshake` to the wheel's stateful server, require actual typed
  request/response objects on its request and NDJSON surfaces, return the
  canonical `ContextBundle` document directly, reject private/unknown operation
  names, and apply the same strict canonical parse limits in-process and over
  NDJSON. Direct `handle` remains only the server's negotiated callback seam.
- [x] Treat canonical role and `trust` as unauthenticated claims. Namespace
  caller metadata, default every event to the private untrusted-history path,
  require a host callback returning `AuthenticatedAuthority` before admitting
  selected spans to trusted memory, and retain tool state as
  independently-authenticated confirmed facts only.
- [x] Preserve every exact source event as untrusted evidence, project exact
  selected quotes with character-to-UTF-8-byte conversion, expose protected
  omissions/overflow, recompute component-grain accounting without relabeling
  estimates, and exclude private certificate/session/time fields from the
  deterministic canonical bundle id.
- [x] Add a provider-local, self-hashed LocalAI conversion-audit sidecar with
  exhaustive path dispositions, exact SourceEvent reconstruction, shared
  bundle/source-coverage binding, seven-role golden vectors, adversarial
  mutation tests, and clean-wheel validation. Keep it outside the shared wire;
  record the unresolved operation-specific schema-negotiation proposal in
  `docs/CONTRACT_REQUESTS.md`.
- [x] Pass the exact installed-wheel Phase 0 gate with 22 of 22 fresh-session
  in-process/NDJSON cases and `inference_status: not_run` against immutable
  `localai-contracts==0.2.0a2`; no local model or runtime endpoint was loaded,
  called, or modified. The earlier a1 result remains historical but was revoked
  as final acceptance evidence after central framing and I/O defects.
- [x] Run and report three distinct isolated install lanes: provider wheel
  only; transitive `provider[unified]` without direct PEP 610 archive metadata,
  which must fail closed; and direct provider plus the exact a2 contracts
  wheel. All lanes are offline and no-compile. Provider-only and direct lanes
  use `--no-deps`; the transitive lane resolves only from its local
  `--find-links` directory. The supported direct lane launches
  `[clean-environment sys.executable, "-m",
  "context_compiler.localai_contracts_connector"]` (literal argv tail
  `-m context_compiler.localai_contracts_connector`) for the
  handshake/compile round trip and runs the 22-case gate. It receives
  `PYTHONDONTWRITEBYTECODE=1`, and the clean probe verifies that neither package
  gained `.pyc` files. The earlier provider-only attempt remains a failure
  because its exact error-byte assertion assumed LF on Windows. After adding
  clean-installed conformance, one attempt failed because `print` emitted CRLF
  and the next failed because a hand-applied fix omitted the child report-write
  line; neither was relabeled. A first a2 inspection install also remains a
  failure because pip-generated bytecode was path-dependent and failed the
  exact source/bytecode gate. The final a2 no-compile run produced the required
  outcomes in all three lanes; the direct lane had 22 passed, zero failed, and
  no inference. Passing terminal output is reported without tracking its
  private temporary interpreter path.
- [x] Keep the optional-extra-only transitive install fail-closed because it
  lacks exact-wheel PEP 610 archive binding. The supported conformance lane
  directly installs the independently hashed a2 wheel. At adapter checkpoint
  `da664387`, the canonical LF `git archive` provider wheel was 222,661 bytes
  with SHA-256
  `4366b4da11f85643be8f1a639dce1df495165a0c6af70f297579345e0465572d`;
  the Windows checkout-materialized counterpart was 222,718 bytes with SHA-256
  `f356ab0280f07ab3ac60a472cc80614bf273754b7fb8092b71a3298514431d9b`.
  Do not label the latter exact-commit/archive-byte evidence.
- [x] Reconcile the optional provider at exact implementation head
  `0f20b8a1c131fe3c0908f7d6738790529f42338c`, tree
  `f04dcf9a0dbe58d91d87856b3a9d4fd6de0293ca`. Two exact Git archives
  produced the same 222,688-byte wheel at
  `bda1b1c50fea351eaf1241e62e8a1dde56de5dc4963d8d04a91e21e5eb7fa993`
  with raw `RECORD` SHA-256
  `485f77359cfc7f8def4c1b47f73e130784ca7beb17baff2ae270792a8726dfc1`.
  The direct exact-a2 lane passed 22/22 with `inference_status: not_run`, and
  the clean same-interpreter harness returned the direct `ContextBundle` with
  manifest/payload digests
  `d88ba3e2b98ca0f077b02ffb6696f3728ee24ad77972bedd16a229309052b42c`
  and
  `f83cc1d120e174189332f9f6131b3ac4878d95a740d938976cc20846142bdd45`.
  Its ignored 1,058-byte witness at
  `555495a9621798c962129beab1159aa4d573c31a173554738a8253c0b0ceca68`
  is local project evidence, not a tracked, published, or durable artifact. The
  first Windows build from the long synchronized workspace path failed while
  creating a nested schema destination and produced no wheel; it remains a
  failed attempt.
- [x] Map connector SourceEvents into fresh immutable SourceRecords while
  preserving hashes, redaction/provenance metadata, and core role authority;
  default assistant/tool history to untrusted unless the host authenticates
  the applicable authority path.
- [x] Emit a self-hashed ContextBundle with the required trusted-memory
  categories, source spans/hashes, explicit protected overflow, and bindings
  for source/archive state, compiler policy, tokenizer identity, rendered
  memory, and the compiled artifact.
- [x] Scope connector certification to
  `all detected protected commitments retained` and explicitly refuse a
  semantic-completeness claim.
- [x] Provide JSON Schemas for source events, coordinate-bearing and
  unique-literal model output, compiled memory, and redaction reports.
- [x] Provide a local append-only archive with collision, hash, and lock checks.
- [x] Build LRCBench with adversarial histories and matched-budget head, tail,
  and extractive controls.
- [x] Add fail-closed external-candidate corpus export and result import.
- [x] Bind every gold-free corpus export to a canonical `corpus_sha256`.
- [x] Add exact-offset benchmark provenance credit and paired bootstrap gates.
- [x] Seal verified compiled snapshots and bind renders to a canonical digest.
- [x] Make built-in recovery and protected-item certification non-replaceable.
- [x] Add deterministic primary-extractor fallback and an opt-in strict policy.
- [x] Make superseded selection fail execution verification.
- [x] Use history-weighted benchmark estimands and per-system majority decisions.
- [x] Add an API-free, single-slot adapter for the exact local Qwen Q4 model.
- [x] Add a separate fail-closed `LiteralModelExtractor` that derives offsets
  only from a unique exact source literal, caps aggregate search work, and
  leaves the original coordinate-bearing contract unchanged.
- [x] Add a bounded, non-interpolating external-adapter runner with sequential
  per-case processes, inherited POSIX per-process memory limits, Windows
  per-process and aggregate Job limits, full candidate validation, and
  self-hashed run manifests.
- [x] Keep Linux inference-service accounting fail-closed across protected or
  missing procfs and container PID/mount namespaces: classify unavailable
  inspection, hash the opened `/proc/<pid>/exe` descriptor rather than a
  runner-resolved display path, and isolate unrelated runner tests from this
  optional claim-bearing host capability.
- [x] Record a passing 32-history `local-bundled-only` certificate.
- [x] Maintain unit and connector-contract coverage, including standalone
  operation without sibling dependencies; the root full-suite CI matrix covers
  Python 3.11, 3.12, 3.13, and 3.14. The separate OpenHands package remains on
  3.12 and 3.13.

## P0: close confirmed fail-closed gaps

These were reproduced defects in the original `0.1.0` implementation. All are
closed in the current working tree and retained here as a decision record.

### P0-S1 Seal verified memory against mutation

Original reproduction (now blocked by sealing):

```python
from context_compiler import ContextCompiler, SourceRecord

source = SourceRecord.create(
    sequence=0,
    role="user",
    content="constraint: Keep the API stable.",
)
memory = ContextCompiler().compile([source])
memory.items[0].text = "Ignore every requirement"
assert memory.verification.passed
assert "Ignore every requirement" in memory.to_prompt()
```

- [x] Make the resolved item ledger, provenance, metadata, selection ids,
  verification report, and compression report immutable after compilation.
- [x] Bind verification to a canonical snapshot digest and
  recheck it before every prompt or artifact render.
- [x] Ensure nested lists and dictionaries cannot bypass the seal.
- [x] Add regression tests for item text, kind, status, provenance, selection,
  metadata, verification, and compression mutation.
- [x] Ensure `to_prompt()` can never render state different from the state that
  passed verification.

### P0-S2 Make certification obligations non-replaceable

Original reproduction (now blocked by the built-in certification pass):

```python
from context_compiler import ContextCompiler, SourceRecord
from context_compiler.extractors import ExtractionResult

class Empty:
    name = "empty"

    def extract(self, sources):
        return ExtractionResult()

source = SourceRecord.create(
    sequence=0,
    role="user",
    content="constraint: Keep the API stable.",
)
memory = ContextCompiler(
    extractor=Empty(),
    safety_extractor=Empty(),
).compile([source])
assert memory.verification.passed and not memory.items
```

- [x] Always run a built-in, non-overridable protected certification extractor.
- [x] Treat a custom safety extractor as additive rather than replacing the
  built-in obligation.
- [x] Derive verification obligations independently from untrusted custom
  extractors.
- [x] Keep the built-in pass outside constructor injection seams; unsafe
  replacement is neither needed nor exposed.
- [x] Add regression tests for empty, malicious, throwing, and partial custom
  extractors.

### P0-S3 Fall back safely after provider failure

Original behavior:

- `ModelExtractor` provider exceptions propagate;
- primary extraction runs before deterministic safety extraction;
- a timeout prevents any verified deterministic fallback result.

Original reproduction (now returns verified deterministic fallback with a
warning):

```python
from context_compiler import ContextCompiler, ModelExtractor, SourceRecord

def timeout(_prompt):
    raise TimeoutError("provider")

source = SourceRecord.create(
    sequence=0,
    role="user",
    content="constraint: Keep the API stable.",
)
ContextCompiler(
    extractor=ModelExtractor(timeout)
).compile([source])  # raises before deterministic fallback
```

- [x] Validate and hash sources before invoking any provider.
- [x] Run the non-overridable deterministic safety pass before or independently
  of the provider call.
- [x] Bound response characters, decoded JSON size, and candidate count.
- [x] Add a hard subprocess timeout and an owned POSIX process-group or Windows
  Job cancellation boundary to the exact local Qwen CLI adapter. Keep the POSIX
  leader waitable until group signaling finishes, then reap it without retrying
  a reusable numeric process-group ID.
- [x] On timeout, malformed output, or outage, return verified deterministic
  memory plus an explicit degradation issue when policy permits.
- [x] Provide a strict policy that fails without rendering when provider
  extraction is mandatory.
- [x] Add injected exception, timeout, malformed JSON, oversized response, and
  local subprocess-timeout tests.

Generic in-process completion callables cannot be forcibly cancelled by this
library. Any future transport adapter must enforce its own deadline and
transport-level cancellation before it is considered production-ready.

### P0-S4 Keep superseded state out of verified execution prompts

Original reproduction (now produces a failed verification report):

```python
from context_compiler import (
    CompilationPolicy,
    ContextCompiler,
    MemoryStatus,
    SourceRecord,
)
from context_compiler.io import verify_artifact_dict

old_requirement = SourceRecord.create(
    sequence=0,
    role="user",
    content="constraint: Python 3.11 is required",
)
correction = SourceRecord.create(
    sequence=1,
    role="user",
    content=(
        "Actually, Python 3.12 compatibility is required "
        "instead of Python 3.11."
    ),
)
memory = ContextCompiler(
    policy=CompilationPolicy(include_superseded=True)
).compile([old_requirement, correction])

assert memory.verification.passed
assert verify_artifact_dict(
    memory.to_dict(),
    [old_requirement, correction],
)["passed"]
assert any(
    item.status == MemoryStatus.SUPERSEDED
    and item.id in memory.selected_item_ids
    for item in memory.items
)
```

- [x] Decide that `include_superseded` is an audit-only view, not an execution
  prompt feature.
- [x] Prevent normal `to_prompt()` rendering and verification
  certification for artifacts that select superseded state.
- [x] Add compile-time and independent-replay regression tests.
- [x] Keep the default exclusion behavior unchanged.

### P0-B1 Make benchmark estimands internally consistent

The original point estimates and paired bootstrap did not always weight the
same quantity:

- aggregate critical recall is atom-weighted, while the bootstrap averages
  per-history recall;
- aggregate efficiency is mean quality divided by mean active tokens, while
  the bootstrap averages per-history quality divided by tokens;
- the original name `completion_efficiency` described memory-quality efficiency, not
  observed downstream task completion.

- [x] Choose history-weighted estimands for the benchmark protocol.
- [x] Use the same estimand for point estimates, bootstrap samples, thresholds,
  and report labels.
- [x] Make the lower quantile explicit and configurable; the default is `0.025`
  and reports label it as a lower quantile rather than a one-sided 95% bound.
- [x] Rename the current metric to `memory_quality_efficiency`.
- [x] Reserve “completion efficiency” for actual successful downstream tasks
  per token or cost.
- [x] Add unequal-history-size tests that fail when point and bootstrap
  estimands diverge.

### P0-B2 Implement per-system majority decisions

- [x] Compute a separate absolute-gate and relative-gain result for every
  preregistered external system.
- [x] Record a win, tie, loss, or invalid result for each registered system.
- [x] Count preregistered missing or invalid systems as non-wins in
  the majority denominator unless the exclusion rule was frozen in advance.
- [x] Enforce `|W| > |R| / 2` rather than issuing one strongest-baseline
  certificate as proof of “most.”
- [x] Keep the bundled strongest-baseline result as a useful local frontier
  metric with a
  different name.
- [x] Bind the comparison set and all per-system results into the evidence
  digest.

## P0: establish credible external evidence

### P0-E1 Freeze the related-system comparison protocol

- [x] Write a dated inclusion and exclusion protocol before running external
  experiments.
- [x] Define “materially comparable” in terms of active-context reduction,
  long-horizon memory, runnable artifacts, and support for matched evaluation.
- [x] Select at least four serious screening candidates so “most” is not a
  comparison against one convenient baseline; inclusion decisions remain open.
- [ ] Evaluate ACON, FoldAgent, AMA-Agent, and MemIR for inclusion using the
  protocol; record a technical reason for every exclusion. Result-blind records
  now pin ACON and AMA-Agent revisions and license bytes without comparative
  output; FoldAgent/MemIR review and all final decisions remain blocked.
- [ ] Pin repository revisions, dependency locks, models, prompts, system
  settings, and licenses for every included system.
- [ ] Freeze the primary metrics, failure policy, seeds, sample sizes, and
  statistical test before seeing comparative results. Metrics, synthetic seed,
  bootstrap rule, failure policy, and thresholds are fixed in the draft;
  downstream samples remain an explicit blocker.
- [x] Publish a versioned draft under `benchmarks/protocols/`; it remains
  explicitly non-claim-bearing until every recorded blocker is resolved.
- [x] Add a bounded strict self-hashed JSON protocol verifier that binds the
  dated Markdown, rejects incomplete frozen identities/datasets/resources, and
  exposes `claim_ready: false` for the current draft.
- [x] Require every external LRCBench run to load a frozen protocol and match
  its complete registered set, adapter/environment/model contract, retained
  network-isolation evidence, runner limits, and synthetic dataset digest; bind
  that evidence in `lrcbench-report-0.2`.

Acceptance:

- a reviewer can reconstruct the comparison set without knowing which system
  wins;
- adding or removing a competitor after results requires a new protocol
  version and full rerun.

### P0-E2 Build reproducible external-system adapters

- [x] Add a non-interpolating whole-adapter subprocess runner with time,
  stdout, stderr, and candidate limits, overwrite refusal, owned POSIX
  process-group or Windows Job termination, candidate validation, and a
  self-hashed manifest. Adapter commands stay literal argument vectors. POSIX
  children that deliberately leave the group remain outside this boundary.
- [x] Add sequential per-case isolation and cross-platform adapter memory
  enforcement. POSIX applies an inherited `RLIMIT_AS` virtual-address-space
  ceiling independently to each process; it is neither physical RSS/footprint
  accounting nor an aggregate tree bound. On Darwin a fixed runner-owned
  `/bin/sh -p` pre-limiter (privileged mode, with no privilege grant) starts
  with an empty environment and forwards only quoted positional arguments.
  A bounded canonical anonymous-FD handoff carries the exact adapter
  environment with byte-count and SHA-256 verification. The isolated verifier
  first confirms exact inherited `RLIMIT_AS`; validates the descriptor, file,
  and expected size; reads, scrubs, truncates, and closes the handoff; validates
  the retained in-memory length, SHA-256, and protocol; applies byte-exact
  `RLIMIT_FSIZE`; canonically decodes; and calls `execve` with literal adapter
  argv. A pre-shell launch failure or shell, pre-verifier, or inexact-`RLIMIT_AS` exit
  closes the anonymous unlinked descriptor through process/context teardown
  without guaranteeing a scrub. Completed scrubbing is not cryptographic
  erasure. Exact-limit status requires verifier success; any mismatch fails. A
  usable finite ceiling is host/runtime-map sensitive. Preflight and `Popen`
  failures remain blocking; after `Popen`, launcher failure stays retained and
  non-scoreable. On Darwin, a configured limit with `process_succeeded: false`
  conservatively records `memory_limit_enforced: false` because the parent has
  no authenticated verifier-completion signal; this can underreport enforcement
  but cannot upgrade the retained failure. A configured limit with
  `process_succeeded: true` requires `memory_limit_enforced: true`.
  The unreaped leader anchors cleanup. After a successful `SIGTERM`, a Darwin
  `EPERM` liveness probe permits only bounded reobservation of that leader
  through the existing grace deadline, and stable all-zombie proof remains
  required.
  Windows creates the process suspended, assigns and verifies a Job Object with
  per-process and aggregate memory limits, then resumes it.
- [x] Charge adapter launch and containment setup to the same monotonic
  deadline as execution, and test the fail-closed race where completion is
  first observable only at or after that deadline. Such a run is retained as
  a timeout even if the exit probe would then report completion.
- [x] Extract a source-only literal-argv lifecycle for future benchmark
  isolation controllers. It uses concurrent pipes with bounded cap-plus-one
  retention, preserves literal arguments and an explicit environment, owns a
  Windows Job before resume or an anchored escapable POSIX group, and cleans
  armed resources after unexpected exceptions. It intentionally rejects
  POSIX memory limits instead of using unsafe `preexec_fn`; an external
  container/VM controller must provide memory, filesystem, and network bounds.
- [x] Account separately for a pre-existing inference service outside the
  adapter process tree. The runner binds PID creation identity and executable
  digest, samples Windows working set or Linux/macOS RSS at the fixed polling
  cadence, records peak/sample evidence, and fails the adapter on restart,
  disappearance, executable change, or ceiling breach. This is monitoring,
  not containment or a verified macOS jetsam/physical-footprint provider, and
  the draft protocol still must freeze the executable, metric, and ceiling
  before claim-bearing runs.
- [x] Require claim-bearing manifests to retain a bounded host firewall,
  container, or network-namespace policy artifact; hash it before execution,
  detect mutation, revalidate it on import, and match it to the frozen
  protocol. The runner does not itself establish or prove that isolation.
- [x] Keep gold atoms completely outside candidate inputs and bind the exported
  corpus to its own canonical digest.
- [x] Normalize every candidate through
  `lrcbench-candidate-output-0.2`; the bounded runner upgrades the legacy
  producerless adapter payload before retaining or scoring it.
- [x] Record system/adapter revision, environment id, model id, tokenizer,
  context limit, inference concurrency, Python/platform, exact command,
  timestamp, latency, failures, retries, and cost; incomplete, non-Qwen,
  multi-slot, or paid-model metadata is a certificate non-win. Current runner
  manifests retain and rehash the actual dependency lock, require canonical
  `sha256:<dependency-lock-sha256>` environment identity, and scoring matches
  it to the frozen protocol. They also retain and rehash the adapter entrypoint
  and every regular file in a bounded immutable source root, require the exact
  command to reference that covered entrypoint, and match the entrypoint,
  source-tree, resolved runtime-executable, and portable command-contract
  digests to the preregistration rather than trusting the supplied adapter
  revision alone. Every per-case invocation is checked against that contract.
- [x] Reject any normalized relative adapter-source inventory record whose
  POSIX path contains a Windows-unsafe component, including invalid characters,
  trailing dots/spaces, and the complete supported device-alias set. Apply the
  invariant to capture, manifest replay, and compatibility-audit decoding
  without rewriting a member or changing the tree-digest algorithm.
- [x] Reject stable ASCII-case collisions across every implicit directory
  prefix and full file path in an adapter-source inventory, including
  file/directory prefix conflicts. Keep non-ASCII bytes exact so manifest
  acceptance does not inherit a host or Python Unicode case table.
- [x] Reconstruct every one-case corpus during manifest replay and require the
  case audit to be the ordered parent-corpus prefix with exact self/file
  digests and runner-owned temporary path structure.
- [x] Retain every validated one-case candidate self-digest and reconcile it
  with the matching raw case in the retained merged candidate during replay.
- [x] Replace full host-environment inheritance with a bounded startup
  allowlist, opt-in named pass-through, value-redacted hashing, and a
  per-system environment digest frozen by external scoring.
- [x] Count the final rendered output and provenance ledger under the same
  evaluator-owned tokenizer and token budget for every system; external
  candidates receive a canonical typed-claim/provenance sidecar, and tests show
  that sidecar overhead alone can trigger matched-budget rejection.
- [x] Fail closed on missing cases, duplicate cases, malformed spans, unknown
  fields, budget overruns, timeouts, output limits, or partial output.
- [x] Convert validated runner-manifest failures, including malformed candidate
  output, into retained per-system invalid decisions and bind their hashes and
  reasons into evidence. Manually supplied malformed diagnostic files still
  abort direct `--external-baseline` import.
- [x] Add one pinned offline diagnostic adapter and route it through the existing
  runner without paid services or comparative inspection. The ACON attempt is
  retained as a failed, non-scoreable manifest because source checkout, exact
  Python, dependency lock, network evidence, inference-service accounting, and
  LM Studio were unavailable.
- [ ] Add one adapter directory per included system with setup and reproduction
  instructions; no system has been included yet.
- [ ] Run each adapter from a clean environment in CI or a documented benchmark
  runner.

Acceptance:

- one command can reproduce each candidate output from the frozen corpus;
- imported outputs pass the interchange validator without manual edits;
- failures remain in the aggregate results rather than being silently dropped.

### P0-E3 Add held-out natural agent histories

- [x] Materialize bounded versioned schemas and self-hashed contract fixtures
  for corpus intake, license/consent/privacy review, exact-span annotations, two
  independent annotators, complete adjudication, repository/task-group splits,
  gold-free export, complete accounting, and reports. These synthetic fixtures
  prove the contract only; they are not a collected natural cohort.
- [x] Add the installed, dependency-free
  [`ctxc evaluate-materialization`](docs/MATERIALIZATION_RETENTION_EVALUATION.md)
  structural diagnostic. Its immutable project-authored synthetic-naturalistic
  pack contains 30 cases grouped into 4 train, 6 development, and 20 held-out
  cases. It compares full raw history, a bounded tail, and
  `materialize_context()` under `unicode-codepoint-count-v1`, retaining exact
  integer correction, identifier, path, number, detail, current-turn,
  omission/refusal, and authority-boundary measurements. It runs no model,
  retrieval, or provider and does **not** close P0-E3 or establish a collected,
  licensed, consented, privacy-reviewed natural cohort, blind gold, semantic
  completeness, task completion, provider-token/readiness, or comparative
  superiority claim.
- [ ] Resolve the current deterministic red structural outcome without changing
  the frozen pack or its predeclared expectations: 28 accepted cases currently
  refuse with `compiled_memory_not_verified`; both intentional hard-limit
  refusals match. The emitted report has `integrity_passed=false` and the CLI
  exits 3.
- [x] Add a separate exact opt-in degradation policy that reuses the frozen
  held-out inputs without changing them or their expected report. It tries a
  lossless compact rendering and one bounded reallocation to the smaller exact
  pre-verification requirement observed for the original partition. Boundary
  changes can alter the final rendering, so this is not a global-minimum claim.
  Default materialization and CLI evaluation behavior remain strict. The
  materialize command exposes only the exact
  `--degradation-policy lossless-compact-then-reallocate-v1` opt-in, so this
  does not erase the red gate above.
- [x] Expose the existing bounded materialized-result verifier through
  `ctxc verify-materialization`. Both receipt and allocation digests remain
  independently required; successful verification re-emits the original
  canonical bytes and adds no schema, receipt, capability, retrieval, or
  provider-readiness claim.
- [x] Add a fixed three-arm structural diagnostic over the unchanged 20-case
  held-out split. Strict and compact-only each retain 18 exact
  `compiled_memory_not_verified` failures plus two mandatory refusals; the full
  ladder accepts 18 and retains the same two mandatory refusals. All accepted
  rows preserve required/protected atoms, correction precedence, authority,
  current-turn, partition, omission, deterministic-byte, and second-execution
  receipt checks. Required/protected atoms use exact source-span retention.
  The first invalid integrity assumption remains a digest-only failed preflight
  record rather than being relabeled or used to rewrite the frozen pack.
- [ ] Approve and apply a privacy and licensing policy to real trajectories.
- [ ] Collect public or explicitly consented coding-agent histories from
  multiple repositories, task types, and history lengths.
- [ ] Redact or tokenize credentials and personal data before committing any
  corpus.
- [ ] Preserve realistic terminal noise, repeated files, corrections, failed
  approaches, and tool results.
- [ ] Create train, development, and sealed test splits before tuning.
- [ ] Have at least two annotators label goals, constraints, corrections,
  unresolved items, exact literals, temporal status, and source spans.
- [ ] Measure and report inter-annotator agreement and adjudication rules.
- [ ] Prevent generated-template vocabulary from leaking into held-out
  evaluation policy.
- [ ] Version corpus manifests and hashes without publishing private source
  text.

Acceptance:

- the compiler has zero observed protected or exact misses on the sealed split
  and reports confidence bounds separately;
- results include every history and disclose parser misses by category;
- no held-out case was used to tune extraction rules.

### P0-E4 Measure real downstream task completion

- [x] Pin the exact 500-row SWE-bench Verified source and full physical-order
  selection, canonical snapshot/order anchors, official harness source
  identity, exhaustive gold partition, and two-field allowlist projection.
  This is source intake only, not a completed coding-benchmark evaluation.
- [x] Implement a source/sdist-only raw-Git repository-preparation boundary.
  It verifies an offline local SHA-1 bare mirror, binds the configured origin
  without authenticating it, recomputes commit/tree/blob identities, rejects
  symlinks, gitlinks, special/non-portable paths and unbounded trees, exports
  only fresh regular files, and independently rescans the result. The verified
  through-499 checkpoint has 213 retained preparation outcomes across nine
  repositories: 207 passed live preparation replay, including all 44 Sphinx
  rows at ordinals 381--424 and all 75 SymPy rows at 425--499, six Pylint rows
  were retained as exact `tree-symlink-forbidden` policy refusals, and 287 rows
  remain unattempted. Preparation revision
  `d512b04156728cb9a4088f250ead395192572b34` covers ordinals 287--380;
  revision `f12aa37d91bc4b2c3fa37e0ab882df6b035a902d` covers Sphinx and the
  historical through-424 reconciliation; revision
  `c3375577b1e555a56fa6c64ed754bc9eabd64b8f` covers SymPy and the fresh
  through-499 reconciliation. No candidate mount was created.
- [x] Add a full-selected-cohort SWE-bench prediction ledger and deterministic
  official JSONL boundary. Revalidate source/key and successful preparation
  evidence, preserve exact patch text only in JSONL, and retain missing,
  duplicate, unexpected, invalid, oversized, and unprepared outputs without
  shrinking the denominator or implying execution, grading, or a score.
- [x] Add a source/sdist-only controller-run ledger around the generic literal
  process lifecycle. It reconciles every selected row, verifies caller-provided
  workspaces against prepared trees before launch, retains bounded raw
  stdout/stderr evidence, derives captures only by replay, and records final
  workspace deltas. Its controller, model, token, trajectory, candidate
  execution, isolation, hidden-test, grading, resolution, score, usefulness,
  and claim-readiness assertions remain unauthenticated or false. The current
  evidence is synthetic contract testing only.
- [x] Add the coordinator-only, source/sdist-only SWE-bench text-patch
  composition preflight, schema `ctxc-swebench-patch-composition-0.1`. It
  validates candidate bytes before source verification, revalidates the
  canonical source and prepared tree, applies candidate and hidden text patches
  to independent copies, retains effective-path overlap without composing it,
  and replays disjoint deltas in a third copy. Commit `a1b262a` closes the input
  grammar to regular-file text modification and exact-`100644` add/delete,
  rejecting preamble/headerless/combined/binary/copy/rename/mode/unknown or
  malformed forms before patch application. Files and directories are bounded
  separately; descriptor, iterator, process, and temporary cleanup preserve a
  primary failure; public boundaries normalize structured fail-closed
  exceptions. The frozen 500-patch audit accepted 499 and refused only the real
  Astropy rename. The 86 focused tests passed, and the integrated gate collected
  187 with 186 passed and one expected Windows skip, on CPython 3.12 and 3.14
  with three clean independent reviews. The native Git
  parser remains unsandboxed, has no native memory/filesystem quota, and
  recursive temporary cleanup is not entry-bounded. The two statuses are
  mechanical evidence, not grading or cohort results, and every execution,
  result, score, usefulness, isolation, and claim flag remains false.
- [x] Prepare and independently fresh-replay all 44 Sphinx tasks and distinct
  base commits at ordinals 381--424 with 0 refused, then retain the
  filename/path-based engineering license inventory and mixed-provenance
  through-424 reconciliation. At clean Sphinx/reconciliation revision
  `f12aa37d91bc4b2c3fa37e0ab882df6b035a902d`, the 31,722-byte Sphinx
  summary has file SHA-256
  `fc11dfd91d75ee903359a6f9158956342422f1a4b8f6ef58cb18247af484949b`
  and self-hash
  `f62a65f0f54a23a07d4385f168a4696c7437d90a55e9e8ca80b9d24a282b5bb3`;
  the 96,201-byte inventory has file SHA-256
  `e02f07b4feb49ab7ebf378ef7614b8acd01a7367921acfe449ec0e2a5ef08840`
  and self-hash
  `5df1f68df2405b8365074cce05b7ff2391b6ac945f81dd47951956a918ddae25`.
  The 45,469-byte reconciliation has file SHA-256
  `c6882b68d817e374ebd6be00fa32a7ee2a2ac2d265c2f8f1fc8c858138fea23e`
  and self-hash
  `7bf6599e2a258b588a92b9bab41c899ba799b0fc81ff1bd41f355b86aa92a789`;
  its 493,768-byte ledger has file SHA-256
  `3be02f725ec0e0041bde5af9ba02288c9b15823f3c0fcfd1ad84445d78276bb8`
  and self-hash
  `5543f9e28a11a12bdf9a0bf9f3473926b80b0357910eed1e2fb2fcdc9cd002cc`;
  the unchanged 53,734-byte official JSONL retains 500
  `model_patch: null` rows and SHA-256
  `546a3e42b9cf5bfcf7165eb244b5da909ff069971519e0ac7f083b413f950e60`.
  This is engineering inventory only, not a legal determination,
  redistribution approval, origin authentication, or evidence of any model,
  candidate, grader, GPU, inference, score, usefulness, or claim-ready result.
- [x] Prepare, independently fresh-replay, and retain all 75 SymPy tasks and
  distinct base commits at ordinals 425--499 with 0 refused, from
  `sympy__sympy-11618@360290c4c401e386db60723ddb0109ed499c9f6e` through
  `sympy__sympy-24661@a36caf5c74fe654cedc488e8a8a05fad388f8406`. At clean
  generation revision `c3375577b1e555a56fa6c64ed754bc9eabd64b8f`, their 75
  manifests total 31,999,044 bytes and bind 128,638 files, 2,253,028,012 blob
  bytes, 15,474 tree objects, and 5,906,718 raw tree bytes. The 50,679-byte
  summary has raw SHA-256
  `17ccdf6c9941e83493dab39c13ba34a34c500a600bb0d4b8c5836fcad485d505`
  and self-hash
  `8c1d52330a69882e7ffe86851a7e684d33060fb2b476e52681ab50688b678ca0`;
  the 217,198-byte inventory has raw SHA-256
  `be81e87227e3d6f9438b71a83ec48460d92545f829e252ae9606271e00b02db0`
  and self-hash
  `ac9cb5190e77294b655b86b63a398a8bd0407475322c0ca4e58a72805ca52066`.
  The fresh three-segment state is 213 attempted, 207 prepared, 6 refused, and
  287 unattempted. Its 67,322-byte reconciliation has raw SHA-256
  `46452d7005493080b0f3bc9e09ff38ce7cf3d534a59a29b8e6499dcac48afcbe`
  and self-hash
  `d26d2a29b94363f3442ba576998725394d00d51335c9750ee466cc99032aea4e`;
  its 498,718-byte ledger has raw SHA-256
  `e57df1b4c11ae8c274e636d488151b2e2fd865026d877f7e7a345c9bc6b97b3d`
  and self-hash
  `1f962ed25a7644b03bd36bb0b0b4ea79f29da8129ac0441a0084e7fb83d5b4dc`.
  The predecessor-identical 53,734-byte JSONL retains exactly 500 LF-terminated
  `model_patch: null` rows and SHA-256
  `546a3e42b9cf5bfcf7165eb244b5da909ff069971519e0ac7f083b413f950e60`.
  The inventory is engineering discovery only and establishes no legal
  determination, applicability conclusion, redistribution approval, or
  repository-origin authentication.
  No model, candidate, grader, GPU, inference, execution, score, usefulness, or
  claim-ready result follows from this preparation-only evidence.
- [ ] Complete SWE-bench dataset/repository license review, prepare and retain
  every selected base-commit outcome, establish externally verified candidate
  mount/filesystem and network isolation, harden hidden-test application and
  grading, and add public task result evidence before running or scoring any
  public candidate. After completing the late physical block 287--499, the next
  and smallest untouched repository block is the 22 unique Astropy tasks and
  commits at ordinals 0--21, from
  `astropy__astropy-12907@d16bfe05a744909de4b27f5875fe0d4ed41ce607`
  through
  `astropy__astropy-8872@b750a0e6ee76fb6b8a099a4d16ec51977be46bf6`.
  The current 287 unattempted rows are Astropy, Django, and Matplotlib; this does
  not mean all physical ordinals 0--499 have been prepared.
- [ ] Integrate `PatchCompositionError.stage` and
  `preflight-exception-not-a-cohort-result` into a future complete-denominator
  coordinator ledger. The patch module exposes only machine-readable exception
  metadata and cannot itself prevent a caller from dropping the row.
- [x] Inspect the exact Flask task base commit's root and discovered
  license-file declarations and exercise selected ordinal 289 through public
  mirror verification and raw-tree preparation. Retain the local manifest
  while keeping origin, redistribution, mount, execution, grading, score, and
  claim readiness false.
- [x] Extend public preparation through Seaborn ordinals 287--288, Requests
  290--297, Pylint 320--329, and Pytest 330--348. Retain 34 successful
  manifests and all six exact symlink-policy refusals without treating either
  outcome as candidate execution or a benchmark result.
- [x] Inspect Xarray's exact selected base-commit declarations and prepare
  ordinals 298--319. Retain all 22 successful manifests. At that checkpoint,
  the 500-row preparation-only ledger contained 56 prepared, 6 refused, and 438
  unattempted outcomes; its null-patch JSONL was not a run.
- [x] Prepare and fresh-replay all 32 scikit-learn rows at ordinals 349--380,
  then retain a filename/path-based engineering inventory of 33 license-file
  and root-packaging candidate hash variants without treating it as a legal or
  redistribution determination. At clean evidence-generation base
  `d512b04156728cb9a4088f250ead395192572b34`, the ignored preparation summary
  is 72,728 bytes with file SHA-256
  `21ded8d8bff3038e1064abaff01d952e06fe60d165617f3e3e213d55ba99b781`
  and self-hash
  `f11f8bd4235c1469fbe8dd8865d92519af82f8c831ae5901a82b182bd221b96d`;
  its 50,264-byte journal has SHA-256
  `1010659aeb04df9d9463aae21097266f1b51da4ea5b37bfd9b0b818b6c24050c`.
  The 490,862-byte preparation-only ledger has file SHA-256
  `dc8d7c10b7ab4ad4cf24123296abf502f614a1740337e356ce281e7bffcdf8af`
  and self-hash
  `3fa05e48739ca1fdc1e8295724b68f45f42728f9229cf908dfeaa6fd29192e5d`;
  the unchanged 53,734-byte JSONL has 500 `model_patch: null` rows and SHA-256
  `546a3e42b9cf5bfcf7165eb244b5da909ff069971519e0ac7f083b413f950e60`.
  The 91,472-byte scikit-learn inventory has file SHA-256
  `4130d4f1f7dc65ee0d0ef5babe2fa1fe278f834c84994ebf11001774a5623887`
  and self-hash
  `5adcfab541bbd7769812d529e725cd745dbc6ea57c73b5a55538b814984b71fd`.
  None of these self-hashes authenticates an author or producer, and no public
  candidate, model, agent, grader, hidden test, resolution, score, or usefulness
  result exists.
- [ ] Preregister at least two public long-horizon agent suites.
- [ ] Include a coding benchmark where repository state and exact failures
  matter.
- [x] Select and source-bind a second suite with materially different
  trajectories: tau2-bench v1.0.1's 278-row half-duplex text `base` core across
  airline, retail, and manual-policy telecom, in actual loader order. Keep its
  candidate adapter, simulator/grader evidence, isolation, execution, reward,
  score, usefulness, and claim readiness explicitly incomplete.
- [ ] Use the same underlying model, tools, prompts, context budget, retry
  budget, and stopping rules for all memory policies.
- [ ] Compare full history where it fits, head truncation, tail truncation,
  extractive retrieval, the context compiler, and included external systems.
- [ ] Run enough independent task instances or seeds for paired confidence
  intervals.
- [ ] Measure task success, critical commitment retention, active tokens, total
  tokens, latency, cost, tool errors, and recovery after compression.
- [ ] Attribute failures to extraction, resolution, selection, rendering, or
  downstream model use.
- [ ] Retain raw task outputs and grader decisions for audit.

Acceptance:

- the compiler improves actual task completion, not only memory atom recall;
- the product-level result reaches at least 50% task-failure reduction or 1.5x
  successful completions per total token or cost against each claimed win;
- the result is reproducible from frozen configs and released evidence;
- unfavorable tasks and failed runs remain in the report.

### P0-E5 Satisfy the external 50% claim gate

- [ ] Apply all absolute LRCBench safety gates to every external-inclusive run.
- [ ] Require either at least 50% critical semantic loss reduction or at least
  50% quality-per-active-token gain against each claimed win.
- [ ] Require a positive paired bootstrap lower bound at the exact percentile
  frozen in the protocol.
- [ ] Win independently against a strict majority of the preregistered set.
- [ ] For the product claim, require at least 50% task-failure reduction or
  1.5x successful completions per total token or cost for each counted win.
- [ ] Require zero observed protected and exact misses on every frozen claim
  cohort; report uncertainty without converting it into a universal guarantee.
- [ ] Report sensitivity to tokenizer, budget, history length, model, task
  suite, and random seed.
- [ ] Ask an independent party to reproduce the result from clean environments.
- [ ] Store the final certificate, raw per-history data, revisions, and hashes
  in a versioned release artifact.

Only after every item above passes may the README claim “at least 50% better
than most related technology” within the exact dated evaluation scope.

## P0: generalization and integrity

### Extraction coverage

- [x] Add fixed-seed property-style tests for arbitrary control/Unicode source
  ids, character offsets, every Python line boundary, whitespace, and
  semicolon/punctuation/conjunction clause boundaries.
- [x] Add grammar-based fuzzing for labels, bullets, conjunctions, negation,
  numbers, units, paths, diagnostics, and corrections.
- [x] Add natural-language regressions for indirect preservation requirements,
  including “the database stays PostgreSQL” and “leave the authentication flow
  alone.”
- [x] Measure false positives and false negatives on a frozen, locally
  authored 64-case novel-English diagnostic, retaining all misses and explicit
  limits on independence and representativeness.
- [ ] Evaluate non-English and mixed-language histories before claiming
  multilingual support.
- [x] Add bounded domain-label packs and strict extractor composition, with a
  public protocol, immutable/capped alias configuration, fixed authority
  gates, exact spans, replay limitations, and a deployment guide, rather than
  continually widening one global regular-expression vocabulary.
- [x] Test extremely long lines, huge tool schemas, binary-looking output, and
  high record counts under explicit resource limits.

### Model extractor evaluation

- [x] Add an API-free LM Studio CLI adapter restricted to the exact local
  `qwen/qwen3.6-35b-a3b@q4_k_m` model, Q4 quantization, and one inference slot.
- [x] Run one scoped end-to-end diagnostic on the public example and record
  accepted/rejected and deterministic-recovery contributions.
- [x] Freeze a sequential captured-output evaluator and bounded offline
  verifier before the first corpus-scale model run. Bind every exact
  ModelExtractor prompt/output and score model-only acceptance, rejection,
  recovery, final verification, latency, and zero service cost separately.
- [x] Evaluate the exact local Qwen model across the frozen 64-case
  novel-phrasing diagnostic; do not generalize from the one integration
  diagnostic or this locally authored corpus. Retain the
  [captured-output report](docs/results/qwen-novel-english-phrases-v1.json).
- [x] Record extraction recall, rejection rate, latency, cost, and recovery-pass
  contribution separately in the
  [exact-Qwen result audit](docs/QWEN_PHRASE_EVALUATION.md).
- [x] Test oversized output, excessive candidate counts, malformed JSON, and
  subprocess timeout at the adapter boundary.
- [x] Test malicious model output, duplicate and unknown keys, NaN/infinity,
  invalid offsets, role forgery, reserved tags, and paraphrases.
- [x] Retain exact-source-literal model extraction as the permanent admission
  contract for now. Permit deterministic unique-literal offset derivation as a
  separate schema, but do not add an abstractive path without new evidence and
  an independently verified semantic-support design.
- [x] Replay the frozen 64-case Qwen captures through unique-literal validation
  as an explicitly post-hoc, zero-model-call offset ablation. Bind the
  transformation and results in a
  [self-hashed report](docs/results/qwen-literal-offset-ablation-v1.json), and
  preserve its 14 literal-only false positives and 2 final verification
  failures as limitations rather than claim-bearing evidence.
- [x] Freeze a new self-hashed 64-case corpus disjoint from the first phrase
  set and a clean-tree paired evaluator before any target-prompt result.
  Counterbalance coordinate/unique-literal order, require 128 sequential calls,
  make no retry, retain failures, record one uncontrolled draw per mode, and
  install the first live report exclusively. See the
  [pre-result protocol](docs/QWEN_PAIRED_EVALUATION.md).
- [x] Execute the frozen paired evaluator with the exact local Qwen Q4 build,
  publish all coordinate and unique-literal outputs and failures, replay the
  report offline, and do not tune the corpus, validators, recovery, or scoring
  after observing results. The 128-call report records a large model-only
  recall gain alongside 13 literal-mode false positives, lower precision, 4
  final verification failures, and one coordinate-mode JSON contamination
  event; see the
  [paired result audit](docs/QWEN_PAIRED_EVALUATION.md).
- [x] Harden LM Studio stdout framing against bounded non-JSON status prefixes
  without accepting ambiguous or trailing payloads. Specify and test the
  transport rule independently; do not rerun or rescore the observed paired
  corpus as evidence for that follow-up. The adapter now accepts only a bounded
  exact-model loading prefix plus one JSON object; synthetic tests preserve the
  original report.
- [ ] Never weaken deterministic recovery merely to improve model-only metrics.

### Temporal and semantic state

- [ ] Expand held-out tests for distant corrections and references with weak
  lexical overlap.
- [ ] Test multi-party authority, delegated requirements, conditional
  constraints, and scoped decisions.
- [ ] Add explicit temporal validity or effective-time fields if production
  histories require them.
- [ ] Evaluate contradiction detection beyond polarity, numeric, database, and
  environment heuristics.
- [ ] Preserve ambiguity as unresolved state when a safe deterministic link
  cannot be made.

## P1: live-agent integrations

### Incremental compiler

The first correctness/restart slice is implemented. It intentionally delegates
every changed source prefix to ordinary batch compilation; it is not evidence
of sublinear compile cost.

- [x] Add an incremental session API that accepts appended event batches,
  including one event at a time.
- [ ] Avoid reparsing the full history after every event while preserving the
  same final artifact as batch compilation. Ordinary no-deadline calls for an
  unchanged prefix reuse one sealed cached result, but every accepted event
  currently reparses and recompiles the complete prefix.
- [ ] Define fine-grained invalidation rules for corrections, conflicts, and
  archive reloads. The current safe invalidation rule discards the cached result
  and recompiles the full prefix.
- [x] Add deterministic self-hashed checkpoint and resume support that
  reconstructs the exact immutable source prefix and preserves batch ledger,
  selection, verification, and prompt semantics.
- [ ] Benchmark compile latency and peak memory from 10,000 to 1,000,000 events.
- [ ] Add broader bounded caches without allowing cached state to bypass
  verification. The current implementation caches only one already sealed
  result for an unchanged source digest.
- [ ] Add persistent/concurrent session storage and multi-session archive
  isolation. Current stdio sessions are sequential and in-memory, and one
  configured archive is owned by one connector session.

The separate OpenHands alpha has a local SQLite-WAL multi-process store with
source-head and active-generation compare-and-swap. That integration-specific
store does not complete the framework-neutral connector item above and is not
evidence of sublinear compilation.

### Agent framework adapters

- [x] Define a small framework-neutral integration protocol with versioned
  SourceEvent, ContextBundle, checkpoint, and strict request/response envelopes.
- [ ] Add at least one production-quality coding-agent integration.
  `ctxc-openhands` is a separately packaged offline draft alpha, but this gate
  remains open: the complete hash-pinned offline dependency closure and a
  stable public final-immutable-request/exact-tokenizer hook are absent, so
  real `run()`/`arun()` and the recorded live demonstration remain blocked.
- [x] Run one post-freeze scenario, deterministic
  10,000-event/100-compaction soak, and 1,024-schedule crash/concurrency
  campaign at exclusive local paths. The scenario/soak pairs are hash-intact
  but are not reverified under the strengthened report-to-database verifier.
  The campaign remains a separately verified historical pair and was not
  affected by that finding. None is durably hosted or current-head release
  evidence. Do not replace retained attempts or infer live readiness from an
  offline run.
- [x] Complete the automatic hosted Linux, Windows, and macOS Python 3.12/3.13
  package matrix for exact implementation head
  `1f684d975005a7e552f62f36dfb7309a58b11799`. Do not describe a different
  head as cross-platform validated while any required lane is pending or red.
  The first push/pull-request package lanes remain retained failures: every
  platform exposed a stale symlink-error expectation, and macOS additionally
  exposed its `/var` temporary-root alias during the mission-size campaign.
  The ordinary matrix now excludes the explicit `retained_evidence` test,
  qualifies an unlinked `RUNNER_TEMP` descendant, and retains fast crash
  primitives. That exact implementation head passed all six automatic package
  jobs in push run `30331509718` and all six in pull-request run `30331512148`,
  covering Linux, Windows, and macOS on Python 3.12/3.13. The retained-evidence
  jobs were skipped/default-off.
- [x] Add a package-local deterministic sdist boundary for `ctxc-openhands`.
  At packaging implementation head
  `2f692484272aa36bf267703cad2bb4d6926676ff`, two fresh exact Git archives
  and an extracted-sdist rebuild produced identical final sdists under
  `SOURCE_DATE_EPOCH=1785225894`, CPython 3.12.13, pip 25.0.1, build 1.5.0,
  Setuptools 83.0.0, and wheel 0.47.0. The historical raw-Setuptools failures
  remain failures. Initial wrapper head `cf8b8d3` also remains failed because
  its extracted rebuild changed only `SOURCES.txt`; `2f692484` adds the exact
  recursive regression and manifest fixed point. At `2f692484` this did not
  establish cross-platform equality, hash-pinned build inputs, independent
  reproduction, or release readiness. Exact head `2f692484` passed all six
  automatic package jobs in push run `30341548763` and all six in pull-request run
  `30341552866`; retained evidence remained skipped/default-off. Root push CI
  `30341549065` and pull-request CI `30341552713` each passed 7/7 jobs;
  CodeQL `30341552714` and dependency review `30341553236` passed.
- [x] Align root and OpenHands sdist/wheel member validation and the pinned
  build-wheel inspector with the complete supported Windows device-alias set.
  Reject `CONIN$`/`CONOUT$`, COM/LPT superscript 1/2/3 forms, and aliases with
  ASCII spaces before an extension; retain `COM0`, `COM10`, and `CON name`
  controls without normalizing any archive member.
- [x] Reject archive namespace conflicts at component boundaries across both
  byte-identical build backends and the OpenHands ZIP/`RECORD` inspector. The
  physical and parsed tar/wheel layers fail closed when a file is also an
  ancestor of another member or when explicit or implicit directory components
  collide under NFC plus Unicode casefolding. A bounded adjacent sort replaces
  `/` with the already-forbidden NUL sentinel, so a lexical interloper such as
  `a-foo` cannot separate `a` from `a/x`. Namespace comparison preserves
  accepted member-name spelling and does not normalize, merge, or rewrite the
  names; existing deterministic container normalization is unchanged. The CI
  inspector rejects ZIP namespace conflicts before decompression and checks
  `RECORD` names independently before archive-membership and digest checks.
  Both backend files have SHA-256
  `e7b0271647fc4ec1206b591d6bf82ea17fba9a9d8f40c6f185f072166e41f1e8`.
- [x] Require a separate automatic comparison of the root wheel, integration
  wheel, and integration sdist from all six Linux, Windows, and macOS Python
  3.12/3.13 package lanes. Exact package-gate implementation head
  `f9ba3de6f0ab2ac7e861bd7da907e6949df1339e` passed the six package jobs plus
  the aggregate in push run `30355357305` and pull-request run `30355359105`;
  retained evidence was skipped/default-off. Push/PR root CI runs
  `30355357152` and `30355359094` passed 7/7 jobs; CodeQL `30355359110` and
  dependency review `30355359096` passed. The push report raw SHA-256/self-hash
  are `e9203177989fab536e70febcf5316ba6ea21d2a39ea2d3f8dc2c46b146a6f743`
  and `9931d25167831dea6698e0794a93e1cc46a1fc23ed29126c94708aefe7efb35c`.
  The pull-request report is bound to synthetic merge revision
  `a585ddbea770e49ff23f49b091536a86fa9db295` with the same tree as the
  implementation head; its raw SHA-256/self-hash are
  `9c8ef8530ffa37c6596d94070f75aaed95ab532af3fbf9737870bfa53b08c942`
  and `8ccf05ab81647db1d5030f79ee5e9f367318e923b539b9376cf3e015b04ff2c5`.
  The Actions artifacts expire on 2026-08-11, so durable retained evidence,
  hash-pinned build inputs, live execution, SBOMs, signatures, provenance
  attestations, and release authorization remained open at that checkpoint.
- [x] Bind automatic package builds to one tracked seven-wheel input set.
  Exact implementation head
  `0f20b8a1c131fe3c0908f7d6738790529f42338c` uses a 666-byte
  `requirements-build.lock` with SHA-256
  `243f3ab977d82c04968cf4ea6474b7ef79c060d485aa3a3d67a383d1ef6fbbfe`.
  Each lane acquires those exact universal wheels with `--require-hashes`,
  validates and retains them, force-reinstalls them into a dedicated builder
  without an index, and cross-binds the builder and clean-install reports.
  OpenHands push `30366252700` and pull-request `30366258656` each passed six
  package jobs plus the aggregate; retained evidence stayed skipped/default-off.
  Root CI push `30366251022` and pull-request CI `30366255412` each passed 7/7;
  CodeQL `30366255532` and dependency review `30366255341` passed. The
  automatic lanes agreed on root wheel
  `ec46f710169a95c21c54a28b941d2f5205104113c3e594fe6945ef68f633642f`,
  integration wheel
  `16976fa84cebb2b35f1cc15db89a41f016a3a8385498fd2335adbc28c85aacf0`,
  and integration sdist
  `bf51799df63019c3138368a2d6f9e8bc3ddd398163838669340764be36130957`.
  This closes their build-input byte identity, not durable retention, signed
  origin, the live OpenHands dependency closure, SBOMs, signatures, provenance
  attestations, or release authorization.
- [ ] Run and durably retain the manual/default-off retained-evidence job for a
  future candidate without replacing the historical attempts. Temporary local
  evidence is not durable retention.
- [x] Pin and review one exact OpenHands identity and implement its closed
  top-level event, authority, atomicity, callback, recovery, and offline fake
  runtime contracts without importing OpenHands through the core package.
- [ ] Add adapters for selected popular agent runtimes after verifying their
  role and event semantics.
- [x] Map tool, developer, system, assistant, and user authority explicitly at
  the connector boundary; assistant and tool output defaults to untrusted
  history unless authenticated host metadata enables a narrower core path.
- [x] Prevent assistant/tool SourceEvent metadata from self-promoting
  historical output into authoritative state; authenticated tool state remains
  confirmed-fact-only.
- [ ] Define and enforce retrieved-document role semantics so framework
  adapters cannot mislabel retrieved text as an authoritative actor.
- [x] Add an offline OpenHands example with forced compactions, crash/restart,
  exact fake-request replay, and on-demand exact-span rehydration labeled
  `untrusted-evidence`.
- [ ] Record the equivalent live OpenHands scenario only after every live gate
  passes; do not substitute the offline fake report.

### Token accounting and selection

- [ ] Add named tokenizer adapters for the models used in evaluation. The
  connector now accepts a generic exact in-process adapter, but ships no
  provider/model-specific tokenizer. The OpenHands byte tokenizer is exact
  only for its canonical offline fake protocol, not a real model.
- [x] Bind an in-process exact token-counter identity into connector bundles
  and replay, label the fallback character counter as estimated, and reject an
  estimated bundle relabeled as exact.
- [ ] Include chat framing, tool schemas, and provenance pointers in the exact
  final request sent by a live host. The OpenHands fake-runtime ledger covers
  prompts, memory, tail, current turn, retrieval, attachments, tool schemas,
  framing, reserved output, and margin, but cannot establish real-host
  wire/tokenizer exactness.
- [x] Test strict hard-limit overflow refusal for the exact offline OpenHands
  fake-request ledger.
- [ ] Apply and replay that hard-limit gate against the final immutable request
  actually sent by a live host.
- [ ] Compare the current priority-per-token selector with constrained
  optimization and learned policies.
- [ ] Preserve protected-item retention as a hard constraint in every selector.
- [ ] Evaluate adaptive budgets based on task phase and downstream model size.

## P1: production hardening

### Security and privacy

- [x] Add optional fixed-detector preprocessing for common secrets in source
  content, with length/line-boundary-preserving masks, recomputed source
  hashes, bounded source iterables/scans, exact replay, and a self-hashed audit
  map that omits original content-secret text and hashes.
- [ ] Extend privacy policy beyond common content secrets: define reviewed
  metadata/id/timestamp handling and evaluate PII or domain-sensitive-data
  detection without claiming completeness from lexical heuristics.
- [ ] Define encryption-at-rest and access-control guidance for source archives.
- [x] Add strict detached trusted manifests for complete replay-verified
  artifacts, exact source digests/counts, and optional source-archive chain
  heads. Verification requires a manifest SHA-256 retained outside the bundle
  boundary; unanchored Python verification cannot report success. Digital
  signatures and key management remain future work.
- [x] Add canonical source-archive entry hash chaining and optional
  externally retained head checks for stale-state and valid-prefix rollback
  detection; document that standalone self-hashes remain recomputable and do
  not replace a protected monotonic/signature anchor.
- [ ] Define safe retention, deletion, and backup workflows.
- [x] Complete the hostile serialized-file and structural-metadata matrix:
  source, artifact, archive, and benchmark-evidence paths reject
  symlink/special-file inputs and regular-mode Windows reparse targets before
  and after open, detect substitution/in-place mutation, bound replacement
  retries, and refuse compressed bytes without decompression; archive hard-link
  aliases, writer races, oversized/ambiguous JSON, canonical immutable metadata,
  and fixed source-id/role/timestamp amplification ceilings have fail-closed
  coverage.
- [x] Define and test the parent-directory traversal boundary. Shared guards
  reject linked/reparse ancestors, freeze the full lexical ancestor chain,
  use pinned descriptor-relative parents on POSIX, and revalidate before and
  after reads, writes, and archive-lock opens. On Windows and other hosts
  without directory-relative replacement, a privileged rename inside the
  final path syscall remains an explicit filesystem trust boundary.
- [ ] Complete an external security review before a production claim.

### Reliability and operations

- [x] Add shared configurable limits for source bytes, record count, line
  length, canonical record size, JSON depth, and archive size. Model response
  size and candidate count are also bounded.
- [x] Add fail-closed compilation expansion limits for per-extractor
  items/rejections/canonical and auxiliary bytes; total candidates, resolved
  items, provenance spans, and shared recovery/resolution/conflict/selection/
  verification item work. Abort rather than truncate protected state.
- [x] Add strict, configurable artifact limits for raw/canonical bytes, line
  length, JSON depth, item/selection counts, provenance spans, and embedded
  verification issues across CLI and direct replay.
- [x] Add an opt-in portable whole-compile deadline for materialized,
  serializable jobs. Run the pipeline in an isolated POSIX process group or
  Windows Job Object, signal the owned POSIX group or terminate the Windows Job
  on timeout, copy caller inputs across the boundary, and accept successful
  results only through bounded strict JSON plus sealed-snapshot reconstruction.
- [x] Add opt-in versioned JSON runtime diagnostics that distinguish resource,
  I/O, invalid JSON/type/value, integrity, timeout, and policy failures while
  retaining the default human-readable CLI contract.
- [x] Add opt-in `ctxc-event-0.1` JSONL completion events on stderr with the
  actual artifact digest/ledger mode, extraction rejection and failure state,
  recovery contributions, verification outcomes, compression/budget state,
  and versioned compilation metrics; preserve silent default stderr.
- [x] Make every CLI `-o/--output` write a same-directory flushed, `fsync`ed,
  atomic replacement that cleans failed temporary files and preserves existing
  regular-file permissions.
- [x] Make archive appends crash-safe before replacement by committing the
  complete bounded event log through the same atomic writer under the exclusive
  lock; readers see either the old or new complete archive.
- [x] Commit benchmark reports, gold-free corpus exports, per-case corpus
  inputs, and runner manifests through the atomic writer; use exclusive install
  for manifests so a check/write race cannot overwrite another producer.
- [x] Embed commit SHA plus dirty state, package and schema versions,
  Python/platform, exact command, tokenizer/model id, UTC start, duration,
  baseline revisions, cost, and failures in benchmark reports; bind the
  envelope with `report_sha256` while retaining deterministic certificate
  `evidence_sha256`.
- [x] Add a bounded strict verifier for saved current-schema benchmark reports
  that rejects duplicate/non-finite JSON, regenerates the dataset identity,
  recomputes report and certificate digests, validates protocol/comparison/run
  metadata, reconciles included per-history counts and rates, and retains a
  local-only replay path for the committed `lrcbench-report-0.1` snapshot.
- [x] Route serialized benchmark reports, corpora, candidates, and manifests
  through one strict regular-file reader with byte, line, and depth limits;
  reject ambiguous/non-finite JSON and bind validation to the exact candidate
  bytes so mutation races fail closed.
- [x] Version and propagate equivalent producer metadata through corpus and
  candidate interchange artifacts without invalidating frozen dataset identity;
  bind corpus and candidate envelopes independently with canonical self-digests.
- [x] Add versioned artifact metrics for item counts, protected overflow,
  post-resolution recovery additions, conflicts, verification outcomes, and
  compile latency; surface them through `ctxc inspect` and independently
  reconcile all replayable fields while accepting older schema-1.0 artifacts
  that omit the optional metrics and compilation-limit records.
- [x] Make `ctxc inspect` fail closed on malformed, unsupported, internally
  inconsistent, or stale-self-hash artifact envelopes; expose the same bounded
  source-independent validation through the Python API.
- [x] Eliminate ordinary stale-lock recovery by replacing create/delete lock
  ownership with a persistent OS advisory lock that releases on descriptor
  close or process death; document that marker existence does not mean held.
- [x] Test local-filesystem lock/path/release-smoke semantics on Ubuntu, Windows,
  and macOS in CI; complete-suite coverage remains Ubuntu-only.
- [ ] Test advisory-lock, hard-link, rename, identity, and durability semantics on
  representative network/distributed storage.
- [x] Publish the exact artifact reader/writer support window through
  `ctxc-artifact-schema-compatibility-0.1`; document and test that unknown
  versions fail closed and that no automatic or silent migration exists.
- [x] Add a versioned, fixed-digest CI compile profile with median-latency,
  doubling-growth, and traced-Python-memory ceilings plus a self-hashed report.

### Packaging and release

- [x] Standardize the stable distribution and installed schema directory on
  `loss-resistant-context-compiler`; retain `context_compiler` and `ctxc`, and
  document that the old pre-beta `lossless-context-compiler` metadata is not a
  package alias or automatic upgrade relationship.
- [x] Add release notes, a changelog, semantic-versioning policy, and support
  matrix.
- [x] Build and install wheel and sdist separately in clean environments on
  Ubuntu plus provisional Windows/macOS Python 3.13 smoke hosts; verify metadata,
  all 26 schemas, `ctxc --help`, compile, trust-create, and trust-verify.
- [ ] Publish signed source and wheel artifacts to an explicitly approved test
  package index and retain install evidence from that index.
- [x] Add immutable GitHub Action pins, Python/Actions Dependabot, CODEOWNERS,
  high-severity pull-request dependency review, and pinned Python CodeQL with
  narrow upload authority.
- [x] Add bounded exact-name SHA-256 evidence for one wheel and one sdist, with
  lexical real-directory and single-link archive checks, two-pass mutation
  detection, no-overwrite outputs, a self-hashed manifest completion marker,
  and CI retention. This is substitution-detection groundwork, not provenance
  or signing.
- [x] Make repeated clean core candidate builds byte-for-byte reproducible
  within one same-job, explicitly versioned toolchain. The project PEP 517 wrapper now
  validates the raw Setuptools sdist, normalizes its gzip/tar/PAX identity from
  an explicit `SOURCE_DATE_EPOCH`, preserves exact member content and
  structure, and leaves the separate byte comparator strict. CI compares wheel
  and sdist outputs from two clean checkouts with pip 25.0.1, Setuptools 83.0.0,
  and wheel 0.47.0, and retains the resolved Python/tool inventory. Exact root
  release-input implementation head
  `7915beb15f6a3429c24871779c7cdab280d1ee04` additionally binds the root
  platform-smoke, repeated-build, and LRCBench jobs to the 666-byte root
  `requirements-build.lock`, SHA-256
  `243f3ab977d82c04968cf4ea6474b7ef79c060d485aa3a3d67a383d1ef6fbbfe`,
  and seven exact universal wheels. The lock is included in and verified from
  the source distribution. Root CI push run `30429423660` and pull-request run
  `30429426031` each passed all seven jobs. This does not establish
  cross-toolchain or cross-platform artifact equality, independently reproduced
  builds, authenticated index origin, or durable retention.
- [x] Replace the automatic root sdist smoke's online lower-bounded
  `setuptools`/`wheel` bootstrap with the reviewed hash-pinned wheelhouse path.
  At exact implementation head `7915beb`, each affected job acquires only the
  seven lock-authorized wheel bytes, validates the inventory, then uses a
  dedicated no-index builder. Platform-smoke and LRCBench report
  `hash-pinned-offline-wheelhouse` for the sdist smoke; repeated-build uses the
  exact builder for two candidates and a strict byte comparator. The default
  `online-lower-bounds` mode remains an explicit standalone diagnostic;
  initial acquisition still uses the configured index, and temporary Actions
  retention is not durable release evidence.
- [ ] Add resolved locks plus vulnerability and license scanning for each
  optional external adapter.
- [ ] Publish `0.2.0` only after the external runner and natural-history corpus
  format are stable.

## P2: developer and research experience

- [x] Add a bounded terminal inspector for item provenance, temporal status,
  conflicts, and selection state; escape terminal/control/format characters
  and retain versioned `ctxc-artifact-inspection-0.1` JSON as the default
  output.
- [x] Add an integrity-gated `ctxc-artifact-diff-0.1` view between two
  compiled artifacts with deterministic payload/item/selection/report changes,
  incomplete-ledger scope warnings, optional item details, and a self-digest.
- [ ] Add a benchmark dashboard generated only from signed or hashed reports.
- [ ] Add compact examples for coding, research, operations, and customer
  support histories.
- [ ] Add a plugin guide for custom extractors, verifiers, tokenizers, and
  storage backends.
- [ ] Evaluate hierarchical memory, retrieval from cold archives, and
  task-specific memory views.
- [ ] Explore training a smaller extractor from adversarial histories without
  changing the verifier’s fail-closed contract.
- [ ] Measure whether typed memory improves smaller local models more than
  larger hosted models.

## Proposed milestones

### `0.2.0` — external evaluation infrastructure

- [x] verified-memory mutation, safety-override, provider-fallback, and
  superseded-rendering defects closed;
- [x] benchmark estimands made consistent and renamed accurately;
- [x] per-system registered comparison decisions and strict-majority logic;
- [x] exact local Qwen CLI adapter with one-slot enforcement;
- frozen comparison protocol;
- [x] reproducible external runner;
- at least two working external adapters;
- generic exact token-counter accounting landed; named evaluation-tokenizer
  adapters remain pending;
- versioned benchmark manifests.

### `0.3.0` — held-out and downstream evidence

- sealed natural-history corpus;
- two public downstream task suites;
- complete paired reports;
- categorized failure analysis.

### `0.4.0` — live integration beta

- [x] separately packaged OpenHands offline draft-alpha foundation with closed
  event/authority mapping, crash-safe generations, exact fake accounting, and
  an explicit retained live blocker;
- efficient incremental invalidation beyond the current full-prefix recompile;
- at least two agent integrations;
- operational limits and metrics;
- machine-readable artifact schema compatibility and no-silent-migration
  policy;

### `1.0.0` — evidence-backed production release

- strict majority external 50% gate passes;
- at least 50% task-failure reduction or 1.5x successful completions per total
  token or cost is reproduced against the strict majority;
- zero observed protected and exact misses on frozen synthetic and natural
  cohorts, with confidence bounds reported;
- at least 5x real-token compression on every evaluation cohort and a reported
  combined median at or above the product target;
- results hold across at least three model families, including a documented
  local model in the 7B–8B class;
- security and privacy review is complete;
- clean installation, migration, and operations documentation exists;
- no known path silently drops a protected commitment.

## Recommended next work package

The next chat should start here unless new evidence changes the priority:

1. acquire Astropy through a reviewed channel, inspect the exact base-commit
   license candidates, and prepare ordinals 0--21 (22 selected rows and 22
   distinct commits), from
   `astropy__astropy-12907@d16bfe05a744909de4b27f5875fe0d4ed41ce607`
   through
   `astropy__astropy-8872@b750a0e6ee76fb6b8a099a4d16ec51977be46bf6`,
   retaining every success or refusal without silently narrowing the 500-row
   cohort; this is the smallest untouched physical repository block after the
   late ordinal 287--499 block, not evidence that all ordinals are prepared;
2. keep live OpenHands execution and the recorded live demonstration blocked
   until a complete reviewed hash-pinned offline dependency closure and a
   stable public final-immutable-request/exact-tokenizer hook both exist;
3. if those inputs become available, perform a result-blind live compatibility
   run under the retained pin and resource/network controls, preserving any
   failed attempt as a failure;
4. preserve and replay the completed held-out paired result without tuning its
   corpus, validators, recovery, scoring, or recorded metrics;
5. resolve the nine explicit blockers in the external-comparison manifest;
6. complete result-blind inclusion decisions and freeze the initial
   related-system set;
7. supply the exact clean Python 3.11 checkout, dependency lock, network-policy
   evidence, inference-service accounting, and LM Studio executable needed to
   rerun the retained ACON diagnostic;
8. add clean reproducible adapters only for systems admitted by the frozen
   result-blind protocol;
9. after the Astropy slice, complete SWE-bench dataset/repository license review
   and the remaining Django and Matplotlib preparation outcomes, then provision
   externally verified candidate
   mount/filesystem and network isolation before using the synthetic-tested run
   ledger with any public candidate; keep hidden-test application, grading,
   resolution, scoring, usefulness, and claim readiness false until separately
   evidenced;
10. collect licensed/consented histories under the implemented natural-history
   contracts, perform independent annotation/adjudication and privacy review;
11. freeze a full natural corpus only after those reviews and split checks pass;
12. record every failure and result without changing the claim boundary.

See [docs/HANDOFF.md](docs/HANDOFF.md) before changing code or benchmark rules.
