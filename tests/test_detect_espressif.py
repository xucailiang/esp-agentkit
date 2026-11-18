"""ROM worker results and serial cleanup across the core probe outcomes."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import unittest
from unittest.mock import Mock, patch

import esptool
from esptool.targets.esp32 import ESP32ROM
import serial

import support  # Add the distributed scripts to the import path.
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
        self.enterContext(patch("serial.serial_for_url", return_value=self.connection))
        self.detect = self.enterContext(patch("esptool.detect_chip", return_value=self.loader))
        self.attach = self.enterContext(patch("esptool.attach_flash"))
        self.enterContext(patch("esptool.detect_flash_size", return_value="4MB"))
        self.fallback_reset = self.enterContext(patch("esptool.reset.HardReset"))
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.enterContext(redirect_stdout(self.stdout))
        self.enterContext(redirect_stderr(self.stderr))

    def test_rom_identity_is_reported_without_writing_flash_and_releases_the_port(self):
        self.attach.side_effect = lambda loader: print("Enabling default SPI flash mode...")
        self.assertEqual(detect_espressif.main(["--port", "COM7"]), 0)
        result = json.loads(self.stdout.getvalue())
        self.assertEqual(result["chip"]["family"], "ESP32")
        self.assertEqual(result["flash"]["size_bytes"], 4 * 1024 * 1024)
        self.assertEqual(result["reset"]["status"], "requested")
        self.assertIn("Enabling default SPI flash mode", self.stderr.getvalue())
        self.loader.hard_reset.assert_called_once_with()
        self.connection.close.assert_called_once_with()
        self.loader.run_stub.assert_not_called()
        self.loader.flash_begin.assert_not_called()
        self.loader.flash_block.assert_not_called()

    def test_flash_failure_preserves_chip_identity_and_resets_then_closes(self):
        self.attach.side_effect = esptool.FatalError("flash is inaccessible")
        self.assertEqual(detect_espressif.main(["--port", "COM7"]), 0)
        result = json.loads(self.stdout.getvalue())
        self.assertEqual(result["chip"]["family"], "ESP32")
        self.assertEqual(result["flash"]["status"], "unconfirmed")
        self.assertIsNone(result["flash"]["size_bytes"])
        self.assertIn("flash is inaccessible", result["warnings"][0])
        self.loader.hard_reset.assert_called_once_with()
        self.connection.close.assert_called_once_with()

    def test_connection_failure_reports_error_and_releases_the_port_for_retry(self):
        self.detect.side_effect = esptool.FatalError("No sync reply")
        self.assertEqual(detect_espressif.main(["--port", "COM7"]), 3)
        self.assertEqual(json.loads(self.stdout.getvalue()), {"error": "No sync reply"})
        self.fallback_reset.assert_called_once_with(self.connection)
        self.fallback_reset.return_value.assert_called_once_with()
        self.connection.close.assert_called_once_with()
