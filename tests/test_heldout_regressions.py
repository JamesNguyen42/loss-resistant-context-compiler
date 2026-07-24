from __future__ import annotations

import json
import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from context_compiler import (
    CompilationPolicy,
    ContextCompiler,
    MemoryKind,
    MemoryStatus,
    SourceArchive,
    SourceRecord,
)
from context_compiler.io import load_sources_path


def source(
    sequence: int,
    content: str,
    *,
    role: str = "user",
    metadata: dict[str, object] | None = None,
) -> SourceRecord:
    return SourceRecord.create(
        id=f"heldout-{sequence}",
        sequence=sequence,
        role=role,
        content=content,
        metadata=metadata,
    )


def items_of_kind(result: object, kind: MemoryKind) -> list[object]:
    return [item for item in result.items if item.kind == kind]


class HeldoutClauseAndTemporalTests(unittest.TestCase):
    def test_python_correction_preserves_unrelated_api_constraint_in_four_forms(
        self,
    ) -> None:
        histories = {
            "semicolon": (
                "constraint: Do not change the public API; "
                "Python 3.11 compatibility is required."
            ),
            "conjunction": (
                "constraint: Do not change the public API and "
                "Python 3.11 compatibility is required."
            ),
            "sentences": (
                "constraint: Do not change the public API. "
                "Python 3.11 compatibility is required."
            ),
            "section_bullets": (
                "constraints:\n"
                "- Do not change the public API\n"
                "- Python 3.11 compatibility is required."
            ),
        }
        correction_text = (
            "Actually, Python 3.12 compatibility is required "
            "instead of Python 3.11."
        )

        for form, original_text in histories.items():
            with self.subTest(form=form):
                result = ContextCompiler().compile(
                    [source(0, original_text), source(1, correction_text)]
                )
                constraints = items_of_kind(result, MemoryKind.CONSTRAINT)
                api = [
                    item
                    for item in constraints
                    if "public api" in item.text.casefold()
                ]
                old_python = [
                    item
                    for item in constraints
                    if "python 3.11" in item.text.casefold()
                    and item.source_sequence == 0
                ]
                current_python = [
                    item
                    for item in constraints
                    if "python 3.12" in item.text.casefold()
                    and item.status == MemoryStatus.ACTIVE
                ]

                self.assertEqual(len(api), 1)
                self.assertEqual(api[0].status, MemoryStatus.ACTIVE)
                self.assertIn(api[0].id, result.selected_item_ids)
                self.assertEqual(len(old_python), 1)
                self.assertEqual(old_python[0].status, MemoryStatus.SUPERSEDED)
                self.assertNotIn(old_python[0].id, result.selected_item_ids)
                self.assertEqual(len(current_python), 1)
                self.assertIn("current-value", current_python[0].tags)
                self.assertIn(old_python[0].id, current_python[0].supersedes)
                self.assertTrue(result.verification.passed)

    def test_labeled_database_correction_supersedes_mysql(self) -> None:
        original = source(0, "constraint: Database must use MySQL.")
        correction = source(
            1,
            "Correction: Database must use PostgreSQL instead of MySQL.",
        )

        result = ContextCompiler().compile([original, correction])
        constraints = items_of_kind(result, MemoryKind.CONSTRAINT)
        corrections = items_of_kind(result, MemoryKind.USER_CORRECTION)
        mysql = [
            item
            for item in constraints
            if item.source_sequence == 0 and "mysql" in item.text.casefold()
        ]
        postgres = [
            item
            for item in constraints
            if item.status == MemoryStatus.ACTIVE
            and "postgresql" in item.text.casefold()
        ]

        self.assertEqual(len(mysql), 1)
        self.assertEqual(mysql[0].status, MemoryStatus.SUPERSEDED)
        self.assertNotIn(mysql[0].id, result.selected_item_ids)
        self.assertEqual(len(postgres), 1)
        self.assertIn("current-value", postgres[0].tags)
        self.assertIn(mysql[0].id, postgres[0].supersedes)
        self.assertEqual(len(corrections), 1)
        self.assertIn("explicit-correction-label", corrections[0].tags)
        self.assertIn(mysql[0].id, corrections[0].supersedes)
        self.assertFalse(
            any(item.status == MemoryStatus.CONFLICTING for item in constraints)
        )
        self.assertTrue(result.verification.passed)

    def test_explicit_revocations_retire_without_inventing_replacement_state(
        self,
    ) -> None:
        revocations = (
            "Correction: Ignore my earlier Python 3.11 compatibility requirement.",
            "Correction: Python 3.11 compatibility is no longer required.",
            (
                "Correction: Instead, the Python 3.11 compatibility "
                "requirement is revoked."
            ),
        )

        for revocation in revocations:
            with self.subTest(revocation=revocation):
                result = ContextCompiler().compile(
                    [
                        source(
                            0,
                            "constraint: Python 3.11 compatibility is required.",
                        ),
                        source(1, revocation),
                    ]
                )
                constraints = items_of_kind(result, MemoryKind.CONSTRAINT)
                corrections = items_of_kind(result, MemoryKind.USER_CORRECTION)

                self.assertEqual(len(constraints), 1)
                self.assertEqual(
                    constraints[0].status,
                    MemoryStatus.SUPERSEDED,
                )
                self.assertNotIn(constraints[0].id, result.selected_item_ids)
                self.assertEqual(len(corrections), 1)
                self.assertIn(constraints[0].id, corrections[0].supersedes)
                self.assertEqual(
                    [
                        item
                        for item in constraints
                        if item.status == MemoryStatus.ACTIVE
                    ],
                    [],
                )
                self.assertFalse(
                    any(
                        "current-value" in item.tags
                        for item in result.items
                        if item.source_sequence == 1
                    )
                )
                self.assertTrue(result.verification.passed)

    def test_opposites_in_distinct_qualifier_scopes_are_not_conflicts(self) -> None:
        scoped_pairs = (
            (
                "constraint: Enable tracing in development.",
                "constraint: Disable tracing in production.",
            ),
            (
                "constraint: Use SQLite as the database in development.",
                "constraint: Use PostgreSQL as the database in production.",
            ),
            (
                "constraint: Include debug symbols in development.",
                "constraint: Exclude debug symbols in production.",
            ),
            (
                "constraint: Enable verbose logging in staging.",
                "constraint: Disable verbose logging in testing.",
            ),
        )

        for left, right in scoped_pairs:
            with self.subTest(left=left, right=right):
                result = ContextCompiler().compile(
                    [source(0, left), source(1, right)]
                )
                constraints = items_of_kind(result, MemoryKind.CONSTRAINT)

                self.assertEqual(len(constraints), 2)
                self.assertTrue(
                    all(item.status == MemoryStatus.ACTIVE for item in constraints)
                )
                self.assertTrue(
                    all(not item.conflicts_with for item in constraints)
                )
                self.assertFalse(
                    any(
                        item.kind == MemoryKind.UNRESOLVED
                        and "detected-conflict" in item.tags
                        for item in result.items
                    )
                )
                self.assertTrue(result.verification.passed)


