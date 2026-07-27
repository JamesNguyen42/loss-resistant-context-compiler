from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.release_reproducibility import (
    ReleaseReproducibilityError,
    _sdist_inventory,
    compare_release_builds,
    main,
)

WHEEL = "loss_resistant_context_compiler-0.1.0-py3-none-any.whl"
SDIST = "loss_resistant_context_compiler-0.1.0.tar.gz"
ROOT = "loss_resistant_context_compiler-0.1.0"


def _write_wheel(path: Path, *, timestamp: tuple[int, int, int, int, int, int]) -> None:
    entries = {
        "context_compiler/__init__.py": b'__version__ = "0.1.0"\n',
        "loss_resistant_context_compiler-0.1.0.dist-info/METADATA": (
            b"Metadata-Version: 2.4\nName: loss-resistant-context-compiler\nVersion: 0.1.0\n"
        ),
        "loss_resistant_context_compiler-0.1.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        "loss_resistant_context_compiler-0.1.0.dist-info/RECORD": b"",
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


def test_release_reproducibility_passes_only_for_exact_archive_bytes(tmp_path: Path) -> None:
    first = _dist(tmp_path, "first")
    second = tmp_path / "second"
    second.mkdir()
    for artifact in first.iterdir():
        (second / artifact.name).write_bytes(artifact.read_bytes())

    report = compare_release_builds(first, second)

    assert report["status"] == "passed"
    assert all(item["byte_identical"] is True for item in report["artifacts"])
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
