from __future__ import annotations

import math
import types
import unittest
from unittest.mock import patch

import context_compiler.compiler as compiler_module
import context_compiler.models as models_module
from context_compiler import CompilationPolicy, ContextCompiler, RuleBasedExtractor
from context_compiler.limits import CompilationLimitError, _CompilationWorkBudget
from context_compiler.models import (
    CONTEXT_WINDOW_DEGRADATION_MODE,
    LOSSLESS_COMPACT_MEMORY_RENDERING_PROFILE,
    MemoryItem,
    MemoryKind,
    ProvenanceSpan,
    SourceRecord,
    render_typed_memory,
)


class _LegacyContextCompiler(ContextCompiler):
    """A subclass intentionally excluded from the exact internal fast path."""


class _PolicySubclass(CompilationPolicy):
    pass


class _ItemSubclass(MemoryItem):
    pass


def _source(content: str = "x") -> SourceRecord:
    return SourceRecord.create(
        id="selection-source",
        sequence=0,
        role="user",
        content=content,
    )


def _item(
    source: SourceRecord,
    *,
    item_id: str,
    kind: MemoryKind,
    text: str,
    priority: int,
    sequence: int,
    item_type: type[MemoryItem] = MemoryItem,
) -> MemoryItem:
    return item_type(
        id=item_id,
        kind=kind,
        text=text,
        provenance=[ProvenanceSpan.from_source(source, 0, len(source.content))],
        priority=priority,
        metadata={"source_role": "user", "source_sequence": sequence},
    )


def _select(
    compiler: ContextCompiler,
    items: list[MemoryItem],
    source: SourceRecord,
    *,
    maximum_work: int = 1_000_000,
) -> tuple[list[str], int]:
    work_budget = _CompilationWorkBudget(maximum_work)
    selected = compiler._select(items, [source], work_budget=work_budget)
    return selected, work_budget.used


