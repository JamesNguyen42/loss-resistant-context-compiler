from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from context_compiler import (
    CompilationLimitError,
    CompilationLimits,
    CompilationPolicy,
    ContextCompiler,
    ExtractionResult,
    MemoryItem,
    MemoryKind,
    ProvenanceSpan,
    RuleBasedExtractor,
    SourceRecord,
)
from context_compiler.cli import main
from context_compiler.io import verify_artifact_dict
from context_compiler.limits import (
    DEFAULT_COMPILATION_LIMITS,
    _CompilationWorkBudget,
)
from context_compiler.verifier import verify_memory


def source(content: str) -> SourceRecord:
    return SourceRecord.create(
        id="source",
        sequence=0,
        role="user",
        content=content,
    )


def item(record: SourceRecord, index: int = 0) -> MemoryItem:
    return MemoryItem(
        id=f"custom-{index}",
        kind=MemoryKind.CONTEXT,
        text=record.content,
        provenance=[
            ProvenanceSpan.from_source(
                record,
                0,
                len(record.content),
            )
        ],
        metadata={
            "source_sequence": record.sequence,
            "source_role": record.role,
            "extractor": "static",
        },
    )


class StaticExtractor:
    name = "static"

    def __init__(self, result: ExtractionResult) -> None:
        self.result = result

    def extract(self, _sources: list[SourceRecord]) -> ExtractionResult:
        return self.result


