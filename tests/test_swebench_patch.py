from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import ItemsView, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

import benchmarks.swebench_patch as patch_module
from benchmarks.literal_process import (
    LiteralProcessLimits,
    LiteralProcessResult,
    StreamEvidence,
)
from benchmarks.swebench import (
    DEFAULT_SUITE,
    VerifiedSweBenchSource,
    decode_suite,
    load_source_snapshot,
)
from benchmarks.swebench_patch import (
    PatchCompositionError,
    PatchCompositionLimits,
    VerifiedPatchComposition,
    build_patch_composition,
    decode_patch_composition,
    load_patch_composition,
    replay_patch_composition,
    verify_patch_composition,
    write_patch_composition,
)
from benchmarks.swebench_repository import (
    PreparedSweBenchRepository,
    prepare_task_repository,
    verify_local_bare_mirror,
)

_REPOSITORY = "example/project"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_CANDIDATE_PATCH = """diff --git a/app.txt b/app.txt
--- a/app.txt
+++ b/app.txt
@@ -1 +1 @@
-base
+candidate
"""
_HIDDEN_NEW_FILE_PATCH = """diff --git a/tests/test_hidden.py b/tests/test_hidden.py
new file mode 100644
--- /dev/null
+++ b/tests/test_hidden.py
@@ -0,0 +1 @@
+def test_hidden(): assert True
"""
_HIDDEN_OVERLAP_PATCH = """diff --git a/app.txt b/app.txt
--- a/app.txt
+++ b/app.txt
@@ -1 +1 @@
-base
+hidden
"""
_CANDIDATE_DELETE_PATCH = """diff --git a/app.txt b/app.txt
deleted file mode 100644
--- a/app.txt
+++ /dev/null
@@ -1 +0,0 @@
-base
"""
_INDEXED_CANDIDATE_PATCH = """diff --git a/app.txt b/app.txt
index 1111111..2222222 100644
--- a/app.txt
+++ b/app.txt
@@ -1 +1 @@
-base
+candidate
"""
_QUOTED_UTF8_PATCH = r'''diff --git "a/docs/caf\303\251 name.txt" "b/docs/caf\303\251 name.txt"
--- "a/docs/caf\303\251 name.txt"
+++ "b/docs/caf\303\251 name.txt"
@@ -1 +1 @@
-old
+new
'''
_UNQUOTED_SPACE_PATCH = """diff --git a/docs/user guide.txt b/docs/user guide.txt
--- a/docs/user guide.txt
+++ b/docs/user guide.txt
@@ -1 +1 @@
-old
+new
"""
_HEADER_LOOKING_PAYLOAD_PATCH = """diff --git a/app.txt b/app.txt
--- a/app.txt
+++ b/app.txt
@@ -1 +1 @@
--- /dev/null
+++ /dev/null
"""
_CANDIDATE_RENAME_PATCH = """diff --git a/app.txt b/renamed-one.txt
similarity index 100%
rename from app.txt
rename to renamed-one.txt
diff --git a/app.txt b/renamed-two.txt
similarity index 100%
rename from app.txt
rename to renamed-two.txt
diff --git a/app.txt b/renamed-three.txt
similarity index 100%
rename from app.txt
rename to renamed-three.txt
"""
_CANDIDATE_COPY_PATCH = """diff --git a/app.txt b/copied.txt
similarity index 100%
copy from app.txt
copy to copied.txt
"""
_CANDIDATE_MODE_PATCH = """diff --git a/app.txt b/app.txt
old mode 100644
new mode 100755
"""
_GIT_BINARY_PATCH = """diff --git a/blob.bin b/blob.bin
new file mode 100644
index 0000000..1111111
GIT binary patch
literal 33554432
HcmV?d00001
"""
_GIT_BINARY_DELETION_PATCH = """diff --git a/blob.bin b/blob.bin
deleted file mode 100644
index 0123456789012345678901234567890123456789..0000000000000000000000000000000000000000
Binary files a/blob.bin and /dev/null differ
"""
_EMPTY_FILE_ADD_PATCH = """diff --git a/empty.txt b/empty.txt
new file mode 100644
index 0000000..e69de29
"""
_EMPTY_FILE_DELETE_PATCH = """diff --git a/empty.txt b/empty.txt
deleted file mode 100644
index e69de29..0000000
"""
_TWO_HUNK_PATCH = """diff --git a/app.txt b/app.txt
--- a/app.txt
+++ b/app.txt
@@ -1 +1 @@
-one
+first
@@ -3 +3 @@
-three
+third
"""


class _SplitSourceRecord(Mapping[str, str]):
    """Expose validated items while returning a substituted direct lookup."""

    def __init__(self, valid: Mapping[str, str], substituted_patch: str) -> None:
        self._valid = dict(valid)
        self._substituted_patch = substituted_patch

    def __getitem__(self, key: str) -> str:
        if key == "test_patch":
            return self._substituted_patch
        return self._valid[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._valid)

    def __len__(self) -> int:
        return len(self._valid)

    def items(self) -> ItemsView[str, str]:
        return self._valid.items()


class _SequencedDocument(Mapping[str, object]):
    def __init__(self, *documents: dict[str, object]) -> None:
        self._documents = tuple(copy.deepcopy(document) for document in documents)
        self.calls = 0

    def _selected(self) -> dict[str, object]:
        return self._documents[min(self.calls, len(self._documents) - 1)]

    def __getitem__(self, key: str) -> object:
        return self._selected()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._selected())

    def __len__(self) -> int:
        return len(self._selected())

    def items(self) -> ItemsView[str, object]:
        selected = self._selected()
        self.calls += 1
        return selected.items()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _resign(document: dict[str, object], field: str) -> None:
    unsigned = copy.deepcopy(document)
    unsigned.pop(field, None)
    document[field] = _digest(unsigned)


def _record(ordinal: int, commit: str, test_patch: str) -> dict[str, str]:
    suffix = ordinal + 1
    return {
        "repo": _REPOSITORY,
        "instance_id": f"example__project-{suffix}",
        "base_commit": commit,
        "patch": f"GOLD_SOLUTION_{ordinal}",
        "test_patch": test_patch,
        "problem_statement": f"Fix public problem {ordinal} without gold.",
        "hints_text": "coordinator-only hint",
        "created_at": f"2024-01-{suffix:02d}T00:00:00Z",
        "version": f"{suffix}.0",
        "FAIL_TO_PASS": f'["GOLD_FAIL_{ordinal}"]',
        "PASS_TO_PASS": f'["GOLD_PASS_{ordinal}"]',
        "environment_setup_commit": chr(97 + ordinal) * 40,
        "difficulty": "<15 min fix",
    }


