"""Cross-platform clean-install validation for ctxc-openhands release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import stat
import subprocess
import sys
import textwrap
import venv
from pathlib import Path
from typing import Any

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement

CORE_DISTRIBUTION = "loss-resistant-context-compiler"
CORE_VERSION = "0.1.1a5"
INTEGRATION_DISTRIBUTION = "ctxc-openhands"
INTEGRATION_VERSION = "0.1.0a6"
LIVE_DISTRIBUTIONS = (
    "openhands-ai",
    "openhands-sdk",
    "openhands-tools",
    "openhands-agent-server",
)
EXPECTED_BLOCKER = "hash-pinned-wheelhouse-absent"
BUILD_LOCK_NAME = "requirements-build.lock"

_PROBE = textwrap.dedent(
    f"""
    import importlib.metadata
    import importlib.resources
    import importlib.util
    import json
    import site
    import sys

    live_distributions = {LIVE_DISTRIBUTIONS!r}
    before = sorted(
        name for name in sys.modules
        if name == "openhands" or name.startswith("openhands.")
    )
    import context_compiler
    after_core = sorted(
        name for name in sys.modules
        if name == "openhands" or name.startswith("openhands.")
    )
    import ctxc_openhands
    after_integration = sorted(
        name for name in sys.modules
        if name == "openhands" or name.startswith("openhands.")
    )
    from ctxc_openhands.compatibility import verify_pin_manifest

    absent = []
    present = []
    for distribution in live_distributions:
        try:
            version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            absent.append(distribution)
        else:
            present.append({{"distribution": distribution, "version": version}})

    core_requirements = importlib.metadata.requires({CORE_DISTRIBUTION!r}) or []
    integration_requirements = (
        importlib.metadata.requires({INTEGRATION_DISTRIBUTION!r}) or []
    )
    manifest = verify_pin_manifest()
    packaged_manifest = importlib.resources.files("ctxc_openhands").joinpath(
        "data", "openhands-1.8.0.json"
    )
    packaged_vectors = importlib.resources.files("ctxc_openhands").joinpath(
        "data", "canonical-utf8-byte-tokenizer-vectors.json"
    )
    report = {{
        "core_version": context_compiler.__version__,
        "integration_version": ctxc_openhands.__version__,
        "core_file": context_compiler.__file__,
        "integration_file": ctxc_openhands.__file__,
        "sys_prefix": sys.prefix,
        "core_requirements": core_requirements,
        "integration_requirements": integration_requirements,
        "openhands_modules_before": before,
        "openhands_modules_after_core": after_core,
        "openhands_modules_after_integration": after_integration,
        "openhands_distributions_absent": sorted(absent),
        "openhands_distributions_present": present,
        "openhands_import_spec_present": importlib.util.find_spec("openhands")
        is not None,
        "user_site_enabled": site.ENABLE_USER_SITE,
        "manifest_blocker": manifest.blocker_code,
        "manifest_file_sha256": manifest.file_sha256,
        "packaged_manifest_present": packaged_manifest.is_file(),
        "packaged_vectors_present": packaged_vectors.is_file(),
    }}
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    """
)


class CleanInstallError(RuntimeError):
    """A release artifact failed isolated installation or smoke validation."""


def _load_build_input_helper() -> Any:
    path = Path(__file__).with_name("ci_build_inputs.py")
    spec = importlib.util.spec_from_file_location(
        "_ctxc_openhands_ci_build_inputs",
        path,
    )
    if spec is None or spec.loader is None:
        raise CleanInstallError("build-input verifier could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise CleanInstallError("build-input verifier could not be loaded") from exc
    return module


_BUILD_INPUTS = _load_build_input_helper()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(directory: Path, pattern: str, *, label: str) -> Path:
    matches = tuple(sorted(directory.glob(pattern)))
    if len(matches) != 1:
        raise CleanInstallError(
            f"{label} requires exactly one {pattern!r} artifact, found {len(matches)}"
        )
    artifact = matches[0]
    if artifact.is_symlink() or not artifact.is_file():
        raise CleanInstallError(f"{label} artifact must be a regular non-symbolic file")
    return artifact.resolve()


def _python_in(environment: Path) -> Path:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    executable = environment / relative
    if not executable.is_file():
        raise CleanInstallError(f"virtual environment Python is absent: {executable}")
    return executable


def _entrypoint_for(python: Path) -> Path:
    name = "ctxc-openhands.exe" if os.name == "nt" else "ctxc-openhands"
    entrypoint = python.parent / name
    if not entrypoint.is_file():
        raise CleanInstallError(f"ctxc-openhands entry point is absent: {entrypoint}")
    return entrypoint


def _clean_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONPYCACHEPREFIX", None)
    environment.pop("VIRTUAL_ENV", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    environment["PIP_CONFIG_FILE"] = os.devnull
    environment["PIP_NO_INDEX"] = "1"
    return environment


def _build_input_install_command(
    python: Path,
    wheelhouse: Path,
    build_lock: Path,
) -> list[str]:
    return [
        str(python),
        "-m",
        "pip",
        "--isolated",
        "install",
        "--no-deps",
        "--no-index",
        "--find-links",
        str(wheelhouse),
        "--only-binary=:all:",
        "--require-hashes",
        "--force-reinstall",
        "--requirement",
        str(build_lock),
    ]


def _package_install_command(
    python: Path,
    artifact: Path,
    *,
    no_build_isolation: bool,
) -> list[str]:
    command = [
        str(python),
        "-m",
        "pip",
        "--isolated",
        "install",
        "--no-index",
        "--no-deps",
        "--no-compile",
    ]
    if no_build_isolation:
        command.append("--no-build-isolation")
    command.append(str(artifact))
    return command


def _verify_build_inputs(
    build_lock: Path,
    wheelhouse: Path,
    *,
    builder_python: Path | None = None,
) -> dict[str, Any]:
    try:
        result = _BUILD_INPUTS.verify_build_inputs(
            build_lock,
            wheelhouse,
            builder_python=builder_python,
        )
    except _BUILD_INPUTS.BuildInputError as exc:
        raise CleanInstallError(f"build-input validation failed: {exc}") from exc
    if not isinstance(result, dict):
        raise CleanInstallError("build-input verifier returned an invalid result")
    return result


def _run(
    command: list[str],
    *,
    environment: dict[str, str],
    expected: int = 0,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=environment,
    )
    if result.returncode != expected:
        stdout = result.stdout[-4000:]
        stderr = result.stderr[-4000:]
        raise CleanInstallError(
            f"command returned {result.returncode}, expected {expected}: "
            f"{command!r}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )
    return result


def _json_stdout(result: subprocess.CompletedProcess[str], *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CleanInstallError(f"{label} did not emit one JSON value") from exc
    if not isinstance(value, dict):
        raise CleanInstallError(f"{label} JSON must be an object")
    return value


def _installed_package_path(value: object, prefix: object) -> bool:
    if not isinstance(value, str) or not isinstance(prefix, str):
        return False
    try:
        candidate = Path(value).resolve(strict=True)
        environment = Path(prefix).resolve(strict=True)
        candidate.relative_to(environment)
    except (OSError, ValueError):
        return False
    return any(part.casefold() == "site-packages" for part in candidate.parts)


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        status = path.lstat()
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if bool(getattr(status, "st_file_attributes", 0) & reparse_flag):
        return True
    reparse_tag = getattr(status, "st_reparse_tag", 0)
    return bool(reparse_tag)


def _assert_no_package_bytecode(environment: Path, *, label: str) -> None:
    package_names = ("context_compiler", "ctxc_openhands")
    roots: dict[str, list[Path]] = {name: [] for name in package_names}

    def raise_walk_error(error: OSError) -> None:
        raise error

    try:
        for current, directories, _files in os.walk(
            environment,
            topdown=True,
            onerror=raise_walk_error,
            followlinks=False,
        ):
            current_path = Path(current)
            if current_path.name.casefold() != "site-packages":
                continue
            for package_name in package_names:
                package_root = current_path / package_name
                if _is_link_or_reparse(package_root):
                    raise CleanInstallError(
                        f"{label} installed package root must not be linked"
                    )
                if package_root.is_dir():
                    roots[package_name].append(package_root)
            directories[:] = []
    except OSError as exc:
        raise CleanInstallError(f"{label} package bytecode scan failed") from exc

    if any(len(matches) != 1 for matches in roots.values()):
        raise CleanInstallError(
            f"{label} must contain exactly one installed root for each package"
        )

    try:
        for package_name in package_names:
            package_root = roots[package_name][0]
            for current, directories, files in os.walk(
                package_root,
                topdown=True,
                onerror=raise_walk_error,
                followlinks=False,
            ):
                current_path = Path(current)
                if any(
                    _is_link_or_reparse(current_path / name)
                    for name in (*directories, *files)
                ):
                    raise CleanInstallError(
                        f"{label} installed package tree contains a linked entry"
                    )
                if any(name.casefold() == "__pycache__" for name in directories):
                    raise CleanInstallError(
                        f"{label} contains an installed package bytecode cache"
                    )
                if any(name.casefold().endswith(".pyc") for name in files):
                    raise CleanInstallError(
                        f"{label} contains an installed package bytecode file"
                    )
    except OSError as exc:
        raise CleanInstallError(f"{label} package bytecode scan failed") from exc


def _active_requirements(value: object, *, label: str) -> tuple[str, ...]:
    """Return requirements enabled for an ordinary install with no extra."""

    if not isinstance(value, list) or any(type(item) is not str for item in value):
        raise CleanInstallError(f"{label} requirement metadata must be an array of strings")
    environment = default_environment()
    environment["extra"] = ""
    active: list[str] = []
    for raw in value:
        try:
            requirement = Requirement(raw)
        except InvalidRequirement as exc:
            raise CleanInstallError(f"{label} requirement metadata is malformed") from exc
        try:
            enabled = requirement.marker is None or requirement.marker.evaluate(environment)
        except Exception as exc:
            raise CleanInstallError(
                f"{label} requirement marker could not be evaluated"
            ) from exc
        if enabled:
            active.append(str(requirement))
    return tuple(sorted(active))


def _assert_probe(probe: dict[str, Any]) -> None:
    expected_absent = sorted(LIVE_DISTRIBUTIONS)
    core_active = _active_requirements(
        probe.get("core_requirements"),
        label="core",
    )
    integration_active = _active_requirements(
        probe.get("integration_requirements"),
        label="integration",
    )
    checks = {
        "core version": probe.get("core_version") == CORE_VERSION,
        "integration version": probe.get("integration_version") == INTEGRATION_VERSION,
        "core imported from clean environment": _installed_package_path(
            probe.get("core_file"), probe.get("sys_prefix")
        ),
        "integration imported from clean environment": _installed_package_path(
            probe.get("integration_file"), probe.get("sys_prefix")
        ),
        "dependency-free core": core_active == (),
        "exact core dependency": (
            integration_active
            == (f"{CORE_DISTRIBUTION}=={CORE_VERSION}",)
        ),
        "no modules before import": probe.get("openhands_modules_before") == [],
        "core import isolation": probe.get("openhands_modules_after_core") == [],
        "integration import isolation": (
            probe.get("openhands_modules_after_integration") == []
        ),
        "live distributions absent": (
            probe.get("openhands_distributions_absent") == expected_absent
            and probe.get("openhands_distributions_present") == []
        ),
        "no OpenHands import spec": probe.get("openhands_import_spec_present") is False,
        "user site disabled": probe.get("user_site_enabled") is False,
        "blocker retained": probe.get("manifest_blocker") == EXPECTED_BLOCKER,
        "manifest packaged": probe.get("packaged_manifest_present") is True,
        "tokenizer vectors packaged": probe.get("packaged_vectors_present") is True,
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise CleanInstallError("clean-install probe failed: " + ", ".join(failures))


def _assert_doctor(doctor: dict[str, Any], *, require_live: bool) -> None:
    checks = {
        "passed": doctor.get("passed") is (not require_live),
        "require_live": doctor.get("require_live") is require_live,
        "offline ready": doctor.get("offline_ready") is True,
        "live blocked": doctor.get("live_ready") is False,
        "ordinary import isolated": (
            doctor.get("ordinary_import_loaded_openhands") is False
        ),
        "blocker retained": (
            isinstance(doctor.get("manifest"), dict)
            and doctor["manifest"].get("retained_blocker_code") == EXPECTED_BLOCKER
        ),
        "no completeness claim": (
            doctor.get("semantic_completeness_claimed") is False
        ),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise CleanInstallError("doctor validation failed: " + ", ".join(failures))


def _manifest_digest(document: dict[str, Any], *, label: str) -> str:
    manifest = document.get("manifest")
    digest = manifest.get("file_sha256") if isinstance(manifest, dict) else None
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise CleanInstallError(f"{label} manifest digest is invalid")
    return digest


def _assert_manifest_evidence(
    probe: dict[str, Any],
    doctor: dict[str, Any],
    live_doctor: dict[str, Any],
) -> str:
    probe_digest = probe.get("manifest_file_sha256")
    doctor_digest = _manifest_digest(doctor, label="doctor")
    live_digest = _manifest_digest(live_doctor, label="live doctor")
    if (
        not isinstance(probe_digest, str)
        or probe_digest != doctor_digest
        or probe_digest != live_digest
    ):
        raise CleanInstallError(
            "import probe and doctor manifest evidence do not match"
        )
    return doctor_digest


def _validate_mode(
    *,
    mode: str,
    work_root: Path,
    wheelhouse: Path,
    build_lock: Path,
    core_wheel: Path,
    integration_artifact: Path,
) -> dict[str, Any]:
    initial_build_inputs = _verify_build_inputs(build_lock, wheelhouse)
    environment_path = work_root / mode
    if environment_path.exists():
        raise CleanInstallError(f"refusing to replace existing path: {environment_path}")
    venv.EnvBuilder(with_pip=True, clear=False).create(environment_path)
    python = _python_in(environment_path)
    environment = _clean_environment()
    _run(
        _build_input_install_command(python, wheelhouse, build_lock),
        environment=environment,
    )
    installed_build_inputs = _verify_build_inputs(
        build_lock,
        wheelhouse,
        builder_python=python,
    )
    if (
        installed_build_inputs["lock"] != initial_build_inputs["lock"]
        or installed_build_inputs["wheelhouse"] != initial_build_inputs["wheelhouse"]
        or installed_build_inputs["builder"].get("verified") is not True
    ):
        raise CleanInstallError("installed build-input inventory did not remain exact")
    _run(
        _package_install_command(
            python,
            core_wheel,
            no_build_isolation=False,
        ),
        environment=environment,
    )
    _run(
        _package_install_command(
            python,
            integration_artifact,
            no_build_isolation=mode == "sdist",
        ),
        environment=environment,
    )
    _assert_no_package_bytecode(environment_path, label=f"{mode} post-install")

    probe = _json_stdout(
        _run([str(python), "-c", _PROBE], environment=environment),
        label=f"{mode} import probe",
    )
    _assert_probe(probe)
    entrypoint = _entrypoint_for(python)
    doctor = _json_stdout(
        _run([str(entrypoint), "doctor"], environment=environment),
        label=f"{mode} doctor",
    )
    _assert_doctor(doctor, require_live=False)
    live_doctor = _json_stdout(
        _run(
            [str(entrypoint), "doctor", "--require-live"],
            environment=environment,
            expected=2,
        ),
        label=f"{mode} live doctor",
    )
    _assert_doctor(live_doctor, require_live=True)
    manifest_digest = _assert_manifest_evidence(probe, doctor, live_doctor)
    _assert_no_package_bytecode(environment_path, label=f"{mode} post-probe")
    final_build_inputs = _verify_build_inputs(build_lock, wheelhouse)
    if final_build_inputs != initial_build_inputs:
        raise CleanInstallError("build-input inventory changed during clean-install validation")
    return {
        "mode": mode,
        "artifact": integration_artifact.name,
        "artifact_sha256": _sha256(integration_artifact),
        "probe": probe,
        "doctor_manifest_sha256": manifest_digest,
        "doctor_live_exit": 2,
        "build_lock_sha256": initial_build_inputs["lock"]["sha256"],
        "build_input_inventory_sha256": initial_build_inputs["wheelhouse"][
            "inventory_sha256"
        ],
        "builder_inventory_verified": True,
        "passed": True,
    }


def _write_report_exclusive(path: Path, report: dict[str, Any]) -> None:
    payload = (
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise CleanInstallError(
            f"refusing to replace existing report: {path}"
        ) from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-dist", required=True)
    parser.add_argument("--integration-dist", required=True)
    parser.add_argument("--wheelhouse", required=True)
    parser.add_argument("--build-lock", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--report", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    core_dist = Path(args.core_dist).expanduser().absolute()
    integration_dist = Path(args.integration_dist).expanduser().absolute()
    wheelhouse = Path(args.wheelhouse).expanduser().absolute()
    build_lock = Path(args.build_lock).expanduser().absolute()
    work_root = Path(args.work_dir).expanduser().absolute()
    report_path = Path(args.report).expanduser().absolute()
    for directory, label in (
        (core_dist, "core distribution directory"),
        (integration_dist, "integration distribution directory"),
        (wheelhouse, "tool wheelhouse"),
    ):
        if directory.is_symlink() or not directory.is_dir():
            raise CleanInstallError(f"{label} is not a regular directory: {directory}")
    if (
        build_lock.name != BUILD_LOCK_NAME
        or build_lock.is_symlink()
        or not build_lock.is_file()
    ):
        raise CleanInstallError(
            f"build lock must be the regular {BUILD_LOCK_NAME} file"
        )
    if work_root.exists():
        raise CleanInstallError(f"refusing to replace existing work directory: {work_root}")
    if report_path.exists():
        raise CleanInstallError(f"refusing to replace existing report: {report_path}")
    work_root.parent.mkdir(parents=True, exist_ok=True)
    work_root.mkdir()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    core_wheel = _artifact(
        core_dist,
        "loss_resistant_context_compiler-*.whl",
        label="core wheel",
    )
    integration_wheel = _artifact(
        integration_dist,
        "ctxc_openhands-*.whl",
        label="integration wheel",
    )
    integration_sdist = _artifact(
        integration_dist,
        "ctxc_openhands-*.tar.gz",
        label="integration sdist",
    )
    initial_build_inputs = _verify_build_inputs(build_lock, wheelhouse)
    modes = [
        _validate_mode(
            mode="wheel",
            work_root=work_root,
            wheelhouse=wheelhouse,
            build_lock=build_lock,
            core_wheel=core_wheel,
            integration_artifact=integration_wheel,
        ),
        _validate_mode(
            mode="sdist",
            work_root=work_root,
            wheelhouse=wheelhouse,
            build_lock=build_lock,
            core_wheel=core_wheel,
            integration_artifact=integration_sdist,
        ),
    ]
    if modes[0]["doctor_manifest_sha256"] != modes[1]["doctor_manifest_sha256"]:
        raise CleanInstallError(
            "wheel and sdist manifest evidence do not match"
        )
    final_build_inputs = _verify_build_inputs(build_lock, wheelhouse)
    if final_build_inputs != initial_build_inputs:
        raise CleanInstallError("build-input inventory changed across clean-install modes")
    report: dict[str, Any] = {
        "schema": "ctxc-openhands-clean-install-ci-0.2",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "core_wheel": core_wheel.name,
        "core_wheel_sha256": _sha256(core_wheel),
        "build_lock": BUILD_LOCK_NAME,
        "build_lock_sha256": initial_build_inputs["lock"]["sha256"],
        "build_input_inventory_sha256": initial_build_inputs["wheelhouse"][
            "inventory_sha256"
        ],
        "build_input_count": initial_build_inputs["wheelhouse"]["file_count"],
        "modes": modes,
        "live_dependencies_installed": False,
        "live_execution_claimed": False,
        "semantic_completeness_claimed": False,
        "passed": all(mode["passed"] is True for mode in modes),
    }
    report["report_sha256"] = hashlib.sha256(
        _canonical_json(report).encode("utf-8")
    ).hexdigest()
    _write_report_exclusive(report_path, report)
    print(_canonical_json({"passed": report["passed"], "report": str(report_path)}))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
