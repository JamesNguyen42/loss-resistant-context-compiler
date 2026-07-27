from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import FrozenInstanceError

import pytest

import ctxc_openhands.request_ledger as request_ledger_module
from ctxc_openhands.request_ledger import (
    REQUIRED_COMPONENT_CATEGORIES,
    FinalRequestLedger,
    RequestLedgerError,
    RequestLedgerLimits,
    build_final_request_ledger,
    validate_final_request_ledger,
)
from ctxc_openhands.tokenizer import CanonicalUtf8ByteTokenizer


class _EntriesMapping(Mapping[object, object]):
    def __init__(self, entries: list[tuple[object, object]]) -> None:
        self._entries = entries

    def __getitem__(self, key: object) -> object:
        for candidate, value in self._entries:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[object]:
        return (key for key, _value in self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def items(self) -> Iterator[tuple[object, object]]:
        return iter(self._entries)


class _ExplodingMapping(_EntriesMapping):
    def items(self) -> Iterator[tuple[object, object]]:
        raise RuntimeError("mapping inspection trap")


class _ControlledSequence(Sequence[object]):
    def __init__(self, values: list[object], *, explode: bool = False) -> None:
        self._values = values
        self._explode = explode

    def __getitem__(self, index: int | slice) -> object:
        return self._values[index]

    def __len__(self) -> int:
        raise RuntimeError("sequence length trap")

    def __iter__(self) -> Iterator[object]:
        if self._explode:
            raise RuntimeError("sequence iteration trap")
        return iter(self._values)


def transport_parts() -> list[dict[str, str]]:
    return [
        {"category": "provider_framing", "text": '{"model":"fake","messages":['},
        {"category": "prompts", "text": '{"role":"system","content":"safe"},'},
        {"category": "verified_memory", "text": '{"role":"system","content":"memory"},'},
        {"category": "recent_tail", "text": '{"role":"user","content":"tail"},'},
        {"category": "current_turn", "text": '{"role":"user","content":"now"},'},
        {"category": "retrieval", "text": '{"role":"user","content":"evidence"},'},
        {"category": "attachments", "text": '{"role":"user","content":"file"},'},
        {"category": "tool_schemas", "text": '"tools":[{"name":"read"}]'},
        {"category": "provider_framing", "text": "]}"},
    ]


def build_ledger(
    *,
    parts: Sequence[Mapping[str, str]] | None = None,
    reserved: int = 64,
    margin: int = 16,
    hard_limit: int = 4096,
    limits: RequestLedgerLimits | None = None,
    session_id: str = "session-7",
    generation_id: str = "generation-7",
    active_epoch: int = 7,
) -> FinalRequestLedger:
    return build_final_request_ledger(
        transport_parts=transport_parts() if parts is None else parts,
        model="offline-fake-model",
        path="/fake/v1/chat/completions",
        session_id=session_id,
        generation_id=generation_id,
        active_epoch=active_epoch,
        source_head_sha256="1" * 64,
        semantic_result_digest="2" * 64,
        reserved_output_tokens=reserved,
        safety_margin_tokens=margin,
        hard_limit_tokens=hard_limit,
        tokenizer=CanonicalUtf8ByteTokenizer(),
        limits=limits,
    )


def test_builds_bounded_self_hashed_exact_ledger() -> None:
    ledger = build_ledger()
    value = ledger.to_dict()
    accounting = value["accounting"]

    assert value["version"] == 2
    assert value["accounting_mode"] == "exact"
    assert value["bindings"]["session_id"] == "session-7"
    assert value["bindings"]["generation_id"] == "generation-7"
    assert value["bindings"]["active_epoch"] == 7
    assert value["bindings"]["source_head_sha256"] == "1" * 64
    assert value["bindings"]["semantic_result_digest"] == "2" * 64
    assert set(accounting["component_counts"]) == set(REQUIRED_COMPONENT_CATEGORIES)
    assert sum(accounting["component_counts"].values()) == accounting["total_tokens"]
    assert accounting["occupied_tokens"] == accounting["total_tokens"] + 64 + 16
    assert value["transport"]["byte_length"] == accounting["total_tokens"]
    assert len(value["tool_schema_sha256"]) == 64
    assert len(value["final_request_sha256"]) == 64
    assert ledger.ledger_sha256 == value["ledger_sha256"]
    assert validate_final_request_ledger(
        ledger, tokenizer=CanonicalUtf8ByteTokenizer()
    ) is ledger


def test_ledger_is_deep_detached_from_inputs_and_returned_copies() -> None:
    parts = transport_parts()
    ledger = build_ledger(parts=parts)
    before = ledger.canonical_bytes

    parts[0]["text"] = "mutated"
    copy = ledger.to_dict()
    copy["transport"]["parts"][0]["text"] = "also mutated"

    assert ledger.canonical_bytes == before
    assert ledger.to_dict()["transport"]["parts"][0]["text"].startswith('{"model"')
    with pytest.raises(FrozenInstanceError):
        ledger._canonical = b"changed"  # type: ignore[misc]


def test_deterministic_build_produces_identical_canonical_ledger() -> None:
    first = build_ledger()
    second = build_ledger()

    assert first.canonical_bytes == second.canonical_bytes
    assert first.ledger_sha256 == second.ledger_sha256


def test_generation_bindings_do_not_change_exact_request_digest_or_counts() -> None:
    first = build_ledger()
    rebound = build_ledger(
        session_id="session-8",
        generation_id="generation-8",
        active_epoch=8,
    )

    assert first["final_request_sha256"] == rebound["final_request_sha256"]
    assert first["accounting"] == rebound["accounting"]
    assert first["tool_schema_sha256"] == rebound["tool_schema_sha256"]
    assert first["ledger_sha256"] != rebound["ledger_sha256"]


def test_missing_category_and_unknown_part_field_fail_closed() -> None:
    missing = [
        part for part in transport_parts() if part["category"] != "attachments"
    ]
    with pytest.raises(RequestLedgerError, match="missing required categories"):
        build_ledger(parts=missing)

    unknown: list[dict[str, str]] = transport_parts()
    unknown[0]["extra"] = "no"
    with pytest.raises(RequestLedgerError, match="unknown fields"):
        build_ledger(parts=unknown)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("active_epoch", True),
        ("active_epoch", 0),
        ("reserved_output_tokens", -1),
        ("safety_margin_tokens", True),
        ("hard_limit_tokens", 0),
    ],
)
def test_bool_negative_and_zero_counts_are_rejected(field: str, value: object) -> None:
    kwargs: dict[str, object] = {
        "transport_parts": transport_parts(),
        "model": "offline-fake-model",
        "path": "/fake",
        "session_id": "session-1",
        "generation_id": "generation-1",
        "active_epoch": 1,
        "source_head_sha256": "1" * 64,
        "semantic_result_digest": "2" * 64,
        "reserved_output_tokens": 1,
        "safety_margin_tokens": 1,
        "hard_limit_tokens": 4096,
        "tokenizer": CanonicalUtf8ByteTokenizer(),
    }
    kwargs[field] = value

    with pytest.raises(RequestLedgerError):
        build_final_request_ledger(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_id", ""),
        ("session_id", "session with spaces"),
        ("generation_id", "generation\n1"),
        ("generation_id", "g" * 513),
    ],
)
def test_binding_identifiers_are_strict(field: str, value: str) -> None:
    kwargs: dict[str, object] = {
        "transport_parts": transport_parts(),
        "model": "offline-fake-model",
        "path": "/fake",
        "session_id": "session-1",
        "generation_id": "generation-1",
        "active_epoch": 1,
        "source_head_sha256": "1" * 64,
        "semantic_result_digest": "2" * 64,
        "reserved_output_tokens": 1,
        "safety_margin_tokens": 1,
        "hard_limit_tokens": 4096,
        "tokenizer": CanonicalUtf8ByteTokenizer(),
    }
    kwargs[field] = value

    with pytest.raises(RequestLedgerError, match=field):
        build_final_request_ledger(**kwargs)  # type: ignore[arg-type]