def _suite_document(records: list[dict[str, str]]) -> dict[str, object]:
    document = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
    snapshot_bytes = _canonical_bytes(records)
    source_hashes = [_digest(record) for record in records]
    candidate_hashes = [
        _digest({"problem_statement": record["problem_statement"]}) for record in records
    ]
    snapshot = document["canonical_snapshot"]
    snapshot.update(
        {
            "byte_count": len(snapshot_bytes),
            "sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
            "row_count": len(records),
            "first_instance_id": records[0]["instance_id"],
            "last_instance_id": records[-1]["instance_id"],
            "ordered_instance_ids_sha256": _digest([record["instance_id"] for record in records]),
            "ordered_source_sha256s_sha256": _digest(source_hashes),
            "ordered_candidate_input_sha256s_sha256": _digest(candidate_hashes),
            "record_binding_fields": [
                "ordinal",
                "instance_id",
                "source_sha256",
                "candidate_input_sha256",
            ],
            "record_bindings": [
                [
                    ordinal,
                    record["instance_id"],
                    source_hashes[ordinal],
                    candidate_hashes[ordinal],
                ]
                for ordinal, record in enumerate(records)
            ],
        }
    )
    document["selection"].update(
        {
            "population_count": len(records),
            "selected_count": len(records),
            "ordered_selected_instance_ids_sha256": snapshot["ordered_instance_ids_sha256"],
        }
    )
    _resign(document, "suite_sha256")
    return document


def _git_executable() -> Path:
    located = shutil.which("git")
    if located is None:
        pytest.skip("Git is required for patch-composition tests")
    executable = Path(located).resolve()
    assert executable.is_absolute() and executable.is_file()
    return executable


def _git(
    executable: Path,
    cwd: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> str:
    completed = subprocess.run(
        [str(executable), *arguments],
        cwd=cwd,
        env=environment,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    return completed.stdout.decode("ascii", "strict").strip()


def _snapshot(root: Path) -> tuple[tuple[str, bytes, int], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            path.read_bytes(),
            path.stat(follow_symlinks=False).st_mode,
        )
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix())
        if path.is_file() and not path.is_symlink()
    )


@dataclass(frozen=True, slots=True)
class _PatchFixture:
    source: VerifiedSweBenchSource
    disjoint: PreparedSweBenchRepository
    overlap: PreparedSweBenchRepository


@pytest.fixture(scope="module")
def patch_fixture(tmp_path_factory: pytest.TempPathFactory) -> _PatchFixture:
    root = tmp_path_factory.mktemp("swebench-patch")
    git = _git_executable()
    source_repository = root / "source"
    mirror = root / "mirror.git"
    _git(
        git,
        root,
        "init",
        "--object-format=sha1",
        "--initial-branch=main",
        str(source_repository),
    )
    for name, value in (
        ("user.name", "Patch Fixture"),
        ("user.email", "fixture@example.invalid"),
        ("core.autocrlf", "false"),
        ("core.filemode", "true"),
        ("commit.gpgsign", "false"),
    ):
        _git(git, source_repository, "config", name, value)
    (source_repository / "app.txt").write_bytes(b"base\n")
    _git(git, source_repository, "add", "--all")
    commit_environment = dict(os.environ)
    commit_environment.update(
        {
            "GIT_AUTHOR_DATE": "2001-02-03T04:05:06Z",
            "GIT_COMMITTER_DATE": "2001-02-03T04:05:06Z",
        }
    )
    _git(
        git,
        source_repository,
        "commit",
        "-m",
        "fixture",
        environment=commit_environment,
    )
    commit = _git(git, source_repository, "rev-parse", "HEAD")
    _git(
        git,
        root,
        "clone",
        "--mirror",
        "--no-local",
        str(source_repository),
        str(mirror),
    )
    _git(
        git,
        mirror,
        "remote",
        "set-url",
        "origin",
        f"https://github.com/{_REPOSITORY}.git",
    )
    records = [
        _record(0, commit, _HIDDEN_NEW_FILE_PATCH),
        _record(1, commit, _HIDDEN_OVERLAP_PATCH),
    ]
    suite = decode_suite(_suite_document(records))
    snapshot = root / "source.json"
    snapshot.write_bytes(_canonical_bytes(records))
    source = load_source_snapshot(snapshot, suite=suite)
    verified_mirror = verify_local_bare_mirror(mirror, _REPOSITORY, git)
    outputs = root / "prepared"
    outputs.mkdir()
    disjoint_parent = outputs / "disjoint"
    disjoint_parent.mkdir()
    overlap_parent = outputs / "overlap"
    overlap_parent.mkdir()
    disjoint = prepare_task_repository(source, 0, verified_mirror, disjoint_parent)
    overlap = prepare_task_repository(source, 1, verified_mirror, overlap_parent)
    return _PatchFixture(source=source, disjoint=disjoint, overlap=overlap)


@pytest.fixture(scope="module")
def successful_composition(
    patch_fixture: _PatchFixture,
) -> VerifiedPatchComposition:
    before = _snapshot(patch_fixture.disjoint.path)
    result = build_patch_composition(
        patch_fixture.disjoint,
        patch_fixture.source,
        _CANDIDATE_PATCH,
    )
    assert _snapshot(patch_fixture.disjoint.path) == before
    return result


def test_hidden_new_file_composes_without_mutating_input(
    successful_composition: VerifiedPatchComposition,
) -> None:
    document = successful_composition.to_dict()
    assert successful_composition.retained_disjoint
    assert document["status"] == ("disjoint-composition-preflight-verified-not-a-grader")
    assert document["overlap"] == {
        "disjoint": True,
        "conflicting_paths": [],
    }
    assert document["hidden"]["new_file_count"] == 1
    entry = document["hidden"]["delta"]["entries"][0]
    assert entry["path"] == "tests/test_hidden.py"
    assert entry["pre"] is None
    assert entry["post"]["byte_count"] > 0
    assert document["composition"]["candidate_delta_revalidated"] is True
    assert document["composition"]["hidden_delta_revalidated"] is True
    evidence = document["evidence_state"]
    assert evidence["hidden_new_file_patch_supported"] is True
    for name in (
        "candidate_code_executed",
        "hidden_tests_executed",
        "official_grader_executed",
        "official_result_generated",
        "external_score_generated",
        "external_usefulness_claimed",
        "filesystem_isolation_verified",
        "native_filesystem_quota_verified",
        "native_memory_limit_verified",
        "git_patch_parser_sandbox_verified",
        "git_binary_patch_supported",
        "git_extended_copy_headers_supported",
        "git_extended_rename_headers_supported",
        "git_mode_changes_supported",
        "nonportable_file_modes_supported",
        "preflight_exceptions_are_cohort_results",
        "claim_ready",
    ):
        assert evidence[name] is False
    contract = document["git_apply_contract"]
    assert contract["repository_discovery_ceiling"] == "owned-temporary-root"
    assert contract["binary_patch_policy"] == "rejected-fail-closed"
    assert contract["extended_copy_policy"] == "rejected-fail-closed"
    assert contract["mode_header_policy"] == (
        "mode-changes-rejected-new-and-deleted-files-100644-only"
    )
    assert contract["rename_header_policy"] == "rejected-fail-closed"
    assert contract["native_resource_quota"] is False
    assert contract["failure_disposition"] == ("preflight-exception-not-a-cohort-result")
    assert "--binary" not in contract["argv_tail"]


