"""Exercise cross-process exclusion and cleanup of cancelled tool descendants."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from support import SKILL
from operations import BusyError, resource_lock, run_process, serial_key


class OperationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.locks = self.root / "locks"
        self.environment = {**os.environ, "PYTHONPATH": str(SKILL / "scripts")}
        self.processes = []
        self.addCleanup(self.stop_processes)

    def stop_processes(self):
        for process in reversed(self.processes):
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def spawn(self, source):
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", source], cwd=self.root,
            env=self.environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.processes.append(process)
        return process

    def wait_file(self, path, timeout=5):
        deadline = time.monotonic() + timeout
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(path.exists(), f"Child process did not create {path.name}.")

    def test_shared_endpoint_excludes_other_process_and_recovers_after_exit(self):
        if os.name == "nt":
            port, alias, another = "COM7", r"\\.\com7", "COM8"
        else:
            port, alias, another = "/dev/cu.agentkit-test", "/dev/tty.agentkit-test", "/dev/cu.another-test"
        owner = self.spawn(
            "import sys\nfrom operations import resource_lock, serial_key\n"
            f"with resource_lock([serial_key({port!r})], 'monitor', lock_dir={str(self.locks)!r}):\n"
            " print('locked', flush=True)\n sys.stdin.read()\n"
        )
        self.assertEqual(owner.stdout.readline(), b"locked\n")
        with self.assertRaises(BusyError) as blocked:
            with resource_lock([serial_key(alias)], "flash", lock_dir=self.locks):
                self.fail("A second process acquired the same serial endpoint.")
        self.assertEqual(blocked.exception.owner["pid"], owner.pid)
        self.assertIn("monitor", str(blocked.exception))
        with resource_lock([serial_key(another)], "flash", lock_dir=self.locks):
            pass
        # Abrupt process exit must release the OS lock without deleting the file.
        owner.kill()
        owner.communicate(timeout=5)
        with resource_lock([serial_key(port)], "retry", lock_dir=self.locks):
            pass

    def descendant_command(self):
        ready = self.root / "descendant-ready"
        source = (
            "import signal,time\nfrom pathlib import Path\n"
            "from operations import resource_lock\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"with resource_lock(['descendant'], 'child', lock_dir={str(self.locks)!r}):\n"
            f" Path({str(ready)!r}).write_text('ready')\n"
            " while True: time.sleep(0.1)\n"
        )
        tool = self.root / "tool.py"
        tool.write_text(
            "import subprocess,sys,time\n"
            f"child = subprocess.Popen([sys.executable, '-c', {source!r}])\n"
            "while True: time.sleep(0.1)\n",
            encoding="utf-8",
        )
        return [sys.executable, str(tool)], ready

    @unittest.skipIf(os.name == "nt", "Native Windows tool execution remains pending validation.")
    def test_timeout_stops_descendants_before_releasing_operation(self):
        command, ready = self.descendant_command()
        with resource_lock(["tool"], "build", lock_dir=self.locks):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_process(
                    command, self.root, env=self.environment, timeout=1,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
        self.assertTrue(ready.exists(), "The descendant did not start before the timeout.")
        with resource_lock(["tool", "descendant"], "retry", lock_dir=self.locks):
            pass
        # A command that exits normally must not leave descendants holding a port.
        ready.unlink()
        tool = Path(command[1])
        source = tool.read_text(encoding="utf-8")
        tool.write_text(source.replace(
            "while True: time.sleep(0.1)\n",
            f"from pathlib import Path\nwhile not Path({str(ready)!r}).exists(): time.sleep(0.01)\n",
        ), encoding="utf-8")
        completed = run_process(command, self.root, env=self.environment, timeout=3)
        self.assertEqual(completed.returncode, 0)
        with resource_lock(["descendant"], "next operation", lock_dir=self.locks):
            pass

    @unittest.skipIf(os.name == "nt", "SIGINT process-group cancellation is a POSIX check.")
    def test_cancel_waits_for_tool_and_its_child_then_allows_retry(self):
        command, ready = self.descendant_command()
        wrapper = self.spawn(
            "import sys\nfrom operations import resource_lock, run_process\n"
            "try:\n"
            f" with resource_lock(['tool'], 'monitor', lock_dir={str(self.locks)!r}):\n"
            f"  run_process({command!r}, {str(self.root)!r})\n"
            "except KeyboardInterrupt:\n sys.exit(130)\n"
        )
        self.wait_file(ready)
        with self.assertRaises(BusyError):
            with resource_lock(["tool"], "flash", lock_dir=self.locks):
                pass
        wrapper.send_signal(signal.SIGINT)
        _, errors = wrapper.communicate(timeout=5)
        self.assertEqual(wrapper.returncode, 130, errors.decode())
        with resource_lock(["tool", "descendant"], "flash", lock_dir=self.locks):
            pass


if __name__ == "__main__":
    unittest.main()
