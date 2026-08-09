from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import benchmarks.tau2 as tau2
from benchmarks.swebench_repository import RepositoryPreparationLimits
from benchmarks.tau2 import (
    DEFAULT_SUITE,
    MANIFEST_SHA256,
    ORDERED_SOURCE_SHA256S_SHA256,
    ORDERED_TASK_KEY_SHA256S_SHA256,
    PINNED_OBJECTS,
    PINNED_REQUIRED_FILES,
    Tau2Error,
    decode_suite,
    load_suite,
    main,
    verify_local_source,
    verify_suite,
)


def _descriptor() -> dict:
    return json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))


def _resign(document: dict) -> None:
    unsigned = copy.deepcopy(document)
    unsigned.pop("suite_sha256", None)
    document["suite_sha256"] = tau2._canonical_sha256(unsigned)


def _run_git(git: str, cwd: Path, *arguments: str) -> bytes:
    return subprocess.check_output(
        [git, *arguments],
        cwd=cwd,
        stderr=subprocess.STDOUT,
        env={
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        },
    )


def _task(domain: str, task_id: str) -> dict:
    task = {
        "id": task_id,
        "description": None,
        "user_scenario": {"instructions": "simulator only"},
        "initial_state": None,
        "evaluation_criteria": {},
    }
    if domain == "airline":
        task["annotations"] = None
    elif domain == "telecom":
        task["ticket"] = "evaluator-side ticket"
    return task


def _fixture_files(*, reverse_base: bool = False) -> dict[str, bytes]:
    files: dict[str, bytes] = {
        "LICENSE": b"MIT fixture\n",
        "pyproject.toml": (b'[project]\nname = "tau2"\nversion = "1.0.1"\nlicense = "MIT"\n'),
        "uv.lock": b"version = 1\n",
    }
    for domain in tau2.DOMAINS:
        ids = [f"{domain}-first", f"{domain}-second"]
        tasks = [_task(domain, task_id) for task_id in ids]
        base = list(reversed(ids)) if reverse_base else ids
        split: dict[str, list[str]] = {
            "train": [base[0]],
            "test": [base[1]],
            "base": base,
        }
        if domain == "telecom":
            split = {"small": [ids[0]], **split, "full": ids}
        files[tau2.TASK_PATHS[domain]] = json.dumps(tasks).encode("utf-8")
        files[tau2.SPLIT_PATHS[domain]] = json.dumps(split).encode("utf-8")
        files[f"src/tau2/domains/{domain}/environment.py"] = f"# {domain} loader fixture\n".encode()
    return files


def _git_object(git: str, mirror: Path, kind: str, oid: str) -> bytes:
    return subprocess.check_output(
        [git, f"--git-dir={mirror}", "cat-file", kind, oid],
        stderr=subprocess.DEVNULL,
    )


