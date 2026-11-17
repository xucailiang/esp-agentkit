"""Read local setup and WebREPL credentials without exposing them in command arguments."""

import getpass
import sys
import warnings


def read_hidden(prompt):
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except (getpass.GetPassWarning, EOFError):
            raise RuntimeError("Use a local interactive terminal with hidden input.") from None


def read_webrepl_password(*, from_stdin=False):
    if from_stdin:
        if sys.stdin.isatty():
            raise RuntimeError("Use a pipe for --password-stdin, or omit it for a hidden prompt.")
        # Allow nine characters plus CRLF. An oversized line must fail validation.
        line = sys.stdin.buffer.readline(12).removesuffix(b"\n").removesuffix(b"\r")
        try:
            password = line.decode("ascii")
        except UnicodeDecodeError:
            raise RuntimeError("The WebREPL password must use printable ASCII characters.") from None
    else:
        password = read_hidden("WebREPL password (4-9 printable ASCII characters): ")
    if not 4 <= len(password) <= 9 or not all(32 <= ord(c) <= 126 for c in password):
        raise RuntimeError("Use 4-9 printable ASCII characters for the WebREPL password.")
    return password
