from __future__ import annotations

import copy
import pprint

import pytest

from ctxc_openhands.authority import (
    AuthorityError,
    AuthorityPolicy,
    AuthorityVerificationError,
    canonical_event_bytes,
    issue_authority_receipt,
    verify_authority_receipt,
)

SECRET = b"independent-host-authority-key!" * 2
SESSION_ID = "conversation-7"
ISSUER = "test-auth-gateway"


def message_event() -> dict:
    return {
        "kind": "MessageEvent",
        "id": "message-1",
        "timestamp": "2026-07-27T12:00:00+00:00",
        "source": "user",
        "llm_message": {
            "role": "user",
            "content": [{"type": "text", "text": "Keep PostgreSQL."}],
        },
    }


def observation_event(*, tool_name: str = "terminal") -> dict:
    return {
        "kind": "ObservationEvent",
        "id": "observation-1",
        "timestamp": "2026-07-27T12:00:01+00:00",
        "source": "environment",
        "tool_name": tool_name,
        "tool_call_id": "call-1",
        "action_id": "action-1",
        "observation": {
            "kind": "TerminalObservation",
            "content": [{"type": "text", "text": "ok"}],
            "is_error": False,
        },
    }


def policy(
    *,
    roles: frozenset[str] = frozenset({"tool", "user"}),
    kinds: frozenset[str] = frozenset({"MessageEvent", "ObservationEvent"}),
    state_tools: frozenset[str] = frozenset({"terminal"}),
) -> AuthorityPolicy:
    return AuthorityPolicy(
        issuer_secrets={ISSUER: SECRET},
        allowed_roles={ISSUER: roles},
        allowed_event_kinds={ISSUER: kinds},
        trusted_state_tools={ISSUER: state_tools},
    )


def test_policy_rendering_redacts_issuer_secrets() -> None:
    configured = policy()
    renderings = (
        repr(configured),
        str(configured),
        pprint.pformat(configured),
        repr(RuntimeError(configured)),
        str(RuntimeError(configured)),
    )

    secret_text = SECRET.decode("ascii")
    recognizable_fragments = (
        secret_text,
        "independent-host-authority-key",
        repr(SECRET),
        SECRET.hex(),
    )
    for rendering in renderings:
        assert all(fragment not in rendering for fragment in recognizable_fragments)

    safe_rendering = repr(configured)
    assert safe_rendering.startswith("AuthorityPolicy(")
    assert f"'{ISSUER}'" in safe_rendering
    assert "allowed_roles=" in safe_rendering
    assert "allowed_event_kinds=" in safe_rendering
    assert "trusted_state_tools=" in safe_rendering
    assert "issuer_secrets=" not in safe_rendering


def test_receipt_round_trip_binds_exact_event_and_identity() -> None:
    event = message_event()
    receipt = issue_authority_receipt(
        event,
        session_id=SESSION_ID,
        claimed_role="user",
        issuer=ISSUER,
        secret=SECRET,
    )

    verified = verify_authority_receipt(
        event,
        receipt,
        session_id=SESSION_ID,
        policy=policy(),
    )

    assert verified.claimed_role == "user"
    assert verified.event_id == event["id"]
    assert verified.event_kind == event["kind"]
    assert verified.trusted_for_state is False
    assert verified.connector_envelope() == {
        "authenticated": True,
        "trusted_for_state": False,
        "issuer": ISSUER,
    }
    assert canonical_event_bytes(event) == canonical_event_bytes(
        dict(reversed(list(event.items())))
    )


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda event: event["llm_message"]["content"][0].__setitem__("text", "changed"), "digest"),
        (lambda event: event.__setitem__("id", "message-2"), "digest"),
        (lambda event: event.__setitem__("source", "agent"), "digest"),
    ],
)
def test_receipt_rejects_content_identity_and_source_mutation(
    mutation,
    match: str,
) -> None:
    event = message_event()
    receipt = issue_authority_receipt(
        event,
        session_id=SESSION_ID,
        claimed_role="user",
        issuer=ISSUER,
        secret=SECRET,
    )
    mutated = copy.deepcopy(event)
    mutation(mutated)

    with pytest.raises(AuthorityVerificationError, match=match):
        verify_authority_receipt(
            mutated,
            receipt,
            session_id=SESSION_ID,
            policy=policy(),
        )


def test_receipt_rejects_session_replay_and_bad_mac() -> None:
    event = message_event()
    receipt = issue_authority_receipt(
        event,
        session_id=SESSION_ID,
        claimed_role="user",
        issuer=ISSUER,
        secret=SECRET,
    )
    with pytest.raises(AuthorityVerificationError, match="session mismatch"):
        verify_authority_receipt(
            event,
            receipt,
            session_id="other-conversation",
            policy=policy(),
        )

    tampered = copy.deepcopy(receipt)
    tampered["mac"] = "0" * 64
    with pytest.raises(AuthorityVerificationError, match="MAC verification"):
        verify_authority_receipt(
            event,
            tampered,
            session_id=SESSION_ID,
            policy=policy(),
        )


