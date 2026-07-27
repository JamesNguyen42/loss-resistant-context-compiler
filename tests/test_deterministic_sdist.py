from __future__ import annotations

import gzip
import io
import os
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import _ctxc_build_backend as backend
from _ctxc_build_backend import (
    DeterministicSdistError,
    _normalize_sdist_archive,
    _parse_source_date_epoch,
)

ROOT = "loss_resistant_context_compiler-0.1.0"
SDIST = f"{ROOT}.tar.gz"
EPOCH = 1_700_000_000


def _add_directory(
    archive: tarfile.TarFile,
    name: str,
    *,
    mtime: float,
    pax_headers: dict[str, str] | None = None,
) -> None:
    member = tarfile.TarInfo(name)
    member.type = tarfile.DIRTYPE
    member.mode = 0o755
    member.mtime = mtime
    member.pax_headers = dict(pax_headers or {})
    archive.addfile(member)


def _add_file(
    archive: tarfile.TarFile,
    name: str,
    content: bytes,
    *,
    mtime: float,
    pax_headers: dict[str, str] | None = None,
) -> None:
    member = tarfile.TarInfo(name)
    member.mode = 0o644
    member.mtime = mtime
    member.size = len(content)
    member.pax_headers = dict(pax_headers or {})
    archive.addfile(member, io.BytesIO(content))


def _write_raw_sdist(
    path: Path,
    *,
    gzip_mtime: int,
    member_mtime: float,
    package_content: bytes = b'VALUE = "fixture"\n',
    unsafe_name: str | None = None,
    duplicate: bool = False,
    link: bool = False,
    unexpected_pax: bool = False,
    include_pyproject: bool = True,
    portable_collision: bool = False,
    tar_format: int = tarfile.PAX_FORMAT,
) -> None:
    with (
        path.open("xb") as raw_output,
        gzip.GzipFile(
            filename="raw-build.tar",
            mode="wb",
            fileobj=raw_output,
            mtime=gzip_mtime,
        ) as compressed,
        tarfile.open(
            fileobj=compressed,
            mode="w",
            format=tar_format,
        ) as archive,
    ):
        _add_directory(archive, ROOT, mtime=member_mtime)
        _add_file(
            archive,
            f"{ROOT}/PKG-INFO",
            b"Metadata-Version: 2.4\nName: fixture\nVersion: 0.1.0\n",
            mtime=member_mtime,
        )
        if include_pyproject:
            _add_file(
                archive,
                f"{ROOT}/pyproject.toml",
                b"[build-system]\nrequires = []\n",
                mtime=member_mtime,
            )
        _add_file(
            archive,
            f"{ROOT}/package.py",
            package_content,
            mtime=member_mtime,
            pax_headers={"comment": "not-authorized"} if unexpected_pax else None,
        )
        if unsafe_name is not None:
            _add_file(
                archive,
                unsafe_name,
                b"unsafe\n",
                mtime=member_mtime,
            )
        if duplicate:
            _add_file(
                archive,
                f"{ROOT}/package.py",
                package_content,
                mtime=member_mtime,
            )
        if link:
            member = tarfile.TarInfo(f"{ROOT}/linked.py")
            member.type = tarfile.SYMTYPE
            member.linkname = f"{ROOT}/package.py"
            member.mtime = member_mtime
            archive.addfile(member)
        if portable_collision:
            _add_file(
                archive,
                f"{ROOT}/Case.py",
                b"upper\n",
                mtime=member_mtime,
            )
            _add_file(
                archive,
                f"{ROOT}/case.py",
                b"lower\n",
                mtime=member_mtime,
            )


def _raw_archive(
    tmp_path: Path,
    directory: str,
    *,
    gzip_mtime: int = EPOCH + 1,
    member_mtime: float = EPOCH + 1.25,
    **kwargs: object,
) -> Path:
    output = tmp_path / directory
    output.mkdir()
    path = output / SDIST
    _write_raw_sdist(
        path,
        gzip_mtime=gzip_mtime,
        member_mtime=member_mtime,
        **kwargs,
    )
    return path


def _pax_record(key: bytes, value: bytes) -> bytes:
    body = b" " + key + b"=" + value + b"\n"
    record_length = len(body) + 1
    while True:
        encoded_length = str(record_length).encode("ascii")
        updated = len(encoded_length) + len(body)
        if updated == record_length:
            return encoded_length + body
        record_length = updated


