"""Loss-resistant, provenance-linked context compilation for long-running agents."""

from .archive import SourceArchive
from .artifact_diff import ARTIFACT_DIFF_SCHEMA, diff_artifacts
from .artifact_inspection import (
    ARTIFACT_INSPECTION_SCHEMA,
    render_artifact_text,
    summarize_artifact,
)
from .compiler import ContextCompiler
from .extractors import ModelExtractor, RuleBasedExtractor
from .io import validate_artifact_envelope
from .isolation import CompilationIsolationError
from .limits import ArtifactLimitError, ArtifactLimits, SourceLimitError, SourceLimits
from .local_qwen import LmsQwenCompletion, LocalQwenError
from .models import (
    COMPILATION_METRICS_SCHEMA,
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

__all__ = [
    "ArtifactLimitError",
    "ArtifactLimits",
    "ARTIFACT_DIFF_SCHEMA",
    "ARTIFACT_INSPECTION_SCHEMA",
    "COMPILATION_METRICS_SCHEMA",
    "CompilationIsolationError",
    "CompilationMetrics",
    "CompilationPolicy",
    "CompiledMemory",
    "ContextCompiler",
    "MemoryItem",
    "MemoryKind",
    "MemoryStatus",
    "ModelExtractor",
    "LmsQwenCompletion",
    "LocalQwenError",
    "ProvenanceSpan",
    "RuleBasedExtractor",
    "SourceArchive",
    "SourceLimitError",
    "SourceLimits",
    "SourceRecord",
    "VerificationReport",
    "diff_artifacts",
    "render_artifact_text",
    "summarize_artifact",
    "validate_artifact_envelope",
]

__version__ = "0.1.0"