def _object_spec(git: str, mirror: Path, kind: str, oid: str) -> dict:
    body = _git_object(git, mirror, kind, oid)
    return {
        "type": kind,
        "oid": oid,
        "byte_count": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def _synthetic_mirror(tmp_path: Path) -> tuple[str, Path, dict, dict[str, bytes]]:
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is unavailable")
    source = tmp_path / "source"
    source.mkdir()
    _run_git(git, source, "init", "-q")
    files = _fixture_files()
    for relative, encoded in files.items():
        path = source.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
    _run_git(git, source, "add", ".")
    _run_git(
        git,
        source,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-q",
        "-m",
        "fixture source",
    )
    _run_git(
        git,
        source,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "tag",
        "-a",
        tau2.TAG_NAME,
        "-m",
        "fixture tag",
    )
    mirror = (tmp_path / "mirror.git").resolve()
    _run_git(git, tmp_path, "clone", "-q", "--mirror", "--no-hardlinks", str(source), str(mirror))
    _run_git(
        git,
        tmp_path,
        f"--git-dir={mirror}",
        "config",
        "remote.origin.url",
        tau2.REPOSITORY_URL + ".git",
    )
    tag_oid = (
        _run_git(git, tmp_path, f"--git-dir={mirror}", "rev-parse", tau2.TAG_REF).decode().strip()
    )
    commit_oid = (
        _run_git(git, tmp_path, f"--git-dir={mirror}", "rev-parse", f"{tau2.TAG_REF}^{{commit}}")
        .decode()
        .strip()
    )
    root_oid = (
        _run_git(git, tmp_path, f"--git-dir={mirror}", "show", "-s", "--format=%T", commit_oid)
        .decode()
        .strip()
    )

    bindings = []
    for path in tau2.REQUIRED_PATHS:
        oid = (
            _run_git(git, tmp_path, f"--git-dir={mirror}", "rev-parse", f"{commit_oid}:{path}")
            .decode()
            .strip()
        )
        body = _git_object(git, mirror, "blob", oid)
        bindings.append(
            {
                "path": path,
                "git_mode": "100644",
                "blob_oid": oid,
                "byte_count": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )

    document = _descriptor()
    document["source"]["tag_object"] = _object_spec(git, mirror, "tag", tag_oid)
    document["source"]["commit_object"] = _object_spec(git, mirror, "commit", commit_oid)
    document["source"]["root_tree_object"] = _object_spec(git, mirror, "tree", root_oid)
    document["source"]["required_files"] = bindings
    selection = tau2._derive_selection(files)
    document["selection"] = selection
    _resign(document)
    return git, mirror, document, files


def _patch_fixture_pins(monkeypatch: pytest.MonkeyPatch, document: dict) -> None:
    source = document["source"]
    monkeypatch.setattr(
        tau2,
        "PINNED_OBJECTS",
        {name: dict(source[name]) for name in ("tag_object", "commit_object", "root_tree_object")},
    )
    monkeypatch.setattr(
        tau2,
        "PINNED_REQUIRED_FILES",
        tuple(
            (item["path"], item["git_mode"], item["blob_oid"], item["byte_count"], item["sha256"])
            for item in source["required_files"]
        ),
    )
    selection = document["selection"]
    monkeypatch.setattr(
        tau2, "DOMAIN_COUNTS", tuple(tuple(item) for item in selection["domain_counts"])
    )
    monkeypatch.setattr(tau2, "TASK_COUNT", selection["task_count"])
    monkeypatch.setattr(tau2, "MANIFEST_BYTE_COUNT", selection["manifest_byte_count"])
    monkeypatch.setattr(tau2, "MANIFEST_SHA256", selection["manifest_sha256"])
    monkeypatch.setattr(
        tau2,
        "ORDERED_TASK_KEY_SHA256S_SHA256",
        selection["ordered_task_key_sha256s_sha256"],
    )
    monkeypatch.setattr(
        tau2,
        "ORDERED_SOURCE_SHA256S_SHA256",
        selection["ordered_source_sha256s_sha256"],
    )


def test_committed_descriptor_freezes_source_selection_and_false_claims() -> None:
    suite = load_suite()
    summary = verify_suite()
    source = suite.document["source"]
    selection = suite.document["selection"]

    assert {name: dict(source[name]) for name in PINNED_OBJECTS} == {
        name: dict(value) for name, value in PINNED_OBJECTS.items()
    }
    assert (
        tuple(
            (item["path"], item["git_mode"], item["blob_oid"], item["byte_count"], item["sha256"])
            for item in source["required_files"]
        )
        == PINNED_REQUIRED_FILES
    )
    assert selection["task_count"] == 278
    assert selection["domain_counts"] == (("airline", 50), ("retail", 114), ("telecom", 114))
    assert selection["manifest_byte_count"] == 15_948
    assert selection["manifest_sha256"] == MANIFEST_SHA256
    assert selection["ordered_task_key_sha256s_sha256"] == ORDERED_TASK_KEY_SHA256S_SHA256
    assert selection["ordered_source_sha256s_sha256"] == ORDERED_SOURCE_SHA256S_SHA256
    assert selection["record_binding_fields"] == (
        "ordinal",
        "domain",
        "task_key_sha256",
        "source_sha256",
    )
    assert selection["record_bindings"][0][2] == (
        "53c1815ae8d8056f3fa83961cba03469802376130ce8a239596b5f0214f188e7"
    )
    assert selection["record_bindings"][-1][2] == (
        "8232a45c6995f4dfea3ac1390dc20a94de25eb14091f205763cb9de1685dc06c"
    )
    assert selection["exclusions"] == {
        "domains": tau2.EXCLUDED_DOMAINS,
        "task_sets": tau2.EXCLUDED_TASK_SETS,
        "task_files": tau2.EXCLUDED_TASK_FILES,
        "modalities": tau2.EXCLUDED_MODALITIES,
        "source_path_prefixes": tau2.EXCLUDED_SOURCE_PATH_PREFIXES,
        "candidate_excluded_categories": tau2.CANDIDATE_EXCLUDED_CATEGORIES,
    }
    assert suite.file_sha256 == hashlib.sha256(DEFAULT_SUITE.read_bytes()).hexdigest()
    assert suite.document["projection"]["candidate_source_fields"] == ()
    assert summary["candidate_executed"] is False
    assert summary["evaluator_executed"] is False
    assert summary["external_score_generated"] is False
    assert summary["claim_ready"] is False


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["source"]["tag_object"].__setitem__("oid", "0" * 40), "tag_object"),
        (
            lambda d: d["source"]["root_tree_object"].__setitem__("sha256", "0" * 64),
            "root_tree_object",
        ),
        (
            lambda d: d["source"]["required_files"][0].__setitem__("sha256", "0" * 64),
            "required_files",
        ),
        (
            lambda d: d["selection"]["record_bindings"][0].__setitem__(3, "0" * 64),
            "not pinned",
        ),
        (
            lambda d: d["projection"]["candidate_source_fields"].append("user_scenario"),
            "frozen policy",
        ),
        (
            lambda d: d["selection"]["exclusions"]["source_path_prefixes"].append("data/extra/"),
            "excluded source path prefixes",
        ),
        (
            lambda d: d["run_profile"].__setitem__("enforce_communication_protocol", False),
            "run_profile",
        ),
        (
            lambda d: d["claim_boundary"].__setitem__("candidate_executed", True),
            "candidate_executed",
        ),
    ],
)
def test_resigned_descriptor_mutations_fail_closed(mutate, message: str) -> None:
    document = _descriptor()
    mutate(document)
    if message == "not pinned":
        hashes = [binding[3] for binding in document["selection"]["record_bindings"]]
        document["selection"]["ordered_source_sha256s_sha256"] = tau2._canonical_sha256(hashes)
    _resign(document)
    with pytest.raises(Tau2Error, match=message):
        decode_suite(document)


