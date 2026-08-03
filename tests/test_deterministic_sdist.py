from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import io
import os
import stat
import struct
import tarfile
import zipfile
import zlib
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
WHEEL = "fixture-0.1.0-py3-none-any.whl"
DIST_INFO = "fixture-0.1.0.dist-info"


def _add_directory(
    archive: tarfile.TarFile,
    name: str,
    *,
    mtime: float,
    mode: int = 0o755,
    pax_headers: dict[str, str] | None = None,
) -> None:
    member = tarfile.TarInfo(name)
    member.type = tarfile.DIRTYPE
    member.mode = mode
    member.mtime = mtime
    member.pax_headers = dict(pax_headers or {})
    archive.addfile(member)


def _add_file(
    archive: tarfile.TarFile,
    name: str,
    content: bytes,
    *,
    mtime: float,
    mode: int = 0o644,
    pax_headers: dict[str, str] | None = None,
) -> None:
    member = tarfile.TarInfo(name)
    member.mode = mode
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
    directory_mode: int = 0o755,
    file_mode: int = 0o644,
    metadata_newline: bytes = b"\n",
    include_generated_metadata: bool = False,
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
        _add_directory(
            archive,
            ROOT,
            mtime=member_mtime,
            mode=directory_mode,
        )
        _add_file(
            archive,
            f"{ROOT}/PKG-INFO",
            metadata_newline.join(
                (
                    b"Metadata-Version: 2.4",
                    b"Name: fixture",
                    b"Version: 0.1.0",
                    b"",
                )
            ),
            mtime=member_mtime,
            mode=file_mode,
        )
        if include_pyproject:
            _add_file(
                archive,
                f"{ROOT}/pyproject.toml",
                b"[build-system]\nrequires = []\n",
                mtime=member_mtime,
                mode=file_mode,
            )
        _add_file(
            archive,
            f"{ROOT}/package.py",
            package_content,
            mtime=member_mtime,
            mode=file_mode,
            pax_headers={"comment": "not-authorized"} if unexpected_pax else None,
        )
        if include_generated_metadata:
            generated_pkg_info = metadata_newline.join(
                (
                    b"Metadata-Version: 2.4",
                    b"Name: fixture",
                    b"Version: 0.1.0",
                    b"",
                )
            )
            _add_file(
                archive,
                f"{ROOT}/setup.cfg",
                metadata_newline.join(
                    (
                        b"[egg_info]",
                        b"tag_build = ",
                        b"tag_date = 0",
                        b"",
                        b"",
                    )
                ),
                mtime=member_mtime,
                mode=file_mode,
            )
            _add_file(
                archive,
                f"{ROOT}/src/fixture.egg-info/PKG-INFO",
                generated_pkg_info,
                mtime=member_mtime,
                mode=file_mode,
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


def _wheel_info(
    name: str,
    *,
    timestamp: tuple[int, int, int, int, int, int],
    creator: int,
    mode: int,
    compression: int = zipfile.ZIP_DEFLATED,
) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=timestamp)
    info.compress_type = compression
    info.create_system = creator
    info.external_attr = (stat.S_IFREG | mode) << 16
    return info


