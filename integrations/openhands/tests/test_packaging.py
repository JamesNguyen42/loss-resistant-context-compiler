from __future__ import annotations

import importlib.util
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import venv
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_NAME = "openhands-1.8.0.json"
TOKENIZER_VECTORS_NAME = "canonical-utf8-byte-tokenizer-vectors.json"
SDIST_AUDIT_PATHS = (
    "docs/RUNBOOK.md",
    "docs/UPSTREAM_RFC.md",
    "docs/RELEASE_CHECKLIST.md",
    "docs/EVENT_AUTHORITY_MAP.md",
    "compatibility/README.md",
    f"compatibility/{MANIFEST_NAME}",
    "demo/Dockerfile",
    "demo/Dockerfile.dockerignore",
    "demo/README.md",
)
PACKAGED_MARKDOWN_PATHS = (
    "README.md",
    "compatibility/README.md",
    "demo/README.md",
    "docs/RUNBOOK.md",
)
SUPPLY_CHAIN_URL = "https://github.com/JamesNguyen42/loss-resistant-context-compiler/blob/main/docs/SUPPLY_CHAIN.md"



def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        pytest.fail(
            f"command failed ({completed.returncode}): {command!r}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def _offline_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
        }
    )
    return env


def _copy_source(source: Path, target: Path, *, exclude_integrations: bool) -> None:
    ignored = [
        ".git",
        ".venv",
        ".artifacts",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "dist",
        "*.egg-info",
        "*.pyc",
    ]
    if exclude_integrations:
        ignored.append("integrations")
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(*ignored))


def _build(
    source: Path,
    output: Path,
    *,
    backend: str,
    wheel: bool,
    sdist: bool,
    env: dict[str, str],
) -> None:
    if not wheel and not sdist:
        raise ValueError("at least one distribution kind is required")
    script = [f"import {backend} as backend"]
    if wheel:
        script.append(f"print(backend.build_wheel({str(output)!r}))")
    if sdist:
        script.append(f"print(backend.build_sdist({str(output)!r}))")
    _run([sys.executable, "-B", "-c", "\n".join(script)], cwd=source, env=env)


@pytest.fixture(scope="module")
def built_distributions(request: pytest.FixtureRequest) -> dict[str, Path]:
    root = Path(tempfile.mkdtemp(prefix="ctxc-oh-package-"))
    request.addfinalizer(lambda: shutil.rmtree(root, ignore_errors=True))
    integration_source = root / "integration-source"
    core_source = root / "core-source"
    integration_dist = root / "integration-dist"
    core_dist = root / "core-dist"
    _copy_source(PROJECT_ROOT, integration_source, exclude_integrations=False)
    _copy_source(REPOSITORY_ROOT, core_source, exclude_integrations=True)
    integration_dist.mkdir()
    core_dist.mkdir()
    env = _offline_env()

    _build(
        integration_source,
        integration_dist,
        backend="setuptools.build_meta",
        wheel=True,
        sdist=True,
        env=env,
    )
    _build(
        core_source,
        core_dist,
        backend="_ctxc_build_backend",
        wheel=True,
        sdist=False,
        env=env,
    )

    integration_wheels = list(integration_dist.glob("ctxc_openhands-*.whl"))
    integration_sdists = list(integration_dist.glob("ctxc_openhands-*.tar.gz"))
    core_wheels = list(core_dist.glob("loss_resistant_context_compiler-*.whl"))
    assert len(integration_wheels) == 1
    assert len(integration_sdists) == 1
    assert len(core_wheels) == 1
    return {
        "integration_wheel": integration_wheels[0],
        "integration_sdist": integration_sdists[0],
        "core_wheel": core_wheels[0],
        "root": root,
    }


def _single_name(names: list[str], suffix: str) -> str:
    matches = [name for name in names if name.endswith(suffix)]
    assert matches == [matches[0]]
    return matches[0]


