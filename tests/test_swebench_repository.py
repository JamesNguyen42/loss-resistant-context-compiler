from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import FrozenInstanceError, dataclass, replace
from pathlib import Path

import pytest

import benchmarks.swebench_repository as swebench_repository_module
import benchmarks.swebench_repository_worker as repository_worker
from benchmarks.swebench_repository import (
    PreparedSweBenchRepository,
    RepositoryPreparationError,
    RepositoryPreparationLimits,
    VerifiedBareMirror,
    prepare_repository_from_commit,
    verify_local_bare_mirror,
    verify_prepared_repository,
)

_REPOSITORY = "example/project"
_INSTANCE_ID = "example__project-1"
_SOURCE_RECORD_SHA256 = "a" * 64
_REGULAR_BYTES = b"\x00\xffraw-blob\r\nbytes\x00"
_EXECUTABLE_BYTES = b"#!/bin/sh\nprintf 'fixture\\n'\n"


def _git_executable() -> Path:
    located = shutil.which("git")
    if located is None:
        pytest.skip(
            "Git is required for repository-preparation tests",
            allow_module_level=True,
        )
    executable = Path(located).resolve()
    assert executable.is_absolute() and executable.is_file()
    return executable


_GIT = _git_executable()


def _git(
    cwd: Path,
    *arguments: str,
    input_bytes: bytes | None = None,
    environment: dict[str, str] | None = None,
) -> bytes:
    completed = subprocess.run(
        [str(_GIT), *arguments],
        cwd=cwd,
        env=environment,
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(
            "fixture Git command failed: "
            f"{arguments!r}: {completed.stderr.decode('utf-8', errors='replace')}"
        )
    return completed.stdout


def _git_text(cwd: Path, *arguments: str, input_bytes: bytes | None = None) -> str:
    return _git(cwd, *arguments, input_bytes=input_bytes).decode("ascii").strip()


@dataclass(frozen=True, slots=True)
class _RepositoryFixture:
    source: Path
    mirror: Path
    commit: str


@pytest.fixture
def repository_fixture(tmp_path: Path) -> _RepositoryFixture:
    source = tmp_path / "source"
    mirror = tmp_path / "mirror.git"
    _git(
        tmp_path,
        "init",
        "--object-format=sha1",
        "--initial-branch=main",
        str(source),
    )
    for name, value in (
        ("user.name", "Repository Fixture"),
        ("user.email", "fixture@example.invalid"),
        ("core.autocrlf", "false"),
        ("core.filemode", "true"),
        ("commit.gpgsign", "false"),
    ):
        _git(source, "config", name, value)

    (source / "bin").mkdir()
    (source / "regular.bin").write_bytes(_REGULAR_BYTES)
    (source / "bin" / "tool.sh").write_bytes(_EXECUTABLE_BYTES)
    _git(source, "add", "--all")
    _git(source, "update-index", "--chmod=+x", "bin/tool.sh")
    commit_environment = dict(os.environ)
    commit_environment.update(
        {
            "GIT_AUTHOR_DATE": "2001-02-03T04:05:06Z",
            "GIT_COMMITTER_DATE": "2001-02-03T04:05:06Z",
        }
    )
    _git(source, "commit", "-m", "fixture", environment=commit_environment)
    commit = _git_text(source, "rev-parse", "HEAD")
    assert len(commit) == 40

    _git(tmp_path, "clone", "--mirror", "--no-local", str(source), str(mirror))
    _git(
        mirror,
        "remote",
        "set-url",
        "origin",
        f"https://github.com/{_REPOSITORY}.git",
    )
    assert _git_text(mirror, "rev-parse", "--show-object-format") == "sha1"
    return _RepositoryFixture(source=source, mirror=mirror, commit=commit)


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assert_self_hash(document: dict[str, object], field: str) -> None:
    claimed = document[field]
    assert isinstance(claimed, str) and len(claimed) == 64
    unsigned = copy.deepcopy(document)
    unsigned.pop(field)
    assert claimed == _canonical_sha256(unsigned)


def _resign(document: dict[str, object], field: str) -> None:
    unsigned = copy.deepcopy(document)
    unsigned.pop(field, None)
    document[field] = _canonical_sha256(unsigned)


def _verified_mirror(repository_fixture: _RepositoryFixture, *, limits=None):
    return verify_local_bare_mirror(
        repository_fixture.mirror,
        _REPOSITORY,
        _GIT,
        limits=limits,
    )


def _prepare(
    repository_fixture: _RepositoryFixture,
    output_parent: Path,
    *,
    limits: RepositoryPreparationLimits | None = None,
    commit: str | None = None,
):
    output_parent.mkdir()
    mirror = _verified_mirror(repository_fixture, limits=limits)
    prepared = prepare_repository_from_commit(
        mirror,
        repository_fixture.commit if commit is None else commit,
        _REPOSITORY,
        _INSTANCE_ID,
        0,
        _SOURCE_RECORD_SHA256,
        output_parent,
        limits=limits,
    )
    return mirror, prepared


def _file_snapshot(root: Path) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix())
        if path.is_file()
    )


