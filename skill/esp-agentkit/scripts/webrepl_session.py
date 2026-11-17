"""Authenticated WebREPL sessions for the ESP32 reference firmware."""

from contextlib import contextmanager
from secrets import token_hex
from threading import TIMEOUT_MAX
from time import monotonic

from mpremote.transport import TransportError, TransportExecError
from mpremote.transport_serial import SerialTransport
from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect


class LoginError(RuntimeError):
    """The board did not confirm WebREPL authentication."""


class SessionError(RuntimeError):
    """The authenticated interpreter did not confirm the requested operation."""


class BoardError(RuntimeError):
    """The interpreter confirmed a failure while operating on its filesystem."""


MAX_RESPONSE_BYTES = 1024 * 1024

# A dropped writer can survive in the interpreter until the next connection.
RELEASE_FILES = """\
if isinstance(globals().get('_agentkit_fs'), dict):
    try:
        if hasattr(_agentkit_fs.get('f'), 'close'):
            _agentkit_fs['f'].close()
    finally:
        del _agentkit_fs
"""


def _wait_for_login_message(connection, expected):
    deadline = monotonic() + 5
    received = bytearray()
    while len(received) < 2048:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError
        received.extend(connection.recv(timeout=remaining, decode=False))
        if b"Access denied" in received:
            raise LoginError(
                "WebREPL rejected the password. Check the address and retry "
                "with the configured WebREPL password."
            )
        if expected in received:
            return
    raise LoginError("Unexpected WebREPL greeting. Check the board address and firmware.")


@contextmanager
def open_session(address, password):
    """Authenticate without interrupting the interpreter until an operation is requested."""
    try:
        connection = connect(
            f"ws://{address}:8266/",
            proxy=None,
            compression=None,
            open_timeout=5,
            close_timeout=1,
            ping_interval=None,
        )
    except (OSError, WebSocketException) as exc:
        raise LoginError(
            f"Could not connect to WebREPL at {address}:8266. "
            "Check the address, LAN reachability and whether the board is servicing Wi-Fi."
        ) from exc

    with connection:
        try:
            _wait_for_login_message(connection, b"Password: ")
            connection.send(password + "\r")
            # WebREPL itself prints this prompt even if the application is still running.
            _wait_for_login_message(connection, b"WebREPL connected\r\n>>> ")
        except (OSError, WebSocketException) as exc:
            raise LoginError(
                f"WebREPL login at {address}:8266 could not be confirmed. "
                "Close other WebREPL clients and check that the board is servicing Wi-Fi."
            ) from exc
        yield WebReplSession(connection)


class _WebSocketStream:
    """Present WebREPL text frames to mpremote's byte-oriented raw-paste writer."""

    def __init__(self, connection):
        self.connection = connection
        self.buffer = bytearray()
        self.deadline = monotonic()

    def remaining(self):
        seconds = self.deadline - monotonic()
        if seconds <= 0:
            raise TimeoutError
        return min(seconds, TIMEOUT_MAX)

    def write(self, data):
        self.remaining()
        # WebREPL routes binary messages to its file-transfer handler.
        self.connection.send(data, text=True)

    def _receive(self):
        self.buffer.extend(self.connection.recv(timeout=self.remaining(), decode=False))

    def inWaiting(self):
        self.remaining()
        if not self.buffer:
            try:
                self.buffer.extend(self.connection.recv(timeout=0, decode=False))
            except TimeoutError:
                pass
        return len(self.buffer)

    def read(self, count):
        while len(self.buffer) < count:
            self._receive()
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data

    def read_through(self, marker):
        start = 0
        while True:
            index = self.buffer.find(marker, start)
            if 0 <= index <= MAX_RESPONSE_BYTES:
                return self.read(index + len(marker))
            if len(self.buffer) >= MAX_RESPONSE_BYTES + len(marker):
                raise SessionError(
                    "The REPL response exceeded 1 MiB. Use stop to regain the REPL "
                    "and rerun a probe that prints a shorter sample."
                )
            start = max(0, len(self.buffer) - len(marker) + 1)
            self._receive()


