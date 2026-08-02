"""Coordinator-only full-cohort SWE-bench prediction evidence.

This source/sdist-only module reconciles opaque candidate captures against an
exact source/key-bound task projection, revalidates every successful repository
preparation, and writes the official three-field prediction JSONL.  It does not
run a candidate, apply a patch, invoke a grader, or establish a score.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from context_compiler.atomic import atomic_write_text

from .json_io import (
    StrictJsonError,
    StrictJsonLimits,
    load_strict_json_file,
    read_bounded_regular_file,
)
from .swebench import (
    SweBenchError,
    VerifiedSweBenchSource,
    _revalidate_verified_source,
    verify_task_input_binding,
)
from .swebench import (
    _canonical_sha256 as _source_canonical_sha256,
)
from .swebench_repository import (
    PreparedSweBenchRepository,
    RepositoryPreparationError,
    verify_prepared_repository,
)

PREDICTION_LEDGER_SCHEMA = "ctxc-swebench-prediction-ledger-0.1"
OFFICIAL_JSONL_FIELDS = ("instance_id", "model_name_or_path", "model_patch")

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_REVISION_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_OPAQUE_ID_RE = re.compile(r"task-[0-9a-f]{64}\Z")
_INSTANCE_RE = re.compile(
    r"[A-Za-z0-9_.-]{1,100}__[A-Za-z0-9_.-]{1,100}-[0-9]{1,12}\Z"
)
_CODE_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_MAX_HARD_ARTIFACT_BYTES = 512 * 1024 * 1024
_MAX_HARD_LEDGER_BYTES = 64 * 1024 * 1024
_MAX_HARD_PATCH_BYTES = 64 * 1024 * 1024
_MAX_HARD_VIOLATIONS = 1_000_000
_MAX_HARD_CAPTURE_BYTES = 1024 * 1024 * 1024
_MAX_SELECTED_TASKS = 10_000

_PREPARATION_STATUSES = frozenset({"not-attempted", "refused", "prepared"})
_CAPTURE_STATUSES = frozenset({"recorded", "invalid", "oversized"})
_DISPOSITIONS = (
    "repository-not-attempted",
    "repository-preparation-refused",
    "repository-prepared-no-prediction",
    "prediction-recorded",
)


class PredictionLedgerError(ValueError):
    """Prediction inputs or retained evidence violated the frozen boundary."""


class _PatchTooLarge(PredictionLedgerError):
    """Internal signal that strict UTF-8 patch bytes exceed their bound."""


def _bounded_integer(
    value: object,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if (
        type(value) is not int
        or not minimum <= value <= maximum
    ):
        raise PredictionLedgerError(
            f"{label} must be an integer from {minimum} through {maximum}"
        )
    return value


def _sha256(value: object, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise PredictionLedgerError(f"{label} must be lowercase SHA-256")
    return value


def _code(value: object, *, label: str) -> str:
    if type(value) is not str or _CODE_RE.fullmatch(value) is None:
        raise PredictionLedgerError(f"{label} must be a stable lowercase code")
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise PredictionLedgerError(
            "prediction evidence is not canonical finite JSON"
        ) from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(nested) for key, nested in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(nested) for nested in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(nested) for nested in value]
    return value


def _object(value: Any, *, fields: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise PredictionLedgerError(f"{label} fields are invalid")
    return value


def _boolean(value: object, *, expected: bool, label: str) -> None:
    if value is not expected:
        raise PredictionLedgerError(f"{label} must equal {expected!r}")


def _portable_label(value: object, *, label: str, maximum: int = 512) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise PredictionLedgerError(f"{label} must be a bounded non-empty string")
    if value != unicodedata.normalize("NFC", value):
        raise PredictionLedgerError(f"{label} must be NFC")
    if value.startswith(("/", "\\", ".")) or "\\" in value or ":" in value:
        raise PredictionLedgerError(f"{label} must be a portable label, not a path")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise PredictionLedgerError(f"{label} has an invalid component")
    if any(
        character == "\ufeff"
        or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        for character in value
    ):
        raise PredictionLedgerError(f"{label} contains an unsafe character")
    return value


@dataclass(frozen=True, slots=True)
class PredictionLedgerLimits:
    """Finite patch, artifact, and reconciliation limits."""

    max_patch_bytes: int = 4 * 1024 * 1024
    max_jsonl_bytes: int = 64 * 1024 * 1024
    max_ledger_bytes: int = 32 * 1024 * 1024
    max_protocol_violations: int = 20_000
    max_candidate_captures: int = 30_000
    max_total_capture_bytes: int = 128 * 1024 * 1024

    def __post_init__(self) -> None:
        values = {
            "max_patch_bytes": (self.max_patch_bytes, _MAX_HARD_PATCH_BYTES),
            "max_jsonl_bytes": (self.max_jsonl_bytes, _MAX_HARD_ARTIFACT_BYTES),
            "max_ledger_bytes": (self.max_ledger_bytes, _MAX_HARD_LEDGER_BYTES),
            "max_protocol_violations": (
                self.max_protocol_violations,
                _MAX_HARD_VIOLATIONS,
            ),
            "max_candidate_captures": (
                self.max_candidate_captures,
                _MAX_HARD_VIOLATIONS,
            ),
            "max_total_capture_bytes": (
                self.max_total_capture_bytes,
                _MAX_HARD_CAPTURE_BYTES,
            ),
        }
        for name, (value, maximum) in values.items():
            object.__setattr__(
                self,
                name,
                _bounded_integer(value, label=name, minimum=1, maximum=maximum),
            )
        if self.max_patch_bytes * 6 + 4096 > self.max_jsonl_bytes:
            raise PredictionLedgerError(
                "max_jsonl_bytes must cover worst-case canonical patch escaping"
            )
        if self.max_protocol_violations * 512 > self.max_ledger_bytes:
            raise PredictionLedgerError(
                "max_ledger_bytes must cover bounded protocol-violation evidence"
            )


_LIMIT_FIELDS = (
    "max_patch_bytes",
    "max_jsonl_bytes",
    "max_ledger_bytes",
    "max_protocol_violations",
    "max_candidate_captures",
    "max_total_capture_bytes",
)


def _limits_document(limits: PredictionLedgerLimits) -> dict[str, int]:
    return {name: getattr(limits, name) for name in _LIMIT_FIELDS}


def _revalidate_limits(value: object) -> PredictionLedgerLimits:
    if type(value) is not PredictionLedgerLimits:
        raise PredictionLedgerError("limits must be PredictionLedgerLimits")
    try:
        return PredictionLedgerLimits(**_limits_document(value))
    except (AttributeError, TypeError, PredictionLedgerError) as exc:
        raise PredictionLedgerError("prediction limits are invalid") from exc


@dataclass(frozen=True, slots=True)
class RetainedCaptureEvidence:
    """Pathless evidence for a bounded candidate-output observation."""

    observed_bytes: int
    captured_bytes: int
    captured_sha256: str
    complete: bool

    def __post_init__(self) -> None:
        observed = _bounded_integer(
            self.observed_bytes,
            label="observed_bytes",
            minimum=0,
            maximum=_MAX_HARD_ARTIFACT_BYTES,
        )
        captured = _bounded_integer(
            self.captured_bytes,
            label="captured_bytes",
            minimum=0,
            maximum=_MAX_HARD_PATCH_BYTES + 1,
        )
        if captured > observed:
            raise PredictionLedgerError("captured_bytes exceeds observed_bytes")
        _sha256(self.captured_sha256, label="captured_sha256")
        if not isinstance(self.complete, bool):
            raise PredictionLedgerError("complete must be a boolean")
        if self.complete is not (captured == observed):
            raise PredictionLedgerError(
                "complete must exactly describe the retained capture"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "observed_bytes": self.observed_bytes,
            "captured_bytes": self.captured_bytes,
            "captured_sha256": self.captured_sha256,
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True)
class RepositoryPreparationOutcome:
    """One explicit repository outcome keyed by an opaque candidate id."""

    opaque_task_id: str
    status: str
    prepared: PreparedSweBenchRepository | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if type(self.opaque_task_id) is not str or _OPAQUE_ID_RE.fullmatch(
            self.opaque_task_id
        ) is None:
            raise PredictionLedgerError("preparation opaque_task_id is invalid")
        if type(self.status) is not str or self.status not in _PREPARATION_STATUSES:
            raise PredictionLedgerError("repository preparation status is invalid")
        if self.status == "prepared":
            if not isinstance(self.prepared, PreparedSweBenchRepository):
                raise PredictionLedgerError("prepared outcome requires a repository")
            if self.failure_code is not None:
                raise PredictionLedgerError("prepared outcome cannot have a failure code")
        elif self.prepared is not None:
            raise PredictionLedgerError("unprepared outcome cannot retain a repository")
        elif self.status == "refused":
            _code(self.failure_code, label="preparation failure_code")
        elif self.failure_code is not None:
            raise PredictionLedgerError(
                "not-attempted outcome cannot have a failure code"
            )


@dataclass(frozen=True, slots=True)
class CandidatePatchCapture:
    """One bounded candidate capture; candidate ids remain opaque here."""

    opaque_task_id: object
    status: str
    model_patch: object | None = None
    capture: RetainedCaptureEvidence | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not str or self.status not in _CAPTURE_STATUSES:
            raise PredictionLedgerError("candidate capture status is invalid")
        if type(self.capture) is not RetainedCaptureEvidence:
            raise PredictionLedgerError("candidate capture evidence is required")
        if self.status == "recorded":
            if type(self.model_patch) is not str:
                raise PredictionLedgerError("recorded capture requires patch text")
            if self.failure_code is not None:
                raise PredictionLedgerError("recorded capture cannot have a failure code")
        else:
            if self.model_patch is not None:
                raise PredictionLedgerError("rejected capture cannot retain patch text")
            _code(self.failure_code, label="candidate failure_code")


@dataclass(frozen=True, slots=True)
class SystemIdentity:
    """Portable coordinator labels and exact implementation digests."""

    code_revision: str
    repository_clean: bool
    model_name_or_path: str
    model_sha256: str
    agent_sha256: str
    prompt_sha256: str
    tool_sha256: str
    controller_sha256: str

    def __post_init__(self) -> None:
        if type(self.code_revision) is not str or _REVISION_RE.fullmatch(
            self.code_revision
        ) is None:
            raise PredictionLedgerError("code_revision must be a lowercase Git id")
        if not isinstance(self.repository_clean, bool):
            raise PredictionLedgerError("repository_clean must be a boolean")
        _portable_label(self.model_name_or_path, label="model_name_or_path")
        for name in (
            "model_sha256",
            "agent_sha256",
            "prompt_sha256",
            "tool_sha256",
            "controller_sha256",
        ):
            _sha256(getattr(self, name), label=name)

    def to_dict(self) -> dict[str, object]:
        return {
            "code_revision": self.code_revision,
            "repository_clean": self.repository_clean,
            "model_name_or_path": self.model_name_or_path,
            "model_sha256": self.model_sha256,
            "agent_sha256": self.agent_sha256,
            "prompt_sha256": self.prompt_sha256,
            "tool_sha256": self.tool_sha256,
            "controller_sha256": self.controller_sha256,
        }


@dataclass(frozen=True, slots=True)
class VerifiedPredictionLedger:
    """Runtime JSONL path, preparation handles, and frozen ledger evidence."""

    jsonl_path: Path
    preparations: tuple[RepositoryPreparationOutcome, ...]
    limits: PredictionLedgerLimits
    document: Mapping[str, Any]

    @property
    def prediction_ledger_sha256(self) -> str:
        return str(self.document["prediction_ledger_sha256"])

    @property
    def claim_ready(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return _thaw_json(self.document)


def _capture_evidence(value: object, *, label: str) -> RetainedCaptureEvidence:
    if type(value) is not RetainedCaptureEvidence:
        raise PredictionLedgerError(f"{label} must be RetainedCaptureEvidence")
    try:
        return RetainedCaptureEvidence(**value.to_dict())
    except (AttributeError, TypeError, PredictionLedgerError) as exc:
        raise PredictionLedgerError(f"{label} is invalid") from exc


def _system_identity(value: object) -> SystemIdentity:
    if type(value) is not SystemIdentity:
        raise PredictionLedgerError("system must be SystemIdentity")
    try:
        return SystemIdentity(**value.to_dict())
    except (AttributeError, TypeError, PredictionLedgerError) as exc:
        raise PredictionLedgerError("system identity is invalid") from exc


def _patch_bytes(value: str, *, max_bytes: int) -> bytes:
    if len(value) > max_bytes:
        raise _PatchTooLarge("patch text exceeds its UTF-8 byte limit")
    chunks: list[bytes] = []
    total = 0
    for start in range(0, len(value), 64 * 1024):
        try:
            encoded = value[start : start + 64 * 1024].encode(
                "utf-8",
                errors="strict",
            )
        except UnicodeError as exc:
            raise PredictionLedgerError("patch text is not strict UTF-8") from exc
        total += len(encoded)
        if total > max_bytes:
            raise _PatchTooLarge("patch text exceeds its UTF-8 byte limit")
        chunks.append(encoded)
    return b"".join(chunks)


def _capture_document(value: RetainedCaptureEvidence) -> dict[str, object]:
    return value.to_dict()


def _safe_token_digest(value: object) -> str:
    if type(value) is not str:
        return hashlib.sha256(b"non-string-opaque-task-id").hexdigest()
    prefix = value[:1024].encode("utf-8", errors="surrogatepass")
    framing = len(value).to_bytes(8, "big", signed=False) + prefix
    return hashlib.sha256(b"bounded-opaque-id\0" + framing).hexdigest()


def _output_path(value: str | Path, *, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise PredictionLedgerError(f"{label} must be absolute")
    name = path.name
    if (
        not name
        or name != unicodedata.normalize("NFC", name)
        or name.endswith((".", " "))
        or ":" in name
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in name)
    ):
        raise PredictionLedgerError(f"{label} filename is not portable")
    return path


def _single_link_regular(path: Path, *, label: str) -> tuple[int, int]:
    try:
        value = path.lstat()
    except OSError as exc:
        raise PredictionLedgerError(f"could not inspect {label}") from exc
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if (
        not stat.S_ISREG(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or value.st_nlink != 1
        or bool(getattr(value, "st_file_attributes", 0) & reparse)
    ):
        raise PredictionLedgerError(f"{label} must be a single-link regular file")
    return value.st_dev, value.st_ino


def _cleanup_owned_file(
    path: Path,
    identity: tuple[int, int],
    *,
    label: str,
) -> None:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PredictionLedgerError(f"could not inspect failed {label}") from exc
    if (value.st_dev, value.st_ino) != identity or not stat.S_ISREG(value.st_mode):
        raise PredictionLedgerError(
            f"failed {label} path no longer identifies the owned output"
        )
    try:
        os.unlink(path)
    except OSError as exc:
        raise PredictionLedgerError(f"could not remove failed {label}") from exc


def _jsonl_line(instance_id: str, model: str, patch: str | None) -> bytes:
    return _canonical_json_bytes(
        {
            "instance_id": instance_id,
            "model_name_or_path": model,
            "model_patch": patch,
        }
    ) + b"\n"


def _source_and_tasks(
    source: VerifiedSweBenchSource,
    task_input: Any,
    opaque_key: bytes,
) -> tuple[Any, tuple[Mapping[str, str], ...], dict[str, Any]]:
    try:
        suite, records = _revalidate_verified_source(source)
        verified_task_input = verify_task_input_binding(
            task_input,
            source,
            opaque_key=opaque_key,
        )
    except SweBenchError as exc:
        raise PredictionLedgerError(str(exc)) from exc
    if len(records) > _MAX_SELECTED_TASKS:
        raise PredictionLedgerError("selected cohort exceeds the task limit")
    return suite, records, verified_task_input


def _revalidate_preparations(
    preparations: Sequence[RepositoryPreparationOutcome],
    *,
    source: VerifiedSweBenchSource,
    task_input: Mapping[str, Any],
) -> tuple[tuple[RepositoryPreparationOutcome, ...], list[dict[str, Any]]]:
    if type(preparations) not in (list, tuple):
        raise PredictionLedgerError("preparations must be a materialized sequence")
    tasks = task_input["tasks"]
    retained_inputs = tuple(preparations[: len(tasks) + 1])
    if len(retained_inputs) != len(tasks):
        raise PredictionLedgerError(
            "preparations must cover the complete selected cohort"
        )
    retained: list[RepositoryPreparationOutcome] = []
    documents: list[dict[str, Any]] = []
    for ordinal, (raw, task) in enumerate(zip(retained_inputs, tasks, strict=True)):
        if type(raw) is not RepositoryPreparationOutcome:
            raise PredictionLedgerError(
                f"preparations[{ordinal}] must be RepositoryPreparationOutcome"
            )
        try:
            outcome = RepositoryPreparationOutcome(
                opaque_task_id=raw.opaque_task_id,
                status=raw.status,
                prepared=raw.prepared,
                failure_code=raw.failure_code,
            )
        except (AttributeError, TypeError, PredictionLedgerError) as exc:
            raise PredictionLedgerError(
                f"preparations[{ordinal}] is invalid"
            ) from exc
        if outcome.opaque_task_id != task["opaque_task_id"]:
            raise PredictionLedgerError(
                f"preparations[{ordinal}] opaque task id/order mismatch"
            )
        preparation_sha256: str | None = None
        if outcome.status == "prepared":
            assert outcome.prepared is not None
            try:
                verified = verify_prepared_repository(outcome.prepared, source=source)
            except RepositoryPreparationError as exc:
                raise PredictionLedgerError(
                    f"preparations[{ordinal}] failed source-bound verification: {exc}"
                ) from exc
            binding = verified.document["source_binding"]
            if (
                binding["source_ordinal"] != ordinal
                or binding["instance_id"]
                != source.records[ordinal]["instance_id"]
            ):
                raise PredictionLedgerError(
                    f"preparations[{ordinal}] source row mismatch"
                )
            preparation_sha256 = verified.preparation_sha256
        retained.append(outcome)
        documents.append(
            {
                "status": outcome.status,
                "preparation_sha256": preparation_sha256,
                "failure_code": outcome.failure_code,
            }
        )
    return tuple(retained), documents


@dataclass(frozen=True, slots=True)
class _CaptureAssessment:
    ordinal: int
    opaque_task_id: str | None
    token_digest: str
    status: str
    patch: str | None
    patch_bytes: bytes | None
    capture: RetainedCaptureEvidence | None
    failure_code: str


def _assess_capture(
    raw: object,
    *,
    ordinal: int,
    limits: PredictionLedgerLimits,
    remaining_capture_bytes: int,
) -> _CaptureAssessment:
    if type(raw) is not CandidatePatchCapture:
        return _CaptureAssessment(
            ordinal,
            None,
            _safe_token_digest(None),
            "invalid",
            None,
            None,
            None,
            "malformed-candidate-capture",
        )
    raw_id = getattr(raw, "opaque_task_id", None)
    valid_id = (
        raw_id
        if type(raw_id) is str and _OPAQUE_ID_RE.fullmatch(raw_id) is not None
        else None
    )
    token_digest = _safe_token_digest(raw_id)
    try:
        status = raw.status
        model_patch = raw.model_patch
        failure_code = raw.failure_code
        evidence = _capture_evidence(
            raw.capture,
            label=f"captures[{ordinal}].capture",
        )
    except (AttributeError, TypeError, PredictionLedgerError):
        return _CaptureAssessment(
            ordinal,
            valid_id,
            token_digest,
            "invalid",
            None,
            None,
            None,
            "malformed-candidate-capture",
        )
    if evidence.captured_bytes > remaining_capture_bytes:
        raise PredictionLedgerError(
            "candidate captures exceed the aggregate byte limit"
        )
    if evidence.captured_bytes > limits.max_patch_bytes + 1:
        return _CaptureAssessment(
            ordinal,
            valid_id,
            token_digest,
            "invalid",
            None,
            None,
            evidence,
            "capture-limit-mismatch",
        )
    if evidence.observed_bytes > limits.max_patch_bytes:
        expected_captured = min(
            evidence.observed_bytes,
            limits.max_patch_bytes + 1,
        )
        code = (
            "candidate-output-oversized"
            if evidence.captured_bytes == expected_captured
            else "capture-limit-mismatch"
        )
        return _CaptureAssessment(
            ordinal,
            valid_id,
            token_digest,
            "oversized" if code == "candidate-output-oversized" else "invalid",
            None,
            None,
            evidence,
            code,
        )
    if type(status) is not str or status not in _CAPTURE_STATUSES:
        return _CaptureAssessment(
            ordinal,
            valid_id,
            token_digest,
            "invalid",
            None,
            None,
            evidence,
            "invalid-candidate-status",
        )
    if status == "oversized":
        return _CaptureAssessment(
            ordinal,
            valid_id,
            token_digest,
            "invalid",
            None,
            None,
            evidence,
            "invalid-oversized-evidence",
        )
    if status != "recorded":
        try:
            stable_failure = _code(
                failure_code,
                label=f"captures[{ordinal}].failure_code",
            )
        except PredictionLedgerError:
            stable_failure = "invalid-candidate-failure-code"
        return _CaptureAssessment(
            ordinal,
            valid_id,
            token_digest,
            status,
            None,
            None,
            evidence,
            stable_failure,
        )
    if type(model_patch) is not str or failure_code is not None:
        return _CaptureAssessment(
            ordinal,
            valid_id,
            token_digest,
            "invalid",
            None,
            None,
            evidence,
            "invalid-recorded-capture",
        )
    if not evidence.complete or len(model_patch) > evidence.observed_bytes:
        return _CaptureAssessment(
            ordinal,
            valid_id,
            token_digest,
            "invalid",
            None,
            None,
            evidence,
            "capture-evidence-mismatch",
        )
    try:
        encoded = _patch_bytes(
            model_patch,
            max_bytes=limits.max_patch_bytes,
        )
    except _PatchTooLarge:
        return _CaptureAssessment(
            ordinal,
            valid_id,
            token_digest,
            "oversized",
            None,
            None,
            evidence,
            "candidate-output-oversized",
        )
    except PredictionLedgerError:
        return _CaptureAssessment(
            ordinal,
            valid_id,
            token_digest,
            "invalid",
            None,
            None,
            evidence,
            "invalid-patch-text",
        )
    digest = hashlib.sha256(encoded).hexdigest()
    if (
        not evidence.complete
        or evidence.observed_bytes != len(encoded)
        or evidence.captured_bytes != len(encoded)
        or evidence.captured_sha256 != digest
    ):
        return _CaptureAssessment(
            ordinal,
            valid_id,
            token_digest,
            "invalid",
            None,
            None,
            evidence,
            "capture-evidence-mismatch",
        )
    return _CaptureAssessment(
        ordinal,
        valid_id,
        token_digest,
        "recorded",
        model_patch,
        encoded,
        evidence,
        "prediction-recorded",
    )


def _protocol_violation(
    assessment: _CaptureAssessment,
    *,
    code: str,
) -> dict[str, Any]:
    return {
        "capture_ordinal": assessment.ordinal,
        "code": code,
        "opaque_task_id_sha256": assessment.token_digest,
        "capture": (
            _capture_document(assessment.capture)
            if assessment.capture is not None
            else None
        ),
    }


def _ensure_violation_limit(
    violations: list[dict[str, Any]],
    limits: PredictionLedgerLimits,
) -> None:
    if len(violations) > limits.max_protocol_violations:
        raise PredictionLedgerError("candidate protocol violations exceed the limit")


def _preflight_jsonl_destination(
    path: Path,
    preparations: Sequence[RepositoryPreparationOutcome],
) -> None:
    try:
        destination = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PredictionLedgerError("JSONL output path could not be resolved") from exc
    for outcome in preparations:
        if outcome.prepared is None:
            continue
        try:
            if destination.is_relative_to(outcome.prepared.path.resolve(strict=True)):
                raise PredictionLedgerError(
                    "prediction JSONL cannot be written inside a prepared tree"
                )
            if destination.is_relative_to(
                outcome.prepared.mirror.path.resolve(strict=True)
            ):
                raise PredictionLedgerError(
                    "prediction output cannot be written inside a bare mirror"
                )
        except (OSError, RuntimeError, ValueError) as exc:
            if isinstance(exc, PredictionLedgerError):
                raise
            raise PredictionLedgerError(
                "prepared tree boundary could not be inspected"
            ) from exc


def build_prediction_ledger(
    source: VerifiedSweBenchSource,
    task_input: Any,
    *,
    opaque_key: bytes,
    preparations: Sequence[RepositoryPreparationOutcome],
    captures: Sequence[CandidatePatchCapture],
    system: SystemIdentity,
    jsonl_path: str | Path,
    limits: PredictionLedgerLimits | None = None,
) -> VerifiedPredictionLedger:
    """Reconcile a complete cohort and exclusively emit canonical JSONL."""

    selected_limits = (
        PredictionLedgerLimits()
        if limits is None
        else _revalidate_limits(limits)
    )
    selected_system = _system_identity(system)
    suite, records, verified_task_input = _source_and_tasks(
        source,
        task_input,
        opaque_key,
    )
    retained_preparations, preparation_documents = _revalidate_preparations(
        preparations,
        source=source,
        task_input=verified_task_input,
    )
    if type(captures) not in (list, tuple):
        raise PredictionLedgerError("captures must be a materialized sequence")
    retained_captures = tuple(
        captures[: selected_limits.max_candidate_captures + 1]
    )
    if len(retained_captures) > selected_limits.max_candidate_captures:
        raise PredictionLedgerError("candidate capture count exceeds its finite bound")

    output_path = _output_path(jsonl_path, label="prediction JSONL output")
    _preflight_jsonl_destination(output_path, retained_preparations)

    known_ids = {
        str(task["opaque_task_id"])
        for task in verified_task_input["tasks"]
    }
    grouped: dict[str, list[_CaptureAssessment]] = defaultdict(list)
    violations: list[dict[str, Any]] = []
    total_capture_bytes = 0
    for ordinal, raw in enumerate(retained_captures):
        assessment = _assess_capture(
            raw,
            ordinal=ordinal,
            limits=selected_limits,
            remaining_capture_bytes=(
                selected_limits.max_total_capture_bytes - total_capture_bytes
            ),
        )
        if assessment.capture is not None:
            total_capture_bytes += assessment.capture.captured_bytes
            if total_capture_bytes > selected_limits.max_total_capture_bytes:
                raise PredictionLedgerError(
                    "candidate captures exceed the aggregate byte limit"
                )
        if assessment.opaque_task_id is None:
            violations.append(
                _protocol_violation(
                    assessment,
                    code=(
                        "malformed-candidate-capture"
                        if assessment.failure_code == "malformed-candidate-capture"
                        else "invalid-opaque-task-id"
                    ),
                )
            )
        elif assessment.opaque_task_id not in known_ids:
            violations.append(
                _protocol_violation(assessment, code="unexpected-opaque-task-id")
            )
        else:
            grouped[assessment.opaque_task_id].append(assessment)
        _ensure_violation_limit(violations, selected_limits)

    task_documents: list[dict[str, Any]] = []
    jsonl_lines: list[bytes] = []
    jsonl_byte_count = 0
    disposition_counts: Counter[str] = Counter()
    preparation_counts: Counter[str] = Counter()
    prediction_count = 0

    for ordinal, (record, task, repository) in enumerate(
        zip(
            records,
            verified_task_input["tasks"],
            preparation_documents,
            strict=True,
        )
    ):
        opaque_id = str(task["opaque_task_id"])
        candidates = grouped.get(opaque_id, [])
        preparation_counts[str(repository["status"])] += 1
        patch: str | None = None
        patch_bytes: bytes | None = None
        capture_document: dict[str, object] | None = None
        candidate_capture_ordinal: int | None = None
        candidate_failure_code: str | None = None

        if repository["status"] == "not-attempted":
            disposition = "repository-not-attempted"
            absence_code = "repository-not-attempted"
        elif repository["status"] == "refused":
            disposition = "repository-preparation-refused"
            absence_code = "repository-preparation-refused"
        elif not candidates:
            disposition = "repository-prepared-no-prediction"
            absence_code = "missing-candidate-output"
        elif len(candidates) > 1:
            disposition = "repository-prepared-no-prediction"
            absence_code = "duplicate-candidate-output"
            candidate_failure_code = "duplicate-candidate-output"
        else:
            candidate = candidates[0]
            if candidate.status == "recorded":
                disposition = "prediction-recorded"
                absence_code = None
                patch = candidate.patch
                patch_bytes = candidate.patch_bytes
                assert patch is not None and patch_bytes is not None
                capture_document = _capture_document(candidate.capture)  # type: ignore[arg-type]
                candidate_capture_ordinal = candidate.ordinal
                prediction_count += 1
            else:
                disposition = "repository-prepared-no-prediction"
                absence_code = (
                    "oversized-candidate-output"
                    if candidate.status == "oversized"
                    else "invalid-candidate-output"
                )
                candidate_failure_code = candidate.failure_code
                capture_document = (
                    _capture_document(candidate.capture)
                    if candidate.capture is not None
                    else None
                )
                if capture_document is not None:
                    candidate_capture_ordinal = candidate.ordinal

        if candidates and repository["status"] != "prepared":
            for candidate in candidates:
                violations.append(
                    _protocol_violation(
                        candidate,
                        code="candidate-output-for-unprepared-task",
                    )
                )
        elif len(candidates) > 1:
            for candidate in candidates:
                violations.append(
                    _protocol_violation(
                        candidate,
                        code="duplicate-candidate-output",
                    )
                )
        elif candidates and candidates[0].status != "recorded":
            violations.append(
                _protocol_violation(
                    candidates[0],
                    code=(
                        "oversized-candidate-output"
                        if candidates[0].status == "oversized"
                        else "invalid-candidate-output"
                    ),
                )
            )
        _ensure_violation_limit(violations, selected_limits)

        line = _jsonl_line(
            str(record["instance_id"]),
            selected_system.model_name_or_path,
            patch,
        )
        jsonl_lines.append(line)
        jsonl_byte_count += len(line)
        if jsonl_byte_count > selected_limits.max_jsonl_bytes:
            raise PredictionLedgerError("prediction JSONL exceeds its byte limit")
        disposition_counts[disposition] += 1
        task_documents.append(
            {
                "ordinal": ordinal,
                "instance_id": str(record["instance_id"]),
                "opaque_task_id": opaque_id,
                "source_record_sha256": _source_canonical_sha256(record),
                "candidate_input_sha256": _source_canonical_sha256(
                    {"problem_statement": record["problem_statement"]}
                ),
                "repository": repository,
                "disposition": disposition,
                "prediction": {
                    "status": "recorded" if patch is not None else "not-recorded",
                    "absence_code": absence_code,
                    "candidate_failure_code": candidate_failure_code,
                    "candidate_capture": capture_document,
                    "candidate_capture_ordinal": candidate_capture_ordinal,
                    "patch": (
                        {
                            "byte_count": len(patch_bytes),
                            "sha256": hashlib.sha256(patch_bytes).hexdigest(),
                        }
                        if patch_bytes is not None
                        else None
                    ),
                    "jsonl_line": {
                        "byte_count": len(line),
                        "sha256": hashlib.sha256(line).hexdigest(),
                    },
                },
            }
        )

    encoded_jsonl = b"".join(jsonl_lines)
    unsigned: dict[str, Any] = {
        "schema": PREDICTION_LEDGER_SCHEMA,
        "suite": {
            "suite_id": suite.suite_id,
            "suite_sha256": suite.suite_sha256,
            "source_snapshot_sha256": source.file_sha256,
            "selected_count": len(records),
            "physical_source_order": True,
        },
        "task_input": {
            "task_input_sha256": verified_task_input["task_input_sha256"],
            "opaque_key_fingerprint": verified_task_input["opaque_key_fingerprint"],
            "source_record_sha256s_sha256": verified_task_input[
                "source_record_sha256s_sha256"
            ],
            "candidate_input_sha256s_sha256": verified_task_input[
                "candidate_input_sha256s_sha256"
            ],
            "task_count": verified_task_input["task_count"],
        },
        "system": selected_system.to_dict(),
        "limits": _limits_document(selected_limits),
        "cohort": {
            "selected_count": len(records),
            "prediction_count": prediction_count,
            "nonprediction_count": len(records) - prediction_count,
            "preparation_counts": {
                status: preparation_counts[status]
                for status in sorted(_PREPARATION_STATUSES)
            },
            "disposition_counts": {
                disposition: disposition_counts[disposition]
                for disposition in _DISPOSITIONS
            },
            "protocol_violation_count": len(violations),
        },
        "tasks": task_documents,
        "protocol_violations": violations,
        "official_jsonl": {
            "format": "swebench-predictions-jsonl",
            "fields": list(OFFICIAL_JSONL_FIELDS),
            "encoding": "utf-8",
            "line_ending": "lf",
            "line_count": len(jsonl_lines),
            "byte_count": len(encoded_jsonl),
            "sha256": hashlib.sha256(encoded_jsonl).hexdigest(),
        },
        "evidence_state": {
            "full_selected_cohort_reconciled": True,
            "source_and_task_input_reverified": True,
            "repository_outcomes_reconciled": True,
            "successful_preparations_reverified": True,
            "expected_system_reconciled": True,
            "candidate_mount_created": False,
            "candidate_execution_performed": False,
            "hidden_tests_applied": False,
            "grading_performed": False,
            "external_score_computed": False,
            "usefulness_measured": False,
            "claim_ready": False,
            "self_hash_authenticates_producer": False,
            "system_identity_authenticated": False,
            "candidate_capture_origin_authenticated": False,
            "protocol_violations_independently_replayable": False,
        },
    }
    unsigned["prediction_ledger_sha256"] = _canonical_sha256(unsigned)
    if len(_canonical_json_bytes(unsigned)) > selected_limits.max_ledger_bytes:
        raise PredictionLedgerError("prediction ledger exceeds its byte limit")
    try:
        rendered_jsonl = encoded_jsonl.decode("utf-8", errors="strict")
        output_identity = atomic_write_text(
            output_path,
            rendered_jsonl,
            overwrite=False,
        )
    except FileExistsError as exc:
        raise PredictionLedgerError("prediction JSONL output already exists") from exc
    except (OSError, UnicodeError, ValueError) as exc:
        raise PredictionLedgerError("prediction JSONL could not be written safely") from exc
    try:
        if _single_link_regular(
            output_path,
            label="prediction JSONL",
        ) != output_identity:
            raise PredictionLedgerError("written prediction JSONL identity changed")
        try:
            retained_jsonl = read_bounded_regular_file(
                output_path,
                max_bytes=selected_limits.max_jsonl_bytes,
                label="prediction JSONL",
                expected_identity=output_identity,
            )
        except StrictJsonError as exc:
            raise PredictionLedgerError(str(exc)) from exc
        if _single_link_regular(
            output_path,
            label="prediction JSONL",
        ) != output_identity:
            raise PredictionLedgerError("written prediction JSONL identity changed")
        if retained_jsonl.value != encoded_jsonl:
            raise PredictionLedgerError("written prediction JSONL bytes changed")
        ledger = VerifiedPredictionLedger(
            jsonl_path=output_path,
            preparations=retained_preparations,
            limits=selected_limits,
            document=_freeze_json(unsigned),
        )
        return verify_prediction_ledger(
            ledger,
            source,
            verified_task_input,
            opaque_key=opaque_key,
            system=selected_system,
        )
    except BaseException as exc:
        try:
            _cleanup_owned_file(
                output_path,
                output_identity,
                label="prediction JSONL",
            )
        except PredictionLedgerError as cleanup_exc:
            raise PredictionLedgerError(
                "prediction JSONL verification failed and cleanup was incomplete: "
                f"{cleanup_exc}"
            ) from exc
        raise


def _decode_capture_document(value: Any, *, label: str) -> dict[str, Any]:
    payload = _object(
        value,
        fields={"observed_bytes", "captured_bytes", "captured_sha256", "complete"},
        label=label,
    )
    try:
        return RetainedCaptureEvidence(**payload).to_dict()
    except (TypeError, PredictionLedgerError) as exc:
        raise PredictionLedgerError(f"{label} is invalid") from exc


def _decode_file_evidence(
    value: Any,
    *,
    label: str,
    maximum: int,
    allow_empty: bool,
) -> dict[str, Any]:
    payload = _object(value, fields={"byte_count", "sha256"}, label=label)
    _bounded_integer(
        payload["byte_count"],
        label=f"{label}.byte_count",
        minimum=0 if allow_empty else 1,
        maximum=maximum,
    )
    _sha256(payload["sha256"], label=f"{label}.sha256")
    return payload


def _decode_system_document(value: Any) -> dict[str, Any]:
    payload = _object(
        value,
        fields={
            "code_revision",
            "repository_clean",
            "model_name_or_path",
            "model_sha256",
            "agent_sha256",
            "prompt_sha256",
            "tool_sha256",
            "controller_sha256",
        },
        label="prediction system",
    )
    try:
        return SystemIdentity(**payload).to_dict()
    except (TypeError, PredictionLedgerError) as exc:
        raise PredictionLedgerError("prediction system is invalid") from exc


def _decode_limits_document(value: Any) -> PredictionLedgerLimits:
    payload = _object(value, fields=set(_LIMIT_FIELDS), label="prediction limits")
    try:
        return PredictionLedgerLimits(**payload)
    except (TypeError, PredictionLedgerError) as exc:
        raise PredictionLedgerError("prediction limits are invalid") from exc


def decode_prediction_ledger(value: Any) -> dict[str, Any]:
    """Strictly decode one self-hashed, pathless prediction ledger."""

    payload = _object(
        value,
        fields={
            "schema",
            "suite",
            "task_input",
            "system",
            "limits",
            "cohort",
            "tasks",
            "protocol_violations",
            "official_jsonl",
            "evidence_state",
            "prediction_ledger_sha256",
        },
        label="prediction ledger",
    )
    if (
        type(payload["schema"]) is not str
        or payload["schema"] != PREDICTION_LEDGER_SCHEMA
    ):
        raise PredictionLedgerError(
            f"prediction ledger.schema must equal {PREDICTION_LEDGER_SCHEMA!r}"
        )
    suite = _object(
        payload["suite"],
        fields={
            "suite_id",
            "suite_sha256",
            "source_snapshot_sha256",
            "selected_count",
            "physical_source_order",
        },
        label="prediction suite",
    )
    if (
        type(suite["suite_id"]) is not str
        or _CODE_RE.fullmatch(suite["suite_id"]) is None
    ):
        raise PredictionLedgerError("prediction suite id is invalid")
    _sha256(suite["suite_sha256"], label="prediction suite SHA-256")
    _sha256(
        suite["source_snapshot_sha256"],
        label="prediction source snapshot SHA-256",
    )
    selected_count = _bounded_integer(
        suite["selected_count"],
        label="prediction selected_count",
        minimum=1,
        maximum=_MAX_SELECTED_TASKS,
    )
    _boolean(
        suite["physical_source_order"],
        expected=True,
        label="prediction suite physical_source_order",
    )

    task_input = _object(
        payload["task_input"],
        fields={
            "task_input_sha256",
            "opaque_key_fingerprint",
            "source_record_sha256s_sha256",
            "candidate_input_sha256s_sha256",
            "task_count",
        },
        label="prediction task input",
    )
    for name in (
        "task_input_sha256",
        "opaque_key_fingerprint",
        "source_record_sha256s_sha256",
        "candidate_input_sha256s_sha256",
    ):
        _sha256(task_input[name], label=f"prediction task input {name}")
    if _bounded_integer(
        task_input["task_count"],
        label="prediction task count",
        minimum=1,
        maximum=_MAX_SELECTED_TASKS,
    ) != selected_count:
        raise PredictionLedgerError("prediction task count mismatch")

    _decode_system_document(payload["system"])
    limits = _decode_limits_document(payload["limits"])
    cohort = _object(
        payload["cohort"],
        fields={
            "selected_count",
            "prediction_count",
            "nonprediction_count",
            "preparation_counts",
            "disposition_counts",
            "protocol_violation_count",
        },
        label="prediction cohort",
    )
    if _bounded_integer(
        cohort["selected_count"],
        label="cohort.selected_count",
        minimum=1,
        maximum=_MAX_SELECTED_TASKS,
    ) != selected_count:
        raise PredictionLedgerError("cohort selected count mismatch")
    prediction_count = _bounded_integer(
        cohort["prediction_count"],
        label="cohort.prediction_count",
        minimum=0,
        maximum=selected_count,
    )
    nonprediction_count = _bounded_integer(
        cohort["nonprediction_count"],
        label="cohort.nonprediction_count",
        minimum=0,
        maximum=selected_count,
    )
    if prediction_count + nonprediction_count != selected_count:
        raise PredictionLedgerError("prediction/nonprediction counts are not exhaustive")
    preparation_counts = _object(
        cohort["preparation_counts"],
        fields=set(_PREPARATION_STATUSES),
        label="cohort preparation counts",
    )
    decoded_preparation_counts = {
        status: _bounded_integer(
            preparation_counts[status],
            label=f"cohort preparation count {status}",
            minimum=0,
            maximum=selected_count,
        )
        for status in _PREPARATION_STATUSES
    }
    if sum(decoded_preparation_counts.values()) != selected_count:
        raise PredictionLedgerError("preparation counts are not exhaustive")
    disposition_counts = _object(
        cohort["disposition_counts"],
        fields=set(_DISPOSITIONS),
        label="cohort disposition counts",
    )
    decoded_disposition_counts = {
        disposition: _bounded_integer(
            disposition_counts[disposition],
            label=f"cohort disposition count {disposition}",
            minimum=0,
            maximum=selected_count,
        )
        for disposition in _DISPOSITIONS
    }
    if sum(decoded_disposition_counts.values()) != selected_count:
        raise PredictionLedgerError("disposition counts are not exhaustive")
    protocol_violation_count = _bounded_integer(
        cohort["protocol_violation_count"],
        label="cohort protocol violation count",
        minimum=0,
        maximum=limits.max_protocol_violations,
    )

    tasks = payload["tasks"]
    if type(tasks) is not list or len(tasks) != selected_count:
        raise PredictionLedgerError("prediction tasks must cover selected_count")
    observed_instances: set[str] = set()
    observed_opaque_ids: set[str] = set()
    observed_preparations: Counter[str] = Counter()
    observed_dispositions: Counter[str] = Counter()
    observed_predictions = 0
    line_byte_total = 0
    retained_capture_bytes = 0
    capture_evidence_by_ordinal: dict[int, dict[str, object]] = {}
    task_capture_ordinals: set[int] = set()

    def retain_capture_evidence(
        capture_ordinal: int,
        capture: dict[str, object],
    ) -> None:
        nonlocal retained_capture_bytes
        previous = capture_evidence_by_ordinal.get(capture_ordinal)
        if previous is not None:
            if previous != capture:
                raise PredictionLedgerError(
                    "one candidate capture has inconsistent retained evidence"
                )
            return
        capture_evidence_by_ordinal[capture_ordinal] = capture
        retained_capture_bytes += int(capture["captured_bytes"])
        if retained_capture_bytes > limits.max_total_capture_bytes:
            raise PredictionLedgerError(
                "candidate captures exceed the aggregate byte limit"
            )

    for ordinal, raw in enumerate(tasks):
        task = _object(
            raw,
            fields={
                "ordinal",
                "instance_id",
                "opaque_task_id",
                "source_record_sha256",
                "candidate_input_sha256",
                "repository",
                "disposition",
                "prediction",
            },
            label=f"prediction tasks[{ordinal}]",
        )
        if task["ordinal"] != ordinal:
            raise PredictionLedgerError(
                f"prediction tasks[{ordinal}] ordinal mismatch"
            )
        instance_id = task["instance_id"]
        opaque_id = task["opaque_task_id"]
        if type(instance_id) is not str or _INSTANCE_RE.fullmatch(instance_id) is None:
            raise PredictionLedgerError(
                f"prediction tasks[{ordinal}] instance id is invalid"
            )
        if type(opaque_id) is not str or _OPAQUE_ID_RE.fullmatch(opaque_id) is None:
            raise PredictionLedgerError(
                f"prediction tasks[{ordinal}] opaque id is invalid"
            )
        if instance_id in observed_instances or opaque_id in observed_opaque_ids:
            raise PredictionLedgerError("prediction task identities are not unique")
        observed_instances.add(instance_id)
        observed_opaque_ids.add(opaque_id)
        _sha256(
            task["source_record_sha256"],
            label=f"prediction tasks[{ordinal}] source record SHA-256",
        )
        _sha256(
            task["candidate_input_sha256"],
            label=f"prediction tasks[{ordinal}] candidate input SHA-256",
        )
        repository = _object(
            task["repository"],
            fields={"status", "preparation_sha256", "failure_code"},
            label=f"prediction tasks[{ordinal}] repository",
        )
        status = repository["status"]
        if type(status) is not str or status not in _PREPARATION_STATUSES:
            raise PredictionLedgerError(
                f"prediction tasks[{ordinal}] repository status is invalid"
            )
        observed_preparations[status] += 1
        if status == "prepared":
            _sha256(
                repository["preparation_sha256"],
                label=f"prediction tasks[{ordinal}] preparation SHA-256",
            )
            if repository["failure_code"] is not None:
                raise PredictionLedgerError("prepared task has a failure code")
        elif repository["preparation_sha256"] is not None:
            raise PredictionLedgerError("unprepared task has a preparation hash")
        elif status == "refused":
            _code(
                repository["failure_code"],
                label=f"prediction tasks[{ordinal}] preparation failure code",
            )
        elif repository["failure_code"] is not None:
            raise PredictionLedgerError("not-attempted task has a failure code")

        disposition = task["disposition"]
        if type(disposition) is not str or disposition not in _DISPOSITIONS:
            raise PredictionLedgerError(
                f"prediction tasks[{ordinal}] disposition is invalid"
            )
        observed_dispositions[disposition] += 1
        prediction = _object(
            task["prediction"],
            fields={
                "status",
                "absence_code",
                "candidate_failure_code",
                "candidate_capture",
                "candidate_capture_ordinal",
                "patch",
                "jsonl_line",
            },
            label=f"prediction tasks[{ordinal}] prediction",
        )
        line_evidence = _decode_file_evidence(
            prediction["jsonl_line"],
            label=f"prediction tasks[{ordinal}] JSONL line",
            maximum=limits.max_patch_bytes + 4096,
            allow_empty=False,
        )
        line_byte_total += int(line_evidence["byte_count"])
        capture = (
            _decode_capture_document(
                prediction["candidate_capture"],
                label=f"prediction tasks[{ordinal}] candidate capture",
            )
            if prediction["candidate_capture"] is not None
            else None
        )
        raw_capture_ordinal = prediction["candidate_capture_ordinal"]
        if capture is None:
            if raw_capture_ordinal is not None:
                raise PredictionLedgerError(
                    "candidate capture ordinal lacks retained evidence"
                )
        else:
            capture_ordinal = _bounded_integer(
                raw_capture_ordinal,
                label=(
                    f"prediction tasks[{ordinal}] candidate capture ordinal"
                ),
                minimum=0,
                maximum=limits.max_candidate_captures - 1,
            )
            if capture_ordinal in task_capture_ordinals:
                raise PredictionLedgerError(
                    "one candidate capture is assigned to multiple tasks"
                )
            task_capture_ordinals.add(capture_ordinal)
            retain_capture_evidence(capture_ordinal, capture)
        if capture is not None and capture["captured_bytes"] > limits.max_patch_bytes + 1:
            raise PredictionLedgerError("candidate capture exceeds the patch cap witness")
        prediction_status = prediction["status"]
        if type(prediction_status) is not str:
            raise PredictionLedgerError(
                f"prediction tasks[{ordinal}] prediction status is invalid"
            )
        if prediction_status == "recorded":
            observed_predictions += 1
            if (
                disposition != "prediction-recorded"
                or status != "prepared"
                or prediction["absence_code"] is not None
                or prediction["candidate_failure_code"] is not None
                or capture is None
                or prediction["patch"] is None
            ):
                raise PredictionLedgerError("recorded prediction state is inconsistent")
            patch = _decode_file_evidence(
                prediction["patch"],
                label=f"prediction tasks[{ordinal}] patch",
                maximum=limits.max_patch_bytes,
                allow_empty=True,
            )
            if (
                capture["complete"] is not True
                or capture["observed_bytes"] != patch["byte_count"]
                or capture["captured_bytes"] != patch["byte_count"]
                or capture["captured_sha256"] != patch["sha256"]
            ):
                raise PredictionLedgerError("recorded capture/patch evidence mismatch")
        elif prediction_status == "not-recorded":
            if disposition == "prediction-recorded" or prediction["patch"] is not None:
                raise PredictionLedgerError("nonprediction retains recorded patch state")
            absence_code = _code(
                prediction["absence_code"],
                label=f"prediction tasks[{ordinal}] absence code",
            )
            failure_code = prediction["candidate_failure_code"]
            if failure_code is not None:
                _code(
                    failure_code,
                    label=f"prediction tasks[{ordinal}] candidate failure code",
                )
            expected_disposition = {
                "not-attempted": "repository-not-attempted",
                "refused": "repository-preparation-refused",
                "prepared": "repository-prepared-no-prediction",
            }[status]
            if disposition != expected_disposition:
                raise PredictionLedgerError("nonprediction disposition is inconsistent")
            if status == "not-attempted" and absence_code != "repository-not-attempted":
                raise PredictionLedgerError("not-attempted absence code mismatch")
            if status == "refused" and absence_code != "repository-preparation-refused":
                raise PredictionLedgerError("refused absence code mismatch")
            if status != "prepared" and (
                capture is not None or failure_code is not None
            ):
                raise PredictionLedgerError(
                    "unprepared task row retains candidate evidence"
                )
            if status == "prepared" and absence_code not in {
                "missing-candidate-output",
                "duplicate-candidate-output",
                "invalid-candidate-output",
                "oversized-candidate-output",
            }:
                raise PredictionLedgerError("prepared nonprediction absence code is invalid")
            if absence_code == "missing-candidate-output" and (
                capture is not None or failure_code is not None
            ):
                raise PredictionLedgerError("missing output retains candidate evidence")
            if absence_code == "duplicate-candidate-output" and (
                capture is not None or failure_code != "duplicate-candidate-output"
            ):
                raise PredictionLedgerError("duplicate output evidence is inconsistent")
            if absence_code == "invalid-candidate-output" and failure_code is None:
                raise PredictionLedgerError("invalid output lacks a failure code")
            if absence_code == "oversized-candidate-output" and (
                capture is None or failure_code is None
            ):
                raise PredictionLedgerError("oversized output evidence is incomplete")
        else:
            raise PredictionLedgerError(
                f"prediction tasks[{ordinal}] prediction status is invalid"
            )

    if observed_predictions != prediction_count:
        raise PredictionLedgerError("prediction count does not match task rows")
    if observed_predictions + nonprediction_count != selected_count:
        raise PredictionLedgerError("nonprediction count does not match task rows")
    if any(
        observed_preparations[key] != decoded_preparation_counts[key]
        for key in _PREPARATION_STATUSES
    ):
        raise PredictionLedgerError("preparation counts do not match task rows")
    if any(
        observed_dispositions[key] != decoded_disposition_counts[key]
        for key in _DISPOSITIONS
    ):
        raise PredictionLedgerError("disposition counts do not match task rows")

    violations = payload["protocol_violations"]
    if type(violations) is not list or len(violations) != protocol_violation_count:
        raise PredictionLedgerError("protocol violation count mismatch")
    violation_ordinals: set[int] = set()
    for index, raw in enumerate(violations):
        violation = _object(
            raw,
            fields={"capture_ordinal", "code", "opaque_task_id_sha256", "capture"},
            label=f"protocol violations[{index}]",
        )
        capture_ordinal = _bounded_integer(
            violation["capture_ordinal"],
            label=f"protocol violations[{index}] capture ordinal",
            minimum=0,
            maximum=limits.max_candidate_captures - 1,
        )
        if capture_ordinal in violation_ordinals:
            raise PredictionLedgerError("one candidate capture has duplicate violations")
        violation_ordinals.add(capture_ordinal)
        _code(violation["code"], label=f"protocol violations[{index}] code")
        _sha256(
            violation["opaque_task_id_sha256"],
            label=f"protocol violations[{index}] opaque id digest",
        )
        if violation["capture"] is not None:
            capture = _decode_capture_document(
                violation["capture"],
                label=f"protocol violations[{index}] capture",
            )
            if capture["captured_bytes"] > limits.max_patch_bytes + 1:
                raise PredictionLedgerError("violation capture exceeds the patch cap witness")
            retain_capture_evidence(capture_ordinal, capture)
        elif capture_ordinal in capture_evidence_by_ordinal:
            raise PredictionLedgerError(
                "protocol violation dropped retained candidate evidence"
            )

    observed_capture_ordinals = task_capture_ordinals | violation_ordinals
    if observed_capture_ordinals and sorted(observed_capture_ordinals) != list(
        range(max(observed_capture_ordinals) + 1)
    ):
        raise PredictionLedgerError(
            "candidate capture ordinals are not a contiguous zero-based sequence"
        )

    official = _object(
        payload["official_jsonl"],
        fields={
            "format",
            "fields",
            "encoding",
            "line_ending",
            "line_count",
            "byte_count",
            "sha256",
        },
        label="official prediction JSONL",
    )
    if (
        type(official["format"]) is not str
        or official["format"] != "swebench-predictions-jsonl"
    ):
        raise PredictionLedgerError("official JSONL format is invalid")
    if (
        type(official["fields"]) is not list
        or any(type(field) is not str for field in official["fields"])
        or official["fields"] != list(OFFICIAL_JSONL_FIELDS)
    ):
        raise PredictionLedgerError("official JSONL fields are invalid")
    if (
        type(official["encoding"]) is not str
        or type(official["line_ending"]) is not str
        or official["encoding"] != "utf-8"
        or official["line_ending"] != "lf"
    ):
        raise PredictionLedgerError("official JSONL encoding/framing is invalid")
    if _bounded_integer(
        official["line_count"],
        label="official JSONL line count",
        minimum=1,
        maximum=_MAX_SELECTED_TASKS,
    ) != selected_count:
        raise PredictionLedgerError("official JSONL line count mismatch")
    official_bytes = _bounded_integer(
        official["byte_count"],
        label="official JSONL byte count",
        minimum=1,
        maximum=limits.max_jsonl_bytes,
    )
    if line_byte_total != official_bytes:
        raise PredictionLedgerError("JSONL line bytes do not match artifact bytes")
    _sha256(official["sha256"], label="official JSONL SHA-256")

    evidence = _object(
        payload["evidence_state"],
        fields={
            "full_selected_cohort_reconciled",
            "source_and_task_input_reverified",
            "repository_outcomes_reconciled",
            "successful_preparations_reverified",
            "expected_system_reconciled",
            "candidate_mount_created",
            "candidate_execution_performed",
            "hidden_tests_applied",
            "grading_performed",
            "external_score_computed",
            "usefulness_measured",
            "claim_ready",
            "self_hash_authenticates_producer",
            "system_identity_authenticated",
            "candidate_capture_origin_authenticated",
            "protocol_violations_independently_replayable",
        },
        label="prediction evidence state",
    )
    for name in (
        "full_selected_cohort_reconciled",
        "source_and_task_input_reverified",
        "repository_outcomes_reconciled",
        "successful_preparations_reverified",
        "expected_system_reconciled",
    ):
        _boolean(evidence[name], expected=True, label=f"prediction evidence {name}")
    for name in (
        "candidate_mount_created",
        "candidate_execution_performed",
        "hidden_tests_applied",
        "grading_performed",
        "external_score_computed",
        "usefulness_measured",
        "claim_ready",
        "self_hash_authenticates_producer",
        "system_identity_authenticated",
        "candidate_capture_origin_authenticated",
        "protocol_violations_independently_replayable",
    ):
        _boolean(evidence[name], expected=False, label=f"prediction evidence {name}")

    claimed = _sha256(
        payload["prediction_ledger_sha256"],
        label="prediction ledger SHA-256",
    )
    unsigned = dict(payload)
    unsigned.pop("prediction_ledger_sha256")
    if _canonical_sha256(unsigned) != claimed:
        raise PredictionLedgerError("prediction ledger SHA-256 mismatch")
    if len(_canonical_json_bytes(payload)) > limits.max_ledger_bytes:
        raise PredictionLedgerError("prediction ledger exceeds its byte limit")
    return payload


def _strict_jsonl_object(encoded: bytes, *, ordinal: int) -> dict[str, Any]:
    if not encoded.endswith(b"\n") or encoded == b"\n":
        raise PredictionLedgerError(
            f"prediction JSONL line {ordinal} has invalid framing"
        )
    raw = encoded[:-1]
    if raw.startswith(b"\xef\xbb\xbf"):
        raise PredictionLedgerError("prediction JSONL must not contain a BOM")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise PredictionLedgerError(
            f"prediction JSONL line {ordinal} is not UTF-8"
        ) from exc

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, nested in pairs:
            if key in value:
                raise PredictionLedgerError(
                    f"prediction JSONL line {ordinal} has a duplicate key"
                )
            value[key] = nested
        return value

    def reject_constant(value: str) -> None:
        raise PredictionLedgerError(
            f"prediction JSONL line {ordinal} has non-standard constant {value}"
        )

    try:
        decoded = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except PredictionLedgerError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise PredictionLedgerError(
            f"prediction JSONL line {ordinal} is not strict JSON"
        ) from exc
    payload = _object(
        decoded,
        fields=set(OFFICIAL_JSONL_FIELDS),
        label=f"prediction JSONL line {ordinal}",
    )
    if _canonical_json_bytes(payload) + b"\n" != encoded:
        raise PredictionLedgerError(
            f"prediction JSONL line {ordinal} is not canonical"
        )
    return payload


def _read_live_jsonl(
    path: Path,
    *,
    limits: PredictionLedgerLimits,
) -> bytes:
    initial_identity = _single_link_regular(path, label="prediction JSONL")
    try:
        document = read_bounded_regular_file(
            path,
            max_bytes=limits.max_jsonl_bytes,
            label="prediction JSONL",
            expected_identity=initial_identity,
        )
    except StrictJsonError as exc:
        raise PredictionLedgerError(str(exc)) from exc
    final_identity = _single_link_regular(path, label="prediction JSONL")
    if final_identity != initial_identity:
        raise PredictionLedgerError(
            "prediction JSONL path identity changed while it was read"
        )
    if document.value.startswith(b"\xef\xbb\xbf"):
        raise PredictionLedgerError("prediction JSONL must not contain a BOM")
    return document.value


def verify_prediction_ledger(
    ledger: VerifiedPredictionLedger,
    source: VerifiedSweBenchSource,
    task_input: Any,
    *,
    opaque_key: bytes,
    system: SystemIdentity,
    preparations: Sequence[RepositoryPreparationOutcome] | None = None,
) -> VerifiedPredictionLedger:
    """Replay source, preparation, ledger, and exact live JSONL bindings."""

    if type(ledger) is not VerifiedPredictionLedger:
        raise PredictionLedgerError("ledger must be VerifiedPredictionLedger")
    runtime_limits = _revalidate_limits(ledger.limits)
    expected_system = _system_identity(system)
    try:
        decoded = decode_prediction_ledger(ledger.to_dict())
    except (AttributeError, TypeError, PredictionLedgerError) as exc:
        if isinstance(exc, PredictionLedgerError):
            raise
        raise PredictionLedgerError("prediction ledger runtime value is invalid") from exc
    decoded_limits = _decode_limits_document(decoded["limits"])
    if decoded_limits != runtime_limits:
        raise PredictionLedgerError("runtime prediction limits do not match the ledger")
    suite, records, verified_task_input = _source_and_tasks(
        source,
        task_input,
        opaque_key,
    )
    expected_suite = {
        "suite_id": suite.suite_id,
        "suite_sha256": suite.suite_sha256,
        "source_snapshot_sha256": source.file_sha256,
        "selected_count": len(records),
        "physical_source_order": True,
    }
    if decoded["suite"] != expected_suite:
        raise PredictionLedgerError("prediction ledger suite binding mismatch")
    expected_task_input = {
        "task_input_sha256": verified_task_input["task_input_sha256"],
        "opaque_key_fingerprint": verified_task_input["opaque_key_fingerprint"],
        "source_record_sha256s_sha256": verified_task_input[
            "source_record_sha256s_sha256"
        ],
        "candidate_input_sha256s_sha256": verified_task_input[
            "candidate_input_sha256s_sha256"
        ],
        "task_count": verified_task_input["task_count"],
    }
    if decoded["task_input"] != expected_task_input:
        raise PredictionLedgerError("prediction ledger task-input binding mismatch")
    if decoded["system"] != expected_system.to_dict():
        raise PredictionLedgerError("prediction ledger system identity mismatch")

    runtime_preparations = (
        ledger.preparations if preparations is None else preparations
    )
    retained_preparations, preparation_documents = _revalidate_preparations(
        runtime_preparations,
        source=source,
        task_input=verified_task_input,
    )
    if [task["repository"] for task in decoded["tasks"]] != preparation_documents:
        raise PredictionLedgerError("runtime preparations do not match the ledger")

    try:
        runtime_jsonl_path = _output_path(
            ledger.jsonl_path,
            label="prediction JSONL path",
        )
    except (TypeError, PredictionLedgerError) as exc:
        raise PredictionLedgerError("runtime prediction JSONL path is invalid") from exc
    _preflight_jsonl_destination(runtime_jsonl_path, retained_preparations)
    encoded_jsonl = _read_live_jsonl(runtime_jsonl_path, limits=runtime_limits)
    official = decoded["official_jsonl"]
    if (
        len(encoded_jsonl) != official["byte_count"]
        or hashlib.sha256(encoded_jsonl).hexdigest() != official["sha256"]
    ):
        raise PredictionLedgerError("live prediction JSONL artifact mismatch")
    if not encoded_jsonl.endswith(b"\n"):
        raise PredictionLedgerError("prediction JSONL must end with one LF-framed line")
    raw_lines = encoded_jsonl.split(b"\n")
    if raw_lines[-1] != b"":
        raise PredictionLedgerError("prediction JSONL final framing is invalid")
    lines = [raw + b"\n" for raw in raw_lines[:-1]]
    if len(lines) != len(records):
        raise PredictionLedgerError("prediction JSONL does not cover the cohort")

    model_name = decoded["system"]["model_name_or_path"]
    for ordinal, (record, projected, task, encoded_line) in enumerate(
        zip(
            records,
            verified_task_input["tasks"],
            decoded["tasks"],
            lines,
            strict=True,
        )
    ):
        expected_task_binding = {
            "ordinal": ordinal,
            "instance_id": record["instance_id"],
            "opaque_task_id": projected["opaque_task_id"],
            "source_record_sha256": _source_canonical_sha256(record),
            "candidate_input_sha256": _source_canonical_sha256(
                {"problem_statement": record["problem_statement"]}
            ),
        }
        if any(task[name] != expected for name, expected in expected_task_binding.items()):
            raise PredictionLedgerError(
                f"prediction task {ordinal} source binding mismatch"
            )
        line_evidence = task["prediction"]["jsonl_line"]
        if (
            len(encoded_line) != line_evidence["byte_count"]
            or hashlib.sha256(encoded_line).hexdigest() != line_evidence["sha256"]
        ):
            raise PredictionLedgerError(
                f"prediction task {ordinal} JSONL-line evidence mismatch"
            )
        line = _strict_jsonl_object(encoded_line, ordinal=ordinal)
        if line["instance_id"] != record["instance_id"]:
            raise PredictionLedgerError(
                f"prediction JSONL line {ordinal} instance mismatch"
            )
        if line["model_name_or_path"] != model_name:
            raise PredictionLedgerError(
                f"prediction JSONL line {ordinal} model mismatch"
            )
        patch = line["model_patch"]
        prediction = task["prediction"]
        if prediction["status"] == "recorded":
            if type(patch) is not str:
                raise PredictionLedgerError(
                    f"prediction JSONL line {ordinal} lost its patch"
                )
            encoded_patch = _patch_bytes(
                patch,
                max_bytes=runtime_limits.max_patch_bytes,
            )
            patch_evidence = prediction["patch"]
            if (
                len(encoded_patch) != patch_evidence["byte_count"]
                or hashlib.sha256(encoded_patch).hexdigest()
                != patch_evidence["sha256"]
            ):
                raise PredictionLedgerError(
                    f"prediction JSONL line {ordinal} patch evidence mismatch"
                )
        elif patch is not None:
            raise PredictionLedgerError(
                f"prediction JSONL line {ordinal} gives a nonprediction a patch"
            )
    return VerifiedPredictionLedger(
        jsonl_path=runtime_jsonl_path,
        preparations=retained_preparations,
        limits=runtime_limits,
        document=_freeze_json(decoded),
    )


def load_prediction_ledger(
    ledger_path: str | Path,
    jsonl_path: str | Path,
    source: VerifiedSweBenchSource,
    task_input: Any,
    *,
    opaque_key: bytes,
    preparations: Sequence[RepositoryPreparationOutcome],
    system: SystemIdentity,
) -> VerifiedPredictionLedger:
    """Load bounded ledger JSON and replay it against live coordinator state."""

    if type(preparations) not in (list, tuple):
        raise PredictionLedgerError("preparations must be a materialized sequence")
    input_path = _output_path(ledger_path, label="prediction ledger input")
    live_jsonl_path = _output_path(jsonl_path, label="prediction JSONL input")
    initial_identity = _single_link_regular(
        input_path,
        label="prediction ledger",
    )
    try:
        loaded = load_strict_json_file(
            input_path,
            limits=StrictJsonLimits(
                max_bytes=_MAX_HARD_LEDGER_BYTES,
                max_line_chars=_MAX_HARD_LEDGER_BYTES,
                max_depth=32,
            ),
            label="prediction ledger",
            allow_bom=False,
            expected_identity=initial_identity,
        )
    except StrictJsonError as exc:
        raise PredictionLedgerError(str(exc)) from exc
    final_identity = _single_link_regular(
        input_path,
        label="prediction ledger",
    )
    if final_identity != initial_identity:
        raise PredictionLedgerError(
            "prediction ledger path identity changed while it was read"
        )
    decoded = decode_prediction_ledger(loaded.value)
    limits = _decode_limits_document(decoded["limits"])
    if loaded.byte_count > limits.max_ledger_bytes:
        raise PredictionLedgerError("serialized prediction ledger exceeds its limit")
    retained_runtime_preparations = tuple(
        preparations[: _MAX_SELECTED_TASKS + 1]
    )
    runtime = VerifiedPredictionLedger(
        jsonl_path=live_jsonl_path,
        preparations=retained_runtime_preparations,
        limits=limits,
        document=_freeze_json(decoded),
    )
    return verify_prediction_ledger(
        runtime,
        source,
        task_input,
        opaque_key=opaque_key,
        preparations=retained_runtime_preparations,
        system=system,
    )


def write_prediction_ledger(
    path: str | Path,
    ledger: VerifiedPredictionLedger,
    source: VerifiedSweBenchSource,
    task_input: Any,
    *,
    opaque_key: bytes,
    system: SystemIdentity,
    preparations: Sequence[RepositoryPreparationOutcome] | None = None,
) -> None:
    """Exclusively write a live-replayed ledger outside prepared trees."""

    verified = verify_prediction_ledger(
        ledger,
        source,
        task_input,
        opaque_key=opaque_key,
        preparations=preparations,
        system=system,
    )
    output = _output_path(path, label="prediction ledger output")
    try:
        if output.resolve(strict=False) == verified.jsonl_path.resolve(strict=True):
            raise PredictionLedgerError(
                "prediction ledger and JSONL outputs must be different files"
            )
    except (OSError, RuntimeError) as exc:
        raise PredictionLedgerError("prediction output paths could not be inspected") from exc
    _preflight_jsonl_destination(output, verified.preparations)
    rendered = json.dumps(
        verified.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"
    try:
        encoded = rendered.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise PredictionLedgerError("prediction ledger is not strict UTF-8") from exc
    if len(encoded) > verified.limits.max_ledger_bytes:
        raise PredictionLedgerError("serialized prediction ledger exceeds its limit")
    try:
        output_identity = atomic_write_text(output, rendered, overwrite=False)
    except FileExistsError as exc:
        raise PredictionLedgerError("prediction ledger output already exists") from exc
    except (OSError, ValueError) as exc:
        raise PredictionLedgerError("prediction ledger could not be written safely") from exc
    try:
        if _single_link_regular(
            output,
            label="prediction ledger",
        ) != output_identity:
            raise PredictionLedgerError("written prediction ledger identity changed")
        try:
            retained = read_bounded_regular_file(
                output,
                max_bytes=verified.limits.max_ledger_bytes,
                label="prediction ledger",
                expected_identity=output_identity,
            )
        except StrictJsonError as exc:
            raise PredictionLedgerError(str(exc)) from exc
        if retained.value != encoded:
            raise PredictionLedgerError("written prediction ledger bytes changed")
        if _single_link_regular(
            output,
            label="prediction ledger",
        ) != output_identity:
            raise PredictionLedgerError("written prediction ledger identity changed")
    except BaseException as exc:
        try:
            _cleanup_owned_file(
                output,
                output_identity,
                label="prediction ledger",
            )
        except PredictionLedgerError as cleanup_exc:
            raise PredictionLedgerError(
                "prediction ledger verification failed and cleanup was incomplete: "
                f"{cleanup_exc}"
            ) from exc
        raise
