"""Independent semantic verification for atomic host event groups.

Atomic metadata is an adapter convenience, not evidence.  This module derives
call/result bindings from the immutable canonical host event itself, compares
any declared metadata with those bindings, and verifies complete prefixes
without allowing event-family substitution.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

ATOMIC_GROUP_SCHEMA: Final = "ctxc.openhands.atomic-group.v1"

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+~-]{0,511}")
_DECLARED_ROLES = frozenset({"call", "result", "llm-call", "llm-result"})
_ORDINARY_RESULTS = frozenset(
    {"ObservationEvent", "UserRejectObservation", "AgentErrorEvent"}
)
_PAIR_FAMILIES = (
    frozenset({"call", "result"}),
    frozenset({"llm-call", "llm-result"}),
)


class AtomicBindingError(ValueError):
    """Canonical host events and atomic bindings disagree."""


class IncompleteAtomicBindings(AtomicBindingError):
    """A source prefix contains calls or results without a valid counterpart."""

    def __init__(self, group_ids: Sequence[str], *, detail: str | None = None) -> None:
        self.group_ids = tuple(sorted(set(group_ids)))
        suffix = ", ".join(self.group_ids)
        message = detail or "incomplete atomic tool groups"
        super().__init__(f"{message}: {suffix}")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _identifier(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise AtomicBindingError(f"{label} must be a bounded identifier")
    return value


def _text(value: Any, *, label: str, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or "\x00" in value
    ):
        raise AtomicBindingError(f"{label} must be a non-empty bounded string")
    return value


def _object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise AtomicBindingError(f"{label} must be an object with string fields")
    return dict(value)


def _atomic_group_sha256(*, session_id: str, tool_call_id: str) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "schema": ATOMIC_GROUP_SCHEMA,
                "session_id": session_id,
                "tool_call_id": tool_call_id,
            }
        ).encode("utf-8")
    ).hexdigest()


def _group(
    *,
    session_id: str,
    event_kind: str,
    event_id: str,
    role: str,
    tool_call_id: str,
    tool_name: str | None,
    action_id: str | None,
) -> dict[str, Any]:
    return {
        "group_sha256": _atomic_group_sha256(
            session_id=session_id,
            tool_call_id=tool_call_id,
        ),
        "role": role,
        "tool_call_id": tool_call_id,
        "action_id": action_id,
        "tool_name": tool_name,
        "event_kind": event_kind,
        "event_id": event_id,
    }


def derive_atomic_groups(
    event: Mapping[str, Any],
    *,
    session_id: str,
) -> tuple[dict[str, Any], ...]:
    """Derive atomic semantics solely from one canonical host event."""

    raw = _object(event, label="host event")
    session_id = _identifier(session_id, label="session_id")
    kind = raw.get("kind")
    if not isinstance(kind, str):
        raise AtomicBindingError("host event kind must be a string")
    event_id = _identifier(raw.get("id"), label=f"{kind} id")
    groups: list[dict[str, Any]] = []

    if kind == "ActionEvent":
        tool_call_id = _identifier(
            raw.get("tool_call_id"),
            label="ActionEvent.tool_call_id",
        )
        tool_name = _text(raw.get("tool_name"), label="ActionEvent.tool_name")
        nested = _object(raw.get("tool_call"), label="ActionEvent.tool_call")
        nested_id = _identifier(
            nested.get("id"),
            label="ActionEvent.tool_call.id",
        )
        nested_name = _text(
            nested.get("name"),
            label="ActionEvent.tool_call.name",
        )
        if nested_id != tool_call_id or nested_name != tool_name:
            raise AtomicBindingError(
                "ActionEvent nested tool call does not match its canonical id/name"
            )
        groups.append(
            _group(
                session_id=session_id,
                event_kind=kind,
                event_id=event_id,
                role="call",
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                action_id=event_id,
            )
        )
    elif kind in _ORDINARY_RESULTS:
        tool_call_id = _identifier(
            raw.get("tool_call_id"),
            label=f"{kind}.tool_call_id",
        )
        tool_name = _text(raw.get("tool_name"), label=f"{kind}.tool_name")
        action_id = (
            None
            if kind == "AgentErrorEvent"
            else _identifier(raw.get("action_id"), label=f"{kind}.action_id")
        )
        groups.append(
            _group(
                session_id=session_id,
                event_kind=kind,
                event_id=event_id,
                role="result",
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                action_id=action_id,
            )
        )
    elif kind == "MessageEvent":
        message = _object(raw.get("llm_message"), label="MessageEvent.llm_message")
        message_role = message.get("role")
        if message_role == "assistant":
            tool_calls = message.get("tool_calls")
            if tool_calls is not None:
                if not isinstance(tool_calls, list):
                    raise AtomicBindingError(
                        "assistant MessageEvent.tool_calls must be an array or null"
                    )
                for index, value in enumerate(tool_calls):
                    tool_call = _object(
                        value,
                        label=f"MessageEvent.tool_calls[{index}]",
                    )
                    tool_call_id = _identifier(
                        tool_call.get("id"),
                        label=f"MessageEvent.tool_calls[{index}].id",
                    )
                    tool_name = _text(
                        tool_call.get("name"),
                        label=f"MessageEvent.tool_calls[{index}].name",
                    )
                    groups.append(
                        _group(
                            session_id=session_id,
                            event_kind=kind,
                            event_id=event_id,
                            role="llm-call",
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            action_id=None,
                        )
                    )
        elif message_role == "tool":
            tool_call_id = _identifier(
                message.get("tool_call_id"),
                label="tool MessageEvent.tool_call_id",
            )
            tool_name = _text(
                message.get("name"),
                label="tool MessageEvent.name",
            )
            groups.append(
                _group(
                    session_id=session_id,
                    event_kind=kind,
                    event_id=event_id,
                    role="llm-result",
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    action_id=None,
                )
            )

    seen: set[tuple[str, str]] = set()
    for group in groups:
        identity = (group["group_sha256"], group["role"])
        if identity in seen:
            raise AtomicBindingError(
                "one host event contains duplicate atomic group roles"
            )
        seen.add(identity)
    return tuple(groups)


def _declared_atomic_groups(metadata: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw_metadata = _object(metadata, label="SourceRecord metadata")
    host = raw_metadata.get("openhands")
    if host is None:
        return ()
    host_metadata = _object(host, label="SourceRecord metadata.openhands")
    values: list[Any] = []
    single = host_metadata.get("atomic_group")
    if single is not None:
        values.append(single)
    many = host_metadata.get("atomic_groups")
    if many is not None:
        if not isinstance(many, list):
            raise AtomicBindingError("openhands atomic_groups must be an array")
        values.extend(many)

    groups: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        raw = _object(value, label=f"declared atomic group {index}")
        role = raw.get("role")
        if role not in _DECLARED_ROLES:
            raise AtomicBindingError(f"declared atomic group {index} role is unsupported")
        action_value = raw.get("action_id")
        action_id = (
            None
            if action_value is None
            else _identifier(
                action_value,
                label=f"declared atomic group {index}.action_id",
            )
        )
        tool_name_present = "tool_name" in raw
        tool_name_value = raw.get("tool_name")
        tool_name = (
            None
            if tool_name_value is None
            else _text(
                tool_name_value,
                label=f"declared atomic group {index}.tool_name",
            )
        )
        groups.append(
            {
                "group_sha256": _identifier(
                    raw.get("group_sha256"),
                    label=f"declared atomic group {index}.group_sha256",
                ),
                "role": role,
                "tool_call_id": _identifier(
                    raw.get("tool_call_id"),
                    label=f"declared atomic group {index}.tool_call_id",
                ),
                "action_id": action_id,
                "tool_name_present": tool_name_present,
                "tool_name": tool_name,
            }
        )
    return tuple(groups)


def bind_atomic_groups(
    event: Mapping[str, Any],
    metadata: Mapping[str, Any],
    *,
    session_id: str,
) -> tuple[dict[str, Any], ...]:
    """Derive groups and reject any contradictory declared metadata."""

    expected = derive_atomic_groups(event, session_id=session_id)
    declared = _declared_atomic_groups(metadata)
    if not declared:
        return expected
    if len(declared) != len(expected):
        raise AtomicBindingError(
            "declared atomic metadata count differs from canonical host event"
        )
    for index, (claim, derived) in enumerate(zip(declared, expected, strict=True)):
        for field in ("group_sha256", "role", "tool_call_id", "action_id"):
            if claim[field] != derived[field]:
                raise AtomicBindingError(
                    f"declared atomic group {index} {field} "
                    "differs from canonical host event"
                )
        if claim["tool_name_present"] and claim["tool_name"] != derived["tool_name"]:
            raise AtomicBindingError(
                f"declared atomic group {index} tool_name "
                "differs from canonical host event"
            )
    return expected


def validate_stored_atomic_groups(
    event: Mapping[str, Any],
    stored_groups: Any,
    *,
    session_id: str,
) -> tuple[dict[str, Any], ...]:
    """Re-derive and compare the immutable internal group representation."""

    if not isinstance(stored_groups, list):
        raise AtomicBindingError("stored atomic groups must be an array")
    expected = derive_atomic_groups(event, session_id=session_id)
    if stored_groups != list(expected):
        raise AtomicBindingError(
            "stored atomic groups differ from canonical host event semantics"
        )
    return expected


def _validate_complete_group(
    group_members: Sequence[tuple[int, Mapping[str, Any]]],
) -> bool:
    by_role: dict[str, tuple[int, Mapping[str, Any]]] = {}
    for ordinal, group in group_members:
        role = group["role"]
        if role in by_role:
            raise AtomicBindingError("atomic group contains a duplicate role")
        by_role[role] = (ordinal, group)
    roles = frozenset(by_role)
    if roles not in _PAIR_FAMILIES:
        if roles.issubset(_PAIR_FAMILIES[0]) or roles.issubset(_PAIR_FAMILIES[1]):
            return False
        raise AtomicBindingError("atomic group mixes incompatible event families")

    if roles == _PAIR_FAMILIES[0]:
        call_ordinal, call = by_role["call"]
        result_ordinal, result = by_role["result"]
        if call["event_kind"] != "ActionEvent":
            raise AtomicBindingError(
                "ordinary atomic call must derive from ActionEvent"
            )
        if result["event_kind"] not in _ORDINARY_RESULTS:
            raise AtomicBindingError(
                "ordinary atomic result has an unsupported event kind"
            )
        if call["action_id"] != call["event_id"]:
            raise AtomicBindingError("ActionEvent atomic action id is not self-bound")
        if result["event_kind"] == "AgentErrorEvent":
            if result["action_id"] is not None:
                raise AtomicBindingError(
                    "AgentErrorEvent must not synthesize an action id"
                )
        elif result["action_id"] != call["action_id"]:
            raise AtomicBindingError("atomic pair action_id mismatch")
    else:
        call_ordinal, call = by_role["llm-call"]
        result_ordinal, result = by_role["llm-result"]
        if (
            call["event_kind"] != "MessageEvent"
            or result["event_kind"] != "MessageEvent"
            or call["action_id"] is not None
            or result["action_id"] is not None
        ):
            raise AtomicBindingError("LLM atomic pair has invalid event semantics")

    if call_ordinal >= result_ordinal:
        raise AtomicBindingError("atomic result must follow its call")
    if call["tool_call_id"] != result["tool_call_id"]:
        raise AtomicBindingError("atomic pair tool_call_id mismatch")
    if call["tool_name"] != result["tool_name"]:
        raise AtomicBindingError("atomic pair tool_name mismatch")
    return True


def validate_atomic_pair(
    call_entry: tuple[int, Mapping[str, Any], Any],
    result_entry: tuple[int, Mapping[str, Any], Any],
    *,
    session_id: str,
) -> None:
    """Validate one targeted call/result pair without requiring sibling calls."""

    call_ordinal, call_event, call_stored_groups = call_entry
    result_ordinal, result_event, result_stored_groups = result_entry
    call_groups = validate_stored_atomic_groups(
        call_event, call_stored_groups, session_id=session_id
    )
    result_groups = validate_stored_atomic_groups(
        result_event, result_stored_groups, session_id=session_id
    )
    if len(result_groups) != 1 or result_groups[0]["role"] not in {
        "result",
        "llm-result",
    }:
        raise AtomicBindingError("atomic pair requires exactly one result role")
    result = result_groups[0]
    expected_call_role = "call" if result["role"] == "result" else "llm-call"
    matching_calls = tuple(
        group
        for group in call_groups
        if group["group_sha256"] == result["group_sha256"]
        and group["role"] == expected_call_role
    )
    if len(matching_calls) != 1:
        raise AtomicBindingError("atomic result requires exactly one matching call")
    if not _validate_complete_group(
        ((call_ordinal, matching_calls[0]), (result_ordinal, result))
    ):
        raise AtomicBindingError("atomic call/result pair is incomplete")


def validate_atomic_prefix(
    entries: Sequence[tuple[int, Mapping[str, Any], Any]],
    *,
    session_id: str,
) -> None:
    """Require exact, ordered, same-family call/result pairs in one prefix."""

    members: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for ordinal, event, stored_groups in entries:
        if (
            isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or ordinal < 0
        ):
            raise AtomicBindingError("atomic prefix ordinal must be non-negative")
        groups = validate_stored_atomic_groups(
            event,
            stored_groups,
            session_id=session_id,
        )
        for group in groups:
            members.setdefault(group["group_sha256"], []).append((ordinal, group))

    incomplete: list[str] = []
    for group_id, group_members in members.items():
        if not _validate_complete_group(group_members):
            incomplete.append(group_id)

    if incomplete:
        raise IncompleteAtomicBindings(incomplete)


__all__ = [
    "ATOMIC_GROUP_SCHEMA",
    "AtomicBindingError",
    "IncompleteAtomicBindings",
    "bind_atomic_groups",
    "derive_atomic_groups",
    "validate_atomic_pair",
    "validate_atomic_prefix",
    "validate_stored_atomic_groups",
]