def test_accounting_counts_are_bounded_to_signed_63_bit_values() -> None:
    with pytest.raises(RequestLedgerError, match="no greater than"):
        build_final_request_ledger(
            transport_parts=transport_parts(),
            model="offline-fake-model",
            path="/fake",
            session_id="session-1",
            generation_id="generation-1",
            active_epoch=1 << 63,
            source_head_sha256="1" * 64,
            semantic_result_digest="2" * 64,
            reserved_output_tokens=1,
            safety_margin_tokens=1,
            hard_limit_tokens=4096,
            tokenizer=CanonicalUtf8ByteTokenizer(),
        )


@pytest.mark.parametrize("target", ["transport part", "model"])
def test_invalid_utf8_text_fails_inside_the_typed_ledger_boundary(
    target: str,
) -> None:
    parts = transport_parts()
    model = "offline-fake-model"
    if target == "transport part":
        parts[0]["text"] = "\ud800"
    else:
        model = "\ud800"

    with pytest.raises(RequestLedgerError, match="valid UTF-8"):
        build_final_request_ledger(
            transport_parts=parts,
            model=model,
            path="/fake",
            session_id="session-1",
            generation_id="generation-1",
            active_epoch=1,
            source_head_sha256="1" * 64,
            semantic_result_digest="2" * 64,
            reserved_output_tokens=1,
            safety_margin_tokens=1,
            hard_limit_tokens=4096,
            tokenizer=CanonicalUtf8ByteTokenizer(),
        )


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        (RequestLedgerLimits(max_ledger_bytes=64), "64-byte limit"),
        (RequestLedgerLimits(max_json_items=4), "item limit"),
        (RequestLedgerLimits(max_json_depth=1), "depth limit"),
    ],
)
def test_validation_applies_bounds_before_canonical_json_allocation(
    monkeypatch: pytest.MonkeyPatch,
    limits: RequestLedgerLimits,
    message: str,
) -> None:
    value = build_ledger().to_dict()

    def forbidden_canonical_allocation(_value: object) -> bytes:
        raise AssertionError("canonical JSON allocation ran before the bound")

    monkeypatch.setattr(
        request_ledger_module,
        "_canonical_json_bytes",
        forbidden_canonical_allocation,
    )

    with pytest.raises(RequestLedgerError, match=message):
        validate_final_request_ledger(
            value,
            tokenizer=CanonicalUtf8ByteTokenizer(),
            limits=limits,
        )


