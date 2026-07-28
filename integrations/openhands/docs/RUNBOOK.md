# CtxC OpenHands operator runbook

This runbook covers the separately packaged `ctxc-openhands` alpha. It does
not authorize a live OpenHands deployment. The current environment supports
offline diagnostics and the fake runtime only; the exact live blocker is
documented in the [package README](../README.md) and
[compatibility policy](../compatibility/README.md).

Run every command with CPython 3.12 or 3.13. Examples assume the
`ctxc-openhands` entry point is installed. From a source checkout, replace
`ctxc-openhands` with:

```sh
PYTHONPATH=src:integrations/openhands/src python -m ctxc_openhands.cli
```

On Windows, separate `PYTHONPATH` entries with `;`.

## Invariants an operator must preserve

1. Do not treat host role/source labels as authentication. Assistant, tool,
   retrieval, attachment, file/search, hook, and delegated-agent output is
   untrusted without an independently verified authority receipt.
2. Do not expose an incomplete tool call/result group to compaction. A result
   must match exactly one retained action by tool call ID and tool name, and
   by action ID where the host supplies one.
3. Do not edit or delete source-event rows, transition rows, ledgers, or
   generations. Rollback changes generation visibility and retains evidence;
   it never rewinds source history.
4. Do not call `ask_agent()` or `execute_tool()` through another reference to
   the host object. Integrated mode refuses both because they bypass recorded,
   guarded execution.
5. Do not call `run()` or `arun()` until a supported exact immutable
   final-request adapter exists. The current guard refuses them even if the
   reviewed distributions are present.
6. Do not relabel `character-estimate-v1`, provider estimates, or the offline
   byte tokenizer as a real-model exact count. Refuse a hard-limit overflow.
7. Do not convert a failed doctor, replay, integrity, recovery, crash, or
   external run into a success record. Retain its report and exit status.
8. Do not infer semantic completeness, correctness, or superiority from a
   verified generation or certificate.

## Storage requirements

Use a local, non-cloud-synchronized, non-symbolic filesystem path. In-memory
databases, Windows UNC paths, and paths beneath symbolic-link parents are
rejected. Network, distributed, and cloud-synchronized filesystem semantics
are not qualified. The store requires SQLite 3.37 or newer for `STRICT` tables
and verifies:

- WAL journal mode;
- `synchronous=FULL`;
- foreign keys enabled;
- dirty reads disabled;
- trusted schema disabled; and
- the configured bounded busy timeout.

Keep the database, `-wal`, and `-shm` files on the same local filesystem. Do
not copy a live database with an ordinary file-copy tool.
`SQLiteGenerationStore.backup()` exclusively creates its destination and
refuses every existing file, link, or alias. It checkpoints the completed
backup, validates its frozen schema and full retained history, and never
overwrites or removes a failed partial backup. Hash and retain a successful
backup separately; retain a failed partial backup as failed evidence.
Report producers likewise require new database/report paths and reserve report
outputs with exclusive creation. A repeated path is an error, not permission
to replace earlier evidence.

Store schema 1 existed only as an unreleased draft. This alpha requires schema
2 and refuses a schema-1 database at open; that refusal is a red gate. There
is no automatic or destructive migration. Before any future separately
reviewed migration, take an SQLite-aware backup, retain the original database,
and export the complete immutable source history. A migration must never
silently drop, delete, or rewrite source events.

The generation states are:

| State | Visible to readers | Recovery meaning |
| --- | --- | --- |
| `prepared` | No | Source snapshot captured; no independently verified result exists. |
| `verified` | No | Bundle and replay evidence passed and are retained. |
| `committed` | No | Verified candidate is durable and eligible for activation. |
| `active` | Yes | The one visible verified generation for the session. |
| `superseded` | No | Former active generation retained for explicit rollback/audit. |
| `rolled_back` | No | Provisional candidate retained but terminally ineligible. |

Activation is one SQLite transaction with source-head, parent-generation, and
active-epoch compare-and-swap checks. After a crash, a reader sees the old or
new active generation. It must never repair state by directly editing SQL.

## Preflight: `doctor`

Run the offline preflight before opening any database:

```sh
ctxc-openhands doctor > doctor-offline.json
```

Exit 0 means:

- the core version matches;
- the self-hashed compatibility manifest verifies;
- SQLite supports strict tables;
- offline tokenizer vectors replay; and
- ordinary imports did not load OpenHands.

