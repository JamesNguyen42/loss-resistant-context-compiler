from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import stat
import tarfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from scripts import release_reproducibility as reproducibility
from scripts.release_reproducibility import (
    PACKAGE_MATRIX_LANES,
    PACKAGE_MATRIX_REPORT_SCHEMA,
    ReleaseReproducibilityError,
    _sdist_inventory,
    compare_package_matrix,
    compare_release_builds,
    main,
)

WHEEL = "loss_resistant_context_compiler-0.1.1a19-py3-none-any.whl"
SDIST = "loss_resistant_context_compiler-0.1.1a19.tar.gz"
ROOT = "loss_resistant_context_compiler-0.1.1a19"
MATRIX_REVISION = "1" * 40
MATRIX_ROOT_WHEEL = "loss_resistant_context_compiler-0.1.1a19-py3-none-any.whl"
MATRIX_INTEGRATION_VERSION = "0.1.0a20"
MATRIX_INTEGRATION_WHEEL = f"ctxc_openhands-{MATRIX_INTEGRATION_VERSION}-py3-none-any.whl"
MATRIX_INTEGRATION_SDIST = f"ctxc_openhands-{MATRIX_INTEGRATION_VERSION}.tar.gz"
MATRIX_BUILD_INPUTS = tuple(reproducibility._BUILD_INPUT_WHEELS)
MATRIX_TRACKED_BUILD_LOCK: bytes | None = None


@pytest.fixture(autouse=True)
def _bind_synthetic_matrix_to_its_tracked_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    global MATRIX_TRACKED_BUILD_LOCK
    original = reproducibility._tracked_build_lock_payload

    def tracked_lock() -> bytes:
        if MATRIX_TRACKED_BUILD_LOCK is None:
            return original()
        return MATRIX_TRACKED_BUILD_LOCK

    monkeypatch.setattr(
        reproducibility,
        "_tracked_build_lock_payload",
        tracked_lock,
    )
    try:
        yield
    finally:
        MATRIX_TRACKED_BUILD_LOCK = None


def _self_hashed_report(unsigned: dict[str, object]) -> dict[str, object]:
    encoded = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {**unsigned, "report_sha256": hashlib.sha256(encoded).hexdigest()}


def _write_wheel(path: Path, *, timestamp: tuple[int, int, int, int, int, int]) -> None:
    entries = {
        "context_compiler/__init__.py": b'__version__ = "0.1.1a19"\n',
        "loss_resistant_context_compiler-0.1.1a19.dist-info/METADATA": (
            b"Metadata-Version: 2.4\nName: loss-resistant-context-compiler\nVersion: 0.1.1a19\n"
        ),
        "loss_resistant_context_compiler-0.1.1a19.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        "loss_resistant_context_compiler-0.1.1a19.dist-info/RECORD": b"",
    }
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            member = zipfile.ZipInfo(name, date_time=timestamp)
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100644 << 16
            archive.writestr(member, content)


def _write_sdist(
    path: Path,
    *,
    member_mtime: int,
    pkg_info: bytes = b"Metadata-Version: 2.4\nName: loss-resistant-context-compiler\n",
    unsafe_name: str | None = None,
    include_pyproject: bool = True,
) -> None:
    compressed = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=compressed, mode="wb", filename="", mtime=1_700_000_000) as zipped,
        tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive,
    ):
        root = tarfile.TarInfo(ROOT)
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        root.mtime = member_mtime
        archive.addfile(root)

        metadata = tarfile.TarInfo(f"{ROOT}/PKG-INFO")
        metadata.mode = 0o644
        metadata.mtime = member_mtime
        metadata.size = len(pkg_info)
        archive.addfile(metadata, io.BytesIO(pkg_info))

        if include_pyproject:
            project_data = b"[build-system]\nrequires = []\n"
            project = tarfile.TarInfo(f"{ROOT}/pyproject.toml")
            project.mode = 0o644
            project.mtime = member_mtime
            project.size = len(project_data)
            archive.addfile(project, io.BytesIO(project_data))

        if unsafe_name is not None:
            unsafe = tarfile.TarInfo(unsafe_name)
            unsafe.mode = 0o644
            unsafe.mtime = member_mtime
            unsafe.size = 1
            archive.addfile(unsafe, io.BytesIO(b"x"))
    path.write_bytes(compressed.getvalue())


def _dist(tmp_path: Path, name: str, *, mtime: int = 1_700_000_000) -> Path:
    dist = tmp_path / name
    dist.mkdir()
    _write_wheel(dist / WHEEL, timestamp=(2023, 11, 14, 22, 13, 20))
    _write_sdist(dist / SDIST, member_mtime=mtime)
    return dist


