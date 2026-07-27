# Offline container demo

This container runs the `ctxc-openhands` source-checkout diagnostics without
installing OpenHands, resolving packages, contacting a model, or using a paid
service. It is an offline fake-runtime demonstration only. See the
[package boundaries](../README.md) and [operator runbook](../docs/RUNBOOK.md)
before using its output.

## Supply-chain boundary

The Dockerfile has a fail-safe `scratch` default, which contains no Python and
cannot run the demo. Supply a CPython 3.12 or 3.13 image that is already
present locally and referenced by an immutable digest:

```text
registry.example/python-runtime@sha256:<64-lowercase-hex-digest>
```

The operator is responsible for reviewing and retaining that image's
provenance, license, architecture, SBOM, signature/attestation status, and
digest. Do not substitute a mutable tag. The Dockerfile contains no `RUN`
instruction and performs no package installation.

Build from the repository root with the network disabled and pulling
disabled:

```sh
export CTXC_PYTHON_IMAGE='registry.example/python-runtime@sha256:<reviewed-digest>'
docker image inspect "$CTXC_PYTHON_IMAGE" >/dev/null
docker build \
  --pull=false \
  --network=none \
  --build-arg PYTHON_IMAGE="$CTXC_PYTHON_IMAGE" \
  --file integrations/openhands/demo/Dockerfile \
  --tag ctxc-openhands-offline:local \
  .
```

Replace the placeholder with the complete reviewed digest before execution.
Omitting the argument produces a deliberately non-runnable image; an empty
argument or mutable tag is outside this demo's release procedure. Retain the
resolved base-image ID and the resulting image digest with the run evidence.

The image copies only the core source, integration source, and self-hashed
compatibility data needed by the offline CLI. It does not package or validate
a release wheel.

## Offline doctor

```sh
docker run --rm \
  --network=none \
  --read-only \
  ctxc-openhands-offline:local doctor
```

Exit 0 is offline readiness only. Verify that:

- `ordinary_import_loaded_openhands` is false;
- `offline_ready` is true;
- `live_ready` is false in the recorded environment;
- the retained blocker is `hash-pinned-wheelhouse-absent`; and
- `semantic_completeness_claimed` is false.

The live-required check is expected to remain red:

```sh
docker run --rm \
  --network=none \
  --read-only \
  ctxc-openhands-offline:local doctor --require-live
```

Retain its JSON and exit code 2. Do not present the ordinary doctor result as
a successful OpenHands import or live run.

## Recorded offline crash scenario

The image runs as numeric UID/GID `65532:65532`. Create a new host evidence
directory writable by that identity, or override the container user with an
equally constrained local identity. Do not reuse an existing database or
report path.

```sh
mkdir -p .artifacts/openhands-container-scenario
docker run --rm \
  --read-only \
  --network=none \
  --mount \
type=bind,src="$PWD/.artifacts/openhands-container-scenario",dst=/evidence \
  ctxc-openhands-offline:local offline-scenario \
  --database /evidence/scenario.sqlite \
  --output /evidence/scenario.json
```

If the bind mount is not writable by UID 65532, fix the host-directory
ownership or use the runtime's reviewed user-mapping facility. Do not run a
privileged container to bypass the check.

Retain together:

- `scenario.sqlite`;
- `scenario.json`;
- the container image digest;
- the immutable base-image reference;
- the complete command and exit status; and
- evidence that `--network=none` was enforced.

The report must say `passed-offline-fake-runtime` and
`live_openhands.status: blocked-not-run`. It records three compactions, one
injected activation crash/restart, immutable retention of the PostgreSQL and
authentication constraints, and exact replay for the fake canonical-byte
protocol. It does not demonstrate a real OpenHands event loop, model
tokenizer, final provider request, or provider response.

The report deliberately retains
`isolation.network_isolation_enforced: false`: Python cannot attest the
container runtime flags. Preserve the external `docker run --network=none`
record instead of editing that field. `--read-only` constrains the container
root; it does not provide network isolation. Outside this documented
read-only, `--network=none` invocation, the demo commands do not enforce
process-level network isolation.

Independently verify the report/database pair through the same read-only
image:

```sh
docker run --rm \
  --read-only \
  --network=none \
  --mount \
type=bind,src="$PWD/.artifacts/openhands-container-scenario",dst=/evidence \
  ctxc-openhands-offline:local verify-evidence \
  --report /evidence/scenario.json \
  --database /evidence/scenario.sqlite
```

Only this DB-backed scope can exit 0. Omitting `--database` is a JSON-only
diagnostic that reports `passed: false` and exits 2. Retain that distinction;
a self-hashed JSON report is not a substitute for the exact SQLite bytes.

## Optional deterministic soak

Use a separate new evidence directory:

```sh
mkdir -p .artifacts/openhands-container-soak
docker run --rm \
  --read-only \
  --network=none \
  --mount type=bind,src="$PWD/.artifacts/openhands-container-soak",dst=/evidence \
  ctxc-openhands-offline:local soak \
  --database /evidence/soak.sqlite \
  --events 10000 \
  --compactions 100 \
  --restart-every 10 \
  --output /evidence/soak.json
```

The 10,000-event and 100-compaction values are the implemented maxima. A
passing soak is evidence only for its deterministic offline schedule. Preserve
`soak.json` and `soak.sqlite` together and verify them with
`verify-evidence` against `/evidence/soak.json` and
`/evidence/soak.sqlite`. It does not remove the
`hash-pinned-wheelhouse-absent` live blocker or widen semantic claims.

## Deliberately absent

This demo does not:

- install the `live` extra or any unpinned dependency;
- fetch a wheelhouse or base image;
- run `ask_agent()`, `execute_tool()`, `run()`, or `arun()`;
- send provider traffic;
- claim byte-identical provider HTTP capture;
- claim semantic completeness or superiority; or
- publish an image or package.
