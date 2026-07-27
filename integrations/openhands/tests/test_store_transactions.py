from __future__ import annotations

import copy
import hashlib
import os
import sqlite3

import pytest
from store_helpers import (
    append_message,
    canonical_json,
    compile_generation,
    message_event,
    source_record,
    store_path,
)

import ctxc_openhands.storage as storage_module
from ctxc_openhands.request_ledger import build_final_request_ledger
from ctxc_openhands.storage import (
    GenerationStateError,
    ImmutableEventError,
    IncompleteAtomicGroupError,
    SQLiteGenerationStore,
    StaleGenerationError,
    StoreIntegrityError,
)
from ctxc_openhands.tokenizer import CanonicalUtf8ByteTokenizer


def test_append_is_exactly_once_and_source_rows_are_immutable(tmp_path) -> None:
    path = store_path(tmp_path)
    store = SQLiteGenerationStore(path)
    event = message_event(0)
    record = source_record(event, 0)

    first = store.append_source_event(
        session_id="session-1",
        host_event=event,
        source_record=record,
        request_id="request-1",
    )
    retry = store.append_source_event(
        session_id="session-1",
        host_event=event,
        source_record=record,
        request_id="request-1",
    )

    assert first == retry
    assert first.accepted is True
    assert store.snapshot("session-1").records == (record,)
    with pytest.raises(ImmutableEventError, match="different payload"):
        changed_event = {**event, "source": "user"}
        store.append_source_event(
            session_id="session-1",
            host_event=changed_event,
            source_record=source_record(changed_event, 0),
            request_id="request-1",
        )

    direct = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            direct.execute(
                "UPDATE source_events SET event_kind='forged' WHERE event_id='event-0'"
            )
    finally:
        direct.close()


def test_existing_store_mode_never_creates_a_missing_database(tmp_path) -> None:
    path = tmp_path / "missing" / "store.sqlite3"

    with pytest.raises(FileNotFoundError, match="does not exist"):
        SQLiteGenerationStore(path, require_existing=True)

    assert not path.exists()
    assert not path.parent.exists()


def test_incompatible_store_refusal_does_not_mutate_database_or_sidecars(
    tmp_path,
) -> None:
    path = tmp_path / "incompatible.sqlite3"
    direct = sqlite3.connect(path)
    try:
        direct.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        direct.executemany(
            "INSERT INTO metadata(key, value) VALUES(?, ?)",
            (
                ("schema_version", "1"),
                ("store_uuid", "incompatible-store"),
            ),
        )
        direct.commit()
    finally:
        direct.close()

    tracked = tuple(
        path.with_name(path.name + suffix)
        for suffix in ("", "-wal", "-shm", "-journal")
    )
    before = {
        candidate: (
            candidate.exists(),
            candidate.read_bytes() if candidate.exists() else None,
        )
        for candidate in tracked
    }

    with pytest.raises(StoreIntegrityError, match="unsupported.*schema version"):
        SQLiteGenerationStore(path)

    after = {
        candidate: (
            candidate.exists(),
            candidate.read_bytes() if candidate.exists() else None,
        )
        for candidate in tracked
    }
    assert after == before


def test_require_new_refuses_an_existing_store_without_mutation(tmp_path) -> None:
    path = store_path(tmp_path)
    original = SQLiteGenerationStore(path)
    store_uuid = original.store_uuid

    with pytest.raises(FileExistsError, match="already exists"):
        SQLiteGenerationStore(path, require_new=True)

    assert SQLiteGenerationStore(path, require_existing=True).store_uuid == store_uuid


