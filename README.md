# Loss-resistant Context Compiler

A small, model-agnostic Python library and CLI that turns long agent histories
into typed working memory with exact source provenance.

> **Evidence boundary:** this project includes a deterministic local benchmark
> that can issue a certificate against its bundled head, tail, and extractive
> baselines. That certificate is **not proof** that this project is 50% better
> than ACON, FoldAgent, AMA-Agent, MemIR, or any other external system. An
> external superiority claim requires matched, head-to-head, independently
> reproducible evaluation. See [Benchmarking](docs/BENCHMARKING.md).

The compiler keeps a ledger of goals, constraints, corrections, confirmed
facts, decisions, unresolved questions, exact errors and references, discarded
attempts, progress, and background context. Every item carries one or more
character spans into immutable source records. A separate safety extraction
pass and verifier protect the commitments most costly to lose.

This is an alpha research implementation. “Loss-resistant” means that explicit
invariants are checked and failures are surfaced; it does not mean arbitrary
meaning can be compressed without loss.

## Project status

| Area | Current state |
| --- | --- |
| Release | Alpha research implementation, package version `0.1.0` |
| Runtime | Python 3.11+, standard-library-only core |
| Interfaces | Python API, `ctxc` CLI, JSON/JSONL input, JSON artifacts |
| Regression suite | 378 tests; CI runs Python 3.11, 3.12, and 3.13 |
| Local synthetic benchmark | 32.60x compression and 100% critical recall on the recorded run |
| Local bundled certificate | `ISSUED` against head, tail, and extractive controls |
| Named external comparisons | Not run |
| Downstream agent task completion | Not measured |
| “50% better than most related technology” | **Not established** |
| Production readiness | Not production-ready |
| Confirmed fail-closed blockers | The four identified in-process P0 paths are closed |

The local benchmark result is meaningful evidence that the current design can
beat simple truncation and extraction policies on its own adversarial corpus.
It is not evidence that the same result generalizes to natural histories,
different languages, external context systems, or real agent task completion.

## Mission

Long-running agents accumulate terminal output, repeated files, superseded
plans, installation logs, duplicate search results, tool schemas, abandoned
approaches, and stale reasoning. Keeping all of it active wastes tokens and can
distract the model. Replacing it with an ordinary prose summary creates a
different risk: goals, prohibitions, corrections, exact failures, and open
questions can disappear or change meaning.

This project treats context reduction as compilation rather than
summarization:

1. parse source events into typed claims;
2. attach every claim to exact source spans;
3. resolve corrections, revocations, conflicts, and closed questions;
4. select the most useful active state within a token budget;
5. independently verify protected coverage and provenance;
6. retain the full audit ledger and optional cold source archive.

The intended result is a compact working-memory prompt for the next model call
plus a larger machine-verifiable artifact for audit and replay.

## Target and definition of success

The product target has six parts. All six are required before the original
goal should be considered complete.

| Target | Required evidence |
| --- | --- |
| 5–10x less active context | At least 5x source-to-active compression on synthetic and held-out natural histories |
| No lost explicit commitments | 100% recall for goals, constraints, user corrections, unresolved questions, exact errors, and exact references |
| Exact provenance | 100% valid source ids, character offsets, quotes, and hashes for retained claims |
| Safer temporal state | No stale superseded claims, authority violations, unsupported claims, or unresolved-to-fact promotions |
| Better agent outcomes | Higher completion on at least two public long-horizon task suites under matched models, tools, budgets, and retries |
| At least 50% better than most related systems | A preregistered, paired comparison must clear the statistical bar for a strict majority of the dated comparison set |

For a component-level memory claim, “50% better” can mean at least 50% less
critical semantic loss or at least 50% more memory quality per active token,
with a positive preregistered paired bootstrap lower bound. For the original
product claim, memory proxies are not enough: the compiler must also deliver at
least 50% task-failure reduction or 1.5x successful completions per total token
or cost against a strict majority of the dated comparison set. The exact
estimand and percentile must be frozen before external runs. The complete claim
protocol is in [Benchmarking](docs/BENCHMARKING.md).

The 100% retention target means zero observed protected misses on the frozen
synthetic and natural cohorts, with a confidence bound reported separately. It
is not a universal proof that unseen phrasing can never be missed. The current
LRCBench certificate thresholds of 98% critical and 99% exact recall are useful
alpha gates but are not sufficient for the final no-loss release target.

