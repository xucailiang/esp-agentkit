"""Core foreground debugging results through the public CLI."""

from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from support import run_cli
from webrepl_session import WebReplSession


class CliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.probe = Path(directory.name) / "probe.py"
        self.probe.write_text("print(42)\n", encoding="utf-8")
        self.args = [
            "run", "--address", "127.0.0.1", "--password-stdin", str(self.probe)
        ]

    def test_stop_then_probe_preserves_output_and_python_errors(self):
        session = Mock(spec=WebReplSession)
        connect = Mock(return_value=nullcontext(session))
        stopped = run_cli(
            ["stop", "--address", "127.0.0.1", "--password-stdin"], connect,
        )
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertEqual(json.loads(stopped.stdout), {
            "address": "127.0.0.1", "repl_ready": True,
        })
        session.stop.assert_called_once_with()

        traceback = b"Traceback (most recent call last):\nValueError: probe marker\n"
        for diagnostic, code in ((b"", 0), (traceback, 4)):
            with self.subTest(exit_code=code):
                session.execute.return_value = b"42\n", diagnostic, None
                result = run_cli(self.args, connect)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(result.stdout, "42\n")
                self.assertEqual(result.stderr.splitlines()[1:], diagnostic.decode().splitlines())
                connect.assert_called_with("127.0.0.1", "testpass")
                session.execute.assert_called_with(b"print(42)\n", timeout=10)

    def test_lost_connection_preserves_output_without_claiming_success(self):
        session = Mock(spec=WebReplSession)
        session.execute.return_value = b"42\n", b"", "Script execution finished. Connection lost."
        result = run_cli(self.args, Mock(return_value=nullcontext(session)))
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertEqual(result.stdout, "42\n")
        self.assertIn("Connection lost", result.stderr)
