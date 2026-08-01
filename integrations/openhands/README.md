# CtxC for OpenHands

`ctxc-openhands` is a separately packaged, fail-closed integration between the
loss-resistant context compiler and one reviewed OpenHands identity. The
ordinary `ctxc` package remains dependency-free and does not import OpenHands.
Importing `ctxc_openhands` also does not import OpenHands; host imports occur
only behind the exact-version compatibility gate.

This package is alpha software. Its offline fake runtime, SQLite-WAL
generation protocol, event mapping, recovery tools, and immutable request
ledger are testable without a model or paid service. A live OpenHands run has
not succeeded and is not claimed.

## Supported identity

The only reviewed host identity is:

- OpenHands `1.8.0` at
  `bc26df351dd5d833a95131556dbe2da69af82253`;
- `openhands-sdk`, `openhands-tools`, and `openhands-agent-server` `1.27.0` at
  `904279edf2df5fa12d7caecc7576f62659b2e2dd`; and
- CPython 3.12 or 3.13.

The closed source and artifact bindings are in the
[compatibility policy](compatibility/README.md) and
[machine-readable pin](compatibility/openhands-1.8.0.json). Version ranges,
unreviewed source bytes, and unknown event kinds fail closed.
The exact 18-class retention, authority, state-promotion, atomicity, and field
policy is documented in the
[event and authority map](docs/EVENT_AUTHORITY_MAP.md).

The retained live blocker is
`hash-pinned-wheelhouse-absent`: the reviewed artifacts are not installed and
there is no local hash-pinned wheelhouse containing their complete dependency
closure. In addition, integrated `run()` and `arun()` remain intentionally
refused until the exact immutable request actually sent by a real provider can
be captured, counted, and replayed. Installing packages manually does not
remove that accounting gate.

## Safety and claim boundaries

- Host `source`, message `role`, tool output, retrieval, file/search output,
  hook output, and delegated-agent output are claims, not authentication.
  They remain untrusted unless an independently issued, event-bound authority
  receipt verifies them. Unreviewed tool names remain generic and cannot
  receive authority.
- An `ActionEvent` and its result remain incomplete until their call ID, tool
  name, and, where available, action ID match. Incomplete tool groups block
  generation visibility; neither half is silently dropped.
- Source events are immutable and retained. Compaction changes which verified
  generation is active; it does not delete or rewrite source history.
- Rehydrated spans are exact source evidence labeled `untrusted-evidence`.
  Fidelity does not establish truth, authority, or instruction priority.
- The SQLite transaction moves through `prepared` → `verified` → `committed`
  → `active`. Only `active` is visible. `superseded` and `rolled_back`
  generations remain retained for audit and recovery.
- A crash during activation exposes the old or new verified generation, not a
  partly switched generation. Source-head and active-epoch compare-and-swap
  checks reject stale candidates.
- No certificate, replay, test, or offline scenario claims semantic
  completeness, live compatibility, or superiority.

## Exact request accounting

The immutable ledger attributes the final fake-runtime transport to all eight
required categories: prompts, verified memory, recent tail, current turn,
retrieval, attachments, tool schemas, and provider framing. It also binds the
model, route, session ID, source head, active generation ID and active epoch,
semantic result digest, exact tokenizer identity and vectors, tool-schema
digest, reserved output, safety
margin, and final request digest.

The bundled exact tokenizer is exact only for the offline
canonical-UTF-8-byte fake protocol. It is not a tokenizer for a real OpenHands
model. Core compilation may report `character-estimate-v1`; that is an
estimate and must never be described as exact. Any request whose counted
payload plus reserved output and margin exceeds its hard limit is refused.

## Source-checkout quickstart

Use a clean checkout and CPython 3.12 or 3.13. These commands run from the
repository root and do not install OpenHands or contact a service.

On POSIX shells:

```sh
export PYTHONPATH="$PWD/src:$PWD/integrations/openhands/src"
python -m ctxc_openhands.cli doctor
python -m ctxc_openhands.cli offline-scenario \
  --database .artifacts/openhands-offline-scenario.sqlite \
  --output .artifacts/openhands-offline-scenario.json
```

On PowerShell:

```powershell
$env:PYTHONPATH = "$PWD\src;$PWD\integrations\openhands\src"
python -m ctxc_openhands.cli doctor
python -m ctxc_openhands.cli offline-scenario `
  --database .artifacts\openhands-offline-scenario.sqlite `
  --output .artifacts\openhands-offline-scenario.json
```

The scenario database and report paths must not already exist. Preserve both
files together. A successful report says `passed-offline-fake-runtime` and
retains `live_openhands.status: blocked-not-run`; it performs three
compactions, injects one activation crash, restarts, and checks that the
PostgreSQL and authentication constraints remain in immutable source history.
It is offline fake-runtime evidence, not a live demonstration.

To make the current live gate observable without weakening it:

```sh
python -m ctxc_openhands.cli doctor --require-live
```

The expected status in the recorded environment is exit code 2. Retain that
failure as evidence; do not reinterpret ordinary `doctor` success as live
readiness.

Structured JSON reports and file-write receipts use strict UTF-8 bytes with
exact LF framing independently of the host stdout text encoding. Output is
written once and is not retried after an incomplete write or failed flush.
This narrow guarantee does not cover argparse help/version text or stderr
diagnostics, which remain owned by the host text streams.

## Guard construction API

The supported construction path is available from the package root without
importing OpenHands. The OpenHands modules are imported only when
`load_pinned_host_api()` has verified the exact installed distributions and
reviewed source bytes.

SQLite storage is qualified only on a local, non-cloud-synchronized,
non-symbolic filesystem. Network, distributed, cloud-synchronized, UNC, and
linked-parent storage are outside the supported boundary.

For evidence producers, construct a fresh database with
`SQLiteGenerationStore(path, require_new=True)`. This reserves the path with
exclusive creation and fails if any leaf already exists. Diagnostic, replay,
recovery, rehydration, and verification paths use
`SQLiteGenerationStore(path, require_existing=True)` so a typo cannot create an
empty store. These modes are mutually exclusive; the compatibility default
opens an existing store or exclusively creates a missing one. Existing stores
are inspected without schema repair or journal-mode conversion. Store schema 2
has no automatic repair or migration path.

```python
from ctxc_openhands import (
    AuthorityPolicy,
    GuardedLocalConversation,
    OpenHandsSession,
    SQLiteGenerationStore,
    load_pinned_host_api,
    pinned_callback,
)


def build_guarded_conversation(*, agent, workspace, database, authority_secret: bytes):
    store = SQLiteGenerationStore(database, require_new=True)
    policy = AuthorityPolicy(
        issuer_secrets={"deployment-authenticator": authority_secret},
        allowed_roles={"deployment-authenticator": frozenset({"user", "tool"})},
        allowed_event_kinds={
            "deployment-authenticator": frozenset({"MessageEvent", "ObservationEvent"})
        },
        trusted_state_tools={"deployment-authenticator": frozenset()},
    )
    session = OpenHandsSession(
        session_id="example-conversation-001",
        store=store,
        authority_policy=policy,
    )
    api = load_pinned_host_api()
    callback = pinned_callback(session, api)
    conversation = api.local_conversation_type(
        agent=agent,
        workspace=workspace,
        persistence_dir=".artifacts/openhands-host-events",
        callbacks=[callback],
        visualizer=None,
    )
    guarded = GuardedLocalConversation(
        conversation,
        api=api,
        callback=callback,
    )
    return guarded, callback, session
```

This construction currently stops at `load_pinned_host_api()` with
`LiveCompatibilityError` because the retained live blocker is unresolved.
Even after that dependency gate is resolved, `guarded.run()` and
`guarded.arun()` intentionally raise
`LiveRequestAccountingUnavailableError` until the host supplies the exact
immutable request/tokenizer hook described above. Do not catch either error
and fall back to an unguarded host object. Authority receipts must be issued
by the independently authenticated boundary with
`issue_authority_receipt()`; host event fields never authenticate themselves.
The caller must supply at least 32 secret bytes from its authenticated secret
boundary; do not hard-code that secret in application source.

For the 10,000-event/100-compaction offline soak, choose a new database and
report path:

```sh
python -m ctxc_openhands.cli soak \
  --database .artifacts/openhands-soak.sqlite \
  --events 10000 \
  --compactions 100 \
  --restart-every 10 \
  --output .artifacts/openhands-soak.json
```