def test_public_source_mapping_cannot_substitute_hidden_patch(
    patch_fixture: _PatchFixture,
) -> None:
    original = patch_fixture.source
    hostile = VerifiedSweBenchSource(
        suite=original.suite,
        records=(
            _SplitSourceRecord(original.records[0], _HIDDEN_OVERLAP_PATCH),
            *original.records[1:],
        ),
        file_sha256=original.file_sha256,
        byte_count=original.byte_count,
    )
    result = build_patch_composition(
        patch_fixture.disjoint,
        hostile,
        _CANDIDATE_PATCH,
    ).to_dict()
    assert result["status"] == "disjoint-composition-preflight-verified-not-a-grader"
    assert result["hidden"]["delta"]["changed_paths"] == ["tests/test_hidden.py"]
    assert (
        result["source_binding"]["hidden_patch"]["sha256"]
        == hashlib.sha256(_HIDDEN_NEW_FILE_PATCH.encode("utf-8")).hexdigest()
    )


def test_public_prepared_mapping_is_snapshotted_before_verification_and_use(
    patch_fixture: _PatchFixture,
) -> None:
    sequenced = _SequencedDocument(
        patch_fixture.disjoint.to_dict(),
        patch_fixture.overlap.to_dict(),
        patch_fixture.overlap.to_dict(),
    )
    hostile = PreparedSweBenchRepository(
        path=patch_fixture.disjoint.path,
        mirror=patch_fixture.disjoint.mirror,
        limits=patch_fixture.disjoint.limits,
        document=sequenced,
    )

    result = build_patch_composition(
        hostile,
        patch_fixture.source,
        _CANDIDATE_PATCH,
    ).to_dict()

    assert sequenced.calls == 1
    assert result["source_binding"]["source_ordinal"] == 0
    assert result["source_binding"]["preparation_sha256"] == (
        patch_fixture.disjoint.preparation_sha256
    )
    assert result["hidden"]["delta"]["changed_paths"] == ["tests/test_hidden.py"]


def test_temporary_workspace_ignores_ancestor_git_repository(
    tmp_path: Path,
    patch_fixture: _PatchFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git = _git_executable()
    ancestor = tmp_path / "ancestor"
    _git(git, tmp_path, "init", "--initial-branch=main", str(ancestor))
    temporary_parent = ancestor / "nested-temp"
    temporary_parent.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary_parent))

    result = build_patch_composition(
        patch_fixture.disjoint,
        patch_fixture.source,
        _CANDIDATE_PATCH,
    ).to_dict()

    assert result["candidate"]["delta"]["changed_paths"] == ["app.txt"]
    assert result["hidden"]["delta"]["changed_paths"] == ["tests/test_hidden.py"]
    assert result["composition"] is not None


def test_overlap_is_retained_without_composition(
    patch_fixture: _PatchFixture,
) -> None:
    before = _snapshot(patch_fixture.overlap.path)
    result = build_patch_composition(
        patch_fixture.overlap,
        patch_fixture.source,
        _CANDIDATE_PATCH,
    )
    assert _snapshot(patch_fixture.overlap.path) == before
    document = result.to_dict()
    assert not result.retained_disjoint
    assert document["status"] == "overlap-detected-not-composable"
    assert document["overlap"] == {
        "disjoint": False,
        "conflicting_paths": ["app.txt"],
    }
    assert document["composition"] is None
    assert document["evidence_state"]["disjoint_composition_replayed"] is False


def test_overlap_work_is_linear_in_paths_and_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_calls = 0
    original = patch_module._path_prefixes

    def counted(path: str) -> tuple[str, ...]:
        nonlocal observed_calls
        observed_calls += 1
        return original(path)

    monkeypatch.setattr(patch_module, "_path_prefixes", counted)
    count = 20_000
    candidate = {"changed_paths": [f"candidate/{index:05d}" for index in range(count)]}
    hidden = {"changed_paths": [f"hidden/{index:05d}" for index in range(count)]}
    assert patch_module._overlap(candidate, hidden) == []
    assert observed_calls == 2 * count


def test_overlap_detects_equal_and_ancestor_paths() -> None:
    candidate = {"changed_paths": ["equal.txt", "tests"]}
    hidden = {"changed_paths": ["equal.txt", "tests/unit/test_hidden.py"]}
    assert patch_module._overlap(candidate, hidden) == [
        "equal.txt",
        "tests",
        "tests/unit/test_hidden.py",
    ]


def test_candidate_apply_failure_fails_closed(
    patch_fixture: _PatchFixture,
) -> None:
    invalid_patch = _CANDIDATE_PATCH.replace("-base", "-not-the-base")
    before = _snapshot(patch_fixture.disjoint.path)
    with pytest.raises(PatchCompositionError, match="failed closed") as captured:
        build_patch_composition(
            patch_fixture.disjoint,
            patch_fixture.source,
            invalid_patch,
        )
    assert captured.value.stage == "candidate-isolated-apply"
    assert captured.value.disposition == "preflight-exception-not-a-cohort-result"
    assert _snapshot(patch_fixture.disjoint.path) == before


def test_git_binary_patch_is_rejected_before_native_parsing(
    patch_fixture: _PatchFixture,
) -> None:
    with pytest.raises(PatchCompositionError, match="Git binary patch") as captured:
        build_patch_composition(
            patch_fixture.disjoint,
            patch_fixture.source,
            _GIT_BINARY_PATCH,
        )
    assert captured.value.stage == "patch-input-validation"
    assert captured.value.disposition == "preflight-exception-not-a-cohort-result"


def test_payload_free_binary_deletion_is_rejected_before_git(
    patch_fixture: _PatchFixture,
) -> None:
    with pytest.raises(PatchCompositionError, match="binary patch indicator"):
        build_patch_composition(
            patch_fixture.disjoint,
            patch_fixture.source,
            _GIT_BINARY_DELETION_PATCH,
        )


