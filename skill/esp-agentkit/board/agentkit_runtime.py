"""Load a committed application and retain diagnostics for the current boot."""

import binascii
import errno
import hashlib
import io
import json
import os
import sys

VERSION = 1
APP_DIR = "/agentkit_app"
MANIFEST = "/.agentkit-manifest.json"
PENDING = "/.agentkit-deploying"
_boot_error = None


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError as exc:
        if exc.args[0] == errno.ENOENT:
            return False
        raise


def _installed_id():
    with open(MANIFEST) as stream:
        manifest = json.load(stream)
    files = manifest["files"]
    if manifest["entry"] != "app.py" or not isinstance(files, dict) or "app.py" not in files:
        raise ValueError
    digest = hashlib.sha256()
    for path in sorted(files):
        checksum = files[path]
        if not isinstance(checksum, str) or len(checksum) != 64 or any(
            c not in "0123456789abcdef" for c in checksum
        ):
            raise ValueError
        if any(not part or part.startswith(".") for part in path.split("/")) or any(
            c == "\\" or ord(c) < 32 or ord(c) == 127 for c in path
        ):
            raise ValueError
        if os.stat(APP_DIR + "/" + path)[0] & 0xf000 != 0x8000:
            raise ValueError
        # The installer verifies file bytes before committing these ordered records.
        digest.update(path.encode() + b"\x00" + checksum.encode() + b"\n")
    identifier = binascii.hexlify(digest.digest()).decode()
    if manifest["id"] != identifier:
        raise ValueError
    return identifier


def status():
    identifier = None
    try:
        if _exists(PENDING):
            state = "deploying"
        elif _exists(MANIFEST):
            identifier = _installed_id()
            state = "installed"
        else:
            state = "invalid" if _exists(APP_DIR) else "empty"
    except (OSError, ValueError, KeyError, TypeError, AttributeError, MemoryError):
        state = "invalid"
    return {"state": state, "manifest_id": identifier, "boot_error": _boot_error}


class _TraceTail(io.IOBase):
    """Keep the last 2 KiB, including the exception message, as it is printed."""

    def __init__(self):
        self.data = bytearray()
        self.truncated = False

    def write(self, chunk):
        excess = len(self.data) + len(chunk) - 2048
        if excess > 0:
            self.truncated = True
            self.data = self.data[excess:]
        self.data.extend(chunk[-2048:])
        return len(chunk)


def start(namespace):
    global _boot_error
    deployment = status()
    if deployment["state"] != "installed":
        return
    path = APP_DIR + "/app.py"
    original_directory = os.getcwd()
    original_path = sys.path
    try:
        os.chdir(APP_DIR)
        sys.path = [APP_DIR] + original_path
        namespace["__file__"] = path
        namespace["__name__"] = "__main__"
        if sys.platform == "esp8266":
            # This port omits execfile(). Close the file before running the app.
            with open(path, "rb") as stream:
                source = stream.read()
            exec(source, namespace)
        else:
            execfile(path, namespace)
    except Exception as exc:
        # KeyboardInterrupt propagates to the REPL when an operator stops the app.
        writer = _TraceTail()
        sys.print_exception(exc, writer)
        prefix = "...\n" if writer.truncated else ""
        _boot_error = {
            "manifest_id": deployment["manifest_id"],
            "traceback": prefix + bytes(writer.data).decode("utf-8", "replace"),
        }
    finally:
        sys.path = original_path
        os.chdir(original_directory)
