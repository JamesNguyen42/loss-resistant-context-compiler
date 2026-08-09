"""Independent authority receipts for the pinned OpenHands adapter.

OpenHands ``source`` and message ``role`` fields are claims made by the host
event.  They are deliberately not treated as authentication.  A deployment
that wants to promote such a claim must verify a receipt produced outside the
adapter with a separately configured secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

AUTHORITY_RECEIPT_SCHEMA = "ctxc.openhands.authority-receipt.v1"
AUTHORITY_ALGORITHM = "hmac-sha256"
MAX_CANONICAL_JSON_BYTES = 1_048_576
MAX_JSON_DEPTH = 32
MAX_JSON_ITEMS = 50_000
MAX_JSON_COLLECTION_ITEMS = 10_000
MAX_JSON_STRING_CHARS = 262_144
MIN_AUTHORITY_SECRET_BYTES = 32

AUTHORITY_ROLES = frozenset({"assistant", "developer", "system", "tool", "user"})
TRUSTED_STATE_EVENT_KINDS = frozenset({"ObservationEvent"})

_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "algorithm",
        "event_sha256",
        "event_id",
        "event_kind",
        "session_id",
        "claimed_role",
        "issuer",
        "tool_name",
        "trusted_for_state",
        "mac",
    }
)
_HEX_CHARS = frozenset("0123456789abcdef")


class AuthorityError(ValueError):
    """Base class for authority configuration, issuance, and verification errors."""


class AuthorityVerificationError(AuthorityError):
    """An authority receipt failed closed."""


@dataclass(frozen=True, slots=True)
class VerifiedAuthority:
    """A verified, event-bound authority result."""

    issuer: str
    claimed_role: str
    event_kind: str
    event_id: str
    session_id: str
    event_sha256: str
    tool_name: str | None
    trusted_for_state: bool
    receipt_sha256: str

    def connector_envelope(self) -> dict[str, Any]:
        """Return the narrow authority envelope accepted by the core connector."""

        return {
            "authenticated": True,
            "trusted_for_state": self.trusted_for_state,
            "issuer": self.issuer,
        }


def _strict_text(value: Any, *, label: str, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise AuthorityError(f"{label} must be a string")
    if not value or value != value.strip():
        raise AuthorityError(f"{label} must be non-empty without surrounding whitespace")
    if len(value) > maximum:
        raise AuthorityError(f"{label} exceeds {maximum} characters")
    if any(ord(character) < 0x20 for character in value):
        raise AuthorityError(f"{label} must not contain control characters")
    return value


def _secret_bytes(secret: Any) -> bytes:
    if not isinstance(secret, bytes):
        raise AuthorityError("authority secret must be bytes")
    if len(secret) < MIN_AUTHORITY_SECRET_BYTES:
        raise AuthorityError(
            f"authority secret must contain at least {MIN_AUTHORITY_SECRET_BYTES} bytes"
        )
    return secret


def _json_value(
    value: Any,
    *,
    depth: int,
    counter: list[int],
    label: str,
) -> Any:
    if depth > MAX_JSON_DEPTH:
        raise AuthorityError(f"{label} exceeds the maximum JSON depth")
    counter[0] += 1
    if counter[0] > MAX_JSON_ITEMS:
        raise AuthorityError(f"{label} exceeds the maximum JSON item count")

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AuthorityError(f"{label} contains a non-finite number")
        return value
    if isinstance(value, str):
        if len(value) > MAX_JSON_STRING_CHARS:
            raise AuthorityError(f"{label} contains an oversized string")
        return value
    if isinstance(value, Mapping):
        if len(value) > MAX_JSON_COLLECTION_ITEMS:
            raise AuthorityError(f"{label} contains an oversized object")
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise AuthorityError(f"{label} object keys must be strings")
            if key in normalized:
                raise AuthorityError(f"{label} contains a duplicate object key")
            normalized[key] = _json_value(
                item,
                depth=depth + 1,
                counter=counter,
                label=label,
            )
        return normalized
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_JSON_COLLECTION_ITEMS:
            raise AuthorityError(f"{label} contains an oversized array")
        return [
            _json_value(
                item,
                depth=depth + 1,
                counter=counter,
                label=label,
            )
            for item in value
        ]
    raise AuthorityError(f"{label} contains a non-JSON value: {type(value).__name__}")


def object_as_mapping(value: Any, *, label: str = "value") -> dict[str, Any]:
    """Return a detached JSON-object view without importing OpenHands.

    Pydantic v2 models use ``model_dump(mode="json", exclude_none=True)``.
    Plain mappings and small test/fake-runtime objects are also supported.
    """

    if isinstance(value, Mapping):
        raw: Any = dict(value)
    else:
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                raw = model_dump(mode="json", exclude_none=True)
            except TypeError:
                raw = model_dump()
        else:
            to_dict = getattr(value, "to_dict", None)
            if callable(to_dict):
                raw = to_dict()
            elif hasattr(value, "__dict__"):
                raw = {key: item for key, item in vars(value).items() if not key.startswith("_")}
            else:
                raise AuthorityError(f"{label} must be a mapping or expose model_dump()/to_dict()")
        if isinstance(raw, Mapping) and "kind" not in raw:
            kind = getattr(value, "kind", None)
            if isinstance(kind, str):
                raw = dict(raw)
                raw["kind"] = kind
    if not isinstance(raw, Mapping):
        raise AuthorityError(f"{label} serialization must produce an object")
    normalized = _json_value(raw, depth=0, counter=[0], label=label)
    if not isinstance(normalized, dict):  # pragma: no cover - guarded above
        raise AuthorityError(f"{label} serialization must produce an object")
    return normalized


def canonical_json_bytes(value: Any, *, label: str = "value") -> bytes:
    """Encode bounded canonical JSON suitable for hashing and HMAC input."""

    normalized = _json_value(value, depth=0, counter=[0], label=label)
    try:
        encoded = json.dumps(
            normalized,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:  # defensive normalization boundary
        raise AuthorityError(f"{label} is not canonical JSON: {exc}") from exc
    if len(encoded) > MAX_CANONICAL_JSON_BYTES:
        raise AuthorityError(f"{label} canonical JSON exceeds {MAX_CANONICAL_JSON_BYTES} bytes")
    return encoded


def canonical_event_bytes(event: Any) -> bytes:
    """Return the exact bounded event bytes bound by an authority receipt."""

    return canonical_json_bytes(object_as_mapping(event, label="host event"), label="host event")


def host_event_sha256(event: Any) -> str:
    """Hash an exact canonical host event."""

    return hashlib.sha256(canonical_event_bytes(event)).hexdigest()


def _event_identity(event: Any) -> tuple[dict[str, Any], str, str]:
    raw = object_as_mapping(event, label="host event")
    event_id = _strict_text(raw.get("id"), label="host event id")
    event_kind = _strict_text(raw.get("kind"), label="host event kind", maximum=128)
    return raw, event_id, event_kind


def _receipt_body(
    *,
    event_sha256: str,
    event_id: str,
    event_kind: str,
    session_id: str,
    claimed_role: str,
    issuer: str,
    tool_name: str | None,
    trusted_for_state: bool,
) -> dict[str, Any]:
    return {
        "schema": AUTHORITY_RECEIPT_SCHEMA,
        "algorithm": AUTHORITY_ALGORITHM,
        "event_sha256": event_sha256,
        "event_id": event_id,
        "event_kind": event_kind,
        "session_id": session_id,
        "claimed_role": claimed_role,
        "issuer": issuer,
        "tool_name": tool_name,
        "trusted_for_state": trusted_for_state,
    }


def issue_authority_receipt(
    event: Any,
    *,
    session_id: str,
    claimed_role: str,
    issuer: str,
    secret: bytes,
    tool_name: str | None = None,
    trusted_for_state: bool = False,
) -> dict[str, Any]:
    """Host-side helper that signs one exact event.

    The adapter never calls this function automatically.  Keeping issuance
    explicit makes the trust boundary reviewable and prevents host literals
    from silently becoming authority.
    """

    raw, event_id, event_kind = _event_identity(event)
    session_id = _strict_text(session_id, label="session_id")
    issuer = _strict_text(issuer, label="issuer")
    claimed_role = _strict_text(claimed_role, label="claimed_role", maximum=32)
    if claimed_role not in AUTHORITY_ROLES:
        raise AuthorityError(f"unsupported authority role: {claimed_role!r}")
    if tool_name is not None:
        tool_name = _strict_text(tool_name, label="tool_name", maximum=256)
    if not isinstance(trusted_for_state, bool):
        raise AuthorityError("trusted_for_state must be a boolean")
    if claimed_role != "tool" and tool_name is not None:
        raise AuthorityError("tool_name is only valid for a tool authority receipt")
    if trusted_for_state and (claimed_role != "tool" or tool_name is None):
        raise AuthorityError("trusted_for_state requires a tool claim with an explicit tool_name")
    body = _receipt_body(
        event_sha256=hashlib.sha256(canonical_json_bytes(raw, label="host event")).hexdigest(),
        event_id=event_id,
        event_kind=event_kind,
        session_id=session_id,
        claimed_role=claimed_role,
        issuer=issuer,
        tool_name=tool_name,
        trusted_for_state=trusted_for_state,
    )
    mac = hmac.new(
        _secret_bytes(secret),
        canonical_json_bytes(body, label="authority receipt body"),
        hashlib.sha256,
    ).hexdigest()
    return {**body, "mac": mac}


def _frozen_string_sets(
    value: Mapping[str, Any],
    *,
    label: str,
    allowed_values: frozenset[str] | None = None,
) -> Mapping[str, frozenset[str]]:
    if not isinstance(value, Mapping):
        raise AuthorityError(f"{label} must be a mapping")
    frozen: dict[str, frozenset[str]] = {}
    for raw_issuer, raw_items in value.items():
        issuer = _strict_text(raw_issuer, label=f"{label} issuer")
        if not isinstance(raw_items, (set, frozenset, list, tuple)):
            raise AuthorityError(f"{label}[{issuer!r}] must be a collection")
        items: set[str] = set()
        for raw_item in raw_items:
            item = _strict_text(raw_item, label=f"{label}[{issuer!r}] item", maximum=256)
            if allowed_values is not None and item not in allowed_values:
                raise AuthorityError(f"{label}[{issuer!r}] contains unsupported value {item!r}")
            items.add(item)
        frozen[issuer] = frozenset(items)
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True)
class AuthorityPolicy:
    """Adapter-side issuer, role, event-kind, and state-tool allowlists."""

    issuer_secrets: Mapping[str, bytes] = field(repr=False)
    allowed_roles: Mapping[str, frozenset[str]]
    allowed_event_kinds: Mapping[str, frozenset[str]]
    trusted_state_tools: Mapping[str, frozenset[str]]

    def __post_init__(self) -> None:
        if not isinstance(self.issuer_secrets, Mapping) or not self.issuer_secrets:
            raise AuthorityError("issuer_secrets must be a non-empty mapping")
        secrets: dict[str, bytes] = {}
        for raw_issuer, raw_secret in self.issuer_secrets.items():
            issuer = _strict_text(raw_issuer, label="issuer_secrets issuer")
            secrets[issuer] = _secret_bytes(raw_secret)
        roles = _frozen_string_sets(
            self.allowed_roles,
            label="allowed_roles",
            allowed_values=AUTHORITY_ROLES,
        )
        kinds = _frozen_string_sets(
            self.allowed_event_kinds,
            label="allowed_event_kinds",
        )
        state_tools = _frozen_string_sets(
            self.trusted_state_tools,
            label="trusted_state_tools",
        )
        issuer_names = frozenset(secrets)
        for label, configured in (
            ("allowed_roles", roles),
            ("allowed_event_kinds", kinds),
            ("trusted_state_tools", state_tools),
        ):
            unknown = frozenset(configured).difference(issuer_names)
            missing = issuer_names.difference(configured)
            if unknown or missing:
                raise AuthorityError(f"{label} issuer set must exactly match issuer_secrets")
        object.__setattr__(self, "issuer_secrets", MappingProxyType(secrets))
        object.__setattr__(self, "allowed_roles", roles)
        object.__setattr__(self, "allowed_event_kinds", kinds)
        object.__setattr__(self, "trusted_state_tools", state_tools)


def _receipt_mapping(receipt: Any) -> dict[str, Any]:
    try:
        raw = object_as_mapping(receipt, label="authority receipt")
    except AuthorityError as exc:
        raise AuthorityVerificationError(str(exc)) from exc
    fields = frozenset(raw)
    if fields != _RECEIPT_FIELDS:
        missing = sorted(_RECEIPT_FIELDS.difference(fields))
        extra = sorted(fields.difference(_RECEIPT_FIELDS))
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unexpected " + ", ".join(extra))
        raise AuthorityVerificationError(
            "authority receipt has invalid fields: " + "; ".join(detail)
        )
    return raw


def verify_authority_receipt(
    event: Any,
    receipt: Any,
    *,
    session_id: str,
    policy: AuthorityPolicy,
) -> VerifiedAuthority:
    """Verify one receipt with constant-time MAC comparison and strict bindings."""

    if not isinstance(policy, AuthorityPolicy):
        raise AuthorityVerificationError("policy must be an AuthorityPolicy")
    try:
        raw_event, event_id, event_kind = _event_identity(event)
        expected_session = _strict_text(session_id, label="session_id")
    except AuthorityError as exc:
        raise AuthorityVerificationError(str(exc)) from exc
    raw = _receipt_mapping(receipt)
    if raw["schema"] != AUTHORITY_RECEIPT_SCHEMA:
        raise AuthorityVerificationError("unsupported authority receipt schema")
    if raw["algorithm"] != AUTHORITY_ALGORITHM:
        raise AuthorityVerificationError("unsupported authority receipt algorithm")
    try:
        issuer = _strict_text(raw["issuer"], label="receipt issuer")
        claimed_role = _strict_text(
            raw["claimed_role"],
            label="receipt claimed_role",
            maximum=32,
        )
        receipt_event_id = _strict_text(raw["event_id"], label="receipt event_id")
        receipt_kind = _strict_text(
            raw["event_kind"],
            label="receipt event_kind",
            maximum=128,
        )
        receipt_session = _strict_text(raw["session_id"], label="receipt session_id")
        event_sha = _strict_text(
            raw["event_sha256"],
            label="receipt event_sha256",
            maximum=64,
        )
        mac = _strict_text(raw["mac"], label="receipt mac", maximum=64)
        tool_name = raw["tool_name"]
        if tool_name is not None:
            tool_name = _strict_text(tool_name, label="receipt tool_name", maximum=256)
    except AuthorityError as exc:
        raise AuthorityVerificationError(str(exc)) from exc
    if (
        len(event_sha) != 64
        or not set(event_sha).issubset(_HEX_CHARS)
        or len(mac) != 64
        or not set(mac).issubset(_HEX_CHARS)
    ):
        raise AuthorityVerificationError("receipt digests must be lowercase SHA-256 hex")
    trusted_for_state = raw["trusted_for_state"]
    if not isinstance(trusted_for_state, bool):
        raise AuthorityVerificationError("receipt trusted_for_state must be a boolean")
    if claimed_role not in AUTHORITY_ROLES:
        raise AuthorityVerificationError("receipt claimed_role is unsupported")
    if issuer not in policy.issuer_secrets:
        raise AuthorityVerificationError("receipt issuer is not configured")

    body = {key: raw[key] for key in raw if key != "mac"}
    expected_mac = hmac.new(
        policy.issuer_secrets[issuer],
        canonical_json_bytes(body, label="authority receipt body"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_mac, mac):
        raise AuthorityVerificationError("authority receipt MAC verification failed")

    actual_event_sha = hashlib.sha256(
        canonical_json_bytes(raw_event, label="host event")
    ).hexdigest()
    if not hmac.compare_digest(actual_event_sha, event_sha):
        raise AuthorityVerificationError("authority receipt event digest mismatch")
    if not hmac.compare_digest(receipt_event_id, event_id):
        raise AuthorityVerificationError("authority receipt event id mismatch")
    if not hmac.compare_digest(receipt_kind, event_kind):
        raise AuthorityVerificationError("authority receipt event kind mismatch")
    if not hmac.compare_digest(receipt_session, expected_session):
        raise AuthorityVerificationError("authority receipt session mismatch")
    if claimed_role not in policy.allowed_roles[issuer]:
        raise AuthorityVerificationError("authority role is not allowed for issuer")
    if event_kind not in policy.allowed_event_kinds[issuer]:
        raise AuthorityVerificationError("event kind is not allowed for issuer")
    if claimed_role != "tool" and tool_name is not None:
        raise AuthorityVerificationError("non-tool authority receipt carries tool_name")
    if trusted_for_state:
        if claimed_role != "tool" or tool_name is None:
            raise AuthorityVerificationError(
                "trusted_for_state requires a tool claim and tool_name"
            )
        if event_kind not in TRUSTED_STATE_EVENT_KINDS:
            raise AuthorityVerificationError(
                "trusted_for_state is only valid for a completed observation"
            )
        if tool_name not in policy.trusted_state_tools[issuer]:
            raise AuthorityVerificationError("tool is not allowlisted for trusted state")
    receipt_sha = hashlib.sha256(canonical_json_bytes(raw, label="authority receipt")).hexdigest()
    return VerifiedAuthority(
        issuer=issuer,
        claimed_role=claimed_role,
        event_kind=event_kind,
        event_id=event_id,
        session_id=expected_session,
        event_sha256=actual_event_sha,
        tool_name=tool_name,
        trusted_for_state=trusted_for_state,
        receipt_sha256=receipt_sha,
    )


__all__ = [
    "AUTHORITY_ALGORITHM",
    "AUTHORITY_RECEIPT_SCHEMA",
    "AUTHORITY_ROLES",
    "AuthorityError",
    "AuthorityPolicy",
    "AuthorityVerificationError",
    "MAX_CANONICAL_JSON_BYTES",
    "TRUSTED_STATE_EVENT_KINDS",
    "VerifiedAuthority",
    "canonical_event_bytes",
    "canonical_json_bytes",
    "host_event_sha256",
    "issue_authority_receipt",
    "object_as_mapping",
    "verify_authority_receipt",
]
