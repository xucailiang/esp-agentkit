"""USB discovery, target selection and the detection CLI's public contract."""

from contextlib import redirect_stderr
import builtins
import io
import json
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

from serial.tools.list_ports_common import ListPortInfo

from support import SKILL
import detection


def serial_interface(port, usb=True, description="USB serial bridge"):
    return {
        "transport": "serial",
        "port": port,
        "description": description,
        "usb": {
            "vid": 0x1A86,
            "pid": 0x7523,
            "serial_number": "bridge-serial",
            "manufacturer": "Bridge vendor",
            "product": "USB serial",
            "location": "1-2",
        } if usb else None,
    }


def detected_chip(flash=True, reset=True):
    return {
        "chip": {
            "family": "ESP8266",
            "description": "ESP8266EX",
            "features": ["Wi-Fi"],
            "mac": "02:00:00:00:00:01",
        },
        "flash": {
            "id": "0x1640ef" if flash else None,
            "size_bytes": 4 * 1024 * 1024 if flash else None,
            "status": "detected" if flash else "unconfirmed",
        },
        "reset": {"status": "requested" if reset else "unconfirmed"},
        "warnings": [] if flash and reset else ["Flash or reset information is unconfirmed."],
    }


def worker_result(payload=None, code=0, stderr=""):
    if payload is None:
        payload = detected_chip()
    return subprocess.CompletedProcess(
        ["detector"], code, json.dumps(payload), stderr
    )


def run_detection_cli(args):
    """Run the actual entry without allowing wireless imports or credentials."""
    entry = str(SKILL / "scripts" / "agentkit.py")
    real_import = builtins.__import__

    def local_import(name, *import_args, **kwargs):
        if name in ("deployment", "webrepl_session"):
            raise AssertionError(f"Detection unexpectedly imports {name}.")
        return real_import(name, *import_args, **kwargs)

    with (
        io.TextIOWrapper(io.BytesIO(), encoding="utf-8", write_through=True) as stdout,
        io.TextIOWrapper(io.BytesIO(), encoding="utf-8", write_through=True) as stderr,
        patch.object(sys, "argv", [entry, *args]),
        patch.object(sys, "stdin", io.StringIO("")),
        patch.object(sys, "stdout", stdout),
        patch.object(sys, "stderr", stderr),
        patch("builtins.__import__", side_effect=local_import),
        patch("credentials.read_webrepl_password", side_effect=AssertionError("Password requested")),
    ):
        try:
            runpy.run_path(entry, run_name="__main__")
        except SystemExit as exc:
            code = exc.code
        else:
            raise AssertionError("The CLI entry did not exit.")
        return subprocess.CompletedProcess(
            args, code, stdout.buffer.getvalue().decode(), stderr.buffer.getvalue().decode()
        )


