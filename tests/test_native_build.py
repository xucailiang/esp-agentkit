"""Build, retain and hand off native releases without an installed SDK or hardware."""

from contextlib import redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from support import SKILL
from native_build import artifact_path, build_project, verify_artifact


FAKE_IDF = r'''
import json, os, re, struct, subprocess
from pathlib import Path
import sys, time

source = Path.cwd()
build = Path(sys.argv[sys.argv.index('-B') + 1])
build.mkdir(parents=True, exist_ok=True)
action = sys.argv[-1]
sdk = Path(os.environ['IDF_PATH'])
assert not any(argument.startswith('-DPROJECT_VER=') for argument in sys.argv)
if action == 'reconfigure':
    (source / 'sdkconfig').write_text('CONFIG_IDF_TARGET="esp32"\n'
                                    'CONFIG_ESP_WIFI_WPS_PASSPHRASE=y\n'
                                    + os.environ.get('TEST_SECURITY_SETTINGS', ''))
    (source / 'dependencies.lock').write_text('camera: pinned-test-version\n')
    dependency = source / 'managed_components' / 'camera'
    (dependency / 'private').mkdir(parents=True, exist_ok=True)
    (dependency / 'CMakeLists.txt').write_text('idf_component_register(SRCS camera.c)\n')
    (dependency / 'private' / 'camera.h').write_text('#define CAMERA_VERSION 1\n')
    (dependency / 'camera.c').write_text('int camera(void) { return 1; }\n')
    component = os.environ.get('TEST_EXTERNAL_COMPONENT', str(source / 'main'))
    description = {
        'target': 'esp32', 'app_bin': 'camera.bin', 'app_elf': 'camera.elf',
        'project_version': re.search(
            r'set\(PROJECT_VER "([^"]+)"\)', (source / 'CMakeLists.txt').read_text()).group(1),
        'config_file': str(source / 'sdkconfig'),
        'build_component_paths': [component, str(sdk / 'components' / 'core'), ''],
    }
    (build / 'project_description.json').write_text(json.dumps(description))
    (build / 'compile_commands.json').write_text(json.dumps([
        {'file': str(source / 'main' / 'main.c'), 'directory': str(build)}
    ]))
elif action == 'build':
    if os.environ.get('TEST_FAIL_BUILD'):
        print('synthetic compiler failure', flush=True)
        sys.exit(7)
    if os.environ.get('TEST_SLOW_BUILD'):
        time.sleep(30)
    if os.environ.get('TEST_CHANGED_INPUT'):
        (source / 'main' / 'main.c').write_text('changed while compiling')
    contents = subprocess.check_output(['./generate.sh'], cwd=source)
    (build / 'camera.elf').write_bytes(b'ELF' + contents)
    (build / 'camera.bin').write_bytes(b'BIN' + contents)
    (build / 'camera.map').write_text('synthetic map\n')
    (build / 'bootloader').mkdir(exist_ok=True)
    (build / 'bootloader' / 'bootloader.bin').write_bytes(b'BOOT')
    (build / 'partition_table').mkdir(exist_ok=True)
    table = struct.pack('<HBBII16sI', 0x50AA, 0, 0, 0x10000, 0x100000, b'factory', 0)
    (build / 'partition_table' / 'partition-table.bin').write_bytes(table + b'\xff' * 32)
    (build / 'flasher_args.json').write_text(json.dumps({
        'partition-table': {'offset': '0x8000', 'file': 'partition_table/partition-table.bin'},
        'flash_files': {'0x1000': 'bootloader/bootloader.bin',
                        '0x8000': 'partition_table/partition-table.bin', '0x10000': 'camera.bin'},
        'flash_settings': {'flash_mode': 'dio', 'flash_size': '4MB', 'flash_freq': '40m'},
        'extra_esptool_args': {'chip': 'esp32'},
    }))
print('finished ' + action)
'''