def _retain_one_tar_end_block(raw: bytes) -> bytes:
    last_nonzero = max(index for index, value in enumerate(raw) if value)
    content_end = (
        (last_nonzero + 1 + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE
    ) * tarfile.BLOCKSIZE
    return raw[:content_end] + bytes(tarfile.BLOCKSIZE)


def test_normalization_is_exact_reproducible_and_idempotent(tmp_path: Path) -> None:
    first = _raw_archive(
        tmp_path,
        "first",
        gzip_mtime=EPOCH + 10,
        member_mtime=EPOCH + 10.125,
    )
    second = _raw_archive(
        tmp_path,
        "second",
        gzip_mtime=EPOCH + 20,
        member_mtime=EPOCH + 20.875,
    )

    _normalize_sdist_archive(first, EPOCH)
    _normalize_sdist_archive(second, EPOCH)

    expected = first.read_bytes()
    assert second.read_bytes() == expected
    assert int.from_bytes(expected[4:8], "little") == EPOCH
    with tarfile.open(first, mode="r:gz") as archive:
        members = archive.getmembers()
    assert members
    assert all(member.mtime == EPOCH for member in members)
    assert all(member.uid == member.gid == 0 for member in members)
    assert all(not member.uname and not member.gname for member in members)
    assert all("mtime" not in member.pax_headers for member in members)

    _normalize_sdist_archive(first, EPOCH)
    assert first.read_bytes() == expected


def test_normalization_never_collapses_different_member_content(tmp_path: Path) -> None:
    first = _raw_archive(tmp_path, "first", package_content=b"first\n")
    second = _raw_archive(tmp_path, "second", package_content=b"second\n")

    _normalize_sdist_archive(first, EPOCH)
    _normalize_sdist_archive(second, EPOCH)

    assert first.read_bytes() != second.read_bytes()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"unsafe_name": f"{ROOT}/../escape"}, "unsafe member name"),
        ({"duplicate": True}, "duplicate member"),
        ({"link": True}, "link or special member"),
        ({"unexpected_pax": True}, "unexpected PAX fields"),
        ({"include_pyproject": False}, "pyproject.toml"),
        ({"unsafe_name": f"{ROOT}/stream:ads"}, "nonportable member name"),
        ({"unsafe_name": f"{ROOT}/CON"}, "nonportable member name"),
        ({"unsafe_name": f"{ROOT}/trailing."}, "nonportable member name"),
        ({"portable_collision": True}, "collide portably"),
    ],
)
def test_normalization_rejects_unsafe_or_incomplete_archives(
    tmp_path: Path,
    kwargs: dict[str, object],
    message: str,
) -> None:
    path = _raw_archive(tmp_path, "raw", **kwargs)
    original = path.read_bytes()

    with pytest.raises(DeterministicSdistError, match=message):
        _normalize_sdist_archive(path, EPOCH)

    assert path.read_bytes() == original


def test_normalization_rejects_corrupt_and_hard_linked_inputs(tmp_path: Path) -> None:
    corrupt_directory = tmp_path / "corrupt"
    corrupt_directory.mkdir()
    corrupt = corrupt_directory / SDIST
    corrupt.write_bytes(b"not a tar archive")
    with pytest.raises(DeterministicSdistError, match="valid bounded"):
        _normalize_sdist_archive(corrupt, EPOCH)

    linked = _raw_archive(tmp_path, "linked")
    alias = linked.with_name("alias.tar.gz")
    try:
        os.link(linked, alias)
    except OSError:
        pytest.skip("hard links are unavailable on this filesystem")
    with pytest.raises(DeterministicSdistError, match="single-link"):
        _normalize_sdist_archive(linked, EPOCH)


def test_normalization_detects_source_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _raw_archive(tmp_path, "raw")
    original_writer = backend._write_normalized_archive

    def replace_after_write(
        source_path: Path,
        raw_output: object,
        *,
        expected_root: str,
        expected_source_snapshot: object,
        source_date_epoch: int,
    ) -> None:
        original_writer(
            source_path,
            raw_output,
            expected_root=expected_root,
            expected_source_snapshot=expected_source_snapshot,
            source_date_epoch=source_date_epoch,
        )
        replacement = source_path.with_name("replacement.tar.gz")
        replacement.write_bytes(source_path.read_bytes())
        os.replace(replacement, source_path)

    monkeypatch.setattr(
        backend,
        "_write_normalized_archive",
        replace_after_write,
    )

    with pytest.raises(DeterministicSdistError, match="changed during"):
        _normalize_sdist_archive(path, EPOCH)
    retained = list(path.parent.glob(f".{path.name}.*.tmp"))
    assert len(retained) == 1


def test_normalization_detects_temporary_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _raw_archive(tmp_path, "raw")
    original = path.read_bytes()
    original_validator = backend._validate_normalized_metadata

    def replace_after_validation(
        candidate: Path,
        *,
        expected_root: str,
        source_date_epoch: int,
    ) -> None:
        original_validator(
            candidate,
            expected_root=expected_root,
            source_date_epoch=source_date_epoch,
        )
        if candidate != path:
            replacement = candidate.with_name("replacement.tmp")
            replacement.write_bytes(b"do-not-delete-concurrent-replacement")
            os.replace(replacement, candidate)

    monkeypatch.setattr(
        backend,
        "_validate_normalized_metadata",
        replace_after_validation,
    )

    with pytest.raises(DeterministicSdistError, match="normalized.*changed"):
        _normalize_sdist_archive(path, EPOCH)
    assert path.read_bytes() == original
    retained = list(path.parent.glob(f".{path.name}.*.tmp"))
    assert len(retained) == 1
    assert retained[0].read_bytes() == b"do-not-delete-concurrent-replacement"


