"""Install a complete application or a single trial file over an mpremote filesystem session."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat


APP = "/agentkit_app"
TRIALS = "/agentkit_trials"
MARKER = "/.agentkit-deploying"
MANIFEST = "/.agentkit-manifest.json"
STAGING = "/.agentkit-upload.tmp"
ENTRY = "app.py"


class DeploymentError(RuntimeError):
    """The board or the release did not satisfy an installation check."""


@dataclass(frozen=True)
class Asset:
    path: Path
    size: int
    digest: str


@dataclass(frozen=True)
class Bundle:
    files: dict[str, Asset]
    manifest_id: str
    manifest: bytes


def _asset(path):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"Expected a regular file, found {path}.")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return Asset(path, metadata.st_size, digest)


def _check_name(name):
    if (
        not name or name.startswith(".") or "/" in name or "\\" in name
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
        or len(name.encode("utf-8")) > 255
    ):
        raise ValueError(f"Unsupported application filename: {name!r}.")


def prepare_put(path):
    _check_name(path.name)
    return path.name, _asset(path)


def prepare_deploy(root):
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError(f"Select a dedicated application directory containing {ENTRY}.")
    files = {}
    directories = [root]
    while directories:
        for child in directories.pop().iterdir():
            if child.name.startswith(".") or child.name == "__pycache__":
                continue
            _check_name(child.name)
            if stat.S_ISDIR(child.lstat().st_mode):
                directories.append(child)
            else:
                files[child.relative_to(root).as_posix()] = _asset(child)
    if ENTRY not in files:
        raise ValueError(f"The application directory must contain {ENTRY}.")
    digests = {name: files[name].digest for name in sorted(files)}
    checksum = hashlib.sha256()
    for name, digest in digests.items():
        checksum.update(name.encode("utf-8") + b"\0" + digest.encode("ascii") + b"\n")
    manifest_id = checksum.hexdigest()
    manifest = json.dumps(
        {"id": manifest_id, "entry": ENTRY, "files": digests}, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return Bundle(files, manifest_id, manifest)


def _identify(board, expected):
    board.exec("import os, machine, binascii, vfs")
    device_id = board.eval("binascii.hexlify(machine.unique_id()).decode()")
    if device_id != expected.lower():
        raise DeploymentError(f"Device ID {device_id} does not match {expected}. Check the board address.")
    return device_id


def _stat(board, path):
    try:
        return board.fs_stat(path)
    except FileNotFoundError:
        return None


def _managed_file_size(board, path):
    metadata = _stat(board, path)
    if metadata is None:
        return 0
    if not stat.S_ISREG(metadata.st_mode):
        raise DeploymentError(f"Expected a file at {path}. Inspect it over USB before retrying.")
    return metadata.st_size


def _inventory(board):
    files, dirs = {}, set()
    root = _stat(board, APP)
    pending = [""] if root is not None else []
    if root is not None and not stat.S_ISDIR(root.st_mode):
        raise DeploymentError(f"Expected a directory at {APP}. Back up the conflicting path.")
    while pending:
        parent = pending.pop()
        for entry in board.fs_listdir(APP + "/" + parent):
            if entry.name in ("", ".", "..") or "/" in entry.name:
                raise DeploymentError("Invalid directory entry on the board. Inspect its filesystem.")
            name = (parent + "/" if parent else "") + entry.name
            if stat.S_ISDIR(entry.st_mode):
                dirs.add(name)
                pending.append(name)
            elif stat.S_ISREG(entry.st_mode):
                # Some MicroPython filesystems omit sizes in ilistdir.
                files[name] = entry.st_size or board.fs_stat(APP + "/" + name).st_size
            else:
                raise DeploymentError(f"Unexpected file type inside {APP}: {name!r}.")
    return files, dirs, root is not None


def _available_bytes(board, reclaimed_bytes, new_dirs, removed_dirs=0):
    usage, littlefs = board.eval(
        "(tuple(os.statvfs('/')), any(type(fs) is getattr(vfs, 'VfsLfs2', None) "
        "for fs, mount in vfs.mount() if mount == '/'))"
    )
    # On a nearly full littlefs2 volume, statvfs can underflow when counting
    # directory metadata. Only credit free blocks within the volume's size.
    free_blocks = usage[4] if 0 <= usage[4] <= usage[2] else 0
    available = usage[1] * free_blocks + reclaimed_bytes
    if littlefs:
        # Each littlefs2 directory owns a pair of metadata blocks. Old
        # directories are removed before we allocate their replacements.
        directory_bytes = 2 * usage[0]
        available += removed_dirs * directory_bytes
        _check_space(available, new_dirs * directory_bytes)
        available -= new_dirs * directory_bytes
    return available


def _check_space(available, needed):
    if needed > available:
        raise DeploymentError(
            f"The update needs about {needed} bytes for this step with {available} available. "
            "Reduce the upload or free board storage and retry."
        )


def _clear_staging(board):
    try:
        board.fs_rmfile(STAGING)
    except FileNotFoundError:
        pass


def _replace(board, path, data, digest):
    board.fs_writefile(STAGING, data)
    if board.fs_hashfile(STAGING, "sha256").hex() != digest:
        raise DeploymentError(f"Upload digest mismatch for {path}. Retry the complete operation.")
    board.exec(f"os.rename({STAGING!r}, {path!r})")


def _upload(board, destination, asset):
    try:
        data = asset.path.read_bytes()
    except OSError as exc:
        raise DeploymentError(f"Could not read the local source {asset.path}. Restore it and retry.") from exc
    if hashlib.sha256(data).hexdigest() != asset.digest:
        raise DeploymentError(f"Local file changed during upload: {asset.path}. Retry with the current source.")
    _replace(board, destination, data, asset.digest)


def put(board, prepared, expected_device_id):
    name, asset = prepared
    device_id = _identify(board, expected_device_id)
    destination = TRIALS + "/" + name
    directory = _stat(board, TRIALS)
    if directory is not None and not stat.S_ISDIR(directory.st_mode):
        raise DeploymentError(f"Expected a directory at {TRIALS}. Back up the conflicting path.")
    if directory is not None:
        _managed_file_size(board, destination)
    available = _available_bytes(board, _managed_file_size(board, STAGING), int(directory is None))
    _check_space(available, asset.size)
    _clear_staging(board)
    if directory is None:
        board.fs_mkdir(TRIALS)
    _upload(board, destination, asset)
    return {"device_id": device_id, "path": destination, "sha256": asset.digest}


def _check_launcher(board):
    launcher = (Path(__file__).resolve().parent.parent / "board" / "main.py").read_bytes().replace(b"\r\n", b"\n")
    hashes = {hashlib.sha256(data).digest() for data in (launcher, launcher.replace(b"\n", b"\r\n"))}
    if _stat(board, "/main.py") is None or board.fs_hashfile("/main.py", "sha256") not in hashes:
        raise DeploymentError(
            "The installed launcher differs from this Skill. Run provision.py --port PORT --keep-config over USB."
        )
    board.exec("try:\n import agentkit_runtime as _ak_runtime\nexcept ImportError:\n _ak_runtime = None")
    if board.eval("getattr(_ak_runtime, 'VERSION', None)") != 1:
        raise DeploymentError(
            "The deployment runtime is missing or incompatible. Run provision.py --port PORT --keep-config over USB."
        )


def deploy(board, bundle, expected_device_id, *, progress=None):
    def report(stage, **fields):
        if progress is not None:
            progress({"stage": stage, **fields})

    report("identify")
    device_id = _identify(board, expected_device_id)
    report("prepare")
    _check_launcher(board)
    files, dirs, app_exists = _inventory(board)
    marker = _stat(board, MARKER)
    if marker is not None and not stat.S_ISREG(marker.st_mode):
        raise DeploymentError(f"Expected a marker file at {MARKER}. Inspect it over USB.")

    required_dirs = {
        str(parent) for name in bundle.files for parent in PurePosixPath(name).parents if parent.parts
    }
    stale_files = files.keys() - bundle.files.keys()
    new_dirs = required_dirs - dirs
    stale_dirs = dirs - required_dirs
    staging_size = _managed_file_size(board, STAGING)
    _managed_file_size(board, MANIFEST)
    changed = []
    for name in sorted(bundle.files, key=lambda path: (path == ENTRY, path)):
        asset = bundle.files[name]
        if files.get(name) == asset.size and board.fs_hashfile(APP + "/" + name, "sha256").hex() == asset.digest:
            continue
        changed.append(name)
    result = {
        "device_id": device_id, "manifest_id": bundle.manifest_id, "entry": ENTRY,
        "file_count": len(bundle.files), "committed": True,
    }
    if marker is None and not changed and not stale_files and dirs == required_dirs:
        status = board.eval("_ak_runtime.status()")
        if status["state"] == "installed" and status["manifest_id"] == bundle.manifest_id:
            return result
    reclaimed_bytes = staging_size + sum(files[name] for name in stale_files)
    available = _available_bytes(board, reclaimed_bytes, len(new_dirs) + int(not app_exists), len(stale_dirs))
    for name in changed:
        size = bundle.files[name].size
        _check_space(available, size)
        available += files.get(name, 0) - size
    _check_space(available, len(bundle.manifest))

    # Reclaim a previous partial upload before allocating the marker.
    _clear_staging(board)
    # Once this marker exists, boot always leaves the management REPL available.
    if marker is None:
        board.fs_touchfile(MARKER)
    board.fs_stat(MARKER)
    for name in sorted(stale_files):
        board.fs_rmfile(APP + "/" + name)
    for name in sorted(stale_dirs, key=lambda path: path.count("/"), reverse=True):
        board.fs_rmdir(APP + "/" + name)
    if not app_exists:
        board.fs_mkdir(APP)
    for name in sorted(new_dirs, key=lambda path: path.count("/")):
        board.fs_mkdir(APP + "/" + name)
    transferred = 0
    total = sum(bundle.files[name].size for name in changed)
    for name in changed:
        report("upload", file=name, completed_bytes=transferred, total_bytes=total)
        _upload(board, APP + "/" + name, bundle.files[name])
        transferred += bundle.files[name].size
        report("upload", file=name, completed_bytes=transferred, total_bytes=total, verified=True)
    report("commit")
    _replace(board, MANIFEST, bundle.manifest, hashlib.sha256(bundle.manifest).hexdigest())
    board.fs_rmfile(MARKER)
    status = board.eval("_ak_runtime.status()")
    if status["state"] != "installed" or status["manifest_id"] != bundle.manifest_id:
        raise DeploymentError("The committed manifest was not confirmed. Run info, then repeat the complete deploy.")
    return result
