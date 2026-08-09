"""Strict, dependency-free mapping of pinned OpenHands SDK events.

The module intentionally does not import OpenHands.  It accepts the exact JSON
shape emitted by the pinned SDK (or an object exposing ``model_dump``), checks
every supported event kind explicitly, and maps it into the public CtxC
``SourceEvent`` API.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from context_compiler import SourceEvent

from .authority import (
    AuthorityError,
    AuthorityPolicy,
    AuthorityVerificationError,
    VerifiedAuthority,
    canonical_json_bytes,
    object_as_mapping,
    verify_authority_receipt,
)

OPENHANDS_HOST_VERSION = "1.8.0"
OPENHANDS_HOST_REVISION = "bc26df351dd5d833a95131556dbe2da69af82253"
OPENHANDS_SDK_VERSION = "1.27.0"
OPENHANDS_SDK_REVISION = "904279edf2df5fa12d7caecc7576f62659b2e2dd"

ATOMIC_GROUP_SCHEMA = "ctxc.openhands.atomic-group.v1"
EVENT_METADATA_SCHEMA = "ctxc.openhands.event-metadata.v1"

_EVENT_SOURCES = frozenset({"agent", "user", "environment", "hook"})
_HOOK_EVENT_TYPES = frozenset(
    {
        "PreToolUse",
        "PostToolUse",
        "UserPromptSubmit",
        "SessionStart",
        "SessionEnd",
        "Stop",
    }
)
_MAX_EVENT_ARRAY_ITEMS = 10_000
_MAX_TOKEN_IDS = 10_000
_COMMON_FIELDS = frozenset({"kind", "id", "timestamp", "source"})
_MESSAGE_FIELDS = frozenset(
    {
        "role",
        "content",
        "tool_calls",
        "tool_call_id",
        "name",
        "reasoning_content",
        "thinking_blocks",
        "responses_reasoning_item",
    }
)
_TOOL_CALL_FIELDS = frozenset({"id", "responses_item_id", "name", "arguments", "origin"})

_FILE_TOOLS = frozenset({"apply_patch", "file_editor", "str_replace_editor"})
_SEARCH_TOOLS = frozenset({"glob", "grep", "web_search"})
_PINNED_BROWSER_TOOLS = frozenset(
    {
        "browser_click",
        "browser_close_tab",
        "browser_get_content",
        "browser_get_state",
        "browser_get_storage",
        "browser_go_back",
        "browser_list_tabs",
        "browser_navigate",
        "browser_scroll",
        "browser_set_storage",
        "browser_start_recording",
        "browser_stop_recording",
        "browser_switch_tab",
        "browser_type",
    }
)
_RETRIEVAL_TOOLS = (
    frozenset({"browser", "retrieval", "retrieve", "web_fetch"}) | _PINNED_BROWSER_TOOLS
)
_DELEGATED_TOOLS = frozenset({"delegate", "delegated_agent", "subagent", "task"})
_GENERIC_TOOLS = frozenset(
    {
        "bash",
        "finish",
        "python",
        "shell",
        "terminal",
        "think",
    }
)
KNOWN_TOOL_NAMES = frozenset(
    _FILE_TOOLS | _SEARCH_TOOLS | _RETRIEVAL_TOOLS | _DELEGATED_TOOLS | _GENERIC_TOOLS
)

_ACTION_KIND_CATEGORIES = {
    "ApplyPatchAction": "file",
    "BrowserClickAction": "retrieval",
    "BrowserCloseTabAction": "retrieval",
    "BrowserGetContentAction": "retrieval",
    "BrowserGetStateAction": "retrieval",
    "BrowserGetStorageAction": "retrieval",
    "BrowserGoBackAction": "retrieval",
    "BrowserListTabsAction": "retrieval",
    "BrowserNavigateAction": "retrieval",
    "BrowserScrollAction": "retrieval",
    "BrowserSetStorageAction": "retrieval",
    "BrowserStartRecordingAction": "retrieval",
    "BrowserStopRecordingAction": "retrieval",
    "BrowserSwitchTabAction": "retrieval",
    "BrowserTypeAction": "retrieval",
    "DelegateAction": "delegated",
    "FileEditorAction": "file",
    "GlobAction": "search",
    "GrepAction": "search",
    "TaskAction": "delegated",
    "TerminalAction": "generic",
}
_OBSERVATION_KIND_CATEGORIES = {
    "ApplyPatchObservation": "file",
    "BrowserObservation": "retrieval",
    "DelegateObservation": "delegated",
    "FileEditorObservation": "file",
    "GlobObservation": "search",
    "GrepObservation": "search",
    "TaskObservation": "delegated",
    "TerminalObservation": "generic",
}


class EventMappingError(ValueError):
    """A host event could not be mapped without weakening a boundary."""


@dataclass(frozen=True, slots=True)
class EventDescriptor:
    """Static handling policy for one exact pinned SDK event class."""

    kind: str
    allowed_sources: frozenset[str]
    core_role: str
    category: str
    model_visibility: bool
    transient: bool
    atomic_role: str


@dataclass(frozen=True, slots=True)
class _EventSpec:
    descriptor: EventDescriptor
    allowed_fields: frozenset[str]
    required_fields: frozenset[str]


@dataclass(frozen=True, slots=True)
class AtomicPair:
    """A verified call/result pair; individual events remain incomplete."""

    group_sha256: str
    action_id: str
    result_id: str
    tool_call_id: str
    tool_name: str
    result_kind: str

    def metadata(self) -> dict[str, Any]:
        return {
            "schema": ATOMIC_GROUP_SCHEMA,
            "group_sha256": self.group_sha256,
            "role": "pair",
            "complete": True,
            "action_id": self.action_id,
            "result_id": self.result_id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "result_kind": self.result_kind,
        }


def _spec(
    kind: str,
    *,
    sources: frozenset[str],
    role: str,
    category: str,
    visible: bool,
    transient: bool = False,
    atomic_role: str = "none",
    allowed: frozenset[str] = frozenset(),
    required: frozenset[str] = frozenset(),
) -> _EventSpec:
    return _EventSpec(
        descriptor=EventDescriptor(
            kind=kind,
            allowed_sources=sources,
            core_role=role,
            category=category,
            model_visibility=visible,
            transient=transient,
            atomic_role=atomic_role,
        ),
        allowed_fields=_COMMON_FIELDS | allowed,
        required_fields=_COMMON_FIELDS | required,
    )


_EVENT_SPECS = {
    "MessageEvent": _spec(
        "MessageEvent",
        sources=_EVENT_SOURCES,
        role="assistant",
        category="message",
        visible=True,
        allowed=frozenset(
            {
                "llm_message",
                "llm_response_id",
                "activated_skills",
                "extended_content",
                "sender",
                "critic_result",
            }
        ),
        required=frozenset({"llm_message"}),
    ),
    "SystemPromptEvent": _spec(
        "SystemPromptEvent",
        sources=frozenset({"agent"}),
        role="assistant",
        category="system-prompt",
        visible=True,
        allowed=frozenset({"system_prompt", "tools", "dynamic_context"}),
        required=frozenset({"system_prompt", "tools"}),
    ),
    "ActionEvent": _spec(
        "ActionEvent",
        sources=frozenset({"agent"}),
        role="tool",
        category="tool",
        visible=True,
        atomic_role="call",
        allowed=frozenset(
            {
                "thought",
                "reasoning_content",
                "thinking_blocks",
                "responses_reasoning_item",
                "action",
                "tool_name",
                "tool_call_id",
                "tool_call",
                "llm_response_id",
                "security_risk",
                "critic_result",
                "summary",
            }
        ),
        required=frozenset(
            {
                "thought",
                "tool_name",
                "tool_call_id",
                "tool_call",
                "llm_response_id",
            }
        ),
    ),
    "ObservationEvent": _spec(
        "ObservationEvent",
        sources=frozenset({"environment"}),
        role="tool",
        category="tool",
        visible=True,
        atomic_role="result",
        allowed=frozenset({"tool_name", "tool_call_id", "observation", "action_id"}),
        required=frozenset({"tool_name", "tool_call_id", "observation", "action_id"}),
    ),
    "UserRejectObservation": _spec(
        "UserRejectObservation",
        sources=frozenset({"environment"}),
        role="tool",
        category="tool",
        visible=True,
        atomic_role="result",
        allowed=frozenset(
            {
                "tool_name",
                "tool_call_id",
                "rejection_reason",
                "rejection_source",
                "action_id",
            }
        ),
        required=frozenset(
            {
                "tool_name",
                "tool_call_id",
                "rejection_reason",
                "rejection_source",
                "action_id",
            }
        ),
    ),
    "AgentErrorEvent": _spec(
        "AgentErrorEvent",
        sources=frozenset({"agent"}),
        role="tool",
        category="tool",
        visible=True,
        atomic_role="result",
        allowed=frozenset({"tool_name", "tool_call_id", "error"}),
        required=frozenset({"tool_name", "tool_call_id", "error"}),
    ),
    "Condensation": _spec(
        "Condensation",
        sources=frozenset({"environment"}),
        role="assistant",
        category="condenser",
        visible=True,
        allowed=frozenset({"forgotten_event_ids", "summary", "summary_offset", "llm_response_id"}),
        required=frozenset({"llm_response_id"}),
    ),
    "CondensationRequest": _spec(
        "CondensationRequest",
        sources=frozenset({"environment"}),
        role="assistant",
        category="control",
        visible=False,
        atomic_role="request",
    ),
    "CondensationSummaryEvent": _spec(
        "CondensationSummaryEvent",
        sources=frozenset({"environment"}),
        role="assistant",
        category="condenser",
        visible=True,
        transient=True,
        allowed=frozenset({"summary"}),
        required=frozenset({"summary"}),
    ),
    "ConversationStateUpdateEvent": _spec(
        "ConversationStateUpdateEvent",
        sources=frozenset({"environment"}),
        role="assistant",
        category="control",
        visible=False,
        allowed=frozenset({"key", "value"}),
        required=frozenset({"key", "value"}),
    ),
    "ConversationErrorEvent": _spec(
        "ConversationErrorEvent",
        sources=frozenset({"environment"}),
        role="assistant",
        category="control",
        visible=False,
        allowed=frozenset({"code", "detail"}),
        required=frozenset({"code", "detail"}),
    ),
    "HookExecutionEvent": _spec(
        "HookExecutionEvent",
        sources=frozenset({"hook"}),
        role="assistant",
        category="hook",
        visible=False,
        allowed=frozenset(
            {
                "hook_event_type",
                "hook_command",
                "tool_name",
                "success",
                "blocked",
                "exit_code",
                "stdout",
                "stderr",
                "reason",
                "additional_context",
                "error",
                "action_id",
                "message_id",
                "hook_input",
            }
        ),
        required=frozenset(
            {
                "hook_event_type",
                "hook_command",
                "success",
                "blocked",
                "exit_code",
                "stdout",
                "stderr",
            }
        ),
    ),
    "LLMCompletionLogEvent": _spec(
        "LLMCompletionLogEvent",
        sources=frozenset({"environment"}),
        role="assistant",
        category="telemetry",
        visible=False,
        allowed=frozenset({"filename", "log_data", "model_name", "usage_id"}),
        required=frozenset({"filename", "log_data", "model_name", "usage_id"}),
    ),
    "ACPToolCallEvent": _spec(
        "ACPToolCallEvent",
        sources=frozenset({"agent"}),
        role="tool",
        category="tool",
        visible=False,
        allowed=frozenset(
            {
                "tool_call_id",
                "title",
                "status",
                "tool_kind",
                "raw_input",
                "raw_output",
                "content",
                "is_error",
            }
        ),
        required=frozenset({"tool_call_id", "title", "is_error"}),
    ),
    "StreamingDeltaEvent": _spec(
        "StreamingDeltaEvent",
        sources=frozenset({"agent"}),
        role="assistant",
        category="stream",
        visible=False,
        transient=True,
        allowed=frozenset({"content", "reasoning_content"}),
    ),
    "TokenEvent": _spec(
        "TokenEvent",
        sources=_EVENT_SOURCES,
        role="assistant",
        category="telemetry",
        visible=False,
        transient=True,
        allowed=frozenset({"prompt_token_ids", "response_token_ids"}),
        required=frozenset({"prompt_token_ids", "response_token_ids"}),
    ),
    "PauseEvent": _spec(
        "PauseEvent",
        sources=frozenset({"user"}),
        role="assistant",
        category="control",
        visible=False,
    ),
    "InterruptEvent": _spec(
        "InterruptEvent",
        sources=frozenset({"user"}),
        role="assistant",
        category="control",
        visible=False,
    ),
}

SUPPORTED_EVENT_KINDS = frozenset(_EVENT_SPECS)


def _strict_text(
    value: Any,
    *,
    label: str,
    maximum: int = 512,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise EventMappingError(f"{label} must be a string")
    if value != value.strip() or (not value and not allow_empty):
        raise EventMappingError(
            f"{label} must be {'a string' if allow_empty else 'non-empty'} "
            "without surrounding whitespace"
        )
    if len(value) > maximum:
        raise EventMappingError(f"{label} exceeds {maximum} characters")
    if any(ord(character) < 0x20 for character in value):
        raise EventMappingError(f"{label} must not contain control characters")
    return value


def _mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EventMappingError(f"{label} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise EventMappingError(f"{label} keys must be strings")
    return dict(value)


def _exact_fields(
    value: Mapping[str, Any],
    *,
    allowed: frozenset[str],
    required: frozenset[str],
    label: str,
) -> None:
    fields = frozenset(value)
    missing = sorted(required.difference(fields))
    unexpected = sorted(fields.difference(allowed))
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise EventMappingError(f"{label} has invalid fields: {'; '.join(details)}")


def _event_mapping(event: Any) -> dict[str, Any]:
    try:
        raw = object_as_mapping(event, label="OpenHands event")
    except AuthorityError as exc:
        raise EventMappingError(str(exc)) from exc
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in _EVENT_SPECS:
        raise EventMappingError(f"unsupported OpenHands event kind: {kind!r}")
    if not isinstance(event, Mapping) and type(event).__name__ != kind:
        raise EventMappingError("OpenHands object class does not match its serialized kind")
    spec = _EVENT_SPECS[kind]
    _exact_fields(
        raw,
        allowed=spec.allowed_fields,
        required=spec.required_fields,
        label=kind,
    )
    _validate_common(raw, spec)
    _validate_variant(raw)
    return raw


def validate_host_event_shape(event: Any) -> dict[str, Any]:
    """Return one detached, canonical, fully bounded pinned host event."""

    raw = _event_mapping(event)
    try:
        encoded = canonical_json_bytes(raw, label="OpenHands event")
    except AuthorityError as exc:
        raise EventMappingError(str(exc)) from exc
    canonical = json.loads(encoded)
    if not isinstance(canonical, dict):  # pragma: no cover
        raise EventMappingError("OpenHands event canonical JSON must be an object")
    return canonical


def _validate_common(raw: Mapping[str, Any], spec: _EventSpec) -> None:
    kind = _strict_text(raw["kind"], label="event kind", maximum=128)
    if kind != spec.descriptor.kind:
        raise EventMappingError("event kind changed during dispatch")
    _strict_text(raw["id"], label=f"{kind}.id")
    source = _strict_text(raw["source"], label=f"{kind}.source", maximum=32)
    if source not in _EVENT_SOURCES:
        raise EventMappingError(f"{kind}.source is not an SDK EventSource")
    if source not in spec.descriptor.allowed_sources:
        raise EventMappingError(f"{kind}.source {source!r} is not valid for the pinned event kind")
    timestamp = _strict_text(
        raw["timestamp"],
        label=f"{kind}.timestamp",
        maximum=128,
    )
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EventMappingError(f"{kind}.timestamp is not ISO-8601") from exc


def _validate_content_item(value: Any, *, label: str) -> None:
    item = _mapping(value, label=label)
    content_type = item.get("type")
    if content_type == "text":
        _exact_fields(
            item,
            allowed=frozenset({"type", "text", "cache_prompt"}),
            required=frozenset({"type", "text"}),
            label=label,
        )
        _strict_text(item["text"], label=f"{label}.text", allow_empty=True, maximum=262_144)
    elif content_type == "image":
        _exact_fields(
            item,
            allowed=frozenset({"type", "image_urls", "cache_prompt"}),
            required=frozenset({"type", "image_urls"}),
            label=label,
        )
        urls = item["image_urls"]
        if not isinstance(urls, list) or len(urls) > 256:
            raise EventMappingError(f"{label}.image_urls must be a bounded array")
        for index, url in enumerate(urls):
            _strict_text(url, label=f"{label}.image_urls[{index}]", maximum=16_384)
    else:
        raise EventMappingError(f"{label}.type is unsupported")
    cache_prompt = item.get("cache_prompt")
    if cache_prompt is not None and not isinstance(cache_prompt, bool):
        raise EventMappingError(f"{label}.cache_prompt must be a boolean")


def _validate_message(value: Any) -> dict[str, Any]:
    message = _mapping(value, label="MessageEvent.llm_message")
    _exact_fields(
        message,
        allowed=_MESSAGE_FIELDS,
        required=frozenset({"role", "content"}),
        label="MessageEvent.llm_message",
    )
    role = _strict_text(
        message["role"],
        label="MessageEvent.llm_message.role",
        maximum=16,
    )
    if role not in {"assistant", "system", "tool", "user"}:
        raise EventMappingError("MessageEvent.llm_message.role is unsupported")
    contents = message["content"]
    if not isinstance(contents, list) or len(contents) > 10_000:
        raise EventMappingError("MessageEvent.llm_message.content must be a bounded array")
    for index, item in enumerate(contents):
        _validate_content_item(
            item,
            label=f"MessageEvent.llm_message.content[{index}]",
        )
    tool_calls = message.get("tool_calls")
    if tool_calls is not None:
        if not isinstance(tool_calls, list) or not tool_calls or len(tool_calls) > 256:
            raise EventMappingError(
                "MessageEvent.llm_message.tool_calls must be a non-empty bounded array"
            )
        seen: set[str] = set()
        for index, tool_call in enumerate(tool_calls):
            parsed = _validate_tool_call(
                tool_call,
                label=f"MessageEvent.llm_message.tool_calls[{index}]",
            )
            if parsed["id"] in seen:
                raise EventMappingError("MessageEvent carries duplicate tool call ids")
            seen.add(parsed["id"])
        if role != "assistant":
            raise EventMappingError("only an assistant Message can carry tool_calls")
    tool_call_id = message.get("tool_call_id")
    name = message.get("name")
    if role == "tool":
        _strict_text(tool_call_id, label="MessageEvent.llm_message.tool_call_id")
        _strict_text(name, label="MessageEvent.llm_message.name", maximum=256)
    elif tool_call_id is not None or name is not None:
        raise EventMappingError("only a tool Message can carry tool_call_id/name")
    return message


def _reject_tool_argument_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    detached: dict[str, Any] = {}
    for key, value in pairs:
        if key in detached:
            raise EventMappingError(
                f"tool-call arguments contain duplicate JSON key: {key!r}"
            )
        detached[key] = value
    return detached


def _reject_tool_argument_constant(value: str) -> None:
    raise EventMappingError(
        f"tool-call arguments contain non-finite JSON number: {value}"
    )


def _validate_tool_call(value: Any, *, label: str) -> dict[str, Any]:
    tool_call = _mapping(value, label=label)
    _exact_fields(
        tool_call,
        allowed=_TOOL_CALL_FIELDS,
        required=frozenset({"id", "name", "arguments", "origin"}),
        label=label,
    )
    _strict_text(tool_call["id"], label=f"{label}.id")
    _strict_text(tool_call["name"], label=f"{label}.name", maximum=256)
    arguments = _strict_text(
        tool_call["arguments"],
        label=f"{label}.arguments",
        maximum=262_144,
        allow_empty=False,
    )
    try:
        parsed = json.loads(
            arguments,
            object_pairs_hook=_reject_tool_argument_pairs,
            parse_constant=_reject_tool_argument_constant,
        )
    except EventMappingError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise EventMappingError(f"{label}.arguments is not JSON") from exc
    if not isinstance(parsed, dict):
        raise EventMappingError(f"{label}.arguments must encode a JSON object")
    try:
        canonical_json_bytes(parsed, label=f"{label}.arguments")
    except AuthorityError as exc:
        raise EventMappingError(str(exc)) from exc
    if tool_call["origin"] not in {"completion", "responses"}:
        raise EventMappingError(f"{label}.origin is unsupported")
    response_id = tool_call.get("responses_item_id")
    if response_id is not None:
        _strict_text(response_id, label=f"{label}.responses_item_id")
    return tool_call


def _validate_tool_name(raw: Mapping[str, Any]) -> str | None:
    value = raw.get("tool_name")
    if value is None:
        return None
    return _strict_text(value, label=f"{raw['kind']}.tool_name", maximum=256)


def _bounded_string(
    value: Any,
    *,
    label: str,
    maximum: int = 262_144,
    allow_empty: bool = True,
) -> str:
    if not isinstance(value, str):
        raise EventMappingError(f"{label} must be a string")
    if not allow_empty and not value:
        raise EventMappingError(f"{label} must be non-empty")
    if len(value) > maximum:
        raise EventMappingError(f"{label} exceeds {maximum} characters")
    return value


def _bounded_json(value: Any, *, label: str) -> None:
    try:
        canonical_json_bytes(value, label=label)
    except AuthorityError as exc:
        raise EventMappingError(str(exc)) from exc


def _validate_token_ids(value: Any, *, label: str) -> None:
    if not isinstance(value, list) or len(value) > _MAX_TOKEN_IDS:
        raise EventMappingError(f"{label} must be a bounded array")
    for index, token_id in enumerate(value):
        if (
            not isinstance(token_id, int)
            or isinstance(token_id, bool)
            or not 0 <= token_id <= 2_147_483_647
        ):
            raise EventMappingError(
                f"{label}[{index}] must be a non-negative 32-bit integer"
            )


def _validate_variant(raw: Mapping[str, Any]) -> None:
    kind = raw["kind"]
    if kind == "MessageEvent":
        _validate_message(raw["llm_message"])
    elif kind == "SystemPromptEvent":
        _validate_content_item(raw["system_prompt"], label="SystemPromptEvent.system_prompt")
        if not isinstance(raw["tools"], list) or len(raw["tools"]) > 10_000:
            raise EventMappingError("SystemPromptEvent.tools must be a bounded array")
    elif kind == "ActionEvent":
        tool_name = _strict_text(
            raw["tool_name"],
            label="ActionEvent.tool_name",
            maximum=256,
        )
        tool_call_id = _strict_text(
            raw["tool_call_id"],
            label="ActionEvent.tool_call_id",
        )
        tool_call = _validate_tool_call(raw["tool_call"], label="ActionEvent.tool_call")
        if tool_call["id"] != tool_call_id or tool_call["name"] != tool_name:
            raise EventMappingError(
                "ActionEvent tool_call id/name must match tool_call_id/tool_name"
            )
        _strict_text(
            raw["llm_response_id"],
            label="ActionEvent.llm_response_id",
        )
        thought = raw["thought"]
        if not isinstance(thought, list) or len(thought) > 10_000:
            raise EventMappingError("ActionEvent.thought must be a bounded array")
        for index, item in enumerate(thought):
            _validate_content_item(item, label=f"ActionEvent.thought[{index}]")
        _validate_nested_tool_category(
            raw.get("action"),
            tool_name=tool_name,
            kind_categories=_ACTION_KIND_CATEGORIES,
            label="ActionEvent.action",
        )
    elif kind == "ObservationEvent":
        tool_name = _validate_tool_name(raw)
        if tool_name is None:
            raise EventMappingError("ObservationEvent.tool_name must be a string")
        _strict_text(raw["tool_call_id"], label="ObservationEvent.tool_call_id")
        _strict_text(raw["action_id"], label="ObservationEvent.action_id")
        _validate_nested_tool_category(
            raw["observation"],
            tool_name=tool_name,
            kind_categories=_OBSERVATION_KIND_CATEGORIES,
            label="ObservationEvent.observation",
        )
    elif kind == "UserRejectObservation":
        tool_name = _validate_tool_name(raw)
        if tool_name is None:
            raise EventMappingError("UserRejectObservation.tool_name must be a string")
        _strict_text(raw["tool_call_id"], label="UserRejectObservation.tool_call_id")
        _strict_text(raw["action_id"], label="UserRejectObservation.action_id")
        _bounded_string(
            raw["rejection_reason"],
            label="UserRejectObservation.rejection_reason",
        )
        rejection_source = raw["rejection_source"]
        if rejection_source not in {"user", "hook"}:
            raise EventMappingError("UserRejectObservation.rejection_source is unsupported")
    elif kind == "AgentErrorEvent":
        tool_name = _validate_tool_name(raw)
        if tool_name is None:
            raise EventMappingError("AgentErrorEvent.tool_name must be a string")
        _strict_text(raw["tool_call_id"], label="AgentErrorEvent.tool_call_id")
        _bounded_string(
            raw["error"],
            label="AgentErrorEvent.error",
        )
    elif kind == "Condensation":
        _strict_text(raw["llm_response_id"], label="Condensation.llm_response_id")
        forgotten = raw.get("forgotten_event_ids")
        if forgotten is not None:
            if not isinstance(forgotten, list) or len(forgotten) > 10_000:
                raise EventMappingError("Condensation.forgotten_event_ids must be a bounded array")
            seen_forgotten: set[str] = set()
            for index, event_id in enumerate(forgotten):
                event_id = _strict_text(
                    event_id,
                    label=f"Condensation.forgotten_event_ids[{index}]",
                )
                if event_id in seen_forgotten:
                    raise EventMappingError(
                        "Condensation.forgotten_event_ids contains duplicates"
                    )
                seen_forgotten.add(event_id)
        summary = raw.get("summary")
        if summary is not None:
            _bounded_string(summary, label="Condensation.summary")
        offset = raw.get("summary_offset")
        if offset is not None and (
            not isinstance(offset, int) or isinstance(offset, bool) or offset < 0
        ):
            raise EventMappingError("Condensation.summary_offset must be a non-negative integer")
    elif kind == "CondensationSummaryEvent":
        _bounded_string(
            raw["summary"],
            label="CondensationSummaryEvent.summary",
        )
    elif kind == "ConversationStateUpdateEvent":
        _bounded_string(
            raw["key"],
            label="ConversationStateUpdateEvent.key",
            maximum=512,
        )
        _bounded_json(raw["value"], label="ConversationStateUpdateEvent.value")
    elif kind == "ConversationErrorEvent":
        _strict_text(raw["code"], label="ConversationErrorEvent.code", maximum=128)
        _bounded_string(
            raw["detail"],
            label="ConversationErrorEvent.detail",
        )
    elif kind == "HookExecutionEvent":
        hook_event_type = _strict_text(
            raw["hook_event_type"],
            label="HookExecutionEvent.hook_event_type",
            maximum=128,
        )
        if hook_event_type not in _HOOK_EVENT_TYPES:
            raise EventMappingError("HookExecutionEvent.hook_event_type is unsupported")
        _bounded_string(
            raw["hook_command"],
            label="HookExecutionEvent.hook_command",
            maximum=16_384,
            allow_empty=False,
        )
        _validate_tool_name(raw)
        if not isinstance(raw["success"], bool):
            raise EventMappingError("HookExecutionEvent.success must be a boolean")
        if not isinstance(raw["blocked"], bool):
            raise EventMappingError("HookExecutionEvent.blocked must be a boolean")
        exit_code = raw["exit_code"]
        if (
            not isinstance(exit_code, int)
            or isinstance(exit_code, bool)
            or not -(2**31) <= exit_code < 2**31
        ):
            raise EventMappingError("HookExecutionEvent.exit_code must be a signed 32-bit integer")
        for field in ("stdout", "stderr", "reason", "additional_context", "error"):
            value = raw.get(field)
            if value is not None:
                _bounded_string(value, label=f"HookExecutionEvent.{field}")
        for field in ("action_id", "message_id"):
            value = raw.get(field)
            if value is not None:
                _strict_text(value, label=f"HookExecutionEvent.{field}")
        hook_input = raw.get("hook_input")
        if hook_input is not None:
            if not isinstance(hook_input, Mapping):
                raise EventMappingError("HookExecutionEvent.hook_input must be an object")
            _bounded_json(hook_input, label="HookExecutionEvent.hook_input")
    elif kind == "LLMCompletionLogEvent":
        _strict_text(
            raw["filename"],
            label="LLMCompletionLogEvent.filename",
            maximum=4_096,
        )
        _bounded_string(
            raw["log_data"],
            label="LLMCompletionLogEvent.log_data",
        )
        _strict_text(
            raw["model_name"],
            label="LLMCompletionLogEvent.model_name",
            maximum=512,
        )
        _strict_text(
            raw["usage_id"],
            label="LLMCompletionLogEvent.usage_id",
            maximum=512,
        )
    elif kind == "ACPToolCallEvent":
        _strict_text(raw["tool_call_id"], label="ACPToolCallEvent.tool_call_id")
        _strict_text(raw["title"], label="ACPToolCallEvent.title", maximum=16_384)
        for field in ("status", "tool_kind"):
            value = raw.get(field)
            if value is not None:
                _strict_text(value, label=f"ACPToolCallEvent.{field}", maximum=256)
        for field in ("raw_input", "raw_output"):
            value = raw.get(field)
            if value is not None:
                _bounded_json(value, label=f"ACPToolCallEvent.{field}")
        content = raw.get("content")
        if content is not None:
            if not isinstance(content, list) or len(content) > _MAX_EVENT_ARRAY_ITEMS:
                raise EventMappingError("ACPToolCallEvent.content must be a bounded array")
            _bounded_json(content, label="ACPToolCallEvent.content")
        if not isinstance(raw["is_error"], bool):
            raise EventMappingError("ACPToolCallEvent.is_error must be a boolean")
    elif kind == "StreamingDeltaEvent":
        for field in ("content", "reasoning_content"):
            value = raw.get(field)
            if value is not None:
                _bounded_string(value, label=f"StreamingDeltaEvent.{field}")
    elif kind == "TokenEvent":
        _validate_token_ids(
            raw["prompt_token_ids"],
            label="TokenEvent.prompt_token_ids",
        )
        _validate_token_ids(
            raw["response_token_ids"],
            label="TokenEvent.response_token_ids",
        )


def _validate_nested_tool_category(
    value: Any,
    *,
    tool_name: str,
    kind_categories: Mapping[str, str],
    label: str,
) -> None:
    if value is None:
        return
    nested = _mapping(value, label=label)
    nested_kind = nested.get("kind")
    if nested_kind is None:
        return
    nested_kind = _strict_text(nested_kind, label=f"{label}.kind", maximum=128)
    expected_category = kind_categories.get(nested_kind)
    if expected_category is None:
        raise EventMappingError(
            f"{label}.kind {nested_kind!r} is unsupported by the pinned compatibility policy"
        )
    actual_category = categorize_tool_name(tool_name)
    if actual_category != expected_category:
        raise EventMappingError(f"{label}.kind disagrees with the explicit tool_name category")


def classify_host_event(event_or_kind: Any) -> EventDescriptor:
    """Return the frozen policy descriptor for one exact event kind."""

    if isinstance(event_or_kind, str):
        kind = event_or_kind
    else:
        try:
            kind = object_as_mapping(
                event_or_kind,
                label="OpenHands event",
            ).get("kind")
        except AuthorityError as exc:
            raise EventMappingError(str(exc)) from exc
    if not isinstance(kind, str) or kind not in _EVENT_SPECS:
        raise EventMappingError(f"unsupported OpenHands event kind: {kind!r}")
    return _EVENT_SPECS[kind].descriptor


def categorize_tool_name(tool_name: str) -> str:
    """Classify only reviewed names; arbitrary MCP names remain generic."""

    name = _strict_text(tool_name, label="tool_name", maximum=256)
    if name in _FILE_TOOLS:
        return "file"
    if name in _SEARCH_TOOLS:
        return "search"
    if name in _RETRIEVAL_TOOLS:
        return "retrieval"
    if name in _DELEGATED_TOOLS:
        return "delegated"
    return "generic"


def _tool_details(raw: Mapping[str, Any]) -> tuple[str | None, str, bool]:
    kind = raw["kind"]
    if kind in {
        "ActionEvent",
        "ObservationEvent",
        "UserRejectObservation",
        "AgentErrorEvent",
    }:
        tool_name = _strict_text(
            raw["tool_name"],
            label=f"{kind}.tool_name",
            maximum=256,
        )
        return tool_name, categorize_tool_name(tool_name), tool_name in KNOWN_TOOL_NAMES
    if kind == "MessageEvent":
        message = _mapping(raw["llm_message"], label="MessageEvent.llm_message")
        if message["role"] == "tool":
            tool_name = _strict_text(
                message["name"],
                label="MessageEvent.llm_message.name",
                maximum=256,
            )
            return (
                tool_name,
                categorize_tool_name(tool_name),
                tool_name in KNOWN_TOOL_NAMES,
            )
    return None, _EVENT_SPECS[kind].descriptor.category, False


def _claimed_roles(raw: Mapping[str, Any]) -> frozenset[str]:
    kind = raw["kind"]
    descriptor = _EVENT_SPECS[kind].descriptor
    if not descriptor.model_visibility:
        return frozenset()
    if kind == "MessageEvent":
        role = _mapping(raw["llm_message"], label="MessageEvent.llm_message")["role"]
        if role == "system":
            return frozenset({"developer", "system"})
        return frozenset({role})
    if kind == "SystemPromptEvent":
        return frozenset({"developer", "system"})
    if descriptor.core_role == "tool":
        return frozenset({"tool"})
    return frozenset({"assistant"})


def _atomic_group_sha256(*, session_id: str, tool_call_id: str) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "schema": ATOMIC_GROUP_SCHEMA,
                "session_id": session_id,
                "tool_call_id": tool_call_id,
            },
            label="atomic group identity",
        )
    ).hexdigest()


def _atomic_metadata(raw: Mapping[str, Any], *, session_id: str) -> dict[str, Any] | None:
    kind = raw["kind"]
    if kind == "ActionEvent":
        tool_call_id = raw["tool_call_id"]
        return {
            "schema": ATOMIC_GROUP_SCHEMA,
            "group_sha256": _atomic_group_sha256(
                session_id=session_id,
                tool_call_id=tool_call_id,
            ),
            "role": "call",
            "complete": False,
            "tool_call_id": tool_call_id,
            "action_id": raw["id"],
            "llm_response_id": raw["llm_response_id"],
        }
    if kind in {"ObservationEvent", "UserRejectObservation", "AgentErrorEvent"}:
        tool_call_id = raw["tool_call_id"]
        return {
            "schema": ATOMIC_GROUP_SCHEMA,
            "group_sha256": _atomic_group_sha256(
                session_id=session_id,
                tool_call_id=tool_call_id,
            ),
            "role": "result",
            "complete": False,
            "tool_call_id": tool_call_id,
            "action_id": raw.get("action_id"),
        }
    return None


def _message_atomic_groups(
    raw: Mapping[str, Any],
    *,
    session_id: str,
) -> list[dict[str, Any]]:
    if raw["kind"] != "MessageEvent":
        return []
    message = _mapping(raw["llm_message"], label="MessageEvent.llm_message")
    groups: list[dict[str, Any]] = []
    if message["role"] == "assistant":
        for tool_call in message.get("tool_calls") or []:
            groups.append(
                {
                    "schema": ATOMIC_GROUP_SCHEMA,
                    "group_sha256": _atomic_group_sha256(
                        session_id=session_id,
                        tool_call_id=tool_call["id"],
                    ),
                    "role": "llm-call",
                    "complete": False,
                    "tool_call_id": tool_call["id"],
                    "action_id": None,
                }
            )
    elif message["role"] == "tool":
        groups.append(
            {
                "schema": ATOMIC_GROUP_SCHEMA,
                "group_sha256": _atomic_group_sha256(
                    session_id=session_id,
                    tool_call_id=message["tool_call_id"],
                ),
                "role": "llm-result",
                "complete": False,
                "tool_call_id": message["tool_call_id"],
                "action_id": None,
            }
        )
    return groups


def _verify_authority(
    raw: Mapping[str, Any],
    *,
    session_id: str,
    authority_policy: AuthorityPolicy | None,
    authority_receipt: Any | None,
    tool_name: str | None,
    known_tool: bool,
) -> VerifiedAuthority | None:
    if authority_receipt is None:
        return None
    descriptor = _EVENT_SPECS[raw["kind"]].descriptor
    if not descriptor.model_visibility or descriptor.transient:
        raise EventMappingError("auxiliary or transient events cannot carry authority")
    if authority_policy is None:
        raise EventMappingError("authority receipt requires an AuthorityPolicy")
    try:
        verified = verify_authority_receipt(
            raw,
            authority_receipt,
            session_id=session_id,
            policy=authority_policy,
        )
    except AuthorityVerificationError as exc:
        raise EventMappingError(f"authority receipt rejected: {exc}") from exc
    expected_roles = _claimed_roles(raw)
    if verified.claimed_role not in expected_roles:
        raise EventMappingError("authority receipt role does not match the pinned event semantics")
    if verified.claimed_role == "tool":
        if tool_name is not None and verified.tool_name not in {None, tool_name}:
            raise EventMappingError("authority receipt tool_name does not match event")
        if tool_name is not None and not known_tool:
            raise EventMappingError("unknown tool names cannot receive authority")
        if verified.trusted_for_state and verified.tool_name != tool_name:
            raise EventMappingError("trusted state receipt must bind the exact event tool_name")
    elif verified.tool_name is not None:
        raise EventMappingError("non-tool event receipt carries tool_name")
    return verified


def map_host_event(
    event: Any,
    *,
    sequence: int,
    session_id: str,
    authority_policy: AuthorityPolicy | None = None,
    authority_receipt: Any | None = None,
) -> SourceEvent:
    """Map one exact pinned host event without trusting host role literals."""

    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise EventMappingError("sequence must be a non-negative integer")
    session_id = _strict_text(session_id, label="session_id")
    raw = validate_host_event_shape(event)
    descriptor = _EVENT_SPECS[raw["kind"]].descriptor
    tool_name, category, known_tool = _tool_details(raw)
    verified = _verify_authority(
        raw,
        session_id=session_id,
        authority_policy=authority_policy,
        authority_receipt=authority_receipt,
        tool_name=tool_name,
        known_tool=known_tool,
    )

    effective_role = descriptor.core_role
    if raw["kind"] == "MessageEvent":
        message_role = _mapping(
            raw["llm_message"],
            label="MessageEvent.llm_message",
        )["role"]
        effective_role = "tool" if message_role == "tool" else "assistant"
    if verified is not None:
        effective_role = verified.claimed_role

    event_bytes = canonical_json_bytes(raw, label="OpenHands event")
    event_sha = hashlib.sha256(event_bytes).hexdigest()
    authority_metadata: dict[str, Any] = {
        "authenticated": verified is not None,
        "claimed_roles": sorted(_claimed_roles(raw)),
        "issuer": None if verified is None else verified.issuer,
        "receipt_sha256": None if verified is None else verified.receipt_sha256,
        "trusted_for_state": (False if verified is None else verified.trusted_for_state),
    }
    host_metadata: dict[str, Any] = {
        "schema": EVENT_METADATA_SCHEMA,
        "host_version": OPENHANDS_HOST_VERSION,
        "host_revision": OPENHANDS_HOST_REVISION,
        "sdk_version": OPENHANDS_SDK_VERSION,
        "sdk_revision": OPENHANDS_SDK_REVISION,
        "event_kind": raw["kind"],
        "event_source_claim": raw["source"],
        "event_sha256": event_sha,
        "category": category,
        "known_tool_name": known_tool,
        "tool_name": tool_name,
        "model_visibility": descriptor.model_visibility,
        "transient": descriptor.transient,
        "authority": authority_metadata,
    }
    if raw["kind"] == "MessageEvent":
        host_metadata["message_role_claim"] = raw["llm_message"]["role"]
    atomic = _atomic_metadata(raw, session_id=session_id)
    if atomic is not None:
        host_metadata["atomic_group"] = atomic
    message_groups = _message_atomic_groups(raw, session_id=session_id)
    if message_groups:
        host_metadata["atomic_groups"] = message_groups

    connector_authority = (
        {"authenticated": False, "trusted_for_state": False, "issuer": None}
        if verified is None
        else verified.connector_envelope()
    )
    return SourceEvent(
        id=raw["id"],
        sequence=sequence,
        role=effective_role,
        content=event_bytes.decode("utf-8"),
        timestamp=raw["timestamp"],
        metadata={"openhands": host_metadata},
        authority=connector_authority,
        provenance={
            "host": "OpenHands",
            "event_id": raw["id"],
            "event_sha256": event_sha,
            "session_id": session_id,
        },
    )


def validate_atomic_event_pair(
    action_event: Any,
    result_event: Any,
    *,
    session_id: str,
) -> AtomicPair:
    """Validate exact action/result linkage before exposing a complete group."""

    session_id = _strict_text(session_id, label="session_id")
    action = validate_host_event_shape(action_event)
    result = validate_host_event_shape(result_event)
    if action["kind"] != "ActionEvent":
        raise EventMappingError("atomic pair must start with ActionEvent")
    if result["kind"] not in {
        "ObservationEvent",
        "UserRejectObservation",
        "AgentErrorEvent",
    }:
        raise EventMappingError("atomic pair must end with an observation or agent error")
    if action["tool_call_id"] != result["tool_call_id"]:
        raise EventMappingError("atomic pair tool_call_id mismatch")
    if action["tool_name"] != result["tool_name"]:
        raise EventMappingError("atomic pair tool_name mismatch")
    if result["kind"] != "AgentErrorEvent" and action["id"] != result["action_id"]:
        raise EventMappingError("atomic pair action_id mismatch")
    return AtomicPair(
        group_sha256=_atomic_group_sha256(
            session_id=session_id,
            tool_call_id=action["tool_call_id"],
        ),
        action_id=action["id"],
        result_id=result["id"],
        tool_call_id=action["tool_call_id"],
        tool_name=action["tool_name"],
        result_kind=result["kind"],
    )


__all__ = [
    "ATOMIC_GROUP_SCHEMA",
    "EVENT_METADATA_SCHEMA",
    "KNOWN_TOOL_NAMES",
    "OPENHANDS_HOST_REVISION",
    "OPENHANDS_HOST_VERSION",
    "OPENHANDS_SDK_REVISION",
    "OPENHANDS_SDK_VERSION",
    "SUPPORTED_EVENT_KINDS",
    "AtomicPair",
    "EventDescriptor",
    "EventMappingError",
    "categorize_tool_name",
    "classify_host_event",
    "map_host_event",
    "validate_atomic_event_pair",
    "validate_host_event_shape",
]
