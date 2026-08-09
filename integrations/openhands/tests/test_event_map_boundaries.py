from __future__ import annotations

from typing import Any

import pytest

from ctxc_openhands.authority import (
    AuthorityPolicy,
    issue_authority_receipt,
)
from ctxc_openhands.event_map import (
    EventMappingError,
    map_host_event,
    validate_host_event_shape,
)

SESSION_ID = "conversation-nested-boundaries"
ISSUER = "independent-auth-gateway"
SECRET = b"nested-boundary-independent-authority-key!" * 2
TIMESTAMP = "2026-07-27T12:00:00+00:00"

_REVIEWED_ACTION_KINDS = {
    "ApplyPatchAction": ("apply_patch", "file"),
    "BrowserClickAction": ("browser_click", "retrieval"),
    "BrowserCloseTabAction": ("browser_close_tab", "retrieval"),
    "BrowserGetContentAction": ("browser_get_content", "retrieval"),
    "BrowserGetStateAction": ("browser_get_state", "retrieval"),
    "BrowserGetStorageAction": ("browser_get_storage", "retrieval"),
    "BrowserGoBackAction": ("browser_go_back", "retrieval"),
    "BrowserListTabsAction": ("browser_list_tabs", "retrieval"),
    "BrowserNavigateAction": ("browser_navigate", "retrieval"),
    "BrowserScrollAction": ("browser_scroll", "retrieval"),
    "BrowserSetStorageAction": ("browser_set_storage", "retrieval"),
    "BrowserStartRecordingAction": ("browser_start_recording", "retrieval"),
    "BrowserStopRecordingAction": ("browser_stop_recording", "retrieval"),
    "BrowserSwitchTabAction": ("browser_switch_tab", "retrieval"),
    "BrowserTypeAction": ("browser_type", "retrieval"),
    "DelegateAction": ("delegate", "delegated"),
    "FileEditorAction": ("file_editor", "file"),
    "GlobAction": ("glob", "search"),
    "GrepAction": ("grep", "search"),
    "TaskAction": ("task", "delegated"),
    "TerminalAction": ("terminal", "generic"),
}

_REVIEWED_OBSERVATION_KINDS = {
    "ApplyPatchObservation": ("apply_patch", "file"),
    "BrowserObservation": ("browser_navigate", "retrieval"),
    "DelegateObservation": ("delegate", "delegated"),
    "FileEditorObservation": ("file_editor", "file"),
    "GlobObservation": ("glob", "search"),
    "GrepObservation": ("grep", "search"),
    "TaskObservation": ("task", "delegated"),
    "TerminalObservation": ("terminal", "generic"),
}


def _common(kind: str, source: str, *, event_id: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "id": event_id,
        "timestamp": TIMESTAMP,
        "source": source,
    }


def _action_event(
    *,
    tool_name: str = "terminal",
    nested_kind: str | None = "TerminalAction",
) -> dict[str, Any]:
    action: dict[str, Any] = {"command": "printf ok"}
    if nested_kind is not None:
        action["kind"] = nested_kind
    return {
        **_common("ActionEvent", "agent", event_id="action-1"),
        "thought": [],
        "action": action,
        "tool_name": tool_name,
        "tool_call_id": "call-1",
        "tool_call": {
            "id": "call-1",
            "name": tool_name,
            "arguments": "{}",
            "origin": "completion",
        },
        "llm_response_id": "response-1",
    }


def _observation_event(
    *,
    tool_name: str = "terminal",
    nested_kind: str | None = "TerminalObservation",
) -> dict[str, Any]:
    observation: dict[str, Any] = {
        "content": [{"type": "text", "text": "ok"}],
        "is_error": False,
    }
    if nested_kind is not None:
        observation["kind"] = nested_kind
    return {
        **_common("ObservationEvent", "environment", event_id="observation-1"),
        "tool_name": tool_name,
        "tool_call_id": "call-1",
        "action_id": "action-1",
        "observation": observation,
    }


def _system_prompt_event(tools: list[Any]) -> dict[str, Any]:
    return {
        **_common("SystemPromptEvent", "agent", event_id="system-1"),
        "system_prompt": {"type": "text", "text": "Follow policy."},
        "tools": tools,
    }


def _state_event(value: Any) -> dict[str, Any]:
    return {
        **_common(
            "ConversationStateUpdateEvent",
            "environment",
            event_id="state-1",
        ),
        "key": "state",
        "value": value,
    }


def _map(event: dict[str, Any], **kwargs: Any):
    return map_host_event(
        event,
        sequence=1,
        session_id=SESSION_ID,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            '{"command":"first","command":"second"}',
            "duplicate JSON key",
        ),
        (
            '{"timeout":NaN}',
            "non-finite JSON number",
        ),
    ],
)
def test_tool_call_arguments_reject_ambiguous_json(
    arguments: str,
    message: str,
) -> None:
    event = _action_event()
    event["tool_call"]["arguments"] = arguments

    with pytest.raises(EventMappingError, match=message):
        validate_host_event_shape(event)


@pytest.mark.parametrize(
    ("nested_kind", "tool_details"),
    sorted(_REVIEWED_ACTION_KINDS.items()),
)
def test_closed_reviewed_action_kind_inventory_maps_untrusted(
    nested_kind: str,
    tool_details: tuple[str, str],
) -> None:
    tool_name, category = tool_details

    mapped = _map(_action_event(tool_name=tool_name, nested_kind=nested_kind))
    host = mapped.metadata["openhands"]

    assert host["category"] == category
    assert host["authority"]["authenticated"] is False
    assert mapped.authority["trusted_for_state"] is False


