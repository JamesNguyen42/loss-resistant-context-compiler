from __future__ import annotations

import hashlib
import json

from ctxc_openhands.replay import replay_final_request
from ctxc_openhands.request_ledger import build_final_request_ledger
from ctxc_openhands.tokenizer import CanonicalUtf8ByteTokenizer


def transport_parts() -> list[dict[str, str]]:
    return [
        {"category": "provider_framing", "text": "{"},
        {"category": "prompts", "text": '"prompt":"p",'},
        {"category": "verified_memory", "text": '"memory":"m",'},
        {"category": "recent_tail", "text": '"tail":"t",'},
        {"category": "current_turn", "text": '"turn":"c",'},
        {"category": "retrieval", "text": '"retrieval":"r",'},
        {"category": "attachments", "text": '"attachments":[], '},
        {"category": "tool_schemas", "text": '"tools":[{"name":"x"}]'},
        {"category": "provider_framing", "text": "}"},
    ]


def ledger():
    return build_final_request_ledger(
        transport_parts=transport_parts(),
        model="offline-fake-model",
        path="/fake",
        session_id="replay-session",
        generation_id="replay-generation",
        active_epoch=4,
        source_head_sha256="a" * 64,
        semantic_result_digest="b" * 64,
        reserved_output_tokens=32,
        safety_margin_tokens=8,
        hard_limit_tokens=2048,
        tokenizer=CanonicalUtf8ByteTokenizer(),
    )


def _rehash(value: dict) -> None:
    body = {key: item for key, item in value.items() if key != "ledger_sha256"}
    canonical = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    value["ledger_sha256"] = hashlib.sha256(canonical).hexdigest()


def test_synchronous_replay_is_deterministic_and_complete() -> None:
    request_ledger = ledger()
    tokenizer = CanonicalUtf8ByteTokenizer()

    first = replay_final_request(request_ledger, tokenizer=tokenizer)
    second = replay_final_request(request_ledger, tokenizer=tokenizer)

    assert first == second
    assert first.passed
    assert first.issues == ()
    assert request_ledger["version"] == 2
    assert request_ledger["bindings"]["session_id"] == "replay-session"
    assert request_ledger["bindings"]["generation_id"] == "replay-generation"
    assert request_ledger["bindings"]["active_epoch"] == 4
    assert first.total_tokens == sum(dict(first.component_counts).values())
    assert first.tokenizer_identity == request_ledger["tokenizer"]["identity"]
    assert (
        first.tokenizer_vector_sha256
        == request_ledger["tokenizer"]["vector_sha256"]
    )
    assert first.tool_schema_sha256 == request_ledger["tool_schema_sha256"]
    assert first.final_request_sha256 == request_ledger["final_request_sha256"]
    assert first.ledger_sha256 == request_ledger["ledger_sha256"]
    assert first.to_dict()["passed"] is True


def test_standalone_replay_rejects_missing_extra_malformed_and_v1_bindings() -> None:
    cases = (
        (
            "missing session",
            "bindings is missing fields: session_id",
            lambda value: value["bindings"].pop("session_id"),
        ),
        (
            "legacy generation alias",
            "bindings has unknown fields: generation",
            lambda value: value["bindings"].update({"generation": 4}),
        ),
        (
            "malformed session",
            "bindings.session_id has an invalid identifier",
            lambda value: value["bindings"].update({"session_id": "bad session"}),
        ),
        (
            "empty generation",
            "bindings.generation_id must not be empty",
            lambda value: value["bindings"].update({"generation_id": ""}),
        ),
        (
            "zero epoch",
            "bindings.active_epoch must be a positive signed 64-bit integer",
            lambda value: value["bindings"].update({"active_epoch": 0}),
        ),
        (
            "boolean epoch",
            "bindings.active_epoch must be a positive signed 64-bit integer",
            lambda value: value["bindings"].update({"active_epoch": True}),
        ),
        (
            "legacy version",
            "unsupported ledger version",
            lambda value: value.update({"version": 1}),
        ),
    )

    for label, expected_issue, mutate in cases:
        value = ledger().to_dict()
        mutate(value)
        _rehash(value)

        report = replay_final_request(
            value,
            tokenizer=CanonicalUtf8ByteTokenizer(),
        )

        assert not report.passed, label
        assert expected_issue in report.issues, label


def test_supplied_identical_final_parts_replay_and_changed_request_fails() -> None:
    request_ledger = ledger()
    tokenizer = CanonicalUtf8ByteTokenizer()

    exact = replay_final_request(
        request_ledger,
        tokenizer=tokenizer,
        transport_parts=transport_parts(),
    )
    changed_parts = transport_parts()
    changed_parts[4]["text"] = '"turn":"changed",'
    changed = replay_final_request(
        request_ledger,
        tokenizer=tokenizer,
        transport_parts=changed_parts,
    )

    assert exact.passed
    assert not changed.passed
    assert changed.final_request_sha256 != request_ledger["final_request_sha256"]
    assert any("digest mismatch" in issue for issue in changed.issues)
    assert any("component count mismatch" in issue for issue in changed.issues)


def test_supplied_parts_cannot_hide_malformed_stored_parts() -> None:
    value = ledger().to_dict()
    value["transport"]["parts"][0]["unknown"] = "fail closed"

    report = replay_final_request(
        value,
        tokenizer=CanonicalUtf8ByteTokenizer(),
        transport_parts=transport_parts(),
    )

    assert not report.passed
    assert any("unknown fields" in issue for issue in report.issues)
    assert any("self-hash mismatch" in issue for issue in report.issues)


def test_tampered_ledger_remains_failed_with_recomputed_values() -> None:
    value = ledger().to_dict()
    value["transport"]["parts"][1]["tokens"] += 1

    report = replay_final_request(value, tokenizer=CanonicalUtf8ByteTokenizer())

    assert not report.passed
    assert report.total_tokens is not None
    assert report.final_request_sha256 is not None
    assert report.ledger_sha256 is not None
    assert any("tokens mismatch" in issue for issue in report.issues)
    assert any("self-hash mismatch" in issue for issue in report.issues)


class UnsupportedExactTokenizer:
    identity = "not-registered"
    exact_scope = "unknown"
    accounting_mode = "exact"
    vector_sha256 = "0" * 64

    def count_text(self, text: str) -> int:
        return len(text)

    def count_parts(self, parts: tuple[str, ...]) -> tuple[int, ...]:
        return tuple(len(part) for part in parts)


def test_replay_reports_unsupported_tokenizer_without_raising() -> None:
    report = replay_final_request(ledger(), tokenizer=UnsupportedExactTokenizer())

    assert not report.passed
    assert report.total_tokens is None
    assert any("not supported" in issue for issue in report.issues)


def test_replay_reports_lone_surrogates_without_raising() -> None:
    for location in ("model", "path", "transport-part"):
        value = ledger().to_dict()
        if location == "model":
            value["bindings"]["model"] = "\ud800"
        elif location == "path":
            value["bindings"]["path"] = "\ud800"
        else:
            value["transport"]["parts"][0]["text"] = "\ud800"

        report = replay_final_request(value, tokenizer=CanonicalUtf8ByteTokenizer())

        assert report.passed is False
        assert any("valid UTF-8" in issue for issue in report.issues)
