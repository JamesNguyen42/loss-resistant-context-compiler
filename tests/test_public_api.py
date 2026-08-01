# ruff: noqa: E501 -- exact signature fixtures are intentionally literal
from __future__ import annotations

import inspect

import context_compiler
from context_compiler.context_window import ContextWindowError as ModuleContextWindowError
from context_compiler.context_window import compose_context_window
from context_compiler.materialized_window import compose_materialized_context_window

EXPECTED_EXPORTS = (
    "ARCHIVE_ENTRY_SCHEMA",
    "ARCHIVE_GENESIS_SHA256",
    "ARCHIVE_REPORT_SCHEMA",
    "ArchiveReport",
    "ArtifactLimitError",
    "ArtifactLimits",
    "ARTIFACT_DIFF_SCHEMA",
    "ARTIFACT_INSPECTION_SCHEMA",
    "ARTIFACT_SCHEMA_COMPATIBILITY_SCHEMA",
    "COMPILATION_METRICS_SCHEMA",
    "CONNECTOR_INSPECTION_SCHEMA",
    "CONNECTOR_OPERATIONS",
    "CONNECTOR_PROTOCOL_VERSION",
    "CONNECTOR_REQUEST_SCHEMA",
    "CONNECTOR_RESPONSE_SCHEMA",
    "CONTEXT_BUNDLE_SCHEMA",
    "MATERIALIZED_CONTEXT_COMPONENTS_SCHEMA",
    "MATERIALIZED_CONTEXT_RECEIPT_SCHEMA",
    "MATERIALIZED_CONTEXT_RESULT_SCHEMA",
    "MAX_SOURCE_ID_CHARS",
    "MAX_SOURCE_ROLE_CHARS",
    "MAX_SOURCE_TIMESTAMP_CHARS",
    "CompilationIsolationError",
    "CompilationLimitError",
    "CompilationLimits",
    "CompilationMetrics",
    "CompilationPolicy",
    "CompiledMemory",
    "CompositeExtractor",
    "ContextCompiler",
    "ContextBundle",
    "ContextWindowBudget",
    "ContextWindowDegradationPolicy",
    "ContextWindowError",
    "DomainLabelExtractor",
    "ExtractionResult",
    "Extractor",
    "ExactTokenCounterAdapter",
    "INCREMENTAL_CHECKPOINT_SCHEMA",
    "IncrementalCompiler",
    "MemoryItem",
    "MemoryKind",
    "MemoryStatus",
    "ModelExtractor",
    "PathBoundaryError",
    "LmsQwenCompletion",
    "LocalQwenError",
    "LiteralModelExtractor",
    "LocalAIConnector",
    "LEGACY_ARCHIVE_SCHEMA",
    "ProvenanceSpan",
    "REDACTION_REPORT_SCHEMA",
    "REDACTION_VERIFICATION_SCHEMA",
    "RETENTION_CERTIFICATE_WORDING",
    "RedactionError",
    "RedactionFinding",
    "RedactionLimitError",
    "RedactionPolicy",
    "RedactionResult",
    "RuleBasedExtractor",
    "SECRET_DETECTOR_NAMES",
    "SOURCE_EVENT_SCHEMA",
    "SourceArchive",
    "SourceLimitError",
    "SourceLimits",
    "SourceRecord",
    "SourceEvent",
    "TRUST_MANIFEST_SCHEMA",
    "TRUST_VERIFICATION_SCHEMA",
    "TrustManifestError",
    "VerificationReport",
    "artifact_schema_registry",
    "artifact_schema_support",
    "create_trust_manifest",
    "decode_connector_request",
    "diff_artifacts",
    "load_trust_manifest",
    "load_trust_manifest_path",
    "materialize_context",
    "render_artifact_text",
    "redact_sources",
    "summarize_artifact",
    "serve_stdio",
    "source_event_to_record",
    "trust_manifest_sha256",
    "validate_artifact_envelope",
    "validate_trust_manifest",
    "verify_redaction_report_hash",
    "verify_redaction_result",
    "verify_materialized_context_result",
    "verify_trust_manifest",
)

