"""Install Wi-Fi and WebREPL on a supported MicroPython board over USB."""

import argparse
from contextlib import closing, redirect_stdout
import hashlib
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import re
import sys
import time

from credentials import read_hidden, read_webrepl_password

BOARD_FILES = Path(__file__).resolve().parent.parent / "board"
PENDING_CONFIG = ".agentkit-wifi-pending.json"
REPLACEABLE_HASHES = {
    "boot.py": {
        # Factory boot.py created by MicroPython v1.29.0 on ESP32 or ESP8266.
        "16f5b4bcb120e9a032242b47967e649a0cc577b41939e81ef7d4b4da181bd17f",
        "839e06589e61c1163a93544a39de1f5faa5253a06f49fe8b0a65c49bf81842d1",
        # First ESP Agent Kit bootstrap, as distributed with LF or CRLF.
        "c5d65d2891214130502bd59adca7dc60d29b2e7a8afe68d45a6638474678d4b7",
        "171dba8b24a968a0b4e3636cffb6a67c45f75c9844f2ac32e0c0810a3b70f346",
        # ESP32 bootstrap shipped before ESP8266 support.
        "bb3af60798665cc606f720dfb24b9007f8cd2c6a7c210998fb85853280b34951",
        "0c3b6c1b959c13a9f1660f05eada1208eb3c86cd7348d821b229374af0aeb598",
        # First ESP8266-compatible bootstrap, as installed on the test NodeMCU.
        "510134c0af55376118f206f6991bf48e480c776d4126f47a1ec57dd7a7224707",
        "ff86de918961c502619560badf9feecbee7c28b7c73c8bea2889586f32f20821",
    },
    "agentkit_runtime.py": {
        # Launcher runtime shipped before ESP8266 support.
        "c06858520919c86cc72357c5a2ac2866aaf666f6cc600c0922d3d6fe22a80764",
        "683c17c9033b19e92771d87ad60693043813a92020eddb556c8f69912d654847",
    },
    "main.py": {
        # The empty startup entry shipped with the USB and wireless-debugging phases.
        "5c4b90082fd566bd7860b6b6143b5d606988d57a89cbc1633df1a3f63488a2ee",
        "95977f4a38f36ee3b91c84aa7fb226e1de18d43899ba5a2ce73daabccd65ba52",
    },
}
BOOT_ERRORS = {
    b"config_invalid": "The board rejected its configuration. Repeat USB setup.",
    b"config_commit_failed": "The board could not save the verified configuration. Check free space over USB and retry.",
    b"wifi_auth_failed": "Wi-Fi authentication failed with the new credentials. Check the network name and password, then repeat USB setup.",
    b"wifi_failed": "Wi-Fi could not start. Check the network settings and repeat USB setup.",
    b"webrepl_failed": "WebREPL did not start. Check the board firmware and repeat USB setup.",
}


def read_credentials():
    ssid = read_hidden("Wi-Fi SSID (hidden): ")
    wifi = read_hidden("Wi-Fi password (leave empty for an open network): ")
    if not 1 <= len(ssid.encode("utf-8")) <= 32 or "\0" in ssid:
        raise RuntimeError("A Wi-Fi SSID must contain 1-32 UTF-8 bytes and no NUL.")
    if len(wifi.encode("utf-8")) > 64 or "\0" in wifi:
        raise RuntimeError("The Wi-Fi password exceeds the supported size or contains a NUL.")
    password = read_webrepl_password()
    while password == wifi:
        print("Choose a different password for WebREPL. Your Wi-Fi settings are already entered.",
              file=sys.stderr)
        password = read_webrepl_password()
    return {"ssid": ssid, "wifi_password": wifi, "webrepl_password": password}