class HeldoutLanguageCoverageTests(unittest.TestCase):
    def test_common_user_phrasings_become_selected_protected_commitments(
        self,
    ) -> None:
        examples = (
            ("Please preserve the public API.", MemoryKind.CONSTRAINT),
            ("Leave the authentication endpoint unchanged.", MemoryKind.CONSTRAINT),
            ("The SDK needs to stay unchanged.", MemoryKind.CONSTRAINT),
            ("No modifications to the wire protocol.", MemoryKind.CONSTRAINT),
            (
                "Under no circumstances change the response schema.",
                MemoryKind.CONSTRAINT,
            ),
            ("Keep the command-line interface intact.", MemoryKind.CONSTRAINT),
            ("Could you repair the authentication timeout?", MemoryKind.GOAL),
            ("I need you to implement refresh-token recovery.", MemoryKind.GOAL),
        )

        for text, expected_kind in examples:
            with self.subTest(text=text):
                result = ContextCompiler().compile([source(0, text)])
                matching = [
                    item for item in result.items if item.kind == expected_kind
                ]

                self.assertEqual(len(matching), 1)
                self.assertTrue(matching[0].protected)
                self.assertEqual(matching[0].status, MemoryStatus.ACTIVE)
                self.assertIn(matching[0].id, result.selected_item_ids)
                self.assertEqual(result.verification.protected_recall, 1.0)
                self.assertTrue(result.verification.passed)

    def test_uncertainty_variants_remain_open_and_never_become_facts(self) -> None:
        uncertain_statements = (
            "Clock skew has not been ruled out.",
            "Clock skew has not yet been ruled out.",
            "Clock skew remains unconfirmed.",
            "Clock skew is still being investigated.",
            "Clock skew cannot be confirmed.",
            "Whether clock skew causes expiration remains unknown.",
            "Clock skew is probably contributing to expiration.",
        )

        for text in uncertain_statements:
            with self.subTest(text=text):
                result = ContextCompiler().compile([source(0, text)])
                unresolved = items_of_kind(result, MemoryKind.UNRESOLVED)

                self.assertEqual(len(unresolved), 1)
                self.assertEqual(unresolved[0].text, text)
                self.assertTrue(unresolved[0].protected)
                self.assertIn(unresolved[0].id, result.selected_item_ids)
                self.assertFalse(
                    any(
                        item.kind == MemoryKind.CONFIRMED_FACT
                        for item in result.items
                    )
                )
                self.assertTrue(result.verification.passed)


