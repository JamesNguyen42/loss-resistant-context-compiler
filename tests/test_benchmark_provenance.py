from __future__ import annotations

from benchmarks.lrcbench import (
    BenchmarkConfig,
    CandidateOutput,
    GoldAtom,
    HistoryCase,
    OutputClaim,
    OutputSpan,
    evaluate_history,
)
from context_compiler import SourceRecord


def test_coarse_span_cannot_game_exact_recall_or_provenance() -> None:
    atom_text = "Do not change the public API."
    content = "terminal noise " * 30 + atom_text + " trailing package log" * 10
    start = content.index(atom_text)
    end = start + len(atom_text)
    source = SourceRecord.create(sequence=0, role="user", content=content)
    atom = GoldAtom(
        id="g-exact",
        kind="constraint",
        text=atom_text,
        source_id=source.id,
        start=start,
        end=end,
        exact=True,
    )
    case = HistoryCase("coarse-span", (source,), (atom,), ("exact-provenance",))
    coarse_claim = OutputClaim(
        atom_text,
        "constraint",
        (OutputSpan(source.id, 0, len(content), content),),
    )
    coarse = CandidateOutput("coarse", (coarse_claim,), atom_text, 10)

    metrics = evaluate_history(case, coarse, BenchmarkConfig())
    assert metrics.critical_recalled == 0
    assert metrics.exact_recalled == 0
    assert metrics.provenance_validity == 0
    assert metrics.perfect is False


def test_exact_atom_span_receives_exact_and_provenance_credit() -> None:
    atom_text = "Do not change the public API."
    source = SourceRecord.create(sequence=0, role="user", content=atom_text)
    atom = GoldAtom(
        id="g-exact",
        kind="constraint",
        text=atom_text,
        source_id=source.id,
        start=0,
        end=len(atom_text),
        exact=True,
    )
    case = HistoryCase("tight-span", (source,), (atom,), ("exact-provenance",))
    claim = OutputClaim(
        atom_text,
        "constraint",
        (OutputSpan(source.id, 0, len(atom_text), atom_text),),
    )
    output = CandidateOutput("tight", (claim,), atom_text, 10)

    metrics = evaluate_history(case, output, BenchmarkConfig())
    assert metrics.critical_atom_recall == 1
    assert metrics.exact_literal_recall == 1
    assert metrics.provenance_validity == 1
