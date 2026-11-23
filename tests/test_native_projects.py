"""Discover and hand off native projects alongside unchanged MicroPython projects."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from support import SKILL
from projects import discover_projects, load_project, resolve_project


class NativeProjectTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        (self.workspace / ".git").mkdir()
        self.root = self.workspace / "device-projects" / "camera"
        self.source = self.root / "firmware"
        self.source.mkdir(parents=True)
        (self.source / "CMakeLists.txt").write_text("project(camera)\n", encoding="utf-8")
        (self.source / "main.c").write_text("void app_main(void) {}\n", encoding="utf-8")
        self.config = {
            "schema_version": 2,
            "backend": "esp-idf",
            "name": "Camera 原生",
            "aliases": ["native camera"],
            "description": "USB camera project",
            "source": "firmware",
            "idf": {"target": "esp32", "version": "v5.5.1"},
            "device": {
                "identity": {"kind": "espressif-mac", "value": "AABBCCDDEEFF"},
                "port": "COM7",
            },
        }
        self.save()

    def save(self):
        (self.root / "project.json").write_text(
            json.dumps(self.config, ensure_ascii=False), encoding="utf-8"
        )

    def test_native_project_is_discovered_and_copied_without_sdk_or_network_config(self):
        legacy = self.workspace / "device-projects" / "legacy"
        (legacy / "app").mkdir(parents=True)
        (legacy / "app" / "app.py").write_text("print('legacy')\n", encoding="utf-8")
        (legacy / "project.json").write_text(json.dumps({
            "name": "Legacy", "app": "app",
            "device": {"device_id": "01020304", "address": "127.0.0.1",
                       "webrepl_password": "testpass"},
        }), encoding="utf-8")
        # An archived project must not become another discovery result.
        shutil.copytree(self.root, self.workspace / "artifacts" / "camera")
        discovered = discover_projects(self.workspace)
        self.assertEqual({project.name for project in discovered}, {"Camera 原生", "Legacy"})
        native = resolve_project("native camera", self.workspace)
        self.assertEqual(native.public_summary()["identity"], {
            "kind": "espressif-mac", "value": "aabbccddeeff",
        })
        self.assertEqual(load_project(legacy).public_summary(), {
            "name": "Legacy", "aliases": [], "description": "",
            "path": str(legacy / "project.json"), "app": str(legacy / "app"),
            "device_id": "01020304", "address": "127.0.0.1",
        })

        receiving = self.workspace / "receiving"
        receiving.mkdir()
        (receiving / ".git").mkdir()
        copied = Path(shutil.copytree(self.root, receiving / "camera"))
        self.root.rename(self.root.with_name("original-offline"))
        selected = resolve_project("native camera", receiving)
        self.assertEqual(selected.root, copied)
        self.assertEqual(selected.source, copied / "firmware")
        self.assertEqual(selected.idf_version, "v5.5.1")
        self.assertEqual(selected.port, "COM7")
        listed = subprocess.run(
            [sys.executable, str(SKILL / "scripts" / "agentkit.py"), "projects"],
            cwd=receiving, capture_output=True, timeout=10,
            env={**os.environ, "IDF_PATH": "", "PYTHONIOENCODING": "cp1252"},
        )
        self.assertEqual(listed.returncode, 0, listed.stderr)
        summaries = json.loads(listed.stdout)["projects"]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["backend"], "esp-idf")
        self.assertNotIn("webrepl_password", summaries[0])

    def test_source_can_be_the_existing_native_project_root(self):
        self.config["source"] = "."
        self.config["device"].pop("port")
        (self.root / "CMakeLists.txt").write_text("project(camera)\n", encoding="utf-8")
        self.save()
        project = load_project(self.root)
        self.assertEqual(project.source, self.root)
        self.assertIsNone(project.port)

    def test_invalid_source_and_untyped_device_are_rejected_before_use(self):
        # A copied configuration must not silently build a source tree outside itself.
        self.config["source"] = "../outside"
        self.save()
        with self.assertRaisesRegex(ValueError, "relative directory"):
            load_project(self.root)
        linked = self.root / "linked"
        try:
            linked.symlink_to(self.source, target_is_directory=True)
        except (OSError, NotImplementedError):
            # Windows may require privileges or Developer Mode to create a symlink.
            pass
        else:
            self.config["source"] = "linked"
            self.save()
            with self.assertRaisesRegex(ValueError, "symbolic links"):
                load_project(self.root)
        self.config["source"] = "firmware"
        self.config["device"] = {"device_id": "aabbccddeeff"}
        self.save()
        with self.assertRaisesRegex(ValueError, "identity"):
            load_project(self.root)


if __name__ == "__main__":
    unittest.main()
