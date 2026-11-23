"""Coordinate local operations and release managed child processes on cancellation."""

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import getpass
import hashlib
from ipaddress import ip_address
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import time


class BusyError(RuntimeError):
    """A cooperating local process already holds this resource."""

    def __init__(self, owner):
        self.owner = owner
        description = f"PID {owner['pid']}" if isinstance(owner.get("pid"), int) else "another process"
        if isinstance(owner.get("operation"), str):
            description += f" ({owner['operation']})"
        super().__init__(f"Resource is in use by {description}. Stop that operation and retry.")


def _label(value, name):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{name} must be nonempty text without control characters.")
    return value.strip()


def serial_key(port):
    """Normalize an endpoint, including macOS callout/TTY and Windows COM aliases."""
    port = _label(port, "Serial port")
    if "://" in port:
        raise ValueError("Use a local serial endpoint, not a URL.")
    windows = port.removeprefix("\\\\.\\")
    if re.fullmatch(r"COM\d+", windows, re.IGNORECASE):
        return "serial:" + windows.upper()
    resolved = os.path.realpath(port)
    if resolved.startswith("/dev/tty."):
        resolved = "/dev/cu." + resolved[len("/dev/tty."):]
    return "serial:" + os.path.normcase(resolved)


def network_key(address):
    """Normalize a host endpoint without treating it as a stable device identity."""
    address = _label(str(address), "Network address")
    try:
        normalized = str(ip_address(address))
    except ValueError:
        normalized = address.rstrip(".").lower()
        if not re.fullmatch(r"[a-z0-9.-]+", normalized):
            raise ValueError("Use a host address without credentials or a URL.") from None
    return "network:" + normalized


def identity_key(namespace, value):
    """Only callers with a verified identity may add this lock alongside an endpoint."""
    namespace = _label(namespace, "Identity namespace").lower()
    if not re.fullmatch(r"[a-z0-9._-]+", namespace):
        raise ValueError("Use an explicit identity namespace.")
    return "identity:" + namespace + ":" + _label(value, "Device identity").lower()


def _lock_directory():
    user = str(os.getuid()) if hasattr(os, "getuid") else hashlib.sha256(getpass.getuser().encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / ("esp-agentkit-locks-" + user)


def _try_lock(descriptor):
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            return False
        raise


def _owner(descriptor):
    try:
        os.lseek(descriptor, 1, os.SEEK_SET)
        owner = json.loads(os.read(descriptor, 4096))
        if isinstance(owner, dict):
            return owner
    except (OSError, ValueError):
        pass
    return {}


@contextmanager
def resource_lock(keys, operation, *, lock_dir=None):
    """Hold sorted OS locks across projects; refuse busy resources immediately.

    These locks coordinate this user's cooperating processes on one machine.
    Files persist so contenders share one inode.
    They do not reserve hardware against unrelated tools or other computers.
    Identity aliases must be established by the caller, not inferred here.
    """
    operation = _label(operation, "Operation")
    if len(operation) > 200:
        raise ValueError("Use a short operation name without credentials.")
    if isinstance(keys, str):
        raise ValueError("Provide a sequence of resource keys.")
    keys = sorted({_label(key, "Resource key") for key in keys})
    if not keys:
        raise ValueError("Provide at least one resource key.")
    directory = _lock_directory() if lock_dir is None else Path(lock_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("The lock directory must be a regular local directory.")
    held = []
    try:
        for key in keys:
            path = directory / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".lock")
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            acquired = False
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError("Expected a regular resource lock file.")
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                if not _try_lock(descriptor):
                    raise BusyError(_owner(descriptor))
                acquired = True
                held.append(descriptor)
                payload = json.dumps({
                    "pid": os.getpid(), "operation": operation,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }).encode("utf-8")
                os.lseek(descriptor, 1, os.SEEK_SET)
                os.write(descriptor, payload)
                os.ftruncate(descriptor, len(payload) + 1)
            finally:
                if not acquired:
                    os.close(descriptor)
        yield
    finally:
        for descriptor in reversed(held):
            # Closing the descriptor releases the kernel lock even on errors.
            os.close(descriptor)


def _stop_process_tree(process):
    """Signal only our process group, then force remaining children to exit."""
    try:
        previous_interrupt = signal.signal(signal.SIGINT, signal.SIG_IGN)
    except ValueError:
        # Only the main thread may install a signal handler.
        previous_interrupt = None
    try:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        # The leader can exit while a child still owns the serial port or pipe.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        # SIGKILL is asynchronous; wait() only reaps the group leader.
        deadline = time.monotonic() + 2
        while True:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Could not confirm cleanup of process group {process.pid}.")
            time.sleep(0.01)
    finally:
        if previous_interrupt is not None:
            signal.signal(signal.SIGINT, previous_interrupt)


def run_process(argv, cwd, env=None, timeout=None, stdout=None, stderr=None):
    """Run an argument vector with streamed output and bounded tree cleanup.

    Nonzero exit codes are returned for the caller to interpret. TimeoutExpired
    and KeyboardInterrupt are re-raised after terminating the process group.
    PIPE streams are drained by communicate and returned as bytes. Windows
    execution is deferred until process-tree cleanup has native validation;
    the existing MicroPython entry points do not depend on this runner.
    """
    if isinstance(argv, (str, bytes)) or not argv:
        raise ValueError("Provide a nonempty argument vector, not a shell command.")
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("Process timeout must be finite and greater than zero.")
    if os.name == "nt":
        raise NotImplementedError("Managed native tool execution on Windows is pending process-tree cleanup validation.")
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
    try:
        output, errors = process.communicate(timeout=timeout)
    except BaseException:
        _stop_process_tree(process)
        # Drain buffered output after descendants have released inherited pipes.
        process.communicate(timeout=2)
        raise
    else:
        _stop_process_tree(process)
        return subprocess.CompletedProcess(argv, process.returncode, output, errors)