The target is deliberately harder than obtaining a high summary-similarity
score. A compact output fails if it drops one protected requirement, promotes
one open question to a fact, accepts one tool-output instruction as an
authoritative goal, or cannot trace a claim back to the source.

## What exists today

The repository currently includes:

- a typed memory model for goals, constraints, corrections, facts, decisions,
  unresolved questions, exact errors and references, discarded attempts,
  progress, and background context;
- immutable source records with content and canonical-record SHA-256 digests;
- exact character-offset provenance with quote hashes;
- a deterministic rule extractor and an optional provider-neutral
  `ModelExtractor`;
- an independent deterministic recovery pass for rule-recognized content;
- conservative correction, revocation, conflict, and unresolved-state
  resolution;
- budget-aware selection that never silently drops protected items;
- sealed compiled snapshots with recursive immutability, an integrity digest,
  verification, and independent artifact replay;
- versioned compilation telemetry for item flow, protected-budget pressure,
  recovery, conflicts, verification outcomes, and elapsed compile time, with
  deterministic fields checked again during artifact replay;
- non-replaceable built-in deterministic recovery and protected-item
  certification passes;
- strict bounded model-output parsing with duplicate-key and non-finite-number
  rejection, deterministic provider-failure fallback, and an opt-in strict
  provider-failure policy;
- shared default-on source and compiled-artifact limits for UTF-8 input bytes,
  physical line length, JSON depth, canonical size, and schema collections;
- shared strict regular-file loading for benchmark reports, corpora, external
  candidates, and run manifests, with byte/line/depth limits plus duplicate-key
  and non-finite-number rejection;
- versioned corpus and candidate producer records that are bound by envelope
  self-digests while remaining outside the frozen benchmark dataset identity;
- reusable same-directory atomic file replacement for CLI outputs, archive
  commits, benchmark reports, corpus exports, and runner manifests, plus opt-in
  versioned JSON error diagnostics with stable resource, I/O, input, integrity,
  timeout, and policy categories;
- opt-in `ctxc-event-0.1` JSONL completion events that bind the corresponding
  artifact envelope and expose extraction rejections/failures, recovery,
  verification, compression, and compilation telemetry without changing
  default stderr;
- an opt-in whole-compile deadline that runs materialized inputs in an isolated
  POSIX process group or Windows Job Object, terminates the owned descendant
  tree on timeout, and reconstructs successful output from bounded strict JSON;
- a portable JSON artifact, compact prompt renderer, and three JSON Schemas;
- a machine-readable artifact reader/writer registry with an explicit
  no-silent-migration policy;
- a fail-closed JSON inspector plus a bounded terminal item view with escaped
  control/format characters, provenance coordinates, status, conflicts, and
  active-selection state;
- a logically append-only local source archive with integrity checks, exclusive
  locking, and bounded old-or-new atomic commits;
- the `ctxc compile`, `verify`, `inspect`, `diff`, `schema`, and `archive`
  commands;
- LRCBench, external-candidate import/export, history-weighted paired bootstrap
  gates, per-system decisions, and self-hashed JSON reports with producer/run
  metadata;
- gold-free corpus self-digests plus a bounded, shell-free external runner with
  sequential per-case processes, cross-platform process-tree memory limits,
  and valid or failed manifests that feed per-system certificate decisions;
- an API-free, single-inference adapter for the exact local
  `qwen/qwen3.6-35b-a3b@q4_k_m` LM Studio model;
- a fixed-digest, versioned CI compile-performance gate with latency-growth and
  traced-Python-memory ceilings;
- cross-version CI, linting, wheel/schema checks, and 378 regression tests.

## In development

The next phase is mainly evidence, generalization, and integration rather than
adding more claims to the README:

- run matched external systems through the existing candidate interchange;
- add held-out natural coding-agent histories with independent annotations;
- measure end-to-end task completion on public long-horizon suites;
- test the optional model extractor across providers and novel phrasing;
- add exact provider tokenizers and framework adapters;
- support efficient incremental compilation for live agent loops;
- continue hardening generic-provider transport deadlines, secrets handling,
  storage authenticity, and observability;
- obtain independent reproduction before making a state-of-the-art claim.

The ordered engineering backlog is in [TODO.md](TODO.md). The current design
state, base revision, non-negotiable decisions, code map, and restart procedure
for a new chat are in [docs/HANDOFF.md](docs/HANDOFF.md).

