"""Exact-version OpenHands import gate and fail-closed conversation proxy."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import stat
import sys
import threading
import weakref
from collections.abc import Mapping
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any

from .callback import DurableEventCallback
from .compatibility import (
    OPENHANDS_HOST_DISTRIBUTION,
    OPENHANDS_HOST_VERSION,
    OPENHANDS_PIN_MANIFEST_MAX_BYTES,
    OPENHANDS_SDK_DISTRIBUTION,
    OPENHANDS_SDK_VERSION,
    OPENHANDS_TOOLS_DISTRIBUTION,
    OPENHANDS_TOOLS_VERSION,
    SUPPORTED_PYTHON,
    verify_pin_manifest,
)
from .session import OpenHandsSession

AGENT_SERVER_DISTRIBUTION = "openhands-agent-server"
AGENT_SERVER_VERSION = OPENHANDS_SDK_VERSION
MAX_REVIEWED_SOURCE_BYTES = 4 * 1024 * 1024

PINNED_EVENT_CLASS_NAMES = (
    "ACPToolCallEvent",
    "ActionEvent",
    "AgentErrorEvent",
    "Condensation",
    "CondensationRequest",
    "CondensationSummaryEvent",
    "ConversationErrorEvent",
    "ConversationStateUpdateEvent",
    "HookExecutionEvent",
    "InterruptEvent",
    "LLMCompletionLogEvent",
    "MessageEvent",
    "ObservationEvent",
    "PauseEvent",
    "StreamingDeltaEvent",
    "SystemPromptEvent",
    "TokenEvent",
    "UserRejectObservation",
)

_VERIFIED_HOST_API_MARKER = object()
_PINNED_CALLBACK_APIS: weakref.WeakKeyDictionary[
    DurableEventCallback,
    object,
] = weakref.WeakKeyDictionary()
_PINNED_CALLBACK_APIS_LOCK = threading.Lock()


class LiveCompatibilityError(RuntimeError):
    """The installed host does not match every exact reviewed binding."""


class HostBypassRefusedError(RuntimeError):
    """An unrecorded/bypass host API was refused in integrated mode."""


class LiveRequestAccountingUnavailableError(RuntimeError):
    """A live request lacks an exact immutable accounting gate."""


@dataclass(frozen=True, slots=True)
class DistributionStatus:
    distribution: str
    expected_version: str
    installed_version: str | None
    exact: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "distribution": self.distribution,
            "expected_version": self.expected_version,
            "installed_version": self.installed_version,
            "exact": self.exact,
        }


@dataclass(frozen=True, slots=True)
class LiveCompatibilityReport:
    passed: bool
    manifest_file_sha256: str
    python_supported: bool
    distributions: tuple[DistributionStatus, ...]
    reviewed_file_count: int
    reviewed_files_verified: int
    issues: tuple[str, ...]
    live_execution_claimed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "manifest_file_sha256": self.manifest_file_sha256,
            "python_supported": self.python_supported,
            "distributions": [item.to_dict() for item in self.distributions],
            "reviewed_file_count": self.reviewed_file_count,
            "reviewed_files_verified": self.reviewed_files_verified,
            "issues": list(self.issues),
            "live_execution_claimed": self.live_execution_claimed,
        }


@dataclass(frozen=True, slots=True)
class PinnedHostAPI:
    """Private verified host capability minted after exact compatibility checks."""

    event_types: Mapping[str, type]
    local_conversation_type: type
    llm_type: type
    _marker_input: InitVar[object | None] = None
    _verified_marker: object = field(init=False, repr=False, compare=False)
    _identity: object = field(init=False, repr=False, compare=False)

    def __post_init__(self, _marker_input: object | None) -> None:
        if _marker_input is not _VERIFIED_HOST_API_MARKER:
            raise TypeError("PinnedHostAPI can only be minted after verified host compatibility")
        if not isinstance(self.event_types, Mapping):
            raise TypeError("event_types must be a mapping")
        try:
            copied_event_types = dict(self.event_types)
        except (TypeError, ValueError, RuntimeError) as exc:
            raise TypeError("event_types could not be copied safely") from exc
        expected_names = frozenset(PINNED_EVENT_CLASS_NAMES)
        if frozenset(copied_event_types) != expected_names:
            raise TypeError("event_types must contain exactly the supported pinned event inventory")
        for name in PINNED_EVENT_CLASS_NAMES:
            event_type = copied_event_types[name]
            if not isinstance(event_type, type) or event_type.__name__ != name:
                raise TypeError(f"event_types[{name!r}] is not the exact-named event class")
        if not isinstance(self.local_conversation_type, type):
            raise TypeError("local_conversation_type must be a type")
        if not isinstance(self.llm_type, type):
            raise TypeError("llm_type must be a type")
        object.__setattr__(
            self,
            "event_types",
            MappingProxyType(copied_event_types),
        )
        object.__setattr__(self, "_verified_marker", _VERIFIED_HOST_API_MARKER)
        object.__setattr__(self, "_identity", object())

    def validate_event_type(self, event: Any) -> None:
        kind = type(event).__name__
        expected = self.event_types.get(kind)
        if expected is None:
            raise TypeError(f"unsupported pinned OpenHands event class: {kind!r}")
        if type(event) is not expected:
            raise TypeError(f"event kind {kind!r} is not the exact pinned SDK class identity")


def _mint_pinned_host_api(
    *,
    event_types: Mapping[str, type],
    local_conversation_type: type,
    llm_type: type,
) -> PinnedHostAPI:
    return PinnedHostAPI(
        event_types=event_types,
        local_conversation_type=local_conversation_type,
        llm_type=llm_type,
        _marker_input=_VERIFIED_HOST_API_MARKER,
    )


def _require_verified_host_api(api: Any) -> PinnedHostAPI:
    if (
        not isinstance(api, PinnedHostAPI)
        or getattr(api, "_verified_marker", None) is not _VERIFIED_HOST_API_MARKER
    ):
        raise TypeError("api must be a verified PinnedHostAPI returned by load_pinned_host_api")
    return api


def _strict_manifest_value(path: Path, *, expected_sha256: str) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(OPENHANDS_PIN_MANIFEST_MAX_BYTES + 1)
    except OSError as exc:
        raise LiveCompatibilityError(f"cannot re-read verified pin manifest: {exc}") from exc
    if len(raw) > OPENHANDS_PIN_MANIFEST_MAX_BYTES:
        raise LiveCompatibilityError("verified pin manifest changed to an oversized file")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise LiveCompatibilityError("verified pin manifest changed during live inspection")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise LiveCompatibilityError("verified pin manifest changed to invalid JSON") from exc
    if not isinstance(value, dict):
        raise LiveCompatibilityError("verified pin manifest is not an object")
    return value


def _distribution_status(
    distribution: str,
    expected_version: str,
) -> tuple[DistributionStatus, importlib.metadata.Distribution | None]:
    try:
        installed = importlib.metadata.distribution(distribution)
    except importlib.metadata.PackageNotFoundError:
        return (
            DistributionStatus(
                distribution=distribution,
                expected_version=expected_version,
                installed_version=None,
                exact=False,
            ),
            None,
        )
    version = installed.version
    return (
        DistributionStatus(
            distribution=distribution,
            expected_version=expected_version,
            installed_version=version,
            exact=version == expected_version,
        ),
        installed,
    )


def _reviewed_source_location(
    distribution: importlib.metadata.Distribution,
    manifest_path: str,
) -> tuple[Path, Path]:
    prefix = "openhands-sdk/"
    if not manifest_path.startswith(prefix):
        raise LiveCompatibilityError(
            f"reviewed source path has unsupported distribution prefix: {manifest_path}"
        )
    relative = manifest_path.removeprefix(prefix)
    candidate = Path(os.path.abspath(os.fspath(distribution.locate_file(relative))))
    root = Path(os.path.abspath(os.fspath(distribution.locate_file(""))))
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise LiveCompatibilityError(
            f"reviewed source escapes installed distribution: {manifest_path}"
        ) from exc
    return root, candidate


def _reviewed_file_path(
    distribution: importlib.metadata.Distribution,
    manifest_path: str,
) -> Path:
    return _reviewed_source_location(distribution, manifest_path)[1]


@dataclass(frozen=True, slots=True)
class _PathIdentity:
    device: int
    inode: int
    file_type: int
    size: int | None


def _is_symbolic_or_reparse(info: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _path_identity(info: os.stat_result, *, include_size: bool) -> _PathIdentity:
    return _PathIdentity(
        device=info.st_dev,
        inode=info.st_ino,
        file_type=stat.S_IFMT(info.st_mode),
        size=info.st_size if include_size else None,
    )


def _path_components(root: Path, path: Path) -> tuple[Path, ...]:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise LiveCompatibilityError("reviewed source escaped its distribution root") from exc
    current = root
    components = [root]
    for part in relative.parts:
        current /= part
        components.append(current)
    return tuple(components)


def _snapshot_reviewed_source(
    root: Path,
    path: Path,
    *,
    manifest_path: str,
) -> tuple[_PathIdentity, ...]:
    identities: list[_PathIdentity] = []
    components = _path_components(root, path)
    for index, component in enumerate(components):
        try:
            info = os.lstat(component)
        except OSError as exc:
            raise LiveCompatibilityError(
                f"cannot inspect reviewed source path component for {manifest_path}: {exc}"
            ) from exc
        if _is_symbolic_or_reparse(info):
            raise LiveCompatibilityError(
                f"reviewed source path is symbolic or a reparse point: {manifest_path}"
            )
        terminal = index == len(components) - 1
        if terminal:
            if not stat.S_ISREG(info.st_mode):
                raise LiveCompatibilityError(
                    f"reviewed source is not a regular file: {manifest_path}"
                )
        elif not stat.S_ISDIR(info.st_mode):
            raise LiveCompatibilityError(
                f"reviewed source parent is not a directory: {manifest_path}"
            )
        identities.append(_path_identity(info, include_size=terminal))
    return tuple(identities)


def _read_reviewed_source(
    distribution: importlib.metadata.Distribution,
    manifest_path: str,
) -> bytes:
    root, source = _reviewed_source_location(distribution, manifest_path)
    before = _snapshot_reviewed_source(root, source, manifest_path=manifest_path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise LiveCompatibilityError(
            f"cannot open reviewed source safely for {manifest_path}: {exc}"
        ) from exc
    try:
        opened_before = os.fstat(descriptor)
        if not stat.S_ISREG(opened_before.st_mode):
            raise LiveCompatibilityError(
                f"opened reviewed source is not a regular file: {manifest_path}"
            )
        if _path_identity(opened_before, include_size=True) != before[-1]:
            raise LiveCompatibilityError(
                f"reviewed source changed before descriptor open: {manifest_path}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_REVIEWED_SOURCE_BYTES + 1)
        opened_after = os.fstat(descriptor)
        after = _snapshot_reviewed_source(root, source, manifest_path=manifest_path)
    except OSError as exc:
        raise LiveCompatibilityError(
            f"cannot read reviewed source safely for {manifest_path}: {exc}"
        ) from exc
    finally:
        os.close(descriptor)
    if len(raw) > MAX_REVIEWED_SOURCE_BYTES:
        raise LiveCompatibilityError(f"reviewed source exceeds byte limit: {manifest_path}")
    if _path_identity(opened_after, include_size=True) != before[-1]:
        raise LiveCompatibilityError(f"reviewed source changed while hashing: {manifest_path}")
    if len(raw) != opened_after.st_size:
        raise LiveCompatibilityError(f"reviewed source size changed while hashing: {manifest_path}")
    if after != before:
        raise LiveCompatibilityError(f"reviewed source path changed while hashing: {manifest_path}")
    return raw


def inspect_live_compatibility() -> LiveCompatibilityReport:
    """Inspect exact installed bytes without importing OpenHands."""

    verified = verify_pin_manifest()
    manifest = _strict_manifest_value(
        verified.path,
        expected_sha256=verified.file_sha256,
    )
    python_supported = sys.version_info[:2] in SUPPORTED_PYTHON
    issues: list[str] = [
        "retained compatibility blocker: "
        f"{verified.blocker_code} (real_offline_import={verified.real_offline_import}, "
        f"live_scenario={verified.live_scenario})"
    ]
    if not python_supported:
        issues.append(f"Python runtime is outside the supported exact range {SUPPORTED_PYTHON!r}")

    checks: list[DistributionStatus] = []
    installed: dict[str, importlib.metadata.Distribution] = {}
    for name, version in (
        (OPENHANDS_HOST_DISTRIBUTION, OPENHANDS_HOST_VERSION),
        (OPENHANDS_SDK_DISTRIBUTION, OPENHANDS_SDK_VERSION),
        (OPENHANDS_TOOLS_DISTRIBUTION, OPENHANDS_TOOLS_VERSION),
        (AGENT_SERVER_DISTRIBUTION, AGENT_SERVER_VERSION),
    ):
        status, distribution = _distribution_status(name, version)
        checks.append(status)
        if distribution is None:
            issues.append(f"{name}=={version} is not installed")
        elif not status.exact:
            issues.append(
                f"{name} version mismatch: expected {version}, found {status.installed_version}"
            )
        else:
            installed[name] = distribution

    reviewed = manifest["reviewed_api"]["files"]
    verified_files = 0
    sdk = installed.get(OPENHANDS_SDK_DISTRIBUTION)
    if sdk is not None:
        for record in reviewed:
            manifest_path = record["path"]
            expected_sha = record["sha256"]
            try:
                raw = _read_reviewed_source(sdk, manifest_path)
                actual = hashlib.sha256(raw).hexdigest()
                if actual != expected_sha:
                    raise LiveCompatibilityError(
                        f"reviewed source digest mismatch: {manifest_path}"
                    )
            except (OSError, LiveCompatibilityError) as exc:
                issues.append(str(exc))
            else:
                verified_files += 1

    return LiveCompatibilityReport(
        passed=(
            python_supported
            and all(check.exact for check in checks)
            and verified_files == len(reviewed)
            and not issues
        ),
        manifest_file_sha256=verified.file_sha256,
        python_supported=python_supported,
        distributions=tuple(checks),
        reviewed_file_count=len(reviewed),
        reviewed_files_verified=verified_files,
        issues=tuple(issues),
    )


def require_live_compatibility() -> LiveCompatibilityReport:
    report = inspect_live_compatibility()
    if not report.passed:
        raise LiveCompatibilityError(
            "OpenHands live compatibility failed closed: " + "; ".join(report.issues)
        )
    return report


def _require_class(module: ModuleType, name: str) -> type:
    value = getattr(module, name, None)
    if not isinstance(value, type) or value.__name__ != name:
        raise LiveCompatibilityError(f"pinned SDK class is unavailable: {name}")
    return value


def load_pinned_host_api() -> PinnedHostAPI:
    """Import exact host classes only after all version/source checks pass."""

    require_live_compatibility()
    event_module = importlib.import_module("openhands.sdk.event")
    local_module = importlib.import_module("openhands.sdk.conversation.impl.local_conversation")
    llm_module = importlib.import_module("openhands.sdk.llm.llm")
    event_types = {name: _require_class(event_module, name) for name in PINNED_EVENT_CLASS_NAMES}
    return _mint_pinned_host_api(
        event_types=event_types,
        local_conversation_type=_require_class(local_module, "LocalConversation"),
        llm_type=_require_class(llm_module, "LLM"),
    )


def pinned_callback(
    session: OpenHandsSession,
    api: PinnedHostAPI,
) -> DurableEventCallback:
    """Create the exact-class callback to pass in the host constructor."""

    verified_api = _require_verified_host_api(api)
    callback = DurableEventCallback(
        session,
        event_validator=verified_api.validate_event_type,
    )
    with _PINNED_CALLBACK_APIS_LOCK:
        _PINNED_CALLBACK_APIS[callback] = verified_api._identity
    return callback


class GuardedLocalConversation:
    """Narrow proxy for the exact LocalConversation request-driving API."""

    def __init__(
        self,
        conversation: Any,
        *,
        api: PinnedHostAPI,
        callback: DurableEventCallback,
    ) -> None:
        verified_api = _require_verified_host_api(api)
        if type(conversation) is not verified_api.local_conversation_type:
            raise TypeError("conversation is not the exact pinned LocalConversation class")
        if not isinstance(callback, DurableEventCallback):
            raise TypeError("callback must be a DurableEventCallback")
        with _PINNED_CALLBACK_APIS_LOCK:
            callback_api_identity = _PINNED_CALLBACK_APIS.get(callback)
        if callback_api_identity is not verified_api._identity:
            raise TypeError("callback must be minted by pinned_callback for this verified host API")
        self._conversation = conversation
        self._callback = callback

    @property
    def state(self) -> Any:
        return self._conversation.state

    def send_message(self, message: Any, sender: str | None = None) -> None:
        self._callback.assert_dispatch_allowed()
        self._conversation.send_message(message, sender=sender)

    def run(self) -> None:
        self._callback.assert_dispatch_allowed()
        raise LiveRequestAccountingUnavailableError(
            "live run refused: this package has no supported exact immutable "
            "final-request tokenizer/wire adapter"
        )

    async def arun(self) -> None:
        self._callback.assert_dispatch_allowed()
        raise LiveRequestAccountingUnavailableError(
            "live run refused: this package has no supported exact immutable "
            "final-request tokenizer/wire adapter"
        )

    def ask_agent(self, _question: str) -> str:
        raise HostBypassRefusedError(
            "LocalConversation.ask_agent is stateless and unrecorded; integrated mode refuses it"
        )

    def execute_tool(self, _tool_name: str, _action: Any) -> Any:
        raise HostBypassRefusedError(
            "LocalConversation.execute_tool bypasses normal events, confirmation, "
            "and security analysis; integrated mode refuses it"
        )


__all__ = [
    "AGENT_SERVER_DISTRIBUTION",
    "AGENT_SERVER_VERSION",
    "DistributionStatus",
    "GuardedLocalConversation",
    "HostBypassRefusedError",
    "LiveCompatibilityError",
    "LiveCompatibilityReport",
    "LiveRequestAccountingUnavailableError",
    "PINNED_EVENT_CLASS_NAMES",
    "inspect_live_compatibility",
    "load_pinned_host_api",
    "pinned_callback",
    "require_live_compatibility",
]