def test_extended_copy_and_mode_change_headers_are_rejected(
    patch_fixture: _PatchFixture,
) -> None:
    for candidate, pattern in (
        (_CANDIDATE_COPY_PATCH, "different logical paths"),
        (_CANDIDATE_MODE_PATCH, "mode-change header"),
        (
            _HIDDEN_NEW_FILE_PATCH.replace(
                "new file mode 100644",
                "new file mode 100755",
            ),
            "non-portable new-file mode",
        ),
    ):
        with pytest.raises(PatchCompositionError, match=pattern):
            build_patch_composition(
                patch_fixture.disjoint,
                patch_fixture.source,
                candidate,
            )


def test_repeated_source_rename_headers_are_rejected_before_source_verification_or_apply(
    patch_fixture: _PatchFixture,
) -> None:
    with pytest.raises(PatchCompositionError, match="different logical paths"):
        build_patch_composition(
            patch_fixture.disjoint,
            patch_fixture.source,
            _CANDIDATE_RENAME_PATCH,
        )


@pytest.mark.parametrize(
    "patch",
    [
        pytest.param(_CANDIDATE_PATCH, id="modify"),
        pytest.param(_HIDDEN_NEW_FILE_PATCH, id="add"),
        pytest.param(_CANDIDATE_DELETE_PATCH, id="delete"),
        pytest.param(_INDEXED_CANDIDATE_PATCH, id="indexed-modify"),
        pytest.param(_QUOTED_UTF8_PATCH, id="quoted-octal-utf8-path"),
        pytest.param(_UNQUOTED_SPACE_PATCH, id="unquoted-space-path"),
        pytest.param(_HEADER_LOOKING_PAYLOAD_PATCH, id="header-looking-payload"),
        pytest.param(_CANDIDATE_PATCH[:-1], id="no-terminal-lf"),
        pytest.param(_EMPTY_FILE_ADD_PATCH, id="header-only-empty-add"),
        pytest.param(_EMPTY_FILE_DELETE_PATCH, id="header-only-empty-delete"),
    ],
)
def test_closed_text_patch_parser_accepts_exact_bytes(patch: str) -> None:
    assert patch_module._patch_bytes(
        patch,
        label="candidate model_patch",
        limits=PatchCompositionLimits(),
    ) == patch.encode("utf-8")


def test_hunk_count_is_not_coupled_to_file_count_limit() -> None:
    assert patch_module._patch_bytes(
        _TWO_HUNK_PATCH,
        label="candidate model_patch",
        limits=PatchCompositionLimits(max_files=1),
    ) == _TWO_HUNK_PATCH.encode("utf-8")


@pytest.mark.parametrize(
    "first_section",
    [
        pytest.param(_CANDIDATE_PATCH, id="contentful-predecessor"),
        pytest.param(_EMPTY_FILE_ADD_PATCH, id="header-only-predecessor"),
    ],
)
def test_unquoted_space_header_is_recognized_after_another_section(
    first_section: str,
) -> None:
    patch = first_section + _UNQUOTED_SPACE_PATCH
    assert patch_module._patch_bytes(
        patch,
        label="candidate model_patch",
        limits=PatchCompositionLimits(),
    ) == patch.encode("utf-8")


def test_section_path_conflicts_are_component_ordered_and_casefolded() -> None:
    separated_ancestor = """diff --git a/A b/A
new file mode 100644
index 0000000..e69de29
diff --git a/a! b/a!
new file mode 100644
index 0000000..e69de29
diff --git a/a/b b/a/b
new file mode 100644
index 0000000..e69de29
"""
    with pytest.raises(PatchCompositionError, match="ancestor-conflicting") as captured:
        patch_module._patch_bytes(
            separated_ancestor,
            label="candidate model_patch",
            limits=PatchCompositionLimits(),
        )

    assert captured.value.stage == "patch-input-validation"


@pytest.mark.parametrize(
    "hostile_patch",
    [
        pytest.param(
            "diff --git a/empty.txt b/empty.txt\nindex e69de29..e69de29 100644\n",
            id="header-only-modify",
        ),
        pytest.param(
            _EMPTY_FILE_ADD_PATCH.replace("0000000..e69de29", "1111111..e69de29"),
            id="inconsistent-empty-add-index",
        ),
        pytest.param(
            _EMPTY_FILE_DELETE_PATCH.replace("e69de29..0000000", "e69de29..2222222"),
            id="inconsistent-empty-delete-index",
        ),
    ],
)
def test_header_only_sections_require_consistent_add_or_delete(
    hostile_patch: str,
) -> None:
    with pytest.raises(PatchCompositionError) as captured:
        patch_module._patch_bytes(
            hostile_patch,
            label="candidate model_patch",
            limits=PatchCompositionLimits(),
        )
    assert captured.value.stage == "patch-input-validation"


def test_candidate_deletion_composes_without_mutating_input(
    patch_fixture: _PatchFixture,
) -> None:
    before = _snapshot(patch_fixture.disjoint.path)
    document = build_patch_composition(
        patch_fixture.disjoint,
        patch_fixture.source,
        _CANDIDATE_DELETE_PATCH,
    ).to_dict()

    assert _snapshot(patch_fixture.disjoint.path) == before
    candidate_entry = document["candidate"]["delta"]["entries"][0]
    assert candidate_entry["path"] == "app.txt"
    assert candidate_entry["pre"] is not None
    assert candidate_entry["post"] is None
    assert document["hidden"]["delta"]["changed_paths"] == ["tests/test_hidden.py"]
    assert document["overlap"]["disjoint"] is True


