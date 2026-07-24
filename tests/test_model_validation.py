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

    def test_raw_json_rejects_duplicate_keys_at_every_object_depth(self) -> None:
        candidate_json = json.dumps(self.candidate())
        duplicate_candidate_key = candidate_json.replace(
            '"kind": "constraint"',
            '"kind": "goal", "kind": "constraint"',
            1,
        )
        duplicate_provenance_key = candidate_json.replace(
            '"source_id": "source-0"',
            '"source_id": "forged", "source_id": "source-0"',
            1,
        )
        responses = (
            '{"items": [], "items": []}',
            '{"items": [' + duplicate_candidate_key + "]}",
            '{"items": [' + duplicate_provenance_key + "]}",
        )

        for response in responses:
            with self.subTest(response=response):
                result = ModelExtractor(lambda _, value=response: value).extract([self.source])
                self.assertEqual(result.items, [])
                self.assertEqual(result.rejected[0]["reason"], "invalid_json")
                self.assertEqual(result.metadata["failure_reason"], "invalid_json")

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
        self.assertTrue(all(entry["reason"] == "invalid_candidate" for entry in result.rejected))

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
        self.assertTrue(all(entry["reason"] == "invalid_candidate" for entry in result.rejected))

    def test_invalid_span_shapes_and_paraphrases_are_rejected(self) -> None:
        invalid_candidates: list[dict[str, object]] = []
        for mutation in (
            {
                "source_id": self.source.id,
                "start": len(self.source.content),
                "end": 0,
            },
            {
                "source_id": self.source.id,
                "start": 0,
                "end": len(self.source.content) + 1,
            },
            {
                "source_id": "unknown-source",
                "start": 0,
                "end": len(self.source.content),
            },
        ):
            candidate = self.candidate()
            candidate["provenance"] = [mutation]
            invalid_candidates.append(candidate)
        paraphrase = self.candidate()
        paraphrase["text"] = "Keep the API stable."
        invalid_candidates.append(paraphrase)

        result = self.extract({"items": invalid_candidates})

        self.assertEqual(result.items, [])
        self.assertEqual(len(result.rejected), len(invalid_candidates))
        self.assertTrue(all(entry["reason"] == "invalid_candidate" for entry in result.rejected))

    def test_role_forgery_and_reserved_internal_tags_are_rejected(self) -> None:
        role_forgery = self.candidate()
        forged_provenance = deepcopy(role_forgery["provenance"])
        forged_provenance[0]["role"] = "user"
        role_forgery["provenance"] = forged_provenance
        reserved_tag = self.candidate()
        reserved_tag["tags"] = ["verifier-recovered"]

        result = self.extract({"items": [role_forgery, reserved_tag]})

        self.assertEqual(result.items, [])
        self.assertEqual(len(result.rejected), 2)
        self.assertIn("unknown keys: role", result.rejected[0]["detail"])
        self.assertIn("reserved internal tags", result.rejected[1]["detail"])

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
        result = self.extract({"items": [*invalid_candidates, valid_integer, valid_float]})

        self.assertEqual([item.confidence for item in result.items], [1, 0.25])
        self.assertEqual(len(result.rejected), len(invalid_candidates))
        self.assertTrue(all(entry["reason"] == "invalid_candidate" for entry in result.rejected))

    def test_non_finite_json_numbers_fail_as_invalid_json(self) -> None:
        candidate = self.candidate()
        candidate["confidence"] = 0.5
        template = json.dumps({"items": [candidate]})

        for literal in ("NaN", "Infinity", "-Infinity", "1e309"):
            with self.subTest(literal=literal):
                raw = template.replace("0.5", literal, 1)
                result = ModelExtractor(lambda _, value=raw: value).extract([self.source])
                self.assertEqual(result.items, [])
                self.assertEqual(result.rejected[0]["reason"], "invalid_json")
                self.assertEqual(result.metadata["failure_reason"], "invalid_json")

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

    def test_response_and_candidate_limits_are_strictly_validated(self) -> None:
        for name, kwargs in (
            ("max_response_chars", {"max_response_chars": 0}),
            ("max_candidates", {"max_candidates": 0}),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "positive"):
                ModelExtractor(lambda _: {"items": []}, **kwargs)
        with self.assertRaisesRegex(TypeError, "integer"):
            ModelExtractor(lambda _: {"items": []}, max_response_chars=True)

    def test_oversized_string_and_decoded_responses_fail_closed(self) -> None:
        for response in ('{"items":[]}', {"items": []}):
            with self.subTest(response_type=type(response).__name__):
                result = ModelExtractor(
                    lambda _, value=response: value,  # type: ignore[arg-type,return-value]
                    max_response_chars=5,
                ).extract([self.source])
                self.assertEqual(result.items, [])
                self.assertEqual(result.rejected, [{"reason": "response_too_large"}])
                self.assertTrue(result.metadata["degraded"])

    def test_candidate_count_is_bounded_before_candidate_decoding(self) -> None:
        result = ModelExtractor(
            lambda _: {"items": [self.candidate(), self.candidate()]},
            max_candidates=1,
        ).extract([self.source])

        self.assertEqual(result.items, [])
        self.assertEqual(result.rejected, [{"reason": "too_many_candidates"}])
        self.assertEqual(result.metadata["failure_reason"], "too_many_candidates")

    def test_cyclic_decoded_response_is_rejected_without_unbounded_traversal(self) -> None:
        response: dict = {"items": []}
        response["cycle"] = response

        result = ModelExtractor(lambda _: response).extract([self.source])

        self.assertEqual(result.items, [])
        self.assertEqual(result.rejected, [{"reason": "response_too_large"}])

    def test_json_integer_decoder_limit_becomes_degraded_output(self) -> None:
        raw = '{"items":[' + ("9" * 5_000) + "]}"

        result = ModelExtractor(
            lambda _: raw,
            max_response_chars=10_000,
        ).extract([self.source])

        self.assertEqual(result.items, [])
        self.assertEqual(result.rejected[0]["reason"], "invalid_json")
        self.assertEqual(result.metadata["failure_reason"], "invalid_json")


if __name__ == "__main__":
    unittest.main()
