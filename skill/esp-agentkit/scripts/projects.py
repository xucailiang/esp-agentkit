"""Find portable device projects and read their project-local configuration."""

from dataclasses import dataclass, field
from ipaddress import IPv4Address
import json
import os
from pathlib import Path, PurePosixPath
import re


CONFIG_NAME = "project.json"
SKIP_DIRECTORIES = {
    "venv", "node_modules", "__pycache__", "accepted", "releases", "records",
    "build", "managed_components", "artifacts", "sdk",
}


class ProjectSelectionError(ValueError):
    """More than one project matches the supplied name or alias."""


@dataclass(frozen=True)
class Project:
    path: Path
    root: Path
    name: str
    aliases: tuple[str, ...]
    description: str
    app: Path
    device_id: str
    address: str
    password: str = field(repr=False)
    backend: str = "micropython"
    source: Path | None = None
    target: str | None = None
    port: str | None = None
    idf_version: str | None = None

    def public_summary(self):
        """Return the fields suitable for discovery output and receipts."""
        if self.backend == "esp-idf":
            return {
                "name": self.name,
                "aliases": list(self.aliases),
                "description": self.description,
                "path": str(self.path),
                "backend": self.backend,
                "source": str(self.source),
                "target": self.target,
                "idf_version": self.idf_version,
                "device_id": self.device_id,
                "identity": {"kind": "espressif-mac", "value": self.device_id},
                "port": self.port,
            }
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "description": self.description,
            "path": str(self.path),
            "app": str(self.app),
            "device_id": self.device_id,
            "address": self.address,
        }


def _text(value, field_name, *, optional=False):
    if not isinstance(value, str) or (not optional and not value.strip()):
        raise ValueError(f"Project field '{field_name}' must be a nonempty string.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"Project field '{field_name}' must not contain control characters.")
    return value.strip()


def _load_native(source, data, name, aliases, description, device):
    """Load a native project without importing an SDK or requiring network credentials."""
    if data.get("backend") != "esp-idf":
        raise ValueError("Project schema_version 2 requires backend 'esp-idf'.")
    identity = device.get("identity")
    if not isinstance(identity, dict) or identity.get("kind") != "espressif-mac":
        raise ValueError("Native device.identity must specify kind 'espressif-mac'.")
    device_id = identity.get("value")
    if not isinstance(device_id, str) or re.fullmatch(r"[0-9a-fA-F]{12}", device_id) is None:
        raise ValueError("Native device.identity.value must contain a verified 12-digit hexadecimal MAC.")
    port = device.get("port")
    if port is not None:
        port = _text(port, "device.port")
    idf = data.get("idf")
    if not isinstance(idf, dict):
        raise ValueError("Native project field 'idf' must be an object.")
    target = _text(idf.get("target"), "idf.target")
    if re.fullmatch(r"[a-z][a-z0-9_]*", target) is None:
        raise ValueError("Native idf.target must be a chip target name.")
    version = _text(idf.get("version"), "idf.version")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version) is None:
        raise ValueError("Native idf.version must identify an SDK tag.")
    source_name = _text(data.get("source"), "source")
    relative = PurePosixPath(source_name)
    if relative.is_absolute() or "\\" in source_name or ":" in source_name or ".." in relative.parts:
        raise ValueError("Native source must be a relative directory inside the project.")
    root = source.parent
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("Native source must not traverse symbolic links.")
    try:
        native_source = current.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("Native source must select an existing project directory.") from None
    if not native_source.is_relative_to(root) or not native_source.is_dir():
        raise ValueError("Native source must stay inside the project directory.")
    cmake = native_source / "CMakeLists.txt"
    if cmake.is_symlink() or not cmake.is_file():
        raise ValueError("Native source must contain a regular CMakeLists.txt file.")
    return Project(
        source, root, name, aliases, description, native_source, device_id.lower(), "", "",
        backend="esp-idf", source=native_source, target=target, port=port, idf_version=version,
    )