EXPECTED_SIGNATURES = {
    "ArtifactLimits": "(max_input_bytes: 'int' = 134217728, max_line_chars: 'int' = 8388608, max_canonical_bytes: 'int' = 134217728, max_json_depth: 'int' = 128, max_items: 'int' = 200000, max_selected_items: 'int' = 200000, max_provenance_spans: 'int' = 1000000, max_verification_issues: 'int' = 100000) -> None",
    "CompilationLimits": "(max_extractor_items: 'int' = 10000, max_extractor_rejections: 'int' = 10000, max_extractor_bytes: 'int' = 67108864, max_extractor_auxiliary_bytes: 'int' = 16777216, max_total_candidate_items: 'int' = 30000, max_resolved_items: 'int' = 20000, max_provenance_spans: 'int' = 100000, max_item_work: 'int' = 5000000) -> None",
    "CompilationPolicy": "(token_budget: 'int' = 4000, minimum_compression_ratio: 'float' = 5.0, fail_on_budget_overflow: 'bool' = False, include_superseded: 'bool' = False, include_discarded: 'bool' = True, verify: 'bool' = True, recover_missed_protected: 'bool' = True, chars_per_token: 'float' = 4.0, fail_on_primary_extractor_error: 'bool' = False) -> None",
    "ContextCompiler": "(extractor: 'Extractor | None' = None, *, policy: 'CompilationPolicy | None' = None, safety_extractor: 'Extractor | None' = None, token_counter: 'TokenCounter | None' = None, token_counter_id: 'str | None' = None, source_limits: 'SourceLimits | None' = None, compilation_limits: 'CompilationLimits | None' = None, untrusted_historical_roles: 'bool' = False) -> 'None'",
    "ContextBundle": "(artifact: 'dict[str, Any]', trusted_memory: 'dict[str, Any]', bindings: 'dict[str, Any]', certificate: 'dict[str, Any]', token_accounting: 'dict[str, Any]', schema: 'str' = 'localai-context-bundle-0.1', protocol_version: 'str' = '0.1', bundle_sha256: 'str' = '') -> None",
    "ContextWindowBudget": "(hard_limit_tokens: 'int', memory_budget_tokens: 'int', reserved_output_tokens: 'int' = 1024, safety_margin_tokens: 'int' = 256, fixed_input_tokens: 'int' = 0, minimum_recent_messages: 'int' = 0, maximum_recent_messages: 'int' = 4096, per_message_overhead_tokens: 'int' = 0) -> None",
    "ContextWindowDegradationPolicy": "(mode: 'str' = 'lossless-compact-then-reallocate-v1') -> None",
    "ContextWindowError": "(message: 'str', *, reason: 'str' = 'invalid_context_window', diagnostic: 'dict[str, object] | None' = None) -> 'None'",
    "ExactTokenCounterAdapter": "(identity: 'str', count_tokens: 'Callable[[str], int]') -> None",
    "IncrementalCompiler": "(compiler: 'ContextCompiler | None' = None, *, session_id: 'str | None' = None, sources: 'Iterable[SourceRecord]' = (), archive_chain_head_sha256: 'str | None' = None, archive_head_verified: 'bool' = False) -> 'None'",
    "LocalAIConnector": "(*, policy: 'CompilationPolicy | None' = None, token_counter: 'ExactTokenCounterAdapter | Callable[[str], int] | Any | None' = None, token_counter_id: 'str | None' = None, source_limits: 'SourceLimits | None' = None, compilation_limits: 'CompilationLimits | None' = None, artifact_limits: 'ArtifactLimits | None' = None, source_archive: 'SourceArchive | None' = None) -> 'None'",
    "LmsQwenCompletion": "(lms_executable: 'str | Path', model_key: 'str' = 'qwen/qwen3.6-35b-a3b', expected_variant: 'str' = 'qwen/qwen3.6-35b-a3b@q4_k_m', timeout_seconds: 'float' = 120.0, max_output_chars: 'int' = 1000000, require_single_concurrency: 'bool' = True) -> None",
    "SourceArchive": "(directory: 'str | Path', *, lock_timeout: 'float' = 5.0, source_limits: 'SourceLimits | None' = None) -> 'None'",
    "SourceEvent": "(role: 'str', content: 'str', sequence: 'int | None' = None, id: 'str | None' = None, timestamp: 'str | None' = None, metadata: 'dict[str, Any] | None' = None, authority: 'dict[str, Any] | None' = None, redaction: 'dict[str, Any] | None' = None, provenance: 'dict[str, Any] | None' = None, content_sha256: 'str' = '', record_sha256: 'str' = '', schema: 'str' = 'localai-source-event-0.1') -> None",
    "SourceLimits": "(max_input_bytes: 'int' = 67108864, max_records: 'int' = 100000, max_line_chars: 'int' = 1048576, max_record_bytes: 'int' = 4194304, max_total_record_bytes: 'int' = 67108864, max_json_depth: 'int' = 128) -> None",
    "SourceRecord": "(id: 'str', sequence: 'int', role: 'str', content: 'str', timestamp: 'str | None' = None, metadata: 'dict[str, Any]' = <factory>, content_sha256: 'str' = '', record_sha256: 'str' = '') -> None",
    "create_trust_manifest": "(artifact: 'Any', sources: 'list[SourceRecord]', *, archive_chain_head_sha256: 'str | None' = None, created_at: 'str | None' = None, source_limits: 'SourceLimits | None' = None, artifact_limits: 'ArtifactLimits | None' = None) -> 'dict[str, Any]'",
    "decode_connector_request": "(raw: 'str', *, max_request_bytes: 'int' = 8388608, max_json_depth: 'int' = 128) -> 'dict[str, Any]'",
    "diff_artifacts": "(before: 'Any', after: 'Any', *, include_item_details: 'bool' = True, limits: 'ArtifactLimits | None' = None) -> 'dict[str, Any]'",
    "redact_sources": "(sources: 'Iterable[SourceRecord]', *, policy: 'RedactionPolicy | None' = None) -> 'RedactionResult'",
    "materialize_context": "(sources: 'Iterable[SourceRecord | Mapping[str, Any]]', *, current_turn_id: 'str', budget: 'ContextWindowBudget', token_counter: 'ExactTokenCounterAdapter', allocation_plan_sha256: 'str', fixed_input_sha256: 'str | None' = None, policy: 'CompilationPolicy | None' = None, source_limits: 'SourceLimits | None' = None, compilation_limits: 'CompilationLimits | None' = None, degradation_policy: 'ContextWindowDegradationPolicy | None' = None) -> 'dict[str, Any]'",
    "serve_stdio": "(*, connector: 'LocalAIConnector | None' = None, input_stream: 'Any', output_stream: 'Any', max_request_bytes: 'int' = 8388608, max_json_depth: 'int' = 128) -> 'int'",
    "source_event_to_record": "(event: 'SourceEvent | Mapping[str, Any] | Any', *, default_sequence: 'int') -> 'SourceRecord'",
    "validate_artifact_envelope": "(artifact: 'Any', *, limits: 'ArtifactLimits | None' = None) -> 'dict[str, Any]'",
    "verify_materialized_context_result": "(value: 'Mapping[str, Any] | bytes', *, expected_receipt_sha256: 'str', expected_allocation_plan_sha256: 'str') -> 'dict[str, Any]'",
    "verify_trust_manifest": "(manifest: 'Any', artifact: 'Any', sources: 'list[SourceRecord]', *, expected_manifest_sha256: 'str | None', archive_chain_head_sha256: 'str | None' = None, source_limits: 'SourceLimits | None' = None, artifact_limits: 'ArtifactLimits | None' = None) -> 'dict[str, Any]'",
    "ContextCompiler.compile": "(self, sources: 'Iterable[SourceRecord | dict]', *, timeout_seconds: 'float | None' = None) -> 'CompiledMemory'",
    "CompiledMemory.to_dict": "(self, *, include_all_items: 'bool' = True) -> 'dict[str, Any]'",
    "CompiledMemory.to_prompt": "(self, *, allow_unverified: 'bool' = False) -> 'str'",
    "SourceRecord.create": "(*, sequence: 'int', role: 'str', content: 'str', id: 'str | None' = None, timestamp: 'str | None' = None, metadata: 'dict[str, Any] | None' = None, content_sha256: 'str' = '', record_sha256: 'str' = '') -> 'SourceRecord'",
    "ContextBundle.from_dict": "(value: 'Mapping[str, Any]') -> 'ContextBundle'",
    "IncrementalCompiler.checkpoint": "(self) -> 'dict[str, Any]'",
    "IncrementalCompiler.compile": "(self, *, timeout_seconds: 'float | None' = None)",
    "LocalAIConnector.compile_memory": "(self, *, session_id: 'str | None' = None, checkpoint: 'Mapping[str, Any] | None' = None, events: 'Iterable[SourceEvent | Mapping[str, Any] | Any] | None' = None, policy: 'CompilationPolicy | Mapping[str, Any] | None' = None, archive_chain_head_sha256: 'str | None' = None, timeout_seconds: 'float | None' = None) -> 'ContextBundle'",
    "LocalAIConnector.render_context": "(self, bundle: 'ContextBundle | Mapping[str, Any]') -> 'str'",
    "LocalAIConnector.verify_memory": "(self, bundle: 'ContextBundle | Mapping[str, Any]', *, session_id: 'str | None' = None, checkpoint: 'Mapping[str, Any] | None' = None, source_records: 'Iterable[SourceRecord | Mapping[str, Any]] | None' = None, events: 'Iterable[SourceEvent | Mapping[str, Any] | Any] | None' = None, expected_archive_chain_head_sha256: 'str | None' = None) -> 'dict[str, Any]'",
    "LocalAIConnector.inspect_memory": "(self, bundle: 'ContextBundle | Mapping[str, Any]') -> 'dict[str, Any]'",
    "LocalAIConnector.handle_request": "(self, request: 'Mapping[str, Any]') -> 'dict[str, Any]'",
    "SourceArchive.append": "(self, records: 'Iterable[SourceRecord]', *, expected_chain_head: 'str | None' = None) -> 'int'",
    "SourceArchive.load": "(self, *, expected_chain_head: 'str | None' = None) -> 'list[SourceRecord]'",
    "SourceArchive.verify": "(self, *, expected_chain_head: 'str | None' = None) -> 'ArchiveReport'",
}