def test_normalization_never_changes_temporary_mode_through_a_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _raw_archive(tmp_path, "raw")

    def unexpected_path_chmod(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("temporary mode changes must stay descriptor-bound")

    monkeypatch.setattr(backend.os, "chmod", unexpected_path_chmod)

    _normalize_sdist_archive(path, EPOCH)


def test_normalization_rejects_trailing_and_concatenated_gzip_bytes(
    tmp_path: Path,
) -> None:
    appended = _raw_archive(tmp_path, "appended")
    appended_original = appended.read_bytes()
    appended.write_bytes(appended_original + b"APPENDED-HIDDEN-BYTES")

    with pytest.raises(
        DeterministicSdistError,
        match="trailing bytes or multiple gzip members",
    ):
        _normalize_sdist_archive(appended, EPOCH)
    assert appended.read_bytes() == appended_original + b"APPENDED-HIDDEN-BYTES"

    concatenated = _raw_archive(tmp_path, "concatenated")
    concatenated_original = concatenated.read_bytes()
    second_member = gzip.compress(b"second gzip member", mtime=EPOCH)
    concatenated.write_bytes(concatenated_original + second_member)

    with pytest.raises(
        DeterministicSdistError,
        match="trailing bytes or multiple gzip members",
    ):
        _normalize_sdist_archive(concatenated, EPOCH)
    assert concatenated.read_bytes() == concatenated_original + second_member


def test_preflight_rejects_decompression_member_and_pax_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decompression = _raw_archive(tmp_path, "decompression")
    monkeypatch.setattr(backend, "_MAX_DECOMPRESSED_BYTES", 1024)
    with pytest.raises(DeterministicSdistError, match="decompressed byte limit"):
        _normalize_sdist_archive(decompression, EPOCH)

    monkeypatch.setattr(
        backend,
        "_MAX_DECOMPRESSED_BYTES",
        backend._MAX_EXPANDED_BYTES + backend._MAX_TAR_METADATA_BYTES,
    )
    members = _raw_archive(tmp_path, "members", member_mtime=EPOCH)
    monkeypatch.setattr(backend, "_MAX_MEMBERS", 3)
    with pytest.raises(DeterministicSdistError, match="member count"):
        _normalize_sdist_archive(members, EPOCH)

    monkeypatch.setattr(backend, "_MAX_MEMBERS", 20_000)
    pax = _raw_archive(tmp_path, "pax")
    monkeypatch.setattr(backend, "_MAX_PAX_PAYLOAD_BYTES", 16)
    with pytest.raises(DeterministicSdistError, match="PAX payload"):
        _normalize_sdist_archive(pax, EPOCH)


def test_preflight_rejects_gnu_longname_extensions(tmp_path: Path) -> None:
    path = _raw_archive(
        tmp_path,
        "gnu",
        member_mtime=float(EPOCH),
        unsafe_name=f"{ROOT}/{'a' * 160}.py",
        tar_format=tarfile.GNU_FORMAT,
    )

    with pytest.raises(
        DeterministicSdistError,
        match="unsafe member name|link or special member",
    ):
        _normalize_sdist_archive(path, EPOCH)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            _pax_record(b"path", b"first") + _pax_record(b"path", b"second"),
            "duplicate PAX field",
        ),
        (b"09 path=x\n", "PAX length"),
        (b"10 path=x!", "PAX framing"),
        (_pax_record(b"path", b"\xff"), "valid UTF-8"),
        (_pax_record(b"size", b"1"), "unexpected PAX fields"),
        (_pax_record(b"mtime", b"not-a-time"), "PAX mtime"),
    ],
)
def test_pax_preflight_rejects_ambiguous_or_unsupported_records(
    payload: bytes,
    message: str,
) -> None:
    with pytest.raises(DeterministicSdistError, match=message):
        backend._parse_pax_payload(payload, allow_mtime=True)


