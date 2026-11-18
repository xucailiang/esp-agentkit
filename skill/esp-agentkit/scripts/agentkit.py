"""Detect local devices, then debug and deploy MicroPython applications over WebREPL."""

import argparse
from ipaddress import IPv4Address
import json
import math
from pathlib import Path
import sys
import time

from credentials import read_webrepl_password

INFO_SOURCE = b"""\
import binascii, json, machine, os, sys
if 'agentkit_runtime.py' not in os.listdir('/'):
    deployment = {'state': 'setup_required', 'manifest_id': None, 'boot_error': None}
else:
    try:
        import agentkit_runtime
        deployment = agentkit_runtime.status()
    except Exception as exc:
        deployment = {'state': 'invalid', 'manifest_id': None, 'detail': str(exc)}
print(json.dumps({
    'implementation': sys.implementation.name,
    'version': '%d.%d.%d' % sys.implementation.version[:3],
    'machine': os.uname().machine,
    'device_id': binascii.hexlify(machine.unique_id()).decode(),
    'deployment': deployment,
}))
"""

EXIT_CODES = (
    "Exit codes: 0 confirmed, 2 arguments or local input, 3 target not found, connection or login, "
    "4 board Python exception, 5 target selection required, deployment refused or operation unconfirmed, "
    "130 cancelled."
)


def positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("expected a finite number of seconds greater than zero")
    return seconds


def read_probe(path):
    try:
        source = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(f"{path}: expected a UTF-8 Python source file") from None
    if not source.strip():
        raise ValueError(f"{path}: the probe is empty")
    if any(ord(char) < 32 and char not in "\t\r\n" for char in source):
        raise ValueError(f"{path}: use Python escapes for control characters in REPL source")
    return source.encode("utf-8")


def board_info(output, address):
    try:
        info = json.loads(output)
        fields = ("implementation", "version", "machine", "device_id")
        if isinstance(info, dict) and all(isinstance(info.get(k), str) and info[k] for k in fields):
            return {**info, "address": str(address)}
    except ValueError:
        pass
    raise ValueError("Incomplete board identity. Check the firmware and retry info.")


def arguments():
    parser = argparse.ArgumentParser(description=__doc__, epilog=EXIT_CODES)
    commands = parser.add_subparsers(dest="command", required=True)
    detect = commands.add_parser(
        "detect", help="Discover serial interfaces and identify an Espressif chip over USB.",
        description="Identify a chip with esptool. Probing can reset the device; it does not erase or write flash.",
    )
    selection = detect.add_mutually_exclusive_group()
    selection.add_argument("--list", dest="list_only", action="store_true",
                           help="list serial interfaces without opening them")
    selection.add_argument("--port", help="local serial port; otherwise select the only USB serial candidate")
    detect.add_argument("--backend", choices=("auto", "esptool"), default="auto",
                        help="detection backend (default: auto; currently esptool only)")
    detect.add_argument("--timeout", type=positive_seconds, default=20, metavar="SECONDS",
                        help="time limit for the hardware probe (default: 20 seconds)")
    for name, description in (
        ("info", "Interrupt the foreground program and read board identity and deployment status."),
        ("run", "Interrupt the foreground program and execute a short UTF-8 Python probe."),
        ("stop", "Interrupt the foreground program and confirm the interactive prompt."),
        ("put", "Interrupt the foreground program and upload one trial file."),
        ("deploy", "Install a complete application directory and restart the board."),
    ):
        cmd = commands.add_parser(name, help=description, description=description)
        cmd.add_argument("--address", required=True, type=IPv4Address, help="board's LAN IPv4 address")
        cmd.add_argument("--password-stdin", action="store_true", help="read a password line from a pipe")
        if name in ("put", "deploy"):
            cmd.add_argument("--device-id", required=True, help="device ID from this project's USB record")
        if name in ("run", "put"):
            cmd.add_argument("file", type=Path, help="local file")
        if name == "run":
            cmd.add_argument("--timeout", type=positive_seconds, default=10, metavar="SECONDS",
                             help="time budget for the probe (default: 10 seconds)")
        if name == "deploy":
            cmd.add_argument("directory", type=Path, help="application directory with app.py and its dependencies")
    return parser.parse_args()


