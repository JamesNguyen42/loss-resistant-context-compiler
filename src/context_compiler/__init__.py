"""Loss-resistant, provenance-linked context compilation for long-running agents."""

from .archive import (
    ARCHIVE_ENTRY_SCHEMA,
    ARCHIVE_GENESIS_SHA256,
    ARCHIVE_REPORT_SCHEMA,
    LEGACY_ARCHIVE_SCHEMA,
    ArchiveReport,
    SourceArchive,
)
from .artifact_diff import ARTIFACT_DIFF_SCHEMA, diff_artifacts
from .artifact_inspection import (
    ARTIFACT_INSPECTION_SCHEMA,
    render_artifact_text,
    summarize_artifact,
)
from .compiler import ContextCompiler
from .extractors import (
    CompositeExtractor,
    DomainLabelExtractor,
    ExtractionResult,
    Extractor,
    LiteralModelExtractor,
    ModelExtractor,
    RuleBasedExtractor,
)
from .io import validate_artifact_envelope
from .isolation import CompilationIsolationError
from .limits import (
    ArtifactLimitError,
    ArtifactLimits,
    CompilationLimitError,
    CompilationLimits,
    SourceLimitError,
    SourceLimits,
)
from .local_qwen import LmsQwenCompletion, LocalQwenError
from .models import (
    COMPILATION_METRICS_SCHEMA,
    MAX_SOURCE_ID_CHARS,
    MAX_SOURCE_ROLE_CHARS,
    MAX_SOURCE_TIMESTAMP_CHARS,
    CompilationMetrics,
    CompilationPolicy,
    CompiledMemory,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    ProvenanceSpan,
    SourceRecord,
    VerificationReport,
)
from .redaction import (
    REDACTION_REPORT_SCHEMA,
    REDACTION_VERIFICATION_SCHEMA,
    SECRET_DETECTOR_NAMES,
    RedactionError,
    RedactionFinding,
    RedactionLimitError,
    RedactionPolicy,
    RedactionResult,
    redact_sources,
    verify_redaction_report_hash,
    verify_redaction_result,
)
from .schema_compatibility import (
    ARTIFACT_SCHEMA_COMPATIBILITY_SCHEMA,
    artifact_schema_registry,
    artifact_schema_support,
)

__all__ = [
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
    "DomainLabelExtractor",
    "ExtractionResult",
    "Extractor",
    "MemoryItem",
    "MemoryKind",
    "MemoryStatus",
    "ModelExtractor",
    "LmsQwenCompletion",
    "LocalQwenError",
    "LiteralModelExtractor",
    "LEGACY_ARCHIVE_SCHEMA",
    "ProvenanceSpan",
    "REDACTION_REPORT_SCHEMA",
    "REDACTION_VERIFICATION_SCHEMA",
    "RedactionError",
    "RedactionFinding",
    "RedactionLimitError",
    "RedactionPolicy",
    "RedactionResult",
    "RuleBasedExtractor",
    "SECRET_DETECTOR_NAMES",
    "SourceArchive",
    "SourceLimitError",
    "SourceLimits",
    "SourceRecord",
    "VerificationReport",
    "artifact_schema_registry",
    "artifact_schema_support",
    "diff_artifacts",
    "render_artifact_text",
    "redact_sources",
    "summarize_artifact",
    "validate_artifact_envelope",
    "verify_redaction_report_hash",
    "verify_redaction_result",
]

__version__ = "0.1.0"
