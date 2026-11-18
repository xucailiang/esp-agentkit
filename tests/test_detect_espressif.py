"""Espressif probing preserves identity and cleans up without writing firmware."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import runpy
import sys
import unittest
from unittest.mock import Mock, patch

import esptool
from esptool.targets.esp32 import ESP32ROM
import serial

from support import SKILL
import detect_espressif


class EspressifProbeTests(unittest.TestCase):
    def setUp(self):
        self.connection = Mock(spec=serial.Serial)
        self.connection.is_open = True
        self.loader = Mock(spec=ESP32ROM)
        self.loader.CHIP_NAME = "ESP32"
        self.loader.IS_STUB = False
        self.loader.get_chip_description.return_value = "ESP32-D0WD (revision v3.0)"
        self.loader.get_chip_features.return_value = ["Wi-Fi", "BT", "Dual Core"]
        self.loader.read_mac.return_value = (0xAA, 0xBB, 0xCC, 0x11, 0x22, 0x33)
        self.loader.flash_id.return_value = 0x1640EF
        self.serial_factory = self.enterContext(patch(
            "serial.serial_for_url", return_value=self.connection
        ))
        self.detect = self.enterContext(patch("esptool.detect_chip", return_value=self.loader))
        self.attach = self.enterContext(patch("esptool.attach_flash"))
        self.size = self.enterContext(patch("esptool.detect_flash_size", return_value="4MB"))
        self.fallback_reset = self.enterContext(patch("esptool.reset.HardReset"))
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.enterContext(redirect_stdout(self.stdout))
        self.enterContext(redirect_stderr(self.stderr))

    def test_rom_probe_returns_structured_identity_and_releases_port(self):
        def detect(*args, **kwargs):
            print("Detecting chip type... ESP32")
            return self.loader

        self.detect.side_effect = detect
        result = detect_espressif.probe("/dev/ttyUSB0")

        self.assertEqual(result, {
            "chip": {
                "family": "ESP32",
                "description": "ESP32-D0WD (revision v3.0)",
                "features": ["Wi-Fi", "BT", "Dual Core"],
                "mac": "aa:bb:cc:11:22:33",
            },
            "flash": {"id": "0x1640ef", "size_bytes": 4 * 1024 * 1024, "status": "detected"},
            "reset": {"status": "requested"},
            "warnings": [],
        })
        self.serial_factory.assert_called_once_with("/dev/ttyUSB0", exclusive=True, do_not_open=True)
        self.connection.open.assert_called_once_with()
        self.detect.assert_called_once_with(self.connection, connect_attempts=2)
        self.attach.assert_called_once_with(self.loader)
        self.loader.hard_reset.assert_called_once_with()
        self.connection.close.assert_called_once_with()
        self.loader.run_stub.assert_not_called()
        self.loader.flash_begin.assert_not_called()
        self.loader.flash_block.assert_not_called()
        self.assertEqual(self.stdout.getvalue(), "")
        self.assertIn("Detecting chip type", self.stderr.getvalue())

    def test_flash_attach_failure_preserves_chip_and_requests_reset(self):
        self.attach.side_effect = esptool.FatalError("flash is inaccessible")
        result = detect_espressif.probe("COM7")
        self.assertEqual(result["chip"]["family"], "ESP32")
        self.assertEqual(result["flash"], {"id": None, "size_bytes": None, "status": "unconfirmed"})
        self.assertIn("flash is inaccessible", result["warnings"][0])
        self.loader.flash_id.assert_not_called()
        self.loader.hard_reset.assert_called_once_with()
        self.connection.close.assert_called_once_with()

    def test_invalid_flash_id_does_not_report_a_capacity(self):
        self.loader.flash_id.return_value = 0xFFFFFF
        result = detect_espressif.probe("COM7")
        self.assertEqual(result["flash"], {"id": None, "size_bytes": None, "status": "unconfirmed"})
        self.assertIn("invalid ID", result["warnings"][0])
        self.size.assert_not_called()

    def test_unknown_capacity_preserves_flash_id(self):
        self.size.return_value = None
        result = detect_espressif.probe("COM7")
        self.assertEqual(result["flash"], {"id": "0x1640ef", "size_bytes": None, "status": "unconfirmed"})
        self.assertIn("capacity was not recognized", result["warnings"][0])

    def test_flash_capacity_read_failure_preserves_detected_id(self):
        self.size.side_effect = esptool.FatalError("capacity read failed")
        result = detect_espressif.probe("COM7")
        self.assertEqual(result["flash"], {
            "id": "0x1640ef", "size_bytes": None, "status": "unconfirmed"
        })
        self.assertIn("capacity read failed", result["warnings"][0])
        self.loader.hard_reset.assert_called_once_with()
        self.connection.close.assert_called_once_with()

    def test_individual_identity_read_failure_preserves_other_fields(self):
        self.loader.get_chip_description.side_effect = esptool.FatalError("restricted registers")
        self.loader.read_mac.side_effect = esptool.FatalError("MAC unavailable")
        result = detect_espressif.probe("COM7")
        self.assertEqual(result["chip"]["family"], "ESP32")
        self.assertEqual(result["chip"]["description"], "ESP32")
        self.assertEqual(result["chip"]["features"], ["Wi-Fi", "BT", "Dual Core"])
        self.assertIsNone(result["chip"]["mac"])
        self.assertEqual(len(result["warnings"]), 2)
        self.assertEqual(result["flash"]["status"], "detected")

    def test_reset_failure_preserves_identity_and_still_closes(self):
        self.loader.hard_reset.side_effect = serial.SerialException("RTS unsupported")
        self.connection.close.side_effect = OSError("close failed")
        result = detect_espressif.probe("COM7")
        self.assertEqual(result["chip"]["family"], "ESP32")
        self.assertEqual(result["reset"], {"status": "unconfirmed"})
        self.assertEqual(len(result["warnings"]), 2)
        self.assertIn("Reset the board manually", result["warnings"][0])
        self.connection.close.assert_called_once_with()

    def test_detection_failure_uses_fallback_reset_and_closes(self):
        self.detect.side_effect = esptool.FatalError("No sync reply")
        self.fallback_reset.return_value.side_effect = serial.SerialException("reset failed")
        with self.assertRaisesRegex(esptool.FatalError, "No sync reply"):
            detect_espressif.probe("COM7")
        self.fallback_reset.assert_called_once_with(self.connection)
        self.fallback_reset.return_value.assert_called_once_with()
        self.connection.close.assert_called_once_with()
        self.assertIn("reset failed", self.stderr.getvalue())

    def test_detection_closed_port_is_not_reopened_for_cleanup(self):
        def fail(*args, **kwargs):
            self.connection.is_open = False
            raise esptool.FatalError("Connection failed")

        self.detect.side_effect = fail
        with self.assertRaisesRegex(esptool.FatalError, "Connection failed"):
            detect_espressif.probe("COM7")
        self.fallback_reset.assert_not_called()
        self.connection.open.assert_called_once_with()
        self.connection.close.assert_called_once_with()
        self.assertIn("before a reset could be requested", self.stderr.getvalue())

    def test_open_failure_closes_owned_serial_object(self):
        self.connection.is_open = False
        self.connection.open.side_effect = serial.SerialException("Access denied")
        with self.assertRaisesRegex(serial.SerialException, "Access denied"):
            detect_espressif.probe("COM7")
        self.detect.assert_not_called()
        self.fallback_reset.assert_not_called()
        self.connection.close.assert_called_once_with()

    def test_existing_stub_is_rejected_without_flash_probe(self):
        self.loader.IS_STUB = True
        with self.assertRaisesRegex(RuntimeError, "ROM connection was not confirmed"):
            detect_espressif.probe("COM7")
        self.attach.assert_not_called()
        self.loader.hard_reset.assert_called_once_with()
        self.connection.close.assert_called_once_with()

    def test_worker_emits_json_and_routes_esptool_logs_to_stderr(self):
        self.attach.side_effect = lambda loader: print("Enabling default SPI flash mode...")
        entry = str(SKILL / "scripts" / "detect_espressif.py")
        with (
            patch.object(sys, "argv", [entry, "--port", "COM7"]),
            self.assertRaises(SystemExit) as exited,
        ):
            runpy.run_path(entry, run_name="__main__")
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(json.loads(self.stdout.getvalue())["chip"]["family"], "ESP32")
        self.assertIn("Enabling default SPI flash mode", self.stderr.getvalue())

    def test_worker_connection_error_emits_json_and_nonzero_exit(self):
        self.detect.side_effect = esptool.FatalError("No sync reply")
        status = detect_espressif.main(["--port", "COM7"])
        self.assertEqual(status, 3)
        self.assertEqual(json.loads(self.stdout.getvalue()), {"error": "No sync reply"})
        self.connection.close.assert_called_once_with()

    def test_worker_missing_dependency_uses_exit_two_without_opening_port(self):
        with patch.dict("sys.modules", {"esptool": None}):
            status = detect_espressif.main(["--port", "COM7"])
        self.assertEqual(status, 2)
        self.assertIn("Missing dependency", json.loads(self.stdout.getvalue())["error"])
        self.serial_factory.assert_not_called()

    def test_remote_serial_url_is_rejected_before_opening_port(self):
        with self.assertRaisesRegex(ValueError, "local USB serial"):
            detect_espressif.probe("rfc2217://example.invalid:4000")
        self.serial_factory.assert_not_called()
