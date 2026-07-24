from __future__ import annotations

import json
import math
import unittest
from copy import deepcopy

from context_compiler import ModelExtractor, SourceRecord


class ModelResponseValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = SourceRecord.create(
            id="source-0",
            sequence=0,
            role="user",
            content="Do not change the public API.",
        )

    def candidate(self) -> dict[str, object]:
        return {
            "kind": "constraint",
            "text": self.source.content,
            "provenance": [
                {
                    "source_id": self.source.id,
                    "start": 0,
                    "end": len(self.source.content),
                }
            ],
        }

    def extract(self, response: object):
        return ModelExtractor(lambda _: response).extract([self.source])  # type: ignore[arg-type]

    def test_envelope_requires_exact_schema_keys(self) -> None:
        invalid_envelopes = [
            {},
            {"items": [], "extra": True},
            {"items": {}},
            [],
            None,
        ]

        for response in invalid_envelopes:
            with self.subTest(response=response):
                result = self.extract(response)
                self.assertEqual(result.items, [])
                self.assertEqual(result.rejected, [{"reason": "invalid_envelope"}])

    def test_candidate_requires_known_keys_and_all_required_keys(self) -> None:
        missing_kind = self.candidate()
        del missing_kind["kind"]
        missing_text = self.candidate()
        del missing_text["text"]
        missing_provenance = self.candidate()
        del missing_provenance["provenance"]
        extra_key = self.candidate()
        extra_key["explanation"] = "trust me"

        result = self.extract(
            {"items": [missing_kind, missing_text, missing_provenance, extra_key]}
        )

        self.assertEqual(result.items, [])
        self.assertEqual(len(result.rejected), 4)
        self.assertTrue(
            all(entry["reason"] == "invalid_candidate" for entry in result.rejected)
        )

    def test_provenance_requires_exact_keys_and_strict_offsets(self) -> None:
        invalid_candidates: list[dict[str, object]] = []
        for mutation in (
            {"source_id": self.source.id, "start": 0},
            {
                "source_id": self.source.id,
                "start": 0,
                "end": len(self.source.content),
                "quote": self.source.content,
            },
            {"source_id": "", "start": 0, "end": len(self.source.content)},
            {
                "source_id": self.source.id,
                "start": True,
                "end": len(self.source.content),
            },
            {
                "source_id": self.source.id,
                "start": -1,
                "end": len(self.source.content),
            },
        ):
            candidate = self.candidate()
            candidate["provenance"] = [mutation]
            invalid_candidates.append(candidate)

        result = self.extract({"items": invalid_candidates})

        self.assertEqual(result.items, [])
        self.assertEqual(len(result.rejected), len(invalid_candidates))
        self.assertTrue(
            all(entry["reason"] == "invalid_candidate" for entry in result.rejected)
        )

    def test_priority_is_a_bounded_non_boolean_integer_without_coercion(self) -> None:
        invalid_values = [True, False, 50.0, "50", -1, 101, math.inf]
        invalid_candidates = []
        for value in invalid_values:
            candidate = self.candidate()
            candidate["priority"] = value
            invalid_candidates.append(candidate)

        valid = self.candidate()
        valid["priority"] = 42
        result = self.extract({"items": [*invalid_candidates, valid]})

        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].priority, 42)
        self.assertIs(type(result.items[0].priority), int)
        self.assertEqual(len(result.rejected), len(invalid_candidates))

    def test_confidence_is_a_bounded_finite_non_boolean_number(self) -> None:
        invalid_values = [
            True,
            False,
            "0.5",
            -0.01,
            1.01,
            math.nan,
            math.inf,
            -math.inf,
            10**10000,
        ]
        invalid_candidates = []
        for value in invalid_values:
            candidate = self.candidate()
            candidate["confidence"] = value
            invalid_candidates.append(candidate)

        valid_integer = self.candidate()
        valid_integer["confidence"] = 1
        valid_float = self.candidate()
        valid_float["confidence"] = 0.25
        result = self.extract(
            {"items": [*invalid_candidates, valid_integer, valid_float]}
        )

        self.assertEqual([item.confidence for item in result.items], [1, 0.25])
        self.assertEqual(len(result.rejected), len(invalid_candidates))
        self.assertTrue(
            all(entry["reason"] == "invalid_candidate" for entry in result.rejected)
        )

    def test_non_finite_json_number_fails_closed_instead_of_raising(self) -> None:
        candidate = self.candidate()
        candidate["priority"] = 50
        raw = json.dumps({"items": [candidate]}).replace(
            '"priority": 50', '"priority": 1e309'
        )

        result = ModelExtractor(lambda _: raw).extract([self.source])

        self.assertEqual(result.items, [])
        self.assertEqual(len(result.rejected), 1)
        self.assertEqual(result.rejected[0]["reason"], "invalid_candidate")

    def test_valid_optional_fields_are_preserved(self) -> None:
        candidate = self.candidate()
        candidate.update(
            {
                "priority": 87,
                "confidence": 0.75,
                "exact": False,
                "tags": ["provider-tag"],
            }
        )

        result = self.extract({"items": [deepcopy(candidate)]})

        self.assertEqual(result.rejected, [])
        self.assertEqual(len(result.items), 1)
        item = result.items[0]
        self.assertEqual(item.priority, 87)
        self.assertEqual(item.confidence, 0.75)
        self.assertFalse(item.exact)
        self.assertEqual(item.tags, ["provider-tag"])


if __name__ == "__main__":
    unittest.main()
