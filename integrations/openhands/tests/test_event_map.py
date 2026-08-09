from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from context_compiler import source_event_to_record

from ctxc_openhands.authority import (
    AuthorityPolicy,
    issue_authority_receipt,
)
from ctxc_openhands.event_map import (
    SUPPORTED_EVENT_KINDS,
    EventMappingError,
    categorize_tool_name,
    classify_host_event,
    map_host_event,
    validate_atomic_event_pair,
)

SESSION_ID = "conversation-event-map"
ISSUER = "independent-auth-gateway"
SECRET = b"event-map-independent-authority-key!" * 2
TIMESTAMP = "2026-07-27T12:00:00+00:00"

EXPECTED_EVENT_KINDS = frozenset(
    {
        "SystemPromptEvent",
        "MessageEvent",
        "ActionEvent",
        "ObservationEvent",
        "UserRejectObservation",
        "AgentErrorEvent",
        "Condensation",
        "CondensationRequest",
        "CondensationSummaryEvent",
        "ACPToolCallEvent",
        "ConversationErrorEvent",
        "ConversationStateUpdateEvent",
        "HookExecutionEvent",
        "LLMCompletionLogEvent",
        "StreamingDeltaEvent",
        "TokenEvent",
        "PauseEvent",
        "InterruptEvent",
    }
)


def _common(kind: str, source: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "id": f"{kind}-1",
        "timestamp": TIMESTAMP,
        "source": source,
    }


def _tool_call(*, tool_name: str = "terminal", tool_call_id: str = "call-1") -> dict:
    return {
        "id": tool_call_id,
        "name": tool_name,
        "arguments": '{"command":"printf ok"}',
        "origin": "completion",
    }


def action_event(
    *,
    tool_name: str = "terminal",
    tool_call_id: str = "call-1",
    action_id: str = "action-1",
) -> dict:
    return {
        **_common("ActionEvent", "agent"),
        "id": action_id,
        "thought": [{"type": "text", "text": "Run a bounded command."}],
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "tool_call": _tool_call(tool_name=tool_name, tool_call_id=tool_call_id),
        "llm_response_id": "response-1",
        "action": {"kind": "TerminalAction", "command": "printf ok"},
    }


def observation_event(
    *,
    tool_name: str = "terminal",
    tool_call_id: str = "call-1",
    action_id: str = "action-1",
    observation_kind: str | None = "TerminalObservation",
) -> dict:
    return {
        **_common("ObservationEvent", "environment"),
        "id": "observation-1",
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "action_id": action_id,
        "observation": {
            **({} if observation_kind is None else {"kind": observation_kind}),
            "content": [{"type": "text", "text": "ok"}],
            "is_error": False,
        },
    }


def reject_event(
    *,
    tool_name: str = "terminal",
    tool_call_id: str = "call-1",
    action_id: str = "action-1",
) -> dict:
    return {
        **_common("UserRejectObservation", "environment"),
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "action_id": action_id,
        "rejection_reason": "Denied by the user.",
        "rejection_source": "user",
    }


def agent_error_event(
    *,
    tool_name: str = "terminal",
    tool_call_id: str = "call-1",
) -> dict:
    return {
        **_common("AgentErrorEvent", "agent"),
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "error": "Tool execution failed.",
    }


