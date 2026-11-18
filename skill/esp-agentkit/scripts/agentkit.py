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
    commands.add_parser("projects", help="List projects in this workspace without displaying passwords.")
    accept = commands.add_parser("accept", help="Record the developer's observation for a deployed project version.")
    accept.add_argument("--project", required=True, help="project name, alias, directory or project.json")
    accept.add_argument("--manifest", required=True, help="deployed manifest confirmed by the developer")
    accept.add_argument("--observation", required=True, help="developer's actual observation; never inferred from login")
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
        cmd.add_argument("--project", help="project name, alias, directory or project.json")
        cmd.add_argument("--address", type=IPv4Address, help="board's LAN IPv4 address; defaults to the project")
        cmd.add_argument("--password-stdin", action="store_true", help="read a password line from a pipe")
        cmd.add_argument("--device-id", help="expected device ID; required for put/deploy without --project")
        if name in ("run", "put"):
            cmd.add_argument("file", type=Path, help="local file")
        if name == "run":
            cmd.add_argument("--timeout", type=positive_seconds, default=10, metavar="SECONDS",
                             help="time budget for the probe (default: 10 seconds)")
        if name == "deploy":
            cmd.add_argument("directory", nargs="?", type=Path,
                             help="application directory; defaults to the project's app directory")
    return parser.parse_args()


def resolve_target(args):
    """Apply portable project defaults while preserving explicit CLI operation."""
    project = None
    if args.project:
        from projects import resolve_project

        project = resolve_project(args.project)
        if args.device_id and args.device_id.lower() != project.device_id:
            raise ValueError("--device-id conflicts with the selected project.")
        args.device_id = project.device_id
        args.address = args.address or IPv4Address(project.address)
        if args.command == "deploy":
            if args.directory is not None and args.directory.resolve() != project.app:
                raise ValueError("The deployment directory must match the selected project's app directory.")
            args.directory = project.app
        if args.command in ("run", "put") and not args.file.is_absolute():
            args.file = project.root / args.file
    if args.address is None:
        raise ValueError("Supply --project or --address.")
    if args.command in ("put", "deploy") and not args.device_id:
        raise ValueError("Supply --project or --device-id for this operation.")
    if args.command == "deploy" and args.directory is None:
        raise ValueError("Supply --project or an application directory.")
    return project


def verify_identity(session, expected):
    """Check the target before a probe; querying identity interrupts the app."""
    output, traceback, cleanup_error = session.execute(INFO_SOURCE, timeout=10)
    if traceback:
        sys.stderr.buffer.write(traceback)
        return 4
    if cleanup_error:
        print(cleanup_error, file=sys.stderr)
        return 5
    info = board_info(output, "")
    if info["device_id"].lower() != expected.lower():
        print(f"Device ID {info['device_id']} does not match {expected}. Check the project and address.",
              file=sys.stderr)
        return 5
    return 0