class _RawRepl(SerialTransport):
    """Run mpremote's filesystem and raw-paste operations on an authenticated WebREPL."""

    def __init__(self, session):
        self.serial = session.stream
        self.step = session._step
        self.isolated = False
        self.reset_requested = False

    def read_until(self, min_num_bytes, ending, *, data_consumer=None, **kwargs):
        data = self.serial.read_through(ending)
        if data_consumer is not None:
            data_consumer(data)
            return ending
        return data

    def _submit(self, source):
        if isinstance(source, str):
            source = source.encode("utf-8")
        # A raw-paste credit boundary can fall inside a multibyte UTF-8 character.
        # Use ASCII on the wire; decode the original source inside the interpreter.
        if self.isolated or not source.isascii():
            namespace = b", _agentkit_fs" if self.isolated else b""
            source = b"exec(" + repr(source).encode("ascii") + b".decode()" + namespace + b")"
        self.serial.write(b"\x05A\x01")
        if self.serial.read(2) != b"R\x01":
            raise SessionError("Raw-paste mode is unavailable. Check the reference firmware.")
        self.raw_paste_write(source)

    def exec_raw(self, command, timeout=10, data_consumer=None):
        with self.step("script execution and its complete output", timeout):
            self._submit(command)
            result = self.follow(timeout, data_consumer)
            if self.serial.read(1) != b">":
                raise SessionError(
                    "Unexpected raw REPL output framing. Print binary data as hex. "
                    "Reconnect with stop before sending another probe."
                )
            return result

    def restart(self):
        """Get a board-side acknowledgment before the hard reset drops Wi-Fi."""
        marker = "ESP_AGENTKIT_RESTART_" + token_hex(8)
        self.reset_requested = True
        try:
            with self.step("the board's reset acknowledgment"):
                self._submit(
                    f"import machine, time\nprint({marker!r})\ntime.sleep_ms(150)\nmachine.reset()"
                )
                self.serial.read_through(marker.encode("ascii"))
        except SessionError as exc:
            raise SessionError(
                "Reset acknowledgment missing. Use info to compare the device ID and committed "
                "manifest before deciding whether to restart or redeploy."
            ) from exc

        # A hard reset can power down TCP before a FIN is sent. A raw EOF here
        # means the reset call actually returned on the original interpreter.
        self.serial.deadline = monotonic() + 1
        try:
            self.serial.read_through(b"\x04")
        except (ConnectionClosed, OSError):
            return
        with self.step("the result of the reset call"):
            error = self.serial.read_through(b"\x04")[:-1].decode("utf-8", "replace")
        raise BoardError("Reset returned on the original interpreter. " + error)


class WebReplSession:
    def __init__(self, connection):
        self.stream = _WebSocketStream(connection)

    @contextmanager
    def _step(self, action, timeout=5):
        self.stream.deadline = monotonic() + timeout
        try:
            yield
        except (OSError, WebSocketException, TransportError) as exc:
            raise SessionError(
                f"Could not confirm {action}. Reconnect with stop to regain the REPL. "
                "Use USB if the board stays unresponsive."
            ) from exc

    def _enter_raw(self):
        with self._step("the interrupt and raw REPL prompt"):
            self.stream.write(b"\x03")
            self.stream.write(b"\x01")
            self.stream.read_through(b"raw REPL; CTRL-B to exit\r\n>")

    def _leave_raw(self):
        with self._step("the return to the friendly REPL"):
            self.stream.write(b"\x02")
            # The firmware prints its banner even when a probe has changed sys.ps1.
            self.stream.read_through(b'Type "help()" for more information.\r\n')

    def stop(self):
        self._enter_raw()
        self._leave_raw()

    @contextmanager
    def filesystem(self):
        """Keep one raw REPL across related file operations without resetting the board."""
        self._enter_raw()
        board = _RawRepl(self)
        try:
            board.exec(RELEASE_FILES + "_agentkit_fs = {}")
            board.isolated = True
            synchronized = True
            try:
                yield board
            except (SessionError, KeyboardInterrupt):
                synchronized = False
                raise
            finally:
                if synchronized and not board.reset_requested:
                    board.isolated = False
                    board.exec(RELEASE_FILES)
                    self._leave_raw()
        except (TransportExecError, OSError) as exc:
            raise BoardError(str(exc)) from exc

    def execute(self, source, timeout=10):
        self._enter_raw()
        output, traceback = _RawRepl(self).exec_raw(source, timeout)
        try:
            self._leave_raw()
        except SessionError as exc:
            # Both EOFs and the raw prompt already confirmed execution.
            return output, traceback, f"Script execution finished. {exc}"
        return output, traceback, None
