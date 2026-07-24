"""Context compilation pipeline."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable

from .extractors import ExtractionResult, Extractor, RuleBasedExtractor
from .limits import (
    SourceLimitError,
    SourceLimits,
    add_source_size,
    resolve_source_limits,
    source_value_size,
)
from .models import (
    ADDITIVE_SAFETY_EXTRACTOR_FAILED_MESSAGE,
    PRIMARY_EXTRACTOR_DEGRADED_MESSAGE,
    PRIMARY_EXTRACTOR_FAILED_MESSAGE,
    SCHEMA_VERSION,
    CompilationPolicy,
    CompiledMemory,
    CompressionStats,
    IssueSeverity,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    SourceRecord,
    VerificationIssue,
    VerificationReport,
    render_prompt_item,
    render_typed_memory,
    source_digest,
    utc_now,
)
from .resolver import resolve_temporal_state
from .verifier import verify_memory

TokenCounter = Callable[[str], int]


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
    ) -> None:
        if token_counter is None and token_counter_id is not None:
            raise ValueError("token_counter_id requires token_counter")
        if token_counter_id is not None and (
            not isinstance(token_counter_id, str) or not token_counter_id.strip()
        ):
            raise TypeError("token_counter_id must be a non-empty string")
        self.extractor = extractor if extractor is not None else RuleBasedExtractor()
        self.policy = policy if policy is not None else CompilationPolicy()
        self.safety_extractor = safety_extractor
        self._custom_token_counter = token_counter
        self._token_counter_id = token_counter_id.strip() if token_counter_id else None
        self.source_limits = resolve_source_limits(source_limits)

    def compile(self, sources: Iterable[SourceRecord | dict]) -> CompiledMemory:
        ordered = self._prepare_sources(sources, limits=self.source_limits)
        trusted_source_digest = source_digest(ordered)

        # These built-in passes are deliberately not constructor seams. Custom
        # extractors may add coverage, but cannot reduce the obligation against
        # which verified output is certified.
        recovery_extractor = RuleBasedExtractor()
        certification_extractor = RuleBasedExtractor(protected_only=True)
        recovery = recovery_extractor.extract(ordered)
        certification = certification_extractor.extract(ordered)
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
                    self.safety_extractor.extract(ordered)
                )
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
                safety_results.append((custom_safety_name, custom_safety))
                custom_safety_rejections = custom_safety.rejected

        primary_failure: dict[str, str] | None = None
        primary_degradation: dict[str, str] | None = None
        try:
            primary = self._validated_extraction(self.extractor.extract(ordered))
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

        # Recheck after every caller-controlled extractor. A custom extractor
        # must not mutate even a deliberately forged SourceRecord before the
        # verified result is bound to the preflight source digest.
        if source_digest(ordered) != trusted_source_digest:
            raise ValueError("source history changed during extraction")

        items = list(primary.items)
        recovered = 0
        if self.policy.recover_missed_protected:
            for extractor_name, safety in safety_results:
                for candidate in safety.items:
                    if not self._span_kind_present(candidate, items):
                        recovered_candidate = MemoryItem.from_dict(candidate.to_dict())
                        recovered_candidate.tags = sorted(
                            set(recovered_candidate.tags) | {"verifier-recovered"}
                        )
                        recovered_candidate.metadata["recovered_by"] = extractor_name
                        items.append(recovered_candidate)
                        recovered += 1

        items = resolve_temporal_state(items)
        selected_ids = self._select(items, ordered)

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
        ids = [source.id for source in prepared]
        if len(ids) != len(set(ids)):
            raise ValueError("source ids must be unique")
        sequences = [source.sequence for source in prepared]
        if len(sequences) != len(set(sequences)):
            raise ValueError("source sequences must be unique")
        return sorted(prepared, key=lambda source: source.sequence)

    @staticmethod
    def _span_kind_present(candidate: MemoryItem, items: list[MemoryItem]) -> bool:
        candidate_spans = {
            (span.source_id, span.start, span.end) for span in candidate.provenance
        }
        return any(
            item.kind == candidate.kind
            and item.text.strip() == candidate.text.strip()
            and candidate_spans
            <= {(span.source_id, span.start, span.end) for span in item.provenance}
            for item in items
        )

    @staticmethod
    def _validated_extraction(result: ExtractionResult) -> ExtractionResult:
        if not isinstance(result, ExtractionResult):
            raise TypeError("extractor must return ExtractionResult")
        if not all(isinstance(item, MemoryItem) for item in result.items):
            raise TypeError("extractor result items must be MemoryItem values")
        if not isinstance(result.rejected, list) or not all(
            isinstance(rejection, dict) for rejection in result.rejected
        ):
            raise TypeError("extractor rejections must be dictionaries")
        if not isinstance(result.metadata, dict):
            raise TypeError("extractor metadata must be an object")
        return result

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
        return self._count_tokens(
            f"{item.kind.value}:\n{render_prompt_item(item)}\n"
        )

    def _select(self, items: list[MemoryItem], sources: list[SourceRecord]) -> list[str]:
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
        optional.sort(
            key=lambda item: (
                item.priority,
                item.confidence,
                item.source_sequence,
                -self._item_cost(item),
            ),
            reverse=True,
        )
        protected.sort(key=lambda item: (item.kind.value, item.source_sequence, item.id))

        chosen: list[MemoryItem] = list(protected)
        protected_prompt = render_typed_memory(chosen, [item.id for item in chosen])
        protected_floor = self._count_tokens(protected_prompt)
        # A target below the mandatory envelope/protected floor is impossible.
        # In that case retain useful optional state under the ordinary budget
        # and report target_met=false rather than discarding everything.
        effective_budget = self.policy.token_budget
        if target_budget >= protected_floor:
            effective_budget = min(effective_budget, target_budget)
        for item in optional:
            trial = [*chosen, item]
            rendered = render_typed_memory(trial, [value.id for value in trial])
            if self._count_tokens(rendered) <= effective_budget:
                chosen.append(item)
        return [item.id for item in chosen]