### Closed P0 safety paths

The four identified in-process fail-closed defects now have regression-tested
controls:

1. verified `CompiledMemory` snapshots and their items are recursively sealed;
   prompt and artifact rendering recheck a canonical snapshot digest;
2. built-in deterministic recovery and protected-item certification execute
   independently of caller-supplied extractors and cannot be replaced;
3. primary extractor failures and wholly unusable outputs fall back to
   deterministic recovery with explicit verification warnings, while
   `fail_on_primary_extractor_error=True` provides strict behavior;
4. selecting superseded state is audit-only and makes verification fail, so it
   cannot enter a prompt labeled verified.

LRCBench also now uses one history-weighted memory estimand for point estimates
and paired bootstrap bounds, records per-system decisions, and applies a strict
majority rule to an explicitly registered external comparison set. External
systems and natural-history task outcomes still have not been run, so the
external-superiority claim remains unestablished.

## Intended use

This project is aimed at long-running coding, research, operations, and tool
agents where a small amount of durable state must survive a much larger noisy
history. It is especially useful when histories contain:

- requirements changed halfway through a task;
- a correction buried thousands of tokens after the original instruction;
- two files or functions with the same name;
- an exact error code, test count, path, line range, or node id;
- untrusted tool output that resembles an instruction;
- unresolved questions that must remain questions;
- hard context limits where protected overflow must be explicit.

It is not a general semantic theorem prover, a secure identity system, a
tamper-proof database, a secrets scanner, or proof that arbitrary meaning can
be compressed without loss. Those boundaries are detailed in the
[threat model](docs/THREAT_MODEL.md).

## Why typed memory

Ordinary summaries blur important distinctions: a question can become a fact,
an old requirement can look current, and a paraphrased error can lose the one
number needed to debug it. This compiler represents those states explicitly:

```yaml
goal:
  - Repair authentication timeout
constraint:
  - Do not change the public API
confirmed_fact:
  - Failure occurs only after token refresh
unresolved:
  - Whether clock skew causes expiration
exact_reference:
  - tests/test_token_refresh.py::test_clock_skew
```

The compact prompt includes pointers such as
`source-7:118-164#9f24e1c77a`; the JSON artifact retains the full source quote
and SHA-256 digest.

## Current guarantees

At compilation time, for inputs the extractors recognize, the current
implementation enforces these structural properties:

- every memory item has source provenance;
- goals, constraints, user corrections, unresolved questions, exact errors,
  and exact references are protected from budget-driven removal;
- under the default configuration, a full deterministic rule pass can recover
  every rule-recognized item missed by a primary extractor, while the
  independent coverage certificate is scoped to protected kinds;
- independent constraint commitments in labeled sections, bullets, sentences,
  conjunctions, and semicolon-separated clauses are atomized before temporal
  resolution, so correcting one does not retire its neighboring constraints;
- model-produced ordinary claims must equal a complete atomic source span;
  model paraphrases and truncated clauses are rejected;
- exact items must equal every cited source literal;
- recognized diagnostics retain their complete literal line, and recognized
  references retain full POSIX/Windows paths plus line ranges, GitHub line
  anchors, or pytest node ids;
- uncertain source text cannot silently validate as a confirmed fact;
- tool output cannot assert goals, constraints, corrections, decisions,
  unresolved state, or confirmed facts; a tool fact is accepted only when the
  host sets `metadata.trusted_for_state` to the JSON boolean `true`;
- explicit corrections retain the old item as superseded state;
- explicit revocations retire the matched old commitment without inventing
  replacement state;
- clear numeric or polarity conflicts remain visible and generate an
  unresolved item;
- each source-record hash binds id, sequence, role, content, timestamp, and
  metadata; quote, source-set, and artifact digests make changes visible when
  checked against an independently trusted source set;
- source metadata is deep-copied, restricted to finite canonical JSON values,
  and recursively immutable after `SourceRecord` construction;
- the final compiled ledger, verification report, statistics, selection, and
  compiler metadata are recursively sealed, and prompt/artifact rendering
  rechecks a canonical snapshot digest.

These are implementation invariants, not a proof of semantic completeness.
The rule extractor is deliberately conservative and heuristic. Upstream roles
must be authenticated, archives must be protected by the host, and secrets
must be removed before ingestion. See [Threat model](docs/THREAT_MODEL.md).