def test_new_store_reservation_loses_injected_race_without_overwrite(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "raced.sqlite3"
    competitor = b"competitor-owned bytes"
    original_open = os.open
    injected = False

    def racing_open(candidate, flags, mode=0o777):  # type: ignore[no-untyped-def]
        nonlocal injected
        if (
            not injected
            and os.fspath(candidate) == os.fspath(path)
            and flags & os.O_EXCL
        ):
            path.write_bytes(competitor)
            injected = True
        return original_open(candidate, flags, mode)

    monkeypatch.setattr(storage_module.os, "open", racing_open)

    with pytest.raises(FileExistsError):
        SQLiteGenerationStore(path, require_new=True)

    assert injected is True
    assert path.read_bytes() == competitor


def test_initialized_store_path_removal_never_recreates_database(tmp_path) -> None:
    path = store_path(tmp_path)
    store = SQLiteGenerationStore(path, require_new=True)
    path.unlink()

    with pytest.raises(FileNotFoundError, match="does not exist"):
        _ = store.store_uuid

    assert not path.exists()


def test_existing_store_replacement_during_read_only_open_fails_closed(
    tmp_path,
    monkeypatch,
) -> None:
    path = store_path(tmp_path)
    replacement_path = tmp_path / "replacement.sqlite3"
    original_uuid = SQLiteGenerationStore(path, require_new=True).store_uuid
    replacement_uuid = SQLiteGenerationStore(
        replacement_path,
        require_new=True,
    ).store_uuid
    assert replacement_uuid != original_uuid

    original_connect = storage_module.sqlite3.connect
    target_uri = f"{path.as_uri()}?mode=ro"
    injected = False

    def replacing_connect(database, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal injected
        if not injected and os.fspath(database) == target_uri:
            path.unlink()
            replacement_path.replace(path)
            injected = True
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(storage_module.sqlite3, "connect", replacing_connect)

    with pytest.raises(
        StoreIntegrityError,
        match="identity changed during read-only open",
    ):
        SQLiteGenerationStore(path, require_existing=True)

    assert injected is True
    assert SQLiteGenerationStore(
        path,
        require_existing=True,
    ).store_uuid == replacement_uuid


def test_initialized_store_unlink_during_read_write_open_fails_closed(
    tmp_path,
    monkeypatch,
) -> None:
    path = store_path(tmp_path)
    stand_in_path = tmp_path / "stand-in.sqlite3"
    store = SQLiteGenerationStore(path, require_new=True)
    SQLiteGenerationStore(stand_in_path, require_new=True)

    original_connect = storage_module.sqlite3.connect
    target_uri = f"{path.as_uri()}?mode=rw"
    stand_in_uri = f"{stand_in_path.as_uri()}?mode=rw"
    injected = False

    def unlinking_connect(database, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal injected
        if not injected and os.fspath(database) == target_uri:
            path.unlink()
            injected = True
            return original_connect(stand_in_uri, *args, **kwargs)
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(storage_module.sqlite3, "connect", unlinking_connect)

    with pytest.raises(FileNotFoundError, match="does not exist"):
        _ = store.store_uuid

    assert injected is True
    assert not path.exists()
    assert stand_in_path.exists()


def test_new_store_schema_and_metadata_roll_back_together(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "initialization-failure.sqlite3"
    original_validate = SQLiteGenerationStore._validate_existing_store

    def fail_after_validation(connection, *, schema_sql):  # type: ignore[no-untyped-def]
        original_validate(connection, schema_sql=schema_sql)
        raise StoreIntegrityError("injected initialization failure")

    monkeypatch.setattr(
        SQLiteGenerationStore,
        "_validate_existing_store",
        staticmethod(fail_after_validation),
    )

    with pytest.raises(StoreIntegrityError, match="injected initialization failure"):
        SQLiteGenerationStore(path, require_new=True)

    assert path.exists()
    direct = sqlite3.connect(path)
    try:
        objects = direct.execute(
            """
            SELECT type, name FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
    finally:
        direct.close()
    assert objects == []


@pytest.mark.parametrize(
    ("object_name", "alteration"),
    [
        (
            "request_ledgers_no_delete",
            """
            DROP TRIGGER request_ledgers_no_delete;
            CREATE TRIGGER request_ledgers_no_delete
            BEFORE DELETE ON request_ledgers BEGIN
                SELECT 1;
            END;
            """,
        ),
        (
            "source_atomic_groups_one_role",
            """
            DROP INDEX source_atomic_groups_one_role;
            CREATE INDEX source_atomic_groups_one_role
            ON source_atomic_groups(session_id, group_sha256, ordinal);
            """,
        ),
    ],
)
def test_altered_integrity_object_definition_is_refused_without_repair(
    tmp_path,
    object_name: str,
    alteration: str,
) -> None:
    path = store_path(tmp_path)
    SQLiteGenerationStore(path)
    direct = sqlite3.connect(path)
    try:
        direct.executescript(alteration)
        altered_sql = direct.execute(
            "SELECT sql FROM sqlite_master WHERE name = ?",
            (object_name,),
        ).fetchone()[0]
        direct.commit()
    finally:
        direct.close()

    with pytest.raises(
        StoreIntegrityError,
        match=rf"definition mismatch: {object_name}",
    ):
        SQLiteGenerationStore(path)

    direct = sqlite3.connect(path)
    try:
        retained_sql = direct.execute(
            "SELECT sql FROM sqlite_master WHERE name = ?",
            (object_name,),
        ).fetchone()[0]
    finally:
        direct.close()
    assert retained_sql == altered_sql


def test_wrong_journal_mode_is_refused_without_conversion_or_sidecar_mutation(
    tmp_path,
) -> None:
    path = store_path(tmp_path)
    SQLiteGenerationStore(path)
    direct = sqlite3.connect(path)
    try:
        mode = direct.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        assert str(mode).casefold() == "delete"
    finally:
        direct.close()

    tracked = tuple(
        path.with_name(path.name + suffix)
        for suffix in ("", "-wal", "-shm", "-journal")
    )
    before = {
        candidate: (
            candidate.exists(),
            candidate.read_bytes() if candidate.exists() else None,
        )
        for candidate in tracked
    }

    with pytest.raises(StoreIntegrityError, match="journal_mode must already be WAL"):
        SQLiteGenerationStore(path)

    after = {
        candidate: (
            candidate.exists(),
            candidate.read_bytes() if candidate.exists() else None,
        )
        for candidate in tracked
    }
    assert after == before


def test_existing_v2_store_missing_integrity_object_is_not_repaired(tmp_path) -> None:
    path = store_path(tmp_path)
    SQLiteGenerationStore(path)
    direct = sqlite3.connect(path)
    try:
        direct.execute("DROP TRIGGER request_ledgers_no_delete")
        direct.commit()
    finally:
        direct.close()

    with pytest.raises(
        StoreIntegrityError,
        match="missing required trigger objects: request_ledgers_no_delete",
    ):
        SQLiteGenerationStore(path)

    direct = sqlite3.connect(path)
    try:
        row = direct.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'trigger' AND name = 'request_ledgers_no_delete'
            """
        ).fetchone()
    finally:
        direct.close()
    assert row is None


def test_backup_refuses_to_overwrite_any_existing_destination(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    destination = tmp_path / "existing-backup.sqlite3"
    original = b"existing evidence must not be overwritten"
    destination.write_bytes(original)

    with pytest.raises(FileExistsError, match="already exists"):
        store.backup(destination)

    assert destination.read_bytes() == original


def test_backup_is_self_contained_and_reopens_as_verified_store(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    record = append_message(store, 0, text="retained backup history")
    destination = tmp_path / "backup.sqlite3"

    store.backup(destination)

    assert destination.exists()
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = destination.with_name(destination.name + suffix)
        if sidecar.exists():
            sidecar.unlink()
    reopened = SQLiteGenerationStore(destination, require_existing=True)
    snapshot = reopened.snapshot("session-1")
    assert snapshot.records == (record,)
    report = reopened.integrity_report()
    assert report["passed"] is True
    assert report["foreign_key_issue_count"] == 0


def test_store_and_backup_reject_dangling_symbolic_links(tmp_path) -> None:
    dangling_store = tmp_path / "dangling-store.sqlite3"
    dangling_backup = tmp_path / "dangling-backup.sqlite3"
    missing = tmp_path / "missing-target.sqlite3"
    try:
        dangling_store.symlink_to(missing)
        dangling_backup.symlink_to(missing)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")

    with pytest.raises(ValueError, match="symbolic link or reparse point"):
        SQLiteGenerationStore(dangling_store)

    store = SQLiteGenerationStore(store_path(tmp_path))
    with pytest.raises(ValueError, match="symbolic link or reparse point"):
        store.backup(dangling_backup)
    assert dangling_store.is_symlink()
    assert dangling_backup.is_symlink()


def test_store_rejects_symbolic_link_parent_before_creating_database(tmp_path) -> None:
    real_parent = tmp_path / "real-parent"
    linked_parent = tmp_path / "linked-parent"
    real_parent.mkdir()
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symbolic links unavailable: {exc}")

    target = linked_parent / "store.sqlite3"
    with pytest.raises(ValueError, match="parent.*symbolic link or reparse point"):
        SQLiteGenerationStore(target)
    assert not (real_parent / "store.sqlite3").exists()


def test_store_rejects_hard_link_aliases(tmp_path) -> None:
    path = store_path(tmp_path)
    store = SQLiteGenerationStore(path)
    store_uuid = store.store_uuid
    alias = tmp_path / "store-alias.sqlite3"
    try:
        os.link(path, alias)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    try:
        with pytest.raises(ValueError, match="hard-link aliases"):
            SQLiteGenerationStore(alias, require_existing=True)
    finally:
        alias.unlink()

    assert (
        SQLiteGenerationStore(path, require_existing=True).store_uuid
        == store_uuid
    )


def test_append_fails_closed_if_session_creation_does_not_materialize(
    tmp_path,
    monkeypatch,
) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    event = message_event(0)
    monkeypatch.setattr(
        store,
        "_ensure_session",
        lambda _connection, _session_id: None,
    )

    with pytest.raises(StoreIntegrityError, match="session row is missing"):
        store.append_source_event(
            session_id="session-1",
            host_event=event,
            source_record=source_record(event, 0),
            request_id="request-1",
        )


def test_prepared_verified_and_committed_generations_are_invisible(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    assert store.read_active("session-1") is None

    generation_id, _bundle, _replay = compile_generation(
        store,
        1,
        activate=False,
    )
    assert store.read_active("session-1") is None

    epoch = store.activate_generation(
        generation_id=generation_id,
        operation_id="activate-later",
    )
    active = store.read_active("session-1")
    assert epoch == 1
    assert active is not None
    assert active.generation_id == generation_id
    assert active.tail == ()


def test_activation_fails_closed_if_generation_session_row_is_missing(tmp_path) -> None:
    path = store_path(tmp_path)
    store = SQLiteGenerationStore(path)
    append_message(store, 0)
    generation_id, _bundle, _replay = compile_generation(
        store,
        1,
        activate=False,
    )
    direct = sqlite3.connect(path)
    try:
        direct.execute(
            "DELETE FROM sessions WHERE session_id = ?",
            ("session-1",),
        )
        direct.commit()
    finally:
        direct.close()

    with pytest.raises(
        StoreIntegrityError,
        match="generation session row is missing",
    ):
        store.activate_generation(
            generation_id=generation_id,
            operation_id="missing-session-activation",
        )


def test_stale_generation_cannot_replace_old_active_pointer(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    first_id, _bundle, _replay = compile_generation(store, 1)
    append_message(store, 1)
    stale_id, _bundle, _replay = compile_generation(store, 2, activate=False)
    append_message(store, 2)

    with pytest.raises(StaleGenerationError, match="compare-and-swap"):
        store.activate_generation(
            generation_id=stale_id,
            operation_id="stale-activation",
        )

    active = store.read_active("session-1")
    assert active is not None
    assert active.generation_id == first_id
    assert [record.id for record in active.tail] == ["event-1", "event-2"]


def test_atomic_tool_call_and_result_must_enter_generation_together(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    action = {
        "kind": "ActionEvent",
        "id": "action-1",
        "timestamp": "2026-07-27T12:00:00+00:00",
        "source": "agent",
        "thought": [],
        "tool_name": "terminal",
        "tool_call_id": "call-1",
        "tool_call": {
            "id": "call-1",
            "name": "terminal",
            "arguments": "{}",
            "origin": "completion",
        },
        "llm_response_id": "response-1",
    }
    store.append_source_event(
        session_id="session-1",
        host_event=action,
        source_record=source_record(action, 0),
        request_id="append-action",
    )
    with pytest.raises(IncompleteAtomicGroupError, match="incomplete"):
        store.prepare_generation(
            session_id="session-1",
            generation_id="generation-incomplete",
            operation_id="prepare-incomplete",
            policy_sha256=hashlib.sha256(b"policy").hexdigest(),
            tokenizer_identity="character-estimate-v1",
        )

    result = {
        "kind": "ObservationEvent",
        "id": "result-1",
        "timestamp": "2026-07-27T12:00:01+00:00",
        "source": "environment",
        "tool_name": "terminal",
        "tool_call_id": "call-1",
        "action_id": "action-1",
        "observation": {
            "content": [{"type": "text", "text": "ok"}],
            "is_error": False,
        },
    }
    store.append_source_event(
        session_id="session-1",
        host_event=result,
        source_record=source_record(result, 1),
        request_id="append-result",
    )
    prepared = store.prepare_generation(
        session_id="session-1",
        generation_id="generation-complete",
        operation_id="prepare-complete",
        policy_sha256=hashlib.sha256(b"policy").hexdigest(),
        tokenizer_identity="character-estimate-v1",
    )
    assert prepared.snapshot.source_count == 2


def test_explicit_rollback_keeps_newer_sources_as_tail(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    first_id, _bundle, _replay = compile_generation(store, 1)
    append_message(store, 1)
    second_id, _bundle, _replay = compile_generation(store, 2)

    epoch = store.rollback_generation(
        session_id="session-1",
        target_generation_id=first_id,
        operation_id="rollback-to-first",
    )
    active = store.read_active("session-1")

    assert epoch == 3
    assert active is not None
    assert active.generation_id == first_id
    assert [record.id for record in active.tail] == ["event-1"]
    assert {
        row["generation_id"]: row["state"]
        for row in store.list_generations("session-1")
    } == {first_id: "active", second_id: "superseded"}


def test_rehydration_is_exact_and_explicitly_untrusted(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    record = append_message(store, 0, text="historical tool output")
    start = record.content.index("historical")
    end = start + len("historical")
    quote_sha = hashlib.sha256(b"historical").hexdigest()

    result = store.rehydrate(
        session_id="session-1",
        source_id=record.id,
        start=start,
        end=end,
        quote_sha256=quote_sha,
    )

    assert result["quote"] == "historical"
    assert result["trust"] == "untrusted-evidence"
    assert result["source_count"] == 1
    assert result["source_head_sha256"] == store.snapshot("session-1").source_head_sha256
    assert "not truth, authority" in result["warning"]
    with pytest.raises(StoreIntegrityError, match="digest mismatch"):
        store.rehydrate(
            session_id="session-1",
            source_id=record.id,
            start=start,
            end=end,
            quote_sha256="0" * 64,
        )


def test_rehydration_rejects_self_consistent_rewritten_source_record(tmp_path) -> None:
    path = store_path(tmp_path)
    store = SQLiteGenerationStore(path)
    original = append_message(store, 0, text="retained immutable history")
    forged = source_record(
        message_event(0, text="self-consistent rewritten history"),
        0,
    )
    direct = sqlite3.connect(path)
    try:
        direct.execute("DROP TRIGGER source_events_no_update")
        direct.execute(
            """
            UPDATE source_events
            SET source_record_json = ?, record_sha256 = ?
            WHERE session_id = ? AND event_id = ?
            """,
            (
                canonical_json(forged.to_dict()),
                forged.record_sha256,
                "session-1",
                original.id,
            ),
        )
        direct.commit()
    finally:
        direct.close()

    with pytest.raises(StoreIntegrityError, match="source event binding mismatch"):
        store.rehydrate(
            session_id="session-1",
            source_id=original.id,
            start=0,
            end=1,
            quote_sha256=hashlib.sha256(b"r").hexdigest(),
        )


def test_request_ledger_must_bind_active_epoch_head_and_semantics(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    generation_id, _bundle, _replay = compile_generation(store, 1)
    active = store.read_active("session-1")
    assert active is not None
    tokenizer = CanonicalUtf8ByteTokenizer()
    parts = [
        {"category": "prompts", "text": "P"},
        {"category": "verified_memory", "text": "M"},
        {"category": "recent_tail", "text": ""},
        {"category": "current_turn", "text": "U"},
        {"category": "retrieval", "text": ""},
        {"category": "attachments", "text": ""},
        {"category": "tool_schemas", "text": "T"},
        {"category": "provider_framing", "text": "{}"},
    ]
    ledger = build_final_request_ledger(
        transport_parts=parts,
        model="offline-fake",
        path="chat",
        session_id=active.session_id,
        generation_id=active.generation_id,
        active_epoch=active.active_epoch,
        source_head_sha256=active.source_head_sha256,
        semantic_result_digest=active.semantic_result_digest,
        reserved_output_tokens=8,
        safety_margin_tokens=4,
        hard_limit_tokens=100,
        tokenizer=tokenizer,
    )
    store.store_request_ledger(
        session_id="session-1",
        generation_id=generation_id,
        request_id="model-request-1",
        ledger=ledger,
        tokenizer=tokenizer,
    )

    forged = copy.deepcopy(ledger.to_dict())
    forged["bindings"]["active_epoch"] = 999
    forged["ledger_sha256"] = hashlib.sha256(
        canonical_json(
            {key: value for key, value in forged.items() if key != "ledger_sha256"}
        ).encode()
    ).hexdigest()
    with pytest.raises((StoreIntegrityError, ValueError)):
        store.store_request_ledger(
            session_id="session-1",
            generation_id=generation_id,
            request_id="model-request-2",
            ledger=forged,
            tokenizer=tokenizer,
        )


def test_provisional_rollback_is_terminal_and_retains_sources(tmp_path) -> None:
    store = SQLiteGenerationStore(store_path(tmp_path))
    append_message(store, 0)
    generation_id, _bundle, _replay = compile_generation(
        store,
        1,
        activate=False,
    )
    store.roll_back_provisional(
        generation_id=generation_id,
        operation_id="explicit-recover-rollback",
    )
    assert store.list_generations("session-1")[0]["state"] == "rolled_back"
    assert store.snapshot("session-1").source_count == 1
    with pytest.raises(GenerationStateError):
        store.activate_generation(
            generation_id=generation_id,
            operation_id="cannot-activate",
        )
