from __future__ import annotations

import random
import re
import subprocess
import sys

import context_compiler.extractors as extractors
from context_compiler.extractors import RuleBasedExtractor


def _legacy_reference_matches(text: str) -> list[re.Match[str]]:
    matches: list[re.Match[str]] = []
    for pattern in (
        extractors._LINE_WORD_REFERENCE,
        extractors._GITHUB_REFERENCE,
        extractors._WINDOWS_SPACE_REFERENCE,
        extractors._TEST_REFERENCE,
        extractors._REFERENCE,
    ):
        matches.extend(pattern.finditer(text))
    unique: dict[tuple[int, int], re.Match[str]] = {}
    dynamic_sorted = extractors.__dict__.get("sorted", sorted)
    dynamic_any = extractors.__dict__.get("any", any)
    dynamic_list = extractors.__dict__.get("list", list)
    for match in dynamic_sorted(
        matches,
        key=lambda value: (
            value.start(),
            -(value.end() - value.start()),
        ),
    ):
        span = (match.start("ref"), match.end("ref"))
        if dynamic_any(
            existing[0] <= span[0] and existing[1] >= span[1]
            for existing in unique
        ):
            continue
        unique[span] = match
    return dynamic_list(unique.values())


def _match_signature(matches: list[re.Match[str]]) -> list[tuple[int, int, str]]:
    return [
        (match.start("ref"), match.end("ref"), match.group("ref"))
        for match in matches
    ]


def test_reference_deduplication_matches_legacy_for_large_nonoverlapping_set():
    text = " ".join(f"src/package/file_{index}.py" for index in range(4_000))

    assert _match_signature(RuleBasedExtractor._reference_matches(text)) == (
        _match_signature(_legacy_reference_matches(text))
    )


def test_reference_deduplication_matches_legacy_for_overlapping_forms():
    text = " ".join(
        (
            "src/pkg/test_worker.py::test_case[one]",
            "src/pkg/worker.py lines 10-25",
            "src/pkg/worker.py#L10-L25",
            r"C:\Program Files\Project\worker.py:10-25",
            "/srv/pkg/worker.py:10-25",
        )
        * 100
    )

    assert _match_signature(RuleBasedExtractor._reference_matches(text)) == (
        _match_signature(_legacy_reference_matches(text))
    )


def test_reference_deduplication_randomized_differential():
    randomizer = random.Random(20_260_812)
    fragments = (
        "src/pkg/file.py",
        "src/pkg/file.py:12-14",
        "src/pkg/file.py#L12-L14",
        "src/pkg/file.py lines 12-14",
        "tests/test_file.py::test_case[param]",
        r"C:\Program Files\Project\file.py:12-14",
        "plain text",
        "(src/other/name.ext)",
    )
    separators = (" ", ", ", "; ", " [", "] ")

    for _ in range(2_000):
        text = "".join(
            randomizer.choice(fragments) + randomizer.choice(separators)
            for _ in range(randomizer.randrange(1, 24))
        )
        assert _match_signature(
            RuleBasedExtractor._reference_matches(text)
        ) == _match_signature(_legacy_reference_matches(text))


def test_replaced_any_keeps_legacy_calls_and_result(monkeypatch):
    text = "src/pkg/one.py src/pkg/two.py src/pkg/two.py:10"
    calls: list[int] = []
    original_any = any

    def tracked_any(values):
        materialized = tuple(values)
        calls.append(len(materialized))
        return original_any(materialized)

    monkeypatch.setattr("builtins.any", tracked_any)

    expected = _legacy_reference_matches(text)
    expected_calls = list(calls)
    calls.clear()
    actual = RuleBasedExtractor._reference_matches(text)

    assert _match_signature(actual) == _match_signature(expected)
    assert calls == expected_calls


def test_replaced_sorted_keeps_single_legacy_dispatch(monkeypatch):
    text = "src/pkg/one.py src/pkg/two.py src/pkg/two.py:10"
    calls = 0
    original_sorted = sorted

    def tracked_sorted(values, *, key):
        nonlocal calls
        calls += 1
        return original_sorted(values, key=key)

    monkeypatch.setattr("builtins.sorted", tracked_sorted)

    expected = _legacy_reference_matches(text)
    assert calls == 1
    calls = 0
    actual = RuleBasedExtractor._reference_matches(text)

    assert _match_signature(actual) == _match_signature(expected)
    assert calls == 1