def test_invalid_candidate_is_rejected_before_source_verification_or_apply(
    patch_fixture: _PatchFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_source_verification(*args: object, **kwargs: object) -> object:
        del args, kwargs
        pytest.fail("invalid candidate reached source verification")

    monkeypatch.setattr(patch_module, "_source_inputs", unexpected_source_verification)
    with pytest.raises(PatchCompositionError) as captured:
        build_patch_composition(
            patch_fixture.disjoint,
            patch_fixture.source,
            "not a unified diff",
        )
    assert captured.value.stage == "patch-input-validation"
    assert captured.value.disposition == "preflight-exception-not-a-cohort-result"


@pytest.mark.parametrize(
    "hostile_patch",
    [
        pytest.param("Subject: wrapped patch\n" + _CANDIDATE_PATCH, id="preamble"),
        pytest.param(_CANDIDATE_PATCH + "\n", id="double-final-lf"),
        pytest.param(
            _CANDIDATE_PATCH.removeprefix("diff --git a/app.txt b/app.txt\n"),
            id="headerless",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace(
                "diff --git a/app.txt b/app.txt",
                "diff --cc app.txt",
                1,
            ),
            id="combined-cc",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace(
                "diff --git a/app.txt b/app.txt",
                "diff --combined app.txt",
                1,
            ),
            id="combined-long",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace(
                "--- a/app.txt",
                "similarity index 100%\n--- a/app.txt",
                1,
            ),
            id="similarity",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace(
                "--- a/app.txt",
                "dissimilarity index 50%\n--- a/app.txt",
                1,
            ),
            id="dissimilarity",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace(
                "--- a/app.txt",
                "rename from app.txt\nrename to app.txt\n--- a/app.txt",
                1,
            ),
            id="standard-rename",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace(
                "--- a/app.txt",
                "rename old app.txt\nrename new app.txt\n--- a/app.txt",
                1,
            ),
            id="legacy-rename",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace(
                "--- a/app.txt",
                "copy from app.txt\ncopy to app.txt\n--- a/app.txt",
                1,
            ),
            id="standard-copy",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace(
                "--- a/app.txt",
                "copy old app.txt\ncopy new app.txt\n--- a/app.txt",
                1,
            ),
            id="legacy-copy",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace(
                "--- a/app.txt",
                "Files a/app.txt and b/app.txt differ\n--- a/app.txt",
                1,
            ),
            id="files-binary",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace(
                "--- a/app.txt",
                "index 1111111..2222222 120000\n--- a/app.txt",
                1,
            ),
            id="unsafe-index",
        ),
        pytest.param(
            _CANDIDATE_DELETE_PATCH.replace(
                "deleted file mode 100644",
                "deleted file mode 100755",
                1,
            ),
            id="non-100644-delete",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace(
                "--- a/app.txt\n+++ b/app.txt",
                "--- a/app.txt\n--- a/app.txt\n+++ b/app.txt",
                1,
            ),
            id="duplicate-old-header",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace(
                "--- a/app.txt\n+++ b/app.txt",
                "+++ b/app.txt\n--- a/app.txt",
                1,
            ),
            id="misordered-headers",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace("+++ b/app.txt", "+++ b/other.txt", 1),
            id="file-header-path-mismatch",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace(
                "diff --git a/app.txt b/app.txt",
                "diff --git a/app.txt b/other.txt",
                1,
            ),
            id="diff-path-mismatch",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace("@@ -1 +1 @@", "@@ -1,2 +1 @@", 1),
            id="hunk-count",
        ),
        pytest.param(
            _CANDIDATE_PATCH.replace("-base", "?base", 1),
            id="hunk-prefix",
        ),
        pytest.param(
            """diff --git a/app.txt b/app.txt
--- a/app.txt
+++ b/app.txt
@@ -1 +1 @@
 base
""",
            id="context-only-hunk",
        ),
    ],
)
def test_closed_text_patch_parser_rejects_non_allowlisted_structure(
    hostile_patch: str,
) -> None:
    with pytest.raises(PatchCompositionError) as captured:
        patch_module._patch_bytes(
            hostile_patch,
            label="candidate model_patch",
            limits=PatchCompositionLimits(),
        )
    assert captured.value.stage == "patch-input-validation"
    assert captured.value.disposition == "preflight-exception-not-a-cohort-result"


@pytest.mark.parametrize(
    "reserved_path",
    [
        "CON .txt",
        "AUX .foo",
        "COM1 .txt",
        "COM\N{SUPERSCRIPT ONE} .txt",
        "LPT\N{SUPERSCRIPT TWO} .log",
    ],
)
def test_patch_paths_reject_spaced_windows_device_aliases(
    reserved_path: str,
) -> None:
    with pytest.raises(PatchCompositionError, match="reserved device name"):
        patch_module._portable_relative_path(
            reserved_path,
            PatchCompositionLimits(),
        )


@pytest.mark.parametrize("portable_path", ["COM0.txt", "COM10.txt", "CON name.txt"])
def test_patch_paths_retain_non_device_name_controls(portable_path: str) -> None:
    assert (
        patch_module._portable_relative_path(
            portable_path,
            PatchCompositionLimits(),
        )
        == portable_path
    )


def test_tree_scan_separately_bounds_directories(
    tmp_path: Path,
) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    (root / "one").mkdir()
    (root / "two").mkdir()
    limits = PatchCompositionLimits(max_files=2)

    assert patch_module._scan_tree(root, limits).summary()["file_count"] == 0
    (root / "three").mkdir()
    with pytest.raises(PatchCompositionError, match="directory count exceeds"):
        patch_module._scan_tree(root, limits)


def test_tree_scan_cleanup_failure_does_not_replace_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingEntry:
        def stat(self, *, follow_symlinks: bool) -> os.stat_result:
            assert follow_symlinks is False
            raise OSError("entry-stat-primary")

    class FailingIterator:
        def __init__(self) -> None:
            self._remaining = [FailingEntry()]

        def __iter__(self) -> FailingIterator:
            return self

        def __next__(self) -> FailingEntry:
            if not self._remaining:
                raise StopIteration
            return self._remaining.pop()

        def close(self) -> None:
            raise OSError("iterator-close-secondary")

    monkeypatch.setattr(patch_module.os, "scandir", lambda _path: FailingIterator())
    with pytest.raises(PatchCompositionError, match="entry could not be inspected") as captured:
        patch_module._scan_tree(tmp_path, PatchCompositionLimits())

    assert "iterator-close-secondary" in captured.value.__notes__[0]
    assert isinstance(captured.value.__cause__, OSError)
    assert "entry-stat-primary" in str(captured.value.__cause__)


def test_tree_scan_normalizes_cleanup_only_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyFailingIterator:
        def __iter__(self) -> EmptyFailingIterator:
            return self

        def __next__(self) -> object:
            raise StopIteration

        def close(self) -> None:
            raise OSError("iterator-close")

    monkeypatch.setattr(
        patch_module.os,
        "scandir",
        lambda _path: EmptyFailingIterator(),
    )
    with pytest.raises(PatchCompositionError, match="iterator could not be closed") as captured:
        patch_module._scan_tree(tmp_path, PatchCompositionLimits())

    assert captured.value.stage == "resource-cleanup"
    assert captured.value.disposition == "preflight-exception-not-a-cohort-result"


@pytest.mark.parametrize(
    "hostile_patch,pattern",
    [
        (
            """diff --git a/../../escape b/../../escape
new file mode 100644
--- /dev/null
+++ b/../../escape
@@ -0,0 +1 @@
+escaped
""",
            "non-portable Git path",
        ),
        (
            """diff --git a/link b/link
new file mode 120000
--- /dev/null
+++ b/link
@@ -0,0 +1 @@
+outside
""",
            "link or submodule mode",
        ),
    ],
)
def test_path_escape_and_symlink_patches_fail_closed(
    patch_fixture: _PatchFixture,
    hostile_patch: str,
    pattern: str,
) -> None:
    outside = patch_fixture.disjoint.path.parent / "escape"
    with pytest.raises(PatchCompositionError, match=pattern):
        build_patch_composition(
            patch_fixture.disjoint,
            patch_fixture.source,
            hostile_patch,
        )
    assert not outside.exists()


def test_literal_process_overflow_fails_closed(
    patch_fixture: _PatchFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def overflow(
        argv: list[str],
        *,
        cwd: Path,
        environment: dict[str, str],
        limits: LiteralProcessLimits,
    ) -> LiteralProcessResult:
        del environment
        stdout = StreamEvidence(
            observed_bytes=limits.max_stdout_bytes + 1,
            captured_bytes=limits.max_stdout_bytes + 1,
            captured_sha256=hashlib.sha256(b"x" * (limits.max_stdout_bytes + 1)).hexdigest(),
            prefix=b"x" * (limits.max_stdout_bytes + 1),
            complete=True,
        )
        empty = StreamEvidence(0, 0, _EMPTY_SHA256, b"", True)
        return LiteralProcessResult(
            argv=tuple(argv),
            cwd=str(cwd),
            limits=limits,
            exit_code=None,
            duration_seconds=0.0,
            setup_duration_seconds=0.0,
            process_duration_seconds=0.0,
            cleanup_duration_seconds=0.0,
            termination_trigger="stdout_limit",
            execution_error=None,
            cleanup_error=None,
            cleanup_detail=None,
            stdout=stdout,
            stderr=empty,
            memory_limit_scope="none",
            containment_scope="test-only",
            environment_names=(),
            environment_sha256=_EMPTY_SHA256,
        )

    monkeypatch.setattr(patch_module, "run_literal_argv", overflow)
    with pytest.raises(PatchCompositionError, match="failed closed"):
        build_patch_composition(
            patch_fixture.disjoint,
            patch_fixture.source,
            _CANDIDATE_PATCH,
            limits=PatchCompositionLimits(max_stdout_bytes=8),
        )


def test_close_descriptors_preserves_primary_error_and_attempts_every_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def failing_close(descriptor: int) -> None:
        calls.append(descriptor)
        raise OSError(f"close-{descriptor}")

    monkeypatch.setattr(patch_module, "os", SimpleNamespace(close=failing_close))
    primary = RuntimeError("primary")
    patch_module._close_descriptors(
        ((10, "first"), (11, "second")),
        prior_error=primary,
    )

    assert calls == [10, 11]
    assert "first descriptor close also failed" in primary.__notes__[0]
    assert "second descriptor close also failed" in primary.__notes__[1]


def test_close_descriptors_normalizes_cleanup_failure_without_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def failing_close(descriptor: int) -> None:
        calls.append(descriptor)
        raise OSError(f"close-{descriptor}")

    monkeypatch.setattr(patch_module, "os", SimpleNamespace(close=failing_close))
    with pytest.raises(PatchCompositionError, match="first descriptor") as captured:
        patch_module._close_descriptors(((10, "first"), (11, "second")))

    assert calls == [10, 11]
    assert captured.value.stage == "resource-cleanup"
    assert captured.value.disposition == "preflight-exception-not-a-cohort-result"
    assert "second descriptor close also failed" in captured.value.__notes__[0]


def test_copy_tree_closes_source_when_destination_open_fails_unexpectedly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("content", encoding="utf-8")
    expected = patch_module._scan_tree(source, PatchCompositionLimits())
    destination = tmp_path / "destination"
    real_open = patch_module.os.open
    source_descriptors: list[int] = []

    def injected_open(path: object, flags: int, *args: object) -> int:
        if Path(path) == destination / "file.txt":
            raise RuntimeError("destination-open-unexpected")
        descriptor = real_open(path, flags, *args)
        if Path(path) == source / "file.txt":
            source_descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(patch_module.os, "open", injected_open)
    with pytest.raises(RuntimeError, match="destination-open-unexpected"):
        patch_module._copy_verified_tree(
            source,
            destination,
            expected,
            PatchCompositionLimits(),
        )

    assert len(source_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(source_descriptors[0])


def test_copy_tree_closes_both_descriptors_when_digest_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("content", encoding="utf-8")
    expected = patch_module._scan_tree(source, PatchCompositionLimits())
    destination = tmp_path / "destination"
    real_open = patch_module.os.open
    descriptors: list[int] = []

    def observed_open(path: object, flags: int, *args: object) -> int:
        descriptor = real_open(path, flags, *args)
        descriptors.append(descriptor)
        return descriptor

    def failing_digest() -> object:
        raise MemoryError("digest-setup")

    monkeypatch.setattr(patch_module.os, "open", observed_open)
    monkeypatch.setattr(patch_module.hashlib, "sha256", failing_digest)
    with pytest.raises(MemoryError, match="digest-setup"):
        patch_module._copy_verified_tree(
            source,
            destination,
            expected,
            PatchCompositionLimits(),
        )

    assert len(descriptors) == 2
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_owned_temporary_directory_preserves_primary_cleanup_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingTemporaryDirectory:
        def __init__(self, *, prefix: str, dir: Path) -> None:
            self.name = str(Path(dir) / f"{prefix}owned")
            Path(self.name).mkdir()

        def cleanup(self) -> None:
            raise OSError("cleanup")

    monkeypatch.setattr(
        patch_module,
        "tempfile",
        SimpleNamespace(TemporaryDirectory=FailingTemporaryDirectory),
    )
    primary = RuntimeError("primary")
    with (
        pytest.raises(RuntimeError) as captured,
        patch_module._owned_temporary_directory(tmp_path),
    ):
        raise primary

    assert captured.value is primary
    assert "temporary workspace cleanup also failed" in primary.__notes__[0]


def test_owned_temporary_directory_normalizes_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingTemporaryDirectory:
        def __init__(self, *, prefix: str, dir: Path) -> None:
            self.name = str(Path(dir) / f"{prefix}owned")
            Path(self.name).mkdir()

        def cleanup(self) -> None:
            raise OSError("cleanup")

    monkeypatch.setattr(
        patch_module,
        "tempfile",
        SimpleNamespace(TemporaryDirectory=FailingTemporaryDirectory),
    )
    with (
        pytest.raises(PatchCompositionError, match="cleanup failed") as captured,
        patch_module._owned_temporary_directory(tmp_path),
    ):
        pass

    assert captured.value.stage == "temporary-workspace-cleanup"
    assert captured.value.disposition == "preflight-exception-not-a-cohort-result"


def test_build_wrapper_normalizes_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("unexpected")

    def fail_build(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise failure

    monkeypatch.setattr(patch_module, "_build_patch_composition_impl", fail_build)
    with pytest.raises(PatchCompositionError, match="build failed closed") as captured:
        build_patch_composition(object(), object(), _CANDIDATE_PATCH)  # type: ignore[arg-type]

    assert captured.value.stage == "preflight-build"
    assert captured.value.disposition == "preflight-exception-not-a-cohort-result"
    assert captured.value.__cause__ is failure


def test_decode_rejects_oversized_document_before_canonical_hash(
    successful_composition: VerifiedPatchComposition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = successful_composition.to_dict()
    document["limits"]["max_document_bytes"] = 1

    def unexpected_hash(_value: object) -> str:
        pytest.fail("oversized document reached canonical hashing")

    monkeypatch.setattr(patch_module, "_canonical_sha256", unexpected_hash)
    with pytest.raises(PatchCompositionError, match="byte or depth limit") as captured:
        decode_patch_composition(document)

    assert captured.value.stage == "patch-composition-decode"
    assert captured.value.disposition == "preflight-exception-not-a-cohort-result"


def test_decode_rejects_hostile_dict_subclass_without_iterating() -> None:
    class HostileDict(dict[str, object]):
        def __iter__(self) -> Iterator[str]:
            raise RuntimeError("hostile iteration")

    with pytest.raises(PatchCompositionError, match="fields are invalid") as captured:
        decode_patch_composition(HostileDict())

    assert captured.value.disposition == "preflight-exception-not-a-cohort-result"


def test_resigned_false_claim_is_rejected(
    successful_composition: VerifiedPatchComposition,
) -> None:
    forged = successful_composition.to_dict()
    forged["evidence_state"]["official_grader_executed"] = True
    _resign(forged, "patch_composition_sha256")
    with pytest.raises(PatchCompositionError, match="must be false"):
        decode_patch_composition(forged)


def test_retained_disjoint_fails_closed_for_publicly_constructed_evidence() -> None:
    forged = VerifiedPatchComposition(
        PatchCompositionLimits(),
        {"overlap": {"disjoint": True}},
    )
    assert forged.retained_disjoint is False
    assert forged.claim_ready is False


def test_resigned_noncanonical_or_out_of_bounds_evidence_is_rejected(
    successful_composition: VerifiedPatchComposition,
) -> None:
    forged = successful_composition.to_dict()
    forged["candidate"]["apply"]["role"] = "wrong-role"
    _resign(forged, "patch_composition_sha256")
    with pytest.raises(PatchCompositionError, match="role is invalid"):
        decode_patch_composition(forged)

    forged = successful_composition.to_dict()
    stdout = forged["candidate"]["apply"]["stdout"]
    stdout["observed_bytes"] = forged["limits"]["max_stdout_bytes"] + 1
    stdout["captured_bytes"] = stdout["observed_bytes"]
    stdout["captured_sha256"] = "a" * 64
    _resign(forged, "patch_composition_sha256")
    with pytest.raises(PatchCompositionError, match="stdout is incomplete"):
        decode_patch_composition(forged)

    forged = successful_composition.to_dict()
    forged["candidate"]["delta"]["entries"][0]["path"] = "./app.txt"
    _resign(forged, "patch_composition_sha256")
    with pytest.raises(PatchCompositionError, match="path is not canonical"):
        decode_patch_composition(forged)

    forged = successful_composition.to_dict()
    forged["candidate"]["apply"]["exit_code"] = False
    _resign(forged, "patch_composition_sha256")
    with pytest.raises(PatchCompositionError, match="exit_code must be zero"):
        decode_patch_composition(forged)

    forged = successful_composition.to_dict()
    forged["hidden"]["new_file_count"] = True
    _resign(forged, "patch_composition_sha256")
    with pytest.raises(PatchCompositionError, match="new-file count mismatch"):
        decode_patch_composition(forged)

    forged = successful_composition.to_dict()
    forged["candidate"]["post_manifest"]["total_bytes"] += 1
    _resign(forged, "patch_composition_sha256")
    with pytest.raises(PatchCompositionError, match="derived byte count mismatch"):
        decode_patch_composition(forged)

    for field in (
        "binary_patch_policy",
        "extended_copy_policy",
        "mode_header_policy",
        "rename_header_policy",
    ):
        forged = successful_composition.to_dict()
        forged["git_apply_contract"][field] = "forged-policy"
        _resign(forged, "patch_composition_sha256")
        with pytest.raises(PatchCompositionError, match="launch contract"):
            decode_patch_composition(forged)


def test_decoded_delta_entry_count_is_bounded() -> None:
    with pytest.raises(PatchCompositionError, match="entries exceed"):
        patch_module._decode_delta(
            {
                "changed_path_count": 3,
                "changed_paths": [],
                "entries": [{}, {}, {}],
                "entries_sha256": _EMPTY_SHA256,
            },
            label="adversarial delta",
            limits=PatchCompositionLimits(max_files=1),
        )


def test_live_verification_binds_exact_current_base_manifest(
    patch_fixture: _PatchFixture,
    successful_composition: VerifiedPatchComposition,
) -> None:
    forged = successful_composition.to_dict()
    for summary in (
        forged["base_manifest"],
        forged["candidate"]["post_manifest"],
        forged["hidden"]["post_manifest"],
        forged["composition"]["mid_manifest"],
        forged["composition"]["final_manifest"],
    ):
        summary["total_bytes"] += 1
    _resign(forged, "patch_composition_sha256")
    decoded = decode_patch_composition(forged)
    composition = VerifiedPatchComposition(
        successful_composition.limits,
        decoded,
    )
    with pytest.raises(PatchCompositionError, match="live base manifest mismatch"):
        verify_patch_composition(
            composition,
            patch_fixture.disjoint,
            patch_fixture.source,
            _CANDIDATE_PATCH,
        )


def test_public_verify_load_and_write_require_exact_semantic_replay(
    tmp_path: Path,
    patch_fixture: _PatchFixture,
    successful_composition: VerifiedPatchComposition,
) -> None:
    forged = successful_composition.to_dict()
    empty_delta = {
        "changed_path_count": 0,
        "changed_paths": [],
        "entries": [],
        "entries_sha256": _digest([]),
    }
    forged["candidate"]["delta"] = empty_delta
    forged["candidate"]["post_manifest"] = copy.deepcopy(forged["base_manifest"])
    forged["composition"]["mid_manifest"] = copy.deepcopy(forged["base_manifest"])
    forged["composition"]["final_manifest"] = copy.deepcopy(forged["hidden"]["post_manifest"])
    _resign(forged, "patch_composition_sha256")
    decoded = decode_patch_composition(forged)
    composition = VerifiedPatchComposition(
        successful_composition.limits,
        decoded,
    )
    assert composition.retained_disjoint is True

    with pytest.raises(PatchCompositionError, match="semantic replay mismatch"):
        verify_patch_composition(
            composition,
            patch_fixture.disjoint,
            patch_fixture.source,
            _CANDIDATE_PATCH,
        )

    output = tmp_path / "forged-output.json"
    with pytest.raises(PatchCompositionError, match="semantic replay mismatch"):
        write_patch_composition(
            output,
            composition,
            patch_fixture.disjoint,
            patch_fixture.source,
            _CANDIDATE_PATCH,
        )
    assert not output.exists()

    retained = tmp_path / "forged-input.json"
    retained.write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(PatchCompositionError, match="semantic replay mismatch"):
        load_patch_composition(
            retained,
            patch_fixture.disjoint,
            patch_fixture.source,
            _CANDIDATE_PATCH,
        )


def test_strict_write_load_and_replay(
    tmp_path: Path,
    patch_fixture: _PatchFixture,
    successful_composition: VerifiedPatchComposition,
) -> None:
    output = tmp_path / "patch-composition.json"
    write_patch_composition(
        output,
        successful_composition,
        patch_fixture.disjoint,
        patch_fixture.source,
        _CANDIDATE_PATCH,
    )
    loaded = load_patch_composition(
        output,
        patch_fixture.disjoint,
        patch_fixture.source,
        _CANDIDATE_PATCH,
    )
    assert loaded.to_dict() == successful_composition.to_dict()
    replayed = replay_patch_composition(
        loaded,
        patch_fixture.disjoint,
        patch_fixture.source,
        _CANDIDATE_PATCH,
    )
    assert replayed.to_dict() == successful_composition.to_dict()
    with pytest.raises(PatchCompositionError, match="already exists"):
        write_patch_composition(
            output,
            successful_composition,
            patch_fixture.disjoint,
            patch_fixture.source,
            _CANDIDATE_PATCH,
        )


def test_write_normalizes_unexpected_atomic_publication_failure(
    tmp_path: Path,
    patch_fixture: _PatchFixture,
    successful_composition: VerifiedPatchComposition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = OSError("publication")

    def retain_verified_inputs(*args: object, **kwargs: object) -> tuple[object, object, object]:
        del args, kwargs
        return successful_composition, patch_fixture.disjoint, patch_fixture.source

    def fail_publication(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise failure

    monkeypatch.setattr(
        patch_module,
        "_verify_patch_composition_semantic",
        retain_verified_inputs,
    )
    monkeypatch.setattr(patch_module, "atomic_write_text", fail_publication)
    output = tmp_path / "unpublished.json"
    with pytest.raises(PatchCompositionError, match="publication failed") as captured:
        write_patch_composition(
            output,
            successful_composition,
            patch_fixture.disjoint,
            patch_fixture.source,
            _CANDIDATE_PATCH,
        )

    assert not output.exists()
    assert captured.value.stage == "patch-composition-output-publication"
    assert captured.value.disposition == "preflight-exception-not-a-cohort-result"
    assert captured.value.__cause__ is failure


def test_write_output_path_inspection_failure_has_output_stage(
    patch_fixture: _PatchFixture,
    successful_composition: VerifiedPatchComposition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def retain_verified_inputs(*args: object, **kwargs: object) -> tuple[object, object, object]:
        del args, kwargs
        return successful_composition, patch_fixture.disjoint, patch_fixture.source

    monkeypatch.setattr(
        patch_module,
        "_verify_patch_composition_semantic",
        retain_verified_inputs,
    )
    failing_output = SimpleNamespace(
        resolve=lambda *, strict: (_ for _ in ()).throw(OSError("inspection"))
    )
    monkeypatch.setattr(patch_module, "Path", lambda _path: failing_output)
    with pytest.raises(PatchCompositionError, match="could not be inspected") as captured:
        write_patch_composition(
            "ignored",
            successful_composition,
            patch_fixture.disjoint,
            patch_fixture.source,
            _CANDIDATE_PATCH,
        )

    assert captured.value.stage == "patch-composition-output"
    assert captured.value.disposition == "preflight-exception-not-a-cohort-result"


def test_write_size_failure_has_output_stage(
    tmp_path: Path,
    patch_fixture: _PatchFixture,
    successful_composition: VerifiedPatchComposition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tiny = VerifiedPatchComposition(
        PatchCompositionLimits(max_document_bytes=1),
        successful_composition.document,
    )

    def retain_verified_inputs(*args: object, **kwargs: object) -> tuple[object, object, object]:
        del args, kwargs
        return tiny, patch_fixture.disjoint, patch_fixture.source

    monkeypatch.setattr(
        patch_module,
        "_verify_patch_composition_semantic",
        retain_verified_inputs,
    )
    output = tmp_path / "too-large.json"
    with pytest.raises(PatchCompositionError, match="exceeds its byte limit") as captured:
        write_patch_composition(
            output,
            tiny,
            patch_fixture.disjoint,
            patch_fixture.source,
            _CANDIDATE_PATCH,
        )

    assert not output.exists()
    assert captured.value.stage == "patch-composition-output"
    assert captured.value.disposition == "preflight-exception-not-a-cohort-result"


def test_write_accepts_exact_canonical_document_byte_limit(
    tmp_path: Path,
    patch_fixture: _PatchFixture,
    successful_composition: VerifiedPatchComposition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = patch_module._canonical_json_bytes(successful_composition.to_dict())
    boundary = VerifiedPatchComposition(
        PatchCompositionLimits(max_document_bytes=len(encoded)),
        successful_composition.document,
    )

    def retain_verified_inputs(*args: object, **kwargs: object) -> tuple[object, object, object]:
        del args, kwargs
        return boundary, patch_fixture.disjoint, patch_fixture.source

    monkeypatch.setattr(
        patch_module,
        "_verify_patch_composition_semantic",
        retain_verified_inputs,
    )
    output = tmp_path / "boundary.json"
    write_patch_composition(
        output,
        boundary,
        patch_fixture.disjoint,
        patch_fixture.source,
        _CANDIDATE_PATCH,
    )

    assert output.read_bytes() == encoded