def test_wheel_and_sdist_contain_exact_resources_and_supply_chain_evidence(
    built_distributions: dict[str, Path],
) -> None:
    packaged_manifest = (
        PROJECT_ROOT / "src" / "ctxc_openhands" / "data" / MANIFEST_NAME
    ).read_bytes()
    audit_manifest = (PROJECT_ROOT / "compatibility" / MANIFEST_NAME).read_bytes()
    tokenizer_vectors = (
        PROJECT_ROOT / "src" / "ctxc_openhands" / "data" / TOKENIZER_VECTORS_NAME
    ).read_bytes()
    assert packaged_manifest == audit_manifest

    wheel_path = built_distributions["integration_wheel"]
    with zipfile.ZipFile(wheel_path) as wheel:
        names = wheel.namelist()
        assert wheel.read(f"ctxc_openhands/data/{MANIFEST_NAME}") == packaged_manifest
        assert wheel.read(
            f"ctxc_openhands/data/{TOKENIZER_VECTORS_NAME}"
        ) == tokenizer_vectors
        metadata_name = _single_name(names, ".dist-info/METADATA")
        metadata = wheel.read(metadata_name).decode("utf-8")
        assert (
            'Requires-Dist: openhands-agent-server==1.27.0; extra == "live"'
            in metadata
        )
        assert any(name.endswith(".dist-info/licenses/LICENSE") for name in names)
        assert not any(name.startswith(("openhands/", "openhands_sdk/")) for name in names)

    sdist_path = built_distributions["integration_sdist"]
    with tarfile.open(sdist_path, mode="r:gz") as archive:
        names = archive.getnames()
        manifest_member = _single_name(
            names, f"/src/ctxc_openhands/data/{MANIFEST_NAME}"
        )
        tokenizer_member = _single_name(
            names, f"/src/ctxc_openhands/data/{TOKENIZER_VECTORS_NAME}"
        )
        audit_member = _single_name(names, f"/compatibility/{MANIFEST_NAME}")
        lock_member = _single_name(names, "/requirements-live.lock")
        for member_name, expected in (
            (manifest_member, packaged_manifest),
            (audit_member, audit_manifest),
            (tokenizer_member, tokenizer_vectors),
        ):
            stream = archive.extractfile(member_name)
            assert stream is not None
            assert stream.read() == expected
        lock_stream = archive.extractfile(lock_member)
        assert lock_stream is not None
        lock_text = lock_stream.read().decode("utf-8")
        assert "intentionally NOT a complete transitive dependency lock" in lock_text
        for relative_path in SDIST_AUDIT_PATHS:
            member_name = _single_name(names, f"/{relative_path}")
            stream = archive.extractfile(member_name)
            assert stream is not None
            assert stream.read() == (PROJECT_ROOT / relative_path).read_bytes()

        archive_root = lock_member.rsplit("/", 1)[0]
        for markdown_path in PACKAGED_MARKDOWN_PATHS:
            markdown_member = f"{archive_root}/{markdown_path}"
            assert markdown_member in names
            stream = archive.extractfile(markdown_member)
            assert stream is not None
            markdown = stream.read().decode("utf-8")
            for raw_target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", markdown):
                target = raw_target.split("#", 1)[0]
                if (
                    not target
                    or "://" in target
                    or target.startswith(("mailto:", "data:"))
                ):
                    continue
                normalized = posixpath.normpath(
                    posixpath.join(posixpath.dirname(markdown_path), target)
                )
                assert normalized != ".."
                assert not normalized.startswith("../")
                assert f"{archive_root}/{normalized}" in names
            if markdown_path == "README.md":
                assert SUPPLY_CHAIN_URL in markdown


        assert "openhands-agent-server==1.27.0" in lock_text