class DiscoveryTests(unittest.TestCase):
    def test_usb_metadata_is_inventory_not_a_chip_identity(self):
        bridge = ListPortInfo("/dev/cu.usbserial-test")
        bridge.description = "ESP32 USB serial adapter"
        bridge.vid = 0x1A86
        bridge.pid = 0x7523
        bridge.serial_number = "bridge-serial"
        bridge.manufacturer = "Bridge vendor"
        bridge.product = "USB serial"
        bridge.location = "1-2"
        bluetooth = ListPortInfo("/dev/cu.Bluetooth-Incoming-Port")
        with (
            patch("serial.tools.list_ports.comports", return_value=[bridge, bluetooth]),
            patch("subprocess.run") as worker,
        ):
            report, code = detection.run_detection(list_only=True)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "interfaces_only")
        self.assertIsNone(report["device"])
        self.assertEqual(report["reset"]["status"], "not_requested")
        interfaces = {item["port"]: item for item in report["interfaces"]}
        self.assertEqual(interfaces[bridge.device]["usb"]["vid"], 0x1A86)
        self.assertEqual(interfaces[bridge.device]["usb"]["pid"], 0x7523)
        self.assertIsNone(interfaces[bluetooth.device]["usb"])
        worker.assert_not_called()


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.port = "/dev/cu.usbserial-test"
        self.interface = serial_interface(self.port)
        self.discover = patch(
            "detection.discover_interfaces", return_value=[self.interface]
        ).start()
        self.addCleanup(patch.stopall)
        self.worker = patch("subprocess.run", return_value=worker_result()).start()
        self.diagnostics = io.StringIO()
        self.stderr = redirect_stderr(self.diagnostics)
        self.stderr.__enter__()
        self.addCleanup(self.stderr.__exit__, None, None, None)

    def test_one_usb_candidate_is_probed_with_a_bounded_worker(self):
        self.discover.return_value.append(serial_interface("/dev/ttyS0", usb=False))
        report, code = detection.run_detection(timeout=7.5)
        self.assertEqual(code, 0)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["status"], "detected")
        self.assertEqual(report["backend"], "esptool")
        self.assertEqual(report["device"]["port"], self.port)
        self.assertIsNone(report["device"]["board"])
        self.assertEqual(report["device"]["chip"]["family"], "ESP8266")
        self.assertEqual(report["device"]["flash"]["size_bytes"], 4 * 1024 * 1024)
        self.assertEqual(report["runtime"]["status"], "not_checked")
        self.assertEqual(report["capabilities"], {
            "firmware": "not_checked", "filesystem": "not_checked",
            "network": "not_checked", "deployment": "not_checked",
        })
        self.worker.assert_called_once()
        command = self.worker.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[-2:], ["--port", self.port])
        self.assertEqual(self.worker.call_args.kwargs["timeout"], 7.5)
        self.assertFalse(self.worker.call_args.kwargs.get("shell", False))

    def test_no_usb_candidate_does_not_probe_an_unrelated_serial_port(self):
        for interfaces in ([], [serial_interface("/dev/ttyS0", usb=False)]):
            with self.subTest(interfaces=interfaces):
                self.discover.return_value = interfaces
                report, code = detection.run_detection()
                self.assertEqual(code, 3)
                self.assertEqual(report["status"], "no_candidate")
                self.assertIsNone(report["device"])
                self.worker.assert_not_called()

    def test_multiple_usb_candidates_require_selection_before_any_probe(self):
        self.discover.return_value.append(serial_interface("/dev/cu.second"))
        report, code = detection.run_detection()
        self.assertEqual(code, 5)
        self.assertEqual(report["status"], "selection_required")
        self.assertEqual(len(report["interfaces"]), 2)
        self.assertIn("--port", report["message"])
        self.worker.assert_not_called()

    def test_explicit_port_can_select_an_interface_missing_usb_metadata(self):
        self.discover.return_value = []
        report, code = detection.run_detection(port="/dev/custom-port", backend="esptool")
        self.assertEqual(code, 0)
        self.assertEqual(report["device"]["port"], "/dev/custom-port")
        self.assertEqual(self.worker.call_args.args[0][-2:], ["--port", "/dev/custom-port"])

    def test_list_only_never_starts_a_worker(self):
        report, code = detection.run_detection(list_only=True)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "interfaces_only")
        self.assertIsNone(report["device"])
        self.worker.assert_not_called()

    def test_flash_or_reset_failure_keeps_confirmed_chip_identity(self):
        self.worker.return_value = worker_result(detected_chip(flash=False, reset=False))
        report, code = detection.run_detection()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "detected")
        self.assertEqual(report["device"]["chip"]["family"], "ESP8266")
        self.assertEqual(report["device"]["flash"]["status"], "unconfirmed")
        self.assertIsNone(report["device"]["flash"]["size_bytes"])
        self.assertEqual(report["reset"]["status"], "unconfirmed")
        self.assertTrue(report["warnings"])
        self.assertEqual(report["capabilities"]["deployment"], "not_checked")

    def test_worker_errors_preserve_failure_and_do_not_guess_chip(self):
        for worker_code, message in ((2, "Install esptool"), (3, "No serial response")):
            with self.subTest(worker_code=worker_code):
                self.worker.return_value = worker_result({"error": message}, worker_code)
                report, code = detection.run_detection()
                self.assertEqual(code, worker_code)
                self.assertIsNone(report["device"])
                self.assertIn(message, report["message"])

    def test_timeout_leaves_identity_and_reset_unconfirmed(self):
        self.worker.side_effect = subprocess.TimeoutExpired(["detector"], 1)
        report, code = detection.run_detection(timeout=1)
        self.assertEqual(code, 5)
        self.assertEqual(report["status"], "unconfirmed")
        self.assertIsNone(report["device"])
        self.assertEqual(report["reset"]["status"], "unconfirmed")
        self.assertIn("reset", report["message"].lower())

    def test_invalid_worker_output_is_not_reported_as_detection(self):
        for output in ("progress\n{}", "[]", "{}", '{"chip": {"family": "ESP32"}}'):
            with self.subTest(output=output):
                self.worker.return_value = subprocess.CompletedProcess(["detector"], 0, output, "")
                report, code = detection.run_detection()
                self.assertEqual(code, 5)
                self.assertIsNone(report["device"])
                self.assertEqual(report["status"], "unconfirmed")


class DetectionCliTests(unittest.TestCase):
    def setUp(self):
        self.discover = patch("detection.discover_interfaces", return_value=[
            serial_interface("/dev/cu.usbserial-test")
        ]).start()
        self.worker = patch("subprocess.run", return_value=worker_result()).start()
        self.addCleanup(patch.stopall)

    def test_detect_uses_no_wireless_dependencies_or_password_and_keeps_json_clean(self):
        self.worker.return_value.stderr = "Connecting to ROM...\n"
        process = run_detection_cli(["detect"])
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["status"], "detected")
        self.assertIn("Connecting to ROM", process.stderr)
        self.assertNotIn("Connecting to ROM", process.stdout)

    def test_list_is_available_without_a_probe(self):
        process = run_detection_cli(["detect", "--list"])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["status"], "interfaces_only")
        self.worker.assert_not_called()

    def test_invalid_arguments_exit_before_discovery(self):
        for extra in (
            ["--timeout", "0"], ["--timeout", "-1"],
            ["--timeout", "nan"], ["--timeout", "inf"],
            ["--backend", "stm32"], ["--list", "--port", "COM5"],
        ):
            with self.subTest(extra=extra):
                process = run_detection_cli(["detect", *extra])
                self.assertEqual(process.returncode, 2, process.stderr)
                self.discover.assert_not_called()
                self.worker.assert_not_called()

    def test_remote_or_malformed_port_names_never_start_a_worker(self):
        for port in ("socket://example.test:1234", "rfc2217://example.test:1234", " ", "COM5\n", "COM\x005"):
            with self.subTest(port=repr(port)):
                process = run_detection_cli(["detect", "--port", port])
                self.assertEqual(process.returncode, 2, process.stderr)
                self.worker.assert_not_called()

    def test_cancelled_probe_uses_the_cancellation_exit_code(self):
        self.worker.side_effect = KeyboardInterrupt()
        process = run_detection_cli(["detect"])
        self.assertEqual(process.returncode, 130, process.stderr)
        self.assertIn("cancel", process.stderr.lower())