def valid_events() -> dict[str, dict[str, Any]]:
    return {
        "MessageEvent": {
            **_common("MessageEvent", "agent"),
            "llm_message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "I will inspect the repository."}],
            },
        },
        "SystemPromptEvent": {
            **_common("SystemPromptEvent", "agent"),
            "system_prompt": {"type": "text", "text": "Follow repository policy."},
            "tools": [],
        },
        "ActionEvent": action_event(),
        "ObservationEvent": observation_event(),
        "UserRejectObservation": reject_event(),
        "AgentErrorEvent": agent_error_event(),
        "Condensation": {
            **_common("Condensation", "environment"),
            "forgotten_event_ids": ["old-1", "old-2"],
            "summary": "Earlier work retained the database requirement.",
            "summary_offset": 0,
            "llm_response_id": "response-2",
        },
        "CondensationRequest": _common("CondensationRequest", "environment"),
        "CondensationSummaryEvent": {
            **_common("CondensationSummaryEvent", "environment"),
            "summary": "Earlier work retained the database requirement.",
        },
        "ConversationStateUpdateEvent": {
            **_common("ConversationStateUpdateEvent", "environment"),
            "key": "execution_status",
            "value": {"status": "running"},
        },
        "ConversationErrorEvent": {
            **_common("ConversationErrorEvent", "environment"),
            "code": "RuntimeError",
            "detail": "The host run stopped.",
        },
        "HookExecutionEvent": {
            **_common("HookExecutionEvent", "hook"),
            "hook_event_type": "PreToolUse",
            "hook_command": "verify-action",
            "tool_name": "terminal",
            "success": True,
            "blocked": False,
            "exit_code": 0,
            "stdout": "approved\n",
            "stderr": "",
            "hook_input": {"tool_name": "terminal"},
        },
        "LLMCompletionLogEvent": {
            **_common("LLMCompletionLogEvent", "environment"),
            "filename": "completion-1.json",
            "log_data": '{"request_id":"request-1"}',
            "model_name": "fake-model",
            "usage_id": "usage-1",
        },
        "ACPToolCallEvent": {
            **_common("ACPToolCallEvent", "agent"),
            "tool_call_id": "acp-call-1",
            "title": "Read source file",
            "status": "completed",
            "tool_kind": "read",
            "raw_input": {"path": "README.md"},
            "raw_output": {"bytes": 10},
            "content": [{"type": "text", "text": "contents"}],
            "is_error": False,
        },
        "StreamingDeltaEvent": {
            **_common("StreamingDeltaEvent", "agent"),
            "content": "partial",
            "reasoning_content": "bounded",
        },
        "TokenEvent": {
            **_common("TokenEvent", "agent"),
            "prompt_token_ids": [1, 2, 3],
            "response_token_ids": [4, 5],
        },
        "PauseEvent": _common("PauseEvent", "user"),
        "InterruptEvent": _common("InterruptEvent", "user"),
    }


def _map(event: dict[str, Any], *, sequence: int = 1, **kwargs: Any):
    return map_host_event(
        event,
        sequence=sequence,
        session_id=SESSION_ID,
        **kwargs,
    )


def _policy(
    *,
    roles: frozenset[str] = frozenset({"tool", "user"}),
    kinds: frozenset[str] = frozenset(
        {"MessageEvent", "ObservationEvent", "TokenEvent"}
    ),
    state_tools: frozenset[str] = frozenset({"terminal"}),
) -> AuthorityPolicy:
    return AuthorityPolicy(
        issuer_secrets={ISSUER: SECRET},
        allowed_roles={ISSUER: roles},
        allowed_event_kinds={ISSUER: kinds},
        trusted_state_tools={ISSUER: state_tools},
    )


def test_supported_event_set_is_exhaustive_and_every_variant_maps() -> None:
    events = valid_events()

    assert SUPPORTED_EVENT_KINDS == EXPECTED_EVENT_KINDS
    assert frozenset(events) == EXPECTED_EVENT_KINDS

    for sequence, kind in enumerate(sorted(EXPECTED_EVENT_KINDS), start=1):
        mapped = _map(events[kind], sequence=sequence)
        host = mapped.metadata["openhands"]
        assert mapped.sequence == sequence
        assert mapped.id == events[kind]["id"]
        assert json.loads(mapped.content) == events[kind]
        assert host["event_kind"] == kind
        assert host["authority"]["authenticated"] is False
        assert mapped.authority == {
            "authenticated": False,
            "trusted_for_state": False,
            "issuer": None,
        }


def test_unknown_kind_and_extra_top_level_fields_fail_closed() -> None:
    unknown = _common("FutureSemanticEvent", "agent")
    with pytest.raises(EventMappingError, match="unsupported OpenHands event kind"):
        _map(unknown)

    extra = valid_events()["MessageEvent"]
    extra["host_says_trusted"] = True
    with pytest.raises(EventMappingError, match="unexpected host_says_trusted"):
        _map(extra)


def test_malformed_common_fields_and_spoofed_fixed_source_fail_closed() -> None:
    bad_timestamp = valid_events()["PauseEvent"]
    bad_timestamp["timestamp"] = "not-a-timestamp"
    with pytest.raises(EventMappingError, match="ISO-8601"):
        _map(bad_timestamp)

    spoofed = action_event()
    spoofed["source"] = "user"
    with pytest.raises(EventMappingError, match="not valid for the pinned event kind"):
        _map(spoofed)

    unsupported_source = valid_events()["MessageEvent"]
    unsupported_source["source"] = "developer"
    with pytest.raises(EventMappingError, match="not an SDK EventSource"):
        _map(unsupported_source)


def test_nested_message_shape_and_role_are_strict() -> None:
    extra = valid_events()["MessageEvent"]
    extra["llm_message"]["host_says_trusted"] = True
    with pytest.raises(EventMappingError, match="unexpected host_says_trusted"):
        _map(extra)

    invalid_role = valid_events()["MessageEvent"]
    invalid_role["llm_message"]["role"] = "developer"
    with pytest.raises(EventMappingError, match="role is unsupported"):
        _map(invalid_role)


