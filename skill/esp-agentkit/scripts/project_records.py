"""Freeze deployable applications and keep portable deployment and acceptance records."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from uuid import uuid4

from deployment import prepare_deploy


STAGES = frozenset({
    "prepare", "connect", "identify", "upload", "deploy", "commit", "restart",
    "reconnect", "complete", "cancelled", "failed",
})
RESULT_FLAGS = ("committed", "restart_acknowledged", "webrepl_authenticated")


def _manifest_id(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("Expected a complete lowercase SHA-256 manifest ID.")
    return value


def _directory(project, name):
    path = project.root / name
    if path.is_symlink():
        raise ValueError("Project release and record directories cannot be symbolic links.")
    path.mkdir(exist_ok=True)
    if not path.is_dir():
        raise ValueError("Expected a project release or record directory.")
    return path


def _snapshot(project, manifest_id):
    path = _directory(project, "releases") / _manifest_id(manifest_id)
    if path.is_symlink():
        raise ValueError("An application snapshot cannot be a symbolic link.")
    try:
        bundle = prepare_deploy(path)
    except (OSError, ValueError):
        raise ValueError("The application snapshot is missing or invalid.") from None
    if bundle.manifest_id != manifest_id or any(Path(name).name == "project.json" for name in bundle.files):
        raise ValueError("The application snapshot does not match its manifest.")
    return bundle


def freeze_application(project):
    """Copy manifest-selected assets into a verified snapshot before uploading."""
    root, app = project.root.resolve(), project.app.resolve()
    if app == root or not app.is_relative_to(root):
        raise ValueError("The application must be a dedicated directory inside the project.")
    source = prepare_deploy(project.app)
    if any(Path(name).name == "project.json" for name in source.files):
        raise ValueError("Keep project.json outside the deployable application.")
    releases = _directory(project, "releases")
    destination = releases / source.manifest_id
    if destination.exists() or destination.is_symlink():
        return _snapshot(project, source.manifest_id)

    with tempfile.TemporaryDirectory(prefix=".freeze-", dir=releases) as temporary:
        staging = Path(temporary) / "app"
        staging.mkdir()
        for name, asset in source.files.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            checksum = hashlib.sha256()
            size = 0
            with asset.path.open("rb") as incoming, target.open("xb") as outgoing:
                while chunk := incoming.read(65536):
                    outgoing.write(chunk)
                    checksum.update(chunk)
                    size += len(chunk)
            if size != asset.size or checksum.hexdigest() != asset.digest:
                raise ValueError("Application files changed while freezing the release. Retry preparation.")
        if prepare_deploy(staging).manifest_id != source.manifest_id:
            raise ValueError("The application snapshot failed verification.")
        try:
            staging.rename(destination)
        except OSError:
            # Another local operation may have published the same release.
            if not destination.exists():
                raise
            _snapshot(project, source.manifest_id)
    return _snapshot(project, source.manifest_id)


def _write_record(project, record):
    records = _directory(project, "records")
    now = datetime.now(timezone.utc)
    identifier = now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex
    target = records / (identifier + ".json")
    staging = records / ("." + identifier + ".tmp")
    document = {"schema_version": 1, "recorded_at": now.isoformat(), **record}
    try:
        with staging.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        staging.rename(target)
    finally:
        if staging.exists():
            staging.unlink()
    return target


def record_deployment(project, bundle, result, exit_code, stage):
    """Record only confirmed result flags; an absent flag remains unknown."""
    if stage not in STAGES:
        raise ValueError("Unknown deployment record stage.")
    if type(exit_code) is not int:
        raise ValueError("The deployment exit code must be an integer.")
    if not isinstance(result, dict):
        raise ValueError("Expected structured deployment results.")
    if result.get("device_id", project.device_id) != project.device_id:
        raise ValueError("Deployment results belong to a different device.")
    if result.get("manifest_id", bundle.manifest_id) != bundle.manifest_id:
        raise ValueError("Deployment results belong to a different manifest.")
    flags = {name: result.get(name) if type(result.get(name)) is bool else None for name in RESULT_FLAGS}
    return _write_record(project, {
        "kind": "deployment", "project": project.name,
        "device_id": project.device_id, "address": str(project.address),
        "manifest_id": _manifest_id(bundle.manifest_id),
        "source_snapshot": "releases/" + bundle.manifest_id,
        "file_count": len(bundle.files), "stage": stage, "exit_code": exit_code,
        **flags, "application_validation": "not_checked",
    })


def record_restart(project, operation_id, result, exit_code, stage):
    """Keep restart diagnostics and outcomes separate from deployment and acceptance."""
    if stage not in STAGES or (exit_code is not None and type(exit_code) is not int):
        raise ValueError("Invalid restart record stage or exit code.")
    if result.get("device_id") != project.device_id:
        raise ValueError("Restart results belong to a different device.")
    return _write_record(project, {
        "kind": "restart", "project": project.name, "operation_id": operation_id,
        "address": project.address, "stage": stage, "exit_code": exit_code, **result,
    })


def record_acceptance(project, manifest_id, observation):
    """Save a developer's observation for a confirmed, locally retained release."""
    if not isinstance(observation, str) or not observation.strip():
        raise ValueError("Describe the developer's actual observation before recording acceptance.")
    _snapshot(project, manifest_id)
    confirmed = False
    committed = False
    recovered = False
    # Record filenames begin with UTC timestamps. A later failed or unfinished
    # restart must not reuse an earlier recovery's successful login.
    for path in sorted(_directory(project, "records").glob("*.json")):
        if path.is_symlink():
            continue
        try:
            with path.open(encoding="utf-8") as stream:
                record = json.load(stream)
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(record, dict) or (
            record.get("schema_version") != 1 or record.get("device_id") != project.device_id
        ):
            continue
        if record.get("kind") == "deployment":
            recovered = False
            if record.get("manifest_id") == manifest_id:
                committed = committed or record.get("committed") is True
                confirmed = confirmed or (
                    record.get("exit_code") == 0
                    and all(record.get(name) is True for name in RESULT_FLAGS)
                )
        elif record.get("kind") == "restart":
            recovered = committed and (
                record.get("manifest_id") == manifest_id
                and record.get("stage") == "complete"
                and record.get("exit_code") == 0
                and all(record.get(name) is True for name in (
                    "restart_requested", "restart_acknowledged", "webrepl_authenticated"
                ))
            )
    if not (confirmed or recovered):
        raise ValueError("No confirmed successful deployment or restart recovery matches this device and manifest.")
    # Feedback is user supplied; avoid retaining an accidentally included password.
    feedback = observation.strip().replace(project.password, "[redacted]") if project.password else observation.strip()
    return _write_record(project, {
        "kind": "acceptance", "project": project.name,
        "device_id": project.device_id, "manifest_id": manifest_id,
        "source_snapshot": "releases/" + manifest_id,
        "application_validation": "developer_confirmed", "observation": feedback,
    })