def _write_tree(mirror: Path, entries: list[tuple[str, str, str, str]]) -> str:
    payload = b"".join(
        f"{mode} {kind} {object_id}\t{name}".encode() + b"\0"
        for mode, kind, object_id, name in entries
    )
    return _git_text(mirror, "mktree", "-z", input_bytes=payload)


def _single_path_tree(
    mirror: Path,
    path: str,
    *,
    mode: str,
    kind: str,
    object_id: str,
) -> str:
    components = path.split("/")
    current = _write_tree(mirror, [(mode, kind, object_id, components[-1])])
    for component in reversed(components[:-1]):
        current = _write_tree(mirror, [("040000", "tree", current, component)])
    return current


def _commit_tree(mirror: Path, tree: str, parent: str, ref_name: str) -> str:
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_AUTHOR_NAME": "Repository Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Repository Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_DATE": "2001-02-03T04:05:07Z",
            "GIT_COMMITTER_DATE": "2001-02-03T04:05:07Z",
        }
    )
    commit = _git(
        mirror,
        "commit-tree",
        tree,
        "-p",
        parent,
        "-m",
        "malicious fixture",
        environment=environment,
    ).decode("ascii").strip()
    _git(mirror, "update-ref", f"refs/heads/{ref_name}", commit)
    return commit


def test_worker_python_argv_is_isolated_before_the_staged_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_arguments: list[str] = []

    class ObservedWorkerLaunch(RuntimeError):
        pass

    def observe(arguments: list[str], **_options: object) -> None:
        observed_arguments.extend(arguments)
        raise ObservedWorkerLaunch

    monkeypatch.setattr(swebench_repository_module, "run_literal_argv", observe)
    with pytest.raises(ObservedWorkerLaunch):
        swebench_repository_module._invoke_worker(
            "verify-mirror",
            mirror_path=tmp_path.resolve(),
            repository=_REPOSITORY,
            git_executable=_GIT,
            git_sha256="0" * 64,
            limits=RepositoryPreparationLimits(),
        )

    assert observed_arguments[:4] == [
        str(Path(sys.executable).resolve()),
        "-B",
        "-I",
        "-S",
    ]
    assert Path(observed_arguments[4]).is_absolute()
    assert Path(observed_arguments[4]).name == "repository-worker.py"
    assert observed_arguments[5] == "verify-mirror"


@pytest.mark.parametrize("descriptor", [1, 2], ids=["stdout", "stderr"])
def test_git_runner_bounds_oversized_streams_and_fails_as_output_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    descriptor: int,
) -> None:
    maximum = 16
    observed_drainers: list[repository_worker._BoundedPipeDrainer] = []
    original_drainer = repository_worker._BoundedPipeDrainer

    class ObservedDrainer(original_drainer):
        def __init__(self, stream, configured_maximum: int) -> None:
            super().__init__(stream, configured_maximum)
            observed_drainers.append(self)

    monkeypatch.setattr(repository_worker, "_BoundedPipeDrainer", ObservedDrainer)
    monkeypatch.setattr(repository_worker, "_MAX_GIT_STDERR_BYTES", maximum)
    runner = repository_worker.GitRunner(
        Path(sys.executable).resolve(),
        tmp_path.resolve(),
        timeout_seconds=1.0,
    )
    program = (
        f"import os,time;os.write({descriptor},b'x'*64);time.sleep(30)"
    )
    monkeypatch.setattr(
        runner,
        "argv",
        lambda *_arguments: [
            str(Path(sys.executable).resolve()),
            "-B",
            "-I",
            "-S",
            "-c",
            program,
        ],
    )

    with pytest.raises(repository_worker.WorkerError) as caught:
        runner.run("bounded-output-probe", maximum=maximum)

    failures: list[str] = []
    if str(caught.value) != "git-command-output-limit":
        failures.append(f"unexpected error {caught.value}")
    if len(observed_drainers) != 2:
        failures.append(f"observed {len(observed_drainers)} drainers")
    for index, drainer in enumerate(observed_drainers):
        if len(drainer.data) > drainer.maximum:
            failures.append(
                f"drainer {index} retained {len(drainer.data)} > {drainer.maximum} bytes"
            )
    assert not failures, failures


