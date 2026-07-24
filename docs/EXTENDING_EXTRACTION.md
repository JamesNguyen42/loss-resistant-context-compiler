# Extending extraction without widening the core grammar

The built-in English rule vocabulary is deliberately conservative. Domain
terms should normally live in explicit, versioned extractors instead of being
added to one global regular expression.

This repository provides two public building blocks:

- `DomainLabelExtractor` for exact domain-specific section labels; and
- `CompositeExtractor` for combining multiple named extractors.

The public `Extractor` protocol and `ExtractionResult` type remain available
for domains that need a more specialized implementation.

## Exact domain labels

`DomainLabelExtractor` maps an explicit label to a `MemoryKind` without
accepting caller-supplied regexes:

```python
from context_compiler import (
    ContextCompiler,
    DomainLabelExtractor,
    MemoryKind,
    SourceRecord,
)

payments = DomainLabelExtractor(
    {
        "acceptance criterion": MemoryKind.CONSTRAINT,
        "incident evidence": MemoryKind.CONFIRMED_FACT,
        "runbook locator": MemoryKind.EXACT_REFERENCE,
    },
    name="domain.payments-v1",
)

sources = [
    SourceRecord.create(
        id="event-1",
        sequence=1,
        role="user",
        content=(
            "Acceptance_Criterion:\n"
            "- Preserve the billing ledger.\n"
            "- Retry budget is at most 3 attempts."
        ),
    )
]
memory = ContextCompiler(extractor=payments).compile(sources)
assert memory.verification.passed
```

Labels are ASCII, contain at most 128 characters, and may use letters, digits,
spaces, underscores, and hyphens. They start with a letter. Matching is
case-insensitive after underscores become spaces and repeated whitespace is
collapsed. The mapping is copied at construction, capped at 256 entries, and
exposed through a read-only view. The extractor accepts inline values and the
same bullet forms as built-in labeled sections.

Every emitted value is still an exact source substring with character
offsets. Coordinated constraints use the existing conservative clause
atomizer. A clean domain value after a label or bullet can satisfy an
equivalent broader built-in recovery candidate, preventing the label wrapper
from reappearing as a duplicate claim. Exact errors and references are never
allowed to use this wrapper equivalence: their complete literal must still
match.

### Authority is not configurable

Domain labels do not bypass the core role policy:

| Kind | Allowed source roles |
| --- | --- |
| Goal, constraint, user correction | `user`, `system`, `developer` |
| Decision, unresolved | `user`, `system`, `developer`, `assistant` |
| Confirmed fact | Non-tool roles, or a tool explicitly trusted with the JSON boolean `metadata.trusted_for_state: true` |
| Exact error, exact reference, discarded attempt, progress, context | Any supported role |

The host must authenticate roles before constructing `SourceRecord`s. The
`trusted_for_state` flag must never be copied from tool-controlled data.

## Composing domain packs

Use `CompositeExtractor` when several independently versioned packs are
needed:

```python
from context_compiler import CompositeExtractor, ContextCompiler

domains = CompositeExtractor(
    payments,
    release_engineering,
    name="domains.product-v3",
)
memory = ContextCompiler(extractor=domains).compile(sources)
```

Component names must be unique stable identifiers. Composition is ordered and
fail-fast. Each component must return an `ExtractionResult` containing
`MemoryItem` values, dictionary rejection records, and dictionary metadata.
The composite result records each component name plus item and rejection
counts without copying arbitrary component metadata into the artifact.

Do not add `RuleBasedExtractor()` to this composite merely to retain core
coverage. `ContextCompiler` always runs non-replaceable built-in recovery and
protected certification independently. The recommended composite contains
only the additional domain packs.

If a component raises or violates the result contract, the composite raises.
Under the default compiler policy, that becomes an explicit
`primary_extractor_failed` warning and verified built-in fallback. Set
`fail_on_primary_extractor_error=True` only when missing all domain output must
abort compilation.

A `safety_extractor` is another additive seam, but it does not turn custom
domain coverage into an independently certified obligation. The compiler can
verify every returned item and retain protected returned items; it cannot
prove that arbitrary custom code found every domain-specific statement it was
supposed to find.

## Implementing the protocol directly

A specialized extractor has this shape:

```python
from context_compiler import ExtractionResult, Extractor


class MyDomainExtractor:
    name = "domain.my-product-v1"

    def extract(self, sources):
        items = []
        # Build MemoryItem values from exact source spans.
        return ExtractionResult(
            items=items,
            rejected=[],
            metadata={"extractor": self.name},
        )


assert isinstance(MyDomainExtractor(), Extractor)
```

Custom extractors are trusted executable code. They can read all source text,
consume CPU/memory, mutate objects through unsafe reflective techniques, open
files, or use the network. The compiler validates the returned outer shape,
reruns source digests, verifies item provenance and authority, and retains its
built-in safety passes; it is not a sandbox for the extractor.

`CompilationLimits` bounds each returned result's item count, rejection count,
canonical item bytes, and rejection/metadata bytes before the result enters
recovery or resolution. All passes also share total-candidate, resolved-item,
provenance, and item-work ceilings. A limit violation aborts rather than being
treated as an ordinary provider failure, so protected state is not silently
truncated. These checks cannot recover CPU or memory that arbitrary extractor
code consumed before returning; use `timeout_seconds` and host isolation when
the implementation itself is not trusted.

The ordinary-item contract is intentionally extractive:

- cite existing immutable `SourceRecord` ids;
- use Python character offsets, not UTF-8 byte offsets;
- make ordinary text equal a complete atomic cited clause;
- make exact-item text equal every cited literal;
- retain negation, uncertainty, numbers, units, and scope;
- use a stable, versioned extractor name; and
- do not set reserved internal tags.

An extractor used with `compile(..., timeout_seconds=N)` must be pickle-safe.
`DomainLabelExtractor` and `CompositeExtractor` satisfy that requirement when
their component extractors do.

## Replay boundary

Full artifact verification rechecks custom ledger items for source provenance,
atomic support, role authority, exactness, protected selection, temporal links,
and all other core invariants. It does not load or execute arbitrary custom
extractor code, so it cannot independently reconstruct custom-domain
completeness. Built-in protected coverage remains independently replayable.

This distinction should remain explicit in product claims:

- “the returned custom items passed core verification” can be established;
- “the custom extractor found every domain obligation” requires a separately
  frozen domain corpus or independent implementation.

## Minimum domain-pack test matrix

Before deploying a pack, test:

- every configured inline and section/bullet label;
- case, underscore, whitespace, and numeric variants;
- exact character spans across all relevant line separators;
- unauthorized assistant and tool instruction-shaped text;
- trusted versus untrusted tool facts;
- negation, numbers, units, corrections, and conflicts;
- malformed component results and exceptions;
- oversized item/rejection/metadata output and the intended
  `CompilationLimits` policy;
- isolated compilation/pickling when deadlines are used;
- clean-wheel imports; and
- full artifact replay with separately retained source records.

Version the pack name whenever labels or semantics change. Keep old reports
with the exact pack version and source-corpus digest that produced them.
