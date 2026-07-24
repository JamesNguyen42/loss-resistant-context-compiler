from __future__ import annotations

import hashlib
import json
import unittest
from collections.abc import Callable
from typing import Any

from context_compiler.compiler import ContextCompiler
from context_compiler.io import verify_artifact_dict
from context_compiler.models import SourceRecord


def make_artifact() -> tuple[SourceRecord, dict[str, Any]]:
    source = SourceRecord.create(
        id="source-0",
        sequence=0,
        role="user",
        content="constraint: Do not change the public API",
    )
    return source, ContextCompiler().compile([source]).to_dict()


def resign(artifact: dict[str, Any]) -> None:
    unsigned = {
        key: value for key, value in artifact.items() if key != "artifact_sha256"
    }
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    artifact["artifact_sha256"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


class ArtifactSchemaTests(unittest.TestCase):
    def assert_rejected(
        self,
        mutation: Callable[[dict[str, Any]], None],
        expected_code: str,
    ) -> dict[str, Any]:
        source, artifact = make_artifact()
        mutation(artifact)
        resign(artifact)

        report = verify_artifact_dict(artifact, [source])

        self.assertFalse(report["passed"])
        self.assertIn(expected_code, {issue["code"] for issue in report["issues"]})
        return report

    def test_canonical_compiler_artifact_passes_strict_shape_validation(self) -> None:
        source, artifact = make_artifact()

        report = verify_artifact_dict(artifact, [source])

        self.assertTrue(report["passed"])

    def test_top_level_requires_exact_fields_and_nonempty_compiled_at(self) -> None:
        cases: tuple[tuple[str, Callable[[dict[str, Any]], None], str], ...] = (
            (
                "unknown field",
                lambda artifact: artifact.__setitem__("unexpected", True),
                "invalid_artifact_shape",
            ),
            (
                "missing field",
                lambda artifact: artifact.pop("compiled_at"),
                "invalid_artifact_shape",
            ),
            (
                "empty compiled_at",
                lambda artifact: artifact.__setitem__("compiled_at", "  "),
                "invalid_compiled_at",
            ),
            (
                "non-object metadata",
                lambda artifact: artifact.__setitem__("compiler_metadata", []),
                "invalid_compiler_metadata",
            ),
        )
        for name, mutation, expected_code in cases:
            with self.subTest(name=name):
                self.assert_rejected(mutation, expected_code)

    def test_unknown_nested_fields_are_rejected_at_every_schema_boundary(self) -> None:
        def add_item_field(artifact: dict[str, Any]) -> None:
            artifact["items"][0]["unexpected"] = True

        def add_provenance_field(artifact: dict[str, Any]) -> None:
            artifact["items"][0]["provenance"][0]["unexpected"] = True

        def add_verification_field(artifact: dict[str, Any]) -> None:
            artifact["verification"]["unexpected"] = True

        def add_verification_issue_field(artifact: dict[str, Any]) -> None:
            artifact["verification"]["issues"].append(
                {
                    "code": "synthetic",
                    "severity": "warning",
                    "message": "synthetic",
                    "item_id": None,
                    "source_id": None,
                    "unexpected": True,
                }
            )

        def add_compression_field(artifact: dict[str, Any]) -> None:
            artifact["compression"]["unexpected"] = True

        def add_policy_field(artifact: dict[str, Any]) -> None:
            artifact["compiler_metadata"]["policy"]["unexpected"] = True

        def add_metrics_field(artifact: dict[str, Any]) -> None:
            artifact["compiler_metadata"]["metrics"]["unexpected"] = True

        cases = (
            (add_item_field, "invalid_item_shape"),
            (add_provenance_field, "invalid_provenance_shape"),
            (add_verification_field, "invalid_verification_shape"),
            (add_verification_issue_field, "invalid_verification_issue_shape"),
            (add_compression_field, "invalid_compression_shape"),
            (add_policy_field, "invalid_policy_shape"),
            (add_metrics_field, "invalid_compilation_metrics"),
        )
        for mutation, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                report = self.assert_rejected(mutation, expected_code)
                matching = [
                    issue
                    for issue in report["issues"]
                    if issue["code"] == expected_code
                ]
                self.assertTrue(
                    any("unknown fields" in issue["message"] for issue in matching)
                )

    def test_missing_nested_fields_are_rejected_even_when_models_have_defaults(self) -> None:
        cases: tuple[tuple[Callable[[dict[str, Any]], None], str], ...] = (
            (
                lambda artifact: artifact["items"][0].pop("status"),
                "invalid_item_shape",
            ),
            (
                lambda artifact: artifact["items"][0]["provenance"][0].pop(
                    "quote_sha256"
                ),
                "invalid_provenance_shape",
            ),
            (
                lambda artifact: artifact["verification"].pop("recovered_items"),
                "invalid_verification_shape",
            ),
            (
                lambda artifact: artifact["compression"].pop("target_met"),
                "invalid_compression_shape",
            ),
            (
                lambda artifact: artifact["compiler_metadata"]["policy"].pop(
                    "include_discarded"
                ),
                "invalid_policy_shape",
            ),
            (
                lambda artifact: artifact["compiler_metadata"]["metrics"].pop(
                    "resolved_items"
                ),
                "invalid_compilation_metrics",
            ),
        )
        for mutation, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self.assert_rejected(mutation, expected_code)

    def test_nonfinite_numbers_and_boolean_integers_fail_without_raising(self) -> None:
        cases: tuple[tuple[Callable[[dict[str, Any]], None], str], ...] = (
            (
                lambda artifact: artifact["items"][0].__setitem__(
                    "confidence", float("nan")
                ),
                "invalid_item_shape",
            ),
            (
                lambda artifact: artifact["verification"].__setitem__(
                    "protected_recall", float("inf")
                ),
                "invalid_verification_shape",
            ),
            (
                lambda artifact: artifact["compression"].__setitem__(
                    "compression_ratio", float("-inf")
                ),
                "invalid_compression_shape",
            ),
            (
                lambda artifact: artifact["compiler_metadata"]["policy"].__setitem__(
                    "minimum_compression_ratio", 10**400
                ),
                "invalid_policy_shape",
            ),
            (
                lambda artifact: artifact["compression"].__setitem__(
                    "token_budget", True
                ),
                "invalid_compression_shape",
            ),
        )
        for mutation, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self.assert_rejected(mutation, expected_code)

    def test_non_object_artifact_returns_a_failure_report(self) -> None:
        source, _ = make_artifact()

        report = verify_artifact_dict(["not", "an", "object"], [source])

        self.assertFalse(report["passed"])
        self.assertIn(
            "invalid_artifact_shape",
            {issue["code"] for issue in report["issues"]},
        )


if __name__ == "__main__":
    unittest.main()