class HeldoutExactLiteralTests(unittest.TestCase):
    def test_diagnostic_variants_are_preserved_as_complete_exact_literals(
        self,
    ) -> None:
        diagnostics = (
            "E   AssertionError: expected 17, got 16",
            "Expected: status 200; Received: status 401",
            "TS2322: Type 'string' is not assignable to type 'number'",
            "error[E0308]: mismatched types: expected u64, found i64",
            "Null reference: refresh token was missing",
            "exit code 137 after refresh-token worker stopped",
            "3 tests failed in 2.14s",
            (
                "FAILED tests/test_token_refresh.py::test_clock_skew - "
                "AssertionError: expected 200, got 401"
            ),
        )

        for diagnostic in diagnostics:
            with self.subTest(diagnostic=diagnostic):
                result = ContextCompiler().compile(
                    [source(0, diagnostic, role="tool")]
                )
                errors = items_of_kind(result, MemoryKind.EXACT_ERROR)

                self.assertEqual([item.text for item in errors], [diagnostic])
                self.assertTrue(errors[0].exact)
                self.assertEqual(errors[0].provenance[0].quote, diagnostic)
                self.assertIn(errors[0].id, result.selected_item_ids)
                self.assertTrue(result.verification.passed)

    def test_reference_variants_keep_the_full_path_and_locator(self) -> None:
        references = (
            (
                "Inspect src/auth/token.py lines 118-164 before editing.",
                "src/auth/token.py lines 118-164",
            ),
            (
                "Inspect src/auth/token.py#L118-L164 before editing.",
                "src/auth/token.py#L118-L164",
            ),
            (
                r"C:\Repo With Space\src\auth token.py:118-164.",
                r"C:\Repo With Space\src\auth token.py:118-164",
            ),
            (
                "Run tests/test_token_refresh.py::"
                "TestRefresh::test_clock_skew[param-1].",
                "tests/test_token_refresh.py::"
                "TestRefresh::test_clock_skew[param-1]",
            ),
            (
                "Review /workspace/src/auth/token.py:118-164 before editing.",
                "/workspace/src/auth/token.py:118-164",
            ),
        )

        for text, expected in references:
            with self.subTest(expected=expected):
                result = ContextCompiler().compile(
                    [source(0, text, role="assistant")]
                )
                exact_references = items_of_kind(
                    result,
                    MemoryKind.EXACT_REFERENCE,
                )
                matches = [
                    item for item in exact_references if item.text == expected
                ]

                self.assertEqual(len(matches), 1)
                self.assertTrue(matches[0].exact)
                self.assertEqual(matches[0].provenance[0].quote, expected)
                self.assertIn(matches[0].id, result.selected_item_ids)
                self.assertTrue(result.verification.passed)


