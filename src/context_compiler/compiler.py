"""Context compilation pipeline."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable

from .extractors import Extractor, RuleBasedExtractor
from .models import (
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

    A provider-specific model extractor may improve semantic coverage, and an
    independent rule-based safety pass runs by default after primary extraction
    succeeds. Provider exceptions currently propagate before that pass; this
    alpha limitation is tracked as a P0 item in the project roadmap.
    """

    def __init__(
        self,
        extractor: Extractor | None = None,
        *,
        policy: CompilationPolicy | None = None,
        safety_extractor: Extractor | None = None,
        token_counter: TokenCounter | None = None,
        token_counter_id: str | None = None,
    ) -> None:
        if token_counter is None and token_counter_id is not None:
            raise ValueError("token_counter_id requires token_counter")
        if token_counter_id is not None and (
            not isinstance(token_counter_id, str) or not token_counter_id.strip()
        ):
            raise TypeError("token_counter_id must be a non-empty string")
        self.extractor = extractor or RuleBasedExtractor()
        self.policy = policy or CompilationPolicy()
        self.safety_extractor = safety_extractor or RuleBasedExtractor()
        self._custom_token_counter = token_counter
        self._token_counter_id = token_counter_id.strip() if token_counter_id else None

    def compile(self, sources: Iterable[SourceRecord | dict]) -> CompiledMemory:
        ordered = self._prepare_sources(sources)
        primary = self.extractor.extract(ordered)
        safety = self.safety_extractor.extract(ordered)

        items = list(primary.items)
        recovered = 0
        if self.policy.recover_missed_protected:
            for candidate in safety.items:
                if not self._span_kind_present(candidate, items):
                    candidate.tags = sorted(set(candidate.tags) | {"verifier-recovered"})
                    candidate.metadata["recovered_by"] = self.safety_extractor.name
                    items.append(candidate)
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
            source_digest=source_digest(ordered),
            source_count=len(ordered),
            items=items,
            selected_item_ids=selected_ids,
            verification=VerificationReport(passed=True),
            compression=placeholder,
            compiler_metadata={
                "extractor": self.extractor.name,
                "safety_extractor": self.safety_extractor.name,
                "primary_rejections": primary.rejected,
                "recovered_items": recovered,
                "loss_policy": "protected-items-never-drop",
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
                protected_candidates=[item for item in safety.items if item.protected],
                recovered_items=recovered,
                budget_overflow=result.compression.budget_overflow,
                compression_target_met=result.compression.target_met,
            )
        else:
            result.verification = VerificationReport(
                passed=False,
                issues=[
                    VerificationIssue(
                        code="verification_not_performed",
                        severity=IssueSeverity.ERROR,
                        message="Loss-resistant verification was explicitly disabled.",
                    )
                ],
            )
        return result

    @staticmethod
    def _prepare_sources(sources: Iterable[SourceRecord | dict]) -> list[SourceRecord]:
        prepared: list[SourceRecord] = []
        for index, value in enumerate(sources):
            if isinstance(value, SourceRecord):
                prepared.append(value)
            elif isinstance(value, dict):
                prepared.append(SourceRecord.from_dict(value, default_sequence=index))
            else:
                raise TypeError("sources must contain SourceRecord or dictionary values")
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
