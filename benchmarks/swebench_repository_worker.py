"""Internal raw-Git-object exporter for SWE-bench repository preparation.

This module is launched as a separately owned literal subprocess by
``benchmarks.swebench_repository``.  It is intentionally standalone so the
outer lifecycle can bound and clean both this worker and its ``git cat-file``
descendants without importing Git libraries or using a checkout/archive path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
import unicodedata
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

_SHA1_RE = re.compile(r"[0-9a-f]{40}\Z")
_REPOSITORY_RE = re.compile(
    r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z"
)
_GIT_VERSION_RE = re.compile(r"git version [ -~]{1,200}\Z")
_BATCH_HEADER_RE = re.compile(
    rb"([0-9a-f]{40}) (commit|tree|blob) ([0-9]{1,20})\n\Z"
)
_RESERVED_WINDOWS_NAMES = {
    "aux",
    "clock$",
    "con",
    "conin$",
    "conout$",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
    "com¹",
    "com²",
    "com³",
    "lpt¹",
    "lpt²",
    "lpt³",
}
_WORKER_RESULT_SCHEMA = "ctxc-swebench-repository-worker-result-0.1"
_WORKER_ERROR_SCHEMA = "ctxc-swebench-repository-worker-error-0.1"
_READ_BLOCK_BYTES = 1024 * 1024
_MAX_GIT_STDERR_BYTES = 1024 * 1024


class WorkerError(RuntimeError):
    """One internal preparation boundary failed with a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Limits:
    timeout_seconds: float
    max_commit_bytes: int
    max_tree_object_bytes: int
    max_total_tree_bytes: int
    max_tree_objects: int
    max_entries: int
    max_path_bytes: int
    max_depth: int
    max_blob_bytes: int
    max_total_blob_bytes: int
    max_mirror_entries: int


@dataclass(frozen=True, slots=True)
class TreeEntry:
    path: str
    path_bytes: bytes
    git_mode: str
    blob_oid: str
    byte_count: int = -1


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", errors="strict")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _object_oid(kind: str, body: bytes) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(f"{kind} {len(body)}\0".encode("ascii"))
    digest.update(body)
    return digest.hexdigest()


def _has_reparse_attribute(file_stat: os.stat_result) -> bool:
    return bool(getattr(file_stat, "st_file_attributes", 0) & 0x400)


def _path_components(path: Path) -> tuple[Path, ...]:
    resolved = path.absolute()
    components: list[Path] = []
    current = resolved
    while True:
        components.append(current)
        if current.parent == current:
            break
        current = current.parent
    return tuple(reversed(components))


def _secure_existing_path(path: Path, *, directory: bool, code: str) -> Path:
    if not path.is_absolute():
        raise WorkerError(f"{code}-not-absolute")
    normalized = Path(os.path.abspath(path))
    if normalized != path:
        raise WorkerError(f"{code}-not-normalized")
    for component in _path_components(normalized):
        try:
            file_stat = component.lstat()
        except OSError as exc:
            raise WorkerError(f"{code}-inspection-failed") from exc
        if stat.S_ISLNK(file_stat.st_mode) or _has_reparse_attribute(file_stat):
            raise WorkerError(f"{code}-link-or-reparse")
    try:
        target_stat = normalized.lstat()
    except OSError as exc:
        raise WorkerError(f"{code}-inspection-failed") from exc
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(target_stat.st_mode):
        raise WorkerError(f"{code}-wrong-type")
    return normalized