class _StatefulMatch:
    def __init__(self, start: int, end: int, value: str) -> None:
        self._start = start
        self._end = end
        self._value = value
        self.calls: list[tuple[str, str | None]] = []

    def start(self, group: str | None = None) -> int:
        self.calls.append(("start", group))
        return self._start

    def end(self, group: str | None = None) -> int:
        self.calls.append(("end", group))
        return self._end

    def group(self, group: str) -> str:
        assert group == "ref"
        return self._value


class _StatefulPattern:
    def __init__(self, matches: list[_StatefulMatch]) -> None:
        self._matches = matches

    def finditer(self, text: str):
        assert text == "opaque"
        return iter(self._matches)


def test_replaced_pattern_keeps_custom_match_access_order(monkeypatch):
    expected_matches = [
        _StatefulMatch(0, 9, "one/path"),
        _StatefulMatch(0, 5, "one/p"),
        _StatefulMatch(12, 20, "two/path"),
    ]
    monkeypatch.setattr(
        extractors,
        "_REFERENCE",
        _StatefulPattern(expected_matches),
    )
    expected = _legacy_reference_matches("opaque")
    expected_calls = [list(match.calls) for match in expected_matches]

    actual_matches = [
        _StatefulMatch(0, 9, "one/path"),
        _StatefulMatch(0, 5, "one/p"),
        _StatefulMatch(12, 20, "two/path"),
    ]
    monkeypatch.setattr(
        extractors,
        "_REFERENCE",
        _StatefulPattern(actual_matches),
    )
    actual = RuleBasedExtractor._reference_matches("opaque")

    assert expected == [expected_matches[0], expected_matches[2]]
    assert actual == [actual_matches[0], actual_matches[2]]
    assert [match.calls for match in actual_matches] == expected_calls


def test_string_subclass_uses_legacy_deduplication(monkeypatch):
    class StringSubclass(str):
        pass

    calls = 0
    original_any = any

    def tracked_any(values):
        nonlocal calls
        calls += 1
        return original_any(values)

    monkeypatch.setattr("builtins.any", tracked_any)
    text = StringSubclass("src/pkg/one.py src/pkg/two.py")

    assert _match_signature(RuleBasedExtractor._reference_matches(text)) == (
        _match_signature(_legacy_reference_matches(text))
    )
    assert calls > 0


def test_preimport_compile_hook_with_nonmonotonic_named_spans_falls_back():
    source = r'''\
import re

original_compile = re.compile
replacement_patterns = iter((
    original_compile(r"^.{100}(?P<ref>.{100})"),
    original_compile(r"(?<=^.{50})(?P<ref>.{100})"),
    original_compile(r"(?<=^.{150})(?P<ref>.{100})"),
    original_compile(r"(?!)"),
    original_compile(r"(?!)"),
))

def hooked_compile(pattern, flags=0):
    if isinstance(pattern, str) and "(?P<ref>" in pattern:
        try:
            return next(replacement_patterns)
        except StopIteration:
            pass
    return original_compile(pattern, flags)

re.compile = hooked_compile
from context_compiler.extractors import RuleBasedExtractor

matches = RuleBasedExtractor._reference_matches("x" * 250)
assert [match.span() for match in matches] == [
    (0, 200),
    (50, 150),
    (150, 250),
]
assert [match.span("ref") for match in matches] == [
    (100, 200),
    (50, 150),
    (150, 250),
]
'''
    completed = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_preimport_compile_hook_with_unmatched_named_group_falls_back():
    source = r'''
import re

original_compile = re.compile
replacement = original_compile(r"(?P<ref>x)?y")

def hooked_compile(pattern, flags=0):
    if isinstance(pattern, str) and "(?P<ref>" in pattern:
        return replacement
    return original_compile(pattern, flags)

re.compile = hooked_compile
from context_compiler.extractors import RuleBasedExtractor

matches = RuleBasedExtractor._reference_matches("y")
assert [match.span() for match in matches] == [(0, 1)]
assert [match.span("ref") for match in matches] == [(-1, -1)]
'''
    completed = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_missing_any_is_not_resolved_when_there_are_no_matches():
    source = r'''
import builtins
from context_compiler.extractors import RuleBasedExtractor

del builtins.any
assert RuleBasedExtractor._reference_matches("plain words only") == []
'''
    completed = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