def _record_digest(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
    return "sha256=" + encoded.rstrip(b"=").decode("ascii")


def _write_raw_wheel(
    path: Path,
    *,
    metadata_newline: bytes,
    timestamp: tuple[int, int, int, int, int, int],
    creator: int,
    mode: int,
    package_content: bytes = b'VALUE = "fixture"\n',
    tamper_record_digest: bool = False,
    package_digest_field: str | None = None,
    package_size_field: str | None = None,
    package_name: str = "fixture/__init__.py",
    package_mode: int | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
) -> None:
    metadata = metadata_newline.join(
        (
            b"Metadata-Version: 2.4",
            b"Name: fixture",
            b"Version: 0.1.0",
            b"",
        )
    )
    entries = [
        (package_name, package_content),
        (f"{DIST_INFO}/METADATA", metadata),
        (
            f"{DIST_INFO}/WHEEL",
            b"Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\n"
            b"Tag: py3-none-any\n",
        ),
    ]
    record_name = f"{DIST_INFO}/RECORD"
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name, payload in entries:
        digest_field = (
            package_digest_field
            if name == package_name and package_digest_field is not None
            else _record_digest(payload)
        )
        size_field = (
            package_size_field
            if name == package_name and package_size_field is not None
            else str(len(payload))
        )
        writer.writerow((name, digest_field, size_field))
    writer.writerow((record_name, "", ""))
    record = output.getvalue().encode("utf-8")
    if tamper_record_digest:
        marker = record.index(b"sha256=") + len(b"sha256=")
        replacement = b"A" if record[marker : marker + 1] != b"A" else b"B"
        record = record[:marker] + replacement + record[marker + 1 :]
    entries.append((record_name, record))
    with zipfile.ZipFile(
        path,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name, payload in entries:
            archive.writestr(
                _wheel_info(
                    name,
                    timestamp=timestamp,
                    creator=creator,
                    mode=package_mode if name == package_name and package_mode else mode,
                    compression=compression,
                ),
                payload,
            )


def _insert_wheel_gap_before_central_directory(path: Path, gap: bytes) -> None:
    payload = path.read_bytes()
    end_record_offset = len(payload) - 22
    assert payload[end_record_offset : end_record_offset + 4] == b"PK\x05\x06"
    directory_offset = struct.unpack_from("<L", payload, end_record_offset + 16)[0]
    modified = bytearray(
        payload[:directory_offset] + gap + payload[directory_offset:]
    )
    struct.pack_into(
        "<L",
        modified,
        end_record_offset + len(gap) + 16,
        directory_offset + len(gap),
    )
    path.write_bytes(modified)


def _append_to_final_wheel_compressed_extent(path: Path, suffix: bytes) -> None:
    payload = path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(payload), mode="r") as archive:
        member = archive.infolist()[-1]
    end_record_offset = len(payload) - 22
    directory_offset = struct.unpack_from("<L", payload, end_record_offset + 16)[0]
    local_name_size, local_extra_size = struct.unpack_from(
        "<2H",
        payload,
        member.header_offset + 26,
    )
    compressed_end = (
        member.header_offset
        + 30
        + local_name_size
        + local_extra_size
        + member.compress_size
    )
    assert compressed_end == directory_offset

    central_offset = directory_offset
    target_central_offset: int | None = None
    while central_offset < end_record_offset:
        assert payload[central_offset : central_offset + 4] == b"PK\x01\x02"
        name_size, extra_size, comment_size = struct.unpack_from(
            "<3H",
            payload,
            central_offset + 28,
        )
        name = payload[central_offset + 46 : central_offset + 46 + name_size]
        if name.decode("utf-8") == member.filename:
            target_central_offset = central_offset
        central_offset += 46 + name_size + extra_size + comment_size
    assert central_offset == end_record_offset
    assert target_central_offset is not None

    modified = bytearray(payload[:compressed_end] + suffix + payload[compressed_end:])
    struct.pack_into(
        "<L",
        modified,
        member.header_offset + 18,
        member.compress_size + len(suffix),
    )
    struct.pack_into(
        "<L",
        modified,
        target_central_offset + len(suffix) + 20,
        member.compress_size + len(suffix),
    )
    struct.pack_into(
        "<L",
        modified,
        end_record_offset + len(suffix) + 16,
        directory_offset + len(suffix),
    )
    path.write_bytes(modified)


def _mutate_first_wheel_local_u16(path: Path, field_offset: int) -> None:
    payload = bytearray(path.read_bytes())
    with zipfile.ZipFile(io.BytesIO(payload), mode="r") as archive:
        member = archive.infolist()[0]
    absolute_offset = member.header_offset + field_offset
    original = struct.unpack_from("<H", payload, absolute_offset)[0]
    struct.pack_into("<H", payload, absolute_offset, original ^ 1)
    path.write_bytes(payload)


def _raw_wheel_member_payload(path: Path, name: str) -> bytes:
    payload = path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(payload), mode="r") as archive:
        member = archive.getinfo(name)
    local_name_size, local_extra_size = struct.unpack_from(
        "<2H",
        payload,
        member.header_offset + 26,
    )
    start = member.header_offset + 30 + local_name_size + local_extra_size
    return payload[start : start + member.compress_size]


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


