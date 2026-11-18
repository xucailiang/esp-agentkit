"""Discover local interfaces and dispatch bounded hardware identity probes."""

import json
from pathlib import Path
import subprocess
import sys


# Each backend is a separate process: vendor connection loops must not hang the CLI.
BACKENDS = {"esptool": Path(__file__).with_name("detect_espressif.py")}
RECOVERY = (
    "The probe may have reset the device or left it in download mode. "
    "Release BOOT/FLASH and reset it to resume its installed program."
)


def discover_interfaces():
    """List serial interfaces without opening them or inferring a target chip."""
    from serial.tools import list_ports

    interfaces = {}
    for port in list_ports.comports():
        usb = None
        if port.vid is not None and port.pid is not None:
            usb = {
                "vid": port.vid,
                "pid": port.pid,
                "serial_number": port.serial_number,
                "manufacturer": port.manufacturer,
                "product": port.product,
                "location": port.location,
            }
        interfaces[port.device] = {
            "transport": "serial",
            "port": port.device,
            "description": port.description,
            "usb": usb,
        }
    return [interfaces[name] for name in sorted(interfaces)]


def probe_backend(backend, port, timeout):
    return subprocess.run(
        [sys.executable, str(BACKENDS[backend]), "--port", port],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _valid_identity(result):
    if not isinstance(result, dict):
        return False
    chip = result.get("chip")
    return (
        isinstance(chip, dict)
        and isinstance(chip.get("family"), str)
        and bool(chip["family"])
        and isinstance(result.get("flash"), dict)
        and isinstance(result.get("reset"), dict)
        and isinstance(result.get("warnings"), list)
    )


def run_detection(port=None, list_only=False, backend="auto", timeout=20):
    report = {
        "schema_version": 1,
        "status": "interfaces_only",
        "interfaces": [],
        "backend": None,
        "device": None,
        "runtime": {
            "status": "not_checked",
            "reason": "ROM detection does not inspect installed firmware or its runtime.",
        },
        "capabilities": {
            "firmware": "not_checked",
            "filesystem": "not_checked",
            "network": "not_checked",
            "deployment": "not_checked",
        },
        "reset": {"status": "not_requested"},
        "warnings": [],
    }

    def fail(status, message, code):
        report.update(status=status, message=message)
        return report, code

    if backend != "auto" and backend not in BACKENDS:
        return fail("local_error", f"Unknown detection backend: {backend}.", 2)
    if port is not None and (
        not port.strip() or port != port.strip() or "://" in port
        or any(ord(char) < 32 or ord(char) == 127 for char in port)
    ):
        return fail("local_error", "Use a local serial port path or COM name, not a URL.", 2)
    try:
        report["interfaces"] = discover_interfaces()
    except ModuleNotFoundError:
        return fail("local_error", "Install the Skill's requirements.txt with this Python interpreter.", 2)
    except OSError as exc:
        return fail("local_error", f"Cannot enumerate serial interfaces: {exc}", 2)
    if list_only:
        return report, 0

    if port is None:
        candidates = [item for item in report["interfaces"] if item["usb"] is not None]
        if not candidates:
            return fail(
                "no_candidate",
                "No USB serial candidate found. Check the data cable and driver, "
                "or specify a known local serial interface with --port.",
                3,
            )
        if len(candidates) > 1:
            return fail(
                "selection_required",
                "Multiple USB serial candidates found. Select the intended device with --port; "
                "no interface was opened.",
                5,
            )
        port = candidates[0]["port"]

    # Only the Espressif protocol is implemented today. USB metadata selects a
    # candidate interface, not its chip family or carrier board.
    backend = "esptool" if backend == "auto" else backend
    report["backend"] = backend
    report["port"] = port
    report["reset"] = {"status": "unconfirmed"}
    print(f"detect: probing {port} with {backend}; this can reset the device.", file=sys.stderr)
    try:
        process = probe_backend(backend, port, timeout)
    except subprocess.TimeoutExpired:
        return fail("unconfirmed", f"Detection timed out after {timeout:g} seconds. {RECOVERY}", 5)
    except OSError as exc:
        report["reset"] = {"status": "not_requested"}
        return fail("local_error", f"Cannot start the {backend} backend: {exc}", 2)

    if process.stderr:
        print(process.stderr, end="" if process.stderr.endswith("\n") else "\n", file=sys.stderr)
    try:
        result = json.loads(process.stdout)
    except (ValueError, TypeError):
        result = None
    if process.returncode != 0:
        detail = result.get("error") if isinstance(result, dict) else None
        message = detail if isinstance(detail, str) else f"The {backend} backend did not confirm device identity."
        code = process.returncode if process.returncode in (2, 3) else 5
        if code == 2:
            report["reset"] = {"status": "not_requested"}
        else:
            message += " " + RECOVERY
        return fail("local_error" if code == 2 else "unconfirmed", message, code)
    if not _valid_identity(result):
        return fail("unconfirmed", f"The {backend} backend returned an incomplete identity. {RECOVERY}", 5)

    report.update(
        status="detected",
        device={"port": port, "board": None, "chip": result["chip"], "flash": result["flash"]},
        reset=result["reset"],
        warnings=result["warnings"],
    )
    return report, 0