def _write_package_matrix(tmp_path: Path) -> Path:
    global MATRIX_TRACKED_BUILD_LOCK
    root = tmp_path / "matrix"
    root.mkdir()
    for lane in PACKAGE_MATRIX_LANES:
        lane_python = lane.rsplit("-python-", 1)[-1] + ".0"
        lane_platform = lane.removeprefix("ctxc-openhands-").split("-python-", 1)[0]
        lane_path = root / lane
        core = lane_path / "dist" / "core"
        openhands = lane_path / "dist" / "openhands"
        wheelhouse = lane_path / reproducibility._BUILD_WHEELHOUSE_DIRECTORY
        core.mkdir(parents=True)
        openhands.mkdir()
        wheelhouse.mkdir()
        (core / MATRIX_ROOT_WHEEL).write_bytes(b"root-wheel\n")
        (openhands / MATRIX_INTEGRATION_WHEEL).write_bytes(b"integration-wheel\n")
        (openhands / MATRIX_INTEGRATION_SDIST).write_bytes(b"integration-sdist\n")
        for _name, _version, filename in MATRIX_BUILD_INPUTS:
            payload = f"{filename}\n".encode("ascii")
            (wheelhouse / filename).write_bytes(payload)
        _rewrite_matrix_build_lock(root, lane)
        lock_payload = (
            lane_path / reproducibility._BUILD_LOCK_SUPPORT_FILE
        ).read_bytes()
        lock_sha256 = hashlib.sha256(lock_payload).hexdigest()
        wheel_records = []
        for name, version, filename in MATRIX_BUILD_INPUTS:
            payload = (wheelhouse / filename).read_bytes()
            tags = ["py2-none-any", "py3-none-any"] if name == "colorama" else [
                "py3-none-any"
            ]
            wheel_records.append(
                {
                    "name": name,
                    "version": version,
                    "filename": filename,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "tags": tags,
                }
            )
        inventory_sha256 = hashlib.sha256(
            json.dumps(
                wheel_records,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        (lane_path / "doctor-source.json").write_text(
            '{"passed":true}\n',
            encoding="utf-8",
        )
        (lane_path / "doctor-live-source.json").write_text(
            '{"passed":false}\n',
            encoding="utf-8",
        )
        build_input_report = _self_hashed_report(
            {
                "schema": reproducibility._BUILD_INPUT_REPORT_SCHEMA,
                "revision": MATRIX_REVISION,
                "source_date_epoch": reproducibility._EXPECTED_SOURCE_DATE_EPOCH,
                "validation_only": True,
                "acquisition_performed": False,
                "network_action_performed": False,
                "semantic_completeness_claimed": False,
                "status": "passed",
                "passed": True,
                "lock": {
                    "filename": "requirements-build.lock",
                    "bytes": len(lock_payload),
                    "sha256": lock_sha256,
                    "record_count": 7,
                },
                "wheelhouse": {
                    "file_count": 7,
                    "aggregate_bytes": sum(item["bytes"] for item in wheel_records),
                    "inventory_sha256": inventory_sha256,
                    "wheels": wheel_records,
                },
                "builder": {
                    "verified": True,
                    "implementation": "CPython",
                    "python_version": lane_python,
                    "distributions": [
                        {"name": name, "version": version}
                        for name, version, _filename in MATRIX_BUILD_INPUTS
                    ],
                },
            }
        )
        (lane_path / "openhands-build-input-report.json").write_text(
            json.dumps(build_input_report, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        probe = {
            "core_version": "0.1.1a19",
            "integration_version": MATRIX_INTEGRATION_VERSION,
            "core_file": "/fixture/site-packages/context_compiler/__init__.py",
            "integration_file": "/fixture/site-packages/ctxc_openhands/__init__.py",
            "sys_prefix": "/fixture",
            "core_requirements": [
                'localai-contracts==0.2.0a2; extra == "unified"',
                'pytest>=8.0; extra == "dev"',
                'pytest-cov>=5.0; extra == "dev"',
                'ruff>=0.6; extra == "dev"',
                'setuptools>=77; extra == "dev"',
                'wheel>=0.41; extra == "dev"',
            ],
            "integration_requirements": [
                "loss-resistant-context-compiler==0.1.1a19",
                'openhands-ai==1.8.0; extra == "live"',
                'openhands-sdk==1.27.0; extra == "live"',
                'openhands-tools==1.27.0; extra == "live"',
                'openhands-agent-server==1.27.0; extra == "live"',
                'build>=1.2; extra == "dev"',
                'pytest>=8.0; extra == "dev"',
                'ruff>=0.6; extra == "dev"',
                'setuptools>=77; extra == "dev"',
                'wheel>=0.41; extra == "dev"',
            ],
            "openhands_modules_before": [],
            "openhands_modules_after_core": [],
            "openhands_modules_after_integration": [],
            "openhands_distributions_absent": sorted(
                [
                    "openhands-ai",
                    "openhands-sdk",
                    "openhands-tools",
                    "openhands-agent-server",
                ]
            ),
            "openhands_distributions_present": [],
            "openhands_import_spec_present": False,
            "user_site_enabled": False,
            "manifest_blocker": "hash-pinned-wheelhouse-absent",
            "manifest_file_sha256": "a" * 64,
            "packaged_manifest_present": True,
            "packaged_vectors_present": True,
        }
        clean_install_report = _self_hashed_report(
            {
                "schema": reproducibility._CLEAN_INSTALL_REPORT_SCHEMA,
                "python": lane_python,
                "platform": f"{lane_platform}-fixture",
                "core_wheel": MATRIX_ROOT_WHEEL,
                "core_wheel_sha256": hashlib.sha256(
                    (core / MATRIX_ROOT_WHEEL).read_bytes()
                ).hexdigest(),
                "passed": True,
                "build_lock": "requirements-build.lock",
                "build_lock_sha256": lock_sha256,
                "build_input_inventory_sha256": inventory_sha256,
                "build_input_count": 7,
                "live_dependencies_installed": False,
                "live_execution_claimed": False,
                "semantic_completeness_claimed": False,
                "modes": [
                    {
                        "mode": mode,
                        "artifact": (
                            MATRIX_INTEGRATION_WHEEL
                            if mode == "wheel"
                            else MATRIX_INTEGRATION_SDIST
                        ),
                        "artifact_sha256": hashlib.sha256(
                            (
                                openhands
                                / (
                                    MATRIX_INTEGRATION_WHEEL
                                    if mode == "wheel"
                                    else MATRIX_INTEGRATION_SDIST
                                )
                            ).read_bytes()
                        ).hexdigest(),
                        "probe": probe,
                        "doctor_manifest_sha256": "a" * 64,
                        "doctor_live_exit": 2,
                        "passed": True,
                        "build_lock_sha256": lock_sha256,
                        "build_input_inventory_sha256": inventory_sha256,
                        "builder_inventory_verified": True,
                    }
                    for mode in ("wheel", "sdist")
                ],
            }
        )
        (lane_path / "openhands-clean-install-report.json").write_text(
            json.dumps(clean_install_report, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (lane_path / "openhands-ci-toolchain.txt").write_text(
            f"{lane}\n",
            encoding="utf-8",
        )
    MATRIX_TRACKED_BUILD_LOCK = (
        root
        / PACKAGE_MATRIX_LANES[0]
        / reproducibility._BUILD_LOCK_SUPPORT_FILE
    ).read_bytes()
    return root


def _rewrite_matrix_build_lock(root: Path, lane: str) -> None:
    wheelhouse = root / lane / reproducibility._BUILD_WHEELHOUSE_DIRECTORY
    lock_lines = []
    for name, version, filename in MATRIX_BUILD_INPUTS:
        payload = (wheelhouse / filename).read_bytes()
        lock_lines.append(
            f"{name}=={version} --hash=sha256:{hashlib.sha256(payload).hexdigest()}\n"
        )
    (root / lane / reproducibility._BUILD_LOCK_SUPPORT_FILE).write_text(
        "".join(lock_lines),
        encoding="ascii",
        newline="\n",
    )


def _rebind_matrix_build_reports(root: Path, lane: str) -> None:
    lane_path = root / lane
    wheelhouse = lane_path / reproducibility._BUILD_WHEELHOUSE_DIRECTORY
    lock = lane_path / reproducibility._BUILD_LOCK_SUPPORT_FILE
    lock_payload = lock.read_bytes()
    lock_sha256 = hashlib.sha256(lock_payload).hexdigest()

    build_report_path = lane_path / "openhands-build-input-report.json"
    build_report = json.loads(build_report_path.read_text(encoding="utf-8"))
    build_report.pop("report_sha256")
    build_report["lock"]["bytes"] = len(lock_payload)
    build_report["lock"]["sha256"] = lock_sha256
    for item in build_report["wheelhouse"]["wheels"]:
        wheel = wheelhouse / item["filename"]
        payload = wheel.read_bytes()
        item["bytes"] = len(payload)
        item["sha256"] = hashlib.sha256(payload).hexdigest()
    build_report["wheelhouse"]["aggregate_bytes"] = sum(
        item["bytes"] for item in build_report["wheelhouse"]["wheels"]
    )
    build_report["wheelhouse"]["inventory_sha256"] = (
        reproducibility._canonical_sha256(
            build_report["wheelhouse"]["wheels"]
        )
    )
    build_report_path.write_text(
        json.dumps(_self_hashed_report(build_report), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    clean_report_path = lane_path / "openhands-clean-install-report.json"
    clean_report = json.loads(clean_report_path.read_text(encoding="utf-8"))
    clean_report.pop("report_sha256")
    clean_report["build_lock_sha256"] = lock_sha256
    clean_report["build_input_inventory_sha256"] = build_report["wheelhouse"][
        "inventory_sha256"
    ]
    for mode in clean_report["modes"]:
        mode["build_lock_sha256"] = lock_sha256
        mode["build_input_inventory_sha256"] = clean_report[
            "build_input_inventory_sha256"
        ]
    clean_report_path.write_text(
        json.dumps(_self_hashed_report(clean_report), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _matrix_artifact_path(root: Path, lane: str, kind: str) -> Path:
    if kind == "root_wheel":
        return root / lane / "dist" / "core" / MATRIX_ROOT_WHEEL
    if kind == "integration_wheel":
        return root / lane / "dist" / "openhands" / MATRIX_INTEGRATION_WHEEL
    if kind == "integration_sdist":
        return root / lane / "dist" / "openhands" / MATRIX_INTEGRATION_SDIST
    if kind == "build_lock":
        return root / lane / reproducibility._BUILD_LOCK_SUPPORT_FILE
    if kind.startswith("build_input_"):
        for name, _version, filename in MATRIX_BUILD_INPUTS:
            if kind == f"build_input_{name.replace('-', '_')}":
                return root / lane / reproducibility._BUILD_WHEELHOUSE_DIRECTORY / filename
    raise AssertionError(f"unsupported test artifact kind: {kind}")


def _assert_report_self_hash(report: dict[str, object]) -> None:
    unsigned = dict(report)
    claimed = unsigned.pop("report_sha256")
    encoded = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert claimed == hashlib.sha256(encoded).hexdigest()


def _rewrite_self_hashed_json(path: Path, mutate) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    report.pop("report_sha256")
    mutate(report)
    path.write_text(
        json.dumps(_self_hashed_report(report), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_project_version_rejects_oversize_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_bytes(b"x" * (reproducibility._MAX_PROJECT_METADATA_BYTES + 1))
    monkeypatch.setattr(
        os,
        "read",
        lambda *_args, **_kwargs: pytest.fail("oversized metadata was read"),
    )

    with pytest.raises(ReleaseReproducibilityError, match="outside the supported range"):
        reproducibility._project_version(project, label="test project metadata")


def test_project_version_returns_structured_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "1.0"\n', encoding="utf-8")

    def failed_read(_descriptor: int, _size: int) -> bytes:
        raise OSError("simulated read failure")

    monkeypatch.setattr(os, "read", failed_read)

    with pytest.raises(ReleaseReproducibilityError, match="could not be read"):
        reproducibility._project_version(project, label="test project metadata")


def test_project_version_rejects_short_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "1.0"\n', encoding="utf-8")
    monkeypatch.setattr(os, "read", lambda _descriptor, _size: b"")

    with pytest.raises(ReleaseReproducibilityError, match="declared bytes"):
        reproducibility._project_version(project, label="test project metadata")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"\xff", "not valid UTF-8 TOML"),
        (b"[project\n", "not valid UTF-8 TOML"),
        (b'[project]\nversion = "has space"\n', "invalid project version"),
    ],
)
def test_project_version_rejects_invalid_metadata(
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_bytes(payload)

    with pytest.raises(ReleaseReproducibilityError, match=message):
        reproducibility._project_version(project, label="test project metadata")


def test_project_version_bounds_parser_recursion(tmp_path: Path) -> None:
    project = tmp_path / "pyproject.toml"
    project.write_text(
        "[project]\nversion = " + ("[" * 1200) + '"1"' + ("]" * 1200),
        encoding="utf-8",
    )

    with pytest.raises(ReleaseReproducibilityError, match="not valid UTF-8 TOML"):
        reproducibility._project_version(project, label="test project metadata")


def test_configured_identity_rejects_core_public_version_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reproducibility, "_CORE_VERSION", "9.9.9")

    with pytest.raises(
        ReleaseReproducibilityError,
        match="project and public API versions do not match",
    ):
        reproducibility._configured_package_identity()


def test_release_reproducibility_passes_only_for_exact_archive_bytes(tmp_path: Path) -> None:
    first = _dist(tmp_path, "first")
    second = tmp_path / "second"
    second.mkdir()
    for artifact in first.iterdir():
        (second / artifact.name).write_bytes(artifact.read_bytes())

    report = compare_release_builds(first, second)

    assert report["status"] == "passed"
    assert all(item["byte_identical"] is True for item in report["artifacts"])
    _assert_report_self_hash(report)


def test_release_reproducibility_reports_sdist_tar_metadata_drift(tmp_path: Path) -> None:
    first = _dist(tmp_path, "first", mtime=1_700_000_000)
    second = _dist(tmp_path, "second", mtime=1_700_000_001)

    report = compare_release_builds(first, second)
    artifacts = {item["kind"]: item for item in report["artifacts"]}

    assert report["status"] == "failed"
    assert artifacts["wheel"]["byte_identical"] is True
    assert artifacts["sdist"]["byte_identical"] is False
    assert artifacts["sdist"]["first_mismatch"] == {
        "scope": "tar_metadata",
        "member": ROOT,
        "differing_fields": ["mtime"],
    }


def test_release_reproducibility_reports_wheel_metadata_drift(tmp_path: Path) -> None:
    first = _dist(tmp_path, "first")
    second = _dist(tmp_path, "second")
    _write_wheel(second / WHEEL, timestamp=(2023, 11, 14, 22, 13, 22))

    report = compare_release_builds(first, second)
    artifacts = {item["kind"]: item for item in report["artifacts"]}

    assert report["status"] == "failed"
    assert artifacts["sdist"]["byte_identical"] is True
    assert artifacts["wheel"]["first_mismatch"]["scope"] == "zip_metadata"
    assert artifacts["wheel"]["first_mismatch"]["differing_fields"] == ["date_time"]


def test_release_reproducibility_reports_member_content_drift(tmp_path: Path) -> None:
    first = _dist(tmp_path, "first")
    second = _dist(tmp_path, "second")
    _write_sdist(
        second / SDIST,
        member_mtime=1_700_000_000,
        pkg_info=b"Metadata-Version: 2.4\nName: substituted\n",
    )

    report = compare_release_builds(first, second)
    sdist = next(item for item in report["artifacts"] if item["kind"] == "sdist")

    assert report["status"] == "failed"
    assert sdist["first_mismatch"] == {
        "scope": "member_content",
        "member": f"{ROOT}/PKG-INFO",
        "differing_fields": ["size", "sha256"],
    }


def test_release_reproducibility_rejects_unsafe_sdist_members(tmp_path: Path) -> None:
    sdist = tmp_path / SDIST
    _write_sdist(
        sdist,
        member_mtime=1_700_000_000,
        unsafe_name=f"{ROOT}/../escape",
    )

    with pytest.raises(ReleaseReproducibilityError, match="unsafe member name"):
        _sdist_inventory(sdist)


def test_release_reproducibility_requires_standard_sdist_members(tmp_path: Path) -> None:
    sdist = tmp_path / SDIST
    _write_sdist(
        sdist,
        member_mtime=1_700_000_000,
        include_pyproject=False,
    )

    with pytest.raises(ReleaseReproducibilityError, match="pyproject.toml"):
        _sdist_inventory(sdist)


def test_release_reproducibility_cli_retains_failed_red_gate(tmp_path: Path) -> None:
    first = _dist(tmp_path, "first", mtime=1_700_000_000)
    second = _dist(tmp_path, "second", mtime=1_700_000_001)
    report_path = tmp_path / "reproducibility.json"
    arguments = [
        "--first-dist",
        str(first),
        "--second-dist",
        str(second),
        "--json-out",
        str(report_path),
    ]

    assert main(arguments) == 1
    retained = json.loads(report_path.read_text(encoding="utf-8"))
    assert retained["status"] == "failed"
    assert main(arguments) == 2
    assert json.loads(report_path.read_text(encoding="utf-8")) == retained


def test_release_reproducibility_cli_preserves_incomplete_legacy_parser_error(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "must-not-exist.json"

    with pytest.raises(SystemExit) as captured:
        main(
            [
                "--first-dist",
                str(tmp_path / "first"),
                "--json-out",
                str(report_path),
            ]
        )

    assert captured.value.code == 2
    assert not report_path.exists()


def test_release_reproducibility_cli_rejects_mixed_modes_without_report(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "must-not-exist.json"

    with pytest.raises(SystemExit) as captured:
        main(
            [
                "--matrix-root",
                str(tmp_path / "matrix"),
                "--revision",
                MATRIX_REVISION,
                "--upstream-result",
                "success",
                "--first-dist",
                str(tmp_path / "first"),
                "--json-out",
                str(report_path),
            ]
        )

    assert captured.value.code == 2
    assert not report_path.exists()


def test_package_matrix_requires_exact_six_lanes_and_self_hashes(
    tmp_path: Path,
) -> None:
    root = _write_package_matrix(tmp_path)

    report = compare_package_matrix(
        root,
        revision=MATRIX_REVISION,
        upstream_result="success",
    )

    assert report["schema"] == PACKAGE_MATRIX_REPORT_SCHEMA
    assert report["status"] == "passed"
    assert report["required_lanes"] == list(PACKAGE_MATRIX_LANES)
    assert report["package_identity"] == {
        "core_distribution": "loss-resistant-context-compiler",
        "core_version": "0.1.1a19",
        "root_wheel": MATRIX_ROOT_WHEEL,
        "integration_distribution": "ctxc-openhands",
        "integration_version": MATRIX_INTEGRATION_VERSION,
        "integration_wheel": MATRIX_INTEGRATION_WHEEL,
        "integration_sdist": MATRIX_INTEGRATION_SDIST,
        "build_lock": reproducibility._BUILD_LOCK_SUPPORT_FILE,
        "build_lock_sha256": hashlib.sha256(
            (
                root
                / PACKAGE_MATRIX_LANES[0]
                / reproducibility._BUILD_LOCK_SUPPORT_FILE
            ).read_bytes()
        ).hexdigest(),
        "build_input_count": "7",
    }
    assert [lane["lane"] for lane in report["lanes"]] == list(PACKAGE_MATRIX_LANES)
    assert len(report["artifact_groups"]) == 11
    assert all(group["byte_identical"] is True for group in report["artifact_groups"])
    _assert_report_self_hash(report)


@pytest.mark.parametrize(
    "kind",
    ["root_wheel", "integration_wheel", "integration_sdist"],
)
def test_package_matrix_reports_one_byte_drift_for_each_artifact_kind(
    tmp_path: Path,
    kind: str,
) -> None:
    root = _write_package_matrix(tmp_path)
    lane = PACKAGE_MATRIX_LANES[-1]
    changed = _matrix_artifact_path(root, lane, kind)
    changed.write_bytes(changed.read_bytes() + b"x")
    clean_report_path = root / lane / "openhands-clean-install-report.json"

    def bind_changed_artifact(report: dict[str, object]) -> None:
        digest = hashlib.sha256(changed.read_bytes()).hexdigest()
        if kind == "root_wheel":
            report["core_wheel_sha256"] = digest
        else:
            mode = "wheel" if kind == "integration_wheel" else "sdist"
            selected = next(item for item in report["modes"] if item["mode"] == mode)
            selected["artifact_sha256"] = digest

    _rewrite_self_hashed_json(clean_report_path, bind_changed_artifact)

    report = compare_package_matrix(
        root,
        revision=MATRIX_REVISION,
        upstream_result="success",
    )
    groups = {group["kind"]: group for group in report["artifact_groups"]}

    assert report["status"] == "failed"
    assert groups[kind]["byte_identical"] is False
    assert groups[kind]["lanes"][-1]["matches_reference_bytes"] is False
    assert all(
        group["byte_identical"] is True
        for other_kind, group in groups.items()
        if other_kind != kind
    )
    _assert_report_self_hash(report)


def test_package_matrix_rejects_cross_lane_build_input_and_lock_drift(
    tmp_path: Path,
) -> None:
    root = _write_package_matrix(tmp_path)
    lane = PACKAGE_MATRIX_LANES[-1]
    kind = "build_input_setuptools"
    changed = _matrix_artifact_path(root, lane, kind)
    changed.write_bytes(changed.read_bytes() + b"x")
    _rewrite_matrix_build_lock(root, lane)
    lock = _matrix_artifact_path(root, lane, "build_lock")
    build_report_path = root / lane / "openhands-build-input-report.json"
    build_report = json.loads(build_report_path.read_text(encoding="utf-8"))
    build_report.pop("report_sha256")
    build_report["lock"]["sha256"] = hashlib.sha256(lock.read_bytes()).hexdigest()
    changed_record = next(
        item
        for item in build_report["wheelhouse"]["wheels"]
        if item["name"] == "setuptools"
    )
    changed_record["bytes"] = len(changed.read_bytes())
    changed_record["sha256"] = hashlib.sha256(changed.read_bytes()).hexdigest()
    build_report["wheelhouse"]["aggregate_bytes"] = sum(
        item["bytes"] for item in build_report["wheelhouse"]["wheels"]
    )
    build_report["wheelhouse"]["inventory_sha256"] = reproducibility._canonical_sha256(
        build_report["wheelhouse"]["wheels"]
    )
    build_report_path.write_text(
        json.dumps(_self_hashed_report(build_report), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    clean_report_path = root / lane / "openhands-clean-install-report.json"
    clean_report = json.loads(clean_report_path.read_text(encoding="utf-8"))
    clean_report.pop("report_sha256")
    clean_report["build_lock_sha256"] = build_report["lock"]["sha256"]
    clean_report["build_input_inventory_sha256"] = build_report["wheelhouse"][
        "inventory_sha256"
    ]
    for mode in clean_report["modes"]:
        mode["build_lock_sha256"] = clean_report["build_lock_sha256"]
        mode["build_input_inventory_sha256"] = clean_report[
            "build_input_inventory_sha256"
        ]
    clean_report_path.write_text(
        json.dumps(_self_hashed_report(clean_report), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ReleaseReproducibilityError,
        match="does not match the tracked build lock",
    ):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_rejects_all_six_resealed_build_input_drifts(
    tmp_path: Path,
) -> None:
    root = _write_package_matrix(tmp_path)
    for lane in PACKAGE_MATRIX_LANES:
        changed = _matrix_artifact_path(
            root,
            lane,
            "build_input_setuptools",
        )
        changed.write_bytes(changed.read_bytes() + b"x")
        _rewrite_matrix_build_lock(root, lane)
        _rebind_matrix_build_reports(root, lane)

    with pytest.raises(
        ReleaseReproducibilityError,
        match="does not match the tracked build lock",
    ):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_rejects_build_input_digest_mismatch(
    tmp_path: Path,
) -> None:
    root = _write_package_matrix(tmp_path)
    changed = _matrix_artifact_path(
        root,
        PACKAGE_MATRIX_LANES[0],
        "build_input_build",
    )
    changed.write_bytes(changed.read_bytes() + b"x")

    with pytest.raises(ReleaseReproducibilityError, match="does not match its lock digest"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


@pytest.mark.parametrize("mutation", ["missing", "extra", "renamed"])
def test_package_matrix_rejects_build_wheelhouse_inventory_changes(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = _write_package_matrix(tmp_path)
    lane = root / PACKAGE_MATRIX_LANES[0]
    wheelhouse = lane / reproducibility._BUILD_WHEELHOUSE_DIRECTORY
    wheel = wheelhouse / MATRIX_BUILD_INPUTS[0][2]
    if mutation == "missing":
        wheel.unlink()
    elif mutation == "extra":
        (wheelhouse / "extra-1.0-py3-none-any.whl").write_bytes(b"extra")
    else:
        wheel.rename(wheelhouse / "renamed-1.0-py3-none-any.whl")

    with pytest.raises(ReleaseReproducibilityError, match="inventory mismatch"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.replace(b"\n", b"\r\n"),
        lambda payload: payload[:-1],
        lambda payload: payload + payload.splitlines(keepends=True)[0],
        lambda payload: b"".join(reversed(payload.splitlines(keepends=True))),
        lambda payload: payload.replace(b"--hash=sha256:", b"--hash=sha256:A", 1),
    ],
    ids=["crlf", "missing-final-lf", "duplicate", "reordered", "uppercase-hash"],
)
def test_package_matrix_rejects_noncanonical_build_lock(
    tmp_path: Path,
    mutation,
) -> None:
    root = _write_package_matrix(tmp_path)
    lock = _matrix_artifact_path(root, PACKAGE_MATRIX_LANES[0], "build_lock")
    lock.write_bytes(mutation(lock.read_bytes()))

    with pytest.raises(ReleaseReproducibilityError, match="build lock"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


@pytest.mark.parametrize(
    "mutation",
    ["wheel-extra", "wheel-tags", "builder-python", "lock-extra"],
)
def test_package_matrix_rejects_self_rehashed_build_report_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = _write_package_matrix(tmp_path)
    report_path = (
        root
        / PACKAGE_MATRIX_LANES[0]
        / "openhands-build-input-report.json"
    )

    def mutate(report: dict[str, object]) -> None:
        if mutation == "wheel-extra":
            report["wheelhouse"]["wheels"][0]["unexpected"] = True
        elif mutation == "wheel-tags":
            report["wheelhouse"]["wheels"][0]["tags"] = ["py2-none-any"]
        elif mutation == "builder-python":
            report["builder"]["python_version"] = "3.13.0"
        else:
            report["lock"]["unexpected"] = True

    _rewrite_self_hashed_json(report_path, mutate)

    with pytest.raises(
        ReleaseReproducibilityError,
        match="wheel record|builder inventory|lane lock",
    ):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "top-extra",
        "core-digest",
        "mode-digest",
        "probe-boundary",
        "doctor-digest",
    ],
)
def test_package_matrix_rejects_self_rehashed_clean_install_report_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = _write_package_matrix(tmp_path)
    report_path = (
        root
        / PACKAGE_MATRIX_LANES[0]
        / "openhands-clean-install-report.json"
    )

    def mutate(report: dict[str, object]) -> None:
        if mutation == "top-extra":
            report["unexpected"] = True
        elif mutation == "core-digest":
            report["core_wheel_sha256"] = "c" * 64
        elif mutation == "mode-digest":
            report["modes"][0]["artifact_sha256"] = "d" * 64
        elif mutation == "probe-boundary":
            report["modes"][0]["probe"]["openhands_modules_after_core"] = [
                "openhands"
            ]
        else:
            report["modes"][0]["doctor_manifest_sha256"] = "e" * 64

    _rewrite_self_hashed_json(report_path, mutate)

    with pytest.raises(
        ReleaseReproducibilityError,
        match="unexpected shape|does not match retained bytes|isolated package boundary",
    ):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_rejects_missing_lane(tmp_path: Path) -> None:
    root = _write_package_matrix(tmp_path)
    missing = root / PACKAGE_MATRIX_LANES[-1]
    for path in sorted(missing.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()
    missing.rmdir()

    with pytest.raises(ReleaseReproducibilityError, match="inventory mismatch"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_rejects_extra_lane(tmp_path: Path) -> None:
    root = _write_package_matrix(tmp_path)
    (root / "ctxc-openhands-Linux-python-3.11").mkdir()

    with pytest.raises(ReleaseReproducibilityError, match="inventory mismatch"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_bounds_directory_enumeration(tmp_path: Path) -> None:
    root = _write_package_matrix(tmp_path)
    for index in range(reproducibility._MAX_MATRIX_DIRECTORY_ENTRIES + 1):
        (root / f"extra-{index:03d}").mkdir()

    with pytest.raises(ReleaseReproducibilityError, match="too many entries"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_stops_directory_scan_at_the_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "matrix"
    root.mkdir()
    observed = 0

    class BoundedIterator:
        def __enter__(self) -> BoundedIterator:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def __iter__(self) -> BoundedIterator:
            return self

        def __next__(self) -> object:
            nonlocal observed
            observed += 1
            if observed > reproducibility._MAX_MATRIX_DIRECTORY_ENTRIES + 1:
                raise AssertionError("directory iterator read beyond the bound")
            return object()

    monkeypatch.setattr(os, "scandir", lambda _path: BoundedIterator())

    with pytest.raises(ReleaseReproducibilityError, match="too many entries"):
        reproducibility._matrix_directory_entries(root, label="bounded test root")
    assert observed == reproducibility._MAX_MATRIX_DIRECTORY_ENTRIES + 1


def test_package_matrix_rejects_directory_replacement_during_enumeration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "matrix"
    root.mkdir()
    (root / "entry.txt").write_text("x", encoding="utf-8")
    original_lstat = Path.lstat
    root_inspections = 0

    def replaced_lstat(path: Path) -> os.stat_result:
        nonlocal root_inspections
        value = original_lstat(path)
        if path == root:
            root_inspections += 1
            if root_inspections > 1:
                fields = list(value)
                fields[1] += 1
                return os.stat_result(fields)
        return value

    monkeypatch.setattr(Path, "lstat", replaced_lstat)

    with pytest.raises(ReleaseReproducibilityError, match="changed while being enumerated"):
        reproducibility._matrix_directory_entries(root, label="replacement test root")


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_package_matrix_rejects_missing_or_extra_lane_file(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = _write_package_matrix(tmp_path)
    lane = root / PACKAGE_MATRIX_LANES[0]
    if mutation == "missing":
        (lane / "doctor-source.json").unlink()
    else:
        (lane / "unexpected.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ReleaseReproducibilityError, match="inventory mismatch"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_rejects_integration_cross_format_version_mismatch(
    tmp_path: Path,
) -> None:
    root = _write_package_matrix(tmp_path)
    for lane_name in PACKAGE_MATRIX_LANES:
        lane = root / lane_name / "dist" / "openhands"
        (lane / MATRIX_INTEGRATION_WHEEL).rename(lane / "ctxc_openhands-2.0-py3-none-any.whl")
        (lane / MATRIX_INTEGRATION_SDIST).rename(lane / "ctxc_openhands-3.0.tar.gz")

    with pytest.raises(
        ReleaseReproducibilityError,
        match="configured integration wheel and sdist",
    ):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_rejects_wrong_root_version_in_every_lane(
    tmp_path: Path,
) -> None:
    root = _write_package_matrix(tmp_path)
    for lane_name in PACKAGE_MATRIX_LANES:
        lane = root / lane_name / "dist" / "core"
        (lane / MATRIX_ROOT_WHEEL).rename(
            lane / "loss_resistant_context_compiler-9.9.9-py3-none-any.whl"
        )

    with pytest.raises(
        ReleaseReproducibilityError,
        match="configured artifact filename",
    ):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def _symlink_or_skip(target: Path, link: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable in this test environment: {exc}")


def test_package_matrix_rejects_symbolic_link_file(tmp_path: Path) -> None:
    root = _write_package_matrix(tmp_path)
    artifact = _matrix_artifact_path(
        root,
        PACKAGE_MATRIX_LANES[0],
        "root_wheel",
    )
    target = tmp_path / "outside.whl"
    artifact.replace(target)
    _symlink_or_skip(target, artifact)

    with pytest.raises(ReleaseReproducibilityError, match="linked or reparse"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_rejects_symbolic_link_directory(tmp_path: Path) -> None:
    root = _write_package_matrix(tmp_path)
    lane = root / PACKAGE_MATRIX_LANES[0]
    target = tmp_path / "outside-lane"
    lane.replace(target)
    _symlink_or_skip(target, lane, directory=True)

    with pytest.raises(ReleaseReproducibilityError, match="linked or reparse"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_package_matrix_rejects_simulated_symbolic_link_cross_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    root = _write_package_matrix(tmp_path)
    linked_path = (
        _matrix_artifact_path(root, PACKAGE_MATRIX_LANES[0], "root_wheel")
        if kind == "file"
        else root / PACKAGE_MATRIX_LANES[0] / "dist"
    )
    original_lstat = Path.lstat

    def simulated_lstat(path: Path) -> os.stat_result:
        value = original_lstat(path)
        if path == linked_path:
            fields = list(value)
            fields[0] = stat.S_IFLNK | 0o777
            return os.stat_result(fields)
        return value

    monkeypatch.setattr(Path, "lstat", simulated_lstat)

    with pytest.raises(ReleaseReproducibilityError, match="linked or reparse"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_rejects_hard_linked_file(tmp_path: Path) -> None:
    root = _write_package_matrix(tmp_path)
    artifact = _matrix_artifact_path(
        root,
        PACKAGE_MATRIX_LANES[0],
        "root_wheel",
    )
    try:
        os.link(artifact, tmp_path / "second-link.whl")
    except OSError as exc:
        pytest.skip(f"hard links are unavailable in this test environment: {exc}")

    with pytest.raises(ReleaseReproducibilityError, match="multiply linked"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_rejects_simulated_reparse_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _write_package_matrix(tmp_path)
    reparse_path = root / PACKAGE_MATRIX_LANES[0] / "dist"
    original = reproducibility._is_reparse_or_junction

    def simulated_reparse(path: Path, value: os.stat_result) -> bool:
        return path == reparse_path or original(path, value)

    monkeypatch.setattr(
        reproducibility,
        "_is_reparse_or_junction",
        simulated_reparse,
    )

    with pytest.raises(ReleaseReproducibilityError, match="linked or reparse"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_rejects_oversized_support_file(tmp_path: Path) -> None:
    root = _write_package_matrix(tmp_path)
    support = root / PACKAGE_MATRIX_LANES[0] / "openhands-ci-toolchain.txt"
    support.write_bytes(b"x" * (reproducibility._MAX_MATRIX_SUPPORT_BYTES + 1))

    with pytest.raises(ReleaseReproducibilityError, match="byte limit"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_rejects_empty_support_file(tmp_path: Path) -> None:
    root = _write_package_matrix(tmp_path)
    support = root / PACKAGE_MATRIX_LANES[0] / "doctor-source.json"
    support.write_bytes(b"")

    with pytest.raises(ReleaseReproducibilityError, match="cannot be empty"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_rejects_total_byte_overflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _write_package_matrix(tmp_path)
    monkeypatch.setattr(reproducibility, "_MAX_MATRIX_TOTAL_BYTES", 1)
    monkeypatch.setattr(
        reproducibility,
        "_hash_matrix_file",
        lambda *_args, **_kwargs: pytest.fail(
            "matrix file hashing started after the stat exceeded remaining bytes"
        ),
    )

    with pytest.raises(ReleaseReproducibilityError, match="total byte limit"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_rejects_replacement_between_validation_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _write_package_matrix(tmp_path)
    original = reproducibility._exact_matrix_file_equal
    replaced = False

    def replace_after_comparison(
        first: Path,
        second: Path,
        *,
        expected_bytes: int,
        first_snapshot: tuple[int, ...],
        second_snapshot: tuple[int, ...],
    ) -> bool:
        nonlocal replaced
        result = original(
            first,
            second,
            expected_bytes=expected_bytes,
            first_snapshot=first_snapshot,
            second_snapshot=second_snapshot,
        )
        if not replaced:
            second.write_bytes(second.read_bytes() + b"x")
            replaced = True
        return result

    monkeypatch.setattr(
        reproducibility,
        "_exact_matrix_file_equal",
        replace_after_comparison,
    )

    with pytest.raises(
        ReleaseReproducibilityError,
        match="changed between validation passes|does not match retained bytes",
    ):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="success",
        )


def test_package_matrix_rejects_precomparison_link_without_reading_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.whl"
    second = tmp_path / "second.whl"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    first_snapshot = reproducibility._path_stat_snapshot(first.lstat())
    second_snapshot = reproducibility._path_stat_snapshot(second.lstat())
    original_lstat = Path.lstat

    def simulated_lstat(path: Path) -> os.stat_result:
        value = original_lstat(path)
        if path == second:
            fields = list(value)
            fields[0] = stat.S_IFLNK | 0o777
            return os.stat_result(fields)
        return value

    monkeypatch.setattr(Path, "lstat", simulated_lstat)
    monkeypatch.setattr(
        os,
        "read",
        lambda *_args, **_kwargs: pytest.fail(
            "replacement target was read before fail-closed rejection"
        ),
    )

    with pytest.raises(
        ReleaseReproducibilityError,
        match="changed before exact comparison",
    ):
        reproducibility._exact_matrix_file_equal(
            first,
            second,
            expected_bytes=4,
            first_snapshot=first_snapshot,
            second_snapshot=second_snapshot,
        )


def test_package_matrix_rejects_lstat_to_descriptor_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.whl"
    artifact.write_bytes(b"same")
    expected_snapshot = reproducibility._path_stat_snapshot(artifact.lstat())

    @contextmanager
    def mismatched_open(
        path: Path,
        *,
        label: str,
    ) -> Iterator[tuple[int, os.stat_result]]:
        del label
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0),
        )
        try:
            fields = list(os.fstat(descriptor))
            fields[1] += 1
            yield descriptor, os.stat_result(fields)
        finally:
            os.close(descriptor)

    monkeypatch.setattr(
        reproducibility,
        "_open_regular_file",
        mismatched_open,
    )

    with (
        pytest.raises(
            ReleaseReproducibilityError,
            match="changed while opening for exact comparison",
        ),
        reproducibility._open_matrix_regular_file(
            artifact,
            expected_snapshot=expected_snapshot,
            label="mismatch test artifact",
        ),
    ):
        pytest.fail("descriptor mismatch was accepted")


def test_package_matrix_rejects_post_open_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.whl"
    second = tmp_path / "second.whl"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    first_snapshot = reproducibility._path_stat_snapshot(first.lstat())
    second_snapshot = reproducibility._path_stat_snapshot(second.lstat())
    original_lstat = Path.lstat
    original_read = os.read
    comparison_started = False

    def observed_read(descriptor: int, size: int) -> bytes:
        nonlocal comparison_started
        block = original_read(descriptor, size)
        comparison_started = True
        return block

    def replaced_lstat(path: Path) -> os.stat_result:
        value = original_lstat(path)
        if path == second and comparison_started:
            fields = list(value)
            fields[1] += 1
            return os.stat_result(fields)
        return value

    monkeypatch.setattr(os, "read", observed_read)
    monkeypatch.setattr(Path, "lstat", replaced_lstat)

    with pytest.raises(
        ReleaseReproducibilityError,
        match="changed during exact comparison",
    ):
        reproducibility._exact_matrix_file_equal(
            first,
            second,
            expected_bytes=4,
            first_snapshot=first_snapshot,
            second_snapshot=second_snapshot,
        )


def test_package_matrix_rejects_post_open_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.whl"
    second = tmp_path / "second.whl"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    first_snapshot = reproducibility._path_stat_snapshot(first.lstat())
    second_snapshot = reproducibility._path_stat_snapshot(second.lstat())
    original_lstat = Path.lstat
    original_read = os.read
    comparison_started = False

    def observed_read(descriptor: int, size: int) -> bytes:
        nonlocal comparison_started
        block = original_read(descriptor, size)
        comparison_started = True
        return block

    def unlinked_lstat(path: Path) -> os.stat_result:
        if path == second and comparison_started:
            raise FileNotFoundError(path)
        return original_lstat(path)

    monkeypatch.setattr(os, "read", observed_read)
    monkeypatch.setattr(Path, "lstat", unlinked_lstat)

    with pytest.raises(
        ReleaseReproducibilityError,
        match="changed after exact comparison",
    ):
        reproducibility._exact_matrix_file_equal(
            first,
            second,
            expected_bytes=4,
            first_snapshot=first_snapshot,
            second_snapshot=second_snapshot,
        )


def test_package_matrix_returns_structured_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.whl"
    second = tmp_path / "second.whl"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    first_snapshot = reproducibility._path_stat_snapshot(first.lstat())
    second_snapshot = reproducibility._path_stat_snapshot(second.lstat())

    def failed_read(_descriptor: int, _size: int) -> bytes:
        raise OSError("simulated read failure")

    monkeypatch.setattr(os, "read", failed_read)

    with pytest.raises(
        ReleaseReproducibilityError,
        match="could not be read during exact comparison",
    ):
        reproducibility._exact_matrix_file_equal(
            first,
            second,
            expected_bytes=4,
            first_snapshot=first_snapshot,
            second_snapshot=second_snapshot,
        )


def test_package_matrix_retains_upstream_failure(tmp_path: Path) -> None:
    root = _write_package_matrix(tmp_path)

    report = compare_package_matrix(
        root,
        revision=MATRIX_REVISION,
        upstream_result="failure",
    )

    assert report["status"] == "failed"
    assert all(group["byte_identical"] is True for group in report["artifact_groups"])
    assert report["issues"] == ["offline-package matrix result was 'failure', not 'success'"]
    _assert_report_self_hash(report)


def test_package_matrix_rejects_unknown_upstream_result(tmp_path: Path) -> None:
    root = _write_package_matrix(tmp_path)

    with pytest.raises(ReleaseReproducibilityError, match="upstream result is invalid"):
        compare_package_matrix(
            root,
            revision=MATRIX_REVISION,
            upstream_result="unknown",
        )


def test_package_matrix_cli_writes_once_and_neutralizes_input_path(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-matrix"
    report_path = tmp_path / "package-matrix-report.json"
    arguments = [
        "--matrix-root",
        str(missing),
        "--revision",
        MATRIX_REVISION,
        "--upstream-result",
        "failure",
        "--json-out",
        str(report_path),
    ]

    assert main(arguments) == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema"] == PACKAGE_MATRIX_REPORT_SCHEMA
    assert report["status"] == "failed"
    assert str(tmp_path) not in json.dumps(report)
    _assert_report_self_hash(report)
    assert main(arguments) == 2
    assert json.loads(report_path.read_text(encoding="utf-8")) == report


def test_package_matrix_cli_neutralizes_invalid_revision_value(tmp_path: Path) -> None:
    root = _write_package_matrix(tmp_path)
    report_path = tmp_path / "invalid-revision-report.json"
    private_revision = str(tmp_path) + ("x" * 2048)

    assert (
        main(
            [
                "--matrix-root",
                str(root),
                "--revision",
                private_revision,
                "--upstream-result",
                "success",
                "--json-out",
                str(report_path),
            ]
        )
        == 1
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["revision"] == "invalid"
    assert str(tmp_path) not in json.dumps(report)
    assert len(report["issues"][0]) <= reproducibility._MAX_MATRIX_ISSUE_CHARS
    _assert_report_self_hash(report)


def test_package_matrix_cli_preserves_relative_root_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    report_path = Path("relative-root-report.json")

    assert (
        main(
            [
                "--matrix-root",
                ".",
                "--revision",
                MATRIX_REVISION,
                "--upstream-result",
                "success",
                "--json-out",
                str(report_path),
            ]
        )
        == 1
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "package matrix root inventory mismatch" in report["issues"][0]
    assert "<matrix-root>" not in report["issues"][0]
    _assert_report_self_hash(report)


def test_openhands_workflow_aggregates_six_package_lanes_independently() -> None:
    workflow = (
        Path(__file__).parents[1] / ".github" / "workflows" / "openhands-integration.yml"
    ).read_text(encoding="utf-8")
    job = workflow.split("  package-byte-reproducibility:\n", 1)[1].split(
        "  retained-offline-evidence:\n",
        1,
    )[0]

    for required_filter in (
        ".gitattributes",
        "MANIFEST.in",
        "README.md",
        "benchmarks/**",
        "schemas/**",
        "scripts/release_artifact_manifest.py",
        "scripts/release_reproducibility.py",
        "tests/test_release_reproducibility.py",
    ):
        assert workflow.count(f'      - "{required_filter}"') == 2
    assert "    if: ${{ always() }}" in job
    assert "    needs: [offline-package]" in job
    assert "retained-offline-evidence" not in job
    assert ("actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c") in job
    assert "pattern: ctxc-openhands-*-python-*" in job
    assert "merge-multiple: false" in job
    assert '--upstream-result "${{ needs.offline-package.result }}"' in job
    assert "if-no-files-found: error" in job
    assert "continue-on-error" not in job
