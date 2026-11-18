"""Core project handoff, deployment and acceptance workflows through the CLI."""

from contextlib import chdir, nullcontext
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from support import SKILL, run_cli
from board_fixture import FileBoard
from deployment import prepare_deploy
from webrepl_session import LoginError, WebReplSession


class ProjectCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        (self.workspace / ".git").mkdir()
        self.root = self.workspace / "device-projects" / "屏幕项目"
        self.app = self.root / "app"
        self.app.mkdir(parents=True)
        (self.app / "app.py").write_text("print('display')\n", encoding="utf-8")
        (self.root / "probe.py").write_text("print(42)\n", encoding="utf-8")
        self.config = {
            "name": "ESP12屏幕项目", "aliases": ["ESP12屏幕"], "app": "app",
            "description": "SPI 显示测试 🖥",
            "device": {"device_id": "01020304", "address": "127.0.0.1",
                       "webrepl_password": "testpass"},
        }
        (self.root / "project.json").write_text(
            json.dumps(self.config, ensure_ascii=False), encoding="utf-8"
        )

    def invoke(self, args, factory, *, encoding="utf-8"):
        with chdir(self.workspace):
            return run_cli(args, factory, encoding=encoding)

    def invoke_piped(self, args):
        """Exercise the real entry point with a representative legacy output encoding."""
        return subprocess.run(
            [sys.executable, str(SKILL / "scripts" / "agentkit.py"), *args],
            cwd=self.workspace, capture_output=True, timeout=10,
            env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        )

    def make_board(self):
        board = FileBoard(self.workspace / "board")
        board.path("/main.py").write_bytes((SKILL / "board/main.py").read_bytes())
        return board

    def identity(self, device_id="01020304"):
        return (json.dumps({"device_id": device_id, "implementation": "micropython",
                            "version": "1.29.0", "machine": "ESP8266"}).encode(), b"", None)

    def test_copied_project_is_discovered_deployed_and_accepted(self):
        # Build real local history before handing the complete directory over.
        board = self.make_board()
        first_manifest = prepare_deploy(self.app).manifest_id
        deployed = self.invoke(["deploy", "--project", "ESP12屏幕"], board.connect)
        self.assertEqual(deployed.returncode, 0, deployed.stderr)
        accepted = self.invoke_piped([
            "accept", "--project", "ESP12屏幕", "--manifest", first_manifest,
            "--observation", "Developer confirmed the first display behavior.",
        ])
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        original_root = self.root
        # The receiver must use its own portable copy, including history and a BOM.
        (self.root / "project.json").write_text(
            json.dumps(self.config, ensure_ascii=False), encoding="utf-8-sig"
        )
        self.workspace /= "handoff"
        self.workspace.mkdir()
        (self.workspace / ".git").mkdir()
        self.root = Path(shutil.copytree(self.root, self.workspace / "屏幕项目"))
        self.app = self.root / "app"
        shutil.rmtree(original_root)

        listed = self.invoke_piped(["projects"])
        self.assertEqual(listed.returncode, 0, listed.stderr)
        projects = json.loads(listed.stdout.decode("ascii"))["projects"]
        self.assertEqual(len(projects), 1)
        for key in ("name", "aliases", "description"):
            self.assertEqual(projects[0][key], self.config[key])
        self.assertNotIn(b"testpass", listed.stdout + listed.stderr)

        history = [json.loads(path.read_text(encoding="utf-8"))
                   for path in (self.root / "records").glob("*.json")]
        self.assertEqual({record["kind"] for record in history}, {"deployment", "acceptance"})
        for record in history:
            self.assertEqual(prepare_deploy(self.root / record["source_snapshot"]).manifest_id,
                             first_manifest)
        # Change application behavior, not just an asset, in the receiving workspace.
        (self.app / "app.py").write_text("print('display twice')\nprint('display twice')\n", encoding="utf-8")
        expected = prepare_deploy(self.app)
        self.assertNotEqual(expected.manifest_id, first_manifest)
        refused = self.invoke_piped([
            "accept", "--project", "ESP12屏幕", "--manifest", expected.manifest_id,
            "--observation", "Old observations do not validate this new behavior.",
        ])
        self.assertEqual(refused.returncode, 2, refused.stderr)
        deployed = self.invoke(["deploy", "--project", projects[0]["name"]], board.connect, encoding="cp1252")
        self.assertEqual(deployed.returncode, 0, deployed.stderr)
        self.assertEqual(json.loads(deployed.stdout)["manifest_id"], expected.manifest_id)
        self.assertEqual(board.restarts, 2)
        record_event = json.loads(deployed.stderr.splitlines()[-1])
        self.assertEqual(record_event["event"], "deployment_record")
        record_path = Path(record_event["path"])
        self.assertEqual(record_path.parent, self.root / "records")
        receipt = json.loads(record_path.read_text(encoding="utf-8"))
        for flag in ("committed", "restart_acknowledged", "webrepl_authenticated"):
            self.assertTrue(receipt[flag])
        self.assertEqual(receipt["application_validation"], "not_checked")
        self.assertEqual(board.path("/agentkit_app/app.py").read_bytes(), (self.app / "app.py").read_bytes())
        self.assertEqual(prepare_deploy(self.root / "releases" / first_manifest).manifest_id, first_manifest)
        self.assertFalse(any(record.get("manifest_id") == expected.manifest_id
                             for record in history if record["kind"] == "acceptance"))

        snapshot = self.root / receipt["source_snapshot"]
        (self.app / "app.py").write_text("print('next version')\n", encoding="utf-8")
        observation = "图像已切换，颜色正常。🖥"
        accepted = self.invoke_piped([
            "accept", "--project", "ESP12屏幕", "--manifest", expected.manifest_id,
            "--observation", observation,
        ])
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        response = json.loads(accepted.stdout.decode("ascii"))
        self.assertEqual(response["project"], self.config["name"])
        self.assertEqual(response["manifest_id"], expected.manifest_id)
        acceptance = json.loads(Path(response["acceptance_record"]).read_text(encoding="utf-8"))
        self.assertEqual(acceptance["observation"], observation)
        self.assertEqual(acceptance["application_validation"], "developer_confirmed")
        self.assertEqual(prepare_deploy(snapshot).manifest_id, expected.manifest_id)
        self.assertEqual(len(list((self.root / "records").glob("*.json"))), 4)
        self.assertFalse((snapshot / "project.json").exists())
        self.assertNotIn("testpass", json.dumps(receipt) + json.dumps(acceptance))

    def test_project_probe_checks_identity_before_executing(self):
        session = Mock(spec=WebReplSession)
        session.execute.side_effect = [self.identity("deadbeef"), self.identity(), (b"42\n", b"", None)]
        factory = Mock(return_value=nullcontext(session))
        command = ["run", "--project", "ESP12屏幕", "probe.py"]
        rejected = self.invoke(command, factory)
        self.assertEqual(rejected.returncode, 5, rejected.stderr)
        self.assertEqual(session.execute.call_count, 1)
        self.assertEqual(rejected.stdout, "")

        result = self.invoke(command, factory)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "42\n")
        factory.assert_called_with("127.0.0.1", "testpass")
        self.assertEqual(session.execute.call_args.args[0], b"print(42)\n")

    def test_ambiguous_project_requires_selection_before_connecting(self):
        # A same-named directory must not bypass selection among matching projects.
        shutil.copytree(self.root, self.workspace / self.config["name"])
        session = Mock(spec=WebReplSession)
        session.execute.return_value = self.identity()
        factory = Mock(return_value=nullcontext(session))
        rejected = self.invoke(["info", "--project", self.config["name"]], factory)
        self.assertEqual(rejected.returncode, 5, rejected.stderr)
        self.assertIn("ambiguous", rejected.stderr)
        factory.assert_not_called()
        selected = self.invoke(["info", "--project", str(self.root)], factory)
        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertEqual(json.loads(selected.stdout)["device_id"], "01020304")

    def test_failed_or_cancelled_connection_records_uncertainty_and_refuses_acceptance(self):
        for failure, code, stage in (
            (LoginError("WebREPL rejected the password."), 3, "connect"),
            (KeyboardInterrupt(), 130, "cancelled"),
        ):
            with self.subTest(stage=stage):
                before = set((self.root / "records").glob("*.json"))
                result = self.invoke(["deploy", "--project", "ESP12屏幕"], Mock(side_effect=failure))
                self.assertEqual(result.returncode, code, result.stderr)
                created = set((self.root / "records").glob("*.json")) - before
                self.assertEqual(len(created), 1)
                raw = created.pop().read_text(encoding="utf-8")
                record = json.loads(raw)
                self.assertEqual(record["stage"], stage)
                self.assertIsNone(record["committed"])
                self.assertEqual(record["application_validation"], "not_checked")
                self.assertNotIn("testpass", raw + result.stdout + result.stderr)
                self.assertTrue((self.root / record["source_snapshot"] / "app.py").is_file())
                factory = Mock()
                refused = self.invoke([
                    "accept", "--project", "ESP12屏幕", "--manifest", record["manifest_id"],
                    "--observation", "Looks correct",
                ], factory)
                self.assertEqual(refused.returncode, 2, refused.stderr)
                self.assertIn("No confirmed successful deployment", refused.stderr)
                factory.assert_not_called()

    def test_committed_but_unconfirmed_restart_cannot_be_accepted(self):
        board = self.make_board()
        factory = Mock(side_effect=[board.connect("127.0.0.1", "testpass"), LoginError("offline")])
        # Installation completes, but the post-restart login times out.
        with patch("time.monotonic", side_effect=[0, 0, 36]), patch("time.sleep"):
            result = self.invoke(["deploy", "--project", "ESP12屏幕"], factory)
        self.assertEqual(result.returncode, 5, result.stderr)
        record = json.loads(next((self.root / "records").glob("*.json")).read_text(encoding="utf-8"))
        self.assertTrue(record["committed"])
        self.assertTrue(record["restart_acknowledged"])
        self.assertFalse(record["webrepl_authenticated"])
        refused = self.invoke([
            "accept", "--project", "ESP12屏幕", "--manifest", record["manifest_id"],
            "--observation", "Looks correct",
        ], Mock())
        self.assertEqual(refused.returncode, 2, refused.stderr)

    def test_modified_snapshot_cannot_be_accepted_or_redeployed(self):
        board = self.make_board()
        deployed = self.invoke(["deploy", "--project", "ESP12屏幕"], board.connect)
        self.assertEqual(deployed.returncode, 0, deployed.stderr)
        manifest = json.loads(deployed.stdout)["manifest_id"]
        (self.root / "releases" / manifest / "app.py").write_text("print('changed archive')\n", encoding="utf-8")
        factory = Mock()
        for command in (
            ["accept", "--project", "ESP12屏幕", "--manifest", manifest, "--observation", "Looks correct"],
            ["deploy", "--project", "ESP12屏幕"],
        ):
            with self.subTest(command=command[0]):
                result = self.invoke(command, factory)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("does not match", result.stderr)
        factory.assert_not_called()
        self.assertEqual(len(list((self.root / "records").glob("*.json"))), 1)

    def test_project_metadata_is_refused_before_upload(self):
        nested = self.app / "nested"
        nested.mkdir()
        shutil.copyfile(self.root / "project.json", nested / "project.json")
        factory = Mock()
        result = self.invoke(["deploy", "--project", "ESP12屏幕"], factory)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("testpass", result.stdout + result.stderr)
        factory.assert_not_called()