@unittest.skipIf(os.name == "nt", "Managed native tool execution on Windows is not yet supported.")
class NativeBuildTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        root = self.workspace / "camera-project"
        source = root / "firmware"
        (source / "main").mkdir(parents=True)
        (source / "CMakeLists.txt").write_text('set(PROJECT_VER "1.0.0")\nproject(camera)\n', encoding="utf-8")
        (source / "main" / "main.c").write_text("int app_version = 1;\n", encoding="utf-8")
        generator = source / "generate.sh"
        generator.write_text("#!/bin/sh\ncat main/main.c managed_components/camera/private/camera.h\n")
        generator.chmod(0o755)
        (root / "project.json").write_text('{"private": "testpass"}\n')
        self.project = SimpleNamespace(root=root, source=source, target="esp32",
                                       backend="esp-idf", idf_version="v5.5.1")
        self.sdk = self.workspace / "sdk"
        (self.sdk / "tools").mkdir(parents=True)
        (self.sdk / "tools" / "idf.py").write_text(FAKE_IDF, encoding="utf-8")
        (self.sdk / "components" / "core").mkdir(parents=True)
        self.environment = {**os.environ, "IDF_PATH": str(self.sdk)}
        self.sdk_record = {"version": "v5.5.1", "commit": "a" * 40}
        self.enterContext(patch("native_build._sdk_environment", side_effect=self.export_sdk))

    def export_sdk(self, project, idf_path, overrides):
        return self.sdk, Path(sys.executable), {**self.environment, **(overrides or {})}, self.sdk_record

    def build(self, **kwargs):
        return build_project(self.project, self.sdk, **kwargs)

    def published(self):
        return [path for path in (self.project.root / "artifacts").glob("*")
                if path.is_dir() and len(path.name) == 64]

    def test_build_archive_survives_source_change_and_complete_project_handoff(self):
        source = self.project.source
        for private in (source / "private", source / "config" / "private"):
            private.mkdir(parents=True)
            (private / "wifi.txt").write_text("synthetic private configuration")
        (source / "config" / "CMakeLists.txt").write_text("set(EXAMPLE 1)\n")
        local = source / "drivers" / "sensor"
        (local / "private").mkdir(parents=True)
        (local / "CMakeLists.txt").write_text('idf_component_register(PRIV_INCLUDE_DIRS "private")\n')
        (local / "private" / "sensor.h").write_text("#define SENSOR 1\n")
        first = self.build()
        first_id = first["build_id"]
        original = artifact_path(self.project, first_id)
        manifest = verify_artifact(self.project, first_id)
        self.assertEqual(manifest["project_version"], "1.0.0")
        self.assertEqual(manifest["flash_files"]["0x10000"], "build/camera.bin")
        self.assertEqual(manifest["app_elf_sha256"], hashlib.sha256(
            (original / "build" / "camera.elf").read_bytes()).hexdigest())
        self.assertIn("source/managed_components/camera/camera.c", manifest["files"])
        self.assertIn("source/dependencies.lock", manifest["files"])
        self.assertIn("source/sdkconfig", manifest["files"])
        self.assertEqual(manifest["environment_reconstruction"], "not_checked")
        self.assertFalse((original / "source" / "project.json").exists())
        self.assertFalse((original / "source" / "private").exists())
        self.assertFalse((original / "source" / "config" / "private").exists())
        self.assertIn("source/drivers/sensor/private/sensor.h", manifest["files"])
        generated = subprocess.check_output(["./generate.sh"], cwd=original / "source")
        self.assertEqual((original / manifest["app_elf"]).read_bytes(), b"ELF" + generated)
        self.assertFalse((self.project.source / "sdkconfig").exists())

        (self.project.source / "main" / "main.c").write_text("int app_version = 2;\n")
        second = self.build()
        self.assertNotEqual(second["build_id"], first_id)
        self.assertEqual((original / "source" / "main" / "main.c").read_text(), "int app_version = 1;\n")
        self.assertNotEqual(second["app_elf_sha256"], first["app_elf_sha256"])
        destination = self.workspace / "receiving-workspace" / "camera"
        shutil.copytree(self.project.root, destination)
        shutil.rmtree(self.project.root)
        self.project.root = destination
        self.project.source = destination / "firmware"
        received = verify_artifact(self.project, first_id)
        self.assertEqual(received, manifest)
        rebuilt = self.build()
        self.assertEqual(rebuilt["project_version"], "1.0.0")
        self.assertEqual(rebuilt["app_elf_sha256"], second["app_elf_sha256"])

    def test_failed_builds_never_publish_and_unsafe_imported_archives_are_refused(self):
        for environment, timeout, error in (
            ({"TEST_FAIL_BUILD": "1"}, 10, ValueError),
            ({"TEST_SLOW_BUILD": "1"}, 0.3, subprocess.TimeoutExpired),
            ({"TEST_CHANGED_INPUT": "1"}, 10, ValueError),
        ):
            with self.subTest(environment=environment), redirect_stderr(io.StringIO()):
                with self.assertRaises(error):
                    self.build(timeout=timeout, env=environment)
                self.assertEqual(self.published(), [])
        # Even a byte-consistent imported archive must stay within the ordinary
        # development workflow's first-boot policy before flashing is allowed.
        current = self.build()
        archive = artifact_path(self.project, current["build_id"])
        manifest = verify_artifact(self.project, current["build_id"])
        config = archive / manifest["sdkconfig"]
        config.write_text(config.read_text() + "CONFIG_SECURE_FLASH_ENC_ENABLED=y\n")
        data = config.read_bytes()
        manifest["files"][manifest["sdkconfig"]] = {
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        }
        manifest.pop("build_id")
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        restricted_id = hashlib.sha256(encoded).hexdigest()
        manifest["build_id"] = restricted_id
        (archive / "manifest.json").write_text(json.dumps(manifest))
        archive.rename(archive.parent / restricted_id)
        with self.assertRaisesRegex(ValueError, "Secure Boot"):
            verify_artifact(self.project, restricted_id)

    def test_external_components_and_unsafe_configuration_are_refused(self):
        config = self.project.source / "sdkconfig.defaults"
        config.write_text('CONFIG_ESP_WIFI_PASSWORD="not-for-the-archive"\n')
        with self.assertRaisesRegex(ValueError, "credentials"):
            self.build()
        self.assertEqual(self.published(), [])
        config.unlink()
        for setting in ("CONFIG_SECURE_BOOT", "CONFIG_SECURE_DISABLE_ROM_DL_MODE"):
            with self.subTest(setting=setting), redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "Secure Boot"):
                    self.build(env={"TEST_SECURITY_SETTINGS": setting + "=y\n"})
                self.assertEqual(self.published(), [])
        with redirect_stderr(io.StringIO()), self.assertRaisesRegex(ValueError, "External component") as rejected:
            self.build(env={"TEST_EXTERNAL_COMPONENT": str(self.workspace / "outside")})
        self.assertIn(str(self.workspace / "outside"), str(rejected.exception))
        self.assertEqual(self.published(), [])
        (self.project.source / "linked.c").symlink_to(self.project.source / "main" / "main.c")
        with self.assertRaisesRegex(ValueError, "regular files"):
            self.build()


if __name__ == "__main__":
    unittest.main()