def test_validation_rejects_duplicate_non_string_and_surrogate_keys() -> None:
    baseline = build_ledger().to_dict()
    duplicate = _EntriesMapping(
        [*baseline.items(), ("schema", baseline["schema"])]
    )
    with pytest.raises(RequestLedgerError, match="duplicate key"):
        validate_final_request_ledger(
            duplicate,
            tokenizer=CanonicalUtf8ByteTokenizer(),
        )

    non_string = _EntriesMapping([(1, "not-a-field")])
    with pytest.raises(RequestLedgerError, match="field names must be strings"):
        validate_final_request_ledger(
            non_string,
            tokenizer=CanonicalUtf8ByteTokenizer(),
        )

    surrogate = build_ledger().to_dict()
    surrogate["\ud800"] = None
    with pytest.raises(RequestLedgerError, match="valid UTF-8"):
        validate_final_request_ledger(
            surrogate,
            tokenizer=CanonicalUtf8ByteTokenizer(),
        )


def test_validation_normalizes_mapping_and_sequence_traps() -> None:
    with pytest.raises(RequestLedgerError, match="mapping could not be inspected"):
        validate_final_request_ledger(
            _ExplodingMapping([]),
            tokenizer=CanonicalUtf8ByteTokenizer(),
        )

    value = build_ledger().to_dict()
    value["transport"]["parts"] = _ControlledSequence([], explode=True)
    with pytest.raises(RequestLedgerError, match="array could not be inspected"):
        validate_final_request_ledger(
            value,
            tokenizer=CanonicalUtf8ByteTokenizer(),
        )


def test_construction_uses_bounded_iteration_not_sequence_length() -> None:
    ledger = build_ledger(
        parts=_ControlledSequence(transport_parts())  # type: ignore[arg-type]
    )

    assert ledger["accounting_mode"] == "exact"


def test_construction_normalizes_mapping_and_sequence_traps() -> None:
    with pytest.raises(RequestLedgerError, match="array could not be inspected"):
        build_ledger(
            parts=_ControlledSequence(  # type: ignore[arg-type]
                transport_parts(),
                explode=True,
            )
        )

    parts: list[Mapping[str, str]] = transport_parts()
    parts[0] = _ExplodingMapping([])  # type: ignore[assignment]
    with pytest.raises(RequestLedgerError, match="mapping could not be inspected"):
        build_ledger(parts=parts)


