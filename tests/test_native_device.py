"""Verify native flashing, running evidence and cancellation without touching hardware."""

import hashlib
import io
import itertools
import json
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from support import SKILL
from native_build import artifact_path
import native_device
from operations import BusyError, identity_key, resource_lock, serial_key
from projects import load_project


class SerialPeer:
    def __init__(self, frames):
        self.frames = list(frames)
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.frames[0]) if self.frames else 0

    def open(self):
        pass

    def close(self):
        self.closed = True

    def read(self, size):
        return self.frames.pop(0) if self.frames else b""

    def reset_input_buffer(self):
        pass


class NativeDeviceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve() / "camera"
        source = self.root / "firmware"
        source.mkdir(parents=True)
        (source / "CMakeLists.txt").write_text("project(camera)\n", encoding="utf-8")
        (self.root / "project.json").write_text(json.dumps({
            "schema_version": 2, "backend": "esp-idf", "name": "camera",
            "source": "firmware", "idf": {"target": "esp32", "version": "v5.5.1"},
            "device": {"identity": {"kind": "espressif-mac", "value": "aabbccddeeff"},
                       "port": str(self.root / "synthetic-port")},
        }), encoding="utf-8")
        self.project = load_project(self.root)
        self.contents = {
            "source/main.c": b"void app_main(void) {}\n",
            "source/sdkconfig": b'CONFIG_IDF_TARGET="esp32"\n',
            "build/camera.bin": b"application image",
            "build/camera.elf": b"application debug information",
            "build/bootloader.bin": b"bootloader image",
            "build/flasher_args.json": b'{"partition-table":{"offset":"0x8000","file":"partition-table.bin"}}',
            "build/partition-table.bin": struct.pack(
                "<HBBII16sI", 0x50AA, 0, 0, 0x10000, 0x100000, b"factory", 0,
            ) + b"\xff" * 32,
        }
        self.publish_archive(self.contents)

        self.frames = []
        self.connections = []
        self.actual_id = self.project.device_id
        self.writes = []
        self.loader = SimpleNamespace(
            CHIP_NAME="ESP32", read_mac=lambda: bytes.fromhex(self.actual_id),
            get_secure_boot_enabled=lambda: False, get_flash_encryption_enabled=lambda: False,
            run_stub=lambda: self.loader, change_baud=lambda baud: None,
            hard_reset=lambda: None,
        )
        serial = SimpleNamespace(Serial=self.connect)
        esptool = SimpleNamespace(
            detect_chip=lambda connection, connect_attempts: self.loader,
            attach_flash=lambda loader: None,
            detect_flash_size=lambda loader: "4MB",
            write_flash=lambda loader, images, **options: self.writes.append(images),
            verify_flash=lambda loader, images: self.assertEqual(images, self.writes[-1]),
        )
        self.enterContext(patch.dict("sys.modules", {"serial": serial, "esptool": esptool}))
        self.clock = itertools.count(step=0.1)
        self.enterContext(patch.object(native_device, "time", SimpleNamespace(monotonic=lambda: next(self.clock))))
        self.output = self.enterContext(io.TextIOWrapper(io.BytesIO(), encoding="utf-8", write_through=True))
        self.enterContext(patch.object(native_device.sys, "stderr", self.output))
        self.runner = self.enterContext(patch.object(native_device, "run_process", side_effect=self.run_worker))

    def publish_archive(self, contents, flash_size="4MB", *, extra_images=None):
        self.manifest = {
            "schema_version": 1, "backend": "esp-idf", "target": "esp32",
            "app_bin": "build/camera.bin", "app_elf": "build/camera.elf",
            "app_elf_sha256": hashlib.sha256(contents["build/camera.elf"]).hexdigest(),
            "flash_settings": {"flash_size": flash_size, "flash_mode": "dio", "flash_freq": "40m"},
            "flash_files": {"0x1000": "build/bootloader.bin", "0x8000": "build/partition-table.bin",
                            "0x10000": "build/camera.bin", **(extra_images or {})},
            "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                      for name, data in contents.items()},
        }
        encoded = json.dumps(self.manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        self.build_id = hashlib.sha256(encoded).hexdigest()
        self.manifest["build_id"] = self.build_id
        archive = self.root / "artifacts" / self.build_id
        for name, data in contents.items():
            path = archive / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (archive / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def connect(self, **kwargs):
        connection = SerialPeer(self.frames)
        self.connections.append(connection)
        return connection

    def run_worker(self, argv, **kwargs):
        request = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(argv, native_device.worker(request))

    def evidence(self, *, digest=None):
        return b"AGENTKIT_RUNNING " + json.dumps({
            "device_id": self.project.device_id,
            "elf_sha256": digest or self.manifest["app_elf_sha256"],
            "version": "synthetic-version",
        }).encode() + b"\n"

    def operate(self, *, flash=True, app_only=False):
        return native_device.operate(
            self.project, self.build_id, self.project.port,
            flash=flash, app_only=app_only, duration=1, timeout=5,
        )

    def accept(self):
        return native_device.accept_native(self.project, self.build_id, "Synthetic developer observation.")

    def assert_refused_before_write(self, reason):
        result, code = self.operate()
        self.assertEqual(code, 5)
        self.assertIsNone(result["flash_verified"])
        self.assertIn(reason, result["error"])
        self.assertEqual(self.writes, [])
        self.assertTrue(self.connections[-1].closed)

    def test_wrong_device_incompatible_layout_and_changed_archive_are_refused_before_writing(self):
        self.actual_id = "001122334455"
        self.assert_refused_before_write("identity")
        with self.assertRaisesRegex(ValueError, "verified flash"):
            self.accept()

        # An edit after selection must not authorize the connected board instead.
        original = self.project.path.read_text(encoding="utf-8")

        def switch_device(argv, **kwargs):
            config = json.loads(original)
            config["device"]["identity"]["value"] = self.actual_id
            self.project.path.write_text(json.dumps(config), encoding="utf-8")
            return self.run_worker(argv, **kwargs)

        connections = len(self.connections)
        with patch.object(self.runner, "side_effect", switch_device):
            result, code = self.operate()
        self.project.path.write_text(original, encoding="utf-8")
        self.assertEqual(code, 5)
        self.assertIn("changed before connecting", result["error"])
        self.assertEqual(len(self.connections), connections)
        record = json.loads(Path(result["record"]).read_text(encoding="utf-8"))
        self.assertEqual(record["device_id"], self.project.device_id)
        self.assertEqual(self.writes, [])

        # Small image bytes do not make an 8 MiB firmware fit a 4 MiB device.
        self.actual_id = self.project.device_id
        self.publish_archive(self.contents, flash_size="8MB")
        self.assert_refused_before_write("capacity")

        # Validate data partition ranges even when no image is written there.
        for offset, size, reason in (
            (0x3F0000, 0x20000, "partition exceeds"),
            (0x100000, 0x20000, "partitions overlap"),
        ):
            with self.subTest(reason=reason):
                table = self.contents["build/partition-table.bin"][:-32] + struct.pack(
                    "<HBBII16sI", 0x50AA, 1, 0x82, offset, size, b"data", 0,
                ) + b"\xff" * 32
                self.publish_archive({**self.contents, "build/partition-table.bin": table})
                self.assert_refused_before_write(reason)

        # A larger OTA slot cannot make the write to the smaller factory slot safe.
        table = b"".join(struct.pack("<HBBII16sI", 0x50AA, *entry, 0) for entry in (
            (0, 0, 0x10000, 0x10000, b"factory"),
            (1, 0x82, 0x20000, 0x10000, b"data"),
            (0, 0x10, 0x30000, 0x40000, b"ota_0"),
        )) + b"\xff" * 32
        self.publish_archive({**self.contents, "build/partition-table.bin": table,
                              "build/camera.bin": bytes(0x18000)})
        self.assert_refused_before_write("target application partition")

        # Extra data and OTA images can start inside their partitions and end at the edge.
        extra = {"0x28000": "build/data.bin", "0x68000": "build/extra-app.bin"}
        contents = {**self.contents, "build/partition-table.bin": table,
                    **{name: bytes(0x8000) for name in extra.values()}}
        for name in extra.values():
            self.publish_archive({**contents, name: bytes(0x8001)}, extra_images=extra)
            self.assert_refused_before_write(f"Archived image {name}")

        image = artifact_path(self.project, self.build_id) / "build" / "camera.bin"
        image.write_bytes(b"modified after building")
        calls = self.runner.call_count
        with self.assertRaisesRegex(ValueError, "differ"):
            self.operate()
        self.assertEqual(self.runner.call_count, calls)
        self.assertEqual(self.writes, [])

        self.publish_archive(contents, extra_images=extra)
        self.frames = [self.evidence()]
        _, code = self.operate()
        self.assertEqual(code, 0)
        installed = dict(self.writes[-1])
        self.assertEqual(installed[0x28000], contents["build/data.bin"])
        self.assertEqual(installed[0x68000], contents["build/extra-app.bin"])

        # A non-application image is only compared on an app-only update.
        extended = contents["build/extra-app.bin"] + b"\x5a"
        self.publish_archive({**contents, "build/extra-app.bin": extended}, extra_images=extra)

        def installed_md5(offset, size):
            data = installed[offset][:size].ljust(size, b"\xff")
            return hashlib.md5(data).hexdigest()

        self.loader.flash_md5sum = installed_md5
        rejected, code = self.operate(app_only=True)
        self.assertIn("differs", rejected["error"])
        self.assertEqual(code, 5)
        self.assertEqual(len(self.writes), 1)
        installed[0x68000] = extended
        _, code = self.operate(app_only=True)
        self.assertEqual(code, 0)
        self.assertEqual(list(dict(self.writes[-1])), [0x10000])

    def test_monitor_recovers_unknown_flash_and_later_mismatch_invalidates_acceptance(self):
        result, code = self.operate()
        self.assertEqual(code, 5)
        self.assertTrue(result["flash_verified"])
        self.assertIsNone(result["running_verified"])
        self.assertTrue(result["reset_requested"])
        with self.assertRaisesRegex(ValueError, "verified flash"):
            self.accept()
        self.frames = [b"ordinary boot output\n", self.evidence()]
        observed, code = self.operate(flash=False)
        self.assertEqual(code, 0)
        self.assertTrue(observed["running_verified"])
        self.assertIsNone(observed["flash_verified"])
        self.assertEqual(len(self.writes), 1)
        self.assertIn(self.evidence(), (self.root / observed["log"]).read_bytes())
        acceptance = json.loads(self.accept().read_text(encoding="utf-8"))
        self.assertEqual(acceptance["build_id"], self.build_id)
        self.assertEqual(acceptance["application_validation"], "developer_confirmed")
        self.frames = [self.evidence(digest="0" * 64)]
        observed, code = self.operate(flash=False)
        self.assertEqual(code, 5)
        self.assertFalse(observed["running_verified"])
        self.assertTrue(self.connections[-1].closed)
        with self.assertRaisesRegex(ValueError, "verified flash"):
            self.accept()

    def test_cancelled_worker_with_partial_progress_keeps_receipt_and_releases_resources(self):
        self.frames = [self.evidence()]
        _, code = self.operate()
        self.assertEqual(code, 0)
        self.assertTrue(self.accept().is_file())
        pending_records = []

        def interrupted(argv, **kwargs):
            request = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))
            pending = json.loads(sorted((self.root / "records").glob("*.json"))[-1].read_text(encoding="utf-8"))
            self.assertEqual(pending["kind"], "native_flash")
            self.assertEqual(pending["stage"], "pending")
            self.assertIsNone(pending["flash_verified"])
            pending_records.append(pending)
            with self.assertRaises(BusyError):
                self.accept()
            Path(request["response"]).write_text('{"stage": "write",', encoding="utf-8")
            raise KeyboardInterrupt

        self.runner.side_effect = interrupted
        result, code = self.operate()
        self.assertEqual(code, 130)
        self.assertEqual(result["stage"], "cancelled")
        self.assertIsNone(result["flash_verified"])
        saved = json.loads(Path(result["record"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["exit_code"], 130)
        self.assertEqual(saved["operation_id"], pending_records[-1]["operation_id"])
        with self.assertRaisesRegex(ValueError, "verified flash"):
            self.accept()
        keys = [serial_key(self.project.port), identity_key("espressif-mac", self.project.device_id)]
        with resource_lock(keys, "retry"):
            pass
        self.runner.side_effect = self.run_worker
        self.frames = [self.evidence()]
        recovered, code = self.operate()
        self.assertEqual(code, 0)
        self.assertTrue(recovered["running_verified"])
        self.assertTrue(self.accept().is_file())

        # If the final receipt is lost, the already-persisted pending operation
        # still prevents acceptance using the earlier successful installation.
        write_record = native_device.record_native

        def lose_final_receipt(project, record):
            if record["stage"] == "pending":
                return write_record(project, record)
            raise OSError("Simulated interruption before the final receipt.")

        self.runner.side_effect = interrupted
        with patch.object(native_device, "record_native", side_effect=lose_final_receipt):
            with self.assertRaisesRegex(OSError, "final receipt"):
                self.operate()
        with self.assertRaisesRegex(ValueError, "verified flash"):
            self.accept()
        with resource_lock(keys, "retry-after-lost-receipt"):
            pass


if __name__ == "__main__":
    unittest.main()
