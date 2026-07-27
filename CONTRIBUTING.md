# Contributing

Thank you for helping make the evidence and implementation more reliable. This
repository is an alpha research project: correctness, retained failures, and
narrow claims take priority over feature count.

## Development setup

Use CPython 3.11 or newer:

```console
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
```

On POSIX, use `.venv/bin/python` instead. Before editing, read `README.md`,
`TODO.md`, `docs/HANDOFF.md`, `docs/ARCHITECTURE.md`,
`docs/BENCHMARKING.md`, `docs/THREAT_MODEL.md`, and
`docs/RELEASE_POLICY.md`. Inspect the branch, diff, frozen evidence, and public
API first.

OpenHands integration changes also require CPython 3.12 or 3.13 and review of
the [separate package README](integrations/openhands/README.md),
[event-authority map](integrations/openhands/docs/EVENT_AUTHORITY_MAP.md),
[operator runbook](integrations/openhands/docs/RUNBOOK.md),
[compatibility policy](integrations/openhands/compatibility/README.md), and
[release checklist](integrations/openhands/docs/RELEASE_CHECKLIST.md).

## Required engineering rules

- Preserve standalone `context_compiler` and `ctxc` behavior and keep the core
  runtime dependency-free.
- Preserve fail-closed provenance, authority, protected retention, replay,
  bounded decoding, and claim boundaries.
- Never imply semantic completeness or external superiority from local or
  synthetic evidence.
- Never tune, replace, or rescore frozen evidence after observing results.
- Retain failed external and natural-history runs as failures; do not silently
  drop histories or cases.
- Add a focused regression for every security, parsing, integrity, or
  concurrency change.
- Keep stored-format changes versioned, strict, and free of silent migration.
- Keep `ctxc-openhands` separately packaged. Do not add OpenHands or its
  dependencies to the core graph or ordinary import path.
- Keep unknown host events fail-closed, host source/role claims
  unauthenticated, tool pairs atomic, source history immutable, and rehydrated
  spans untrusted.
- Use local, non-cloud-synchronized, non-symbolic paths for integration SQLite
  evidence. Schema 2 has no repair/automatic migration; new reports, stores,
  and backups must not overwrite an existing path.
- Preserve each scenario/soak/campaign JSON report with its exact SQLite
  database. JSON-only evidence verification must remain a failed scope, and
  report network-isolation fields must remain false absent report-level
  attestation.
- Do not add assistant, model, tool, or service authorship. Repository commits
  use the sole configured `JamesNguyen42` identity and must not include
  co-author trailers.

## Validation

Run the checks relevant to the change and, before a release candidate, the
complete [release checklist](docs/RELEASE_CHECKLIST.md). At minimum:

```console
python -m pytest -q
python -m ruff check src tests benchmarks scripts conformance _ctxc_build_backend.py
python -m compileall -q src benchmarks tests scripts conformance _ctxc_build_backend.py
```

For an OpenHands integration change, install it without live dependencies and
run its isolated checks too:

```console
python -m pip install -e integrations/openhands --no-deps
python -m pytest -q integrations/openhands/tests
python -m ruff check integrations/openhands/src integrations/openhands/tests integrations/openhands/scripts
python -m compileall -q integrations/openhands/src integrations/openhands/tests integrations/openhands/scripts
python -m ctxc_openhands.cli doctor
```

Generate exploratory reports only at temporary paths. Do not overwrite files in
`docs/results/`, reuse evidence output paths, or freeze a protocol based on
observed comparative results. Never install the `live` extra to make a blocked
compatibility result look green.

## Pull requests

Keep commits atomic and describe the root cause, public/API or stored-format
impact, validation performed, and every remaining red gate. Pull requests stay
draft until the full requested validation is complete. Do not merge, publish to
production PyPI, widen claims, or mark a blocked evidence gate complete without
explicit maintainer approval.
