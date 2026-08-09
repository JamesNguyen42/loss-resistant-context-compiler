from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

import pytest

from scripts.release_reproducibility import (
    ReleaseReproducibilityError,
    _sdist_inventory,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATE_EPOCH = 1_700_000_000


def _git_paths(*arguments: str) -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", "ls-files", *arguments, "-z"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    values = completed.stdout.split(b"\0")
    assert values[-1] == b""
    decoded = tuple(value.decode("utf-8", "strict") for value in values[:-1])
    assert len(decoded) == len(set(decoded))
    return decoded


def _canonical_parts(value: str) -> tuple[str, ...]:
    assert value
    assert "\\" not in value
    assert not value.startswith("/")
    path = PurePosixPath(value)
    assert path.as_posix() == value
    assert all(part not in {"", ".", ".."} for part in path.parts)
    return path.parts


def _is_reparse(path_stat: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(path_stat, "st_file_attributes", 0) & flag)


def _tracked_source(target: Path) -> tuple[Path, tuple[str, ...]]:
    source = target / "source"
    source.mkdir()
    for value in _git_paths():
        parts = _canonical_parts(value)
        original = ROOT.joinpath(*parts)
        original_stat = original.lstat()
        assert stat.S_ISREG(original_stat.st_mode)
        assert not stat.S_ISLNK(original_stat.st_mode)
        assert not _is_reparse(original_stat)
        destination = source.joinpath(*parts)
        assert source in destination.parents
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
    return source, _git_paths("--others", "--exclude-standard")


def _build_sdist(source: Path, output: Path) -> Path:
    output.mkdir()
    environment = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONPYCACHEPREFIX"):
        environment.pop(name, None)
    environment.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "SOURCE_DATE_EPOCH": str(SOURCE_DATE_EPOCH),
        }
    )
    script = (
        "import _ctxc_build_backend as backend\n"
        f"print(backend.build_sdist({str(output)!r}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=source,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode == 0, (
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    sdists = tuple(output.glob("loss_resistant_context_compiler-*.tar.gz"))
    assert len(sdists) == 1
    return sdists[0]


def _safe_extract(sdist: Path, target: Path) -> Path:
    _sdist_inventory(sdist)
    target.mkdir()
    with tarfile.open(sdist, mode="r:gz") as archive:
        archive.extractall(target, filter="data")
    roots = tuple(target.iterdir())
    assert len(roots) == 1
    root = roots[0]
    assert root.name == sdist.name.removesuffix(".tar.gz")
    root_stat = root.lstat()
    assert stat.S_ISDIR(root_stat.st_mode)
    assert not stat.S_ISLNK(root_stat.st_mode)
    assert not _is_reparse(root_stat)
    assert not (hasattr(os.path, "isjunction") and os.path.isjunction(root))
    resolved_root = root.resolve(strict=True)
    assert resolved_root.parent == target.resolve(strict=True)
    for path in resolved_root.rglob("*"):
        path_stat = path.lstat()
        assert not stat.S_ISLNK(path_stat.st_mode)
        assert not _is_reparse(path_stat)
        assert not (hasattr(os.path, "isjunction") and os.path.isjunction(path))
        assert stat.S_ISDIR(path_stat.st_mode) or stat.S_ISREG(path_stat.st_mode)
        assert resolved_root in path.resolve(strict=True).parents
    return resolved_root


def _single_name(names: list[str], suffix: str) -> str:
    matches = [name for name in names if name.endswith(suffix)]
    assert len(matches) == 1
    return matches[0]


def test_root_sdist_is_an_exact_extracted_rebuild_fixed_point(
    tmp_path: Path,
) -> None:
    source, untracked = _tracked_source(tmp_path)
    first = _build_sdist(source, tmp_path / "dist-first")
    extracted = _safe_extract(first, tmp_path / "source-roundtrip")
    roundtrip = _build_sdist(extracted, tmp_path / "dist-roundtrip")

    expected = first.read_bytes()
    assert first.name == roundtrip.name
    assert roundtrip.read_bytes() == expected
    assert int.from_bytes(expected[4:8], "little") == SOURCE_DATE_EPOCH

    first_inventory = _sdist_inventory(first)
    assert _sdist_inventory(roundtrip) == first_inventory
    with tarfile.open(first, mode="r:gz") as archive:
        members = archive.getmembers()
        names = archive.getnames()
        setup_name = _single_name(names, "/setup.cfg")
        sources_name = _single_name(
            names,
            "/src/loss_resistant_context_compiler.egg-info/SOURCES.txt",
        )
        setup_member = archive.getmember(setup_name)
        sources_member = archive.getmember(sources_name)
        setup_stream = archive.extractfile(setup_member)
        sources_stream = archive.extractfile(sources_member)
        assert setup_member.isfile()
        assert sources_member.isfile()
        assert setup_stream is not None
        assert sources_stream is not None
        setup_payload = setup_stream.read()
        sources_payload = sources_stream.read()

    assert b"\r" not in setup_payload
    assert setup_payload.startswith(b"[egg_info]\n")
    assert b"\r" not in sources_payload
    sources_lines = sources_payload.decode("utf-8", "strict").splitlines()
    assert len(sources_lines) == len(set(sources_lines))
    assert all(line.lstrip("./") != "setup.cfg" for line in sources_lines)
    archive_root = first.name.removesuffix(".tar.gz")
    assert all(f"{archive_root}/{path}" not in names for path in untracked)
    assert members
    assert all(member.mtime == SOURCE_DATE_EPOCH for member in members)
    assert all("mtime" not in member.pax_headers for member in members)
    assert all(
        member.mode == (0o755 if member.isdir() else 0o644)
        for member in members
    )


def test_roundtrip_preflight_rejects_a_link_before_extraction(
    tmp_path: Path,
) -> None:
    root_name = "loss_resistant_context_compiler-0.1.0"
    sdist = tmp_path / f"{root_name}.tar.gz"
    with tarfile.open(sdist, mode="w:gz") as archive:
        root = tarfile.TarInfo(root_name)
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        for name, payload in (
            ("PKG-INFO", b"Metadata-Version: 2.4\nName: example\n"),
            ("pyproject.toml", b"[build-system]\n"),
        ):
            member = tarfile.TarInfo(f"{root_name}/{name}")
            member.size = len(payload)
            archive.addfile(member, fileobj=io.BytesIO(payload))
        linked = tarfile.TarInfo(f"{root_name}/linked")
        linked.type = tarfile.SYMTYPE
        linked.linkname = "PKG-INFO"
        archive.addfile(linked)

    extraction = tmp_path / "extraction"
    with pytest.raises(ReleaseReproducibilityError, match="link or special"):
        _safe_extract(sdist, extraction)
    assert not extraction.exists()
