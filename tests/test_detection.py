"""Core USB workflow: list interfaces, select a target, then report a bounded probe."""

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


def serial_interface(port, usb=True):
    return {
        "transport": "serial", "port": port, "description": "USB serial bridge",
        "usb": {"vid": 0x1A86, "pid": 0x7523} if usb else None,
    }


def worker_result():
    return subprocess.CompletedProcess(["detector"], 0, json.dumps({
        "chip": {"family": "ESP8266", "description": "ESP8266EX"},
        "flash": {"id": "0x1640ef", "size_bytes": 4 * 1024 * 1024, "status": "detected"},
        "reset": {"status": "requested"}, "warnings": [],
    }), "Connecting to ROM...\n")


def run_detection_cli(args):
    """Detection must work before wireless dependencies or credentials are available."""
    entry = str(SKILL / "scripts" / "agentkit.py")
    real_import = builtins.__import__

    def local_import(name, *import_args, **kwargs):
        if name in ("deployment", "webrepl_session"):
            raise AssertionError(f"Detection unexpectedly imports {name}.")
        return real_import(name, *import_args, **kwargs)

    with (
        io.TextIOWrapper(io.BytesIO(), encoding="utf-8", write_through=True) as stdout,
        io.TextIOWrapper(io.BytesIO(), encoding="utf-8", write_through=True) as stderr,
        patch.object(sys, "argv", [entry, "detect", *args]),
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


class DetectionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.worker = self.enterContext(patch("subprocess.run", return_value=worker_result()))

    def test_list_reports_usb_interfaces_without_claiming_chip_identity(self):
        bridge = ListPortInfo("COM7")
        bridge.description = "ESP32 USB serial adapter"
        bridge.vid, bridge.pid = 0x1A86, 0x7523
        other = ListPortInfo("COM1")
        with patch("serial.tools.list_ports.comports", return_value=[bridge, other]):
            process = run_detection_cli(["--list"])
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["status"], "interfaces_only")
        self.assertIsNone(report["device"])
        self.assertEqual(report["reset"]["status"], "not_requested")
        interfaces = {item["port"]: item for item in report["interfaces"]}
        self.assertEqual(interfaces["COM7"]["usb"]["vid"], 0x1A86)
        self.assertIsNone(interfaces["COM1"]["usb"])
        self.worker.assert_not_called()

    def test_single_usb_candidate_returns_identity_with_clean_json_and_bounded_probe(self):
        with patch("detection.discover_interfaces", return_value=[
            serial_interface("COM7"), serial_interface("COM1", usb=False),
        ]):
            process = run_detection_cli(["--timeout", "7.5"])
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["status"], "detected")
        self.assertEqual(report["device"]["port"], "COM7")
        self.assertEqual(report["device"]["chip"]["family"], "ESP8266")
        self.assertEqual(report["device"]["flash"]["size_bytes"], 4 * 1024 * 1024)
        self.assertIsNone(report["device"]["board"])
        self.assertEqual(report["runtime"]["status"], "not_checked")
        self.assertEqual(report["capabilities"]["deployment"], "not_checked")
        self.assertIn("Connecting to ROM", process.stderr)
        self.worker.assert_called_once()
        self.assertEqual(self.worker.call_args.args[0][-2:], ["--port", "COM7"])
        self.assertEqual(self.worker.call_args.kwargs["timeout"], 7.5)

    def test_multiple_candidates_require_explicit_selection_before_probing(self):
        with patch("detection.discover_interfaces", return_value=[
            serial_interface("COM7"), serial_interface("COM8"),
        ]):
            ambiguous = run_detection_cli([])
            self.assertEqual(ambiguous.returncode, 5, ambiguous.stderr)
            self.assertEqual(json.loads(ambiguous.stdout)["status"], "selection_required")
            self.worker.assert_not_called()
            selected = run_detection_cli(["--port", "COM8"])
        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertEqual(json.loads(selected.stdout)["device"]["port"], "COM8")
        self.assertEqual(self.worker.call_args.args[0][-2:], ["--port", "COM8"])

    def test_missing_usb_metadata_requires_a_known_manual_port(self):
        with patch("detection.discover_interfaces", return_value=[serial_interface("COM7", usb=False)]):
            automatic = run_detection_cli([])
            self.assertEqual(automatic.returncode, 3, automatic.stderr)
            self.assertEqual(json.loads(automatic.stdout)["status"], "no_candidate")
            self.worker.assert_not_called()
            selected = run_detection_cli(["--port", "COM7"])
        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertEqual(json.loads(selected.stdout)["device"]["port"], "COM7")

    def test_failed_or_timed_out_probe_leaves_identity_unconfirmed(self):
        failure = subprocess.CompletedProcess(["detector"], 3, '{"error": "No sync reply"}', "")
        with patch("detection.discover_interfaces", return_value=[serial_interface("COM7")]):
            for outcome, code in ((failure, 3), (subprocess.TimeoutExpired(["detector"], 1), 5)):
                with self.subTest(outcome=type(outcome).__name__):
                    self.worker.side_effect = [outcome]
                    process = run_detection_cli(["--timeout", "1"])
                    self.assertEqual(process.returncode, code, process.stderr)
                    report = json.loads(process.stdout)
                    self.assertEqual(report["status"], "unconfirmed")
                    self.assertIsNone(report["device"])
                    self.assertEqual(report["reset"]["status"], "unconfirmed")
                    self.assertIn("reset", report["message"].lower())