def load_project(path):
    """Read an explicitly selected directory or project.json, without echoing secrets."""
    candidate = Path(path).expanduser()
    if candidate.is_dir():
        candidate = candidate / CONFIG_NAME
    if candidate.name != CONFIG_NAME or candidate.is_symlink():
        raise ValueError("Select a project directory or its regular project.json file.")
    try:
        source = candidate.resolve(strict=True)
        with source.open(encoding="utf-8-sig") as stream:
            data = json.load(stream)
    except (OSError, UnicodeError, ValueError, RuntimeError):
        raise ValueError("Cannot read a valid UTF-8 JSON object from project.json.") from None
    if not isinstance(data, dict):
        raise ValueError("project.json must contain a JSON object.")
    version = data.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Unsupported project schema_version; expected 1 or 2.")
    name = _text(data.get("name"), "name")
    raw_aliases = data.get("aliases", [])
    if not isinstance(raw_aliases, list):
        raise ValueError("Project field 'aliases' must be a list of strings.")
    aliases = tuple(_text(alias, "aliases") for alias in raw_aliases)
    description = _text(data.get("description", ""), "description", optional=True)
    device = data.get("device")
    if not isinstance(device, dict):
        raise ValueError("Project field 'device' must be an object.")
    if version == 2:
        return _load_native(source, data, name, aliases, description, device)
    device_id = _text(device.get("device_id"), "device.device_id").lower()
    if len(device_id) % 2 or any(char not in "0123456789abcdef" for char in device_id):
        raise ValueError("Project field 'device.device_id' must contain hexadecimal byte pairs.")
    address = _text(device.get("address"), "device.address")
    try:
        address = str(IPv4Address(address))
    except ValueError:
        raise ValueError("Project field 'device.address' must be an IPv4 address.") from None
    password = device.get("webrepl_password")
    if not isinstance(password, str) or not 4 <= len(password) <= 9 or any(
        not 32 <= ord(char) <= 126 for char in password
    ):
        raise ValueError("Project field 'device.webrepl_password' must contain 4-9 printable ASCII characters.")
    app_name = _text(data.get("app"), "app")
    relative = PurePosixPath(app_name)
    if relative.is_absolute() or "\\" in app_name or ":" in app_name or ".." in relative.parts:
        raise ValueError("Project field 'app' must be a relative path inside the project directory.")
    root = source.parent
    unresolved = root.joinpath(*relative.parts)
    try:
        app = unresolved.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("Project field 'app' must select an existing application directory.") from None
    if app == root or not app.is_relative_to(root) or not app.is_dir():
        raise ValueError("Project field 'app' must select a directory strictly inside the project.")
    if app in (root / "releases", root / "records"):
        raise ValueError("Project field 'app' must not select the generated releases or records directory itself.")
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("Project field 'app' must not traverse symbolic links.")
    if (app / CONFIG_NAME).exists():
        raise ValueError("The application directory must not contain project.json.")
    return Project(source, root, name, aliases, description, app, device_id, address, password)


def _workspace(cwd):
    current = Path.cwd() if cwd is None else Path(cwd)
    try:
        current = current.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("Choose an existing working directory for project discovery.") from None
    if not current.is_dir():
        raise ValueError("Choose an existing working directory for project discovery.")
    scope = current
    for directory in (current, *current.parents):
        if (directory / ".git").exists():
            scope = directory
            break
    if scope == Path(scope.anchor) or scope == Path.home().resolve():
        raise ValueError("Project discovery requires a workspace directory, not a home or filesystem root.")
    return scope


def discover_projects(cwd=None):
    """Discover valid configurations in the workspace, skipping unrelated invalid files.

    The nearest enclosing Git workspace is searched, or cwd when no Git
    marker exists. No global index or operating-system configuration is used.
    Explicit selection through load_project exposes validation errors for a
    malformed configuration that discovery intentionally omits.
    """
    scope = _workspace(cwd)
    projects = []
    for directory, children, files in os.walk(scope, followlinks=False):
        parent = Path(directory)
        children[:] = sorted(
            name for name in children
            if not name.startswith(".")
            and name.casefold() not in SKIP_DIRECTORIES
            and not (parent / name).is_symlink()
        )
        if CONFIG_NAME in files and not (parent / CONFIG_NAME).is_symlink():
            try:
                projects.append(load_project(parent / CONFIG_NAME))
            except ValueError:
                continue
    return sorted(projects, key=lambda project: str(project.path))


def resolve_project(selector, cwd=None):
    """Select an explicit path or an exact project name, alias or directory name."""
    if not isinstance(selector, (str, Path)) or not str(selector).strip():
        raise ValueError("Provide a project name, alias, directory or project.json path.")
    selected = str(selector)
    base = Path.cwd() if cwd is None else Path(cwd)
    path = Path(selected).expanduser()
    if not path.is_absolute():
        path = base / path
    # Bare names must still detect ambiguity with matching project names and aliases.
    if isinstance(selector, Path) or "/" in selected or "\\" in selected or selected == CONFIG_NAME:
        return load_project(path)
    key = selected.strip().casefold()
    matches = [
        project for project in discover_projects(cwd)
        if key in {value.casefold() for value in (project.name, project.root.name, *project.aliases)}
    ]
    if not matches:
        raise ValueError("No matching project was found in the current workspace. Use a project name, alias or path.")
    if len(matches) > 1:
        paths = ", ".join(str(project.path) for project in matches)
        raise ProjectSelectionError("Project name or alias is ambiguous. Select a project file: " + paths)
    return matches[0]
