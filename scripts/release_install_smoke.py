"""Validate clean wheel and source-distribution installs on any supported host."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

DISTRIBUTION = "loss-resistant-context-compiler"
EXPECTED_VERSION = "0.1.0"
SCHEMA_GLOB = "*.schema.json"


def _run(arguments: Sequence[str], *, cwd: Path | None = None) -> None:
    subprocess.run(
        list(arguments),
        cwd=cwd,
        check=True,
        text=True,
    )


def _venv_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _venv_ctxc(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "ctxc.exe"
    return environment / "bin" / "ctxc"


def _artifact_kind(path: Path) -> str:
    if path.name.endswith(".whl"):
        return "wheel"
    if path.name.endswith(".tar.gz"):
        return "sdist"
    raise ValueError(f"unsupported release artifact: {path}")


def _is_reparse_point(path_stat: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(path_stat, "st_file_attributes", 0) & reparse_flag)


def _path_identity(path_stat: os.stat_result) -> tuple[int, int]:
    return path_stat.st_dev, path_stat.st_ino


def _artifact_fingerprint(path_stat: os.stat_result) -> tuple[int, ...]:
    return (
        path_stat.st_dev,
        path_stat.st_ino,
        path_stat.st_mode,
        path_stat.st_nlink,
        path_stat.st_size,
        path_stat.st_mtime_ns,
        path_stat.st_ctime_ns,
    )


def _verified_artifact_snapshot(path: Path) -> tuple[Path, tuple[int, ...]]:
    lexical = path.expanduser().absolute()
    try:
        lexical_stat = lexical.lstat()
    except OSError as exc:
        raise ValueError(f"release artifact is unavailable: {lexical}") from exc
    if (
        not stat.S_ISREG(lexical_stat.st_mode)
        or stat.S_ISLNK(lexical_stat.st_mode)
        or _is_reparse_point(lexical_stat)
        or lexical_stat.st_nlink != 1
    ):
        raise ValueError(
            f"release smoke requires a lexical regular single-link artifact: {lexical}"
        )
    try:
        resolved = lexical.resolve(strict=True)
        resolved_stat = resolved.stat()
    except OSError as exc:
        raise ValueError(f"release artifact cannot be resolved: {lexical}") from exc
    if (
        not stat.S_ISREG(resolved_stat.st_mode)
        or _is_reparse_point(resolved_stat)
        or resolved_stat.st_nlink != 1
        or _artifact_fingerprint(resolved_stat) != _artifact_fingerprint(lexical_stat)
    ):
        raise ValueError(f"release artifact changed while resolving: {lexical}")
    return resolved, _artifact_fingerprint(resolved_stat)


def _assert_artifact_snapshot(
    path: Path,
    expected: tuple[Path, tuple[int, ...]],
) -> None:
    if _verified_artifact_snapshot(path) != expected:
        raise ValueError(f"release artifact changed during smoke install: {path}")


def _verified_distribution_directory(directory: Path) -> Path:
    lexical = directory.expanduser().absolute()
    try:
        lexical_stat = lexical.lstat()
    except OSError as exc:
        raise ValueError(f"release smoke directory is unavailable: {lexical}") from exc
    is_junction = bool(hasattr(os.path, "isjunction") and os.path.isjunction(lexical))
    if (
        not stat.S_ISDIR(lexical_stat.st_mode)
        or stat.S_ISLNK(lexical_stat.st_mode)
        or _is_reparse_point(lexical_stat)
        or is_junction
    ):
        raise ValueError(f"release smoke requires a lexical real directory: {lexical}")
    try:
        resolved = lexical.resolve(strict=True)
        resolved_stat = resolved.stat()
    except OSError as exc:
        raise ValueError(f"release smoke directory cannot be resolved: {lexical}") from exc
    if not stat.S_ISDIR(resolved_stat.st_mode) or _path_identity(resolved_stat) != _path_identity(
        lexical_stat
    ):
        raise ValueError(f"release smoke directory changed while resolving: {lexical}")
    return resolved


def _assert_installed_package(
    python: Path,
    environment: Path,
    expected_schema_names: list[str],
) -> None:
    probe = (
        "import importlib.metadata as m, json, pathlib, sys; "
        f"assert m.version('{DISTRIBUTION}') == '{EXPECTED_VERSION}'; "
        f"requirements=m.requires('{DISTRIBUTION}') or []; "
        "assert all('extra ==' in value for value in requirements), requirements; "
        "root=pathlib.Path(sys.prefix)/'share'/'loss-resistant-context-compiler'/'schemas'; "
        "names=sorted(path.name for path in root.glob('*.schema.json')); "
        f"assert names == {expected_schema_names!r}, (root, names); "
        "assert all(isinstance(json.loads(path.read_text(encoding='utf-8')), dict) "
        "for path in root.glob('*.schema.json'))"
    )
    _run([str(python), "-c", probe])

    ctxc = _venv_ctxc(environment)
    _run([str(ctxc), "--help"])

    source_path = environment / "sources.json"
    artifact_path = environment / "artifact.json"
    manifest_path = environment / "manifest.json"
    report_path = environment / "trust-report.json"
    source_path.write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "content": "constraint: preserve the release contract",
                }
            ]
        ),
        encoding="utf-8",
    )
    _run([str(ctxc), "compile", str(source_path), "-o", str(artifact_path)])
    _run(
        [
            str(ctxc),
            "trust",
            "create",
            str(artifact_path),
            str(source_path),
            "-o",
            str(manifest_path),
        ]
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    anchor = manifest["manifest_sha256"]
    _run(
        [
            str(ctxc),
            "trust",
            "verify",
            str(manifest_path),
            str(artifact_path),
            str(source_path),
            "--expected-manifest-sha256",
            anchor,
            "-o",
            str(report_path),
        ]
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not (
        report.get("passed") is True
        and report.get("anchored") is True
        and all(report.get("checks", {}).values())
    ):
        raise RuntimeError(f"installed trust round trip failed: {report}")


def _temporary_root() -> Path:
    return Path(tempfile.gettempdir()).resolve(strict=True)


def _offline_build_inputs(
    wheelhouse: Path | None,
    requirements: Path | None,
) -> tuple[Path, tuple[Path, tuple[int, ...]]] | None:
    if (wheelhouse is None) != (requirements is None):
        raise ValueError("--build-wheelhouse and --build-requirements must be supplied together")
    if wheelhouse is None or requirements is None:
        return None
    verified_wheelhouse = _verified_distribution_directory(wheelhouse)
    requirements_snapshot = _verified_artifact_snapshot(requirements)
    return verified_wheelhouse, requirements_snapshot


def _build_tool_install_command(
    python: Path,
    offline_inputs: tuple[Path, tuple[Path, tuple[int, ...]]] | None,
) -> tuple[list[str], str]:
    base = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    if offline_inputs is None:
        return [*base, "setuptools>=77", "wheel>=0.41"], "online-lower-bounds"
    wheelhouse, requirements_snapshot = offline_inputs
    requirements, _fingerprint = requirements_snapshot
    return (
        [
            *base,
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "--only-binary=:all:",
            "--require-hashes",
            "-r",
            str(requirements),
        ],
        "hash-pinned-offline-wheelhouse",
    )


def _artifact_install_command(python: Path, artifact: Path, *, kind: str) -> list[str]:
    command = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-index",
        "--no-deps",
    ]
    if kind == "sdist":
        command.append("--no-build-isolation")
    command.append(str(artifact))
    return command


def _smoke_artifact(
    path: Path,
    expected_schema_names: list[str],
    *,
    offline_build_inputs: tuple[Path, tuple[Path, tuple[int, ...]]] | None,
) -> dict[str, str]:
    artifact_snapshot = _verified_artifact_snapshot(path)
    artifact, _ = artifact_snapshot
    kind = _artifact_kind(artifact)
    build_bootstrap = "not-applicable"
    with tempfile.TemporaryDirectory(
        prefix=f"ctxc-{kind}-install-",
        dir=_temporary_root(),
    ) as directory:
        environment = Path(directory) / "venv"
        _run([sys.executable, "-m", "venv", str(environment)])
        python = _venv_python(environment)
        if kind == "sdist":
            bootstrap_command, build_bootstrap = _build_tool_install_command(
                python,
                offline_build_inputs,
            )
            _run(bootstrap_command)
            if offline_build_inputs is not None:
                wheelhouse, requirements_snapshot = offline_build_inputs
                _assert_artifact_snapshot(requirements_snapshot[0], requirements_snapshot)
                if _verified_distribution_directory(wheelhouse) != wheelhouse:
                    raise ValueError("offline build wheelhouse changed during bootstrap")
        _assert_artifact_snapshot(path, artifact_snapshot)
        _run(_artifact_install_command(python, artifact, kind=kind))
        _assert_artifact_snapshot(path, artifact_snapshot)
        _assert_installed_package(python, environment, expected_schema_names)
    return {
        "artifact": artifact.name,
        "kind": kind,
        "status": "passed",
        "build_bootstrap": build_bootstrap,
    }


def _release_artifacts(directory: Path) -> list[Path]:
    verified_directory = _verified_distribution_directory(directory)
    artifacts = sorted(verified_directory.glob("*.whl")) + sorted(
        verified_directory.glob("*.tar.gz")
    )
    kinds = [_artifact_kind(path) for path in artifacts]
    if kinds.count("wheel") != 1 or kinds.count("sdist") != 1:
        raise ValueError(
            "release smoke requires exactly one wheel and one source distribution: "
            f"{[path.name for path in artifacts]}"
        )
    return artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install and smoke-test one wheel and one sdist in separate clean environments."
    )
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument("--schema-dir", type=Path, default=Path("schemas"))
    parser.add_argument("--build-wheelhouse", type=Path)
    parser.add_argument("--build-requirements", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    offline_build_inputs = _offline_build_inputs(
        args.build_wheelhouse,
        args.build_requirements,
    )
    expected_schema_names = sorted(path.name for path in args.schema_dir.glob(SCHEMA_GLOB))
    if not expected_schema_names:
        raise ValueError(f"no packaged schemas found in {args.schema_dir}")
    results = [
        _smoke_artifact(
            path,
            expected_schema_names,
            offline_build_inputs=offline_build_inputs,
        )
        for path in _release_artifacts(args.dist_dir)
    ]
    print(
        json.dumps(
            {
                "schema": "ctxc-release-install-smoke-0.1",
                "schema_count": len(expected_schema_names),
                "artifacts": results,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
