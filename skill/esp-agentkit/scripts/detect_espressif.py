"""Inspect an Espressif ROM loader without uploading code or writing flash."""

import argparse
from contextlib import redirect_stdout
import json
import sys


CONNECT_ATTEMPTS = 2
INVALID_FLASH_IDS = (0x000000, 0xFFFFFF, 0xFFFF3F)


def _flash_size_bytes(value):
    if value is None:
        return None
    for suffix, scale in (("KB", 1024), ("MB", 1024 * 1024)):
        if value.endswith(suffix):
            return int(value[:-len(suffix)]) * scale
    raise ValueError(f"Unrecognized flash size from esptool: {value!r}.")


def _probe(port):
    import esptool
    from esptool.reset import HardReset
    import serial

    if not port or "://" in port:
        raise ValueError("Select a local USB serial device path or COM port.")

    warnings = []
    result = {
        "chip": None,
        "flash": {"id": None, "size_bytes": None, "status": "unconfirmed"},
        "reset": {"status": "unconfirmed"},
        "warnings": warnings,
    }
    loader = None
    # Own the serial object so detection failures cannot leave it open. esptool
    # 5.1 accepts this object throughout detect_chip(), despite its str annotation.
    connection = serial.serial_for_url(port, exclusive=True, do_not_open=True)
    try:
        if sys.platform == "win32":
            connection.rts = False
            connection.dtr = False
        connection.open()
        loader = esptool.detect_chip(connection, connect_attempts=CONNECT_ATTEMPTS)
        if loader.IS_STUB:
            raise RuntimeError("The device is running a flasher stub; a ROM connection was not confirmed.")
        chip = {
            "family": loader.CHIP_NAME,
            "description": loader.CHIP_NAME,
            "features": [],
            "mac": None,
        }
        result["chip"] = chip
        for field, read in (
            ("description", loader.get_chip_description),
            ("features", loader.get_chip_features),
            ("mac", loader.read_mac),
        ):
            try:
                value = read()
                if field == "mac":
                    value = ":".join(f"{byte:02x}" for byte in value) if value else None
                elif field == "features":
                    value = list(value)
                chip[field] = value
            except Exception as exc:
                warnings.append(f"Chip {field} could not be read: {exc}")

        try:
            esptool.attach_flash(loader)
            flash_id = loader.flash_id()
            if flash_id in INVALID_FLASH_IDS:
                raise ValueError(f"The flash returned an invalid ID: 0x{flash_id:06x}.")
            result["flash"]["id"] = f"0x{flash_id:06x}"
            size = _flash_size_bytes(esptool.detect_flash_size(loader))
            if size is None:
                warnings.append("The flash ID was read, but its capacity was not recognized by esptool.")
            else:
                result["flash"].update(size_bytes=size, status="detected")
        except Exception as exc:
            warnings.append(f"Flash information could not be confirmed: {exc}")
    finally:
        try:
            if connection.is_open:
                if loader is None:
                    HardReset(connection)()
                else:
                    loader.hard_reset()
                # RTS being toggled does not confirm that the application booted.
                result["reset"]["status"] = "requested"
            else:
                warnings.append(
                    "The serial connection was closed before a reset could be requested. "
                    "Reset the board manually."
                )
        except Exception as exc:
            warnings.append(f"Reset could not be requested: {exc}. Reset the board manually.")
        finally:
            try:
                connection.close()
            except Exception as exc:
                warnings.append(f"The serial port could not be closed cleanly: {exc}")
        # Preserve cleanup diagnostics even when detection itself raised an error.
        if result["chip"] is None:
            for warning in warnings:
                print(warning, file=sys.stderr)
    return result


def probe(port):
    """Return chip identity and best-effort flash information from a local port."""
    # esptool's normal logger writes to stdout. Keep the worker's stdout JSON-only.
    with redirect_stdout(sys.stderr):
        return _probe(port)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="local USB serial device path or COM port")
    args = parser.parse_args(argv)
    try:
        result = probe(args.port)
    except ModuleNotFoundError as exc:
        print(json.dumps({
            "error": f"Missing dependency {exc.name!r}. From the repository root, "
                     "run 'uv sync --project skill/esp-agentkit --locked', then retry this tool "
                     "with 'uv run --project skill/esp-agentkit --locked python'."
        }))
        return 2
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 3
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