def test_preflight_rejects_corrupt_tar_end_and_header_bytes(tmp_path: Path) -> None:
    for directory, mutate, message in [
        (
            "nonzero-trailer",
            lambda raw: raw[:-1] + b"\x01",
            "nonzero bytes after the tar end marker",
        ),
        (
            "missing-end",
            _retain_one_tar_end_block,
            "tar end marker|tar byte count|missing its end marker",
        ),
        (
            "bad-checksum",
            lambda raw: bytes([raw[0] ^ 1]) + raw[1:],
            "tar header is invalid",
        ),
    ]:
        path = _raw_archive(tmp_path, directory, member_mtime=EPOCH)
        expanded = gzip.decompress(path.read_bytes())
        path.write_bytes(gzip.compress(mutate(expanded), mtime=EPOCH))
        with pytest.raises(DeterministicSdistError, match=message):
            _normalize_sdist_archive(path, EPOCH)


def test_post_install_substitution_is_retained_as_a_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _raw_archive(tmp_path, "candidate", package_content=b"expected\n")
    adversary = _raw_archive(tmp_path, "adversary", package_content=b"substituted\n")
    _normalize_sdist_archive(adversary, EPOCH)
    adversary_bytes = adversary.read_bytes()
    real_replace = os.replace

    def replace_then_substitute(source: Path, destination: Path) -> None:
        real_replace(source, destination)
        replacement = Path(destination).with_name("post-install-replacement.tar.gz")
        replacement.write_bytes(adversary_bytes)
        real_replace(replacement, destination)

    monkeypatch.setattr(backend.os, "replace", replace_then_substitute)

    with pytest.raises(
        DeterministicSdistError,
        match="installed normalized source distribution changed",
    ):
        _normalize_sdist_archive(path, EPOCH)
    assert path.read_bytes() == adversary_bytes


def test_file_snapshot_ignores_descriptor_only_change_time(tmp_path: Path) -> None:
    path = _raw_archive(tmp_path, "raw")
    observed = path.stat()
    descriptor_view = SimpleNamespace(
        st_dev=observed.st_dev,
        st_ino=observed.st_ino,
        st_mode=observed.st_mode,
        st_nlink=observed.st_nlink,
        st_size=observed.st_size,
        st_mtime_ns=observed.st_mtime_ns,
        st_ctime_ns=observed.st_ctime_ns + 1,
    )

    assert backend._snapshot_from_stat(descriptor_view) == (backend._snapshot_from_stat(observed))


@pytest.mark.parametrize(
    "value",
    [True, -1, 1 << 32, "", "01", "+1", "1.0", "not-a-time"],
)
def test_source_date_epoch_rejects_ambiguous_or_out_of_range_values(
    value: object,
) -> None:
    with pytest.raises(DeterministicSdistError, match="SOURCE_DATE_EPOCH"):
        _parse_source_date_epoch(value)


def test_build_hook_delegates_unchanged_without_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    monkeypatch.setattr(
        backend._setuptools_backend,
        "build_sdist",
        lambda *_args, **_kwargs: "delegated.tar.gz",
    )

    def unexpected_normalization(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("normalization must remain opt-in")

    monkeypatch.setattr(
        backend,
        "_normalize_sdist_archive",
        unexpected_normalization,
    )

    assert backend.build_sdist("unused") == "delegated.tar.gz"


def test_build_hook_normalizes_with_an_explicit_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()

    def fake_build_sdist(
        directory: str,
        *,
        config_settings: dict[str, object] | None = None,
    ) -> str:
        assert config_settings is None
        _write_raw_sdist(
            Path(directory) / SDIST,
            gzip_mtime=EPOCH + 10,
            member_mtime=EPOCH + 10.5,
        )
        return SDIST

    monkeypatch.setenv("SOURCE_DATE_EPOCH", str(EPOCH))
    monkeypatch.setattr(
        backend._setuptools_backend,
        "build_sdist",
        fake_build_sdist,
    )

    assert backend.build_sdist(str(dist)) == SDIST
    built = (dist / SDIST).read_bytes()
    assert int.from_bytes(built[4:8], "little") == EPOCH


def test_invalid_epoch_fails_before_setuptools_is_invoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoked = False

    def fake_build_sdist(*_args: object, **_kwargs: object) -> str:
        nonlocal invoked
        invoked = True
        return SDIST

    monkeypatch.setenv("SOURCE_DATE_EPOCH", "01")
    monkeypatch.setattr(
        backend._setuptools_backend,
        "build_sdist",
        fake_build_sdist,
    )

    with pytest.raises(DeterministicSdistError, match="decimal integer"):
        backend.build_sdist("unused")
    assert not invoked


def test_deterministic_build_validates_output_directory_before_setuptools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoked = False

    def fake_build_sdist(*_args: object, **_kwargs: object) -> str:
        nonlocal invoked
        invoked = True
        return SDIST

    monkeypatch.setenv("SOURCE_DATE_EPOCH", str(EPOCH))
    monkeypatch.setattr(
        backend._setuptools_backend,
        "build_sdist",
        fake_build_sdist,
    )

    with pytest.raises(DeterministicSdistError, match="unavailable"):
        backend.build_sdist(str(tmp_path / "missing"))
    assert not invoked
