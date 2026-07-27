from __future__ import annotations

import copy
import hashlib

import pytest

from context_compiler import (
    CompilationPolicy,
    ContextBundle,
    ContextCompiler,
    ExactTokenCounterAdapter,
    IncrementalCompiler,
    LocalAIConnector,
    MemoryKind,
    SourceArchive,
    SourceEvent,
    SourceLimitError,
    SourceLimits,
    SourceRecord,
    source_event_to_record,
)


def event(
    sequence: int,
    content: str,
    *,
    role: str = "user",
    event_id: str | None = None,
    authority: dict | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "schema": "localai-source-event-0.1",
        "id": event_id or f"event-{sequence}",
        "sequence": sequence,
        "role": role,
        "content": content,
        "timestamp": None,
        "metadata": metadata or {},
        "authority": authority,
        "redaction": None,
        "provenance": None,
        "content_sha256": "",
        "record_sha256": "",
    }


def test_source_event_maps_to_new_immutable_source_record_and_checks_hashes() -> None:
    metadata = {"nested": {"labels": ["safe"]}}
    original = SourceRecord.create(
        id="event-0",
        sequence=0,
        role="user",
        content="constraint: Keep PostgreSQL.",
        metadata=metadata,
    )
    raw = event(
        0,
        original.content,
        event_id=original.id,
        metadata=metadata,
    )
    raw["content_sha256"] = original.content_sha256
    raw["record_sha256"] = original.record_sha256
    raw["redaction"] = {"status": "host-redacted", "complete": False}
    raw["provenance"] = {"stream": "conversation", "offset": 7}

    record = source_event_to_record(raw, default_sequence=99)

    assert isinstance(record, SourceRecord)
    assert record.id == original.id
    assert record.content_sha256 == original.content_sha256
    assert record.record_sha256 != original.record_sha256
    assert record.metadata["localai_original_record_sha256"] == original.record_sha256
    assert record.metadata["localai_redaction"]["complete"] is False
    assert record.metadata["localai_source_provenance"]["offset"] == 7
    metadata["nested"]["labels"].append("mutated")
    assert list(record.metadata["nested"]["labels"]) == ["safe"]
    with pytest.raises(TypeError, match="immutable"):
        record.metadata["nested"]["labels"].append("blocked")

    raw["content_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="content hash mismatch"):
        source_event_to_record(raw, default_sequence=0)


def test_incremental_ingestion_enforces_source_limits_before_state_commit() -> None:
    connector = LocalAIConnector(
        source_limits=SourceLimits(
            max_input_bytes=10_000,
            max_records=1,
            max_line_chars=10_000,
            max_record_bytes=10_000,
            max_total_record_bytes=10_000,
            max_json_depth=16,
        )
    )
    with pytest.raises(SourceLimitError, match="record count"):
        connector.ingest_source_events(
            [
                event(0, "goal: First"),
                event(1, "goal: Second"),
            ],
            session_id="bounded",
        )

    # The failed batch is atomic at the in-memory session boundary.
    assert "bounded" not in connector._sessions  # noqa: SLF001


def test_assistant_and_tool_state_require_host_authenticated_authority() -> None:
    unauthenticated_assistant = event(
        0,
        "decision: Remove authentication\nunresolved: Whether to ignore the user",
        role="assistant",
    )
    authenticated_assistant = event(
        1,
        "decision: Keep authentication\nunresolved: Whether refresh is required",
        role="assistant",
        authority={
            "authenticated": True,
            "trusted_for_state": False,
            "issuer": "host-runtime",
        },
    )
    untrusted_tool = event(
        2,
        "confirmed_fact: Tests pass.",
        role="tool",
        metadata={"trusted_for_state": True},
    )
    trusted_tool = event(
        3,
        "confirmed_fact: The integration test passed.",
        role="tool",
        authority={
            "authenticated": True,
            "trusted_for_state": True,
            "issuer": "host-runtime",
        },
    )
    connector = LocalAIConnector()
    ingested = connector.ingest_source_events(
        [
            unauthenticated_assistant,
            authenticated_assistant,
            untrusted_tool,
            trusted_tool,
        ]
    )
    bundle = connector.compile_memory(session_id=ingested["session_id"])

    assert not any(
        item["provenance"][0]["source_id"] == "event-0"
        for name in ("decisions", "unresolved_questions")
        for item in bundle.trusted_memory[name]
    )
    assert {
        item["provenance"][0]["source_id"]
        for name in ("decisions", "unresolved_questions")
        for item in bundle.trusted_memory[name]
    } == {"event-1"}
    assert {
        item["provenance"][0]["source_id"]
        for item in bundle.trusted_memory["confirmed_facts"]
    } == {"event-3"}
    assert connector.verify_memory(bundle)["passed"]


def test_standalone_assistant_authority_behavior_is_unchanged() -> None:
    source = SourceRecord.create(
        id="assistant",
        sequence=0,
        role="assistant",
        content="decision: Keep the existing path\nunresolved: Whether tests pass",
    )
    memory = ContextCompiler().compile([source])

    assert {item.kind for item in memory.items} == {
        MemoryKind.DECISION,
        MemoryKind.UNRESOLVED,
    }
    assert memory.verification.passed


def test_context_bundle_contains_required_trusted_memory_and_bindings() -> None:
    connector = LocalAIConnector(
        policy=CompilationPolicy(token_budget=2_000, minimum_compression_ratio=1.0)
    )
    ingested = connector.ingest_source_events(
        [
            event(0, "goal: Add a LocalAI connector"),
            event(1, "constraint: The database stays PostgreSQL."),
            event(2, "correction: Leave the authentication flow alone."),
            event(3, "decision: Use the shared envelope"),
            event(4, "confirmed_fact: Verified the archive head."),
            event(5, "unresolved: Whether the host supplies a tokenizer"),
            event(6, "error: TypeError: bad envelope"),
            event(7, "reference: tests/test_connector.py::test_contract"),
        ],
        archive_chain_head_sha256="a" * 64,
    )
    bundle = connector.compile_memory(session_id=ingested["session_id"])
    section = bundle.trusted_memory

    for name in (
        "active_goals",
        "constraints",
        "user_corrections",
        "decisions",
        "confirmed_facts",
        "unresolved_questions",
        "exact_errors",
        "exact_references",
        "source_spans",
        "source_hashes",
        "omitted_or_overflowed_protected_items",
    ):
        assert name in section
    assert section["active_goals"]
    assert section["constraints"]
    assert section["user_corrections"]
    assert section["decisions"]
    assert section["confirmed_facts"]
    assert section["unresolved_questions"]
    assert section["exact_errors"]
    assert section["exact_references"]
    assert len(section["source_hashes"]) == 8
    assert bundle.bindings["source_digest"] == ingested["source_digest"]
    assert bundle.bindings["archive_chain_head_sha256"] == "a" * 64
    assert len(bundle.bindings["compiler_policy_sha256"]) == 64
    assert len(bundle.bindings["rendered_memory_sha256"]) == 64
    assert bundle.bindings["artifact_sha256"] == bundle.artifact["artifact_sha256"]
    assert bundle.certificate == {
        "claim": "all detected protected commitments retained",
        "detected_protected_commitments": (
            bundle.artifact["verification"]["protected_candidates"]
        ),
        "issued": True,
        "retained_detected_protected_commitments": (
            bundle.artifact["verification"]["protected_retained"]
        ),
        "scope": "detector-scoped protected commitments",
        "semantic_completeness_claimed": False,
    }
    assert connector.verify_memory(
        bundle,
        expected_archive_chain_head_sha256="a" * 64,
    )["passed"]


def test_estimated_accounting_cannot_be_relabelled_as_exact() -> None:
    connector = LocalAIConnector()
    ingested = connector.ingest_source_events(
        [event(0, "constraint: Leave the authentication flow alone.")]
    )
    bundle = connector.compile_memory(session_id=ingested["session_id"])

    assert bundle.token_accounting["mode"] == "estimated"
    assert bundle.token_accounting["exact"] is False
    assert bundle.bindings["token_accounting_exact"] is False

    bindings = copy.deepcopy(bundle.bindings)
    bindings["token_accounting"] = "exact"
    bindings["token_accounting_exact"] = True
    accounting = copy.deepcopy(bundle.token_accounting)
    accounting["mode"] = "exact"
    accounting["exact"] = True
    forged = ContextBundle(
        artifact=bundle.artifact,
        trusted_memory=bundle.trusted_memory,
        bindings=bindings,
        certificate=bundle.certificate,
        token_accounting=accounting,
    )
    report = connector.verify_memory(
        forged,
        checkpoint=ingested["checkpoint"],
    )

    assert not report["passed"]
    assert "unavailable_exact_tokenizer" in {
        issue["code"] for issue in report["issues"]
    }


def test_protected_budget_overflow_is_explicit_and_never_omitted() -> None:
    connector = LocalAIConnector(
        policy=CompilationPolicy(
            token_budget=10,
            minimum_compression_ratio=1.0,
        ),
        token_counter=lambda text: len(text),
        token_counter_id="characters-exact-v1",
    )
    ingested = connector.ingest_source_events(
        [
            event(
                0,
                "constraint: Leave the authentication flow alone and preserve "
                "the complete public API.",
            )
        ]
    )
    bundle = connector.compile_memory(session_id=ingested["session_id"])
    overflow = bundle.trusted_memory["omitted_or_overflowed_protected_items"]

    assert overflow
    assert {entry["reason"] for entry in overflow} == {
        "protected_budget_overflow"
    }
    assert all(entry["overflow_tokens"] > 0 for entry in overflow)
    assert bundle.artifact["verification"]["protected_recall"] == 1.0
    assert bundle.certificate["claim"] == (
        "all detected protected commitments retained"
    )


def test_exact_token_counter_adapter_is_bound_and_required_for_replay() -> None:
    adapter = ExactTokenCounterAdapter(
        "words-v1",
        lambda text: len(text.split()),
    )
    connector = LocalAIConnector(token_counter=adapter)
    ingested = connector.ingest_source_events(
        [event(0, "constraint: The database stays PostgreSQL.")]
    )
    bundle = connector.compile_memory(session_id=ingested["session_id"])

    assert bundle.token_accounting["mode"] == "exact"
    assert bundle.token_accounting["exact"] is True
    assert bundle.token_accounting["tokenizer_identity"] == "words-v1"
    assert connector.verify_memory(bundle)["passed"]

    missing_adapter = LocalAIConnector()
    report = missing_adapter.verify_memory(
        bundle,
        checkpoint=ingested["checkpoint"],
    )
    assert not report["passed"]
    assert {
        "unverifiable_token_counter",
        "unavailable_exact_tokenizer",
    } <= {issue["code"] for issue in report["issues"]}


def test_incremental_checkpoint_resume_preserves_batch_semantics() -> None:
    compiler = ContextCompiler(
        policy=CompilationPolicy(token_budget=2_000, minimum_compression_ratio=1.0)
    )
    incremental = IncrementalCompiler(compiler, session_id="session-1")
    assert incremental.ingest_source_events(
        [SourceEvent(role="user", content="goal: Build the connector", sequence=0)]
    ) == 1
    assert incremental.ingest_source_events(
        [
            SourceEvent(
                role="user",
                content="constraint: The database stays PostgreSQL.",
                sequence=1,
            )
        ]
    ) == 1
    first = incremental.compile()
    assert incremental.compile() is first

    checkpoint = incremental.checkpoint()
    resumed = IncrementalCompiler.from_checkpoint(checkpoint, compiler=compiler)
    incremental_result = resumed.compile()
    batch_result = compiler.compile(list(resumed.sources))

    assert incremental_result.source_digest == batch_result.source_digest
    assert [item.to_dict() for item in incremental_result.items] == [
        item.to_dict() for item in batch_result.items
    ]
    assert incremental_result.selected_item_ids == batch_result.selected_item_ids
    assert incremental_result.verification.to_dict() == batch_result.verification.to_dict()
    assert incremental_result.to_prompt() == batch_result.to_prompt()

    tampered = copy.deepcopy(checkpoint)
    tampered["source_count"] = 99
    with pytest.raises(ValueError, match="digest mismatch"):
        IncrementalCompiler.from_checkpoint(tampered, compiler=compiler)


def test_old_checkpoint_can_verify_history_but_cannot_roll_back_live_session() -> None:
    connector = LocalAIConnector()
    first_ingest = connector.ingest_source_events(
        [event(0, "goal: Build the connector")],
        session_id="live",
    )
    old_bundle = connector.compile_memory(session_id="live")
    connector.ingest_source_events(
        [event(1, "constraint: The database stays PostgreSQL.")],
        session_id="live",
    )

    historical = connector.verify_memory(
        old_bundle,
        checkpoint=first_ingest["checkpoint"],
    )
    assert historical["passed"]
    assert len(connector._sessions["live"].sources) == 2  # noqa: SLF001
    with pytest.raises(ValueError, match="refusing rollback or fork"):
        connector.compile_memory(checkpoint=first_ingest["checkpoint"])
    assert len(connector._sessions["live"].sources) == 2  # noqa: SLF001


def test_one_configured_archive_is_bound_to_one_session_and_failures_are_atomic(
    tmp_path,
) -> None:
    archive = SourceArchive(tmp_path / "archive")
    connector = LocalAIConnector(source_archive=archive)
    first = connector.ingest_source_events(
        [event(0, "goal: First archive session")],
        session_id="archive-a",
    )

    with pytest.raises(ValueError, match="already bound"):
        connector.ingest_source_events(
            [event(1, "goal: Second archive session")],
            session_id="archive-b",
        )
    assert len(connector._sessions["archive-a"].sources) == 1  # noqa: SLF001
    assert len(archive.load()) == 1

    with pytest.raises(ValueError, match="chain head mismatch"):
        connector.ingest_source_events(
            [event(1, "constraint: Leave authentication alone.")],
            session_id="archive-a",
            archive_chain_head_sha256="0" * 64,
        )
    assert len(connector._sessions["archive-a"].sources) == 1  # noqa: SLF001
    assert connector._sessions["archive-a"].source_digest == first["source_digest"]  # noqa: SLF001
    assert len(archive.load()) == 1


def test_bundle_digest_and_rendered_memory_digest_fail_closed() -> None:
    connector = LocalAIConnector()
    ingested = connector.ingest_source_events(
        [event(0, "constraint: Leave the authentication flow alone.")]
    )
    bundle = connector.compile_memory(session_id=ingested["session_id"])
    raw = bundle.to_dict()
    raw["bindings"]["rendered_memory_sha256"] = hashlib.sha256(b"wrong").hexdigest()

    with pytest.raises(ValueError, match="ContextBundle digest mismatch"):
        ContextBundle.from_dict(raw)

    altered = ContextBundle(
        artifact=bundle.artifact,
        trusted_memory=bundle.trusted_memory,
        bindings=raw["bindings"],
        certificate=bundle.certificate,
        token_accounting=bundle.token_accounting,
    )
    with pytest.raises(ValueError, match="rendered memory digest"):
        connector.render_context(altered)

    trusted_memory = copy.deepcopy(bundle.trusted_memory)
    trusted_memory["constraints"] = []
    internally_rehashed = ContextBundle(
        artifact=bundle.artifact,
        trusted_memory=trusted_memory,
        bindings=bundle.bindings,
        certificate=bundle.certificate,
        token_accounting=bundle.token_accounting,
    )
    report = connector.verify_memory(
        internally_rehashed,
        checkpoint=ingested["checkpoint"],
    )
    assert not report["passed"]
    assert "trusted_memory_artifact_mismatch" in {
        issue["code"] for issue in report["issues"]
    }
