"""Verify a WebREPL login over Wi-Fi without interrupting the board."""

import argparse
from ipaddress import IPv4Address
import json

from credentials import read_webrepl_password


def verify_webrepl(address, password):
    """Wait for the board's positive authentication response on its LAN address."""
    from webrepl_session import open_session
    from operations import network_key, resource_lock

    with resource_lock([network_key(address)], "verify-login"), open_session(address, password):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--address", required=True, type=IPv4Address, help="board's current LAN IPv4 address"
    )
    parser.add_argument("--password-stdin", action="store_true", help="read a password line from a pipe")
    args = parser.parse_args()
    address = str(args.address)
    try:
        verify_webrepl(address, read_webrepl_password(from_stdin=args.password_stdin))
    except ModuleNotFoundError:
        parser.exit(
            1, "From the repository root, run 'uv sync --project skill/esp-agentkit --locked', "
            "then retry this tool with 'uv run --project skill/esp-agentkit --locked python'.\n"
        )
    except RuntimeError as exc:
        parser.exit(1, f"{exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "Login check interrupted.\n")
    print(json.dumps({"address": address, "webrepl_port": 8266, "webrepl_authenticated": True}))


if __name__ == "__main__":
    main()
