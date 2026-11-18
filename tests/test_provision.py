"""Provisioning, local save recovery, and refusal of unverified project targets."""

from contextlib import nullcontext, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from support import SKILL
from mpremote.transport_serial import SerialTransport
import project_setup
import provision
from projects import load_project


class ProvisionProjectTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "screen"
        self.root.mkdir()
        (self.root / "firmware.bin").write_bytes(b"retained image")
        self.identity = {
            "board": "NodeMCU V3 / ESP-12F", "firmware": "MicroPython v1.29.0 ESP8266_GENERIC",
            "device_id": "01020304", "flash_mib": 4,
        }
        self.credentials = {"ssid": "test-network", "wifi_password": "wifi-secret", "webrepl_password": "testpass"}
        self.serial = Mock()
        self.serial.return_value.fs_listdir.return_value = [SimpleNamespace(name="agentkit_config.json")]
        self.read_password = Mock(return_value="testpass")
        self.session = Mock()
        self.session.filesystem.return_value = nullcontext(Mock())
        self.connect = Mock(return_value=nullcontext(self.session))
        self.setup = ["--port", "TEST_PORT", "--project-dir", str(self.root)]
        self.recover = ["--save-project-only", "--project-dir", str(self.root),
                        "--address", "192.0.2.10", "--device-id", "01020304", "--password-stdin"]

    def invoke(self, arguments, *, exit_code=0, identify=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("sys.argv", [str(SKILL / "scripts" / "provision.py"), *arguments]),
            patch("mpremote.transport_serial.SerialTransport", self.serial),
            patch("webrepl_session.open_session", self.connect),
            patch.multiple(
                provision, read_credentials=Mock(return_value=self.credentials),
                read_webrepl_password=self.read_password,
                identify_board=identify or Mock(return_value=self.identity), startup_updates=Mock(return_value={}),
                install_file=Mock(), await_boot=Mock(return_value="192.0.2.10"),
            ),
            redirect_stdout(stdout), redirect_stderr(stderr),
        ):
            try:
                provision.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        self.assertEqual(code, exit_code, stderr.getvalue())
        self.assert_private(stdout.getvalue() + stderr.getvalue())
        return json.loads(stdout.getvalue()) if stdout.getvalue() else {}

    def assert_private(self, public):
        for secret in self.credentials.values():
            self.assertNotIn(secret, public)

    def test_setup_then_create_project_without_overwriting_existing_files(self):
        # The original setup-only entry remains usable before creating a project.
        record = self.invoke(["--port", "TEST_PORT"])
        self.assertTrue(record["webrepl_authenticated"])
        self.assertFalse((self.root / "project.json").exists())
        result = self.invoke([*self.setup, "--keep-config", "--password-stdin"])
        self.read_password.assert_called_once_with(from_stdin=True)
        self.assertTrue(result["project_created"])
        project = load_project(result["project_path"])
        self.assertEqual((project.device_id, project.address, project.password),
                         ("01020304", "192.0.2.10", "testpass"))
        self.assertEqual((self.root / "firmware.bin").read_bytes(), b"retained image")
        self.assert_private((self.root / "README.md").read_text() + (project.app / "app.py").read_text())
        self.assertNotIn("wifi-secret", project.path.read_text())

        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.serial.reset_mock()
        self.connect.reset_mock()
        self.invoke(self.setup, exit_code=1)
        self.serial.assert_not_called()
        self.connect.assert_not_called()
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_local_save_failure_can_be_retried_without_reconfiguring_usb(self):
        real_open = project_setup.os.open

        def fail_configuration_file(path, *args, **kwargs):
            if Path(path).name == "project.json":
                raise OSError("testpass wifi-secret")
            return real_open(path, *args, **kwargs)

        with patch("project_setup.os.open", side_effect=fail_configuration_file):
            result = self.invoke(self.setup, exit_code=1)
        self.assertTrue(result["webrepl_authenticated"])
        self.assertFalse(result["project_created"])
        self.assertEqual(set(path.name for path in self.root.iterdir()), {"firmware.bin"})
        self.serial.reset_mock()
        result = self.invoke(self.recover)
        self.serial.assert_not_called()
        self.read_password.assert_called_once_with(from_stdin=True)
        self.assertTrue(result["project_created"])
        self.assertFalse(result["configuration_changed"])
        self.assertTrue(result["application_interrupted"])
        self.assertEqual(load_project(self.root).device_id, "01020304")
        self.assertEqual((self.root / "firmware.bin").read_bytes(), b"retained image")

    def test_unverified_wireless_target_cannot_create_a_project(self):
        self.identity["device_id"] = "deadbeef"
        self.invoke(self.recover, exit_code=1)
        self.assertFalse((self.root / "project.json").exists())

        # Run mpremote's real parser: asynchronous log text must not escape via SyntaxError.
        board = Mock()
        board.exec.return_value = b"Wi-Fi password: wifi-secret\n('micropython', (1, 29, 0))\n"
        board.eval = lambda expression: SerialTransport.eval(board, expression)
        self.session.filesystem.return_value = nullcontext(board)
        self.invoke(self.recover, exit_code=1, identify=provision.identify_board)
        self.assertFalse((self.root / "project.json").exists())
        self.serial.assert_not_called()