def report_probe(session, args, source):
    output, traceback, cleanup_error = session.execute(source, timeout=getattr(args, "timeout", 10))
    if args.command == "run":
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
    if traceback:
        sys.stderr.buffer.write(traceback)
    elif args.command == "info":
        try:
            print(json.dumps(board_info(output, args.address)))
        except ValueError as exc:
            print(f"info: {exc}", file=sys.stderr)
            return 5
    if cleanup_error:
        print(f"{args.command}: {cleanup_error}", file=sys.stderr)
        return 5
    return 4 if traceback else 0


def wait_for_login(address, password):
    from webrepl_session import LoginError, open_session

    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        try:
            with open_session(address, password):
                return True
        except LoginError:
            time.sleep(1)
    return False


def main():
    args = arguments()
    if args.command == "detect":
        from detection import run_detection

        try:
            result, code = run_detection(args.port, args.list_only, args.backend, args.timeout)
        except KeyboardInterrupt:
            print("Local detection cancelled. The device may be in download mode; "
                  "release BOOT/FLASH and reset it to resume its program.", file=sys.stderr)
            return 130
        print(json.dumps(result))
        if "message" in result:
            print(f"detect: {result['message']}", file=sys.stderr)
        return code
    try:
        from deployment import DeploymentError, deploy, prepare_deploy, prepare_put, put
        from webrepl_session import BoardError, LoginError, SessionError, open_session

        if args.command == "deploy":
            bundle = prepare_deploy(args.directory)
            print(f"Target manifest {bundle.manifest_id}", file=sys.stderr)
        elif args.command == "put":
            trial = prepare_put(args.file)
        elif args.command == "run":
            source = read_probe(args.file)
        password = read_webrepl_password(from_stdin=args.password_stdin)
    except ModuleNotFoundError:
        print("Install the Skill's requirements.txt with this Python interpreter.", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    address = str(args.address)
    committed = None
    try:
        with open_session(address, password) as session:
            print(f"{args.command}: sending an interrupt to the foreground interpreter.", file=sys.stderr)
            if args.command == "stop":
                session.stop()
                print(json.dumps({"address": address, "repl_ready": True}))
                return 0
            if args.command in ("run", "info"):
                return report_probe(session, args, source if args.command == "run" else INFO_SOURCE)
            with session.filesystem() as board:
                if args.command == "put":
                    uploaded = put(board, trial, args.device_id)
                else:
                    committed = deploy(board, bundle, args.device_id)
                    print(f"Committed {bundle.manifest_id}. Requesting a normal restart.", file=sys.stderr)
                    board.restart()
    except LoginError as exc:
        print(f"{args.command}: {exc}", file=sys.stderr)
        return 3
    except (BoardError, SessionError, DeploymentError) as exc:
        print(f"{args.command}: {exc}", file=sys.stderr)
        if committed is not None:
            print(json.dumps({**committed, "restart_acknowledged": False, "webrepl_authenticated": False}))
            print("Startup unconfirmed. Use info to compare the device ID and committed manifest.", file=sys.stderr)
            return 5
        if args.command == "deploy":
            print("Inspect info on the intended device before retrying the complete application.", file=sys.stderr)
        return 4 if isinstance(exc, BoardError) else 5

    if args.command == "put":
        print(json.dumps(uploaded))
        return 0
    authenticated = wait_for_login(address, password)
    print(json.dumps({**committed, "restart_acknowledged": True, "webrepl_authenticated": authenticated}))
    if not authenticated:
        print("The manifest was committed. Check the DHCP address and retry the login-only verifier. "
              "If the application disabled Wi-Fi, suspend it over USB.", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Local command cancelled. Use info to inspect an interrupted deployment, "
              "or stop to regain the prompt after a probe.", file=sys.stderr)
        sys.exit(130)
