"""Flash verified ESP-IDF artifacts and retain serial running-version evidence."""

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import time
from uuid import uuid4

from native_build import artifact_path, verify_artifact
from operations import identity_key, resource_lock, run_process, serial_key


def write_json(path, data):
    staging = path.with_name(path.name + ".tmp")
    try:
        with staging.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def record_native(project, data):
    from project_records import _write_record

    return _write_record(project, {
        "backend": "esp-idf", "project": project.name,
        "device_id": project.device_id, **data,
    })


def accept_native(project, build_id, observation):
    if not observation.strip():
        raise ValueError("Supply the developer's actual observation.")
    # Serialize the evidence scan and receipt with every endpoint for this device.
    with resource_lock([identity_key("espressif-mac", project.device_id)], "accept"):
        return _accept_native_locked(project, build_id, observation)


def _accept_native_locked(project, build_id, observation):
    verify_artifact(project, build_id)
    confirmed = False
    flashed = False
    records = project.root / "records"
    if records.is_symlink():
        raise ValueError("Project records cannot be a symbolic link.")
    for path in sorted(records.glob("*.json")):
        if path.is_symlink():
            continue
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(item, dict) or item.get("backend") != "esp-idf" or item.get("device_id") != project.device_id:
            continue
        if item.get("kind") == "native_flash":
            # A later failed write makes earlier installed-version evidence stale.
            flashed = item.get("build_id") == build_id and item.get("flash_verified") is True
            confirmed = flashed and item.get("running_verified") is True and item.get("exit_code") == 0
        elif item.get("kind") == "native_monitor":
            if item.get("running_verified") is False:
                confirmed = False
            elif item.get("running_verified") is True:
                confirmed = flashed and item.get("build_id") == build_id and item.get("exit_code") == 0
    if not confirmed:
        raise ValueError("No current verified flash and running-version record matches this build and device.")
    return record_native(project, {
        "kind": "acceptance", "build_id": build_id,
        "artifact": "artifacts/" + build_id,
        "application_validation": "developer_confirmed", "observation": observation.strip(),
    })


def operate(project, build_id, port, *, flash=False, app_only=False, duration=30, timeout=180):
    """Keep a device lock across identity checking, writing, reset and observation."""
    verify_artifact(project, build_id)
    port = port or project.port
    if not port:
        raise ValueError("Supply --port or set device.port in the project.")
    operation_id = uuid4().hex
    records = project.root / "records"
    if records.is_symlink():
        raise ValueError("Project records cannot be a symbolic link.")
    logs = records / "serial"
    if logs.is_symlink():
        raise ValueError("Serial logs cannot be a symbolic link.")
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / (operation_id + ".log")
    result = {
        "kind": "native_flash" if flash else "native_monitor",
        "operation_id": operation_id, "build_id": build_id, "port": port,
        "artifact": "artifacts/" + build_id,
        "log": log.relative_to(project.root).as_posix(),
        "flash_verified": None, "reset_requested": False, "running_verified": None,
        "application_validation": "not_checked", "stage": "prepare",
    }
    code = 5
    local_stage = None
    keys = [serial_key(port), identity_key("espressif-mac", project.device_id)]
    with resource_lock(keys, "flash" if flash else "monitor"):
        if flash:
            # Survive a lost parent/worker or final receipt: earlier evidence is stale
            # before any interaction that may change the installed application.
            record_native(project, {**result, "stage": "pending"})
        with tempfile.TemporaryDirectory(prefix="agentkit-native-") as temporary:
            request_path = Path(temporary) / "request.json"
            response_path = Path(temporary) / "response.json"
            write_json(request_path, {
                "project": str(project.path), "build_id": build_id, "port": port,
                "device_id": project.device_id, "target": project.target,
                "flash": flash, "app_only": app_only, "duration": duration,
                "log": str(log), "response": str(response_path),
            })
            try:
                process = run_process(
                    [sys.executable, str(Path(__file__).resolve()), "--worker", str(request_path)],
                    cwd=project.root, timeout=timeout if flash else (duration + 15 if duration else None),
                    stdout=sys.stderr, stderr=sys.stderr,
                )
                code = process.returncode
            except subprocess.TimeoutExpired:
                local_stage = "timeout"
                result.update(stage="timeout", error="Operation timed out; device state remains unconfirmed.")
            except KeyboardInterrupt:
                code = 130
                local_stage = "cancelled"
                result.update(stage="cancelled", error="Local operation cancelled; inspect the device before retrying.")
            finally:
                if response_path.exists():
                    try:
                        saved = json.loads(response_path.read_text(encoding="utf-8"))
                        if isinstance(saved, dict):
                            result.update(saved)
                    except (OSError, ValueError):
                        result["response_error"] = "Worker result incomplete; device state remains unconfirmed."
                if local_stage:
                    result["stage"] = local_stage
                result["exit_code"] = code
                record = record_native(project, result)
    return {**result, "record": str(record)}, code