def test_unauthenticated_user_and_system_claims_remain_untrusted_assistant_history() -> None:
    user = valid_events()["MessageEvent"]
    user["source"] = "user"
    user["llm_message"]["role"] = "user"
    mapped_user = _map(user)

    system_claim = valid_events()["MessageEvent"]
    system_claim["source"] = "user"
    system_claim["llm_message"]["role"] = "system"
    mapped_system = _map(system_claim, sequence=2)

    for mapped in (mapped_user, mapped_system):
        assert mapped.role == "assistant"
        assert mapped.authority["authenticated"] is False
        record = source_event_to_record(mapped, default_sequence=99)
        assert record.metadata["ctxc_authenticated_authority"] is False
        assert "trusted_for_state" not in record.metadata


def test_independently_authenticated_user_receipt_promotes_only_bound_event() -> None:
    event = valid_events()["MessageEvent"]
    event["source"] = "user"
    event["llm_message"]["role"] = "user"
    receipt = issue_authority_receipt(
        event,
        session_id=SESSION_ID,
        claimed_role="user",
        issuer=ISSUER,
        secret=SECRET,
    )

    mapped = _map(
        event,
        authority_policy=_policy(),
        authority_receipt=receipt,
    )

    assert mapped.role == "user"
    assert mapped.authority == {
        "authenticated": True,
        "trusted_for_state": False,
        "issuer": ISSUER,
    }
    assert mapped.metadata["openhands"]["authority"]["receipt_sha256"]


def test_mutated_or_malformed_authority_receipt_fails_closed() -> None:
    event = valid_events()["MessageEvent"]
    event["source"] = "user"
    event["llm_message"]["role"] = "user"
    receipt = issue_authority_receipt(
        event,
        session_id=SESSION_ID,
        claimed_role="user",
        issuer=ISSUER,
        secret=SECRET,
    )
    changed = copy.deepcopy(event)
    changed["llm_message"]["content"][0]["text"] = "Changed after signing."
    with pytest.raises(EventMappingError, match="event digest mismatch"):
        _map(
            changed,
            authority_policy=_policy(),
            authority_receipt=receipt,
        )

    malformed = {**receipt, "host_says_trusted": True}
    with pytest.raises(EventMappingError, match="unexpected host_says_trusted"):
        _map(
            event,
            authority_policy=_policy(),
            authority_receipt=malformed,
        )


@pytest.mark.parametrize(
    ("tool_name", "observation_kind", "category", "known"),
    [
        ("apply_patch", "ApplyPatchObservation", "file", True),
        ("grep", "GrepObservation", "search", True),
        ("browser", "BrowserObservation", "retrieval", True),
        ("task", "TaskObservation", "delegated", True),
        ("terminal", "TerminalObservation", "generic", True),
        ("third_party_mcp", None, "generic", False),
    ],
)
def test_tool_output_categories_never_self_promote(
    tool_name: str,
    observation_kind: str | None,
    category: str,
    known: bool,
) -> None:
    mapped = _map(
        observation_event(
            tool_name=tool_name,
            observation_kind=observation_kind,
        )
    )
    host = mapped.metadata["openhands"]

    assert categorize_tool_name(tool_name) == category
    assert mapped.role == "tool"
    assert host["tool_name"] == tool_name
    assert host["category"] == category
    assert host["known_tool_name"] is known
    assert host["authority"]["authenticated"] is False
    assert mapped.authority["trusted_for_state"] is False


def test_unknown_tool_cannot_receive_tool_authority() -> None:
    event = observation_event(
        tool_name="third_party_mcp",
        observation_kind=None,
    )
    receipt = issue_authority_receipt(
        event,
        session_id=SESSION_ID,
        claimed_role="tool",
        issuer=ISSUER,
        secret=SECRET,
        tool_name="third_party_mcp",
    )
    with pytest.raises(EventMappingError, match="unknown tool names cannot receive authority"):
        _map(
            event,
            authority_policy=_policy(kinds=frozenset({"ObservationEvent"})),
            authority_receipt=receipt,
        )


def test_agent_error_exposes_tool_name_and_cannot_promote_itself() -> None:
    mapped = _map(agent_error_event(tool_name="terminal"))
    host = mapped.metadata["openhands"]

    assert mapped.role == "tool"
    assert host["tool_name"] == "terminal"
    assert host["known_tool_name"] is True
    assert host["authority"]["authenticated"] is False


