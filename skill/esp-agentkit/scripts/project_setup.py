"""Create a portable project from verified setup details without replacing files."""

from datetime import datetime, timezone
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import re
import tempfile

from projects import load_project


GENERATED_NAMES = ("app", "README.md", "project.json")


def prepare_project_directory(directory):
    """Check names and local write access before connecting to a device."""
    root = Path(directory).expanduser()
    try:
        if root.is_symlink() or not root.name.strip() or any(
            ord(char) < 32 or ord(char) == 127 for char in root.name
        ):
            raise ValueError
        root.mkdir(parents=True, exist_ok=True)
        root = root.resolve(strict=True)
        if any((root / name).exists() or (root / name).is_symlink() for name in GENERATED_NAMES):
            raise ValueError
        with tempfile.TemporaryFile(dir=root):
            pass
    except (OSError, ValueError, RuntimeError):
        raise RuntimeError(
            "Project destination must be writable and must not contain app, README.md or project.json. "
            "Existing files are not replaced; inspect a partial project or choose another directory."
        ) from None
    return root


def create_project(directory, record, password):
    """Write only new files, with project.json last so partial output is not discoverable."""
    root = prepare_project_directory(directory)
    device_id = record.get("device_id")
    if not isinstance(device_id, str) or re.fullmatch(r"(?:[0-9a-fA-F]{2})+", device_id) is None:
        raise RuntimeError("The verified device ID is invalid; no project was created.")
    try:
        address = str(IPv4Address(record["address"]))
    except (KeyError, ValueError):
        raise RuntimeError("The verified address is invalid; no project was created.") from None
    if not isinstance(password, str) or not 4 <= len(password) <= 9 or any(
        not 32 <= ord(char) <= 126 for char in password
    ):
        raise RuntimeError("The management password is invalid; no project was created.")
    config = {
        "schema_version": 1, "name": root.name, "aliases": [], "description": "",
        "app": "app",
        "device": {"device_id": device_id.lower(), "address": address,
                   "webrepl_password": password},
    }
    observed_at = datetime.now(timezone.utc).isoformat()
    readme = (
        f"# {root.name}\n\n"
        f"Setup verified at {observed_at}.\n\n"
        f"- Device ID: `{device_id.lower()}`\n"
        f"- Last verified address: `{address}` (DHCP can change it)\n"
        f"- Firmware: {record['firmware']}\n"
        f"- Firmware profile: {record['board']}\n"
        "- Physical carrier, peripherals and wiring: not recorded; verify before using GPIO.\n"
        "- WebREPL login: authenticated during setup.\n"
        "- Application deployment, startup and developer acceptance: not checked.\n\n"
        "## Application\n\n"
        "`app/app.py` is a local starter that prints a message and does not access GPIO. "
        "It has not been deployed. Add application dependencies and assets under `app/`.\n\n"
        "## Development record\n\n"
        "Record wiring, dependency versions, unresolved issues and actual observations here. "
        "For each deployment or observation, include its time and manifest ID. "
        "Distinguish attempted deployment, confirmed installation, startup and developer acceptance. "
        "Historical records do not establish the device's current state.\n\n"
        "Keep and back up this whole directory, including firmware images, `project.json`, "
        "source assets, `releases/` and `records/`, when handing off to another Agent or computer. "
        "`project.json` contains a plaintext management password. Keep it private; "
        "do not copy the password into notes, logs or application files.\n"
    )
    content = (
        (root / "app" / "app.py", '"""Replace this starter with the device application."""\n\nprint("Application starter")\n'),
        (root / "README.md", readme),
        (root / "project.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n"),
    )
    created = []
    app_created = False
    try:
        (root / "app").mkdir()
        app_created = True
        for path, source in content:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(source)
        return load_project(root)
    except (OSError, ValueError, RuntimeError, KeyboardInterrupt):
        # Remove only entries made by this call, preserving existing firmware and assets.
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        if app_created:
            try:
                (root / "app").rmdir()
            except OSError:
                pass
        raise
