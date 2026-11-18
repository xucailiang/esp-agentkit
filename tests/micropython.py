"""Run startup and littlefs2 regressions against a local Unix MicroPython build."""

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import select
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time
import unittest

from support import SKILL
from deployment import DeploymentError, deploy, prepare_deploy
from mpremote.transport_serial import SerialTransport


class PtyStream:
    def __init__(self, descriptor):
        self.descriptor = descriptor

    def write(self, data):
        return os.write(self.descriptor, data)

    def read(self, count):
        data = bytearray()
        deadline = time.monotonic() + 5
        while len(data) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.descriptor], [], [], remaining)[0]:
                raise TimeoutError("Unix MicroPython did not answer within five seconds.")
            chunk = os.read(self.descriptor, count - len(data))
            if not chunk:
                raise EOFError("Unix MicroPython exited during a request.")
            data.extend(chunk)
        return bytes(data)

    def inWaiting(self):
        return int(bool(select.select([self.descriptor], [], [], 0)[0]))


@contextmanager
def interpreter(executable):
    import pty
    import tty

    master, slave = pty.openpty()
    process = None
    try:
        tty.setraw(slave)
        process = subprocess.Popen(
            [executable, "-i", str(Path(__file__).parent / "fixtures/littlefs.py"), str(SKILL / "board")],
            stdin=slave, stdout=slave, stderr=slave,
        )
        os.close(slave)
        slave = None
        board = SerialTransport.__new__(SerialTransport)
        board.serial = PtyStream(master)
        board.in_raw_repl = False
        board.use_raw_paste = True
        board.is_pty = False
        ready = board.read_until(1, b"ESP_AGENTKIT_FIXTURE_READY", timeout_overall=5)
        if not ready.endswith(b"ESP_AGENTKIT_FIXTURE_READY"):
            raise RuntimeError("The littlefs2 fixture failed to initialize.\n" + ready.decode(errors="replace"))
        board.enter_raw_repl(soft_reset=False)
        yield board
    finally:
        os.close(master)
        if slave is not None:
            os.close(slave)
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def install_fixture(board, source, directory):
    app = directory / "app"
    app.mkdir()
    (app / "app.py").write_text(source, encoding="utf-8")
    bundle = prepare_deploy(app)
    board.fs_mkdir("/agentkit_app")
    board.fs_writefile("/agentkit_app/app.py", source.encode())
    board.fs_writefile("/.agentkit-manifest.json", bundle.manifest)
    board.fs_writefile("/main.py", (SKILL / "board/main.py").read_bytes())
    board.fs_writefile("/agentkit_config.json", b"fixture configuration")
    return bundle


def verify(executable):
    checks = unittest.TestCase()
    probe = subprocess.run(
        [executable, "-c", "import sys, vfs; assert sys.implementation.version[:3] == (1, 29, 0); assert hasattr(vfs, 'VfsLfs2')"],
        capture_output=True, text=True, timeout=5,
    )
    if probe.returncode:
        raise RuntimeError("Use Unix MicroPython v1.29.0 built with MICROPY_VFS_LFS2=1.\n" + probe.stderr)

    with TemporaryDirectory() as temporary, interpreter(executable) as board:
        bundle = install_fixture(board, "raise ImportError('required sensor driver')\n", Path(temporary))
        board.exec("exec(open('/main.py').read(), {})")
        status = board.eval("agentkit_runtime.status()")
        checks.assertEqual(status["state"], "installed", status)
        checks.assertEqual(status["boot_error"]["manifest_id"], bundle.manifest_id, status)
        checks.assertIn("ImportError: required sensor driver", status["boot_error"]["traceback"], status)
        print("PASS startup exception retains its manifest and traceback")

    with TemporaryDirectory() as temporary, interpreter(executable) as board:
        root = Path(temporary)
        original = install_fixture(board, "started = True\n", root)
        before = {
            path: bytes(board.fs_readfile(path))
            for path in ("/agentkit_app/app.py", "/.agentkit-manifest.json", "/agentkit_config.json")
        }
        update = root / "update"
        (update / "drivers").mkdir(parents=True)
        (update / "app.py").write_text("import drivers.sensor\n")
        (update / "drivers/sensor.py").write_text("value = 42\n")
        try:
            deploy(board, prepare_deploy(update), b"desktop".hex())
        except DeploymentError as exc:
            checks.assertIn("available", str(exc))
        else:
            raise AssertionError("The five-block volume accepted a new directory.")
        for path, content in before.items():
            checks.assertEqual(bytes(board.fs_readfile(path)), content, path)
        checks.assertFalse(board.fs_exists("/.agentkit-deploying"))
        board.exec("namespace = {}\nexec(open('/main.py').read(), namespace)")
        checks.assertIs(board.eval("namespace['started']"), True)
        checks.assertEqual(board.eval("agentkit_runtime.status()['manifest_id']"), original.manifest_id)
        print("PASS littlefs2 capacity refusal preserves the runnable application")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--micropython", required=True, help="Unix MicroPython v1.29.0 executable with littlefs2")
    args = parser.parse_args()
    executable = shutil.which(args.micropython)
    if executable is None:
        parser.error("The MicroPython executable was not found.")
    if os.name != "posix":
        parser.error("Run this PTY integration check on macOS, Linux or WSL.")
    verify(executable)


if __name__ == "__main__":
    main()