def test_atomic_observation_rejection_and_agent_error_pairs_match_exactly() -> None:
    action = action_event()
    for result in (observation_event(), reject_event(), agent_error_event()):
        pair = validate_atomic_event_pair(action, result, session_id=SESSION_ID)
        assert pair.action_id == "action-1"
        assert pair.tool_call_id == "call-1"
        assert pair.tool_name == "terminal"
        assert pair.result_kind == result["kind"]
        assert pair.group_sha256


@pytest.mark.parametrize(
    ("result", "match"),
    [
        (observation_event(tool_call_id="other-call"), "tool_call_id mismatch"),
        (observation_event(action_id="other-action"), "action_id mismatch"),
        (observation_event(tool_name="python"), "tool_name mismatch"),
        (reject_event(tool_name="python"), "tool_name mismatch"),
        (agent_error_event(tool_name="python"), "tool_name mismatch"),
    ],
)
def test_atomic_pair_mismatches_fail_closed(result: dict, match: str) -> None:
    with pytest.raises(EventMappingError, match=match):
        validate_atomic_event_pair(
            action_event(),
            result,
            session_id=SESSION_ID,
        )


@pytest.mark.parametrize(
    "event",
    [
        observation_event(),
        reject_event(),
        agent_error_event(),
    ],
)
def test_result_events_reject_null_tool_name_with_typed_error(event: dict) -> None:
    event["tool_name"] = None

    with pytest.raises(
        EventMappingError,
        match=rf"{event['kind']}\.tool_name must be a string",
    ):
        _map(event)


def test_result_events_reject_null_tool_name_under_python_optimized_mode() -> None:
    script = r'''
from ctxc_openhands.event_map import EventMappingError, validate_host_event_shape

common = {
    "id": "event-1",
    "timestamp": "2026-07-27T12:00:00+00:00",
    "tool_name": None,
    "tool_call_id": "call-1",
}
events = [
    {
        **common,
        "kind": "ObservationEvent",
        "source": "environment",
        "action_id": "action-1",
        "observation": {
            "content": [{"type": "text", "text": "ok"}],
            "is_error": False,
        },
    },
    {
        **common,
        "kind": "UserRejectObservation",
        "source": "environment",
        "action_id": "action-1",
        "rejection_reason": "Denied.",
        "rejection_source": "user",
    },
    {
        **common,
        "kind": "AgentErrorEvent",
        "source": "agent",
        "error": "failed",
    },
]
for event in events:
    expected = f"{event['kind']}.tool_name must be a string"
    try:
        validate_host_event_shape(event)
    except EventMappingError as exc:
        if str(exc) != expected:
            raise SystemExit(f"unexpected typed error: {exc}")
    else:
        raise SystemExit(f"accepted null tool_name for {event['kind']}")
'''
    integration_root = Path(__file__).resolve().parents[1]
    repository_root = integration_root.parents[1]
    env = os.environ.copy()
    python_path = os.pathsep.join(
        (
            str(integration_root / "src"),
            str(repository_root / "src"),
            env.get("PYTHONPATH", ""),
        )
    )
    env["PYTHONPATH"] = python_path

    completed = subprocess.run(
        [sys.executable, "-O", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_agent_error_requires_and_validates_tool_name() -> None:
    missing = agent_error_event()
    del missing["tool_name"]
    with pytest.raises(EventMappingError, match="missing tool_name"):
        _map(missing)

    malformed = agent_error_event()
    malformed["tool_name"] = 7
    with pytest.raises(EventMappingError, match="tool_name must be a string"):
        _map(malformed)


def test_transient_descriptors_are_explicit_and_cannot_carry_authority() -> None:
    for kind in ("StreamingDeltaEvent", "TokenEvent", "CondensationSummaryEvent"):
        descriptor = classify_host_event(kind)
        assert descriptor.transient is True

    token = valid_events()["TokenEvent"]
    receipt = issue_authority_receipt(
        token,
        session_id=SESSION_ID,
        claimed_role="assistant",
        issuer=ISSUER,
        secret=SECRET,
    )
    with pytest.raises(EventMappingError, match="transient events cannot carry authority"):
        _map(
            token,
            authority_policy=_policy(
                roles=frozenset({"assistant"}),
                kinds=frozenset({"TokenEvent"}),
                state_tools=frozenset(),
            ),
            authority_receipt=receipt,
        )


@pytest.mark.parametrize("source", ["agent", "user", "environment", "hook"])
def test_token_event_accepts_every_sdk_source_without_authority_promotion(source: str) -> None:
    event = valid_events()["TokenEvent"]
    event["source"] = source

    mapped = _map(event)

    assert mapped.role == "assistant"
    assert mapped.authority["authenticated"] is False
    assert mapped.metadata["openhands"]["event_source_claim"] == source
    assert mapped.metadata["openhands"]["transient"] is True


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("prompt_token_ids", {"token": 1}, "bounded array"),
        ("prompt_token_ids", [True], "non-negative 32-bit integer"),
        ("prompt_token_ids", [-1], "non-negative 32-bit integer"),
        ("prompt_token_ids", [2**31], "non-negative 32-bit integer"),
        ("response_token_ids", ["1"], "non-negative 32-bit integer"),
        ("response_token_ids", [0] * 10_001, "array"),
    ],
)
def test_token_arrays_are_strict_and_bounded(field: str, value: Any, match: str) -> None:
    event = valid_events()["TokenEvent"]
    event[field] = value
    with pytest.raises(EventMappingError, match=match):
        _map(event)


