"""Non-throwing host callback with fail-closed dispatch poisoning."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .authority import canonical_event_bytes, object_as_mapping
from .session import OpenHandsSession


class IntegrationPoisonedError(RuntimeError):
    """A prior callback failure requires exact EventLog reconciliation."""


@dataclass(frozen=True, slots=True)
class CallbackFailure:
    """Bounded diagnostic retained after a callback refuses an event."""

    ordinal: int
    event_id: str | None
    event_kind: str | None
    event_sha256: str | None
    error_type: str
    error: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "event_id": self.event_id,
            "event_kind": self.event_kind,
            "event_sha256": self.event_sha256,
            "error_type": self.error_type,
            "error": self.error,
        }


def _exception_type_name(exc: BaseException) -> str:
    try:
        value = type(exc).__name__
    except BaseException:
        return "BaseException"
    return value[:128] if isinstance(value, str) and value else "BaseException"


def _bounded_error(exc: BaseException) -> str:
    try:
        value = str(exc)
    except BaseException:
        return "<exception string unavailable>"
    if len(value) > 2048:
        return value[:2045] + "..."
    return value


def _bounded_identity(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:512]


def _event_identity(event: Any) -> tuple[str | None, str | None, str | None]:
    event_id: str | None = None
    event_kind: str | None = None
    event_sha: str | None = None
    try:
        raw = object_as_mapping(event, label="OpenHands callback event")
        event_id = _bounded_identity(raw.get("id"))
        event_kind = _bounded_identity(raw.get("kind"))
        event_sha = hashlib.sha256(canonical_event_bytes(raw)).hexdigest()
    except BaseException:
        pass
    return event_id, event_kind, event_sha


class DurableEventCallback:
    """Callback safe to install before OpenHands' own persistence callback.

    It never raises into the host.  A rejected event poisons subsequent model
    dispatch and source ingestion until the operator reconciles the complete
    persisted host EventLog in exact order.
    """

    def __init__(
        self,
        session: OpenHandsSession,
        *,
        event_validator: Callable[[Any], None] | None = None,
    ) -> None:
        if not isinstance(session, OpenHandsSession):
            raise TypeError("session must be an OpenHandsSession")
        self.session = session
        if event_validator is not None and not callable(event_validator):
            raise TypeError("event_validator must be callable or null")
        self._lock = threading.RLock()
        self._failure: CallbackFailure | None = None
        self._callback_ordinal = 0

        self._event_validator = event_validator

    @property
    def failure(self) -> CallbackFailure | None:
        with self._lock:
            return self._failure

    @property
    def poisoned(self) -> bool:
        return self.failure is not None

    def _record_failure(self, event: Any, exc: BaseException) -> None:
        if self._failure is not None:
            return
        try:
            event_id, event_kind, event_sha = _event_identity(event)
            error_type = _exception_type_name(exc)
            error = _bounded_error(exc)
        except BaseException:
            event_id, event_kind, event_sha = None, None, None
            error_type = "BaseException"
            error = "<failure diagnostic unavailable>"
        self._failure = CallbackFailure(
            ordinal=self._callback_ordinal,
            event_id=event_id,
            event_kind=event_kind,
            event_sha256=event_sha,
            error_type=error_type,
            error=error,
        )

    def __call__(self, event: Any) -> None:
        with self._lock:
            self._callback_ordinal += 1
            if self._failure is not None:
                return
            try:
                event_id, _kind, event_sha = _event_identity(event)
                suffix = event_id or event_sha or f"unidentified-{self._callback_ordinal}"
                if self._event_validator is not None:
                    self._event_validator(event)
                self.session.ingest(
                    event,
                    request_id=f"host-callback:{suffix}",
                )
            except BaseException as exc:
                # This callback executes before OpenHands' default EventLog
                # persistence callback. Propagating would prevent host
                # persistence and make exact reconciliation impossible.
                self._record_failure(event, exc)

    def assert_dispatch_allowed(self) -> None:
        failure = self.failure
        if failure is not None:
            raise IntegrationPoisonedError(
                "CtxC callback rejected host event "
                f"{failure.event_id or '<unknown>'} ({failure.error_type}: "
                f"{failure.error}); reconcile the exact persisted EventLog "
                "before another model request"
            )

    def reconcile(self, persisted_events: Sequence[Any]) -> int:
        """Replay the complete persisted host EventLog and clear poison on success."""

        if isinstance(persisted_events, (str, bytes, bytearray)) or not isinstance(
            persisted_events, Sequence
        ):
            raise TypeError("persisted_events must be an ordered sequence")
        with self._lock:
            for sequence, event in enumerate(persisted_events):
                if self._event_validator is not None:
                    self._event_validator(event)
                event_id, _kind, event_sha = _event_identity(event)
                suffix = event_id or event_sha or f"unidentified-{sequence}"
                self.session.ingest(
                    event,
                    request_id=f"host-reconcile:{sequence}:{suffix}",
                    sequence=sequence,
                )
            snapshot = self.session.store.snapshot(self.session.session_id)
            if snapshot.source_count != len(persisted_events):
                raise IntegrationPoisonedError(
                    "reconciled source count differs from the persisted EventLog"
                )
            self._failure = None
            return snapshot.source_count

    def status(self) -> dict[str, Any]:
        failure = self.failure
        return {
            "poisoned": failure is not None,
            "callback_count": self._callback_ordinal,
            "failure": None if failure is None else failure.to_dict(),
        }


__all__ = [
    "CallbackFailure",
    "DurableEventCallback",
    "IntegrationPoisonedError",
]
