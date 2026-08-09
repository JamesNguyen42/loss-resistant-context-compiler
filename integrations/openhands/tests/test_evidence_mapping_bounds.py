from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest

import ctxc_openhands.evidence as evidence_module
from ctxc_openhands.evidence import (
    EvidenceValidationError,
    finalize_evidence_report,
    validate_evidence_report,
)


class HostileItemsMapping(Mapping[str, Any]):
    def __getitem__(self, key: str) -> Any:
        raise AssertionError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 1

    def items(self) -> Any:
        raise RuntimeError("hostile items")


@pytest.mark.parametrize(
    "operation",
    [finalize_evidence_report, validate_evidence_report],
)
def test_public_mapping_operations_wrap_hostile_items(
    operation: Any,
) -> None:
    with pytest.raises(
        EvidenceValidationError,
        match="could not be traversed safely",
    ):
        operation(HostileItemsMapping())


@pytest.mark.parametrize(
    "operation",
    [finalize_evidence_report, validate_evidence_report],
)
@pytest.mark.parametrize("shared", [False, True])
def test_public_mapping_operations_reject_cycles_and_shared_containers(
    operation: Any,
    shared: bool,
) -> None:
    container: list[Any] = []
    if shared:
        value: dict[str, Any] = {"left": container, "right": container}
    else:
        container.append(container)
        value = {"cycle": container}

    with pytest.raises(
        EvidenceValidationError,
        match="cyclic or shared container",
    ):
        operation(value)


@pytest.mark.parametrize(
    "operation",
    [finalize_evidence_report, validate_evidence_report],
)
def test_public_mapping_operations_reject_depth_oversize_and_surrogate_text(
    operation: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deep: list[Any] = []
    cursor = deep
    for _ in range(evidence_module._MAX_JSON_DEPTH + 1):
        child: list[Any] = []
        cursor.append(child)
        cursor = child

    with pytest.raises(EvidenceValidationError, match="depth limit"):
        operation({"deep": deep})
    with pytest.raises(EvidenceValidationError, match="byte limit"):
        operation({"oversize": "x" * (evidence_module._MAX_STRING_BYTES + 1)})
    with pytest.raises(EvidenceValidationError, match="valid UTF-8"):
        operation({"surrogate": "\ud800"})

    monkeypatch.setattr(evidence_module, "_MAX_JSON_ITEMS", 8)
    with pytest.raises(EvidenceValidationError, match="item limit"):
        operation({"items": [1, 2, 3, 4, 5, 6, 7, 8]})
