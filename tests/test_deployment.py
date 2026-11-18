"""Deployment refusals and retry recovery through the CLI entry point."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from support import SKILL, run_cli
from board_fixture import FileBoard
from deployment import APP, MANIFEST, MARKER, STAGING, prepare_deploy


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.board = FileBoard(self.root / "board")
        self.source = self.root / "application"
        self.source.mkdir()
        (self.source / "app.py").write_text("started = True\n")
        (self.source / "driver.py").write_text("value = 'original'\n")
        bundle = prepare_deploy(self.source)
        self.board.path(APP).mkdir()
        for name, asset in bundle.files.items():
            self.board.path(APP + "/" + name).write_bytes(asset.path.read_bytes())
        self.board.path(MANIFEST).write_bytes(bundle.manifest)
        self.board.path("/main.py").write_bytes((SKILL / "board/main.py").read_bytes())
        self.board.path("/agentkit_config.json").write_text('{"fixture": "management configuration"}')

    def command(self, command="deploy", device_id=None):
        source = self.source if command == "deploy" else self.source / "app.py"
        return run_cli(
            [command, "--address", "127.0.0.1", "--password-stdin", "--device-id",
             device_id or self.board.device_id, str(source)],
            self.board.connect,
        )

    def assert_refused_before_writes(self, before, result):
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertEqual(self.board.snapshot(), before)
        self.assertEqual(self.board.mutations, [])
        self.assertEqual(self.board.restarts, 0)
        self.assertFalse(self.board.path(MARKER).exists())

    def test_wrong_device_id_refuses_put_and_deploy_before_writes(self):
        before = self.board.snapshot()
        for command in ("put", "deploy"):
            with self.subTest(command=command):
                result = self.command(command, device_id="deadbeef")
                self.assert_refused_before_writes(before, result)
                self.assertIn("does not match", result.stderr)

    def test_corrupted_upload_keeps_marker_and_complete_retry_recovers(self):
        before = self.board.snapshot()
        (self.source / "app.py").write_text("started = 'updated'\n")
        (self.source / "driver.py").write_text("value = 'updated'\n")
        self.board.uploads_until_corruption = 2

        first = self.command()

        self.assertEqual(first.returncode, 5, first.stderr)
        self.assertIn("digest mismatch", first.stderr)
        self.assertTrue(self.board.path(MARKER).is_file())
        self.assertTrue(self.board.path(STAGING).is_file())
        self.assertEqual(self.board.path(MANIFEST).read_bytes(), before[MANIFEST[1:]])
        self.assertEqual(self.board.path(APP + "/app.py").read_bytes(), before["agentkit_app/app.py"])
        self.assertEqual(self.board.path(APP + "/driver.py").read_bytes(), (self.source / "driver.py").read_bytes())
        self.assertNotEqual(self.board.path(APP + "/driver.py").read_bytes(), before["agentkit_app/driver.py"])
        self.assertEqual(self.board.restarts, 0)
        namespace = {}
        self.board.runtime.start(namespace)
        self.assertNotIn("started", namespace)
        self.assertEqual(self.board.runtime.status()["state"], "deploying")

        second = self.command()

        self.assertEqual(second.returncode, 0, second.stderr)
        receipt = json.loads(second.stdout)
        expected = prepare_deploy(self.source)
        self.assertEqual(receipt["manifest_id"], expected.manifest_id)
        self.assertTrue(receipt["webrepl_authenticated"])
        self.assertEqual(self.board.connections, 3)
        self.assertEqual(self.board.restarts, 1)
        self.assertFalse(self.board.path(MARKER).exists())
        self.assertFalse(self.board.path(STAGING).exists())
        for name, asset in expected.files.items():
            actual = self.board.path(APP + "/" + name).read_bytes()
            self.assertEqual(hashlib.sha256(actual).hexdigest(), asset.digest)
        self.assertEqual(self.board.path(MANIFEST).read_bytes(), expected.manifest)
        self.assertEqual(self.board.mutations.count(("rename", STAGING, APP + "/driver.py")), 1)
        self.assertEqual(self.board.path("/agentkit_config.json").read_bytes(), before["agentkit_config.json"])
        self.assertEqual(self.board.runtime.status()["state"], "installed")

    def test_file_capacity_refusal_preserves_previous_application(self):
        self.board.free_blocks = 1
        before = self.board.snapshot()
        # The smaller upload fits by itself but leaves too little room for the manifest.
        for size in (self.board.block_size + 1, self.board.block_size - 1):
            with self.subTest(upload_bytes=size):
                (self.source / "app.py").write_bytes(b"#" * size)
                result = self.command()
                self.assert_refused_before_writes(before, result)
                self.assertIn("available", result.stderr)

    def test_new_directory_metadata_is_included_in_capacity(self):
        drivers = self.source / "drivers"
        drivers.mkdir()
        (drivers / "sensor.py").write_text("value = 1\n")
        self.board.free_blocks = 1
        before = self.board.snapshot()
        result = self.command()
        self.assert_refused_before_writes(before, result)
        self.assertIn("8192 bytes", result.stderr)

        self.board.free_blocks = 3
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.board.path(APP + "/drivers/sensor.py").read_bytes(),
            (drivers / "sensor.py").read_bytes(),
        )
        self.assertEqual(self.board.path(MANIFEST).read_bytes(), prepare_deploy(self.source).manifest)
        self.assertFalse(self.board.path(MARKER).exists())

    def test_invalid_free_block_counts_cannot_authorize_an_update(self):
        (self.source / "app.py").write_text("started = 'updated'\n")
        before = self.board.snapshot()
        for free_blocks in (-1, self.board.total_blocks + 1, 2**32 - 1):
            with self.subTest(free_blocks=free_blocks):
                self.board.free_blocks = free_blocks
                result = self.command()
                self.assert_refused_before_writes(before, result)
                self.assertIn("0 available", result.stderr)