def test_container_demo_dockerfile_is_offline_fake_runtime_only() -> None:
    dockerfile = (PROJECT_ROOT / "demo" / "Dockerfile").read_text(encoding="utf-8")
    demo_readme = (PROJECT_ROOT / "demo" / "README.md").read_text(encoding="utf-8")
    demo_contract = " ".join(demo_readme.split())
    instructions = {
        line.split(maxsplit=1)[0].upper()
        for line in dockerfile.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert dockerfile.splitlines()[:2] == [
        "ARG PYTHON_IMAGE=scratch",
        "FROM ${PYTHON_IMAGE}",
    ]
    assert instructions.isdisjoint({"RUN", "ADD"})
    assert re.search(
        r"(?mi)^\s*(?:ONBUILD\s+)?(?:RUN|ADD)\b",
        dockerfile,
    ) is None
    for forbidden in (
        "pip install",
        "apt-get",
        "apk add",
        "dnf install",
        "yum install",
        "curl ",
        "wget ",
        "git clone",
        "invoke-webrequest",
    ):
        assert forbidden not in dockerfile.lower()

    numeric_user = re.search(
        r"(?m)^USER ([1-9][0-9]*):([1-9][0-9]*)$",
        dockerfile,
    )
    assert numeric_user is not None
    assert numeric_user.groups() == ("65532", "65532")
    assert 'ENTRYPOINT ["python", "-m", "ctxc_openhands.cli"]' in dockerfile
    assert 'CMD ["doctor"]' in dockerfile

    assert "offline fake-runtime demonstration only" in demo_contract
    assert "already present locally and referenced by an immutable digest" in demo_contract
    assert "@sha256:<64-lowercase-hex-digest>" in demo_contract
    assert "Do not substitute a mutable tag." in demo_contract
    assert "--pull=false" in demo_contract
    assert "--network=none" in demo_contract



def test_direct_live_lock_binds_all_reviewed_wheels_without_claiming_closure() -> None:
    lock_text = (PROJECT_ROOT / "requirements-live.lock").read_text(encoding="utf-8")
    expected = {
        "openhands-ai==1.8.0": (
            "f389eb993659d5f8da7f4f47724311678aaca075d5e4233b2e34b23f7c895aec"
        ),
        "openhands-sdk==1.27.0": (
            "6a0fb0c664017757d25ab299c3581d400d7f9ce7a3ea3ad8b8f6ef00100ae7a8"
        ),
        "openhands-tools==1.27.0": (
            "593a9e56aeb9175fed25985748f0048e1a69db4a906cb923e9cd6bda4ff738dc"
        ),
        "openhands-agent-server==1.27.0": (
            "a15511e58b8032459a1a1cac0c9851eaa22fe48e291d6ba482da02700b656d47"
        ),
    }
    assert "--only-binary=:all:" in lock_text
    assert "NOT a complete transitive dependency lock" in lock_text
    for requirement, digest in expected.items():
        assert lock_text.count(requirement) == 1
        assert lock_text.count(f"--hash=sha256:{digest}") == 1


def test_clean_installed_core_and_integration_import_without_openhands(
    built_distributions: dict[str, Path],
) -> None:
    root = built_distributions["root"]
    environment = root / "clean-venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = _offline_env()

    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            str(built_distributions["core_wheel"]),
        ],
        cwd=root,
        env=env,
    )
    core_probe = textwrap.dedent(
        """
        import importlib.util
        import sys

        assert importlib.util.find_spec("ctxc_openhands") is None
        assert importlib.util.find_spec("openhands") is None
        before = {
            name
            for name in sys.modules
            if name == "openhands" or name.startswith("openhands.")
        }
        import context_compiler
        after = {
            name
            for name in sys.modules
            if name == "openhands" or name.startswith("openhands.")
        }
        assert after == before == set()
        print(context_compiler.__file__)
        """
    )
    _run([str(python), "-B", "-c", core_probe], cwd=root, env=env)

    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            str(built_distributions["integration_wheel"]),
        ],
        cwd=root,
        env=env,
    )
    integration_probe = textwrap.dedent(
        """
        import hashlib
        import importlib.metadata
        import importlib.util
        import sys
        from importlib.resources import files

        for distribution in (
            "openhands-ai",
            "openhands-sdk",
            "openhands-tools",
            "openhands-agent-server",
        ):
            try:
                importlib.metadata.distribution(distribution)
            except importlib.metadata.PackageNotFoundError:
                pass
            else:
                raise AssertionError(f"unexpected host distribution: {distribution}")
        assert importlib.util.find_spec("openhands") is None
        before = {
            name
            for name in sys.modules
            if name == "openhands" or name.startswith("openhands.")
        }
        import ctxc_openhands
        from ctxc_openhands.compatibility import verify_pin_manifest
        from ctxc_openhands.tokenizer import (
            CANONICAL_UTF8_BYTE_VECTOR_RESOURCE_FILENAME,
            CANONICAL_UTF8_BYTE_VECTOR_RESOURCE_FILE_SHA256,
        )
        verification = verify_pin_manifest()
        assert verification.live_scenario == "blocked"
        resource = files("ctxc_openhands").joinpath("data").joinpath(
            CANONICAL_UTF8_BYTE_VECTOR_RESOURCE_FILENAME
        )
        assert hashlib.sha256(resource.read_bytes()).hexdigest() == (
            CANONICAL_UTF8_BYTE_VECTOR_RESOURCE_FILE_SHA256
        )
        after = {
            name
            for name in sys.modules
            if name == "openhands" or name.startswith("openhands.")
        }
        assert after == before == set()
        assert "site-packages" in ctxc_openhands.__file__.replace("\\\\", "/")
        print(verification.file_sha256)
        """
    )
    _run([str(python), "-B", "-c", integration_probe], cwd=root, env=env)