def test_sdist_normalization_canonicalizes_generated_text_and_modes(
    tmp_path: Path,
) -> None:
    posix = _raw_archive(
        tmp_path,
        "posix",
        directory_mode=0o755,
        file_mode=0o644,
        metadata_newline=b"\n",
        include_generated_metadata=True,
    )
    windows = _raw_archive(
        tmp_path,
        "windows",
        directory_mode=0o777,
        file_mode=0o666,
        metadata_newline=b"\r\n",
        include_generated_metadata=True,
    )

    _normalize_sdist_archive(posix, EPOCH)
    _normalize_sdist_archive(windows, EPOCH)

    expected = posix.read_bytes()
    assert windows.read_bytes() == expected
    with tarfile.open(posix, mode="r:gz") as archive:
        members = archive.getmembers()
        generated = [
            member
            for member in members
            if member.name.endswith(("PKG-INFO", "setup.cfg"))
        ]
        for member in generated:
            stream = archive.extractfile(member)
            assert stream is not None
            assert b"\r" not in stream.read()
    assert members
    assert generated
    assert all(
        member.mode == (0o755 if member.isdir() else 0o644)
        for member in members
    )


def test_wheel_normalization_canonicalizes_platform_representations(
    tmp_path: Path,
) -> None:
    posix_directory = tmp_path / "posix-wheel"
    windows_directory = tmp_path / "windows-wheel"
    posix_directory.mkdir()
    windows_directory.mkdir()
    posix = posix_directory / WHEEL
    windows = windows_directory / WHEEL
    _write_raw_wheel(
        posix,
        metadata_newline=b"\n",
        timestamp=(2023, 11, 14, 22, 13, 18),
        creator=3,
        mode=0o644,
    )
    _write_raw_wheel(
        windows,
        metadata_newline=b"\r\n",
        timestamp=(2023, 11, 14, 22, 13, 22),
        creator=0,
        mode=0o666,
    )

    backend._normalize_wheel_archive(posix, EPOCH)
    backend._normalize_wheel_archive(windows, EPOCH)

    expected = posix.read_bytes()
    assert windows.read_bytes() == expected
    with zipfile.ZipFile(posix) as archive:
        members = archive.infolist()
        metadata = archive.read(f"{DIST_INFO}/METADATA")
    assert members
    assert b"\r" not in metadata
    assert all(member.date_time == backend._wheel_timestamp(EPOCH) for member in members)
    assert all(member.create_system == 3 for member in members)
    assert all(
        (member.external_attr >> 16) == (stat.S_IFREG | 0o644)
        for member in members
    )
    backend._normalize_wheel_archive(posix, EPOCH)
    assert posix.read_bytes() == expected


def test_wheel_timestamp_floors_odd_seconds_and_clamps_before_1980() -> None:
    assert backend._wheel_timestamp(EPOCH + 1) == backend._wheel_timestamp(EPOCH)
    assert backend._wheel_timestamp(0) == (1980, 1, 1, 0, 0, 0)


def test_wheel_normalization_uses_explicit_deflate_level_nine(
    tmp_path: Path,
) -> None:
    path = tmp_path / WHEEL
    content = b"".join(
        bytes([index % 251]) * ((index % 97) + 1)
        for index in range(500)
    )
    _write_raw_wheel(
        path,
        metadata_newline=b"\n",
        timestamp=(2023, 11, 14, 22, 13, 20),
        creator=3,
        mode=0o644,
        package_content=content,
    )

    backend._normalize_wheel_archive(path, EPOCH)

    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    expected = compressor.compress(content) + compressor.flush()
    default_compressor = zlib.compressobj(-1, zlib.DEFLATED, -zlib.MAX_WBITS)
    default = default_compressor.compress(content) + default_compressor.flush()
    assert expected != default
    assert _raw_wheel_member_payload(path, "fixture/__init__.py") == expected