def _hash_regular_file(path: Path, *, maximum: int, code: str) -> tuple[int, str]:
    secured = _secure_existing_path(path, directory=False, code=code)
    before = secured.stat(follow_symlinks=False)
    if before.st_size > maximum:
        raise WorkerError(f"{code}-too-large")
    digest = hashlib.sha256()
    total = 0
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(secured, flags)
    except OSError as exc:
        raise WorkerError(f"{code}-open-failed") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise WorkerError(f"{code}-wrong-type")
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise WorkerError(f"{code}-changed")
        while True:
            block = os.read(descriptor, _READ_BLOCK_BYTES)
            if not block:
                break
            total += len(block)
            if total > maximum:
                raise WorkerError(f"{code}-too-large")
            digest.update(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    # Use descriptor observations for the content snapshot. On Windows,
    # pathname stat and descriptor fstat can expose different synthesized
    # permission/ctime fields for the same file identity.
    snapshots = (
        opened.st_dev,
        opened.st_ino,
        opened.st_mode,
        opened.st_nlink,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
    ), (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_nlink,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if snapshots[0] != snapshots[1] or total != opened.st_size:
        raise WorkerError(f"{code}-changed")
    return total, digest.hexdigest()


def _scan_mirror(path: Path, *, maximum_entries: int) -> dict[str, object]:
    bindings: list[list[object]] = []
    pending: list[tuple[Path, str]] = [(path, "")]
    while pending:
        directory, prefix = pending.pop()
        try:
            children = os.scandir(directory)
        except OSError as exc:
            raise WorkerError("mirror-scan-failed") from exc
        try:
            with children:
                for child in children:
                    if len(bindings) >= maximum_entries:
                        raise WorkerError("mirror-entry-limit")
                    relative = f"{prefix}/{child.name}" if prefix else child.name
                    if "\\" in child.name or "/" in child.name or "\0" in child.name:
                        raise WorkerError("mirror-unsafe-name")
                    try:
                        file_stat = child.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise WorkerError("mirror-scan-failed") from exc
                    if child.is_symlink() or _has_reparse_attribute(file_stat):
                        raise WorkerError("mirror-link-or-reparse")
                    if stat.S_ISDIR(file_stat.st_mode):
                        kind = "directory"
                        pending.append((Path(child.path), relative))
                        size = 0
                    elif stat.S_ISREG(file_stat.st_mode):
                        kind = "regular-file"
                        # Windows DirEntry.stat() reports zero link counts for
                        # normal files on some Python/filesystem combinations.
                        if os.name != "nt" and file_stat.st_nlink != 1:
                            raise WorkerError("mirror-hard-link")
                        size = file_stat.st_size
                    else:
                        raise WorkerError("mirror-special-file")
                    bindings.append([relative.replace("\\", "/"), kind, size])
        except OSError as exc:
            raise WorkerError("mirror-scan-failed") from exc
    bindings.sort(key=lambda item: str(item[0]).encode("utf-8", errors="strict"))
    return {
        "entry_count": len(bindings),
        "metadata_sha256": _sha256(_canonical_bytes(bindings)),
    }


def _git_environment() -> dict[str, str]:
    allowed: dict[str, str] = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "LANG": "C",
    }
    for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"):
        value = os.environ.get(name)
        if value:
            allowed[name] = value
    return allowed


class _BoundedPipeDrainer:
    def __init__(self, stream: BinaryIO, maximum: int) -> None:
        self.stream = stream
        self.maximum = maximum
        self.data = bytearray()
        self.overflow = False
        self.overflow_event = threading.Event()
        self.error = False
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            while True:
                block = self.stream.read(64 * 1024)
                if not block:
                    return
                remaining = self.maximum - len(self.data)
                if remaining > 0:
                    self.data.extend(block[:remaining])
                if len(block) > remaining:
                    self.overflow = True
                    self.overflow_event.set()
        except (OSError, ValueError):
            self.error = True


class GitRunner:
    def __init__(self, executable: Path, mirror: Path, timeout_seconds: float) -> None:
        self.executable = executable
        self.mirror = mirror
        self.timeout_seconds = timeout_seconds
        self.environment = _git_environment()

    def argv(self, *arguments: str) -> list[str]:
        return [
            str(self.executable),
            "--no-pager",
            "--no-replace-objects",
            f"--git-dir={self.mirror}",
            *arguments,
        ]

    def run(self, *arguments: str, maximum: int = 1024 * 1024) -> bytes:
        try:
            process = subprocess.Popen(
                self.argv(*arguments),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                env=self.environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorkerError("git-command-failed") from exc
        if process.stdout is None or process.stderr is None:
            with suppress(OSError):
                process.kill()
            raise WorkerError("git-command-pipes-missing")
        stdout = _BoundedPipeDrainer(process.stdout, maximum)
        stderr = _BoundedPipeDrainer(process.stderr, _MAX_GIT_STDERR_BYTES)
        stdout.thread.start()
        stderr.thread.start()
        return_code: int | None = None
        failure: WorkerError | None = None
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while return_code is None:
                if stdout.overflow_event.is_set() or stderr.overflow_event.is_set():
                    failure = WorkerError("git-command-output-limit")
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = WorkerError("git-command-failed")
                    break
                try:
                    return_code = process.wait(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired:
                    continue
                except (OSError, subprocess.SubprocessError):
                    failure = WorkerError("git-command-failed")
                    break
            if failure is not None:
                with suppress(OSError):
                    process.kill()
                with suppress(OSError, subprocess.SubprocessError):
                    process.wait(timeout=5)
        finally:
            stdout.thread.join(timeout=2)
            stderr.thread.join(timeout=2)
            with suppress(OSError):
                process.stdout.close()
            with suppress(OSError):
                process.stderr.close()
        if (
            stdout.thread.is_alive()
            or stderr.thread.is_alive()
            or stdout.error
            or stderr.error
        ):
            raise WorkerError("git-command-output-incomplete")
        if stdout.overflow or stderr.overflow:
            raise WorkerError("git-command-output-limit")
        if failure is not None:
            raise failure
        if return_code is None:
            raise WorkerError("git-command-failed")
        if return_code != 0:
            raise WorkerError("git-command-nonzero")
        return bytes(stdout.data)


def _parse_config(raw: bytes) -> dict[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {}
    for encoded in raw.split(b"\0"):
        if not encoded:
            continue
        try:
            key_raw, value_raw = encoded.split(b"\n", 1)
            key = key_raw.decode("ascii", errors="strict").lower()
            value = value_raw.decode("utf-8", errors="strict")
        except (UnicodeError, ValueError) as exc:
            raise WorkerError("mirror-config-malformed") from exc
        values.setdefault(key, []).append(value)
    return {key: tuple(items) for key, items in values.items()}


def _one_config(
    config: dict[str, tuple[str, ...]],
    key: str,
    *,
    expected: str | None = None,
    required: bool = True,
) -> str | None:
    values = config.get(key, ())
    if not values and not required:
        return None
    if len(values) != 1:
        raise WorkerError("mirror-config-invalid")
    value = values[0]
    if expected is not None and value != expected:
        raise WorkerError("mirror-config-invalid")
    return value


def _assert_absent(path: Path, code: str) -> None:
    try:
        exists = path.exists() or path.is_symlink()
    except OSError as exc:
        raise WorkerError("mirror-security-inspection-failed") from exc
    if exists:
        raise WorkerError(code)


def _verify_mirror_state(
    runner: GitRunner,
    repository: str,
    limits: Limits,
) -> dict[str, object]:
    mirror = runner.mirror
    # Reject links/reparse points before any targeted subpath inspection can
    # traverse them (for example refs/replace or objects/pack).
    scan = _scan_mirror(mirror, maximum_entries=limits.max_mirror_entries)
    for relative, code in (
        ("shallow", "mirror-shallow"),
        ("objects/info/alternates", "mirror-alternates"),
        ("objects/info/http-alternates", "mirror-alternates"),
        ("info/grafts", "mirror-grafts"),
        ("commondir", "mirror-linked-worktree"),
        ("worktrees", "mirror-linked-worktree"),
        ("config.worktree", "mirror-worktree-config"),
        (".git", "mirror-nested-git"),
    ):
        _assert_absent(mirror / relative, code)
    replace_root = mirror / "refs" / "replace"
    if replace_root.exists():
        try:
            if any(replace_root.iterdir()):
                raise WorkerError("mirror-replace-refs")
        except OSError as exc:
            raise WorkerError("mirror-security-inspection-failed") from exc
    packed_replace_refs = runner.run(
        "for-each-ref",
        "--format=%(refname)",
        "refs/replace/",
        maximum=1024 * 1024,
    )
    if packed_replace_refs:
        raise WorkerError("mirror-replace-refs")
    pack_root = mirror / "objects" / "pack"
    try:
        if pack_root.exists() and any(
            child.name.endswith(".promisor") for child in pack_root.iterdir()
        ):
            raise WorkerError("mirror-partial-clone")
    except OSError as exc:
        raise WorkerError("mirror-security-inspection-failed") from exc
    config_bytes = runner.run(
        "config",
        "--local",
        "--no-includes",
        "--null",
        "--list",
        maximum=2 * 1024 * 1024,
    )
    config = _parse_config(config_bytes)
    if any(key.startswith("include.") or key.startswith("includeif.") for key in config):
        raise WorkerError("mirror-config-includes")
    if any(key in {"core.worktree", "extensions.worktreeconfig"} for key in config):
        raise WorkerError("mirror-worktree-config")
    if any(
        key.endswith(".promisor")
        or key.endswith(".partialclonefilter")
        or key in {"extensions.partialclone", "core.alternateRefsCommand".lower()}
        for key in config
    ):
        raise WorkerError("mirror-partial-clone")
    remotes = {
        key.split(".", 2)[1]
        for key in config
        if key.startswith("remote.") and key.count(".") >= 2
    }
    if remotes != {"origin"}:
        raise WorkerError("mirror-remotes-invalid")
    _one_config(config, "core.bare", expected="true")
    origin_url = _one_config(config, "remote.origin.url")
    expected_urls = {
        f"https://github.com/{repository}",
        f"https://github.com/{repository}.git",
    }
    if origin_url not in expected_urls:
        raise WorkerError("mirror-origin-url-mismatch")
    _one_config(config, "remote.origin.mirror", expected="true")
    fetch_values = config.get("remote.origin.fetch", ())
    if fetch_values != ("+refs/*:refs/*",):
        raise WorkerError("mirror-refspec-invalid")
    bare = runner.run("rev-parse", "--is-bare-repository", maximum=128)
    if bare != b"true\n":
        raise WorkerError("mirror-not-bare")
    object_format = runner.run("rev-parse", "--show-object-format", maximum=128)
    if object_format != b"sha1\n":
        raise WorkerError("mirror-object-format")
    version = runner.run("--version", maximum=512).decode("ascii", errors="strict").strip()
    if _GIT_VERSION_RE.fullmatch(version) is None:
        raise WorkerError("git-version-invalid")
    return {
        "origin_url": origin_url,
        "object_format": "sha1",
        "git_version": version,
        "config_sha256": _sha256(config_bytes),
        "metadata": scan,
        "origin_authenticated": False,
    }


class _StderrDrainer:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.data = bytearray()
        self.overflow = False
        self.error = False
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            while True:
                block = self.stream.read(64 * 1024)
                if not block:
                    return
                remaining = _MAX_GIT_STDERR_BYTES + 1 - len(self.data)
                if remaining > 0:
                    self.data.extend(block[:remaining])
                if len(self.data) > _MAX_GIT_STDERR_BYTES:
                    self.overflow = True
        except (OSError, ValueError):
            self.error = True


class BatchReader:
    def __init__(self, runner: GitRunner, mode: str) -> None:
        arguments = ["cat-file", mode]
        try:
            self.process = subprocess.Popen(
                runner.argv(*arguments),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                env=runner.environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorkerError("git-batch-start-failed") from exc
        if self.process.stdin is None or self.process.stdout is None or self.process.stderr is None:
            raise WorkerError("git-batch-pipes-missing")
        self.stdin = self.process.stdin
        self.stdout = self.process.stdout
        self.stderr = _StderrDrainer(self.process.stderr)
        self.stderr.thread.start()

    def request_header(self, oid: str) -> tuple[str, int]:
        try:
            self.stdin.write(oid.encode("ascii") + b"\n")
            self.stdin.flush()
            header = self.stdout.readline(256)
        except OSError as exc:
            raise WorkerError("git-batch-io-failed") from exc
        match = _BATCH_HEADER_RE.fullmatch(header)
        if match is None or match.group(1).decode("ascii") != oid:
            raise WorkerError("git-batch-header-invalid")
        kind = match.group(2).decode("ascii")
        size = int(match.group(3))
        return kind, size

    def read_body(self, *, oid: str, kind: str, size: int) -> bytes:
        try:
            body = self.stdout.read(size)
            trailing = self.stdout.read(1)
        except OSError as exc:
            raise WorkerError("git-batch-io-failed") from exc
        if len(body) != size or trailing != b"\n":
            raise WorkerError("git-batch-frame-truncated")
        if _object_oid(kind, body) != oid:
            raise WorkerError("git-object-id-mismatch")
        return body

    def close(self) -> None:
        primary: WorkerError | None = None
        trailing = _BoundedPipeDrainer(self.stdout, 0)
        trailing_started = False
        try:
            self.stdin.close()
            trailing.thread.start()
            trailing_started = True
            return_code = self.process.wait(timeout=30)
            if return_code != 0:
                primary = WorkerError("git-batch-nonzero")
        except (OSError, subprocess.SubprocessError):
            primary = WorkerError("git-batch-cleanup-failed")
            try:
                self.process.kill()
                self.process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        if trailing_started:
            trailing.thread.join(timeout=2)
            if trailing.thread.is_alive() or trailing.error:
                primary = primary or WorkerError("git-batch-stdout-incomplete")
            if trailing.overflow:
                primary = primary or WorkerError("git-batch-stdout-extra")
        self.stderr.thread.join(timeout=2)
        if self.stderr.thread.is_alive() or self.stderr.error:
            primary = primary or WorkerError("git-batch-stderr-incomplete")
        if self.stderr.overflow:
            primary = primary or WorkerError("git-batch-stderr-limit")
        if self.stderr.data:
            primary = primary or WorkerError("git-batch-stderr-nonempty")
        if primary is not None:
            raise primary

    def abort(self) -> None:
        with suppress(OSError):
            self.process.kill()
        with suppress(OSError, subprocess.SubprocessError):
            self.process.wait(timeout=5)
        with suppress(OSError):
            self.stdin.close()


def _batch_object(
    batch: BatchReader,
    oid: str,
    *,
    expected_kind: str,
    maximum: int,
) -> bytes:
    kind, size = batch.request_header(oid)
    if kind != expected_kind:
        raise WorkerError("git-object-type-mismatch")
    if size > maximum:
        raise WorkerError(f"{expected_kind}-object-limit")
    return batch.read_body(oid=oid, kind=kind, size=size)


def _parse_commit(body: bytes) -> str:
    headers = body.split(b"\n\n", 1)[0].split(b"\n")
    first_line = headers[0]
    if not first_line.startswith(b"tree "):
        raise WorkerError("commit-tree-header-missing")
    raw_oid = first_line[5:]
    try:
        oid = raw_oid.decode("ascii", errors="strict")
    except UnicodeError as exc:
        raise WorkerError("commit-tree-header-invalid") from exc
    if _SHA1_RE.fullmatch(oid) is None:
        raise WorkerError("commit-tree-header-invalid")
    if sum(line.startswith(b"tree ") for line in headers) != 1:
        raise WorkerError("commit-tree-header-invalid")
    return oid


def _portable_component(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise WorkerError("tree-path-not-utf8") from exc
    if not value or value in {".", ".."}:
        raise WorkerError("tree-path-component-invalid")
    if unicodedata.normalize("NFC", value) != value:
        raise WorkerError("tree-path-not-nfc")
    if value.endswith((" ", ".")) or "\\" in value:
        raise WorkerError("tree-path-not-portable")
    if any(
        ord(character) < 32
        or ord(character) == 127
        or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        or character in '<>:"/\\|?*'
        for character in value
    ):
        raise WorkerError("tree-path-not-portable")
    folded_stem = value.casefold().split(".", 1)[0]
    if folded_stem in _RESERVED_WINDOWS_NAMES:
        raise WorkerError("tree-path-reserved-device")
    if value.casefold() in {".git", "git~1"}:
        raise WorkerError("tree-path-git-alias")
    return value


def _parse_tree(body: bytes) -> list[tuple[str, bytes, str]]:
    entries: list[tuple[str, bytes, str]] = []
    cursor = 0
    previous_key: bytes | None = None
    while cursor < len(body):
        space = body.find(b" ", cursor)
        nul = body.find(b"\0", space + 1)
        if space <= cursor or nul <= space or nul + 21 > len(body):
            raise WorkerError("tree-object-malformed")
        mode_raw = body[cursor:space]
        name_raw = body[space + 1 : nul]
        oid_raw = body[nul + 1 : nul + 21]
        cursor = nul + 21
        try:
            mode = mode_raw.decode("ascii", errors="strict")
        except UnicodeError as exc:
            raise WorkerError("tree-mode-invalid") from exc
        if mode not in {"40000", "100644", "100755"}:
            if mode == "120000":
                raise WorkerError("tree-symlink-forbidden")
            if mode == "160000":
                raise WorkerError("tree-gitlink-forbidden")
            raise WorkerError("tree-mode-invalid")
        _portable_component(name_raw)
        ordering_key = name_raw + (b"/" if mode == "40000" else b"")
        if previous_key is not None and ordering_key <= previous_key:
            raise WorkerError("tree-order-invalid")
        previous_key = ordering_key
        entries.append((mode, name_raw, oid_raw.hex()))
    if cursor != len(body):
        raise WorkerError("tree-object-malformed")
    return entries


def _collect_tree(
    batch: BatchReader,
    root_oid: str,
    limits: Limits,
) -> tuple[list[TreeEntry], list[list[object]], str]:
    files: list[TreeEntry] = []
    tree_bindings: list[list[object]] = []
    collision_keys: set[str] = set()
    pending: list[tuple[bytes, str, int]] = [(b"", root_oid, 0)]
    total_tree_bytes = 0
    root_tree_sha256: str | None = None
    while pending:
        prefix, tree_oid, depth = pending.pop()
        if depth > limits.max_depth:
            raise WorkerError("tree-depth-limit")
        body = _batch_object(
            batch,
            tree_oid,
            expected_kind="tree",
            maximum=limits.max_tree_object_bytes,
        )
        total_tree_bytes += len(body)
        if total_tree_bytes > limits.max_total_tree_bytes:
            raise WorkerError("tree-total-object-byte-limit")
        if not prefix:
            root_tree_sha256 = _sha256(body)
        tree_path = prefix.decode("utf-8", errors="strict")
        tree_bindings.append(
            [tree_path, tree_oid, len(body), _sha256(body)]
        )
        if len(tree_bindings) > limits.max_tree_objects:
            raise WorkerError("tree-object-count-limit")
        children = _parse_tree(body)
        directory_children: list[tuple[bytes, str, int]] = []
        for mode, name_raw, oid in children:
            path_bytes = prefix + (b"/" if prefix else b"") + name_raw
            if len(path_bytes) > limits.max_path_bytes:
                raise WorkerError("tree-path-byte-limit")
            path = path_bytes.decode("utf-8", errors="strict")
            collision = unicodedata.normalize("NFC", path).casefold()
            if collision in collision_keys:
                raise WorkerError("tree-path-collision")
            collision_keys.add(collision)
            if mode == "40000":
                directory_children.append((path_bytes, oid, depth + 1))
            else:
                files.append(
                    TreeEntry(
                        path=path,
                        path_bytes=path_bytes,
                        git_mode=mode,
                        blob_oid=oid,
                    )
                )
                if len(files) > limits.max_entries:
                    raise WorkerError("tree-entry-limit")
        pending.extend(reversed(directory_children))
    files.sort(key=lambda entry: entry.path_bytes)
    tree_bindings.sort(key=lambda item: str(item[0]).encode("utf-8"))
    if root_tree_sha256 is None:
        raise WorkerError("root-tree-missing")
    expected_tree_paths = {""}
    for entry in files:
        parts = PurePosixPath(entry.path).parts
        expected_tree_paths.update(
            PurePosixPath(*parts[:index]).as_posix()
            for index in range(1, len(parts))
        )
    if {str(binding[0]) for binding in tree_bindings} != expected_tree_paths:
        raise WorkerError("tree-empty-directory-forbidden")
    return files, tree_bindings, root_tree_sha256


def _preflight_blob_sizes(
    batch: BatchReader,
    files: list[TreeEntry],
    limits: Limits,
) -> list[TreeEntry]:
    sizes: dict[str, int] = {}
    total = 0
    result: list[TreeEntry] = []
    for entry in files:
        size = sizes.get(entry.blob_oid)
        if size is None:
            kind, size = batch.request_header(entry.blob_oid)
            if kind != "blob":
                raise WorkerError("git-object-type-mismatch")
            if size > limits.max_blob_bytes:
                raise WorkerError("blob-byte-limit")
            # --batch-check produces no body. BatchReader uses the same header
            # grammar, but there is no trailing frame byte to consume.
            sizes[entry.blob_oid] = size
        total += size
        if total > limits.max_total_blob_bytes:
            raise WorkerError("tree-total-byte-limit")
        result.append(
            TreeEntry(
                path=entry.path,
                path_bytes=entry.path_bytes,
                git_mode=entry.git_mode,
                blob_oid=entry.blob_oid,
                byte_count=size,
            )
        )
    return result


def _open_output_file(root: Path, entry: TreeEntry) -> tuple[int, Path]:
    relative = PurePosixPath(entry.path)
    destination = root.joinpath(*relative.parts)
    parent = destination.parent
    try:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise WorkerError("output-directory-create-failed") from exc
    current = root
    for component in relative.parts[:-1]:
        current = current / component
        try:
            file_stat = current.lstat()
        except OSError as exc:
            raise WorkerError("output-directory-inspection-failed") from exc
        if not stat.S_ISDIR(file_stat.st_mode) or _has_reparse_attribute(file_stat):
            raise WorkerError("output-directory-substituted")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(destination, flags, 0o600)
    except OSError as exc:
        raise WorkerError("output-file-create-failed") from exc
    return descriptor, destination


def _consume_blob(
    batch: BatchReader,
    entry: TreeEntry,
    *,
    root: Path | None,
) -> list[object]:
    kind, size = batch.request_header(entry.blob_oid)
    if kind != "blob" or size != entry.byte_count:
        raise WorkerError("blob-preflight-mismatch")
    descriptor: int | None = None
    destination: Path | None = None
    if root is not None:
        descriptor, destination = _open_output_file(root, entry)
    object_digest = hashlib.sha1(usedforsecurity=False)
    object_digest.update(f"blob {size}\0".encode("ascii"))
    content_digest = hashlib.sha256()
    remaining = size
    try:
        while remaining:
            block = batch.stdout.read(min(_READ_BLOCK_BYTES, remaining))
            if not block:
                raise WorkerError("git-batch-frame-truncated")
            remaining -= len(block)
            object_digest.update(block)
            content_digest.update(block)
            if descriptor is not None:
                view = memoryview(block)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise WorkerError("output-file-write-failed")
                    view = view[written:]
        trailing = batch.stdout.read(1)
        if trailing != b"\n":
            raise WorkerError("git-batch-frame-truncated")
        if object_digest.hexdigest() != entry.blob_oid:
            raise WorkerError("git-object-id-mismatch")
        if descriptor is not None and os.name != "nt":
            os.fchmod(descriptor, 0o755 if entry.git_mode == "100755" else 0o644)
        if descriptor is not None:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size != size:
                raise WorkerError("output-file-verification-failed")
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if destination is not None:
        try:
            final_stat = destination.lstat()
        except OSError as exc:
            raise WorkerError("output-file-verification-failed") from exc
        if (
            not stat.S_ISREG(final_stat.st_mode)
            or _has_reparse_attribute(final_stat)
            or final_stat.st_nlink != 1
            or final_stat.st_size != size
        ):
            raise WorkerError("output-file-verification-failed")
    return [
        entry.path,
        entry.git_mode,
        entry.blob_oid,
        size,
        content_digest.hexdigest(),
    ]


def _verify_output_root(root: Path) -> None:
    secured = _secure_existing_path(root, directory=True, code="output-root")
    try:
        if any(secured.iterdir()):
            raise WorkerError("output-root-not-empty")
    except OSError as exc:
        raise WorkerError("output-root-inspection-failed") from exc


def _export_commit(
    runner: GitRunner,
    root: Path | None,
    commit_oid: str,
    limits: Limits,
) -> dict[str, object]:
    if root is not None:
        _verify_output_root(root)
    batch = BatchReader(runner, "--batch")
    try:
        commit_body = _batch_object(
            batch,
            commit_oid,
            expected_kind="commit",
            maximum=limits.max_commit_bytes,
        )
        root_tree_oid = _parse_commit(commit_body)
        files, tree_bindings, root_tree_sha256 = _collect_tree(
            batch,
            root_tree_oid,
            limits,
        )
        batch.close()
    except BaseException:
        batch.abort()
        raise

    checker = BatchReader(runner, "--batch-check")
    try:
        files = _preflight_blob_sizes(checker, files, limits)
        checker.close()
    except BaseException:
        checker.abort()
        raise

    exporter = BatchReader(runner, "--batch")
    bindings: list[list[object]] = []
    try:
        for entry in files:
            bindings.append(_consume_blob(exporter, entry, root=root))
        exporter.close()
    except BaseException:
        exporter.abort()
        raise

    return {
        "base_object": {
            "oid": commit_oid,
            "byte_count": len(commit_body),
            "sha256": _sha256(commit_body),
        },
        "tree": {
            "root_tree_oid": root_tree_oid,
            "root_tree_object_sha256": root_tree_sha256,
            "tree_object_count": len(tree_bindings),
            "total_tree_object_bytes": sum(
                int(binding[2]) for binding in tree_bindings
            ),
            "tree_object_binding_fields": [
                "path",
                "tree_oid",
                "byte_count",
                "sha256",
            ],
            "tree_object_bindings": tree_bindings,
            "ordered_tree_object_bindings_sha256": _sha256(
                _canonical_bytes(tree_bindings)
            ),
            "entry_count": len(bindings),
            "executable_file_count": sum(
                1 for binding in bindings if binding[1] == "100755"
            ),
            "total_blob_bytes": sum(int(binding[3]) for binding in bindings),
            "path_policy": "portable-utf8-nfc-regular-files-v1",
            "entry_binding_fields": [
                "path",
                "git_mode",
                "blob_oid",
                "byte_count",
                "sha256",
            ],
            "entry_bindings": bindings,
            "ordered_entry_bindings_sha256": _sha256(_canonical_bytes(bindings)),
        },
    }


def _limits_from_args(args: argparse.Namespace) -> Limits:
    return Limits(
        timeout_seconds=args.timeout_seconds,
        max_commit_bytes=args.max_commit_bytes,
        max_tree_object_bytes=args.max_tree_object_bytes,
        max_total_tree_bytes=args.max_total_tree_bytes,
        max_tree_objects=args.max_tree_objects,
        max_entries=args.max_entries,
        max_path_bytes=args.max_path_bytes,
        max_depth=args.max_depth,
        max_blob_bytes=args.max_blob_bytes,
        max_total_blob_bytes=args.max_total_blob_bytes,
        max_mirror_entries=args.max_mirror_entries,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "operation",
        choices=("verify-mirror", "inspect", "prepare"),
    )
    parser.add_argument("--mirror", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--git-executable", required=True)
    parser.add_argument("--expected-worker-sha256", required=True)
    parser.add_argument("--expected-git-sha256", required=True)
    parser.add_argument("--commit")
    parser.add_argument("--output")
    parser.add_argument("--timeout-seconds", required=True, type=float)
    parser.add_argument("--max-commit-bytes", required=True, type=int)
    parser.add_argument("--max-tree-object-bytes", required=True, type=int)
    parser.add_argument("--max-total-tree-bytes", required=True, type=int)
    parser.add_argument("--max-tree-objects", required=True, type=int)
    parser.add_argument("--max-entries", required=True, type=int)
    parser.add_argument("--max-path-bytes", required=True, type=int)
    parser.add_argument("--max-depth", required=True, type=int)
    parser.add_argument("--max-blob-bytes", required=True, type=int)
    parser.add_argument("--max-total-blob-bytes", required=True, type=int)
    parser.add_argument("--max-mirror-entries", required=True, type=int)
    return parser


def _run(args: argparse.Namespace) -> dict[str, object]:
    if _REPOSITORY_RE.fullmatch(args.repository) is None:
        raise WorkerError("repository-invalid")
    mirror = _secure_existing_path(Path(args.mirror), directory=True, code="mirror")
    git_executable = _secure_existing_path(
        Path(args.git_executable),
        directory=False,
        code="git-executable",
    )
    worker_bytes, worker_sha256 = _hash_regular_file(
        Path(__file__).resolve(),
        maximum=4 * 1024 * 1024,
        code="worker-source",
    )
    git_bytes, git_sha256 = _hash_regular_file(
        git_executable,
        maximum=256 * 1024 * 1024,
        code="git-executable",
    )
    if worker_sha256 != args.expected_worker_sha256:
        raise WorkerError("worker-source-hash-mismatch")
    if git_sha256 != args.expected_git_sha256:
        raise WorkerError("git-executable-hash-mismatch")
    limits = _limits_from_args(args)
    runner = GitRunner(git_executable, mirror, limits.timeout_seconds)
    before = _verify_mirror_state(runner, args.repository, limits)
    export: dict[str, object] | None = None
    if args.operation in {"inspect", "prepare"}:
        if args.commit is None or _SHA1_RE.fullmatch(args.commit) is None:
            raise WorkerError("base-commit-invalid")
        output: Path | None = None
        if args.operation == "prepare":
            if args.output is None:
                raise WorkerError("output-root-missing")
            output = _secure_existing_path(
                Path(args.output),
                directory=True,
                code="output-root",
            )
        elif args.output is not None:
            raise WorkerError("inspect-output-forbidden")
        export = _export_commit(runner, output, args.commit, limits)
    after = _verify_mirror_state(runner, args.repository, limits)
    if before != after:
        raise WorkerError("mirror-changed-during-operation")
    _, final_worker_sha256 = _hash_regular_file(
        Path(__file__).resolve(),
        maximum=4 * 1024 * 1024,
        code="worker-source",
    )
    _, final_git_sha256 = _hash_regular_file(
        git_executable,
        maximum=256 * 1024 * 1024,
        code="git-executable",
    )
    if final_worker_sha256 != worker_sha256:
        raise WorkerError("worker-source-changed")
    if final_git_sha256 != git_sha256:
        raise WorkerError("git-executable-changed")
    return {
        "schema": _WORKER_RESULT_SCHEMA,
        "operation": args.operation,
        "repository": args.repository,
        "mirror": before,
        "git": {
            "executable_byte_count": git_bytes,
            "executable_sha256": git_sha256,
            "version": before["git_version"],
        },
        "worker": {
            "source_byte_count": worker_bytes,
            "source_sha256": worker_sha256,
        },
        "export": export,
    }


def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
        result = _run(args)
    except WorkerError as exc:
        result = {
            "schema": _WORKER_ERROR_SCHEMA,
            "status": "failed",
            "error_code": exc.code,
        }
        sys.stdout.buffer.write(_canonical_bytes(result))
        return 1
    except BaseException as exc:
        result = {
            "schema": _WORKER_ERROR_SCHEMA,
            "status": "failed",
            "error_code": f"internal-{type(exc).__name__}",
        }
        sys.stdout.buffer.write(_canonical_bytes(result))
        return 1
    sys.stdout.buffer.write(_canonical_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
