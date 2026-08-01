"""OpenHands integration for the loss-resistant context compiler.

Importing this package deliberately does not import OpenHands.  Host-specific
imports are isolated behind the compatibility bridge and are performed only
after the exact supported release has been checked.
"""

from .authority import (
    AuthorityError,
    AuthorityPolicy,
    AuthorityVerificationError,
    issue_authority_receipt,
)
from .callback import DurableEventCallback, IntegrationPoisonedError
from .compatibility import (
    OPENHANDS_API_MANIFEST_SHA256,
    OPENHANDS_HOST_COMMIT,
    OPENHANDS_HOST_VERSION,
    OPENHANDS_SDK_COMMIT,
    OPENHANDS_SDK_VERSION,
    CompatibilityManifestError,
    verify_pin_manifest,
)
from .deterministic import deterministic_bundle_copy
from .event_map import (
    SUPPORTED_EVENT_KINDS,
    EventMappingError,
    map_host_event,
    validate_atomic_event_pair,
)
from .host_guard import (
    GuardedLocalConversation,
    HostBypassRefusedError,
    LiveCompatibilityError,
    LiveRequestAccountingUnavailableError,
    inspect_live_compatibility,
    load_pinned_host_api,
    pinned_callback,
)
from .request_ledger import (
    REQUIRED_COMPONENT_CATEGORIES,
    FinalRequestLedger,
    RequestLedgerError,
    RequestLedgerLimits,
    build_final_request_ledger,
    validate_final_request_ledger,
)
from .semantic import (
    SEMANTIC_RESULT_SCHEMA,
    semantic_result_digest,
    semantic_result_projection,
)
from .session import OpenHandsSession
from .storage import SQLiteGenerationStore
from .tokenizer import CanonicalUtf8ByteTokenizer

__all__ = [
    "AuthorityError",
    "AuthorityPolicy",
    "AuthorityVerificationError",
    "CanonicalUtf8ByteTokenizer",
    "CompatibilityManifestError",
    "DurableEventCallback",
    "EventMappingError",
    "FinalRequestLedger",
    "GuardedLocalConversation",
    "HostBypassRefusedError",
    "IntegrationPoisonedError",
    "LiveCompatibilityError",
    "LiveRequestAccountingUnavailableError",
    "OPENHANDS_API_MANIFEST_SHA256",
    "OPENHANDS_HOST_COMMIT",
    "OPENHANDS_HOST_VERSION",
    "OPENHANDS_SDK_COMMIT",
    "OPENHANDS_SDK_VERSION",
    "OpenHandsSession",
    "REQUIRED_COMPONENT_CATEGORIES",
    "RequestLedgerError",
    "RequestLedgerLimits",
    "SEMANTIC_RESULT_SCHEMA",
    "SQLiteGenerationStore",
    "SUPPORTED_EVENT_KINDS",
    "build_final_request_ledger",
    "deterministic_bundle_copy",
    "inspect_live_compatibility",
    "issue_authority_receipt",
    "load_pinned_host_api",
    "map_host_event",
    "pinned_callback",
    "semantic_result_digest",
    "semantic_result_projection",
    "validate_atomic_event_pair",
    "validate_final_request_ledger",
    "verify_pin_manifest",
]

__version__ = "0.1.0a19"