def test_wheel_normalization_rejects_record_tampering_before_rewrite(
    tmp_path: Path,
) -> None:
    path = tmp_path / WHEEL
    _write_raw_wheel(
        path,
        metadata_newline=b"\n",
        timestamp=(2023, 11, 14, 22, 13, 20),
        creator=3,
        mode=0o644,
        tamper_record_digest=True,
    )
    original = path.read_bytes()

    with pytest.raises(
        DeterministicSdistError,
        match="RECORD digest does not match",
    ):
        backend._normalize_wheel_archive(path, EPOCH)

    assert path.read_bytes() == original
    assert not tuple(tmp_path.glob(f".{path.name}.*.tmp"))


@pytest.mark.parametrize(
    "digest_field",
    [
        "sha256=A",
        "sha256=" + ("A" * 43) + "=",
        "sha256=" + ("+" * 43),
    ],
)
def test_wheel_normalization_rejects_noncanonical_record_digests(
    tmp_path: Path,
    digest_field: str,
) -> None:
    path = tmp_path / WHEEL
    _write_raw_wheel(
        path,
        metadata_newline=b"\n",
        timestamp=(2023, 11, 14, 22, 13, 20),
        creator=3,
        mode=0o644,
        package_digest_field=digest_field,
    )
    original = path.read_bytes()

    with pytest.raises(DeterministicSdistError, match="RECORD.*SHA-256"):
        backend._normalize_wheel_archive(path, EPOCH)

    assert path.read_bytes() == original


@pytest.mark.parametrize("size_field", ["٤", "04", "9999999999"])
def test_wheel_normalization_rejects_noncanonical_record_sizes(
    tmp_path: Path,
    size_field: str,
) -> None:
    path = tmp_path / WHEEL
    _write_raw_wheel(
        path,
        metadata_newline=b"\n",
        timestamp=(2023, 11, 14, 22, 13, 20),
        creator=3,
        mode=0o644,
        package_content=b"DATA",
        package_size_field=size_field,
    )
    original = path.read_bytes()

    with pytest.raises(DeterministicSdistError, match="RECORD size"):
        backend._normalize_wheel_archive(path, EPOCH)

    assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("package_name", "package_mode", "message"),
    [
        ("../escape.py", None, "unsafe member name"),
        ("fixture/CON .txt", None, "nonportable member name"),
        ("fixture/CONIN$.txt", None, "nonportable member name"),
        ("fixture/COM1 .txt", None, "nonportable member name"),
        ("fixture/COM\u00b9.txt", None, "nonportable member name"),
        ("fixture/LPT\u00b2 .log", None, "nonportable member name"),
        ("fixture/link.py", stat.S_IFLNK | 0o777, "link or special member"),
    ],
)
def test_wheel_normalization_rejects_unsafe_or_linked_members(
    tmp_path: Path,
    package_name: str,
    package_mode: int | None,
    message: str,
) -> None:
    path = tmp_path / WHEEL
    _write_raw_wheel(
        path,
        metadata_newline=b"\n",
        timestamp=(2023, 11, 14, 22, 13, 20),
        creator=3,
        mode=0o644,
        package_name=package_name,
        package_mode=package_mode,
    )
    original = path.read_bytes()

    with pytest.raises(DeterministicSdistError, match=message):
        backend._normalize_wheel_archive(path, EPOCH)

    assert path.read_bytes() == original


@pytest.mark.parametrize("component", ["COM0.txt", "COM10.txt", "CON name.txt"])
def test_archive_member_validators_retain_non_device_controls(
    component: str,
) -> None:
    sdist_name = f"{ROOT}/{component}"
    wheel_name = f"fixture/{component}"

    assert backend._safe_member_name(sdist_name, expected_root=ROOT) == sdist_name
    assert backend._safe_wheel_member_name(wheel_name) == wheel_name