def test_receipt_fields_algorithm_and_lowercase_digests_are_strict() -> None:
    event = message_event()
    receipt = issue_authority_receipt(
        event,
        session_id=SESSION_ID,
        claimed_role="user",
        issuer=ISSUER,
        secret=SECRET,
    )
    extra = {**receipt, "host_says_trusted": True}
    with pytest.raises(AuthorityVerificationError, match="unexpected"):
        verify_authority_receipt(
            event,
            extra,
            session_id=SESSION_ID,
            policy=policy(),
        )
    upper = {**receipt, "mac": receipt["mac"].upper()}
    with pytest.raises(AuthorityVerificationError, match="lowercase"):
        verify_authority_receipt(
            event,
            upper,
            session_id=SESSION_ID,
            policy=policy(),
        )


def test_policy_separately_enforces_issuer_role_and_event_kind() -> None:
    event = message_event()
    receipt = issue_authority_receipt(
        event,
        session_id=SESSION_ID,
        claimed_role="user",
        issuer=ISSUER,
        secret=SECRET,
    )
    with pytest.raises(AuthorityVerificationError, match="role is not allowed"):
        verify_authority_receipt(
            event,
            receipt,
            session_id=SESSION_ID,
            policy=policy(roles=frozenset({"tool"})),
        )
    with pytest.raises(AuthorityVerificationError, match="kind is not allowed"):
        verify_authority_receipt(
            event,
            receipt,
            session_id=SESSION_ID,
            policy=policy(kinds=frozenset({"ObservationEvent"})),
        )


def test_trusted_state_requires_exact_allowlisted_observation_tool() -> None:
    event = observation_event()
    receipt = issue_authority_receipt(
        event,
        session_id=SESSION_ID,
        claimed_role="tool",
        issuer=ISSUER,
        secret=SECRET,
        tool_name="terminal",
        trusted_for_state=True,
    )
    verified = verify_authority_receipt(
        event,
        receipt,
        session_id=SESSION_ID,
        policy=policy(),
    )
    assert verified.trusted_for_state is True

    with pytest.raises(AuthorityVerificationError, match="not allowlisted"):
        verify_authority_receipt(
            event,
            receipt,
            session_id=SESSION_ID,
            policy=policy(state_tools=frozenset({"file_editor"})),
        )

    action = {**event, "kind": "ActionEvent"}
    action_receipt = issue_authority_receipt(
        action,
        session_id=SESSION_ID,
        claimed_role="tool",
        issuer=ISSUER,
        secret=SECRET,
        tool_name="terminal",
        trusted_for_state=True,
    )
    with pytest.raises(AuthorityVerificationError, match="completed observation"):
        verify_authority_receipt(
            action,
            action_receipt,
            session_id=SESSION_ID,
            policy=policy(kinds=frozenset({"ActionEvent"})),
        )


def test_issuance_never_infers_authority_and_rejects_invalid_requests() -> None:
    event = message_event()
    with pytest.raises(AuthorityError, match="at least"):
        issue_authority_receipt(
            event,
            session_id=SESSION_ID,
            claimed_role="user",
            issuer=ISSUER,
            secret=b"too-short",
        )
    with pytest.raises(AuthorityError, match="trusted_for_state"):
        issue_authority_receipt(
            event,
            session_id=SESSION_ID,
            claimed_role="user",
            issuer=ISSUER,
            secret=SECRET,
            trusted_for_state=True,
        )


def test_canonical_event_is_bounded_json_only() -> None:
    with pytest.raises(AuthorityError, match="non-JSON"):
        canonical_event_bytes({"kind": "MessageEvent", "bad": object()})
    with pytest.raises(AuthorityError, match="oversized string"):
        canonical_event_bytes(
            {
                "kind": "MessageEvent",
                "content": "x" * 262_145,
            }
        )


def test_policy_configuration_is_exact_and_defensively_frozen() -> None:
    secrets = {ISSUER: SECRET}
    configured = policy()
    secrets[ISSUER] = b"x" * 32
    assert configured.issuer_secrets[ISSUER] == SECRET
    with pytest.raises(TypeError):
        configured.allowed_roles[ISSUER] = frozenset({"assistant"})
    with pytest.raises(AuthorityError, match="issuer set"):
        AuthorityPolicy(
            issuer_secrets={ISSUER: SECRET},
            allowed_roles={},
            allowed_event_kinds={ISSUER: frozenset({"MessageEvent"})},
            trusted_state_tools={ISSUER: frozenset()},
        )