def report_probe(session, args, source):
    output, traceback, cleanup_error = session.execute(source, timeout=getattr(args, "timeout", 10))
    if args.command == "run":
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
    if traceback:
        sys.stderr.buffer.write(traceback)
    elif args.command == "info":
        try:
            info = board_info(output, args.address)
            if args.device_id and info["device_id"].lower() != args.device_id.lower():
                print(f"Device ID {info['device_id']} does not match {args.device_id}. Check the project and address.",
                      file=sys.stderr)
                return 5
            print(json.dumps(info))
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
    if args.command in ("projects", "accept"):
        from projects import ProjectSelectionError, discover_projects, resolve_project

        try:
            # ASCII-safe JSON also works when redirected streams use a legacy encoding.
            if args.command == "projects":
                print(json.dumps({"schema_version": 1, "projects": [
                    project.public_summary() for project in discover_projects()
                ]}))
            else:
                from project_records import record_acceptance

                project = resolve_project(args.project)
                path = record_acceptance(project, args.manifest, args.observation)
                print(json.dumps({"project": project.name, "manifest_id": args.manifest,
                                  "acceptance_record": str(path)}))
            return 0
        except ProjectSelectionError as exc:
            print(str(exc), file=sys.stderr)
            return 5
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2

    project = None
    bundle = None
    stage = "prepare"

    def progress(event):
        nonlocal stage
        stage = event["stage"]
        if project is not None:
            print(json.dumps({"event": "deployment_progress", **event}), file=sys.stderr)

    def finish(code, result=None):
        if project is not None and args.command == "deploy" and bundle is not None:
            from project_records import record_deployment

            try:
                path = record_deployment(project, bundle, result or {}, code, stage)
                print(json.dumps({"event": "deployment_record", "path": str(path)}), file=sys.stderr)
            except (OSError, ValueError) as exc:
                print(f"Could not save the local deployment record: {exc}", file=sys.stderr)
                return code or 5
        return code

    try:
        from dataclasses import replace
        from deployment import DeploymentError, deploy, prepare_deploy, prepare_put, put
        from projects import ProjectSelectionError
        from webrepl_session import BoardError, LoginError, SessionError, open_session

        project = resolve_target(args)
        if project is not None:
            project = replace(project, address=str(args.address))
        if args.command == "deploy":
            if project is None:
                bundle = prepare_deploy(args.directory)
            else:
                from project_records import freeze_application

                bundle = freeze_application(project)
            print(f"Target manifest {bundle.manifest_id}", file=sys.stderr)
        elif args.command == "put":
            trial = prepare_put(args.file)
        elif args.command == "run":
            source = read_probe(args.file)
        if project is not None and not args.password_stdin:
            password = project.password
        else:
            password = read_webrepl_password(from_stdin=args.password_stdin)
    except ModuleNotFoundError:
        print(
            "From the repository root, run 'uv sync --project skill/esp-agentkit --locked', "
            "then retry this tool with 'uv run --project skill/esp-agentkit --locked python'.",
            file=sys.stderr,
        )
        return 2
    except ProjectSelectionError as exc:
        print(str(exc), file=sys.stderr)
        return finish(5)
    except KeyboardInterrupt:
        stage = "cancelled"
        print("Local preparation cancelled before connecting to the board.", file=sys.stderr)
        return finish(130)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return finish(2)

    address = str(args.address)
    committed = None
    stage = "connect"
    try:
        with open_session(address, password) as session:
            print(f"{args.command}: sending an interrupt to the foreground interpreter.", file=sys.stderr)
            if args.command in ("run", "stop") and args.device_id:
                result = verify_identity(session, args.device_id)
                if result:
                    return result
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
                    stage = "identify"
                    committed = deploy(board, bundle, args.device_id, progress=progress)
                    print(f"Committed {bundle.manifest_id}. Requesting a normal restart.", file=sys.stderr)
                    stage = "restart"
                    board.restart()
    except LoginError as exc:
        print(f"{args.command}: {exc}", file=sys.stderr)
        return finish(3)
    except (BoardError, SessionError, DeploymentError, ValueError) as exc:
        print(f"{args.command}: {exc}", file=sys.stderr)
        if committed is not None:
            result = {**committed, "restart_acknowledged": False, "webrepl_authenticated": False}
            print(json.dumps(result))
            print("Startup unconfirmed. Use info to compare the device ID and committed manifest.", file=sys.stderr)
            return finish(5, result)
        if args.command == "deploy":
            print("Inspect info on the intended device before retrying the complete application.", file=sys.stderr)
        return finish(4 if isinstance(exc, BoardError) else 5)
    except KeyboardInterrupt:
        stage = "cancelled"
        print("Local command cancelled. Inspect deployment state before retrying.", file=sys.stderr)
        return finish(130, committed)

    if args.command == "put":
        print(json.dumps(uploaded))
        return 0
    stage = "reconnect"
    try:
        authenticated = wait_for_login(address, password)
    except KeyboardInterrupt:
        print("Local login check cancelled after the application was committed.", file=sys.stderr)
        return finish(130, {**committed, "restart_acknowledged": True})
    result = {**committed, "restart_acknowledged": True, "webrepl_authenticated": authenticated}
    print(json.dumps(result))
    if not authenticated:
        print("The manifest was committed. Check the DHCP address and retry the login-only verifier. "
              "If the application disabled Wi-Fi, suspend it over USB.", file=sys.stderr)
        return finish(5, result)
    stage = "complete"
    return finish(0, result)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Local command cancelled. Use info to inspect an interrupted deployment, "
              "or stop to regain the prompt after a probe.", file=sys.stderr)
        sys.exit(130)