It does not mean live readiness. Check `offline_ready`, `live_ready`,
`ordinary_import_loaded_openhands`, `live.issues`, and
`semantic_completeness_claimed` in the report.

The live-required preflight is:

```sh
ctxc-openhands doctor --require-live > doctor-live.json
```

The recorded environment must retain this as a failure with exit 2. Its
machine-readable blocker is `hash-pinned-wheelhouse-absent`. The expanded
scenario blocker is:

> No hash-pinned offline wheelhouse for openhands-ai==1.8.0,
> openhands-sdk==1.27.0, openhands-tools==1.27.0, and
> openhands-agent-server==1.27.0 is present. A real offline/live OpenHands
> demonstration was not executed.

If the exit status unexpectedly becomes zero, stop. Verify the exact package
versions, all reviewed source hashes, the wheelhouse lock and hashes, network
isolation, and the immutable live-request accounting gate before changing any
status or claim.

## Inspect a session: `explain`

```sh
ctxc-openhands explain \
  --database /absolute/local/session.sqlite \
  --session-id conversation-123 > explain.json
```

`explain` opens the database with `require_existing=True`. A missing path,
symbolic path, wrong schema, or invalid store is refused; this diagnostic never
initializes, repairs, or migrates a database. The report includes the immutable
source count/head, active epoch, active generation, tail size, bundle and
semantic digests, certificate, generation transition inventory, and full
integrity report.

Treat any `integrity.passed: false` as a stop condition. Do not run
`recover --apply`, compaction, replay for dispatch, or another model request
until the database and retained evidence have been reviewed. A certificate
with `semantic_completeness_claimed: false` must remain false.

## Replay an immutable request: `replay`

Replay a standalone ledger:

```sh
ctxc-openhands replay --ledger /absolute/evidence/request-ledger.json \
  > replay.json
```

Or replay a retained ledger bound to its database/session/request identity:

```sh
ctxc-openhands replay \
  --database /absolute/local/session.sqlite \
  --session-id conversation-123 \
  --request-id request-456 > replay.json
```

Do not mix the two modes. Stored replay requires all three database arguments.
Exit 0 requires identical:

- ordered transport components and counts;
- total and occupied tokens;
- tokenizer identity, exactness scope, and vector digest;
- source head, active generation, and semantic result digest;
- tool-schema digest;
- transport and final-request digests; and
- ledger self-hash.

The bundled tokenizer makes an exact claim only for the offline canonical
UTF-8 byte protocol. A real OpenHands request has no supported exact tokenizer
or immutable transport capture today. Replay exits 2 on any mismatch or
hard-limit overflow; retain that report as a failure.

## Inspect and recover a crash: `recover`

Always begin without `--apply`:

```sh
ctxc-openhands recover \
  --database /absolute/local/session.sqlite \
  --session-id conversation-123 > recovery-plan.json
```

The default is a diagnostic plan. Confirm the integrity report and take an
SQLite-aware backup before applying:

```sh
ctxc-openhands recover \
  --database /absolute/local/session.sqlite \
  --session-id conversation-123 \
  --apply > recovery-applied.json
```

Applied recovery uses ordinary verified transitions:

- `verified` candidates are reloaded, committed, and then activated through
  compare-and-swap;
- `committed` candidates are activated through compare-and-swap;
- `prepared` candidates have no verified result, so they become retained
  `rolled_back` generations and the complete current source head is recompiled
  into a new generation; and
- stale candidates become retained `rolled_back` generations.

Recovery refuses to proceed when its integrity preflight is red. Exit 2 means
the report did not pass or provisional generations remain. Never make a
candidate active with direct SQL.

## Rehydrate exact source evidence: `rehydrate`

Compute the SHA-256 of the UTF-8 bytes of the expected quote, then request its
exact half-open character span:

```sh
ctxc-openhands rehydrate \
  --database /absolute/local/session.sqlite \
  --session-id conversation-123 \
  --source-id event-789 \
  --start 120 \
  --end 184 \
  --quote-sha256 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
  > rehydrated.json
```

`start` and `end` are Python string indices into retained source content, not
byte offsets or grapheme indices. The result binds the source record, quote,
span, quote digest, and response self-hash. Its trust label is always
`untrusted-evidence`. Exact provenance establishes fidelity only.

## Run the offline crash scenario

Choose output paths that do not exist:

```sh
ctxc-openhands offline-scenario \
  --database /absolute/evidence/offline-scenario.sqlite \
  --output /absolute/evidence/offline-scenario.json
```

The scenario makes no network request and uses no paid service, but the Python
process retains ordinary network capability unless externally isolated. It
appends three constraints, forces three compactions, crashes during the third
activation, reconstructs
fresh store/session objects, activates the retained committed candidate, and
replays/dispatches one immutable fake-runtime request. It checks exact
retention of the PostgreSQL and authentication constraints.

Required report boundaries:

- `status` is `passed-offline-fake-runtime`;
- `live_openhands.status` is `blocked-not-run`;
- `semantic_completeness_claimed` is false;
- the live blocker remains present; and
- `report_sha256` verifies after removing only that self-hash field from the
  canonical payload.

Preserve the JSON report and SQLite database together. Do not describe this as
a recorded live OpenHands scenario.

## Run the seeded crash/concurrency campaign

Choose new database and report paths. The release gate does not accept fewer
than 1,024 schedules:

```sh
ctxc-openhands fault-campaign \
  --database /absolute/evidence/crash-campaign.sqlite \
  --schedules 1024 \
  --seed 0x5A17C7C0 \
  --output /absolute/evidence/crash-campaign.json
```

A complete seeded cycle spans append, prepare, verify, commit, and activation
fault domains. Every schedule injects one in-process fault and then releases
three real worker threads from one barrier; actor winner identity is explicitly
not evidence. Six additional `spawn` subprocesses terminate via `os._exit` at
representative precommit, postcommit, and activation points. The parent reopens
SQLite after each process exit and checks exactly-once source counts,
old-or-new verified generation visibility, and full store integrity.

The self-hashed report records all schedule digests, the stable schedule
manifest digest, seed, per-domain/fault totals, outcome totals, and
`semantic_completeness_claimed: false`. Preserve the report and database
together. A passing result covers only the recorded offline SQLite-WAL
campaign; it is not a live OpenHands result or a semantic-completeness claim.

## Run the deterministic soak

```sh
ctxc-openhands soak \
  --database /absolute/evidence/soak.sqlite \
  --events 10000 \
  --compactions 100 \
  --restart-every 10 \
  --output /absolute/evidence/soak.json
```

The database and report paths must not exist. A passing report proves only the
bounded offline schedule it records: unique events, contiguous sequences,
retained source head, one active generation, 99 superseded generations, and
an integrity report for that run. It is not evidence of semantic
completeness or live host compatibility.

## Verify retained evidence

Verify each retained scenario, soak, or crash-campaign JSON report against the
exact SQLite database produced by the same run:

```sh
ctxc-openhands verify-evidence \
  --report /absolute/evidence/offline-scenario.json \
  --database /absolute/evidence/offline-scenario.sqlite
```

Exit 0 requires the combined `json-and-database` scope. If `--database` is
omitted, the command intentionally performs only bounded JSON validation,
reports `passed: false` and `scope: json-only`, and exits 2. JSON-only
validation cannot verify SQLite bytes, source history, generation states,
request ledgers, or old-or-new visibility. A report self-hash by itself is
recomputable and is not an attestation.

The scenario and soak evidence contract, and the corresponding
crash-campaign contract, bound raw and canonical report JSON to 4 MiB, nesting
depth to 64, structural items to 250,000, and each string to 1 MiB. Scenario
and soak SQLite inputs are capped at 16 GiB; crash-campaign databases are
capped at 512 MiB. Oversize input is rejected rather than truncated. The
10,000-event and 100-compaction soak values are the implemented maxima, not
defaults that may be raised for a release run.

Before report finalization, the producer performs a truncating WAL checkpoint,
requires no remaining WAL/SHM sidecar, hashes and sizes the resulting SQLite
file, binds the checkpoint result and database identity into the report, and
then computes the canonical report self-hash. Verification requires the
sidecars to remain absent, matches those exact database bytes, opens the store
with `require_existing=True`, reconciles source/session/generation/integrity
state and retained request ledgers, and rechecks the original database after
the reconciliation. Never separate the JSON from its database, copy only the
main file from a live WAL store, or overwrite either member of the pair.

For scenario and soak, verification also cross-binds the sole session and
ordered generation rows: count, epoch/parent lineage, state, passed-verification
flag, activation transition, captured source count/head, bundle and semantic
digests, active pointer, and report-specific arrays must match. A storage or
I/O error during final-request ledger replay becomes a structured failed
verification rather than escaping. The scenario recovery operation identifies
the durable recovery path; it does not attest an operating-system crash.

