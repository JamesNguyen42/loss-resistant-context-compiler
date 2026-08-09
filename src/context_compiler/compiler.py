"""Context compilation pipeline."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from . import models as _models_module
from .extractors import ExtractionResult, Extractor, RuleBasedExtractor
from .limits import (
    CompilationLimitError,
    CompilationLimits,
    SourceLimitError,
    SourceLimits,
    _CompilationWorkBudget,
    add_source_size,
    bounded_json_utf8_size,
    resolve_compilation_limits,
    resolve_source_limits,
    source_value_size,
)
from .models import (
    ADDITIVE_SAFETY_EXTRACTOR_FAILED_MESSAGE,
    CONTEXT_WINDOW_DEGRADATION_METADATA_KEY,
    CONTEXT_WINDOW_DEGRADATION_MODE,
    CONTEXT_WINDOW_DEGRADATION_RUNGS,
    LOSSLESS_COMPACT_MEMORY_RENDERING_PROFILE,
    MEMORY_RENDERING_PROFILE_METADATA_KEY,
    PRIMARY_EXTRACTOR_DEGRADED_MESSAGE,
    PRIMARY_EXTRACTOR_FAILED_MESSAGE,
    SCHEMA_VERSION,
    CompilationMetrics,
    CompilationPolicy,
    CompiledMemory,
    CompressionStats,
    IssueSeverity,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    ProvenanceSpan,
    SourceRecord,
    VerificationIssue,
    VerificationReport,
    memory_item_covers_candidate,
    render_lossless_compact_typed_memory,
    render_prompt_item,
    render_typed_memory,
    source_digest,
    utc_now,
)
from .resolver import resolve_temporal_state
from .verifier import verify_memory

TokenCounter = Callable[[str], int]


_BUDGET_OVERFLOW_CAPTURE: ContextVar[list[tuple[int, int]] | None] = ContextVar(
    "ctxc_budget_overflow_capture",
    default=None,
)


@contextmanager
def _capture_budget_overflow() -> Iterator[list[tuple[int, int]]]:
    """Capture exact overflow counts for one internal materialization call.

    Ordinary compiler and connector callers still receive the same plain
    ``ValueError``.  A context-local capture avoids changing a public method
    signature and prevents concurrent materializations from sharing details.
    """

    capture: list[tuple[int, int]] = []
    token = _BUDGET_OVERFLOW_CAPTURE.set(capture)
    try:
        yield capture
    finally:
        _BUDGET_OVERFLOW_CAPTURE.reset(token)


def _budget_overflow_error(*, required_tokens: int, token_budget: int) -> ValueError:
    """Return the unchanged public error while optionally recording counts."""

    if type(required_tokens) is not int or required_tokens < 0:
        raise TypeError("required_tokens must be a non-negative exact integer")
    if type(token_budget) is not int or token_budget < 0:
        raise TypeError("token_budget must be a non-negative exact integer")
    if required_tokens <= token_budget:
        raise ValueError("a budget overflow requires required_tokens > token_budget")
    capture = _BUDGET_OVERFLOW_CAPTURE.get()
    if capture is not None:
        capture.append((required_tokens, token_budget))
    return ValueError(
        "loss-resistant context exceeds token budget by "
        f"{required_tokens - token_budget} estimated tokens"
    )


class ContextCompiler:
    """Compile verbose histories into typed, verifiable active context.

    Built-in deterministic recovery and certification passes run before an
    optional primary extractor. A primary provider failure therefore degrades
    to verified deterministic memory unless strict failure policy is enabled.
    A caller-supplied safety extractor is additive and cannot replace the
    built-in certification obligation.
    """

    def __init__(
        self,
        extractor: Extractor | None = None,
        *,
        policy: CompilationPolicy | None = None,
        safety_extractor: Extractor | None = None,
        token_counter: TokenCounter | None = None,
        token_counter_id: str | None = None,
        source_limits: SourceLimits | None = None,
        compilation_limits: CompilationLimits | None = None,
        untrusted_historical_roles: bool = False,
    ) -> None:
        if token_counter is None and token_counter_id is not None:
            raise ValueError("token_counter_id requires token_counter")
        if token_counter_id is not None and (
            not isinstance(token_counter_id, str) or not token_counter_id.strip()
        ):
            raise TypeError("token_counter_id must be a non-empty string")
        if not isinstance(untrusted_historical_roles, bool):
            raise TypeError("untrusted_historical_roles must be a boolean")
        self.untrusted_historical_roles = untrusted_historical_roles
        self.compilation_limits = resolve_compilation_limits(
            compilation_limits
        )
        self.extractor = (
            extractor
            if extractor is not None
            else RuleBasedExtractor(
                max_items=self.compilation_limits.max_extractor_items,
                untrusted_historical_roles=self.untrusted_historical_roles,
            )
        )
        self._owned_default_extractor = self.extractor if extractor is None else None
        self.policy = policy if policy is not None else CompilationPolicy()
        self.safety_extractor = safety_extractor
        self._custom_token_counter = token_counter
        self._token_counter_id = token_counter_id.strip() if token_counter_id else None
        self.source_limits = resolve_source_limits(source_limits)
        self._context_window_memory_rendering_profile: str | None = None
        self._context_window_degradation: dict[str, object] | None = None

    def _set_context_window_degradation(
        self,
        *,
        mode: str,
        rung: str,
        requested_memory_budget_tokens: int,
        memory_rendering_profile: str | None,
    ) -> None:
        """Bind the one closed opt-in degradation decision before compilation."""

        if type(mode) is not str or mode != CONTEXT_WINDOW_DEGRADATION_MODE:
            raise ValueError("unsupported context-window degradation mode")
        if type(rung) is not str or rung not in CONTEXT_WINDOW_DEGRADATION_RUNGS:
            raise ValueError("unsupported context-window degradation rung")
        if (
            type(requested_memory_budget_tokens) is not int
            or requested_memory_budget_tokens <= 0
        ):
            raise TypeError("requested memory budget must be a positive exact integer")
        compact = rung in {
            "lossless_compact",
            "minimal_memory_reallocation_compact",
        }
        if compact:
            if memory_rendering_profile != LOSSLESS_COMPACT_MEMORY_RENDERING_PROFILE:
                raise ValueError("compact degradation rung requires the compact profile")
        elif memory_rendering_profile is not None:
            raise ValueError("standard degradation rung cannot use a compact profile")
        effective = self.policy.token_budget
        if rung == "lossless_compact" and effective != requested_memory_budget_tokens:
            raise ValueError("lossless compact rung cannot reallocate memory")
        if rung != "lossless_compact" and effective <= requested_memory_budget_tokens:
            raise ValueError("memory reallocation rung must increase the requested budget")
        self._context_window_memory_rendering_profile = memory_rendering_profile
        self._context_window_degradation = {
            "mode": mode,
            "rung": rung,
            "requested_memory_budget_tokens": requested_memory_budget_tokens,
            "effective_memory_budget_tokens": effective,
        }

    def _render_selected(
        self,
        items: Iterable[MemoryItem],
        selected_item_ids: Iterable[str],
    ) -> str:
        if self._context_window_memory_rendering_profile is None:
            return render_typed_memory(items, selected_item_ids)
        return render_lossless_compact_typed_memory(items, selected_item_ids)

    def compile(
        self,
        sources: Iterable[SourceRecord | dict],
        *,
        timeout_seconds: float | None = None,
    ) -> CompiledMemory:
        """Compile sources directly or inside an owned deadline process tree."""

        if timeout_seconds is None:
            return self._compile_impl(sources)
        from .isolation import compile_isolated

        return compile_isolated(
            self,
            sources,
            timeout_seconds=timeout_seconds,
        )

    def _compile_impl(
        self,
        sources: Iterable[SourceRecord | dict],
    ) -> CompiledMemory:
        started = time.perf_counter()
        ordered = self._prepare_sources(sources, limits=self.source_limits)
        trusted_source_digest = source_digest(ordered)

        # These built-in passes are deliberately not constructor seams. Custom
        # extractors may add coverage, but cannot reduce the obligation against
        # which verified output is certified.
        recovery_extractor = RuleBasedExtractor(
            max_items=self.compilation_limits.max_extractor_items,
            untrusted_historical_roles=self.untrusted_historical_roles,
        )
        certification_extractor = RuleBasedExtractor(
            protected_only=True,
            max_items=self.compilation_limits.max_extractor_items,
            untrusted_historical_roles=self.untrusted_historical_roles,
        )
        recovery = self._validated_extraction(
            recovery_extractor.extract(ordered),
            label="built-in recovery extractor",
        )
        certification = self._validated_extraction(
            certification_extractor.extract(ordered),
            label="built-in certification extractor",
        )
        total_candidate_items = len(recovery.items) + len(
            certification.items
        )
        self._validate_total_candidate_items(total_candidate_items)
        safety_results: list[tuple[str, ExtractionResult]] = [
            (recovery_extractor.name, recovery)
        ]
        primary_name = self._extractor_name(self.extractor)
        initial_issues: list[VerificationIssue] = []
        custom_safety_failure: dict[str, str] | None = None
        custom_safety_rejections: list[dict] = []
        custom_safety_name = (
            self._extractor_name(self.safety_extractor)
            if self.safety_extractor is not None
            else None
        )
        if self.safety_extractor is not None:
            try:
                custom_safety = self._validated_extraction(
                    self.safety_extractor.extract(ordered),
                    label=f"additive safety extractor {custom_safety_name!r}",
                )
            except CompilationLimitError:
                raise
            except Exception as exc:
                custom_safety_failure = {
                    "extractor": custom_safety_name,
                    "exception_type": type(exc).__name__,
                }
                initial_issues.append(
                    VerificationIssue(
                        code="additive_safety_extractor_failed",
                        severity=IssueSeverity.WARNING,
                        message=ADDITIVE_SAFETY_EXTRACTOR_FAILED_MESSAGE,
                    )
                )
            else:
                total_candidate_items += len(custom_safety.items)
                self._validate_total_candidate_items(total_candidate_items)
                safety_results.append((custom_safety_name, custom_safety))
                custom_safety_rejections = custom_safety.rejected

        primary_failure: dict[str, str] | None = None
        primary_degradation: dict[str, str] | None = None
        try:
            primary = self._validated_extraction(
                self.extractor.extract(ordered),
                label=f"primary extractor {primary_name!r}",
            )
        except CompilationLimitError:
            raise
        except Exception as exc:
            if self.policy.fail_on_primary_extractor_error:
                raise RuntimeError(
                    f"primary extractor {primary_name!r} failed"
                ) from exc
            primary_failure = {
                "extractor": primary_name,
                "exception_type": type(exc).__name__,
            }
            primary = ExtractionResult(
                metadata={"extractor": primary_name, "degraded": True}
            )
            initial_issues.append(
                VerificationIssue(
                    code="primary_extractor_failed",
                    severity=IssueSeverity.WARNING,
                    message=PRIMARY_EXTRACTOR_FAILED_MESSAGE,
                )
            )
        total_candidate_items += len(primary.items)
        self._validate_total_candidate_items(total_candidate_items)
        if primary_failure is None and primary.metadata.get("degraded") is True:
            raw_reason = primary.metadata.get("failure_reason")
            reason = (
                raw_reason
                if isinstance(raw_reason, str) and raw_reason
                else "unusable_output"
            )
            primary_degradation = {
                "extractor": primary_name,
                "reason": reason,
            }
            initial_issues.append(
                VerificationIssue(
                    code="primary_extractor_degraded",
                    severity=IssueSeverity.WARNING,
                    message=PRIMARY_EXTRACTOR_DEGRADED_MESSAGE,
                )
            )
        primary_extracted_items = len(primary.items)

        # Recheck after every caller-controlled extractor. A custom extractor
        # must not mutate even a deliberately forged SourceRecord before the
        # verified result is bound to the preflight source digest.
        if source_digest(ordered) != trusted_source_digest:
            raise ValueError("source history changed during extraction")

        work_budget = _CompilationWorkBudget(
            self.compilation_limits.max_item_work
        )
        items = list(primary.items)
        self._validate_resolved_collection(
            items,
            phase="primary extraction",
        )
        if self.policy.recover_missed_protected:
            for extractor_name, safety in safety_results:
                for candidate in safety.items:
                    if not self._span_kind_present(
                        candidate,
                        items,
                        work_budget=work_budget,
                    ):
                        recovered_candidate = MemoryItem.from_dict(candidate.to_dict())
                        recovered_candidate.tags = sorted(
                            set(recovered_candidate.tags) | {"verifier-recovered"}
                        )
                        recovered_candidate.metadata["recovered_by"] = extractor_name
                        items.append(recovered_candidate)
                        self._validate_resolved_collection(
                            items,
                            phase="protected recovery",
                        )

        items = resolve_temporal_state(
            items,
            limits=self.compilation_limits,
            work_budget=work_budget,
        )
        # Count replayable recovered ledger items after temporal resolution.
        # Pre-resolution insertions may merge, so reporting insertion attempts
        # would make a freshly compiled artifact fail independent replay.
        recovered = sum("verifier-recovered" in item.tags for item in items)
        selected_ids = self._select(
            items,
            ordered,
            work_budget=work_budget,
        )

        # Build once with placeholder stats so prompt rendering and its schema
        # overhead are included in the active token accounting.
        placeholder = CompressionStats(
            source_chars=sum(len(source.content) for source in ordered),
            active_chars=0,
            source_tokens_estimate=self._count_tokens(
                "\n".join(source.content for source in ordered)
            ),
            active_tokens_estimate=0,
            compression_ratio=1.0,
            token_budget=self.policy.token_budget,
            budget_overflow=0,
            target_met=False,
        )
        result = CompiledMemory(
            schema_version=SCHEMA_VERSION,
            compiled_at=utc_now(),
            source_digest=trusted_source_digest,
            source_count=len(ordered),
            items=items,
            selected_item_ids=selected_ids,
            verification=VerificationReport(passed=True),
            compression=placeholder,
            compiler_metadata={
                "extractor": primary_name,
                "safety_extractor": recovery_extractor.name,
                "certification_extractor": certification_extractor.name,
                "additive_safety_extractor": (
                    custom_safety_name
                ),
                "primary_rejections": primary.rejected,
                "primary_extractor_metadata": primary.metadata,
                "primary_failure": primary_failure,
                "primary_degradation": primary_degradation,
                "additive_safety_rejections": custom_safety_rejections,
                "additive_safety_failure": custom_safety_failure,
                "recovered_items": recovered,
                "loss_policy": "protected-items-never-drop",
                "source_limits": self.source_limits.to_dict(),
                "compilation_limits": self.compilation_limits.to_dict(),
                "policy": {
                    "token_budget": self.policy.token_budget,
                    "minimum_compression_ratio": self.policy.minimum_compression_ratio,
                    "chars_per_token": self.policy.chars_per_token,
                    "token_counter": (
                        "character-estimate-v1"
                        if self._custom_token_counter is None
                        else (
                            f"custom:{self._token_counter_id}"
                            if self._token_counter_id
                            else "custom-unverifiable"
                        )
                    ),
                    "include_superseded": self.policy.include_superseded,
                    "include_discarded": self.policy.include_discarded,
                },
                "fail_on_primary_extractor_error": (
                    self.policy.fail_on_primary_extractor_error
                ),
                **(
                    {"connector_untrusted_historical_roles": True}
                    if self.untrusted_historical_roles
                    else {}
                ),
                **(
                    {
                        MEMORY_RENDERING_PROFILE_METADATA_KEY: (
                            self._context_window_memory_rendering_profile
                        )
                    }
                    if self._context_window_memory_rendering_profile is not None
                    else {}
                ),
                **(
                    {CONTEXT_WINDOW_DEGRADATION_METADATA_KEY: self._context_window_degradation}
                    if self._context_window_degradation is not None
                    else {}
                ),
            },
        )
        active_prompt = result.to_prompt()
        active_tokens = self._count_tokens(active_prompt)
        source_tokens = placeholder.source_tokens_estimate
        ratio = source_tokens / max(1, active_tokens)
        result.compression = CompressionStats(
            source_chars=placeholder.source_chars,
            active_chars=len(active_prompt),
            source_tokens_estimate=source_tokens,
            active_tokens_estimate=active_tokens,
            compression_ratio=round(ratio, 4),
            token_budget=self.policy.token_budget,
            budget_overflow=max(0, active_tokens - self.policy.token_budget),
            target_met=ratio >= self.policy.minimum_compression_ratio,
        )
        if self.policy.fail_on_budget_overflow and result.compression.budget_overflow:
            if type(active_tokens) is int and type(self.policy.token_budget) is int:
                raise _budget_overflow_error(
                    required_tokens=active_tokens,
                    token_budget=self.policy.token_budget,
                )
            raise ValueError(
                "loss-resistant context exceeds token budget by "
                f"{result.compression.budget_overflow} estimated tokens"
            )

        if self.policy.verify:
            result.verification = verify_memory(
                sources=ordered,
                items=items,
                selected_item_ids=selected_ids,
                protected_candidates=certification.items,
                recovered_items=recovered,
                budget_overflow=result.compression.budget_overflow,
                compression_target_met=result.compression.target_met,
                initial_issues=initial_issues,
                work_budget=work_budget,
                untrusted_historical_roles=self.untrusted_historical_roles,
            )
        else:
            result.verification = VerificationReport(
                passed=False,
                issues=[
                    *initial_issues,
                    VerificationIssue(
                        code="verification_not_performed",
                        severity=IssueSeverity.ERROR,
                        message="Loss-resistant verification was explicitly disabled.",
                    )
                ],
            )
        selected_set = set(selected_ids)
        protected_selected = [
            item
            for item in items
            if item.id in selected_set and item.protected
        ]
        protected_prompt_tokens = (
            self._count_tokens(
                self._render_selected(
                    protected_selected,
                    [item.id for item in protected_selected],
                )
            )
            if protected_selected
            else 0
        )
        metrics = CompilationMetrics(
            source_records=len(ordered),
            primary_extracted_items=primary_extracted_items,
            recovery_candidate_items=len(recovery.items),
            certification_candidate_items=len(certification.items),
            recovery_added_items=recovered,
            resolved_items=len(items),
            selected_items=len(selected_ids),
            active_items=sum(
                item.status == MemoryStatus.ACTIVE for item in items
            ),
            superseded_items=sum(
                item.status == MemoryStatus.SUPERSEDED for item in items
            ),
            discarded_items=sum(
                item.status == MemoryStatus.DISCARDED for item in items
            ),
            conflicting_items=sum(
                item.status == MemoryStatus.CONFLICTING for item in items
            ),
            detected_conflicts=sum(
                item.kind == MemoryKind.UNRESOLVED
                and "detected-conflict" in item.tags
                for item in items
            ),
            protected_items=sum(item.protected for item in items),
            protected_selected_items=len(protected_selected),
            protected_prompt_tokens=protected_prompt_tokens,
            protected_budget_overflow=max(
                0,
                protected_prompt_tokens - self.policy.token_budget,
            ),
            verification_error_count=sum(
                issue.severity == IssueSeverity.ERROR
                for issue in result.verification.issues
            ),
            verification_warning_count=sum(
                issue.severity == IssueSeverity.WARNING
                for issue in result.verification.issues
            ),
            verification_info_count=sum(
                issue.severity == IssueSeverity.INFO
                for issue in result.verification.issues
            ),
            compile_duration_seconds=time.perf_counter() - started,
        )
        result.compiler_metadata["metrics"] = metrics.to_dict()
        return result.seal()

    @staticmethod
    def _prepare_sources(
        sources: Iterable[SourceRecord | dict],
        *,
        limits: SourceLimits,
    ) -> list[SourceRecord]:
        prepared: list[SourceRecord] = []
        total_size = 0
        for index, value in enumerate(sources):
            if index >= limits.max_records:
                raise SourceLimitError(
                    f"source record count exceeds {limits.max_records} records"
                )
            if isinstance(value, SourceRecord):
                source = value
                raw_size = source_value_size(
                    source.to_dict(),
                    limits=limits,
                    index=index,
                )
            elif isinstance(value, dict):
                raw_size = source_value_size(value, limits=limits, index=index)
                source = SourceRecord.from_dict(value, default_sequence=index)
                normalized_size = source_value_size(
                    source.to_dict(),
                    limits=limits,
                    index=index,
                )
                raw_size = max(raw_size, normalized_size)
            else:
                raise TypeError("sources must contain SourceRecord or dictionary values")
            total_size = add_source_size(
                total_size,
                raw_size,
                limits=limits,
            )
            prepared.append(source)
        # Exact SourceRecord instances have immutable, validated string IDs and
        # integer sequences.  Validate them without materializing parallel ID
        # and sequence lists, then sort the local list in place and detect
        # adjacent duplicate sequences.  Preserve the legacy access pattern for
        # subclasses, whose attribute reads may be caller-controlled.
        if all(
            type(source) is SourceRecord
            and type(source.id) is str
            and type(source.sequence) is int
            for source in prepared
        ):
            seen_ids: set[str] = set()
            for source in prepared:
                if source.id in seen_ids:
                    raise ValueError("source ids must be unique")
                seen_ids.add(source.id)
            del seen_ids
            prepared.sort(key=lambda source: source.sequence)
            if any(
                prepared[index - 1].sequence == prepared[index].sequence
                for index in range(1, len(prepared))
            ):
                raise ValueError("source sequences must be unique")
            return prepared

        ids = [source.id for source in prepared]
        if len(ids) != len(set(ids)):
            raise ValueError("source ids must be unique")
        sequences = [source.sequence for source in prepared]
        if len(sequences) != len(set(sequences)):
            raise ValueError("source sequences must be unique")
        return sorted(prepared, key=lambda source: source.sequence)

    @staticmethod
    def _span_kind_present(
        candidate: MemoryItem,
        items: list[MemoryItem],
        *,
        work_budget: _CompilationWorkBudget,
    ) -> bool:
        for item in items:
            work_budget.consume(1, phase="protected recovery")
            if memory_item_covers_candidate(candidate, item):
                return True
        return False

    def _validated_extraction(
        self,
        result: ExtractionResult,
        *,
        label: str,
    ) -> ExtractionResult:
        if not isinstance(result, ExtractionResult):
            raise TypeError("extractor must return ExtractionResult")
        if not all(isinstance(item, MemoryItem) for item in result.items):
            raise TypeError("extractor result items must be MemoryItem values")
        if len(result.items) > self.compilation_limits.max_extractor_items:
            raise CompilationLimitError(
                f"{label} exceeds "
                f"{self.compilation_limits.max_extractor_items} memory items"
            )
        if not isinstance(result.rejected, list) or not all(
            isinstance(rejection, dict) for rejection in result.rejected
        ):
            raise TypeError("extractor rejections must be dictionaries")
        if (
            len(result.rejected)
            > self.compilation_limits.max_extractor_rejections
        ):
            raise CompilationLimitError(
                f"{label} exceeds "
                f"{self.compilation_limits.max_extractor_rejections} "
                "rejections"
            )
        if not isinstance(result.metadata, dict):
            raise TypeError("extractor metadata must be an object")

        item_bytes = 2
        provenance_spans = 0
        for index, item in enumerate(result.items):
            provenance_spans += len(item.provenance)
            if (
                provenance_spans
                > self.compilation_limits.max_provenance_spans
            ):
                raise CompilationLimitError(
                    f"{label} exceeds "
                    f"{self.compilation_limits.max_provenance_spans} "
                    "provenance spans"
                )
            try:
                item_payload = item.to_dict()
            except (RecursionError, TypeError, ValueError, OverflowError) as exc:
                raise CompilationLimitError(
                    f"{label} item {index} cannot be measured as canonical JSON"
                ) from exc
            item_size = bounded_json_utf8_size(
                item_payload,
                max_bytes=self.compilation_limits.max_extractor_bytes,
                max_depth=128,
                label=f"{label} item {index}",
                limit_error=CompilationLimitError,
            )
            item_bytes += item_size + (1 if index else 0)
            if item_bytes > self.compilation_limits.max_extractor_bytes:
                raise CompilationLimitError(
                    f"{label} items exceed "
                    f"{self.compilation_limits.max_extractor_bytes} "
                    "canonical UTF-8 JSON bytes"
                )

        bounded_json_utf8_size(
            {
                "rejected": result.rejected,
                "metadata": result.metadata,
            },
            max_bytes=(
                self.compilation_limits.max_extractor_auxiliary_bytes
            ),
            max_depth=128,
            label=f"{label} auxiliary data",
            limit_error=CompilationLimitError,
        )
        return result

    def _validate_total_candidate_items(self, count: int) -> None:
        if count > self.compilation_limits.max_total_candidate_items:
            raise CompilationLimitError(
                "combined extractor candidates exceed "
                f"{self.compilation_limits.max_total_candidate_items} items"
            )

    def _validate_resolved_collection(
        self,
        items: list[MemoryItem],
        *,
        phase: str,
    ) -> None:
        if len(items) > self.compilation_limits.max_resolved_items:
            raise CompilationLimitError(
                f"{phase} exceeds "
                f"{self.compilation_limits.max_resolved_items} resolved items"
            )
        provenance_spans = sum(len(item.provenance) for item in items)
        if provenance_spans > self.compilation_limits.max_provenance_spans:
            raise CompilationLimitError(
                f"{phase} exceeds "
                f"{self.compilation_limits.max_provenance_spans} "
                "provenance spans"
            )

    @staticmethod
    def _extractor_name(extractor: Extractor) -> str:
        """Return a stable diagnostic name without trusting a custom property."""

        try:
            name = extractor.name
        except Exception:
            name = None
        if isinstance(name, str) and name.strip():
            return name.strip()
        return type(extractor).__name__

    def _count_tokens(self, text: str) -> int:
        if self._custom_token_counter is not None:
            value = self._custom_token_counter(text)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError("token_counter must return an integer")
            if value < 0:
                raise ValueError("token_counter cannot return a negative count")
            return value
        return max(1, math.ceil(len(text) / self.policy.chars_per_token))

    def _item_cost(self, item: MemoryItem) -> int:
        if self._context_window_memory_rendering_profile is not None:
            return self._count_tokens(self._render_selected([item], [item.id]))
        return self._count_tokens(
            f"{item.kind.value}:\n{render_prompt_item(item)}\n"
        )

    def _can_count_default_selection_by_length(
        self,
        items: list[MemoryItem],
    ) -> bool:
        """Return whether default prompt token counts are exactly length-derived.

        The optimized selection path deliberately excludes every caller-defined
        dispatch seam.  Besides a custom token counter or compact renderer,
        that includes compiler subclasses, instance method overrides, policy
        subclasses, replaced module renderers, and non-exact prompt data.  All
        excluded configurations retain the legacy render-and-count loop.
        """

        if (
            type(self) is not ContextCompiler
            or self._custom_token_counter is not None
            or self._context_window_memory_rendering_profile is not None
            or type(self.extractor) is not RuleBasedExtractor
            or self.extractor is not self._owned_default_extractor
            or self.safety_extractor is not None
            or type(self.policy) is not CompilationPolicy
            or type(self.policy.chars_per_token) not in {int, float}
            or render_typed_memory is not _DEFAULT_TYPED_MEMORY_RENDERER
            or render_prompt_item is not _DEFAULT_PROMPT_ITEM_RENDERER
            or _models_module.render_prompt_item is not _DEFAULT_PROMPT_ITEM_RENDERER
            or type(self)._count_tokens is not _DEFAULT_COUNT_TOKENS_METHOD
            or type(self)._item_cost is not _DEFAULT_ITEM_COST_METHOD
            or type(self)._render_selected is not _DEFAULT_RENDER_SELECTED_METHOD
        ):
            return False
        instance_state = vars(self)
        if any(
            name in instance_state
            for name in ("_count_tokens", "_item_cost", "_render_selected")
        ):
            return False
        for item in items:
            if (
                type(item) is not MemoryItem
                or type(item.id) is not str
                or type(item.kind) is not MemoryKind
                or type(item.text) is not str
                or type(item.status) is not MemoryStatus
                or type(item.exact) is not bool
                or type(item.metadata) is not dict
                or type(item.metadata.get("source_role", "unknown")) is not str
                or type(item.provenance) is not list
            ):
                return False
            for span in item.provenance:
                if (
                    type(span) is not ProvenanceSpan
                    or type(span.source_id) is not str
                    or type(span.start) is not int
                    or type(span.end) is not int
                    or type(span.quote_sha256) is not str
                ):
                    return False
        return True

    def _select(
        self,
        items: list[MemoryItem],
        sources: list[SourceRecord],
        *,
        work_budget: _CompilationWorkBudget,
    ) -> list[str]:
        source_text = "\n".join(source.content for source in sources)
        source_tokens = self._count_tokens(source_text)
        target_budget = math.floor(
            source_tokens / float(self.policy.minimum_compression_ratio)
        )

        eligible: list[MemoryItem] = []
        for item in items:
            if item.status == MemoryStatus.SUPERSEDED and not self.policy.include_superseded:
                continue
            if item.status == MemoryStatus.DISCARDED and not self.policy.include_discarded:
                continue
            if item.kind == MemoryKind.DISCARDED_ATTEMPT and not self.policy.include_discarded:
                continue
            eligible.append(item)

        protected = [item for item in eligible if item.protected]
        optional = [item for item in eligible if not item.protected]
        # Old discarded attempts remain useful but lose to current decisions and
        # facts. Recency breaks ties rather than dominating semantic priority.
        optional_costs: dict[str, int] = {}
        for item in optional:
            work_budget.consume(1, phase="selection item costing")
            optional_costs[item.id] = self._item_cost(item)
        optional.sort(
            key=lambda item: (
                item.priority,
                item.confidence,
                item.source_sequence,
                -optional_costs[item.id],
            ),
            reverse=True,
        )
        protected.sort(key=lambda item: (item.kind.value, item.source_sequence, item.id))

        chosen: list[MemoryItem] = list(protected)
        protected_prompt = self._render_selected(chosen, [item.id for item in chosen])
        protected_floor = self._count_tokens(protected_prompt)
        # A target below the mandatory envelope/protected floor is impossible.
        # In that case retain useful optional state under the ordinary budget
        # and report target_met=false rather than discarding everything.
        effective_budget = self.policy.token_budget
        if target_budget >= protected_floor:
            effective_budget = min(effective_budget, target_budget)
        count_by_length = ContextCompiler._can_count_default_selection_by_length(
            self,
            eligible,
        )
        if count_by_length:
            rendered_length = len(protected_prompt)
            selected_kinds = {item.kind for item in chosen}
        for item in optional:
            trial = [*chosen, item]
            work_budget.consume(
                len(trial),
                phase="selection prompt rendering",
            )
            if count_by_length:
                item_line_length = len(_DEFAULT_PROMPT_ITEM_RENDERER(item))
                trial_rendered_length = rendered_length + item_line_length + 1
                if item.kind not in selected_kinds:
                    # A newly non-empty kind adds its ``kind:`` header and one
                    # more newline. The item's own newline is included above.
                    trial_rendered_length += len(item.kind.value) + 2
                trial_tokens = max(
                    1,
                    math.ceil(
                        trial_rendered_length / self.policy.chars_per_token
                    ),
                )
            else:
                rendered = self._render_selected(
                    trial,
                    [value.id for value in trial],
                )
                trial_tokens = self._count_tokens(rendered)
            if trial_tokens <= effective_budget:
                chosen.append(item)
                if count_by_length:
                    rendered_length = trial_rendered_length
                    selected_kinds.add(item.kind)
        return [item.id for item in chosen]


# Freeze the exact implementation identities used by the internal fast-path
# gate. Runtime replacement or subclass dispatch must fall back to the legacy
# render-and-count behavior rather than inherit assumptions about these bodies.
_DEFAULT_TYPED_MEMORY_RENDERER = render_typed_memory
_DEFAULT_PROMPT_ITEM_RENDERER = render_prompt_item
_DEFAULT_COUNT_TOKENS_METHOD = ContextCompiler._count_tokens
_DEFAULT_ITEM_COST_METHOD = ContextCompiler._item_cost
_DEFAULT_RENDER_SELECTED_METHOD = ContextCompiler._render_selected