## Install

Python 3.11 or newer is required. The runtime uses only the standard library.

```console
python -m pip install -e .
```

For development tools:

```console
python -m pip install -e ".[dev]"
```

## CLI quick start

Compile the included small JSONL history to a typed prompt:

```console
ctxc compile examples/auth_timeout.jsonl --format prompt
```

Write a portable JSON artifact, verify it independently against the sources,
and inspect its headline metrics:

```console
ctxc compile examples/auth_timeout.jsonl -o compiled-memory.json
ctxc verify compiled-memory.json examples/auth_timeout.jsonl
ctxc inspect compiled-memory.json
ctxc inspect compiled-memory.json --format text --show-items
```

JSON remains the default inspection format. The terminal view bounds displayed
items, per-item provenance/state links, and raw string length through
`--max-display-items`, `--max-display-links`, and `--max-text-chars`. It
JSON-quotes untrusted strings and visibly escapes terminal controls, Unicode
format controls, and line/paragraph separators. Printable confusable Unicode
is not normalized, so the original JSON artifact remains the authoritative
audit input. JSON item details serialize non-ASCII characters as JSON escapes
without changing their decoded values; the same safe serialization applies to
summary strings. Python callers can use
`summarize_artifact()` or `render_artifact_text()` directly; JSON summaries
identify their contract as `ctxc-artifact-inspection-0.1`.

Compare two integrity-checked artifact envelopes without requiring their source
histories:

```console
ctxc diff before.json after.json -o artifact-diff.json
ctxc diff before.json after.json --summary-only
```

The deterministic `ctxc-artifact-diff-0.1` report identifies payload additions,
removals, same-id field changes, active-selection changes, verification,
compression, and metric changes. It binds itself with `diff_sha256`.
Per-item details preserve item text and compact provenance coordinates while
hashing rather than copying arbitrary item metadata. If either input is
active-only, the report marks `ledger_comparison_complete: false` and warns
that payload changes do not prove complete-ledger changes; selection changes
remain exact.

Query the exact artifact reader/writer support window before an upgrade or
deployment:

```console
ctxc schema
ctxc schema --artifact-version 1.0
ctxc schema --artifact-version 2.0
```

The versioned `ctxc-artifact-schema-compatibility-0.1` response reports only
artifact schema `1.0` as readable and writable. A query for an unknown
well-formed version succeeds with `status: "unsupported"`; malformed version
syntax is an input error. The package never silently migrates artifacts.
See [Schema compatibility](docs/SCHEMA_COMPATIBILITY.md) for the preservation
and trusted-source-replay requirements imposed on any future migration.

To place the complete compile pipeline inside a killable process-tree boundary:

```console
ctxc compile examples/auth_timeout.jsonl -o compiled-memory.json \
  --compile-timeout-seconds 60
```

Successful and completed nonzero outcomes can emit one compact JSONL event on
stderr while the normal artifact or prompt stays on stdout or at `-o`:

```console
ctxc compile examples/auth_timeout.jsonl -o compiled-memory.json \
  --event-format jsonl
```

For JSON output, the opt-in `ctxc-event-0.1` record includes the exact emitted
artifact digest and ledger mode. For prompt output it binds the corresponding
complete audit envelope. It also carries the exit outcome, extraction
rejection/failure counts, recovery contributions, a verification summary, the
compression report, and versioned compile metrics. Default
`--event-format none` preserves silent success stderr. A verification-failed
prompt request may emit a completion event followed by its ordinary error
diagnostic; each remains one JSON line when JSON error format is also enabled.

Use an append-only local cold archive while keeping only compiled state active:

```console
ctxc archive append .context-archive examples/auth_timeout.jsonl
ctxc archive verify .context-archive
ctxc compile examples/auth_timeout.jsonl --archive .context-archive --format prompt
```

Archive writes are logical appends but physical transactions: while holding an
exclusive OS advisory lock, `SourceArchive` validates and serializes the
complete bounded history into a same-directory temporary file, flushes and
`fsync`s it, then atomically replaces `events.jsonl`. Readers therefore observe
either the previous complete archive or the next complete archive, never a
partially appended JSONL tail. The `.append.lock` marker intentionally persists;
ownership is the kernel lock, not file existence, and descriptor close or
process death releases it. The tradeoff is O(archive size) work and temporary
disk space per append, and the filesystem must implement local advisory locks
and atomic replacement correctly.