def test_loader_order_uses_tasks_file_order_not_base_array_order() -> None:
    selection = tau2._derive_selection(_fixture_files(reverse_base=True))
    assert [binding[2] for binding in selection["record_bindings"][:2]] == [
        hashlib.sha256(b"airline\tairline-first").hexdigest(),
        hashlib.sha256(b"airline\tairline-second").hexdigest(),
    ]


def test_descriptor_and_summary_do_not_disclose_task_ids() -> None:
    descriptor_text = DEFAULT_SUITE.read_text(encoding="utf-8")
    summary_text = json.dumps(verify_suite(), sort_keys=True)
    for canary in ("PERSONA", "[service_issue]", "[mms_issue]", "[mobile_data_issue]"):
        assert canary not in descriptor_text
        assert canary not in summary_text


def test_task_and_split_shape_failures_are_rejected() -> None:
    files = _fixture_files()
    tasks = json.loads(files[tau2.TASK_PATHS["retail"]])
    tasks[0]["evaluation_gold"] = "must never be silently classified"
    files[tau2.TASK_PATHS["retail"]] = json.dumps(tasks).encode()
    with pytest.raises(Tau2Error, match="fields are not frozen"):
        tau2._derive_selection(files)

    files = _fixture_files()
    split = json.loads(files[tau2.SPLIT_PATHS["airline"]])
    split["test"] = [split["train"][0]]
    files[tau2.SPLIT_PATHS["airline"]] = json.dumps(split).encode()
    with pytest.raises(Tau2Error, match="overlap|base split"):
        tau2._derive_selection(files)