def identify_board(board):
    board.exec("import sys, os, esp, binascii, machine")
    implementation, version, build, machine_name, flash_size = board.eval(
        "(sys.implementation.name, sys.implementation.version[:3], getattr(sys.implementation, '_build', None), "
        "os.uname().machine, esp.flash_size())"
    )
    if implementation != "micropython" or version != (1, 29, 0) or flash_size != 4 * 1024 * 1024:
        raise RuntimeError(
            f"Expected MicroPython v1.29.0 and 4 MiB flash. Detected {build!r}, "
            f"version {version}, {flash_size} flash bytes. Check the firmware for your board."
        )
    if build == "ESP32_GENERIC-OTA" and machine_name == "Generic ESP32 module with OTA with ESP32":
        board.exec("from esp32 import Partition")
        layout = board.eval("[Partition(name).info()[2:4] for name in ('ota_0', 'ota_1', 'vfs')]")
        if layout != [(0x10000, 0x180000), (0x190000, 0x180000), (0x310000, 0xF0000)]:
            raise RuntimeError("The flash layout does not match the ESP32 OTA profile. Check the installed image.")
        board_name = "ESP32-DevKitC V4 / ESP32-WROOM-32E"
        firmware = "MicroPython v1.29.0 ESP32_GENERIC OTA"
    elif build == "ESP8266_GENERIC" and machine_name == "ESP module with ESP8266":
        board_name = "NodeMCU V3 / ESP-12F"
        firmware = "MicroPython v1.29.0 ESP8266_GENERIC"
    else:
        raise RuntimeError(
            f"Unsupported firmware {build!r} on {machine_name}. "
            "Use ESP32_GENERIC OTA for DevKitC V4 or standard ESP8266_GENERIC for ESP-12F."
        )
    return {
        "board": board_name,
        "device_id": board.eval("binascii.hexlify(machine.unique_id()).decode()"),
        "flash_mib": flash_size // (1024 * 1024),
        "firmware": firmware,
    }


def startup_updates(board):
    existing = {entry.name for entry in board.fs_listdir("/")}
    updates = {}
    for name in ("agentkit_runtime.py", "main.py", "boot.py"):
        content = (BOARD_FILES / name).read_bytes().replace(b"\r\n", b"\n")
        if name in existing:
            current = board.fs_hashfile("/" + name, "sha256").hex()
            if current in (
                hashlib.sha256(content).hexdigest(),
                hashlib.sha256(content.replace(b"\n", b"\r\n")).hexdigest(),
            ):
                continue
            if current not in REPLACEABLE_HASHES.get(name, ()):
                raise RuntimeError(
                    f"Existing /{name} was customized. Back it up and move it aside "
                    "over USB before retrying. Reset the board to restart the current application."
                )
        updates[name] = content
    if "main.py" in updates and "agentkit_runtime.py" not in existing and "agentkit_app" in existing:
        raise RuntimeError(
            "This board already has /agentkit_app. Back up and move it aside before installing "
            "the launcher, which gives deploy ownership of that directory."
        )
    return updates


def install_file(board, name, data):
    target = "/" + name
    staging = "/.agentkit-setup-" + name + ".tmp"
    board.fs_writefile(staging, data)
    if board.fs_hashfile(staging, "sha256") != hashlib.sha256(data).digest():
        raise RuntimeError(f"USB verification failed for {name}. Repeat USB setup.")
    board.exec(f"os.rename({staging!r}, {target!r})")


def await_boot(board):
    board.exec_raw_no_follow("import machine, time\ntime.sleep_ms(100)\nmachine.reset()")
    board.serial.timeout = 0.25
    for attempt in range(2):
        if attempt:
            print("No boot status yet. Reset the board with BOOT/FLASH released.", file=sys.stderr)
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            line = board.serial.readline(256).strip()
            pending = re.fullmatch(rb"ESP_AGENTKIT_PENDING ((?:[0-9a-f]{2}:){5}[0-9a-f]{2})", line)
            if pending:
                mac = pending[1].decode("ascii")
                raise RuntimeError(
                    f"Wi-Fi setup is unconfirmed (station MAC {mac}). "
                    "When the router shows its DHCP lease, run "
                    "uv run --project skill/esp-agentkit --locked python "
                    "skill/esp-agentkit/scripts/verify_webrepl.py --address <assigned-ip> "
                    "from the repository root. "
                    "If no lease appears, check the Wi-Fi settings and repeat USB setup."
                )
            if line.startswith(b"ESP_AGENTKIT_ERROR "):
                reason = line.split(maxsplit=1)[1]
                raise RuntimeError(BOOT_ERRORS.get(reason, "Unknown boot failure. Inspect the USB REPL."))
            if line.startswith(b"ESP_AGENTKIT_READY "):
                try:
                    return str(IPv4Address(line.split(maxsplit=1)[1].decode("ascii")))
                except ValueError:
                    raise RuntimeError("The board reported an invalid IPv4 address. Check DHCP.") from None
    raise RuntimeError("No boot status received. Check the USB cable, board firmware and power, then retry.")


