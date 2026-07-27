"""Fail-closed OpenHands compatibility identity and offline pin verification.

The package supports one host and SDK release at a time. These constants and
checks are evidence bindings, not claims that a version string authenticates an
installation or that offline review proves live or semantically complete use.
This module deliberately imports no OpenHands package.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from typing import Any

OPENHANDS_HOST_DISTRIBUTION = "openhands-ai"
OPENHANDS_HOST_VERSION = "1.8.0"
OPENHANDS_HOST_COMMIT = "bc26df351dd5d833a95131556dbe2da69af82253"

OPENHANDS_SDK_DISTRIBUTION = "openhands-sdk"
OPENHANDS_SDK_VERSION = "1.27.0"
OPENHANDS_SDK_COMMIT = "904279edf2df5fa12d7caecc7576f62659b2e2dd"

OPENHANDS_TOOLS_DISTRIBUTION = "openhands-tools"
OPENHANDS_TOOLS_VERSION = "1.27.0"

# SHA-256 over sorted ``path<TAB>content-sha256<LF>`` entries for the reviewed
# host/API source set. The exact manifest is packaged under ``data/`` and an
# audit copy is retained in the integration's ``compatibility/`` directory.
OPENHANDS_API_MANIFEST_SHA256 = (
    "c639c67873756e96b3ddebca23e868a5827825531f630b1f4a6913569546c163"
)
OPENHANDS_LLM_SOURCE_SHA256 = (
    "0640aa9330f308a3ba3e6756163ca6deba0d9adf4bf09a876691ca9c564acc7b"
)
OPENHANDS_PIN_MANIFEST_PAYLOAD_SHA256 = (
    "d259602fd35f720b7ad5e37f450c5e5035c4083841e98ae1bd62141da1780d25"
)
OPENHANDS_PIN_MANIFEST_FILE_SHA256 = (
    "3ae4155d60b60c04042fab38b63f07dcdad6432a466ace8b560db16402dfb132"
)
OPENHANDS_REVIEWED_API_FILE_COUNT = 23
OPENHANDS_PIN_MANIFEST_MAX_BYTES = 256 * 1024
OPENHANDS_PIN_MANIFEST_FILENAME = "openhands-1.8.0.json"
SUPPORTED_PYTHON = (3, 12), (3, 13)

_CANONICALIZATION = (
    "UTF-8 JSON, sorted keys, separators comma/colon, ensure_ascii=false, integrity omitted"
)
_LLM_SOURCE_PATH = "openhands-sdk/openhands/sdk/llm/llm.py"
_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "manifest_id",
        "status",
        "claim_boundaries",
        "host",
        "sdk",
        "tools",
        "agent_server",
        "reviewed_api",
        "private_llm_transport",
        "observed_environment",
        "integrity",
    }
)


class CompatibilityManifestError(ValueError):
    """The exact compatibility pin is absent, malformed, or not authentic."""


@dataclass(frozen=True, slots=True)
class CompatibilityVerification:
    """Validated, non-live compatibility evidence."""

    path: Path
    file_sha256: str
    payload_sha256: str
    reviewed_api_sha256: str
    reviewed_api_file_count: int
    blocker_code: str
    real_offline_import: str
    live_scenario: str


def default_pin_manifest_path() -> Path:
    """Return the filesystem-backed packaged compatibility resource."""

    resource = (
        files("ctxc_openhands")
        .joinpath("data")
        .joinpath(OPENHANDS_PIN_MANIFEST_FILENAME)
    )
    if not isinstance(resource, Path):
        _fail("packaged compatibility manifest is not filesystem-backed")
    return resource


def _fail(message: str) -> None:
    raise CompatibilityManifestError(message)


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    _fail(f"non-finite JSON number is forbidden: {value}")


def _mapping(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _fail(f"{where} must be a JSON object")
    return value


def _list(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{where} must be a JSON array")
    return value


def _exact_keys(value: dict[str, Any], expected: frozenset[str], where: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        _fail(f"{where} keys mismatch; missing={missing!r}, unknown={unknown!r}")


def _exact(value: object, expected: object, where: str) -> None:
    if type(value) is not type(expected) or value != expected:
        _fail(f"{where} must equal {expected!r}")


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{where} must be a non-empty string")
    return value


def _hex(value: object, length: int, where: str) -> str:
    text = _text(value, where)
    if len(text) != length or any(character not in "0123456789abcdef" for character in text):
        _fail(f"{where} must be a lowercase {length}-character hexadecimal digest")
    return text


def _validate_digest_fields(value: object, where: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_where = f"{where}.{key}"
            if key == "sha256" or key.endswith("_sha256"):
                _hex(child, 64, child_where)
            elif key == "git_blob_sha1":
                _hex(child, 40, child_where)
            _validate_digest_fields(child, child_where)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_digest_fields(child, f"{where}[{index}]")


def _validate_identity(root: dict[str, Any]) -> None:
    expected = {
        "host": (
            OPENHANDS_HOST_DISTRIBUTION,
            OPENHANDS_HOST_VERSION,
            OPENHANDS_HOST_COMMIT,
            ">=3.12,<3.14",
        ),
        "sdk": (
            OPENHANDS_SDK_DISTRIBUTION,
            OPENHANDS_SDK_VERSION,
            OPENHANDS_SDK_COMMIT,
            ">=3.12",
        ),
        "tools": (
            OPENHANDS_TOOLS_DISTRIBUTION,
            OPENHANDS_TOOLS_VERSION,
            OPENHANDS_SDK_COMMIT,
            ">=3.12",
        ),
    }
    for section, (distribution, version, revision, python_requires) in expected.items():
        item = _mapping(root[section], section)
        _exact(item.get("distribution"), distribution, f"{section}.distribution")
        _exact(item.get("version"), version, f"{section}.version")
        _exact(item.get("revision"), revision, f"{section}.revision")
        _exact(item.get("python_requires"), python_requires, f"{section}.python_requires")
        _hex(item.get("tree"), 40, f"{section}.tree")

    agent_server = _mapping(root["agent_server"], "agent_server")
    _exact(
        agent_server.get("distribution"),
        "openhands-agent-server",
        "agent_server.distribution",
    )
    _exact(agent_server.get("version"), OPENHANDS_SDK_VERSION, "agent_server.version")
    _exact(agent_server.get("revision"), OPENHANDS_SDK_COMMIT, "agent_server.revision")


def _validate_artifacts(root: dict[str, Any]) -> None:
    for section in ("host", "sdk", "tools", "agent_server"):
        item = _mapping(root[section], section)
        artifacts = _mapping(item.get("artifacts"), f"{section}.artifacts")
        _exact_keys(artifacts, frozenset({"wheel", "sdist"}), f"{section}.artifacts")
        for kind in ("wheel", "sdist"):
            artifact = _mapping(artifacts[kind], f"{section}.artifacts.{kind}")
            _exact_keys(
                artifact,
                frozenset({"filename", "sha256", "bytes", "signed", "yanked"}),
                f"{section}.artifacts.{kind}",
            )
            _text(artifact["filename"], f"{section}.artifacts.{kind}.filename")
            _hex(artifact["sha256"], 64, f"{section}.artifacts.{kind}.sha256")
            byte_count = artifact["bytes"]
            if type(byte_count) is not int or byte_count <= 0:
                _fail(f"{section}.artifacts.{kind}.bytes must be a positive integer")
            _exact(artifact["signed"], False, f"{section}.artifacts.{kind}.signed")
            _exact(artifact["yanked"], False, f"{section}.artifacts.{kind}.yanked")


def _validate_status(root: dict[str, Any]) -> dict[str, Any]:
    status = _mapping(root["status"], "status")
    _exact_keys(
        status,
        frozenset(
            {
                "compatibility",
                "real_offline_import",
                "live_scenario",
                "blocker_code",
                "blocker_detail",
                "retain_as_failure",
                "success_claim",
            }
        ),
        "status",
    )
    _exact(status["compatibility"], "reviewed-offline", "status.compatibility")
    _exact(status["real_offline_import"], "blocked", "status.real_offline_import")
    _exact(status["live_scenario"], "blocked", "status.live_scenario")
    _exact(
        status["blocker_code"],
        "hash-pinned-wheelhouse-absent",
        "status.blocker_code",
    )
    _text(status["blocker_detail"], "status.blocker_detail")
    _exact(status["retain_as_failure"], True, "status.retain_as_failure")
    _exact(status["success_claim"], False, "status.success_claim")

    claims = _mapping(root["claim_boundaries"], "claim_boundaries")
    _exact_keys(
        claims,
        frozenset(
            {
                "semantic_completeness",
                "superiority",
                "live_execution_success",
                "authority_from_host_source_literal",
            }
        ),
        "claim_boundaries",
    )
    for key, value in claims.items():
        _exact(value, False, f"claim_boundaries.{key}")

    observed = _mapping(root["observed_environment"], "observed_environment")
    for key in (
        "openhands_ai_installed",
        "openhands_sdk_installed",
        "openhands_tools_installed",
        "openhands_agent_server_installed",
        "hash_pinned_wheelhouse_present",
    ):
        _exact(observed.get(key), False, f"observed_environment.{key}")
    _text(observed.get("observation_date"), "observed_environment.observation_date")
    _text(observed.get("python"), "observed_environment.python")
    return status


def _validate_reviewed_api(root: dict[str, Any]) -> tuple[str, int]:
    reviewed = _mapping(root["reviewed_api"], "reviewed_api")
    _exact_keys(
        reviewed,
        frozenset({"revision", "aggregate_algorithm", "aggregate_sha256", "files"}),
        "reviewed_api",
    )
    _exact(reviewed["revision"], OPENHANDS_SDK_COMMIT, "reviewed_api.revision")
    _exact(
        reviewed["aggregate_algorithm"],
        "sha256 over UTF-8 sorted path<TAB>content-sha256<LF> records",
        "reviewed_api.aggregate_algorithm",
    )
    records = _list(reviewed["files"], "reviewed_api.files")
    if len(records) != OPENHANDS_REVIEWED_API_FILE_COUNT:
        _fail(
            "reviewed_api.files must contain exactly "
            f"{OPENHANDS_REVIEWED_API_FILE_COUNT} records"
        )

    paths: list[str] = []
    digests: list[str] = []
    for index, value in enumerate(records):
        record = _mapping(value, f"reviewed_api.files[{index}]")
        _exact_keys(record, frozenset({"path", "sha256"}), f"reviewed_api.files[{index}]")
        path = _text(record["path"], f"reviewed_api.files[{index}].path")
        if path.startswith(("/", "\\")) or "\\" in path or ".." in Path(path).parts:
            _fail(f"reviewed_api.files[{index}].path must be a safe relative POSIX path")
        paths.append(path)
        digests.append(_hex(record["sha256"], 64, f"reviewed_api.files[{index}].sha256"))

    if paths != sorted(paths):
        _fail("reviewed_api.files must be sorted by path")
    if len(set(paths)) != len(paths):
        _fail("reviewed_api.files contains duplicate paths")

    aggregate_input = "".join(
        f"{path}\t{digest}\n" for path, digest in zip(paths, digests, strict=True)
    ).encode("utf-8")
    aggregate = sha256(aggregate_input).hexdigest()
    _exact(reviewed["aggregate_sha256"], aggregate, "reviewed_api.aggregate_sha256")
    _exact(aggregate, OPENHANDS_API_MANIFEST_SHA256, "reviewed_api computed aggregate")

    try:
        llm_index = paths.index(_LLM_SOURCE_PATH)
    except ValueError:
        _fail("reviewed_api.files is missing the private LLM transport source")
    _exact(digests[llm_index], OPENHANDS_LLM_SOURCE_SHA256, "reviewed_api LLM source")

    transport = _mapping(root["private_llm_transport"], "private_llm_transport")
    _exact_keys(
        transport,
        frozenset(
            {
                "source_path",
                "source_sha256",
                "stability",
                "chat_seam",
                "responses_seam",
                "wire_exactness",
            }
        ),
        "private_llm_transport",
    )
    _exact(transport["source_path"], _LLM_SOURCE_PATH, "private_llm_transport.source_path")
    _exact(
        transport["source_sha256"],
        OPENHANDS_LLM_SOURCE_SHA256,
        "private_llm_transport.source_sha256",
    )
    _exact(
        transport["stability"],
        "private-api-exact-source-hash-required",
        "private_llm_transport.stability",
    )
    _exact(
        transport["chat_seam"],
        "_prepare_transport_kwargs",
        "private_llm_transport.chat_seam",
    )
    _exact(
        transport["responses_seam"],
        "_build_responses_call_kwargs",
        "private_llm_transport.responses_seam",
    )
    _exact(
        transport["wire_exactness"],
        "LiteLLM-bound kwargs are not a byte-identical HTTP-wire claim",
        "private_llm_transport.wire_exactness",
    )
    return aggregate, len(records)


def _read_bounded(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            raw = stream.read(OPENHANDS_PIN_MANIFEST_MAX_BYTES + 1)
    except OSError as exc:
        raise CompatibilityManifestError(
            f"cannot read compatibility manifest {path}: {exc}"
        ) from exc
    if len(raw) > OPENHANDS_PIN_MANIFEST_MAX_BYTES:
        _fail(
            "compatibility manifest exceeds "
            f"{OPENHANDS_PIN_MANIFEST_MAX_BYTES} bytes"
        )
    return raw


def verify_pin_manifest(path: str | Path | None = None) -> CompatibilityVerification:
    """Verify the exact pin without importing or executing OpenHands.

    Any byte change, duplicate key, unknown top-level field, identity drift,
    digest drift, claim widening, or blocker removal fails closed.
    """

    manifest_path = Path(path) if path is not None else default_pin_manifest_path()
    raw = _read_bounded(manifest_path)
    try:
        text = raw.decode("utf-8", errors="strict")
        decoded = json.loads(
            text,
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except CompatibilityManifestError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CompatibilityManifestError(f"invalid compatibility manifest JSON: {exc}") from exc

    root = _mapping(decoded, "manifest")
    _exact_keys(root, _TOP_LEVEL_KEYS, "manifest")
    _exact(root["schema_version"], 1, "schema_version")
    _exact(root["kind"], "ctxc.openhands.compatibility-pin", "kind")
    _exact(root["manifest_id"], "openhands-1.8.0-sdk-1.27.0", "manifest_id")
    _validate_digest_fields(root, "manifest")
    _validate_identity(root)
    _validate_artifacts(root)
    status = _validate_status(root)
    aggregate, file_count = _validate_reviewed_api(root)

    integrity = _mapping(root["integrity"], "integrity")
    _exact_keys(
        integrity,
        frozenset({"payload_canonicalization", "payload_sha256"}),
        "integrity",
    )
    _exact(
        integrity["payload_canonicalization"],
        _CANONICALIZATION,
        "integrity.payload_canonicalization",
    )
    payload = dict(root)
    payload.pop("integrity")
    canonical_payload = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    payload_digest = sha256(canonical_payload).hexdigest()
    _exact(integrity["payload_sha256"], payload_digest, "integrity.payload_sha256")
    _exact(
        payload_digest,
        OPENHANDS_PIN_MANIFEST_PAYLOAD_SHA256,
        "computed manifest payload digest",
    )

    file_digest = sha256(raw).hexdigest()
    _exact(file_digest, OPENHANDS_PIN_MANIFEST_FILE_SHA256, "manifest file digest")
    return CompatibilityVerification(
        path=manifest_path.resolve(),
        file_sha256=file_digest,
        payload_sha256=payload_digest,
        reviewed_api_sha256=aggregate,
        reviewed_api_file_count=file_count,
        blocker_code=status["blocker_code"],
        real_offline_import=status["real_offline_import"],
        live_scenario=status["live_scenario"],
    )