EXPECTED_MODULE_SIGNATURES = {
    "compose_context_window": "(sources: 'Iterable[SourceRecord | Mapping[str, Any]]', *, current_turn_id: 'str', budget: 'ContextWindowBudget', token_counter: 'ExactTokenCounterAdapter', fixed_input_sha256: 'str | None' = None, policy: 'CompilationPolicy | None' = None, source_limits: 'SourceLimits | None' = None, compilation_limits: 'CompilationLimits | None' = None, degradation_policy: 'ContextWindowDegradationPolicy | None' = None) -> 'ContextWindowPrototype'",
    "compose_materialized_context_window": "(sources: 'Iterable[SourceRecord | Mapping[str, Any]]', *, current_turn_id: 'str', budget: 'ContextWindowBudget', token_counter: 'ExactTokenCounterAdapter', fixed_input_sha256: 'str | None' = None, allocation_plan_sha256: 'str | None' = None, policy: 'CompilationPolicy | None' = None, source_limits: 'SourceLimits | None' = None, compilation_limits: 'CompilationLimits | None' = None, degradation_policy: 'ContextWindowDegradationPolicy | None' = None) -> 'MaterializedContextWindow'",
}


def _resolve(path: str) -> object:
    value: object = context_compiler
    for name in path.split("."):
        value = getattr(value, name)
    return value


