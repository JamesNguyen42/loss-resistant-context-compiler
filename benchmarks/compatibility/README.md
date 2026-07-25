# Result-blind external compatibility preflights

These records screen pinned external systems against the draft external
comparison contract before any comparative output is generated or inspected.
They are compatibility evidence only: neither record is an inclusion decision,
a completed environment lock, a runnable claim configuration, or evidence of
relative quality.

Primary sources are pinned to immutable GitHub commit and file URLs. The
records deliberately keep `status: "screening"` and `claim_ready: false`.
Missing locks, local source trees, model/runtime matches, and enforced network
isolation remain blockers instead of being inferred from upstream setup prose.

The ACON diagnostic adapter lives at
`benchmarks/adapters/acon_diagnostic/adapter.py`. Its `preflight` subcommand is
safe and result-blind. The `run` subcommand refuses to invoke ACON until the
pinned clean checkout, exact Python, retained dependency lock, exact local Qwen
CLI state, inference-service identity, resource ceilings, and network-policy
evidence are all supplied. A successful diagnostic would emit only rendered
text with an empty provenance claim list; it must not be scored or presented
as a comparison.

Validate the retained records and the executable blocker with:

```console
.venv/Scripts/python -m pytest -q tests/test_external_compatibility.py
```


## Retained ACON runner failure

The result-blind preflight was subsequently routed through
`benchmarks.external_runner` in per-case mode against the committed one-case
gold-free diagnostic corpus. The runner bound the adapter source tree and
entrypoint, ACON revision, Python executable bytes, value-redacted process
environment, portable command, 300-second timeout, 32 GiB adapter ceiling, and
exact local-Qwen model contract. The adapter exited `2` before external-system
execution because its source checkout, Python 3.11 environment, dependency
lock, network-isolation evidence, inference-service accounting, and LM Studio
executable were not supplied.

The self-hashed
`acon-diagnostic-failed-manifest.json` therefore remains
`process_succeeded: false`, `candidate_valid: false`,
`ready_for_scoring: false`, and `claim_metadata_complete: false`; no candidate
file exists. The earlier host preflight blocker is also retained rather than
rewritten. These files are failure evidence, not a benchmark result or an
inclusion/exclusion decision.