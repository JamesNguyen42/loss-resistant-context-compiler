"""Lazy, fail-closed boundary to the optional ``localai-contracts`` package.

The ordinary :mod:`context_compiler` package does not import this module.  This
module, in turn, imports ``localai_contracts`` only when an adapter is created.
The legacy six-operation connector and its JSONL protocol remain separate.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.machinery
import importlib.metadata
import importlib.util
import marshal
import os
import stat
import sys
import threading
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .connector import LocalAIConnector
from .connector import SourceEvent as PrivateSourceEvent
from .path_safety import _is_link_or_reparse

LOCALAI_CONTRACTS_DISTRIBUTION = "localai-contracts"
LOCALAI_CONTRACTS_VERSION = "0.2.0a2"
LOCALAI_CONTRACTS_PROTOCOL_VERSION = "1.0.0"
LOCALAI_CONTRACTS_WHEEL_SHA256 = (
    "36a02dbc4267402949dddda1da180d800590cc579e0c1ecb022fc96f6a7c29ae"
)
LOCALAI_CONTRACTS_SOURCE_COMMIT = "3858190e8b458847da94e9ed24be83f4928b7d1a"
CONTEXT_COMPILE_OPERATION = "context.compile"
MAX_CONTEXT_SOURCE_EVENTS = 8
MAX_CONTEXT_SPANS = 10_000
_PROJECTION_VERSION = "ctxc-localai-context-bundle-projection-v1"
_CONTRACTS_IMPORT_NAME = "localai_contracts"
_CONTRACTS_VALIDATION_ERROR = (
    "localai-contracts 0.2.0a2 failed optional-adapter validation"
)
_CONTRACTS_INSTALLED_TREE_SHA256 = (
    "296f49a2d7b48158d2d3a33e36b77d5b5c495362cbe3aceaaf8975fb256e538c"
)
_MAX_CONTRACTS_TREE_ENTRIES = 256
_MAX_CONTRACTS_BYTECODE_BYTES = 4 * 1024 * 1024
_CONTRACTS_INSTALLED_FILES = (
    ("__init__.py", 6_474),
    ("_integration_bootstrap.py", 1_684),
    ("canonical.py", 8_389),
    ("conformance.py", 60_091),
    ("connector.py", 40_852),
    ("errors.py", 1_862),
    ("fixtures/phase0-conformance-v1.json", 7_551),
    ("inference_lease.py", 92_469),
    ("integration.py", 141_988),
    ("models.py", 14_235),
    ("privacy.py", 2_139),
    ("profiles.py", 876),
    ("profiles/r9700-qwen3.6-q4.development.json", 1_743),
    ("py.typed", 27),
    ("schemas/common.schema.json", 2_817),
    ("schemas/component-capability-manifest.schema.json", 3_386),
    ("schemas/connector-request.schema.json", 1_030),
    ("schemas/connector-response.schema.json", 1_198),
    ("schemas/context-bundle.schema.json", 4_248),
    ("schemas/deployment-plan.schema.json", 3_452),
    ("schemas/error-envelope.schema.json", 224),
    ("schemas/generation-receipt.schema.json", 2_385),
    ("schemas/identity-envelope.schema.json", 2_786),
    ("schemas/model-state-handle.schema.json", 2_221),
    ("schemas/runtime-control-plan.schema.json", 4_169),
    ("schemas/source-event.schema.json", 996),
    ("schemas/telemetry-event.schema.json", 2_935),
    ("smoke.py", 80_032),
    ("validation.py", 10_997),
)


class LocalAIContractsUnavailableError(RuntimeError):
    """The exact optional contract package is absent or incompatible."""


@dataclass(frozen=True, slots=True)
class AuthenticatedAuthority:
    """An out-of-band authentication decision made by the embedding host.

    Merely setting ``SourceEvent.trust`` or event metadata never constructs this
    value.  The host-owned verifier callback must return it independently.
    """

    issuer: str
    trusted_for_state: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.issuer, str)
            or not self.issuer
            or self.issuer != self.issuer.strip()
        ):
            raise TypeError("authority issuer must be a non-empty trimmed string")
        if len(self.issuer) > 256:
            raise ValueError("authority issuer exceeds 256 characters")
        if not isinstance(self.trusted_for_state, bool):
            raise TypeError("trusted_for_state must be boolean")


AuthorityVerifier = Callable[[Any], AuthenticatedAuthority | None]
ConnectorFactory = Callable[[], LocalAIConnector]


@dataclass(frozen=True, slots=True)
class _ContractsOrigin:
    package_directory: Path
    initializer: Path


def _validation_failed() -> LocalAIContractsUnavailableError:
    return LocalAIContractsUnavailableError(_CONTRACTS_VALIDATION_ERROR)


def _same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except (OSError, ValueError):
        return False


def _validated_namespace(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError("contracts namespace is not an exact dictionary")
    keys = tuple(value)
    if any(type(key) is not str for key in keys):
        raise ValueError("contracts namespace key is not an exact string")
    return value


def _validate_source_loader(
    loader: object,
    *,
    module_name: str,
    expected_path: Path,
) -> None:
    if type(loader) is not importlib.machinery.SourceFileLoader:
        raise ValueError("contracts source loader type mismatch")
    loader_state = _validated_namespace(vars(loader))
    if sorted(loader_state) != ["name", "path"]:
        raise ValueError("contracts source loader state mismatch")
    loader_name = loader_state.get("name")
    loader_path = loader_state.get("path")
    if (
        type(loader_name) is not str
        or loader_name != module_name
        or type(loader_path) is not str
        or not _same_file(Path(loader_path), expected_path)
    ):
        raise ValueError("contracts source loader origin mismatch")


def _hash_expected_regular_file(
    path: Path,
    *,
    relative: str,
    expected_size: int,
    tree_digest: Any,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.stat(path, follow_symlinks=False)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or _is_link_or_reparse(before)
            or not stat.S_ISREG(opened.st_mode)
            or before.st_dev != opened.st_dev
            or before.st_ino != opened.st_ino
            or before.st_size != expected_size
            or opened.st_size != expected_size
        ):
            raise ValueError("contracts file identity mismatch")
        encoded_name = relative.encode("utf-8")
        tree_digest.update(len(encoded_name).to_bytes(4, "big"))
        tree_digest.update(encoded_name)
        tree_digest.update(expected_size.to_bytes(8, "big"))
        remaining = expected_size
        contents = bytearray()
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                raise ValueError("contracts file truncated")
            tree_digest.update(chunk)
            contents.extend(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValueError("contracts file exceeds expected size")
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
        ):
            raise ValueError("contracts file changed during validation")
        return bytes(contents)
    finally:
        os.close(descriptor)


def _read_bounded_regular_file(path: Path, *, maximum_size: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.stat(path, follow_symlinks=False)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or _is_link_or_reparse(before)
            or not stat.S_ISREG(opened.st_mode)
            or before.st_dev != opened.st_dev
            or before.st_ino != opened.st_ino
            or opened.st_size < 16
            or opened.st_size > maximum_size
        ):
            raise ValueError("contracts bytecode identity mismatch")
        remaining = opened.st_size
        contents = bytearray()
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                raise ValueError("contracts bytecode truncated")
            contents.extend(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValueError("contracts bytecode exceeds expected size")
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
        ):
            raise ValueError("contracts bytecode changed during validation")
        return bytes(contents)
    finally:
        os.close(descriptor)


def _validate_bytecode_cache(
    path: Path,
    *,
    source: bytes,
    source_path: Path,
    optimize: int,
) -> None:
    cached = _read_bounded_regular_file(
        path,
        maximum_size=_MAX_CONTRACTS_BYTECODE_BYTES,
    )
    flags = int.from_bytes(cached[4:8], "little")
    expected_code = compile(
        source,
        str(source_path),
        "exec",
        dont_inherit=True,
        optimize=optimize,
    )
    if (
        cached[:4] != importlib.util.MAGIC_NUMBER
        or flags not in {0, 1, 3}
        or cached[16:] != marshal.dumps(expected_code)
    ):
        raise ValueError("contracts bytecode does not match verified source")


def _validate_installed_tree_shape(
    package_directory: Path,
) -> tuple[tuple[Path, str, int], ...]:
    expected_files = {path for path, _size in _CONTRACTS_INSTALLED_FILES}
    expected_directories: set[str] = set()
    for relative in expected_files:
        parts = relative.split("/")
        expected_directories.update(
            "/".join(parts[:index]) for index in range(1, len(parts))
        )
    expected_cache_names = {
        Path(
            importlib.util.cache_from_source(
                str(package_directory / relative),
                optimization=tag,
            )
        ).name: (
            relative,
            optimize,
        )
        for relative in expected_files
        if relative.endswith(".py") and "/" not in relative
        for optimize, tag in ((0, ""), (1, "1"), (2, "2"))
    }
    cached_files: list[tuple[Path, str, int]] = []
    observed_files: set[str] = set()
    pending = [(package_directory, "")]
    entry_count = 0
    while pending:
        directory, prefix = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                entry_count += 1
                if entry_count > _MAX_CONTRACTS_TREE_ENTRIES:
                    raise ValueError("contracts package tree exceeds limit")
                relative = f"{prefix}/{entry.name}" if prefix else entry.name
                entry_stat = entry.stat(follow_symlinks=False)
                if _is_link_or_reparse(entry_stat):
                    raise ValueError("contracts package tree contains a link")
                if stat.S_ISDIR(entry_stat.st_mode):
                    if relative == "__pycache__":
                        with os.scandir(entry.path) as cache_entries:
                            for cached in cache_entries:
                                entry_count += 1
                                if entry_count > _MAX_CONTRACTS_TREE_ENTRIES:
                                    raise ValueError(
                                        "contracts package tree exceeds limit"
                                    )
                                cached_stat = cached.stat(follow_symlinks=False)
                                if (
                                    _is_link_or_reparse(cached_stat)
                                    or not stat.S_ISREG(cached_stat.st_mode)
                                ):
                                    raise ValueError(
                                        "contracts bytecode cache is unexpected"
                                    )
                                cache_identity = expected_cache_names.get(cached.name)
                                if cache_identity is None:
                                    raise ValueError(
                                        "contracts bytecode cache is unexpected"
                                    )
                                cached_files.append((Path(cached.path), *cache_identity))
                        continue
                    if relative not in expected_directories:
                        raise ValueError("contracts package directory is unexpected")
                    pending.append((Path(entry.path), relative))
                    continue
                if (
                    not stat.S_ISREG(entry_stat.st_mode)
                    or relative not in expected_files
                ):
                    raise ValueError("contracts package file is unexpected")
                observed_files.add(relative)
    if observed_files != expected_files:
        raise ValueError("contracts package file set mismatch")
    return tuple(cached_files)


def _module_file_for_name(name: str) -> str | None:
    if name == _CONTRACTS_IMPORT_NAME:
        return "__init__.py"
    prefix = f"{_CONTRACTS_IMPORT_NAME}."
    if not name.startswith(prefix):
        return None
    relative = name.removeprefix(prefix).replace(".", "/")
    expected_files = {path for path, _size in _CONTRACTS_INSTALLED_FILES}
    module_file = f"{relative}.py"
    if module_file in expected_files:
        return module_file
    package_file = f"{relative}/__init__.py"
    if package_file in expected_files:
        return package_file
    return None


def _validate_loaded_modules(origin: _ContractsOrigin) -> None:
    prefix = f"{_CONTRACTS_IMPORT_NAME}."
    modules = vars(sys).get("modules")
    if type(modules) is not dict:
        raise ValueError("invalid interpreter module registry")
    for name, module in tuple(modules.items()):
        if type(name) is not str:
            raise ValueError("invalid interpreter module name")
        if name != _CONTRACTS_IMPORT_NAME and not name.startswith(prefix):
            continue
        if type(module) is not types.ModuleType:
            raise ValueError("invalid preloaded contracts module")
        module_state = _validated_namespace(vars(module))
        if module_state.get("__name__") != name:
            raise ValueError("invalid preloaded contracts module")
        relative = _module_file_for_name(name)
        if relative is None:
            raise ValueError("unexpected preloaded contracts module")
        expected = origin.package_directory.joinpath(*relative.split("/"))
        module_spec = module_state.get("__spec__")
        if type(module_spec) is not importlib.machinery.ModuleSpec:
            raise ValueError("preloaded contracts module spec mismatch")
        spec_state = _validated_namespace(vars(module_spec))
        module_origin = spec_state.get("origin")
        module_file = module_state.get("__file__")
        if (
            module_state.get("__loader__") is not spec_state.get("loader")
            or type(module_origin) is not str
            or type(module_file) is not str
            or not _same_file(Path(module_origin), expected)
            or not _same_file(Path(module_file), expected)
        ):
            raise ValueError("preloaded contracts module origin mismatch")
        _validate_source_loader(
            spec_state.get("loader"),
            module_name=name,
            expected_path=expected,
        )
        expected_package = (
            _CONTRACTS_IMPORT_NAME
            if name == _CONTRACTS_IMPORT_NAME
            else name.rpartition(".")[0]
        )
        if module_state.get("__package__") != expected_package:
            raise ValueError("preloaded contracts module package mismatch")
        if name == _CONTRACTS_IMPORT_NAME:
            search_locations = module_state.get("__path__")
            spec_search_locations = spec_state.get("submodule_search_locations")
            if (
                type(search_locations) is not list
                or len(search_locations) != 1
                or type(search_locations[0]) is not str
                or type(spec_search_locations) is not list
                or len(spec_search_locations) != 1
                or type(spec_search_locations[0]) is not str
                or not _same_file(
                    Path(search_locations[0]),
                    origin.package_directory,
                )
                or not _same_file(
                    Path(spec_search_locations[0]),
                    origin.package_directory,
                )
            ):
                raise ValueError("preloaded contracts package path mismatch")
        elif spec_state.get("submodule_search_locations") is not None:
            raise ValueError("preloaded contracts submodule is a package")


def _preflight_contracts_origin() -> _ContractsOrigin:
    try:
        interpreter_state = _validated_namespace(vars(sys))
        if (
            type(interpreter_state.get("modules")) is not dict
            or interpreter_state.get("pycache_prefix") is not None
        ):
            raise ValueError("external bytecode cache prefix is unsupported")
        modules = interpreter_state["modules"]
        if any(type(name) is not str for name in tuple(modules)):
            raise ValueError("invalid interpreter module name")
        distributions = list(
            importlib.metadata.distributions(name=LOCALAI_CONTRACTS_DISTRIBUTION)
        )
        if len(distributions) != 1:
            raise ValueError("contracts distribution is absent or ambiguous")
        distribution = distributions[0]
        distribution_name = distribution.metadata.get("Name")
        if (
            not isinstance(distribution_name, str)
            or distribution_name.casefold() != LOCALAI_CONTRACTS_DISTRIBUTION
            or distribution.version != LOCALAI_CONTRACTS_VERSION
        ):
            raise ValueError("contracts distribution identity mismatch")

        recorded_files = distribution.files
        if recorded_files is None:
            raise ValueError("contracts distribution has no file inventory")
        by_name: dict[str, list[Any]] = {}
        for recorded in recorded_files:
            by_name.setdefault(recorded.as_posix(), []).append(recorded)
        expected_paths = {
            f"{_CONTRACTS_IMPORT_NAME}/{relative}"
            for relative, _size in _CONTRACTS_INSTALLED_FILES
        }
        if any(len(by_name.get(path, ())) != 1 for path in expected_paths):
            raise ValueError("contracts distribution inventory mismatch")

        initializer_record = by_name[f"{_CONTRACTS_IMPORT_NAME}/__init__.py"][0]
        initializer = Path(distribution.locate_file(initializer_record))
        package_directory = initializer.parent
        directory_stat = os.stat(package_directory, follow_symlinks=False)
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or _is_link_or_reparse(directory_stat)
        ):
            raise ValueError("contracts package is not a directory")

        spec = importlib.util.find_spec(_CONTRACTS_IMPORT_NAME)
        if type(spec) is not importlib.machinery.ModuleSpec:
            raise ValueError("contracts import spec is invalid")
        spec_state = _validated_namespace(vars(spec))
        spec_origin = spec_state.get("origin")
        spec_loader = spec_state.get("loader")
        spec_locations = spec_state.get("submodule_search_locations")
        if (
            spec_state.get("name") != _CONTRACTS_IMPORT_NAME
            or spec_state.get("_set_fileattr") is not True
            or type(spec_origin) is not str
            or type(spec_loader) is not importlib.machinery.SourceFileLoader
            or type(spec_locations) is not list
        ):
            raise ValueError("contracts import spec is invalid")
        if (
            len(spec_locations) != 1
            or type(spec_locations[0]) is not str
            or not _same_file(Path(spec_origin), initializer)
            or not _same_file(Path(spec_locations[0]), package_directory)
        ):
            raise ValueError("contracts import spec origin mismatch")
        import_initializer = Path(spec_origin)
        import_package_directory = Path(spec_locations[0])
        _validate_source_loader(
            spec_loader,
            module_name=_CONTRACTS_IMPORT_NAME,
            expected_path=import_initializer,
        )
        if (
            not import_initializer.is_absolute()
            or not import_package_directory.is_absolute()
        ):
            raise ValueError("contracts import spec paths are not absolute")
        origin = _ContractsOrigin(
            package_directory=import_package_directory,
            initializer=import_initializer,
        )
        cached_files = _validate_installed_tree_shape(origin.package_directory)
        tree_digest = hashlib.sha256()
        verified_sources: dict[str, bytes] = {}
        for relative, expected_size in _CONTRACTS_INSTALLED_FILES:
            recorded = by_name[f"{_CONTRACTS_IMPORT_NAME}/{relative}"][0]
            located = Path(distribution.locate_file(recorded))
            expected = origin.package_directory.joinpath(*relative.split("/"))
            if not _same_file(located, expected):
                raise ValueError("contracts distribution file origin mismatch")
            contents = _hash_expected_regular_file(
                expected,
                relative=relative,
                expected_size=expected_size,
                tree_digest=tree_digest,
            )
            if relative.endswith(".py"):
                verified_sources[relative] = contents
        if tree_digest.hexdigest() != _CONTRACTS_INSTALLED_TREE_SHA256:
            raise ValueError("contracts installed tree digest mismatch")
        for cached_path, source_relative, optimize in cached_files:
            _validate_bytecode_cache(
                cached_path,
                source=verified_sources[source_relative],
                source_path=origin.package_directory.joinpath(
                    *source_relative.split("/")
                ),
                optimize=optimize,
            )
        _validate_loaded_modules(origin)
        return origin
    except Exception:
        raise _validation_failed() from None


@lru_cache(maxsize=1)
def _load_contracts() -> Any:
    origin = _preflight_contracts_origin()
    try:
        contracts = importlib.import_module(_CONTRACTS_IMPORT_NAME)
    except Exception:
        raise _validation_failed() from None
    if (
        sys.modules.get(_CONTRACTS_IMPORT_NAME) is not contracts
        or _preflight_contracts_origin() != origin
    ):
        raise _validation_failed() from None
    try:
        contracts_state = _validated_namespace(vars(contracts))
        if contracts_state.get("__version__") != LOCALAI_CONTRACTS_VERSION:
            raise ValueError("contracts runtime version mismatch")
        if (
            contracts_state.get("PROTOCOL_VERSION")
            != LOCALAI_CONTRACTS_PROTOCOL_VERSION
        ):
            raise ValueError("contracts protocol version mismatch")
    except Exception:
        raise _validation_failed() from None
    required = (
        "ComponentCapabilityManifest",
        "ConnectorRequest",
        "ConnectorResponse",
        "ConnectorServer",
        "ContextBundle",
        "DEFAULT_PARSE_LIMITS",
        "NdjsonConnectorServer",
        "ParseLimits",
        "SourceEvent",
        "SubjectOperationProbe",
        "SubjectOperationPurpose",
        "UnsupportedOperationError",
        "bounded_canonical_bytes",
        "canonical_bytes",
        "parse_json",
    )
    try:
        if any(name not in contracts_state for name in required):
            raise ValueError("contracts API mismatch")
    except Exception:
        raise _validation_failed() from None
    return contracts


def _component_version() -> str:
    from . import __version__

    return __version__


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_record(value: str) -> dict[str, str]:
    return {"algorithm": "sha256", "value": value}


def _stable_id(kind: str, *parts: object) -> str:
    body = "\x00".join(str(part) for part in parts).encode("utf-8")
    return f"ctxc-{kind}-{_digest(body)}"


class LocalAIContractsAdapter:
    """Execute the one canonical, non-inference ``context.compile`` operation."""

    def __init__(
        self,
        *,
        connector_factory: ConnectorFactory | None = None,
        authority_verifier: AuthorityVerifier | None = None,
        limits: Any | None = None,
    ) -> None:
        contracts = _load_contracts()
        if connector_factory is not None and not callable(connector_factory):
            raise TypeError("connector_factory must be callable")
        if authority_verifier is not None and not callable(authority_verifier):
            raise TypeError("authority_verifier must be callable")
        selected_limits = (
            contracts.DEFAULT_PARSE_LIMITS if limits is None else limits
        )
        if type(selected_limits) is not contracts.ParseLimits:
            raise TypeError("limits must be localai_contracts.ParseLimits")

        self._contracts = contracts
        self._connector_factory = connector_factory or LocalAIConnector
        self._authority_verifier = authority_verifier
        self._limits = selected_limits
        self._request_server = contracts.ConnectorServer(
            self,
            limits=self._limits,
        )
        self._request_lock = threading.RLock()

    @property
    def limits(self) -> Any:
        """Return the immutable parser/frame limits shared by both transports."""

        return self._limits

    def get_manifest(self) -> Any:
        """Return an actual validated ``ComponentCapabilityManifest``."""

        contracts = self._contracts
        manifest = contracts.ComponentCapabilityManifest(
            component_name="loss-resistant-context-compiler",
            component_version=_component_version(),
            supported_operations=[CONTEXT_COMPILE_OPERATION],
            supported_schema_versions={
                name: [LOCALAI_CONTRACTS_PROTOCOL_VERSION]
                for name in (
                    "ComponentCapabilityManifest",
                    "ConnectorRequest",
                    "ConnectorResponse",
                    "ErrorEnvelope",
                    "SourceEvent",
                    "ContextBundle",
                )
            },
            restrictions={"models": [], "runtimes": [], "backends": []},
            operation_capabilities=[
                {
                    "operation": CONTEXT_COMPILE_OPERATION,
                    "evidence": "executed",
                    "limitations": [
                        "Canonical trust is not authentication.",
                        "Only independently authenticated exact source spans may "
                        "enter trusted active memory.",
                        "Accounting covers projected span-content components only.",
                    ],
                    "disabled_reason": None,
                }
            ],
            limitations=[
                {
                    "code": "no_inference",
                    "message": "The adapter performs no model inference.",
                    "operation": CONTEXT_COMPILE_OPERATION,
                },
                {
                    "code": "no_semantic_completeness",
                    "message": "The projection makes no semantic-completeness claim.",
                    "operation": CONTEXT_COMPILE_OPERATION,
                },
                {
                    "code": "private_certificate_not_projected",
                    "message": (
                        "The private CtxC artifact and detector-scoped retention "
                        "certificate remain outside the canonical projection."
                    ),
                    "operation": CONTEXT_COMPILE_OPERATION,
                },
            ],
        )
        manifest.validate()
        return manifest

    def handle(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Execute an already-negotiated canonical operation.

        Callers should use ``ConnectorServer``, :meth:`handle_request`, or
        :meth:`serve_ndjson`; those endpoints enforce handshake-first state.
        """

        if operation != CONTEXT_COMPILE_OPERATION:
            raise self._contracts.UnsupportedOperationError(
                "the canonical operation is unsupported"
            )
        bounded = self._bounded_json(payload, label="context.compile payload")
        if not isinstance(bounded, dict) or set(bounded) != {"source_events"}:
            raise ValueError(
                "context.compile payload must contain only source_events"
            )
        documents = bounded["source_events"]
        if (
            not isinstance(documents, list)
            or not documents
            or len(documents) > MAX_CONTEXT_SOURCE_EVENTS
        ):
            raise ValueError(
                f"context.compile requires 1 to {MAX_CONTEXT_SOURCE_EVENTS} "
                "source events"
            )

        events, private_events, authenticated = self._map_source_events(documents)
        payload_digest = _digest(
            self._contracts.bounded_canonical_bytes(bounded, limits=self._limits)
        )
        connector = self._connector_factory()
        if not isinstance(connector, LocalAIConnector):
            raise TypeError("connector_factory must return LocalAIConnector")
        rich_bundle = connector.compile_memory(
            session_id=f"localai-contracts-{payload_digest}",
            events=private_events,
        )
        replay = connector.verify_memory(rich_bundle, events=private_events)
        if replay.get("passed") is not True:
            raise ValueError("private CtxC replay verification failed")

        projected = self._project_bundle(
            rich_bundle=rich_bundle,
            events=events,
            authenticated=authenticated,
            connector=connector,
        )
        encoded = self._contracts.bounded_canonical_bytes(
            projected.to_dict(),
            limits=self._limits,
        )
        parsed = self._contracts.parse_json(encoded, limits=self._limits)
        checked = self._contracts.ContextBundle.from_dict(parsed)
        return checked.to_dict()

    def handle_request(self, request: Any) -> Any:
        """Accept an actual ``ConnectorRequest`` and return ``ConnectorResponse``."""

        if type(request) is not self._contracts.ConnectorRequest:
            raise TypeError(
                "request must be an actual localai_contracts.ConnectorRequest"
            )
        with self._request_lock:
            response = self._request_server.handle(request)
        if type(response) is not self._contracts.ConnectorResponse:
            raise TypeError("connector server returned an invalid response type")
        return response

    def serve_ndjson(self, reader: Any, writer: Any) -> int:
        """Serve bounded canonical NDJSON until EOF and return records consumed."""

        server = self._contracts.NdjsonConnectorServer(
            self,
            limits=self._limits,
        )
        consumed = 0
        while server.serve_once(reader, writer):
            consumed += 1
        return consumed

    def build_phase0_probe(self, source_events: Sequence[Any]) -> Any:
        """Build the mandatory typed non-inference probe for the wheel runner."""

        if (
            not isinstance(source_events, Sequence)
            or isinstance(source_events, (str, bytes, bytearray))
            or not source_events
            or len(source_events) > MAX_CONTEXT_SOURCE_EVENTS
        ):
            raise ValueError(
                f"probe requires 1 to {MAX_CONTEXT_SOURCE_EVENTS} SourceEvents"
            )
        documents: list[dict[str, Any]] = []
        for event in source_events:
            if type(event) is not self._contracts.SourceEvent:
                raise TypeError(
                    "probe events must be actual localai_contracts.SourceEvent values"
                )
            event.validate()
            documents.append(event.to_dict())
        payload = {"source_events": documents}
        expected = self._contracts.ContextBundle.from_dict(
            self.handle(CONTEXT_COMPILE_OPERATION, payload)
        )
        return self._contracts.SubjectOperationProbe(
            purpose=self._contracts.SubjectOperationPurpose.CONTEXT_BUNDLE_COMPILE,
            operation=CONTEXT_COMPILE_OPERATION,
            payload=payload,
            expected_response=expected,
        )

    def _bounded_json(self, value: Any, *, label: str) -> Any:
        try:
            encoded = self._contracts.bounded_canonical_bytes(value, limits=self._limits)
            return self._contracts.parse_json(encoded, limits=self._limits)
        except self._contracts.LocalAIContractsError:
            raise
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} is not bounded canonical JSON") from exc

    def _map_source_events(
        self,
        documents: list[Any],
    ) -> tuple[list[Any], list[PrivateSourceEvent], dict[str, bool]]:
        contracts = self._contracts
        parsed_events: list[Any] = []
        private_events: list[PrivateSourceEvent] = []
        authenticated: dict[str, bool] = {}
        seen_ids: set[str] = set()
        seen_sequences: set[int] = set()

        for document in documents:
            if not isinstance(document, dict):
                raise TypeError("source_events entries must be objects")
            event = contracts.SourceEvent.from_dict(document)
            event.validate()
            if event.to_dict() != document:
                raise ValueError("SourceEvent normalization changed the document")
            if event.source_event_id in seen_ids:
                raise ValueError("source_events contains a duplicate id")
            if event.sequence in seen_sequences:
                raise ValueError("source_events contains a duplicate sequence")
            seen_ids.add(event.source_event_id)
            seen_sequences.add(event.sequence)

            authority: AuthenticatedAuthority | None = None
            if event.trust == "trusted" and self._authority_verifier is not None:
                decision_event = contracts.SourceEvent.from_dict(event.to_dict())
                decision = self._authority_verifier(decision_event)
                if decision is not None and type(decision) is not AuthenticatedAuthority:
                    raise TypeError(
                        "authority_verifier must return AuthenticatedAuthority or None"
                    )
                authority = decision
                if (
                    authority is not None
                    and authority.trusted_for_state
                    and event.role != "tool"
                ):
                    raise ValueError(
                        "trusted_for_state is restricted to authenticated tool events"
                    )

            is_authenticated = authority is not None
            authenticated[event.source_event_id] = is_authenticated
            canonical_document = event.to_dict()
            metadata = {
                "localai_contracts_event": {
                    "schema_version": event.schema_version,
                    "original_role": event.role,
                    "declared_trust": event.trust,
                    "metadata": canonical_document["metadata"],
                    "content_hash": canonical_document["content_hash"],
                    "independently_authenticated": is_authenticated,
                    "authority_issuer": (
                        authority.issuer if authority is not None else None
                    ),
                }
            }
            private_authority = (
                {
                    "authenticated": True,
                    "trusted_for_state": authority.trusted_for_state,
                    "issuer": authority.issuer,
                }
                if authority is not None
                else {"authenticated": False, "trusted_for_state": False}
            )
            private_role = event.role if is_authenticated else "assistant"
            content_digest = event.content_hash
            private_events.append(
                PrivateSourceEvent(
                    id=event.source_event_id,
                    sequence=event.sequence,
                    role=private_role,
                    content=event.content,
                    metadata=metadata,
                    authority=private_authority,
                    provenance={
                        "producer": "localai-contracts-adapter",
                        "schema_version": event.schema_version,
                        "source_event_id": event.source_event_id,
                        "original_role": event.role,
                        "declared_trust": event.trust,
                    },
                    content_sha256=(
                        content_digest["value"]
                        if content_digest["algorithm"] == "sha256"
                        else ""
                    ),
                )
            )
            parsed_events.append(event)

        return parsed_events, private_events, authenticated

    def _project_bundle(
        self,
        *,
        rich_bundle: Any,
        events: list[Any],
        authenticated: dict[str, bool],
        connector: LocalAIConnector,
    ) -> Any:
        events_by_id = {event.source_event_id: event for event in events}
        trusted_spans: list[dict[str, Any]] = []
        retrieved_spans: list[dict[str, Any]] = []
        provenance: list[dict[str, Any]] = []

        for event in sorted(
            events,
            key=lambda item: (item.sequence, item.source_event_id),
        ):
            content_hash = _sha256_text(event.content)
            span_id = _stable_id(
                "source",
                event.source_event_id,
                event.sequence,
                content_hash,
            )
            span = {
                "span_id": span_id,
                "source_event_id": event.source_event_id,
                "start_byte": 0,
                "end_byte": len(event.content.encode("utf-8")),
                "content": event.content,
                "content_hash": _digest_record(content_hash),
                "token_count": None,
            }
            retrieved_spans.append(span)
            provenance.append(
                {
                    "item_id": span_id,
                    "source_event_id": event.source_event_id,
                    "transform": "exact_source_event_projection",
                    "producer": {
                        "name": "loss-resistant-context-compiler",
                        "version": _component_version(),
                    },
                }
            )

        rich_spans = rich_bundle.trusted_memory.get("source_spans")
        if not isinstance(rich_spans, list):
            raise TypeError("private trusted-memory source spans are invalid")
        for raw in sorted(
            rich_spans,
            key=lambda item: (
                item.get("source_id", ""),
                item.get("start", -1),
                item.get("end", -1),
                item.get("quote_sha256", ""),
            ),
        ):
            if not isinstance(raw, dict):
                raise TypeError("private trusted-memory span must be an object")
            source_id = raw.get("source_id")
            source = events_by_id.get(source_id)
            if source is None:
                raise ValueError("private span references an unknown source event")
            start = raw.get("start")
            end = raw.get("end")
            quote = raw.get("quote")
            if (
                isinstance(start, bool)
                or not isinstance(start, int)
                or isinstance(end, bool)
                or not isinstance(end, int)
                or start < 0
                or end < start
                or end > len(source.content)
                or not isinstance(quote, str)
            ):
                raise ValueError("private span has invalid character offsets")
            if source.content[start:end] != quote:
                raise ValueError("private span does not match exact source content")
            quote_hash = _sha256_text(quote)
            if raw.get("quote_sha256") != quote_hash:
                raise ValueError("private span quote digest mismatch")
            start_byte = len(source.content[:start].encode("utf-8"))
            end_byte = len(source.content[:end].encode("utf-8"))
            span_id = _stable_id(
                "active",
                source_id,
                start_byte,
                end_byte,
                quote_hash,
            )
            span = {
                "span_id": span_id,
                "source_event_id": source_id,
                "start_byte": start_byte,
                "end_byte": end_byte,
                "content": quote,
                "content_hash": _digest_record(quote_hash),
                "token_count": None,
            }
            if authenticated.get(source_id) is True:
                trusted_spans.append(span)
            else:
                retrieved_spans.append(span)
            provenance.append(
                {
                    "item_id": span_id,
                    "source_event_id": source_id,
                    "transform": "verified_active_exact_source_span_projection",
                    "producer": {
                        "name": "loss-resistant-context-compiler",
                        "version": _component_version(),
                    },
                }
            )

        if len(trusted_spans) + len(retrieved_spans) > MAX_CONTEXT_SPANS:
            raise ValueError("canonical ContextBundle exceeds the span count bound")

        exact_counter = connector.token_counter
        if exact_counter is None:
            counter = _estimated_tokens
            accounting_kind = "estimated"
            accounting_method = (
                "ctxc_projected_span_contents_estimated_"
                "ceil_unicode_characters_divided_by_4;framing_excluded"
            )
        else:
            counter = exact_counter
            accounting_kind = "exact"
            identity = connector.token_counter_id
            if not isinstance(identity, str) or not identity:
                raise ValueError("exact token counter lacks a stable identity")
            accounting_method = (
                "ctxc_projected_span_contents_exact_"
                f"tokenizer_identity_sha256={_sha256_text(identity)};"
                "framing_excluded"
            )

        for span in trusted_spans + retrieved_spans:
            span["token_count"] = counter(span["content"])
        trusted_tokens = sum(span["token_count"] for span in trusted_spans)
        retrieved_tokens = sum(span["token_count"] for span in retrieved_spans)

        omissions, overflow = self._project_omissions_and_overflow(
            rich_bundle,
            exact_accounting=exact_counter is not None,
        )
        policy_digest = rich_bundle.bindings.get("compiler_policy_sha256")
        if not isinstance(policy_digest, str) or len(policy_digest) != 64:
            raise ValueError("private compiler policy identity is invalid")
        private_source_digest = rich_bundle.bindings.get("source_digest")
        if not isinstance(private_source_digest, str) or len(private_source_digest) != 64:
            raise ValueError("private source-set identity is invalid")
        canonical_source_documents = [
            event.to_dict()
            for event in sorted(
                events,
                key=lambda item: (item.sequence, item.source_event_id),
            )
        ]
        source_bytes = self._contracts.bounded_canonical_bytes(
            canonical_source_documents,
            limits=self._limits,
        )
        source_digest = _digest(source_bytes)

        body = {
            "trusted_active_memory": trusted_spans,
            "untrusted_retrieved_spans": retrieved_spans,
            "provenance": sorted(
                provenance,
                key=lambda item: (
                    item["source_event_id"],
                    item["item_id"],
                    item["transform"],
                ),
            ),
            "token_accounting": {
                "kind": accounting_kind,
                "method": accounting_method,
                "trusted_tokens": trusted_tokens,
                "retrieved_tokens": retrieved_tokens,
                "total_tokens": trusted_tokens + retrieved_tokens,
            },
            "omissions": omissions,
            "overflow": overflow,
            "policy_identity": {
                "name": "ctxc-private-compilation-policy",
                "version": "localai-context-bundle-0.1",
                "digest": _digest_record(policy_digest),
            },
            "source_store_identity": {
                "name": "localai-contracts-exact-source-events",
                "version": LOCALAI_CONTRACTS_PROTOCOL_VERSION,
                "digest": _digest_record(source_digest),
            },
        }
        body_bytes = self._contracts.bounded_canonical_bytes(
            body,
            limits=self._limits,
        )
        bundle_digest = _digest(
            _PROJECTION_VERSION.encode("utf-8")
            + b"\x00"
            + body_bytes
        )
        projected = self._contracts.ContextBundle(
            bundle_id=f"ctxc-context-{bundle_digest}",
            **body,
        )
        projected.validate()
        return projected

    @staticmethod
    def _project_omissions_and_overflow(
        rich_bundle: Any,
        *,
        exact_accounting: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        raw_entries = rich_bundle.trusted_memory.get(
            "omitted_or_overflowed_protected_items"
        )
        if not isinstance(raw_entries, list):
            raise TypeError("private protected omission ledger is invalid")
        omissions_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        overflow_values: set[int] = set()
        for entry in raw_entries:
            if not isinstance(entry, dict):
                raise TypeError("private protected omission entry is invalid")
            reason = entry.get("reason")
            item = entry.get("item")
            if not isinstance(item, dict):
                raise TypeError("private omitted item is invalid")
            provenance = item.get("provenance")
            if not isinstance(provenance, list) or not provenance:
                raise ValueError("private omitted item lacks exact provenance")
            source_ids = sorted(
                {
                    span.get("source_id")
                    for span in provenance
                    if isinstance(span, dict)
                    and isinstance(span.get("source_id"), str)
                    and span.get("source_id")
                }
            )
            if not source_ids:
                raise ValueError("private omitted item has no valid source event")
            if reason == "omitted":
                for source_id in source_ids:
                    key = (source_id, "private_protected_item_not_selected")
                    omissions_by_key[key] = {
                        "source_event_id": source_id,
                        "reason": key[1],
                        "estimated_tokens": None,
                    }
            elif reason == "protected_budget_overflow":
                value = entry.get("overflow_tokens")
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise ValueError("private protected overflow is invalid")
                overflow_values.add(value)
            else:
                raise ValueError("private protected omission reason is unsupported")

        if len(overflow_values) > 1:
            raise ValueError("private protected overflow totals disagree")
        if overflow_values:
            amount = next(iter(overflow_values))
            overflow = {
                "occurred": True,
                "dropped_items": 0,
                "dropped_tokens": amount if exact_accounting else None,
                "reason": "private_protected_budget_overflow_retained",
            }
        else:
            overflow = {
                "occurred": False,
                "dropped_items": 0,
                "dropped_tokens": 0,
                "reason": None,
            }
        omissions = [
            omissions_by_key[key] for key in sorted(omissions_by_key)
        ]
        return omissions, overflow


def _estimated_tokens(text: str) -> int:
    if not isinstance(text, str):
        raise TypeError("projected span content must be a string")
    return 0 if not text else max(1, (len(text) + 3) // 4)


__all__ = [
    "AuthenticatedAuthority",
    "AuthorityVerifier",
    "CONTEXT_COMPILE_OPERATION",
    "LOCALAI_CONTRACTS_DISTRIBUTION",
    "LOCALAI_CONTRACTS_PROTOCOL_VERSION",
    "LOCALAI_CONTRACTS_SOURCE_COMMIT",
    "LOCALAI_CONTRACTS_VERSION",
    "LOCALAI_CONTRACTS_WHEEL_SHA256",
    "LocalAIContractsAdapter",
    "LocalAIContractsUnavailableError",
    "MAX_CONTEXT_SOURCE_EVENTS",
]