def test_public_exports_are_exact_and_resolvable() -> None:
    assert tuple(context_compiler.__all__) == EXPECTED_EXPORTS
    assert len(EXPECTED_EXPORTS) == len(set(EXPECTED_EXPORTS))
    assert all(hasattr(context_compiler, name) for name in EXPECTED_EXPORTS)


def test_critical_public_signatures_are_exact() -> None:
    actual = {path: str(inspect.signature(_resolve(path))) for path in EXPECTED_SIGNATURES}
    assert actual == EXPECTED_SIGNATURES


def test_context_window_module_signatures_are_exact() -> None:
    actual = {
        "compose_context_window": str(inspect.signature(compose_context_window)),
        "compose_materialized_context_window": str(
            inspect.signature(compose_materialized_context_window)
        ),
    }
    assert actual == EXPECTED_MODULE_SIGNATURES


def test_context_window_error_is_the_stable_root_exception() -> None:
    assert context_compiler.ContextWindowError is ModuleContextWindowError
    error = context_compiler.ContextWindowError(
        "bounded refusal",
        reason="mandatory_components_do_not_fit",
    )
    assert error.reason == "mandatory_components_do_not_fit"
    assert error.diagnostic is None


def test_compilation_policy_preserves_legacy_positional_order() -> None:
    policy = context_compiler.CompilationPolicy(4_000, 5.0, False, False, True, True, True, 3.5)
    assert policy.chars_per_token == 3.5
    assert policy.fail_on_primary_extractor_error is False
