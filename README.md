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

For inputs the extractors recognize, the current implementation enforces these
structural properties:

- every memory item has source provenance;
- goals, constraints, user corrections, unresolved questions, exact errors,
  and exact references are protected from budget-driven removal;
- a full deterministic rule pass can recover every rule-recognized item missed
  by a primary extractor, while the independent coverage certificate is scoped
  to protected kinds;
- independent commitments in labeled sections, bullets, sentences,
  conjunctions, and semicolon-separated clauses are atomized before temporal
  resolution, so correcting one does not retire its neighbors;
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
  and recursively immutable after `SourceRecord` construction.

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

Compile the included JSONL history to a compact prompt:

```console
ctxc compile examples/auth_timeout.jsonl --format prompt
```

Write a portable JSON artifact, verify it independently against the sources,
and inspect its headline metrics:

```console
ctxc compile examples/auth_timeout.jsonl -o compiled-memory.json
ctxc verify compiled-memory.json examples/auth_timeout.jsonl
ctxc inspect compiled-memory.json
```

Use an append-only local cold archive while keeping only compiled state active:

```console
ctxc archive append .context-archive examples/auth_timeout.jsonl
ctxc archive verify .context-archive
ctxc compile examples/auth_timeout.jsonl --archive .context-archive --format prompt
```

`ctxc compile` also accepts `-` for stdin. Inputs may be a JSON list, an object
containing `sources`, `events`, or `messages`, or JSONL. Each record accepts
`id`, `sequence`, `role`, `content`, `timestamp`, and `metadata`.

The default JSON output contains the complete typed ledger and is the format to
retain for audit. It carries `artifact_sha256`, which `ctxc verify`
recomputes over the canonical artifact payload before checking its contents.
Independent verification also rebuilds the canonical prompt and compression
report, reruns the invariant verifier, and compares the embedded verification
report with that replay. A self-hash is an integrity check, not a signature;
an attacker who can rewrite both an artifact and its expected trust anchors is
outside this guarantee.
`--active-only` intentionally omits cold and superseded ledger entries for
compact transport. Its artifact is marked `ledger_complete: false`;
independent `ctxc verify` rejects it with `incomplete_ledger` because omitted
protected coverage cannot receive a full certificate.

Important compile options:

- `--token-budget N`: requested active-context budget, default `4000`;
- `--minimum-compression R`: target source/active ratio, default `5.0`;
- `--strict-budget`: fail instead of reporting a protected-item overflow;
- `--require-target`: return a failure status if the compression target misses;
- `--include-superseded`: include superseded items in active selection;
- `--no-recovery`: disable the independent full deterministic recovery pass;
- `--format json|prompt`: choose the output representation;
- `--active-only`: emit a compact, intentionally incomplete non-audit ledger.

Exit status is `0` on success, `2` for invalid input or policy errors, `3` for
a failed verification, and `4` when `--require-target` is set and the requested
compression ratio is not achieved.

## Python API

```python
from context_compiler import CompilationPolicy, ContextCompiler, SourceRecord

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
    policy=CompilationPolicy(token_budget=800, minimum_compression_ratio=5.0)
)
memory = compiler.compile(sources)

if not memory.verification.passed:
    raise RuntimeError(memory.verification.to_dict())

print(memory.to_prompt())
```

For exact provider token accounting, give the compiler a stable counter name
and give independent artifact verification the same callback and name:

```python
from context_compiler.io import verify_artifact_dict

def count_words(text: str) -> int:
    return len(text.split())

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
)
assert checked["passed"]
```

The id must identify the exact tokenizer and configuration. A custom counter
without `token_counter_id`, or verification without the matching callback and
id, fails compression replay with `unverifiable_token_counter`.

Pass a `ModelExtractor(complete)` as the primary extractor to use any provider
that can return the documented JSON envelope. This adapter is deliberately
extractive: each ordinary candidate must equal a complete atomic cited span,
and exact candidates must equal every cited span. It does not accept model
paraphrases. Invalid kinds, roles, tags, and spans are rejected before they
enter memory; the full deterministic rule recovery pass still runs by default,
and protected-only candidates define the independent coverage certificate.
The completion callable and any data it sends remain the integrator’s security
and privacy responsibility.

`CompilationPolicy(verify=False)` is an explicitly unsafe diagnostic mode. It
returns a failed report with `verification_not_performed`; normal
`CompiledMemory.to_prompt()` and CLI prompt output refuse to render that
memory. The Python-only `allow_unverified=True` override exists for deliberate
diagnostics and must not be used to feed an agent.

## Validate

Run the cross-runner unit suite:

```console
python -m unittest discover -s tests -v
python -m pytest -q
```

Run the deterministic LRCBench harness:

```console
python -m benchmarks --histories 24
```

Validate the fail-closed external-candidate interchange, or export the exact
gold-free corpus for a separately run system:

```console
python -m benchmarks --self-test
python -m benchmarks --histories 24 --export-corpus lrcbench-corpus.json
```

External outputs can be imported with repeatable `--external-baseline`
options. See [Benchmarking](docs/BENCHMARKING.md) for the strict schema and
claim scope.

LRCBench requires exact gold-atom offsets for credited provenance. A candidate
cannot cite a broad enclosing source span to obtain recall credit for a smaller
literal inside it.

The benchmark exits `0` only when every absolute gate and the paired 50% local
frontier check passes; otherwise it exits `2` and explains why. A failed
certificate is a valid evaluation result, not necessarily a harness error.

Current local snapshot (2026-07-23): all 102 tests pass, and the recorded default
32-history LRCBench certificate is `ISSUED` with scope
`local-bundled-only`. Dataset SHA-256
`9dd650433b9d1a018951a7a4745ba31907f6314965e44aee01ea9ecca24389ae`
produced 100% compiler critical recall, exact recall, provenance validity,
semantic-support accuracy, authority accuracy, and history-perfect rate; 0%
stale-claim and unresolved-to-fact rates; and 32.60x corpus compression. The
strongest bundled extractive baseline recorded 88.1% critical recall, 83.6%
exact recall, 100% provenance and semantic-support validity, 0% authority
accuracy, a 100% stale-claim rate, 0% perfect histories, and 30.13x
compression. The reviewed JSON evidence is
[docs/results/lrcbench-local.json](docs/results/lrcbench-local.json).

These local generated results are **not an external-system comparison** and do
not establish the requested 50% advantage over most related technology. Rerun
the commands above for the current revision and environment.

## Documentation

- [Architecture and invariants](docs/ARCHITECTURE.md)
- [Benchmark design and the exact 50% bar](docs/BENCHMARKING.md)
- [Threat model](docs/THREAT_MODEL.md)
- [Related work](docs/RELATED_WORK.md)
- [LRCBench harness notes](benchmarks/README.md)
- JSON Schemas:
  [source event](schemas/source-event.schema.json),
  [model extraction](schemas/model-extraction.schema.json), and
  [compiled memory](schemas/compiled-memory.schema.json)

## License

[MIT](LICENSE)