`ctxc compile` also accepts `-` for stdin. Inputs may be a JSON list, an object
containing `sources`, `events`, or `messages`, or JSONL. Each record accepts
`id`, `sequence`, `role`, `content`, `timestamp`, and `metadata`.
Source JSON uses strict decoding: duplicate object keys, non-standard
NaN/infinity constants, overflowed non-finite floats, and excessive nesting
are rejected.

Source ingestion is bounded by default across `compile`, `verify`, and archive
commands:

| Boundary | Default | CLI override |
| --- | ---: | --- |
| Serialized source or archive input | 64 MiB | `--max-source-bytes` |
| Source records | 100,000 | `--max-source-records` |
| One physical JSON/JSONL line | 1,048,576 characters | `--max-source-line-chars` |
| One canonical source record | 4 MiB | `--max-source-record-bytes` |
| All canonical source records | 64 MiB | `--max-total-source-bytes` |
| JSON container nesting | 128 levels | `--max-source-json-depth` |

The same six limits are available through `SourceLimits` for the Python API.
They are hard ingestion boundaries, not a promise that total process memory
equals the byte caps; Python objects, compiler state, and caller-controlled
extractors have additional overhead.

`ctxc verify`, `ctxc inspect`, and `ctxc diff` also decode compiled artifacts
strictly and bound each input independently:

| Boundary | Default | CLI override |
| --- | ---: | --- |
| Serialized artifact input | 128 MiB | `--max-artifact-bytes` |
| One physical artifact line | 8,388,608 characters | `--max-artifact-line-chars` |
| Decoded compact artifact JSON | 128 MiB | `--max-artifact-canonical-bytes` |
| JSON container nesting | 128 levels | `--max-artifact-json-depth` |
| Memory items / selected ids | 200,000 each | `--max-artifact-items`, `--max-artifact-selected-items` |
| Provenance spans | 1,000,000 | `--max-artifact-provenance-spans` |
| Embedded verification issues | 100,000 | `--max-artifact-verification-issues` |

`ArtifactLimits` exposes the same boundaries to `load_artifact()`,
`load_artifact_path()`, and `verify_artifact_dict()`. Duplicate keys,
non-standard or overflowed non-finite numbers, and excessive depth fail before
artifact shape or cryptographic replay work begins.

`ctxc inspect` additionally requires a supported, structurally valid envelope,
valid item/selection references, and a matching canonical `artifact_sha256`
before reporting health. The exported `validate_artifact_envelope()` function
provides the same source-independent check to Python callers. It detects stale
or malformed envelopes; it is not authenticity or semantic verification, which
still requires trusted sources and `verify_artifact_dict()`.

`diff_artifacts()` and `ctxc diff` apply that envelope check to both inputs
before comparing them. A self-consistent diff remains an artifact-derived view,
not proof that either input is authentic or true.

`artifact_schema_registry()` and `artifact_schema_support(version)` expose the
same compatibility policy as `ctxc schema`. The only current reader/writer
version is `1.0`; the optional absence of
`compiler_metadata.metrics` is the one documented additive compatibility case
within that version. Unknown artifact versions are never inferred or silently
migrated.

The default JSON output contains the complete typed ledger and is the format to
retain for audit. It carries `artifact_sha256`, which `ctxc verify`
recomputes over the canonical artifact payload before checking its contents.
Independent verification also rebuilds the canonical prompt and compression
report, reruns the invariant verifier, and compares the embedded verification
report with that replay. New artifacts include optional
`compilation-metrics-0.1` telemetry under `compiler_metadata.metrics`;
`ctxc inspect` surfaces it, and replay reconciles every field derivable from
the sources and typed ledger. Schema-1.0 artifacts created before this addition
remain valid without metrics. Primary-extractor volume and elapsed time are
measured evidence and can only be shape-checked during replay. A self-hash is
an integrity check, not a signature; an attacker who can rewrite both an
artifact and its expected trust anchors is outside this guarantee.
`--active-only` intentionally omits unselected ledger entries for compact
transport. Its artifact is marked `ledger_complete: false`;
independent `ctxc verify` rejects it with `incomplete_ledger` because omitted
protected coverage cannot receive a full certificate.

Important compile options:

- `--token-budget N`: requested active-context budget, default `4000`;
- `--minimum-compression R`: target source/active ratio, default `5.0`;
- `--strict-budget`: fail instead of reporting a protected-item overflow;
- `--require-target`: return a failure status if the compression target misses;
- `--include-superseded`: include superseded items for diagnosis; verification
  deliberately fails and verified prompt rendering is refused;
- `--no-recovery`: disable the independent full deterministic recovery pass;
- `--format json|prompt`: choose the output representation;
- `--active-only`: emit a compact, intentionally incomplete non-audit ledger;
- `--error-format text|json`: keep human-readable runtime errors (default) or
  emit one compact `ctxc-diagnostic-0.1` JSON object to stderr.

Exit status is `0` on success, `2` for invalid input or policy errors, `3` for
a failed verification, and `4` when `--require-target` is set and the requested
compression ratio is not achieved.

Every `-o/--output` file and archive commit uses the same restrictive
same-directory atomic writer: the complete payload is flushed and `fsync`ed,
then installed with `os.replace()`. Existing regular-file permissions are
preserved. A failure before replacement leaves the prior destination unchanged
and removes the temporary file; on POSIX, the destination directory is also
`fsync`ed after replacement. Stdout behavior is unchanged.

With `--error-format json`, runtime failures have stable top-level fields:
`schema`, `command`, `category`, `code`, `exit_code`, `exception_type`, and
`message`. Argument-parser usage errors remain argparse text, while failed
verification reports and compiled artifacts continue to carry their detailed
issue/rejection data in normal command output.

## Python API

```python
from context_compiler import (
    CompilationPolicy,
    ContextCompiler,
    SourceLimits,
    SourceRecord,
    artifact_schema_support,
    diff_artifacts,
    validate_artifact_envelope,
)

sources = [
    SourceRecord.create(
        sequence=0,
        role="user",
        content="constraint: Do not change the public API",
    ),
    SourceRecord.create(
        sequence=1,
        role="assistant",
        content="unresolved: Whether clock skew causes expiration",
    ),
]

compiler = ContextCompiler(
    policy=CompilationPolicy(token_budget=800, minimum_compression_ratio=5.0),
    source_limits=SourceLimits(max_records=10_000, max_input_bytes=16 * 1024 * 1024),
)
memory = compiler.compile(sources)
validate_artifact_envelope(memory.to_dict())
assert artifact_schema_support(memory.schema_version)["readable"]

if not memory.verification.passed:
    raise RuntimeError(memory.verification.to_dict())

print(memory.to_prompt())
```

`diff_artifacts(before, after, include_item_details=False)` returns the same
self-hashed summary used by `ctxc diff --summary-only`.

Pass `timeout_seconds` to execute the entire compiler pipeline in a dedicated
process tree:

```python
memory = compiler.compile(sources, timeout_seconds=60)
```

Deadline mode requires `sources` to be a materialized list or tuple and the
compiler configuration to be serializable. It copies that job into the worker,
so extractor mutation cannot change the caller's source objects. On timeout it
terminates the worker and owned descendants; on success it accepts only a
bounded strict-JSON artifact, validates its shape and self-digest, and rebuilds
a sealed snapshot. This is a cancellation and state-isolation boundary, not a
filesystem, network, or hostile-code sandbox. Compiler configuration crosses
the local boundary with pickle and must therefore already be trusted. An
extractor that deliberately escapes its POSIX process group is outside the
guarantee.

For exact provider token accounting, give the compiler a stable counter name
and give independent artifact verification the same callback and name:

```python
from context_compiler import ArtifactLimits
from context_compiler.io import verify_artifact_dict

def count_words(text: str) -> int:
    return len(text.split())

artifact_limits = ArtifactLimits(max_items=50_000)
compiler = ContextCompiler(
    token_counter=count_words,
    token_counter_id="words-v1",
)
memory = compiler.compile(sources)
checked = verify_artifact_dict(
    memory.to_dict(),
    sources,
    token_counter=count_words,
    token_counter_id="words-v1",
    artifact_limits=artifact_limits,
)
assert checked["passed"]
```

The id must identify the exact tokenizer and configuration. A custom counter
without `token_counter_id`, or verification without the matching callback and
id, fails compression replay with `unverifiable_token_counter`.
The CLI does not accept a custom counter callback, so custom-token artifacts
must be verified through `verify_artifact_dict()` in Python rather than
`ctxc verify`.