def test_strict_json_rejects_duplicate_keys_bom_and_delimited_ids() -> None:
    with pytest.raises(Tau2Error, match="duplicate key"):
        tau2._strict_json_bytes(b'{"x":1,"x":2}', label="fixture")
    with pytest.raises(Tau2Error, match="BOM"):
        tau2._strict_json_bytes(b"\xef\xbb\xbf{}", label="fixture")
    with pytest.raises(Tau2Error, match="forbidden delimiter"):
        tau2._task_id("bad\tid", label="fixture ID")


def test_synthetic_annotated_tag_mirror_verifies_without_exposing_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git, mirror, document, _files = _synthetic_mirror(tmp_path)
    _patch_fixture_pins(monkeypatch, document)
    decoded = decode_suite(document)
    assert decoded.file_sha256 is None
    suite = tau2.Tau2Suite(document=decoded.document, file_sha256="0" * 64)
    verified = verify_local_source(
        mirror,
        Path(git).resolve(),
        suite=suite,
        limits=RepositoryPreparationLimits(timeout_seconds=30),
    )

    evidence = verified.to_dict()
    assert evidence["task_count"] == 6
    assert evidence["evidence_state"]["annotated_tag_chain_verified"] is True
    assert evidence["evidence_state"]["full_tasks_exposed"] is False
    assert evidence["evidence_state"]["candidate_executed"] is False
    evidence_text = json.dumps(evidence, sort_keys=True)
    assert "descriptor_file_sha256" not in evidence
    assert "0" * 64 not in evidence_text
    assert "airline-first" not in evidence_text
    assert "telecom-second" not in evidence_text
    assert not hasattr(verified, "tasks")

    descriptor_path = tmp_path / "suite.json"
    descriptor_bytes = tau2._canonical_json_bytes(document) + b"\n"
    descriptor_path.write_bytes(descriptor_bytes)
    path_verified = verify_local_source(
        mirror,
        Path(git).resolve(),
        suite_path=descriptor_path,
        limits=RepositoryPreparationLimits(timeout_seconds=30),
    )
    assert (
        path_verified.to_dict()["descriptor_file_sha256"]
        == hashlib.sha256(descriptor_bytes).hexdigest()
    )

    with pytest.raises(Tau2Error, match="mutually exclusive"):
        verify_local_source(
            mirror,
            Path(git).resolve(),
            suite=decoded,
            suite_path=descriptor_path,
        )


def test_git_lifecycle_failure_is_totalized_as_tau_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailedResult:
        process_succeeded = False

        class Stream:
            complete = False
            observed_bytes = 0
            captured_bytes = 0
            prefix = b""

        stdout = Stream()
        stderr = Stream()

    monkeypatch.setattr(tau2, "run_literal_argv", lambda *args, **kwargs: FailedResult())
    mirror = object.__new__(tau2.VerifiedBareMirror)
    object.__setattr__(mirror, "path", Path.cwd())
    object.__setattr__(mirror, "git_executable", Path(sys.executable))
    object.__setattr__(mirror, "limits", RepositoryPreparationLimits())
    with pytest.raises(Tau2Error, match="failed closed"):
        tau2._GitReader(mirror).run(("--version",), maximum=128)


def test_cli_and_module_do_not_import_upstream_tau2(capsys: pytest.CaptureFixture[str]) -> None:
    sys.modules.pop("tau2", None)
    assert main(["verify-suite"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["task_count"] == 278
    assert output["claim_ready"] is False
    assert "tau2" not in sys.modules
