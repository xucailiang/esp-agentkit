"""Run production entry points with temporary files and local transport substitutes."""

import importlib.util
import io
from pathlib import Path
import runpy
import subprocess
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import patch


SKILL = Path(__file__).resolve().parents[1] / "skill" / "esp-agentkit"
sys.path.insert(0, str(SKILL / "scripts"))


def run_cli(args, session_factory):
    """Exercise argument parsing, password input, output streams and process exit."""
    entry = str(SKILL / "scripts" / "agentkit.py")
    with (
        io.TextIOWrapper(io.BytesIO(b"testpass\n"), encoding="utf-8") as stdin,
        io.TextIOWrapper(io.BytesIO(), encoding="utf-8", write_through=True) as stdout,
        io.TextIOWrapper(io.BytesIO(), encoding="utf-8", write_through=True) as stderr,
        patch.object(sys, "argv", [entry, *args]),
        patch.object(sys, "stdin", stdin),
        patch.object(sys, "stdout", stdout),
        patch.object(sys, "stderr", stderr),
        patch("webrepl_session.open_session", session_factory),
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


def load_runtime(root, platform="esp8266"):
    """Load a fresh boot runtime with CPython adapters for MicroPython-only APIs."""
    spec = importlib.util.spec_from_file_location(
        "agentkit_runtime", SKILL / "board" / "agentkit_runtime.py"
    )
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    runtime.APP_DIR = str(root / "agentkit_app")
    runtime.MANIFEST = str(root / ".agentkit-manifest.json")
    runtime.PENDING = str(root / ".agentkit-deploying")

    def print_exception(exc, stream):
        for part in traceback.format_exception(exc):
            stream.write(part.encode("utf-8"))

    def execfile(path, namespace):
        exec(compile(Path(path).read_bytes(), path, "exec"), namespace)

    runtime.sys = SimpleNamespace(
        platform=platform, path=sys.path.copy(), print_exception=print_exception
    )
    if platform != "esp8266":
        runtime.execfile = execfile
    return runtime
