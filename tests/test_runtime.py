"""Retain actionable diagnostics from the installed application's current boot."""

from contextlib import nullcontext, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from support import load_runtime, run_cli
from deployment import prepare_deploy


class StartupDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.app = self.root / "agentkit_app"
        self.app.mkdir()

    def install(self, source):
        (self.app / "app.py").write_text(source, encoding="utf-8")
        bundle = prepare_deploy(self.app)
        (self.root / ".agentkit-manifest.json").write_bytes(bundle.manifest)
        return bundle.manifest_id

    def test_startup_failure_is_reported_by_info(self):
        identifier = self.install("raise ImportError('required sensor driver')\n")
        for platform in ("esp8266", "esp32"):
            with self.subTest(platform=platform):
                runtime = load_runtime(self.root, platform)
                original_path = runtime.sys.path
                original_directory = Path.cwd()
                runtime.start({})

                def execute(source, timeout):
                    machine = SimpleNamespace(unique_id=lambda: b"desktop")
                    with (
                        io.StringIO() as output,
                        redirect_stdout(output),
                        patch.dict(sys.modules, machine=machine, agentkit_runtime=runtime),
                        patch.object(os, "listdir", return_value=["agentkit_runtime.py"]),
                        patch.object(os, "uname", return_value=SimpleNamespace(machine="desktop"), create=True),
                    ):
                        exec(source, {})
                        return output.getvalue().encode(), b"", None

                session = SimpleNamespace(execute=execute)
                result = run_cli(
                    ["info", "--address", "127.0.0.1", "--password-stdin"],
                    lambda address, password: nullcontext(session),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                status = json.loads(result.stdout)["deployment"]
                self.assertEqual(status["state"], "installed")
                self.assertEqual(status["manifest_id"], identifier)
                self.assertEqual(status["boot_error"]["manifest_id"], identifier)
                self.assertIn("ImportError: required sensor driver", status["boot_error"]["traceback"])
                self.assertEqual(Path.cwd(), original_directory)
                self.assertIs(runtime.sys.path, original_path)

    def test_long_traceback_keeps_its_tail_and_clears_on_next_boot(self):
        self.install("raise ValueError('x' * 4096 + 'END_OF_FAILURE')\n")
        runtime = load_runtime(self.root)
        runtime.start({})
        traceback = runtime.status()["boot_error"]["traceback"]
        self.assertTrue(traceback.startswith("...\n"))
        self.assertTrue(traceback.rstrip().endswith("END_OF_FAILURE"))
        self.assertEqual(len(traceback.encode()), 2052)

        self.install("started = True\n")
        runtime = load_runtime(self.root)
        namespace = {}
        runtime.start(namespace)
        self.assertTrue(namespace["started"])
        self.assertIsNone(runtime.status()["boot_error"])