def test_wheel_normalization_rejects_prepended_and_trailing_bytes(
    tmp_path: Path,
) -> None:
    for directory, mutate in (
        ("prepended", lambda payload: b"PREFIX" + payload),
        ("trailing", lambda payload: payload + b"TRAILER"),
    ):
        root = tmp_path / directory
        root.mkdir()
        path = root / WHEEL
        _write_raw_wheel(
            path,
            metadata_newline=b"\n",
            timestamp=(2023, 11, 14, 22, 13, 20),
            creator=3,
            mode=0o644,
        )
        modified = mutate(path.read_bytes())
        path.write_bytes(modified)

        with pytest.raises(
            DeterministicSdistError,
            match="prepended|trailing|framing|valid bounded|central directory",
        ):
            backend._normalize_wheel_archive(path, EPOCH)

        assert path.read_bytes() == modified


def test_wheel_normalization_rejects_internal_unreferenced_bytes(
    tmp_path: Path,
) -> None:
    path = tmp_path / WHEEL
    _write_raw_wheel(
        path,
        metadata_newline=b"\n",
        timestamp=(2023, 11, 14, 22, 13, 20),
        creator=3,
        mode=0o644,
    )
    _insert_wheel_gap_before_central_directory(path, b"UNREFERENCED-INTERNAL-GAP")
    modified = path.read_bytes()

    with pytest.raises(
        DeterministicSdistError,
        match="contiguous archive|end at the central directory",
    ):
        backend._normalize_wheel_archive(path, EPOCH)

    assert path.read_bytes() == modified


@pytest.mark.parametrize(
    "compression",
    [zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED],
)
def test_wheel_normalization_rejects_suffix_inside_compressed_extent(
    tmp_path: Path,
    compression: int,
) -> None:
    path = tmp_path / WHEEL
    _write_raw_wheel(
        path,
        metadata_newline=b"\n",
        timestamp=(2023, 11, 14, 22, 13, 20),
        creator=3,
        mode=0o644,
        compression=compression,
    )
    _append_to_final_wheel_compressed_extent(path, b"HIDDEN")
    modified = path.read_bytes()

    with pytest.raises(
        DeterministicSdistError,
        match="compressed|unsupported",
    ):
        backend._normalize_wheel_archive(path, EPOCH)

    assert path.read_bytes() == modified


@pytest.mark.parametrize("field_offset", [4, 10, 12])
def test_wheel_normalization_rejects_local_header_metadata_mismatch(
    tmp_path: Path,
    field_offset: int,
) -> None:
    path = tmp_path / WHEEL
    _write_raw_wheel(
        path,
        metadata_newline=b"\n",
        timestamp=(2023, 11, 14, 22, 13, 20),
        creator=3,
        mode=0o644,
    )
    _mutate_first_wheel_local_u16(path, field_offset)
    modified = path.read_bytes()

    with pytest.raises(
        DeterministicSdistError,
        match="local record conflicts",
    ):
        backend._normalize_wheel_archive(path, EPOCH)

    assert path.read_bytes() == modified


def test_wheel_normalization_enforces_generated_text_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / WHEEL
    _write_raw_wheel(
        path,
        metadata_newline=b"\n",
        timestamp=(2023, 11, 14, 22, 13, 20),
        creator=3,
        mode=0o644,
    )
    original = path.read_bytes()
    monkeypatch.setattr(backend, "_MAX_GENERATED_TEXT_BYTES", 16)

    with pytest.raises(
        DeterministicSdistError,
        match="wheel RECORD exceeds|wheel is not a valid bounded archive",
    ):
        backend._normalize_wheel_archive(path, EPOCH)

    assert path.read_bytes() == original


