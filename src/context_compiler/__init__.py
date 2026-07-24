"""Loss-resistant, provenance-linked context compilation for long-running agents."""

from .archive import SourceArchive
from .compiler import ContextCompiler
from .extractors import ModelExtractor, RuleBasedExtractor
from .local_qwen import LmsQwenCompletion, LocalQwenError
from .models import (
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
    "SourceRecord",
    "VerificationReport",
]

__version__ = "0.1.0"