def test_construction_rejects_duplicate_and_non_string_part_keys() -> None:
    duplicate_parts: list[Mapping[str, str]] = transport_parts()
    duplicate_parts[0] = _EntriesMapping(  # type: ignore[assignment]
        [
            ("category", "provider_framing"),
            ("text", "first"),
            ("text", "second"),
        ]
    )
    with pytest.raises(RequestLedgerError, match="duplicate key"):
        build_ledger(parts=duplicate_parts)

    non_string_parts: list[Mapping[str, str]] = transport_parts()
    non_string_parts[0] = _EntriesMapping(  # type: ignore[assignment]
        [("category", "provider_framing"), ("text", "safe"), (7, "bad")]
    )
    with pytest.raises(RequestLedgerError, match="field names must be strings"):
        build_ledger(parts=non_string_parts)


@pytest.mark.parametrize(
    "location",
    ("model", "path", "transport-part"),
)
def test_validation_normalizes_lone_surrogates_to_ledger_error(location: str) -> None:
    value = build_ledger().to_dict()
    if location == "model":
        value["bindings"]["model"] = "\ud800"
    elif location == "path":
        value["bindings"]["path"] = "\ud800"
    else:
        value["transport"]["parts"][0]["text"] = "\ud800"

    with pytest.raises(RequestLedgerError, match="valid UTF-8"):
        validate_final_request_ledger(
            value,
            tokenizer=CanonicalUtf8ByteTokenizer(),
        )


def test_validation_rejects_out_of_range_claimed_counts() -> None:
    value = build_ledger().to_dict()
    value["accounting"]["reserved_output_tokens"] = 1 << 63

    with pytest.raises(RequestLedgerError, match="reserved_output_tokens"):
        validate_final_request_ledger(
            value,
            tokenizer=CanonicalUtf8ByteTokenizer(),
        )


def test_validation_wraps_recursive_input_in_typed_ledger_error() -> None:
    value = build_ledger().to_dict()
    recursive: dict[str, object] = {}
    recursive["self"] = recursive
    value["transport"]["parts"][0]["text"] = recursive

    with pytest.raises(RequestLedgerError, match="cycle or shared container"):
        validate_final_request_ledger(
            value,
            tokenizer=CanonicalUtf8ByteTokenizer(),
        )


def test_hard_limit_overflow_is_refused() -> None:
    baseline = build_ledger(reserved=0, margin=0)
    total = baseline.to_dict()["accounting"]["total_tokens"]

    with pytest.raises(RequestLedgerError, match="exceeds hard token limit"):
        build_ledger(reserved=1, margin=1, hard_limit=total + 1)


def test_transport_and_ledger_size_bounds_are_enforced() -> None:
    parts = transport_parts()
    with pytest.raises(RequestLedgerError, match="transport payload exceeds"):
        build_ledger(
            parts=parts,
            limits=RequestLedgerLimits(max_transport_bytes=10),
        )

    with pytest.raises(RequestLedgerError, match="ledger exceeds"):
        build_ledger(
            parts=parts,
            limits=RequestLedgerLimits(
                max_transport_bytes=4096,
                max_ledger_bytes=100,
            ),
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unknown": 1}),
        lambda value: value.update({"version": 1}),
        lambda value: value["accounting"].update({"total_tokens": True}),
        lambda value: value["accounting"]["component_counts"].update({"prompts": -1}),
        lambda value: value["transport"]["parts"][0].update({"text": "tampered"}),
        lambda value: value["bindings"].update({"semantic_result_digest": "bad"}),
        lambda value: value["bindings"].update({"generation": 7}),
    ],
)
def test_tampering_unknown_fields_counts_and_digests_fail_closed(mutate: object) -> None:
    value = build_ledger().to_dict()
    mutate(value)  # type: ignore[operator]

    with pytest.raises(RequestLedgerError):
        validate_final_request_ledger(
            value,
            tokenizer=CanonicalUtf8ByteTokenizer(),
        )
