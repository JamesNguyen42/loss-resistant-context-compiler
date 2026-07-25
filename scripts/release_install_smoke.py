"""Validate clean wheel and source-distribution installs on any supported host."""

from __future__ import annotations

import argparse
import json
import os
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


def _smoke_artifact(path: Path, expected_schema_names: list[str]) -> dict[str, str]:
    artifact = path.resolve(strict=True)
    kind = _artifact_kind(artifact)
    with tempfile.TemporaryDirectory(prefix=f"ctxc-{kind}-install-") as directory:
        environment = Path(directory) / "venv"
        _run([sys.executable, "-m", "venv", str(environment)])
        python = _venv_python(environment)
        install = [str(python), "-m", "pip", "install"]
        if kind == "wheel":
            install.extend(["--no-index", "--no-deps"])
        else:
            _run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "setuptools>=77",
                    "wheel>=0.41",
                ]
            )
            install.extend(["--no-build-isolation", "--no-deps"])
        install.append(str(artifact))
        _run(install)
        _assert_installed_package(python, environment, expected_schema_names)
    return {"artifact": artifact.name, "kind": kind, "status": "passed"}


def _release_artifacts(directory: Path) -> list[Path]:
    artifacts = sorted(directory.glob("*.whl")) + sorted(directory.glob("*.tar.gz"))
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    expected_schema_names = sorted(
        path.name for path in args.schema_dir.glob(SCHEMA_GLOB)
    )
    if not expected_schema_names:
        raise ValueError(f"no packaged schemas found in {args.schema_dir}")
    results = [
        _smoke_artifact(path, expected_schema_names)
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