def test_wheel_physical_preflight_rejects_member_count_before_zipfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / WHEEL
    _write_raw_wheel(
        path,
        metadata_newline=b"\n",
        timestamp=(2023, 11, 14, 22, 13, 20),
        creator=3,
        mode=0o644,
    )
    original = path.read_bytes()
    monkeypatch.setattr(backend, "_MAX_MEMBERS", 1)

    def unexpected_zipfile(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ZipFile must not run before the physical bound")

    monkeypatch.setattr(backend.zipfile, "ZipFile", unexpected_zipfile)

    with pytest.raises(DeterministicSdistError, match="central directory"):
        backend._normalize_wheel_archive(path, EPOCH)

    assert path.read_bytes() == original


def test_generated_metadata_rejects_bare_carriage_returns(
    tmp_path: Path,
) -> None:
    sdist = _raw_archive(
        tmp_path,
        "bare-cr-sdist",
        metadata_newline=b"\r",
        include_generated_metadata=True,
    )
    wheel_directory = tmp_path / "bare-cr-wheel"
    wheel_directory.mkdir()
    wheel = wheel_directory / WHEEL
    _write_raw_wheel(
        wheel,
        metadata_newline=b"\r",
        timestamp=(2023, 11, 14, 22, 13, 20),
        creator=3,
        mode=0o644,
    )
    sdist_original = sdist.read_bytes()
    wheel_original = wheel.read_bytes()

    with pytest.raises(DeterministicSdistError, match="bare carriage return"):
        _normalize_sdist_archive(sdist, EPOCH)
    with pytest.raises(DeterministicSdistError, match="bare carriage return"):
        backend._normalize_wheel_archive(wheel, EPOCH)

    assert sdist.read_bytes() == sdist_original
    assert wheel.read_bytes() == wheel_original


def test_wheel_normalization_detects_source_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / WHEEL
    _write_raw_wheel(
        path,
        metadata_newline=b"\n",
        timestamp=(2023, 11, 14, 22, 13, 20),
        creator=3,
        mode=0o644,
    )
    original_writer = backend._write_normalized_wheel

    def replace_after_write(
        source_path: Path,
        raw_output: object,
        *,
        expected_source_snapshot: object,
        source_date_epoch: int,
    ) -> None:
        original_writer(
            source_path,
            raw_output,
            expected_source_snapshot=expected_source_snapshot,
            source_date_epoch=source_date_epoch,
        )
        replacement = source_path.with_name("replacement.whl")
        replacement.write_bytes(source_path.read_bytes())
        os.replace(replacement, source_path)

    monkeypatch.setattr(backend, "_write_normalized_wheel", replace_after_write)

    with pytest.raises(DeterministicSdistError, match="wheel changed during"):
        backend._normalize_wheel_archive(path, EPOCH)
    retained = tuple(tmp_path.glob(f".{path.name}.*.tmp"))
    assert len(retained) == 1


def test_wheel_normalization_detects_temporary_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / WHEEL
    _write_raw_wheel(
        path,
        metadata_newline=b"\n",
        timestamp=(2023, 11, 14, 22, 13, 20),
        creator=3,
        mode=0o644,
    )
    original = path.read_bytes()
    original_inventory = backend._wheel_inventory

    def replace_after_inventory(
        candidate: Path,
        *,
        source_date_epoch: int | None,
    ) -> object:
        result = original_inventory(
            candidate,
            source_date_epoch=source_date_epoch,
        )
        if candidate != path:
            replacement = candidate.with_name("replacement.tmp")
            replacement.write_bytes(b"do-not-delete-concurrent-wheel-replacement")
            os.replace(replacement, candidate)
        return result

    monkeypatch.setattr(backend, "_wheel_inventory", replace_after_inventory)

    with pytest.raises(DeterministicSdistError, match="normalized wheel changed"):
        backend._normalize_wheel_archive(path, EPOCH)
    assert path.read_bytes() == original
    retained = tuple(tmp_path.glob(f".{path.name}.*.tmp"))
    assert len(retained) == 1
    assert retained[0].read_bytes() == b"do-not-delete-concurrent-wheel-replacement"


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
        ({"unsafe_name": f"{ROOT}/CON .txt"}, "nonportable member name"),
        ({"unsafe_name": f"{ROOT}/CONOUT$.txt"}, "nonportable member name"),
        ({"unsafe_name": f"{ROOT}/COM1 .txt"}, "nonportable member name"),
        ({"unsafe_name": f"{ROOT}/COM\u00b9.txt"}, "nonportable member name"),
        ({"unsafe_name": f"{ROOT}/LPT\u00b2 .log"}, "nonportable member name"),
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


def test_wheel_post_install_substitution_is_retained_as_a_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_directory = tmp_path / "candidate-wheel"
    adversary_directory = tmp_path / "adversary-wheel"
    candidate_directory.mkdir()
    adversary_directory.mkdir()
    path = candidate_directory / WHEEL
    adversary = adversary_directory / WHEEL
    common = {
        "metadata_newline": b"\n",
        "timestamp": (2023, 11, 14, 22, 13, 20),
        "creator": 3,
        "mode": 0o644,
    }
    _write_raw_wheel(path, package_content=b"expected\n", **common)
    _write_raw_wheel(adversary, package_content=b"substituted\n", **common)
    backend._normalize_wheel_archive(adversary, EPOCH)
    adversary_bytes = adversary.read_bytes()
    real_replace = os.replace

    def replace_then_substitute(source: Path, destination: Path) -> None:
        real_replace(source, destination)
        replacement = Path(destination).with_name("post-install-replacement.whl")
        replacement.write_bytes(adversary_bytes)
        real_replace(replacement, destination)

    monkeypatch.setattr(backend.os, "replace", replace_then_substitute)

    with pytest.raises(
        DeterministicSdistError,
        match="installed normalized wheel changed",
    ):
        backend._normalize_wheel_archive(path, EPOCH)
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


def test_wheel_build_hook_delegates_unchanged_without_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, object, object]] = []

    def build_wheel(
        directory: str,
        *,
        config_settings: object,
        metadata_directory: object,
    ) -> str:
        observed.append((directory, config_settings, metadata_directory))
        return "delegated.whl"

    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    monkeypatch.setattr(backend._setuptools_backend, "build_wheel", build_wheel)
    settings = {"tag-date": "false"}

    assert backend.build_wheel("unused", settings, "metadata") == "delegated.whl"
    assert observed == [("unused", settings, "metadata")]


