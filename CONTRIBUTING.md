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
- Do not add assistant, model, tool, or service authorship. Repository commits
  use the sole configured `JamesNguyen42` identity and must not include
  co-author trailers.

## Validation

Run the checks relevant to the change and, before a release candidate, the
complete [release checklist](docs/RELEASE_CHECKLIST.md). At minimum:

```console
python -m pytest -q
python -m ruff check src tests benchmarks scripts conformance
python -m compileall -q src benchmarks tests scripts conformance
```

Generate exploratory reports only at temporary paths. Do not overwrite files in
`docs/results/` or freeze a protocol based on observed comparative results.

## Pull requests

Keep commits atomic and describe the root cause, public/API or stored-format
impact, validation performed, and every remaining red gate. Pull requests stay
draft until the full requested validation is complete. Do not merge, publish to
production PyPI, widen claims, or mark a blocked evidence gate complete without
explicit maintainer approval.