Every evidence output path is exclusive and non-overwriting. Independently
check a retained scenario, soak, or crash-campaign report against its exact
SQLite database:

```sh
python -m ctxc_openhands.cli verify-evidence \
  --report .artifacts/openhands-offline-scenario.json \
  --database .artifacts/openhands-offline-scenario.sqlite
```

Only the combined JSON-and-database check can exit 0. Omitting `--database`
performs a bounded JSON-only diagnostic, reports `passed: false` with scope
`json-only`, and exits 2 because source history, generations, request ledgers,
and SQLite bytes were not verified. Preserve each JSON report and database as
a pair; do not overwrite either file or substitute a later passing run for a
failed one. The [operator runbook](docs/RUNBOOK.md) records the exact canonical
bounds, checkpoint/hash binding, command-argument scope, and filesystem
assumptions.

The 2026-07-27 scenario and 10,000-event/100-compaction soak are hash-intact
historical artifacts that passed the then-current verifier. Independent review
later found that verifier did not bind their ordered generation claims to
database rows. Those pairs were not modified or relabeled and have not been
reverified under the strengthened verifier. The 1,024-schedule campaign remains
a separately verified historical pair; the ordered-generation finding did not
apply to its verifier. It was not rerun in this review cycle. All three remain
temporary rather than live, cross-platform, durable, or current-head release
evidence.

The mission-size pytest case is marked `retained_evidence` and is excluded from
the automatic package matrix. Each ordinary lane proves that exact selection,
uses a path-guard-qualified descendant of `RUNNER_TEMP`, and keeps the bounded
crash primitives. The 10,000/100 soak and 1,024-schedule campaign run only in
the manual/default-off retained-evidence job.

Exact implementation head `1f684d975005a7e552f62f36dfb7309a58b11799`
passed all six automatic Linux, Windows, and macOS Python 3.12/3.13 package jobs
in push run `30331509718` and all six in pull-request run `30331512148`. The
retained-evidence jobs were skipped/default-off, and the manual durable
retained-evidence gate remains pending.

The 2026-07-27 direct-Setuptools sdists remain a retained failure because
generated gzip/member timestamps varied. Initial wrapper head `cf8b8d3`
also remains a failed recursive result: its two source builds matched, but its
extracted rebuild changed only `SOURCES.txt` when generated `setup.cfg` became
an input. Packaging implementation head
`2f692484272aa36bf267703cad2bb4d6926676ff` adds an integration-local
backend whose source bytes are parity-checked against the root backend. With
`SOURCE_DATE_EPOCH=1785225894` and the recorded CPython 3.12.13, pip 25.0.1,
build 1.5.0, Setuptools 83.0.0, and wheel 0.47.0 toolchain, two fresh exact
Git archives and an extracted-sdist rebuild produced the same 232,006-byte
sdist at SHA-256
`9a8f5035d8cbe904dc03142b3be54e3e15fae699153fb7630b4948b7415ac6be`.
The backend is present in the sdist, absent from the runtime wheel, and normal
Setuptools behavior remains when no epoch is supplied. Clean wheel and
recursive-sdist installs passed. The build-tool wheelhouse was still acquired
online without a reviewed hash-pinned closure, so that release gate remains
open. That `2f692484` result did not establish cross-platform artifact
equality. Exact packaging head `2f692484` passed all six automatic Linux,
Windows, and macOS Python 3.12/3.13 jobs in push run `30341548763` and all six
in pull-request run
`30341552866`; retained evidence remained skipped/default-off. Root push CI
`30341549065` and pull-request CI `30341552713` each passed 7/7 jobs;
CodeQL `30341552714` and dependency review `30341553236` passed.

