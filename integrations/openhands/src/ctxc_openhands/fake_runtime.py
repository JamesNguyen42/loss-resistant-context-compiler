"""Deterministic offline runtime used to validate the integration boundary.

The fake runtime is not OpenHands and is never presented as a live
demonstration.  It exists to exercise exact request accounting, immutable
dispatch, replay, and crash-safe storage without a model or paid service.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .replay import RequestReplayReport, replay_final_request
from .request_ledger import (
    REQUIRED_COMPONENT_CATEGORIES,
    FinalRequestLedger,
    build_final_request_ledger,
)
from .session import OpenHandsSession
from .tokenizer import CanonicalUtf8ByteTokenizer

FAKE_RUNTIME_MODEL = "ctxc-openhands-offline-fake-runtime@1"
FAKE_RUNTIME_PATH = "/offline/v1/final-request"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _component(category: str, value: Any) -> str:
    return _canonical_json({"component": category, "value": value}) + "\n"


@dataclass(frozen=True, slots=True)
class FakeFinalRequest:
    """One immutable, exactly counted fake-runtime request."""

    request_id: str
    model: str
    path: str
    _parts: tuple[tuple[str, str], ...]
    ledger: FinalRequestLedger

    @property
    def transport_payload(self) -> str:
        return "".join(text for _category, text in self._parts)

    @property
    def transport_parts(self) -> tuple[dict[str, str], ...]:
        return tuple(
            {"category": category, "text": text}
            for category, text in self._parts
        )

    @property
    def final_request_sha256(self) -> str:
        return str(self.ledger["final_request_sha256"])

    @property
    def total_tokens(self) -> int:
        value = self.ledger["accounting"]["total_tokens"]
        if isinstance(value, bool) or not isinstance(value, int):
            raise RuntimeError("validated exact ledger lost its integer total")
        return value

    def replay(self) -> RequestReplayReport:
        return replay_final_request(
            self.ledger,
            tokenizer=CanonicalUtf8ByteTokenizer(),
            transport_parts=self.transport_parts,
        )


@dataclass(frozen=True, slots=True)
class FakeDispatchReceipt:
    """Deterministic evidence that the immutable fake request was replayed."""

    request_id: str
    final_request_sha256: str
    response_sha256: str
    total_tokens: int
    component_counts: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "final_request_sha256": self.final_request_sha256,
            "response_sha256": self.response_sha256,
            "total_tokens": self.total_tokens,
            "component_counts": dict(self.component_counts),
        }


class OfflineFakeRuntime:
    """Build and dispatch the canonical-UTF-8 fake request protocol."""

    def __init__(self, session: OpenHandsSession) -> None:
        if not isinstance(session, OpenHandsSession):
            raise TypeError("session must be an OpenHandsSession")
        self.session = session
        self.tokenizer = CanonicalUtf8ByteTokenizer()

    def prepare(
        self,
        *,
        request_id: str,
        current_turn: str,
        prompts: Sequence[str] = (),
        retrieval: Sequence[Mapping[str, Any]] = (),
        attachments: Sequence[Mapping[str, Any]] = (),
        tool_schemas: Sequence[Mapping[str, Any]] = (),
        reserved_output_tokens: int = 1024,
        safety_margin_tokens: int = 256,
        hard_limit_tokens: int = 2_000_000,
    ) -> FakeFinalRequest:
        if not isinstance(request_id, str) or not request_id:
            raise TypeError("request_id must be a non-empty string")
        if not isinstance(current_turn, str):
            raise TypeError("current_turn must be a string")
        active = self.session.active()
        if active is None:
            raise RuntimeError("an active verified generation is required")
        verified_memory = {
            "bundle_sha256": active.bundle.bundle_sha256,
            "certificate": active.bundle.certificate,
            "trusted_memory": active.bundle.trusted_memory,
        }
        recent_tail = [record.to_dict() for record in active.tail]
        values: dict[str, Any] = {
            "prompts": list(prompts),
            "verified_memory": verified_memory,
            "recent_tail": recent_tail,
            "current_turn": current_turn,
            "retrieval": list(retrieval),
            "attachments": list(attachments),
            "tool_schemas": list(tool_schemas),
            "provider_framing": {
                "encoding": "canonical-jsonl-utf8",
                "model": FAKE_RUNTIME_MODEL,
                "path": FAKE_RUNTIME_PATH,
                "protocol": "ctxc-openhands-offline-final-request@1",
            },
        }
        parts = tuple(
            (category, _component(category, values[category]))
            for category in REQUIRED_COMPONENT_CATEGORIES
        )
        ledger = build_final_request_ledger(
            transport_parts=tuple(
                {"category": category, "text": text}
                for category, text in parts
            ),
            model=FAKE_RUNTIME_MODEL,
            path=FAKE_RUNTIME_PATH,
            session_id=active.session_id,
            generation_id=active.generation_id,
            active_epoch=active.active_epoch,
            source_head_sha256=active.source_head_sha256,
            semantic_result_digest=active.semantic_result_digest,
            reserved_output_tokens=reserved_output_tokens,
            safety_margin_tokens=safety_margin_tokens,
            hard_limit_tokens=hard_limit_tokens,
            tokenizer=self.tokenizer,
        )
        return FakeFinalRequest(
            request_id=request_id,
            model=FAKE_RUNTIME_MODEL,
            path=FAKE_RUNTIME_PATH,
            _parts=parts,
            ledger=ledger,
        )

    def dispatch(self, request: FakeFinalRequest) -> FakeDispatchReceipt:
        """Replay and persist the exact immutable ledger before fake dispatch."""

        if not isinstance(request, FakeFinalRequest):
            raise TypeError("request must be a FakeFinalRequest")
        replay = request.replay()
        if not replay.passed:
            raise RuntimeError(
                "immutable final-request replay failed: " + "; ".join(replay.issues)
            )
        if replay.total_tokens is None:
            raise RuntimeError(
                "successful immutable final-request replay omitted total_tokens"
            )
        active = self.session.active()
        if active is None:
            raise RuntimeError("active generation disappeared before dispatch")
        self.session.store.store_request_ledger(
            session_id=self.session.session_id,
            generation_id=active.generation_id,
            request_id=request.request_id,
            ledger=request.ledger,
            tokenizer=self.tokenizer,
        )
        response = _canonical_json(
            {
                "fake": True,
                "request_id": request.request_id,
                "request_sha256": request.final_request_sha256,
                "warning": "offline fake-runtime result; not a live OpenHands run",
            }
        )
        return FakeDispatchReceipt(
            request_id=request.request_id,
            final_request_sha256=request.final_request_sha256,
            response_sha256=hashlib.sha256(response.encode("utf-8")).hexdigest(),
            total_tokens=replay.total_tokens,
            component_counts=replay.component_counts,
        )


__all__ = [
    "FAKE_RUNTIME_MODEL",
    "FAKE_RUNTIME_PATH",
    "FakeDispatchReceipt",
    "FakeFinalRequest",
    "OfflineFakeRuntime",
]
