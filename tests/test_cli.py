"""The CLI's public exit codes and output streams."""

from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from support import run_cli
from webrepl_session import BoardError, LoginError, SessionError, WebReplSession


class CliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.probe = Path(directory.name) / "probe.py"
        self.probe.write_text("print(42)\n", encoding="utf-8")
        self.args = [
            "run", "--address", "127.0.0.1", "--password-stdin", str(self.probe)
        ]

    def test_probe_results_and_exit_codes(self):
        traceback = b"Traceback (most recent call last):\nValueError: probe marker\n"
        cases = (
            ((b"42\n", b"", None), 0),
            ((b"42\n", traceback, None), 4),
            ((b"42\n", b"", "Script execution finished. Connection lost."), 5),
            ((b"42\n", traceback, "Script execution finished. Connection lost."), 5),
            (SessionError("Probe timed out."), 5),
        )
        for result, expected in cases:
            with self.subTest(result=result):
                session = Mock(spec=WebReplSession)
                if isinstance(result, Exception):
                    session.execute.side_effect = result
                else:
                    session.execute.return_value = result
                connect = Mock(return_value=nullcontext(session))
                process = run_cli(self.args, connect)
                self.assertEqual(process.returncode, expected, process.stderr)
                connect.assert_called_once_with("127.0.0.1", "testpass")
                session.execute.assert_called_once_with(b"print(42)\n", timeout=10)
                if isinstance(result, Exception):
                    self.assertEqual(process.stdout, "")
                    self.assertIn("Probe timed out", process.stderr)
                else:
                    self.assertEqual(process.stdout, result[0].decode())
                    diagnostics = result[1].decode()
                    if result[2]:
                        diagnostics += f"run: {result[2]}\n"
                    self.assertEqual(process.stderr.splitlines()[1:], diagnostics.splitlines())

    def test_local_errors_exit_before_connecting(self):
        cases = (
            ([*self.args, "--timeout", "0"], "greater than zero"),
            ([*self.args[:-1], str(self.probe.with_name("missing.py"))], "missing.py"),
        )
        for args, message in cases:
            with self.subTest(args=args):
                connect = Mock()
                process = run_cli(args, connect)
                self.assertEqual(process.returncode, 2)
                self.assertEqual(process.stdout, "")
                self.assertIn(message, process.stderr)
                connect.assert_not_called()

    def test_login_failure_and_local_cancellation(self):
        for error, code, message in (
            (LoginError("WebREPL rejected the password."), 3, "rejected the password"),
            (KeyboardInterrupt(), 130, "Local command cancelled"),
        ):
            with self.subTest(code=code):
                process = run_cli(self.args, Mock(side_effect=error))
                self.assertEqual(process.returncode, code)
                self.assertEqual(process.stdout, "")
                self.assertIn(message, process.stderr)

    def test_stop_confirms_the_prompt(self):
        session = Mock(spec=WebReplSession)
        process = run_cli(
            ["stop", "--address", "127.0.0.1", "--password-stdin"],
            Mock(return_value=nullcontext(session)),
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), {
            "address": "127.0.0.1", "repl_ready": True
        })
        session.stop.assert_called_once_with()

    def test_board_storage_failure_returns_board_error_exit_code(self):
        session = Mock(spec=WebReplSession)
        message = "Board storage is full. Reduce the upload or free board storage, then retry."
        session.filesystem.side_effect = BoardError(message)
        process = run_cli(
            ["put", "--address", "127.0.0.1", "--password-stdin",
             "--device-id", "01020304", str(self.probe)],
            Mock(return_value=nullcontext(session)),
        )
        self.assertEqual(process.returncode, 4)
        self.assertEqual(process.stdout, "")
        self.assertIn(message, process.stderr)
