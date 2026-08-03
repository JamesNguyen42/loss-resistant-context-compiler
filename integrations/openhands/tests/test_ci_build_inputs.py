from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = PROJECT_ROOT / "scripts" / "ci_build_inputs.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location(
        "_ctxc_openhands_ci_build_inputs_test",
        HELPER_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


HELPER = _load_helper()


def _record_digest(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
    return "sha256=" + encoded.rstrip(b"=").decode("ascii")


def _zip_info(
    name: str,
    *,
    member_mode: int = stat.S_IFREG | 0o644,
) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2023, 11, 14, 22, 13, 20))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = member_mode << 16
    return info


def _wheel_bytes(
    approved,
    *,
    metadata_name: str | None = None,
    metadata_version: str | None = None,
    tags: tuple[str, ...] | None = None,
    corrupt_record: bool = False,
    duplicate_member_name: str | None = None,
    member_mode: int = stat.S_IFREG | 0o644,
    extra_member_name: str | None = None,
    extra_member_names: tuple[str, ...] = (),
    record_path_override: str | None = None,
    reverse_record_rows: bool = False,
) -> bytes:
    package_name = approved.name.replace("-", "_")
    package_member = f"{package_name}/__init__.py"
    metadata_member = f"{approved.dist_info}/METADATA"
    wheel_member = f"{approved.dist_info}/WHEEL"
    record_member = f"{approved.dist_info}/RECORD"
    metadata = (
        "Metadata-Version: 2.4\n"
        f"Name: {metadata_name or approved.name}\n"
        f"Version: {metadata_version or approved.version}\n"
        "\n"
    ).encode()
    expected_tags = approved.tags if tags is None else tags
    wheel_metadata = (
        "Wheel-Version: 1.0\n"
        "Generator: ctxc-test\n"
        "Root-Is-Purelib: true\n"
        + "".join(f"Tag: {tag}\n" for tag in expected_tags)
        + "\n"
    ).encode("utf-8")
    members = {
        package_member: b'"""fixture"""\n',
        metadata_member: metadata,
        wheel_member: wheel_metadata,
    }
    if extra_member_name is not None:
        members[extra_member_name] = b"extra\n"
    for name in extra_member_names:
        members[name] = b"extra\n"
    rows = [
        [name, _record_digest(payload), str(len(payload))]
        for name, payload in members.items()
    ]
    if record_path_override is not None:
        rows[0][0] = record_path_override
    if corrupt_record:
        rows[0][1] = "sha256=" + ("A" * 43)
    rows.append([record_member, "", ""])
    if reverse_record_rows:
        rows.reverse()
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows(rows)
    members[record_member] = output.getvalue().encode("utf-8")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as wheel:
        for name, payload in members.items():
            wheel.writestr(_zip_info(name, member_mode=member_mode), payload)
        if duplicate_member_name is not None:
            wheel.writestr(
                _zip_info(duplicate_member_name, member_mode=member_mode),
                b"duplicate\n",
            )
    return archive.getvalue()


def _write_wheel(
    wheelhouse: Path,
    approved,
    **kwargs: object,
) -> Path:
    path = wheelhouse / approved.filename
    path.write_bytes(_wheel_bytes(approved, **kwargs))
    return path


def _write_lock(lock: Path, wheelhouse: Path) -> None:
    lines = []
    for approved in HELPER.APPROVED_BUILD_WHEELS:
        digest = hashlib.sha256((wheelhouse / approved.filename).read_bytes()).hexdigest()
        lines.append(
            f"{approved.name}=={approved.version} --hash=sha256:{digest}\n"
        )
    lock.write_bytes("".join(lines).encode("ascii"))


def _valid_inputs(tmp_path: Path) -> tuple[Path, Path]:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    for approved in HELPER.APPROVED_BUILD_WHEELS:
        _write_wheel(wheelhouse, approved)
    lock = tmp_path / "requirements-build.lock"
    _write_lock(lock, wheelhouse)
    return lock, wheelhouse


def _expected_builder() -> dict[str, object]:
    return {
        "verified": True,
        "implementation": "CPython",
        "python_version": "3.13.0",
        "distributions": [
            {"name": approved.name, "version": approved.version}
            for approved in HELPER.APPROVED_BUILD_WHEELS
        ],
    }