def _load_clean_install_script():
    path = PROJECT_ROOT / "scripts" / "ci_clean_install.py"
    spec = importlib.util.spec_from_file_location(
        "_ctxc_openhands_ci_clean_install_test",
        path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clean_install_report_is_exclusive_and_import_paths_are_bound(
    tmp_path: Path,
) -> None:
    clean_install = _load_clean_install_script()
    environment = tmp_path / "environment"
    package = (
        environment
        / ("Lib" if os.name == "nt" else "lib")
        / "site-packages"
        / "example"
        / "__init__.py"
    )
    package.parent.mkdir(parents=True)
    package.write_text("", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("", encoding="utf-8")
    assert clean_install._installed_package_path(str(package), str(environment))
    assert not clean_install._installed_package_path(str(outside), str(environment))

    report = {"passed": True}
    destination = tmp_path / "clean-install-report.json"
    clean_install._write_report_exclusive(destination, report)
    assert json.loads(destination.read_text(encoding="utf-8")) == report
    retained = destination.read_bytes()
    with pytest.raises(clean_install.CleanInstallError, match="refusing to replace"):
        clean_install._write_report_exclusive(destination, {"passed": False})
    assert destination.read_bytes() == retained


def test_clean_install_probe_distinguishes_dev_extras_from_runtime_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean_install = _load_clean_install_script()
    assert clean_install._active_requirements(
        [
            'pytest>=8.0; extra == "dev"',
            'ruff>=0.6; extra == "dev"',
        ],
        label="core",
    ) == ()
    assert clean_install._active_requirements(
        [
            "loss-resistant-context-compiler==0.1.0",
            'openhands-ai==1.8.0; extra == "live"',
        ],
        label="integration",
    ) == ("loss-resistant-context-compiler==0.1.0",)

    monkeypatch.setattr(
        clean_install,
        "_installed_package_path",
        lambda _value, _prefix: True,
    )
    valid_probe = {
        "core_version": clean_install.CORE_VERSION,
        "integration_version": clean_install.INTEGRATION_VERSION,
        "core_file": "core.py",
        "integration_file": "integration.py",
        "sys_prefix": "environment",
        "core_requirements": ['pytest>=8.0; extra == "dev"'],
        "integration_requirements": [
            "loss-resistant-context-compiler==0.1.0",
            'openhands-ai==1.8.0; extra == "live"',
        ],
        "openhands_modules_before": [],
        "openhands_modules_after_core": [],
        "openhands_modules_after_integration": [],
        "openhands_distributions_absent": sorted(clean_install.LIVE_DISTRIBUTIONS),
        "openhands_distributions_present": [],
        "openhands_import_spec_present": False,
        "user_site_enabled": False,
        "manifest_blocker": clean_install.EXPECTED_BLOCKER,
        "packaged_manifest_present": True,
        "packaged_vectors_present": True,
    }
    clean_install._assert_probe(valid_probe)

    runtime_dependency = {
        **valid_probe,
        "core_requirements": ["requests>=2"],
    }
    with pytest.raises(clean_install.CleanInstallError, match="dependency-free core"):
        clean_install._assert_probe(runtime_dependency)
    with pytest.raises(clean_install.CleanInstallError, match="malformed"):
        clean_install._active_requirements(["not a valid requirement @"], label="core")


def test_offline_clean_install_toolchain_includes_wheel_dependency_closure() -> None:
    clean_install = _load_clean_install_script()
    assert clean_install.TOOLCHAIN_REQUIREMENTS == (
        "pip==25.0.1",
        "setuptools==83.0.0",
        "wheel==0.47.0",
        "packaging==26.2",
    )

    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "openhands-integration.yml"
    ).read_text(encoding="utf-8")
    assert workflow.count('"packaging==26.2"') == 3