def test_batch_reader_close_rejects_stdout_after_the_expected_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"bounded-frame"
    object_id = repository_worker._object_oid("blob", body)
    framed = (
        f"{object_id} blob {len(body)}\n".encode("ascii")
        + body
        + b"\nTRAILING"
    )

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(framed)
            self.stderr = io.BytesIO()

        def wait(self, *, timeout: float) -> int:
            assert timeout == 30
            return 0

        def kill(self) -> None:
            pytest.fail("successful fake batch cleanup attempted to kill the process")

    monkeypatch.setattr(
        repository_worker.subprocess,
        "Popen",
        lambda *_arguments, **_options: FakeProcess(),
    )
    runner = repository_worker.GitRunner(_GIT, tmp_path.resolve(), 5)
    batch = repository_worker.BatchReader(runner, "--batch")

    assert batch.request_header(object_id) == ("blob", len(body))
    assert batch.read_body(oid=object_id, kind="blob", size=len(body)) == body
    with pytest.raises(
        repository_worker.WorkerError,
        match="git-batch-stdout-extra",
    ):
        batch.close()


def test_limits_are_frozen_and_preparation_exports_only_raw_tree(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
) -> None:
    limits = RepositoryPreparationLimits(timeout_seconds=30)
    with pytest.raises(FrozenInstanceError):
        limits.max_entries = 1  # type: ignore[misc]

    mirror, first = _prepare(repository_fixture, tmp_path / "first", limits=limits)
    _other_mirror, second = _prepare(
        repository_fixture,
        tmp_path / "second",
        limits=limits,
    )
    first_root = Path(first.path)
    second_root = Path(second.path)

    assert mirror.path == repository_fixture.mirror.resolve()
    assert mirror.git_executable == _GIT
    assert mirror.repository == _REPOSITORY
    assert mirror.origin_url == f"https://github.com/{_REPOSITORY}.git"
    assert first_root.is_absolute() and first_root.is_dir()
    assert _file_snapshot(first_root) == (
        ("bin/tool.sh", _EXECUTABLE_BYTES),
        ("regular.bin", _REGULAR_BYTES),
    )
    assert _file_snapshot(first_root) == _file_snapshot(second_root)
    assert not (first_root / ".git").exists()
    assert all("manifest" not in name.casefold() for name, _data in _file_snapshot(first_root))
    if os.name != "nt":
        assert (first_root / "bin" / "tool.sh").stat().st_mode & stat.S_IXUSR
        assert not ((first_root / "regular.bin").stat().st_mode & stat.S_IXUSR)
    encoded_document = json.dumps(first.to_dict(), sort_keys=True)
    assert "100755" in encoded_document and "100644" in encoded_document
    verify_prepared_repository(first)
    verify_prepared_repository(second)

    mirror_document = mirror.to_dict()
    assert mirror.mirror_sha256 == mirror_document["mirror_sha256"]
    _assert_self_hash(mirror_document, "mirror_sha256")


def test_mirror_and_commit_identity_fail_closed(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
) -> None:
    with pytest.raises(RepositoryPreparationError):
        verify_local_bare_mirror(
            repository_fixture.mirror,
            "other/project",
            _GIT,
        )
    with pytest.raises(RepositoryPreparationError):
        verify_local_bare_mirror(
            repository_fixture.source,
            _REPOSITORY,
            _GIT,
        )

    mirror = _verified_mirror(repository_fixture)
    output_parent = tmp_path / "invalid-commits"
    output_parent.mkdir()
    tree_object = _git_text(
        repository_fixture.mirror,
        "rev-parse",
        f"{repository_fixture.commit}^{{tree}}",
    )
    for index, invalid_commit in enumerate(("0" * 40, tree_object)):
        invalid_output = output_parent / str(index)
        invalid_output.mkdir()
        with pytest.raises(RepositoryPreparationError):
            prepare_repository_from_commit(
                mirror,
                invalid_commit,
                _REPOSITORY,
                _INSTANCE_ID,
                index,
                _SOURCE_RECORD_SHA256,
                invalid_output,
            )


