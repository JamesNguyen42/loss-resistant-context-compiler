from __future__ import annotations

import pytest
from context_compiler import SourceRecord
from store_helpers import store_path

from ctxc_openhands import session, storage
from ctxc_openhands.storage import SQLiteGenerationStore, StoreIntegrityError


def test_canonical_store_json_rejects_lone_surrogate_with_typed_error() -> None:
    with pytest.raises(StoreIntegrityError, match="not canonical JSON"):
        storage._canonical_json({"value": "\ud800"})


def test_canonical_store_json_rejects_recursive_mapping_with_typed_error() -> None:
    recursive: dict[str, object] = {}
    recursive["self"] = recursive

    with pytest.raises(StoreIntegrityError, match="maximum depth"):
        storage._canonical_json(recursive)


def test_stored_json_decode_rejects_lone_surrogate_with_typed_error() -> None:
    with pytest.raises(StoreIntegrityError, match="not valid UTF-8 text"):
        storage._decode_canonical_json('{"value":"\ud800"}', label="stored value")


def test_stored_json_decode_rejects_excessive_nesting_with_typed_error() -> None:
    deeply_nested = "[" * 2_000 + "0" + "]" * 2_000

    with pytest.raises(StoreIntegrityError, match="maximum depth"):
        storage._decode_canonical_json(deeply_nested, label="stored value")


@pytest.mark.parametrize(
    ("content", "error"),
    (
        ('{"kind":"MessageEvent","kind":"ActionEvent"}', "duplicate JSON key"),
        ("[" * 2_000 + "0" + "]" * 2_000, "maximum depth"),
        ('{"kind": "MessageEvent"}', "not canonical JSON"),
    ),
)
def test_recorded_host_event_uses_strict_bounded_decoder(
    content: str,
    error: str,
) -> None:
    record = SourceRecord.create(sequence=0, role="assistant", content=content)
    with pytest.raises(StoreIntegrityError, match=error):
        session._recorded_host_event(record)


def test_canonical_store_json_rejects_oversized_collection() -> None:
    with pytest.raises(StoreIntegrityError, match="collection limit"):
        storage._canonical_json([None] * 100_001)


def test_canonical_store_json_rejects_excessive_total_items() -> None:
    payload = [[None] * 100_000 for _ in range(5)]
    with pytest.raises(StoreIntegrityError, match="maximum item count"):
        storage._canonical_json(payload)


def test_rehydrate_rejects_integer_beyond_sqlite_signed_range(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))

    with pytest.raises(ValueError, match="signed 64-bit integer"):
        store.rehydrate(
            session_id="session-1",
            source_id="event-1",
            start=1 << 63,
            end=1 << 63,
            quote_sha256="0" * 64,
        )