@pytest.mark.parametrize(
    ("kind", "mutation", "match"),
    [
        (
            "ConversationStateUpdateEvent",
            lambda event: event.__setitem__("key", 1),
            "key must be a string",
        ),
        (
            "ConversationStateUpdateEvent",
            lambda event: event.__setitem__("value", object()),
            "non-JSON value",
        ),
        (
            "Condensation",
            lambda event: event.__setitem__("summary", 1),
            "summary must be a string",
        ),
        (
            "Condensation",
            lambda event: event.__setitem__("forgotten_event_ids", ["old-1", "old-1"]),
            "contains duplicates",
        ),
        (
            "Condensation",
            lambda event: event.__setitem__("forgotten_event_ids", [{}]),
            r"forgotten_event_ids\[0\] must be a string",
        ),
        (
            "Condensation",
            lambda event: event.__setitem__("summary_offset", True),
            "non-negative integer",
        ),
        (
            "UserRejectObservation",
            lambda event: event.__setitem__("rejection_reason", 1),
            "rejection_reason must be a string",
        ),
        (
            "UserRejectObservation",
            lambda event: event.__setitem__("rejection_source", "environment"),
            "rejection_source is unsupported",
        ),
        (
            "HookExecutionEvent",
            lambda event: event.__setitem__("hook_event_type", "UnknownHook"),
            "hook_event_type is unsupported",
        ),
        (
            "HookExecutionEvent",
            lambda event: event.__setitem__("blocked", 0),
            "blocked must be a boolean",
        ),
        (
            "HookExecutionEvent",
            lambda event: event.__setitem__("exit_code", True),
            "signed 32-bit integer",
        ),
        (
            "HookExecutionEvent",
            lambda event: event.__setitem__("stdout", 1),
            "stdout must be a string",
        ),
        (
            "HookExecutionEvent",
            lambda event: event.__setitem__("hook_input", []),
            "hook_input must be an object",
        ),
        (
            "LLMCompletionLogEvent",
            lambda event: event.__setitem__("log_data", 1),
            "log_data must be a string",
        ),
        (
            "LLMCompletionLogEvent",
            lambda event: event.__setitem__("model_name", 1),
            "model_name must be a string",
        ),
        (
            "ACPToolCallEvent",
            lambda event: event.__setitem__("status", 1),
            "status must be a string",
        ),
        (
            "ACPToolCallEvent",
            lambda event: event.__setitem__("content", {}),
            "content must be a bounded array",
        ),
        (
            "ACPToolCallEvent",
            lambda event: event.__setitem__("is_error", 0),
            "is_error must be a boolean",
        ),
        (
            "StreamingDeltaEvent",
            lambda event: event.__setitem__("content", 1),
            "content must be a string",
        ),
    ],
)
def test_variant_payload_types_fail_closed(kind: str, mutation, match: str) -> None:
    event = valid_events()[kind]
    mutation(event)
    with pytest.raises(EventMappingError, match=match):
        _map(event)


@pytest.mark.parametrize(
    ("kind", "field"),
    [
        ("AgentErrorEvent", "tool_name"),
        ("TokenEvent", "prompt_token_ids"),
        ("ConversationStateUpdateEvent", "value"),
        ("HookExecutionEvent", "exit_code"),
        ("LLMCompletionLogEvent", "usage_id"),
        ("ACPToolCallEvent", "is_error"),
    ],
)
def test_serialized_required_variant_fields_cannot_be_omitted(kind: str, field: str) -> None:
    event = valid_events()[kind]
    del event[field]
    with pytest.raises(EventMappingError, match=f"missing {field}"):
        _map(event)