def test_mirror_scan_enforces_entry_bound(
    repository_fixture: _RepositoryFixture,
) -> None:
    limits = RepositoryPreparationLimits(
        timeout_seconds=30,
        max_mirror_entries=1,
    )

    with pytest.raises(RepositoryPreparationError, match="mirror-entry-limit"):
        _verified_mirror(repository_fixture, limits=limits)


@pytest.mark.parametrize("entry_kind", ["symlink", "gitlink"])
def test_preparation_rejects_symlinks_and_gitlinks(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
    entry_kind: str,
) -> None:
    if entry_kind == "symlink":
        object_id = _git_text(
            repository_fixture.mirror,
            "hash-object",
            "-w",
            "--stdin",
            input_bytes=b"regular.bin",
        )
        mode, kind, path = "120000", "blob", "unsafe-link"
    else:
        object_id = repository_fixture.commit
        mode, kind, path = "160000", "commit", "vendor/submodule"
    tree = _single_path_tree(
        repository_fixture.mirror,
        path,
        mode=mode,
        kind=kind,
        object_id=object_id,
    )
    commit = _commit_tree(
        repository_fixture.mirror,
        tree,
        repository_fixture.commit,
        entry_kind,
    )
    mirror = _verified_mirror(repository_fixture)
    output_parent = tmp_path / f"reject-{entry_kind}"
    output_parent.mkdir()

    with pytest.raises(RepositoryPreparationError):
        prepare_repository_from_commit(
            mirror,
            commit,
            _REPOSITORY,
            _INSTANCE_ID,
            0,
            _SOURCE_RECORD_SHA256,
            output_parent,
        )


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "src/.GiT/config",
        "CON.txt",
        "CON .txt",
        "COM\N{SUPERSCRIPT ONE} .txt",
        "LPT\N{SUPERSCRIPT TWO} .log",
    ],
)
def test_preparation_rejects_dot_git_aliases_and_nonportable_paths(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    object_id = _git_text(
        repository_fixture.mirror,
        "hash-object",
        "-w",
        "--stdin",
        input_bytes=b"unsafe",
    )
    tree = _single_path_tree(
        repository_fixture.mirror,
        unsafe_path,
        mode="100644",
        kind="blob",
        object_id=object_id,
    )
    commit = _commit_tree(
        repository_fixture.mirror,
        tree,
        repository_fixture.commit,
        "unsafe-" + hashlib.sha256(unsafe_path.encode()).hexdigest()[:8],
    )
    mirror = _verified_mirror(repository_fixture)
    output_parent = tmp_path / "unsafe-output"
    output_parent.mkdir()

    with pytest.raises(RepositoryPreparationError):
        prepare_repository_from_commit(
            mirror,
            commit,
            _REPOSITORY,
            _INSTANCE_ID,
            0,
            _SOURCE_RECORD_SHA256,
            output_parent,
        )


@pytest.mark.parametrize("portable_path", ["COM0.txt", "COM10.txt", "CON name.txt"])
def test_repository_worker_retains_non_device_name_controls(
    portable_path: str,
) -> None:
    assert repository_worker._portable_component(portable_path.encode("utf-8")) == (
        portable_path
    )


@pytest.mark.parametrize(
    ("limit_name", "limit_value"),
    [
        ("max_entries", 1),
        ("max_blob_bytes", 1),
        ("max_total_blob_bytes", 1),
    ],
)
def test_preparation_enforces_file_count_and_size_bounds(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
    limit_name: str,
    limit_value: int,
) -> None:
    limits = replace(
        RepositoryPreparationLimits(timeout_seconds=30),
        **{limit_name: limit_value},
    )
    mirror = _verified_mirror(repository_fixture, limits=limits)
    output_parent = tmp_path / f"bounded-{limit_name}"
    output_parent.mkdir()

    with pytest.raises(RepositoryPreparationError):
        prepare_repository_from_commit(
            mirror,
            repository_fixture.commit,
            _REPOSITORY,
            _INSTANCE_ID,
            0,
            _SOURCE_RECORD_SHA256,
            output_parent,
            limits=limits,
        )


def test_preparation_requires_an_exclusive_fresh_output(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
) -> None:
    output_parent = tmp_path / "exclusive"
    mirror, prepared = _prepare(repository_fixture, output_parent)
    before = _file_snapshot(Path(prepared.path))

    with pytest.raises(RepositoryPreparationError):
        prepare_repository_from_commit(
            mirror,
            repository_fixture.commit,
            _REPOSITORY,
            _INSTANCE_ID,
            0,
            _SOURCE_RECORD_SHA256,
            output_parent,
        )
    assert _file_snapshot(Path(prepared.path)) == before
    verify_prepared_repository(prepared)


@pytest.mark.parametrize("mutation", ["content", "unexpected-file"])
def test_verification_detects_export_mutation(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
    mutation: str,
) -> None:
    _mirror, prepared = _prepare(repository_fixture, tmp_path / mutation)
    root = Path(prepared.path)
    if mutation == "content":
        (root / "regular.bin").write_bytes(b"mutated")
    else:
        (root / "unexpected.txt").write_bytes(b"unexpected")

    with pytest.raises(RepositoryPreparationError):
        verify_prepared_repository(prepared)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory modes are unavailable")
def test_verification_rejects_permissive_output_directory_mode(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
) -> None:
    _mirror, prepared = _prepare(repository_fixture, tmp_path / "directory-mode")
    (Path(prepared.path) / "bin").chmod(0o777)

    with pytest.raises(RepositoryPreparationError, match="directory mode mismatch"):
        verify_prepared_repository(prepared)


def test_preparation_self_hash_and_claim_boundary_remain_explicit(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
) -> None:
    _mirror, prepared = _prepare(repository_fixture, tmp_path / "claims")
    document = prepared.to_dict()

    assert prepared.claim_ready is False
    assert prepared.preparation_sha256 == document["preparation_sha256"]
    _assert_self_hash(document, "preparation_sha256")
    assert document["evidence_state"] == {
        "task_repository_tree_prepared": True,
        "candidate_mount_created": False,
        "candidate_mount_isolation_verified": False,
        "network_isolation_verified": False,
        "repository_origin_authenticated": False,
        "repository_redistribution_reviewed": False,
        "git_executable_security_reviewed": False,
        "execution_performed": False,
        "grading_performed": False,
        "external_score_generated": False,
        "external_usefulness_claimed": False,
        "claim_ready": False,
        "self_hash_authenticates_author": False,
    }
    verify_prepared_repository(prepared)


def test_mirror_refuses_alternates_partial_clone_includes_and_worktrees(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
) -> None:
    alternates = repository_fixture.mirror / "objects" / "info" / "alternates"
    config_cases = (
        ("remote.origin.promisor", "true", "mirror-partial-clone"),
        ("remote.origin.partialclonefilter", "blob:none", "mirror-partial-clone"),
        ("include.path", str(tmp_path / "untrusted.config"), "mirror-config-includes"),
        ("core.worktree", str(tmp_path / "injected-worktree"), None),
        ("extensions.worktreeConfig", "true", None),
    )

    alternates.write_text(
        str(repository_fixture.source / ".git" / "objects") + "\n",
        encoding="utf-8",
    )
    try:
        with pytest.raises(RepositoryPreparationError, match="mirror-alternates"):
            _verified_mirror(repository_fixture)
    finally:
        alternates.unlink()

    failures: list[str] = []
    for key, value, error_code in config_cases:
        _git(repository_fixture.mirror, "config", "--local", key, value)
        try:
            try:
                _verified_mirror(repository_fixture)
            except RepositoryPreparationError as exc:
                if error_code is not None and error_code not in str(exc):
                    failures.append(f"{key}: unexpected refusal {exc}")
            else:
                failures.append(f"{key}: accepted")
        finally:
            _git(
                repository_fixture.mirror,
                "config",
                "--local",
                "--unset-all",
                key,
            )

    _verified_mirror(repository_fixture)
    assert not failures, failures


def test_forged_mirror_dataclasses_are_compared_with_live_state(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
) -> None:
    mirror = _verified_mirror(repository_fixture)
    bad_self_hash = mirror.to_dict()
    bad_self_hash["mirror_sha256"] = "0" * 64
    resigned_metadata = mirror.to_dict()
    resigned_metadata["metadata"]["entry_count"] += 1
    _resign(resigned_metadata, "mirror_sha256")
    output_parent = tmp_path / "forged-mirrors"
    output_parent.mkdir()

    for document in (bad_self_hash, resigned_metadata):
        forged = VerifiedBareMirror(
            path=mirror.path,
            repository=mirror.repository,
            git_executable=mirror.git_executable,
            limits=mirror.limits,
            document=document,
        )
        with pytest.raises(RepositoryPreparationError, match="mirror evidence changed"):
            prepare_repository_from_commit(
                forged,
                repository_fixture.commit,
                _REPOSITORY,
                _INSTANCE_ID,
                0,
                _SOURCE_RECORD_SHA256,
                output_parent,
            )


def test_prepared_dataclass_self_hash_limits_tree_and_source_are_revalidated(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
) -> None:
    _mirror, prepared = _prepare(repository_fixture, tmp_path / "forged-preparations")
    forged_documents: list[tuple[str, dict[str, object]]] = []

    bad_self_hash = prepared.to_dict()
    bad_self_hash["preparation_sha256"] = "0" * 64
    forged_documents.append(("self hash", bad_self_hash))

    forged_tree = prepared.to_dict()
    forged_tree["tree"]["entry_bindings"][0][4] = "0" * 64
    forged_tree["tree"]["ordered_entry_bindings_sha256"] = _canonical_sha256(
        forged_tree["tree"]["entry_bindings"]
    )
    _resign(forged_tree, "preparation_sha256")
    forged_documents.append(("tree evidence", forged_tree))

    forged_limits = prepared.to_dict()
    forged_limits["limits"]["max_entries"] += 1
    _resign(forged_limits, "preparation_sha256")
    forged_documents.append(("runtime limits", forged_limits))

    forged_source = prepared.to_dict()
    forged_source["source_binding"]["instance_id"] = "other__project-1"
    _resign(forged_source, "preparation_sha256")
    forged_documents.append(("repository/instance binding", forged_source))

    for label, document in forged_documents:
        forged = PreparedSweBenchRepository(
            path=prepared.path,
            mirror=prepared.mirror,
            limits=prepared.limits,
            document=document,
        )
        try:
            verify_prepared_repository(forged)
        except RepositoryPreparationError:
            continue
        pytest.fail(f"forged prepared repository {label} was trusted")


def test_resigned_base_and_root_tree_forgeries_require_live_mirror_evidence(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mirror, prepared = _prepare(repository_fixture, tmp_path / "live-forgeries")
    forged_documents: list[dict[str, object]] = []

    forged_base = prepared.to_dict()
    forged_base["source_binding"]["base_commit"] = "f" * 40
    forged_base["base_object"]["oid"] = "f" * 40
    _resign(forged_base, "preparation_sha256")
    forged_documents.append(forged_base)

    forged_root = prepared.to_dict()
    forged_root["tree"]["root_tree_oid"] = "e" * 40
    root_binding = next(
        binding
        for binding in forged_root["tree"]["tree_object_bindings"]
        if binding[0] == ""
    )
    root_binding[1] = "e" * 40
    forged_root["tree"]["ordered_tree_object_bindings_sha256"] = _canonical_sha256(
        forged_root["tree"]["tree_object_bindings"]
    )
    _resign(forged_root, "preparation_sha256")
    forged_documents.append(forged_root)

    real_invoke_worker = swebench_repository_module._invoke_worker
    observed_operations: list[str] = []

    def observe(operation: str, **options: object):
        observed_operations.append(operation)
        return real_invoke_worker(operation, **options)

    monkeypatch.setattr(swebench_repository_module, "_invoke_worker", observe)
    for document in forged_documents:
        swebench_repository_module.decode_repository_preparation(document)
        forged = PreparedSweBenchRepository(
            path=prepared.path,
            mirror=prepared.mirror,
            limits=prepared.limits,
            document=document,
        )
        with pytest.raises(RepositoryPreparationError):
            verify_prepared_repository(forged)

    assert observed_operations == ["inspect", "inspect"]


def test_verification_rejects_link_special_and_empty_directory_mutations(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
) -> None:
    _mirror, prepared = _prepare(repository_fixture, tmp_path / "entry-types")
    victim = Path(prepared.path) / "regular.bin"
    exercised: list[str] = []

    hard_link = tmp_path / "hard-link-peer.bin"
    try:
        os.link(victim, hard_link)
    except OSError:
        pass
    else:
        exercised.append("hard-link")
        with pytest.raises(RepositoryPreparationError, match="hard-linked"):
            verify_prepared_repository(prepared)
        hard_link.unlink()
        verify_prepared_repository(prepared)

    symlink_target = tmp_path / "symlink-target.bin"
    symlink_target.write_bytes(_REGULAR_BYTES)
    victim.unlink()
    try:
        victim.symlink_to(symlink_target)
    except OSError:
        victim.write_bytes(_REGULAR_BYTES)
    else:
        exercised.append("symbolic-link")
        with pytest.raises(RepositoryPreparationError, match="link or reparse"):
            verify_prepared_repository(prepared)
        victim.unlink()
        victim.write_bytes(_REGULAR_BYTES)
    if os.name != "nt":
        victim.chmod(0o644)

    if hasattr(os, "mkfifo"):
        victim.unlink()
        try:
            os.mkfifo(victim)
        except OSError:
            victim.write_bytes(_REGULAR_BYTES)
        else:
            exercised.append("special-file")
            with pytest.raises(RepositoryPreparationError, match="special file"):
                verify_prepared_repository(prepared)
            victim.unlink()
            victim.write_bytes(_REGULAR_BYTES)
        if os.name != "nt":
            victim.chmod(0o644)

    injected_directory = Path(prepared.path) / "injected-empty-directory"
    injected_directory.mkdir()
    with pytest.raises(RepositoryPreparationError):
        verify_prepared_repository(prepared)
    injected_directory.rmdir()
    exercised.append("empty-directory")

    assert exercised
    verify_prepared_repository(prepared)


@pytest.mark.skipif(os.name != "nt", reason="NTFS alternate streams are Windows-only")
def test_verification_rejects_readable_ntfs_alternate_data_stream(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
) -> None:
    _mirror, prepared = _prepare(repository_fixture, tmp_path / "alternate-stream")
    victim = Path(prepared.path) / "regular.bin"
    alternate = Path(f"{victim}:ctxc-hidden-evidence")
    hidden_bytes = b"independently-readable-alternate-stream\n"
    try:
        alternate.write_bytes(hidden_bytes)
        retained = alternate.read_bytes()
    except OSError:
        pytest.skip("the temporary filesystem does not support NTFS alternate streams")
    if retained != hidden_bytes:
        pytest.skip("the temporary filesystem does not preserve NTFS alternate streams")

    assert victim.read_bytes() == _REGULAR_BYTES
    with pytest.raises(RepositoryPreparationError):
        verify_prepared_repository(prepared)
    assert alternate.read_bytes() == hidden_bytes


def test_noncanonical_limits_and_lower_level_source_mismatches_fail_closed(
    repository_fixture: _RepositoryFixture,
    tmp_path: Path,
) -> None:
    invalid_limit_values = (
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": True},
        {"max_entries": 1.5},
        {"max_total_tree_bytes": 0},
    )
    for values in invalid_limit_values:
        with pytest.raises(RepositoryPreparationError):
            RepositoryPreparationLimits(**values)

    forged_limits = RepositoryPreparationLimits()
    object.__setattr__(forged_limits, "max_worker_stdout_bytes", True)
    with pytest.raises(RepositoryPreparationError):
        verify_local_bare_mirror(
            repository_fixture.mirror,
            _REPOSITORY,
            _GIT,
            limits=forged_limits,
        )

    mirror = _verified_mirror(repository_fixture)
    mismatches = (
        ("other/project", _INSTANCE_ID),
        (_REPOSITORY, "other__project-1"),
    )
    for index, (repository, instance_id) in enumerate(mismatches):
        output_parent = tmp_path / f"source-mismatch-{index}"
        output_parent.mkdir()
        with pytest.raises(RepositoryPreparationError):
            prepare_repository_from_commit(
                mirror,
                repository_fixture.commit,
                repository,
                instance_id,
                index,
                _SOURCE_RECORD_SHA256,
                output_parent,
            )
