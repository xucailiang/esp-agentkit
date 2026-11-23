"""Build an ESP-IDF source snapshot and retain verified, portable artifacts."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from operations import resource_lock, run_process


EXCLUDED = frozenset({
    ".git", ".venv", "venv", "__pycache__", "build", "artifacts", "records",
    "releases", "private", "secret", "secrets", "project.json", ".DS_Store",
    "sdkconfig.old", ".env", "credentials.json", "secrets.json",
})
COMPONENT_EXCLUDED_DIRS = EXCLUDED - {"private"}
COMPONENT_REGISTRATION = re.compile(rb"^\s*(?:idf_component_register|register_component)\s*\(", re.I | re.M)
SECRET_SETTING = re.compile(
    r'^\s*(CONFIG_[A-Z0-9_]*(?:PASSWORD|PASSPHRASE|SSID|API_KEY|SECRET|TOKEN)[A-Z0-9_]*)=(.+)$'
)
IRREVERSIBLE_SETTINGS = frozenset({
    "CONFIG_SECURE_BOOT", "CONFIG_SECURE_FLASH_ENC_ENABLED", "CONFIG_SECURE_DISABLE_ROM_DL_MODE",
})


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(path):
    checksum = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            checksum.update(chunk)
            size += len(chunk)
    return {"sha256": checksum.hexdigest(), "size": size}


def _safe_file(root, name):
    if not isinstance(name, str) or "\\" in name or ":" in name or "\0" in name:
        raise ValueError("Artifact paths must be relative POSIX file names.")
    relative = PurePosixPath(name)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("Artifact paths must stay inside the archive.")
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("Artifact paths must not traverse symbolic links.")
    if not current.is_file() or not current.resolve().is_relative_to(root.resolve()):
        raise ValueError("An archived file is missing or outside its archive.")
    return current


def _check_configuration(path, *, effective=False):
    """Reject known credential strings and unsupported first-boot eFuse settings.

    This bounds the ordinary development workflow; it is not an application
    sandbox or a general detector for secrets in arbitrary source code.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.strip().partition("=")
        if effective and separator and key in IRREVERSIBLE_SETTINGS and value.strip() == "y":
            raise ValueError(
                "This development workflow refuses firmware enabling Secure Boot, "
                "Flash Encryption or permanently disabling ROM download mode."
            )
        match = SECRET_SETTING.match(line)
        if match and match[2].strip().startswith('"') and match[2].strip() != '""':
            raise ValueError(
                "Keep network credentials and secrets out of sdkconfig; "
                "provide them at runtime before archiving this project."
            )


def _inside_component(root, directory):
    """Recognize component roots before the SDK configures the source copy."""
    while directory != root:
        cmake = directory / "CMakeLists.txt"
        if not cmake.is_symlink() and cmake.is_file():
            if (
                directory == root / "main"
                or directory.parent in (root / "components", root / "managed_components")
                or (directory / "idf_component.yml").is_file()
                or COMPONENT_REGISTRATION.search(cmake.read_bytes())
            ):
                return True
        directory = directory.parent
    return False


def _inventory(root, *, source=False):
    files = {}
    for directory, children, names in os.walk(root, followlinks=False):
        parent = Path(directory)
        excluded_dirs = EXCLUDED
        if source and "private" in children and _inside_component(root, parent):
            excluded_dirs = COMPONENT_EXCLUDED_DIRS
        children[:] = sorted(name for name in children if not source or name not in excluded_dirs)
        for name in children:
            if (parent / name).is_symlink():
                raise ValueError("Source and artifact trees must not contain symbolic links.")
        for name in sorted(names):
            if source and name in EXCLUDED:
                continue
            path = parent / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("Source and artifact trees must contain regular files only.")
            if source and name.startswith("sdkconfig"):
                _check_configuration(path)
            files[path.relative_to(root).as_posix()] = _digest(path)
    return files


def _copy_inventory(source, destination, files):
    for name, expected in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_safe_file(source, name), target)
        if _digest(target) != expected:
            raise ValueError("Build inputs changed while copying the source snapshot.")


def _archive_root(project, *, create=False):
    root = project.root / "artifacts"
    if root.is_symlink():
        raise ValueError("The artifact directory must not be a symbolic link.")
    if create:
        root.mkdir(exist_ok=True)
    if not root.is_dir():
        raise ValueError("No artifact directory exists for this project.")
    return root