class HeldoutInputAndIntegrityTests(unittest.TestCase):
    def test_utf8_bom_json_and_jsonl_load_without_polluting_content(self) -> None:
        json_payload = {
            "sources": [
                {
                    "role": "user",
                    "content": "goal: repair authentication timeout",
                }
            ]
        }
        jsonl_payload = (
            json.dumps(
                {
                    "role": "user",
                    "content": "goal: repair authentication timeout",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "role": "user",
                    "content": "constraint: preserve the public API",
                }
            )
            + "\n"
        )

        with TemporaryDirectory() as directory:
            json_path = Path(directory) / "history.json"
            jsonl_path = Path(directory) / "history.jsonl"
            json_path.write_text(
                json.dumps(json_payload),
                encoding="utf-8-sig",
            )
            jsonl_path.write_text(jsonl_payload, encoding="utf-8-sig")

            from_json = load_sources_path(json_path)
            from_jsonl = load_sources_path(jsonl_path)

        self.assertEqual(
            [record.content for record in from_json],
            ["goal: repair authentication timeout"],
        )
        self.assertEqual(
            [record.content for record in from_jsonl],
            [
                "goal: repair authentication timeout",
                "constraint: preserve the public API",
            ],
        )
        self.assertFalse(
            any(record.content.startswith("\ufeff") for record in from_json + from_jsonl)
        )

    def test_source_metadata_is_deeply_detached_and_immutable(self) -> None:
        supplied_metadata = {
            "nested": {
                "trusted": True,
                "labels": ["auth", {"priority": 7}],
            }
        }
        record = source(0, "goal: repair auth", metadata=supplied_metadata)

        supplied_metadata["nested"]["trusted"] = False
        supplied_metadata["nested"]["labels"].append("mutated")

        self.assertTrue(record.metadata["nested"]["trusted"])
        self.assertEqual(
            record.metadata["nested"]["labels"],
            ["auth", {"priority": 7}],
        )
        with self.assertRaisesRegex(TypeError, "immutable"):
            record.metadata["nested"]["trusted"] = False
        with self.assertRaisesRegex(TypeError, "immutable"):
            record.metadata["nested"]["labels"].append("mutated")
        with self.assertRaisesRegex(TypeError, "immutable"):
            record.metadata["nested"]["labels"][1]["priority"] = 0
        record.ensure_integrity()

    def test_archive_preflights_every_record_before_touching_storage(self) -> None:
        valid = source(0, "goal: repair auth")
        forged = source(
            1,
            "constraint: preserve the public API",
            metadata={"nested": {"trusted": True}},
        )
        dict.__setitem__(forged.metadata["nested"], "trusted", False)

        with TemporaryDirectory() as directory:
            archive_directory = Path(directory) / "cold-archive"
            archive = SourceArchive(archive_directory)

            with self.assertRaisesRegex(ValueError, "record hash mismatch"):
                archive.append([valid, forged])

            self.assertFalse(archive_directory.exists())
            self.assertFalse(archive.events_path.exists())
            self.assertFalse(archive.lock_path.exists())


class HeldoutSelectorBoundaryTests(unittest.TestCase):
    def test_selector_accepts_exact_fit_when_ratio_target_is_impossible(self) -> None:
        sources = [
            source(0, "constraint: Keep the API stable."),
            source(1, "confirmed_fact: Refresh passed.", role="assistant"),
            source(2, "confirmed_fact: Cache passed.", role="assistant"),
            source(3, "confirmed_fact: Worker passed.", role="assistant"),
        ]
        generous = CompilationPolicy(
            token_budget=100_000,
            minimum_compression_ratio=5.0,
        )
        baseline = ContextCompiler(
            policy=generous,
            token_counter=len,
        ).compile(sources)
        exact_budget = len(baseline.to_prompt())
        source_tokens = len("\n".join(record.content for record in sources))
        target_budget = math.floor(
            source_tokens / generous.minimum_compression_ratio
        )
        protected_only = ContextCompiler(
            policy=generous,
            token_counter=len,
        ).compile([sources[0]])

        self.assertLess(target_budget, len(protected_only.to_prompt()))
        self.assertEqual(len(baseline.selected_item_ids), 4)

        exact = ContextCompiler(
            policy=CompilationPolicy(
                token_budget=exact_budget,
                minimum_compression_ratio=5.0,
            ),
            token_counter=len,
        ).compile(sources)

        self.assertEqual(exact.selected_item_ids, baseline.selected_item_ids)
        self.assertEqual(exact.compression.active_tokens_estimate, exact_budget)
        self.assertEqual(exact.compression.budget_overflow, 0)
        self.assertFalse(exact.compression.target_met)
        self.assertTrue(exact.verification.passed)

        one_character_short = ContextCompiler(
            policy=CompilationPolicy(
                token_budget=exact_budget - 1,
                minimum_compression_ratio=5.0,
            ),
            token_counter=len,
        ).compile(sources)

        self.assertLess(
            len(one_character_short.selected_item_ids),
            len(exact.selected_item_ids),
        )
        self.assertLessEqual(
            one_character_short.compression.active_tokens_estimate,
            exact_budget - 1,
        )
        self.assertTrue(one_character_short.verification.passed)


if __name__ == "__main__":
    unittest.main()