Exact package-gate implementation head
`f9ba3de6f0ab2ac7e861bd7da907e6949df1339e` adds a separate automatic
comparison of the uploaded package files from all six package lanes. OpenHands
push run `30355357305` and pull-request run `30355359105` each passed six package
jobs plus the aggregate; retained evidence was skipped/default-off. Root push
CI `30355357152` and pull-request CI `30355359094` each passed 7/7 jobs; CodeQL
`30355359110` and dependency review `30355359096` passed. The push aggregate
report raw SHA-256/self-hash are
`e9203177989fab536e70febcf5316ba6ea21d2a39ea2d3f8dc2c46b146a6f743`
and `9931d25167831dea6698e0794a93e1cc46a1fc23ed29126c94708aefe7efb35c`.
It binds the implementation revision and reports byte-identical root wheels,
integration wheels, and integration sdists across Linux, macOS, and Windows on
Python 3.12/3.13. The pull-request report is separately bound to GitHub
synthetic merge revision `a585ddbea770e49ff23f49b091536a86fa9db295`, whose
tree equals the implementation tree; its raw SHA-256/self-hash are
`9c8ef8530ffa37c6596d94070f75aaed95ab532af3fbf9737870bfa53b08c942`
and `8ccf05ab81647db1d5030f79ee5e9f367318e923b539b9376cf3e015b04ff2c5`.
These temporary Actions artifacts expire on 2026-08-11. Durable retained
evidence, hash-pinned build inputs, live OpenHands execution, SBOMs,
signatures, provenance attestations, and release authorization remained open at
that checkpoint.

Exact package-input implementation head
`0f20b8a1c131fe3c0908f7d6738790529f42338c` binds package construction to
the tracked 666-byte `requirements-build.lock`, SHA-256
`243f3ab977d82c04968cf4ea6474b7ef79c060d485aa3a3d67a383d1ef6fbbfe`,
and seven exact universal wheels. Acquisition uses `--require-hashes`; the
dedicated builder, builds, and clean installs use the retained no-index
wheelhouse and cross-bind its exact bytes. OpenHands push run `30366252700` and
pull-request run `30366258656` each passed six Linux, Windows, and macOS Python
3.12/3.13 package jobs plus the aggregate; retained evidence stayed
skipped/default-off. Root CI push `30366251022` and pull-request CI
`30366255412` each passed 7/7 jobs; CodeQL `30366255532` and dependency review
`30366255341` passed. The hosted lanes agreed on the 222,688-byte root wheel
(`ec46f710169a95c21c54a28b941d2f5205104113c3e594fe6945ef68f633642f`),
136,967-byte integration wheel
(`16976fa84cebb2b35f1cc15db89a41f016a3a8385498fd2335adbc28c85aacf0`),
and 255,770-byte integration sdist
(`bf51799df63019c3138368a2d6f9e8bc3ddd398163838669340764be36130957`).
The 44,203-byte push report raw SHA-256/self-hash are
`46e47ee4b6f3d1b7ce0fdcc5e41e5f812f2ee0d17005d0c77acd986198213acd`
and `62cd0a5a4ee351dc9fc2c3bd892a79db5394058e96a611c2a47946e0e42ec876`;
the same-size pull-request report raw SHA-256/self-hash are
`4fd96cb025bd557644e670a79c2ae6eeabb244099886d649ab755f7ffb6e8bda`
and `aba29437bf2ee4ba7d5876eea978bc1bf74cba321ebe047e5ff92acf80e69626`.
This closes exact package-build input byte identity for those temporary lanes,
not signed origin, durable retention, the complete live OpenHands dependency
closure, SBOMs, signatures, provenance attestations, or release authorization.

## Installation policy

Production or release installation must use locally reviewed artifacts and a
hash-pinned dependency closure. Do not use an unconstrained online
`pip install`, do not install the `live` extra as evidence of compatibility,
and do not regenerate pins after observing results. The core wheel should be
installed and verified independently from the integration wheel.

The source-checkout quickstart and [container demo](demo/README.md) deliberately
avoid dependency installation. They exercise only the offline boundary.

## Operations and design review

- [Operator runbook](docs/RUNBOOK.md)
- [Offline container demo](demo/README.md)
- [Compatibility and upgrade policy](compatibility/README.md)
- [Release checklist](docs/RELEASE_CHECKLIST.md)
- [Draft upstream public-hook RFC](docs/UPSTREAM_RFC.md)
- [Repository supply-chain plan](https://github.com/JamesNguyen42/loss-resistant-context-compiler/blob/main/docs/SUPPLY_CHAIN.md),
  which records the future SBOM, provenance, and signing work; absent
  signatures and attestations remain a release red gate

`LocalConversation.ask_agent()` is stateless and unrecorded, while
`execute_tool()` bypasses the normal event, confirmation, and security path.
Integrated mode refuses both. Do not bypass the guard proxy or call private
OpenHands request methods directly.