def resign_artifact(artifact: dict[str, Any]) -> None:
    unsigned = {
        key: value
        for key, value in artifact.items()
        if key != "artifact_sha256"
    }
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    artifact["artifact_sha256"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize(
    ("field", "value", "exception"),
    [
        ("max_extractor_items", True, TypeError),
        ("max_extractor_rejections", 1.5, TypeError),
        ("max_extractor_bytes", 0, ValueError),
        ("max_extractor_auxiliary_bytes", -1, ValueError),
        ("max_total_candidate_items", "10", TypeError),
        ("max_resolved_items", False, TypeError),
        ("max_provenance_spans", 0, ValueError),
        ("max_item_work", 2.5, TypeError),
    ],
)
def test_compilation_limits_require_positive_non_boolean_integers(
    field: str,
    value: object,
    exception: type[Exception],
) -> None:
    with pytest.raises(exception):
        CompilationLimits(**{field: value})


def test_rule_extraction_stops_at_the_first_item_beyond_its_ceiling() -> None:
    record = source(
        "\n".join(
            (
                "constraint: keep alpha",
                "constraint: keep beta",
                "constraint: keep gamma",
            )
        )
    )

    with pytest.raises(
        CompilationLimitError,
        match="rules-v1 extraction exceeds 2 memory items",
    ):
        RuleBasedExtractor(max_items=2).extract([record])

    certification_source = source(
        "progress: first\nprogress: second\ngoal: keep the goal"
    )
    protected = RuleBasedExtractor(
        protected_only=True,
        max_items=1,
    ).extract([certification_source]).items
    assert len(protected) == 1
    assert protected[0].kind == MemoryKind.GOAL


def test_compiler_rejects_oversized_custom_extractor_results() -> None:
    record = source("ordinary context")
    result = ExtractionResult(
        items=[item(record, index) for index in range(3)]
    )
    limits = replace(
        DEFAULT_COMPILATION_LIMITS,
        max_extractor_items=2,
    )

    with pytest.raises(
        CompilationLimitError,
        match="primary extractor 'static' exceeds 2 memory items",
    ):
        ContextCompiler(
            extractor=StaticExtractor(result),
            compilation_limits=limits,
        ).compile([record])

    provenance_limits = replace(
        DEFAULT_COMPILATION_LIMITS,
        max_provenance_spans=1,
    )
    with pytest.raises(
        CompilationLimitError,
        match="primary extractor 'static' exceeds 1 provenance spans",
    ):
        ContextCompiler(
            extractor=StaticExtractor(
                ExtractionResult(
                    items=[item(record, 0), item(record, 1)]
                )
            ),
            compilation_limits=provenance_limits,
        ).compile([record])


def test_compiler_bounds_extractor_rejections_and_metadata_bytes() -> None:
    record = source("ordinary context")
    rejection_limits = replace(
        DEFAULT_COMPILATION_LIMITS,
        max_extractor_rejections=2,
    )
    auxiliary_limits = replace(
        DEFAULT_COMPILATION_LIMITS,
        max_extractor_auxiliary_bytes=256,
    )
    item_byte_limits = replace(
        DEFAULT_COMPILATION_LIMITS,
        max_extractor_bytes=512,
    )

    with pytest.raises(CompilationLimitError, match="exceeds 2 rejections"):
        ContextCompiler(
            extractor=StaticExtractor(
                ExtractionResult(rejected=[{}, {}, {}])
            ),
            compilation_limits=rejection_limits,
        ).compile([record])
    with pytest.raises(
        CompilationLimitError,
        match="auxiliary data exceeds 256 UTF-8 JSON bytes",
    ):
        ContextCompiler(
            extractor=StaticExtractor(
                ExtractionResult(metadata={"blob": "x" * 1_000})
            ),
            compilation_limits=auxiliary_limits,
        ).compile([record])
    oversized_item = item(record)
    oversized_item.metadata["blob"] = "x" * 1_000
    with pytest.raises(
        CompilationLimitError,
        match="primary extractor 'static' item 0 exceeds 512 UTF-8 JSON bytes",
    ):
        ContextCompiler(
            extractor=StaticExtractor(
                ExtractionResult(items=[oversized_item])
            ),
            compilation_limits=item_byte_limits,
        ).compile([record])


def test_combined_candidate_ceiling_accounts_for_all_builtin_passes() -> None:
    record = source("constraint: keep the public API")
    limits = replace(
        DEFAULT_COMPILATION_LIMITS,
        max_total_candidate_items=2,
    )

    with pytest.raises(
        CompilationLimitError,
        match="combined extractor candidates exceed 2 items",
    ):
        ContextCompiler(compilation_limits=limits).compile([record])


def test_conflict_resolution_stops_before_exceeding_resolved_item_ceiling() -> None:
    record = source(
        "constraint: uploads must be enabled\n"
        "constraint: uploads must be disabled"
    )
    limits = replace(
        DEFAULT_COMPILATION_LIMITS,
        max_resolved_items=2,
    )

    with pytest.raises(
        CompilationLimitError,
        match="conflict detection exceeds 2 resolved items",
    ):
        ContextCompiler(
            policy=CompilationPolicy(recover_missed_protected=False),
            compilation_limits=limits,
        ).compile([record])


def test_quadratic_conflict_search_consumes_a_bounded_work_budget() -> None:
    record = source(
        "constraint: alpha feature must be enabled\n"
        "constraint: beta feature must be enabled\n"
        "constraint: gamma feature must be enabled"
    )
    limits = replace(
        DEFAULT_COMPILATION_LIMITS,
        max_item_work=2,
    )

    with pytest.raises(
        CompilationLimitError,
        match=(
            "compilation item work exceeds 2 operations "
            "during conflict detection"
        ),
    ):
        ContextCompiler(
            policy=CompilationPolicy(recover_missed_protected=False),
            compilation_limits=limits,
        ).compile([record])


def test_independent_verifier_consumes_the_same_item_work_budget() -> None:
    record = source("constraint: keep verification bounded")
    extracted = RuleBasedExtractor().extract([record]).items
    candidate = extracted[0]

    with pytest.raises(
        CompilationLimitError,
        match=(
            "compilation item work exceeds 1 operations "
            "during protected retention verification"
        ),
    ):
        verify_memory(
            sources=[record],
            items=extracted,
            selected_item_ids=[candidate.id],
            protected_candidates=[candidate, candidate],
            recovered_items=0,
            work_budget=_CompilationWorkBudget(1),
        )


def test_compiler_records_limits_and_verifier_rejects_forged_limit_shape() -> None:
    record = source("goal: keep compilation bounded")
    limits = replace(
        DEFAULT_COMPILATION_LIMITS,
        max_item_work=123_456,
    )
    artifact = ContextCompiler(
        compilation_limits=limits
    ).compile([record]).to_dict()

    assert artifact["compiler_metadata"]["compilation_limits"] == (
        limits.to_dict()
    )

    artifact["compiler_metadata"]["compilation_limits"][
        "max_item_work"
    ] = True
    resign_artifact(artifact)
    report = verify_artifact_dict(artifact, [record])

    assert report["passed"] is False
    assert "invalid_compilation_limits" in {
        issue["code"] for issue in report["issues"]
    }


def test_verifier_rejects_recorded_ceiling_below_artifact_cardinality() -> None:
    record = source("goal: keep alpha\ngoal: keep beta")
    artifact = ContextCompiler().compile([record]).to_dict()
    assert len(artifact["items"]) == 2

    artifact["compiler_metadata"]["compilation_limits"][
        "max_resolved_items"
    ] = 1
    resign_artifact(artifact)
    report = verify_artifact_dict(artifact, [record])

    assert report["passed"] is False
    assert any(
        issue["code"] == "invalid_compilation_limits"
        and "max_resolved_items" in issue["message"]
        for issue in report["issues"]
    )


def test_compiled_schema_tracks_the_optional_limit_contract() -> None:
    schema = json.loads(
        (
            Path(__file__).parents[1]
            / "schemas"
            / "compiled-memory.schema.json"
        ).read_text(encoding="utf-8")
    )
    reference = schema["properties"]["compiler_metadata"]["properties"][
        "compilation_limits"
    ]
    limit_schema = schema["$defs"]["compilationLimits"]

    assert reference == {"$ref": "#/$defs/compilationLimits"}
    assert set(limit_schema["required"]) == set(
        DEFAULT_COMPILATION_LIMITS.to_dict()
    )
    assert set(limit_schema["properties"]) == set(
        DEFAULT_COMPILATION_LIMITS.to_dict()
    )
    assert all(
        field_schema == {"type": "integer", "minimum": 1}
        for field_schema in limit_schema["properties"].values()
    )
    assert limit_schema["additionalProperties"] is False


def test_cli_compilation_limit_override_is_a_resource_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    history = tmp_path / "history.json"
    history.write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "content": (
                        "constraint: keep alpha\n"
                        "constraint: keep beta"
                    ),
                }
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "compile",
            str(history),
            "--max-extractor-items",
            "1",
            "--error-format",
            "json",
        ]
    )
    diagnostic = json.loads(capsys.readouterr().err)

    assert exit_code == 2
    assert diagnostic["category"] == "resource_limit"
    assert diagnostic["code"] == "resource_limit_exceeded"