def test_wheel_build_hook_normalizes_with_an_explicit_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()

    def fake_build_wheel(
        directory: str,
        *,
        config_settings: dict[str, object] | None = None,
        metadata_directory: str | None = None,
    ) -> str:
        assert config_settings is None
        assert metadata_directory is None
        _write_raw_wheel(
            Path(directory) / WHEEL,
            metadata_newline=b"\r\n",
            timestamp=(2023, 11, 14, 22, 13, 18),
            creator=0,
            mode=0o666,
        )
        return WHEEL

    monkeypatch.setenv("SOURCE_DATE_EPOCH", str(EPOCH))
    monkeypatch.setattr(backend._setuptools_backend, "build_wheel", fake_build_wheel)

    assert backend.build_wheel(str(dist)) == WHEEL
    with zipfile.ZipFile(dist / WHEEL) as archive:
        members = archive.infolist()
        metadata = archive.read(f"{DIST_INFO}/METADATA")
    assert members
    assert b"\r" not in metadata
    assert all(member.date_time == backend._wheel_timestamp(EPOCH) for member in members)
    assert all(member.create_system == 3 for member in members)


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


def test_invalid_wheel_epoch_fails_before_setuptools_is_invoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoked = False

    def fake_build_wheel(*_args: object, **_kwargs: object) -> str:
        nonlocal invoked
        invoked = True
        return WHEEL

    monkeypatch.setenv("SOURCE_DATE_EPOCH", "01")
    monkeypatch.setattr(backend._setuptools_backend, "build_wheel", fake_build_wheel)

    with pytest.raises(DeterministicSdistError, match="decimal integer"):
        backend.build_wheel("unused")
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
