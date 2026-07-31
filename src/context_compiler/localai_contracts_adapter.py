"""Lazy, fail-closed boundary to the optional ``localai-contracts`` package.

The ordinary :mod:`context_compiler` package does not import this module.  This
module, in turn, imports ``localai_contracts`` only when an adapter is created.
The legacy six-operation connector and its JSONL protocol remain separate.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib
import importlib.machinery
import importlib.metadata
import importlib.util
import io
import json
import marshal
import os
import stat
import sys
import threading
import types
import urllib.parse
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .connector import LocalAIConnector
from .connector import SourceEvent as PrivateSourceEvent
from .path_safety import _is_link_or_reparse

LOCALAI_CONTRACTS_DISTRIBUTION = "localai-contracts"
LOCALAI_CONTRACTS_VERSION = "0.2.0a2"
LOCALAI_CONTRACTS_PROTOCOL_VERSION = "1.0.0"
LOCALAI_CONTRACTS_WHEEL_SHA256 = (
    "36a02dbc4267402949dddda1da180d800590cc579e0c1ecb022fc96f6a7c29ae"
)
LOCALAI_CONTRACTS_SOURCE_COMMIT = "3858190e8b458847da94e9ed24be83f4928b7d1a"
CONTEXT_COMPILE_OPERATION = "context.compile"
MAX_CONTEXT_SOURCE_EVENTS = 8
MAX_CONTEXT_SPANS = 10_000
_PROJECTION_VERSION = "ctxc-localai-context-bundle-projection-v1"
_CONTRACTS_IMPORT_NAME = "localai_contracts"
_CONTRACTS_VALIDATION_ERROR = (
    "localai-contracts 0.2.0a2 failed optional-adapter validation"
)
_CONTRACTS_INSTALLED_TREE_SHA256 = (
    "296f49a2d7b48158d2d3a33e36b77d5b5c495362cbe3aceaaf8975fb256e538c"
)
_MAX_CONTRACTS_TREE_ENTRIES = 256
_MAX_CONTRACTS_BYTECODE_BYTES = 4 * 1024 * 1024
_MAX_CONTRACTS_RECORD_BYTES = 64 * 1024
_MAX_CONTRACTS_RECORD_ROWS = 128
_MAX_CONTRACTS_INSTALLER_METADATA_BYTES = 1024 * 1024
_MAX_CONTRACTS_LAUNCHER_BYTES = 4 * 1024 * 1024
_CONTRACTS_DIST_INFO_DIRECTORY = "localai_contracts-0.2.0a2.dist-info"
_CONTRACTS_RECORD_PATH = f"{_CONTRACTS_DIST_INFO_DIRECTORY}/RECORD"
_CONTRACTS_WHEEL_RECORD_SHA256 = (
    "a20ae81b7cc5dd9e80fc2757d5fea6331f2c232818049026caecf63d48d14076"
)
_CONTRACTS_WHEEL_RECORD_BYTES = 3_464
_CONTRACTS_INSTALLED_FILES = (
    ("__init__.py", 6_474),
    ("_integration_bootstrap.py", 1_684),
    ("canonical.py", 8_389),
    ("conformance.py", 60_091),
    ("connector.py", 40_852),
    ("errors.py", 1_862),
    ("fixtures/phase0-conformance-v1.json", 7_551),
    ("inference_lease.py", 92_469),
    ("integration.py", 141_988),
    ("models.py", 14_235),
    ("privacy.py", 2_139),
    ("profiles.py", 876),
    ("profiles/r9700-qwen3.6-q4.development.json", 1_743),
    ("py.typed", 27),
    ("schemas/common.schema.json", 2_817),
    ("schemas/component-capability-manifest.schema.json", 3_386),
    ("schemas/connector-request.schema.json", 1_030),
    ("schemas/connector-response.schema.json", 1_198),
    ("schemas/context-bundle.schema.json", 4_248),
    ("schemas/deployment-plan.schema.json", 3_452),
    ("schemas/error-envelope.schema.json", 224),
    ("schemas/generation-receipt.schema.json", 2_385),
    ("schemas/identity-envelope.schema.json", 2_786),
    ("schemas/model-state-handle.schema.json", 2_221),
    ("schemas/runtime-control-plan.schema.json", 4_169),
    ("schemas/source-event.schema.json", 996),
    ("schemas/telemetry-event.schema.json", 2_935),
    ("smoke.py", 80_032),
    ("validation.py", 10_997),
)
_CONTRACTS_WHEEL_RECORD_ROWS = (
    (
        "localai_contracts/__init__.py",
        "ndvRkTKPdPZZ3kzNT2VBznQugtKHy19hdcAD5kUBYKk",
        6_474,
    ),
    (
        "localai_contracts/_integration_bootstrap.py",
        "kKE07g2SS7n0_Zc7hWepa5NQuUU2abA3vTWKYnLrGR4",
        1_684,
    ),
    (
        "localai_contracts/canonical.py",
        "zPT-kY5itDyfH4Q33JX9s6Oig72pdm3DpLapdBU5dTE",
        8_389,
    ),
    (
        "localai_contracts/conformance.py",
        "XO74LTCJswCms-PJiwEPzkYtcB5DjnP1WA5AqwayCPY",
        60_091,
    ),
    (
        "localai_contracts/connector.py",
        "eHRdkduze0J1bcZ9_ZYGlgjC4A39xSzfT_fu5W8PXEs",
        40_852,
    ),
    (
        "localai_contracts/errors.py",
        "I0axR6ZTgk5wMlARG6HcbLw4Cj8EiVMGewSZcIsb0sg",
        1_862,
    ),
    (
        "localai_contracts/inference_lease.py",
        "wRmWHtZWXQUEWHg51H9YwRwZynGbr1fO7uw6ecdjw5I",
        92_469,
    ),
    (
        "localai_contracts/integration.py",
        "yLvoGoIzdxGK-5TaoUmUvfsdENiW0JHq0ClNXv42Cck",
        141_988,
    ),
    (
        "localai_contracts/models.py",
        "4OVt_2hExL9xFFV689A_Z0Ovf6D6FKlcJZZ0JMT6-ew",
        14_235,
    ),
    (
        "localai_contracts/privacy.py",
        "OoI06zXR__kC_aaTKfuQTrUyLOn9ArAzcdPGX1DV2RE",
        2_139,
    ),
    (
        "localai_contracts/profiles.py",
        "jz6OZvgyUJqqyFfeQ2Z-jwfM3Y8J88rB1S6Um7dtc_g",
        876,
    ),
    (
        "localai_contracts/py.typed",
        "bWew9mHgMy8LqMu7RuqQXFXLBxh2CRx0dUbSx-3wE48",
        27,
    ),
    (
        "localai_contracts/smoke.py",
        "TuhsAUnXvOfuKNcC7FCuZ-F1hnA1FdbXy6jKhPrt0So",
        80_032,
    ),
    (
        "localai_contracts/validation.py",
        "9Oo4r96Q2Z-lXLld_lG5gjgL0a7bEDMIdBzxZkLEv5A",
        10_997,
    ),
    (
        "localai_contracts/fixtures/phase0-conformance-v1.json",
        "RYvddUSccCd9ISdh-E5uBKed3AELMUotCkeZgB7xcG0",
        7_551,
    ),
    (
        "localai_contracts/profiles/r9700-qwen3.6-q4.development.json",
        "VRJscT-WKNrOi2edu2czvjUYB5yTxXvSJ-tTkOLPVlQ",
        1_743,
    ),
    (
        "localai_contracts/schemas/common.schema.json",
        "osflOREnlyX8BRZp-VNXmgH6mSx62_BBTEc-uIk8nlQ",
        2_817,
    ),
    (
        "localai_contracts/schemas/component-capability-manifest.schema.json",
        "7lF5odpEcYO1_Dl0h7kltDLISHAjF3UTOCMDFjw2VK4",
        3_386,
    ),
    (
        "localai_contracts/schemas/connector-request.schema.json",
        "yGXyoZVVCicMzgmn8Fm1NhxpzVgaotsEVeDiODTYx2o",
        1_030,
    ),
    (
        "localai_contracts/schemas/connector-response.schema.json",
        "p1oH8_c9PGrMnO_BxLnbfDLJ_2UxBMgqRKNz0aoS9_w",
        1_198,
    ),
    (
        "localai_contracts/schemas/context-bundle.schema.json",
        "JlQEOwln8j4zav5kp1OuiLtYZ8yzitVjJ3k5825W31g",
        4_248,
    ),
    (
        "localai_contracts/schemas/deployment-plan.schema.json",
        "IsnjppsUOYh7PiBQKKmgs5NuPaJrxtYxTxFfCtG2BxY",
        3_452,
    ),
    (
        "localai_contracts/schemas/error-envelope.schema.json",
        "EjeHA8fbtm5jnfnx-D8fpgCMogQeltyKR5agV7MavF8",
        224,
    ),
    (
        "localai_contracts/schemas/generation-receipt.schema.json",
        "MOdABUIHYOs2RWykB1A5GzV7Z_5QCc9DjaFTAThmi08",
        2_385,
    ),
    (
        "localai_contracts/schemas/identity-envelope.schema.json",
        "XPgArZJIV_IXL2n6lGtGBQz9TKosaQp6fh5E3xralOo",
        2_786,
    ),
    (
        "localai_contracts/schemas/model-state-handle.schema.json",
        "rtz3Yj6Lxa0veX0bnZ98VLfZo4r8J_k1spRSPVV8lLk",
        2_221,
    ),
    (
        "localai_contracts/schemas/runtime-control-plan.schema.json",
        "rXOBCtXCWHTV2DmFyWmPxCXvkPr2NOksL8WenMalORg",
        4_169,
    ),
    (
        "localai_contracts/schemas/source-event.schema.json",
        "uzYp4lj8J_FRYwdSHyIp1cARuBO7SU4H-qJDRdUfPC4",
        996,
    ),
    (
        "localai_contracts/schemas/telemetry-event.schema.json",
        "7yRpo4b16z1iC5LyWoq6QL2HomllbEhCOZW0gw9I904",
        2_935,
    ),
    (
        f"{_CONTRACTS_DIST_INFO_DIRECTORY}/licenses/LICENSE",
        "E2PP2MLZk9Llg9FVk0ePYWaereLGDD3muxHLn_poAdg",
        1_087,
    ),
    (
        f"{_CONTRACTS_DIST_INFO_DIRECTORY}/METADATA",
        "4v6JgaC1aTj3yvn9MtmupDKsZRSzh3HW139X9I9T0Pw",
        9_423,
    ),
    (
        f"{_CONTRACTS_DIST_INFO_DIRECTORY}/WHEEL",
        "K260EYznzXsJYBQGqmI8VTxEdiZYNvDZwW9cBh9-_MA",
        91,
    ),
    (
        f"{_CONTRACTS_DIST_INFO_DIRECTORY}/entry_points.txt",
        "-E94CcVjaEnolzF-b-FNVHceQojzMKZ54AZwi9Lgxwg",
        75,
    ),
    (
        f"{_CONTRACTS_DIST_INFO_DIRECTORY}/top_level.txt",
        "p4y1kQQLylo0sN-jWNceUd1_NfWmKpozGvPx5ayqCqk",
        18,
    ),
    (_CONTRACTS_RECORD_PATH, None, None),
)
_CONTRACTS_WHEEL_FILENAME = "localai_contracts-0.2.0a2-py3-none-any.whl"
_CONTRACTS_LAUNCHER_NAME = "localai-integration"
_WINDOWS_DISTLIB_0_3_9_CONSOLE_STUBS = {
    "win32": (
        97_792,
        "6b4195e640a85ac32eb6f9628822a622057df1e459df7c17a12f97aeabc9415b",
    ),
    "win-amd64": (
        108_032,
        "81a618f21cb87db9076134e70388b6e9cb7c2106739011b6a51772d22cae06b7",
    ),
    "win-arm64": (
        182_784,
        "ebc4c06b7d95e74e315419ee7e88e1d0f71e9e9477538c00a93a9ff8c66a6cfc",
    ),
}
_CONTRACTS_LAUNCHER_BODY = (
    b"# -*- coding: utf-8 -*-\n"
    b"import re\n"
    b"import sys\n"
    b"from localai_contracts.integration import main\n"
    b"if __name__ == '__main__':\n"
    b"    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
    b"    sys.exit(main())\n"
)
_CONTRACTS_LAUNCHER_BODY_REMOVESUFFIX = (
    b"import sys\n"
    b"from localai_contracts.integration import main\n"
    b"if __name__ == '__main__':\n"
    b"    sys.argv[0] = sys.argv[0].removesuffix('.exe')\n"
    b"    sys.exit(main())\n"
)
_CONTRACTS_LAUNCHER_BODIES = frozenset(
    {_CONTRACTS_LAUNCHER_BODY, _CONTRACTS_LAUNCHER_BODY_REMOVESUFFIX}
)


@dataclass(frozen=True, slots=True)
class _ContractsRecordRow:
    path: str
    digest: str | None
    size: int | None


class LocalAIContractsUnavailableError(RuntimeError):
    """The exact optional contract package is absent or incompatible."""


@dataclass(frozen=True, slots=True)
class AuthenticatedAuthority:
    """An out-of-band authentication decision made by the embedding host.

    Merely setting ``SourceEvent.trust`` or event metadata never constructs this
    value.  The host-owned verifier callback must return it independently.
    """

    issuer: str
    trusted_for_state: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.issuer, str)
            or not self.issuer
            or self.issuer != self.issuer.strip()
        ):
            raise TypeError("authority issuer must be a non-empty trimmed string")
        if len(self.issuer) > 256:
            raise ValueError("authority issuer exceeds 256 characters")
        if not isinstance(self.trusted_for_state, bool):
            raise TypeError("trusted_for_state must be boolean")


AuthorityVerifier = Callable[[Any], AuthenticatedAuthority | None]
ConnectorFactory = Callable[[], LocalAIConnector]


@dataclass(frozen=True, slots=True)
class _ContractsOrigin:
    package_directory: Path
    initializer: Path


def _validation_failed() -> LocalAIContractsUnavailableError:
    return LocalAIContractsUnavailableError(_CONTRACTS_VALIDATION_ERROR)


def _same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except (OSError, ValueError):
        return False


def _validated_namespace(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError("contracts namespace is not an exact dictionary")
    keys = tuple(value)
    if any(type(key) is not str for key in keys):
        raise ValueError("contracts namespace key is not an exact string")
    return value


def _validate_source_loader(
    loader: object,
    *,
    module_name: str,
    expected_path: Path,
) -> None:
    if type(loader) is not importlib.machinery.SourceFileLoader:
        raise ValueError("contracts source loader type mismatch")
    loader_state = _validated_namespace(vars(loader))
    if sorted(loader_state) != ["name", "path"]:
        raise ValueError("contracts source loader state mismatch")
    loader_name = loader_state.get("name")
    loader_path = loader_state.get("path")
    if (
        type(loader_name) is not str
        or loader_name != module_name
        or type(loader_path) is not str
        or not _same_file(Path(loader_path), expected_path)
    ):
        raise ValueError("contracts source loader origin mismatch")


def _hash_expected_regular_file(
    path: Path,
    *,
    relative: str,
    expected_size: int,
    tree_digest: Any,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.stat(path, follow_symlinks=False)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or _is_link_or_reparse(before)
            or not stat.S_ISREG(opened.st_mode)
            or before.st_dev != opened.st_dev
            or before.st_ino != opened.st_ino
            or before.st_size != expected_size
            or opened.st_size != expected_size
        ):
            raise ValueError("contracts file identity mismatch")
        encoded_name = relative.encode("utf-8")
        tree_digest.update(len(encoded_name).to_bytes(4, "big"))
        tree_digest.update(encoded_name)
        tree_digest.update(expected_size.to_bytes(8, "big"))
        remaining = expected_size
        contents = bytearray()
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                raise ValueError("contracts file truncated")
            tree_digest.update(chunk)
            contents.extend(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValueError("contracts file exceeds expected size")
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
        ):
            raise ValueError("contracts file changed during validation")
        return bytes(contents)
    finally:
        os.close(descriptor)


def _read_bounded_regular_file(path: Path, *, maximum_size: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.stat(path, follow_symlinks=False)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or _is_link_or_reparse(before)
            or not stat.S_ISREG(opened.st_mode)
            or before.st_dev != opened.st_dev
            or before.st_ino != opened.st_ino
            or opened.st_size < 16
            or opened.st_size > maximum_size
        ):
            raise ValueError("contracts bytecode identity mismatch")
        remaining = opened.st_size
        contents = bytearray()
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                raise ValueError("contracts bytecode truncated")
            contents.extend(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValueError("contracts bytecode exceeds expected size")
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
        ):
            raise ValueError("contracts bytecode changed during validation")
        return bytes(contents)
    finally:
        os.close(descriptor)


def _validate_regular_directory(path: Path) -> None:
    directory_stat = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or _is_link_or_reparse(directory_stat)
    ):
        raise ValueError("contracts directory identity mismatch")


def _read_exact_regular_file(
    path: Path,
    *,
    expected_size: int,
    maximum_size: int,
) -> bytes:
    if (
        type(expected_size) is not int
        or expected_size < 0
        or expected_size > maximum_size
    ):
        raise ValueError("contracts recorded file size is invalid")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.stat(path, follow_symlinks=False)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or _is_link_or_reparse(before)
            or not stat.S_ISREG(opened.st_mode)
            or before.st_dev != opened.st_dev
            or before.st_ino != opened.st_ino
            or before.st_size != expected_size
            or opened.st_size != expected_size
        ):
            raise ValueError("contracts recorded file identity mismatch")
        remaining = expected_size
        contents = bytearray()
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                raise ValueError("contracts recorded file truncated")
            contents.extend(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValueError("contracts recorded file exceeds expected size")
        after = os.fstat(descriptor)
        final_path = os.stat(path, follow_symlinks=False)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or final_path.st_dev != opened.st_dev
            or final_path.st_ino != opened.st_ino
            or final_path.st_size != opened.st_size
            or _is_link_or_reparse(final_path)
        ):
            raise ValueError("contracts recorded file changed during validation")
        return bytes(contents)
    finally:
        os.close(descriptor)


def _urlsafe_sha256(value: bytes) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(value).digest())
        .rstrip(b"=")
        .decode("ascii")
    )


def _validate_urlsafe_sha256(value: str) -> None:
    if type(value) is not str or len(value) != 43:
        raise ValueError("contracts RECORD digest is invalid")
    try:
        decoded = base64.b64decode(
            (value + "=").encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (UnicodeEncodeError, ValueError):
        raise ValueError("contracts RECORD digest is invalid") from None
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if len(decoded) != hashlib.sha256().digest_size or canonical != value:
        raise ValueError("contracts RECORD digest is not canonical")


def _expected_record_rows() -> dict[str, _ContractsRecordRow]:
    rows: dict[str, _ContractsRecordRow] = {}
    encoded = bytearray()
    for path, digest, size in _CONTRACTS_WHEEL_RECORD_ROWS:
        if path in rows:
            raise ValueError("contracts expected RECORD contains a duplicate")
        if digest is None:
            if path != _CONTRACTS_RECORD_PATH or size is not None:
                raise ValueError("contracts expected RECORD row is invalid")
            hash_field = ""
            size_field = ""
        else:
            _validate_urlsafe_sha256(digest)
            if type(size) is not int or size < 0:
                raise ValueError("contracts expected RECORD size is invalid")
            hash_field = f"sha256={digest}"
            size_field = str(size)
        encoded.extend(f"{path},{hash_field},{size_field}\n".encode("ascii"))
        rows[path] = _ContractsRecordRow(path, digest, size)
    if (
        len(rows) != 35
        or len(encoded) != _CONTRACTS_WHEEL_RECORD_BYTES
        or hashlib.sha256(encoded).hexdigest() != _CONTRACTS_WHEEL_RECORD_SHA256
    ):
        raise ValueError("contracts expected RECORD manifest mismatch")
    return rows


def _parse_record_size(value: str) -> int | None:
    if value == "":
        return None
    if len(value) > 20 or (
        value != "0"
        and (not value or value[0] == "0" or not value.isdecimal())
    ):
        raise ValueError("contracts RECORD size is invalid")
    if value == "0":
        return 0
    if not value.isascii():
        raise ValueError("contracts RECORD size is invalid")
    return int(value)


def _parse_record_hash(value: str) -> str | None:
    if value == "":
        return None
    prefix = "sha256="
    if not value.startswith(prefix):
        raise ValueError("contracts RECORD hash algorithm mismatch")
    digest = value.removeprefix(prefix)
    _validate_urlsafe_sha256(digest)
    return digest


def _validate_record_path(path: str) -> None:
    if (
        type(path) is not str
        or not path
        or len(path) > 512
        or not path.isascii()
        or path.startswith("/")
        or path.endswith("/")
        or "\\" in path
        or ":" in path
        or any(part in {"", "."} for part in path.split("/"))
        or any(
            not (character.isalnum() or character in "._/-")
            for character in path
        )
    ):
        raise ValueError("contracts RECORD path is invalid")


def _parse_contracts_record(contents: bytes) -> dict[str, _ContractsRecordRow]:
    try:
        text = contents.decode("ascii")
    except UnicodeDecodeError:
        raise ValueError("contracts RECORD is not ASCII") from None
    rows: dict[str, _ContractsRecordRow] = {}
    normalized = contents.replace(b"\r\n", b"\n")
    physical_rows = normalized.split(b"\n")
    line_feed_count = contents.count(b"\n")
    crlf_count = contents.count(b"\r\n")
    if (
        b"\r" in normalized
        or b'"' in normalized
        or crlf_count not in {0, line_feed_count}
        or not normalized.endswith(b"\n")
        or physical_rows[-1] != b""
        or any(row.count(b",") != 2 for row in physical_rows[:-1])
    ):
        raise ValueError("contracts RECORD framing is not canonical")
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        for fields in reader:
            if len(rows) >= _MAX_CONTRACTS_RECORD_ROWS:
                raise ValueError("contracts RECORD row limit exceeded")
            if len(fields) != 3:
                raise ValueError("contracts RECORD row shape is invalid")
            path, hash_field, size_field = fields
            _validate_record_path(path)
            digest = _parse_record_hash(hash_field)
            size = _parse_record_size(size_field)
            if (digest is None) != (size is None):
                raise ValueError("contracts RECORD hash and size are incomplete")
            if path in rows:
                raise ValueError("contracts RECORD contains a duplicate path")
            rows[path] = _ContractsRecordRow(path, digest, size)
    except csv.Error:
        raise ValueError("contracts RECORD CSV is invalid") from None
    if not rows:
        raise ValueError("contracts RECORD is empty")
    return rows


def _validate_recorded_contents(
    path: Path,
    row: _ContractsRecordRow,
    *,
    maximum_size: int,
) -> bytes:
    if row.digest is None or row.size is None:
        raise ValueError("contracts recorded file lacks integrity fields")
    contents = _read_exact_regular_file(
        path,
        expected_size=row.size,
        maximum_size=maximum_size,
    )
    if _urlsafe_sha256(contents) != row.digest:
        raise ValueError("contracts recorded file digest mismatch")
    return contents


def _validate_relative_parent_directories(site_root: Path, relative: str) -> None:
    current = site_root
    for part in relative.split("/")[:-1]:
        if part == "..":
            raise ValueError("contracts recorded path escapes installation root")
        current /= part
        _validate_regular_directory(current)


def _read_and_validate_contracts_record(
    distribution: Any,
) -> tuple[Path, dict[str, _ContractsRecordRow]]:
    site_root = Path(distribution.locate_file(""))
    if not site_root.is_absolute():
        raise ValueError("contracts distribution root is not absolute")
    _validate_regular_directory(site_root)
    dist_info = site_root / _CONTRACTS_DIST_INFO_DIRECTORY
    _validate_regular_directory(dist_info)
    record_path = dist_info / "RECORD"
    located_record = Path(distribution.locate_file(_CONTRACTS_RECORD_PATH))
    if not located_record.is_absolute() or not _same_file(located_record, record_path):
        raise ValueError("contracts RECORD origin mismatch")
    contents = _read_bounded_regular_file(
        record_path,
        maximum_size=_MAX_CONTRACTS_RECORD_BYTES,
    )
    observed = _parse_contracts_record(contents)
    expected = _expected_record_rows()
    for path, expected_row in expected.items():
        if observed.get(path) != expected_row:
            raise ValueError("contracts immutable RECORD row mismatch")

    for path, expected_row in expected.items():
        if (
            not path.startswith(f"{_CONTRACTS_DIST_INFO_DIRECTORY}/")
            or path == _CONTRACTS_RECORD_PATH
        ):
            continue
        _validate_relative_parent_directories(site_root, path)
        _validate_recorded_contents(
            site_root.joinpath(*path.split("/")),
            expected_row,
            maximum_size=_MAX_CONTRACTS_INSTALLER_METADATA_BYTES,
        )
    return site_root, observed


def _record_path_for(site_root: Path, path: Path) -> str:
    try:
        relative = os.path.relpath(path, site_root)
    except ValueError:
        raise ValueError("contracts generated path is on another volume") from None
    return relative.replace(os.sep, "/")


def _launcher_record_paths(site_root: Path) -> dict[str, Path]:
    executable = Path(sys.executable)
    scripts_directory = executable.parent
    expected_directory_name = "Scripts" if sys.platform == "win32" else "bin"
    if (
        not executable.is_absolute()
        or scripts_directory.name != expected_directory_name
    ):
        raise ValueError("contracts scripts directory is not absolute")
    suffix = ".exe" if sys.platform == "win32" else ""
    launcher = scripts_directory / f"{_CONTRACTS_LAUNCHER_NAME}{suffix}"
    return {_record_path_for(site_root, launcher): launcher}


def _normalized_launcher_body(value: bytes) -> bytes:
    normalized = value.replace(b"\r\n", b"\n")
    if b"\r" in normalized:
        raise ValueError("contracts launcher line endings are invalid")
    return normalized


def _expected_windows_launcher_stub() -> tuple[int, str]:
    if sys.maxsize == 2**31 - 1:
        identity = "win32"
    elif sys.maxsize == 2**63 - 1:
        identity = (
            "win-arm64"
            if "(arm64)" in sys.version.casefold()
            else "win-amd64"
        )
    else:
        raise ValueError("contracts launcher architecture is unsupported")
    return _WINDOWS_DISTLIB_0_3_9_CONSOLE_STUBS[identity]


def _validated_windows_launcher_stub(contents: bytes) -> bytes:
    size, expected_sha256 = _expected_windows_launcher_stub()
    if len(contents) <= size:
        raise ValueError("contracts launcher is truncated")
    stub = contents[:size]
    if hashlib.sha256(stub).hexdigest() != expected_sha256:
        raise ValueError("contracts launcher native stub mismatch")
    return stub


def _canonical_launcher_shebang(executable: bytes, *, safe: bool) -> bytes:
    quoted = executable
    if b" " in quoted and not quoted.startswith(b'"'):
        quoted = b'"' + quoted + b'"'
    if safe:
        return (
            b"#!/bin/sh\n'''exec' "
            + quoted
            + b' "$0" "$@"\n'
            + b"' '''\n"
        )
    return b"#!" + quoted + b"\n"


def _launcher_executable_bytes() -> bytes:
    try:
        return sys.executable.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("contracts launcher interpreter path is not UTF-8") from None


def _split_posix_launcher(contents: bytes) -> bytes:
    executable = _launcher_executable_bytes()
    simple = _canonical_launcher_shebang(executable, safe=False)
    maximum = 512 if sys.platform == "darwin" else 127
    interpreter_state = _validated_namespace(vars(sys))
    safe_required = (
        b" " in executable
        or len(simple) > maximum
        or interpreter_state.get("cross_compiling") is True
    )
    expected = _canonical_launcher_shebang(executable, safe=safe_required)
    if not contents.startswith(expected):
        raise ValueError("contracts launcher shebang is invalid")
    return contents[len(expected) :]


def _validate_launcher_contents(contents: bytes) -> None:
    if sys.platform == "win32":
        stub = _validated_windows_launcher_stub(contents)
        remainder = contents[len(stub) :]
        shebang, separator, archive_bytes = remainder.partition(b"\n")
        executable = _launcher_executable_bytes()
        expected_shebang = _canonical_launcher_shebang(
            executable,
            safe=False,
        ).removesuffix(b"\n")
        if not separator or shebang != expected_shebang:
            raise ValueError("contracts launcher interpreter mismatch")
        try:
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                members = archive.infolist()
                if (
                    archive.comment
                    or len(members) != 1
                    or members[0].filename != "__main__.py"
                ):
                    raise ValueError("contracts launcher archive shape mismatch")
                info = members[0]
                if (
                    info.is_dir()
                    or info.flag_bits & 0x1
                    or info.header_offset != 0
                    or info.compress_type != zipfile.ZIP_STORED
                    or info.extra
                    or info.comment
                    or info.file_size > 16 * 1024
                ):
                    raise ValueError("contracts launcher payload is invalid")
                payload = archive.read(info)
        except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
            raise ValueError("contracts launcher archive is invalid") from None
        end_record = archive_bytes.rfind(b"PK\x05\x06")
        if (
            not archive_bytes.startswith(b"PK\x03\x04")
            or end_record < 0
            or len(archive_bytes) < end_record + 22
        ):
            raise ValueError("contracts launcher archive framing mismatch")
        comment_size = int.from_bytes(
            archive_bytes[end_record + 20 : end_record + 22],
            "little",
        )
        if comment_size != 0 or end_record + 22 != len(archive_bytes):
            raise ValueError("contracts launcher archive has trailing bytes")
    else:
        payload = _split_posix_launcher(contents)
    if _normalized_launcher_body(payload) not in _CONTRACTS_LAUNCHER_BODIES:
        raise ValueError("contracts launcher entry point mismatch")


def _validate_launcher_mode(mode: int) -> None:
    if sys.platform != "win32" and mode & stat.S_IXUSR == 0:
        raise ValueError("contracts launcher is not owner-executable")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if type(key) is not str or key in value:
            raise ValueError("contracts direct URL contains a duplicate key")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> None:
    raise ValueError("contracts direct URL contains a nonstandard constant")


def _decode_canonical_file_url_path(path: str) -> bytes:
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("contracts direct URL path is not absolute")
    index = 0
    while index < len(path):
        if path[index] != "%":
            index += 1
            continue
        escape = path[index + 1 : index + 3]
        if len(escape) != 2 or any(
            character not in "0123456789ABCDEF" for character in escape
        ):
            raise ValueError("contracts direct URL escape is not canonical")
        index += 3
    decoded = urllib.parse.unquote_to_bytes(path)
    safe = "/:" if sys.platform == "win32" else "/"
    if urllib.parse.quote_from_bytes(decoded, safe=safe) != path:
        raise ValueError("contracts direct URL path encoding is not canonical")
    parts = decoded.split(b"/")
    if (
        b"\x00" in decoded
        or not parts[-1]
        or any(part in {b"", b".", b".."} for part in parts[1:-1])
        or (sys.platform == "win32" and b"\\" in decoded)
    ):
        raise ValueError("contracts direct URL path is invalid")
    if sys.platform == "win32":
        if (
            len(decoded) < 4
            or decoded[0:1] != b"/"
            or decoded[1:2] not in b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            or decoded[2:4] != b":/"
            or b":" in decoded[3:]
        ):
            raise ValueError("contracts direct URL is not an absolute drive path")
    return decoded


def _validate_direct_url(contents: bytes) -> None:
    try:
        document = json.loads(
            contents.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError("contracts direct URL metadata is invalid") from None
    if type(document) is not dict or sorted(document) != ["archive_info", "url"]:
        raise ValueError("contracts direct URL shape mismatch")
    archive_info = document["archive_info"]
    if (
        type(archive_info) is not dict
        or sorted(archive_info) != ["hash", "hashes"]
        or archive_info.get("hash")
        != f"sha256={LOCALAI_CONTRACTS_WHEEL_SHA256}"
    ):
        raise ValueError("contracts direct URL archive identity mismatch")
    hashes = archive_info.get("hashes")
    if (
        type(hashes) is not dict
        or hashes != {"sha256": LOCALAI_CONTRACTS_WHEEL_SHA256}
    ):
        raise ValueError("contracts direct URL hash set mismatch")
    url = document["url"]
    if (
        type(url) is not str
        or not url.startswith("file:///")
        or len(url) > 4_096
    ):
        raise ValueError("contracts direct URL is invalid")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "file"
        or parsed.netloc != ""
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("contracts direct URL is not a local archive")
    decoded_path = _decode_canonical_file_url_path(parsed.path)
    if (
        decoded_path.rsplit(b"/", 1)[-1]
        != _CONTRACTS_WHEEL_FILENAME.encode("ascii")
    ):
        raise ValueError("contracts direct URL archive filename mismatch")


def _validate_installer_extras(
    site_root: Path,
    observed: dict[str, _ContractsRecordRow],
) -> None:
    expected_paths = {path for path, _digest, _size in _CONTRACTS_WHEEL_RECORD_ROWS}
    extras = {path: row for path, row in observed.items() if path not in expected_paths}
    installer_path = f"{_CONTRACTS_DIST_INFO_DIRECTORY}/INSTALLER"
    requested_path = f"{_CONTRACTS_DIST_INFO_DIRECTORY}/REQUESTED"
    direct_url_path = f"{_CONTRACTS_DIST_INFO_DIRECTORY}/direct_url.json"
    if installer_path not in extras or direct_url_path not in extras:
        raise ValueError("contracts required installer metadata is absent")

    launcher_paths = _launcher_record_paths(site_root)
    present_launchers = set(extras).intersection(launcher_paths)
    if len(present_launchers) != 1:
        raise ValueError("contracts launcher inventory mismatch")
    allowed = {installer_path, direct_url_path, *present_launchers}
    if requested_path in extras:
        allowed.add(requested_path)
    if set(extras) != allowed:
        raise ValueError("contracts RECORD contains an unrecognized extra row")

    for metadata_path in (installer_path, direct_url_path):
        _validate_relative_parent_directories(site_root, metadata_path)
    installer = _validate_recorded_contents(
        site_root.joinpath(*installer_path.split("/")),
        extras[installer_path],
        maximum_size=_MAX_CONTRACTS_INSTALLER_METADATA_BYTES,
    )
    if installer != b"pip\n":
        raise ValueError("contracts installer marker mismatch")
    direct_url = _validate_recorded_contents(
        site_root.joinpath(*direct_url_path.split("/")),
        extras[direct_url_path],
        maximum_size=_MAX_CONTRACTS_INSTALLER_METADATA_BYTES,
    )
    _validate_direct_url(direct_url)

    if requested_path in extras:
        _validate_relative_parent_directories(site_root, requested_path)
        requested = _validate_recorded_contents(
            site_root.joinpath(*requested_path.split("/")),
            extras[requested_path],
            maximum_size=_MAX_CONTRACTS_INSTALLER_METADATA_BYTES,
        )
        if requested != b"":
            raise ValueError("contracts REQUESTED marker is not empty")
    else:
        requested_file = site_root.joinpath(*requested_path.split("/"))
        try:
            os.stat(requested_file, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("unrecorded contracts REQUESTED marker is present")

    launcher_record = present_launchers.pop()
    launcher_path = launcher_paths[launcher_record]
    _validate_regular_directory(launcher_path.parent)
    launcher_contents = _validate_recorded_contents(
        launcher_path,
        extras[launcher_record],
        maximum_size=_MAX_CONTRACTS_LAUNCHER_BYTES,
    )
    launcher_stat = os.stat(launcher_path, follow_symlinks=False)
    _validate_launcher_mode(launcher_stat.st_mode)
    _validate_launcher_contents(launcher_contents)


def _validate_bytecode_cache(
    path: Path,
    *,
    source: bytes,
    source_path: Path,
    optimize: int,
) -> None:
    cached = _read_bounded_regular_file(
        path,
        maximum_size=_MAX_CONTRACTS_BYTECODE_BYTES,
    )
    flags = int.from_bytes(cached[4:8], "little")
    expected_code = compile(
        source,
        str(source_path),
        "exec",
        dont_inherit=True,
        optimize=optimize,
    )
    if (
        cached[:4] != importlib.util.MAGIC_NUMBER
        or flags not in {0, 1, 3}
        or cached[16:] != marshal.dumps(expected_code)
    ):
        raise ValueError("contracts bytecode does not match verified source")


def _validate_installed_tree_shape(
    package_directory: Path,
) -> tuple[tuple[Path, str, int], ...]:
    expected_files = {path for path, _size in _CONTRACTS_INSTALLED_FILES}
    expected_directories: set[str] = set()
    for relative in expected_files:
        parts = relative.split("/")
        expected_directories.update(
            "/".join(parts[:index]) for index in range(1, len(parts))
        )
    expected_cache_names = {
        Path(
            importlib.util.cache_from_source(
                str(package_directory / relative),
                optimization=tag,
            )
        ).name: (
            relative,
            optimize,
        )
        for relative in expected_files
        if relative.endswith(".py") and "/" not in relative
        for optimize, tag in ((0, ""), (1, "1"), (2, "2"))
    }
    cached_files: list[tuple[Path, str, int]] = []
    observed_files: set[str] = set()
    pending = [(package_directory, "")]
    entry_count = 0
    while pending:
        directory, prefix = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                entry_count += 1
                if entry_count > _MAX_CONTRACTS_TREE_ENTRIES:
                    raise ValueError("contracts package tree exceeds limit")
                relative = f"{prefix}/{entry.name}" if prefix else entry.name
                entry_stat = entry.stat(follow_symlinks=False)
                if _is_link_or_reparse(entry_stat):
                    raise ValueError("contracts package tree contains a link")
                if stat.S_ISDIR(entry_stat.st_mode):
                    if relative == "__pycache__":
                        with os.scandir(entry.path) as cache_entries:
                            for cached in cache_entries:
                                entry_count += 1
                                if entry_count > _MAX_CONTRACTS_TREE_ENTRIES:
                                    raise ValueError(
                                        "contracts package tree exceeds limit"
                                    )
                                cached_stat = cached.stat(follow_symlinks=False)
                                if (
                                    _is_link_or_reparse(cached_stat)
                                    or not stat.S_ISREG(cached_stat.st_mode)
                                ):
                                    raise ValueError(
                                        "contracts bytecode cache is unexpected"
                                    )
                                cache_identity = expected_cache_names.get(cached.name)
                                if cache_identity is None:
                                    raise ValueError(
                                        "contracts bytecode cache is unexpected"
                                    )
                                cached_files.append((Path(cached.path), *cache_identity))
                        continue
                    if relative not in expected_directories:
                        raise ValueError("contracts package directory is unexpected")
                    pending.append((Path(entry.path), relative))
                    continue
                if (
                    not stat.S_ISREG(entry_stat.st_mode)
                    or relative not in expected_files
                ):
                    raise ValueError("contracts package file is unexpected")
                observed_files.add(relative)
    if observed_files != expected_files:
        raise ValueError("contracts package file set mismatch")
    return tuple(cached_files)


def _module_file_for_name(name: str) -> str | None:
    if name == _CONTRACTS_IMPORT_NAME:
        return "__init__.py"
    prefix = f"{_CONTRACTS_IMPORT_NAME}."
    if not name.startswith(prefix):
        return None
    relative = name.removeprefix(prefix).replace(".", "/")
    expected_files = {path for path, _size in _CONTRACTS_INSTALLED_FILES}
    module_file = f"{relative}.py"
    if module_file in expected_files:
        return module_file
    package_file = f"{relative}/__init__.py"
    if package_file in expected_files:
        return package_file
    return None


def _validate_loaded_modules(origin: _ContractsOrigin) -> None:
    prefix = f"{_CONTRACTS_IMPORT_NAME}."
    modules = vars(sys).get("modules")
    if type(modules) is not dict:
        raise ValueError("invalid interpreter module registry")
    for name, module in tuple(modules.items()):
        if type(name) is not str:
            raise ValueError("invalid interpreter module name")
        if name != _CONTRACTS_IMPORT_NAME and not name.startswith(prefix):
            continue
        if type(module) is not types.ModuleType:
            raise ValueError("invalid preloaded contracts module")
        module_state = _validated_namespace(vars(module))
        if module_state.get("__name__") != name:
            raise ValueError("invalid preloaded contracts module")
        relative = _module_file_for_name(name)
        if relative is None:
            raise ValueError("unexpected preloaded contracts module")
        expected = origin.package_directory.joinpath(*relative.split("/"))
        module_spec = module_state.get("__spec__")
        if type(module_spec) is not importlib.machinery.ModuleSpec:
            raise ValueError("preloaded contracts module spec mismatch")
        spec_state = _validated_namespace(vars(module_spec))
        module_origin = spec_state.get("origin")
        module_file = module_state.get("__file__")
        if (
            module_state.get("__loader__") is not spec_state.get("loader")
            or type(module_origin) is not str
            or type(module_file) is not str
            or not _same_file(Path(module_origin), expected)
            or not _same_file(Path(module_file), expected)
        ):
            raise ValueError("preloaded contracts module origin mismatch")
        _validate_source_loader(
            spec_state.get("loader"),
            module_name=name,
            expected_path=expected,
        )
        expected_package = (
            _CONTRACTS_IMPORT_NAME
            if name == _CONTRACTS_IMPORT_NAME
            else name.rpartition(".")[0]
        )
        if module_state.get("__package__") != expected_package:
            raise ValueError("preloaded contracts module package mismatch")
        if name == _CONTRACTS_IMPORT_NAME:
            search_locations = module_state.get("__path__")
            spec_search_locations = spec_state.get("submodule_search_locations")
            if (
                type(search_locations) is not list
                or len(search_locations) != 1
                or type(search_locations[0]) is not str
                or type(spec_search_locations) is not list
                or len(spec_search_locations) != 1
                or type(spec_search_locations[0]) is not str
                or not _same_file(
                    Path(search_locations[0]),
                    origin.package_directory,
                )
                or not _same_file(
                    Path(spec_search_locations[0]),
                    origin.package_directory,
                )
            ):
                raise ValueError("preloaded contracts package path mismatch")
        elif spec_state.get("submodule_search_locations") is not None:
            raise ValueError("preloaded contracts submodule is a package")


def _preflight_contracts_origin() -> _ContractsOrigin:
    try:
        interpreter_state = _validated_namespace(vars(sys))
        if (
            type(interpreter_state.get("modules")) is not dict
            or interpreter_state.get("pycache_prefix") is not None
        ):
            raise ValueError("external bytecode cache prefix is unsupported")
        modules = interpreter_state["modules"]
        if any(type(name) is not str for name in tuple(modules)):
            raise ValueError("invalid interpreter module name")
        distributions = list(
            importlib.metadata.distributions(name=LOCALAI_CONTRACTS_DISTRIBUTION)
        )
        if len(distributions) != 1:
            raise ValueError("contracts distribution is absent or ambiguous")
        distribution = distributions[0]
        site_root, record_rows = _read_and_validate_contracts_record(distribution)
        distribution_name = distribution.metadata.get("Name")
        if (
            not isinstance(distribution_name, str)
            or distribution_name.casefold() != LOCALAI_CONTRACTS_DISTRIBUTION
            or distribution.version != LOCALAI_CONTRACTS_VERSION
        ):
            raise ValueError("contracts distribution identity mismatch")

        initializer = site_root / _CONTRACTS_IMPORT_NAME / "__init__.py"
        package_directory = initializer.parent
        _validate_regular_directory(package_directory)

        spec = importlib.util.find_spec(_CONTRACTS_IMPORT_NAME)
        if type(spec) is not importlib.machinery.ModuleSpec:
            raise ValueError("contracts import spec is invalid")
        spec_state = _validated_namespace(vars(spec))
        spec_origin = spec_state.get("origin")
        spec_loader = spec_state.get("loader")
        spec_locations = spec_state.get("submodule_search_locations")
        if (
            spec_state.get("name") != _CONTRACTS_IMPORT_NAME
            or spec_state.get("_set_fileattr") is not True
            or type(spec_origin) is not str
            or type(spec_loader) is not importlib.machinery.SourceFileLoader
            or type(spec_locations) is not list
        ):
            raise ValueError("contracts import spec is invalid")
        if (
            len(spec_locations) != 1
            or type(spec_locations[0]) is not str
            or not _same_file(Path(spec_origin), initializer)
            or not _same_file(Path(spec_locations[0]), package_directory)
        ):
            raise ValueError("contracts import spec origin mismatch")
        import_initializer = Path(spec_origin)
        import_package_directory = Path(spec_locations[0])
        _validate_source_loader(
            spec_loader,
            module_name=_CONTRACTS_IMPORT_NAME,
            expected_path=import_initializer,
        )
        if (
            not import_initializer.is_absolute()
            or not import_package_directory.is_absolute()
        ):
            raise ValueError("contracts import spec paths are not absolute")
        if not _same_file(import_package_directory.parent, site_root):
            raise ValueError("contracts package root does not match distribution")
        origin = _ContractsOrigin(
            package_directory=import_package_directory,
            initializer=import_initializer,
        )
        cached_files = _validate_installed_tree_shape(origin.package_directory)
        tree_digest = hashlib.sha256()
        verified_sources: dict[str, bytes] = {}
        for relative, expected_size in _CONTRACTS_INSTALLED_FILES:
            record_path = f"{_CONTRACTS_IMPORT_NAME}/{relative}"
            recorded = record_rows[record_path]
            expected = origin.package_directory.joinpath(*relative.split("/"))
            contents = _hash_expected_regular_file(
                expected,
                relative=relative,
                expected_size=expected_size,
                tree_digest=tree_digest,
            )
            if (
                recorded.digest is None
                or recorded.size != expected_size
                or _urlsafe_sha256(contents) != recorded.digest
            ):
                raise ValueError("contracts package file RECORD mismatch")
            if relative.endswith(".py"):
                verified_sources[relative] = contents
        if tree_digest.hexdigest() != _CONTRACTS_INSTALLED_TREE_SHA256:
            raise ValueError("contracts installed tree digest mismatch")
        for cached_path, source_relative, optimize in cached_files:
            _validate_bytecode_cache(
                cached_path,
                source=verified_sources[source_relative],
                source_path=origin.package_directory.joinpath(
                    *source_relative.split("/")
                ),
                optimize=optimize,
            )
        _validate_installer_extras(site_root, record_rows)
        _validate_loaded_modules(origin)
        return origin
    except Exception:
        raise _validation_failed() from None


@lru_cache(maxsize=1)
def _load_contracts() -> Any:
    origin = _preflight_contracts_origin()
    try:
        contracts = importlib.import_module(_CONTRACTS_IMPORT_NAME)
    except Exception:
        raise _validation_failed() from None
    if (
        sys.modules.get(_CONTRACTS_IMPORT_NAME) is not contracts
        or _preflight_contracts_origin() != origin
    ):
        raise _validation_failed() from None
    try:
        contracts_state = _validated_namespace(vars(contracts))
        if contracts_state.get("__version__") != LOCALAI_CONTRACTS_VERSION:
            raise ValueError("contracts runtime version mismatch")
        if (
            contracts_state.get("PROTOCOL_VERSION")
            != LOCALAI_CONTRACTS_PROTOCOL_VERSION
        ):
            raise ValueError("contracts protocol version mismatch")
    except Exception:
        raise _validation_failed() from None
    required = (
        "ComponentCapabilityManifest",
        "ConnectorRequest",
        "ConnectorResponse",
        "ConnectorServer",
        "ContextBundle",
        "DEFAULT_PARSE_LIMITS",
        "NdjsonConnectorServer",
        "ParseLimits",
        "SourceEvent",
        "SubjectOperationProbe",
        "SubjectOperationPurpose",
        "UnsupportedOperationError",
        "bounded_canonical_bytes",
        "canonical_bytes",
        "parse_json",
    )
    try:
        if any(name not in contracts_state for name in required):
            raise ValueError("contracts API mismatch")
    except Exception:
        raise _validation_failed() from None
    return contracts


def _component_version() -> str:
    from . import __version__

    return __version__


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_record(value: str) -> dict[str, str]:
    return {"algorithm": "sha256", "value": value}


def _stable_id(kind: str, *parts: object) -> str:
    body = "\x00".join(str(part) for part in parts).encode("utf-8")
    return f"ctxc-{kind}-{_digest(body)}"


class LocalAIContractsAdapter:
    """Execute the one canonical, non-inference ``context.compile`` operation."""

    def __init__(
        self,
        *,
        connector_factory: ConnectorFactory | None = None,
        authority_verifier: AuthorityVerifier | None = None,
        limits: Any | None = None,
    ) -> None:
        contracts = _load_contracts()
        if connector_factory is not None and not callable(connector_factory):
            raise TypeError("connector_factory must be callable")
        if authority_verifier is not None and not callable(authority_verifier):
            raise TypeError("authority_verifier must be callable")
        selected_limits = (
            contracts.DEFAULT_PARSE_LIMITS if limits is None else limits
        )
        if type(selected_limits) is not contracts.ParseLimits:
            raise TypeError("limits must be localai_contracts.ParseLimits")

        self._contracts = contracts
        self._connector_factory = connector_factory or LocalAIConnector
        self._authority_verifier = authority_verifier
        self._limits = selected_limits
        self._request_server = contracts.ConnectorServer(
            self,
            limits=self._limits,
        )
        self._request_lock = threading.RLock()

    @property
    def limits(self) -> Any:
        """Return the immutable parser/frame limits shared by both transports."""

        return self._limits

    def get_manifest(self) -> Any:
        """Return an actual validated ``ComponentCapabilityManifest``."""

        contracts = self._contracts
        manifest = contracts.ComponentCapabilityManifest(
            component_name="loss-resistant-context-compiler",
            component_version=_component_version(),
            supported_operations=[CONTEXT_COMPILE_OPERATION],
            supported_schema_versions={
                name: [LOCALAI_CONTRACTS_PROTOCOL_VERSION]
                for name in (
                    "ComponentCapabilityManifest",
                    "ConnectorRequest",
                    "ConnectorResponse",
                    "ErrorEnvelope",
                    "SourceEvent",
                    "ContextBundle",
                )
            },
            restrictions={"models": [], "runtimes": [], "backends": []},
            operation_capabilities=[
                {
                    "operation": CONTEXT_COMPILE_OPERATION,
                    "evidence": "executed",
                    "limitations": [
                        "Canonical trust is not authentication.",
                        "Only independently authenticated exact source spans may "
                        "enter trusted active memory.",
                        "Accounting covers projected span-content components only.",
                    ],
                    "disabled_reason": None,
                }
            ],
            limitations=[
                {
                    "code": "no_inference",
                    "message": "The adapter performs no model inference.",
                    "operation": CONTEXT_COMPILE_OPERATION,
                },
                {
                    "code": "no_semantic_completeness",
                    "message": "The projection makes no semantic-completeness claim.",
                    "operation": CONTEXT_COMPILE_OPERATION,
                },
                {
                    "code": "private_certificate_not_projected",
                    "message": (
                        "The private CtxC artifact and detector-scoped retention "
                        "certificate remain outside the canonical projection."
                    ),
                    "operation": CONTEXT_COMPILE_OPERATION,
                },
            ],
        )
        manifest.validate()
        return manifest

    def handle(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Execute an already-negotiated canonical operation.

        Callers should use ``ConnectorServer``, :meth:`handle_request`, or
        :meth:`serve_ndjson`; those endpoints enforce handshake-first state.
        """

        if operation != CONTEXT_COMPILE_OPERATION:
            raise self._contracts.UnsupportedOperationError(
                "the canonical operation is unsupported"
            )
        bounded = self._bounded_json(payload, label="context.compile payload")
        if not isinstance(bounded, dict) or set(bounded) != {"source_events"}:
            raise ValueError(
                "context.compile payload must contain only source_events"
            )
        documents = bounded["source_events"]
        if (
            not isinstance(documents, list)
            or not documents
            or len(documents) > MAX_CONTEXT_SOURCE_EVENTS
        ):
            raise ValueError(
                f"context.compile requires 1 to {MAX_CONTEXT_SOURCE_EVENTS} "
                "source events"
            )

        events, private_events, authenticated = self._map_source_events(documents)
        payload_digest = _digest(
            self._contracts.bounded_canonical_bytes(bounded, limits=self._limits)
        )
        connector = self._connector_factory()
        if not isinstance(connector, LocalAIConnector):
            raise TypeError("connector_factory must return LocalAIConnector")
        rich_bundle = connector.compile_memory(
            session_id=f"localai-contracts-{payload_digest}",
            events=private_events,
        )
        replay = connector.verify_memory(rich_bundle, events=private_events)
        if replay.get("passed") is not True:
            raise ValueError("private CtxC replay verification failed")

        projected = self._project_bundle(
            rich_bundle=rich_bundle,
            events=events,
            authenticated=authenticated,
            connector=connector,
        )
        encoded = self._contracts.bounded_canonical_bytes(
            projected.to_dict(),
            limits=self._limits,
        )
        parsed = self._contracts.parse_json(encoded, limits=self._limits)
        checked = self._contracts.ContextBundle.from_dict(parsed)
        return checked.to_dict()

    def handle_request(self, request: Any) -> Any:
        """Accept an actual ``ConnectorRequest`` and return ``ConnectorResponse``."""

        if type(request) is not self._contracts.ConnectorRequest:
            raise TypeError(
                "request must be an actual localai_contracts.ConnectorRequest"
            )
        with self._request_lock:
            response = self._request_server.handle(request)
        if type(response) is not self._contracts.ConnectorResponse:
            raise TypeError("connector server returned an invalid response type")
        return response

    def serve_ndjson(self, reader: Any, writer: Any) -> int:
        """Serve bounded canonical NDJSON until EOF and return records consumed."""

        server = self._contracts.NdjsonConnectorServer(
            self,
            limits=self._limits,
        )
        consumed = 0
        while server.serve_once(reader, writer):
            consumed += 1
        return consumed

    def build_phase0_probe(self, source_events: Sequence[Any]) -> Any:
        """Build the mandatory typed non-inference probe for the wheel runner."""

        if (
            not isinstance(source_events, Sequence)
            or isinstance(source_events, (str, bytes, bytearray))
            or not source_events
            or len(source_events) > MAX_CONTEXT_SOURCE_EVENTS
        ):
            raise ValueError(
                f"probe requires 1 to {MAX_CONTEXT_SOURCE_EVENTS} SourceEvents"
            )
        documents: list[dict[str, Any]] = []
        for event in source_events:
            if type(event) is not self._contracts.SourceEvent:
                raise TypeError(
                    "probe events must be actual localai_contracts.SourceEvent values"
                )
            event.validate()
            documents.append(event.to_dict())
        payload = {"source_events": documents}
        expected = self._contracts.ContextBundle.from_dict(
            self.handle(CONTEXT_COMPILE_OPERATION, payload)
        )
        return self._contracts.SubjectOperationProbe(
            purpose=self._contracts.SubjectOperationPurpose.CONTEXT_BUNDLE_COMPILE,
            operation=CONTEXT_COMPILE_OPERATION,
            payload=payload,
            expected_response=expected,
        )

    def _bounded_json(self, value: Any, *, label: str) -> Any:
        try:
            encoded = self._contracts.bounded_canonical_bytes(value, limits=self._limits)
            return self._contracts.parse_json(encoded, limits=self._limits)
        except self._contracts.LocalAIContractsError:
            raise
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} is not bounded canonical JSON") from exc

    def _map_source_events(
        self,
        documents: list[Any],
    ) -> tuple[list[Any], list[PrivateSourceEvent], dict[str, bool]]:
        contracts = self._contracts
        parsed_events: list[Any] = []
        private_events: list[PrivateSourceEvent] = []
        authenticated: dict[str, bool] = {}
        seen_ids: set[str] = set()
        seen_sequences: set[int] = set()

        for document in documents:
            if not isinstance(document, dict):
                raise TypeError("source_events entries must be objects")
            event = contracts.SourceEvent.from_dict(document)
            event.validate()
            if event.to_dict() != document:
                raise ValueError("SourceEvent normalization changed the document")
            if event.source_event_id in seen_ids:
                raise ValueError("source_events contains a duplicate id")
            if event.sequence in seen_sequences:
                raise ValueError("source_events contains a duplicate sequence")
            seen_ids.add(event.source_event_id)
            seen_sequences.add(event.sequence)

            authority: AuthenticatedAuthority | None = None
            if event.trust == "trusted" and self._authority_verifier is not None:
                decision_event = contracts.SourceEvent.from_dict(event.to_dict())
                decision = self._authority_verifier(decision_event)
                if decision is not None and type(decision) is not AuthenticatedAuthority:
                    raise TypeError(
                        "authority_verifier must return AuthenticatedAuthority or None"
                    )
                authority = decision
                if (
                    authority is not None
                    and authority.trusted_for_state
                    and event.role != "tool"
                ):
                    raise ValueError(
                        "trusted_for_state is restricted to authenticated tool events"
                    )

            is_authenticated = authority is not None
            authenticated[event.source_event_id] = is_authenticated
            canonical_document = event.to_dict()
            metadata = {
                "localai_contracts_event": {
                    "schema_version": event.schema_version,
                    "original_role": event.role,
                    "declared_trust": event.trust,
                    "metadata": canonical_document["metadata"],
                    "content_hash": canonical_document["content_hash"],
                    "independently_authenticated": is_authenticated,
                    "authority_issuer": (
                        authority.issuer if authority is not None else None
                    ),
                }
            }
            private_authority = (
                {
                    "authenticated": True,
                    "trusted_for_state": authority.trusted_for_state,
                    "issuer": authority.issuer,
                }
                if authority is not None
                else {"authenticated": False, "trusted_for_state": False}
            )
            private_role = event.role if is_authenticated else "assistant"
            content_digest = event.content_hash
            private_events.append(
                PrivateSourceEvent(
                    id=event.source_event_id,
                    sequence=event.sequence,
                    role=private_role,
                    content=event.content,
                    metadata=metadata,
                    authority=private_authority,
                    provenance={
                        "producer": "localai-contracts-adapter",
                        "schema_version": event.schema_version,
                        "source_event_id": event.source_event_id,
                        "original_role": event.role,
                        "declared_trust": event.trust,
                    },
                    content_sha256=(
                        content_digest["value"]
                        if content_digest["algorithm"] == "sha256"
                        else ""
                    ),
                )
            )
            parsed_events.append(event)

        return parsed_events, private_events, authenticated

    def _project_bundle(
        self,
        *,
        rich_bundle: Any,
        events: list[Any],
        authenticated: dict[str, bool],
        connector: LocalAIConnector,
    ) -> Any:
        events_by_id = {event.source_event_id: event for event in events}
        trusted_spans: list[dict[str, Any]] = []
        retrieved_spans: list[dict[str, Any]] = []
        provenance: list[dict[str, Any]] = []

        for event in sorted(
            events,
            key=lambda item: (item.sequence, item.source_event_id),
        ):
            content_hash = _sha256_text(event.content)
            span_id = _stable_id(
                "source",
                event.source_event_id,
                event.sequence,
                content_hash,
            )
            span = {
                "span_id": span_id,
                "source_event_id": event.source_event_id,
                "start_byte": 0,
                "end_byte": len(event.content.encode("utf-8")),
                "content": event.content,
                "content_hash": _digest_record(content_hash),
                "token_count": None,
            }
            retrieved_spans.append(span)
            provenance.append(
                {
                    "item_id": span_id,
                    "source_event_id": event.source_event_id,
                    "transform": "exact_source_event_projection",
                    "producer": {
                        "name": "loss-resistant-context-compiler",
                        "version": _component_version(),
                    },
                }
            )

        rich_spans = rich_bundle.trusted_memory.get("source_spans")
        if not isinstance(rich_spans, list):
            raise TypeError("private trusted-memory source spans are invalid")
        for raw in sorted(
            rich_spans,
            key=lambda item: (
                item.get("source_id", ""),
                item.get("start", -1),
                item.get("end", -1),
                item.get("quote_sha256", ""),
            ),
        ):
            if not isinstance(raw, dict):
                raise TypeError("private trusted-memory span must be an object")
            source_id = raw.get("source_id")
            source = events_by_id.get(source_id)
            if source is None:
                raise ValueError("private span references an unknown source event")
            start = raw.get("start")
            end = raw.get("end")
            quote = raw.get("quote")
            if (
                isinstance(start, bool)
                or not isinstance(start, int)
                or isinstance(end, bool)
                or not isinstance(end, int)
                or start < 0
                or end < start
                or end > len(source.content)
                or not isinstance(quote, str)
            ):
                raise ValueError("private span has invalid character offsets")
            if source.content[start:end] != quote:
                raise ValueError("private span does not match exact source content")
            quote_hash = _sha256_text(quote)
            if raw.get("quote_sha256") != quote_hash:
                raise ValueError("private span quote digest mismatch")
            start_byte = len(source.content[:start].encode("utf-8"))
            end_byte = len(source.content[:end].encode("utf-8"))
            span_id = _stable_id(
                "active",
                source_id,
                start_byte,
                end_byte,
                quote_hash,
            )
            span = {
                "span_id": span_id,
                "source_event_id": source_id,
                "start_byte": start_byte,
                "end_byte": end_byte,
                "content": quote,
                "content_hash": _digest_record(quote_hash),
                "token_count": None,
            }
            if authenticated.get(source_id) is True:
                trusted_spans.append(span)
            else:
                retrieved_spans.append(span)
            provenance.append(
                {
                    "item_id": span_id,
                    "source_event_id": source_id,
                    "transform": "verified_active_exact_source_span_projection",
                    "producer": {
                        "name": "loss-resistant-context-compiler",
                        "version": _component_version(),
                    },
                }
            )

        if len(trusted_spans) + len(retrieved_spans) > MAX_CONTEXT_SPANS:
            raise ValueError("canonical ContextBundle exceeds the span count bound")

        exact_counter = connector.token_counter
        if exact_counter is None:
            counter = _estimated_tokens
            accounting_kind = "estimated"
            accounting_method = (
                "ctxc_projected_span_contents_estimated_"
                "ceil_unicode_characters_divided_by_4;framing_excluded"
            )
        else:
            counter = exact_counter
            accounting_kind = "exact"
            identity = connector.token_counter_id
            if not isinstance(identity, str) or not identity:
                raise ValueError("exact token counter lacks a stable identity")
            accounting_method = (
                "ctxc_projected_span_contents_exact_"
                f"tokenizer_identity_sha256={_sha256_text(identity)};"
                "framing_excluded"
            )

        for span in trusted_spans + retrieved_spans:
            span["token_count"] = counter(span["content"])
        trusted_tokens = sum(span["token_count"] for span in trusted_spans)
        retrieved_tokens = sum(span["token_count"] for span in retrieved_spans)

        omissions, overflow = self._project_omissions_and_overflow(
            rich_bundle,
            exact_accounting=exact_counter is not None,
        )
        policy_digest = rich_bundle.bindings.get("compiler_policy_sha256")
        if not isinstance(policy_digest, str) or len(policy_digest) != 64:
            raise ValueError("private compiler policy identity is invalid")
        private_source_digest = rich_bundle.bindings.get("source_digest")
        if not isinstance(private_source_digest, str) or len(private_source_digest) != 64:
            raise ValueError("private source-set identity is invalid")
        canonical_source_documents = [
            event.to_dict()
            for event in sorted(
                events,
                key=lambda item: (item.sequence, item.source_event_id),
            )
        ]
        source_bytes = self._contracts.bounded_canonical_bytes(
            canonical_source_documents,
            limits=self._limits,
        )
        source_digest = _digest(source_bytes)

        body = {
            "trusted_active_memory": trusted_spans,
            "untrusted_retrieved_spans": retrieved_spans,
            "provenance": sorted(
                provenance,
                key=lambda item: (
                    item["source_event_id"],
                    item["item_id"],
                    item["transform"],
                ),
            ),
            "token_accounting": {
                "kind": accounting_kind,
                "method": accounting_method,
                "trusted_tokens": trusted_tokens,
                "retrieved_tokens": retrieved_tokens,
                "total_tokens": trusted_tokens + retrieved_tokens,
            },
            "omissions": omissions,
            "overflow": overflow,
            "policy_identity": {
                "name": "ctxc-private-compilation-policy",
                "version": "localai-context-bundle-0.1",
                "digest": _digest_record(policy_digest),
            },
            "source_store_identity": {
                "name": "localai-contracts-exact-source-events",
                "version": LOCALAI_CONTRACTS_PROTOCOL_VERSION,
                "digest": _digest_record(source_digest),
            },
        }
        body_bytes = self._contracts.bounded_canonical_bytes(
            body,
            limits=self._limits,
        )
        bundle_digest = _digest(
            _PROJECTION_VERSION.encode("utf-8")
            + b"\x00"
            + body_bytes
        )
        projected = self._contracts.ContextBundle(
            bundle_id=f"ctxc-context-{bundle_digest}",
            **body,
        )
        projected.validate()
        return projected

    @staticmethod
    def _project_omissions_and_overflow(
        rich_bundle: Any,
        *,
        exact_accounting: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        raw_entries = rich_bundle.trusted_memory.get(
            "omitted_or_overflowed_protected_items"
        )
        if not isinstance(raw_entries, list):
            raise TypeError("private protected omission ledger is invalid")
        omissions_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        overflow_values: set[int] = set()
        for entry in raw_entries:
            if not isinstance(entry, dict):
                raise TypeError("private protected omission entry is invalid")
            reason = entry.get("reason")
            item = entry.get("item")
            if not isinstance(item, dict):
                raise TypeError("private omitted item is invalid")
            provenance = item.get("provenance")
            if not isinstance(provenance, list) or not provenance:
                raise ValueError("private omitted item lacks exact provenance")
            source_ids = sorted(
                {
                    span.get("source_id")
                    for span in provenance
                    if isinstance(span, dict)
                    and isinstance(span.get("source_id"), str)
                    and span.get("source_id")
                }
            )
            if not source_ids:
                raise ValueError("private omitted item has no valid source event")
            if reason == "omitted":
                for source_id in source_ids:
                    key = (source_id, "private_protected_item_not_selected")
                    omissions_by_key[key] = {
                        "source_event_id": source_id,
                        "reason": key[1],
                        "estimated_tokens": None,
                    }
            elif reason == "protected_budget_overflow":
                value = entry.get("overflow_tokens")
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise ValueError("private protected overflow is invalid")
                overflow_values.add(value)
            else:
                raise ValueError("private protected omission reason is unsupported")

        if len(overflow_values) > 1:
            raise ValueError("private protected overflow totals disagree")
        if overflow_values:
            amount = next(iter(overflow_values))
            overflow = {
                "occurred": True,
                "dropped_items": 0,
                "dropped_tokens": amount if exact_accounting else None,
                "reason": "private_protected_budget_overflow_retained",
            }
        else:
            overflow = {
                "occurred": False,
                "dropped_items": 0,
                "dropped_tokens": 0,
                "reason": None,
            }
        omissions = [
            omissions_by_key[key] for key in sorted(omissions_by_key)
        ]
        return omissions, overflow


def _estimated_tokens(text: str) -> int:
    if not isinstance(text, str):
        raise TypeError("projected span content must be a string")
    return 0 if not text else max(1, (len(text) + 3) // 4)


__all__ = [
    "AuthenticatedAuthority",
    "AuthorityVerifier",
    "CONTEXT_COMPILE_OPERATION",
    "LOCALAI_CONTRACTS_DISTRIBUTION",
    "LOCALAI_CONTRACTS_PROTOCOL_VERSION",
    "LOCALAI_CONTRACTS_SOURCE_COMMIT",
    "LOCALAI_CONTRACTS_VERSION",
    "LOCALAI_CONTRACTS_WHEEL_SHA256",
    "LocalAIContractsAdapter",
    "LocalAIContractsUnavailableError",
    "MAX_CONTEXT_SOURCE_EVENTS",
]