def artifact_path(project, build_id):
    """Locate one immutable build without accepting paths as build identifiers."""
    if not isinstance(build_id, str) or re.fullmatch(r"[0-9a-f]{64}", build_id) is None:
        raise ValueError("Expected a complete lowercase SHA-256 build ID.")
    path = _archive_root(project) / build_id
    if path.is_symlink() or not path.is_dir():
        raise ValueError("The selected build archive is missing or invalid.")
    return path


def verify_artifact(project, build_id):
    """Verify manifest identity, every retained byte and the selected flash files."""
    root = artifact_path(project, build_id)
    try:
        manifest = json.loads(_safe_file(root, "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError("The build manifest is missing or invalid.") from None
    if not isinstance(manifest, dict):
        raise ValueError("The build manifest must be an object.")
    body = {key: value for key, value in manifest.items() if key != "build_id"}
    if (manifest.get("build_id") != build_id
            or hashlib.sha256(_canonical(body)).hexdigest() != build_id
            or manifest.get("schema_version") != 1 or manifest.get("backend") != "esp-idf"
            or manifest.get("target") != project.target):
        raise ValueError("The build manifest does not match its ID or project target.")
    actual = _inventory(root)
    actual.pop("manifest.json", None)
    if actual != manifest.get("files"):
        raise ValueError("Build archive files differ from the verified manifest.")
    configuration = manifest.get("sdkconfig", "source/sdkconfig")
    if not isinstance(configuration, str) or not configuration.startswith("source/"):
        raise ValueError("The effective SDK configuration must be retained inside the source archive.")
    _check_configuration(_safe_file(root, configuration), effective=True)
    flash_files = manifest.get("flash_files")
    if not isinstance(flash_files, dict) or not flash_files:
        raise ValueError("The archive has no verified flash images.")
    for offset, name in flash_files.items():
        if not isinstance(offset, str) or re.fullmatch(r"0x[0-9a-f]+", offset) is None:
            raise ValueError("The archive has an invalid flash offset.")
        if name not in actual or not name.startswith("build/"):
            raise ValueError("A flash image is missing from the archive manifest.")
        _safe_file(root, name)
    for field in ("app_bin", "app_elf"):
        if manifest.get(field) not in actual:
            raise ValueError("The application image or ELF is missing from the archive.")
    if actual[manifest["app_elf"]]["sha256"] != manifest.get("app_elf_sha256"):
        raise ValueError("The application ELF identity does not match the archive.")
    return manifest


def _command(argv, *, cwd, env, timeout, stdout=subprocess.PIPE, stderr=subprocess.PIPE):
    result = run_process(argv, cwd=cwd, env=env, timeout=timeout, stdout=stdout, stderr=stderr)
    if result.returncode:
        raise ValueError("The SDK command failed; inspect the retained build log.")
    output = result.stdout
    return output.decode("utf-8", "replace").strip() if isinstance(output, bytes) else output


def _sdk_environment(project, idf_path, overrides):
    sdk = Path(idf_path).expanduser().resolve(strict=True)
    if not (sdk / "tools" / "idf.py").is_file():
        raise ValueError("Select an installed ESP-IDF checkout containing tools/idf.py.")
    env = {**os.environ, **(overrides or {}), "IDF_PATH": str(sdk)}
    version = _command(["git", "describe", "--tags", "--exact-match", "HEAD"],
                       cwd=sdk, env=env, timeout=15)
    if version != project.idf_version:
        raise ValueError("The ESP-IDF checkout does not match the project's pinned version.")
    dirty = _command(["git", "status", "--porcelain", "--untracked-files=no"],
                     cwd=sdk, env=env, timeout=30)
    if dirty:
        raise ValueError("The ESP-IDF checkout has modified tracked inputs; use the pinned checkout.")
    commit = _command(["git", "rev-parse", "HEAD"], cwd=sdk, env=env, timeout=15)
    python_env = env.get("IDF_PYTHON_ENV_PATH")
    if not python_env:
        raise ValueError("Activate ESP-IDF's exported environment before building (IDF_PYTHON_ENV_PATH is missing).")
    python = Path(python_env) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise ValueError("ESP-IDF's Python environment is unavailable; prepare the pinned SDK environment.")
    env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "")
    python_version = _command([str(python), "--version"], cwd=sdk, env=env, timeout=15)
    return sdk, python, env, {"version": version, "commit": commit, "python_version": python_version}


def _load_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError("ESP-IDF did not produce valid build metadata.") from None
    if not isinstance(value, dict):
        raise ValueError("ESP-IDF build metadata must be an object.")
    return value


def _check_description(description, source, build, sdk, target):
    if description.get("target") != target:
        raise ValueError("The built SDK target does not match the project target.")
    version = description.get("project_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("ESP-IDF did not report a nonempty project version.")
    components = description.get("build_component_paths")
    if not isinstance(components, list) or not all(isinstance(name, str) for name in components):
        raise ValueError("ESP-IDF did not report valid component input paths.")
    # IDF 5.5 joins component and test-component lists with a semicolon before
    # JSON conversion; no test components leaves a trailing empty list entry.
    components = [name for name in components if name != ""]
    if not components:
        raise ValueError("ESP-IDF did not report the component inputs.")
    for name in components:
        if not Path(name).is_absolute():
            raise ValueError("ESP-IDF reported a component input path that is not absolute.")
        path = Path(name).resolve()
        if not (path.is_relative_to(source) or path.is_relative_to(sdk)):
            raise ValueError("External component input is outside the captured source tree and SDK: " + repr(str(path)))
    config_file = description.get("config_file")
    if not isinstance(config_file, str) or not Path(config_file).is_absolute():
        raise ValueError("ESP-IDF did not report an absolute effective SDK configuration path.")
    configuration = Path(config_file).resolve()
    if not configuration.is_relative_to(source):
        raise ValueError("The effective SDK configuration is outside the project source tree: " + repr(str(configuration)))
    commands = _load_compile_commands(build / "compile_commands.json")
    for entry in commands:
        location = Path(entry.get("directory", build))
        path = Path(entry["file"])
        path = (location / path).resolve() if not path.is_absolute() else path.resolve()
        if not any(path.is_relative_to(root) for root in (source, build, sdk)):
            raise ValueError("An external compilation input is outside the captured project and SDK: " + repr(str(path)))


def _load_compile_commands(path):
    try:
        commands = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError("ESP-IDF did not produce compilation input metadata.") from None
    if not isinstance(commands, list) or not commands or not all(
        isinstance(entry, dict) and isinstance(entry.get("file"), str) for entry in commands
    ):
        raise ValueError("Invalid compilation input metadata.")
    return commands


def _build_file(build, name):
    path = Path(name)
    if path.is_absolute():
        try:
            name = path.relative_to(build).as_posix()
        except ValueError:
            raise ValueError("ESP-IDF referenced an artifact outside its build directory.") from None
    return _safe_file(build, name)


def _collect_build(build, archive, description, target):
    flasher = _load_json(build / "flasher_args.json")
    if flasher.get("extra_esptool_args", {}).get("chip") != target:
        raise ValueError("The flash layout target does not match the selected project.")
    raw_files = flasher.get("flash_files")
    if not isinstance(raw_files, dict) or not raw_files:
        raise ValueError("ESP-IDF did not produce a complete flash layout.")
    files = {}
    ranges = []
    for offset, name in raw_files.items():
        try:
            address = int(offset, 0)
        except (TypeError, ValueError):
            raise ValueError("ESP-IDF produced an invalid flash offset.") from None
        if address < 0:
            raise ValueError("Flash offsets must be nonnegative.")
        path = _build_file(build, name)
        relative = path.relative_to(build).as_posix()
        canonical_offset = hex(address)
        if canonical_offset in files:
            raise ValueError("ESP-IDF produced duplicate flash offsets.")
        files[canonical_offset] = "build/" + relative
        ranges.append((address, address + path.stat().st_size))
    ranges.sort()
    if any(left[1] > right[0] for left, right in zip(ranges, ranges[1:])):
        raise ValueError("ESP-IDF produced overlapping flash images.")
    settings = flasher.get("flash_settings")
    if not isinstance(settings, dict) or any(
        not isinstance(settings.get(key), str) or not re.fullmatch(r"[a-zA-Z0-9]+", settings[key])
        for key in ("flash_mode", "flash_size", "flash_freq")
    ):
        raise ValueError("ESP-IDF did not produce valid flash settings.")
    app_bin = _build_file(build, description["app_bin"])
    app_elf = _build_file(build, description["app_elf"])
    app_map = app_elf.with_suffix(".map")
    if "build/" + app_bin.relative_to(build).as_posix() not in files.values():
        raise ValueError("The application image is absent from the flash layout.")
    needed = {value.removeprefix("build/") for value in files.values()}
    needed.update(path.relative_to(build).as_posix() for path in (app_bin, app_elf, app_map))
    needed.update(("flasher_args.json", "project_description.json", "compile_commands.json"))
    for name in sorted(needed):
        incoming = _safe_file(build, name)
        destination = archive / "build" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(incoming, destination)
    return {
        "flash_files": files, "flash_settings": {key: settings[key] for key in ("flash_mode", "flash_size", "flash_freq")},
        "app_bin": "build/" + app_bin.relative_to(build).as_posix(),
        "app_elf": "build/" + app_elf.relative_to(build).as_posix(),
        "app_elf_sha256": _digest(app_elf)["sha256"],
    }


def build_project(project, idf_path, timeout=1800, *, env=None):
    """Build a frozen project tree; publish an archive only after complete verification."""
    if project.backend != "esp-idf":
        raise ValueError("Native builds require an ESP-IDF project.")
    if not isinstance(timeout, (int, float)) or not 0 < timeout < float("inf"):
        raise ValueError("Build timeout must be a finite positive number.")
    if not (project.source / "CMakeLists.txt").is_file():
        raise ValueError("The ESP-IDF source directory must contain CMakeLists.txt.")
    sdk, python, environment, sdk_record = _sdk_environment(project, idf_path, env)
    artifacts = _archive_root(project, create=True)
    key = "build:" + str(project.root.resolve())
    with resource_lock([key], operation="ESP-IDF build"):
        inputs = _inventory(project.source, source=True)
        input_digest = hashlib.sha256(_canonical(inputs)).hexdigest()
        with tempfile.TemporaryDirectory(prefix=".build-", dir=artifacts) as temporary:
            work = Path(temporary).resolve()
            source, build, archive = work / "source", work / "build", work / "archive"
            source.mkdir()
            _copy_inventory(project.source, source, inputs)
            archive.mkdir()
            log = work / "build.log"
            deadline = time.monotonic() + timeout
            command = [str(python), str(sdk / "tools" / "idf.py"), "-B", str(build),
                       "-DIDF_TARGET=" + project.target]
            try:
                with log.open("wb") as stream:
                    for action in ("reconfigure", "build"):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(command, timeout)
                        _command([*command, action], cwd=source, env=environment, timeout=remaining,
                                 stdout=stream, stderr=subprocess.STDOUT)
                        description = _load_json(build / "project_description.json")
                        _check_description(description, source, build, sdk, project.target)
                        configuration = Path(description["config_file"]).resolve()
                        _check_configuration(configuration, effective=True)
                        current = _inventory(source, source=True)
                        if action == "reconfigure":
                            configured_inputs = current
                        elif current != configured_inputs:
                            raise ValueError("Source inputs changed during compilation; no archive was published.")
                _copy_inventory(source, archive / "source", configured_inputs)
                flash = _collect_build(build, archive, description, project.target)
                shutil.copyfile(log, archive / "build" / "build.log")
                compiler = description.get("c_compiler")
                toolchain = None
                if isinstance(compiler, str) and compiler:
                    compiler_version = _command([compiler, "--version"], cwd=source,
                                                env=environment, timeout=15)
                    toolchain = {"compiler": Path(compiler).name, "version": compiler_version}
                manifest = {
                    "schema_version": 1, "backend": "esp-idf",
                    "created_at": datetime.now(timezone.utc).isoformat(), "target": project.target,
                    "sdk": sdk_record, "toolchain": toolchain,
                    "project_version": description["project_version"],
                    "source_path_at_build": str(source), "source_archive": "source",
                    "sdkconfig": "source/" + configuration.relative_to(source).as_posix(),
                    "input_digest": input_digest,
                    "source_digest": hashlib.sha256(_canonical(configured_inputs)).hexdigest(),
                    "environment_reconstruction": "not_checked", "byte_reproducibility": "not_checked",
                    "files": _inventory(archive), **flash,
                }
                build_id = hashlib.sha256(_canonical(manifest)).hexdigest()
                manifest["build_id"] = build_id
                (archive / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                archive.rename(artifacts / build_id)
                verify_artifact(project, build_id)
                return {"build_id": build_id, "artifact": str(artifacts / build_id),
                        "target": project.target, "project_version": manifest["project_version"],
                        "app_elf_sha256": manifest["app_elf_sha256"]}
            except BaseException:
                if log.is_file():
                    failure = artifacts / ("failed-" + uuid4().hex + ".log")
                    shutil.copyfile(log, failure)
                    print("Build did not publish an artifact. Log: " + str(failure), file=sys.stderr)
                raise