Pass a `ModelExtractor(complete)` as the primary extractor to use any provider
that can return the documented JSON envelope. This adapter is deliberately
extractive: each ordinary candidate must equal a complete atomic cited span,
and exact candidates must equal every cited span. It does not accept model
paraphrases. Invalid kinds, roles, tags, and spans are rejected before they
enter memory. The built-in deterministic recovery and protected-item
certification passes run independently of the provider. Provider exceptions,
invalid envelopes, oversized responses, and wholly unusable candidate sets
produce deterministic fallback memory plus an explicit warning by default.
Set `CompilationPolicy(fail_on_primary_extractor_error=True)` when provider
failure must abort instead. `ModelExtractor` bounds response size and candidate
count, rejects duplicate JSON keys and non-standard or overflowed non-finite
numbers, and validates exact object fields. An outer compile deadline can
terminate the whole owned process tree, but custom completion adapters should
still enforce a shorter transport-level deadline so provider failure can
return deterministic fallback memory instead of aborting the complete run.
The callable and any data it sends remain the integrator’s security and privacy
responsibility.

For the exact locally installed Qwen build approved for this repository, the
API-free LM Studio CLI adapter verifies the model identity, Q4 quantization,
loaded state, and a single inference slot before every call:

```python
from context_compiler import ContextCompiler, LmsQwenCompletion, ModelExtractor

complete = LmsQwenCompletion(
    r"C:\path\to\.lmstudio\bin\lms.exe",
    timeout_seconds=120,
)
compiler = ContextCompiler(
    extractor=ModelExtractor(
        complete,
        model_id=complete.model_id,
        max_response_chars=1_000_000,
        max_candidates=10_000,
    )
)
```

The adapter disables catalog fetching and does not use an HTTP model API.
LM Studio passes the prompt as a process argument, so redact secrets before
using it. See [Local Qwen integration](docs/LOCAL_QWEN.md).

`CompilationPolicy(verify=False)` is an explicitly unsafe diagnostic mode. It
returns a failed report with `verification_not_performed`; normal
`CompiledMemory.to_prompt()` and CLI prompt output refuse to render that
memory. The Python-only `allow_unverified=True` override exists for deliberate
diagnostics and must not be used to feed an agent.

## Validate

Run the complete regression and static checks:

```console
python -m pytest -q
python -m ruff check src tests benchmarks
python -m compileall -q src benchmarks tests
```

Build the wheel and verify the three packaged schemas:

```console
python -c "
import pathlib, subprocess, sys, tempfile, zipfile
with tempfile.TemporaryDirectory() as directory:
    subprocess.run(
        [sys.executable, '-m', 'pip', 'wheel', '.', '--no-deps',
         '--wheel-dir', directory],
        check=True,
    )
    wheels = list(pathlib.Path(directory).glob('*.whl'))
    assert len(wheels) == 1, wheels
    names = zipfile.ZipFile(wheels[0]).namelist()
    assert sum(name.endswith('.schema.json') for name in names) == 3
"
```

Run the same deterministic LRCBench cohort used by CI:

```console
python -m benchmarks --histories 24 --json-out lrcbench-24.json --include-histories
```

Validate the fail-closed external-candidate interchange, or export the exact
gold-free corpus for a separately run system:

```console
python -m benchmarks --self-test
python -m benchmarks --histories 24 --export-corpus lrcbench-corpus.json
python -m benchmarks --verify-report lrcbench-24.json
python -m benchmarks.performance_gate --check --json-out ctxc-performance.json
```

Benchmark JSON reports and corpus exports use the same flushed, `fsync`ed
same-directory replacement as CLI artifacts. External-run manifests use an
exclusive atomic install: a competing file created after the initial check is
preserved and the manifest commit fails instead of overwriting it.

Each JSON report carries `report_schema: lrcbench-report-0.1`,
`report_sha256`, and `run_metadata` containing the producer commit and dirty
state, package and interchange versions, exact command, UTC start time,
duration, Python/platform, tokenizer/model identity, baseline revisions,
model-service cost, and failures. The certificate `evidence_sha256` remains the
deterministic metric digest; `report_sha256` additionally binds the
run-specific envelope.