class SelectionLengthFastPathTests(unittest.TestCase):
    def test_default_selection_matches_legacy_for_empty_new_and_existing_kinds(self) -> None:
        source = _source()
        items = [
            _item(
                source,
                item_id="fact-first",
                kind=MemoryKind.CONFIRMED_FACT,
                text="first fact",
                priority=90,
                sequence=0,
            ),
            _item(
                source,
                item_id="fact-second",
                kind=MemoryKind.CONFIRMED_FACT,
                text="second fact",
                priority=80,
                sequence=1,
            ),
            _item(
                source,
                item_id="decision-first",
                kind=MemoryKind.DECISION,
                text="first decision",
                priority=70,
                sequence=2,
            ),
        ]
        policy = CompilationPolicy(
            token_budget=10_000,
            minimum_compression_ratio=1.0,
            chars_per_token=3.5,
        )

        selected, used = _select(ContextCompiler(policy=policy), items, source)
        legacy_selected, legacy_used = _select(
            _LegacyContextCompiler(policy=policy),
            items,
            source,
        )

        self.assertEqual(selected, [item.id for item in items])
        self.assertEqual(selected, legacy_selected)
        self.assertEqual(used, legacy_used)
        self.assertEqual(
            render_typed_memory(items, selected),
            render_typed_memory(items, legacy_selected),
        )

    def test_fractional_threshold_accepts_exactly_and_rejects_one_token_below(self) -> None:
        source = _source()
        item = _item(
            source,
            item_id="boundary-fact",
            kind=MemoryKind.CONFIRMED_FACT,
            text="fractional token boundary",
            priority=90,
            sequence=0,
        )
        chars_per_token = 3.5
        exact_tokens = max(
            1,
            math.ceil(len(render_typed_memory([item], [item.id])) / chars_per_token),
        )

        for token_budget, expected in (
            (exact_tokens, [item.id]),
            (exact_tokens - 1, []),
        ):
            with self.subTest(token_budget=token_budget):
                policy = CompilationPolicy(
                    token_budget=token_budget,
                    minimum_compression_ratio=1.0,
                    chars_per_token=chars_per_token,
                )
                selected, used = _select(
                    ContextCompiler(policy=policy),
                    [item],
                    source,
                )
                legacy_selected, legacy_used = _select(
                    _LegacyContextCompiler(policy=policy),
                    [item],
                    source,
                )
                self.assertEqual(selected, expected)
                self.assertEqual(selected, legacy_selected)
                self.assertEqual(used, legacy_used)

    def test_rejected_item_does_not_publish_its_kind_or_length(self) -> None:
        source = _source()
        rejected = _item(
            source,
            item_id="large-fact",
            kind=MemoryKind.CONFIRMED_FACT,
            text="large " * 80,
            priority=100,
            sequence=0,
        )
        accepted = _item(
            source,
            item_id="small-fact",
            kind=MemoryKind.CONFIRMED_FACT,
            text="small",
            priority=90,
            sequence=1,
        )
        accepted_tokens = len(render_typed_memory([accepted], [accepted.id]))

        for token_budget, expected in (
            (accepted_tokens, [accepted.id]),
            (accepted_tokens - 1, []),
        ):
            with self.subTest(token_budget=token_budget):
                policy = CompilationPolicy(
                    token_budget=token_budget,
                    minimum_compression_ratio=1.0,
                    chars_per_token=1.0,
                )
                selected, used = _select(
                    ContextCompiler(policy=policy),
                    [rejected, accepted],
                    source,
                )
                legacy_selected, legacy_used = _select(
                    _LegacyContextCompiler(policy=policy),
                    [rejected, accepted],
                    source,
                )

                self.assertEqual(selected, expected)
                self.assertEqual(selected, legacy_selected)
                self.assertEqual(used, legacy_used)

    def test_work_budget_exhaustion_keeps_legacy_order_and_usage(self) -> None:
        source = _source()
        items = [
            _item(
                source,
                item_id=f"fact-{index}",
                kind=MemoryKind.CONFIRMED_FACT,
                text=f"fact {index}",
                priority=90 - index,
                sequence=index,
            )
            for index in range(2)
        ]
        policy = CompilationPolicy(
            token_budget=10_000,
            minimum_compression_ratio=1.0,
        )

        failures: list[tuple[str, int]] = []
        for compiler_type in (ContextCompiler, _LegacyContextCompiler):
            compiler = compiler_type(policy=policy)
            work_budget = _CompilationWorkBudget(4)
            with self.assertRaises(CompilationLimitError) as captured:
                compiler._select(items, [source], work_budget=work_budget)
            failures.append((str(captured.exception), work_budget.used))

        self.assertEqual(failures[0], failures[1])
        self.assertEqual(failures[0][1], 3)
        self.assertIn("during selection prompt rendering", failures[0][0])

    def test_custom_counter_uses_every_legacy_rendered_trial(self) -> None:
        source = _source()
        items = [
            _item(
                source,
                item_id=f"fact-{index}",
                kind=MemoryKind.CONFIRMED_FACT,
                text=f"fact {index}",
                priority=90 - index,
                sequence=index,
            )
            for index in range(2)
        ]
        observed: list[str] = []

        def counter(text: str) -> int:
            observed.append(text)
            return max(1, math.ceil(len(text) / 4))

        compiler = ContextCompiler(
            policy=CompilationPolicy(
                token_budget=10_000,
                minimum_compression_ratio=1.0,
            ),
            token_counter=counter,
            token_counter_id="selection-test-counter",
        )
        selected, _used = _select(compiler, items, source)

        self.assertEqual(selected, [item.id for item in items])
        self.assertFalse(compiler._can_count_default_selection_by_length(items))
        self.assertEqual(len(observed), 6)
        self.assertEqual(observed[-1], render_typed_memory(items, selected))

    def test_custom_counter_error_and_work_limit_precedence_matches_legacy(self) -> None:
        source = _source()
        items = [
            _item(
                source,
                item_id="protected-goal",
                kind=MemoryKind.GOAL,
                text="protected goal",
                priority=100,
                sequence=0,
            ),
            _item(
                source,
                item_id="optional-fact",
                kind=MemoryKind.CONFIRMED_FACT,
                text="optional fact",
                priority=90,
                sequence=1,
            ),
        ]
        policy = CompilationPolicy(
            token_budget=10_000,
            minimum_compression_ratio=1.0,
        )

        for invalid, exception_type, message in (
            (True, TypeError, "token_counter must return an integer"),
            (-1, ValueError, "token_counter cannot return a negative count"),
        ):
            with self.subTest(invalid=invalid):
                calls = 0

                def invalid_on_trial(
                    text: str,
                    invalid_value: int = invalid,
                ) -> int:
                    nonlocal calls
                    calls += 1
                    if calls == 4:
                        return invalid_value
                    return max(1, math.ceil(len(text) / 4))

                compiler = ContextCompiler(
                    policy=policy,
                    token_counter=invalid_on_trial,
                    token_counter_id="precedence-counter",
                )
                work_budget = _CompilationWorkBudget(3)
                with self.assertRaisesRegex(exception_type, message):
                    compiler._select(items, [source], work_budget=work_budget)
                self.assertEqual(calls, 4)
                self.assertEqual(work_budget.used, 3)

        calls = 0

        def valid_counter(text: str) -> int:
            nonlocal calls
            calls += 1
            return max(1, math.ceil(len(text) / 4))

        compiler = ContextCompiler(
            policy=policy,
            token_counter=valid_counter,
            token_counter_id="work-limit-counter",
        )
        work_budget = _CompilationWorkBudget(2)
        with self.assertRaisesRegex(
            CompilationLimitError,
            "during selection prompt rendering",
        ):
            compiler._select(items, [source], work_budget=work_budget)
        self.assertEqual(calls, 3)
        self.assertEqual(work_budget.used, 1)

    def test_subclass_instance_override_and_non_exact_item_use_legacy_path(self) -> None:
        source = _source()
        exact_item = _item(
            source,
            item_id="exact-fact",
            kind=MemoryKind.CONFIRMED_FACT,
            text="exact fact",
            priority=90,
            sequence=0,
        )
        subclass_item = _item(
            source,
            item_id="subclass-fact",
            kind=MemoryKind.CONFIRMED_FACT,
            text="subclass fact",
            priority=80,
            sequence=1,
            item_type=_ItemSubclass,
        )
        policy = CompilationPolicy(
            token_budget=10_000,
            minimum_compression_ratio=1.0,
        )
        compiler = ContextCompiler(policy=policy)

        self.assertTrue(compiler._can_count_default_selection_by_length([exact_item]))
        self.assertFalse(
            _LegacyContextCompiler(policy=policy)._can_count_default_selection_by_length(
                [exact_item]
            )
        )
        self.assertFalse(
            ContextCompiler(
                policy=_PolicySubclass(
                    token_budget=10_000,
                    minimum_compression_ratio=1.0,
                )
            )._can_count_default_selection_by_length([exact_item])
        )
        self.assertFalse(compiler._can_count_default_selection_by_length([subclass_item]))
        self.assertFalse(
            ContextCompiler(
                extractor=RuleBasedExtractor(),
                policy=policy,
            )._can_count_default_selection_by_length([exact_item])
        )
        self.assertFalse(
            ContextCompiler(
                safety_extractor=RuleBasedExtractor(),
                policy=policy,
            )._can_count_default_selection_by_length([exact_item])
        )

        original_render = compiler._render_selected
        compiler._render_selected = types.MethodType(  # type: ignore[method-assign]
            lambda _self, items, selected: original_render(items, selected),
            compiler,
        )
        self.assertFalse(compiler._can_count_default_selection_by_length([exact_item]))

    def test_compact_and_replaced_default_renderers_use_legacy_calls(self) -> None:
        source = _source()
        items = [
            _item(
                source,
                item_id=f"fact-{index}",
                kind=MemoryKind.CONFIRMED_FACT,
                text=f"fact {index}",
                priority=90 - index,
                sequence=index,
            )
            for index in range(2)
        ]
        policy = CompilationPolicy(
            token_budget=10_000,
            minimum_compression_ratio=1.0,
        )
        compact = ContextCompiler(policy=policy)
        compact._set_context_window_degradation(
            mode=CONTEXT_WINDOW_DEGRADATION_MODE,
            rung="lossless_compact",
            requested_memory_budget_tokens=policy.token_budget,
            memory_rendering_profile=LOSSLESS_COMPACT_MEMORY_RENDERING_PROFILE,
        )
        self.assertFalse(compact._can_count_default_selection_by_length(items))
        compact_selected, compact_used = _select(compact, items, source)

        legacy_compact = _LegacyContextCompiler(policy=policy)
        legacy_compact._set_context_window_degradation(
            mode=CONTEXT_WINDOW_DEGRADATION_MODE,
            rung="lossless_compact",
            requested_memory_budget_tokens=policy.token_budget,
            memory_rendering_profile=LOSSLESS_COMPACT_MEMORY_RENDERING_PROFILE,
        )
        legacy_selected, legacy_used = _select(
            legacy_compact,
            items,
            source,
        )
        self.assertEqual(compact_selected, legacy_selected)
        self.assertEqual(compact_used, legacy_used)

        original_renderer = compiler_module.render_typed_memory
        calls = 0

        def observed_renderer(
            rendered_items: list[MemoryItem],
            selected_ids: list[str],
        ) -> str:
            nonlocal calls
            calls += 1
            return original_renderer(rendered_items, selected_ids)

        with patch.object(
            compiler_module,
            "render_typed_memory",
            observed_renderer,
        ):
            replaced = ContextCompiler(policy=policy)
            self.assertFalse(replaced._can_count_default_selection_by_length(items))
            replaced_selected, _used = _select(replaced, items, source)
        self.assertEqual(replaced_selected, [item.id for item in items])
        self.assertEqual(calls, 3)

        prompt_calls = 0
        original_prompt_renderer = models_module.render_prompt_item

        def observed_prompt_renderer(item: MemoryItem) -> str:
            nonlocal prompt_calls
            prompt_calls += 1
            return original_prompt_renderer(item)

        with patch.object(
            models_module,
            "render_prompt_item",
            observed_prompt_renderer,
        ):
            replaced_dependency = ContextCompiler(policy=policy)
            self.assertFalse(replaced_dependency._can_count_default_selection_by_length(items))
            dependency_selected, _used = _select(
                replaced_dependency,
                items,
                source,
            )
        self.assertEqual(dependency_selected, [item.id for item in items])
        self.assertEqual(prompt_calls, 3)

    def test_bounded_dense_compile_preserves_semantic_outputs(self) -> None:
        templates = (
            "constraint: Preserve marker {index} exactly",
            "goal: Complete deterministic batch {index}",
            "unresolved: Whether worker {index} needs retry",
            "Observed failure: worker {index} returned timeout code {code}",
            "tool output line {index}: " + ("noise " * 10),
        )
        sources = [
            SourceRecord.create(
                id=f"dense-{index:04d}",
                sequence=index,
                role="user" if index % 5 < 4 else "tool",
                content=templates[index % len(templates)].format(
                    index=index,
                    code=500 + (index % 10),
                ),
            )
            for index in range(32)
        ]
        policy = CompilationPolicy(
            token_budget=200_000,
            minimum_compression_ratio=1.0,
        )

        fast = ContextCompiler(policy=policy).compile(sources)
        legacy = _LegacyContextCompiler(policy=policy).compile(sources)

        self.assertEqual(fast.source_digest, legacy.source_digest)
        self.assertEqual(
            [item.to_dict() for item in fast.items],
            [item.to_dict() for item in legacy.items],
        )
        self.assertEqual(fast.selected_item_ids, legacy.selected_item_ids)
        self.assertEqual(fast.verification.to_dict(), legacy.verification.to_dict())
        self.assertEqual(fast.compression.to_dict(), legacy.compression.to_dict())
        self.assertEqual(fast.to_prompt(), legacy.to_prompt())


if __name__ == "__main__":
    unittest.main()