def _observe(connection, request, manifest, result, save):
    deadline = time.monotonic() + request["duration"] if request["duration"] else None
    pending = b""
    expected = manifest["app_elf_sha256"]
    result.update(stage="observe", running_verified=None)
    save()
    with Path(request["log"]).open("xb") as log:
        while deadline is None or time.monotonic() < deadline:
            chunk = connection.read(min(connection.in_waiting or 1, 4096))
            if not chunk:
                continue
            log.write(chunk)
            log.flush()
            sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()
            pending += chunk
            if len(pending) > 65536:
                pending = pending[-65536:]
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                marker = b"AGENTKIT_RUNNING "
                if not line.startswith(marker):
                    continue
                try:
                    evidence = json.loads(line[len(marker):])
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(evidence, dict):
                    continue
                result["running_evidence"] = {
                    key: evidence.get(key) for key in ("device_id", "elf_sha256", "version")
                }
                result["running_observed_at"] = datetime.now(timezone.utc).isoformat()
                result["running_verified"] = (
                    evidence.get("device_id") == result["device_id"]
                    and evidence.get("elf_sha256") == expected
                )
                save()
    result["stage"] = "complete"
    save()


def _flash_file(root, manifest, name):
    data = (root / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest["files"][name]["sha256"]:
        raise ValueError("Archived flash input changed before flashing.")
    return data


def _check_partitions(root, payloads, manifest, capacity, *, app_only=False):
    # The SDK identifies its partition table even when other images contain partition-like data.
    try:
        flasher = json.loads(_flash_file(root, manifest, "build/flasher_args.json"))
        table_offset = int(flasher["partition-table"]["offset"], 0)
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError("Cannot identify the partition table in the archived ESP-IDF flash layout.") from None
    table = next((data for address, data, _ in payloads if address == table_offset), b"")
    if not table.startswith(b"\xaa\x50"):
        raise ValueError("The partition table is missing or invalid at its archived flash offset.")

    partitions = []
    for cursor in range(0, len(table) - 31, 32):
        magic = table[cursor:cursor + 2]
        if magic in (b"\xeb\xeb", b"\xff\xff"):
            break
        if magic != b"\xaa\x50":
            raise ValueError("The archived partition table is invalid.")
        kind, start, size = struct.unpack_from("<BxII", table, cursor + 2)
        if not size or start + size > capacity:
            raise ValueError("An archived partition exceeds the configured flash capacity.")
        partitions.append((start, start + size, kind))
    partitions.sort()
    if any(a[1] > b[0] for a, b in zip(partitions, partitions[1:])):
        raise ValueError("The archived partitions overlap.")

    app_slots = {start: end for start, end, kind in partitions if kind == 0}
    apps = [(address, len(data)) for address, data, name in payloads if name == manifest["app_bin"]]
    if not apps:
        raise ValueError("The application image is missing from the flash layout.")
    for address, size in apps:
        if address not in app_slots or address + size > app_slots[address]:
            raise ValueError(f"The application image at {address:#x} does not fit its target application partition.")

    if not app_only:
        # SDK images may live wholly outside the table (e.g. the primary bootloader),
        # or start inside a partition. A write must not straddle either edge.
        for address, data, name in payloads:
            image_end = address + len(data)
            for start, end, _ in partitions:
                if address < start < image_end or address < end < image_end:
                    raise ValueError(
                        f"Archived image {name} ({address:#x}..{image_end:#x}) "
                        f"crosses a partition boundary ({start:#x}..{end:#x})."
                    )


def worker(request):
    import esptool
    import serial
    from projects import load_project

    result = {"device_id": request["device_id"], "stage": "prepare", "flash_verified": None,
              "reset_requested": False, "running_verified": None,
              "connection_effect": "opening the serial port may reset the board"}

    def save():
        write_json(Path(request["response"]), result)

    save()
    connection = None
    loader = None
    try:
        project = load_project(request["project"])
        if project.device_id != request["device_id"] or project.target != request["target"]:
            raise ValueError("The selected project device identity or target changed before connecting.")
        manifest = verify_artifact(project, request["build_id"])
        root = artifact_path(project, request["build_id"])
        result["stage"] = "connect"
        save()
        connection = serial.Serial(port=None, baudrate=115200, timeout=0.2, exclusive=True)
        connection.dtr = False
        connection.rts = False
        connection.port = request["port"]
        connection.open()
        if request["flash"]:
            result["stage"] = "identify"
            save()
            with redirect_stdout(sys.stderr):
                loader = esptool.detect_chip(connection, connect_attempts=2)
                actual_id = bytes(loader.read_mac()).hex()
                actual_target = loader.CHIP_NAME.lower().replace("-", "")
                if actual_id != project.device_id or actual_target != project.target:
                    raise ValueError("Connected device identity or target differs from the selected project.")
                if loader.get_secure_boot_enabled() or loader.get_flash_encryption_enabled():
                    raise ValueError("Secure Boot or Flash Encryption requires a separately verified flashing workflow.")
                esptool.attach_flash(loader)
                detected = esptool.detect_flash_size(loader)
                from detect_espressif import _flash_size_bytes
                capacity = _flash_size_bytes(detected)
                if capacity is None:
                    raise ValueError("Flash capacity could not be confirmed.")
                declared = _flash_size_bytes(manifest["flash_settings"]["flash_size"])
                if declared is None or declared > capacity:
                    raise ValueError("Archived flash capacity is unknown or exceeds the connected device.")
                payloads = []
                for offset, name in manifest["flash_files"].items():
                    data = _flash_file(root, manifest, name)
                    address = int(offset, 0)
                    if address < 0 or address + len(data) > declared:
                        raise ValueError("An archived image exceeds detected flash capacity.")
                    payloads.append((address, data, name))
                _check_partitions(root, payloads, manifest, declared, app_only=request["app_only"])
                payloads.sort()
                for current, following in zip(payloads, payloads[1:]):
                    if current[0] + len(current[1]) > following[0]:
                        raise ValueError("Archived flash ranges overlap.")
                loader = loader.run_stub()
                loader.change_baud(230400)
                if request["app_only"]:
                    for offset, data, name in payloads:
                        if name != manifest["app_bin"] and loader.flash_md5sum(offset, len(data)) != hashlib.md5(data).hexdigest():
                            raise ValueError("Existing bootloader or partition layout differs; use a reviewed full-image flash.")
                    payloads = [item for item in payloads if item[2] == manifest["app_bin"]]
                result.update(stage="write", target_verified=True, flash_capacity=capacity,
                              written_images=[{"offset": offset, "path": name, "size": len(data)}
                                              for offset, data, name in payloads])
                save()
                # Keep headers unchanged so archived hashes describe exact written bytes.
                esptool.write_flash(loader, [(offset, data) for offset, data, _ in payloads],
                                    flash_mode="keep", flash_freq="keep", flash_size="keep")
                esptool.verify_flash(loader, [(offset, data) for offset, data, _ in payloads])
                result.update(flash_verified=True, stage="reset")
                save()
                connection.baudrate = 115200
                connection.reset_input_buffer()
                loader.hard_reset()
                result["reset_requested"] = True
                save()
        _observe(connection, request, manifest, result, save)
        return 0 if result["running_verified"] is True else 5
    except Exception as exc:
        result.update(stage="failed", error=str(exc))
        save()
        return 5
    finally:
        # If checking failed in ROM, request a normal reset without claiming startup.
        if loader is not None and not result["reset_requested"]:
            try:
                with redirect_stdout(sys.stderr):
                    loader.hard_reset()
                result["reset_requested"] = True
                save()
            except Exception:
                pass
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    arguments = parser.parse_args()
    raise SystemExit(worker(json.loads(arguments.worker.read_text(encoding="utf-8"))))