CLI-produced reports bind the argument vector beginning at the producing
subcommand and reconcile each supported flag/value with the report parameters.
This does not capture or attest the shell, executable path, parent process,
terminal, container flags, environment, or operator identity. Direct library
calls honestly record `library-api` and an empty argument vector. Retain the
outer command, environment, image/runtime digest, resource limits, exit status,
and network-control evidence separately.

The producer makes no network request and uses no paid service, but the Python
process retains ordinary network capability unless externally isolated.
Scenario and soak therefore retain
`isolation.network_isolation_enforced: false`; the crash campaign retains
`network_isolation_enforced: false`. Outside the documented read-only
container invocation with `--network=none`, no network isolation is
enforced by these commands. Even inside that container, preserve the external
runtime record showing `--network=none`; do not rewrite the report field to
true. `--read-only` constrains the container filesystem, not its network.

Any mismatch, missing database, unexpected sidecar, path race, malformed
report, or unsupported schema exits 2. Preserve the verifier output and the
original report/database as failed evidence. Do not rerun into the same paths,
patch a report, or treat a later run as verification of earlier bytes.

The 2026-07-27 scenario and soak pairs are hash-intact historical evidence, but
their then-current verifier lacked the ordered report-to-generation bindings
above. They were not rewritten or relabeled and have not been reverified under
the strengthened verifier. The campaign remains a separately verified
historical pair and was not affected by this finding, but it is not durably
hosted or current-head proof. Future qualifying runs must use new
non-overwriting paths and the applicable verifier for each report type.

## Callback failure and EventLog reconciliation

The integration callback executes before the host's default persistence
callback. It catches every error so that OpenHands can still persist the
event, then poisons later ingestion and model dispatch.

When the callback is poisoned:

1. stop model dispatch;
2. retain `callback.status()` and the first bounded failure record;
3. obtain the complete persisted host `EventLog` in exact order;
4. verify that no event has been filtered, reordered, synthesized, or
   truncated;
5. invoke `callback.reconcile(complete_ordered_events)` through reviewed
   application code; and
6. resume only if the retained CtxC source count exactly equals the persisted
   EventLog length and poison clears.

Do not clear the failure flag manually. Unknown event kinds, malformed events,
transient events sent through durable ingestion, authority failures, or
atomic-pair failures must remain visible as incidents.

## Hard-limit and accounting incidents

An exact fake-runtime request is refused when:

```text
payload tokens + reserved output tokens + safety margin tokens > hard limit
```

Do not lower the reserve or margin merely to pass. Reduce the actual immutable
request through an approved product decision, increase the declared hard
limit only when the real model/provider limit permits it, rebuild the complete
request, and create a new ledger. Never edit a retained ledger.

If only an estimated tokenizer is available, report an estimate and refuse
the exact-ledger path. Never coerce an estimated count into
`accounting_mode: exact`.

## Authority and atomicity incidents

- A source/role spoof without a valid independent receipt remains untrusted;
  do not issue a receipt from the event's own metadata.
- A receipt is bound to the exact canonical event, session, event ID, kind,
  role, issuer, and optional tool name. Mutation or replay under another
  session fails closed.
- Unknown tool names remain generic and cannot be authority-promoted.
- A tool result without exactly one retained matching action is rejected.
  Preserve both host EventLog evidence and adapter diagnostics, then reconcile
  rather than fabricating a call or result.
- Known transient events are refused as durable source history. Record the
  diagnostic; do not silently drop a durable host event to make counts align.

## Backup, restore, and evidence retention

Before recovery or migration:

1. stop writers or use the store's SQLite backup API;
2. retain the original database and its hash;
3. retain doctor, explain, recovery, replay, scenario, soak, crash-campaign,
   and evidence-verification JSON with exit statuses and command lines;
4. preserve every scenario/soak/campaign JSON report beside its exact bound
   SQLite database;
5. retain the exact package/revision, Python/SQLite identity, environment,
   resource limits, and externally observed network-isolation evidence; and
6. restore into a new local, non-cloud-synchronized, non-symbolic path, then run
   `doctor`, `explain`,
   integrity checks, and required replays before resuming.

Never overwrite a failed report with a later successful report. Store each
attempt under a new name and preserve chronology.
