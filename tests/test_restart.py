"""Restart, refuse unsafe releases, and recover a deployment for explicit acceptance."""

from contextlib import chdir, contextmanager
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from support import SKILL, run_cli
from board_fixture import FileBoard
from deployment import MARKER, deploy, prepare_deploy
from mpremote.transport_serial import SerialTransport
from webrepl_session import LoginError, SessionError


class RestartBoard(FileBoard):
    def fs_readfile(self, path):
        return self.path(path).read_bytes()


class RestartTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.project = self.workspace / "display"
        self.app = self.project / "app"
        self.app.mkdir(parents=True)
        (self.app / "app.py").write_text("print('installed version')\n", encoding="utf-8")
        (self.project / "project.json").write_text(json.dumps({
            "name": "display", "app": "app",
            "device": {"device_id": "01020304", "address": "127.0.0.1",
                       "webrepl_password": "testpass"},
        }), encoding="utf-8")
        self.board = RestartBoard(self.workspace / "board")
        self.board.path("/main.py").write_bytes((SKILL / "board/main.py").read_bytes())
        self.bundle = prepare_deploy(self.app)
        deploy(self.board, self.bundle, "01020304")
        self.board.mutations.clear()
        self.command = ["restart", "--project", str(self.project)]

    def invoke(self, command=None, factory=None, code=0):
        with chdir(self.workspace):
            result = run_cli(command or self.command, factory or self.board.connect)
        self.assertEqual(result.returncode, code, result.stderr)
        return result

    def records(self):
        return [json.loads(path.read_text(encoding="utf-8"))
                for path in sorted((self.project / "records").glob("*.json"))]

    def test_restart_retains_diagnostics_without_deploying_working_source(self):
        (self.app / "app.py").unlink()  # Local source is no longer deployable.
        saved_password = 'pa\\s"s'
        config_path = self.project / "project.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["device"]["webrepl_password"] = saved_password
        config_path.write_text(json.dumps(config), encoding="utf-8")
        self.command.append("--password-stdin")
        self.board.runtime._boot_error = {
            "manifest_id": self.bundle.manifest_id,
            "traceback": "ValueError: testpass " + repr({"password": saved_password})
                         + " " + json.dumps({"password": saved_password}),
        }

        def restart():
            checkpoint = self.records()[0]
            self.assertIsNone(checkpoint["exit_code"])
            self.assertFalse(checkpoint["restart_requested"])
            self.assertIn("[redacted]", checkpoint["deployment_before_restart"]["boot_error"]["traceback"])
            self.board.runtime._boot_error = None

        with patch.object(self.board, "restart", side_effect=restart) as reset:
            result = self.invoke()
        reset.assert_called_once()
        response = json.loads(result.stdout)
        self.assertEqual(response["manifest_id"], self.bundle.manifest_id)
        self.assertTrue(response["restart_acknowledged"] and response["webrepl_authenticated"])
        self.assertEqual(response["application_validation"], "not_checked")
        self.assertEqual(self.board.mutations, [])
        self.assertFalse((self.project / "releases").exists())
        records = self.records()
        self.assertEqual(records[0]["operation_id"], records[-1]["operation_id"])
        self.assertNotIn("testpass", result.stdout + result.stderr + json.dumps(records))
        diagnostic = response["deployment_before_restart"]["boot_error"]["traceback"]
        for secret in (saved_password, repr(saved_password)[1:-1], json.dumps(saved_password)[1:-1]):
            self.assertNotIn(secret, diagnostic)

    def test_refused_release_can_restart_only_after_restoring_expected_files(self):
        command = ["restart", "--address", "127.0.0.1", "--password-stdin", "--device-id", "01020304"]
        entry, extra = self.board.path("/agentkit_app/app.py"), self.board.path("/agentkit_app/extra.py")
        original = entry.read_bytes()
        for label, change, expected in (
            ("wrong device", lambda: setattr(self.board, "device_id", "deadbeef"), None),
            ("pending install", lambda: self.board.path(MARKER).touch(), None),
            ("unexpected manifest", lambda: None, "f" * 64),
            ("changed bytes", lambda: entry.write_bytes(b"changed"), None),
            ("extra module", lambda: extra.write_bytes(b"print('extra')"), None),
        ):
            with self.subTest(label=label):
                self.board.device_id = "01020304"
                self.board.path(MARKER).unlink(missing_ok=True)
                extra.unlink(missing_ok=True)
                entry.write_bytes(original)
                change()
                result = self.invoke(command + (["--manifest", expected] if expected else []), code=5)
                self.assertFalse(json.loads(result.stdout)["restart_requested"])
        self.assertEqual(self.board.restarts, 0)
        self.assertEqual(self.board.mutations, [])
        extra.unlink()
        result = self.invoke(command + ["--manifest", self.bundle.manifest_id])
        before = next(json.loads(line) for line in result.stderr.splitlines() if line.startswith("{"))
        self.assertEqual(before["event"], "before_restart")
        self.assertFalse(before["restart_requested"])
        self.assertTrue(json.loads(result.stdout)["restart_acknowledged"])
        self.assertEqual(self.records(), [])  # Explicit mode needs no project records.

    def test_pre_reset_failures_keep_safe_parseable_output_and_do_not_reset(self):
        # Use mpremote's parser so a noisy reply cannot escape as a raw traceback.
        noisy = Mock()
        noisy.exec.return_value = b"Wi-Fi password: private-network-value\n'01020304'\n"
        with patch.object(self.board, "eval", side_effect=lambda expr: SerialTransport.eval(noisy, expr)):
            result = self.invoke(code=5)
        self.assertFalse(json.loads(result.stdout)["restart_requested"])
        self.assertNotIn("private-network-value", result.stdout + result.stderr + json.dumps(self.records()))

        @contextmanager
        def filesystem():
            try:
                yield self.board
            finally:
                raise SessionError("Connection failed while leaving raw REPL.")

        @contextmanager
        def disconnect_on_cleanup(address, password):
            yield SimpleNamespace(filesystem=filesystem)

        with patch("project_records.record_restart", side_effect=OSError("disk full")):
            result = self.invoke(factory=disconnect_on_cleanup, code=5)
        # Cleanup failure must not emit a second final JSON document.
        self.assertFalse(json.loads(result.stdout)["restart_requested"])
        self.assertEqual(self.board.restarts, 0)

    def test_uncertain_deployment_requires_restart_recovery_and_explicit_acceptance(self):
        acceptance = ["accept", "--project", str(self.project), "--manifest", self.bundle.manifest_id,
                      "--observation", "Developer confirmed the displayed behavior after recovery."]
        factory = Mock(side_effect=[self.board.connect("127.0.0.1", "testpass"), LoginError("offline")])
        with patch("time.monotonic", side_effect=[0, 0, 36]), patch("time.sleep"):
            deployed = self.invoke(["deploy", "--project", str(self.project)], factory, code=5)
        self.assertFalse(json.loads(deployed.stdout)["webrepl_authenticated"])
        self.invoke(acceptance, code=2)
        with patch.object(self.board, "restart", side_effect=KeyboardInterrupt):
            cancelled = self.invoke(code=130)
        response = json.loads(cancelled.stdout)
        self.assertTrue(response["restart_requested"])
        self.assertIsNone(response["restart_acknowledged"])
        self.assertIsNone(response["webrepl_authenticated"])
        factory = Mock(side_effect=[self.board.connect("127.0.0.1", "testpass"), KeyboardInterrupt()])
        cancelled = self.invoke(factory=factory, code=130)
        response = json.loads(cancelled.stdout)
        self.assertTrue(response["restart_acknowledged"])
        self.assertIsNone(response["webrepl_authenticated"])
        self.invoke(acceptance, code=2)

        self.invoke()
        self.assertEqual(self.records()[-1]["application_validation"], "not_checked")
        self.invoke(acceptance)
        self.assertEqual(self.records()[-1]["application_validation"], "developer_confirmed")
        self.invoke(self.command + ["--manifest", "f" * 64], code=5)
        self.invoke(acceptance, code=2)  # A later failure cannot borrow old success.
        for path in (self.project / "records").glob("*.json"):
            if json.loads(path.read_text(encoding="utf-8"))["kind"] == "deployment":
                path.unlink()
        self.invoke()
        self.invoke(acceptance, code=2)  # A restart alone does not establish deployment.