def provision(port, keep_config=False):
    from mpremote.transport import TransportError
    from mpremote.transport_serial import SerialTransport
    from webrepl_session import open_session

    if "://" in port:
        raise RuntimeError("Select a local USB serial device path or COM port.")
    # Leave the foreground app running until the operator finishes the local prompts.
    if keep_config:
        password = read_webrepl_password()
    else:
        config = read_credentials()
        password = config["webrepl_password"]
    # mpremote can print arbitrary board bytes on a failed raw-REPL negotiation.
    with open(os.devnull, "w") as discard, redirect_stdout(discard):
        try:
            transport = SerialTransport(port, timeout=2)
        except (TransportError, OSError, ValueError):
            raise RuntimeError(
                "Cannot open the selected USB serial port. Run python -m serial.tools.list_ports -v "
                "before and after connecting the board. If the port appears, close other serial "
                "clients and check access permissions. If it is missing, see "
                "'Find the board's serial port' in SKILL.md for cable and driver checks."
            ) from None
        step = "entering the USB REPL and checking the board firmware"
        try:
            with closing(transport) as board:
                board.enter_raw_repl(soft_reset=False)
                identity = identify_board(board)
                print(
                    f"{identity['board']}, {identity['firmware']}, device ID {identity['device_id']}.",
                    file=sys.stderr,
                )
                step = "checking the existing startup files"
                updates = startup_updates(board)
                if keep_config:
                    names = {entry.name for entry in board.fs_listdir("/")}
                    if "agentkit_config.json" in names:
                        if PENDING_CONFIG in names:
                            board.fs_rmfile("/" + PENDING_CONFIG)
                            print("Discarded the pending Wi-Fi settings. Using the saved configuration.", file=sys.stderr)
                    elif PENDING_CONFIG in names:
                        print("Retrying the pending Wi-Fi configuration from the last USB setup.", file=sys.stderr)
                    else:
                        raise RuntimeError("No saved Wi-Fi configuration. Repeat setup without --keep-config.")
                else:
                    updates[PENDING_CONFIG] = json.dumps(config, ensure_ascii=False).encode("utf-8")
                for name, content in updates.items():
                    step = f"installing {name}"
                    install_file(board, name, content)
                step = "restarting the board"
                print("Setup files installed. Waiting for Wi-Fi after reset.", file=sys.stderr)
                address = await_boot(board)
        except (TransportError, OSError, ValueError, SyntaxError):
            # Raw serial responses can contain existing user data or credentials.
            raise RuntimeError(
                f"USB setup failed while {step}. Check the port and board firmware, then retry."
            ) from None
    print(f"Checking WebREPL login at {address}:8266.", file=sys.stderr)
    try:
        with open_session(address, password):
            pass
    except RuntimeError as exc:
        raise RuntimeError(
            f"{exc} Configuration is installed. Retry from the repository root with "
            "'uv run --project skill/esp-agentkit --locked python "
            f"skill/esp-agentkit/scripts/verify_webrepl.py --address {address}'."
        ) from exc
    return {
        **identity,
        "address": address,
        "webrepl_port": 8266,
        "webrepl_authenticated": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="local USB serial device path or COM port")
    parser.add_argument(
        "--keep-config", action="store_true",
        help="use the saved network settings, or retry pending settings if this is the first setup",
    )
    args = parser.parse_args()
    try:
        record = provision(args.port, args.keep_config)
    except ModuleNotFoundError:
        parser.exit(
            1, "From the repository root, run 'uv sync --project skill/esp-agentkit --locked', "
            "then retry this tool with 'uv run --project skill/esp-agentkit --locked python'.\n"
        )
    except RuntimeError as exc:
        parser.exit(1, f"{exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "USB setup interrupted. Repeat setup to verify the installed configuration.\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