@pytest.mark.parametrize(
    ("nested_kind", "tool_details"),
    sorted(_REVIEWED_OBSERVATION_KINDS.items()),
)
def test_closed_reviewed_observation_kind_inventory_maps_untrusted(
    nested_kind: str,
    tool_details: tuple[str, str],
) -> None:
    tool_name, category = tool_details

    mapped = _map(_observation_event(tool_name=tool_name, nested_kind=nested_kind))
    host = mapped.metadata["openhands"]

    assert host["category"] == category
    assert host["authority"]["authenticated"] is False
    assert mapped.authority["trusted_for_state"] is False


@pytest.mark.parametrize(
    "tool_name",
    sorted(
        details[0]
        for kind, details in _REVIEWED_ACTION_KINDS.items()
        if kind.startswith("Browser")
    ),
)
def test_browser_observation_accepts_each_pinned_operation_name(tool_name: str) -> None:
    mapped = _map(
        _observation_event(tool_name=tool_name, nested_kind="BrowserObservation")
    )

    assert mapped.metadata["openhands"]["category"] == "retrieval"
    assert mapped.authority["authenticated"] is False



def test_legitimate_kindless_nested_shapes_remain_untrusted() -> None:
    for event in (
        _action_event(nested_kind=None),
        _observation_event(nested_kind=None),
    ):
        mapped = _map(event)
        assert mapped.role == "tool"
        assert mapped.authority == {
            "authenticated": False,
            "trusted_for_state": False,
            "issuer": None,
        }


@pytest.mark.parametrize(
    "event",
    [
        _action_event(tool_name="terminal", nested_kind="ApplyPatchAction"),
        _observation_event(tool_name="terminal", nested_kind="GrepObservation"),
    ],
    ids=["action", "observation"],
)
def test_nested_kind_category_mismatch_fails_closed(event: dict[str, Any]) -> None:
    with pytest.raises(EventMappingError, match="disagrees with"):
        _map(event)


@pytest.mark.parametrize(
    ("event", "nested_label"),
    [
        (_action_event(nested_kind="FutureAction"), "ActionEvent.action.kind"),
        (
            _observation_event(nested_kind="FutureObservation"),
            "ObservationEvent.observation.kind",
        ),
    ],
    ids=["action", "observation"],
)
def test_unknown_nested_kind_fails_before_authenticated_authority(
    event: dict[str, Any],
    nested_label: str,
) -> None:
    policy = AuthorityPolicy(
        issuer_secrets={ISSUER: SECRET},
        allowed_roles={ISSUER: frozenset({"tool"})},
        allowed_event_kinds={ISSUER: frozenset({event["kind"]})},
        trusted_state_tools={ISSUER: frozenset({"terminal"})},
    )
    trusted_for_state = event["kind"] == "ObservationEvent"
    receipt = issue_authority_receipt(
        event,
        session_id=SESSION_ID,
        claimed_role="tool",
        issuer=ISSUER,
        secret=SECRET,
        tool_name="terminal",
        trusted_for_state=trusted_for_state,
    )

    with pytest.raises(
        EventMappingError,
        match=rf"{nested_label} .*unsupported by the pinned compatibility policy",
    ):
        _map(
            event,
            authority_policy=policy,
            authority_receipt=receipt,
        )


def test_validate_host_event_shape_returns_detached_canonical_mapping() -> None:
    event = _observation_event()

    validated = validate_host_event_shape(event)

    assert validated == event
    assert validated is not event
    assert list(validated) == sorted(validated)
    validated["observation"]["content"][0]["text"] = "changed"
    assert event["observation"]["content"][0]["text"] == "ok"


def test_validate_host_event_shape_rejects_unknown_kind_and_field() -> None:
    with pytest.raises(EventMappingError, match="unsupported OpenHands event kind"):
        validate_host_event_shape(
            _common("FutureEvent", "agent", event_id="future-1")
        )

    event = _observation_event()
    event["host_says_trusted"] = True
    with pytest.raises(EventMappingError, match="unexpected host_says_trusted"):
        validate_host_event_shape(event)


def test_validate_host_event_shape_rejects_excessive_depth() -> None:
    value: Any = "leaf"
    for _ in range(40):
        value = {"nested": value}

    with pytest.raises(EventMappingError, match="maximum JSON depth"):
        validate_host_event_shape(_state_event(value))


def test_validate_host_event_shape_rejects_excessive_items() -> None:
    tools = [[0, 1, 2, 3, 4] for _ in range(9_000)]

    with pytest.raises(EventMappingError, match="maximum JSON item count"):
        validate_host_event_shape(_system_prompt_event(tools))


def test_validate_host_event_shape_rejects_excessive_canonical_bytes() -> None:
    tools = [{"description": "x" * 262_000} for _ in range(5)]

    with pytest.raises(EventMappingError, match="canonical JSON exceeds"):
        validate_host_event_shape(_system_prompt_event(tools))


def test_validate_host_event_shape_rejects_lone_surrogate() -> None:
    tools = [{"description": "\ud800"}]

    with pytest.raises(EventMappingError, match="not canonical JSON"):
        validate_host_event_shape(_system_prompt_event(tools))