def _report_without_hash(report: dict[str, object]) -> dict[str, object]:
    unsigned = dict(report)
    unsigned.pop("report_sha256")
    return unsigned


def test_exact_seven_wheelhouse_is_stable_and_canonically_hashed(
    tmp_path: Path,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)

    first = HELPER.verify_build_inputs(lock, wheelhouse)
    second = HELPER.verify_build_inputs(lock, wheelhouse)

    assert first == second
    assert first["lock"]["record_count"] == 7
    assert first["wheelhouse"]["file_count"] == 7
    assert len(first["wheelhouse"]["wheels"]) == 7
    expected_digest = hashlib.sha256(
        json.dumps(
            first["wheelhouse"]["wheels"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert first["wheelhouse"]["inventory_sha256"] == expected_digest
    assert first["builder"] == {"verified": False, "reason": "not-requested"}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.replace(b"\n", b"\r\n"),
        lambda payload: payload[:-1],
        lambda payload: payload.replace(b"build==1.5.0", b"build[extra]==1.5.0", 1),
        lambda payload: payload.replace(
            b"build==1.5.0 ",
            b"build==1.5.0; python_version>='3.12' ",
            1,
        ),
        lambda payload: payload.replace(
            b"build==1.5.0 ",
            b"build @ https://invalid.example/build.whl ",
            1,
        ),
        lambda payload: payload.replace(
            b" --hash=sha256:",
            b" --hash=sha256:" + (b"0" * 64) + b" --hash=sha256:",
            1,
        ),
        lambda payload: payload.replace(b" --hash=sha256:", b" \\\n --hash=sha256:", 1),
        lambda payload: payload.replace(b"--hash=sha256:", b"--hash=sha256:A", 1),
        lambda payload: b"".join(reversed(payload.splitlines(keepends=True))),
        lambda payload: payload + payload.splitlines(keepends=True)[0],
    ],
    ids=[
        "crlf",
        "missing-final-lf",
        "extra",
        "marker",
        "url",
        "multiple-hash",
        "continuation",
        "uppercase-hash",
        "reordered",
        "duplicate",
    ],
)
def test_requirements_lock_rejects_noncanonical_or_ambiguous_records(
    tmp_path: Path,
    mutation,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    lock.write_bytes(mutation(lock.read_bytes()))

    with pytest.raises(HELPER.BuildInputError, match="lock"):
        HELPER.verify_build_inputs(lock, wheelhouse)


@pytest.mark.parametrize("case", ["missing", "extra", "renamed", "mutated", "nested"])
def test_wheelhouse_rejects_missing_extra_renamed_mutated_or_nested_entries(
    tmp_path: Path,
    case: str,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    if case == "missing":
        wheel.unlink()
    elif case == "extra":
        (wheelhouse / "extra-1.0-py3-none-any.whl").write_bytes(b"extra")
    elif case == "renamed":
        wheel.rename(wheelhouse / "renamed-1.0-py3-none-any.whl")
    elif case == "mutated":
        wheel.write_bytes(wheel.read_bytes() + b"changed")
    else:
        (wheelhouse / "nested").mkdir()

    with pytest.raises(HELPER.BuildInputError):
        HELPER.verify_build_inputs(lock, wheelhouse)


def test_wheelhouse_rejects_symbolic_link(tmp_path: Path) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    target = tmp_path / "target.whl"
    target.write_bytes(wheel.read_bytes())
    wheel.unlink()
    try:
        wheel.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    with pytest.raises(HELPER.BuildInputError, match="regular unlinked"):
        HELPER.verify_build_inputs(lock, wheelhouse)


def test_wheelhouse_rejects_hard_link(tmp_path: Path) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    target = tmp_path / "target.whl"
    target.write_bytes(wheel.read_bytes())
    wheel.unlink()
    try:
        os.link(target, wheel)
    except OSError:
        pytest.skip("hard links are unavailable")
    with pytest.raises(HELPER.BuildInputError, match="regular unlinked"):
        HELPER.verify_build_inputs(lock, wheelhouse)


def test_wheelhouse_rejects_simulated_reparse_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    target_name = HELPER.APPROVED_BUILD_WHEELS[0].filename
    original = HELPER._is_reparse_or_junction

    def simulated(path: Path, path_stat: os.stat_result) -> bool:
        return path.name == target_name or original(path, path_stat)

    monkeypatch.setattr(HELPER, "_is_reparse_or_junction", simulated)
    with pytest.raises(HELPER.BuildInputError, match="regular unlinked"):
        HELPER.verify_build_inputs(lock, wheelhouse)


@pytest.mark.parametrize("case", ["platform", "metadata", "record"])
def test_wheel_validation_rejects_platform_identity_and_record_mismatches(
    tmp_path: Path,
    case: str,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    if case == "platform":
        wheel.write_bytes(_wheel_bytes(approved, tags=("py3-none-win_amd64",)))
    elif case == "metadata":
        wheel.write_bytes(_wheel_bytes(approved, metadata_name="different-project"))
    else:
        wheel.write_bytes(_wheel_bytes(approved, corrupt_record=True))
    _write_lock(lock, wheelhouse)

    with pytest.raises(
        HELPER.BuildInputError,
        match="tags|METADATA identity|RECORD digest",
    ):
        HELPER.verify_build_inputs(lock, wheelhouse)


def test_wheel_accepts_unspecified_file_type_but_rejects_link_members(
    tmp_path: Path,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    wheel.write_bytes(_wheel_bytes(approved, member_mode=0o644))
    _write_lock(lock, wheelhouse)

    HELPER.verify_build_inputs(lock, wheelhouse)

    wheel.write_bytes(_wheel_bytes(approved, member_mode=stat.S_IFLNK | 0o777))
    _write_lock(lock, wheelhouse)
    with pytest.raises(HELPER.BuildInputError, match="link or special"):
        HELPER.verify_build_inputs(lock, wheelhouse)


@pytest.mark.parametrize(
    "component",
    [
        "CON .txt",
        "CONIN$.txt",
        "CONOUT$.txt",
        "COM1 .txt",
        "COM\u00b9.txt",
        "LPT\u00b2 .log",
    ],
)
def test_wheel_rejects_windows_device_alias_members(
    tmp_path: Path,
    component: str,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    wheel.write_bytes(
        _wheel_bytes(approved, extra_member_name=f"build/{component}")
    )
    _write_lock(lock, wheelhouse)

    with pytest.raises(HELPER.BuildInputError, match="nonportable member name"):
        HELPER.verify_build_inputs(lock, wheelhouse)


@pytest.mark.parametrize(
    "component",
    [
        "CON .txt",
        "CONIN$.txt",
        "CONOUT$.txt",
        "COM1 .txt",
        "COM\u00b9.txt",
        "LPT\u00b2 .log",
    ],
)
def test_wheel_record_rejects_windows_device_alias_members(
    tmp_path: Path,
    component: str,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    wheel.write_bytes(
        _wheel_bytes(approved, record_path_override=f"build/{component}")
    )
    _write_lock(lock, wheelhouse)

    with pytest.raises(HELPER.BuildInputError, match="nonportable member name"):
        HELPER.verify_build_inputs(lock, wheelhouse)


@pytest.mark.parametrize("component", ["COM0.txt", "COM10.txt", "CON name.txt"])
def test_wheel_retains_non_device_name_controls(
    tmp_path: Path,
    component: str,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    wheel.write_bytes(
        _wheel_bytes(approved, extra_member_name=f"build/{component}")
    )
    _write_lock(lock, wheelhouse)

    HELPER.verify_build_inputs(lock, wheelhouse)


def test_wheel_rejects_file_ancestor_namespace_conflict_with_interloper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    wheel.write_bytes(
        _wheel_bytes(
            approved,
            extra_member_name="build",
            extra_member_names=("build-foo",),
        )
    )
    _write_lock(lock, wheelhouse)

    def unexpected_read(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("namespace validation must precede decompression")

    monkeypatch.setattr(HELPER, "_read_zip_member", unexpected_read)
    with pytest.raises(HELPER.BuildInputError, match="namespace conflict"):
        HELPER.verify_build_inputs(lock, wheelhouse)


def test_wheel_retains_exact_duplicate_member_diagnostic(tmp_path: Path) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    with pytest.warns(UserWarning, match="Duplicate name"):
        wheel.write_bytes(_wheel_bytes(approved, duplicate_member_name="build/__init__.py"))
    _write_lock(lock, wheelhouse)

    with pytest.raises(
        HELPER.BuildInputError,
        match="duplicate or colliding members",
    ):
        HELPER.verify_build_inputs(lock, wheelhouse)


@pytest.mark.parametrize(
    "extra_member_names",
    [
        ("Build/other.py",),
        ("caf\u00e9/one.py", "cafe\u0301/two.py"),
    ],
    ids=["ascii-case", "nfc"],
)
def test_wheel_rejects_implicit_directory_portability_collisions(
    tmp_path: Path,
    extra_member_names: tuple[str, ...],
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    wheel.write_bytes(_wheel_bytes(approved, extra_member_names=extra_member_names))
    _write_lock(lock, wheelhouse)

    with pytest.raises(HELPER.BuildInputError, match="namespace conflict"):
        HELPER.verify_build_inputs(lock, wheelhouse)


def test_wheel_record_independently_rejects_namespace_conflicts(
    tmp_path: Path,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    wheel.write_bytes(_wheel_bytes(approved, record_path_override=approved.dist_info))
    _write_lock(lock, wheelhouse)

    with pytest.raises(
        HELPER.BuildInputError,
        match="RECORD contains a namespace conflict",
    ):
        HELPER.verify_build_inputs(lock, wheelhouse)


def test_wheel_record_retains_exact_duplicate_path_diagnostic(tmp_path: Path) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    wheel.write_bytes(
        _wheel_bytes(
            approved,
            record_path_override=f"{approved.dist_info}/METADATA",
        )
    )
    _write_lock(lock, wheelhouse)

    with pytest.raises(HELPER.BuildInputError, match="RECORD contains a duplicate path"):
        HELPER.verify_build_inputs(lock, wheelhouse)


def test_wheel_record_accepts_shuffled_rows(tmp_path: Path) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    wheel.write_bytes(_wheel_bytes(approved, reverse_record_rows=True))
    _write_lock(lock, wheelhouse)

    HELPER.verify_build_inputs(lock, wheelhouse)


def test_wheel_namespace_allows_shared_directories_and_distinct_unicode(
    tmp_path: Path,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    wheel.write_bytes(
        _wheel_bytes(
            approved,
            extra_member_names=(
                "build/data/one.txt",
                "build/data/two.txt",
                "caf\u00e9/one.py",
                "caf\u00e8/two.py",
                "plain",
                "plain-foo",
            ),
        )
    )
    _write_lock(lock, wheelhouse)

    HELPER.verify_build_inputs(lock, wheelhouse)


def test_wheel_allows_bound_vendored_metadata_but_rejects_foreign_top_level_dist_info(
    tmp_path: Path,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    wheel.write_bytes(
        _wheel_bytes(
            approved,
            extra_member_name="build/_vendor/example-1.0.dist-info/METADATA",
        )
    )
    _write_lock(lock, wheelhouse)

    HELPER.verify_build_inputs(lock, wheelhouse)

    wheel.write_bytes(
        _wheel_bytes(
            approved,
            extra_member_name="foreign-1.0.dist-info/METADATA",
        )
    )
    _write_lock(lock, wheelhouse)
    with pytest.raises(HELPER.BuildInputError, match="foreign dist-info"):
        HELPER.verify_build_inputs(lock, wheelhouse)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: b"PREFIX" + payload,
        lambda payload: payload + b"TRAILER",
    ],
    ids=["prefix", "trailing"],
)
def test_wheel_validation_rejects_noncanonical_zip_framing(
    tmp_path: Path,
    mutate,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    approved = HELPER.APPROVED_BUILD_WHEELS[0]
    wheel = wheelhouse / approved.filename
    wheel.write_bytes(mutate(wheel.read_bytes()))
    _write_lock(lock, wheelhouse)

    with pytest.raises(HELPER.BuildInputError, match="ZIP framing"):
        HELPER.verify_build_inputs(lock, wheelhouse)


def test_wheel_replacement_between_validation_passes_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    replaced = wheelhouse / HELPER.APPROVED_BUILD_WHEELS[0].filename
    original = HELPER._validate_wheel_bytes
    invoked = False

    def replace_after_validation(payload: bytes, approved) -> None:
        nonlocal invoked
        original(payload, approved)
        if not invoked:
            invoked = True
            replaced.write_bytes(replaced.read_bytes() + b"replacement")

    monkeypatch.setattr(HELPER, "_validate_wheel_bytes", replace_after_validation)
    with pytest.raises(HELPER.BuildInputError, match="lock hash|changed"):
        HELPER.verify_build_inputs(lock, wheelhouse)


def test_builder_inventory_rejects_missing_extra_and_version_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = [
        [approved.name, approved.version]
        for approved in HELPER.APPROVED_BUILD_WHEELS
    ]

    def result(rows: list[list[str]]) -> subprocess.CompletedProcess[bytes]:
        payload = json.dumps(
            {
                "implementation": "CPython",
                "python_version": "3.13.0",
                "executable": str(Path(sys.executable).resolve()),
                "distributions": rows,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return subprocess.CompletedProcess([], 0, payload, b"")

    for rows in (
        expected[:-1],
        [*expected, ["extra", "1.0"]],
        [[name, "0" if name == "build" else version] for name, version in expected],
    ):
        monkeypatch.setattr(
            HELPER.subprocess,
            "run",
            lambda *_args, _rows=rows, **_kwargs: result(_rows),
        )
        with pytest.raises(HELPER.BuildInputError, match="missing, extra, or drifted"):
            HELPER._run_builder_probe(Path(sys.executable))


def test_builder_inventory_uses_the_exact_isolated_python_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        [approved.name, approved.version]
        for approved in HELPER.APPROVED_BUILD_WHEELS
    ]
    observed: dict[str, object] = {}

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed["kwargs"] = kwargs
        payload = json.dumps(
            {
                "implementation": "CPython",
                "python_version": "3.13.0",
                "executable": str(Path(sys.executable).resolve()),
                "distributions": rows,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return subprocess.CompletedProcess(command, 0, payload, b"")

    monkeypatch.setattr(HELPER.subprocess, "run", run)

    result = HELPER._run_builder_probe(Path(sys.executable))

    assert result == _expected_builder()
    assert observed["command"][:4] == [
        str(Path(sys.executable).absolute()),
        "-I",
        "-B",
        "-c",
    ]
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["check"] is False
    assert kwargs["capture_output"] is True
    assert kwargs["timeout"] == 30
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "PYTHONPATH" not in environment
    assert "PYTHONHOME" not in environment


def test_cli_writes_one_bounded_self_hashed_path_neutral_report_exclusively(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    report_path = tmp_path / "build-input-report.json"
    monkeypatch.setattr(HELPER, "_run_builder_probe", lambda _python: _expected_builder())
    arguments = [
        "--lock",
        str(lock),
        "--wheelhouse",
        str(wheelhouse),
        "--builder-python",
        str(Path(sys.executable)),
        "--revision",
        "a" * 40,
        "--source-date-epoch",
        "1700000000",
        "--json-out",
        str(report_path),
    ]

    assert HELPER.main(arguments) == 0
    raw = report_path.read_bytes()
    assert 0 < len(raw) <= HELPER._MAX_REPORT_BYTES
    report = json.loads(raw)
    assert report["passed"] is True
    assert report["status"] == "passed"
    assert report["acquisition_performed"] is False
    assert report["network_action_performed"] is False
    assert report["builder"] == _expected_builder()
    assert str(tmp_path) not in raw.decode("utf-8")
    assert report["report_sha256"] == hashlib.sha256(
        json.dumps(
            _report_without_hash(report),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    with pytest.raises(HELPER.BuildInputError, match="refusing to replace"):
        HELPER.main(arguments)


def test_cli_retains_a_path_neutral_failure_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock, wheelhouse = _valid_inputs(tmp_path)
    lock.write_bytes(lock.read_bytes().replace(b"\n", b"\r\n"))
    report_path = tmp_path / "failed-build-input-report.json"
    monkeypatch.setattr(HELPER, "_run_builder_probe", lambda _python: _expected_builder())

    assert (
        HELPER.main(
            [
                "--lock",
                str(lock),
                "--wheelhouse",
                str(wheelhouse),
                "--builder-python",
                str(Path(sys.executable)),
                "--revision",
                "b" * 40,
                "--source-date-epoch",
                "1700000000",
                "--json-out",
                str(report_path),
            ]
        )
        == 2
    )
    raw = report_path.read_bytes()
    report = json.loads(raw)
    assert report["passed"] is False
    assert report["status"] == "failed"
    assert "canonical LF" in report["issue"]
    assert str(tmp_path) not in raw.decode("utf-8")