The `ctxc-performance-gate-0.1` CI profile compiles fixed 128- and 256-event
item-dense histories three times after a warmup. It fails on a median above
2 or 8 seconds respectively, growth above 8x when the source count doubles,
or more than 64 MiB of Python allocations observed by `tracemalloc` during
the largest compile. A 1 ms denominator floor keeps sub-resolution first
samples from producing meaningless growth ratios. The warmup and measured
prefixes are bound by committed per-size SHA-256 values, and the JSON result is
self-hashed. These deliberately generous ceilings are regression tripwires for
the GitHub Python 3.11 job, not production latency, RSS, or million-event
scalability claims.

`--verify-report` strictly decodes a bounded regular UTF-8 file, rejects
duplicate keys, non-finite numbers, excessive depth/size, and unknown fields,
regenerates the deterministic dataset id, recomputes both digests, validates
run metadata and comparison accounting, and reconciles raw history counts with
their recorded rates when `--include-histories` evidence is present. Its
success means the current-schema document is internally consistent; self-hashes
are not signatures and do not authenticate who produced it. A structurally
valid report with a non-issued certificate still verifies successfully.

External outputs can be imported directly with repeatable
`--external-baseline` for diagnostics. A counted registered comparison also
requires a validated `--external-run-manifest`; otherwise it is an invalid
non-win. Every intended participant must be preregistered with
`--expected-external-system`, including missing or failed systems. See
[Benchmarking](docs/BENCHMARKING.md) for the strict schema and claim scope.
`python -m benchmarks.external_runner` supplies a shell-free, timeout-, output-,
and process-tree-memory-bounded adapter wrapper. Its default mode executes every
case sequentially in a fresh process, records a hashed per-case audit trail,
and merges only fully validated outputs. Whole-corpus mode is diagnostic-only.
The wrapper is not a filesystem or network sandbox, and its memory limit does
not include a pre-existing inference service outside the adapter process tree.
It accepts the legacy producerless adapter payload only at that bounded runner
boundary, then emits the current self-hashed candidate envelope with the
registered adapter/model identity. Direct candidate imports require the current
producer-bearing schema.

LRCBench requires exact gold-atom offsets for credited provenance. A candidate
cannot cite a broad enclosing source span to obtain recall credit for a smaller
literal inside it.

For a valid certificate-producing run, the benchmark exits `0` only when every
absolute gate and the paired 50% local frontier check passes; an unsupported
certificate exits `2` and explains why. `--self-test` has its own success
contract, and malformed CLI/configuration failures can use a different nonzero
status. A failed certificate is a valid evaluation result, not necessarily a
harness error.

Current local snapshot (2026-07-24): 378 tests are collected (373 pass and 5
platform/optional checks are skipped), and the recorded default
32-history LRCBench certificate is `ISSUED` with scope
`local-bundled-only`. Dataset SHA-256
`421d49585ef9ac96fe2a378f79c18da1791e508789ac0290d3cc5018cda07761`
produced 100% compiler critical recall, exact recall, provenance validity,
semantic-support accuracy, authority accuracy, and history-perfect rate; 0%
stale-claim and unresolved-to-fact rates; and 32.60x corpus compression. The
bundled extractive baseline recorded 88.2% history-weighted critical recall,
83.6% exact recall, 100% provenance and semantic-support validity, 0%
authority accuracy, a 100% stale-claim rate, 0% perfect histories, and 30.13x
compression. The reviewed JSON evidence is
[docs/results/lrcbench-local.json](docs/results/lrcbench-local.json).

These local generated results are **not an external-system comparison** and do
not establish the requested 50% advantage over most related technology. Rerun
the commands above for the current revision and environment.

## Documentation

- [TODO and development roadmap](TODO.md)
- [Next-chat handoff and current project state](docs/HANDOFF.md)
- [Architecture and invariants](docs/ARCHITECTURE.md)
- [Benchmark design and the exact 50% bar](docs/BENCHMARKING.md)
- [Threat model](docs/THREAT_MODEL.md)
- [Compiled-artifact schema compatibility](docs/SCHEMA_COMPATIBILITY.md)
- [Related work](docs/RELATED_WORK.md)
- [Exact local Qwen integration](docs/LOCAL_QWEN.md)
- [LRCBench harness notes](benchmarks/README.md)
- [Draft external comparison protocol](benchmarks/protocols/external-comparison-v1.md)
- JSON Schemas:
  [source event](schemas/source-event.schema.json),
  [model extraction](schemas/model-extraction.schema.json), and
  [compiled memory](schemas/compiled-memory.schema.json)

## License

[MIT](LICENSE)
