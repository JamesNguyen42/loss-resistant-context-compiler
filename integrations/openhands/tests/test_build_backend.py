from __future__ import annotations

import gzip
import importlib.util
import io
import sys
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = PROJECT_ROOT / "_ctxc_openhands_build_backend.py"
ARCHIVE_ROOT = "ctxc_openhands-0.1.0a18"
ARCHIVE_NAME = f"{ARCHIVE_ROOT}.tar.gz"
EPOCH = 1_700_000_000


def _load_backend() -> ModuleType:
    module_name = "_ctxc_openhands_build_backend_under_test"
    spec = importlib.util.spec_from_file_location(module_name, BACKEND_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


BACKEND = _load_backend()


def _add_directory(
    archive: tarfile.TarFile,
    name: str,
    *,
    mtime: float,
) -> None:
    member = tarfile.TarInfo(name)
    member.type = tarfile.DIRTYPE
    member.mode = 0o755
    member.mtime = mtime
    member.pax_headers = {"mtime": str(mtime)}
    archive.addfile(member)


def _add_file(
    archive: tarfile.TarFile,
    name: str,
    content: bytes,
    *,
    mtime: float,
) -> None:
    member = tarfile.TarInfo(name)
    member.mode = 0o644
    member.mtime = mtime
    member.size = len(content)
    member.pax_headers = {"mtime": str(mtime)}
    archive.addfile(member, io.BytesIO(content))


def _write_raw_sdist(
    path: Path,
    *,
    gzip_mtime: int,
    member_mtime: float,
    package_content: bytes = b'VALUE = "fixture"\n',
) -> None:
    with (
        path.open("xb") as raw_output,
        gzip.GzipFile(
            filename=f"raw-{gzip_mtime}.tar",
            mode="wb",
            fileobj=raw_output,
            mtime=gzip_mtime,
        ) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive,
    ):
        _add_directory(archive, ARCHIVE_ROOT, mtime=member_mtime)
        _add_file(
            archive,
            f"{ARCHIVE_ROOT}/PKG-INFO",
            b"Metadata-Version: 2.4\nName: ctxc-openhands\nVersion: 0.1.0a18\n",
            mtime=member_mtime,
        )
        _add_file(
            archive,
            f"{ARCHIVE_ROOT}/pyproject.toml",
            b"[build-system]\nrequires = []\n",
            mtime=member_mtime,
        )
        _add_file(
            archive,
            f"{ARCHIVE_ROOT}/module.py",
            package_content,
            mtime=member_mtime,
        )


def _raw_archive(
    tmp_path: Path,
    directory_name: str,
    *,
    gzip_mtime: int,
    member_mtime: float,
    package_content: bytes = b'VALUE = "fixture"\n',
) -> Path:
    directory = tmp_path / directory_name
    directory.mkdir()
    path = directory / ARCHIVE_NAME
    _write_raw_sdist(
        path,
        gzip_mtime=gzip_mtime,
        member_mtime=member_mtime,
        package_content=package_content,
    )
    return path


def test_integration_backend_normalizes_timestamp_variants_exactly(
    tmp_path: Path,
) -> None:
    first = _raw_archive(
        tmp_path,
        "first",
        gzip_mtime=EPOCH + 1,
        member_mtime=EPOCH + 1.125,
    )
    second = _raw_archive(
        tmp_path,
        "second",
        gzip_mtime=EPOCH + 2,
        member_mtime=EPOCH + 2.875,
    )

    BACKEND._normalize_sdist_archive(first, EPOCH)
    BACKEND._normalize_sdist_archive(second, EPOCH)

    expected = first.read_bytes()
    assert second.read_bytes() == expected
    assert int.from_bytes(expected[4:8], "little") == EPOCH
    with tarfile.open(first, mode="r:gz") as archive:
        members = archive.getmembers()
    assert members
    assert all(member.mtime == EPOCH for member in members)
    assert all("mtime" not in member.pax_headers for member in members)

    BACKEND._normalize_sdist_archive(first, EPOCH)
    assert first.read_bytes() == expected


def test_integration_backend_preserves_payload_distinctions(tmp_path: Path) -> None:
    first = _raw_archive(
        tmp_path,
        "first",
        gzip_mtime=EPOCH + 1,
        member_mtime=EPOCH + 1.25,
        package_content=b"first\n",
    )
    second = _raw_archive(
        tmp_path,
        "second",
        gzip_mtime=EPOCH + 2,
        member_mtime=EPOCH + 2.75,
        package_content=b"second\n",
    )

    BACKEND._normalize_sdist_archive(first, EPOCH)
    BACKEND._normalize_sdist_archive(second, EPOCH)

    assert first.read_bytes() != second.read_bytes()


def test_build_sdist_without_epoch_delegates_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: list[tuple[str, object]] = []

    def build_sdist(directory: str, *, config_settings: object) -> str:
        observed.append((directory, config_settings))
        return "raw-result.tar.gz"

    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    monkeypatch.setattr(BACKEND._setuptools_backend, "build_sdist", build_sdist)
    settings = {"tag-date": "false"}

    assert BACKEND.build_sdist(str(tmp_path), settings) == "raw-result.tar.gz"
    assert observed == [(str(tmp_path), settings)]


def test_non_sdist_hook_delegates_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: list[tuple[str, object, object]] = []
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)

    def build_wheel(
        directory: str,
        config_settings: object = None,
        metadata_directory: object = None,
    ) -> str:
        observed.append((directory, config_settings, metadata_directory))
        return "ctxc_openhands-0.1.0a18-py3-none-any.whl"

    monkeypatch.setattr(BACKEND._setuptools_backend, "build_wheel", build_wheel)
    settings = {"tag-date": "false"}

    assert BACKEND.build_wheel(str(tmp_path), settings, "metadata") == (
        "ctxc_openhands-0.1.0a18-py3-none-any.whl"
    )
    assert observed == [(str(tmp_path), settings, "metadata")]


def test_invalid_epoch_fails_before_setuptools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def unexpected_build(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("Setuptools must not run for an invalid epoch")

    monkeypatch.setenv("SOURCE_DATE_EPOCH", "not-an-integer")
    monkeypatch.setattr(BACKEND._setuptools_backend, "build_sdist", unexpected_build)

    with pytest.raises(
        BACKEND.DeterministicSdistError,
        match="SOURCE_DATE_EPOCH must be a decimal integer",
    ):
        BACKEND.build_sdist(str(tmp_path))


def test_unsafe_setuptools_filename_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", str(EPOCH))
    monkeypatch.setattr(
        BACKEND._setuptools_backend,
        "build_sdist",
        lambda *_args, **_kwargs: "../escaped.tar.gz",
    )

    with pytest.raises(
        BACKEND.DeterministicSdistError,
        match="unsafe sdist filename",
    ):
        BACKEND.build_sdist(str(tmp_path))
